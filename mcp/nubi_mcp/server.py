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
import uuid
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


def _get_context(q: "str | None" = None, compact: bool = False) -> dict[str, Any]:
    """Return the single-call authoring catalog (mirrors GET /ai/context).

    This is the DISCOVER step of the dashboard-authoring loop: it lists every
    registered query with its real ``params`` and ``output_schema`` so the agent
    binds to *real* names instead of guessing (the chief cause of invalid specs),
    plus a static ``conventions`` block describing how query binding, params, and
    ``{{vars.*}}`` work.

    Reaches the backend in-process by replicating the ``/ai/context`` route
    body over the live query registry (the route handler itself cannot be called
    directly — it is wrapped in FastAPI ``Depends(current_user)`` auth).

    Parameters
    ----------
    q:
        Optional free-text intent. When given, the deterministic grounding
        scorer ranks + filters the queries to the ones most relevant to *q*
        (most-relevant-first); queries that score zero are dropped. Omit to
        return every query in registry order.
    compact:
        When ``True`` return a trimmed per-query shape (drops ``description``,
        ``datastore``, and per-param ``default``/``options_query_id``) to shrink
        the token footprint.

    Returns
    -------
    dict
        ``{queries: [{id, name, [description, datastore,] params, output_schema}],
        conventions: {...}, compact: bool, filtered_by: str | None}``.
    """
    from app.queries.registry import get_query_registry  # noqa: PLC0415
    from app.routes.ai import (  # noqa: PLC0415
        _AI_CONTEXT_CONVENTIONS,
        _context_query_entry,
    )

    registry = get_query_registry()
    all_queries = registry.all()

    if q:
        from app.ai.grounding import build_catalog, ground  # noqa: PLC0415

        catalog = build_catalog()
        grounding = ground(q, catalog)
        ranked_ids = list(grounding.get("related_queries", []))
        by_id = {rq.id: rq for rq in all_queries}
        selected = [by_id[qid] for qid in ranked_ids if qid in by_id]
    else:
        selected = all_queries

    queries = [_context_query_entry(rq, compact=compact) for rq in selected]

    return {
        "queries": queries,
        "conventions": _AI_CONTEXT_CONVENTIONS,
        "compact": compact,
        "filtered_by": q,
    }


def _get_spec_schema() -> dict[str, Any]:
    """Return the JSON Schema for a DashboardSpec (mirrors GET /ai/dashboard/schema).

    This is the CONTRACT the agent authors against: it is the exact JSON Schema
    of the canonical ``DashboardSpec`` document (the same object accepted by
    ``validate_spec`` and ``upsert_dashboard``). Bind widget ``query_id`` and
    column names to the values discovered via ``get_context``.

    Reaches the backend in-process via ``app.dashboards.spec.spec_json_schema``.

    Returns
    -------
    dict
        The DashboardSpec JSON Schema (``{title, type, properties, ...}``).
    """
    from app.dashboards.spec import spec_json_schema  # noqa: PLC0415

    return spec_json_schema()


