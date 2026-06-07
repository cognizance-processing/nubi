"""Access-token denylist — provider pattern mirroring app.connectors.secret_store.

Two implementations
-------------------
PgTokenDenylist       — asyncpg-backed; stores revoked JTIs in revoked_tokens table.
InMemoryTokenDenylist — dict-backed; for tests (no DB required).

Provider
--------
The module-level singleton is obtained via ``get_token_denylist()``.  Tests
swap in an ``InMemoryTokenDenylist`` via ``set_token_denylist_for_tests()``.

Table schema (created by migration 0010_token_denylist.sql)
-----------------------------------------------------------
    revoked_tokens(
        jti         text        PRIMARY KEY,
        expires_at  timestamptz NOT NULL,
        revoked_at  timestamptz NOT NULL DEFAULT now()
    )

Security contract
-----------------
- Every access token minted by ``mint_access_token`` carries a unique ``jti``.
- On logout, the caller's access-token ``jti`` is inserted here.
- Every authenticated request calls ``is_revoked(jti)`` after JWT signature
  verification; a match yields an immediate 401.
- Rows are pruned by ``purge_expired()`` once ``expires_at`` has passed
  (the JWT is already expired at that point so the row is no longer needed).
- Fail-safe: if the DB is unavailable the default implementation (InMemory)
  is used so the existing test suite (no DB) remains green.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


# ---------------------------------------------------------------------------
# Abstract interface (structural duck-typing — no ABC overhead)
# ---------------------------------------------------------------------------

class TokenDenylist:
    """Interface for access-token denylist storage.

    Concrete implementations must provide revoke / is_revoked / purge_expired.
    """

    async def revoke(self, jti: str, expires_at: datetime) -> None:
        """Mark *jti* as revoked until *expires_at*.

        Parameters
        ----------
        jti:
            The JWT ID claim from the access token.
        expires_at:
            The token's own ``exp`` expressed as a timezone-aware datetime.
            The row is eligible for purging after this time.
        """
        raise NotImplementedError

    async def is_revoked(self, jti: str) -> bool:
        """Return True if *jti* is in the denylist.

        Parameters
        ----------
        jti:
            The JWT ID claim to check.
        """
        raise NotImplementedError

    async def purge_expired(self) -> int:
        """Delete denylist entries whose ``expires_at`` is in the past.

        Returns
        -------
        int
            Number of rows deleted.
        """
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Postgres implementation
# ---------------------------------------------------------------------------

class PgTokenDenylist(TokenDenylist):
    """asyncpg-backed token denylist using the revoked_tokens table."""

    async def revoke(self, jti: str, expires_at: datetime) -> None:
        """Insert *jti* into the denylist (idempotent via ON CONFLICT DO NOTHING)."""
        from app.db import execute  # local import to avoid circular at module load

        await execute(
            """
            INSERT INTO revoked_tokens (jti, expires_at)
            VALUES ($1, $2)
            ON CONFLICT (jti) DO NOTHING
            """,
            jti,
            expires_at,
        )

    async def is_revoked(self, jti: str) -> bool:
        """Return True if a row with this jti exists in the table."""
        from app.db import fetchrow  # local import

        row = await fetchrow(
            "SELECT jti FROM revoked_tokens WHERE jti = $1",
            jti,
        )
        return row is not None

    async def purge_expired(self) -> int:
        """Delete all rows whose expires_at is in the past; return row count."""
        from app.db import execute  # local import

        status = await execute(
            "DELETE FROM revoked_tokens WHERE expires_at < now()",
        )
        # asyncpg returns e.g. "DELETE 3" — parse the count.
        try:
            return int(status.split()[-1])
        except (IndexError, ValueError):
            return 0


# ---------------------------------------------------------------------------
# In-memory implementation (tests)
# ---------------------------------------------------------------------------

class InMemoryTokenDenylist(TokenDenylist):
    """Dict-backed token denylist for tests.

    Stores (jti -> expires_at) pairs in memory.  No DB required.

    Usage in tests::

        from app.auth.denylist import InMemoryTokenDenylist, set_token_denylist_for_tests
        store = InMemoryTokenDenylist()
        set_token_denylist_for_tests(store)
    """

    def __init__(self) -> None:
        # key: jti -> expires_at (datetime)
        self._store: dict[str, datetime] = {}

    def reset(self) -> None:
        """Clear all stored entries."""
        self._store.clear()

    async def revoke(self, jti: str, expires_at: datetime) -> None:
        """Record the revocation in the in-memory store (idempotent)."""
        self._store[jti] = expires_at

    async def is_revoked(self, jti: str) -> bool:
        """Return True if the jti is present (regardless of expiry — JWT is still live)."""
        return jti in self._store

    async def purge_expired(self) -> int:
        """Remove entries whose expires_at is in the past; return count deleted."""
        now = datetime.now(tz=timezone.utc)
        expired = [jti for jti, exp in self._store.items() if exp < now]
        for jti in expired:
            del self._store[jti]
        return len(expired)


# ---------------------------------------------------------------------------
# Provider singleton
# ---------------------------------------------------------------------------

_denylist: TokenDenylist | None = None


def set_token_denylist_for_tests(store: TokenDenylist | None) -> None:
    """Inject a test double (or reset to default PgTokenDenylist by passing None).

    Parameters
    ----------
    store:
        An ``InMemoryTokenDenylist`` instance for tests, or ``None`` to restore
        the default production ``PgTokenDenylist``.
    """
    global _denylist
    _denylist = store


def get_token_denylist() -> TokenDenylist:
    """Return the active TokenDenylist singleton.

    Lazily instantiates a ``PgTokenDenylist`` on first call if no override has
    been set via ``set_token_denylist_for_tests()``.

    Returns
    -------
    TokenDenylist
        The active denylist implementation.
    """
    global _denylist
    if _denylist is None:
        _denylist = PgTokenDenylist()
    return _denylist
