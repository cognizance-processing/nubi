# AI & MCP

> Part of the [API reference](/docs/api-reference) — see it for conventions, error codes, and the audit log.

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
