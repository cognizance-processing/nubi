"""Insights endpoints — cache stats and future observability surfaces.

Endpoints
---------
GET /api/v1/_cache/stats
    Return cache hit/miss counters and current entry count.
    Requires a valid JWT bearer token (``current_user`` dependency).

This module self-registers on ``api_router`` at import time, following the same
pattern as ``routes/query.py`` and ``routes/auth.py``.  Import it in
``main.py`` to activate the routes::

    import app.routes.insights  # noqa: F401
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.auth.deps import current_user
from app.connectors.cache import get_cache
from app.routes import api_router

router = APIRouter(tags=["insights"])


@router.get("/_cache/stats")
async def cache_stats(
    _user: dict = Depends(current_user),
) -> dict:
    """Return current cache statistics.

    Requires authentication (Bearer JWT).

    Parameters
    ----------
    _user:
        Authenticated user injected by ``current_user``.  Not used beyond
        confirming that the caller holds a valid token.

    Returns
    -------
    dict
        ``{"entries": int, "hits": int, "misses": int, "hit_rate": float}``

        ``entries``
            Current number of live (possibly including not-yet-evicted expired)
            entries in the cache.
        ``hits``
            Cumulative cache hits since server start or last ``cache.clear()``.
        ``misses``
            Cumulative cache misses (absent + expired) since start or clear.
        ``hit_rate``
            ``hits / (hits + misses)`` in ``[0.0, 1.0]``.  ``0.0`` when no
            requests have been made yet.
    """
    return get_cache().stats()


# ---------------------------------------------------------------------------
# Register on the shared api_router
# ---------------------------------------------------------------------------

api_router.include_router(router)
