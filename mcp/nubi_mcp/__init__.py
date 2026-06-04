"""Nubi MCP server package.

Exposes a Model Context Protocol (MCP) server named "nubi" with four tools:

- list_dashboards   — list registered queries/dashboards from the query registry.
- run_query         — execute a registered query and return a JSON preview.
- list_lineage      — return the lineage graph (or a clear unavailability message).
- propose_materialized_view — suggest pre-aggregation rollup tables from the query log.

Run with:
    python -m nubi_mcp.server
"""

__version__ = "0.1.0"
