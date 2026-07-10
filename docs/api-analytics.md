# Metrics & data

> Part of the [API reference](/docs/api-reference) — see it for conventions, error codes, and the audit log.

## Metrics (semantic layer)

### `GET /metrics`

List metrics visible to the caller's org.

**Auth:** Any valid token with a read scope.

**Response `200`:**
```json
{
  "metrics": [
    {
      "id": "revenue",
      "name": "Revenue",
      "measure": { "name": "revenue", "agg": "sum", "expr": "amount", "type": "additive", "format": "currency" },
      "dimensions": ["region", "status"],
      "time_grains": ["month", "quarter", "year"],
      "description": "Total order revenue."
    }
  ]
}
```

---

### `GET /metrics/{id}`

Return the full `MetricDefinition` for one metric.

**Auth:** Read scope. Org-scoped — a slug only resolves within the caller's
org (or an in-code seed). Cross-org attempts return 404.

**Path parameters:**

| Param | Type | Description |
|---|---|---|
| `id` | string | The metric slug (stable URL-safe identifier). |

**Response `200`:** Full `MetricDefinition` dict (all fields including `base_sql`,
`dimensions`, `time_dimension`, `rls_keys`, `derived_measures`, `extra_measures`).

**Errors:**
- `404 metric_not_found` — unknown metric or belongs to another org.

---

### `POST /metrics`

Create and register a governed metric.

**Auth:** First-party access token only (embed tokens are blocked). Writer
role.

**Request body (`MetricIn`):**
```json
{
  "name": "Revenue",
  "measure": { "name": "revenue", "agg": "sum", "expr": "amount", "format": "currency" },
  "base_table": "orders",
  "dimensions": [
    { "name": "region", "type": "text" },
    { "name": "status", "type": "text" }
  ],
  "time_dimension": {
    "column": "created_at",
    "grains": ["month", "quarter", "year"],
    "default_grain": "month"
  },
  "rls_keys": ["org_id"],
  "description": "Total order revenue (SUM of amount)."
}
```

`base_table` and `base_sql` are mutually exclusive; exactly one must be
provided. For `agg` other than `count`, `expr` must be a real column or
expression (`*` is only valid for `count`).

**Response `201`:** Full `MetricDefinition` dict with the canonical `id` assigned.

**Errors:**
- `400 invalid_definition` — malformed metric body.
- `400 no_source` / `400 ambiguous_source` — both or neither source specified.
- `400 invalid_measure` — missing name or bad expr/agg combination.
- `403 forbidden` — embed token.

---

### `PUT /metrics/{id}`

Update an existing metric definition.

**Auth:** First-party access token, writer role.

**Request body:** Same as `POST /metrics`. The `id` from the path overrides
any `id` in the body.

**Response `200`:** Updated `MetricDefinition`.

---

### `DELETE /metrics/{id}`

Unregister a metric. Clears the `metric` block from the backing query row
without deleting the query itself (the SQL survives as a plain query).

**Auth:** First-party access token, writer role.

**Response `200`:**
```json
{ "id": "revenue", "deleted": true }
```

---

### `POST /metrics/{id}/query`

Compile a governed metric query and execute it. Returns Arrow IPC exactly
like `POST /query`. Cache, RLS, metering, and rollup routing all apply.

**Auth:** Any valid token with a read scope. Embed-safe (no raw SQL accepted).
Viewers are not metered.

**Path parameters:** `id` — metric slug.

**Request body (`MetricQuery`):**
```json
{
  "dimensions": ["region"],
  "time_grain": "month",
  "filters": [
    { "field": "region", "op": "=", "value": "EMEA" },
    { "field": "status", "op": "in", "value": ["completed", "settled"] }
  ],
  "order_by": [["month", "asc"]],
  "limit": 500,
  "time_comparisons": [
    { "measure": "revenue", "kind": "yoy_pct", "name": "revenue_yoy" }
  ],
  "top_n": {
    "dimension": "region",
    "n": 5,
    "measure": "revenue",
    "other": true,
    "other_label": "Other regions"
  }
}
```

