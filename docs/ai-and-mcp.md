# AI, chat & MCP

Nubi has a built-in AI assistant that lives in every page of the app, plus an MCP server that lets external agents (Claude Desktop, Claude Code, and other MCP clients) reach into your Nubi workspace. The same agent can write and run SQL, explore your data, and build dashboards for you — and because it works through Nubi's planner, it stays inside your row-level-security boundary the whole time.

This page is a how-to guide. It covers:

- The in-app **Nubi AI** chat panel — what you click and what you see.
- **Grounded text-to-SQL** — turning a question into a validated query.
- **Natural-language dashboard generation** — describing a dashboard and watching it build.
- The **dashboard editor's** built-in assistant that proposes and applies dashboard changes.
- The **MCP server** — letting outside agents author Nubi dashboards, with current registration steps.

---

## The Nubi AI chat panel

The assistant is always one click away from anywhere in the app.

### Opening chat

1. Look at the top-right of the topbar for the **chat button** (the speech-bubble icon).
2. Click it to slide the **Nubi AI** panel in from the right. Click it again (or the **✕** in the panel header) to close. On a phone the panel opens full-screen.
3. When the panel is empty you'll see a welcome card — *"Ask Nubi anything"* — with a short blurb and four starter suggestions you can tap to send instantly:
   - **Build a sales dashboard**
   - **Show revenue by region**
   - **Which queries run slowest?**
   - **Summarise connected data sources**

> The global chat button is hidden on pages that have their own assistant — most notably the dashboard editor, which embeds a chat tuned for editing the board in front of you. See [The dashboard editor assistant](#the-dashboard-editor-assistant).

### Choosing a model

In the panel header, next to the close button, is a small model picker. The options are:

- **Nubi Default** — the workspace's configured model.
- **Claude**
- **GPT-4o**

Pick whichever you prefer before sending. If your workspace hasn't been given an AI provider key, the assistant still runs in a deterministic offline mode so you can try the flow end-to-end without any external calls.

### Sending a message

1. Type into the box at the bottom. Press **Enter** to send; use **Shift+Enter** for a newline.
2. Click the **send** button (paper-plane icon) or just press Enter.
3. While the assistant is working the send button becomes a **stop** button (■). Click it any time to cancel the current response.

### Watching the assistant work (live tool streaming)

Nubi's chat streams its work as it happens, Claude-Code style — you don't just get a final answer, you watch each step.

- A small pulsing **status line** ("Thinking…", "doing X…") appears first.
- Each tool the assistant runs shows up as a **tool block** that animates from *running…* to a result, with a spinner that turns into a green check (or a red alert if it failed).
- The written reply then **streams in token by token** with a blinking caret.

Tool blocks are collapsed by default. **Click any block to expand it** and see the exact arguments and the full result. Each block is labelled and color-coded by what it does:

| Tool block | What you see when expanded |
|---|---|
| **Generate SQL** | The generated SQL, a `valid` / `needs review` badge, the tables it touched, and any issues it flagged. |
| **Run query** | A row/column count and a preview table (first rows). |
| **Create dashboard** | The dashboard title and a chip per widget (`kpi`, `chart`, `table`, …). |
| **Edit dashboard** | The applied edit and the re-validated result. |
| **Get schema** | The catalog (tables + columns) the assistant is grounding against. |
| **List queries** | Your registered queries with their ids and parameters. |

Because every step is visible and inspectable, you can always see *why* the assistant answered the way it did — which query it ran, against which tables, returning how many rows.

### What the assistant can do for you

Just ask in plain language. Common requests:

- **"Show me revenue by region last quarter."** → it generates SQL, runs it, and summarises the result with a preview table.
- **"Build a sales dashboard."** → it generates the SQL behind the widgets and assembles a dashboard (see below).
- **"Which of my queries scan the most data?"** → it lists and inspects your registered queries.
- **"Summarise the data sources I have connected."** → it reads the schema/catalog and explains what's available.

The assistant only ever queries data you're allowed to see. Its access is scoped to your account and your organisation's row-level-security policies — it can't reach around them.

---

## Grounded text-to-SQL

When you ask a data question, Nubi doesn't hand a blank prompt to the model and hope. It **grounds** the request first: it inspects your registered queries and the SQL lineage graph to find the tables and columns that actually relate to your question, then asks the model to write SQL against *those real names*. The generated SQL is validated before you ever see it.

In chat this happens automatically inside the **Generate SQL** tool block. Expand it to see:

- The SQL itself.
- A **`valid`** or **`needs review`** badge (Nubi parses and checks the SQL).
- The tables it references.
- Any issues the validator caught.

```sql
SELECT region, SUM(revenue) AS revenue
FROM sales
WHERE quarter = 'Q4'
GROUP BY region
ORDER BY revenue DESC
```

If you want to **keep** a generated query, ask the assistant to save it (or save it from the query library) — saved queries get a stable id and any `{{placeholder}}` in the SQL becomes a typed parameter you can fill in later. See [Queries & Parameters](/docs/queries-and-params) for working with the query library.

---

## Natural-language dashboard generation

Ask for a dashboard and Nubi builds a real one — not a screenshot, a live, cross-filtering board bound to your queries.

1. In chat, type something like **"Build a dashboard of revenue by region for Q1"** (or tap the **Build a sales dashboard** suggestion).
2. Watch the **Generate SQL** block produce the query the dashboard will read from.
3. Watch the **Create dashboard** block assemble the board. Expand it to see the dashboard title and a chip for each widget it added.
4. The assistant replies with a summary of what it built.

Under the hood Nubi generates a structured **DashboardSpec** (referencing your real query ids and columns), compiles it to safe dashboard HTML, and validates it. Dashboards are composed only of Nubi's sandboxed widget elements — `<nubi-kpi>`, `<nubi-table>`, `<nubi-chart>`, `<nubi-filter>`, and `<nubi-text>` — so a generated dashboard can never contain scripts or unsafe markup. See [Dashboards](/docs/dashboards) for the full widget and chart reference.

---

## The dashboard editor assistant

The dashboard editor has its own embedded assistant, tuned for changing the board you're currently editing. (Because it owns the chat for that page, the global topbar chat button is hidden while you're in the editor.)

