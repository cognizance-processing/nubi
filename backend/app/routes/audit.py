"""Audit-log read API.

Endpoints
---------
GET /audit                          — paginated list, org-scoped, newest-first.
GET /audit/{resource_type}/{resource_id}  — one resource's audit history.

Auth gating
-----------
Both endpoints require a valid first-party bearer token (``current_user``).
The org is resolved via ``get_user_org`` (default-first-org, consistent with
how flows/secrets/connectors resolve the org).  Caller must have an *approver*
role (owner or admin) — read access to the audit log is a governance-level
privilege.  Unauthenticated callers get 401; non-approvers get 403.

Cross-org safety
----------------
``org_id`` is ALWAYS taken from the verified identity (org membership), never
from the request body or URL.  A user who is not a member of an org can never
see that org's audit rows.

POPIA note
----------
The audit log stores METADATA ONLY.  Responses from these endpoints therefore
do NOT include row data, SQL literals, or PII — they expose action/resource/
actor metadata recorded at write time (see app/audit.py).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query

from app.auth.deps import current_user
from app.auth.roles import get_org_role, _require_approver_role
from app.db import fetch, fetchrow
from app.errors import AppError
from app.repos.provider import get_repo, Repo
from app.routes import api_router
from app.routes._org import get_user_org as _get_user_org

router = APIRouter(prefix="/audit", tags=["audit"])


# ---------------------------------------------------------------------------
# Auth guard: org-scoped approver (owner/admin only)
# ---------------------------------------------------------------------------

async def _require_audit_access(
    user: dict[str, Any],
    repo: Repo,
) -> str:
    """Resolve org_id and assert the caller is an owner/admin.

    Returns
    -------
    str
        The verified org_id.

    Raises
    ------
    AppError("org_not_found", 404)
        If the user has no org membership.
    AppError("forbidden", 403)
        If the caller is not an owner or admin.
    """
    user_id = str(user["id"])
    org_id = await _get_user_org(user_id, repo)
    role = await get_org_role(user_id, org_id, repo)
    _require_approver_role(role)
    return org_id


# ---------------------------------------------------------------------------
# Serialisation helper
# ---------------------------------------------------------------------------

def _iso(value: Any) -> Any:
    """ISO-8601-serialize datetimes; pass everything else through."""
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def _row_to_dict(row: Any) -> dict[str, Any]:
    """Convert an asyncpg Record (or dict-like) to a JSON-safe plain dict."""
    r = dict(row)
    for k, v in r.items():
        if hasattr(v, "isoformat"):
            r[k] = v.isoformat()
    # Ensure summary is always a dict (asyncpg decodes jsonb to dict automatically)
    if "summary" in r and not isinstance(r["summary"], dict):
        r["summary"] = {}
    return r


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("")
async def list_audit(
    resource_type: str | None = Query(default=None),
    action: str | None = Query(default=None),
    actor: str | None = Query(default=None, description="Filter by actor_user_id"),
    since: str | None = Query(default=None, description="ISO-8601 lower bound (inclusive)"),
    until: str | None = Query(default=None, description="ISO-8601 upper bound (inclusive)"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
) -> dict[str, Any]:
    """List audit-log entries for the caller's org, newest-first.

    All filters are additive (AND).  Returns::

        {
          "items": [{ id, org_id, actor_user_id, actor_kind, action,
                      resource_type, resource_id, summary, at }],
          "total": <int>,
          "limit": <int>,
          "offset": <int>
        }

    Auth
    ----
    - Unauthenticated: 401
    - Non-approver (member/viewer): 403
    """
    org_id = await _require_audit_access(user, repo)

    # Build dynamic WHERE clauses.
    conditions: list[str] = ["org_id = $1"]
    params: list[Any] = [org_id]
    idx = 2

    if resource_type:
        conditions.append(f"resource_type = ${idx}")
        params.append(resource_type)
        idx += 1
    if action:
        conditions.append(f"action = ${idx}")
        params.append(action)
        idx += 1
    if actor:
        conditions.append(f"actor_user_id = ${idx}")
        params.append(actor)
        idx += 1
    if since:
        try:
            since_dt = datetime.fromisoformat(since.replace("Z", "+00:00"))
        except ValueError:
            raise AppError("invalid_param", "since must be a valid ISO-8601 datetime.", 400)
        conditions.append(f"at >= ${idx}")
        params.append(since_dt)
        idx += 1
    if until:
        try:
            until_dt = datetime.fromisoformat(until.replace("Z", "+00:00"))
        except ValueError:
            raise AppError("invalid_param", "until must be a valid ISO-8601 datetime.", 400)
        conditions.append(f"at <= ${idx}")
        params.append(until_dt)
        idx += 1

    where = " AND ".join(conditions)

    count_row = await fetchrow(
        f"SELECT count(*)::int AS total FROM audit_log WHERE {where}",
        *params,
    )
    total = int(dict(count_row or {}).get("total") or 0)

    rows = await fetch(
        f"""
        SELECT id, org_id, actor_user_id, actor_kind, action,
               resource_type, resource_id, summary, at
        FROM audit_log
        WHERE {where}
        ORDER BY at DESC
        LIMIT ${idx} OFFSET ${idx + 1}
        """,
        *params,
        limit,
        offset,
    )

    return {
        "items": [_row_to_dict(r) for r in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.get("/{resource_type}/{resource_id}")
async def get_resource_audit(
    resource_type: str,
    resource_id: str,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
) -> dict[str, Any]:
    """Return the audit history for a single resource, newest-first.

    Returns the same shape as ``GET /audit`` but pre-filtered to a specific
    ``(resource_type, resource_id)`` pair.

    Auth
    ----
    - Unauthenticated: 401
    - Non-approver (member/viewer): 403
    - Cross-org: impossible — org_id is always taken from verified identity
    """
    org_id = await _require_audit_access(user, repo)

    count_row = await fetchrow(
        """
        SELECT count(*)::int AS total FROM audit_log
        WHERE org_id = $1 AND resource_type = $2 AND resource_id = $3
        """,
        org_id,
        resource_type,
        resource_id,
    )
    total = int(dict(count_row or {}).get("total") or 0)

    rows = await fetch(
        """
        SELECT id, org_id, actor_user_id, actor_kind, action,
               resource_type, resource_id, summary, at
        FROM audit_log
        WHERE org_id = $1 AND resource_type = $2 AND resource_id = $3
        ORDER BY at DESC
        LIMIT $4 OFFSET $5
        """,
        org_id,
        resource_type,
        resource_id,
        limit,
        offset,
    )

    return {
        "items": [_row_to_dict(r) for r in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


# ── Attach to the shared api_router ──────────────────────────────────────────
api_router.include_router(router)
