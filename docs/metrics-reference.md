# Metrics / semantic layer — reference for agents

A concise, paste-into-context reference for an AI agent (or a human) authoring
against Nubi's **metrics / semantic layer**. For the design rationale see
[`METRICS_LAYER.md`](../METRICS_LAYER.md); the authoritative types live in
`backend/app/metrics/models.py`.

## What a metric is (governed definition vs raw query)

A **registered query** is reusable SQL: useful, but the *business logic* inside
it ("revenue", "active customer", "churn") is re-encoded per query, so two
dashboards can silently disagree.

A **metric** defines that logic ONCE — with an owner, a grain, the dimensions it
may be grouped by, and the RLS keys it must carry — and is **compiled to SQL on
demand**. An agent answers "what was revenue by region last month" from the
*governed definition*, not from freshly written SQL that merely passed syntax
validation. This is the layer that makes AI authoring **consistent**, not just
valid (the same idea as LookML, dbt metrics, and Cube).

When a metric exists for what you need, **prefer it over hand-writing SQL.**
Discover metrics via `GET /ai/context` (the `metrics[]` block) or the MCP
`list_metrics` tool.

## The `MetricDefinition` shape

A metric definition (`MetricDefinition` in `models.py`) carries:

| field            | meaning |
|------------------|---------|
| `id`             | stable, URL-safe identifier you reference in the query path |
| `name`           | human label |
| `measure`        | the quantity measured — a single `Measure` (below) |
| `base_table` / `base_sql` | exactly ONE source: a physical table OR a trusted SELECT used as a subquery |
| `datastore_id`   | optional datastore the metric compiles/executes against |
| `dimensions`     | the **allowed** grouping columns (`Dimension[]`) — you may group by NOTHING else |
| `time_dimension` | optional `TimeDimension` — the time column + the grains it can be bucketed to |
| `default_filters`| author-governed WHERE fragments inlined verbatim (trusted; never your input) |
| `rls_keys`       | columns that MUST survive into the grain so the planner's RLS predicate lands on a real column |
| `description`    | free-text description |
| `owner`, `required_scope` | governance metadata |
| `extra_measures` | additional measures requestable at the same grain (v1 callers usually use the single `measure`) |

### `Measure`
`{name, agg, expr, type, format}` — `name` is the output column,
`agg` ∈ `sum | count | count_distinct | min | max | avg`, `expr` is the column or
SQL expression aggregated (use `"*"` for `count`), `type` ∈
`additive | semi_additive | non_additive`, `format` is an optional display hint.
Example: `revenue = SUM(amount)` is `{name: "revenue", agg: "sum", expr: "amount"}`.

### `Dimension`
`{name, expr?, type}` — `name` is what you reference (and the output column);
`expr` defaults to a bare column named `name`; `type` ∈
`text | number | bool | date | timestamp`. **Only declared dimensions may be
grouped by or filtered on.**

### `TimeDimension`
`{column, grains, default_grain}` — `column` is the timestamp/date column to
bucket; `grains` is the allowed set (subset of
`hour | day | week | month | quarter | year`); `default_grain` is used when a
query omits `time_grain`.

## How to QUERY a metric

`POST /metrics/{id}/query` with a **`MetricQuery`** body
(`MetricQuery` in `models.py`):

```json
{
  "dimensions": ["region"],
  "time_grain": "month",
  "filters": [{ "field": "region", "op": "=", "value": "EMEA" }],
  "order_by": [["region", "asc"]],
  "limit": 100
}
```

- `dimensions` — a **subset** of the metric's allowed `dimensions`.
- `time_grain` — one of the metric's `time_dimension.grains` (requires the metric
  to declare a `time_dimension`). Omit it to use `default_grain` / no bucketing.
- `filters` — `MetricFilter[]`, each `{field, op, value}`. `field` must be an
  allowed dimension or the time column; `op` ∈
  `= | != | < | <= | > | >= | in | not_in` (`in`/`not_in` take a list `value`).
  `value` is bound as a query parameter — **never** concatenated into SQL.
- `order_by` — `[field, "asc"|"desc"]` entries.
- `limit` — optional row cap.

The response is Arrow rows, exactly like `POST /query` (cache + metering + rollup
routing + RLS all apply). For a **dry compile** (the SQL + params, no execution —
handy for debugging or introspection) use `POST /metrics/{id}/sql`.

> Note: `metric_id` comes from the URL path; when building a `MetricQuery` dict
> directly (e.g. via the MCP `query_metric` tool, which calls
> `MetricQuery.from_dict`) include `"metric_id"` in the dict.

## Governance rules (what gets rejected)

Compilation **governs** the request before any SQL runs. A request is rejected
with a `400` (`MetricError{code, message}`) when:

- you group by a dimension that is **not** in the metric's `dimensions`;
- you filter on a `field` that is **not** an allowed dimension or the time column;
- you ask for a `time_grain` that is **not** in `time_dimension.grains` (or you
  pass a `time_grain` when the metric has no `time_dimension`).

This is the point of the layer: an agent **cannot** ask for an arbitrary column —
it can only compose the metric's own governed vocabulary. `default_filters` and
`rls_keys` are enforced by the author/planner and are not under your control.

## Worked examples

### 1. Define a metric (author-side)

`revenue` = sum of `amount` from the `orders` table, groupable by `region` and
`status`, bucketable by month/quarter/year, RLS-scoped by `org_id`:

```json
{
  "id": "revenue",
  "name": "Revenue",
  "measure": { "name": "revenue", "agg": "sum", "expr": "amount", "format": "currency" },
  "base_table": "orders",
  "dimensions": [
    { "name": "region", "type": "text" },
    { "name": "status", "type": "text" }
  ],
  "time_dimension": { "column": "created_at", "grains": ["month", "quarter", "year"], "default_grain": "month" },
  "rls_keys": ["org_id"],
  "description": "Total order revenue (SUM of amount)."
}
```

### 2. Query it by region + month

`POST /metrics/revenue/query`:

```json
{ "dimensions": ["region"], "time_grain": "month" }
```

→ one row per `(region, month)` with a `revenue` column.

### 3. Filter it

`POST /metrics/revenue/query` — revenue for completed EMEA orders, by month:

```json
{
  "dimensions": ["region"],
  "time_grain": "month",
  "filters": [
    { "field": "region", "op": "=", "value": "EMEA" },
    { "field": "status", "op": "in", "value": ["completed", "settled"] }
  ]
}
```

### 4. A rejected (ungoverned) request

`POST /metrics/revenue/query`:

```json
{ "dimensions": ["customer_email"] }
```

→ `400` `MetricError`, because `customer_email` is not one of the metric's
declared `dimensions`. Re-issue using only allowed dimensions (`region`,
`status`) — or ask the metric's owner to add the dimension to the definition.
