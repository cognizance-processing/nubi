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

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from app.auth.deps import current_user
from app.auth.roles import require_writer
from app.auth.superadmin import caller_is_superadmin, require_superadmin
from app.connectors.cache import (
    ContentAddressedCache,
    RedisCacheBackend,
    get_cache,
)
from app.errors import AppError
from app.repos.provider import Repo, get_repo
from app.routes._org import resolve_org_id

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
    _admin: dict[str, Any] = Depends(require_superadmin),
) -> dict[str, Any]:
    """Return the active cache's statistics plus its backend name.

    The cache is shared by every organisation, so its counters are an
    operator-only view.
    """
    cache = get_cache()
    stats = cache.stats()
    return {**stats, "backend": _backend_name(cache)}


async def _require_own_tag(tag: str, org_id: str, repo: Repo) -> None:
    """A member may only invalidate cache entries tagged for their own org:
    the org tag itself, or a board that belongs to the org."""
    kind, _, ident = tag.partition(":")
    if kind == "org" and ident == str(org_id):
        return
    if kind == "board" and ident and await repo.get("boards", org_id, ident) is not None:
        return
    raise AppError(
        "forbidden", "That cache tag does not belong to this organisation.", 403
    )


@router.post("/invalidate", response_model=InvalidateResponse)
async def cache_invalidate(
    body: InvalidateRequest,
    request: Request,
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
    is_operator: bool = Depends(caller_is_superadmin),
) -> InvalidateResponse:
    """Invalidate cache entries.

    ``all=true`` clears every organisation's entries and is operator-only.
    A *tag* is limited to the caller's own organisation unless the caller is
    an operator.
    """
    cache = get_cache()
    if body.all:
        if not is_operator:
            raise AppError("forbidden", "Superadmin access required.", 403)
        count = cache.invalidate_all()
    elif body.tag:
        if not is_operator:
            # A tenant needs write access in the org and may only touch its tags.
            await require_writer(request, user, repo)
            org_id = await resolve_org_id(str(user["id"]), repo, request)
            await _require_own_tag(body.tag, org_id, repo)
        count = cache.invalidate(body.tag)
    else:
        raise AppError(
            "invalid_request",
            "Provide either `all: true` or a non-empty `tag` to invalidate.",
            400,
        )
    return InvalidateResponse(invalidated=count, backend=_backend_name(cache))
