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

import re
import secrets
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


# ── Members & invites ─────────────────────────────────────────────────────────

# The org role set. `owner` has full control (incl. deleting the org); `admin`
# can manage members + settings; `member` uses the workspace; `viewer` is
# intended read-only (enforcement is a future concern — see routes docstring).
VALID_ROLES = ("owner", "admin", "member", "viewer")
MANAGE_ROLES = ("owner", "admin")
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class MemberOut(BaseModel):
    user_id: str
    name: str | None = None
    email: str
    role: str


class MembersListResponse(BaseModel):
    members: list[MemberOut]


class UpdateMemberRoleIn(BaseModel):
    role: str


class InviteOut(BaseModel):
    id: str
    email: str
    role: str
    status: str
    token: str
    invited_by: str | None = None
    created_at: str
    expires_at: str


class InvitesListResponse(BaseModel):
    invites: list[InviteOut]


class CreateInviteIn(BaseModel):
    email: str
    role: str = "member"


class InvitePreview(BaseModel):
    org_id: str
    org_name: str
    role: str
    email: str
    status: str


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


async def _require_manage(user_id: str, org_id: str) -> dict[str, Any]:
    """Return the caller's membership if they can manage members (owner/admin).

    Raises 404 if not a member (no info leak), 403 if a member but not a manager.
    """
    membership = await _get_user_org_membership(user_id, org_id)
    if membership is None:
        raise AppError("not_found", "Org not found.", 404)
    if membership["role"] not in MANAGE_ROLES:
        raise AppError("forbidden", "Only owners and admins can manage members.", 403)
    return membership


async def _count_owners(org_id: str) -> int:
    """Return the number of owners in an org (for the last-owner guard)."""
    row = await fetchrow(
        "SELECT count(*)::int AS n FROM org_members WHERE org_id = $1::uuid AND role = 'owner'",
        org_id,
    )
    return int(row["n"]) if row else 0


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
    org = await _require_manage(user_id, org_id)  # owner/admin only (also blocks viewer/member)

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

    # NOTE: the `orgs` table has no `updated_at` column (see 0005/0019), so we
    # do not set it here — doing so raised UndefinedColumnError (500) on rename.
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
    org = await _require_manage(user_id, org_id)  # owner/admin only (also blocks viewer/member)

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


# ── Members ───────────────────────────────────────────────────────────────────

@router.get("/{org_id}/members", response_model=MembersListResponse)
async def list_members(
    org_id: str,
    user: dict[str, Any] = Depends(current_user),
) -> MembersListResponse:
    """List the members of an org (any member may view). 404 if not a member."""
    membership = await _get_user_org_membership(str(user["id"]), org_id)
    if membership is None:
        raise AppError("not_found", "Org not found.", 404)
    rows = await fetch(
        """
        SELECT u.id AS user_id, u.name, u.email, om.role
        FROM org_members om
        JOIN users u ON u.id = om.user_id
        WHERE om.org_id = $1::uuid
        ORDER BY
            CASE om.role WHEN 'owner' THEN 0 WHEN 'admin' THEN 1 WHEN 'member' THEN 2 ELSE 3 END,
            lower(u.email)
        """,
        org_id,
    )
    return MembersListResponse(members=[
        MemberOut(user_id=str(r["user_id"]), name=r["name"], email=str(r["email"]), role=r["role"])
        for r in rows
    ])


