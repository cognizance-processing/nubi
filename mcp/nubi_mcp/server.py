"""Nubi MCP server — stdio transport.

This module is the entry-point for the Nubi Model Context Protocol server.
It exposes four tools to MCP clients (e.g. Claude Desktop, Claude Code):

    list_dashboards()
        List all registered queries/dashboards in the Nubi query registry.

    run_query(query_id, limit=100)
        Execute a registered query via the DuckDB connector and return a
        compact JSON preview: {columns, rows (<=limit), row_count}.

    list_lineage()
        Return the full lineage graph when the lineage module (M7-A) is
        available, or a clear 'lineage unavailable' message when it is not.

    propose_materialized_view()
        Analyse the query log and return pre-aggregation rollup suggestions
        produced by connectors/preagg.py.

Import-path strategy
--------------------
The ``backend/`` directory is prepended to ``sys.path`` at import time so that
``app.*`` modules (e.g. ``app.queries.registry``) are importable without
installing the backend as a package.  This approach matches how the existing
backend tests run (they also add ``backend/`` to the path via conftest).

Usage
-----
    # Run via module entry-point (recommended)
    python -m nubi_mcp.server

    # Direct execution
    python /path/to/mcp/nubi_mcp/server.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Path bootstrap — make backend/app importable.
#
# Layout:
#   nubi/
#     backend/         ← add this to sys.path so "import app.*" works
#       app/
#         queries/registry.py
#         connectors/duckdb_conn.py
#         ...
#     mcp/
#       nubi_mcp/
#         server.py    ← this file
# ---------------------------------------------------------------------------

_HERE = Path(__file__).resolve().parent           # mcp/nubi_mcp/
_MCP_DIR = _HERE.parent                           # mcp/
_NUBI_ROOT = _MCP_DIR.parent                      # nubi/
_BACKEND_DIR = _NUBI_ROOT / "backend"             # nubi/backend/

if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))


# ---------------------------------------------------------------------------
# Pure tool-logic functions (no MCP transport dependency — testable directly)
# ---------------------------------------------------------------------------


def _list_dashboards() -> list[dict[str, str]]:
    """Return all registered queries as [{id, name}] dicts.

    Treats the query registry as the dashboard/query catalogue for MVP.
    No I/O; purely reads the in-process singleton registry.

    Returns
    -------
    list[dict[str, str]]
        Each dict has ``id`` and ``name`` keys.
    """
    from app.queries.registry import get_query_registry

    registry = get_query_registry()
    return [{"id": rq.id, "name": rq.name} for rq in registry.all()]


def _run_query(query_id: str, limit: int = 100) -> dict[str, Any]:
    """Execute a registered query and return a compact preview.

    Uses a fresh in-memory DuckDB connection so the tool is self-contained
    and does not depend on any live external database.

    The ``demo_points_10k`` (and other generate_series-based) queries run
    without any seed table — DuckDB's built-in generate_series function is
    always available.

    Parameters
    ----------
    query_id:
        The id of a registered query (e.g. ``"demo_points_10k"``).
    limit:
        Maximum number of rows to include in the preview.  Default: 100.

    Returns
    -------
    dict
        ``{columns: list[str], rows: list[list], row_count: int}``

        - ``columns`` — column names in schema order.
        - ``rows``    — up to *limit* rows, each a list of Python scalars.
        - ``row_count`` — total number of rows in the full result (before
          capping to *limit*).

    Raises
    ------
    ValueError
        When *query_id* is not in the registry.
    RuntimeError
        When the query execution fails.
    """
    from app.queries.registry import get_query_registry
    from app.connectors.duckdb_conn import DuckDBConnector
    from app.connectors import planner

    registry = get_query_registry()
    rq = registry.get(query_id)
    if rq is None:
        known = [q.id for q in registry.all()]
        raise ValueError(
            f"Unknown query_id {query_id!r}. Known ids: {known}"
        )

    connector = DuckDBConnector()
    physical_plan = planner.plan(rq.sql, dialect="duckdb")
    table = connector.execute(physical_plan)

    total_rows = table.num_rows
    columns = table.schema.names

    # Slice to at most *limit* rows.
    preview_table = table.slice(0, min(limit, total_rows))

    # Convert Arrow columns to plain Python lists for JSON serialisation.
    rows: list[list[Any]] = []
    if preview_table.num_rows > 0:
        # Transpose: Arrow column-major → row-major for the preview.
        col_arrays = [
            col.to_pylist() for col in preview_table.columns
        ]
        for i in range(preview_table.num_rows):
            rows.append([arr[i] for arr in col_arrays])

    return {
        "columns": list(columns),
        "rows": rows,
        "row_count": total_rows,
    }


def _list_lineage() -> dict[str, Any]:
    """Return the lineage graph or a clear unavailability message.

    Attempts to import ``app.lineage`` (M7-A module).  If the module is not
    yet available (e.g. M7-A not yet shipped) or is incomplete, returns a
    structured ``{available: false, reason: "..."}`` message instead of
    crashing.

    Returns
    -------
    dict
        On success: ``{available: true, graph: <LineageGraph dict>}``
        On failure: ``{available: false, reason: "<explanation>"}``
    """
    try:
        from app.lineage import build_graph
        from app.queries.registry import get_query_registry

        registry = get_query_registry()
        queries = registry.all()
        graph = build_graph(queries)

        # LineageGraph should be serialisable; try dict() or __dict__.
        if hasattr(graph, "to_dict"):
            graph_data = graph.to_dict()
        elif hasattr(graph, "__dict__"):
            graph_data = _make_serialisable(vars(graph))
        else:
            graph_data = str(graph)

        return {"available": True, "graph": graph_data}

    except ImportError as exc:
        return {
            "available": False,
            "reason": (
                f"Lineage module (M7-A) is not yet available: {exc}. "
                "Build Wave M7-A first."
            ),
        }
    except AttributeError as exc:
        return {
            "available": False,
            "reason": (
                f"Lineage module is present but incomplete (missing symbol): {exc}. "
                "Ensure M7-A is fully built."
            ),
        }
    except Exception as exc:  # pylint: disable=broad-except
        return {
            "available": False,
            "reason": f"Lineage graph build failed: {type(exc).__name__}: {exc}",
        }


def _create_dashboard(
    name: str,
    spec_or_html: "str | dict[str, Any] | None" = None,
    org_id: str = "mcp",
    *,
    html: "str | None" = None,
) -> dict[str, Any]:
    """Validate and store a dashboard as a boards resource.

    Accepts either a DashboardSpec dict (EDITOR-2A canonical format) or a
    legacy HTML string.  When a spec dict is provided it is validated, compiled
    to HTML via ``spec_to_html``, and both are stored in ``config``.  When a
    plain HTML string is provided the original behaviour is preserved for
    backwards compatibility.

    Uses the Repo layer (InMemoryRepo-compatible) so it works in tests without
    a live database.

    Parameters
    ----------
    name:
        Human-readable name for the dashboard board.
    spec_or_html:
        Either a DashboardSpec dict OR a raw HTML string.
    org_id:
        Organisation id to scope the board under.  Defaults to ``"mcp"``.

    Returns
    -------
    dict
        ``{id: str, name: str}`` — the newly created board's id and name.

    Raises
    ------
    ValueError
        When validation fails (bad spec or unsafe HTML).
    """
    from app.ai.dashboard import validate_dashboard_html  # noqa: PLC0415
    from app.repos import get_repo  # noqa: PLC0415

    # Support legacy callers that pass html= as a keyword argument.
    if spec_or_html is None and html is not None:
        spec_or_html = html
    if spec_or_html is None:
        raise ValueError("_create_dashboard requires spec_or_html (or html=) argument.")

    config: dict[str, Any]

    if isinstance(spec_or_html, dict):
        # Spec dict path — validate and compile.
        from app.dashboards.spec import validate_spec, spec_to_html  # noqa: PLC0415

        spec, issues = validate_spec(spec_or_html)
        hard_issues = [
            i for i in issues
            if "not in the registered" not in i  # soft registry warnings are ok
        ]
        if hard_issues or spec is None:
            raise ValueError(
                f"Dashboard spec failed validation: {'; '.join(issues or ['parse error'])}"
            )
        html = spec_to_html(spec)
        ok, html_issues = validate_dashboard_html(html)
        if not ok:
            raise ValueError(
                f"Compiled dashboard HTML failed security validation: {'; '.join(html_issues)}"
            )
        config = {"spec": spec.model_dump(), "html": html}

    else:
        # Legacy HTML string path — preserve original behaviour.
        html = spec_or_html
        ok, issues = validate_dashboard_html(html)
        if not ok:
            raise ValueError(
                f"Dashboard HTML failed validation: {'; '.join(issues)}"
            )
        config = {"html": html}

    repo = get_repo()

    # Run the async create in a new event loop (the MCP server is synchronous).

    async def _create() -> dict[str, Any]:
        return await repo.create(
            resource="boards",
            org_id=org_id,
            created_by="mcp-system",
            name=name,
            config=config,
        )

    try:
        asyncio.get_running_loop()
        # A running loop exists (e.g. inside an async test or FastAPI context).
        import concurrent.futures  # noqa: PLC0415

        with concurrent.futures.ThreadPoolExecutor() as pool:
            future = pool.submit(asyncio.run, _create())
            row = future.result()
    except RuntimeError:
        # No running loop — safe to call asyncio.run directly.
        row = asyncio.run(_create())

    return {"id": row["id"], "name": row["name"]}


def _author_dashboard(question: str) -> dict[str, Any]:
    """Ground *question* and auto-generate + store a dashboard (EDITOR-2A).

    Generates a canonical DashboardSpec via the AI grounding pipeline, compiles
    it to HTML, stores both under ``boards.config = {spec, html}``, and returns
    the board id plus a short HTML preview.

    Parameters
    ----------
    question:
        Natural-language description of the desired dashboard.

    Returns
    -------
    dict
        ``{id: str, html_preview: str}`` — the board id and the first 200
        characters of the generated HTML for quick inspection.
    """
    from app.ai.dashboard import generate_dashboard_spec  # noqa: PLC0415
    from app.ai.grounding import build_catalog  # noqa: PLC0415
    from app.ai.provider import get_provider  # noqa: PLC0415
    from app.dashboards.spec import spec_to_html  # noqa: PLC0415

    catalog = build_catalog()
    provider = get_provider()
    spec = generate_dashboard_spec(question, catalog, provider)
    html = spec_to_html(spec)

    board_name = question[:40].strip()
    # Pass the spec dict so _create_dashboard stores config={spec, html}.
    result = _create_dashboard(name=board_name, spec_or_html=spec.model_dump())

    return {
        "id": result["id"],
        "html_preview": html[:200],
    }


def _propose_materialized_view() -> list[dict[str, Any]]:
    """Return pre-aggregation rollup suggestions from the query log.

    Delegates to ``connectors/preagg.suggest`` over the process-wide
    ``QueryLog`` singleton.  The list may be empty when the query log
    contains fewer than ``min_hits`` (default: 3) matching GROUP BY patterns.

    Returns
    -------
    list[dict]
        One dict per suggestion (see ``RollupSuggestion.to_dict()``).
    """
    from app.connectors.preagg import suggest
    from app.connectors.query_log import get_query_log

    log = get_query_log()
    suggestions = suggest(log)
    return [s.to_dict() for s in suggestions]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_serialisable(obj: Any) -> Any:
    """Recursively convert an object to JSON-serialisable Python primitives."""
    if isinstance(obj, dict):
        return {k: _make_serialisable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_make_serialisable(i) for i in obj]
    if isinstance(obj, (str, int, float, bool)) or obj is None:
        return obj
    if hasattr(obj, "__dict__"):
        return _make_serialisable(vars(obj))
    return str(obj)


# ---------------------------------------------------------------------------
# MCP server wiring
# ---------------------------------------------------------------------------

try:
    from mcp.server.fastmcp import FastMCP as _FastMCP

    _mcp_available = True
except ImportError:  # pragma: no cover
    _mcp_available = False
    _FastMCP = None  # type: ignore[assignment,misc]


def _build_mcp_server() -> "_FastMCP":  # type: ignore[return]
    """Construct and return the FastMCP server with all four tools registered."""
    if not _mcp_available:
        raise RuntimeError(
            "The 'mcp' package is not installed.  "
            "Run: pip install 'mcp>=1.0' in the mcp/ directory."
        )

    server = _FastMCP(
        "nubi",
        instructions=(
            "Nubi analytics MCP server. "
            "Use list_dashboards to discover queries, run_query to execute them, "
            "list_lineage to explore SQL lineage, and propose_materialized_view "
            "for pre-aggregation suggestions."
        ),
    )

    @server.tool(
        name="list_dashboards",
        description=(
            "List all registered dashboards and queries in the Nubi query registry. "
            "Returns an array of {id, name} objects. "
            "Use the returned id values with run_query."
        ),
    )
    def list_dashboards() -> list[dict[str, str]]:
        """List registered dashboards/queries."""
        return _list_dashboards()

    @server.tool(
        name="run_query",
        description=(
            "Execute a registered Nubi query by its id and return a JSON preview. "
            "The preview contains the column names, up to `limit` rows, and the "
            "total row count. "
            "Use list_dashboards first to discover valid query ids."
        ),
    )
    def run_query(query_id: str, limit: int = 100) -> dict[str, Any]:
        """Run a registered query and return {columns, rows, row_count}."""
        return _run_query(query_id, limit)

    @server.tool(
        name="list_lineage",
        description=(
            "Return the SQL lineage graph for all registered queries. "
            "The graph maps query ids to their source tables and columns. "
            "Returns {available: true, graph: ...} when the lineage module is "
            "ready, or {available: false, reason: '...'} when it is not yet built."
        ),
    )
    def list_lineage() -> dict[str, Any]:
        """Return the lineage graph (or unavailability notice)."""
        return _list_lineage()

    @server.tool(
        name="propose_materialized_view",
        description=(
            "Analyse the Nubi query log and propose pre-aggregation materialised "
            "views (rollup tables) for high-frequency GROUP BY patterns. "
            "Returns a list of suggestions with base_table, dimensions, measures, "
            "hit count, and estimated bytes saved. The list is empty when the query "
            "log does not yet contain enough repeated GROUP BY patterns."
        ),
    )
    def propose_materialized_view() -> list[dict[str, Any]]:
        """Propose materialised view rollups from the query log."""
        return _propose_materialized_view()

    @server.tool(
        name="create_dashboard",
        description=(
            "Validate and store a dashboard as a Nubi boards resource. "
            "Accepts either a DashboardSpec dict (preferred, EDITOR-2A format) or "
            "a legacy HTML string (backwards compatible). "
            "Spec dicts are validated and compiled to HTML; HTML strings are "
            "validated for security (no <script>, no on* handlers). "
            "Returns {id, name} of the created board."
        ),
    )
    def create_dashboard(
        name: str, spec_or_html: "str | dict[str, Any]", org_id: str = "mcp"
    ) -> dict[str, Any]:
        """Store a validated dashboard (spec dict or HTML string) as a boards resource."""
        return _create_dashboard(name, spec_or_html, org_id)

    @server.tool(
        name="author_dashboard",
        description=(
            "Ground a natural-language question and auto-generate a dashboard HTML "
            "document using the Nubi AI pipeline, then store it as a boards resource. "
            "Returns {id, html_preview} where html_preview is the first 200 chars of "
            "the generated HTML. No LLM API key is required — NullProvider is used "
            "by default for deterministic, offline generation."
        ),
    )
    def author_dashboard(question: str) -> dict[str, Any]:
        """Generate and store a grounded dashboard from a natural-language question."""
        return _author_dashboard(question)

    return server


# Module-level server singleton (built lazily to avoid import-time side-effects).
_server: "_FastMCP | None" = None  # type: ignore[type-arg]


def get_server() -> "_FastMCP":  # type: ignore[return]
    """Return (or build) the module-level FastMCP server singleton."""
    global _server
    if _server is None:
        _server = _build_mcp_server()
    return _server


# ---------------------------------------------------------------------------
# Entry-point
# ---------------------------------------------------------------------------


def main() -> None:
    """Run the Nubi MCP server over stdio transport.

    Invoked by ``python -m nubi_mcp.server`` (see ``__main__.py``) or
    directly via ``python nubi_mcp/server.py``.

    The stdio transport is required by the MCP spec for local process
    invocation.  Claude Desktop and Claude Code both launch MCP servers this
    way and communicate over stdin/stdout.
    """
    server = get_server()
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
