# Flows — Workflow Orchestrator

Flows is Nubi's built-in workflow orchestrator: a lightweight, LLM-native alternative to Prefect or n8n. Define a directed acyclic graph (DAG) of tasks — query a warehouse, run Python, call the AI agent, materialize a blend, move files in and out of object storage, or refresh pre-aggregations — and Nubi will execute them in dependency order, retry failures, cache results, and keep durable Postgres state throughout.

---

## When to Use Flows vs Scheduled Jobs

| | **Jobs** | **Flows** |
|---|---|---|
| Shape | Single step | Multi-step DAG |
| Steps | One query, script, or report | query → python → agent (chained) |
| State | Job run per execution | Flow run + one task run per node |
| Retries | No | Per-task retries + backoff |
| Caching | No | Per-task TTL-based memoisation |
| Scheduling | Cron or interval | Cron or interval (same format) |
| LLM authoring | No | Yes — `generate_flow` tool |
| UI | None | React Flow DAG builder canvas |

Use **Jobs** for simple one-shot recurring actions (snapshot a query, email a PDF report). Use **Flows** when you need to chain multiple steps, fan out across tasks, or have the AI agent participate as a step in your pipeline.

---

## Blends — Cheap Reads vs Live Federation

A **blend** combines data from two to four source queries into a single dataset a dashboard can read. Nubi deliberately does **not** federate those sources live on every dashboard view. Instead it *materializes*: the multi-source merge runs once on a schedule (the `materialize` task) and writes the combined result to a persistent single-source DuckDB dataset. Dashboards then read that cheap, single-source dataset — content-hashed cache, predicate push-down, and near-zero marginal cost per view all apply, exactly as they do for any other connector.

| | **Live federation** | **Materialized blend (Nubi)** |
|---|---|---|
| When the join runs | Every dashboard view | Once per schedule tick |
| Read cost | N source round-trips per view | One single-source read (cached) |
| RLS at read time | Must be re-derived per source | Injected on the materialized output via preserved `rls_keys` |
| Freshness | Always live | As fresh as the last materialization |

**RLS contract.** The blend declares `rls_keys` (e.g. `["tenant_id"]`). The materialized table **must keep those columns** so the planner can still inject `WHERE tenant_id = <claim>` at read time on the blend output. The `materialize` handler verifies this and fails (`400 rls_key_dropped`) if the merge flattened a declared key away — dropping an RLS column would defeat multi-tenant safety on the served dataset.