@router.put("/{org_id}/members/{member_id}", response_model=MemberOut)
async def update_member_role(
    org_id: str,
    member_id: str,
    body: UpdateMemberRoleIn,
    user: dict[str, Any] = Depends(current_user),
) -> MemberOut:
    """Change a member's role (owner/admin only). Owners are required to grant or
    revoke the `owner` role, and the last owner cannot be demoted."""
    manager = await _require_manage(str(user["id"]), org_id)
    if body.role not in VALID_ROLES:
        raise AppError("invalid_request", f"Role must be one of {VALID_ROLES}.", 400)

    target = await fetchrow(
        "SELECT om.role, u.name, u.email FROM org_members om JOIN users u ON u.id = om.user_id "
        "WHERE om.org_id = $1::uuid AND om.user_id = $2::uuid",
        org_id, member_id,
    )
    if target is None:
        raise AppError("not_found", "Member not found.", 404)

    if (body.role == "owner" or target["role"] == "owner") and manager["role"] != "owner":
        raise AppError("forbidden", "Only an owner can grant or revoke the owner role.", 403)
    if target["role"] == "owner" and body.role != "owner" and await _count_owners(org_id) <= 1:
        raise AppError("invalid_request", "Cannot demote the last owner of the org.", 400)

    await execute(
        "UPDATE org_members SET role = $3 WHERE org_id = $1::uuid AND user_id = $2::uuid",
        org_id, member_id, body.role,
    )
    return MemberOut(user_id=member_id, name=target["name"], email=str(target["email"]), role=body.role)


@router.delete("/{org_id}/members/{member_id}", status_code=204)
async def remove_member(
    org_id: str,
    member_id: str,
    user: dict[str, Any] = Depends(current_user),
) -> None:
    """Remove a member (owner/admin only). Only an owner can remove an owner, and
    the last owner cannot be removed."""
    manager = await _require_manage(str(user["id"]), org_id)
    target = await fetchrow(
        "SELECT role FROM org_members WHERE org_id = $1::uuid AND user_id = $2::uuid",
        org_id, member_id,
    )
    if target is None:
        raise AppError("not_found", "Member not found.", 404)
    if target["role"] == "owner" and manager["role"] != "owner":
        raise AppError("forbidden", "Only an owner can remove an owner.", 403)
    if target["role"] == "owner" and await _count_owners(org_id) <= 1:
        raise AppError("invalid_request", "Cannot remove the last owner of the org.", 400)
    await execute(
        "DELETE FROM org_members WHERE org_id = $1::uuid AND user_id = $2::uuid",
        org_id, member_id,
    )


# ── Invites ─────────────────────────────────────────────────────────────────--

def _invite_row_to_out(r: dict[str, Any]) -> InviteOut:
    return InviteOut(
        id=str(r["id"]),
        email=str(r["email"]),
        role=r["role"],
        status=r["status"],
        token=r["token"],
        invited_by=str(r["invited_by"]) if r["invited_by"] else None,
        created_at=r["created_at"].isoformat(),
        expires_at=r["expires_at"].isoformat(),
    )


@router.get("/{org_id}/invites", response_model=InvitesListResponse)
async def list_invites(
    org_id: str,
    user: dict[str, Any] = Depends(current_user),
) -> InvitesListResponse:
    """List pending invites for an org (owner/admin only)."""
    await _require_manage(str(user["id"]), org_id)
    rows = await fetch(
        "SELECT id, email, role, status, token, invited_by, created_at, expires_at "
        "FROM org_invites WHERE org_id = $1::uuid AND status = 'pending' ORDER BY created_at DESC",
        org_id,
    )
    return InvitesListResponse(invites=[_invite_row_to_out(dict(r)) for r in rows])


