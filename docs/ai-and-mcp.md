# AI, chat & MCP

![Ask questions, build dashboards, and explore data with Nubi AI](illustration:LlmDashboards)

Nubi has a built-in AI assistant that lives on every page of the app, plus an MCP server that lets external agents (Claude Desktop, Claude Code, and other MCP clients) reach into your workspace. The in-app agent can write and run SQL, explore your schema, and build dashboards for you — and because it runs through Nubi's query planner, it stays inside your row-level-security boundary the whole time.

This page covers:

- The **Nubi AI chat panel** — what you click and what you see.
- **Grounded text-to-SQL** — turning a question into a validated query.
- **Natural-language dashboard generation** — describing a dashboard and watching it build.
- The **dashboard editor assistant** — conversational edits on a live board.
- The **MCP server** — six tools that external agents use to author dashboards and run queries.

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

### The six tools

| Tool | Signature | What it does |
|---|---|---|
| `list_dashboards` | `() → [{id, name}]` | List every entry in the query registry so the agent can discover ids. |
| `run_query` | `(query_id, limit=100) → {columns, rows, row_count}` | Execute a registered query and return a JSON preview (up to `limit` rows). |
| `list_lineage` | `() → {available, graph}` | Return the SQL lineage graph (which queries derive from which tables). Returns `{available: false, reason: "..."}` when the lineage module is not yet built. |
| `propose_materialized_view` | `() → [{base_table, dimensions, measures, hits, est_bytes_saved}]` | Analyse the query log and suggest pre-aggregation rollups for high-frequency GROUP BY patterns. |
| `create_dashboard` | `(name, spec_or_html, org_id="mcp") → {id, name}` | Validate and store a dashboard. Accepts a DashboardSpec dict (preferred) or an HTML string. Non-conforming content is rejected. |
| `author_dashboard` | `(question) → {id, html_preview}` | Generate a dashboard from a natural-language question and store it in one call. |

`create_dashboard` and `author_dashboard` both validate before storing: only Nubi's widget elements are allowed (`<nubi-kpi>`, `<nubi-table>`, `<nubi-chart>`, `<nubi-filter>`, `<nubi-text>`); `<script>` tags and inline event handlers are rejected.

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

## Tips

- **Expand tool blocks.** The fastest way to trust an answer is to open the *Generate SQL* and *Run query* blocks and read the actual SQL and row count.
- **Use suggestions to learn the patterns.** The starter chips show the kinds of phrasing the assistant handles well.
- **Stop early.** If a response goes the wrong direction, click ■ and rephrase — you don't have to wait for it to finish.
- **Save generated SQL.** Ask the assistant to save any query you want to reuse; it gets a stable id and typed parameters.
- **Edit dashboards conversationally.** Open a board in the editor and ask for changes; apply the ones you like and ignore the rest.

---

## Related

- [Dashboards](/docs/dashboards) — widget types, chart types, and the editor.
- [Queries & Parameters](/docs/queries-and-params) — saving generated SQL and using `{{named}}` parameters.
- [Flows](/docs/flows) — put the AI agent in a scheduled, multi-step pipeline.
- [Pre-Aggregations](/docs/pre-aggregations) — the rollups `propose_materialized_view` suggests.
- [Organization Settings](/docs/organization-settings) — configure your workspace's LLM provider and API keys.
