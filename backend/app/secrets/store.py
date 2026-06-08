"""Secret store implementations — InMemorySecretStore (tests) + PgSecretStore (prod).

``InMemorySecretStore`` is a dict-backed store for named org-scoped secrets.
It is the primary store used in tests.

``PgSecretStore`` is the asyncpg-backed production store that maps each method
to a parameterised SQL query against the ``secrets`` table (from migration
0015).  Rows are converted to plain dicts; datetime values match the shape
produced by ``InMemorySecretStore``.

Provider
--------
``get_secret_store()`` returns the configured singleton store.  By default it
returns a ``PgSecretStore`` (suitable for production); tests inject an
``InMemorySecretStore`` via ``set_secret_store(store)``.  This mirrors the
pattern used in ``app/flows/store.py``.

Design
------
- Secrets are encrypted at rest using :func:`app.secrets.crypto.encrypt` and
  decrypted on read via :func:`app.secrets.crypto.decrypt`.
- ``list_secrets`` NEVER returns the ``value_encrypted`` column; callers
  receive name, metadata, and timestamps only.
- ``resolve_all`` returns ``{name: plaintext}`` for populating
  ``TaskContext.secrets`` in the flows engine.
- All mutation methods use ``uuid.uuid4()`` and ``datetime.now(timezone.utc)``
  **at call time only** — never at module/class import time.
- Datetimes are always tz-aware UTC; uuids are strings.
"""

from __future__ import annotations

import uuid
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any


# ---------------------------------------------------------------------------
# Type alias
# ---------------------------------------------------------------------------

Secret = dict[str, Any]


# ---------------------------------------------------------------------------
# InMemorySecretStore
# ---------------------------------------------------------------------------


class InMemorySecretStore:
    """Dict-backed store for org-scoped named secrets.

    Values are encrypted at rest (stored as ``bytes``) using
    :func:`app.secrets.crypto.encrypt`.  ``get_secret`` decrypts on read.

    Secret shape (internal)
    -----------------------
    ``{id, org_id, name, value_encrypted(bytes), created_by, created_at, updated_at}``

    Public list shape (no value)
    ----------------------------
    ``{id, org_id, name, created_by, created_at, updated_at}``
    """

    def __init__(self) -> None:
        # (org_id, name) → Secret dict (with value_encrypted)
        self._secrets: dict[tuple[str, str], Secret] = {}

    # ------------------------------------------------------------------
    # Write operations
    # ------------------------------------------------------------------

    async def set_secret(
        self,
        org_id: str,
        name: str,
        value: str,
        created_by: str,
    ) -> dict[str, Any]:
        """Create or update the named secret for *org_id*.

        The plaintext *value* is encrypted before storage.  Returns the
        public (no-value) dict.

        Parameters
        ----------
        org_id:
            Organisation UUID string.
        name:
            Secret name — must be non-empty.
        value:
            Plaintext secret value to encrypt and store.
        created_by:
            User UUID string.

        Returns
        -------
        dict
            Public secret dict (no ``value_encrypted`` field).
        """
        from app.secrets.crypto import encrypt  # noqa: PLC0415

        key = (str(org_id), str(name))
        now = datetime.now(timezone.utc)
        encrypted = encrypt(value)

        existing = self._secrets.get(key)
        if existing is not None:
            existing["value_encrypted"] = encrypted
            existing["updated_at"] = now
            return _public(deepcopy(existing))

        secret_id = str(uuid.uuid4())
        row: Secret = {
            "id": secret_id,
            "org_id": str(org_id),
            "name": str(name),
            "value_encrypted": encrypted,
            "created_by": str(created_by),
            "created_at": now,
            "updated_at": now,
        }
        self._secrets[key] = row
        return _public(deepcopy(row))

    async def delete_secret(self, org_id: str, name: str) -> bool:
        """Delete the named secret; return ``True`` if it existed."""
        key = (str(org_id), str(name))
        if key in self._secrets:
            del self._secrets[key]
            return True
        return False

    # ------------------------------------------------------------------
    # Read operations
    # ------------------------------------------------------------------

    async def get_secret(self, org_id: str, name: str) -> str | None:
        """Return the decrypted plaintext value, or ``None`` if not found."""
        from app.secrets.crypto import decrypt  # noqa: PLC0415

        key = (str(org_id), str(name))
        row = self._secrets.get(key)
        if row is None:
            return None
        return decrypt(row["value_encrypted"])

    async def list_secrets(self, org_id: str) -> list[dict[str, Any]]:
        """Return all secrets for *org_id* — NEVER including the value.

        Sorted by name ascending.
        """
        rows = [
            _public(deepcopy(row))
            for (oid, _name), row in self._secrets.items()
            if oid == str(org_id)
        ]
        rows.sort(key=lambda r: r["name"])
        return rows

    async def resolve_all(self, org_id: str) -> dict[str, str]:
        """Return ``{name: plaintext}`` for all secrets in *org_id*.

        Used by the flows runtime to populate ``TaskContext.secrets`` before
        executing tasks.  Callers should treat the returned dict as read-only.
        """
        from app.secrets.crypto import decrypt  # noqa: PLC0415

        result: dict[str, str] = {}
        for (oid, name), row in self._secrets.items():
            if oid == str(org_id):
                result[name] = decrypt(row["value_encrypted"])
        return result


# ---------------------------------------------------------------------------
# PgSecretStore
# ---------------------------------------------------------------------------


