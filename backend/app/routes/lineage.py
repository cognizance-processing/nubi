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


def _get_graph() -> LineageGraph:
    """Build the lineage graph from the current query registry.

    This is a synchronous helper called inline from route handlers.  For M7-A
    the graph is rebuilt on every request (cheap; ~ms); a caching layer can be
    added in a later milestone.

    Returns
    -------
    LineageGraph
        Fully populated lineage graph.
    """
    registry = get_query_registry()
    return build_graph(registry.all())


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
    """Return the full lineage graph over all registered queries.

    Parameters
    ----------
    _user:
        Injected by FastAPI; the authenticated user dict.  Not used in the
        response body but required to enforce authentication.

    Returns
    -------
    dict
        ``{"queries": {id: {sql, name, tables, columns, outputs}},
        "tables": {table: [query_ids]},
        "columns": {"table.column": [query_ids]}}``
    """
    graph = _get_graph()
    return _graph_to_dict(graph)


@_router.get("/query/{query_id}")
async def get_lineage_for_query(
    query_id: str,
    _user: dict[str, Any] = Depends(current_user),
) -> dict[str, Any]:
    """Return the lineage detail for a single registered query.

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
        If *query_id* is not in the query registry.
    """
    registry = get_query_registry()
    rq = registry.get(query_id)
    if rq is None:
        raise AppError("query_not_found", f"No registered query with id '{query_id}'.", 404)

    graph = _get_graph()
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
api_router.include_router(_router)
