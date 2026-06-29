"""Role-based write guards — block `viewer` from mutating, org-scoped routes.

Org roles are owner / admin / member / viewer. A `viewer` has read-only access:
they may GET dashboards, queries, flows, data, etc., but must not create, edit,
delete, or run anything. This module provides FastAPI dependencies that resolve
the caller's effective org + role and raise 403 for viewers — added to mutating
routes via the decorator's ``dependencies=[...]`` so handler bodies are untouched.

Two variants mirror the two org-resolution patterns in the codebase (see
``app/routes/_org.py``):

- ``require_writer``         — header-aware (``X-Org-Id``), like ``resolve_org_id``.
                               Use on resources / projects / datasets / preagg /
                               export-share / portability / git routes.
- ``require_writer_default`` — default first-org, like ``get_user_org``.
                               Use on flows / secrets / connectors / bridges /
                               jobs routes (which ignore ``X-Org-Id``).

Both resolve the org the SAME way the route does, so the role checked is always
the role for the org the route actually operates on (never a different org).
"""

from __future__ import annotations

from typing import Any

from fastapi import Depends, Request

from app.auth.deps import current_user, verified_identity
from app.auth.verify import VerifiedIdentity
from app.db import fetchrow
from app.errors import AppError
from app.repos.provider import Repo, get_repo
from app.routes._org import get_user_org, resolve_org_id


async def get_org_role(user_id: str, org_id: str, repo: Repo) -> str | None:
    """Return the caller's role in *org_id*, or None if not a member.

    Mirrors the membership-resolution split used elsewhere: read from the
    InMemoryRepo's seeded members for tests, else query ``org_members``.
    """
    if hasattr(repo, "_org_members"):  # InMemoryRepo test double
        entry = repo._org_members.get(f"{org_id}:{user_id}")  # type: ignore[attr-defined]
        return entry["role"] if entry else None
    row = await fetchrow(
        "SELECT role FROM org_members WHERE user_id = $1::uuid AND org_id = $2::uuid",
        user_id,
        org_id,
    )
    return row["role"] if row else None


def _forbid_viewer(role: str | None) -> None:
    if role == "viewer":
        raise AppError(
            "forbidden",
            "Viewers have read-only access and cannot perform this action.",
            403,
        )


def _require_write_scope(scopes: list[str]) -> None:
    """Raise 403 if the token's scope is read-only.

    The DB org role is necessary but NOT sufficient: the JWT ``scope`` claim is
    an independent capability restriction. A token deliberately minted read-only
    (e.g. ``scope="read:*"`` for a read-only integration/API key) must NOT be able
    to mutate even when the underlying user's org role is owner/admin. A token is
    write-capable iff it carries the super-admin sentinel, ``admin``, or any
    ``edit:`` / ``write:`` / ``author:`` scope (first-party login sessions carry
    ``edit:*`` + ``author:*`` and pass; read-only tokens are blocked).
    """
    if "*" in scopes:
        return
    for s in scopes:
        if s == "admin" or s.startswith(("edit:", "write:", "author:")):
            return
    raise AppError(
        "insufficient_scope",
        "This token is read-only (no write-capable scope) and cannot perform "
        "this action.",
        403,
    )


async def require_writer(
    request: Request,
    user: dict[str, Any] = Depends(current_user),
    identity: VerifiedIdentity = Depends(verified_identity),
    repo: Repo = Depends(get_repo),
) -> None:
    """Header-aware write guard (mirrors ``resolve_org_id``). 403 for viewers or read-only-scoped tokens."""
    _require_write_scope(identity.scope)
    user_id = str(user["id"])
    org_id = await resolve_org_id(user_id, repo, request)
    _forbid_viewer(await get_org_role(user_id, org_id, repo))


async def require_writer_default(
    user: dict[str, Any] = Depends(current_user),
    identity: VerifiedIdentity = Depends(verified_identity),
    repo: Repo = Depends(get_repo),
) -> None:
    """Default-first-org write guard (mirrors ``get_user_org``). 403 for viewers or read-only-scoped tokens."""
    _require_write_scope(identity.scope)
    user_id = str(user["id"])
    org_id = await get_user_org(user_id, repo)
    _forbid_viewer(await get_org_role(user_id, org_id, repo))


def _require_approver_role(role: str | None) -> None:
    """Raise 403 if *role* is not an approver role (owner/admin)."""
    if role not in {"owner", "admin"}:
        raise AppError(
            "forbidden",
            "Only members with approver role (owner/admin) may perform this action.",
            403,
        )


async def require_approver_default(
    user: dict[str, Any] = Depends(current_user),
    identity: VerifiedIdentity = Depends(verified_identity),
    repo: Repo = Depends(get_repo),
) -> None:
    """Default-first-org approver guard (mirrors ``get_user_org``). 403 for non-approvers or read-only-scoped tokens.

    Approver roles are owner and admin only.  Members, viewers, and unauthenticated
    callers are rejected with 403.  Use on routes that require elevated approval
    permission (e.g. POST /flows/writeback/{id}/approval).
    """
    _require_write_scope(identity.scope)
    user_id = str(user["id"])
    org_id = await get_user_org(user_id, repo)
    _require_approver_role(await get_org_role(user_id, org_id, repo))
