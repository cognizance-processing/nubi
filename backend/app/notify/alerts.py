"""Alert wiring for Nubi.

``notify_alert(event)`` formats an alert message from an event dict and sends
it through all configured notification channels.

Flow-failure listener
---------------------
At import time this module registers ``on_flow_event`` with
``app.flows.events.register_flow_listener``.  The registration is guarded so
the module still loads cleanly if ``app.flows.events`` is absent or not yet
initialised.

Event dict shape (flow events emitted by app.flows.events)
----------------------------------------------------------
{
    "kind":      str,   # e.g. "flow_run", "task_run"
    "status":    str,   # e.g. "failed", "timed_out", "succeeded"
    "name":      str,   # flow / task name
    "id":        str,   # run ID
    "error":     str,   # optional error message
    "org_id":    str,   # optional org context
    ...
}

Job failure events (can also call notify_alert directly)
--------------------------------------------------------
{
    "kind":   "job_run",
    "status": "failed",
    "name":   "My Report Job",
    "id":     "job-123",
    "error":  "connection refused",
}

Configuration
-------------
``notify_alert`` resolves channels from app settings:
  - SLACK_ALERT_WEBHOOK / SLACK_BOT_TOKEN + SLACK_ALERT_CHANNEL
  - WHATSAPP_ALERT_TOKEN + WHATSAPP_PHONE_NUMBER_ID + WHATSAPP_ALERT_RECIPIENT
  - ALERT_EMAIL_RECIPIENT
When none are configured, a NullChannel is used (alerts are recorded but not
delivered — safe in test / development environments).
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

__all__ = ["notify_alert", "on_flow_event", "format_alert_text"]

# Statuses that should trigger an alert.
_ALERT_STATUSES = frozenset({"failed", "timed_out", "error", "cancelled"})


# ---------------------------------------------------------------------------
# Format helpers
# ---------------------------------------------------------------------------


def format_alert_text(event: dict[str, Any]) -> str:
    """Render a human-readable alert string from *event*.

    Parameters
    ----------
    event:
        Event dict with at least ``kind``, ``status``, and ``name``.

    Returns
    -------
    str
        A formatted alert message suitable for Slack / WhatsApp / email.
    """
    kind = event.get("kind") or "run"
    status = event.get("status") or "unknown"
    name = event.get("name") or event.get("id") or "unknown"
    run_id = event.get("id") or ""
    error = event.get("error") or ""
    org_id = event.get("org_id") or ""

    # Emoji prefix: visual indicator for the channel.
    icon = {
        "failed": ":red_circle:",
        "timed_out": ":warning:",
        "error": ":red_circle:",
        "cancelled": ":grey_question:",
    }.get(status, ":bell:")

    lines = [
        f"{icon} *Nubi Alert* — {kind.replace('_', ' ').title()} {status.upper()}",
        f"*Name:* {name}",
    ]
    if run_id:
        lines.append(f"*Run ID:* {run_id}")
    if org_id:
        lines.append(f"*Org:* {org_id}")
    if error:
        lines.append(f"*Error:* {error[:500]}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Channel resolution
# ---------------------------------------------------------------------------


def _org_integration_channels(org_id: str) -> list[Any]:
    """Resolve the org's connected-integration channels (best-effort, sync).

    ``channels_for_org`` is async; drive it to completion so the synchronous
    ``notify_alert`` path can include per-org integrations. Degrades to an empty
    list on any failure (no loop / DB error / already inside a running loop) —
    org integrations are strictly additive on top of the app-settings channels.
    """
    import asyncio  # noqa: PLC0415

    from app.notify.integrations import channels_for_org  # noqa: PLC0415

    try:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(channels_for_org(org_id))
        logger.debug(
            "alerts: running loop present — skipping inline org integration "
            "resolution for org %s.",
            org_id,
        )
        return []
    except Exception as exc:  # noqa: BLE001 — org integrations are best-effort.
        logger.warning("alerts: org integration resolution failed: %s", exc)
        return []


def _get_configured_channels(org_id: str | None = None) -> list[Any]:
    """Return a list of configured Channel objects from app settings.

    When *org_id* is given, the org's ENABLED connected integrations
    (``org_integrations``) are ALSO appended via
    :func:`app.notify.integrations.channels_for_org` — additive on top of the
    app-settings channels below, so existing behaviour is preserved when no org
    integrations exist.

    Falls back gracefully when settings are absent or credentials are
    incomplete.  Always returns at least a NullChannel so that alert
    delivery is safe in unconfigured environments.
    """
    from app.notify.channels import get_channel, NullChannel  # local to avoid circular

    channels: list[Any] = []

    try:
        from app.config import get_settings  # noqa: PLC0415

        settings = get_settings()

        # ── Slack ────────────────────────────────────────────────────────────
        slack_webhook = getattr(settings, "SLACK_ALERT_WEBHOOK", "") or ""
        slack_token = getattr(settings, "SLACK_BOT_TOKEN", "") or ""
        slack_channel = getattr(settings, "SLACK_ALERT_CHANNEL", "") or ""
        if slack_webhook or slack_token:
            channels.append(
                get_channel(
                    "slack",
                    {
                        "webhook_url": slack_webhook,
                        "bot_token": slack_token,
                        "channel": slack_channel,
                    },
                )
            )

        # ── WhatsApp ─────────────────────────────────────────────────────────
        wa_token = getattr(settings, "WHATSAPP_SEND_TOKEN", "") or ""
        wa_phone_id = getattr(settings, "WHATSAPP_PHONE_NUMBER_ID", "") or ""
        wa_recipient = getattr(settings, "WHATSAPP_ALERT_RECIPIENT", "") or ""
        if wa_token and wa_phone_id and wa_recipient:
            channels.append(
                get_channel(
                    "whatsapp",
                    {
                        "token": wa_token,
                        "phone_number_id": wa_phone_id,
                        "recipient": wa_recipient,
                    },
                )
            )

        # ── Email ────────────────────────────────────────────────────────────
        email_recipient = getattr(settings, "ALERT_EMAIL_RECIPIENT", "") or ""
        if email_recipient:
            channels.append(
                get_channel("email", {"recipient": email_recipient})
            )

    except Exception as exc:  # noqa: BLE001
        logger.warning("alerts: could not load settings for channel resolution: %s", exc)

    # ── Per-org connected integrations (additive) ──────────────────────────
    if org_id:
        channels.extend(_org_integration_channels(str(org_id)))

    if not channels:
        channels.append(NullChannel())

    return channels


# ---------------------------------------------------------------------------
# notify_alert — main entry point
# ---------------------------------------------------------------------------


def notify_alert(
    event: dict[str, Any],
    *,
    channels: list[Any] | None = None,
    image_png: bytes | None = None,
) -> None:
    """Format *event* as an alert and deliver it via configured channels.

    Parameters
    ----------
    event:
        Event dict describing the failure/alert (see module docstring).
    channels:
        Override the channel list (useful in tests).  When ``None``, channels
        are resolved from app settings.
    image_png:
        Optional PNG bytes to attach (e.g. a chart related to the failure).
    """
    text = format_alert_text(event)
    _channels = (
        channels
        if channels is not None
        else _get_configured_channels(event.get("org_id"))
    )

    for ch in _channels:
        try:
            ch.send(text, image_png)
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "alerts: channel %s failed to deliver: %s",
                type(ch).__name__,
                exc,
            )


# ---------------------------------------------------------------------------
# Flow-failure listener
# ---------------------------------------------------------------------------


def on_flow_event(event: dict[str, Any]) -> None:
    """Called by the flow events bus for every flow / task transition.

    Only fires ``notify_alert`` when the event status indicates a failure
    (``failed``, ``timed_out``, ``error``).

    Parameters
    ----------
    event:
        Flow event dict (see module docstring).
    """
    status = str(event.get("status") or "").lower()
    if status in _ALERT_STATUSES:
        logger.info(
            "alerts: flow event %r triggered alert (status=%r)",
            event.get("name") or event.get("id"),
            status,
        )
        notify_alert(event)


# ---------------------------------------------------------------------------
# Register the listener at import time (guarded)
# ---------------------------------------------------------------------------


def _register_flow_listener() -> None:
    """Attempt to register ``on_flow_event`` with the flows events bus.

    Silently skips registration when ``app.flows.events`` is absent or does
    not yet expose ``register_flow_listener`` — so this module loads cleanly
    in all environments.
    """
    try:
        import importlib

        events_mod = importlib.import_module("app.flows.events")
        register_fn = getattr(events_mod, "register_flow_listener", None)
        if register_fn is not None:
            register_fn(on_flow_event)
            logger.debug("alerts: registered on_flow_event with app.flows.events.")
        else:
            logger.debug(
                "alerts: app.flows.events has no register_flow_listener — skipping."
            )
    except ImportError:
        logger.debug("alerts: app.flows.events not importable — skipping listener registration.")
    except Exception as exc:  # noqa: BLE001
        logger.warning("alerts: unexpected error registering flow listener: %s", exc)


_register_flow_listener()
