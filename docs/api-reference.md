# API Reference

Complete reference for the Nubi HTTP API. All endpoints are under the
`/api/v1` prefix unless noted otherwise. The interactive Swagger UI is
available at `/docs` in development mode (`ENV=development`).

---

## Authentication

All endpoints require a Bearer token in the `Authorization` header:

```
Authorization: Bearer <token>
```

Two token kinds are supported:

| Kind | Issued by | Use case |
|---|---|---|
| **First-party access token** | `POST /auth/login` or Google OAuth | Interactive users, CLI, MCP server, AI agents |
| **Embed JWT** | Your backend (RS256/ES256, registered issuer) | Embedded dashboards, per-viewer RLS |

Embed tokens are read-only and can only reference registered queries by
`query_id` — raw SQL is blocked on the embed path.

Error responses use the envelope `{ "error": "<code>", "message": "<text>" }`.

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

## Canvas

### `POST /canvas/validate`

Stateless validation oracle for a `CanvasDoc`. Never persists anything.

**Auth:** First-party access token.

**Request body:**
```json
{ "doc": { "version": 1, "title": "...", "html": "...", "bindings": {} } }
```

**Response `200`:**
```json
{
  "valid": true,
  "errors": [],
  "warnings": ["[WARN] binding el_3: connector_id not found in registry"]
}
```

---

### `GET /canvases`

List all canvases for the caller's org.

**Auth:** First-party access token.

**Response `200`:** Array of canvas records `[{id, org_id, created_by, name, config, created_at, updated_at}]`.

---

### `POST /canvases`

Create a new canvas resource.

**Auth:** First-party access token, writer role. Embed tokens are blocked.

**Request body:**
```json
{
  "name": "Q3 Exec Brief",
  "config": {
    "doc": {
      "version": 1,
      "title": "Q3 Exec Brief",
      "html": "<section><h1>Q3 Summary</h1><nubi-kpi data-el-id=\"el_1\"></nubi-kpi></section>",
      "bindings": {
        "el_1": { "kind": "query", "query_id": "revenue_total", "field": "total", "format": "currency" }
      },
      "variables": []
    }
  }
}
```

When `config.doc` is provided it is parsed as a `CanvasDoc` and validated.
Hard validation failures (script injection, missing bindings) return `400`.

**Response `201`:** Canvas record.

---

### `GET /canvases/{canvas_id}`

Retrieve a canvas by id (org-scoped).

**Response `200`:** Canvas record.

**Errors:** `404 canvas_not_found`.

---

### `PUT /canvases/{canvas_id}`

Update a canvas name and/or config.

**Auth:** First-party access token, writer role.

**Request body (all fields optional):**
```json
{ "name": "New title", "config": { "doc": { ... } } }
```

At least one of `name` or `config` must be provided. `config.doc` is
re-validated when present.

**Response `200`:** Updated canvas record.

---

### `DELETE /canvases/{canvas_id}`

Delete a canvas (org-scoped, writer role).

**Response `204`:** No content.

---

### `POST /canvases/{canvas_id}/schedule`

Create a `report_send` flow that delivers a canvas on a schedule.

**Auth:** First-party access token, writer role.

**Request body:**
```json
{
  "format": "html",
  "recipients": ["cfo@example.com", "ceo@example.com"],
  "subject": "Weekly Q3 Brief",
  "body": "Please find the weekly report attached.",
  "params": { "region": "EMEA" },
  "locked_params": { "cfo@example.com": { "region": "EMEA" } },
  "cron": "0 8 * * MON",
  "flow_name": "q3_brief_weekly"
}
```

| Field | Required | Description |
|---|---|---|
| `format` | No | `"html"` (default) or `"pdf"` |
| `recipients` | Yes | Email addresses |
| `subject` | No | Email subject (defaults to canvas name) |
| `body` | No | Plain-text email body |
| `params` | No | Base query parameters |
| `locked_params` | No | Per-recipient param overrides (for RLS) |
| `cron` | No | Cron expression e.g. `"0 8 * * MON"` |
| `flow_name` | No | Human name for the created flow |

**Response `201`:**
```json
{ "flow_id": "...", "canvas_id": "...", "format": "html", "recipients_count": 2 }
```

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

### `POST /ai/canvas`

Generate a `CanvasDoc` from a natural-language description. Runs a
generate → validate → repair loop (up to `MAX_DASHBOARD_REPAIR_ROUNDS`).

**Request body:**
```json
{ "question": "Exec brief showing total revenue and fill rate by region", "model": null }
```

**Response `200`:**
```json
{
  "doc": { "version": 1, "title": "...", "html": "...", "bindings": {} },
  "html": "<section>...</section>",
  "grounding": { ... },
  "provider": "litellm",
  "valid": true,
  "issues": []
}
```

**Errors:** `422 canvas_generation_failed` — LLM output still invalid after max repair rounds.

