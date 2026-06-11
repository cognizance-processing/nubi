"""In-app notification feed endpoints.

Routes (all under ``/api/v1``)
------------------------------
- ``GET  /notifications``               — paginated feed (``?unread=1``,
                                           ``?limit=``, ``?before=`` cursor).
- ``GET  /notifications/unread_count``   — unread badge count.
- ``POST /notifications/{id}/read``      — mark one read for the caller.
- ``POST /notifications/read_all``       — mark every visible one read.

Auth + tenancy mirror ``routes/variables.py`` / ``routes/connectors.py``: a
verified first-party user (:func:`current_user`) scoped to an org resolved via
:func:`resolve_org_id` (honours ``X-Org-Id`` with a membership check). The feed
returns the user's targeted notifications plus org broadcasts, with per-user
read-state folded in by the store.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request

from app.auth.deps import current_user
from app.notify.notifications import get_notification_store
from app.repos.provider import Repo, get_repo
from app.routes import api_router
from app.routes._org import resolve_org_id

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get("")
async def list_notifications(
    request: Request,
    unread: int = 0,
    limit: int = 50,
    before: str | None = None,
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
) -> dict:
    """Return the caller's notification feed (newest first)."""
    org_id = await resolve_org_id(str(user["id"]), repo, request)
    store = get_notification_store()
    rows = await store.list_for_user(
        org_id,
        str(user["id"]),
        unread_only=bool(unread),
        limit=limit,
        before=before,
    )
    return {"notifications": rows}


@router.get("/unread_count")
async def unread_count(
    request: Request,
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
) -> dict:
    """Return the unread notification count for the caller (badge)."""
    org_id = await resolve_org_id(str(user["id"]), repo, request)
    store = get_notification_store()
    count = await store.unread_count(org_id, str(user["id"]))
    return {"unread": count}


@router.post("/read_all")
async def read_all(
    request: Request,
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
) -> dict:
    """Mark every notification visible to the caller as read."""
    org_id = await resolve_org_id(str(user["id"]), repo, request)
    store = get_notification_store()
    remaining = await store.mark_all_read(org_id, str(user["id"]))
    return {"ok": True, "unread": remaining}


@router.post("/{notification_id}/read")
async def mark_read(
    notification_id: str,
    request: Request,
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
) -> dict:
    """Mark one notification read for the caller."""
    # Resolve org for tenancy consistency (also validates X-Org-Id membership).
    await resolve_org_id(str(user["id"]), repo, request)
    store = get_notification_store()
    ok = await store.mark_read(notification_id, str(user["id"]))
    return {"ok": ok}


api_router.include_router(router)
