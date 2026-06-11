"""Bridge tokens — control-channel credentials for the reverse-tunnel agent (§7).

A *bridge token* is the credential a bridge agent presents on every tunnel
handshake/heartbeat. It mirrors the proven API-key pattern
(:mod:`app.auth.api_keys`, ``0010_api_keys.sql``) but is **scoped to a BRIDGE
identity, not a user**: a token is bound to ``(org_id, bridge_id)`` at mint time,
so it can only ever authenticate that one bridge in that one org.

Format & storage
----------------
The raw token is ``nubi_br_<43-char-base64url>`` (256 bits of entropy). The
``nubi_br_`` prefix distinguishes a bridge token from a user API key
(``nubi_ak_``) or a JWT. Only the SHA-256 hex digest is ever persisted — the raw
token is returned to the caller EXACTLY ONCE at mint time and never again, the
same one-way discipline as ``api_keys.token_hash`` / ``sessions.token_hash``.

What the token authenticates
----------------------------
The bridge token authenticates the **CONTROL CHANNEL ONLY**. By itself it reads
NO secrets and NO storage: it lets an agent open the tunnel and claim tasks for
its bridge, nothing more. Read access to staged data is granted separately and
ephemerally (see :mod:`app.lakehouse.grants`).

Lifecycle
---------
- **mint**      — create a new active token for ``(org_id, bridge_id)``; raw
                  shown once.
- **validate**  — ``validate(raw) -> (org_id, bridge_id) | None``; returns the
                  binding for a non-revoked, non-expired token, else ``None``.
- **rotate**    — mint a NEW token, then start a grace window on the OLD one
                  (``grace_until = now + grace``). During the grace window BOTH
                  tokens validate, so a running agent can swap its token without
                  a tunnel drop; after the window the old token stops validating.
                  An explicit ``revoke`` of the old token can short-circuit the
                  grace window.
- **revoke**    — set ``revoked_at``; every subsequent ``validate`` fails. The
                  broker drops the live tunnel on the next handshake/heartbeat.

Provider pattern
----------------
Mirrors :mod:`app.auth.api_keys`: a module-level singleton via
:func:`get_bridge_token_store`. Tests swap in :class:`InMemoryBridgeTokenStore`
via :func:`set_bridge_token_store_for_tests`.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

#: Opaque bridge-token prefix. Distinguishes a bridge (control-channel) token
#: from a user API key (``nubi_ak_``) without a decode attempt.
BRIDGE_TOKEN_PREFIX = "nubi_br_"

#: Default grace window for :meth:`BridgeTokenStore.rotate` — both the old and
#: the new token validate during this window so a live agent can swap without a
#: tunnel drop.
DEFAULT_ROTATION_GRACE = timedelta(hours=1)


def hash_bridge_token(raw: str) -> str:
    """Return the SHA-256 hex digest of *raw* — the only stored representation."""
    return hashlib.sha256(raw.encode()).hexdigest()


def looks_like_bridge_token(token: str) -> bool:
    """Return True if *token* carries the opaque bridge-token prefix."""
    return isinstance(token, str) and token.startswith(BRIDGE_TOKEN_PREFIX)


def generate_bridge_token() -> str:
    """Generate a new cryptographically-secure opaque bridge token."""
    return f"{BRIDGE_TOKEN_PREFIX}{secrets.token_urlsafe(32)}"


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


# ---------------------------------------------------------------------------
# Interface
# ---------------------------------------------------------------------------


class BridgeTokenStore:
    """Interface for bridge-token persistence (structural duck-typing, no ABC)."""

    async def mint(self, org_id: str, bridge_id: str, name: str) -> tuple[str, dict[str, Any]]:
        """Mint a new token for ``(org_id, bridge_id)``. Return ``(raw, row)``."""
        raise NotImplementedError

    async def validate(self, raw: str) -> tuple[str, str] | None:
        """Return ``(org_id, bridge_id)`` for a live token, else ``None``.

        A token is live when it is not revoked AND (if it carries a grace
        deadline from a rotation) the grace window has not elapsed.
        """
        raise NotImplementedError

    async def list_for_bridge(self, org_id: str, bridge_id: str) -> list[dict[str, Any]]:
        """Return the (listing-safe) tokens for ``(org_id, bridge_id)``."""
        raise NotImplementedError

    async def rotate(
        self,
        token_id: str,
        org_id: str,
        bridge_id: str,
        grace: timedelta = DEFAULT_ROTATION_GRACE,
    ) -> tuple[str, dict[str, Any]] | None:
        """Mint a replacement token and put the old one into a grace window.

        Returns ``(raw_new, new_row)`` (raw shown once), or ``None`` when the
        old token does not exist / is not in this ``(org, bridge)``.
        """
        raise NotImplementedError

    async def revoke(self, token_id: str, org_id: str, bridge_id: str) -> bool:
        """Revoke a token in ``(org_id, bridge_id)``. Return True if revoked."""
        raise NotImplementedError


def _public_row(row: dict[str, Any]) -> dict[str, Any]:
    """Return the listing-safe shape of a bridge_tokens row (never the hash)."""

    def _iso(value: Any) -> Any:
        return value.isoformat() if hasattr(value, "isoformat") else value

    return {
        "id": str(row["id"]),
        "bridge_id": str(row["bridge_id"]),
        "name": row.get("name") or "bridge token",
        "last_four": row.get("last_four"),
        "created_at": _iso(row.get("created_at")),
        "last_used_at": _iso(row.get("last_used_at")),
        "grace_until": _iso(row.get("grace_until")),
        "revoked_at": _iso(row.get("revoked_at")),
    }


def _row_is_live(row: dict[str, Any], now: datetime) -> bool:
    """True when *row* still authenticates at *now* (not revoked, grace not past)."""
    if row.get("revoked_at") is not None:
        return False
    grace_until = row.get("grace_until")
    if grace_until is not None and grace_until <= now:
        # Old token whose rotation grace window has elapsed.
        return False
    return True


# ---------------------------------------------------------------------------
# Postgres implementation
# ---------------------------------------------------------------------------


class PgBridgeTokenStore(BridgeTokenStore):
    """asyncpg-backed bridge-token store over the ``bridge_tokens`` table."""

    async def mint(self, org_id: str, bridge_id: str, name: str) -> tuple[str, dict[str, Any]]:
        from app.db import fetchrow  # local import to avoid circular load

        raw = generate_bridge_token()
        token_hash = hash_bridge_token(raw)
        token_id = str(uuid.uuid4())
        last_four = raw[-4:]
        row = await fetchrow(
            """
            INSERT INTO bridge_tokens
                (id, org_id, bridge_id, token_hash, name, last_four)
            VALUES ($1, $2, $3, $4, $5, $6)
            RETURNING id, org_id, bridge_id, name, last_four, grace_until,
                      revoked_at, last_used_at, created_at
            """,
            token_id,
            org_id,
            bridge_id,
            token_hash,
            (name or "bridge token").strip() or "bridge token",
            last_four,
        )
        return raw, dict(row) if row is not None else {"id": token_id, "name": name}

    async def validate(self, raw: str) -> tuple[str, str] | None:
        from app.db import execute, fetchrow  # local import

        if not looks_like_bridge_token(raw):
            return None
        token_hash = hash_bridge_token(raw)
        row = await fetchrow(
            """
            SELECT id, org_id, bridge_id, grace_until, revoked_at
            FROM bridge_tokens
            WHERE token_hash = $1
              AND revoked_at IS NULL
              AND (grace_until IS NULL OR grace_until > now())
            """,
            token_hash,
        )
        if row is None:
            return None
        try:  # best-effort last-used stamp; never block auth on it
            await execute(
                "UPDATE bridge_tokens SET last_used_at = now() WHERE id = $1::uuid",
                str(row["id"]),
            )
        except Exception:  # noqa: BLE001
            pass
        return str(row["org_id"]), str(row["bridge_id"])

    async def list_for_bridge(self, org_id: str, bridge_id: str) -> list[dict[str, Any]]:
        from app.db import fetch  # local import

        rows = await fetch(
            """
            SELECT id, org_id, bridge_id, name, last_four, grace_until,
                   revoked_at, last_used_at, created_at
            FROM bridge_tokens
            WHERE org_id = $1::uuid AND bridge_id = $2::uuid
            ORDER BY created_at DESC
            """,
            org_id,
            bridge_id,
        )
        return [_public_row(dict(r)) for r in rows]

    async def rotate(
        self,
        token_id: str,
        org_id: str,
        bridge_id: str,
        grace: timedelta = DEFAULT_ROTATION_GRACE,
    ) -> tuple[str, dict[str, Any]] | None:
        from app.db import execute  # local import

        # Put the old token into a grace window (only if it is live & ours).
        grace_until = _now() + grace
        status = await execute(
            """
            UPDATE bridge_tokens
            SET grace_until = $4
            WHERE id = $1::uuid AND org_id = $2::uuid AND bridge_id = $3::uuid
              AND revoked_at IS NULL
            """,
            token_id,
            org_id,
            bridge_id,
            grace_until,
        )
        try:
            affected = int(status.split()[-1])
        except (IndexError, ValueError, AttributeError):
            affected = 0
        if affected <= 0:
            return None
        # Mint the replacement.
        return await self.mint(org_id, bridge_id, "rotated bridge token")

    async def revoke(self, token_id: str, org_id: str, bridge_id: str) -> bool:
        from app.db import execute  # local import

        status = await execute(
            """
            UPDATE bridge_tokens
            SET revoked_at = now()
            WHERE id = $1::uuid AND org_id = $2::uuid AND bridge_id = $3::uuid
              AND revoked_at IS NULL
            """,
            token_id,
            org_id,
            bridge_id,
        )
        try:
            return int(status.split()[-1]) > 0
        except (IndexError, ValueError, AttributeError):
            return False


# ---------------------------------------------------------------------------
# In-memory implementation (tests)
# ---------------------------------------------------------------------------


class InMemoryBridgeTokenStore(BridgeTokenStore):
    """Dict-backed bridge-token store for tests (no DB)."""

    def __init__(self) -> None:
        # token_id -> row dict (carries token_hash internally; never returned raw)
        self._store: dict[str, dict[str, Any]] = {}

    def reset(self) -> None:
        self._store.clear()

    async def mint(self, org_id: str, bridge_id: str, name: str) -> tuple[str, dict[str, Any]]:
        raw = generate_bridge_token()
        token_id = str(uuid.uuid4())
        now = _now()
        row = {
            "id": token_id,
            "org_id": str(org_id),
            "bridge_id": str(bridge_id),
            "token_hash": hash_bridge_token(raw),
            "name": (name or "bridge token").strip() or "bridge token",
            "last_four": raw[-4:],
            "grace_until": None,
            "revoked_at": None,
            "last_used_at": None,
            "created_at": now,
        }
        self._store[token_id] = row
        return raw, dict(row)

    async def validate(self, raw: str) -> tuple[str, str] | None:
        if not looks_like_bridge_token(raw):
            return None
        token_hash = hash_bridge_token(raw)
        now = _now()
        for row in self._store.values():
            if row["token_hash"] == token_hash and _row_is_live(row, now):
                row["last_used_at"] = now
                return str(row["org_id"]), str(row["bridge_id"])
        return None

    async def list_for_bridge(self, org_id: str, bridge_id: str) -> list[dict[str, Any]]:
        rows = [
            r
            for r in self._store.values()
            if str(r["org_id"]) == str(org_id) and str(r["bridge_id"]) == str(bridge_id)
        ]
        rows.sort(key=lambda r: r["created_at"], reverse=True)
        return [_public_row(r) for r in rows]

    async def rotate(
        self,
        token_id: str,
        org_id: str,
        bridge_id: str,
        grace: timedelta = DEFAULT_ROTATION_GRACE,
    ) -> tuple[str, dict[str, Any]] | None:
        old = self._store.get(str(token_id))
        if old is None:
            return None
        if str(old["org_id"]) != str(org_id) or str(old["bridge_id"]) != str(bridge_id):
            return None
        if old["revoked_at"] is not None:
            return None
        old["grace_until"] = _now() + grace
        return await self.mint(org_id, bridge_id, "rotated bridge token")

    async def revoke(self, token_id: str, org_id: str, bridge_id: str) -> bool:
        row = self._store.get(str(token_id))
        if row is None:
            return False
        if str(row["org_id"]) != str(org_id) or str(row["bridge_id"]) != str(bridge_id):
            return False
        if row["revoked_at"] is not None:
            return False
        row["revoked_at"] = _now()
        return True


# ---------------------------------------------------------------------------
# Provider singleton
# ---------------------------------------------------------------------------

_store: Optional[BridgeTokenStore] = None


def set_bridge_token_store_for_tests(store: BridgeTokenStore | None) -> None:
    """Inject a test double (or pass None to restore the default Pg store)."""
    global _store
    _store = store


def get_bridge_token_store() -> BridgeTokenStore:
    """Return the active :class:`BridgeTokenStore` singleton (lazy Pg default)."""
    global _store
    if _store is None:
        _store = PgBridgeTokenStore()
    return _store