class PgSecretStore:
    """asyncpg-backed secret store for production use.

    Uses the ``fetch`` / ``fetchrow`` / ``execute`` helpers from ``app.db``
    (which acquire a connection from the pool automatically).

    All SQL is parameterised with ``$N`` placeholders.  Column names match
    the ``secrets`` table from migration 0015.

    Rows returned by asyncpg are converted to plain dicts that match the
    shape produced by ``InMemorySecretStore``.
    """

    # ------------------------------------------------------------------
    # Write operations
    # ------------------------------------------------------------------

    async def set_secret(
        self,
        org_id: str,
        name: str,
        value: str,
        created_by: str,
    ) -> dict[str, Any]:
        """Upsert the named secret; return the public (no-value) dict."""
        from app.db import fetchrow as db_fetchrow  # noqa: PLC0415
        from app.secrets.crypto import encrypt  # noqa: PLC0415

        encrypted: bytes = encrypt(value)
        now = datetime.now(timezone.utc)

        row = await db_fetchrow(
            """
            INSERT INTO secrets (org_id, name, value_encrypted, created_by, created_at, updated_at)
            VALUES ($1::uuid, $2, $3, $4::uuid, $5, $5)
            ON CONFLICT (org_id, name)
            DO UPDATE SET
                value_encrypted = EXCLUDED.value_encrypted,
                updated_at      = EXCLUDED.updated_at
            RETURNING id, org_id, name, created_by, created_at, updated_at
            """,
            org_id,
            name,
            encrypted,
            created_by,
            now,
        )
        if row is None:  # pragma: no cover
            raise RuntimeError("INSERT INTO secrets returned no row.")
        return _row_to_public(row)

    async def delete_secret(self, org_id: str, name: str) -> bool:
        """Delete the named secret; return ``True`` if it existed."""
        from app.db import execute as db_execute  # noqa: PLC0415

        status = await db_execute(
            """
            DELETE FROM secrets
            WHERE org_id = $1::uuid AND name = $2
            """,
            org_id,
            name,
        )
        # asyncpg returns "DELETE N" where N is the row count.
        return status.endswith("1") or (not status.endswith("0"))

    # ------------------------------------------------------------------
    # Read operations
    # ------------------------------------------------------------------

    async def get_secret(self, org_id: str, name: str) -> str | None:
        """Return the decrypted plaintext value, or ``None`` if not found."""
        from app.db import fetchrow as db_fetchrow  # noqa: PLC0415
        from app.secrets.crypto import decrypt  # noqa: PLC0415

        row = await db_fetchrow(
            """
            SELECT value_encrypted FROM secrets
            WHERE org_id = $1::uuid AND name = $2
            """,
            org_id,
            name,
        )
        if row is None:
            return None
        return decrypt(bytes(row["value_encrypted"]))

    async def list_secrets(self, org_id: str) -> list[dict[str, Any]]:
        """Return all secrets for *org_id* — NEVER including the value.

        Sorted by name ascending.
        """
        from app.db import fetch as db_fetch  # noqa: PLC0415

        rows = await db_fetch(
            """
            SELECT id, org_id, name, created_by, created_at, updated_at
            FROM secrets
            WHERE org_id = $1::uuid
            ORDER BY name ASC
            """,
            org_id,
        )
        return [_row_to_public(r) for r in rows]

    async def resolve_all(self, org_id: str) -> dict[str, str]:
        """Return ``{name: plaintext}`` for all secrets in *org_id*."""
        from app.db import fetch as db_fetch  # noqa: PLC0415
        from app.secrets.crypto import decrypt  # noqa: PLC0415

        rows = await db_fetch(
            """
            SELECT name, value_encrypted FROM secrets
            WHERE org_id = $1::uuid
            """,
            org_id,
        )
        return {str(r["name"]): decrypt(bytes(r["value_encrypted"])) for r in rows}


# ---------------------------------------------------------------------------
# Row helpers
# ---------------------------------------------------------------------------


def _public(row: Secret) -> dict[str, Any]:
    """Return a copy of *row* without the ``value_encrypted`` field."""
    return {k: v for k, v in row.items() if k != "value_encrypted"}


def _row_to_public(row: Any) -> dict[str, Any]:
    """Convert an asyncpg Record to a public (no-value) secret dict."""
    d = dict(row)
    d.pop("value_encrypted", None)
    for key in ("id", "org_id", "created_by"):
        if key in d and d[key] is not None and not isinstance(d[key], str):
            d[key] = str(d[key])
    for key in ("created_at", "updated_at"):
        val = d.get(key)
        if isinstance(val, datetime) and val.tzinfo is None:
            d[key] = val.replace(tzinfo=timezone.utc)
    return d


# ---------------------------------------------------------------------------
# Module-level singleton / provider
# ---------------------------------------------------------------------------

#: Active singleton — None means "lazily create PgSecretStore on first call".
_secret_store: InMemorySecretStore | PgSecretStore | None = None


def get_secret_store() -> InMemorySecretStore | PgSecretStore:
    """Return (or lazily create) the module-level secret store.

    In production (no override via ``set_secret_store``), returns a
    ``PgSecretStore`` instance.  Tests inject an ``InMemorySecretStore`` via
    ``set_secret_store`` before making requests.

    Route handlers depend on this function; they keep working without changes
    since both stores expose the same interface.
    """
    global _secret_store
    if _secret_store is None:
        _secret_store = PgSecretStore()
    return _secret_store


def set_secret_store(store: InMemorySecretStore | PgSecretStore | None) -> None:
    """Override the module-level store singleton.

    Pass an ``InMemorySecretStore`` instance to inject a test double.
    Pass ``None`` to reset so the next ``get_secret_store()`` call creates a
    fresh ``PgSecretStore`` (the production default).
    """
    global _secret_store
    _secret_store = store