def _validate_spec(spec: dict[str, Any]) -> dict[str, Any]:
    """Validate a DashboardSpec WITHOUT saving (mirrors POST /dashboards/validate).

    This is the REPAIR step — the single most important authoring tool. It runs
    the canonical ``validate_spec`` parser (Pydantic parse + semantic checks:
    chart encodings, filter/text requirements, var refs, tab refs, query-id
    registry lookups) and returns *structured, repair-oriented* issues an agent
    can act on in one round-trip: each issue carries a JSON ``path`` (WHERE),
    a stable machine ``code``, a ``suggestion``, and — crucially —
    ``valid_options`` (the legal VALUES, e.g. the bound query's real columns for
    a bad chart encoding, or the known query ids for an unknown ``query_id``).

    NEVER persists anything — it is a pure validation oracle. Use it to repair a
    spec until ``valid`` is ``True`` before calling ``upsert_dashboard``.

    Reaches the backend in-process via ``app.dashboards.spec.validate_spec`` +
    ``app.dashboards.errors.to_structured_issues`` (the exact pipeline the
    ``/dashboards/validate`` route uses).

    Parameters
    ----------
    spec:
        The dashboard spec dict to validate.

    Returns
    -------
    dict
        ``{valid: bool, errors: [StructuredIssue...], warnings: [StructuredIssue...]}``.
        ``valid`` is ``True`` iff there are zero error-severity issues. Each
        StructuredIssue is ``{path, code, message, severity, suggestion,
        valid_options}``.

    Example
    -------
    A chart widget missing its x encoding yields::

        {"valid": false,
         "errors": [{"path": "widgets[0].encoding.x",
                     "code": "missing_encoding_x",
                     "message": "Widget 'w1' (chart): encoding must include 'x' column.",
                     "severity": "error",
                     "suggestion": "Set encoding.x to one of the bound query's columns ...",
                     "valid_options": ["x", "y", "category"]}],
         "warnings": []}
    """
    from app.dashboards.errors import to_structured_issues  # noqa: PLC0415
    from app.dashboards.spec import validate_spec as _vs  # noqa: PLC0415

    _spec, raw_issues = _vs(spec)
    structured = to_structured_issues(spec, raw_issues)

    errors = [i.to_dict() for i in structured if i.severity == "error"]
    warnings = [i.to_dict() for i in structured if i.severity == "warning"]

    return {"valid": not errors, "errors": errors, "warnings": warnings}


def _resolve_sql_and_params(
    query_id: "str | None",
    sql: "str | None",
    params: "list[Any] | None",
) -> "tuple[str, list[Any]]":
    """Resolve a (sql, params) pair from either a registered query_id or raw sql.

    Mirrors the input contract of POST /query/estimate (``query_id`` XOR raw
    ``sql``). When *query_id* is given it is looked up in the live registry and
    its ``.sql`` is used; otherwise the raw *sql* is used verbatim.
    """
    if query_id and sql:
        raise ValueError("Provide exactly one of query_id or sql, not both.")
    if query_id:
        from app.queries.registry import get_query_registry  # noqa: PLC0415

        registry = get_query_registry()
        rq = registry.get(query_id)
        if rq is None:
            known = [q.id for q in registry.all()]
            raise ValueError(f"Unknown query_id {query_id!r}. Known ids: {known}")
        return rq.sql, list(params or [])
    if sql:
        return sql, list(params or [])
    raise ValueError("estimate_query requires either query_id or sql.")


def _estimate_query(
    query_id: "str | None" = None,
    sql: "str | None" = None,
    params: "list[Any] | None" = None,
) -> dict[str, Any]:
    """Dry-run cost/scan estimate for a query — WITHOUT executing it.

    Mirrors POST /query/estimate (BigQuery dry-run parity): plans the SQL and
    calls ``connector.estimate(plan)``. Lets an agent gate an expensive query
    before previewing or publishing. Supply EITHER a registered ``query_id`` OR
    a raw ``sql`` string (the same XOR contract as the estimate route), plus
    optional positional ``params``.

    Transport note: the HTTP ``/query/estimate`` route is bound to per-request
    identity / RLS plumbing that does not exist in the in-process MCP context,
    so this tool reaches the same connector estimate API directly — it plans the
    SQL with ``planner.plan`` and calls ``DuckDBConnector.estimate(plan)`` (the
    same self-contained in-memory DuckDB mechanism ``run_query`` uses). It does
    NOT apply per-org RLS rewrites (there is no org/claims context here).

    Parameters
    ----------
    query_id:
        Id of a registered query (looked up in the registry for its SQL).
    sql:
        Raw SELECT SQL to estimate (alternative to ``query_id``).
    params:
        Optional positional parameters bound into the plan.

    Returns
    -------
    dict
        ``{supported, est_bytes_scanned, est_rows, est_cost, mechanism, exact,
        connector_type}``. ``supported`` is ``False`` (numeric fields ``None``)
        when the connector cannot dry-run/EXPLAIN the query.
    """
    from app.connectors.duckdb_conn import DuckDBConnector  # noqa: PLC0415
    from app.connectors import planner  # noqa: PLC0415

    resolved_sql, resolved_params = _resolve_sql_and_params(query_id, sql, params)

    connector = DuckDBConnector()
    physical_plan = planner.plan(
        resolved_sql, dialect="duckdb", params=resolved_params or None
    )
    estimate = connector.estimate(physical_plan)

    if estimate is None:
        return {
            "supported": False,
            "est_bytes_scanned": None,
            "est_rows": None,
            "est_cost": None,
            "mechanism": "unsupported",
            "exact": False,
            "connector_type": "duckdb",
        }

    return {
        "supported": True,
        "est_bytes_scanned": estimate.est_bytes_scanned,
        "est_rows": estimate.est_rows,
        "est_cost": estimate.est_cost,
        "mechanism": estimate.mechanism,
        "exact": estimate.exact,
        "connector_type": "duckdb",
    }


