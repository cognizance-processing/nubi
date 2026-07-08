"""Notification channel implementations for Nubi.

Provides a ``Channel`` interface with two implementations:
- ``NullChannel``  — records sends in memory; no network (tests / unconfigured).
- ``EmailChannel`` — reuses the ``EmailSender`` protocol from jobs.report.

Nubi is embedded BI, not a chat-ops platform — the host application owns its
own notification/chat integrations. Email (scheduled reports + alerts) is the
one outbound channel Nubi ships; everything else is a no-op ``NullChannel``.

Factory
-------
get_channel(kind, config) -> Channel
    Return the appropriate Channel implementation for *kind*, initialised from
    the *config* dict.  Falls back to ``NullChannel`` when credentials are absent
    or *kind* is not ``"email"``.

All network calls are lazy (inside ``send``); the module-level import does no I/O.
"""

from __future__ import annotations

import logging
from typing import Any, Protocol, runtime_checkable

logger = logging.getLogger(__name__)

__all__ = [
    "Channel",
    "NullChannel",
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
    ``"email"``
        Requires ``recipient``; uses ``NullSender`` when no real sender is
        supplied in ``config["sender"]``.
    ``"null"`` (or any unknown/unsupported kind)
        Returns a ``NullChannel``.  Nubi ships email as its one outbound
        channel — the embedding host owns any chat/Slack-style notifications.

    Parameters
    ----------
    kind:
        Channel type identifier: ``"email"`` or ``"null"``.
    config:
        Dict of channel-specific configuration values (see above).

    Returns
    -------
    Channel
        An initialised channel object.
    """
    kind = (kind or "").lower().strip()

    if kind == "email":
        from app.jobs.report import NullSender  # lazy local import

        sender = config.get("sender") or NullSender()
        recipient = config.get("recipient") or ""
        subject_prefix = config.get("subject_prefix") or "[Nubi Alert]"
        return EmailChannel(sender=sender, recipient=recipient, subject_prefix=subject_prefix)

    # null / unknown
    return NullChannel()