---

### `POST /ai/canvas/edit`

Apply a natural-language edit to an existing `CanvasDoc`. Runs the same
validate → repair loop.

**Request body:**
```json
{
  "doc": { "version": 1, "title": "...", "html": "...", "bindings": {} },
  "instruction": "Make the header blue and add a revenue KPI",
  "model": null
}
```

**Response `200`:** Same shape as `POST /ai/canvas`.

**Errors:** `422 canvas_edit_failed`.

---

### `GET /ai/canvas/schema`

Return the JSON Schema for `CanvasDoc` and the `CanvasBinding` types. Use
this to ground an LLM before authoring Canvas HTML.

**Response `200`:** JSON Schema dict.

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

### `POST /flows/writeback/preview`

Dry-run a write-back — returns the rows/diff that would be written without
touching the connector.

**Auth:** Writer role (owner / admin / member).

**Request body:**
```json
{
  "rows": [{ "sku_id": "A1", "price": 9.99 }],
  "target": { "connector_id": "pg_warehouse", "table": "prices" },
  "mode": "upsert",
  "key_columns": ["sku_id"]
}
```

**Response `200`:**
```json
{
  "rows": [{ "sku_id": "A1", "price": 9.99 }],
  "row_count": 1,
  "target_object": "prices",
  "mode": "upsert",
  "dry_run": true
}
```

**Errors:**
- `400 row_cap_exceeded` — rows exceeds server cap (default 10 000, configurable via `NUBI_MAX_WRITEBACK_ROWS`).

---

### `POST /flows/writeback`

Submit a write-back (commit immediately or gate for approval).

**Auth:** Writer role.

**Request body:**
```json
{
  "rows": [{ "sku_id": "A1", "price": 9.99 }],
  "target": { "connector_id": "pg_warehouse", "table": "prices" },
  "mode": "upsert",
  "key_columns": ["sku_id"],
  "idempotency_key": "run-uuid:write_prices",
  "approval_required": false,
  "dry_run": false
}
```

| Field | Description |
|---|---|
| `mode` | `"upsert"` / `"insert"` / `"replace"` |
| `key_columns` | Required for upsert — columns that identify the row. |
| `idempotency_key` | Caller-supplied key; a second call with the same key returns the existing record without re-applying. Recommended: `<flow_run_id>:<task_key>`. |
| `approval_required` | When `true`, the record enters `pending_approval` state. When the server-wide `NUBI_WRITEBACK_REQUIRE_APPROVAL=true` env var is set, approval is forced regardless of this field. |
| `dry_run` | When `true`, handled identically to `POST /flows/writeback/preview`. |

**Response `201`:** Write-back record `{id, state, rows, row_count, target_object, mode, idempotency_key, created_at}`.

States: `submitted` → `committed` (immediate) or `pending_approval` → `committed` / `rejected`.

---

### `POST /flows/writeback/{id}/approval`

Approve or reject a pending write-back.

**Auth:** Approver role (owner / admin).

**Request body:**
```json
{ "action": "approve" }
```

`action` ∈ `"approve"` | `"reject"`.

**Response `200`:** Updated write-back record with new `state`.

---

### `GET /flows/writeback`

List write-back records for the caller's org.

**Response `200`:** Array of write-back records.

---

### `GET /flows/writeback/{id}`

Retrieve a single write-back record.

**Response `200`:** Write-back record.

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

## Error codes reference

| Code | HTTP | When |
|---|---|---|
| `unauthorized` | 401 | Missing or invalid Bearer token. |
| `insufficient_scope` | 403 | Token lacks the required scope. |
| `forbidden` | 403 | Embed token on a write endpoint; or role check failed. |
| `query_not_registered` | 403 | Embed token referenced an unregistered query_id. |
| `origin_mismatch` | 403 | `embed_origin` claim does not match the request `Origin` header. |
| `metric_not_found` | 404 | Unknown metric or cross-org attempt. |
| `canvas_not_found` | 404 | Unknown canvas or cross-org attempt. |
| `org_not_found` | 404 | User has no org membership. |
| `quota_exceeded` | 402 | Compute or AI quota exhausted. |
| `row_cap_exceeded` | 400 | Write-back row count exceeds the server cap. |
| `invalid_definition` | 400 | Malformed `MetricDefinition` body. |
| `invalid_canvas_doc` | 400 | Canvas doc fails security validation. |
| `sweep_timeout` | 504 | Sweep exceeded the wall-clock limit. |
| `canvas_generation_failed` | 422 | LLM output invalid after max repair rounds. |
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

## Screenshot reference

| Screenshot | What to show | Target doc |
|---|---|---|
| ![Swagger UI](screenshots/api-swagger.png) | `/docs` Swagger UI in development mode | This page |
| ![Arrow response](screenshots/api-arrow-response.png) | Network tab showing `application/vnd.apache.arrow.stream` response | This page |