def _preview_widget(
    query_id: str,
    params: "list[Any] | None" = None,
    limit: int = 20,
) -> dict[str, Any]:
    """Run a registered query with a row limit and return a small preview.

    Reuses ``run_query``'s mechanism (a self-contained in-memory DuckDB
    connection — no live external database required) so an agent (e.g. a
    vision/where-needed reviewer) can sanity-check a widget's bound query and
    its real columns/values BEFORE publishing the dashboard.

    Parameters
    ----------
    query_id:
        Id of a registered query (discover ids via ``get_context`` /
        ``list_dashboards``).
    params:
        Optional positional parameters bound into the plan.
    limit:
        Maximum rows to include in the preview. Default: 20 (kept small for a
        cheap sanity-check; ``run_query`` defaults to 100 for fuller previews).

    Returns
    -------
    dict
        ``{columns: list[str], rows: list[list], row_count: int}`` — same shape
        as ``run_query``: column names, up to *limit* rows, and the total row
        count of the full result.
    """
    from app.queries.registry import get_query_registry  # noqa: PLC0415
    from app.connectors.duckdb_conn import DuckDBConnector  # noqa: PLC0415
    from app.connectors import planner  # noqa: PLC0415

    registry = get_query_registry()
    rq = registry.get(query_id)
    if rq is None:
        known = [q.id for q in registry.all()]
        raise ValueError(f"Unknown query_id {query_id!r}. Known ids: {known}")

    connector = DuckDBConnector()
    physical_plan = planner.plan(
        rq.sql, dialect="duckdb", params=list(params) if params else None
    )
    table = connector.execute(physical_plan)

    total_rows = table.num_rows
    columns = table.schema.names
    preview_table = table.slice(0, min(limit, total_rows))

    rows: list[list[Any]] = []
    if preview_table.num_rows > 0:
        col_arrays = [col.to_pylist() for col in preview_table.columns]
        for i in range(preview_table.num_rows):
            rows.append([arr[i] for arr in col_arrays])

    return {"columns": list(columns), "rows": rows, "row_count": total_rows}


#: Environments an MCP write may target by default. ``upsert_dashboard`` refuses
#: to write to anything else (e.g. ``prod``) — production is reached only via the
#: governed ``promote`` operation, which is the human-gate.
_WRITABLE_ENVS = {"dev"}

#: Stable namespace for deriving a deterministic project id per org (so the same
#: org always maps to the same project, making upsert → promote consistent).
_MCP_PROJECT_NS = uuid.UUID("0b7f2d1e-3c4a-4f6b-8e9d-1a2b3c4d5e6f")


def _project_id_for_org(org_id: str) -> str:
    """Return a stable, deterministic project id for *org_id*.

    The backend routes resolve an org's default project from Postgres; that path
    is unavailable in the in-process MCP context (no DB), so we derive a stable
    UUID5 from the org id. The env-store's ``ensure_project_envs`` then creates
    the dev/prod environments for it on demand — enough to pin + promote board
    versions without a live database.
    """
    return str(uuid.uuid5(_MCP_PROJECT_NS, str(org_id)))


