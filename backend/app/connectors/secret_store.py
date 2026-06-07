"""Connector secret store — provider pattern mirroring app.repos.

Two implementations
-------------------
PgSecretStore       — asyncpg-backed; stores ciphertext in connector_secrets table.
InMemorySecretStore — dict-backed; for tests (no DB required, no crypto leakage).

Provider
--------
The module-level singleton is obtained via ``get_secret_store()``.  Tests swap
in an ``InMemorySecretStore`` via ``set_secret_store_for_tests()``.

Table schema (created by sibling migration)
-------------------------------------------
    connector_secrets(
        id          uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
        datastore_id uuid       NOT NULL,
        org_id      uuid        NOT NULL,
        ciphertext  bytea       NOT NULL,
        nonce       bytea       NOT NULL,
        key_version int         NOT NULL,
        created_at  timestamptz NOT NULL DEFAULT now(),
        updated_at  timestamptz NOT NULL DEFAULT now(),
        UNIQUE (datastore_id)
    )

Security contract
-----------------
- The application encrypts/decrypts with AES-256-GCM using app.security.crypto.
- The DB receives only ciphertext + nonce + key_version.
- The master key comes from ENV; it is never written to or read from Postgres.
- Every read/write is scoped by org_id to prevent cross-org data leakage.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from app.security.crypto import decrypt_json, encrypt_json


# ---------------------------------------------------------------------------
# Abstract interface (structural duck-typing — no ABC overhead)
# ---------------------------------------------------------------------------

class SecretStore:
    """Interface for connector secret storage.

    Concrete implementations must provide put / get / delete.
    """

    async def put(self, datastore_id: str, org_id: str, secret: dict[str, Any]) -> None:
        """Encrypt *secret* and upsert into the store (one secret per datastore).

        Parameters
        ----------
        datastore_id:
            UUID string identifying the datastore / connector instance.
        org_id:
            UUID string identifying the organisation (used for scoping).
        secret:
            Arbitrary JSON-serialisable dict (DB password, API token, …).
        """
        raise NotImplementedError

    async def get(self, datastore_id: str, org_id: str) -> dict[str, Any] | None:
        """Fetch and decrypt the secret for *datastore_id* scoped to *org_id*.

        Returns ``None`` if no secret exists for this (datastore_id, org_id) pair.
        """
        raise NotImplementedError

    async def delete(self, datastore_id: str, org_id: str) -> None:
        """Delete the secret for *datastore_id* scoped to *org_id* (no-op if absent)."""
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Postgres implementation
# ---------------------------------------------------------------------------

class PgSecretStore(SecretStore):
    """asyncpg-backed secret store using the connector_secrets table.

    All ciphertext / nonce / key_version columns are written and read as raw
    bytes (asyncpg bytea ↔ Python bytes).  The encryption key never touches
    the database; decryption happens in the application process.
    """

    async def put(self, datastore_id: str, org_id: str, secret: dict[str, Any]) -> None:
        """Encrypt *secret* and UPSERT into connector_secrets."""
        from app.db import execute  # local import to avoid circular at module load

        ciphertext, nonce, key_version = encrypt_json(secret)

        await execute(
            """
            INSERT INTO connector_secrets
                (datastore_id, org_id, ciphertext, nonce, key_version)
            VALUES
                ($1::uuid, $2::uuid, $3, $4, $5)
            ON CONFLICT (datastore_id) DO UPDATE
                SET ciphertext   = EXCLUDED.ciphertext,
                    nonce        = EXCLUDED.nonce,
                    key_version  = EXCLUDED.key_version,
                    org_id       = EXCLUDED.org_id,
                    updated_at   = now()
            """,
            datastore_id,
            org_id,
            ciphertext,
            nonce,
            key_version,
        )

    async def get(self, datastore_id: str, org_id: str) -> dict[str, Any] | None:
        """Fetch and decrypt the secret; returns None if not found or org mismatch."""
        from app.db import fetchrow  # local import

        row = await fetchrow(
            """
            SELECT ciphertext, nonce, key_version
            FROM connector_secrets
            WHERE datastore_id = $1::uuid
              AND org_id       = $2::uuid
            """,
            datastore_id,
            org_id,
        )
        if row is None:
            return None

        return decrypt_json(
            bytes(row["ciphertext"]),
            bytes(row["nonce"]),
            int(row["key_version"]),
        )

    async def delete(self, datastore_id: str, org_id: str) -> None:
        """Delete the secret row; no-op if it does not exist."""
        from app.db import execute  # local import

        await execute(
            """
            DELETE FROM connector_secrets
            WHERE datastore_id = $1::uuid
              AND org_id       = $2::uuid
            """,
            datastore_id,
            org_id,
        )


# ---------------------------------------------------------------------------
# In-memory implementation (tests)
# ---------------------------------------------------------------------------

class InMemorySecretStore(SecretStore):
    """Dict-backed secret store for tests.

    Encrypts/decrypts in the same way as PgSecretStore (real AES-GCM), so
    crypto correctness is exercised without a live DB.  Data is scoped by
    org_id; attempting to read another org's secret returns None.

    Usage in tests::

        from app.connectors.secret_store import InMemorySecretStore, set_secret_store_for_tests
        store = InMemorySecretStore()
        set_secret_store_for_tests(store)
    """

    def __init__(self) -> None:
        # key: datastore_id -> {"ciphertext": bytes, "nonce": bytes,
        #                       "key_version": int, "org_id": str}
        self._store: dict[str, dict[str, Any]] = {}

    def reset(self) -> None:
        """Clear all stored data."""
        self._store.clear()

    async def put(self, datastore_id: str, org_id: str, secret: dict[str, Any]) -> None:
        """Encrypt *secret* and store in memory (upsert semantics)."""
        ciphertext, nonce, key_version = encrypt_json(secret)
        self._store[str(datastore_id)] = {
            "ciphertext": ciphertext,
            "nonce": nonce,
            "key_version": key_version,
            "org_id": str(org_id),
        }

    async def get(self, datastore_id: str, org_id: str) -> dict[str, Any] | None:
        """Return decrypted secret or None if absent / wrong org."""
        row = self._store.get(str(datastore_id))
        if row is None:
            return None
        # Org-scope check: a different org cannot access this secret.
        if row["org_id"] != str(org_id):
            return None
        return decrypt_json(row["ciphertext"], row["nonce"], row["key_version"])

    async def delete(self, datastore_id: str, org_id: str) -> None:
        """Remove secret if it exists and belongs to org_id."""
        key = str(datastore_id)
        row = self._store.get(key)
        if row is not None and row["org_id"] == str(org_id):
            del self._store[key]


# ---------------------------------------------------------------------------
# Provider singleton
# ---------------------------------------------------------------------------

_secret_store: SecretStore | None = None


def set_secret_store_for_tests(store: SecretStore | None) -> None:
    """Inject a test double (or reset to default PgSecretStore by passing None).

    Parameters
    ----------
    store:
        An ``InMemorySecretStore`` instance for tests, or ``None`` to restore
        the default production ``PgSecretStore``.
    """
    global _secret_store
    _secret_store = store


def get_secret_store() -> SecretStore:
    """Return the active SecretStore singleton.

    Lazily instantiates a ``PgSecretStore`` on first call if no override has
    been set via ``set_secret_store_for_tests()``.

    Returns
    -------
    SecretStore
        The active store implementation.
    """
    global _secret_store
    if _secret_store is None:
        _secret_store = PgSecretStore()
    return _secret_store