All fields except `dimensions` are optional.

| Field | Description |
|---|---|
| `dimensions` | Subset of the metric's declared dimensions. |
| `time_grain` | One of the metric's `time_dimension.grains`. Omit to skip time bucketing. |
| `filters` | `[{field, op, value}]` — `field` must be an allowed dimension or the time column; `op` ∈ `= \| != \| < \| <= \| > \| >= \| in \| not_in`. |
| `order_by` | `[["column_name", "asc"\|"desc"], …]` |
| `limit` | Integer row cap. |
| `time_comparisons` | Time-intelligence windows (see below). |
| `top_n` | Dynamic top-N with optional Other bucket. |

**`time_comparison` kinds:**

| `kind` | Description |
|---|---|
| `prior_period` | LAG(measure, N) — value N buckets ago |
| `pop_abs` / `pop_pct` | Period-over-period absolute / % change |
| `prior_year` | Same bucket one year prior |
| `yoy_abs` / `yoy_pct` | Year-over-year change |
| `ytd` / `qtd` / `mtd` | Cumulative sum since year/quarter/month start |
| `rolling_sum` / `rolling_avg` | Trailing N-period sum / average (requires `periods`) |
| `latest_snapshot` | QUALIFY deduplication — one row per entity |

**Response `200`:** `Content-Type: application/vnd.apache.arrow.stream`

Arrow IPC byte stream. Headers:
- `X-Nubi-Cache: HIT | MISS`

**Errors:**
- `400 MetricError` — governance violation (unknown dimension, bad grain, bad
  filter field).
- `404 metric_not_found` — unknown or wrong-org metric.
- `402 quota_exceeded` — compute quota exhausted.
- `501` — connector does not support RLS predicate injection.

---

### `POST /metrics/{id}/sql`

Dry compile — returns the SQL and bound params that would run, without
executing. Useful for debugging, introspection, and agent grounding.

**Auth:** Read scope. Org-scoped.

**Request body:** Same as `POST /metrics/{id}/query`.

**Response `200`:**
```json
{
  "sql": "WITH __base AS (\n  SELECT region, DATE_TRUNC('month', created_at) AS created_at_month, SUM(amount) AS revenue\n  FROM orders\n  WHERE region = $1\n  GROUP BY region, DATE_TRUNC('month', created_at)\n)\nSELECT region, created_at_month, revenue FROM __base ORDER BY created_at_month ASC",
  "params": { "p1": "EMEA" }
}
```

**Errors:** Same governance errors as `/query`.

---

### `GET /metrics/{id}/versions`

List all spec versions for a metric, newest first. Specs are omitted from the
list for compactness — use the single-version endpoint to fetch a full spec.

**Auth:** Read scope. Org-scoped (cross-org → 404).

**Response `200`:**
```json
{
  "metric_id": "revenue",
  "versions": [
    { "id": "uuid", "version": 2, "created_by": "uuid", "created_at": "2026-06-26T10:00:00+00:00", "note": null },
    { "id": "uuid", "version": 1, "created_by": "uuid", "created_at": "2026-06-25T09:00:00+00:00", "note": null }
  ]
}
```

---

### `GET /metrics/{id}/versions/{v}`

Fetch the full spec snapshot at version `v`.

**Auth:** Read scope. Org-scoped (cross-org or unknown version → 404).

**Response `200`:**
```json
{
  "id": "uuid",
  "metric_id": "revenue",
  "org_id": "uuid",
  "version": 1,
  "spec": { "id": "revenue", "name": "Revenue", "measure": { "name": "revenue", "agg": "sum", "expr": "amount" }, "base_table": "orders" },
  "created_by": "uuid",
  "created_at": "2026-06-25T09:00:00+00:00",
  "note": null
}
```

---

### `POST /metrics/{id}/revert/{v}`

Revert a metric's live spec to version `v`. The reverted spec is immediately
live (re-registered and re-persisted) and recorded as a new version entry so
the revert is auditable.

