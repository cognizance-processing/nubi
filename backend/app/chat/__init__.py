"""In-app chat for Nubi.

Nubi is embedded BI, not a chat-ops platform: chat is the in-app assistant
(``app/routes/chat.py``'s ``POST /chat/stream``) only — there is no inbound
Slack/WhatsApp webhook gateway. The embedding host owns any chat-platform
integrations it wants.

Public API
----------
render_chart_png(chart_spec, rows) -> bytes
    Render a chart spec + data to PNG bytes (matplotlib, Agg backend).
"""

from app.chat.render import render_chart_png

__all__ = [
    "render_chart_png",
]
