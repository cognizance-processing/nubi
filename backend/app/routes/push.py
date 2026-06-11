"""Web Push (VAPID) subscription endpoints.

Routes (all under ``/api/v1``)
------------------------------
- ``GET  /push/vapid_key``     — the VAPID public key for the browser's
                                 ``pushManager.subscribe`` call.
- ``POST /push/subscribe``     — upsert a browser ``PushSubscription`` (by
                                 endpoint) for the caller.
- ``POST /push/unsubscribe``   — delete a subscription by endpoint.

Auth + tenancy mirror ``routes/notifications.py``: a verified first-party user
(:func:`current_user`) scoped to an org via :func:`resolve_org_id`.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from app.auth.deps import current_user
from app.errors import AppError
from app.notify.push import get_push_store, vapid_public_key
from app.repos.provider import Repo, get_repo
from app.routes import api_router
from app.routes._org import resolve_org_id

router = APIRouter(prefix="/push", tags=["push"])


class SubscribeIn(BaseModel):
    """A browser ``PushSubscription.toJSON()`` payload.

    ``{endpoint, keys: {p256dh, auth}}`` — extra keys (expirationTime, …) are
    tolerated and ignored.
    """

    model_config = {"extra": "allow"}

    endpoint: str = ""
    keys: dict[str, Any] = {}


class UnsubscribeIn(BaseModel):
    endpoint: str = ""


@router.get("/vapid_key")
async def get_vapid_key(
    user: dict[str, Any] = Depends(current_user),
) -> dict:
    """Return the VAPID public key (``null`` when push is unconfigured)."""
    return {"public_key": vapid_public_key()}


@router.post("/subscribe")
async def subscribe(
    body: SubscribeIn,
    request: Request,
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
) -> dict:
    """Upsert the caller's push subscription (keyed by endpoint)."""
    org_id = await resolve_org_id(str(user["id"]), repo, request)

    endpoint = (body.endpoint or "").strip()
    p256dh = str((body.keys or {}).get("p256dh") or "").strip()
    auth = str((body.keys or {}).get("auth") or "").strip()
    if not endpoint or not p256dh or not auth:
        raise AppError(
            "validation_error",
            "A push subscription requires endpoint + keys.p256dh + keys.auth.",
            400,
        )

    user_agent = request.headers.get("user-agent")
    store = get_push_store()
    row = await store.upsert(
        str(user["id"]), org_id, endpoint, p256dh, auth, user_agent
    )
    if not row:
        # The endpoint is already registered to a different user; refuse to
        # rebind it (would silently redirect that user's pushes to this caller).
        raise AppError(
            "conflict",
            "This push endpoint is already registered to another user.",
            409,
        )
    return {"subscription": row}


@router.post("/unsubscribe")
async def unsubscribe(
    body: UnsubscribeIn,
    request: Request,
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
) -> dict:
    """Delete a push subscription by endpoint."""
    # Resolve org for tenancy consistency (validates X-Org-Id membership).
    await resolve_org_id(str(user["id"]), repo, request)
    endpoint = (body.endpoint or "").strip()
    if not endpoint:
        raise AppError("validation_error", "endpoint is required.", 400)
    store = get_push_store()
    # Scope the delete to the calling user — a push endpoint is a guessable URL,
    # so an unscoped delete-by-endpoint is an IDOR (any user could prune another
    # user's subscription).
    removed = await store.delete(endpoint, str(user["id"]))
    return {"ok": True, "removed": removed}


api_router.include_router(router)
