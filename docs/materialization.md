# Materialization — named managed tables for flows

This page documents the **named managed table** pattern: a Flow that computes
a projection and writes the result to a persistent, queryable Parquet table
that downstream metrics and registered queries can SELECT from — without the
host needing a warehouse of their own.

This is the recommended data-plane path for hosts like KeyOne that have no
BYO warehouse.

> For the general blend (multi-source materialize-then-serve) pattern see
> [Flows](/docs/flows.md) and the API reference.  The incremental environments
> and watermark semantics are described in
> [flows-v3-incremental-environments-design.md](/docs/flows-v3-incremental-environments-design.md).

---

## What is a named managed table?

A **named managed table** is a Parquet file written by a Flow's `materialize`
cell and immediately registered in Nubi's runtime query registry.  Any
registered query or metric that binds to the matching `query_id` can then
`SELECT` from it without a server restart — exactly as if the data lived in
a warehouse.

The key properties:

| Property | Value |
|----------|-------|
| **Storage** | Local Parquet (dev/test) or object storage (`s3://`, MinIO, R2) |
| **Refresh** | Scheduled flow run (`@hourly`, `@daily`, …) — cost paid once, not per view |
| **Query path** | Standard query registry → `SELECT * FROM "<table>"` → `read_parquet(…)` |
| **RLS** | `rls_keys` columns survive the materialize step; the planner injects `WHERE <key> = <claim>` at read time |
| **Incremental** | Optional watermark-based incremental append (only new rows processed per run) |

---

## The data-plane gap this fills

Nubi's default "live federation" path requires a warehouse connector on every
query.  Hosts without their own warehouse — or hosts that want materialize-
then-serve economics (query cost paid once per refresh, not per viewer) — need
a different target.

The `materialize` cell with `kind='full'` or `kind='incremental'` writes the
computed result to a Parquet file.  After writing, `register_parquet_query`
wires the Parquet URI into the runtime query registry so any downstream
consumer can read it by `query_id`.  The datastore row (type `duckdb`,
`database: ":memory:"`, `view_sql: "CREATE VIEW … AS SELECT * FROM
read_parquet('…')"`) is the bridge between the Parquet file and the query
planner.

---

## End-to-end recipe

### Step 1 — define the projection flow

Create a flow with two tasks: a SQL (or Python) cell that computes the data
and a `materialize` cell that writes it to the managed table.

```python
from app.flows.spec import validate_flow_spec, flow_spec_is_valid

# IDs you pre-create (or receive from POST /datastores and POST /queries):
DATASTORE_ID = "<uuid of the duckdb datastore row>"
QUERY_ID     = "<uuid of the queries row>"
BASE_URI     = "/var/nubi/managed"   # or "s3://your-bucket/managed" in prod

spec_data = {
    "version": 1,
    "name": "category_projection",
    "tasks": [
        {
            "key": "pull",
            "kind": "query",
            "needs": [],
            "config": {
                "sql": "SELECT category, SUM(amount) AS total FROM orders GROUP BY category",
                "datastore_id": "<source datastore id>",
            },
        },
        {
            "key": "mat",
            "kind": "materialize",
            "needs": ["pull"],
            "config": {
                # Merge SQL — here just pass the upstream through.
                "combine_sql": "SELECT * FROM pull",
                "sources": ["pull"],
                # RLS: keep any column you want to filter at read time.
                "rls_keys": [],
                # Logical name of the managed table (queryable via SELECT * FROM "category_totals").
                "table": "category_totals",
                # Bind to the pre-created datastore + query rows.
                "datastore_id": DATASTORE_ID,
                "query_id": QUERY_ID,
                # Persistence: 'full' overwrites every run; 'incremental' appends.
                "materialized": {
                    "kind": "full",          # or "incremental" (add time_column then)
                    "target": "category_totals",  # Parquet file name under base_uri/env/
                    "base_uri": BASE_URI,
                },
            },
        },
    ],
}

spec, issues = validate_flow_spec(spec_data)
assert flow_spec_is_valid(issues), issues
```

### Step 2 — run the flow

```python
from datetime import datetime, timezone
from app.flows.store import InMemoryFlowStore  # or the Postgres store in prod
from app.flows.runtime import materialize_flow_run, drain_flow_run

store = InMemoryFlowStore()
flow = await store.create_flow(
    org_id="<org-id>",
    created_by="<user-id>",
    name="category_projection",
    spec=spec_data,
    enabled=True,
    schedule="@hourly",   # refresh every hour
)

now = datetime.now(timezone.utc)
run = await materialize_flow_run(store, flow, {}, "manual", now, env="prod")
result = await drain_flow_run(store, run["id"], now)
assert result["state"] == "success"
```

After the run completes:

- The Parquet file exists at `<BASE_URI>/prod/category_totals.parquet`.
- `QUERY_ID` is registered in the runtime query registry pointing at the
  Parquet file via the datastore row's `view_sql`.
- Any widget, metric, or API call that references `QUERY_ID` will execute
  `SELECT * FROM "category_totals"` against the managed Parquet connector.

### Step 3 — read the managed table

#### Via the query API (standard path)

```bash
curl -X POST https://api.example.com/api/v1/query \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"query_id": "<QUERY_ID>"}'
```

Returns Arrow IPC (the same format as any other registered query).

#### Via a registered metric

Declare a metric that references the managed table via a datastore-scoped
registered query:

```json
{
  "id": "category_revenue",
  "label": "Revenue by category",
  "query_id": "<QUERY_ID>",
  "measure": "total",
  "dimensions": ["category"]
}
```

