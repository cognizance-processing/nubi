"""Webhook endpoint store — InMemory (tests) + Pg (production).

Org-scoped store for the ``webhook_endpoints`` table (migration 0016). Mirrors
the structure of :mod:`app.security.issuers_store` and :mod:`app.secrets.store`.

Security
--------
The per-endpoint signing secret is encrypted at rest using
:func:`app.secrets.crypto.encrypt` (Fernet, keyed by ``NUBI_SECRETS_KEY``) and
decrypted on read only by the delivery path via :meth:`get_secret`. Public
read/list APIs NEVER return the secret (encrypted or plaintext).

Public API
----------
WebhookEndpointRow
    Dict shape (public, no secret) returned by CRUD reads.
InMemoryWebhookStore / PgWebhookStore
    Store implementations.
get_webhook_store() / set_webhook_store(store)
    Module-level singleton helpers (same pattern as secrets/store.py).
"""

from __future__ import annotations

import uuid
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

# Sentinel for "not provided" in update() — distinct from None.
_UNSET: Any = object()

WebhookEndpointRow = dict[str, Any]
"""
Public dict with keys: id, org_id, name, url, event_types, active,
created_by, created_at, updated_at. NEVER includes ``secret_encrypted``.
"""


def _public(row: dict[str, Any]) -> WebhookEndpointRow:
    """Return a copy of *row* without the ``secret_encrypted`` field."""
    return {k: v for k, v in row.items() if k != "secret_encrypted"}


# ---------------------------------------------------------------------------
# InMemoryWebhookStore
# ---------------------------------------------------------------------------


class InMemoryWebhookStore:
    """Dict-backed store for webhook_endpoints — used in tests / InMemory mode.

    The signing secret is encrypted at rest (stored as ``bytes``) using
    :func:`app.secrets.crypto.encrypt`, exactly as the Pg store does.
    """

    def __init__(self) -> None:
        self._rows: dict[str, dict[str, Any]] = {}

    # ------------------------------------------------------------------
    # Write operations
    # ------------------------------------------------------------------

    async def create(
        self,
        org_id: str,
        name: str,
        url: str,
        secret: str,
        created_by: str | None,
        *,
        event_types: list[str] | None = None,
        active: bool = True,
    ) -> WebhookEndpointRow:
        """Create and return a new endpoint row (public, no secret)."""
        from app.secrets.crypto import encrypt  # noqa: PLC0415

        now = datetime.now(timezone.utc)
        row_id = str(uuid.uuid4())
        row: dict[str, Any] = {
            "id": row_id,
            "org_id": str(org_id),
            "name": name,
            "url": url,
            "secret_encrypted": encrypt(secret),
            "event_types": list(event_types) if event_types else [],
            "active": active,
            "created_by": str(created_by) if created_by is not None else None,
            "created_at": now,
            "updated_at": now,
        }
        self._rows[row_id] = row
        return _public(deepcopy(row))

    async def update(
        self,
        endpoint_id: str,
        org_id: str,
        *,
        name: str | None = None,
        url: str | None = None,
        secret: str | None = None,
        event_types: list[str] | None = None,
        active: bool | None = None,
    ) -> WebhookEndpointRow | None:
        """Partial-update an endpoint; return public row or ``None`` if missing."""
        from app.secrets.crypto import encrypt  # noqa: PLC0415

        row = self._rows.get(str(endpoint_id))
        if row is None or row["org_id"] != str(org_id):
            return None
        if name is not None:
            row["name"] = name
        if url is not None:
            row["url"] = url
        if secret is not None:
            row["secret_encrypted"] = encrypt(secret)
        if event_types is not None:
            row["event_types"] = list(event_types)
        if active is not None:
            row["active"] = active
        row["updated_at"] = datetime.now(timezone.utc)
        return _public(deepcopy(row))

    async def delete(self, endpoint_id: str, org_id: str) -> bool:
        """Delete an endpoint by id within *org_id*; return ``True`` if it existed."""
        row = self._rows.get(str(endpoint_id))
        if row is None or row["org_id"] != str(org_id):
            return False
        del self._rows[str(endpoint_id)]
        return True

    # ------------------------------------------------------------------
    # Read operations
    # ------------------------------------------------------------------

    async def list_for_org(self, org_id: str) -> list[WebhookEndpointRow]:
        """Return all endpoints for *org_id* (public, no secret), oldest first."""
        rows = [
            _public(deepcopy(r))
            for r in self._rows.values()
            if r["org_id"] == str(org_id)
        ]
        rows.sort(key=lambda r: r["created_at"])
        return rows

    async def get_by_id(
        self, endpoint_id: str, org_id: str
    ) -> WebhookEndpointRow | None:
        """Return the public row for *endpoint_id* within *org_id*, or ``None``."""
        row = self._rows.get(str(endpoint_id))
        if row is None or row["org_id"] != str(org_id):
            return None
        return _public(deepcopy(row))

    async def get_secret(self, endpoint_id: str, org_id: str) -> str | None:
        """Return the decrypted signing secret for delivery, or ``None``.

        Org-scoped: an endpoint id from another org yields ``None``.
        """
        from app.secrets.crypto import decrypt  # noqa: PLC0415

        row = self._rows.get(str(endpoint_id))
        if row is None or row["org_id"] != str(org_id):
            return None
        return decrypt(row["secret_encrypted"])

    async def list_active_for_event(
        self, org_id: str, event_type: str
    ) -> list[dict[str, Any]]:
        """Return active endpoints in *org_id* subscribed to *event_type*.

        Each returned dict includes the decrypted ``secret`` so the delivery
        path can sign without a second round-trip. STRICTLY org-scoped — only
        rows whose ``org_id`` matches are ever considered.
        """
        from app.secrets.crypto import decrypt  # noqa: PLC0415

        out: list[dict[str, Any]] = []
        for r in self._rows.values():
            if r["org_id"] != str(org_id):
                continue
            if not r.get("active", True):
                continue
            if event_type not in (r.get("event_types") or []):
                continue
            pub = _public(deepcopy(r))
            pub["secret"] = decrypt(r["secret_encrypted"])
            out.append(pub)
        out.sort(key=lambda r: r["created_at"])
        return out


