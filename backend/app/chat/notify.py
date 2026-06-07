"""Outbound notifications for Nubi — Prefect-style flow-run alerts.

This module is the notification sink for the chat/gateway domain.  It sends a
concise message to Slack (Incoming Webhook *or* bot token) and/or WhatsApp
(Cloud API) when a flow run finalises, and is otherwise a no-op.

Design
------
- **Lazy / config-driven.**  Channels are resolved from per-flow alert config,
  org-level defaults, and finally app settings.  When nothing is configured the
  module is a pure no-op (it never raises and never makes a network call).
- **Best-effort.**  Every send is wrapped so a delivery failure can never break
  the flow engine — the flow-run alert hook calls into here inside a broad
  ``try`` of its own as well (belt and braces).
- **Reuses the channel implementations** in ``app.notify.channels`` (Slack /
  WhatsApp / Null) so there is a single place that talks to the providers.

Alert config shape (per-flow, stored on ``flow["spec"]["alerts"]`` or
``flow["config"]["alerts"]``)::

    {
        "on": ["failed", "success"],   # which terminal states fire an alert
        "slack_channel": "#data-ops",  # optional channel override (bot token)
        "slack_webhook": "https://hooks.slack.com/...",  # optional override
        "whatsapp_to": "+27821234567", # optional recipient override
    }

Org-level defaults can be supplied by the caller (or, in future, a settings
table) and are merged underneath the per-flow config.

Public API
----------
- ``format_flow_alert(event) -> str``       Prefect-style alert text.
- ``resolve_alert_config(flow, org_defaults) -> dict``  merged alert config.
- ``should_alert(config, state) -> bool``   does *state* fire under *config*?
- ``channels_for(config) -> list[Channel]`` config-driven channel list.
- ``notify_flow_run(event, *, config, channels) -> int``  send; returns count.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "format_flow_alert",
    "resolve_alert_config",
    "should_alert",
    "channels_for",
    "notify_flow_run",
    "DEFAULT_ALERT_EVENTS",
]

#: Terminal flow states that fire an alert when no explicit ``on`` is given.
DEFAULT_ALERT_EVENTS: tuple[str, ...] = ("failed", "timed_out")

# State → status mapping for normalisation (flow run states → alert statuses).
_STATE_ICONS = {
    "failed": ":red_circle:",
    "timed_out": ":warning:",
    "cancelled": ":grey_question:",
    "success": ":large_green_circle:",
}


# ---------------------------------------------------------------------------
# Formatter
# ---------------------------------------------------------------------------


def _fmt_duration(seconds: float | int | None) -> str:
    """Render a human-readable duration (e.g. ``1m 23s``) from *seconds*."""
    if seconds is None:
        return "unknown"
    try:
        secs = max(0, int(round(float(seconds))))
    except (TypeError, ValueError):
        return "unknown"
    if secs < 60:
        return f"{secs}s"
    minutes, rem = divmod(secs, 60)
    if minutes < 60:
        return f"{minutes}m {rem}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes}m"


def format_flow_alert(event: dict[str, Any]) -> str:
    """Render a concise, Prefect-style alert message for a flow run.

    Parameters
    ----------
    event:
        A dict with (all optional except ``state``)::

            {
                "kind": "flow_run",
                "name": "Daily ETL",
                "state": "failed" | "success" | "timed_out" | ...,
                "flow_run_id": "fr-001",
                "duration_s": 83,
                "link": "https://app.nubi.dev/flows/.../runs/...",
                "failed_task": "load_warehouse",
                "error": "connection refused",
                "org_id": "org-abc",
            }

    Returns
    -------
    str
        Slack/WhatsApp-friendly Markdown text.
    """
    state = str(event.get("state") or "unknown").lower()
    name = event.get("name") or event.get("flow_name") or event.get("id") or "flow"
    icon = _STATE_ICONS.get(state, ":bell:")

    headline = f"{icon} *Flow {state.upper()}* — {name}"
    lines = [headline]

    run_id = event.get("flow_run_id") or event.get("id")
    if run_id:
        lines.append(f"*Run:* {run_id}")

    if event.get("duration_s") is not None:
        lines.append(f"*Duration:* {_fmt_duration(event.get('duration_s'))}")

    org_id = event.get("org_id")
    if org_id:
        lines.append(f"*Org:* {org_id}")

    failed_task = event.get("failed_task")
    if failed_task:
        lines.append(f"*Failed task:* {failed_task}")

    error = event.get("error")
    if error:
        lines.append(f"*Error:* {str(error)[:500]}")

    link = event.get("link")
    if link:
        lines.append(f"*Link:* {link}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Alert config resolution
# ---------------------------------------------------------------------------


def _coerce_alert_block(raw: Any) -> dict[str, Any]:
    """Coerce an ``alerts`` config value into a normalised dict.

    Accepts a dict (used directly), ``True``/``"all"`` (alert on every terminal
    state with defaults), a list of states (treated as the ``on`` list), or
    anything falsy (no alerting).
    """
    if isinstance(raw, dict):
        return dict(raw)
    if raw in (True, "all", "*"):
        return {"on": ["failed", "timed_out", "cancelled", "success"]}
    if isinstance(raw, (list, tuple, set)):
        return {"on": [str(s).lower() for s in raw]}
    return {}


def resolve_alert_config(
    flow: dict[str, Any] | None,
    org_defaults: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Merge per-flow and org-level alert config into a single dict.

    Precedence (highest first): per-flow ``spec.alerts`` / ``config.alerts`` →
    org-level defaults → empty.  Keys present on the flow override the org
    defaults; ``on`` is taken from whichever level defines it (flow first).

    Parameters
    ----------
    flow:
        The flow record (``{spec, config, ...}``) or ``None``.
    org_defaults:
        Org-level alert config dict (e.g. ``{"on": ["failed"], "slack_channel":
        "#alerts"}``) or ``None``.

    Returns
    -------
    dict
        The merged alert config.  Empty dict ⇒ no alerting.
    """
    merged: dict[str, Any] = {}

    if org_defaults:
        merged.update(_coerce_alert_block(org_defaults))

    flow_alerts: Any = None
    if flow:
        spec = flow.get("spec") or {}
        config = flow.get("config") or {}
        if isinstance(spec, dict) and spec.get("alerts") is not None:
            flow_alerts = spec.get("alerts")
        elif isinstance(config, dict) and config.get("alerts") is not None:
            flow_alerts = config.get("alerts")

    if flow_alerts is not None:
        merged.update(_coerce_alert_block(flow_alerts))

    return merged


