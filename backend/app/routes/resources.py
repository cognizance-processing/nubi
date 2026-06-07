"""Generic CRUD router for org-scoped resources.

Exposes five endpoints for each resource in the allowlist
(``datastores``, ``boards``, ``queries``, ``widgets``)::

    GET    /{resource}         — list all rows for the caller's org.
    POST   /{resource}         — create a new row; returns 201.
    GET    /{resource}/{id}    — fetch a single row (404 if wrong org or missing).
    PUT    /{resource}/{id}    — update name/config (404 if wrong org or missing).
    DELETE /{resource}/{id}    — delete the row; returns 204.

Authentication
--------------
Every endpoint requires a valid first-party Bearer token (``current_user``
dependency).  The caller's ``org_id`` is resolved via ``resolve_org_id``
which honours the ``X-Org-Id`` request header when present (the user must be
a member of that org — otherwise 403; or falls back to their default org).

Cross-org protection
--------------------
``get`` / ``update`` / ``delete`` return 404 (not 403) for rows that exist
but belong to a different org — no information leaks about other orgs'
resources.

Unknown resource names in the URL path also return 404.

This module attaches itself to the shared ``api_router`` at import time so
that ``main.py``'s ``include_router(api_router, prefix="/api/v1")`` picks
it up automatically.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel

from app.auth.deps import current_user
from app.db import fetchrow, fetch
from app.errors import AppError
from app.repos.base import VALID_RESOURCES
from app.repos.provider import get_repo, Repo
from app.routes import api_router

# ── Sub-router ────────────────────────────────────────────────────────────────
router = APIRouter(tags=["resources"])


# ── Pydantic request schemas ──────────────────────────────────────────────────

class CreateIn(BaseModel):
    """Request body for POST /{resource}."""

    name: str
    config: dict[str, Any] = {}


class UpdateIn(BaseModel):
    """Request body for PUT /{resource}/{id}."""

    name: str | None = None
    config: dict[str, Any] | None = None


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


async def resolve_project_filter(org_id: str, request: Request) -> str | None:
    """Resolve an optional project filter for list endpoints.

    Returns the requested project id when ``X-Project-Id`` / ``?project_id=`` is
    present *and* valid for *org_id*; otherwise ``None`` (meaning: don't filter,
    return all the org's resources — existing behaviour preserved).
    """
    from app.repos import projects as projects_repo  # noqa: PLC0415

    requested = _requested_project_id(request)
    if requested and await projects_repo.project_belongs_to_org(requested, org_id):
        return requested
    return None


# ── Validation helper ─────────────────────────────────────────────────────────

def _require_valid_resource(resource: str) -> None:
    """Raise AppError 404 if *resource* is not in the allowlist."""
    if resource not in VALID_RESOURCES:
        raise AppError("not_found", f"Unknown resource: {resource!r}.", 404)


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/{resource}")
async def list_resources(
    resource: str,
    request: Request,
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
) -> list[dict[str, Any]]:
    """List all rows for the caller's org.

    Honoures the ``X-Org-Id`` header to switch org context (membership checked).

    Returns
    -------
    list[dict]
        Possibly empty list of resource rows.
    """
    _require_valid_resource(resource)
    org_id = await resolve_org_id(str(user["id"]), repo, request)
    project_id = await resolve_project_filter(org_id, request)
    return await repo.list(resource, org_id, project_id)


@router.post("/{resource}", status_code=201)
async def create_resource(
    resource: str,
    body: CreateIn,
    request: Request,
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
) -> dict[str, Any]:
    """Create a new resource row.

    Honoures the ``X-Org-Id`` header to switch org context (membership checked).

    Returns
    -------
    dict
        The newly created row (includes ``id``, ``created_at``, etc.).
    """
    _require_valid_resource(resource)
    org_id = await resolve_org_id(str(user["id"]), repo, request)
    project_id = await resolve_project_id_for_create(org_id, request)
    return await repo.create(
        resource=resource,
        org_id=org_id,
        created_by=str(user["id"]),
        name=body.name,
        config=body.config,
        project_id=project_id,
    )


@router.get("/{resource}/{id}")
async def get_resource(
    resource: str,
    id: str,
    request: Request,
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
) -> dict[str, Any]:
    """Fetch a single resource row.

    Returns 404 if the row does not exist OR belongs to a different org —
    no cross-org information leaks.
    """
    _require_valid_resource(resource)
    org_id = await resolve_org_id(str(user["id"]), repo, request)
    row = await repo.get(resource, org_id, id)
    if row is None:
        raise AppError("not_found", f"{resource[:-1].capitalize()} not found.", 404)
    return row


@router.put("/{resource}/{id}")
async def update_resource(
    resource: str,
    id: str,
    body: UpdateIn,
    request: Request,
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
) -> dict[str, Any]:
    """Update a resource row's ``name`` and/or ``config``.

    Returns 404 if the row does not exist OR belongs to a different org.
    """
    _require_valid_resource(resource)
    org_id = await resolve_org_id(str(user["id"]), repo, request)

    fields: dict[str, Any] = {}
    if body.name is not None:
        fields["name"] = body.name
    if body.config is not None:
        fields["config"] = body.config

    row = await repo.update(resource, org_id, id, fields)
    if row is None:
        raise AppError("not_found", f"{resource[:-1].capitalize()} not found.", 404)
    return row


@router.delete("/{resource}/{id}", status_code=204)
async def delete_resource(
    resource: str,
    id: str,
    request: Request,
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
) -> Response:
    """Delete a resource row.

    Returns 204 on success, 404 if not found or wrong org.
    """
    _require_valid_resource(resource)
    org_id = await resolve_org_id(str(user["id"]), repo, request)
    deleted = await repo.delete(resource, org_id, id)
    if not deleted:
        raise AppError("not_found", f"{resource[:-1].capitalize()} not found.", 404)
    return Response(status_code=204)


# ── Register on the shared api_router ─────────────────────────────────────────
api_router.include_router(router)