# ---------------------------------------------------------------------------
# PgWebhookStore
# ---------------------------------------------------------------------------


class PgWebhookStore:
    """asyncpg-backed store for production use (migration 0016)."""

    _COLS = (
        "id, org_id, name, url, event_types, active, created_by, "
        "created_at, updated_at"
    )

    async def create(
        self,
        org_id: str,
        name: str,
        url: str,
        secret: str,
        created_by: str | None,
        *,
        event_types: list[str] | None = None,
        active: bool = True,
    ) -> WebhookEndpointRow:
        from app.db import fetchrow as db_fetchrow  # noqa: PLC0415
        from app.secrets.crypto import encrypt  # noqa: PLC0415

        now = datetime.now(timezone.utc)
        encrypted = encrypt(secret)
        types = list(event_types) if event_types else []

        row = await db_fetchrow(
            f"""
            INSERT INTO webhook_endpoints
                (org_id, name, url, secret_encrypted, event_types, active,
                 created_by, created_at, updated_at)
            VALUES
                ($1::uuid, $2, $3, $4, $5::text[], $6, $7, $8, $8)
            RETURNING {self._COLS}
            """,
            org_id,
            name,
            url,
            encrypted,
            types,
            active,
            created_by,
            now,
        )
        if row is None:  # pragma: no cover
            raise RuntimeError("INSERT INTO webhook_endpoints returned no row.")
        return _pg_row_to_public(row)

    async def update(
        self,
        endpoint_id: str,
        org_id: str,
        *,
        name: str | None = None,
        url: str | None = None,
        secret: str | None = None,
        event_types: list[str] | None = None,
        active: bool | None = None,
    ) -> WebhookEndpointRow | None:
        from app.db import fetchrow as db_fetchrow  # noqa: PLC0415
        from app.secrets.crypto import encrypt  # noqa: PLC0415

        sets: list[str] = ["updated_at = $3"]
        params: list[Any] = [endpoint_id, org_id, datetime.now(timezone.utc)]
        idx = 4

        if name is not None:
            sets.append(f"name = ${idx}")
            params.append(name)
            idx += 1
        if url is not None:
            sets.append(f"url = ${idx}")
            params.append(url)
            idx += 1
        if secret is not None:
            sets.append(f"secret_encrypted = ${idx}")
            params.append(encrypt(secret))
            idx += 1
        if event_types is not None:
            sets.append(f"event_types = ${idx}::text[]")
            params.append(list(event_types))
            idx += 1
        if active is not None:
            sets.append(f"active = ${idx}")
            params.append(active)
            idx += 1

        sql = f"""
            UPDATE webhook_endpoints
            SET {', '.join(sets)}
            WHERE id = $1::uuid AND org_id = $2::uuid
            RETURNING {self._COLS}
        """
        row = await db_fetchrow(sql, *params)
        if row is None:
            return None
        return _pg_row_to_public(row)

    async def delete(self, endpoint_id: str, org_id: str) -> bool:
        from app.db import execute as db_execute  # noqa: PLC0415

        status = await db_execute(
            """
            DELETE FROM webhook_endpoints
            WHERE id = $1::uuid AND org_id = $2::uuid
            """,
            endpoint_id,
            org_id,
        )
        return status.endswith("1") or (not status.endswith("0"))

    async def list_for_org(self, org_id: str) -> list[WebhookEndpointRow]:
        from app.db import fetch as db_fetch  # noqa: PLC0415

        rows = await db_fetch(
            f"""
            SELECT {self._COLS}
            FROM webhook_endpoints
            WHERE org_id = $1::uuid
            ORDER BY created_at ASC
            """,
            org_id,
        )
        return [_pg_row_to_public(r) for r in rows]

    async def get_by_id(
        self, endpoint_id: str, org_id: str
    ) -> WebhookEndpointRow | None:
        from app.db import fetchrow as db_fetchrow  # noqa: PLC0415

        row = await db_fetchrow(
            f"""
            SELECT {self._COLS}
            FROM webhook_endpoints
            WHERE id = $1::uuid AND org_id = $2::uuid
            """,
            endpoint_id,
            org_id,
        )
        if row is None:
            return None
        return _pg_row_to_public(row)

    async def get_secret(self, endpoint_id: str, org_id: str) -> str | None:
        from app.db import fetchrow as db_fetchrow  # noqa: PLC0415
        from app.secrets.crypto import decrypt  # noqa: PLC0415

        row = await db_fetchrow(
            """
            SELECT secret_encrypted FROM webhook_endpoints
            WHERE id = $1::uuid AND org_id = $2::uuid
            """,
            endpoint_id,
            org_id,
        )
        if row is None:
            return None
        return decrypt(bytes(row["secret_encrypted"]))

    async def list_active_for_event(
        self, org_id: str, event_type: str
    ) -> list[dict[str, Any]]:
        from app.db import fetch as db_fetch  # noqa: PLC0415
        from app.secrets.crypto import decrypt  # noqa: PLC0415

        rows = await db_fetch(
            f"""
            SELECT {self._COLS}, secret_encrypted
            FROM webhook_endpoints
            WHERE org_id = $1::uuid
              AND active = TRUE
              AND $2 = ANY (event_types)
            ORDER BY created_at ASC
            """,
            org_id,
            event_type,
        )
        out: list[dict[str, Any]] = []
        for r in rows:
            pub = _pg_row_to_public(r)
            pub["secret"] = decrypt(bytes(r["secret_encrypted"]))
            out.append(pub)
        return out


