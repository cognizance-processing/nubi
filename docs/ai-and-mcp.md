# AI, Chat & MCP

Nubi's AI surface lets LLMs and MCP agents author dashboards, run queries, inspect lineage, and propose pre-aggregations — without ever writing fetch, WebGL, or auth code. A Slack and WhatsApp chat gateway routes inbound messages through the same agentic loop, so your data is a message away.

---

## LLM Providers

Configure the provider via environment variables:

| Variable | Value |
|----------|-------|
| `LLM_PROVIDER` | `anthropic` \| `openai` \| `gemini` (default: `null` → `NullProvider`) |
| `ANTHROPIC_API_KEY` | API key when `LLM_PROVIDER=anthropic` |
| `OPENAI_API_KEY` | API key when `LLM_PROVIDER=openai` |
| `GEMINI_API_KEY` | API key when `LLM_PROVIDER=gemini` |

The `NullProvider` is deterministic and offline — it returns canned responses. No API keys required for development.

---

## Grounded Ask — `POST /api/v1/ai/ask`

Accepts a natural-language question, runs the deterministic grounding pipeline (token-overlap scoring over the query registry + lineage graph), calls the LLM, and returns a SQL suggestion with grounding context.

```json
POST /api/v1/ai/ask
{ "question": "What was our revenue by region last quarter?" }
```

Response:

```json
{
  "grounding": {
    "relevant_tables":  ["sales", "regions"],
    "relevant_columns": [{"table": "sales", "column": "revenue"}],
    "related_queries":  ["revenue_by_month"],
    "snippets":         [...]
  },
  "suggestion": "SELECT region, SUM(revenue) FROM sales WHERE quarter = 'Q4' GROUP BY 1",
  "provider":   "anthropic"
}
```

---

## Text-to-SQL — `POST /api/v1/ai/sql`

Grounded SQL generation with optional auto-registration. Validates the generated SQL with sqlglot.

```json
POST /api/v1/ai/sql
{
  "question":     "revenue by month for a given region",
  "datastore_id": null,
  "save_as":      "revenue_by_month_region"
}
```

Response:

```json
{
  "sql":           "SELECT month, SUM(revenue) FROM sales WHERE region = {{region}} GROUP BY 1",
  "valid":         true,
  "issues":        [],
  "provider":      "null",
  "grounding":     { ... },
  "registered_id": "revenue_by_month_region"
}
```

When `save_as` is provided, the generated SQL is registered into the query registry. `{{name}}` placeholders in the generated SQL are automatically inferred as `QueryParam` descriptors (type `text`, not required, no default).

---

## AI Dashboard Generation — `POST /api/v1/ai/dashboard`

Generates a full `DashboardSpec` + compiled HTML from a natural-language question.

```json
POST /api/v1/ai/dashboard
{ "question": "Show me revenue by region for Q1 2024" }
```

Pipeline:
1. **Grounding** — `build_catalog` inspects the connected query registry + lineage graph.
2. **LLM generation** — the provider generates a `DashboardSpec` referencing real registered query IDs and real column names.
3. **Compilation** — `spec_to_html` compiles the spec to a CSS-grid HTML fragment.
4. **Validation** — `validate_dashboard_html` runs server-side sanity checks.
5. **Response** — returns spec dict, HTML, grounding, provider name, and validation result.

Get the JSON Schema for the spec (for grounding your own LLMs):

```
GET /api/v1/ai/dashboard/schema
```

---

## Agentic Chat — `POST /api/v1/ai/chat`

The agentic chat endpoint runs a multi-step tool-calling loop and returns the final reply plus a log of all tool actions taken.

```json
POST /api/v1/ai/chat
{
  "messages": [
    { "role": "user", "content": "Show me a dashboard of revenue by region" }
  ],
  "board_id": null
}
```

Response:

```json
{
  "reply":   "I've created a Revenue by Region dashboard for you.",
  "actions": [
    { "tool": "generate_sql",     "arguments": {...}, "result": {...} },
    { "tool": "create_dashboard", "arguments": {...}, "result": {...} }
  ]
}
```

### Agent Tool Registry

The agent has access to these 7 tools (from `app.ai.tools`):

| Tool | Description |
|------|-------------|
| `get_schema` | Return the catalog schema (tables + columns) from the query registry + lineage graph. |
| `list_queries` | Return all registered queries with their ids, names, and param descriptors. |
| `generate_sql` | Generate a grounded SQL SELECT from a natural-language question. |
| `create_query` | Register a query in the query registry under a given id. |
| `run_query` | Execute a registered query (or ad-hoc SELECT) and return JSON rows. |
| `create_dashboard` | Generate a `DashboardSpec` for a natural-language question, compile to HTML, and validate. |
| `edit_dashboard` | Apply an edit operation (`add_widget` / `move_widget` / `configure_widget` / `remove_widget`) to a DashboardSpec and re-validate. |

All tool calls pass `claims` through to the planner — the agent never exceeds the caller's auth scope. `run_query` injects RLS predicates from `claims["policies"]` before executing.