@router.post("/{org_id}/invites", response_model=InviteOut, status_code=201)
async def create_invite(
    org_id: str,
    body: CreateInviteIn,
    user: dict[str, Any] = Depends(current_user),
) -> InviteOut:
    """Create (or refresh) a pending invite for an email + role (owner/admin only).

    Returns the invite incl. its `token`; the frontend builds the accept link
    (`/invite/<token>`). Email delivery is best-effort and only happens if a
    mail sender is configured (none is wired by default)."""
    manager = await _require_manage(str(user["id"]), org_id)
    email = body.email.strip().lower()
    if not _EMAIL_RE.match(email):
        raise AppError("invalid_request", "A valid email address is required.", 400)
    if body.role not in VALID_ROLES:
        raise AppError("invalid_request", f"Role must be one of {VALID_ROLES}.", 400)
    if body.role == "owner" and manager["role"] != "owner":
        raise AppError("forbidden", "Only an owner can invite another owner.", 403)

    # Already a member of this org?
    existing_member = await fetchrow(
        "SELECT 1 FROM org_members om JOIN users u ON u.id = om.user_id "
        "WHERE om.org_id = $1::uuid AND lower(u.email) = $2",
        org_id, email,
    )
    if existing_member is not None:
        raise AppError("already_member", "That person is already a member of this org.", 409)

    token = secrets.token_urlsafe(32)
    # Re-invite refreshes the existing pending row (the partial unique index
    # allows only one pending invite per org+email).
    existing = await fetchrow(
        "SELECT id FROM org_invites WHERE org_id = $1::uuid AND lower(email) = $2 AND status = 'pending'",
        org_id, email,
    )
    if existing is not None:
        row = await fetchrow(
            "UPDATE org_invites SET role = $2, token = $3, invited_by = $4::uuid, "
            "created_at = now(), expires_at = now() + INTERVAL '14 days' "
            "WHERE id = $1 RETURNING id, email, role, status, token, invited_by, created_at, expires_at",
            existing["id"], body.role, token, str(user["id"]),
        )
    else:
        row = await fetchrow(
            "INSERT INTO org_invites (org_id, email, role, token, invited_by) "
            "VALUES ($1::uuid, $2, $3, $4, $5::uuid) "
            "RETURNING id, email, role, status, token, invited_by, created_at, expires_at",
            org_id, email, body.role, token, str(user["id"]),
        )
    return _invite_row_to_out(dict(row))


@router.delete("/{org_id}/invites/{invite_id}", status_code=204)
async def revoke_invite(
    org_id: str,
    invite_id: str,
    user: dict[str, Any] = Depends(current_user),
) -> None:
    """Revoke a pending invite (owner/admin only)."""
    await _require_manage(str(user["id"]), org_id)
    await execute(
        "UPDATE org_invites SET status = 'revoked' WHERE id = $1::uuid AND org_id = $2::uuid AND status = 'pending'",
        invite_id, org_id,
    )


@router.get("/invites/{token}", response_model=InvitePreview)
async def preview_invite(
    token: str,
    user: dict[str, Any] = Depends(current_user),
) -> InvitePreview:
    """Preview an invite by its token (any authenticated user). Used by the
    accept page to show which org + role the invite grants."""
    row = await fetchrow(
        "SELECT i.org_id, i.role, i.email, i.status, i.expires_at, o.name AS org_name "
        "FROM org_invites i JOIN orgs o ON o.id = i.org_id WHERE i.token = $1",
        token,
    )
    if row is None:
        raise AppError("not_found", "Invite not found.", 404)
    return InvitePreview(
        org_id=str(row["org_id"]),
        org_name=row["org_name"],
        role=row["role"],
        email=str(row["email"]),
        status=row["status"],
    )


@router.post("/invites/{token}/accept", response_model=OrgDetailResponse)
async def accept_invite(
    token: str,
    user: dict[str, Any] = Depends(current_user),
) -> OrgDetailResponse:
    """Accept an invite: join the org with the invited role. Idempotent — if the
    caller is already a member, their role is updated to the invited role."""
    user_id = str(user["id"])
    row = await fetchrow(
        "SELECT id, org_id, role, status, expires_at FROM org_invites WHERE token = $1",
        token,
    )
    if row is None:
        raise AppError("not_found", "Invite not found.", 404)
    if row["status"] != "pending":
        raise AppError("invite_unavailable", f"This invite is {row['status']}.", 409)

    org_id = str(row["org_id"])
    await execute(
        "INSERT INTO org_members (org_id, user_id, role) VALUES ($1::uuid, $2::uuid, $3) "
        "ON CONFLICT (org_id, user_id) DO UPDATE SET role = EXCLUDED.role",
        org_id, user_id, row["role"],
    )
    await execute(
        "UPDATE org_invites SET status = 'accepted', accepted_at = now() WHERE id = $1",
        row["id"],
    )
    org_name = await _get_org_name(org_id) or "Organization"
    return OrgDetailResponse(id=org_id, name=org_name, role=row["role"])


# ── Register on the shared api_router ─────────────────────────────────────────
# Runs at import time (api_router imported at the top of this module).
api_router.include_router(router)
