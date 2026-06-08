"""Secrets REST endpoints — org-scoped named secret management.

Endpoints
---------
POST   /secrets              {name, value}   -> 201 secret (no value)
GET    /secrets                              -> [secret] (no values)
DELETE /secrets/{name}                       -> 204

All endpoints require a valid first-party Bearer token (``current_user``).
Secrets are org-scoped: callers can only see and operate on secrets belonging
to their own organisation.  Values are NEVER returned — only metadata.

Organisation resolution
-----------------------
Replicated from ``routes/flows.py`` to avoid circular imports.

Secret store
------------
All secret state is persisted via the store returned by ``get_secret_store()``.
Tests may inject their own store via ``set_secret_store(store)`` before issuing
requests.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel

from app.auth.deps import current_user
from app.db import fetchrow
from app.errors import AppError
from app.repos.provider import Repo, get_repo
from app.secrets.store import get_secret_store

# ---------------------------------------------------------------------------
# Sub-router
# ---------------------------------------------------------------------------

router = APIRouter(prefix="/secrets", tags=["secrets"])


# ---------------------------------------------------------------------------
# Org resolution helper (replicated from routes/flows.py to avoid circular
# imports — same approach, same docstring pattern).
# ---------------------------------------------------------------------------


async def _get_user_org(user_id: str, repo: Repo) -> str:
    """Return the org_id for the user's first membership.

    Mirrors ``routes.flows._get_user_org`` without importing it.
    """
    if hasattr(repo, "get_org_for_user"):
        org_id = repo.get_org_for_user(user_id)  # type: ignore[attr-defined]
        if org_id:
            return org_id
        raise AppError("org_not_found", "User has no org membership.", 404)

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


# ---------------------------------------------------------------------------
# Pydantic request schemas
# ---------------------------------------------------------------------------


class SetSecretIn(BaseModel):
    """Request body for ``POST /secrets``."""

    name: str
    value: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _dt_iso(dt: datetime | None) -> str | None:
    """Convert a datetime to ISO-8601 string, or None."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def _serialize_secret(secret: dict[str, Any]) -> dict[str, Any]:
    """Convert a secret dict to a JSON-serialisable form (no value)."""
    return {
        "id": secret["id"],
        "org_id": secret["org_id"],
        "name": secret["name"],
        "created_by": secret["created_by"],
        "created_at": _dt_iso(secret.get("created_at")),
        "updated_at": _dt_iso(secret.get("updated_at")),
    }


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("", status_code=201)
async def set_secret(
    body: SetSecretIn,
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
) -> dict[str, Any]:
    """Create or update a named secret for the caller's organisation.

    The plaintext *value* is encrypted before storage.  The response NEVER
    includes the value — only the name and metadata.

    Returns ``201`` for both create and update (upsert semantics).

    Raises
    ------
    AppError("bad_request", 400)
        If *name* or *value* is empty.
    AppError("org_not_found", 404)
        If the authenticated user has no org membership.
    """
    name = body.name.strip()
    if not name:
        raise AppError("bad_request", "Secret 'name' must not be empty.", 400)
    if not body.value:
        raise AppError("bad_request", "Secret 'value' must not be empty.", 400)

    org_id = await _get_user_org(str(user["id"]), repo)
    store = get_secret_store()
    secret = await store.set_secret(
        org_id=org_id,
        name=name,
        value=body.value,
        created_by=str(user["id"]),
    )
    return _serialize_secret(secret)


@router.get("", status_code=200)
async def list_secrets(
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
) -> list[dict[str, Any]]:
    """List all secret names (and metadata) for the caller's organisation.

    Values are NEVER included in the response.

    Returns
    -------
    list[dict]
        Secrets sorted by name, each with keys:
        ``{id, org_id, name, created_by, created_at, updated_at}``.
    """
    org_id = await _get_user_org(str(user["id"]), repo)
    store = get_secret_store()
    secrets = await store.list_secrets(org_id)
    return [_serialize_secret(s) for s in secrets]


@router.delete("/{name}", status_code=204)
async def delete_secret(
    name: str,
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
) -> Response:
    """Delete a named secret from the caller's organisation.

    Returns ``204 No Content`` on success.

    Raises
    ------
    AppError("not_found", 404)
        If no secret with *name* exists for the caller's org.
    """
    org_id = await _get_user_org(str(user["id"]), repo)
    store = get_secret_store()
    deleted = await store.delete_secret(org_id=org_id, name=name)
    if not deleted:
        raise AppError("not_found", f"Secret {name!r} not found.", 404)
    return Response(status_code=204)