### `edit_dashboard` Operations

```json
{ "action": "add_widget",      "widget": { ...Widget... } }
{ "action": "move_widget",     "widget_id": "w1", "pos": {"x":1,"y":2,"w":4,"h":2} }
{ "action": "configure_widget","widget_id": "w1", "updates": {"props": {"label": "Q4 Revenue"}} }
{ "action": "remove_widget",   "widget_id": "w1" }
```

Returns `{spec, valid, issues}`. The `id` and `type` fields of an existing widget are immutable; `configure_widget` ignores them.

### NullProvider Scripted Path

With NullProvider (no API key), the agent follows a deterministic scripted path based on intent keywords:

- Contains "chart", "dashboard", "visuali", "graph", "plot" → `generate_sql → create_dashboard → reply`
- Contains "run", "query", "execute", "fetch", "show", "list" → `generate_sql → run_query → reply`
- Any other message → `generate_sql → reply`

---

## MCP Server

The Nubi MCP server exposes 6 tools to any MCP-compatible client (Claude Desktop, Claude Code, etc.) via stdio transport.

### Installation

```bash
cd mcp
pip install -r requirements.txt
```

### Running

```bash
python -m nubi_mcp.server
```

### Tools

| Tool | Signature | Description |
|------|-----------|-------------|
| `list_dashboards` | `() → [{id, name}]` | List all registered dashboards/queries |
| `run_query` | `(query_id, limit=100) → {columns, rows, row_count}` | Execute a registered query and return a JSON preview |
| `list_lineage` | `() → {available, graph}` | Return the SQL lineage graph |
| `propose_materialized_view` | `() → [{base_table, dimensions, measures, hit_count, bytes_saved}]` | Suggest pre-aggregation rollups from the query log |
| `create_dashboard` | `(name, html, org_id) → {id, name}` | Validate and store a dashboard HTML document |
| `author_dashboard` | `(question) → {id, html_preview}` | Ground a question and auto-generate a dashboard |

### Registering with Claude Desktop

Add to `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "nubi": {
      "command": "python",
      "args": ["-m", "nubi_mcp.server"],
      "cwd": "/absolute/path/to/nubi/mcp"
    }
  }
}
```

### Registering with Claude Code

```bash
claude mcp add nubi -- python -m nubi_mcp.server
```

---

## Dashboard Authoring Rules for LLMs

When calling `create_dashboard` or `author_dashboard`, generated HTML must follow these rules:

1. Use only `<nubi-kpi>`, `<nubi-table>`, `<nubi-chart>`, `<nubi-filter>`, `<nubi-text>` widget elements — plus standard layout HTML.
2. No `<script>` tags. No inline event handlers (`onclick`, `onload`, etc.).
3. No `javascript:` URLs.
4. Widget `query-id` attributes must reference registered query ids.
5. Widget `get-token` and `backend` attributes are added by the Nubi SDK at render time — do not hard-code tokens.

The DOMPurify sanitizer enforces these rules server-side. Non-conformant HTML is rejected with a validation error before storage.

---

## SQL Lineage

`GET /api/v1/lineage` returns the lineage graph extracted by sqlglot from the query registry:

```json
{
  "nodes": ["sales", "orders", "revenue_summary"],
  "edges": [
    { "from": "sales",  "to": "revenue_summary" },
    { "from": "orders", "to": "revenue_summary" }
  ]
}
```

The MCP `list_lineage` tool surfaces the same graph. If the lineage module is not yet available, the tool returns `{ "available": false, "reason": "..." }` rather than crashing.

---

## Slack & WhatsApp Chat Gateway

Nubi's chat gateway receives inbound messages from Slack and WhatsApp and routes them through the agentic AI loop. The actual reply is delivered back through the messaging platform.

### Slack Webhook

```
POST /api/v1/chat/slack
```

Verifies the request signature using **HMAC-SHA256** over the raw request body with `SLACK_SIGNING_SECRET`. Returns 200 on success, 401 if the signature is invalid. The endpoint delegates to `handle_inbound("slack", payload)` which normalises the payload and calls the agent.

Configure in Slack's App settings → Event Subscriptions → Request URL.

Required env var: `SLACK_SIGNING_SECRET`

### WhatsApp Webhook

```
POST /api/v1/chat/whatsapp
```

Verifies the `X-Hub-Signature-256` header using **HMAC-SHA256** with `WHATSAPP_APP_SECRET`. Returns 200 on success, 401 if the signature is invalid.

Configure in the WhatsApp Cloud API → Webhooks settings.

Required env var: `WHATSAPP_APP_SECRET`

### Response Shape

Both endpoints return:

```json
{
  "ok":       true,
  "text":     "Here is your revenue by region dashboard...",
  "has_image": false
}
```

When the agent produces a chart, `has_image` is `true` and `image_png` carries a PNG byte blob delivered as an attachment via the platform adapter.

> **Authentication note:** Both webhook endpoints do NOT require a Nubi Bearer token — they are external webhook entry points. HMAC signature verification is the authentication mechanism.