# ---------------------------------------------------------------------------
# Row conversion helper
# ---------------------------------------------------------------------------


def _pg_row_to_public(row: Any) -> WebhookEndpointRow:
    """Convert an asyncpg Record to a public (no-secret) dict, normalising types."""
    d = dict(row)
    d.pop("secret_encrypted", None)
    for key in ("id", "org_id", "created_by"):
        if key in d and d[key] is not None and not isinstance(d[key], str):
            d[key] = str(d[key])
    for key in ("created_at", "updated_at"):
        val = d.get(key)
        if isinstance(val, datetime) and val.tzinfo is None:
            d[key] = val.replace(tzinfo=timezone.utc)
    if d.get("event_types") is None:
        d["event_types"] = []
    else:
        d["event_types"] = list(d["event_types"])
    return d


# ---------------------------------------------------------------------------
# Module-level singleton / provider
# ---------------------------------------------------------------------------

_store: InMemoryWebhookStore | PgWebhookStore | None = None


def get_webhook_store() -> InMemoryWebhookStore | PgWebhookStore:
    """Return (or lazily create) the module-level webhook store.

    In production returns a ``PgWebhookStore``; tests inject an
    ``InMemoryWebhookStore`` via :func:`set_webhook_store`.
    """
    global _store
    if _store is None:
        _store = PgWebhookStore()
    return _store


def set_webhook_store(
    store: InMemoryWebhookStore | PgWebhookStore | None,
) -> None:
    """Override the module-level store singleton (pass ``None`` to reset)."""
    global _store
    _store = store
