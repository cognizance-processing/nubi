"""Pre-aggregation suggestion endpoints.

Endpoints
---------
GET  /api/v1/_preagg/suggestions
    Return current rollup suggestions derived from the query log.
    Requires a valid JWT bearer token (``current_user`` dependency).

POST /api/v1/_preagg/register
    Register a rollup table for a given ``groupby_sig`` (demo/testing).
    Requires a valid JWT bearer token.

This module self-registers on ``api_router`` at import time.  Add the
following to ``main.py`` to activate the routes::

    import app.routes.preagg  # noqa: F401
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.auth.deps import current_user
from app.connectors.preagg import get_registry, suggest
from app.connectors.query_log import get_query_log
from app.routes import api_router

router = APIRouter(tags=["preagg"])


# ---------------------------------------------------------------------------
# GET /_preagg/suggestions
# ---------------------------------------------------------------------------


@router.get("/_preagg/suggestions")
async def preagg_suggestions(
    min_hits: int = 3,
    _user: dict = Depends(current_user),
) -> list[dict]:
    """Return pre-aggregation rollup suggestions from the query log.

    Analyses the in-memory query log and returns suggestions for GROUP BY
    patterns that have been seen at least *min_hits* times.

    Parameters
    ----------
    min_hits:
        Minimum hit count to include a suggestion.  Default ``3``.
    _user:
        Authenticated user injected by ``current_user``.  Required for auth.

    Returns
    -------
    list[dict]
        Each dict has keys: ``base_table``, ``dimensions``, ``measures``,
        ``hits``, ``est_bytes_saved``, ``sig``.
        Sorted by ``hits`` descending.
    """
    suggestions = suggest(get_query_log(), min_hits=min_hits)
    return [s.to_dict() for s in suggestions]


# ---------------------------------------------------------------------------
# POST /_preagg/register
# ---------------------------------------------------------------------------


class RegisterRollupIn(BaseModel):
    """Request body for POST /_preagg/register.

    Attributes
    ----------
    sig:
        The normalised ``groupby_sig`` string that identifies the GROUP BY
        pattern (as returned in suggestion dicts under the ``sig`` key).
    table:
        The name of the materialised rollup table to route matching queries to.
    """

    sig: str
    table: str


@router.post("/_preagg/register")
async def register_rollup(
    body: RegisterRollupIn,
    _user: dict = Depends(current_user),
) -> dict:
    """Register a rollup table for a given groupby_sig.

    Parameters
    ----------
    body:
        ``RegisterRollupIn`` JSON body with ``sig`` and ``table``.
    _user:
        Authenticated user injected by ``current_user``.

    Returns
    -------
    dict
        ``{"registered": true, "sig": sig, "table": table}``
    """
    get_registry().register(body.sig, body.table)
    return {"registered": True, "sig": body.sig, "table": body.table}


# ---------------------------------------------------------------------------
# Register on the shared api_router
# ---------------------------------------------------------------------------

api_router.include_router(router)
