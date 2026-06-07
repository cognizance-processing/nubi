"""Org management endpoints — all endpoints under /orgs.

Endpoints
---------
GET  /orgs        — list the orgs the current user belongs to.
GET  /orgs/{id}   — fetch a single org the user is a member of (404 otherwise).
POST /orgs        — create a new org with the current user as owner.

Authentication
--------------
Every endpoint requires a valid first-party Bearer token (``current_user``
dependency).  A user can only see/access orgs they are a member of.

Response shape for GET /orgs
-----------------------------
::

    {
        "orgs": [
            {"id": "<uuid>", "name": "<str>", "role": "<str>"},
            ...
        ]
    }

The list always contains at least the user's personal org (created at
registration via ``_create_personal_org``).

This module attaches itself to the shared ``api_router`` at import time so
that ``main.py``'s ``include_router(api_router, prefix="/api/v1")`` picks it
up automatically.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.auth.deps import current_user
from app.db import execute, fetch, fetchrow
from app.errors import AppError
from app.repos import projects as projects_repo
from app.routes import api_router

# ── Sub-router ────────────────────────────────────────────────────────────────
router = APIRouter(prefix="/orgs", tags=["orgs"])


# ── Pydantic response schemas ─────────────────────────────────────────────────

class OrgOut(BaseModel):
    """A single org membership entry."""

    id: str
    name: str
    role: str


class OrgsListResponse(BaseModel):
    """Response body for GET /orgs."""

    orgs: list[OrgOut]


class OrgDetailResponse(BaseModel):
    """Response body for GET /orgs/{id}."""

    id: str
    name: str
    role: str


# ── Request schemas ───────────────────────────────────────────────────────────

class CreateOrgIn(BaseModel):
    """Request body for POST /orgs."""

    name: str


# ── DB helpers ────────────────────────────────────────────────────────────────

async def _list_user_orgs(user_id: str) -> list[dict[str, Any]]:
    """Return all orgs the user is a member of, with their role.

    Joins ``org_members`` and ``orgs`` for the given *user_id*.

    Parameters
    ----------
    user_id:
        UUID string of the authenticated user.

    Returns
    -------
    list[dict]
        Each entry has ``id``, ``name``, and ``role``.
    """
    rows = await fetch(
        """
        SELECT o.id, o.name, om.role
        FROM org_members om
        JOIN orgs o ON o.id = om.org_id
        WHERE om.user_id = $1::uuid
        ORDER BY o.name
        """,
        user_id,
    )
    return [{"id": str(r["id"]), "name": r["name"], "role": r["role"]} for r in rows]


async def _get_user_org_membership(user_id: str, org_id: str) -> dict[str, Any] | None:
    """Return the org + role if the user is a member, else None.

    Parameters
    ----------
    user_id:
        UUID string of the authenticated user.
    org_id:
        UUID string of the org to look up.

    Returns
    -------
    dict or None
        ``{id, name, role}`` if the user is a member; ``None`` otherwise.
    """
    row = await fetchrow(
        """
        SELECT o.id, o.name, om.role
        FROM org_members om
        JOIN orgs o ON o.id = om.org_id
        WHERE om.user_id = $1::uuid
          AND om.org_id  = $2::uuid
        """,
        user_id,
        org_id,
    )
    if row is None:
        return None
    return {"id": str(row["id"]), "name": row["name"], "role": row["role"]}


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("", response_model=OrgsListResponse)
async def list_orgs(
    user: dict[str, Any] = Depends(current_user),
) -> OrgsListResponse:
    """List all orgs the current user belongs to.

    Always returns at least the user's personal org (created at registration).

    Returns
    -------
    200 {orgs: [{id, name, role}, ...]}
    """
    user_id = str(user["id"])
    orgs = await _list_user_orgs(user_id)
    return OrgsListResponse(orgs=[OrgOut(**o) for o in orgs])


@router.get("/{org_id}", response_model=OrgDetailResponse)
async def get_org(
    org_id: str,
    user: dict[str, Any] = Depends(current_user),
) -> OrgDetailResponse:
    """Fetch a single org the current user is a member of.

    Returns 404 if the org does not exist OR the user is not a member —
    no cross-org information leaks.

    Parameters
    ----------
    org_id:
        UUID of the org to fetch.

    Returns
    -------
    200 {id, name, role}

    Raises
    ------
    AppError("not_found", 404)
        If the org doesn't exist or the user isn't a member.
    """
    user_id = str(user["id"])
    org = await _get_user_org_membership(user_id, org_id)
    if org is None:
        raise AppError("not_found", "Org not found.", 404)
    return OrgDetailResponse(**org)


@router.post("", response_model=OrgDetailResponse, status_code=201)
async def create_org(
    body: CreateOrgIn,
    user: dict[str, Any] = Depends(current_user),
) -> OrgDetailResponse:
    """Create a new org with the current user as owner.

    Parameters
    ----------
    body.name:
        Human-readable name for the new org.

    Returns
    -------
    201 {id, name, role}
    """
    user_id = str(user["id"])
    org_id = str(uuid.uuid4())

    # Derive a URL-safe slug from the org name + first 8 chars of the new org_id.
    safe_slug = "".join(c if c.isalnum() or c == "-" else "-" for c in body.name.lower())
    slug = f"{safe_slug}-{org_id[:8]}"

    await execute(
        """
        INSERT INTO orgs (id, name, slug)
        VALUES ($1, $2, $3)
        """,
        org_id,
        body.name,
        slug,
    )
    await execute(
        """
        INSERT INTO org_members (org_id, user_id, role)
        VALUES ($1, $2, 'owner')
        """,
        org_id,
        user_id,
    )

    # Every org gets a frictionless default project from the moment it exists.
    await projects_repo.create_project(
        org_id=org_id,
        name="Default",
        created_by=user_id,
    )

    return OrgDetailResponse(id=org_id, name=body.name, role="owner")


# ── Register on the shared api_router ─────────────────────────────────────────
# Runs at import time; main.py imports api_router after this module is loaded.
from app.routes import api_router  # noqa: E402 (re-import fine — same object)
api_router.include_router(router)