This is the cost wedge: *materialize-then-serve, not federate-per-view.* See the [`materialize`](#materialize--build-a-materialized-blend) task kind and the [Materialized Blends](#materialized-blends) API/UI section below.

---

## The FlowSpec

A FlowSpec is a JSON document (version 1) that describes the entire DAG. It is the single source of truth for both the execution engine and the UI builder.

```json
{
  "version": 1,
  "name": "daily_revenue",
  "params": [
    { "name": "region", "type": "text", "default": "us", "required": false }
  ],
  "tasks": [
    {
      "key": "pull",
      "kind": "query",
      "needs": [],
      "config": { "query_id": "revenue_by_region" },
      "retries": 2,
      "retry_backoff_s": 30,
      "timeout_s": 60,
      "cache_ttl_s": 300,
      "ui": { "x": 0, "y": 0 }
    },
    {
      "key": "enrich",
      "kind": "python",
      "needs": ["pull"],
      "config": {
        "code": "result = {'row_count': inputs['pull']['row_count'], 'region': params['region']}"
      },
      "retries": 0,
      "retry_backoff_s": 30,
      "timeout_s": 30,
      "cache_ttl_s": 0,
      "ui": { "x": 220, "y": 0 }
    },
    {
      "key": "summary",
      "kind": "agent",
      "needs": ["enrich"],
      "config": {
        "prompt": "Summarize the revenue data for {{ params.region }}. Row count: {{ inputs.enrich.row_count }}.",
        "max_steps": 4
      },
      "retries": 1,
      "retry_backoff_s": 10,
      "timeout_s": 120,
      "cache_ttl_s": 0,
      "ui": { "x": 440, "y": 0 }
    }
  ]
}
```

### Top-level Fields

| Field | Type | Description |
|-------|------|-------------|
| `version` | `integer` | Schema version. Currently `1`. |
| `name` | `string` | Human-readable flow name (e.g. `"daily_revenue"`). |
| `params` | `array` | Flow-level parameter declarations (optional). |
| `tasks` | `array` | Ordered list of task definitions that form the DAG. |

### Flow Parameters

```json
{ "name": "region", "type": "text", "default": "us", "required": false }
```

| Field | Type | Description |
|-------|------|-------------|
| `name` | `string` | Unique parameter name within this flow. |
| `type` | `string` | `text`, `number`, `date`, `daterange`, `select`, or `multiselect`. |
| `default` | `any` | Default value. |
| `required` | `boolean` | Whether callers must supply this parameter at run time. |

### Task Fields

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `key` | `string` | — | Unique slug within this flow. Used as the task identifier in `needs` lists and `inputs` maps. |
| `kind` | `string` | — | Execution kind: `query`, `python`, `agent`, `materialize`, `noop`, `extract`, `bucket_load`, or `preagg_refresh`. |
| `needs` | `array` | `[]` | Upstream task keys this task depends on (DAG edges). Empty = root task. |
| `config` | `object` | `{}` | Kind-specific configuration. See below. |
| `retries` | `integer` | `0` | Number of retry attempts after the first failure. |
| `retry_backoff_s` | `integer` | `30` | Seconds to wait between retry attempts. |
| `timeout_s` | `integer` | `60` | Per-attempt timeout in seconds. `0` means no timeout. |
| `cache_ttl_s` | `integer` | `0` | Cache TTL in seconds. `0` = no caching. When `> 0`, the engine memoises the result by a content-based cache key. |
| `ui` | `object` | `{x:0,y:0}` | Canvas position for the DAG builder. Ignored by the execution engine. |

---

## Task Kinds

### `query` — Run a Registered Query or SQL

Executes a named query from the query registry (or raw SQL) against the warehouse, respecting RLS via the caller's claims.

```json
{
  "key": "pull",
  "kind": "query",
  "needs": [],
  "config": {
    "query_id": "revenue_by_region",
    "named_params": { "region": "{{ params.region }}" }
  }
}
```

**Config fields:**

| Field | Required | Description |
|-------|----------|-------------|
| `query_id` | One of `query_id` / `sql` required | ID of a registered query. |
| `sql` | One of `query_id` / `sql` required | Raw SQL string. |
| `named_params` | No | Override named parameters for this query run. |

The task result contains `row_count` and the returned rows (Arrow-serialised).

### `python` — Run a Python Script

Runs arbitrary Python in the server kernel (same path as Jobs python executor). The variables `inputs` (a dict of upstream task results, keyed by task key) and `params` (the flow-level parameter values) are injected automatically. Assign the task output to `result`.

```json
{
  "key": "enrich",
  "kind": "python",
  "needs": ["pull"],
  "config": {
    "code": "result = {'total': sum(row['revenue'] for row in inputs['pull']['rows'])}"
  }
}
```

**Config fields:**

| Field | Required | Description |
|-------|----------|-------------|
| `code` | Yes | Python source code. Must assign `result` to a JSON-serialisable value. |

### `agent` — Run the AI Agent

Calls the Nubi AI agent (`run_agent`) with the caller's claims, so the agent can use all registered tools (run queries, inspect lineage, etc.) with the same RLS context as the human user.

```json
{
  "key": "summary",
  "kind": "agent",
  "needs": ["enrich"],
  "config": {
    "prompt": "Summarize the revenue data. Context: {{ inputs.enrich.total }} total revenue.",
    "max_steps": 4
  }
}
```

**Config fields:**

| Field | Required | Description |
|-------|----------|-------------|
| `prompt` | Yes | Natural-language prompt for the agent. Supports `{{ }}` templating. |
| `max_steps` | No | Maximum tool-call steps before the agent stops (default: `4`). |

The task result is `{"reply": "...", "actions": [...]}`.

### `materialize` — Build a Materialized Blend

Merges the results of several upstream `query` tasks into one persistent, single-source dataset (a **blend**) and registers it so a dashboard widget can read it through a single `query_id`. The expensive multi-source merge runs on a schedule — not on every dashboard view. See [Blends — Cheap Reads vs Live Federation](#blends--cheap-reads-vs-live-federation) for the concept.

The handler registers each upstream source result as a DuckDB table named by its source task `key`, runs the author-supplied `combine_sql` against those tables, writes the combined result to an on-disk DuckDB file (`database`, table `table`), verifies the declared `rls_keys` survived the merge, and registers a runtime `SELECT * FROM <table>` query bound to the blend datastore.

```json
{
  "key": "blend",
  "kind": "materialize",
  "needs": ["orders", "signups"],
  "config": {
    "combine_sql": "SELECT o.tenant_id, o.day, o.revenue, s.new_users FROM orders o JOIN signups s USING (tenant_id, day)",
    "sources": ["orders", "signups"],
    "rls_keys": ["tenant_id"],
    "table": "blend",
    "database": "/abs/path/to/blend.duckdb",
    "datastore_id": "ds-uuid",
    "query_id": "q-uuid"
  }
}
```

**Config fields:**

| Field | Required | Description |
|-------|----------|-------------|
| `combine_sql` | Yes | DuckDB SQL that merges the source tables (each registered under its source task `key`) into the combined result. Author-provided, first-party, org-scoped SQL — not end-user input. |
| `database` | Yes | Absolute path to the DuckDB file the combined result is written to. The parent directory is created if missing. |
| `sources` | No | List of upstream source task keys to register as tables. Defaults to all `inputs` keys. |
| `rls_keys` | No | Columns that **must** appear in the combined output so the planner can inject `WHERE <key> = <claim>` at read time. If a declared key was flattened away, the task fails with `400 rls_key_dropped`. |
| `table` | No | Target table name inside the DuckDB file. Default: `blend`. |
| `datastore_id` | No | The pre-created `datastores` row id the blend is served through. |
| `query_id` | No | The pre-created `queries` row id a widget binds to. When both `datastore_id` and `query_id` are given, the handler registers the runtime query. |

The task result (the materialization manifest) is `{datastore_id, query_id, database, table, row_count, columns, rls_keys}`.

> Most callers do not hand-author this task — they use `POST /api/v1/flows/blend`, which pre-creates the datastore + query rows, builds the source `query` tasks plus the `materialize` task, runs the flow once, and returns `{flow, materialized: {datastore_id, query_id}}` for dashboard binding. See [Materialized Blends](#materialized-blends).

### `noop` — Pass-Through Fan-In/Fan-Out

A no-op task whose result is `{"inputs": {...}}` (all upstream results). Useful as an explicit join node when multiple branches converge before a downstream step.

```json
{
  "key": "join",
  "kind": "noop",
  "needs": ["branch_a", "branch_b"],
  "config": {}
}
```

No required config fields.

### `extract` — Unpack an Archive from Storage

Downloads an archive from object storage (or from an upstream task result), unpacks it, and uploads each extracted member to a destination storage prefix. Supports `zip`, `tar`, `tar.gz`/`tgz`, and bare `gz`. Every archive member is checked against a path-traversal guard before any I/O — members that resolve outside the extraction directory (e.g. `..` components, absolute paths) are rejected and the archive is refused.

```json
{
  "key": "unzip",
  "kind": "extract",
  "needs": [],
  "config": {
    "source_uri": "s3://my-bucket/uploads/data.zip",
    "dest_uri": "s3://my-bucket/extracted/",
    "secret": "S3_CREDS",
    "format": "auto"
  }
}
```

**Config fields:**

| Field | Required | Description |
|-------|----------|-------------|
| `source_uri` | One of `source_uri` / `source` | Full storage URI of the archive (e.g. `s3://bucket/data.zip`, `file:///tmp/archive.tar.gz`). |
| `source` | One of `source_uri` / `source` | Key of an upstream task whose result carries a `uri` (storage URI) or `bytes` (raw or base64-encoded) field. Mutually exclusive with `source_uri`. |
| `dest_uri` | Yes | Storage URI prefix members are uploaded under, as `<dest_uri>/<member-relative-name>`. |
| `secret` | No | Name of a secret whose JSON-decoded value is passed as the storage credentials dict. See [Secrets](/docs/secrets). If absent, environment / credential-chain auth is used. |
| `format` | No | One of `auto`, `zip`, `tar`, `tar.gz`, `tgz`, `gz`. Default `auto` (detect from magic bytes / extension). |

The task result is `{"files": [{"name", "size", "uri"}], "file_count": N}`.

### `bucket_load` — Write Data to Object Storage

Serialises an upstream task's rows into the requested format and uploads them to object storage (S3, GCS, Azure, or local) via the `app.storage` abstraction.

```json
{
  "key": "dump",
  "kind": "bucket_load",
  "needs": ["pull"],
  "config": {
    "uri": "s3://my-bucket/exports/revenue.parquet",
    "source": "pull",
    "format": "parquet",
    "mode": "overwrite",
    "secret": "S3_CREDS"
  }
}
```

**Config fields:**

| Field | Required | Description |
|-------|----------|-------------|
| `uri` | Yes | Destination storage URI. |
| `source` | Yes | Key of the upstream task whose result provides the data. Row-shaped (`{rows, columns}`), a raw `{bytes}` payload, a `{uri}` to copy verbatim, or a plain list of dicts are all accepted. |
| `format` | No | `csv`, `json`, `ndjson`, or `parquet`. Default `csv`. (`parquet` requires `pandas` + `pyarrow`.) |
| `mode` | No | `overwrite` (default) or `append`. `append` downloads the existing object, merges rows, and re-uploads. |
| `secret` | No | Name of a secret whose JSON-decoded value is the storage credentials dict. See [Secrets](/docs/secrets). |

The task result is `{"uri", "format", "row_count", "bytes_written"}`.

### `preagg_refresh` — Refresh Auto Pre-Aggregations

Runs the auto pre-aggregation suggest → materialize pass for an org: mines the query log for hot rollup candidates and builds those that clear the `min_hits` threshold. See [Pre-Aggregations](/docs/pre-aggregations) for the underlying engine.

```json
{
  "key": "rollups",
  "kind": "preagg_refresh",
  "needs": [],
  "config": {
    "org_id": "org-uuid",
    "min_hits": 3,
    "source_database": null
  }
}
```

**Config fields:**

| Field | Required | Description |
|-------|----------|-------------|
| `org_id` | Yes | The org whose query log is mined. |
| `min_hits` | No | Minimum query-log frequency for a candidate to be materialized. Default `3`. |
| `source_database` | No | Absolute path to the DuckDB file holding the base fact tables. `null` (the default) uses an in-memory context. |

The task result is `{org_id, candidates_found, rollups_built, rollup_ids, errors}`.

---

## Templating

Strings inside `config` values and `named_params` support `{{ }}` expressions referencing flow parameters and upstream task results:

| Expression | Resolves to |
|------------|-------------|
| `{{ params.region }}` | The value of the `region` flow parameter. |
| `{{ inputs.pull.row_count }}` | The `row_count` field from the `pull` task's result. |
| `{{ inputs.enrich.total }}` | The `total` field from the `enrich` task's result. |
| `{{ secrets.NAME }}` | The plaintext value of the org secret named `NAME`. Resolved server-side from `ctx.secrets`; the value is never exposed to the client. See [Secrets](/docs/secrets). |

Templates are resolved by the executor at runtime, after upstream tasks have completed.

---

## DAG Semantics and Dependency Order

The engine derives execution order from the `needs` edges:

- **Root tasks** — tasks with `needs: []` — are marked `ready` immediately when the flow run is created.
- A **downstream task** becomes `ready` only when every task in its `needs` list has reached `success`.
- If any upstream task ends in `failed` or `skipped`, all directly or transitively dependent tasks are marked `skipped` (no execution).
- The flow run itself moves to `success` when all task runs are terminal and none failed; it moves to `failed` if any task run failed.

The `key` field in `needs` must match an existing task key in the same flow. Forward references (a task that appears later in the `tasks` array) are valid — the engine uses the graph, not the list order.

---

## State Machines

### Flow Run States

```
pending → running → success
                 → failed
                 → cancelled
```

| State | Description |
|-------|-------------|
| `pending` | Created but not yet picked up by the worker. |
| `running` | Execution in progress — at least one task is ready or running. |
| `success` | All task runs completed successfully. |
| `failed` | At least one task run ended in `failed`. |
| `cancelled` | Manually cancelled (not yet implemented in the UI). |

### Task Run States

```
pending → ready → running → success
                          → failed → retrying → running …
                                   → failed (exhausted)
                 → skipped
```

| State | Description |
|-------|-------------|
| `pending` | Waiting for upstream tasks to complete. |
| `ready` | All upstream tasks succeeded; waiting to be claimed by the worker. |
| `running` | Currently executing. |
| `success` | Completed successfully. |
| `failed` | Failed after exhausting all retries. |
| `retrying` | Scheduled for a retry attempt after a backoff delay. |
| `skipped` | Skipped because an upstream task failed or was skipped. |

---

## Retries, Timeout, and Caching

### Retries

Set `retries` to the number of additional attempts after the first failure. `retry_backoff_s` controls the wait between attempts. The `attempt` counter on the task run increments with each try.

```json
{ "retries": 3, "retry_backoff_s": 60 }
```

This gives up to 4 total attempts (1 initial + 3 retries) with 60-second waits between each.

### Timeout

`timeout_s` applies per attempt. If the handler does not return within the timeout, the attempt is treated as a failure. `0` means no timeout is enforced.

### Caching

When `cache_ttl_s > 0`, the engine computes a content-based `cache_key` from the task config and the upstream inputs. If a matching cache entry exists and has not expired, the engine reuses the cached result without re-executing the handler. Set `cache_ttl_s: 0` to always re-run (the default).

---

## Scheduling

Attach a schedule to a flow using the `PUT /api/v1/flows/{id}` endpoint. Flows use the same schedule format as Jobs:

| Format | Syntax | Example |
|--------|--------|---------|
| Cron | 5-field cron | `"0 7 * * 1-5"` — weekdays at 07:00 UTC |
| Interval | `"interval:Ns"` | `"interval:3600s"` — every hour |

When the flow worker is enabled (`FLOWS_WORKER_ENABLED=true`), it picks up due flows at each tick (default: every 5 seconds) and materialises a new flow run with `trigger: "schedule"`.

The `next_run_at` and `last_run_at` fields on the flow record are updated automatically.

---

## REST API

All endpoints require a valid first-party Bearer token. Flows are org-scoped — callers can only access flows belonging to their own org. Cross-org access returns `404` (no information leak).

Base path: `/api/v1/flows`

### Create a Flow

```
POST /api/v1/flows
Authorization: Bearer <jwt>
Content-Type: application/json
```

Request:

```json
{
  "name": "daily_revenue",
  "spec": {
    "version": 1,
    "name": "daily_revenue",
    "params": [],
    "tasks": [
      {
        "key": "pull",
        "kind": "query",
        "needs": [],
        "config": { "query_id": "revenue_by_region" }
      }
    ]
  }
}
```

Response `201`:

```json
{
  "id":           "flow-uuid",
  "org_id":       "org-uuid",
  "created_by":   "user-uuid",
  "name":         "daily_revenue",
  "spec":         { "version": 1, "name": "daily_revenue", "params": [], "tasks": [...] },
  "version":      1,
  "enabled":      true,
  "schedule":     null,
  "next_run_at":  null,
  "last_run_at":  null,
  "created_at":   "2024-01-15T09:00:00Z",
  "updated_at":   "2024-01-15T09:00:00Z"
}
```

Returns `400` when the spec fails hard validation, with `{"detail": "<error messages>"}`.

### List Flows

```
GET /api/v1/flows
Authorization: Bearer <jwt>
```

Response `200`: array of flow objects (same shape as the create response).

### Get a Flow

```
GET /api/v1/flows/{id}
Authorization: Bearer <jwt>
```

Response `200`: single flow object. Returns `404` if the flow does not exist or belongs to a different org.

### Update a Flow

```
PUT /api/v1/flows/{id}
Authorization: Bearer <jwt>
Content-Type: application/json
```

Request — all fields optional:

```json
{
  "name":     "daily_revenue_v2",
  "spec":     { ... },
  "enabled":  false,
  "schedule": "0 7 * * 1-5"
}
```

Response `200`: updated flow object. Returns `400` on spec validation failure; `404` if not found or cross-org.

### Delete a Flow

```
DELETE /api/v1/flows/{id}
Authorization: Bearer <jwt>
```

Response `204` on success. Returns `404` if not found or cross-org.

### Validate a Spec (Dry Run)

```
POST /api/v1/flows/validate
Authorization: Bearer <jwt>
Content-Type: application/json
```

Request:

```json
{ "spec": { "version": 1, "name": "test", "tasks": [...] } }
```

Response `200`:

```json
{
  "valid":  true,
  "issues": ["[warn] Task 'pull': query_id 'unknown_query' is not in the registered query registry (may be a forward reference)."]
}
```

`valid` is `true` when there are no hard errors (warnings prefixed `[warn]` do not affect validity). Hard error example:

```json
{
  "valid":  false,
  "issues": ["Cycle detected: a → b → a."]
}
```

### Run a Flow

Materialises a new flow run and drains all tasks synchronously. The response is returned when all tasks reach a terminal state.

```
POST /api/v1/flows/{id}/run
Authorization: Bearer <jwt>
Content-Type: application/json
```

Request:

```json
{ "params": { "region": "emea" } }
```

`params` is optional. Response `200`:

```json
{
  "id":          "run-uuid",
  "flow_id":     "flow-uuid",
  "org_id":      "org-uuid",
  "state":       "success",
  "params":      { "region": "emea" },
  "trigger":     "manual",
  "scheduled_at": null,
  "started_at":  "2024-01-15T09:00:01Z",
  "finished_at": "2024-01-15T09:00:04Z",
  "error":       null,
  "created_at":  "2024-01-15T09:00:01Z",
  "task_runs": [
    {
      "id":          "tr-uuid-1",
      "flow_run_id": "run-uuid",
      "org_id":      "org-uuid",
      "task_key":    "pull",
      "state":       "success",
      "attempt":     0,
      "depends_on":  [],
      "cache_key":   null,
      "result":      { "row_count": 120 },
      "error":       null,
      "scheduled_at": "2024-01-15T09:00:01Z",
      "started_at":  "2024-01-15T09:00:01Z",
      "finished_at": "2024-01-15T09:00:02Z",
      "created_at":  "2024-01-15T09:00:01Z"
    },
    {
      "id":          "tr-uuid-2",
      "flow_run_id": "run-uuid",
      "org_id":      "org-uuid",
      "task_key":    "summary",
      "state":       "success",
      "attempt":     0,
      "depends_on":  ["pull"],
      "cache_key":   null,
      "result":      { "reply": "Revenue was 120 rows in EMEA.", "actions": [] },
      "error":       null,
      "scheduled_at": "2024-01-15T09:00:02Z",
      "started_at":  "2024-01-15T09:00:02Z",
      "finished_at": "2024-01-15T09:00:04Z",
      "created_at":  "2024-01-15T09:00:02Z"
    }
  ]
}
```

`trigger` is always `"manual"` for API-triggered runs.

### List Flow Runs

```
GET /api/v1/flows/{id}/runs
Authorization: Bearer <jwt>
```

Response `200`: array of flow run objects (without `task_runs`), newest first. Returns `404` if the flow is not found or cross-org.

### Get a Flow Run (Live Polling)

```
GET /api/v1/flows/runs/{run_id}
Authorization: Bearer <jwt>
```

Response `200`: flow run object with `task_runs` array included. Returns `404` if the run is not found or belongs to a different org.

Use this endpoint to poll from the UI while a long run is in progress. The run view polls every ~1.5 seconds until the flow run reaches a terminal state (`success`, `failed`, or `cancelled`).

---

## Materialized Blends

`POST /api/v1/flows/blend` is the high-level entry point for building a [materialized blend](#blends--cheap-reads-vs-live-federation). It does the wiring a hand-authored `materialize` flow would require, then runs the flow once so the dataset exists immediately.

```
POST /api/v1/flows/blend
Authorization: Bearer <jwt>
Content-Type: application/json
```

Request:

```json
{
  "name": "revenue_and_signups",
  "sources": [
    { "key": "orders",  "query_id": "orders_by_day" },
    { "key": "signups", "sql": "SELECT tenant_id, day, count(*) AS new_users FROM signups GROUP BY 1, 2" }
  ],
  "combine_sql": "SELECT o.tenant_id, o.day, o.revenue, s.new_users FROM orders o JOIN signups s USING (tenant_id, day)",
  "schedule": "0 6 * * *",
  "rls_keys": ["tenant_id"]
}
```

| Field | Required | Description |
|-------|----------|-------------|
| `name` | Yes | Blend (and flow) name. |
| `sources` | Yes | One to four sources. Each is `{key, query_id?, sql?, datastore_id?, named_params?}` and must supply `query_id` **or** `sql`. Each becomes a single-source `query` task (so per-source predicate push-down + RLS stay intact). |
| `combine_sql` | Yes | DuckDB SQL merging the source tables (registered under their `key`s) into the materialized result. |
| `schedule` | No | Cron or `interval:Ns` schedule for re-materialization. The blend always runs once immediately on create regardless. |
| `rls_keys` | No | Columns that must survive the merge so RLS injection works at read time. |

On create the endpoint pre-creates the `datastores` + `queries` rows, builds and validates the blend FlowSpec (source `query` tasks + one `materialize` task), persists + schedules the flow, runs it once, and returns:

```json
{
  "flow":         { "id": "flow-uuid", "name": "revenue_and_signups", "schedule": "0 6 * * *", "...": "..." },
  "materialized": { "datastore_id": "ds-uuid", "query_id": "q-uuid" },
  "run":          { "state": "success", "task_runs": [ ... ] }
}
```

Bind a dashboard widget to `materialized.query_id`. Returns `400 blend_materialize_failed` (e.g. `rls_key_dropped`) if the first materialization fails, and `400 bad_blend` if a source omits both `query_id` and `sql`.

In the UI, the **Blend Builder** (`/blends`) provides a form-driven way to pick sources, write `combine_sql`, declare `rls_keys`, set a schedule, and create the blend — then copy the resulting `query_id` for a widget.

---

## Validation Rules

`POST /api/v1/flows` and `PUT /api/v1/flows/{id}` both run `validate_flow_spec` on the spec before persisting. The same logic is exposed as a standalone dry-run endpoint at `POST /api/v1/flows/validate`.

**Hard errors** (cause `valid: false` and `400` on create/update):

1. Pydantic parse failure — wrong types, missing required fields, invalid enum values.
2. Duplicate task `key` within the same flow.
3. A `needs` entry references a task key that does not exist in the spec.
4. The DAG contains a cycle (reported as `"Cycle detected: a → b → a."`).
5. Missing kind-required config fields: `query`→`query_id` or `sql`; `python`→`code`; `agent`→`prompt`; `materialize`→`combine_sql`; `extract`→`dest_uri` plus exactly one of `source_uri`/`source`; `bucket_load`→`uri` and `source`; `preagg_refresh`→`org_id`. (`noop` has no required config fields.)

**Soft warnings** (prefixed `[warn]`, do not block create/update):

- A `query_id` is not present in the live query registry (may be a forward reference to a query not yet created).

---

## AI Tools — Author and Run Flows in Natural Language

Flows are first-class citizens in the Nubi AI agent. The agent has five flow-specific tools available in the `POST /api/v1/ai/chat` agentic loop.

### `list_flows`

Lists all flows for the caller's org.

**Parameters:** none

**Returns:**

```json
{ "flows": [{ "id": "flow-uuid", "name": "daily_revenue" }] }
```

### `create_flow`

Validates a FlowSpec and persists it as a new flow.

**Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `name` | `string` | Yes | Human-readable flow name. |
| `spec` | `object` | Yes | Complete FlowSpec dict. |

**Returns:**

```json
{ "id": "flow-uuid", "valid": true, "issues": [] }
```

When validation fails: `{ "id": null, "valid": false, "issues": ["..."] }`.

### `run_flow`

Materialises and synchronously drains a flow run with the caller's RLS claims.

**Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `flow_id` | `string` | Yes | UUID of the flow to run. |
| `params` | `object` | No | Flow-level parameter values. |

**Returns:**

```json
{
  "flow_run_id": "run-uuid",
  "state":       "success",
  "task_runs":   [{ "task_key": "pull", "state": "success" }]
}
```

### `get_flow_run`

Returns the current state of a flow run, including per-task results and errors.

**Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `flow_run_id` | `string` | Yes | UUID of the flow run. |

**Returns:**

```json
{
  "state": "success",
  "task_runs": [
    {
      "task_key": "pull",
      "state":    "success",
      "result":   { "row_count": 120 },
      "error":    null
    }
  ]
}
```

### `generate_flow`

Generates a complete FlowSpec from a natural-language description, grounded by the FlowSpec JSON Schema. The LLM returns a ready-to-use spec that can be passed directly to `create_flow`.

When no LLM provider is configured (`NullProvider`), returns a deterministic 2-task demo flow (query `demo_all` → agent summary) so the tool is testable without an API key.

**Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `question` | `string` | Yes | Natural-language description of what the flow should do. |

**Returns:**

```json
{
  "spec":     { "version": 1, "name": "demo_flow", "params": [], "tasks": [...] },
  "provider": "anthropic"
}
```

### Example Agent Session

```
User: "Create a flow that pulls yesterday's revenue, calculates a growth rate in Python,
       then asks the AI to write a one-sentence summary. Run it for region=us."

Agent tool call → generate_flow({ "question": "..." })
  → returns a 3-task FlowSpec: pull → enrich → summary

Agent tool call → create_flow({ "name": "revenue_summary", "spec": { ... } })
  → { "id": "flow-uuid", "valid": true, "issues": [] }

Agent tool call → run_flow({ "flow_id": "flow-uuid", "params": { "region": "us" } })
  → { "state": "success", "task_runs": [...] }

Agent: "Done. The flow ran successfully. Revenue was up 4.2% vs the prior day."
```

---

## UI — React Flow DAG Builder

The Flows page (`/flows`) provides a visual canvas for authoring and monitoring flows, built on [React Flow](https://reactflow.dev/).

### Canvas Layout

- **Left panel** — list of your org's flows. Click to open in the builder; "New flow" seeds an empty spec.
- **Center canvas** — the React Flow DAG. Nodes represent tasks; arrows represent `needs` dependencies. A minimap and zoom controls sit in the corner.
- **Right panel (Inspector)** — appears when you click a node. Edit the task's key, kind, config, retries, timeout, and cache settings.

### Building a Flow

1. **Add tasks** — drag task kinds (query / python / agent / noop) from the node palette onto the canvas.
2. **Connect tasks** — drag from a source handle on one node to a target handle on another to create a `needs` edge.
3. **Configure tasks** — click a node to open the inspector. Fill in the kind-specific config (query ID or SQL, Python code, or agent prompt). Set retries, timeout, and cache TTL.
4. **Validate** — click "Validate" to call `POST /api/v1/flows/validate`. Any hard errors or warnings appear in an overlay panel.
5. **Save** — click "Save" to create or update the flow (`POST /api/v1/flows` or `PUT /api/v1/flows/{id}`). Spec is rejected with an error message on validation failure.
6. **Run** — click "Run" to trigger an immediate execution. The UI switches to the run view.

### Task Node Colors

Each node displays a status indicator dot colored by the current `task_run.state` during a live run:

| State | Color |
|-------|-------|
| `pending` | Slate |
| `ready` | Blue |
| `running` | Amber (pulsing) |
| `success` | Green |
| `failed` | Red |
| `retrying` | Orange |
| `skipped` | Gray |

### Live Run View

After clicking "Run", the canvas switches to a read-only run view that polls `GET /api/v1/flows/runs/{run_id}` every ~1.5 seconds. As task runs advance through their states, the node colors update in real time. Click any node to see its `result` or `error` detail. A banner at the top shows the overall flow run state.

Once the flow run reaches a terminal state (`success`, `failed`, or `cancelled`), polling stops automatically.

---

## RLS and Org Scoping

All Flows operations are scoped to the caller's org:

- API endpoints resolve the caller's `org_id` from their first-party Bearer token and apply it to every query. A flow belonging to a different org returns `404` — not `403` — to avoid leaking existence information.
- When a `query` task runs, the caller's `claims` (including `policies`) are passed through to `app.connectors.planner.plan(...)`. RLS predicates are injected at the AST level by the planner, exactly as they are for dashboard and direct query execution.
- When an `agent` task runs, the same `claims` are forwarded to `run_agent`, so the agent operates under the same data access restrictions as the human user who triggered the flow.
- AI tools (`create_flow`, `run_flow`, `get_flow_run`, `list_flows`) resolve the org from `claims["org_id"]`, which is set by the agentic chat endpoint (`POST /api/v1/ai/chat`) from the authenticated user's token.

---

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `FLOWS_WORKER_ENABLED` | `false` | Enable the background flow worker tick loop. Set to `true` in production to process scheduled flows and drain ready task runs automatically. |
| `FLOWS_WORKER_INTERVAL_S` | `5` | Seconds between worker ticks. |

When `FLOWS_WORKER_ENABLED=false` (the default and test mode), flows must be triggered manually via `POST /api/v1/flows/{id}/run` or the AI tools. The worker is never enabled in the test suite to keep tests deterministic.
