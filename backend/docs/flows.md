# Flows engine — materialized blends & deployment

The flows engine is Nubi's scheduled-automation orchestrator. It runs a DAG of
tasks (`query`, `python`, `agent`, `materialize`, `noop`) on a manual trigger
(`POST /flows/{id}/run`) or on a cron schedule. This doc covers two additions:

1. **Materialized multi-source blends** — the cost-wedge feature.
2. **Cloud Run scheduler readiness** — running the engine without an always-on
   worker, plus multi-instance safety.

---

## Part A — Materialized blends

### The model (cost wedge: materialize-then-serve, NOT live federation)

A **blend** is a SCHEDULED flow that fans out to N source queries, merges them in
DuckDB, and materializes the combined result to a cheap **single-source dataset**
that dashboards read (cached + predicate-pushdown-able).

The expensive multi-source join runs **on a schedule, never per dashboard view**.
This is the cost wedge: a widget binds to one `query_id` and reads a single
materialized DuckDB table. There is no live cross-source federation at read time.

```
schedule tick / immediate run
        │
        ▼
┌──────────────┐   ┌──────────────┐        N single-source `query` tasks.
│ query: src_a │   │ query: src_b │ …      Per-source predicate pushdown + RLS
└──────┬───────┘   └──────┬───────┘        stay intact (each runs on its own
       │ rows             │ rows           connector).
       └────────┬─────────┘
                ▼
        ┌───────────────────┐   `materialize` task: registers each upstream
        │ materialize: blend │   result as a DuckDB table named by its source
        └─────────┬─────────┘   `key`, runs `combine_sql`, writes the combined
                  ▼              result to a DuckDB file + table.
        seed_data/blends/<datastore_id>.duckdb  (table: "blend")
                  ▼
        datastores row (type=duckdb, config.database=<abs path>)
        queries   row (SELECT * FROM "blend", bound to the datastore)
                  ▼
        dashboard widget binds to ONE query_id  ──►  POST /query  (cheap, cached)
```

### Materialize target

