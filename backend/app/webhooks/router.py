"""Webhook endpoints management API — org-scoped CRUD (first-party auth).

Endpoints
---------
GET    /webhooks                 -> list endpoints for caller's org (no secrets)
POST   /webhooks                 -> create a new endpoint
GET    /webhooks/{endpoint_id}   -> get a single endpoint by id (no secret)
PUT    /webhooks/{endpoint_id}   -> partial update (incl. secret rotation)
DELETE /webhooks/{endpoint_id}   -> delete an endpoint

All endpoints require a valid first-party Bearer token (``current_user``) and
are org-scoped: callers only see and operate on their own org's endpoints.
Mirrors the conventions in ``app/routes/jwt_issuers.py``.

The signing secret is write-only over the API: it is accepted on create/update
but NEVER returned by any read. Self-registers on ``api_router`` at import time.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from app.auth.deps import current_user
from app.auth.roles import require_writer_default
from app.errors import AppError
from app.repos.provider import Repo, get_repo
from app.routes import api_router
from app.routes._org import get_user_org as _get_user_org
from app.webhooks.events import ALL_EVENT_TYPES
from app.webhooks.schemas import WebhookCreate, WebhookUpdate

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


def _validate_event_types(event_types: list[str]) -> None:
    """Reject unknown event types with a 422 (keeps subscriptions deliverable)."""
    unknown = [t for t in event_types if t not in ALL_EVENT_TYPES]
    if unknown:
        raise AppError(
            "invalid_event_types",
            f"Unknown event types: {unknown}. Allowed: {list(ALL_EVENT_TYPES)}.",
            422,
        )


def _validate_webhook_url(url: str) -> None:
    """Validate the webhook URL is safe to deliver to (SSRF guard).

    Called at create and update time so a malicious URL is rejected immediately
    rather than being stored and attempted on every future event delivery.
    Raises ``AppError("ssrf_blocked", 400)`` for private/loopback/metadata IPs,
    non-http(s) schemes, and URLs with no host.
    """
    from app.connectors.ssrf import guard_url as _guard_url  # noqa: PLC0415

    _guard_url(url)


@router.get("/")
async def list_webhooks(
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
) -> list[dict[str, Any]]:
    """Return all webhook endpoints for the caller's org (never includes secrets)."""
    from app.webhooks.models import get_webhook_store  # noqa: PLC0415

    org_id = await _get_user_org(str(user["id"]), repo)
    return await get_webhook_store().list_for_org(org_id)


@router.post("/", status_code=201, dependencies=[Depends(require_writer_default)])
async def create_webhook(
    body: WebhookCreate,
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
) -> dict[str, Any]:
    """Create a new webhook endpoint for the caller's org."""
    from app.webhooks.models import get_webhook_store  # noqa: PLC0415

    _validate_event_types(body.event_types)
    _validate_webhook_url(body.url)

    org_id = await _get_user_org(str(user["id"]), repo)
    return await get_webhook_store().create(
        org_id=org_id,
        name=body.name,
        url=body.url,
        secret=body.secret,
        created_by=str(user["id"]),
        event_types=body.event_types,
        active=body.active,
    )


@router.get("/{endpoint_id}")
async def get_webhook(
    endpoint_id: str,
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
) -> dict[str, Any]:
    """Return a single webhook endpoint by id (never includes the secret)."""
    from app.webhooks.models import get_webhook_store  # noqa: PLC0415

    org_id = await _get_user_org(str(user["id"]), repo)
    row = await get_webhook_store().get_by_id(endpoint_id, org_id)
    if row is None:
        raise AppError("webhook_not_found", "Webhook endpoint not found.", 404)
    return row


@router.put("/{endpoint_id}", dependencies=[Depends(require_writer_default)])
async def update_webhook(
    endpoint_id: str,
    body: WebhookUpdate,
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
) -> dict[str, Any]:
    """Partially update a webhook endpoint (including secret rotation)."""
    from app.webhooks.models import get_webhook_store  # noqa: PLC0415

    if body.event_types is not None:
        _validate_event_types(body.event_types)
    if body.url is not None:
        _validate_webhook_url(body.url)

    org_id = await _get_user_org(str(user["id"]), repo)
    row = await get_webhook_store().update(
        endpoint_id,
        org_id,
        name=body.name,
        url=body.url,
        secret=body.secret,
        event_types=body.event_types,
        active=body.active,
    )
    if row is None:
        raise AppError("webhook_not_found", "Webhook endpoint not found.", 404)
    return row


@router.delete(
    "/{endpoint_id}", status_code=204, dependencies=[Depends(require_writer_default)]
)
async def delete_webhook(
    endpoint_id: str,
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
) -> None:
    """Delete a webhook endpoint."""
    from app.webhooks.models import get_webhook_store  # noqa: PLC0415

    org_id = await _get_user_org(str(user["id"]), repo)
    deleted = await get_webhook_store().delete(endpoint_id, org_id)
    if not deleted:
        raise AppError("webhook_not_found", "Webhook endpoint not found.", 404)


# ---------------------------------------------------------------------------
# Self-register on api_router
# ---------------------------------------------------------------------------

api_router.include_router(router)
