# How-to guides

Practical, worked examples for every shipped surface. Use the table of
contents to jump to the feature you need.

---

## Contents

1. [Semantic metrics — define, query, extend](#1-semantic-metrics)
2. [Pre-aggregations — build and monitor rollups](#2-pre-aggregations)
3. [Flows — sweep, backfill, triggers](#3-flows-data-app-engine)
4. [DataProvider boards — per-board query fusion](#4-dataprovider-boards)

---

## 1. Semantic metrics

A **metric** is a governed business definition that compiles to SQL on demand.
Two dashboards built from the same metric can never silently disagree on the
definition of "revenue".

### 1.1 Define a metric (author side)

Register a metric via the API or the MCP `create_metric` tool. The body is a
`MetricDefinition` (see [API reference](/docs/api-reference#post-metrics)).

**Example — revenue metric on the `orders` table:**

```http
POST /api/v1/metrics
Authorization: Bearer <access-token>
Content-Type: application/json

{
  "name": "Revenue",
  "measure": {
    "name": "revenue",
    "agg": "sum",
    "expr": "amount",
    "type": "additive",
    "format": "currency"
  },
  "base_table": "orders",
  "dimensions": [
    { "name": "region", "type": "text" },
    { "name": "status", "type": "text" }
  ],
  "time_dimension": {
    "column": "created_at",
    "grains": ["day", "week", "month", "quarter", "year"],
    "default_grain": "month"
  },
  "rls_keys": ["org_id"],
  "description": "Total order revenue (SUM of amount)."
}
```

The response carries the canonical `id` (the metric slug, e.g. `"revenue"`).

### 1.2 Define a derived/ratio metric (fill rate)

`derived_measures` are post-aggregation arithmetic — never aggregated
themselves. The compiler wraps them in `NULLIF` guards automatically.

```json
{
  "name": "Promise vs Delivered",
  "measure": { "name": "ordered", "agg": "sum", "expr": "ordered_qty" },
  "extra_measures": [
    { "name": "delivered", "agg": "sum", "expr": "delivered_qty" }
  ],
  "derived_measures": [
    {
      "name": "fill_rate",
      "formula": "delivered / ordered",
      "format": "percent"
    }
  ],
  "base_table": "order_lines",
  "dimensions": [
    { "name": "region" },
    { "name": "product_category" }
  ],
  "time_dimension": {
    "column": "shipped_at",
    "grains": ["day", "week", "month"],
    "default_grain": "month"
  },
  "rls_keys": ["org_id"]
}
```

The compiler emits a layered CTE automatically:

```sql
WITH __base AS (
    SELECT region, product_category,
           DATE_TRUNC('month', shipped_at) AS shipped_at_month,
           SUM(ordered_qty)   AS ordered,
           SUM(delivered_qty) AS delivered
    FROM order_lines
    GROUP BY region, product_category, DATE_TRUNC('month', shipped_at)
)
SELECT region, product_category, shipped_at_month,
       ordered, delivered,
       delivered / NULLIF(ordered, 0) AS fill_rate
FROM __base
```

### 1.3 Query a metric — `MetricQuery` shapes

**Simple group + grain:**
```http
POST /api/v1/metrics/revenue/query
{ "dimensions": ["region"], "time_grain": "month" }
```

Returns one row per `(region, month)`.

**With filters:**
```json
{
  "dimensions": ["region"],
  "time_grain": "month",
  "filters": [
    { "field": "region", "op": "in", "value": ["EMEA", "APAC"] },
    { "field": "status", "op": "=", "value": "completed" }
  ],
  "order_by": [["month", "asc"]],
  "limit": 100
}
```

**With time intelligence — year-over-year percentage change:**
```json
{
  "dimensions": ["region"],
  "time_grain": "month",
  "time_comparisons": [
    { "measure": "revenue", "kind": "yoy_pct", "name": "revenue_yoy_pct" }
  ]
}
```

The response column `revenue_yoy_pct` contains `(current − prior_year) / NULLIF(prior_year, 0)`.

**Rolling 4-week average:**
```json
{
  "dimensions": ["channel"],
  "time_grain": "week",
  "time_comparisons": [
    { "measure": "revenue", "kind": "rolling_avg", "periods": 4, "name": "revenue_4w_avg" }
  ]
}
```

**Year-to-date cumulative:**
```json
{
  "dimensions": [],
  "time_grain": "month",
  "time_comparisons": [
    { "measure": "revenue", "kind": "ytd", "name": "revenue_ytd" }
  ]
}
```

**Dynamic top-5 products with Other bucket:**
```json
{
  "dimensions": ["product"],
  "time_grain": "month",
  "top_n": {
    "dimension": "product",
    "n": 5,
    "measure": "revenue",
    "other": true,
    "other_label": "Other products"
  }
}
```

The response contains exactly 6 rows per month: the top 5 by revenue and one
row labelled "Other products" aggregating all remaining members.

### 1.4 Dry-compile to inspect the SQL

Use `POST /metrics/{id}/sql` to see the compiled SQL without executing it:

```http
POST /api/v1/metrics/revenue/sql
{ "dimensions": ["region"], "time_grain": "month",
  "filters": [{ "field": "region", "op": "=", "value": "EMEA" }] }
```

Response:
```json
{
  "sql": "WITH __base AS (\n  SELECT region, DATE_TRUNC('month', created_at) AS created_at_month,\n         SUM(amount) AS revenue\n  FROM orders\n  WHERE region = $1\n  GROUP BY region, DATE_TRUNC('month', created_at)\n)\nSELECT region, created_at_month, revenue FROM __base",
  "params": { "p1": "EMEA" }
}
```

### 1.5 What gets rejected (governance)

The compiler enforces governance before any SQL runs:

| What you asked | Error |
|---|---|
| Group by a column not in `dimensions` | `400 MetricError` — dimension not allowed |
| Filter on a column not in `dimensions` or the time column | `400 MetricError` — filter field not allowed |
| Use a `time_grain` not in `time_dimension.grains` | `400 MetricError` — grain not allowed |
| Pass a `time_grain` when no `time_dimension` declared | `400 MetricError` — metric has no time dimension |

These rules prevent agents and dashboards from leaking PII or constructing
ungoverned queries.

---

## 2. Pre-aggregations

Pre-aggregations are materialized rollup tables built from your query log.

### 2.1 Open the Rollups panel

1. Go to **Queries** in the sidebar.
2. Click the **Rollups** toggle in the segmented control (the other option is
   **Editor**).
3. You'll see two lists: **Suggested rollups** (candidates mined from the log)
   and **Active rollups** (already built).

The miner needs at least 3 identical query patterns before a suggestion
appears. Run the same aggregating query a few times if the list is empty.

### 2.2 Read a suggestion card

Each suggested rollup shows:

- **Table name** — the base fact table.
- **score** — `hits × estimated_bytes_scanned` (higher = higher ROI).
- **group by** chips — the `GROUP BY` columns.
- **measures** chips — the aggregates to materialize.
- **filters** chips — columns seen in `WHERE` clauses across clustered queries.

### 2.3 Build a rollup

Click **Build** on a suggestion card. The button shows **Building…** then
flips to **Built**. The rollup appears in **Active rollups** immediately.

Future matching queries are routed to the rollup transparently — you change
nothing in your SQL, dashboards, or embeds.

### 2.4 Build a metric-driven rollup via the API

For a rollup shaped to serve a specific metric (including derived and windowed
queries), use `build_rollup_for_metric`. This is the smart-engine path:

```python
from app.connectors.preagg import build_rollup_for_metric

built = build_rollup_for_metric(
    metric=pvd_metric,          # MetricDefinition
    grains=["month"],           # include the raw time column
    source_database="/data/orders.duckdb",
    rollup_id="pvd_monthly",
)
# built.rollup_id, built.rollup_table, built.datastore_id
```

The rollup mirrors the `__base` CTE of the metric compiler (additive base
measures only — AVG and percentile are skipped since they can't be
re-aggregated). The router proves the rollup covers `__base`, then rewrites
the full layered query to read from the rollup. The outer derived/window
layer runs unchanged on top.

### 2.5 Monitor rollup performance

Check the **hits** count on each Active rollup card — this is the number of
queries routed to that rollup. Click **Refresh** to pull the latest counts.

- Rising hits: the rollup is earning its keep.
- Zero/flat hits: the matching queries may have stopped or drifted.

### 2.6 Keep rollups fresh

A rollup is a snapshot at build time. To stay current:

**Manual:** Click **Refresh**, then **Build** on the suggestion when its
shape resurfaces.

**Scheduled:** Create a `preagg_refresh` flow that runs on a cron schedule:

```json
{
  "name": "preagg_refresh_hourly",
  "spec": {
    "version": 1,
    "tasks": [{ "key": "refresh", "kind": "python",
                "code": "from app.connectors.preagg import mine_and_build_all\nresult = mine_and_build_all()\nresult = result" }]
  },
  "schedule": "0 * * * *"
}
```

The mine-and-build process is idempotent: already-built rollups with
identical dimension sets are skipped.

---

## 3. Flows data-app engine

Flows runs cell-based DAGs on a schedule, trigger, or on demand. The
data-app extensions add compute resources, artifact storage, sweep/backfill,
and triggers.

### 3.1 Per-cell resource requests

Each task in a `FlowSpec` can declare its compute needs:

```json
{
  "key": "train_model",
  "kind": "python",
  "code": "import sklearn\n# ...\nresult = {'model_handle': handle}",
  "cpu_cores": 2.0,
  "mem_mb": 4096,
  "timeout_s": 300,
  "stochastic": false
}
```

| Field | Default | Description |
|---|---|---|
| `cpu_cores` | 0 (provider default) | Fractional CPU cores forwarded to E2B / Modal. |
| `mem_mb` | 0 (provider default) | Memory in MiB. |
| `timeout_s` | 0 (no limit) | Per-attempt wall-clock limit. SIGKILL on local runner; microVM-level on remote. |
| `stochastic` | `false` | When `true`: per-run `__seed__` injected into cell namespace; cache bypassed. |

### 3.2 Artifact channel — share large objects between cells

Use `ctx.put_artifact` / `ctx.get_artifact` for objects too large for the
JSON rows channel (models, binary blobs):

```python
# Cell A — train_model
import joblib
model = train(df)
handle = ctx.put_artifact(model, kind="joblib", name="churn_model_v1")
result = {"model_handle": handle}

# Cell B — score_customers
handle = inputs["train_model"]["model_handle"]
model = ctx.get_artifact(handle)   # org_id enforced — no cross-tenant reads
preds = model.predict(new_df)
result = {"predictions": preds.tolist()}
```

Supported artifact kinds: `pickle`, `joblib`, `bytes`, `json`.

### 3.3 Scenario sweep

Run one flow per param combination and compare outputs side by side:

```http
POST /api/v1/flows/{flow_id}/sweep
{
  "grid": {
    "region": ["EMEA", "APAC", "AMER"],
    "model_version": ["v1", "v2"]
  },
  "max_cells": 6
}
```

The grid is expanded to the full Cartesian product (3 × 2 = 6 cells). Each
cell is a real `flow_run` with its own `run_id`, `seed`, and
`params_snapshot`.

**Explicit param list instead of grid:**
```json
{
  "param_sets": [
    { "region": "EMEA", "threshold": 0.7 },
    { "region": "APAC", "threshold": 0.8 }
  ]
}
```

**Response contains `diff_surface`** — each cell's params plus outputs,
keyed for side-by-side comparison.

### 3.4 Backfill — re-run over a historical date range

```http
POST /api/v1/flows/{flow_id}/backfill
{
  "start": "2026-01-01T00:00:00Z",
  "end":   "2026-06-01T00:00:00Z",
  "window": "7d",
  "params": { "region": "EMEA" }
}
```

One flow run per week from Jan to Jun 2026. Each run gets `__backfill_id__`
and the window start/end in its `params_snapshot`.

### 3.5 Event and webhook triggers

**Register a trigger:**
```http
POST /api/v1/flows/triggers
{
  "flow_id": "flow-uuid",
  "kind": "webhook",
  "source": "shopify_order_paid",
  "secret": "hmac-signing-secret",
  "enabled": true
}
```

Nubi returns a `trigger.id`. External systems POST to:
```
POST /api/v1/flows/triggers/webhook/<trigger.id>
X-Nubi-Signature: sha256=<hmac>
{ "order_id": "12345", "amount": 99.99 }
```

The HMAC header is verified before the flow is triggered.

**Register a downstream trigger** (flow B runs when flow A completes):
```json
{
  "flow_id": "flow-b-uuid",
  "kind": "downstream",
  "source": "flow-a-uuid",
  "enabled": true
}
```

**Fire a named event:**
```http
POST /api/v1/flows/triggers/fire
{ "event_key": "order.completed", "params": { "order_id": "12345" } }
```

---

## 4. DataProvider boards

A `DataProvider` in a `DashboardSpec` declares multiple result queries sharing
a base CTE. The resolver runs them in one round-trip and caches by
`(provider_id, params, rls_hash)`.

### 4.1 Declare a DataProvider in a DashboardSpec

```yaml
version: 1
title: Revenue Dashboard
providers:
  - id: revenue_provider
    base_cte: |
      SELECT region,
             DATE_TRUNC('month', created_at) AS month,
             SUM(amount) AS revenue
      FROM orders
      GROUP BY region, month
    results:
      - name: by_region
        sql: SELECT region, SUM(revenue) AS total FROM revenue_provider GROUP BY region
      - name: by_month
        sql: SELECT month, SUM(revenue) AS total FROM revenue_provider GROUP BY month
      - name: grand_total
        sql: SELECT SUM(revenue) AS revenue FROM revenue_provider
```

### 4.2 Fetch provider data

```http
POST /api/v1/boards/{board_id}/providers/revenue_provider/data
Authorization: Bearer <token>
Content-Type: application/json

{ "params": { "region": "EMEA" } }
```

Returns a multi-table Arrow IPC frame (see [API reference](/docs/api-reference#dataprovider-boards)).

**Parse in JavaScript:**
```js
import { parseMultiTableIPC } from '@nubi/sdk'

const resp = await fetch(`/api/v1/boards/${boardId}/providers/revenue_provider/data`, {
  method: 'POST',
  headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
  body: JSON.stringify({ params: { region: 'EMEA' } }),
})
const tables = await parseMultiTableIPC(await resp.arrayBuffer())
// tables.by_region, tables.by_month, tables.grand_total
```

### 4.3 Cache behaviour

The cache key is `(provider_id, frozen_params, rls_hash)` where
`rls_hash = sha256(json(policies))[:16]`. Two tenants with identical params
never share a cache entry (the RLS hash diverges). The cache TTL is 5 minutes.