- **File:** `backend/seed_data/blends/<datastore_id>.duckdb` (absolute path,
  one file per blend, keyed by the blend's datastore id). The directory is
  created lazily on first materialization. `seed_data/` + `*.duckdb` are
  gitignored.
- **Table:** `blend` (default; configurable via the materialize task's `table`).
- **Served as a normal single-source datastore:** the blend endpoint upserts a
  `datastores` row (`type=duckdb`, `config.database=<abs path>`) and a registered
  `queries` row (`SELECT * FROM "blend"`) bound to that datastore. The standard
  read path (`routes/query.py`) opens the file **read-only** with
  `enable_external_access=false`.

### RLS-key preservation (CRITICAL for the wedge + multi-tenant safety)

A blend declares `rls_keys` (e.g. `["tenant_id"]`). The combined materialized
table **MUST keep those columns** so the planner can still inject
`WHERE tenant_id = <claim>` at READ time on the materialized source (predicate
injection on the blend output).

- The `combine_sql` author is responsible for selecting the RLS columns through
  to the output.
- `materialize_blend` **verifies** this: if any declared `rls_key` is missing
  from the combined output columns it raises `AppError("rls_key_dropped", 400)`
  and nothing is served. Do not flatten away RLS columns.
- At read time, RLS works exactly as for any other DuckDB datastore: the DuckDB
  connector declares `predicate_rls=True`, and `plan()` adds the
  `WHERE <key> = <value>` equality from `claims["policies"]`.

### Spec shape

A blend is an ordinary flow spec. `build_blend_spec(...)` produces:

```jsonc
{
  "version": 1,
  "name": "Revenue blend",
  "tasks": [
    { "key": "src_a", "kind": "query", "needs": [],
      "config": { "sql": "SELECT tenant_id, name, value FROM demo WHERE active" } },
    { "key": "src_b", "kind": "query", "needs": [],
      "config": { "query_id": "some_registered_query", "datastore_id": "..." } },
    { "key": "blend", "kind": "materialize", "needs": ["src_a", "src_b"],
      "config": {
        "combine_sql": "SELECT tenant_id, name, value FROM src_a UNION ALL SELECT tenant_id, name, value FROM src_b",
        "sources": ["src_a", "src_b"],
        "rls_keys": ["tenant_id"],
        "table": "blend",
        "database": "/abs/path/seed_data/blends/<datastore_id>.duckdb",
        "datastore_id": "<datastore uuid>",
        "query_id": "<query uuid>"
      } }
  ]
}
```

The `materialize` task kind (in `app/flows/spec.py`) requires `combine_sql` in
config; the merge logic lives in `app/flows/materialize.py`.

### Convenience endpoint — `POST /flows/blend`

Org-scoped, authed (first-party Bearer token). Builds the blend spec, creates
the datastore + query rows, creates the flow (enabled; with
`schedule → next_run_at` if a schedule is given), **runs it once immediately to
materialize**, and returns the binding the frontend needs.

**Request:**

```jsonc
{
  "name": "Revenue blend",
  "sources": [
    { "key": "src_a", "sql": "SELECT 't1' AS tenant_id, name, value FROM demo WHERE active" },
    { "key": "src_b", "query_id": "regional_sales", "datastore_id": "<ds uuid>", "named_params": { "region": "south" } }
  ],
  "combine_sql": "SELECT tenant_id, name, value FROM src_a UNION ALL SELECT tenant_id, name, value FROM src_b",
  "schedule": "@hourly",          // optional; omit for one-shot
  "rls_keys": ["tenant_id"]       // optional; preserved through the merge
}
```

Each source requires `query_id` OR `sql`. `datastore_id` / `named_params` are
optional per source.

**Response (201):**

```jsonc
{
  "flow": { "id": "...", "name": "Revenue blend", "spec": { ... }, "schedule": "@hourly", "next_run_at": "...", ... },
  "materialized": { "datastore_id": "<ds uuid>", "query_id": "<query uuid>" },
  "run": { "id": "...", "state": "success", "task_runs": [ ... ] }
}
```

The frontend binds a dashboard widget to **`materialized.query_id`** — it reads
the materialized single-source dataset via `POST /query`.

Failure modes: `bad_blend` (400, no sources / source missing query_id|sql),
`bad_flow_spec` (400, invalid spec), `blend_materialize_failed` (400, surfaced
from the materialize task — e.g. `rls_key_dropped`).

---

## Part B — Cloud Run readiness

### Two deployment modes (both call the same `flow_tick`)

**Mode 1 — in-process worker** (local / VM / Cloud Run `min-instances ≥ 1`):
`start_flow_worker` runs a background asyncio loop that calls `flow_tick` every
`FLOWS_WORKER_INTERVAL_S` seconds. Gated by `FLOWS_SCHEDULER_ENABLED` (which
inherits from the legacy `FLOWS_WORKER_ENABLED` / `JOBS_SCHEDULER_ENABLED`
flags). This is the simplest mode but requires an always-running process.

**Mode 2 — `POST /flows/tick` (Cloud Run + Cloud Scheduler):** Cloud Run
throttles CPU off-request and scales to zero, so an always-on background loop is
unreliable. Instead, **Google Cloud Scheduler** POSTs `/flows/tick` on cron and
each call runs exactly one `await flow_tick(store, now)`.

`/flows/tick` is **internal** — authed via a shared-secret header, NOT a user
JWT:

```
POST /api/v1/flows/tick
X-Nubi-Tick-Secret: <FLOWS_TICK_SECRET>
```

- `FLOWS_TICK_SECRET` unset → endpoint disabled, returns `503 tick_not_configured`.
- Wrong / missing header → `401 unauthorized`.
- Correct header → `200 {"materialised": N, "tasks_run": M}`.

Set `FLOWS_TICK_SECRET` in the Cloud Run env and configure a Cloud Scheduler job
that targets `https://<service>/api/v1/flows/tick` with that header on a `* * * *`
(or coarser) cron. Leave `FLOWS_SCHEDULER_ENABLED=false` so no background loop
also runs.

### Multi-instance-safe materialization (atomic claim)

When N concurrent Cloud Run instances tick simultaneously, a due scheduled flow
must materialize **exactly once** per schedule slot. `flow_tick` claims each due
flow atomically before materializing:

```sql
UPDATE flows
SET next_run_at = <next slot>, last_run_at = <now>, updated_at = now()
WHERE id = $1
  AND enabled = TRUE
  AND schedule IS NOT NULL
  AND (next_run_at IS NULL OR next_run_at <= <now>)
RETURNING *;
```

Only ONE instance's `UPDATE` matches the `next_run_at <= now` predicate and gets
a row back (the statement advances `next_run_at` in the same atomic operation);
the others get no row and skip the flow. This is `PgFlowStore.claim_due_scheduled_flow`.
The `InMemoryFlowStore` mirrors the semantics with a guarded re-check (it is
single-threaded, so there is no real contention).

**Task draining is already race-safe:** `claim_ready_task_run` uses
`UPDATE … WHERE id = (SELECT … FOR UPDATE SKIP LOCKED)`, so multiple instances
can drain ready task_runs concurrently without double-executing a task.

### Settings summary

| Setting | Default | Purpose |
|---|---|---|
| `FLOWS_SCHEDULER_ENABLED` | inherits | Enables the in-process worker loop (Mode 1). |
| `FLOWS_WORKER_INTERVAL_S` | `5` | Worker loop interval (Mode 1). |
| `FLOWS_TICK_SECRET` | `""` | Shared secret for `POST /flows/tick` (Mode 2). Empty → endpoint disabled. |
