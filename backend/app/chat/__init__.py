"""Conversational gateway for Nubi (M22-A).

Provides headless chat over Slack/WhatsApp with chart-image replies.

Public API
----------
render_chart_png(chart_spec, rows) -> bytes
    Render a chart spec + data to PNG bytes (matplotlib, Agg backend).

handle_inbound(platform, payload, *, provider, transport, claims) -> OutboundMessage
    Normalize an inbound webhook payload, invoke the M21 agent, attach a
    rendered chart PNG if the agent produced one, deliver via transport.

OutboundMessage
    Dataclass: {text: str, image_png: bytes | None}.

ChatTransport (Protocol)
    send(to: str, message: OutboundMessage) -> None.

NullTransport
    Records sends in .sent — use in tests; no network.
"""

from app.chat.gateway import (
    ChatTransport,
    NullTransport,
    OutboundMessage,
    handle_inbound,
)
from app.chat.render import render_chart_png

__all__ = [
    "render_chart_png",
    "handle_inbound",
    "OutboundMessage",
    "ChatTransport",
    "NullTransport",
]