def _upsert_dashboard(
    name: str,
    spec: dict[str, Any],
    env: str = "dev",
    org_id: str = "mcp",
) -> dict[str, Any]:
    """Create/update a dashboard board in the DEV environment — the governed write.

    This is the WRITE step of the authoring loop and it is governed two ways:

    1. It VALIDATES the spec first (via the exact ``validate_spec`` path) and
       REFUSES to save an invalid spec, returning the structured ``errors`` so
       the agent can repair and retry. A spec with only warnings is allowed.
    2. It will NOT target ``prod`` or any protected env: *env* must be a writable
       env (``dev`` by default). Promotion to prod is the human-gated ``promote``
       tool, never a default write.

    On success it snapshots a new resource VERSION and pins the *env*
    environment's pointer at it (using the same env-store mechanism the
    checkpoint route uses), so the board id + version are returned.

    Reaches the backend in-process via the Repo layer (boards resource) and the
    env-store (versioning/pointers) — the same singletons the backend routes use
    (and that tests inject in-memory).

    Parameters
    ----------
    name:
        Human-readable board name.
    spec:
        The DashboardSpec dict to store. MUST validate (no error-severity issues).
    env:
        Target environment key — must be writable (``dev``). Protected/prod envs
        are refused.
    org_id:
        Organisation id to scope the board under. Defaults to ``"mcp"``.

    Returns
    -------
    dict
        On success: ``{id, name, version, env, deduped}``.
        On a refused write (invalid spec): ``{ok: false, reason: "invalid_spec",
        errors: [...], warnings: [...]}`` — NO board is created.

    Raises
    ------
    ValueError
        When *env* is not a writable environment (e.g. ``"prod"``).
    """
    if env not in _WRITABLE_ENVS:
        raise ValueError(
            f"Environment {env!r} is not writable from upsert_dashboard "
            f"(writable: {sorted(_WRITABLE_ENVS)}). Production is reached only "
            f"via the human-gated promote tool."
        )

    # ── Governed gate: validate before any persistence ───────────────────────
    validation = _validate_spec(spec)
    if not validation["valid"]:
        return {
            "ok": False,
            "reason": "invalid_spec",
            "errors": validation["errors"],
            "warnings": validation["warnings"],
        }

    from app.dashboards.spec import spec_to_html, validate_spec as _vs  # noqa: PLC0415
    from app.environments.store import get_env_store  # noqa: PLC0415
    from app.repos import get_repo  # noqa: PLC0415

    # Re-parse to the typed spec for compilation (validation above guarantees ok).
    parsed, _issues = _vs(spec)
    html = spec_to_html(parsed) if parsed is not None else ""
    config = {"spec": parsed.model_dump() if parsed is not None else spec, "html": html}

    repo = get_repo()
    env_store = get_env_store()

    project_id = _project_id_for_org(org_id)

    async def _write() -> dict[str, Any]:
        # Create the board resource under a stable per-org project so its
        # environments (dev/prod) can be resolved for pinning + promotion.
        row = await repo.create(
            resource="boards",
            org_id=org_id,
            created_by="mcp-system",
            name=name,
            config=config,
            project_id=project_id,
        )
        board_id = row["id"]

        # Snapshot a version and pin the target (writable) environment at it.
        version = await env_store.create_version(
            org_id=org_id,
            project_id=project_id,
            kind="board",
            resource_id=board_id,
            config=config,
            created_by="mcp-system",
        )
        if project_id:
            await env_store.ensure_project_envs(project_id)
            environment = await env_store.get_environment_by_key(project_id, env)
            if environment is not None and not environment.get("protected"):
                await env_store.set_pointer(
                    "board",
                    board_id,
                    environment["id"],
                    version["id"],
                    promoted_by="mcp-system",
                )
        return {
            "id": board_id,
            "name": row["name"],
            "version": version.get("version"),
            "env": env,
            "deduped": bool(version.get("deduped", False)),
        }

    return _run_async(_write())


