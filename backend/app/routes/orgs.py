"""Org management endpoints — all endpoints under /orgs.

Endpoints
---------
GET    /orgs              — list the orgs the current user belongs to.
GET    /orgs/{id}         — fetch a single org the user is a member of (404 otherwise).
POST   /orgs              — create a new org with the current user as owner.
PATCH  /orgs/{id}         — rename an org and/or set its avatar_url.
GET    /orgs/{id}/deletion-impact — describe what would be deleted (can_delete, blockers, deletes).
DELETE /orgs/{id}         — delete an org (409 if projects exist; requires confirm_name).

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


class ImpactBlocker(BaseModel):
    """A blocker that prevents deletion."""

    type: str
    count: int
    reason: str


class ImpactDelete(BaseModel):
    """A resource category that would be deleted."""

    type: str
    count: int


class DeletionImpactResponse(BaseModel):
    """Response body for GET /orgs/{id}/deletion-impact."""

    can_delete: bool
    blockers: list[ImpactBlocker]
    deletes: list[ImpactDelete]
    name: str


# ── Request schemas ───────────────────────────────────────────────────────────

class CreateOrgIn(BaseModel):
    """Request body for POST /orgs."""

    name: str


class PatchOrgIn(BaseModel):
    """Request body for PATCH /orgs/{id}."""

    name: str | None = None
    avatar_url: str | None = None


class DeleteOrgIn(BaseModel):
    """Request body for DELETE /orgs/{id}."""

    confirm_name: str


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


async def _get_org_name(org_id: str) -> str | None:
    """Return the raw org name, or None if not found.

    Parameters
    ----------
    org_id:
        UUID string of the org.

    Returns
    -------
    str or None
        The org name, or ``None`` if the org doesn't exist.
    """
    row = await fetchrow(
        "SELECT name FROM orgs WHERE id = $1::uuid",
        org_id,
    )
    if row is None:
        return None
    return str(row["name"])


async def _count_org_resource(table: str, org_id: str) -> int:
    """Return the row count for *table* scoped to *org_id*.

    Only tables in the known allowlist are queried; unknown tables return 0.

    Parameters
    ----------
    table:
        One of the allowed table names (datastores, boards, queries, widgets,
        flows, secrets).
    org_id:
        UUID string of the org.

    Returns
    -------
    int
        Row count; 0 on unknown table or DB error.
    """
    _ALLOWED_TABLES = frozenset(
        {"datastores", "boards", "queries", "widgets", "flows", "secrets"}
    )
    if table not in _ALLOWED_TABLES:
        return 0
    row = await fetchrow(
        f"SELECT count(*)::int AS n FROM {table} WHERE org_id = $1::uuid",  # noqa: S608
        org_id,
    )
    if row is None:
        return 0
    return int(row["n"])


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


@router.get("/{org_id}/deletion-impact", response_model=DeletionImpactResponse)
async def get_org_deletion_impact(
    org_id: str,
    user: dict[str, Any] = Depends(current_user),
) -> DeletionImpactResponse:
    """Return a summary of what deleting this org would affect.

    Sets ``can_delete=false`` with a ``projects`` blocker when the org has any
    projects (an org with projects cannot be deleted — all projects must be
    removed first).

    Parameters
    ----------
    org_id:
        UUID of the org to inspect.

    Returns
    -------
    200 {can_delete, blockers, deletes, name}

    Raises
    ------
    AppError("not_found", 404)
        If the org doesn't exist or the user isn't a member.
    """
    user_id = str(user["id"])
    org = await _get_user_org_membership(user_id, org_id)
    if org is None:
        raise AppError("not_found", "Org not found.", 404)

    org_name = org["name"]
    blockers: list[ImpactBlocker] = []
    deletes: list[ImpactDelete] = []

    # Projects are a hard blocker — an org with projects cannot be deleted.
    project_count = await projects_repo.count_projects(org_id)
    if project_count > 0:
        blockers.append(
            ImpactBlocker(
                type="projects",
                count=project_count,
                reason=(
                    f"This org has {project_count} project(s). "
                    "Delete all projects before deleting the org."
                ),
            )
        )

    # Count resources that WOULD be deleted (regardless of blocker).
    for resource_type, table in [
        ("datastores", "datastores"),
        ("boards", "boards"),
        ("queries", "queries"),
        ("widgets", "widgets"),
        ("flows", "flows"),
        ("secrets", "secrets"),
    ]:
        count = await _count_org_resource(table, org_id)
        if count > 0:
            deletes.append(ImpactDelete(type=resource_type, count=count))

    can_delete = len(blockers) == 0
    return DeletionImpactResponse(
        can_delete=can_delete,
        blockers=blockers,
        deletes=deletes,
        name=org_name,
    )


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
    project = await projects_repo.create_project(
        org_id=org_id,
        name="Default",
        created_by=user_id,
    )

    # Seed the removable onboarding sample bundle into the new default project.
    from app.routes.auth import seed_sample_bundle_for_org  # noqa: PLC0415

    await seed_sample_bundle_for_org(
        org_id=org_id,
        project_id=(project or {}).get("id"),
        created_by=user_id,
    )

    return OrgDetailResponse(id=org_id, name=body.name, role="owner")


@router.patch("/{org_id}", response_model=OrgDetailResponse)
async def patch_org(
    org_id: str,
    body: PatchOrgIn,
    user: dict[str, Any] = Depends(current_user),
) -> OrgDetailResponse:
    """Rename an org and/or set its avatar_url.

    At least one of ``name`` or ``avatar_url`` must be provided. The caller
    must be a member of the org.

    Parameters
    ----------
    org_id:
        UUID of the org to update.
    body.name:
        New human-readable name for the org (optional).
    body.avatar_url:
        New avatar URL for the org (optional).

    Returns
    -------
    200 {id, name, role}

    Raises
    ------
    AppError("not_found", 404)
        If the org doesn't exist or the user isn't a member.
    AppError("invalid_request", 400)
        If neither ``name`` nor ``avatar_url`` is provided.
    """
    user_id = str(user["id"])
    org = await _get_user_org_membership(user_id, org_id)
    if org is None:
        raise AppError("not_found", "Org not found.", 404)

    if body.name is None and body.avatar_url is None:
        raise AppError("invalid_request", "At least one of name or avatar_url must be provided.", 400)

    updates: list[str] = []
    values: list[Any] = []
    idx = 1

    if body.name is not None:
        name = body.name.strip()
        if not name:
            raise AppError("invalid_request", "Org name cannot be empty.", 400)
        updates.append(f"name = ${idx}")
        values.append(name)
        idx += 1

    if body.avatar_url is not None:
        updates.append(f"avatar_url = ${idx}")
        values.append(body.avatar_url)
        idx += 1

    updates.append("updated_at = now()")
    set_clause = ", ".join(updates)
    values.append(org_id)

    await execute(
        f"UPDATE orgs SET {set_clause} WHERE id = ${idx}::uuid",  # noqa: S608
        *values,
    )

    # Return the updated org detail; re-resolve name from DB in case it changed.
    updated_name = body.name.strip() if body.name is not None else org["name"]
    return OrgDetailResponse(id=org_id, name=updated_name, role=org["role"])


@router.delete("/{org_id}", status_code=204)
async def delete_org(
    org_id: str,
    body: DeleteOrgIn,
    user: dict[str, Any] = Depends(current_user),
) -> None:
    """Delete an org.

    Rules
    -----
    1.  The caller must be a member of the org.
    2.  The org must have **no projects** — if any projects exist, the endpoint
        returns 409 with a clear message listing the project count.
    3.  The caller must supply ``confirm_name`` matching the org's exact name
        (case-sensitive). Mismatch → 422.
    4.  On success, org-scoped data is cascade-deleted by the DB (foreign key
        ``ON DELETE CASCADE`` on all resource tables).

    Parameters
    ----------
    org_id:
        UUID of the org to delete.
    body.confirm_name:
        Must exactly equal the org's current name.

    Returns
    -------
    204 No Content

    Raises
    ------
    AppError("not_found", 404)
        If the org doesn't exist or the user isn't a member.
    AppError("org_has_projects", 409)
        If the org still has projects.
    AppError("confirm_name_mismatch", 422)
        If ``confirm_name`` does not match the org's name exactly.
    """
    user_id = str(user["id"])
    org = await _get_user_org_membership(user_id, org_id)
    if org is None:
        raise AppError("not_found", "Org not found.", 404)

    # Rule 2: reject if any projects exist.
    project_count = await projects_repo.count_projects(org_id)
    if project_count > 0:
        raise AppError(
            "org_has_projects",
            (
                f"Cannot delete org '{org['name']}': it still has {project_count} project(s). "
                "Delete all projects first."
            ),
            409,
        )

    # Rule 3: confirm_name must match.
    if body.confirm_name != org["name"]:
        raise AppError(
            "confirm_name_mismatch",
            (
                f"The confirmation name '{body.confirm_name}' does not match "
                f"the org name '{org['name']}'. Please type the exact org name to confirm deletion."
            ),
            422,
        )

    # Cascade-delete: the DB cascades to org_members + resource tables via FK.
    await execute(
        "DELETE FROM orgs WHERE id = $1::uuid",
        org_id,
    )


# ── Register on the shared api_router ─────────────────────────────────────────
# Runs at import time (api_router imported at the top of this module).
api_router.include_router(router)
