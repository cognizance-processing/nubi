"""Org / project resolution helpers — route-free, side-effect-free.

These helpers used to live in ``app/routes/resources.py`` alongside the generic
``/{resource}`` CRUD catch-all.  That coupling was a footgun: any module that
imported a helper (``from app.routes.resources import resolve_org_id``) also
triggered ``resources.py``'s ``api_router.include_router(...)`` as an import
side-effect, registering the greedy ``/{resource}`` catch-all *ahead* of any
prefixed router (``/flows``, ``/preagg``, …) that imported the helper before its
own routes were registered — silently shadowing those endpoints with 404s.

Several routers (``jobs``, ``flows``, ``lineage``) even copy-pasted
``get_user_org`` verbatim just to avoid importing ``resources``.  This module is
the shared, import-safe home for that logic: it registers **no** routes, so
importing it can never perturb route ordering.

``resources.py`` re-exports these names for backwards compatibility.
"""

from __future__ import annotations

import contextvars

from fastapi import Request

from app.db import fetchrow
from app.errors import AppError
from app.repos.provider import Repo

# When the caller authenticated with a long-lived API key, the key is bound to
# the org it was minted for. ``current_user`` records that org id here so the
# org-resolution helpers below can PIN the request to it — an API key must never
# be usable against any other org, even one the underlying user also belongs to.
# A contextvar keeps the binding request-scoped without threading it through
# every call site (the helpers already take only user_id/repo/request).
api_key_org_pin: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "api_key_org_pin", default=None
)


# ── Org resolution helpers ────────────────────────────────────────────────────

async def get_user_org(user_id: str, repo: Repo) -> str:
    """Return the default org_id for the user's first membership.

    For the ``InMemoryRepo`` test double the membership is seeded by the test
    via ``repo.seed_org_member()``.  For the ``PgRepo`` production
    implementation we query ``org_members`` directly via the DB helper
    (since the repo protocol only handles domain resources, not auth tables).

    Parameters
    ----------
    user_id:
        UUID string of the authenticated user.
    repo:
        The active Repo implementation (used for InMemoryRepo's helper).

    Returns
    -------
    str
        The ``org_id`` UUID string.

    Raises
    ------
    AppError("org_not_found", 404)
        If the user has no org membership.
    """
    # API-key requests are pinned to the org the key was minted for.
    pinned = api_key_org_pin.get()
    if pinned:
        return pinned

    # InMemoryRepo exposes get_org_for_user(); use it when available.
    if hasattr(repo, "get_org_for_user"):
        org_id = repo.get_org_for_user(user_id)  # type: ignore[attr-defined]
        if org_id:
            return org_id
        raise AppError("org_not_found", "User has no org membership.", 404)

    # PgRepo path: query org_members via the DB helper.
    row = await fetchrow(
        """
        SELECT org_id FROM org_members
        WHERE user_id = $1::uuid
        ORDER BY org_id
        LIMIT 1
        """,
        user_id,
    )
    if row is None:
        raise AppError("org_not_found", "User has no org membership.", 404)
    return str(row["org_id"])


async def _user_is_member(user_id: str, org_id: str, repo: Repo) -> bool:
    """Return True if user is a member of org_id.

    Checks InMemoryRepo's in-memory state for test doubles; falls back to
    querying ``org_members`` for the PgRepo production path.
    """
    if hasattr(repo, "_org_members"):
        # InMemoryRepo stores members as "{org_id}:{user_id}" keys.
        key = f"{org_id}:{user_id}"
        return key in repo._org_members  # type: ignore[attr-defined]

    row = await fetchrow(
        """
        SELECT 1 FROM org_members
        WHERE user_id = $1::uuid
          AND org_id  = $2::uuid
        LIMIT 1
        """,
        user_id,
        org_id,
    )
    return row is not None


