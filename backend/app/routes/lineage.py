"""Lineage routes for the Nubi API (M7-A + notebook column lineage).

Endpoints
---------
GET /lineage
    Return the full lineage graph over all registered queries.
    Requires a valid first-party bearer token (``current_user`` dependency).

GET /lineage/query/{id}
    Return the lineage detail for a single registered query by id.
    Returns 404 when the id is not found in the registry.
    Requires a valid first-party bearer token (``current_user`` dependency).

GET /lineage/flow/{id}
    Return the column-level lineage graph for a stored FlowSpec.
    Loads the spec from the flow store, builds cross-cell column lineage,
    and returns a ``CellLineageGraph`` (nodes + edges + column_flow).
    Returns 404 when the flow id is not found.

POST /lineage/plan
    Ephemeral plan — accept a raw FlowSpec dict and a ``changed_cell_key``,
    run ``lineage_plan()``, and return the impact report.  No data is
    written.  Used by the notebook UI before durable materialise runs.

POST /lineage/cell
    Ephemeral column lineage for a single ad-hoc notebook cell (not stored).
    Accepts ``{sql, dialect, cell_key, upstream_cells: {key: sql}}``.
    Returns column-level lineage edges for the provided SQL.

GET /lineage/columns/{node_id}?column=<col>&hops=N
    Walk the DAG upstream for a specific column, returning the full
    provenance chain from source column through each model layer to the
    named metric/query.  Alias-aware; org-scoped.

Registration
-----------
A dedicated sub-router (prefix ``/lineage``) is registered on ``api_router``
via ``include_router`` at import time.  Using a sub-router with an explicit
prefix ensures these routes are not shadowed by the generic
``/{resource}`` catch-all in ``routes/resources.py``.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.auth.deps import current_user
from app.errors import AppError
from app.lineage.graph import LineageGraph, build_graph
from app.queries.registry import get_query_registry
from app.routes import api_router

# Dedicated sub-router — registered with prefix=/lineage so FastAPI resolves
# these routes before the wildcard /{resource} routes from resources.py.
_router = APIRouter(prefix="/lineage", tags=["lineage"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _resolve_org_id(user: dict[str, Any]) -> str | None:
    """Best-effort org resolution for lineage tenant-scoping (never raises).

    Mirrors ``app.routes.ai._resolve_org_id``.  ``None`` means org resolution
    was unavailable (no repo / no membership row) — callers then fall back to
    the unfiltered registry so the demo/test path keeps working.
    """
    try:
        from app.repos.provider import get_repo  # noqa: PLC0415
        from app.routes._org import get_user_org  # noqa: PLC0415

        return await get_user_org(str(user["id"]), get_repo())
    except Exception:  # noqa: BLE001 — best-effort; never break the request
        return None


async def _visible_queries_and_metrics(
    user: dict[str, Any], org_id: str | None
) -> tuple[list[Any], list[Any]]:
    """Return the (queries, metrics) visible to *org_id* — tenant-isolation gate.

    SECURITY (cross-org lineage disclosure)
    ----------------------------------------
    ``QueryRegistry`` and ``MetricRegistry`` are process-global singletons that
    span EVERY org on the deployment (same contract as ``app.routes.ai`` /
    ``app.routes.query`` / ``app.routes.metrics`` — "org scoping happens at the
    route layer", per ``load_metrics_from_queries``'s docstring). Building the
    lineage graph/DAG from the RAW, unfiltered ``.all()`` lists — as the routes
    below originally did — would let any authenticated user of ANY org see
    another org's query/metric ids, SQL structure, and column-level provenance
    via ``/lineage``, ``/lineage/query/{id}``, ``/lineage/dag``, and
    ``/lineage/columns/{node_id}``.

    This reuses the EXACT SAME visibility gate ``GET /ai/context`` already
    applies (no logic duplication): a query/metric is visible when it is a
    system/seed entry, unowned, owned by the caller's own org, or backed by a
    ``queries`` row the caller's org actually owns.
    """
    from app.metrics.registry import SEED_METRIC_IDS, get_metric_registry  # noqa: PLC0415
    from app.routes.ai import (  # noqa: PLC0415
        _query_visible_to_org,
        _visible_metric_slugs,
        _visible_query_row_ids,
    )

    registry = get_query_registry()
    metric_registry = get_metric_registry()

    row_ids = await _visible_query_row_ids(user, org_id)
    queries = [
        rq
        for rq in registry.all()
        if _query_visible_to_org(rq, caller_org=org_id, row_ids=row_ids)
    ]

    metric_slugs = await _visible_metric_slugs(org_id)
    metrics = [
        md
        for md in metric_registry.all()
        if metric_slugs is None or md.id in metric_slugs or md.id in SEED_METRIC_IDS
    ]
    return queries, metrics


def _get_graph(queries: list[Any] | None = None) -> LineageGraph:
    """Build the lineage graph from *queries* (default: the FULL registry).

    This is a synchronous helper called inline from route handlers.  For M7-A
    the graph is rebuilt on every request (cheap; ~ms); a caching layer can be
    added in a later milestone.

    Parameters
    ----------
    queries:
        Pre-filtered, org-visible query list.  Callers MUST pass the
        tenant-scoped list (see ``_visible_queries_and_metrics``) — passing
        ``None`` builds the graph from every query in the process-global
        registry, which is only safe for internal/trusted callers.

    Returns
    -------
    LineageGraph
        Fully populated lineage graph.
    """
    if queries is None:
        queries = get_query_registry().all()
    return build_graph(queries)


def _graph_to_dict(graph: LineageGraph) -> dict[str, Any]:
    """Serialise a ``LineageGraph`` to a JSON-safe dict.

    Parameters
    ----------
    graph:
        The lineage graph to serialise.

    Returns
    -------
    dict
        ``{"queries": {...}, "tables": {...}, "columns": {...}}``
    """
    return {
        "queries": graph.queries,
        "tables": graph.tables,
        "columns": graph.columns,
    }


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@_router.get("")
async def get_lineage(
    _user: dict[str, Any] = Depends(current_user),
) -> dict[str, Any]:
    """Return the full lineage graph over the CALLER'S ORG's registered queries.

    Org-scoped (SEC): the query registry is a process-global singleton
    spanning every org, so the visible query set is gated by the caller's org
    — see ``_visible_queries_and_metrics``. System/seed queries (no tenant)
    are always included.

    Parameters
    ----------
    _user:
        Injected by FastAPI; the authenticated user dict.

    Returns
    -------
    dict
        ``{"queries": {id: {sql, name, tables, columns, outputs}},
        "tables": {table: [query_ids]},
        "columns": {"table.column": [query_ids]}}``
    """
    org_id = await _resolve_org_id(_user)
    queries, _metrics = await _visible_queries_and_metrics(_user, org_id)
    graph = _get_graph(queries)
    return _graph_to_dict(graph)


@_router.get("/query/{query_id}")
async def get_lineage_for_query(
    query_id: str,
    _user: dict[str, Any] = Depends(current_user),
) -> dict[str, Any]:
    """Return the lineage detail for a single registered query.

    Org-scoped (SEC): a query owned by a DIFFERENT org (or not visible to the
    caller) is treated as 404 — same non-disclosure contract as the cross-org
    IDOR gates elsewhere (connectors/flows/metrics).

    Parameters
    ----------
    query_id:
        The registered query identifier (e.g. ``"demo_all"``).
    _user:
        Injected by FastAPI; the authenticated user dict.

    Returns
    -------
    dict
        ``{"id": str, "sql": str, "name": str, "tables": [...],
        "columns": [...], "outputs": [...]}``

    Raises
    ------
    AppError("query_not_found", 404)
        If *query_id* is not in the query registry, or belongs to another org.
    """
    registry = get_query_registry()
    rq = registry.get(query_id)
    if rq is None:
        raise AppError("query_not_found", f"No registered query with id '{query_id}'.", 404)

    org_id = await _resolve_org_id(_user)
    visible_queries, _metrics = await _visible_queries_and_metrics(_user, org_id)
    if query_id not in {q.id for q in visible_queries}:
        # Cross-org: same 404 as "not found" — no existence disclosure.
        raise AppError("query_not_found", f"No registered query with id '{query_id}'.", 404)

    graph = _get_graph(visible_queries)
    detail = graph.for_query(query_id)
    if detail is None:
        # Shouldn't happen but guard defensively.
        raise AppError("query_not_found", f"No lineage for query '{query_id}'.", 404)

    return {"id": query_id, **detail}


# ---------------------------------------------------------------------------
# Pydantic request models for new endpoints
# ---------------------------------------------------------------------------


class CellLineageRequest(BaseModel):
    """Request body for POST /lineage/cell — ad-hoc single-cell column lineage."""

    sql: str = Field(description="SQL string of the cell to analyse.")
    dialect: str = Field(default="duckdb", description="sqlglot dialect for parsing.")
    cell_key: str = Field(default="", description="Optional stable key for this cell.")
    upstream_cells: dict[str, str] = Field(
        default_factory=dict,
        description="Mapping of upstream cell key → SQL string for cross-cell tracing.",
    )


class PlanRequest(BaseModel):
    """Request body for POST /lineage/plan — ephemeral plan-before-apply."""

    spec: dict[str, Any] = Field(description="Raw FlowSpec dict.")
    changed_cell_key: str = Field(
        description="Key of the cell that is about to change.",
    )


# ---------------------------------------------------------------------------
# New endpoints: flow lineage + plan + cell
# ---------------------------------------------------------------------------


@_router.get("/flow/{flow_id}")
async def get_flow_lineage(
    flow_id: str,
    _user: dict[str, Any] = Depends(current_user),
) -> dict[str, Any]:
    """Return the column-level lineage graph for a stored FlowSpec.

    Parameters
    ----------
    flow_id:
        UUID of the flow in the flow store.
    _user:
        Authenticated user (not used in response but enforces auth).

    Returns
    -------
    dict
        ``{"flow_id": str, "lineage": {nodes, edges, column_flow}}``

    Raises
    ------
    AppError("flow_not_found", 404)
        If *flow_id* is not found in the flow store.
    """
    from app.flows.lineage import build_cell_lineage_graph, _serialise_graph  # noqa: PLC0415
    from app.flows.spec import validate_flow_spec  # noqa: PLC0415
    from app.flows.store import get_flow_store  # noqa: PLC0415

    store = get_flow_store()
    flow = await store.get_flow(flow_id)
    if flow is None:
        raise AppError("flow_not_found", f"No flow with id '{flow_id}'.", 404)

    spec_data = flow.get("spec") or {}
    validated_spec, issues = validate_flow_spec(spec_data)
    if validated_spec is None:
        return {
            "flow_id": flow_id,
            "issues": issues,
            "lineage": None,
        }

    graph = build_cell_lineage_graph(validated_spec)
    return {
        "flow_id": flow_id,
        "issues": issues,
        "lineage": _serialise_graph(graph),
    }


@_router.post("/plan")
async def post_lineage_plan(
    body: PlanRequest,
    _user: dict[str, Any] = Depends(current_user),
) -> dict[str, Any]:
    """Ephemeral plan-before-apply: validate a FlowSpec and return column lineage + impact.

    This endpoint does **not** persist any data.  It is the notebook UI's
    "plan gate" — call it before triggering a durable materialise run to
    understand which downstream cells would be affected by changing
    ``changed_cell_key``.

    Parameters
    ----------
    body:
        ``{spec: FlowSpec dict, changed_cell_key: str}``
    _user:
        Authenticated user.

    Returns
    -------
    dict
        ``{valid, issues, lineage, downstream_impact}``
        See ``lineage_plan()`` in ``app.flows.lineage`` for the full schema.
    """
    from app.flows.lineage import lineage_plan  # noqa: PLC0415

    return lineage_plan(body.spec, body.changed_cell_key)


@_router.post("/cell")
async def post_cell_lineage(
    body: CellLineageRequest,
    _user: dict[str, Any] = Depends(current_user),
) -> dict[str, Any]:
    """Ephemeral column lineage for a single ad-hoc notebook cell.

    Accepts raw SQL + optional upstream cell SQL strings and returns the
    column-level lineage edges.  Nothing is stored; this endpoint is called
    by the notebook UI after each interactive cell run to render the lineage
    panel.

    Parameters
    ----------
    body:
        ``{sql, dialect, cell_key, upstream_cells}``
    _user:
        Authenticated user.

    Returns
    -------
    dict
        ``{"cell_key": str, "edges": list[dict]}``
        Each edge: ``{output_col, from_table, from_col, source_name}``.
    """
    from app.flows.lineage import extract_column_lineage  # noqa: PLC0415

    edges = extract_column_lineage(
        sql=body.sql,
        dialect=body.dialect or "duckdb",
        sources=body.upstream_cells or {},
    )
    return {
        "cell_key": body.cell_key,
        "edges": edges,
    }


# ---------------------------------------------------------------------------
# Register on the shared api_router
# ---------------------------------------------------------------------------

# Include BEFORE resources.py's wildcard /{resource} routes.  Since main.py
# imports app.routes.lineage after app.routes.resources, we rely on FastAPI's
# sub-router merging: because our routes have a concrete prefix "/lineage" they
# take precedence over the catch-all "/{resource}" in any router order.


# ---------------------------------------------------------------------------
# DAG endpoints (A.2)
# ---------------------------------------------------------------------------


@_router.get("/dag")
async def get_lineage_dag(
    _user: dict[str, Any] = Depends(current_user),
) -> dict[str, Any]:
    """Return the full inter-model dependency DAG (queries + metrics + tables).

    Org-scoped: the caller's org determines which queries/metrics are visible.
    For the system seed queries/metrics (no org) the full seed set is returned.

    Returns
    -------
    dict
        ``{"nodes": [...], "edges": [...]}``
        Each node: ``{id, type, name, tables, outputs, columns}``.
        Each edge: ``{from, to, via}``.
    """
    from app.lineage.dag import build_dag  # noqa: PLC0415

    org_id = await _resolve_org_id(_user)
    queries, metrics = await _visible_queries_and_metrics(_user, org_id)
    dag = build_dag(queries, metrics)
    return dag.to_dict()


@_router.get("/dag/{node_id:path}")
async def get_lineage_dag_node(
    node_id: str,
    hops: int = 3,
    _user: dict[str, Any] = Depends(current_user),
) -> dict[str, Any]:
    """Return the upstream/downstream neighbourhood of a single DAG node.

    Parameters
    ----------
    node_id:
        Id of the query, metric, or table node.
    hops:
        Maximum traversal depth (default 3, max 20).

    Returns
    -------
    dict
        ``{"node_id", "node", "hops", "upstream": [...ids], "downstream": [...ids]}``

    Raises
    ------
    AppError("node_not_found", 404)
        If *node_id* is not in the DAG, or belongs to another org (SEC: same
        404 as "not found" — no existence disclosure across tenants).
    """
    from app.lineage.dag import build_dag  # noqa: PLC0415

    org_id = await _resolve_org_id(_user)
    queries, metrics = await _visible_queries_and_metrics(_user, org_id)
    dag = build_dag(queries, metrics)

    node = dag.nodes.get(node_id)
    if node is None:
        raise AppError(
            "node_not_found",
            f"No DAG node found with id '{node_id}'.",
            404,
        )

    hops = max(1, min(hops, 20))
    return {
        "node_id": node_id,
        "node": node.to_dict(),
        "hops": hops,
        "upstream": dag.upstream(node_id, hops),
        "downstream": dag.downstream(node_id, hops),
    }


# ---------------------------------------------------------------------------
# Column-level lineage endpoint (Feature A – cross-model column provenance)
# ---------------------------------------------------------------------------


@_router.get("/columns/{node_id:path}")
async def get_column_lineage(
    node_id: str,
    column: str,
    hops: int = 10,
    _user: dict[str, Any] = Depends(current_user),
) -> dict[str, Any]:
    """Walk the DAG upstream for *column* starting at *node_id*.

    Resolves aliases and renames at each model layer, returning the full
    provenance chain from the requested metric/query node back to the physical
    source table/column.

    Parameters
    ----------
    node_id:
        Id of the starting DAG node (query, metric, or table).  Path
        parameter so it may contain slashes.
    column:
        The output column name to trace (query parameter).
    hops:
        Maximum traversal depth (default 10, ceiling 20).

    Returns
    -------
    dict
        ``{"node_id", "column", "hops", "path": [{node, column,
        select_star, alias}, ...]}``

    Raises
    ------
    AppError("node_not_found", 404)
        If *node_id* is not in the DAG, or belongs to another org (SEC: same
        404 as "not found" — a cross-tenant caller cannot distinguish
        "doesn't exist" from "not yours").
    AppError("column_required", 400)
        If the ``column`` query parameter is missing or empty.
    """
    from app.lineage.dag import build_dag, resolve_column_lineage  # noqa: PLC0415

    if not column or not column.strip():
        raise AppError("column_required", "Query parameter 'column' is required.", 400)

    # SEC: the query/metric registries are process-global singletons spanning
    # every org — build the DAG from ONLY the caller's org-visible entries so
    # this endpoint can never disclose another org's node/column provenance.
    org_id = await _resolve_org_id(_user)
    queries, metrics = await _visible_queries_and_metrics(_user, org_id)
    dag = build_dag(queries, metrics)

    if node_id not in dag.nodes:
        raise AppError(
            "node_not_found",
            f"No DAG node found with id '{node_id}'.",
            404,
        )

    hops = max(1, min(hops, 20))
    path = resolve_column_lineage(dag, node_id, column.strip(), max_hops=hops)

    return {
        "node_id": node_id,
        "column": column.strip(),
        "hops": hops,
        "path": path,
    }


# Register on the shared api_router (after all routes are defined)
api_router.include_router(_router)
