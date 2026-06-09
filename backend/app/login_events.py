"""Best-effort login-event recording for login analytics.

``record_login_event`` inserts one ``login_events`` row per successful
login/registration.  It is strictly best-effort: ANY failure (missing table,
DB outage, bad input) is swallowed so authentication is never blocked by
analytics.

The DB helpers are referenced lazily via the ``app.db`` module attribute (not
``from app.db import execute``) so test fixtures that patch ``app.db.execute``
are honoured.
"""

from __future__ import annotations

from fastapi import Request

from app import db


def client_ip_from_request(request: Request) -> str | None:
    """First X-Forwarded-For hop, falling back to the socket peer address."""
    forwarded_for = request.headers.get("x-forwarded-for")
    if forwarded_for:
        first_hop = forwarded_for.split(",")[0].strip()
        if first_hop:
            return first_hop
    if request.client:
        return request.client.host
    return None


async def record_login_event(user_id: str, request: Request) -> None:
    """Insert a login_events row for *user_id*. Never raises."""
    try:
        await db.execute(
            """
            INSERT INTO login_events (user_id, ip, user_agent)
            VALUES ($1::uuid, $2, $3)
            """,
            str(user_id),
            client_ip_from_request(request),
            request.headers.get("user-agent"),
        )
    except Exception:  # noqa: BLE001 — analytics must never break auth
        pass