def _promote(
    board_id: str,
    from_env: str = "dev",
    to_env: str = "prod",
    org_id: str = "mcp",
) -> dict[str, Any]:
    """Promote a board version from one environment to another — the human gate.

    Copies the ``from_env`` version pointer of a board onto ``to_env`` (e.g.
    dev → prod), going through the SAME env-store promotion mechanism the
    ``/environments/promote`` route uses, so whatever protection the env system
    enforces still applies. This is the deliberate publish-to-production step:
    unlike ``upsert_dashboard``, it CAN target a protected/prod env, because
    promotion (not a raw write) is how production changes.

    Reaches the backend in-process via the env-store: it reads the ``from_env``
    pointer and ``set_pointer``s it onto ``to_env`` (preserving the exact version
    that was tested in dev — no new version is minted).

    Parameters
    ----------
    board_id:
        Id of the board to promote (as returned by ``upsert_dashboard``).
    from_env:
        Source environment key (default ``"dev"``).
    to_env:
        Target environment key (default ``"prod"``).
    org_id:
        Organisation id the board is scoped under. Defaults to ``"mcp"``.

    Returns
    -------
    dict
        On success: ``{promoted: true, board_id, from_env, to_env, version_id,
        version}``.
        When nothing is pinned in *from_env*: ``{promoted: false, reason:
        "no_source_pointer", from_env}`` — nothing is changed.

    Raises
    ------
    ValueError
        When the board or its project cannot be resolved.
    """
    from app.environments.store import get_env_store  # noqa: PLC0415
    from app.repos import get_repo  # noqa: PLC0415

    repo = get_repo()
    env_store = get_env_store()

    async def _do() -> dict[str, Any]:
        row = await repo.get("boards", org_id, board_id)
        if row is None:
            raise ValueError(f"Board {board_id!r} not found for org {org_id!r}.")
        project_id = row.get("project_id")
        if not project_id:
            raise ValueError(
                f"Board {board_id!r} has no project; cannot resolve environments."
            )

        await env_store.ensure_project_envs(project_id)
        src_env = await env_store.get_environment_by_key(project_id, from_env)
        dst_env = await env_store.get_environment_by_key(project_id, to_env)
        if src_env is None:
            raise ValueError(f"Environment {from_env!r} not found.")
        if dst_env is None:
            raise ValueError(f"Environment {to_env!r} not found.")

        pointer = await env_store.get_pointer("board", board_id, src_env["id"])
        if pointer is None:
            return {
                "promoted": False,
                "reason": "no_source_pointer",
                "from_env": from_env,
            }

        await env_store.set_pointer(
            "board",
            board_id,
            dst_env["id"],
            pointer["version_id"],
            promoted_by="mcp-system",
        )
        pinned = await env_store.get_version_by_id(pointer["version_id"])
        return {
            "promoted": True,
            "board_id": board_id,
            "from_env": from_env,
            "to_env": to_env,
            "version_id": pointer["version_id"],
            "version": (pinned or {}).get("version"),
        }

    return _run_async(_do())


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


