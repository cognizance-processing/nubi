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

## API sections

This reference is split into focused pages — jump to the area you need:

| Section | Covers |
|---|---|
| **[Authentication & access](/docs/api-auth)** | API keys, sessions, scopes, and access grants |
| **[Core resources](/docs/api-resources)** | Projects, connectors, queries, boards, exports, embedding, scheduled jobs |
| **[Metrics & data](/docs/api-analytics)** | The semantic layer, DataProvider boards, data health, schema drift, SQL transpilation |
| **[AI & MCP](/docs/api-ai)** | AI endpoints and the MCP server |
| **[Flows & variables](/docs/api-flows)** | Flow runs, versions, environments, and variables |
| **[Usage & billing](/docs/api-billing)** | Usage metering and billing endpoints |

The [Conventions](#conventions), [Error codes reference](#error-codes-reference), [Arrow IPC notes](/docs/api-reference#notes-on-arrow-ipc-responses), and [Audit log](#audit-log) below apply across every section.

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
