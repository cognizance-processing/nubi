"""Query-cache administration endpoints (stats + explicit invalidation).

Endpoints
---------
GET /cache/stats
    Returns the active backend's ``stats()`` plus a ``backend`` field
    (``"redis"`` | ``"memory"``) so an operator can see which store is live.

POST /cache/invalidate
    Body ``{tag?: str, all?: bool}``:
      * ``all=true``  → :meth:`invalidate_all` (clear the whole cache).
      * else ``tag``  → :meth:`invalidate` (evict every entry carrying *tag*).
      * neither       → 400 ``invalid_request``.
    Returns ``{"invalidated": <count>, "backend": "redis"|"memory"}``.

    This is the explicit tenant-/datastore-scoped invalidation hook: the query
    path tags cached results with ``org:<id>`` (and ``datastore:<id>``), so an
    operator can flush one tenant's cache with ``{"tag": "org:<id>"}``.

Auth
----
Both routes require a first-party Bearer access token via ``current_user`` —
exactly the gate used by the sibling first-party write routes (``register_query``,
``/dashboards/validate``, ``/ai/*``).  ``current_user`` only accepts first-party
HS256 access tokens, so host-signed embed JWTs (RS256/ES256) cannot decode and
are rejected with 401.  Mutating the shared cache is an operator action and must
never be reachable from an embed token.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.auth.deps import current_user
from app.connectors.cache import (
    ContentAddressedCache,
    RedisCacheBackend,
    get_cache,
)
from app.errors import AppError

router = APIRouter(prefix="/cache", tags=["cache"])


def _backend_name(cache: Any) -> str:
    """Return ``"redis"`` or ``"memory"`` for the active backend."""
    if isinstance(cache, RedisCacheBackend):
        return "redis"
    if isinstance(cache, ContentAddressedCache):
        return "memory"
    # Defensive: unknown backend type — report its class name lowercased.
    return type(cache).__name__.lower()


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class InvalidateRequest(BaseModel):
    """Request body for POST /cache/invalidate."""

    tag: str | None = None
    all: bool = False


class InvalidateResponse(BaseModel):
    """Response body for POST /cache/invalidate."""

    invalidated: int
    backend: str


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/stats")
async def cache_stats(
    _user: dict[str, Any] = Depends(current_user),
) -> dict[str, Any]:
    """Return the active cache's statistics plus its backend name."""
    cache = get_cache()
    stats = cache.stats()
    return {**stats, "backend": _backend_name(cache)}


@router.post("/invalidate", response_model=InvalidateResponse)
async def cache_invalidate(
    body: InvalidateRequest,
    _user: dict[str, Any] = Depends(current_user),
) -> InvalidateResponse:
    """Invalidate the whole cache (``all=true``) or every entry under *tag*."""
    cache = get_cache()
    if body.all:
        count = cache.invalidate_all()
    elif body.tag:
        count = cache.invalidate(body.tag)
    else:
        raise AppError(
            "invalid_request",
            "Provide either `all: true` or a non-empty `tag` to invalidate.",
            400,
        )
    return InvalidateResponse(invalidated=count, backend=_backend_name(cache))
