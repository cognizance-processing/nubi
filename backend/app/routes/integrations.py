"""Per-org connected-integration CRUD for Nubi.

A *connected integration* (Slack / WhatsApp / Google Chat / Teams / Email /
webhook) powers BOTH inbound chat and outbound alerts for an org. This router
is real per-org persistence on top of :class:`app.notify.integrations.IntegrationStore`
(non-secret config in ``org_integrations``, secret material AES-256-GCM encrypted
in ``integration_secrets``).

Endpoints
---------
GET    /integrations            — list the org's integrations (secrets scrubbed;
                                  each carries ``configured: bool``).
POST   /integrations            — create an integration (secret split out + encrypted).
GET    /integrations/{id}       — fetch one (secrets scrubbed).
PUT    /integrations/{id}       — update non-secret config and/or rotate the secret.
DELETE /integrations/{id}       — delete the integration + its secret blob.
POST   /integrations/{id}/test  — build the live channel and send a test message.

Security contract
-----------------
- Secret fields (per ``SECRET_KEYS_BY_KIND``) are NEVER stored in ``config`` and
  NEVER returned in any response — listings carry only non-secret config plus a
  ``configured`` boolean.
- All operations are org-scoped via ``current_user`` + ``resolve_org_id``; a row
  belonging to a different org is treated as not-found (404, no info leak).

The router self-registers on the shared ``api_router`` at import time, mirroring
``routes/connectors.py`` — ``main.py`` mounts ``api_router`` so it is picked up
automatically.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel

from app.auth.deps import current_user
from app.errors import AppError
from app.notify.integrations import (
    VALID_KINDS,
    get_integration_store,
    merged_channel_config,
    public_row,
    split_secret,
)
from app.repos.provider import Repo, get_repo
from app.routes import api_router
from app.routes._org import resolve_org_id

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/integrations", tags=["integrations"])


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class CreateIntegrationIn(BaseModel):
    """Request body for POST /integrations.

    ``config`` carries the FULL field set the caller submits (secret +
    non-secret); the store splits secret fields out per ``SECRET_KEYS_BY_KIND``
    and encrypts them. Callers therefore do not pre-separate secrets.
    """

    kind: str
    name: str
    config: dict[str, Any] = {}
    enabled: bool = True


class UpdateIntegrationIn(BaseModel):
    """Request body for PUT /integrations/{id}. All fields optional."""

    name: str | None = None
    config: dict[str, Any] | None = None
    enabled: bool | None = None


class TestIntegrationIn(BaseModel):
    """Request body for POST /integrations/{id}/test."""

    message: str = "Nubi test message — integration check."


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _validate_kind(kind: str) -> str:
    """Return the normalised kind or raise a 400 AppError."""
    norm = (kind or "").lower().strip()
    if norm not in VALID_KINDS:
        raise AppError(
            "invalid_kind",
            f"Unknown integration kind {kind!r}. Expected one of: {sorted(VALID_KINDS)}.",
            400,
        )
    return norm


async def _scrubbed(store: Any, row: dict[str, Any], org_id: str) -> dict[str, Any]:
    """Return the listing-safe shape of *row* with a ``configured`` flag.

    ``configured`` is True when a (non-empty) secret blob exists for the
    integration — i.e. it has the credentials needed to deliver. Email
    integrations need no secret, so they are ``configured`` whenever they exist.
    """
    has_secret = await store.has_secret(str(row["id"]), org_id)
    configured = has_secret or row.get("kind") == "email"
    return public_row(row, configured=configured)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("")
async def list_integrations(
    request: Request,
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
) -> dict[str, Any]:
    """List the caller's org integrations (no secret material returned)."""
    org_id = await resolve_org_id(str(user["id"]), repo, request)
    store = get_integration_store()
    rows = await store.list_for_org(org_id)
    items = [await _scrubbed(store, row, org_id) for row in rows]
    return {"integrations": items}


