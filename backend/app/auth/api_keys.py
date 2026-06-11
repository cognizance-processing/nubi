"""Long-lived API keys for CLI / CI authentication (files-as-code F-6).

An API key is an opaque, non-expiring credential a user mints for the Nubi CLI
or a CI pipeline. Unlike the short-lived JWT access token (15-minute TTL) it
stays valid until explicitly revoked, so unattended pipelines don't have to run
a refresh dance.

Format & storage
----------------
The raw key is ``nubi_ak_<43-char-base64url>`` (256 bits of entropy). The
``nubi_ak_`` prefix lets :mod:`app.auth.deps` tell an API key from a JWT without
a decode attempt. Only the SHA-256 hex digest is ever persisted (the same
one-way discipline as the refresh-token ``sessions`` table) — the raw key is
returned to the caller EXACTLY ONCE at mint time and never again.

Scoping
-------
A key is bound to the minting user AND their org at mint time. On every
authenticated request the org/user binding is read straight back from the
``api_keys`` row, so a key can never act outside the org it was minted for —
even if the user later joins other orgs.

Provider pattern
----------------
Mirrors :mod:`app.auth.denylist` / :mod:`app.connectors.secret_store`: a
module-level singleton obtained via :func:`get_api_key_store`. Tests swap in an
:class:`InMemoryApiKeyStore` via :func:`set_api_key_store_for_tests` so the
suite needs no live DB.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

#: Opaque-key prefix. Used to distinguish an API key from a JWT access token in
#: the bearer-auth path WITHOUT attempting a (failing) JWT decode first.
API_KEY_PREFIX = "nubi_ak_"


def hash_api_key(raw: str) -> str:
    """Return the SHA-256 hex digest of *raw* (UTF-8 encoded).

    The digest is the only representation ever stored; the raw key is never
    persisted. Matches the ``sessions.token_hash`` discipline.
    """
    return hashlib.sha256(raw.encode()).hexdigest()


def looks_like_api_key(token: str) -> bool:
    """Return True if *token* has the opaque API-key prefix."""
    return isinstance(token, str) and token.startswith(API_KEY_PREFIX)


def generate_api_key() -> str:
    """Generate a new cryptographically-secure opaque API key string."""
    return f"{API_KEY_PREFIX}{secrets.token_urlsafe(32)}"


# ---------------------------------------------------------------------------
# Interface
# ---------------------------------------------------------------------------


class ApiKeyStore:
    """Interface for API-key persistence (structural duck-typing, no ABC)."""

    async def create(self, user_id: str, org_id: str, name: str) -> tuple[str, dict[str, Any]]:
        """Mint a new key. Return ``(raw_key, row)``; *raw_key* is shown once."""
        raise NotImplementedError

    async def resolve(self, raw_key: str) -> dict[str, Any] | None:
        """Return the non-revoked key row for *raw_key*, or ``None``."""
        raise NotImplementedError

    async def list_for_org(self, user_id: str, org_id: str) -> list[dict[str, Any]]:
        """Return the caller's keys in *org_id* (no token material)."""
        raise NotImplementedError

    async def revoke(self, key_id: str, user_id: str, org_id: str) -> bool:
        """Revoke a key owned by *user_id* in *org_id*. Return True if revoked."""
        raise NotImplementedError


def _public_row(row: dict[str, Any]) -> dict[str, Any]:
    """Return the listing-safe shape of an api_keys row (never the hash)."""
    created_at = row.get("created_at")
    last_used_at = row.get("last_used_at")
    revoked_at = row.get("revoked_at")

    def _iso(value: Any) -> Any:
        return value.isoformat() if hasattr(value, "isoformat") else value

    return {
        "id": str(row["id"]),
        "name": row.get("name") or "CLI token",
        "last_four": row.get("last_four"),
        "created_at": _iso(created_at),
        "last_used_at": _iso(last_used_at),
        "revoked_at": _iso(revoked_at),
    }


# ---------------------------------------------------------------------------
# Postgres implementation
# ---------------------------------------------------------------------------