async def resolve_org_id(user_id: str, repo: Repo, request: Request) -> str:
    """Resolve the effective org_id for the current request.

    The caller may pass ``X-Org-Id`` to switch to a different org.  We verify
    the user is a member of the requested org before honouring it; if not, we
    raise 403 (not 404) to distinguish "you can't access this org" from "this
    org doesn't exist".  When the header is absent or empty we fall back to the
    user's default (first) org.

    Parameters
    ----------
    user_id:
        UUID string of the authenticated user.
    repo:
        The active Repo implementation.
    request:
        The incoming FastAPI request (used to read the ``X-Org-Id`` header).

    Returns
    -------
    str
        The verified org_id UUID string to use for this request.

    Raises
    ------
    AppError("forbidden", 403)
        If ``X-Org-Id`` is set but the user is not a member of that org.
    AppError("org_not_found", 404)
        If the user has no org membership at all (no header case).
    """
    # API-key requests are pinned to the minting org. A mismatching X-Org-Id is
    # rejected (403) so a key can never be redirected to another org the user
    # also belongs to; an absent/matching header resolves to the pinned org.
    pinned = api_key_org_pin.get()
    if pinned:
        requested = request.headers.get("x-org-id", "").strip()
        if requested and requested != pinned:
            raise AppError(
                "forbidden",
                "This API key is scoped to a different organisation.",
                403,
            )
        return pinned

    requested_org_id = request.headers.get("x-org-id", "").strip()

    if not requested_org_id:
        # No header — use the default org.
        return await get_user_org(user_id, repo)

    # Header present — verify membership before honouring it.
    is_member = await _user_is_member(user_id, requested_org_id, repo)
    if not is_member:
        raise AppError(
            "forbidden",
            "You are not a member of the requested organisation.",
            403,
        )
    return requested_org_id


# ── Project resolution helpers ─────────────────────────────────────────────────

def _requested_project_id(request: Request) -> str:
    """Return the requested project id from header or ``?project_id=`` query."""
    pid = request.headers.get("x-project-id", "").strip()
    if pid:
        return pid
    return (request.query_params.get("project_id") or "").strip()


async def resolve_project_id_for_create(org_id: str, request: Request) -> str | None:
    """Resolve the project a newly-created resource should belong to.

    Mirrors the ``X-Org-Id`` handling: the caller may pass ``X-Project-Id`` (or
    ``?project_id=``) to target a specific project. We honour it only when it
    is valid for *org_id*; otherwise (header absent, or invalid/foreign) we fall
    back to the org's default project. Returns ``None`` only when no default
    project can be resolved (e.g. test doubles without a projects table) — in
    which case the resource is created with a NULL project_id.
    """
    from app.repos import projects as projects_repo  # noqa: PLC0415

    requested = _requested_project_id(request)
    if requested and await projects_repo.project_belongs_to_org(requested, org_id):
        return requested
    # Fall back to the org's default project (None if none exists).
    return await projects_repo.get_default_project_id(org_id)


async def resolve_org_default_project_id(org_id: str) -> str | None:
    """Return the org's default (oldest) project id, or ``None``.

    Prefers :func:`projects_repo.get_default_project_id` and falls back to the
    first row of :func:`projects_repo.list_projects` — both mean "the org's
    oldest project" in production, but the fallback also resolves under
    fetchrow-level test doubles that only serve list-shaped project queries.
    Used by the environments/versions resolution paths.
    """
    from app.repos import projects as projects_repo  # noqa: PLC0415

    pid = await projects_repo.get_default_project_id(org_id)
    if pid:
        return pid
    try:
        rows = await projects_repo.list_projects(org_id)
    except Exception:  # noqa: BLE001 — degrade gracefully like the repo helpers
        return None
    return str(rows[0]["id"]) if rows else None


async def resolve_project_filter(org_id: str, request: Request) -> str | None:
    """Resolve the project filter for list endpoints.

    Mirrors :func:`resolve_project_id_for_create`: honour ``X-Project-Id`` /
    ``?project_id=`` when present and valid for *org_id*, else fall back to the
    org's default project. Lists are therefore scoped to one project (the active
    one, or the default) instead of returning *every* project's resources —
    otherwise another project's content would leak into the active project's
    queries/dashboards/connectors lists. Each project only ever lists its own
    resources (the demo bundle, when present, lives in the default project).

    Returns ``None`` only when no default project can be resolved (e.g. test
    doubles without a projects table), in which case the list is unfiltered.
    """
    from app.repos import projects as projects_repo  # noqa: PLC0415

    requested = _requested_project_id(request)
    if requested and await projects_repo.project_belongs_to_org(requested, org_id):
        return requested
    # Default-project fallback: resolve_org_default_project_id also tries the
    # list-shaped projects query, which keeps headerless lists scoped under
    # fetchrow-level test doubles that only serve list queries.
    return await resolve_org_default_project_id(org_id)
