"""MCP server registry store — per-org persistent configuration.

Backed by the ``mcp_servers`` table (migration 0019).

Auth tokens are encrypted at rest using AES-256-GCM via
``app.security.crypto``.  The three secret columns
(``auth_secret_ciphertext``, ``auth_secret_nonce``, ``auth_secret_key_version``)
are never returned by ``list_for_org`` or ``get_by_id``.  Only
``get_enabled_for_org`` decrypts them for internal consumption by the MCP client.

Public API
----------
McpServerStore  — singleton class
get_mcp_store() -> McpServerStore

Methods
-------
await store.list_for_org(org_id)          -> list[dict]   (no secret fields)
await store.get_by_id(server_id, org_id)  -> dict | None  (no secret fields)
await store.create(org_id, name, url, ...)                -> dict
await store.update(server_id, org_id, **kwargs)           -> dict | None
await store.delete(server_id, org_id)                     -> bool
await store.get_enabled_for_org(org_id)   -> list[dict]   (includes decrypted auth_token)
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

# Columns returned on public reads (no secret fields).
_PUBLIC_COLS = (
    "id",
    "org_id",
    "name",
    "url",
    "transport",
    "enabled",
    "created_by",
    "created_at",
    "updated_at",
)

_PUBLIC_SELECT = ", ".join(_PUBLIC_COLS)

# Sentinel — distinct from None ("clear the field").
_UNSET: Any = object()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _row_to_public(row: Any) -> dict:
    """Convert an asyncpg Record to a public dict (no secret fields)."""
    d: dict = {}
    for col in _PUBLIC_COLS:
        val = row[col] if hasattr(row, "__getitem__") else getattr(row, col, None)
        if col in ("id", "org_id", "created_by") and val is not None:
            val = str(val)
        if col in ("created_at", "updated_at") and isinstance(val, datetime):
            if val.tzinfo is None:
                val = val.replace(tzinfo=timezone.utc)
        d[col] = val
    return d


def _decrypt_auth_token(row: Any) -> str | None:
    """Decrypt the auth secret from a raw DB row; return None if not set."""
    ct = row["auth_secret_ciphertext"] if hasattr(row, "__getitem__") else getattr(row, "auth_secret_ciphertext", None)
    if ct is None:
        return None
    nonce = row["auth_secret_nonce"] if hasattr(row, "__getitem__") else getattr(row, "auth_secret_nonce", None)
    kv = row["auth_secret_key_version"] if hasattr(row, "__getitem__") else getattr(row, "auth_secret_key_version", None)
    if nonce is None or kv is None:
        return None
    try:
        from app.security.crypto import decrypt_json  # noqa: PLC0415
        data = decrypt_json(bytes(ct), bytes(nonce), int(kv))
        return data.get("token")
    except Exception as exc:  # noqa: BLE001
        logger.warning("mcp_store: failed to decrypt auth token: %s", exc)
        return None


def _row_to_internal(row: Any) -> dict:
    """Convert a raw DB row to an internal dict with decrypted auth_token."""
    d: dict = {}
    for col in ("id", "org_id", "name", "url", "transport", "enabled"):
        val = row[col] if hasattr(row, "__getitem__") else getattr(row, col, None)
        if col in ("id", "org_id") and val is not None:
            val = str(val)
        d[col] = val
    d["auth_token"] = _decrypt_auth_token(row)
    return d


# ---------------------------------------------------------------------------
# McpServerStore
# ---------------------------------------------------------------------------


class McpServerStore:
    """Async store for the ``mcp_servers`` table.

    All methods degrade gracefully when the DB is unavailable (e.g. in tests
    that do not configure a database): they return empty lists / None / False
    rather than propagating exceptions.
    """

    # ------------------------------------------------------------------
    # Read operations — public (no secret fields)
    # ------------------------------------------------------------------

    async def list_for_org(self, org_id: str) -> list[dict]:
        """Return all MCP servers for *org_id*, sorted by created_at asc.

        Never returns secret (auth) fields.
        """
        try:
            from app.db import fetch  # noqa: PLC0415

            rows = await fetch(
                f"SELECT {_PUBLIC_SELECT} FROM mcp_servers "
                "WHERE org_id = $1::uuid ORDER BY created_at ASC",
                org_id,
            )
            return [_row_to_public(r) for r in rows]
        except Exception as exc:  # noqa: BLE001
            logger.warning("mcp_store.list_for_org: %s", exc)
            return []

    async def get_by_id(self, server_id: str, org_id: str) -> dict | None:
        """Return the server row for *server_id* within *org_id*, or ``None``.

        Never returns secret (auth) fields.
        """
        try:
            from app.db import fetchrow  # noqa: PLC0415

            row = await fetchrow(
                f"SELECT {_PUBLIC_SELECT} FROM mcp_servers "
                "WHERE id = $1::uuid AND org_id = $2::uuid",
                server_id,
                org_id,
            )
            if row is None:
                return None
            return _row_to_public(row)
        except Exception as exc:  # noqa: BLE001
            logger.warning("mcp_store.get_by_id: %s", exc)
            return None

    async def get_enabled_for_org(self, org_id: str) -> list[dict]:
        """Return all enabled servers for *org_id* with decrypted auth_token.

        For internal use by the MCP client only — includes the ``auth_token``
        field (plaintext) and omits public-only metadata (created_by, timestamps).
        """
        try:
            from app.db import fetch  # noqa: PLC0415

            rows = await fetch(
                "SELECT id, org_id, name, url, transport, enabled, "
                "auth_secret_ciphertext, auth_secret_nonce, auth_secret_key_version "
                "FROM mcp_servers "
                "WHERE org_id = $1::uuid AND enabled = TRUE "
                "ORDER BY created_at ASC",
                org_id,
            )
            return [_row_to_internal(r) for r in rows]
        except Exception as exc:  # noqa: BLE001
            logger.warning("mcp_store.get_enabled_for_org: %s", exc)
            return []

    # ------------------------------------------------------------------
    # Write operations
    # ------------------------------------------------------------------

    async def create(
        self,
        org_id: str,
        name: str,
        url: str,
        transport: str = "http",
        auth_token: str | None = None,
        enabled: bool = True,
        created_by: str | None = None,
    ) -> dict:
        """Insert a new MCP server row and return the public dict.

        If *auth_token* is provided it is encrypted before storage.
        """
        from app.db import fetchrow  # noqa: PLC0415

        ct: bytes | None = None
        nonce: bytes | None = None
        kv: int | None = None

        if auth_token is not None:
            from app.security.crypto import encrypt_json  # noqa: PLC0415
            ct, nonce, kv = encrypt_json({"token": auth_token})

        row = await fetchrow(
            """
            INSERT INTO mcp_servers
                (org_id, name, url, transport,
                 auth_secret_ciphertext, auth_secret_nonce, auth_secret_key_version,
                 enabled, created_by)
            VALUES
                ($1::uuid, $2, $3, $4,
                 $5, $6, $7,
                 $8, $9::uuid)
            RETURNING
            """
            + _PUBLIC_SELECT,
            org_id,
            name,
            url,
            transport,
            ct,
            nonce,
            kv,
            enabled,
            created_by,
        )
        if row is None:  # pragma: no cover
            raise RuntimeError("INSERT INTO mcp_servers returned no row.")
        return _row_to_public(row)

    async def update(
        self,
        server_id: str,
        org_id: str,
        *,
        name: str | None = None,
        url: str | None = None,
        transport: str | None = None,
        auth_token: Any = _UNSET,
        enabled: bool | None = None,
    ) -> dict | None:
        """Partial-update a server row; return updated public dict or ``None``.

        Pass ``auth_token=None`` to explicitly clear the stored secret.
        Omit *auth_token* (leave at ``_UNSET``) to leave it unchanged.
        """
        try:
            from app.db import fetchrow  # noqa: PLC0415

            sets: list[str] = ["updated_at = $3"]
            params: list[Any] = [server_id, org_id, datetime.now(timezone.utc)]
            idx = 4

            if name is not None:
                sets.append(f"name = ${idx}")
                params.append(name)
                idx += 1
            if url is not None:
                sets.append(f"url = ${idx}")
                params.append(url)
                idx += 1
            if transport is not None:
                sets.append(f"transport = ${idx}")
                params.append(transport)
                idx += 1
            if enabled is not None:
                sets.append(f"enabled = ${idx}")
                params.append(enabled)
                idx += 1
            if auth_token is not _UNSET:
                if auth_token is None:
                    # Clear the secret columns.
                    sets.append(f"auth_secret_ciphertext = ${idx}")
                    params.append(None)
                    idx += 1
                    sets.append(f"auth_secret_nonce = ${idx}")
                    params.append(None)
                    idx += 1
                    sets.append(f"auth_secret_key_version = ${idx}")
                    params.append(None)
                    idx += 1
                else:
                    from app.security.crypto import encrypt_json  # noqa: PLC0415
                    ct, nonce, kv = encrypt_json({"token": auth_token})
                    sets.append(f"auth_secret_ciphertext = ${idx}")
                    params.append(ct)
                    idx += 1
                    sets.append(f"auth_secret_nonce = ${idx}")
                    params.append(nonce)
                    idx += 1
                    sets.append(f"auth_secret_key_version = ${idx}")
                    params.append(kv)
                    idx += 1

            sql = (
                f"UPDATE mcp_servers SET {', '.join(sets)} "
                "WHERE id = $1::uuid AND org_id = $2::uuid "
                "RETURNING " + _PUBLIC_SELECT
            )
            row = await fetchrow(sql, *params)
            if row is None:
                return None
            return _row_to_public(row)
        except Exception as exc:  # noqa: BLE001
            logger.warning("mcp_store.update: %s", exc)
            return None

    async def delete(self, server_id: str, org_id: str) -> bool:
        """Delete the server row; return ``True`` if a row was removed."""
        try:
            from app.db import execute  # noqa: PLC0415

            status = await execute(
                "DELETE FROM mcp_servers WHERE id = $1::uuid AND org_id = $2::uuid",
                server_id,
                org_id,
            )
            # asyncpg returns e.g. "DELETE 1" or "DELETE 0".
            return status.endswith("1") or (not status.endswith("0"))
        except Exception as exc:  # noqa: BLE001
            logger.warning("mcp_store.delete: %s", exc)
            return False


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_store: McpServerStore | None = None


def get_mcp_store() -> McpServerStore:
    """Return (or lazily create) the module-level MCP server store."""
    global _store
    if _store is None:
        _store = McpServerStore()
    return _store
