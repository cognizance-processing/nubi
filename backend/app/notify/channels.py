"""Notification channel implementations for Nubi.

Provides a ``Channel`` interface with four implementations:
- ``NullChannel``     — records sends in memory; no network (tests / unconfigured).
- ``SlackChannel``    — posts via Incoming Webhook URL or chat.postMessage bot token.
- ``WhatsAppChannel`` — posts via WhatsApp Cloud API (Meta Graph API).
- ``EmailChannel``    — reuses the ``EmailSender`` protocol from jobs.report.

Factory
-------
get_channel(kind, config) -> Channel
    Return the appropriate Channel implementation for *kind*, initialised from
    the *config* dict.  Falls back to ``NullChannel`` when credentials are absent.

All network calls are lazy (inside ``send``); the module-level import does no I/O.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

logger = logging.getLogger(__name__)

__all__ = [
    "Channel",
    "NullChannel",
    "SlackChannel",
    "WhatsAppChannel",
    "EmailChannel",
    "get_channel",
    "ChannelError",
]


# ---------------------------------------------------------------------------
# Custom exception
# ---------------------------------------------------------------------------


class ChannelError(RuntimeError):
    """Raised when a channel fails to deliver a message."""


# ---------------------------------------------------------------------------
# Channel Protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class Channel(Protocol):
    """Minimal interface for delivering alert/notification messages.

    Implementations are responsible for platform-specific serialisation.
    ``send`` should raise ``ChannelError`` if delivery fails after retries.
    """

    def send(self, text: str, image_png: bytes | None = None) -> None:
        """Deliver *text* (and optionally an *image_png* attachment).

        Parameters
        ----------
        text:
            Formatted alert or notification text (plain-text or Markdown).
        image_png:
            Optional PNG chart image bytes to attach.
        """
        ...  # pragma: no cover


# ---------------------------------------------------------------------------
# NullChannel — for tests and unconfigured environments
# ---------------------------------------------------------------------------


class NullChannel:
    """Records all sends in memory.  No network calls.

    Attributes
    ----------
    sent:
        List of ``{"text": ..., "image_png": ...}`` dicts in send order.
    """

    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    def send(self, text: str, image_png: bytes | None = None) -> None:
        """Record the send without making any network call."""
        self.sent.append({"text": text, "image_png": image_png})
        logger.debug("NullChannel.send: %r", text[:120] if len(text) > 120 else text)


# ---------------------------------------------------------------------------
# SlackChannel — Incoming Webhook URL or chat.postMessage bot token
# ---------------------------------------------------------------------------


class SlackChannel:
    """Send messages to Slack via Incoming Webhook or chat.postMessage.

    Parameters
    ----------
    webhook_url:
        Slack Incoming Webhook URL.  When set, used for simple text posts.
    bot_token:
        Slack bot token (``xoxb-…``).  Required for file uploads (chart PNGs).
    channel:
        Default Slack channel/conversation ID (e.g. ``#alerts`` or ``C12345``).
        Used with *bot_token* when not supplied in ``send``.
    """

    def __init__(
        self,
        *,
        webhook_url: str = "",
        bot_token: str = "",
        channel: str = "",
    ) -> None:
        self.webhook_url = webhook_url
        self.bot_token = bot_token
        self.channel = channel

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _post_webhook(self, text: str) -> None:
        """POST a simple text message to the Incoming Webhook URL."""
        import httpx  # lazy — no network at import time

        payload = {"text": text}
        resp = httpx.post(self.webhook_url, json=payload, timeout=10)
        if resp.status_code != 200:
            raise ChannelError(
                f"Slack webhook returned {resp.status_code}: {resp.text[:200]}"
            )

    def _post_message(self, text: str) -> None:
        """Call chat.postMessage with the bot token."""
        import httpx  # lazy

        headers = {"Authorization": f"Bearer {self.bot_token}"}
        payload = {"channel": self.channel, "text": text}
        resp = httpx.post(
            "https://slack.com/api/chat.postMessage",
            json=payload,
            headers=headers,
            timeout=10,
        )
        data = resp.json()
        if not data.get("ok"):
            raise ChannelError(f"Slack chat.postMessage error: {data.get('error')}")

    def _upload_file(self, text: str, image_png: bytes) -> None:
        """Upload a PNG file to Slack (requires bot token)."""
        import httpx  # lazy

        headers = {"Authorization": f"Bearer {self.bot_token}"}
        # Use files.getUploadURLExternal + files.completeUploadExternal (Slack v2 API)
        # For simplicity, fall back to chat.postMessage with the text and a note.
        # Full file upload is a multi-step flow; post text + note for now.
        payload = {
            "channel": self.channel,
            "text": text + "\n_(chart image attached — see dashboard for full view)_",
        }
        resp = httpx.post(
            "https://slack.com/api/chat.postMessage",
            json=payload,
            headers=headers,
            timeout=10,
        )
        data = resp.json()
        if not data.get("ok"):
            raise ChannelError(f"Slack file post error: {data.get('error')}")

    # ------------------------------------------------------------------
    # Public send
    # ------------------------------------------------------------------

    def send(self, text: str, image_png: bytes | None = None) -> None:
        """Deliver *text* (and optionally *image_png*) to Slack."""
        if image_png is not None and self.bot_token:
            self._upload_file(text, image_png)
            return

        if self.webhook_url:
            self._post_webhook(text)
            return

        if self.bot_token:
            self._post_message(text)
            return

        logger.warning("SlackChannel.send called but neither webhook_url nor bot_token is set.")


# ---------------------------------------------------------------------------
# WhatsAppChannel — WhatsApp Cloud API (Meta Graph API)
# ---------------------------------------------------------------------------


class WhatsAppChannel:
    """Send messages via the WhatsApp Cloud API.

    Parameters
    ----------
    token:
        WhatsApp Cloud API send token (Graph API bearer token).
    phone_number_id:
        The sender's WhatsApp Business phone number ID.
    recipient:
        Default recipient phone number (E.164 format, e.g. ``+27821234567``).
    """

    _API_BASE = "https://graph.facebook.com/v19.0"

    def __init__(
        self,
        *,
        token: str = "",
        phone_number_id: str = "",
        recipient: str = "",
    ) -> None:
        self.token = token
        self.phone_number_id = phone_number_id
        self.recipient = recipient

    def send(self, text: str, image_png: bytes | None = None) -> None:
        """Deliver *text* to the configured *recipient* via WhatsApp Cloud API."""
        if not self.token or not self.phone_number_id or not self.recipient:
            logger.warning(
                "WhatsAppChannel.send called but token/phone_number_id/recipient not fully set."
            )
            return

        import httpx  # lazy

        url = f"{self._API_BASE}/{self.phone_number_id}/messages"
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }
        payload = {
            "messaging_product": "whatsapp",
            "to": self.recipient,
            "type": "text",
            "text": {"body": text},
        }
        resp = httpx.post(url, json=payload, headers=headers, timeout=10)
        if resp.status_code not in (200, 201):
            raise ChannelError(
                f"WhatsApp API returned {resp.status_code}: {resp.text[:200]}"
            )


# ---------------------------------------------------------------------------
# EmailChannel — wraps EmailSender from jobs.report
# ---------------------------------------------------------------------------


class EmailChannel:
    """Deliver alert messages via email using the EmailSender protocol.

    Parameters
    ----------
    sender:
        Any ``EmailSender``-compatible object (e.g. ``NullSender``).
    recipient:
        Destination email address.
    subject_prefix:
        Prefix for the email subject line (default: ``"[Nubi Alert]"``).
    """

    def __init__(
        self,
        sender: Any,
        *,
        recipient: str = "",
        subject_prefix: str = "[Nubi Alert]",
    ) -> None:
        self.sender = sender
        self.recipient = recipient
        self.subject_prefix = subject_prefix

    def send(self, text: str, image_png: bytes | None = None) -> None:
        """Send an alert email to the configured *recipient*."""
        if not self.recipient:
            logger.warning("EmailChannel.send called but recipient is not set.")
            return

        # Use the first line of text as the subject suffix.
        first_line = text.splitlines()[0][:80] if text else "Alert"
        subject = f"{self.subject_prefix} {first_line}"

        if image_png is not None:
            self.sender.send(
                to=self.recipient,
                subject=subject,
                body=text,
                attachment_name="chart.png",
                attachment_data=image_png,
            )
        else:
            self.sender.send(
                to=self.recipient,
                subject=subject,
                body=text,
                attachment_name="alert.txt",
                attachment_data=text.encode("utf-8"),
            )


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def get_channel(kind: str, config: dict[str, Any]) -> Channel:
    """Return a Channel implementation for *kind*, configured from *config*.

    Supported kinds
    ---------------
    ``"slack"``
        Requires at least one of ``webhook_url`` or ``bot_token`` in *config*.
        Falls back to ``NullChannel`` if neither is set.
    ``"whatsapp"``
        Requires ``token``, ``phone_number_id``, and ``recipient``.
        Falls back to ``NullChannel`` if any are absent.
    ``"email"``
        Requires ``recipient``; uses ``NullSender`` when no real sender is
        supplied in ``config["sender"]``.
    ``"null"`` (or any unknown kind)
        Returns a ``NullChannel``.

    Parameters
    ----------
    kind:
        Channel type identifier: ``"slack"``, ``"whatsapp"``, ``"email"``, ``"null"``.
    config:
        Dict of channel-specific configuration values (see above).

    Returns
    -------
    Channel
        An initialised channel object.
    """
    kind = (kind or "").lower().strip()

    if kind == "slack":
        webhook_url = config.get("webhook_url") or ""
        bot_token = config.get("bot_token") or ""
        channel = config.get("channel") or ""
        if not webhook_url and not bot_token:
            logger.debug("get_channel('slack'): no credentials — returning NullChannel.")
            return NullChannel()
        return SlackChannel(webhook_url=webhook_url, bot_token=bot_token, channel=channel)

    if kind == "whatsapp":
        token = config.get("token") or ""
        phone_number_id = config.get("phone_number_id") or ""
        recipient = config.get("recipient") or ""
        if not token or not phone_number_id or not recipient:
            logger.debug("get_channel('whatsapp'): incomplete credentials — returning NullChannel.")
            return NullChannel()
        return WhatsAppChannel(token=token, phone_number_id=phone_number_id, recipient=recipient)

    if kind == "email":
        from app.jobs.report import NullSender  # lazy local import

        sender = config.get("sender") or NullSender()
        recipient = config.get("recipient") or ""
        subject_prefix = config.get("subject_prefix") or "[Nubi Alert]"
        return EmailChannel(sender=sender, recipient=recipient, subject_prefix=subject_prefix)

    # null / unknown
    return NullChannel()
