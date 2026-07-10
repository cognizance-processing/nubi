# API Reference

Complete reference for the Nubi HTTP API. New to building on Nubi? Start with
the [Developer Guide](/docs/developer-guide) for the big picture, then come
back here for endpoint detail.

---

## Conventions

| Convention | Value |
|---|---|
| **Base URL** | `/api/v1` — every endpoint below is relative to this prefix. |
| **Auth header** | `Authorization: Bearer <token>` on every request. |
| **Content type** | Requests and responses are JSON, except query/metric endpoints, which stream `application/vnd.apache.arrow.stream`. |
| **Org scoping** | All resources are scoped to the caller's org. Cross-org access returns **404** (never 403) so no resource's existence leaks across tenants. |
| **Error shape** | `{ "error": "<code>", "message": "<text>" }`. See [Error codes reference](#error-codes-reference). |
| **Swagger UI** | Interactive docs at `/docs` in development (`ENV=development`); disabled in production. |

**Pagination.** Most list endpoints return the full org-scoped collection.
Endpoints that paginate (e.g. [`GET /audit`](#audit-log)) take `limit` +
`offset` query params and return `{ items, total, limit, offset }`.

---

## Authentication

All endpoints require a Bearer token in the `Authorization` header:

```
Authorization: Bearer <token>
```

Three token kinds are accepted:

| Kind | Prefix / format | Issued by | Use case |
|---|---|---|---|
| **First-party access token** | short-lived JWT (HS256) | `POST /auth/login` or Google OAuth | Interactive users, CLI, MCP server, AI agents |
| **API key** | `nubi_ak_…` (long-lived) | `POST /auth/api-keys` | CLI in CI, automation — works anywhere a Bearer token does |
| **Embed JWT** | RS256/ES256 | Your backend (registered issuer) | Embedded dashboards, per-viewer RLS |

Embed tokens are read-only and can only reference registered queries by
`query_id` — raw SQL is blocked on the embed path.

### `POST /auth/login`

Authenticate with email + password. Uses a constant-time, enumeration-safe
comparison. Also sets an HTTP-only refresh cookie.

**Body:** `{ "email", "password" }`
**Response `200`:** `{ "user": { id, email, name, ... }, "access_token": "<jwt>" }`
**Errors:** `401 invalid_credentials` — unknown user or wrong password (same error for both).

### `POST /auth/register`

Create a new user account. **Response `201`.**

### `POST /auth/refresh`

Exchange the refresh cookie for a fresh `access_token`.

### `POST /auth/logout`

Revoke the refresh session. **Response `204`.**

### `GET /auth/me`

Return the currently authenticated user. **Response `200`:** `{ "user": { ... } }`.

### `GET /auth/config`

Public auth configuration (e.g. whether Google OAuth is enabled). Used by the
login UI.

### Google OAuth

`GET /auth/google/start` redirects to Google; `GET /auth/google/callback`
completes the exchange and issues a first-party token.

### API keys

Long-lived `nubi_ak_…` keys — the right way to authenticate the CLI in CI.

#### `POST /auth/api-keys`

Mint a key scoped to the caller's default org. The raw key is returned **once**
in `key` and is never retrievable again (only a hash + last four are stored).

**Body:** `{ "name": "GitHub Actions" }`
**Response `201`:** `{ "key": "nubi_ak_…", "api_key": { "id", "name", "last_four", "created_at", ... } }`

#### `GET /auth/api-keys`

List the caller's keys. **Response `200`:** `{ "api_keys": [{ id, name, last_four, created_at, last_used_at, revoked_at }] }`. Key material is never returned.

#### `DELETE /auth/api-keys/{key_id}`

Revoke a key. Cross-user/cross-org keys return **404** (never 403). **Response `204`.**

---

## Scope & access grants

### `GET /auth/scope`

Resolve the caller's effective RLS scope from the **verified token** (works for
first-party **and** embed tokens). Hosts call this to discover what a token is
authorised to see, without re-deriving it themselves.

**Auth:** Any valid token.

**Response `200`:**
```json
{
  "org": "<org_id>",
  "scope": ["read:*"],
  "policies": { "region": "Gauteng" },
  "effective_policies": { "region": ["Gauteng", "JHB", "PTA"] },
  "expanded": true
}
```

- `policies` — raw policy claim from the verified token.
- `effective_policies` — hierarchy-expanded **and** merged with any non-expired
  `access_grants` for the caller's subject, normalised to `{dimension: [values]}`.
- `expanded` — `true` when `effective_policies` differs from `policies`.

Policies/org come from the token only (a request body is ignored). Resolution is
org-scoped and **fails closed** — on error it returns the narrower raw policies,
never a widened set.

---

### `GET /access-grants`

List grants for a subject in the caller's org.

**Auth:** Any org member. **Query:** `subject_type` (`user`|`role`|`embed_sub`),
`subject_id`.

**Response `200`:** `{ "grants": [{ id, subject_type, subject_id, dimension, value, expires_at, created_at }] }`

### `POST /access-grants`

Create (or refresh) a grant. **Auth:** owner/admin only.

**Body:** `{ "subject_type", "subject_id", "dimension", "value", "expires_at"? }`
**Response `201`:** `{ "grant": { ... } }`

### `DELETE /access-grants/{id}`

Delete a grant within the caller's org. **Auth:** owner/admin only.
A grant id belonging to another org (or absent) returns **404** (not 403).
**Response:** `204`.

> Grants are org-scoped and merged into `GET /auth/scope`'s `effective_policies`.
> See [governance.md](./governance.md) for the cardinality cap
> (`NUBI_RLS_MAX_POLICY_VALUES`, default 5000) that fails closed on oversized
> policies.

---

## Projects

A project groups a workspace's connectors, queries, dashboards, and flows and
is the unit of files-as-code export/import. All routes are org-scoped.

| Method | Path | Auth | Description |
|---|---|---|---|
| `GET` | `/projects` | Member | List projects for the caller's org. |
| `POST` | `/projects` | Writer | Create a project. **`201`.** |
| `GET` | `/projects/{id}` | Member | Fetch one project. |
| `PATCH` | `/projects/{id}` | Writer | Partial update (name, settings). |
| `PUT` | `/projects/{id}` | Writer | Replace project fields. |
| `DELETE` | `/projects/{id}` | Writer | Delete a project. **`204`.** |
| `GET` | `/projects/{id}/deletion-impact` | Member | Preview what a delete would cascade. |
| `GET` | `/projects/{id}/export` | Member | Export the whole project as a files-as-code bundle. |
| `POST` | `/projects/{id}/import` | Writer | Bulk-upsert a project bundle; returns a per-resource result. |

`GET /projects/{id}/export` and `POST /projects/{id}/import` are the one
round-trip the CLI's [`nubi pull` / `nubi push`](/docs/sdk-and-cli) use when a
project is bound. The bundle format is specified in
[Files-as-Code](/docs/files-as-code).

---

## Connectors

Manage data source connections. Secrets (`password`, `token`, `api_key`, …) are
AES-256-GCM encrypted at rest and **never** returned by any read — connector
credentials do not round-trip. See [Connector security](/docs/connector-security).

### `POST /connectors`

Create a connector. **Auth:** Writer. **`201`.**

**Body (`CreateConnectorIn`):**
```json
{
  "name": "prod-postgres",
  "type": "postgres",
  "config": { "host": "db.example.com", "port": 5432, "database": "app", "user": "readonly", "sslmode": "require" },
  "secret": { "password": "s3cr3t" },
  "seed": "blank"
}
```

- `config` holds **non-secret** connection params (extra fields allowed, e.g. a
  `base_url` for HTTP sources, `network_mode`, `bridge_id`). Putting a secret
  key in `config` is rejected.
- `secret` holds sensitive fields only: `password`, `service_account_json`,
  `token`, `api_key`, `access_token`, `aws_secret_access_key`, `private_key`.
- `seed`: `"demo"` seeds a read-only copy of the demo parquet dataset;
  `"blank"` (default) creates an empty connector.

### `GET /connectors`

List connectors for the org. Secret fields come back blank.

### `GET /connectors/{id}`

Fetch one connector (no secrets).

### `PUT /connectors/{id}`

Update `name`, `config`, and/or `secret` (all optional). **Auth:** Writer.

### `DELETE /connectors/{id}`

Delete a connector. **Auth:** Writer. **`204`.**

### `POST /connectors/{id}/test`

Validate config + secret resolvability against the live source. Backs
`nubi connectors test`.

---

## Queries

Ad-hoc SQL and the server-side query registry. Query results stream Apache
Arrow IPC — see [Notes on Arrow IPC responses](#notes-on-arrow-ipc-responses).

### `POST /query`

Execute a query and stream the result as an Arrow IPC stream.

**Auth:** Any valid token. First-party callers may send raw `sql` (requires the
`author:sql` scope); **embed tokens must send `query_id`** — raw SQL is rejected
on the embed path.

**Body (`QueryIn`):**
```json
{
  "sql": "SELECT region, SUM(amount) AS revenue FROM orders WHERE region = $1 GROUP BY region",
  "query_id": null,
  "params": ["EMEA"],
  "named_params": null,
  "datastore_id": null
}
```

| Field | Description |
|---|---|
| `sql` | SELECT statement. Non-SELECT is rejected. Ignored for embed tokens and when `query_id` is set. |
| `query_id` | Id of a registered query. Required for embed tokens. |
| `params` | Positional params bound to `$1`, `$2`, … (index 0 → `$1`). |
| `named_params` | Named param values, resolved against a registered query's declared params. Token/RLS claim names are locked and win over `named_params`. |
| `datastore_id` | Optional connector to route to; otherwise the built-in DuckDB demo dataset. |

**Response `200`:** `Content-Type: application/vnd.apache.arrow.stream`. Header
`X-Nubi-Cache: HIT | MISS`. RLS policies come exclusively from the verified
token — any `policies` in the request body is ignored.

### `POST /query/estimate`

Run the identical auth/scope/allowlist/RLS resolution as `/query` but call the
connector's `estimate()` instead of executing — returns a cost/row estimate
without scanning data.

### `GET /query/registry`

List registered queries (ids, declared params, output schemas).

### `POST /query/registry`

Register a query in the server-side registry. **`201`.** Registered queries are
what embed tokens and `client.query('<id>')` reference by id.

> The generic CRUD in [Resources](#resources-generic-crud) also manages queries as a
> resource kind (with `.sql` + metadata) — use that for board-authoring
> workflows and the registry endpoints for the execution registry.

---

## Resources (generic CRUD)

A uniform CRUD surface over four resource kinds: **`datastores`**, **`boards`**,
**`queries`**, **`widgets`**. This is what `@nubi/sdk`'s
`client.resources.*` wraps.

**Auth:** First-party Bearer token. Writes require the Writer role. Unknown
resource names return **404**.

| Method | Path | Description |
|---|---|---|
| `GET` | `/{resource}` | List all resources of that kind for the org. |
| `POST` | `/{resource}` | Create. **`201`.** Writer. |
| `GET` | `/{resource}/{id}` | Fetch one. Supports `?env=<key>` to resolve an environment override (adds `resolved_version`). |
| `PUT` | `/{resource}/{id}` | Update fields. Writer. |
| `DELETE` | `/{resource}/{id}` | Delete. **`204`.** Writer. |

All rows share the shape `{ id, org_id, project_id, created_by, name, config, created_at, updated_at }`. A `queries` row whose `config` carries a `metric` block is validated as a governed [metric](#metrics-semantic-layer) on write.

---

## Boards & dashboards

Boards store a `DashboardSpec` in `config.spec` (CRUD via [Resources](#resources-generic-crud)
with `resource = boards`). Additional board-specific endpoints:

### `POST /dashboards/validate`

Validate a `DashboardSpec` and return structured, repair-oriented issues
(chart encodings, filter/text requirements, var refs, tab refs, query-id
registry lookups). Useful for grounding an AI author before saving.

**Body:** `{ "spec": { ... } }`
**Response `200`:** `{ "valid": true, "issues": [] }`

### `POST /boards/{board_id}/providers/{provider_id}/data`

Resolve a `DataProvider` declared in a board's spec and return its named
result-sets as a multi-table Arrow IPC frame. See
[DataProvider boards](#dataprovider-boards) below for the framing and cache-key
details.

---

## Export & share

Board export and the embed-share descriptor. **Auth:** first-party Bearer token.

| Method | Path | Description |
|---|---|---|
| `GET` | `/boards/{board_id}/export.json` | Export a board's spec + data as JSON. |
| `GET` | `/boards/{board_id}/export.csv` | Export the board's data as CSV. |
| `GET` | `/boards/{board_id}/export.pdf` | Render the board to PDF. |
| `POST` | `/boards/{board_id}/share` | Return the embed descriptor: embed URL, a ready-to-paste `<nubi-dashboard>` snippet, the exact claim shape the host must RS256/ES256-sign, and the RLS policy summary. |

`POST /boards/{board_id}/share` never mints a token — **Nubi does not sign embed
JWTs**; the host signs them with its own key. See [Embedding](/docs/embedding).

---

## Embedding

Embedded-dashboard support. The trust boundary and RLS model are in
[Embedding](/docs/embedding); token verification is covered under
[Scope & access grants](#scope--access-grants).

### `GET /embed/config/{dashboard_id}`

Read-only descriptor the host page fetches at runtime to render an embedded
board (spec, provider ids, embed conventions). Auth is the host-signed embed
JWT.

### `POST /embed/embed-token`

**Development only.** Mints a backend-verified HS256 embed token for local
testing. **Disabled by default** — returns `503` unless
`EMBED_DEV_TOKEN_ENABLED=true`. Never enable in production; real embed tokens
are signed by the host's own key.

**Response `200`:** `{ "token": "<jwt>", "expires_in": <seconds> }`

---

## Scheduled jobs

Scheduled exports and recurring jobs (distinct from [Flows](#flows), which are
DAGs). **Auth:** Writer for writes.

| Method | Path | Description |
|---|---|---|
| `POST` | `/jobs` | Create a job. **`201`.** |
| `GET` | `/jobs` | List jobs for the org. |
| `GET` | `/jobs/{id}` | Fetch one job. |
| `DELETE` | `/jobs/{id}` | Delete a job. **`204`.** |
| `POST` | `/jobs/{id}/run` | Trigger a run now. |
| `GET` | `/jobs/{id}/runs` | List a job's runs. |

---

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

## AI endpoints

All AI endpoints require first-party access tokens. AI calls are metered
(`ai_calls`) against the org's quota — the Free tier does not include AI calls.

### `POST /ai/ask`

Generate a grounded SQL suggestion for a natural-language question.

**Request body:**
```json
{ "question": "Show total revenue by region for last month", "model": null }
```

`model` is optional; `null` uses the provider default.

**Response `200`:**
```json
{
  "grounding": {
    "relevant_tables": ["orders"],
    "relevant_columns": [{"table": "orders", "column": "amount"}, ...],
    "related_queries": [...],
    "snippets": [...]
  },
  "suggestion": "SELECT region, SUM(amount) AS revenue FROM orders ...",
  "provider": "litellm"
}
```

---

### `POST /ai/dashboard`

Generate a grounded `DashboardSpec` and compiled HTML from a description.

**Request body:**
```json
{ "question": "Revenue KPI and trend chart by month", "model": null }
```

**Response `200`:**
```json
{
  "spec": { "version": 1, "title": "...", "widgets": [...] },
  "html": "<div class='nubi-dashboard'>...</div>",
  "grounding": { ... },
  "provider": "litellm",
  "valid": true,
  "issues": []
}
```

---

### `GET /ai/dashboard/schema`

Return the JSON Schema for `DashboardSpec`. Use this to ground an LLM
before authoring a spec programmatically.

**Response `200`:** JSON Schema dict.

---

### `GET /ai/context`

Single-call authoring context for external agents. Returns the full query
registry (ids, params, output schemas), the metric catalogue, conventions,
and optionally the dashboard spec schema.

**Query parameters:**

| Param | Default | Description |
|---|---|---|
| `compact` | `false` | When `true`, drops description/default/options_query_id to reduce token footprint. |
| `include_schema` | `false` | When `true`, includes the `DashboardSpec` JSON Schema under `spec_schema`. |

**Response `200`:**
```json
{
  "queries": [{ "id": "...", "name": "...", "params": [...], "output_schema": [...] }],
  "metrics": [{ "id": "...", "name": "...", "dimensions": [...], "time_grains": [...] }],
  "conventions": { "query_binding": "...", "metrics": "..." },
  "spec_schema": { ... }
}
```

---

### `POST /ai/chat`

Agentic chat endpoint. Runs the 14-tool agent loop and returns a reply plus
a list of actions taken.

**Request body:**
```json
{
  "messages": [
    { "role": "user", "content": "Show me revenue by region" }
  ],
  "board_id": null
}
```

**Response `200`:**
```json
{
  "reply": "I've updated the dashboard with a bar chart of revenue by region.",
  "actions": [{ "tool": "set_widget_query", "params": {...}, "result": {...} }]
}
```

---

### `POST /ai/chat/stream`

Streaming version of `/ai/chat`. Returns a `text/event-stream` SSE response
with `data:` lines for each token and a final `data: [DONE]`.

---

### `POST /ai/sql`

Text-to-SQL: accepts a natural-language question and returns a grounded SQL
string. Uses the catalog for grounding and the configured LLM provider.

**Request body:**
```json
{ "question": "Top 10 products by revenue last quarter" }
```

**Response `200`:**
```json
{ "sql": "SELECT product_id, SUM(amount) AS revenue FROM orders ...", "provider": "litellm" }
```

---

## Flows

All flows endpoints require first-party access tokens. Flows are org-scoped;
cross-org access returns `404`.

### `POST /flows`

Create a new flow.

**Auth:** Writer role.

**Request body:**
```json
{
  "name": "Daily revenue rollup",
  "spec": {
    "version": 1,
    "tasks": [
      {
        "key": "pull_orders",
        "kind": "query",
        "sql": "SELECT * FROM orders WHERE order_date >= '{{params.start_date}}'",
        "needs": []
      },
      {
        "key": "aggregate",
        "kind": "python",
        "code": "result = {'revenue': sum(r['amount'] for r in inputs['pull_orders']['rows'])}",
        "needs": ["pull_orders"],
        "cpu_cores": 1.0,
        "mem_mb": 512,
        "timeout_s": 60
      }
    ]
  },
  "schedule": "0 6 * * *",
  "enabled": true
}
```

**Response `201`:** Flow record `{id, org_id, name, spec, version, enabled, schedule, next_run_at, ...}`.

---

### `GET /flows`

List all flows for the caller's org.

**Response `200`:** Array of flow records.

---

### `GET /flows/{id}`

Retrieve a single flow.

**Response `200`:** Flow record.

---

### `PUT /flows/{id}`

Update a flow's name, spec, enabled state, or schedule.

**Auth:** Writer role.

**Request body (all fields optional):**
```json
{ "name": "New name", "spec": { ... }, "enabled": false, "schedule": "0 9 * * 1-5" }
```

**Response `200`:** Updated flow record.

---

### `DELETE /flows/{id}`

Delete a flow.

**Auth:** Writer role.

**Response `204`:** No content.

---

### `POST /flows/validate`

Validate a flow spec without running it.

**Request body:**
```json
{ "spec": { ... } }
```

**Response `200`:**
```json
{ "valid": true, "issues": [] }
```

---

### `POST /flows/{id}/run`

Trigger a durable run of the flow.

**Auth:** Writer role.

**Request body (optional):**
```json
{ "params": { "start_date": "2026-01-01", "region": "EMEA" }, "env": "prod" }
```

**Response `200`:** Flow run record with `task_runs` array.

---

### `GET /flows/{id}/runs`

List all runs of a flow (newest first).

**Response `200`:** Array of `{id, flow_id, state, trigger, params, started_at, finished_at, duration_s, error}`.

---

### `GET /flows/runs/{run_id}`

Retrieve a full run record including all task runs.

**Query parameters:**

| Param | Default | Description |
|---|---|---|
| `include_results` | `0` | Set to `1` to include full task result blobs (default: stubs for blobs > 64 KiB). |
| `task_runs_limit` | `2000` | Max task_run rows included (ceiling: 10 000). |

**Response `200`:**
```json
{
  "id": "...",
  "flow_id": "...",
  "state": "success",
  "trigger": "manual",
  "params": {},
  "started_at": "2026-06-01T06:00:00Z",
  "finished_at": "2026-06-01T06:00:45Z",
  "duration_s": 45.2,
  "task_runs": [
    {
      "task_key": "pull_orders",
      "state": "success",
      "attempt": 1,
      "started_at": "...",
      "finished_at": "...",
      "result": { "rows": [...], "row_count": 1200 }
    }
  ],
  "task_runs_truncated": false
}
```

---

### `POST /flows/{flow_id}/sweep`

Run a parameter sweep — one flow run per param set in a grid or explicit list.

**Auth:** Writer role.

**Request body:**
```json
{
  "grid": { "region": ["EMEA", "APAC", "AMER"], "model": ["v1", "v2"] },
  "max_cells": 50
}
```

Or with an explicit list:

```json
{
  "param_sets": [
    { "region": "EMEA", "model": "v1" },
    { "region": "APAC", "model": "v1" }
  ],
  "max_cells": 50
}
```

Supply `grid` (Cartesian product expanded) or `param_sets` (explicit). Max
cells is capped server-side at `MAX_SWEEP_CELLS` (default 50, configurable
via `MAX_SWEEP_CELLS` env var). The sweep times out at `SWEEP_TIMEOUT_S`
(default 300 s).

**Response `200`:**
```json
{
  "sweep_id": "...",
  "flow_id": "...",
  "total": 6,
  "succeeded": 6,
  "failed": 0,
  "diff_surface": [
    { "index": 0, "params": { "region": "EMEA", "model": "v1" }, "outputs": { ... } }
  ],
  "cells": [
    { "index": 0, "params": {...}, "run_id": "...", "state": "success", "error": null }
  ]
}
```

**Errors:**
- `400 bad_request` — neither `grid` nor `param_sets` supplied.
- `400 bad_request` — `param_sets` length exceeds the server cap.
- `504 sweep_timeout` — wall-clock limit exceeded.

---

### `POST /flows/{flow_id}/backfill`

Re-run a flow over a historical date range, one run per time window.

**Auth:** Writer role.

**Request body:**
```json
{
  "start": "2026-01-01T00:00:00Z",
  "end": "2026-06-01T00:00:00Z",
  "window": "7d",
  "params": { "region": "EMEA" }
}
```

`window` accepts `Nd` (days), `Nw` (weeks), `Nh` (hours). The maximum number
of windows is capped at `MAX_BACKFILL_WINDOWS` (default 500).

---

### `POST /flows/triggers`

Register a new flow trigger.

**Auth:** Writer role.

**Request body:**
```json
{
  "flow_id": "flow-uuid",
  "kind": "event",
  "source": "order.completed",
  "enabled": true,
  "secret": null,
  "extra": {}
}
```

| Field | Description |
|---|---|
| `kind` | `"event"` / `"webhook"` / `"downstream"` |
| `source` | For `event`/`webhook`: event key string. For `downstream`: upstream flow_id. |
| `secret` | Optional HMAC secret for webhook signature verification. |

**Response `201`:**
```json
{ "id": "...", "flow_id": "...", "kind": "event", "source": "order.completed", "org_id": "...", "enabled": true, "created_at": "..." }
```

---

### `GET /flows/triggers`

List all triggers for the caller's org.

**Response `200`:** Array of trigger records.

---

### `POST /flows/triggers/fire`

Fire a named event, triggering any flows registered on that event key.

**Auth:** Writer role.

**Request body:**
```json
{ "event_key": "order.completed", "params": { "order_id": "12345" } }
```

**Response `200`:**
```json
{ "triggered": 2, "flow_ids": ["flow-uuid-1", "flow-uuid-2"] }
```

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

## MCP

### MCP server registry — `/mcp/servers`

Org-scoped CRUD for external MCP servers your agent loop can call. Auth tokens
are never returned after creation (stripped server-side).

**Auth for writes (POST / PUT / DELETE):** First-party Bearer token, writer role.
**Auth for reads (GET):** Any valid first-party Bearer token.

#### `GET /mcp/servers`

List all MCP servers registered for the caller's org. Secret fields (`auth_token`) are stripped.

**Response `200`:** `[{id, org_id, name, url, transport, enabled, created_by, created_at, updated_at}]`

---

#### `POST /mcp/servers`

Register a new external MCP server.

**Request body:**
```json
{
  "name": "Internal tools MCP",
  "url": "https://tools.example.com/mcp",
  "transport": "http",
  "auth_token": "secret-bearer",
  "enabled": true
}
```

| Field | Required | Default | Description |
|---|---|---|---|
| `name` | Yes | — | Display name. |
| `url` | Yes | — | MCP server URL. Validated by SSRF guard. |
| `transport` | No | `"http"` | Transport type (`"http"`). |
| `auth_token` | No | `null` | Bearer token sent to the external server. Encrypted at rest; never returned. |
| `enabled` | No | `true` | Disabled servers are skipped by the agent loop. |

**Response `201`:** Server record (no `auth_token`).

**Errors:** `400` — SSRF guard blocked the URL.

---

#### `GET /mcp/servers/{server_id}`

Return one MCP server record (no secrets).

**Response `200`:** Server record.

**Errors:** `404 mcp_server_not_found`.

---

#### `PUT /mcp/servers/{server_id}`

Partially update an MCP server. All fields are optional; omitted fields are left unchanged.

**Request body (all optional):**
```json
{ "name": "New name", "url": "https://new.example.com/mcp", "transport": "http", "auth_token": "new-secret", "enabled": false }
```

**Response `200`:** Updated server record (no `auth_token`).

**Errors:** `400` — SSRF guard blocked the URL. `404 mcp_server_not_found`.

---

#### `DELETE /mcp/servers/{server_id}`

Delete an MCP server.

**Response `204`:** No content.

**Errors:** `404 mcp_server_not_found`.

---

### Nubi as MCP server — `POST /mcp`

Exposes Nubi's own tool registry to external MCP clients (Claude Desktop,
Claude Code, etc.) via JSON-RPC 2.0 over a single HTTP POST. Auth is a
first-party Bearer JWT — the same token kind used by `/ai/chat`.

**Auth:** First-party Bearer token (`current_user` + `verified_identity`). The
caller's org and RLS scope are resolved from the token and passed to tool
execution — never hard-coded or escalated.

**Request body:** JSON-RPC 2.0 envelope.

Three methods are supported:

#### `initialize`

```json
{ "jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {} }
```

**Response:**
```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": {
    "protocolVersion": "2024-11-05",
    "capabilities": { "tools": {} },
    "serverInfo": { "name": "nubi", "version": "1.0.0" }
  }
}
```

#### `tools/list`

```json
{ "jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {} }
```

**Response:**
```json
{
  "jsonrpc": "2.0",
  "id": 2,
  "result": {
    "tools": [
      { "name": "run_query", "description": "...", "inputSchema": { ... } }
    ]
  }
}
```

The tool list is drawn from the same 14-tool registry used by `/ai/chat` and
is org-scoped.

#### `tools/call`

```json
{
  "jsonrpc": "2.0",
  "id": 3,
  "method": "tools/call",
  "params": {
    "name": "run_query",
    "arguments": { "query_id": "revenue", "params": { "region": "EMEA" } }
  }
}
```

**Response (success):**
```json
{
  "jsonrpc": "2.0",
  "id": 3,
  "result": {
    "content": [{ "type": "text", "text": "{\"rows\": [...]}" }],
    "isError": false
  }
}
```

**Response (tool error):**
```json
{
  "jsonrpc": "2.0",
  "id": 3,
  "result": {
    "content": [{ "type": "text", "text": "error detail" }],
    "isError": true
  }
}
```

Tool calls run in a thread (`asyncio.to_thread`). The caller's scope from the
verified token is forwarded verbatim — embed tokens cannot access this endpoint
(only `current_user` tokens are accepted). RLS policy claims are forwarded from
`verified_identity`.

**Protocol errors (unknown method, parse error):** Standard JSON-RPC error
object (`{ "error": { "code": -32601, "message": "Method not found" } }`).

---

## Flow versions, revert, and environments

### `GET /flows/{id}/versions`

List all spec versions for a flow (newest first). Specs are excluded from the
list for compactness; fetch a specific version to get the full spec.

**Auth:** Any valid first-party Bearer token. Org-scoped.

**Response `200`:**
```json
{
  "flow_id": "flow-uuid",
  "versions": [
    { "id": "ver-uuid", "version": 3, "created_by": "user-uuid", "created_at": "2026-06-26T10:00:00Z" },
    { "id": "ver-uuid", "version": 2, "created_by": "user-uuid", "created_at": "2026-06-25T09:00:00Z" },
    { "id": "ver-uuid", "version": 1, "created_by": "user-uuid", "created_at": "2026-06-24T08:00:00Z" }
  ]
}
```

**Errors:** `404` — flow not found or cross-org.

---

### `GET /flows/{id}/versions/{v}`

Fetch the full spec snapshot for version `v`.

**Auth:** Any valid first-party Bearer token. Org-scoped.

**Response `200`:**
```json
{
  "id": "ver-uuid",
  "flow_id": "flow-uuid",
  "org_id": "org-uuid",
  "version": 2,
  "spec": { "version": 1, "tasks": [...] },
  "created_by": "user-uuid",
  "created_at": "2026-06-25T09:00:00Z"
}
```

The `spec` field has `__owner_policies__` stripped before returning (security).

**Errors:** `404` — flow not found, cross-org, or version number not found.

---

### `POST /flows/{id}/revert/{v}`

Revert a flow's spec to a prior version. The current spec is snapshotted as a
new version first (so revert is undoable), then the target version's spec is
applied as the live spec. The caller's RLS policies are re-snapshotted onto the
reverted spec.

**Auth:** First-party Bearer token. Writer role required.

**Response `200`:** Updated flow record (same shape as `GET /flows/{id}`).

**Errors:** `404` — flow or version not found.

---

### `GET /flows/{id}/environments`

List environments and their materialisation watermarks for a flow.

**Auth:** Any valid first-party Bearer token. Org-scoped. Viewer-friendly (read-only).

**Response `200`:**
```json
{
  "flow_id": "flow-uuid",
  "environments": [
    {
      "key": "prod",
      "watermarks": { "model/revenue": "2026-06-26T05:00:00Z" }
    },
    {
      "key": "dev",
      "watermarks": {}
    }
  ]
}
```

**Errors:** `404` — flow not found or cross-org.

---

## Variables

Project variables are persistent, org/project-scoped key/value pairs used to
store configuration and runtime state (e.g. dashboard defaults, feature flags,
per-project settings).  They are readable by any authenticated member and
writable by members with the **writer** role.

**Scoping:** Each variable belongs to an org and optionally a project.  The
effective scope is resolved from the `X-Org-Id` header (with membership
check) and `X-Project-Id` / `?project_id=` query parameter.  A project
variable and an org-global variable with the same key never collide.
Cross-org access always returns 404 — no information leak.

---

### `GET /variables`

List all variables for the caller's org in the resolved project scope.

**Auth:** Reader role.

**Response `200`:**
```json
[
  {
    "key": "default_region",
    "value": "us-east-1",
    "org_id": "<org-id>",
    "project_id": "<project-id or null>",
    "updated_by": "<user-id>",
    "updated_at": "2026-01-01T00:00:00Z"
  }
]
```

---

### `GET /variables/{key}`

Fetch a single variable by key within the caller's resolved org + project scope.

**Path parameters:**

| Param | Type | Description |
|---|---|---|
| `key` | string | Variable key (case-sensitive). |

**Response `200`:** Variable row (same shape as the list entry above).

**Errors:**
- `404 not_found` — variable not found in the caller's org+scope (also returned for cross-org keys — no leak).

---

### `PUT /variables/{key}`

Upsert a variable's value.  Creates the variable if it does not exist; updates
the value if it does.  The project scope is resolved from the request body's
`project_id` field (when set) or from the request context (`X-Project-Id` /
`?project_id=` / default project); org-global when none resolves.

**Auth:** Writer role (`require_writer`).

**Path parameters:**

| Param | Type | Description |
|---|---|---|
| `key` | string | Variable key (case-sensitive). |

**Request body:**
```json
{
  "value": <any JSON value>,
  "project_id": "<optional project id — overrides header/query scope>"
}
```

**Response `200`:** Updated variable row.

**Errors:**
- `403 insufficient_role` — caller is a viewer (read-only member).

---

### `DELETE /variables/{key}`

Delete a variable.

**Auth:** Writer role.

**Path parameters:**

| Param | Type | Description |
|---|---|---|
| `key` | string | Variable key to delete. |

**Response:** `204 No Content` on success.

**Errors:**
- `403 insufficient_role` — caller is a viewer.
- `404 not_found` — variable not found in the caller's org+scope.

---

## Usage & billing

Two layers. **Usage metering is open core** — the `usage_events` table and its
read views ship in every self-host build (billing off). **Billing routes are
Nubi Cloud only** — they live in the `ee/` tree ([open core](/docs/open-core))
and are mounted only when Nubi's Cloud deployment sets `NUBI_LICENSE_KEY`. A
self-host build never usage-limits and never exposes the `/ee/billing/*` routes.

### Usage (open core)

| Method | Path | Description |
|---|---|---|
| `GET` | `/usage` | Current usage totals for the caller's org, read from `usage_events`. |
| `GET` | `/usage/series` | Time-series of metered usage. |

### Billing (Nubi Cloud only)

Prices are in ZAR; payments via Paystack. See [Billing model](/docs/billing-model).

| Method | Path | Auth | Description |
|---|---|---|---|
| `GET` | `/pricing` | Public | Tier + price catalogue (no auth). |
| `GET` | `/ee/billing/tier` | Member | The org's current tier and quotas. |
| `POST` | `/ee/billing/checkout` | Member | Start a Paystack checkout for a tier/top-up. |
| `POST` | `/ee/billing/webhook` | Paystack HMAC | Paystack event sink (payment confirmations). |
| `GET` | `/ee/billing/events` | Member | Billing event history. |
| `GET` | `/ee/billing/invoices` | Member | List invoices. |
| `GET` | `/ee/billing/invoices/current-cycle` | Member | The in-progress cycle's running charges. |
| `GET` | `/ee/billing/invoices/{invoice_id}/pdf` | Member | Download an invoice PDF. |
| `GET` | `/ee/billing/wallet` | Member | Prepaid ZAR wallet balance. |
| `POST` | `/ee/billing/wallet/topup` | Member | Top up the wallet. |
| `PUT` | `/ee/billing/wallet/autotopup` | Member | Configure auto-top-up thresholds. |
| `GET` | `/ee/billing/admin/orgs/{org_id}` | Superadmin | Per-org billing state (support). |
| `PUT` | `/ee/billing/admin/orgs/{org_id}` | Superadmin | Override a org's tier/quota (disable billing, set overrides). |

### AI provider keys (Nubi Cloud)

| Method | Path | Description |
|---|---|---|
| `POST` | `/ai/keys` | Store a bring-your-own-model provider key (encrypted at rest). |
| `DELETE` | `/ai/keys` | Remove the stored provider key. |

Metered operations raise `402 quota_exceeded` when a Cloud quota is exhausted.
Metered dimensions: `compute_units`, `ai_calls`, `embedded_sessions`,
`agent_runs`, `storage_gb`.

---

## Error codes reference

| Code | HTTP | When |
|---|---|---|
| `unauthorized` | 401 | Missing or invalid Bearer token. |
| `insufficient_scope` | 403 | Token lacks the required scope. |
| `forbidden` | 403 | Embed token on a write endpoint; or role check failed. |
| `query_not_registered` | 403 | Embed token referenced an unregistered query_id. |
| `origin_mismatch` | 403 | `embed_origin` claim does not match the request `Origin` header. |
| `metric_not_found` | 404 | Unknown metric or cross-org attempt. |
| `org_not_found` | 404 | User has no org membership. |
| `quota_exceeded` | 402 | Compute or AI quota exhausted. |
| `invalid_definition` | 400 | Malformed `MetricDefinition` body. |
| `sweep_timeout` | 504 | Sweep exceeded the wall-clock limit. |
| `source_unsupported_rls` | 501 | Connector does not support predicate-level RLS. |

---

## Notes on Arrow IPC responses

Query and metric endpoints return `Content-Type: application/vnd.apache.arrow.stream`.
Parse with `apache-arrow` (JavaScript), `pyarrow` (Python), or any
Arrow-compatible library.

```js
import { tableFromIPC } from 'apache-arrow'
const resp = await fetch('/api/v1/metrics/revenue/query', {
  method: 'POST',
  headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
  body: JSON.stringify({ dimensions: ['region'], time_grain: 'month' }),
})
const table = await tableFromIPC(resp)
console.log(table.toArray())
```

```python
import pyarrow.ipc as ipc
import httpx

resp = httpx.post(
    'http://localhost:8000/api/v1/metrics/revenue/query',
    headers={'Authorization': f'Bearer {token}'},
    json={'dimensions': ['region'], 'time_grain': 'month'},
)
reader = ipc.open_stream(resp.content)
table = reader.read_all()
```

---

## Audit log

Org-scoped action audit trail. Records metadata only — no row data, no PII,
no secret material (POPIA-compliant). Entries are written fire-and-forget by
every mutation path; a failed write never breaks the mutation.

Auth: first-party bearer token required; caller must be **owner or admin**
(approver role). Viewers and members receive **403**.

### `GET /audit`

Returns paginated audit entries for the caller's org, newest-first.

**Query parameters:**

| Parameter | Type | Description |
|---|---|---|
| `resource_type` | string | Filter to a resource type (e.g. `board`, `connector`) |
| `action` | string | Filter to an action (e.g. `board.create`, `connector.delete`) |
| `actor` | string | Filter to a specific actor_user_id |
| `since` | ISO-8601 | Lower bound on `at` (inclusive) |
| `until` | ISO-8601 | Upper bound on `at` (inclusive) |
| `limit` | int 1–200 | Page size (default 50) |
| `offset` | int | Page offset (default 0) |

**Response shape:**

```json
{
  "items": [
    {
      "id": "uuid",
      "org_id": "uuid",
      "actor_user_id": "uuid",
      "actor_kind": "access",
      "action": "board.create",
      "resource_type": "board",
      "resource_id": "uuid",
      "summary": { "name": "My Board" },
      "at": "2026-06-26T10:00:00+00:00"
    }
  ],
  "total": 42,
  "limit": 50,
  "offset": 0
}
```

**Status codes:** 200 OK · 401 Unauthenticated · 403 Insufficient role (not owner/admin).

### `GET /audit/{resource_type}/{resource_id}`

Same shape as `GET /audit` but pre-filtered to a single resource's history.

**Status codes:** 200 OK · 401 Unauthenticated · 403 Insufficient role.

### Covered mutations

| Resource | Covered actions |
|---|---|
| boards, queries, datastores, widgets | `.create`, `.update`, `.delete` (via generic `/{resource}` CRUD) |
| connectors | `connector.create`, `connector.update`, `connector.delete` |
| mcp_server | `mcp_server.create`, `mcp_server.update`, `mcp_server.delete` |
| secret | `secret.set`, `secret.delete` |
