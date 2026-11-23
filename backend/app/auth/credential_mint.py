"""Minting a long-lived credential without racing a password reset.

``current_user`` proves the caller when the request starts. A password reset
that commits after that, but before the new credential is stored, has already
revoked everything it could see, so the new one would outlive it: an API key or
OAuth grant held by whoever the reset was meant to lock out.

:func:`credential_mint` closes that gap. It re-proves the caller's credential
while holding the ``users`` row, in the transaction that stores the new one:

- the reset (``FOR UPDATE``, see "Lock order" in ``app.auth.sessions``) came
  first: the lock waits for it, and the caller's credential is now refused;
- the mint came first: the reset waits for it, and then revokes the new one.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from asyncpg import Connection
from fastapi import Request

from app.auth.api_keys import get_api_key_store, looks_like_api_key
from app.auth.deps import require_token_after_password_change
from app.auth.jwt import decode_access_token
from app.errors import AppError


@asynccontextmanager
async def credential_mint(request: Request, user_id: str) -> AsyncIterator[Connection]:
    """Open the transaction in which to store a new credential of *user_id*.

    Yields its connection; the new credential must be written on THAT
    connection, or the lock does not cover it. Raises 401 when the request's
    bearer (an access JWT or an API key) is no longer good; any other kind of
    credential cannot mint and is refused too.
    """
    from app.db import get_connection  # noqa: PLC0415

    unauthorized = AppError("unauthorized", "Authentication required.", 401)
    scheme, _, raw = request.headers.get("authorization", "").partition(" ")
    raw = raw.strip()
    if scheme.lower() != "bearer" or not raw:
        raise unauthorized

    async with get_connection() as conn:
        async with conn.transaction():
            user = await conn.fetchrow(
                "SELECT password_changed_at FROM users WHERE id = $1::uuid FOR KEY SHARE",
                user_id,
            )
            if user is None:
                raise unauthorized
            if looks_like_api_key(raw):
                if not await get_api_key_store().is_live(raw, conn):
                    raise unauthorized
            else:
                require_token_after_password_change(decode_access_token(raw), dict(user))
            yield conn
