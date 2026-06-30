# AI, chat & MCP

![Ask questions, build dashboards, and explore data with Nubi AI](illustration:LlmDashboards)

Nubi has a built-in AI assistant that lives on every page of the app, plus an MCP server that lets external agents (Claude Desktop, Claude Code, and other MCP clients) reach into your workspace. The in-app agent can write and run SQL, explore your schema, and build dashboards for you — and because it runs through Nubi's query planner, it stays inside your row-level-security boundary the whole time.

This page covers:

- The **Nubi AI chat panel** — what you click and what you see.
- **Grounded text-to-SQL** — turning a question into a validated query.
- **Natural-language dashboard generation** — describing a dashboard and watching it build.
- The **dashboard editor assistant** — conversational edits on a live board.
- The **MCP server** — 15 tools that external agents use to author dashboards, run queries, validate specs, explore metrics, and promote builds.

---

## The Nubi AI chat panel

The assistant is always one click away from anywhere in the app.

### Opening chat

1. Look at the top-right of the topbar for the **chat button** (speech-bubble icon).
2. Click it to slide the **Nubi AI** panel in from the right. Click it again — or the **✕** in the panel header — to close. On a small screen the panel opens full-screen.
3. When the panel is empty you'll see a welcome card with four starter prompts you can tap to send instantly:
   - **Build a sales dashboard**
   - **Show revenue by region**
   - **Which queries run slowest?**
   - **Summarise connected data sources**