def should_alert(config: dict[str, Any], state: str) -> bool:
    """Return True when *state* should fire an alert under *config*.

    The set of alerting states is ``config["on"]`` when present (normalised to
    lowercase), else :data:`DEFAULT_ALERT_EVENTS`.  An empty/absent config never
    alerts (no-op when unconfigured).
    """
    if not config:
        return False
    on = config.get("on")
    if on is None:
        events = DEFAULT_ALERT_EVENTS
    else:
        events = tuple(str(s).lower() for s in on)
    return str(state or "").lower() in events


# ---------------------------------------------------------------------------
# Channel resolution
# ---------------------------------------------------------------------------


def channels_for(config: dict[str, Any]) -> list[Any]:
    """Build the list of delivery channels for *config*.

    Resolution order, per provider:

    Slack — a ``slack_webhook`` / ``slack_channel`` override in *config*, else
    the app settings (``SLACK_ALERT_WEBHOOK`` / ``SLACK_BOT_TOKEN`` +
    ``SLACK_ALERT_CHANNEL``).

    WhatsApp — a ``whatsapp_to`` override in *config* (combined with the app's
    send token + phone number id), else the app settings
    (``WHATSAPP_ALERT_RECIPIENT``).

    Channels with no usable credentials are skipped (``get_channel`` returns a
    ``NullChannel`` which we drop).  When nothing is configured the result is an
    empty list ⇒ ``notify_flow_run`` becomes a no-op.

    Returns
    -------
    list[Channel]
        Configured channels (never includes the placeholder ``NullChannel``).
    """
    from app.notify.channels import NullChannel, get_channel  # noqa: PLC0415

    channels: list[Any] = []

    settings = None
    try:
        from app.config import get_settings  # noqa: PLC0415

        settings = get_settings()
    except Exception as exc:  # noqa: BLE001
        logger.debug("notify: settings unavailable for channel resolution: %s", exc)

    def _s(attr: str) -> str:
        return str(getattr(settings, attr, "") or "") if settings else ""

    # ── Slack ──────────────────────────────────────────────────────────────
    slack_webhook = config.get("slack_webhook") or _s("SLACK_ALERT_WEBHOOK")
    slack_token = _s("SLACK_BOT_TOKEN")
    slack_channel = config.get("slack_channel") or _s("SLACK_ALERT_CHANNEL")
    if slack_webhook or slack_token:
        ch = get_channel(
            "slack",
            {
                "webhook_url": slack_webhook,
                "bot_token": slack_token,
                "channel": slack_channel,
            },
        )
        if not isinstance(ch, NullChannel):
            channels.append(ch)

    # ── WhatsApp ───────────────────────────────────────────────────────────
    wa_to = config.get("whatsapp_to") or _s("WHATSAPP_ALERT_RECIPIENT")
    wa_token = _s("WHATSAPP_SEND_TOKEN")
    wa_phone_id = _s("WHATSAPP_PHONE_NUMBER_ID")
    if wa_to and wa_token and wa_phone_id:
        ch = get_channel(
            "whatsapp",
            {
                "token": wa_token,
                "phone_number_id": wa_phone_id,
                "recipient": wa_to,
            },
        )
        if not isinstance(ch, NullChannel):
            channels.append(ch)

    return channels


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------


def notify_flow_run(
    event: dict[str, Any],
    *,
    config: dict[str, Any] | None = None,
    channels: list[Any] | None = None,
    image_png: bytes | None = None,
) -> int:
    """Format *event* and deliver it to every configured channel.

    Best-effort: a failure in any single channel is logged and swallowed so it
    can never break the caller (the flow engine).  Returns the number of
    channels that accepted the message.

    Parameters
    ----------
    event:
        Flow-run event dict (see :func:`format_flow_alert`).
    config:
        Resolved alert config — used to build channels when *channels* is not
        given.  Channel overrides (slack_channel/webhook, whatsapp_to) are read
        from here.
    channels:
        Explicit channel list (used by tests).  When given, *config* is ignored
        for channel resolution.
    image_png:
        Optional PNG attachment.

    Returns
    -------
    int
        Count of channels that the message was sent to.
    """
    cfg = config or {}
    _channels = channels if channels is not None else channels_for(cfg)
    if not _channels:
        logger.debug("notify_flow_run: no channels configured — no-op.")
        return 0

    text = format_flow_alert(event)
    sent = 0
    for ch in _channels:
        try:
            ch.send(text, image_png)
            sent += 1
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "notify_flow_run: channel %s failed to deliver: %s",
                type(ch).__name__,
                exc,
            )
    return sent
