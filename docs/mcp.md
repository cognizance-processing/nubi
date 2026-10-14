# MCP — Model Context Protocol integration

Nubi implements MCP in two complementary directions:

1. **Host → Nubi registry** — a host registers its own MCP servers with Nubi so
   the Nubi agent loop can discover and call external tools.
2. **Nubi as an MCP server** — an external MCP client (Claude Desktop, another
   agent) can call Nubi's tool catalog over JSON-RPC.

All routes live under `/api/v1/mcp`.

---

## 1. Registering external MCP servers (host integration)

### Why

The Nubi agent loop discovers tools from two sources:

- Nubi's built-in tool registry (14+ tools).
- Per-org external MCP servers registered via this CRUD API.

When an agent is invoked, `combined_tool_schemas(claims)` merges built-in tools
with tools enumerated from every **enabled** server in the caller's org.
External tool names are namespaced as `serverName.toolName` so they never
collide with built-in names.

### Auth & scope

All write endpoints (POST / PUT / DELETE) require `writer` role or higher
(`require_writer_default` dependency). GET endpoints require any authenticated
session. All endpoints are org-scoped: callers can only see and modify servers
that belong to their own org.

### CRUD endpoints

```
GET    /api/v1/mcp/servers              List servers for caller's org (no secrets)
POST   /api/v1/mcp/servers              Register a new server (writer+)
GET    /api/v1/mcp/servers/{server_id}  Get one server (no secrets)
PUT    /api/v1/mcp/servers/{server_id}  Update a server (writer+)
DELETE /api/v1/mcp/servers/{server_id}  Delete a server (writer+) → 204
```

### Request body (POST)

```json
{
  "name": "my-bi-tools",
  "url": "https://tools.example.com/mcp",
  "transport": "http",
  "auth_token": "secret-bearer-token",
  "enabled": true
}
```

| Field | Required | Default | Meaning |
|-------|----------|---------|---------|
| `name` | yes | — | Stable identifier used to namespace tool calls: `name.toolName`. |
| `url` | yes | — | MCP server base URL. Validated against the SSRF guard before storage. |
| `transport` | no | `"http"` | Transport protocol. `"http"` (Streamable-HTTP) is the only currently shipped transport. |
| `auth_token` | no | null | Bearer secret sent to the external server. Encrypted at rest with AES-256-GCM; never returned on reads. |
| `enabled` | no | `true` | Disabled servers are not contacted by the agent loop. |

### Response (all reads)

Secret fields (`auth_token`, `secret`, `token`) are stripped before any read
response. The returned shape is:

```json
{
  "id": "uuid",
  "org_id": "uuid",
  "name": "my-bi-tools",
  "url": "https://tools.example.com/mcp",
  "transport": "http",
  "enabled": true,
  "created_by": "uuid",
  "created_at": "2026-06-24T10:00:00+00:00",
  "updated_at": "2026-06-24T10:00:00+00:00"
}
```

### SSRF guard

Every URL supplied in POST or PUT is validated by `app.connectors.ssrf.guard_url`
before the record is written. Requests to private RFC-1918 ranges, loopback, link-
local, metadata endpoints (`169.254.169.254`), and non-HTTP(S) schemes are
rejected with 400.

### Encryption

`auth_token` is encrypted at rest with AES-256-GCM (`app.security.crypto`). The
three cipher columns (`auth_secret_ciphertext`, `auth_secret_nonce`,
`auth_secret_key_version`) are never exposed on read paths. Decryption happens
only inside `get_enabled_for_org`, which is called exclusively by the agent loop
running server-side.

---

## 2. How the agent loop discovers and calls external MCP tools

When the agent loop builds its tool catalog for a request it calls
`combined_tool_schemas(claims)` from `app.ai.mcp_tools`:

1. `_builtin_tool_schemas()` — the static registry of 14+ Nubi built-in tools.
2. `get_mcp_tool_schemas(org_id)` — for each enabled MCP server in the org,
   calls `list_tools_sync(server)` over Streamable-HTTP and collects the
   advertised tool definitions.

Each external tool is exposed with a namespaced name:

```
{server_name}.{tool_name}
```

For example, a server registered as `"my-bi-tools"` that advertises a
`"run_report"` tool appears to the agent as `"my-bi-tools.run_report"`.

### Tool dispatch

`combined_execute_tool(name, arguments, claims)` routes calls:

- **No dot in name** → built-in tool registry (`execute_tool`).
- **Dot in name** → split on first dot → look up server by `server_name` in
  org's enabled servers → call `call_tool_sync(server, tool_name, arguments)`.