**Auth:** `author:metric` scope required. Org-scoped (cross-org → 404).

**Response `200`:** Full `MetricDefinition.to_dict()` of the reverted metric.

**Errors:**
- `404` — unknown metric, cross-org access, or unknown version number.
- `403` — token does not carry `author:metric`.

---

## DataProvider boards

### `POST /boards/{board_id}/providers/{provider_id}/data`

Resolve a `DataProvider` declared in a board's `DashboardSpec` and return
the named result-sets as a multi-table Arrow IPC frame.

**Auth:** Any valid token (first-party or embed JWT) with a read scope. RLS
comes from the verified token `policies` claim only.

**Path parameters:**

| Param | Description |
|---|---|
| `board_id` | UUID of the board (org-scoped). |
| `provider_id` | The `id` of the `DataProvider` declared in `spec.providers`. |

**Request body:**
```json
{ "params": { "region": "EMEA", "date_range": "2026-Q1" } }
```

**Response `200`:** `Content-Type: application/vnd.apache.arrow.stream`

A binary multi-table IPC frame:
```
4-byte big-endian table count N
then N frames, each:
  4-byte big-endian name length
  UTF-8 name bytes
  4-byte big-endian IPC stream length
  Arrow IPC stream bytes
```

Cache key: `(provider_id, frozen_params, rls_hash)` where
`rls_hash = sha256(json(policies))[:16]`. Different tenants never share a
cache entry.

**Errors:**
- `404` — board or provider not found.
- `501` — connector does not support RLS.

---

## Data Health

All data-health endpoints require a valid Bearer token. Routes are org-scoped:
a dataset key belonging to another org returns `404` (not `403`) to prevent
information leakage.

### `GET /health/freshness`

Return the pre-computed freshness row for every dataset in the caller's org.
Single indexed scan on `org_id` — no live computation.

**Auth:** Any valid token (first-party or embed JWT with a read scope).

**Response `200`:**
```json
{
  "org_id": "org-uuid",
  "datasets": [
    {
      "dataset_key": "raw/orders",
      "status": "fresh",
      "last_success_at": "2026-06-26T05:00:00Z",
      "expected_interval_s": 86400,
      "stale_at": "2026-06-27T05:00:00Z"
    }
  ]
}
```

**Errors:** `404` — no freshness records for the org.

---

### `GET /health/freshness/{dataset_key}`

Return the pre-computed freshness row for one dataset. Single primary-key
lookup: O(1), read SLO target < 5 ms p99.

**Auth:** Any valid token with a read scope.

**Path parameters:**

| Param | Type | Description |
|---|---|---|
| `dataset_key` | string | Dataset key (may contain `/`). |

**Response `200`:**
```json
{
  "dataset_key": "raw/orders",
  "status": "fresh",
  "last_success_at": "2026-06-26T05:00:00Z",
  "expected_interval_s": 86400,
  "stale_at": "2026-06-27T05:00:00Z"
}
```

**Errors:** `404 DATASET_NOT_FOUND` — unknown key or cross-org attempt.

---

### `GET /health/score`

Compute weighted health scores (0–100) for all datasets in the org, or for a
single dataset when `?dataset_key=` is supplied. Scores combine three weighted
dimensions: freshness (default 0.50), completeness (0.30), availability (0.20).

**Auth:** Any valid token with a read scope.

**Query parameters:**

| Param | Default | Description |
|---|---|---|
| `dataset_key` | — | Optional. Filter to a single dataset key. |

**Response `200` (single dataset when `?dataset_key=` is set):**
```json
{
  "dataset_key": "raw/orders",
  "score": 87,
  "grade": "B",
  "dimensions": [
    { "name": "freshness",     "score": 100, "status": "fresh",   "reason": "fresh: updated 1h ago", "weight": 0.5 },
    { "name": "completeness",  "score":  90, "status": "ok",      "reason": "ok",                    "weight": 0.3 },
    { "name": "availability",  "score": 100, "status": "ok",      "reason": "ok",                    "weight": 0.2 }
  ],
  "reasons": ["freshness: fresh"],
  "weights_used": { "freshness": 0.5, "completeness": 0.3, "availability": 0.2 }
}
```

