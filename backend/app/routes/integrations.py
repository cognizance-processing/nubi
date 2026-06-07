"""Integration management endpoints for Nubi.

Provides visibility into configured notification channels and the ability to
send test alerts.

Endpoints
---------
GET  /integrations
    List all known notification channels with their configuration status
    (configured / unconfigured).  Does NOT expose secrets.

POST /integrations/test
    Send a test alert through all configured channels (or NullChannel when
    none are set).  Returns which channels received the alert.

POST /integrations          (optional — org-scoped config save, best-effort)
    Persist per-org channel overrides.  Not wired to a real store yet; accepts
    and returns the payload so that callers can verify schema round-trips.

Authentication
--------------
All endpoints require a valid first-party Bearer token (``current_user``).

Wiring
------
Self-registers on ``api_router`` at the bottom of this module — the
orchestrator imports it in ``main.py`` and the router is mounted automatically,
mirroring the pattern used by ``routes/git.py`` and ``routes/chat.py``.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.auth.deps import current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/integrations", tags=["integrations"])


# ---------------------------------------------------------------------------
# Helper: resolve channel status from settings
# ---------------------------------------------------------------------------


def _channel_status() -> list[dict[str, Any]]:
    """Return a list of channel dicts with ``name``, ``kind``, and ``configured`` flag.

    Never includes secret values.
    """
    try:
        from app.config import get_settings  # noqa: PLC0415

        s = get_settings()
    except Exception:  # noqa: BLE001
        s = None

    def _bool(val: str) -> bool:
        return bool(val and val.strip())

    channels = [
        {
            "name": "slack_webhook",
            "kind": "slack",
            "configured": _bool(getattr(s, "SLACK_ALERT_WEBHOOK", "")),
            "description": "Slack Incoming Webhook for alert delivery.",
        },
        {
            "name": "slack_bot",
            "kind": "slack",
            "configured": _bool(getattr(s, "SLACK_BOT_TOKEN", "")),
            "description": "Slack bot token (chat.postMessage + chart uploads).",
        },
        {
            "name": "whatsapp",
            "kind": "whatsapp",
            "configured": (
                _bool(getattr(s, "WHATSAPP_SEND_TOKEN", ""))
                and _bool(getattr(s, "WHATSAPP_PHONE_NUMBER_ID", ""))
                and _bool(getattr(s, "WHATSAPP_ALERT_RECIPIENT", ""))
            ),
            "description": "WhatsApp Cloud API for alert delivery.",
        },
        {
            "name": "email",
            "kind": "email",
            "configured": _bool(getattr(s, "ALERT_EMAIL_RECIPIENT", "")),
            "description": "Email channel for alert delivery.",
        },
    ]
    return channels


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class TestAlertRequest(BaseModel):
    """Request body for POST /integrations/test."""

    use_null: bool = False
    """When True, always use NullChannel (dry-run — no real delivery)."""

    message: str = "Nubi test alert — integration check."
    """Custom message for the test alert."""


class IntegrationConfigRequest(BaseModel):
    """Request body for POST /integrations (save channel config)."""

    kind: str
    """Channel kind: ``"slack"``, ``"whatsapp"``, ``"email"``."""

    config: dict[str, Any] = {}
    """Channel-specific configuration key/values (excluding secrets)."""


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("")
async def list_integrations(
    _user: dict[str, Any] = Depends(current_user),
) -> dict[str, Any]:
    """List all notification channels with their configuration status.

    Returns
    -------
    dict
        ``{"channels": [...], "any_configured": bool}``
    """
    channels = _channel_status()
    return {
        "channels": channels,
        "any_configured": any(ch["configured"] for ch in channels),
    }


@router.post("/test")
async def test_alert(
    body: TestAlertRequest,
    _user: dict[str, Any] = Depends(current_user),
) -> dict[str, Any]:
    """Send a test alert via all configured channels.

    When ``use_null=true`` the alert is sent to a NullChannel (no real
    network calls) — useful for verifying the alert pipeline without
    delivering to external services.

    Returns
    -------
    dict
        ``{"sent": [{"channel": ..., "kind": ..., "null": bool}], "message": str}``
    """
    from app.notify.alerts import notify_alert  # noqa: PLC0415
    from app.notify.channels import NullChannel  # noqa: PLC0415

    event = {
        "kind": "test",
        "status": "failed",  # use "failed" so format_alert_text renders an alert icon
        "name": "Test Alert",
        "id": "test-001",
        "error": body.message,
    }

    sent_info: list[dict[str, Any]] = []

    if body.use_null:
        ch = NullChannel()
        notify_alert(event, channels=[ch])
        sent_info.append({"channel": "NullChannel", "kind": "null", "null": True, "records": ch.sent})
    else:
        from app.notify.alerts import _get_configured_channels  # noqa: PLC0415

        channels = _get_configured_channels()
        notify_alert(event, channels=channels)
        for ch in channels:
            is_null = isinstance(ch, NullChannel)
            sent_info.append({
                "channel": type(ch).__name__,
                "kind": "null" if is_null else type(ch).__name__.replace("Channel", "").lower(),
                "null": is_null,
            })

    return {
        "ok": True,
        "message": body.message,
        "sent": sent_info,
    }


@router.post("")
async def save_integration_config(
    body: IntegrationConfigRequest,
    _user: dict[str, Any] = Depends(current_user),
) -> dict[str, Any]:
    """Accept and validate a channel configuration request.

    Note: This endpoint currently performs schema validation only.
    Persisting channel config to a DB/secrets store is a future enhancement.
    Secrets should be injected via environment variables, not stored here.

    Returns
    -------
    dict
        The accepted configuration (secrets stripped).
    """
    allowed_kinds = {"slack", "whatsapp", "email", "null"}
    if body.kind not in allowed_kinds:
        from fastapi import HTTPException  # noqa: PLC0415
        raise HTTPException(
            status_code=400,
            detail={
                "code": "invalid_kind",
                "message": (
                    f"Unknown channel kind {body.kind!r}. "
                    f"Expected one of: {sorted(allowed_kinds)}."
                ),
            },
        )

    # Strip any secret-looking keys from the echo (defense-in-depth).
    _secret_keys = {"token", "secret", "password", "key", "bot_token", "webhook_url"}
    safe_config = {
        k: ("***" if any(s in k.lower() for s in _secret_keys) else v)
        for k, v in body.config.items()
    }

    return {
        "ok": True,
        "kind": body.kind,
        "config": safe_config,
        "note": (
            "Config accepted for validation. To persist, set the corresponding "
            "environment variables (SLACK_BOT_TOKEN, SLACK_ALERT_WEBHOOK, etc.)."
        ),
    }


# ---------------------------------------------------------------------------
# Self-register on the shared api_router (mirrors routes/git.py)
# ---------------------------------------------------------------------------

from app.routes import api_router  # noqa: E402

api_router.include_router(router)
