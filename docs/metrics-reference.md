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

---

## KPI targets

A metric may declare an optional **`target`** field (`MetricTarget` in `models.py`).
When present the compiler emits four extra columns in every query result:

| Column | Meaning |
|--------|---------|
| `<measure>_target` | The target value (constant literal or a trusted author SQL expression) |
| `<measure>_vs_target` | `actual - target` signed delta |
| `<measure>_pct_to_goal` | `actual / target` as a ratio (1.0 = 100 %) |
| `<measure>_rag` | RAG status: `"green"` / `"amber"` / `"red"` |

For example, if the primary measure is `revenue` the extra columns are
`revenue_target`, `revenue_vs_target`, `revenue_pct_to_goal`, `revenue_rag`.

### `MetricTarget` shape

| Field | Type | Default | Meaning |
|-------|------|---------|---------|
| `value` | string | required | Target magnitude: a numeric literal (`"1000000"`) or a trusted author-governed SQL expression (e.g. a column name). Stored as a string; both forms are valid. |
| `direction` | `"higher_is_better"` \| `"lower_is_better"` | `"higher_is_better"` | Whether a higher or lower actual value is better. |
| `amber_threshold` | float | `0.8` | Fraction that enters the amber zone. For `higher_is_better`: amber when `actual / target >= amber_threshold` but `< 1.0`. For `lower_is_better`: amber when `actual / target <= 1 / amber_threshold` but `> 1.0`. |
| `measure` | string \| null | null (→ primary measure) | Which base measure this target applies to. Must be a declared base measure of the metric. |

### RAG semantics

**`higher_is_better`** (default):
- `green` when `actual >= target`
- `amber` when `actual >= target * amber_threshold` (but `< target`)
- `red` otherwise

**`lower_is_better`**:
- `green` when `actual <= target`
- `amber` when `actual <= target / amber_threshold` (but `> target`)
- `red` otherwise

### Example definition with a target

```json
{
  "id": "revenue",
  "name": "Revenue",
  "measure": { "name": "revenue", "agg": "sum", "expr": "amount", "format": "currency" },
  "base_table": "orders",
  "dimensions": [{ "name": "region", "type": "text" }],
  "time_dimension": { "column": "created_at", "grains": ["month", "year"], "default_grain": "month" },
  "target": {
    "value": "1000000",
    "direction": "higher_is_better",
    "amber_threshold": 0.8
  }
}
```

Querying this metric returns `revenue`, `revenue_target` (always `1000000`),
`revenue_vs_target`, `revenue_pct_to_goal`, and `revenue_rag` per row.

When a metric has no `target` field the output is identical to pre-target
behaviour — no extra columns are emitted.

---

## Contribution analysis: `POST /metrics/{id}/explain`

Root-cause analysis that explains WHY the metric changed between two time
windows by computing per-dimension member delta contributions.

### Request body

```json
{
  "current":    { "start": "2024-02-01T00:00:00", "end": "2024-03-01T00:00:00" },
  "comparison": { "start": "2024-01-01T00:00:00", "end": "2024-02-01T00:00:00" },
  "dimensions": ["region", "status"],
  "top_n": 10,
  "include_summary": false
}
```

| Field | Type | Default | Meaning |
|-------|------|---------|---------|
| `current` | `TimeWindow` | required | `{start, end}` ISO datetimes for the current period (half-open `[start, end)`) |
| `comparison` | `TimeWindow` | required | `{start, end}` for the comparison period |
| `dimensions` | `string[]` \| null | null → all allowed dims | Subset of the metric's declared dimensions to analyze |
| `top_n` | int (1–50) | `10` | Max member rows per dimension before collapsing into an "Other" bucket |
| `include_summary` | bool | `false` | When `true`, requests a 1–2 sentence natural-language summary from the AI provider |

**Requires**: the metric must declare a `time_dimension`; otherwise returns `400 no_time_dimension`.

### Response shape

```json
{
  "metric_id": "revenue",
  "measure": "revenue",
  "delta_total": 12500.0,
  "current_total": 87500.0,
  "comparison_total": 75000.0,
  "dimensions": [
    {
      "dimension": "region",
      "members": [
        {
          "member": "North",
          "current": 42000.0,
          "comparison": 34000.0,
          "delta": 8000.0,
          "share": 0.64,
          "direction": "up"
        }
      ],
      "other": null,
      "coverage": 1.0,
      "explanatory_power": 1.0
    }
  ],
  "summary": null
}
```

| Field | Meaning |
|-------|---------|
| `delta_total` | `current_total - comparison_total` |
| `dimensions[]` | Sorted by `explanatory_power` descending |
| `dimensions[].members[]` | Top-N members by `abs(delta)`, descending |
| `members[].member` | The dimension member value (`str`, `int`, or `null`) |
| `members[].current` / `comparison` | Measure total for that member in each period (`null` if absent) |
| `members[].delta` | `current - comparison` (using 0 for absent periods) |
| `members[].share` | `delta / abs(delta_total)` (0 when `delta_total ≈ 0`) |
| `members[].direction` | `"up"` / `"down"` / `"flat"` |
| `dimensions[].other` | Aggregated tail beyond `top_n`, or `null` if no tail. Same shape as a member, with `member = "Other"`. |
| `dimensions[].coverage` | `sum(|top_n delta|) / sum(|all delta|)` — fraction of total movement explained by the shown members |
| `dimensions[].explanatory_power` | `sum(|top_n delta|) / |delta_total|`, capped at 1.0 — used for dimension ranking |
| `summary` | NL explanation (non-null only when `include_summary=true` and an AI provider is configured) |

### Governance errors

| HTTP | `code` | Cause |
|------|--------|-------|
| 400 | `unknown_dimension` | A name in `dimensions` is not declared on the metric |
| 400 | `no_time_dimension` | The metric has no `time_dimension`; period comparison requires one |
| 404 | (org scope) | Metric exists in another org (same 404 as a missing metric — no cross-org existence leak) |

---

## Metric lineage — `GET /metrics/{id}/lineage`

Returns the full input-column lineage for a metric: which physical tables and
columns feed the measure, plus any derived measure formulas.

See [docs/lineage.md](lineage.md) for the complete lineage API reference
including `GET /lineage/dag`, `GET /lineage/dag/{node_id}?hops=`, and
`GET /lineage/query/{id}`.