@router.post("", status_code=201)
async def create_integration(
    body: CreateIntegrationIn,
    request: Request,
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
) -> dict[str, Any]:
    """Create an integration. Secret fields are split out and encrypted at rest."""
    org_id = await resolve_org_id(str(user["id"]), repo, request)
    kind = _validate_kind(body.kind)

    config, secret = split_secret(kind, body.config)
    store = get_integration_store()
    row = await store.create(
        org_id=org_id,
        created_by=str(user["id"]),
        kind=kind,
        name=body.name,
        config=config,
        secret=secret,
        enabled=body.enabled,
    )
    return await _scrubbed(store, row, org_id)


@router.get("/{integration_id}")
async def get_integration(
    integration_id: str,
    request: Request,
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
) -> dict[str, Any]:
    """Fetch one integration (no secret material). 404 if absent or cross-org."""
    org_id = await resolve_org_id(str(user["id"]), repo, request)
    store = get_integration_store()
    row = await store.get(integration_id, org_id)
    if row is None:
        raise AppError("not_found", "Integration not found.", 404)
    return await _scrubbed(store, row, org_id)


@router.put("/{integration_id}")
async def update_integration(
    integration_id: str,
    body: UpdateIntegrationIn,
    request: Request,
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
) -> dict[str, Any]:
    """Update non-secret config and/or rotate the secret. 404 if absent/cross-org."""
    org_id = await resolve_org_id(str(user["id"]), repo, request)
    store = get_integration_store()

    existing = await store.get(integration_id, org_id)
    if existing is None:
        raise AppError("not_found", "Integration not found.", 404)

    kind = existing["kind"]
    config_update: dict[str, Any] | None = None
    secret_update: dict[str, Any] | None = None
    if body.config is not None:
        config_update, secret_update = split_secret(kind, body.config)

    row = await store.update(
        integration_id,
        org_id,
        name=body.name,
        config=config_update,
        secret=secret_update,
        enabled=body.enabled,
    )
    if row is None:
        raise AppError("not_found", "Integration not found.", 404)
    return await _scrubbed(store, row, org_id)


@router.delete("/{integration_id}", status_code=204)
async def delete_integration(
    integration_id: str,
    request: Request,
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
) -> Response:
    """Delete the integration + its secret blob. 204 on success, 404 otherwise."""
    org_id = await resolve_org_id(str(user["id"]), repo, request)
    store = get_integration_store()
    deleted = await store.delete(integration_id, org_id)
    if not deleted:
        raise AppError("not_found", "Integration not found.", 404)
    return Response(status_code=204)


@router.post("/{integration_id}/test")
async def test_integration(
    integration_id: str,
    body: TestIntegrationIn,
    request: Request,
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
) -> dict[str, Any]:
    """Build the live channel for this integration and send a test message.

    Reports ``sent`` (True when a real channel delivered) per integration. A
    delivery failure is reported, not raised. Never returns secret material.
    """
    from app.notify.channels import NullChannel, get_channel  # noqa: PLC0415
    from app.notify.integrations import _channel_kind_for  # noqa: PLC0415

    org_id = await resolve_org_id(str(user["id"]), repo, request)
    store = get_integration_store()

    row = await store.get(integration_id, org_id)
    if row is None:
        raise AppError("not_found", "Integration not found.", 404)

    kind = (row.get("kind") or "").lower().strip()
    secret = await store.get_secret(integration_id, org_id) or {}
    merged = merged_channel_config(kind, row.get("config") or {}, secret)
    channel = get_channel(_channel_kind_for(kind), merged)

    if isinstance(channel, NullChannel):
        return {
            "ok": False,
            "sent": False,
            "kind": kind,
            "detail": "Integration is not fully configured (missing credentials).",
        }

    try:
        channel.send(body.message)
    except Exception as exc:  # noqa: BLE001 — surface delivery errors, never raise.
        logger.info("test_integration(%s): delivery failed: %s", integration_id, exc)
        return {
            "ok": False,
            "sent": False,
            "kind": kind,
            "detail": f"Delivery failed: {exc}",
        }

    return {"ok": True, "sent": True, "kind": kind}


# ---------------------------------------------------------------------------
# Self-register on the shared api_router (mirrors routes/connectors.py)
# ---------------------------------------------------------------------------

api_router.include_router(router)