1. Open a dashboard in the editor.
2. Use the editor's chat to describe a change in plain language — for example *"add a KPI for total revenue"*, *"turn the bar chart into a line chart"*, or *"remove the region filter"*.
3. The assistant proposes an updated dashboard. When it has a spec ready, you get an **apply** affordance — applying it updates the board in front of you.
4. It keeps a **conversation history** and a **New chat** action so you can start a fresh thread without losing context.

This is the conversational counterpart to the drag-and-drop editor: edit by hand, by chat, or both.

---

## MCP server — let external agents author dashboards

Nubi ships a **Model Context Protocol (MCP)** server. Register it with an MCP-compatible client (Claude Desktop, Claude Code, etc.) and that agent can discover your queries, run them, explore lineage, and **author dashboards directly in your Nubi workspace** — all over a local stdio connection.

### Tools the agent gets

| Tool | Call | What it does |
|---|---|---|
| `list_dashboards` | `() → [{id, name}]` | List every registered query/dashboard so the agent can discover ids. |
| `run_query` | `(query_id, limit=100) → {columns, rows, row_count}` | Execute a registered query and return a JSON preview. |
| `list_lineage` | `() → {available, graph}` | Return the SQL lineage graph (which queries derive from which tables). |
| `propose_materialized_view` | `() → [{base_table, dimensions, measures, hit_count, bytes_saved}]` | Suggest pre-aggregation rollups mined from the query log. |
| `create_dashboard` | `(name, spec_or_html, org_id="mcp") → {id, name}` | Validate and store a dashboard. Accepts a DashboardSpec dict (preferred) or safe HTML. |
| `author_dashboard` | `(question) → {id, html_preview}` | Generate a dashboard from a natural-language question and store it in one call. |

`create_dashboard` and `author_dashboard` both validate before storing: dashboards may use only Nubi's widget elements (`<nubi-kpi>`, `<nubi-table>`, `<nubi-chart>`, `<nubi-filter>`, `<nubi-text>`) and may not contain `<script>` tags or inline event handlers. Non-conforming content is rejected.

### Install

```bash
cd mcp
pip install -r requirements.txt
```

This installs the official MCP Python SDK plus the connector dependencies, so the server is self-contained.

### Run it manually (optional)

You usually let your MCP client launch the server, but you can run it directly to check it:

```bash
cd mcp
python -m nubi_mcp.server
```

The server communicates over **stdio** — the standard transport for locally launched MCP servers.

### Register with Claude Code

The quickest path:

```bash
claude mcp add nubi -- python -m nubi_mcp.server
```

Or add it to your project's `.claude/settings.json` manually:

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

Replace `/absolute/path/to/nubi/mcp` with the absolute path to the `mcp/` directory in your checkout.

### Register with Claude Desktop

Edit your Claude Desktop config file:

- **macOS:** `~/Library/Application Support/Claude/claude_desktop_config.json`
- **Windows:** `%APPDATA%\Claude\claude_desktop_config.json`

Add a `nubi` server:

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

If you use a virtual environment, point `command` at that environment's Python binary instead of bare `python`:

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

Restart the client after editing the config. Once connected, ask the agent to *"list my Nubi dashboards"* or *"author a Nubi dashboard showing revenue by region"* and it will use the tools above.

> The MCP server reads from the same query registry, lineage graph, and dashboard store as the app. Dashboards an agent authors over MCP show up in your workspace just like ones you build by hand or in chat.

---

## Tips

- **Expand the tool blocks.** The fastest way to trust an answer is to open the *Generate SQL* and *Run query* blocks and read the actual SQL and rows.
- **Use suggestions to learn the patterns.** The starter chips show the kinds of phrasing the assistant handles well.
- **Stop early.** If a response is going the wrong direction, click the ■ stop button and rephrase — you don't have to wait for it to finish.
- **Edit dashboards conversationally.** Open a board in the editor and ask for changes; apply the ones you like.

## Related

- [Dashboards](/docs/dashboards) — widget types, charts, and the editor.
- [Queries & Parameters](/docs/queries-and-params) — saving generated SQL and using `{{named}}` parameters.
- [Flows](/docs/flows) — put the AI agent in a scheduled, multi-step pipeline.
- [Pre-Aggregations](/docs/pre-aggregations) — the rollups `propose_materialized_view` suggests.