On SSRF block or network failure `call_tool_sync` returns
`{"error": {...}, "is_error": true}` and never raises past the boundary.

### MCP client transport

`app.ai.mcp` uses the official `mcp` Python SDK's `streamablehttp_client` +
`ClientSession`. Default timeouts: 10 s for `list_tools`, 30 s for `call_tool`.

---

## 3. Nubi as an MCP server — `POST /api/v1/mcp`

External MCP clients (e.g. Claude Desktop) can connect to Nubi as an MCP
server. The endpoint speaks JSON-RPC 2.0 over a single HTTP POST.

### Auth

```
Authorization: Bearer <first-party JWT or nubi_ak_… API key>
```

Same `current_user` + `verified_identity` dependencies as `/api/v1/ai/chat`.
The token's org and scope are read from the verified identity; they are never
taken from the request body.

For an external client that stays connected across sessions (Claude Desktop,
Claude Code), use a long-lived **API key** (`nubi_ak_…`, minted from
Settings → Connections in the app, or `POST /auth/api-keys`) rather than the
15-minute session JWT — `verified_identity` resolves either credential to the
same normalised identity (`app/auth/deps.py`).

Browser-hosted clients (claude.ai, ChatGPT) can't be given a key, so they
connect over **OAuth** instead — see [§ 4](#4-oauth-for-browser-ai-apps).
An OAuth access token (`nubi_oat_…`) resolves to the same identity an API
key does, but is accepted **only** on this endpoint.

A request without a valid token gets `401` with
`WWW-Authenticate: Bearer resource_metadata="<base>/.well-known/oauth-protected-resource/api/v1/mcp"`,
which is how a remote client discovers where to sign in.

### Protocol version

The server echoes the client's `protocolVersion` when it is one of
`2025-06-18`, `2025-03-26`, `2024-11-05`, and answers `2025-06-18` otherwise.
Responses are plain JSON (no SSE stream, no session id); `GET`/`DELETE` on
the endpoint return `405`. A message without an `id` is a notification and
gets `202` with no body. `ping` returns `{}`.

### Methods

#### `initialize`

Request:
```json
{ "jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {} }
```

Response:
```json
{
  "jsonrpc": "2.0", "id": 1,
  "result": {
    "protocolVersion": "2024-11-05",
    "capabilities": { "tools": {} },
    "serverInfo": { "name": "nubi", "version": "1.0.0" }
  }
}
```

#### `tools/list`

Returns Nubi's full built-in tool catalog in MCP wire format
(`inputSchema` instead of `input_schema`):

```json
{
  "jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}
}
```

Response:
```json
{
  "result": {
    "tools": [
      {
        "name": "run_query",
        "description": "Execute a registered query ...",
        "inputSchema": { "type": "object", "properties": { ... } }
      },
      ...
    ]
  }
}
```

#### `tools/call`

```json
{
  "jsonrpc": "2.0", "id": 3,
  "method": "tools/call",
  "params": {
    "name": "list_metrics",
    "arguments": {}
  }
}
```

Success response:
```json
{
  "result": {
    "content": [{ "type": "text", "text": "{\"metrics\": [...]}" }],
    "isError": false
  }
}
```

Error response (tool execution failed):
```json
{
  "result": {
    "content": [{ "type": "text", "text": "metric_not_found: No metric ..." }],
    "isError": true
  }
}
```

JSON-RPC protocol errors (parse error, unknown method) use standard error
objects:
```json
{ "error": { "code": -32700, "message": "Parse error" } }
```

### Tool catalog (built-in)

| Tool | What it does |
|------|-------------|
| `get_schema` | Return catalog schema (tables + columns) from the query registry |
| `list_queries` | List registered queries with their ids, names, and params |
| `generate_sql` | NL → grounded SQL SELECT |
| `create_query` | Register a SQL query in the registry |
| `run_query` | Execute a registered query or ad-hoc SELECT (RLS enforced) |
| `list_metrics` | List governed metric definitions |
| `query_metric` | Execute a governed metric query (RLS enforced) |
| `create_dashboard` | Generate a DashboardSpec from a NL question |
| `edit_dashboard` | Apply add/move/configure/remove widget operations to a spec |
| `create_flow` | Create a new flow |
| `run_flow` | Trigger a flow run |
| `get_flow_run` | Get a flow run's status and task results |

### Security contract

**Org scope** — `org_id` is taken from the verified JWT, never from request
params. The tool call builds claims from the actual token:

```python
claims = {
    "kind": identity.kind,
    "sub": str(user["id"]),
    "org": org_id,
    "policies": dict(identity.policies or {}),
    "scope": list(identity.scope or []),
}
```

**RLS enforcement** — tool calls that touch data (`run_query`, `query_metric`)
pass these claims to the planner, which injects AST-level WHERE predicates from
`claims["policies"]`. A read-only token can never widen its scope through the
MCP path.

**Raw SQL gating (C1/C2 fix)** — `run_query` with an ad-hoc `sql` argument
requires `author:sql` scope on the token **and** `kind == "access"`. Embed
tokens (kind `"embed"`) are always blocked from raw SQL regardless of scope.

**Scope passed through** — `scope` is copied from the token verbatim. It is
never hard-coded to `write:*` so a restricted token cannot escalate privilege
via the MCP path.

---

## 4. OAuth for browser AI apps

Nubi is its own OAuth 2.1 authorization server, following the MCP
authorization spec (`app/routes/oauth.py`, state in `app/auth/oauth.py`):

| Step | Endpoint |
|---|---|
| Resource metadata (RFC 9728) | `GET /.well-known/oauth-protected-resource[/api/v1/mcp]` |
| Server metadata (RFC 8414) | `GET /.well-known/oauth-authorization-server` |
| Dynamic client registration (RFC 7591) | `POST /api/v1/oauth/register` |
| Authorize → SPA consent page | `GET /api/v1/oauth/authorize` → `/oauth/consent?request=…` |
| Code / refresh exchange | `POST /api/v1/oauth/token` |
| Revocation (RFC 7009) | `POST /api/v1/oauth/revoke` |
| Connected apps (Settings) | `GET/DELETE /api/v1/oauth/connections[/{id}]` |

What the user approves is a **grant** — (app, user, one org). On the consent
page they sign in, see which host the app will send them back to (claude.ai
and chatgpt.com are marked as recognised; anything else carries a warning,
since any app can register under any name), pick an org, and approve.

Security properties:

- PKCE `S256` is mandatory; redirect URIs must be HTTPS (or loopback HTTP)
  and match exactly; a requested `resource` must be this MCP endpoint.
- Codes are single-use (5 min); replaying one revokes its grant. Refresh
  tokens rotate on every use; presenting a rotated one revokes the grant.
- Access tokens live 1 hour, refresh tokens 30 days. Only SHA-256 digests of
  codes and tokens are stored.
- The token is audience-bound: it works on `POST /api/v1/mcp` (and the REST
  calls its `api_request` tool makes, behind that tool's deny-list) and is
  refused everywhere else — a token leaked from a chat app can't, say, mint
  an API key. `api_request` cannot reach `/oauth/*`.
- Every use re-checks that the user is still a member of the org; removing
  someone from an org disconnects their apps immediately.
- Only an interactive sign-in can approve a connection — an API key or
  another app's token cannot.
- Connect and disconnect are audit-logged (`oauth.connect`, `oauth.disconnect`).

**Deployment:** set `PUBLIC_API_URL` to the API's public origin (e.g.
`https://api.example.com`). It is the OAuth issuer and the resource id
clients bind to; when unset it is derived from the request, which is only
right if no proxy rewrites the host or scheme. The API must be reachable over
public HTTPS for claude.ai or ChatGPT to connect.

---

## Quick start — connect your own AI

### Claude or ChatGPT in the browser

In the app, **Settings → Connections** shows the connector URL
(`<api>/api/v1/mcp`). Add it as a custom connector — in Claude under
Settings → Connectors, in ChatGPT as a developer-mode connector with OAuth —
then sign in to Nubi and approve when prompted.

### Claude Desktop / Claude Code with an API key

In the app, go to **Settings → Connections** and generate a connection key
(an API key, `nubi_ak_…`, scoped to your org). The page shows the raw key
exactly once, plus ready-to-paste snippets for both clients below —
copy/paste is all that's needed; the steps here are what those snippets do.

### Claude Code

```bash
claude mcp add --transport http nubi http://localhost:8000/api/v1/mcp \
  --header "Authorization: Bearer <your-nubi_ak_-key>"
```

### Claude Desktop

Add to `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "nubi": {
      "url": "http://localhost:8000/api/v1/mcp",
      "transport": { "type": "http" },
      "headers": {
        "Authorization": "Bearer <your-nubi_ak_-key>"
      }
    }
  }
}
```

Then ask Claude: "List my Nubi metrics" — it will call `tools/list` then
`list_metrics` automatically.
