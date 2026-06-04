# Nubi MCP Server

A [Model Context Protocol](https://modelcontextprotocol.io/) (MCP) server for
the Nubi analytics platform. Exposes Nubi's query registry, execution pipeline,
lineage graph, pre-aggregation suggester, and dashboard authoring tools to any
MCP-compatible client (Claude Desktop, Claude Code, etc.).

## Tools

| Tool | Signature | Description |
|------|-----------|-------------|
| `list_dashboards` | `() → [{id, name}]` | List all registered dashboards/queries in the Nubi query registry. |
| `run_query` | `(query_id, limit=100) → {columns, rows, row_count}` | Execute a registered query via DuckDB and return a compact JSON preview. |
| `list_lineage` | `() → {available, graph|reason}` | Return the SQL lineage graph (M7-A), or `{available: false, reason: "…"}` when the module is not yet built. |
| `propose_materialized_view` | `() → [{base_table, dimensions, measures, hit_count, bytes_saved}]` | Analyse the query log and return pre-aggregation rollup suggestions. |
| `create_dashboard` | `(name, html, org_id="mcp") → {id, name}` | Validate and store a dashboard HTML document as a Nubi boards resource. HTML must use only `<nubi-kpi>`, `<nubi-table>`, `<nubi-chart>` widgets — no `<script>` tags or inline event handlers. |
| `author_dashboard` | `(question) → {id, html_preview}` | Ground a natural-language question and auto-generate a dashboard via the Nubi AI pipeline, then store it as a boards resource. Returns the board id and the first 200 characters of the generated HTML. |

## Prerequisites

- Python 3.11+
- The Nubi `backend/` directory must be present at `../backend` relative to
  this `mcp/` directory (default project layout).

## Installation

```bash
cd mcp
pip install -r requirements.txt
```

`requirements.txt` includes `mcp>=1.0` (the official MCP Python SDK, package
name `mcp` on PyPI) plus the backend connector dependencies (`pyarrow`,
`sqlglot`, `duckdb`) so this package is installable standalone.

## Running

```bash
# Recommended: module entry-point
cd mcp
python -m nubi_mcp.server

# Or direct execution
python mcp/nubi_mcp/server.py
```

The server runs over **stdio** transport (stdin/stdout), which is the standard
transport for local MCP servers launched by Claude Desktop and Claude Code.

## Running Tests

```bash
cd mcp
python -m pytest tests -q
```

Tests exercise the tool-logic functions directly without requiring a live MCP
transport.

## Registering with Claude Desktop

Add the following to your Claude Desktop configuration file
(`~/Library/Application Support/Claude/claude_desktop_config.json` on macOS,
`%APPDATA%\Claude\claude_desktop_config.json` on Windows):

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

Replace `/absolute/path/to/nubi/mcp` with the absolute path to the `mcp/`
directory in your Nubi checkout.

If you are using a virtual environment, replace `"python"` with the full path
to the venv Python binary:

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

## Registering with Claude Code

Run the following command in your terminal:

```bash
claude mcp add nubi -- python -m nubi_mcp.server
```

Or add it manually to your Claude Code project settings
(`.claude/settings.json`):

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

## Architecture

The MCP server (`nubi_mcp/server.py`) adds `nubi/backend/` to `sys.path` at
import time so that `app.*` modules (query registry, DuckDB connector, preagg,
lineage, AI pipeline) are importable without installing the backend as a package.

Each tool's business logic lives in a plain Python function
(`_list_dashboards`, `_run_query`, `_list_lineage`, `_propose_materialized_view`,
`_create_dashboard`, `_author_dashboard`) that the `@server.tool(...)` decorator
wraps. This separation makes the tool logic unit-testable without any MCP transport.

### Lineage (M7-A dependency)

`list_lineage` uses a defensive import: if `app.lineage` is not yet available
(M7-A not yet built), the tool returns `{available: false, reason: "…"}` instead
of crashing the server. Build Wave M7-A to unlock the full lineage graph.

### Dashboard authoring tools

`create_dashboard` accepts an HTML document and validates it via
`app.ai.dashboard.validate_dashboard_html` before writing it to the boards repo.
HTML must use only `<nubi-kpi>`, `<nubi-table>`, `<nubi-chart>` custom elements
and must not contain `<script>` tags or inline event handlers.

`author_dashboard` combines the grounding pipeline (`app.ai.grounding.build_catalog`),
the AI provider (`app.ai.provider.get_provider`), and dashboard HTML generation
(`app.ai.dashboard.generate_dashboard_html`) to go from a natural-language question
to a stored board in one call. The default provider is `NullProvider` (deterministic,
offline) unless the environment is configured with a real LLM API key.
