"""Lineage routes for the Nubi API (M7-A).

Endpoints
---------
GET /lineage
    Return the full lineage graph over all registered queries.
    Requires a valid first-party bearer token (``current_user`` dependency).

GET /lineage/query/{id}
    Return the lineage detail for a single registered query by id.
    Returns 404 when the id is not found in the registry.
    Requires a valid first-party bearer token (``current_user`` dependency).

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
# Register on the shared api_router
# ---------------------------------------------------------------------------

# Include BEFORE resources.py's wildcard /{resource} routes.  Since main.py
# imports app.routes.lineage after app.routes.resources, we rely on FastAPI's
# sub-router merging: because our routes have a concrete prefix "/lineage" they
# take precedence over the catch-all "/{resource}" in any router order.
api_router.include_router(_router)