**Response `200` (org-wide, when no `?dataset_key=`):**
```json
{
  "org_id": "org-uuid",
  "datasets": [ /* array of per-dataset score objects */ ],
  "default_weights": { "freshness": 0.5, "completeness": 0.3, "availability": 0.2 }
}
```

**Errors:** `404 DATASET_NOT_FOUND` — unknown key when `?dataset_key=` is set.

---

### `GET /health/estate`

Return a source → raw → model → feature flow map annotated with each node's
health and freshness status. Composed from the flows store and the freshness
registry.

**Auth:** Any valid token with a read scope.

**Response `200`:**
```json
{
  "org_id": "org-uuid",
  "nodes": [
    {
      "key": "raw/orders",
      "type": "raw",
      "status": "fresh",
      "last_success_at": "2026-06-26T05:00:00Z",
      "expected_interval_s": 86400
    }
  ],
  "edges": [
    { "source_key": "raw/orders", "target_key": "model/revenue", "flow_id": "..." }
  ]
}
```

Node types: `source`, `raw`, `model`, `feature` (inferred from key prefix
conventions: `source/`, `raw/`, `ingest/` → `raw`; `model/`, `transform/` →
`model`; `metric/`, `feature/`, `agg/` → `feature`; otherwise `source`).

---

## Schema drift

### `GET /health/drift`

List recent schema-drift events for the caller's org.

**Auth:** Any valid token (embed or first-party) with a read scope.

**Query params:**
- `dataset_key` (optional) — filter to one dataset.
- `limit` (optional, default 100, max 500)

**Response `200`:**
```json
{
  "org_id": "uuid",
  "dataset_key": null,
  "events": [
    {
      "id": "uuid",
      "org_id": "uuid",
      "dataset_key": "raw/orders",
      "change_type": "added",
      "column_name": "created_at",
      "from_type": null,
      "to_type": "timestamp",
      "detected_at": "2026-06-26T12:00:00+00:00"
    }
  ]
}
```

### `GET /health/drift/{dataset_key}`

Drift history and current snapshot for one dataset. Returns **404** when the
dataset has no baseline snapshot (never been observed).

**Auth:** Any valid token with a read scope.

**Response `200`:**
```json
{
  "org_id": "uuid",
  "dataset_key": "raw/orders",
  "current_snapshot": [
    { "name": "id", "type": "int64" },
    { "name": "amount", "type": "float64" }
  ],
  "events": [...]
}
```

---

## SQL Transpilation

### `POST /transpile`

Transpile SQL from one dialect to another using sqlglot. Pure AST transform —
no data access, no filesystem or network I/O. Auth required; no org scoping.

**Auth:** Any valid first-party Bearer token.

**Request body:**
```json
{
  "sql": "SELECT DATE_TRUNC('month', created_at), SUM(amount) FROM orders GROUP BY 1",
  "from_dialect": "postgres",
  "to_dialect": "bigquery"
}
```

| Field | Required | Description |
|---|---|---|
| `sql` | Yes | Non-empty SQL string. |
| `from_dialect` | Yes | Source dialect (see allowlist below). |
| `to_dialect` | Yes | Target dialect (see allowlist below). |

**Dialect allowlist:** `bigquery`, `clickhouse`, `databricks`, `duckdb`,
`drill`, `hive`, `mysql`, `oracle`, `postgres` (alias: `postgresql`), `presto`,
`redshift`, `snowflake`, `spark`, `spark2`, `sqlite`, `trino`, `tsql`.

**Response `200`:**
```json
{ "sql": "SELECT DATE_TRUNC(created_at, MONTH), SUM(amount) FROM orders GROUP BY 1" }
```

**Errors:**
- `400 unknown_dialect` — `from_dialect` or `to_dialect` not in the allowlist.
- `400 bad_request` — `sql` is empty.
- `400 parse_error` — sqlglot failed to parse or transpile.

---
