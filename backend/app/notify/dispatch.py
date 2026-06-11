"""The single dispatch path for alerts — :func:`notify_event`.

Every alert in the system (watch breaches, flow runs, shares, …) flows through
ONE call so it lands in three places at once:

1. The **in-app feed** — a ``notifications`` row (broadcast when ``user_ids`` is
   None; otherwise one targeted row per user).
2. **Web Push** — to every push subscription of the addressed users (or, for a
   broadcast, to every subscription in the org). Dead endpoints (404/410) are
   pruned.
3. The org's **channels** (Slack/Teams/email/…) via Agent A's
   ``app.notify.integrations.channels_for_org`` seam.

Best-effort contract
--------------------
The feed write is the primary effect; push + channel fan-out are strictly
best-effort. A failure in any one (a dead channel, a missing integrations
module, a push error) is logged and swallowed — :func:`notify_event` never
raises. The integrations seam is imported **lazily** so import order between this
module and Agent A's is irrelevant; if that module/function isn't importable yet
we treat the org as having no channels and continue.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger("nubi.dispatch")


def _event_field(event: dict[str, Any], key: str, default: Any = None) -> Any:
    value = event.get(key)
    return value if value is not None else default


async def _write_feed(
    org_id: str, event: dict[str, Any], user_ids: Optional[list[str]]
) -> list[dict[str, Any]]:
    """Write notification row(s). Returns the created rows (may be empty on error)."""
    from app.notify.notifications import get_notification_store

    store = get_notification_store()
    common = {
        "type": str(_event_field(event, "type", "system")),
        "title": str(_event_field(event, "title", "")),
        "body": str(_event_field(event, "body", "")),
        "severity": str(_event_field(event, "severity", "info")),
        "link": _event_field(event, "link"),
        "metadata": _event_field(event, "metadata", {}) or {},
    }
    rows: list[dict[str, Any]] = []
    try:
        if user_ids is None:
            rows.append(await store.create(org_id, user_id=None, **common))
        else:
            for uid in user_ids:
                rows.append(await store.create(org_id, user_id=str(uid), **common))
    except Exception as exc:  # noqa: BLE001 — feed write best-effort like the rest.
        logger.warning("notify_event: feed write failed for org %s: %s", org_id, exc)
    return rows


async def _send_push(
    org_id: str, event: dict[str, Any], user_ids: Optional[list[str]]
) -> None:
    """Fan out Web Push to the addressed users (whole org for a broadcast)."""
    from app.notify.push import (
        PUSH_GONE,
        get_push_store,
        push_configured,
        send_push,
    )

    if not push_configured():
        return  # No VAPID keys — skip the subscription lookup entirely.

    store = get_push_store()
    try:
        if user_ids is None:
            # Broadcast: target every member with a subscription. Without a
            # member list here, we resolve org subscriptions directly.
            subs = await _list_org_subscriptions(org_id)
        else:
            subs = await store.list_for_users(org_id, [str(u) for u in user_ids])
    except Exception as exc:  # noqa: BLE001
        logger.warning("notify_event: push lookup failed for org %s: %s", org_id, exc)
        return

    payload = {
        "type": _event_field(event, "type", "system"),
        "title": _event_field(event, "title", ""),
        "body": _event_field(event, "body", ""),
        "severity": _event_field(event, "severity", "info"),
        "link": _event_field(event, "link"),
    }
    for sub in subs:
        try:
            result = send_push(sub, payload)
            if result == PUSH_GONE:
                await store.delete(sub["endpoint"])
        except Exception as exc:  # noqa: BLE001 — one bad sub never aborts the loop.
            logger.warning("notify_event: push to %s failed: %s", sub.get("endpoint"), exc)


async def _list_org_subscriptions(org_id: str) -> list[dict[str, Any]]:
    """Return every push subscription in *org_id* (broadcast fan-out helper)."""
    from app.notify.push import InMemoryPushStore, PgPushStore, _public_sub, get_push_store

    store = get_push_store()
    if isinstance(store, InMemoryPushStore):
        return [
            _public_sub(r)
            for r in store._store.values()  # type: ignore[attr-defined]
            if r["org_id"] == str(org_id)
        ]
    if isinstance(store, PgPushStore):
        from app.db import fetch

        rows = await fetch(
            """
            SELECT id, user_id, org_id, endpoint, p256dh, auth, user_agent
            FROM push_subscriptions WHERE org_id = $1::uuid
            """,
            org_id,
        )
        return [_public_sub(dict(r)) for r in rows]
    return []


def _channels_for_org_lazy(org_id: str):
    """Resolve Agent A's channels_for_org lazily; return [] if unavailable.

    Imported at call time so module load order between this module and
    ``app.notify.integrations`` does not matter, and a not-yet-shipped seam
    simply yields no channels instead of an import error.
    """
    try:
        from app.notify.integrations import channels_for_org  # type: ignore
    except Exception:  # noqa: BLE001 — module/function not present yet.
        return None
    return channels_for_org


async def _fan_out_channels(org_id: str, event: dict[str, Any]) -> None:
    """Send the event text to each of the org's configured channels."""
    resolver = _channels_for_org_lazy(org_id)
    if resolver is None:
        return
    try:
        result = resolver(org_id)
        channels = await result if hasattr(result, "__await__") else result
    except Exception as exc:  # noqa: BLE001
        logger.warning("notify_event: channels_for_org failed for %s: %s", org_id, exc)
        return

    text = _format_channel_text(event)
    for channel in channels or []:
        try:
            send = channel.send(text)
            if hasattr(send, "__await__"):
                await send
        except Exception as exc:  # noqa: BLE001 — a dead channel never aborts.
            logger.warning("notify_event: channel send failed for %s: %s", org_id, exc)


def _format_channel_text(event: dict[str, Any]) -> str:
    title = str(_event_field(event, "title", "")).strip()
    body = str(_event_field(event, "body", "")).strip()
    if title and body:
        return f"{title}\n{body}"
    return title or body or "Notification"


async def notify_event(
    org_id: str,
    event: dict[str, Any],
    *,
    user_ids: Optional[list[str]] = None,
) -> list[dict[str, Any]]:
    """Dispatch *event* for *org_id* to the feed + Web Push + channels.

    Parameters
    ----------
    org_id:
        The tenant the notification belongs to.
    event:
        ``{type, title, body, severity, link, metadata}`` (all but ``type`` /
        ``title`` optional).
    user_ids:
        ``None`` ⇒ an org broadcast (one feed row, visible to all members,
        push to all org subscriptions). A list ⇒ one targeted feed row per
        user + push to those users only.

    Returns the created notification rows. Push + channel fan-out are
    best-effort and never cause this to raise.
    """
    rows = await _write_feed(org_id, event, user_ids)
    await _send_push(org_id, event, user_ids)
    await _fan_out_channels(org_id, event)
    return rows