def _run_async(coro: Any) -> Any:
    """Run *coro* to completion from synchronous code, loop-aware.

    Mirrors the loop-detection pattern in ``_create_dashboard``: if a running
    event loop already exists (e.g. inside an async test or FastAPI context) the
    coroutine is run on a worker thread via ``asyncio.run``; otherwise it is run
    directly. Keeps the MCP tool functions synchronous while still driving the
    backend's async Repo / env-store APIs.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        # No running loop — safe to call asyncio.run directly.
        return asyncio.run(coro)

    # A running loop exists — offload to a worker thread with its own loop.
    import concurrent.futures  # noqa: PLC0415

    with concurrent.futures.ThreadPoolExecutor() as pool:
        future = pool.submit(asyncio.run, coro)
        return future.result()


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
            "for pre-aggregation suggestions. "
            "To author a dashboard end-to-end, follow the loop: get_context "
            "(discover queries/params/columns) -> get_spec_schema (the contract) "
            "-> draft a DashboardSpec -> validate_spec (repair until valid) -> "
            "estimate_query / preview_widget (sanity-check) -> upsert_dashboard "
            "(governed write to DEV; refuses invalid specs) -> promote "
            "(human-gated publish to prod)."
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

    # ── Full dashboard-authoring loop (discover → contract → validate/repair →
    #    estimate → preview → governed write → human-gated promote) ────────────

    @server.tool(
        name="get_context",
        description=(
            "DISCOVER step: return the single-call authoring catalog — every "
            "registered query with its real params and output_schema, plus a "
            "conventions block. Use this FIRST so you bind widgets to real "
            "query ids and real column names instead of guessing. Pass q=<intent> "
            "to rank+filter to the most relevant queries, and compact=true to "
            "shrink the payload. Returns {queries, conventions, compact, filtered_by}."
        ),
    )
    def get_context(q: "str | None" = None, compact: bool = False) -> dict[str, Any]:
        """Return the /ai/context authoring catalog (queries + conventions)."""
        return _get_context(q, compact)

    @server.tool(
        name="get_spec_schema",
        description=(
            "CONTRACT step: return the JSON Schema for a DashboardSpec — the exact "
            "shape validate_spec and upsert_dashboard accept. Author your spec "
            "against this schema, binding query_id/columns to values from get_context."
        ),
    )
    def get_spec_schema() -> dict[str, Any]:
        """Return the DashboardSpec JSON Schema."""
        return _get_spec_schema()

    @server.tool(
        name="validate_spec",
        description=(
            "REPAIR step (most important): validate a DashboardSpec WITHOUT saving "
            "and return structured, repair-oriented issues. Returns "
            "{valid, errors, warnings}; each issue has a JSON path (WHERE), a "
            "stable code, a suggestion, and valid_options (the legal VALUES — e.g. "
            "the bound query's real columns for a bad chart encoding). Loop on this "
            "until valid=true before calling upsert_dashboard."
        ),
    )
    def validate_spec(spec: dict[str, Any]) -> dict[str, Any]:
        """Validate a DashboardSpec and return {valid, errors, warnings}."""
        return _validate_spec(spec)

    @server.tool(
        name="estimate_query",
        description=(
            "Dry-run cost/scan estimate for a query WITHOUT executing it (BigQuery "
            "dry-run parity). Supply EITHER a registered query_id OR a raw sql "
            "string (not both), plus optional positional params. Returns "
            "{supported, est_bytes_scanned, est_rows, est_cost, mechanism, exact, "
            "connector_type}. supported=false when the engine cannot dry-run."
        ),
    )
    def estimate_query(
        query_id: "str | None" = None,
        sql: "str | None" = None,
        params: "list[Any] | None" = None,
    ) -> dict[str, Any]:
        """Estimate a query's scan/cost without executing it."""
        return _estimate_query(query_id, sql, params)

    @server.tool(
        name="preview_widget",
        description=(
            "Run a registered query with a small row limit and return a preview "
            "{columns, rows, row_count} (reuses run_query's in-memory DuckDB path). "
            "Use it to sanity-check a widget's bound query and its real columns/"
            "values BEFORE publishing. Defaults to limit=20."
        ),
    )
    def preview_widget(
        query_id: str,
        params: "list[Any] | None" = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        """Preview a registered query's result (small, row-limited)."""
        return _preview_widget(query_id, params, limit)

    @server.tool(
        name="upsert_dashboard",
        description=(
            "GOVERNED WRITE: create/update a dashboard board in the DEV environment "
            "(default). It VALIDATES the spec first and REFUSES to save an invalid "
            "spec — returning {ok:false, reason:'invalid_spec', errors, warnings} so "
            "you can repair and retry (no board is created). On success returns "
            "{id, name, version, env, deduped}. It will NOT target prod/protected "
            "envs — publish to production via the promote tool."
        ),
    )
    def upsert_dashboard(
        name: str,
        spec: dict[str, Any],
        env: str = "dev",
        org_id: str = "mcp",
    ) -> dict[str, Any]:
        """Validate then create/update a board in the DEV env (governed write)."""
        return _upsert_dashboard(name, spec, env, org_id)

    @server.tool(
        name="promote",
        description=(
            "HUMAN GATE: promote a board version from one environment to another "
            "(dev -> prod by default), copying the from_env version pointer onto "
            "to_env via the env-store promotion mechanism (no new version minted). "
            "This is how production changes — it can target a protected/prod env. "
            "Returns {promoted:true, board_id, from_env, to_env, version_id, version} "
            "or {promoted:false, reason:'no_source_pointer'} when nothing is pinned."
        ),
    )
    def promote(
        board_id: str,
        from_env: str = "dev",
        to_env: str = "prod",
        org_id: str = "mcp",
    ) -> dict[str, Any]:
        """Promote a board version between environments (the human-gate write)."""
        return _promote(board_id, from_env, to_env, org_id)

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