class PgApiKeyStore(ApiKeyStore):
    """asyncpg-backed API-key store using the ``api_keys`` table."""

    async def create(self, user_id: str, org_id: str, name: str) -> tuple[str, dict[str, Any]]:
        from app.db import fetchrow  # local import to avoid circular load

        raw = generate_api_key()
        token_hash = hash_api_key(raw)
        key_id = str(uuid.uuid4())
        last_four = raw[-4:]
        row = await fetchrow(
            """
            INSERT INTO api_keys (id, user_id, org_id, token_hash, name, last_four)
            VALUES ($1, $2, $3, $4, $5, $6)
            RETURNING id, user_id, org_id, name, last_four, revoked_at,
                      last_used_at, created_at
            """,
            key_id,
            user_id,
            org_id,
            token_hash,
            (name or "CLI token").strip() or "CLI token",
            last_four,
        )
        return raw, dict(row) if row is not None else {"id": key_id, "name": name}

    async def resolve(self, raw_key: str) -> dict[str, Any] | None:
        from app.db import execute, fetchrow  # local import

        token_hash = hash_api_key(raw_key)
        row = await fetchrow(
            """
            SELECT id, user_id, org_id, name, last_four, revoked_at,
                   last_used_at, created_at
            FROM api_keys
            WHERE token_hash = $1 AND revoked_at IS NULL
            """,
            token_hash,
        )
        if row is None:
            return None
        # Best-effort last-used stamp; never block auth on it.
        try:
            await execute(
                "UPDATE api_keys SET last_used_at = now() WHERE id = $1::uuid",
                str(row["id"]),
            )
        except Exception:  # noqa: BLE001
            pass
        return dict(row)

    async def list_for_org(self, user_id: str, org_id: str) -> list[dict[str, Any]]:
        from app.db import fetch  # local import

        rows = await fetch(
            """
            SELECT id, user_id, org_id, name, last_four, revoked_at,
                   last_used_at, created_at
            FROM api_keys
            WHERE user_id = $1::uuid AND org_id = $2::uuid
            ORDER BY created_at DESC
            """,
            user_id,
            org_id,
        )
        return [_public_row(dict(r)) for r in rows]

    async def revoke(self, key_id: str, user_id: str, org_id: str) -> bool:
        from app.db import execute  # local import

        status = await execute(
            """
            UPDATE api_keys
            SET revoked_at = now()
            WHERE id = $1::uuid AND user_id = $2::uuid AND org_id = $3::uuid
              AND revoked_at IS NULL
            """,
            key_id,
            user_id,
            org_id,
        )
        try:
            return int(status.split()[-1]) > 0
        except (IndexError, ValueError, AttributeError):
            return False


# ---------------------------------------------------------------------------
# In-memory implementation (tests)
# ---------------------------------------------------------------------------


class InMemoryApiKeyStore(ApiKeyStore):
    """Dict-backed API-key store for tests (no DB)."""

    def __init__(self) -> None:
        # key_id -> row dict (carries token_hash internally; never returned raw)
        self._store: dict[str, dict[str, Any]] = {}

    def reset(self) -> None:
        self._store.clear()

    async def create(self, user_id: str, org_id: str, name: str) -> tuple[str, dict[str, Any]]:
        raw = generate_api_key()
        key_id = str(uuid.uuid4())
        now = datetime.now(tz=timezone.utc)
        row = {
            "id": key_id,
            "user_id": str(user_id),
            "org_id": str(org_id),
            "token_hash": hash_api_key(raw),
            "name": (name or "CLI token").strip() or "CLI token",
            "last_four": raw[-4:],
            "revoked_at": None,
            "last_used_at": None,
            "created_at": now,
        }
        self._store[key_id] = row
        return raw, dict(row)

    async def resolve(self, raw_key: str) -> dict[str, Any] | None:
        token_hash = hash_api_key(raw_key)
        for row in self._store.values():
            if row["token_hash"] == token_hash and row["revoked_at"] is None:
                row["last_used_at"] = datetime.now(tz=timezone.utc)
                return dict(row)
        return None

    async def list_for_org(self, user_id: str, org_id: str) -> list[dict[str, Any]]:
        rows = [
            r
            for r in self._store.values()
            if str(r["user_id"]) == str(user_id) and str(r["org_id"]) == str(org_id)
        ]
        rows.sort(key=lambda r: r["created_at"], reverse=True)
        return [_public_row(r) for r in rows]

    async def revoke(self, key_id: str, user_id: str, org_id: str) -> bool:
        row = self._store.get(str(key_id))
        if row is None:
            return False
        if str(row["user_id"]) != str(user_id) or str(row["org_id"]) != str(org_id):
            return False
        if row["revoked_at"] is not None:
            return False
        row["revoked_at"] = datetime.now(tz=timezone.utc)
        return True


# ---------------------------------------------------------------------------
# Provider singleton
# ---------------------------------------------------------------------------

_store: Optional[ApiKeyStore] = None


def set_api_key_store_for_tests(store: ApiKeyStore | None) -> None:
    """Inject a test double (or pass None to restore the default Pg store)."""
    global _store
    _store = store


def get_api_key_store() -> ApiKeyStore:
    """Return the active :class:`ApiKeyStore` singleton (lazy Pg default)."""
    global _store
    if _store is None:
        _store = PgApiKeyStore()
    return _store