> The global chat button is hidden on pages that embed their own assistant. Most notably, the dashboard editor has its own chat tuned for the board in front of you — see [The dashboard editor assistant](#the-dashboard-editor-assistant).

### Choosing a model

Next to the close button in the panel header is a model picker. Options reflect the providers your workspace has configured (Anthropic Claude, OpenAI GPT-4o, Google Gemini, or a self-hosted model). The **Nubi Default** option uses whatever provider the workspace admin set in Organization Settings.

If no provider API key is configured, the assistant still runs in a deterministic offline mode — useful for trying the flow end-to-end without connecting an LLM.

### Sending a message

1. Type into the box at the bottom. Press **Enter** to send; use **Shift+Enter** for a newline.
2. While the assistant works the send button becomes a **stop** button (■). Click it at any time to cancel.

### Watching the assistant work

Nubi's chat streams its work as it happens — you watch each step, not just a final answer.

- A pulsing **status line** ("Thinking…", "Running query…") appears first.
- Each tool the agent calls shows up as a **tool block** that animates from *running…* to a result, with a spinner that turns into a green check on success or a red alert on failure.
- The written reply then **streams in token by token** with a blinking caret.

Tool blocks are collapsed by default. **Click any block to expand it** and see the exact arguments and the full result. Each block is labelled by what it does:

| Tool block | What you see when expanded |
|---|---|
| **Get schema** | The catalog (tables + columns) the assistant is grounding against. |
| **List queries** | Your registered queries with their ids and parameters. |
| **Generate SQL** | The generated SQL, a `valid` / `needs review` badge, the tables referenced, and any validation issues. |
| **Create query** | The query id and SQL that was saved to the registry. |
| **Run query** | A row/column count and a preview of the first rows. |
| **Create dashboard** | The dashboard spec and a chip for each widget type added. |
| **Edit dashboard** | The applied operation (add/move/configure/remove widget) and the re-validated spec. |

Because every step is visible, you can always see why the assistant answered the way it did — which tables it scanned, which SQL it ran, how many rows came back.

### What the assistant can do

Just ask in plain language. Common requests:

- **"Show me revenue by region last quarter."** → generates SQL, runs it, and summarises the result with a preview table.
- **"Build a sales dashboard."** → generates the SQL behind each widget and assembles a live dashboard.
- **"Which of my queries scan the most data?"** → lists and inspects your registered queries.
- **"Summarise the data sources I have connected."** → reads the catalog and explains what's available.

The assistant only ever queries data you're allowed to see. Its access is scoped to your account and your organisation's row-level-security policies.

---

## Grounded text-to-SQL

When you ask a data question, Nubi doesn't send a blank prompt to the model and hope. It **grounds** the request first: it reads your query registry and lineage graph to find the tables and columns that actually relate to your question, then instructs the model to write SQL against only those real names. The generated SQL is parsed and validated before you see it.

In chat this happens automatically inside the **Generate SQL** tool block. Expand it to see:

- The SQL itself.
- A **`valid`** or **`needs review`** badge (Nubi parses the SQL with sqlglot to check it).
- The tables it references.
- Any issues the validator caught.

```sql
SELECT region, SUM(revenue) AS revenue
FROM sales
WHERE quarter = 'Q4'
GROUP BY region
ORDER BY revenue DESC
```

**How grounding works under the hood:** the pipeline tokenises your question, scores each table and column by token overlap, keeps the top-5 tables and top-20 columns, and injects only those into the LLM prompt. Tables with zero relevance score are excluded entirely — the model never even sees them, so it can't hallucinate them into the SQL.

To **keep** a generated query, ask the assistant to save it (e.g. *"save this as revenue_by_region"*). Saved queries get a stable id and any `{{placeholder}}` in the SQL becomes a typed parameter. See [Queries & Parameters](/docs/queries-and-params) for the full parameter system.

---

## Natural-language dashboard generation

Ask for a dashboard and Nubi builds a real one — not a screenshot, a live, cross-filtering board bound to your queries.

1. In chat, type something like **"Build a revenue dashboard for Q1 by region"** (or tap the **Build a sales dashboard** suggestion).
2. Watch the **Generate SQL** block produce the query each widget will read from.
3. Watch the **Create dashboard** block assemble the board. Expand it to see the dashboard name and a chip for each widget added.
4. The assistant replies with a summary and a link to open the dashboard.

Under the hood Nubi generates a structured **DashboardSpec** (referencing real query ids and real column names), compiles it to dashboard HTML, and validates it. Dashboards are composed only of Nubi's sandboxed widget elements — so a generated dashboard can never contain scripts or unsafe markup. Widgets are limited to the types Nubi supports: `kpi`, `metric`, `chart`, `table`, `pivot`, `filter`, `text`, and `section`. See [Dashboards](/docs/dashboards) for the full widget and chart reference.

---

## The dashboard editor assistant

The dashboard editor has its own embedded assistant, tuned for changing the board you're currently editing.

1. Open a dashboard in the editor.
2. Use the editor's chat to describe a change in plain language — for example *"add a KPI for total orders"*, *"turn the bar chart into a line chart"*, or *"remove the region filter"*.
3. The assistant proposes an updated spec. When it has one ready, you get an **Apply** button — clicking it updates the live board in front of you.
4. The panel keeps a conversation history and a **New chat** button so you can start a fresh thread without losing the board.

This is the conversational counterpart to the drag-and-drop canvas: edit by hand, by chat, or both.

> You can also inspect and hand-edit the raw spec in the editor's **Code** panel (the slide-over showing YAML/JSON). Changes made there are validated before being applied.

---

## MCP server — let external agents author dashboards

Nubi ships a **Model Context Protocol (MCP)** server. Register it with an MCP client (Claude Desktop, Claude Code, etc.) and that agent can discover your queries, run them, explore SQL lineage, and author dashboards directly in your Nubi workspace — all over a local stdio connection.

### The tools

| Tool | Signature | What it does |
|---|---|---|
| `list_dashboards` | `() → [{id, name}]` | List every entry in the query registry so the agent can discover ids. |
| `run_query` | `(query_id, limit=100) → {columns, rows, row_count}` | Execute a registered query and return a JSON preview (up to `limit` rows). |
| `list_lineage` | `() → {available, graph}` | Return the SQL lineage graph (which queries derive from which tables). Returns `{available: false, reason: "..."}` when the lineage module is not yet built. |
| `propose_materialized_view` | `() → [{base_table, dimensions, measures, hits, est_bytes_saved}]` | Analyse the query log and suggest pre-aggregation rollups for high-frequency GROUP BY patterns. |
| `create_dashboard` | `(name, spec_or_html, org_id="mcp") → {id, name}` | Validate and store a dashboard. Accepts a DashboardSpec dict (preferred) or an HTML string. Non-conforming content is rejected. |
| `author_dashboard` | `(question) → {id, html_preview}` | Generate a dashboard from a natural-language question and store it in one call. |
| `get_context` | `(q?, compact?) → {schema, queries, ...}` | Return workspace context (schema, query registry, lineage) for grounding agent prompts. |
| `get_spec_schema` | `() → {schema}` | Return the full DashboardSpec JSON schema so agents can construct valid specs. |
| `validate_spec` | `(spec) → {valid, errors}` | Validate a DashboardSpec dict; returns per-field errors on failure. |
| `estimate_query` | `(query_id, ...) → {row_count, bytes_scanned}` | Dry-run a query to estimate cost before execution. |
| `preview_widget` | `(widget, ...) → {data, ...}` | Render a single widget's data for preview without creating a dashboard. |
| `list_metrics` | `() → [{id, name, ...}]` | List all registered metrics in the workspace. |
| `query_metric` | `(metric_id, ...) → {value, ...}` | Execute a metric query and return the current value. |
| `upsert_dashboard` | `(id?, name, spec, ...) → {id, name}` | Create or update a dashboard by id. |
| `promote` | `(id, ...) → {id, ...}` | Promote a draft dashboard to the published/production slot. |

`create_dashboard`, `author_dashboard`, and `upsert_dashboard` all validate before storing: only Nubi's widget elements are allowed (`<nubi-kpi>`, `<nubi-table>`, `<nubi-chart>`, `<nubi-filter>`, `<nubi-text>`); `<script>` tags and inline event handlers are rejected.

Dashboards an agent authors over MCP appear in your workspace alongside boards you build by hand or in chat.

### Install

```bash
cd mcp
pip install -r requirements.txt
```

This installs the MCP Python SDK plus connector dependencies.

### Register with Claude Code

```bash
claude mcp add nubi -- python -m nubi_mcp.server
```

Or add it manually to your project's `.claude/settings.json`:

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

Replace `/absolute/path/to/nubi/mcp` with the real path to the `mcp/` directory in your checkout.

### Register with Claude Desktop

Edit the Claude Desktop config file:

- **macOS:** `~/Library/Application Support/Claude/claude_desktop_config.json`
- **Windows:** `%APPDATA%\Claude\claude_desktop_config.json`

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

If you use a virtual environment, point `command` at that environment's Python binary:

```json
{
  "mcpServers": {
    "nubi": {
      "command": "/absolute/path/to/venv/bin/python",
      "args": ["-m", "nubi_mcp.server"],
      "cwd": "/absolute/path/to/nubi/mcp"
    }
  }
}
```

Restart the client after editing the config. Then try: *"list my Nubi dashboards"* or *"author a Nubi dashboard showing revenue by region"*.

### Run it manually (smoke-test)

```bash
cd mcp
python -m nubi_mcp.server
```

The server communicates over **stdio**. You won't see output unless an MCP client connects, but a clean start confirms the install is valid.

---

## Configuring the LLM provider (operators)

Nubi selects an LLM backend at runtime from environment variables. With none set, the assistant runs in a deterministic **offline mode** (`NullProvider`) — no network, no keys — so the whole flow works in CI and local dev without an LLM.

You have two ways to connect a real model:

### Option A — a single native provider

Set one provider key (or pin it explicitly with `LLM_PROVIDER`). The SDK for that provider is installed and imported lazily.

| Variable | Example | Notes |
|---|---|---|
| `LLM_PROVIDER` | `anthropic` | Optional — pins the provider. Omit to auto-detect from the key set below. |
| `ANTHROPIC_API_KEY` | `sk-ant-…` | `pip install anthropic` |
| `OPENAI_API_KEY` | `sk-…` | `pip install openai` |
| `GEMINI_API_KEY` | `…` | `pip install google-generativeai` |

Auto-detection priority when `LLM_PROVIDER` is unset: Anthropic → OpenAI → Gemini.

### Option B — LiteLLM (recommended: one SDK, all providers, cost tracking)

[LiteLLM](https://docs.litellm.ai/) is used as an **in-process library** — *not* the standalone proxy server, so there is nothing extra to run. One `litellm.completion()` call fronts 100+ providers via a `provider/model` string, and LiteLLM ships a per-model pricing table so every call is priced automatically.

```bash
pip install litellm
```

```bash
LLM_PROVIDER=litellm
LITELLM_MODEL=anthropic/claude-opus-4-8     # default model ("provider/model")
ANTHROPIC_API_KEY=sk-ant-…                  # LiteLLM reads the provider key itself
```

`LITELLM_MODEL` accepts any LiteLLM model string — e.g. `gpt-4o`, `gemini/gemini-1.5-pro`, `ollama/llama3`, `bedrock/anthropic.claude-3-5-sonnet`. Setting `LITELLM_MODEL` alone (no `LLM_PROVIDER`) also activates LiteLLM.

| Variable | Default | Purpose |
|---|---|---|
| `LITELLM_MODEL` | — (required) | Default `provider/model` string. |
| `LITELLM_ALLOWED_MODELS` | _(just the default)_ | Comma-separated extra models a request may pick via the API `model` field. The default is always allowed; anything else is rejected with `model_not_allowed` (400) — a cost/safety gate. |
| `LITELLM_API_KEY` | — | Explicit key override. Usually omitted; LiteLLM reads `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` / … directly. |
| `LITELLM_NUM_RETRIES` | — | Auto-retry 429/5xx/timeout with exponential backoff (SDK-level rate-limit resilience). |
| `LITELLM_TIMEOUT` | — | Per-request timeout (seconds). |
| `LITELLM_MAX_BUDGET_USD` | — | Soft per-process spend cap. Calls past it raise `llm_budget_exceeded` (429) before hitting the network. |

#### Pricing, usage & cost

Every LiteLLM completion records its token counts (from the response `usage`) and a USD cost priced by LiteLLM's per-model table. Totals accumulate in two places and are emitted on the `nubi.ai.provider` logger:

```
litellm completion model=anthropic/claude-opus-4-8 prompt_tokens=812 completion_tokens=143 cost_usd=0.014…
```

- **Per request** — `LiteLLMProvider.usage` (an `LLMUsage` with `prompt_tokens`, `completion_tokens`, `total_tokens`, `cost_usd`, `calls`).
- **Per process** — `app.ai.provider.get_process_usage()` returns the running total since start; call `.as_dict()` for a JSON snapshot.

> **Scope.** Process-wide usage is in-memory: it does not survive a restart and is not shared across replicas. It is for local cost visibility and the soft single-process budget guard — **not** multi-tenant billing. For org-scoped budgets, hard rpm/tpm throttling, and provider fallbacks across a fleet, run the [LiteLLM proxy/Router](https://docs.litellm.ai/docs/proxy/quick_start) as a separate service and point `LITELLM_MODEL` at it.

> **Everything runs through LiteLLM.** All AI surfaces now route through the LiteLLM SDK:
> - **Non-streaming** (grounded text-to-SQL, dashboard generation, `/ai/ask`) and the **global assistant** (`/ai/chat`, `/ai/chat/stream`) call `provider.complete()` → `LiteLLMProvider`.
> - The **dashboard-editor chat** (`/chat/stream`, `app/chat/llm.py`) streams via `litellm.completion(stream=True)` with OpenAI-format tools, reading token deltas from `chunk.choices[0].delta.content` and assembling tool calls with `litellm.stream_chunk_builder`. Editor model ids (`claude-opus-4-8`, …) are mapped to LiteLLM provider strings (`anthropic/claude-opus-4-8`, …) by `app/chat/models.py::to_litellm_model`, so the editor chat now works on any provider, not just Anthropic.
>
> With no provider key configured, both chat surfaces fall back to the same deterministic offline mode as before.

---

## Tips

- **Expand tool blocks.** The fastest way to trust an answer is to open the *Generate SQL* and *Run query* blocks and read the actual SQL and row count.
- **Use suggestions to learn the patterns.** The starter chips show the kinds of phrasing the assistant handles well.
- **Stop early.** If a response goes the wrong direction, click ■ and rephrase — you don't have to wait for it to finish.
- **Save generated SQL.** Ask the assistant to save any query you want to reuse; it gets a stable id and typed parameters.
- **Edit dashboards conversationally.** Open a board in the editor and ask for changes; apply the ones you like and ignore the rest.

---

---

## External MCP server integration (Nubi calls your server)

Beyond the stdio server above, Nubi's agent loop can also **call an external
MCP server** that you register via the API. This is the integration model for
SaaS hosts like KeyOne whose entire integration point is an MCP server.

### How it works

When the AI agent processes a request for an org:

1. It fetches the org's enabled MCP servers from the `mcp_servers` table.
2. It calls `tools/list` on each server (Streamable-HTTP transport, SSRF-guarded).
3. It presents those tools to the LLM **alongside Nubi's built-in tools**,
   namespaced as `serverName.toolName` (e.g. `keyOne.fetch_contract`).
4. It dispatches any `serverName.toolName` tool calls to the correct MCP server
   via `tools/call`.

### Registering a server

```http
POST /api/v1/mcp/servers
Authorization: Bearer <token>
Content-Type: application/json

{
  "name": "keyOne",
  "url": "https://mcp.keyOne.example.com/mcp",
  "transport": "http",
  "auth_token": "sk-keyOne-bearer-token",
  "enabled": true
}
```

Full CRUD:

```
GET    /api/v1/mcp/servers
POST   /api/v1/mcp/servers
GET    /api/v1/mcp/servers/{id}
PUT    /api/v1/mcp/servers/{id}
DELETE /api/v1/mcp/servers/{id}
```

The `auth_token` is **encrypted at rest** (AES-256-GCM) and is **never
returned** in GET or list responses.

### SSRF protection

URLs are validated at create and update time: private IPv4/IPv6, loopback,
link-local, and cloud metadata IPs (169.254.169.254, fd00:ec2::254) are
unconditionally blocked. Connections use DNS-pinned transports to prevent
rebinding attacks.

### Consuming Nubi as an MCP server (HTTP endpoint)

In addition to the stdio server above, Nubi exposes a JSON-RPC 2.0 MCP
endpoint over HTTP for server-to-server use:

```
POST /api/v1/mcp
Authorization: Bearer <nubi-token>
Content-Type: application/json
```

Methods: `initialize`, `tools/list`, `tools/call`.

Tool calls execute under the caller's JWT claims with full RLS enforcement —
the same security guarantees as the REST API. See the Implementation section
of this file for the full tool catalog.

### Database migration

```sql
-- database/migrations/0019_mcp_servers.sql
CREATE TABLE IF NOT EXISTS mcp_servers (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id          uuid NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    name            text NOT NULL,
    url             text NOT NULL,
    transport       text NOT NULL DEFAULT 'http',
    auth_secret_ciphertext  bytea     NULL,
    auth_secret_nonce       bytea     NULL,
    auth_secret_key_version integer   NULL,
    enabled         boolean NOT NULL DEFAULT true,
    created_by      uuid NULL REFERENCES users(id) ON DELETE SET NULL,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_mcp_servers_org_enabled ON mcp_servers (org_id, enabled);
```

---

## Cost-DoS limits (operators)

Chat and AI endpoints run LLM calls, which have real monetary cost. Three
independent guards prevent a single misbehaving client from exhausting
the LLM budget or holding server connections open indefinitely.

### 1. Rate limit — `NUBI_RATELIMIT_CHAT_RPM`

All chat and AI endpoints (`/chat/stream`, `/ai/chat`, `/ai/chat/stream`,
`/ai/ask`, `/ai/dashboard`, `/ai/sql`, `/ai/canvas`, `/ai/canvas/edit`) share
a dedicated rate-limit bucket, separate from the query and auth buckets.

| Env var | Default | Notes |
|---|---|---|
| `NUBI_RATELIMIT_CHAT_RPM` | `20` | Max requests per minute per IP. |
| `NUBI_RATELIMIT_BURST_FACTOR` | `1.5` | Burst ceiling = rpm × factor (e.g. 30 at the default 20 rpm). |
| `NUBI_RATELIMIT_ENABLED` | `true` | Set `false` to disable entirely (dev/test). |

Requests over the cap receive `HTTP 429` with a `Retry-After` header and the
error body `{"error": {"code": "RATE_LIMIT_EXCEEDED", ...}}`. The bucket is
Redis-backed when `REDIS_URL` is set (global across workers/machines) and
falls back to an in-process approximation otherwise.

### 2. Aggregate per-turn token budget — `NUBI_CHAT_TURN_TOKEN_BUDGET`

Both the dashboard-editor chat loop (`app/chat/llm.py`, up to 6 steps × 4096
tokens each) and the AI agent loop (`app/ai/agent.py`, up to 8 steps) track
cumulative token usage across all steps in a single turn. When the total
reaches the budget, the loop stops immediately and emits a clean truncation
event rather than continuing to spend.

| Env var | Default | Notes |
|---|---|---|
| `NUBI_CHAT_TURN_TOKEN_BUDGET` | `16000` | Total tokens allowed per turn across all steps. |

On the streaming paths the truncation appears as a `{"type": "error",
"message": "Turn token budget …"}` SSE event followed by stream close. On the
non-streaming path the loop exits and a synthesised reply is returned from the
steps completed so far.

The NullProvider offline path (no LLM key configured) is unaffected: it
follows a deterministic scripted sequence that consumes no real tokens.

### 3. Per-turn timeout — `NUBI_CHAT_TURN_TIMEOUT_S`

The SSE streaming generators for `/chat/stream` and `/ai/chat/stream` are
wrapped with `asyncio.wait_for` so a slow or stalled provider cannot hold the
HTTP connection open indefinitely. The non-streaming `/ai/chat` endpoint is
also wrapped.

| Env var | Default | Notes |
|---|---|---|
| `NUBI_CHAT_TURN_TIMEOUT_S` | `90` | Max seconds for a complete turn. |

On timeout, streaming endpoints emit a `{"type": "error", "message": "Turn
timeout …"}` SSE event and close the stream cleanly (no abrupt disconnect).
The non-streaming endpoint returns `HTTP 504` with
`{"error": {"code": "turn_timeout", ...}}`.

> **Note on SSE and proxies.** The timeout guard wraps the async generator
> at the application layer, not at the network layer. A reverse proxy (nginx,
> Fly, Cloudflare) may impose its own read timeout; set those to at least
> `NUBI_CHAT_TURN_TIMEOUT_S + 10s` to avoid premature 504s from the proxy
> before the application timeout fires.

---

## `explain_metric_change` — conversational metric drill-downs

The AI agent includes an `explain_metric_change` tool that lets you ask in plain language *why* a metric moved between two time windows. You do not need to write any SQL — just describe what you want to investigate and the agent does the rest.

### What it does

`explain_metric_change` runs the same dimension-contribution computation as `POST /metrics/{id}/explain`, but inside a chat turn so the agent can verbalize the findings and follow up with further investigation.

Given a current period and a comparison period, it:

1. Fetches the metric grouped by each allowed dimension for both periods.
2. Computes per-member delta contributions and sorts dimensions by explanatory power.
3. Returns a structured breakdown that the agent phrases as a plain-language summary.

### Asking for an explanation

Just ask naturally in the chat panel:

- *"Why did revenue drop last week vs the week before?"*
- *"Which regions drove the change in order volume between Q1 and Q2?"*
- *"Explain the spike in cancellations on the 15th compared to the previous 7 days."*

The agent maps your question to `metric_id`, `current_start/end`, `comparison_start/end`, and optionally a dimension subset, then calls the tool and streams the explanation back.

### What you get back

The agent's reply includes:

- The total delta (`current_total - comparison_total`) and direction.
- Per-dimension explanatory power and coverage — which dimension explains most of the movement.
- The top members per dimension sorted by absolute delta, with `current`, `comparison`, `delta`, `share`, and direction (`up` / `down` / `flat`).
- A `summary_hint` field the agent uses to anchor its phrasing.

### Security

RLS is enforced identically to `query_metric` — the tool never returns data outside the caller's verified org and policy scope.

### Example

```text
You: Why did revenue fall last week compared to the week before?

Nubi AI: Revenue fell by $42,300 (−8.2%) last week.

The biggest driver was the East region: it alone contributed $29,100 of the
decline (−68% of the total delta). Within East, the "enterprise" segment dropped
$22,400 (−31%), while "SMB" was roughly flat.

West and South were slightly up (+$4,100 combined), partially offsetting East.
```

---

## Related

- [Dashboards](/docs/dashboards) — widget types, chart types, and the editor.
- [Queries & Parameters](/docs/queries-and-params) — saving generated SQL and using `{{named}}` parameters.
- [Flows](/docs/flows) — put the AI agent in a scheduled, multi-step pipeline.
- [Pre-Aggregations](/docs/pre-aggregations) — the rollups `propose_materialized_view` suggests.
- [Organization Settings](/docs/organization-settings) — configure your workspace's LLM provider and API keys.
