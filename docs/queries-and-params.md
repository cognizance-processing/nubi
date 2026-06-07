# Queries & Parameters

Nubi's query endpoint accepts SQL (inline or from the registry) with positional or named parameters and an optional datastore selector. The planner translates it through sqlglot into a `PhysicalPlan`, injects RLS predicates at the AST level, checks the content-addressed cache, then streams Arrow IPC back to the caller.

---

## Query Endpoint

```
POST /api/v1/query
Authorization: Bearer <jwt>
Content-Type: application/json
```

### Request Body

```json
{
  "sql":          "SELECT region, SUM(revenue) AS total FROM sales WHERE year = $1 GROUP BY 1",
  "params":       [2024],
  "query_id":     null,
  "named_params": null,
  "datastore_id": null
}
```

| Field | Type | Description |
|-------|------|-------------|
| `sql` | `string` | Inline SQL SELECT. Ignored for embed-kind tokens — those must use `query_id`. |
| `params` | `array` | Positional values bound to `$1`, `$2`, … placeholders. |
| `query_id` | `string \| null` | ID of a registered query. Required for embed tokens; optional for first-party tokens. |
| `named_params` | `object \| null` | Named values resolved against the registered query's declared `params` list. See [Named Params](#named-params) below. |
| `datastore_id` | `string \| null` | Routes the query to a specific datastore (org-scoped). Defaults to the built-in DuckDB demo dataset. |

### Response

`Content-Type: application/vnd.apache.arrow.stream`

Arrow IPC stream. The `X-Nubi-Cache` response header is `HIT` or `MISS`.

---

## Positional Parameters

Placeholders use `$N` syntax (1-indexed). The backend rewrites them through sqlglot to the connector's native dialect (`%s` for asyncpg, `?` for DuckDB).

```sql
SELECT date, amount
FROM sales
WHERE year = $1
  AND region = $2
ORDER BY date
```

Params array: `[2024, "EMEA"]`

> Never interpolate user-supplied values into SQL strings. Always use `$N` params — they are bound by the connector driver, not string-concatenated.

---

## Named Params

Registered queries declare typed named parameters using `{{name}}` placeholders in their SQL. The server resolves `{{name}}` to positional `$N` bindings before execution — values are never string-concatenated.

### `QueryParam` Fields

| Field | Type | Description |
|-------|------|-------------|
| `name` | `string` | Must match a `{{name}}` placeholder in the SQL. |
| `type` | `string` | `text` \| `number` \| `date` \| `daterange` \| `select` \| `multiselect` |
| `default` | `any` | Default value when caller does not supply this param. |
| `required` | `bool` | `true` → caller must supply a value; missing required param → HTTP 400. |
| `options_query_id` | `string \| null` | Registered query whose results populate a `select`/`multiselect` option list (used by the frontend). |

### Example Registered Query with Named Params

```json
{
  "id": "sales_by_region",
  "sql": "SELECT date, amount FROM sales WHERE region = {{region}} AND year = {{year}}",
  "name": "Sales by region",
  "params": [
    { "name": "region", "type": "text",   "required": true },
    { "name": "year",   "type": "number", "default": 2024 }
  ]
}
```

Call it with named params:

```json
POST /api/v1/query
{
  "query_id":     "sales_by_region",
  "named_params": { "region": "EMEA" }
}
```

`year` defaults to `2024`; `region` is supplied. The server resolves `{{region}}` → `$1` and `{{year}}` → `$2`, then executes `SELECT date, amount FROM sales WHERE region = $1 AND year = $2` with params `["EMEA", 2024]`.

### Security Constraints on Named Params

Token-claim-reserved names cannot be set via `named_params` — the server rejects them with HTTP 400:

```
policies, user_id, sub, org, org_id, project, roles, scope, iss, aud, exp, iat, embed_origin, kind
```

Resolution precedence (security-critical): **token/RLS claims > named_params values > query default**.

---

## Security: Embed Tokens and the Allowlist Gate

Embed tokens (`kind='embed'`) **cannot execute arbitrary SQL**. They must reference a server-registered query via `query_id`. The registered SQL is used verbatim; any `sql` field in the request body is silently ignored.

First-party tokens (`kind='access'`) may run arbitrary SELECT SQL or optionally reference a registered query.

---

## Query Registry

Registered queries are stored server-side and referenced by `id` or slug. List them:

