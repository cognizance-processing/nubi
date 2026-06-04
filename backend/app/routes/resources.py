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
dependency).  The caller's ``org_id`` is resolved via the first
``org_members`` row for that user.

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

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel

from app.auth.deps import current_user
from app.db import fetchrow
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


# ── Org resolution helper ─────────────────────────────────────────────────────

async def get_user_org(user_id: str, repo: Repo) -> str:
    """Return the org_id for the user's first membership.

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


# ── Validation helper ─────────────────────────────────────────────────────────

def _require_valid_resource(resource: str) -> None:
    """Raise AppError 404 if *resource* is not in the allowlist."""
    if resource not in VALID_RESOURCES:
        raise AppError("not_found", f"Unknown resource: {resource!r}.", 404)


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/{resource}")
async def list_resources(
    resource: str,
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
) -> list[dict[str, Any]]:
    """List all rows for the caller's org.

    Returns
    -------
    list[dict]
        Possibly empty list of resource rows.
    """
    _require_valid_resource(resource)
    org_id = await get_user_org(str(user["id"]), repo)
    return await repo.list(resource, org_id)


@router.post("/{resource}", status_code=201)
async def create_resource(
    resource: str,
    body: CreateIn,
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
) -> dict[str, Any]:
    """Create a new resource row.

    Returns
    -------
    dict
        The newly created row (includes ``id``, ``created_at``, etc.).
    """
    _require_valid_resource(resource)
    org_id = await get_user_org(str(user["id"]), repo)
    return await repo.create(
        resource=resource,
        org_id=org_id,
        created_by=str(user["id"]),
        name=body.name,
        config=body.config,
    )


@router.get("/{resource}/{id}")
async def get_resource(
    resource: str,
    id: str,
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
) -> dict[str, Any]:
    """Fetch a single resource row.

    Returns 404 if the row does not exist OR belongs to a different org —
    no cross-org information leaks.
    """
    _require_valid_resource(resource)
    org_id = await get_user_org(str(user["id"]), repo)
    row = await repo.get(resource, org_id, id)
    if row is None:
        raise AppError("not_found", f"{resource[:-1].capitalize()} not found.", 404)
    return row


@router.put("/{resource}/{id}")
async def update_resource(
    resource: str,
    id: str,
    body: UpdateIn,
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
) -> dict[str, Any]:
    """Update a resource row's ``name`` and/or ``config``.

    Returns 404 if the row does not exist OR belongs to a different org.
    """
    _require_valid_resource(resource)
    org_id = await get_user_org(str(user["id"]), repo)

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
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
) -> Response:
    """Delete a resource row.

    Returns 204 on success, 404 if not found or wrong org.
    """
    _require_valid_resource(resource)
    org_id = await get_user_org(str(user["id"]), repo)
    deleted = await repo.delete(resource, org_id, id)
    if not deleted:
        raise AppError("not_found", f"{resource[:-1].capitalize()} not found.", 404)
    return Response(status_code=204)


# ── Register on the shared api_router ─────────────────────────────────────────
api_router.include_router(router)
