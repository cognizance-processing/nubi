"""Opaque refresh-token session management with family-based reuse detection.

Each refresh token is a 256-bit random string (32 bytes, URL-safe base64).
Only the SHA-256 hex digest is ever stored in the DB.

Session families
----------------
When a refresh token is issued for the first time a ``family_id`` UUID is
assigned.  Every rotation creates a child row pointing at the previous row via
``parent_id``.  Presenting a token that has already been rotated (its row has
``revoked_at`` set from a previous rotation) means the entire family is
compromised — every row in the family is revoked immediately.

Public API
----------
issue_refresh(user_id, family_id=None, parent_id=None, user_agent, ip)
    -> (raw_token: str, expires_at: datetime)

rotate_refresh(raw_token, user_agent, ip)
    -> (new_raw: str, user_id: str, expires_at: datetime)
    Raises AppError("refresh_reuse", 401) if reuse detected; revokes family.

revoke_family(family_id: str) -> None
    Mark all non-revoked tokens in the family as revoked.

revoke_by_token(raw_token: str) -> None
    Revoke the specific session identified by raw_token (if found).
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from app.db import execute, fetchrow, get_connection
from app.errors import AppError

_REFRESH_TTL_DAYS = 30


def _hash_token(raw: str) -> str:
    """Return the SHA-256 hex digest of *raw* (UTF-8 encoded)."""
    return hashlib.sha256(raw.encode()).hexdigest()


def _new_raw_token() -> str:
    """Generate a cryptographically secure 256-bit URL-safe token string."""
    return secrets.token_urlsafe(32)  # 32 bytes → ~43-char base64url string


async def issue_refresh(
    user_id: str,
    *,
    family_id: Optional[str] = None,
    parent_id: Optional[str] = None,
    user_agent: Optional[str] = None,
    ip: Optional[str] = None,
) -> tuple[str, datetime]:
    """Issue a new refresh token and persist the session row.

    Parameters
    ----------
    user_id:
        The owning user's UUID string.
    family_id:
        Existing family UUID string.  Pass ``None`` to start a new family.
    parent_id:
        UUID of the parent session row (rotation chain); ``None`` for root.
    user_agent:
        ``User-Agent`` header value from the request (stored for audit).
    ip:
        Client IP address (stored for audit).

    Returns
    -------
    (raw_token, expires_at)
        The raw opaque token to send to the client, and its expiry datetime.
    """
    raw = _new_raw_token()
    token_hash = _hash_token(raw)
    session_id = str(uuid.uuid4())
    effective_family_id = family_id or str(uuid.uuid4())
    expires_at = datetime.now(tz=timezone.utc) + timedelta(days=_REFRESH_TTL_DAYS)

    await execute(
        """
        INSERT INTO sessions
            (id, user_id, token_hash, family_id, parent_id,
             expires_at, user_agent, ip)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8::inet)
        """,
        session_id,
        user_id,
        token_hash,
        effective_family_id,
        parent_id,
        expires_at,
        user_agent,
        ip,
    )

    return raw, expires_at


async def rotate_refresh(
    raw_token: str,
    *,
    user_agent: Optional[str] = None,
    ip: Optional[str] = None,
) -> tuple[str, str, datetime]:
    """Rotate a refresh token atomically, returning the new token.

    The entire check-and-consume sequence runs inside a single serialisable
    transaction with ``SELECT … FOR UPDATE`` so that two concurrent requests
    presenting the same token cannot both succeed (TOCTOU race eliminated).

    If the session is not found, already revoked, expired, or already has a
    child (token reuse), the whole family is revoked and
    ``AppError("refresh_reuse", 401)`` is raised.

    Parameters
    ----------
    raw_token:
        The raw refresh token presented by the client.
    user_agent:
        ``User-Agent`` header value (stored on the new session row).
    ip:
        Client IP address (stored on the new session row).

    Returns
    -------
    (new_raw, user_id, expires_at)

    Raises
    ------
    AppError("refresh_reuse", 401)
        On token reuse, revocation, or any suspicious condition.
    """
    token_hash = _hash_token(raw_token)

    session_id: Optional[str] = None
    user_id: Optional[str] = None
    family_id: Optional[str] = None

    async with get_connection() as conn:
        async with conn.transaction():
            # Lock the row so a concurrent request with the same token blocks
            # until this transaction commits or rolls back.
            row = await conn.fetchrow(
                """
                SELECT id, user_id, family_id, revoked_at, expires_at
                FROM sessions
                WHERE token_hash = $1
                FOR UPDATE
                """,
                token_hash,
            )

            if row is None:
                # Unknown token — nothing to revoke, just reject.
                raise AppError("refresh_reuse", "Refresh token is invalid.", 401)

            session_id = str(row["id"])
            user_id = str(row["user_id"])
            family_id = str(row["family_id"])
            expires_at_db: datetime = row["expires_at"]

            # ── Reuse / revocation checks ─────────────────────────────────
            already_revoked = row["revoked_at"] is not None
            is_expired = expires_at_db < datetime.now(tz=timezone.utc)

            # Check whether this token already has a child (was already rotated).
            child_row = await conn.fetchrow(
                "SELECT id FROM sessions WHERE parent_id = $1",
                session_id,
            )
            has_child = child_row is not None

            if already_revoked or is_expired or has_child:
                # Compromised family — revoke everything in it within this tx.
                await conn.execute(
                    """
                    UPDATE sessions
                    SET revoked_at = now()
                    WHERE family_id = $1
                      AND revoked_at IS NULL
                    """,
                    family_id,
                )
                raise AppError("refresh_reuse", "Refresh token is invalid.", 401)

            # ── Mark current token consumed (set revoked_at to now) ───────
            await conn.execute(
                "UPDATE sessions SET revoked_at = now() WHERE id = $1",
                session_id,
            )

        # Transaction committed — now issue the replacement token (outside the
        # serialising transaction so we don't hold the row lock during hashing).
    new_raw, new_expires_at = await issue_refresh(
        user_id,
        family_id=family_id,
        parent_id=session_id,
        user_agent=user_agent,
        ip=ip,
    )

    return new_raw, user_id, new_expires_at


async def revoke_family(family_id: str) -> None:
    """Revoke all non-revoked sessions belonging to *family_id*.

    Parameters
    ----------
    family_id:
        The UUID string of the session family to invalidate.
    """
    await execute(
        """
        UPDATE sessions
        SET revoked_at = now()
        WHERE family_id = $1
          AND revoked_at IS NULL
        """,
        family_id,
    )


async def revoke_by_token(raw_token: str) -> None:
    """Revoke the session identified by *raw_token* and its entire family.

    Used during logout to invalidate all sessions in the same family so that
    a stolen refresh token cannot be used after the user logs out.

    Parameters
    ----------
    raw_token:
        The raw opaque token from the cookie.
    """
    token_hash = _hash_token(raw_token)

    row = await fetchrow(
        "SELECT family_id FROM sessions WHERE token_hash = $1",
        token_hash,
    )

    if row is not None:
        await revoke_family(str(row["family_id"]))
