"""Access-grants CRUD — ``/access-grants`` (org-scoped, admin-gated writes).

Lets org owners/admins manage explicit ``(subject, dimension, value)`` grants in
the ``access_grants`` table (migration 0022).  ``GET /auth/scope`` merges a
subject's non-expired grants into their effective RLS policies, so this is the
"user → scope assignment store" companion to host-minted token claims.

Endpoints
---------
GET    /access-grants?subject_type=&subject_id=   List grants for a subject.
POST   /access-grants                             Create a grant (admin/owner).
DELETE /access-grants/{id}                         Delete a grant (admin/owner).

Security
--------
* Every operation is scoped to the caller's org (resolved server-side via
  ``get_user_org`` — NEVER from the request body).
* Writes (POST/DELETE) are gated to approver roles (owner/admin) via
  ``require_approver_default``, mirroring routes/admin.py's gating model.
* A grant id that belongs to another org (or does not exist) returns 404 (not
  403) so a grant's existence never leaks across tenants.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, field_validator

from app.access.grants_store import VALID_SUBJECT_TYPES, get_grants_store
from app.auth.deps import current_user
from app.auth.roles import require_approver_default
from app.errors import AppError
from app.repos.provider import Repo, get_repo
from app.routes import api_router
from app.routes._org import get_user_org

router = APIRouter(prefix="/access-grants", tags=["access-grants"])


class GrantCreateIn(BaseModel):
    """Request body for POST /access-grants.

    NOTE: this body carries the GRANT TARGET (which subject gets which value) —
    it does NOT and cannot set the CALLER's own scope.  The org is always taken
    from the authenticated caller, never from this body.
    """

    subject_type: str
    subject_id: str
    dimension: str
    value: str
    expires_at: datetime | None = None

    @field_validator("subject_type")
    @classmethod
    def _valid_subject_type(cls, v: str) -> str:
        if v not in VALID_SUBJECT_TYPES:
            raise ValueError(
                f"subject_type must be one of {sorted(VALID_SUBJECT_TYPES)}."
            )
        return v

    @field_validator("subject_id", "dimension", "value")
    @classmethod
    def _non_empty(cls, v: str) -> str:
        if not str(v).strip():
            raise ValueError("must be a non-empty string.")
        return str(v).strip()


@router.get("")
async def list_grants(
    subject_type: str = Query(..., description="user | role | embed_sub"),
    subject_id: str = Query(..., description="subject identifier"),
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
) -> dict[str, Any]:
    """List grants for ``(subject_type, subject_id)`` in the caller's org.

    Org-scoped: only grants in the caller's org are returned.  Readable by any
    org member (read is not gated to approvers).

    Returns
    -------
    200 {grants: [{id, subject_type, subject_id, dimension, value, expires_at, ...}]}
    """
    if subject_type not in VALID_SUBJECT_TYPES:
        raise AppError(
            "invalid_subject_type",
            f"subject_type must be one of {sorted(VALID_SUBJECT_TYPES)}.",
            400,
        )
    org_id = await get_user_org(str(user["id"]), repo)
    grants = await get_grants_store().list_for_subject(
        org_id, subject_type, subject_id
    )
    return {"grants": grants}


@router.post("", status_code=201, dependencies=[Depends(require_approver_default)])
async def create_grant(
    body: GrantCreateIn,
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
) -> dict[str, Any]:
    """Create (or refresh) a grant in the caller's org. Admin/owner only.

    Returns
    -------
    201 {grant: {...}}
    """
    org_id = await get_user_org(str(user["id"]), repo)

    expires_at = body.expires_at
    if expires_at is not None and expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)

    grant = await get_grants_store().create(
        org_id=org_id,
        subject_type=body.subject_type,
        subject_id=body.subject_id,
        dimension=body.dimension,
        value=body.value,
        expires_at=expires_at,
        created_by=str(user["id"]),
    )
    return {"grant": grant}


@router.delete(
    "/{grant_id}",
    status_code=204,
    dependencies=[Depends(require_approver_default)],
)
async def delete_grant(
    grant_id: str,
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
) -> None:
    """Delete a grant by id WITHIN the caller's org. Admin/owner only.

    A grant belonging to another org (or absent) returns 404 — never 403 — so
    cross-tenant existence is not enumerable.
    """
    org_id = await get_user_org(str(user["id"]), repo)
    deleted = await get_grants_store().delete(grant_id, org_id)
    if not deleted:
        raise AppError("not_found", "Grant not found.", 404)


# Attach to the shared api_router at import time (main.py imports for side effect).
api_router.include_router(router)