When the metric is queried the planner resolves `QUERY_ID` → the managed
Parquet connector → `SELECT total, category FROM "category_totals"`.

#### Via a dashboard widget

Bind a widget to `query_id: "<QUERY_ID>"` in the DashboardSpec.  The widget
reads the managed table on every dashboard load; because the data is already
materialized the read is a fast local-Parquet scan, not a live warehouse query.

### Step 4 — watch the metric (optional)

Create an alert or watch on the managed metric to be notified when values
cross a threshold:

```json
POST /api/v1/metrics/<id>/watch
{
  "condition": "total > 10000",
  "channel": "slack"
}
```

---

## Full-refresh for full-partition aggregates

When a materialized column is a **full-partition aggregate** — i.e. it depends
on ALL rows in the dataset, not just the rows newer than a watermark — you
**must** use `materialized.kind: "full"`, not `incremental`.

### Why incremental would be wrong

`kind: "incremental"` processes only rows with `time_column > watermark`.
If the aggregate is something like `SUM(amount) OVER ()` (a cross-partition
total) or `MAX(total_for_date)` (a daily constant that is derived from the
entire day's data), an incremental run will see only new rows and produce a
stale denominator for every group that has already been processed.

### Flow YAML snippet — full-refresh pattern

```yaml
# flows/category_summary/flow.toml  (relevant excerpt)
[[tasks]]
key    = "pull"
kind   = "query"
needs  = []

[tasks.config]
sql           = """
  SELECT category,
         SUM(amount)                        AS total_for_category,
         SUM(SUM(amount)) OVER ()           AS grand_total   -- full-partition aggregate
  FROM   orders
  GROUP BY category
"""
datastore_id = "<source-datastore-id>"

[[tasks]]
key   = "mat"
kind  = "materialize"
needs = ["pull"]

[tasks.config]
combine_sql  = "SELECT * FROM pull"
sources      = ["pull"]
rls_keys     = []
table        = "category_summary"
datastore_id = "<duckdb-datastore-id>"
query_id     = "<query-id>"

[tasks.config.materialized]
kind     = "full"          # NOT incremental — grand_total depends on all rows
target   = "category_summary"
base_uri = "/var/nubi/managed"
```

With `kind: "full"` the runtime overwrites the Parquet file completely on
every run, so `grand_total` is always computed over the current full dataset.

> **Decision rule:** does the aggregate make sense if you compute it on only
> the rows added since the last run? If no — use `kind: "full"`.

---

## Incremental refresh (time-series data)

For event streams where you only want to process new rows each run, use
`kind='incremental'` with a `time_column`:

```python
"materialized": {
    "kind": "incremental",
    "target": "events_projection",
    "time_column": "occurred_at",   # column used for the watermark cutoff
    "base_uri": BASE_URI,
    # Optional: lookback window to reprocess near the watermark boundary.
    "lookback": "1 hour",
    # Optional: unique key for upsert semantics (delete-then-insert on match).
    "unique_key": ["event_id"],
},
```

The watermark (ISO timestamp of the last `time_column` value processed) is
stored in `flow_watermarks` by the runtime and passed back on each run.  Only
rows with `time_column > watermark - lookback` are processed per run, keeping
warehouse cost linear in new data volume rather than total table size.

---

## How the wiring works (internals)

```
materialize_blend(config, inputs, *, env, flow, watermark)
  ├── merge sources in DuckDB in-memory
  ├── apply_incremental(storage, combined, materialized, watermark, now)
  │     └── writes Parquet to physical_target
  └── register_parquet_query(query_id, physical_target, table, datastore_id)
        └── get_query_registry().register(query_id, sql, name, datastore_id=...)

At read time (POST /api/v1/query):
  resolve query_id → RegisteredQuery(sql, datastore_id)
  fetch datastores row for datastore_id
    cfg = {connector_type: "duckdb", database: ":memory:",
           view_sql: "CREATE VIEW \"<table>\" AS SELECT * FROM read_parquet('<path>')"}
  open in-memory DuckDB, execute view_sql, execute plan.sql
  return Arrow IPC
```

The datastore row's `view_sql` must point at the Parquet `physical_target`.
When the row is pre-created (before the first run) set `database: ":memory:"`
and `view_sql` to the expected path; the runtime will register the query
pointing at the actual written path after each run.

---

## Comparison to the blend (multi-source) path

| | Blend | Named managed table |
|---|---|---|
| **Sources** | N sources joined by `combine_sql` | Any single SQL/Python output |
| **Storage kind** | Local DuckDB file (`view`) or Parquet (`full`/`incremental`) | Parquet only (`full` or `incremental`) |
| **Registration** | `register_blend_query` (DuckDB) / `register_parquet_query` (Parquet) | `register_parquet_query` |
| **RLS** | `rls_keys` verified on every run | Same |
| **Watermark** | Per-task, per-env, in `flow_watermarks` | Same |
| **Use case** | Multi-source federation materialized for dashboards | Single-source projection for hosts with no warehouse |

---

## Security notes

- `combine_sql` is author-provided (first-party, org-scoped) SQL.  It is NOT
  end-user input.
- `rls_keys` columns MUST survive the materialize step.  The engine verifies
  this and raises `rls_key_dropped` (HTTP 400) if a declared key is missing
  from the combined output.
- The Parquet path is server-pinned under `<base_uri>/<env>/<target>.parquet`;
  no user-controlled path component is accepted.
- At read time the DuckDB connection is opened in-memory (`database: ":memory:"`);
  `disable_external_access` is not set for the Parquet path (it needs FS
  reads), but `block_local_fs` is set for S3-only targets so a rogue
  `read_parquet('/etc/passwd')` is blocked.