```
GET /api/v1/query/registry
Authorization: Bearer <jwt>
```

Response:

```json
{
  "queries": [
    {
      "id":             "demo_all",
      "name":           "Demo — all rows",
      "required_scope": null,
      "params":         []
    },
    {
      "id":             "sales_by_region",
      "name":           "Sales by region",
      "required_scope": null,
      "params": [
        { "name": "region", "type": "text",   "default": null, "required": true,  "options_query_id": null },
        { "name": "year",   "type": "number", "default": 2024, "required": false, "options_query_id": null }
      ]
    }
  ]
}
```

### Seeded Demo Queries

| ID | SQL | Description |
|----|-----|-------------|
| `demo_all` | `SELECT * FROM demo` | All 5 demo rows |
| `demo_active` | `SELECT * FROM demo WHERE active = true` | Active rows only |
| `demo_points_10k` | `generate_series(1, 10000)` | 10 000 synthetic scatter points (`id, x, y, category`) |
| `demo_points_100k` | `generate_series(1, 100000)` | 100 000 synthetic scatter points |
| `demo_points_500k` | `generate_series(1, 500000)` | 500 000 synthetic scatter points |

### Registering a Query Programmatically

```python
from app.queries.registry import get_query_registry, QueryParam

registry = get_query_registry()
registry.register(
    id="revenue_by_month",
    sql="SELECT month, SUM(revenue) AS total FROM sales WHERE region = {{region}} GROUP BY 1 ORDER BY 1",
    name="Revenue by month",
    params=[
        QueryParam(name="region", type="text", required=True),
    ],
)
```

Or via AI text-to-SQL with `save_as`:

```json
POST /api/v1/ai/sql
{
  "question":   "revenue by month for a given region",
  "save_as":    "revenue_by_month"
}
```

The `{{name}}` placeholders found in the generated SQL are automatically inferred as `QueryParam` descriptors (type `text`, not required, no default).

### `required_scope`

A `RegisteredQuery` can carry a `required_scope` string. When set, the caller must carry that scope (or a wildcard covering it) in addition to the base read scope. Example:

```python
registry.register(
    id="admin_audit_log",
    sql="SELECT * FROM audit_log",
    name="Admin audit log",
    required_scope="read:query:admin_audit_log",
)
```

Tokens without the required scope receive HTTP 403 when they request this query.

---

## Query Library UI

The `/queries` route in the frontend lists all registered queries. Each card shows:

- The query `id` and `name`
- The full SQL in a read-only code editor
- Input fields for each declared `param`

Click **Run** to execute the query with the supplied params and see the first 100 rows of the Arrow result inline.

---

## Content-Addressed Cache

Every query + params + RLS policy set maps to a SHA-256 cache key:

```
cache_key = SHA-256(canonical_json({"sql": <rewritten SQL>, "params": [...], "rls": {...policies...}}))
```

- Keys are sorted, compact JSON — no whitespace.
- Only `claims.policies` enters the key. `exp`, `sub`, `iat` and other JWT claims are excluded, so token rotation does not blow the cache.
- N viewers with identical queries and identical RLS context share one cache slot.

The cache is LRU + TTL with a Redis-swappable interface. Cache status is surfaced in `X-Nubi-Cache: HIT | MISS`.

See [Cache-Key Spec](/docs/cache-key-spec) for the full algorithm, test vectors, and Rust pseudocode.

---

## Pushdown Behaviour

| Operation | Pushed down if… |
|-----------|-----------------|
| Predicate (`WHERE`) | `predicate_pushdown=True` |
| Projection (`SELECT cols`) | `projection_pushdown=True` |
| `LIMIT` | implicit for SQL connectors |
| RLS injection | `predicate_rls=True` (SQL: AST; API connectors: post-fetch) |

Operations that cannot be pushed down are applied post-fetch in Python.

---

## Pre-Aggregations

The MCP tool `propose_materialized_view` analyses the query log and returns rollup suggestions:

```json
[
  {
    "base_table":  "sales",
    "dimensions":  ["region", "month"],
    "measures":    ["SUM(revenue)"],
    "hit_count":   42,
    "bytes_saved": 1048576
  }
]
```

Pre-aggregations collapse the warehouse hit count for high-traffic queries, extending the zero-cost advantage to diverse workloads where the raw content-addressed cache hit rate would otherwise be low.
