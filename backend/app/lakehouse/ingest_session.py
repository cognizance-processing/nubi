"""Ingest-session store — idempotent, state-machine-gated, durable-sidecar.

An *ingest session* is the server-side handle for a single producer publish
cycle (open → upload-parts → commit/abort).  Its lifecycle mirrors the
write-back engine's ``WritebackStore`` pattern: an in-process index covers
the hot path; the durable bits (manifest, terminal result, idempotency
index) are also persisted as a JSON sidecar under the lake prefix so a
restart does not lose an in-flight publish.

Layout (object-storage sidecar, file-backed in OSS/local dev)
-------------------------------------------------------------
::

    <lake-prefix>/_nubi/sessions/<session_id>/session.json

The ``(org_id, datastore_id, idempotency_key) → session_id`` index is
persisted as:

::

    <lake-prefix>/_nubi/sessions/_idem_index.json

In-process state
----------------
The ``InMemoryIngestSessionStore`` holds all sessions in two dicts:

* ``_sessions`` — ``session_id → session record``
* ``_idem_index`` — ``(org_id, datastore_id, idempotency_key) → session_id``

Both dicts are the authoritative hot-path store.  The JSON sidecar in object
storage provides restart durability (the hot-path dicts are populated on
``get_session`` / ``get_by_idempotency_key`` cache-miss).

State machine
-------------
::

    open  →  committing  →  committed (terminal)
      │
      └──  aborted (terminal)

``open``: parts can be uploaded; commit has not been attempted.
``committing``: the server is executing promote (transient lock; only ONE
    concurrent commit succeeds when multiple requests race).
``committed``: the session is closed; re-commit returns the stored result
    (idempotent).
``aborted``: the session was aborted; staging was cleaned up.

Production TODO
---------------
Replace ``InMemoryIngestSessionStore`` with a ``PgIngestSessionStore``
that uses ``INSERT … ON CONFLICT DO NOTHING`` and CAS via ``WHERE state = $N``
— the same pattern as ``PgWritebackStore``.  No DB migration is added in this
branch; the sidecar provides partial durability in the meantime.
"""

from __future__ import annotations

import json
import logging
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("nubi.lakehouse.ingest_session")

# ---------------------------------------------------------------------------
# State machine constants
# ---------------------------------------------------------------------------

_VALID_STATES = {"open", "committing", "committed", "aborted"}
_TERMINAL_STATES = {"committed", "aborted"}


# ---------------------------------------------------------------------------
# IngestSessionStore — abstract interface
# ---------------------------------------------------------------------------


class IngestSessionStore:
    """Abstract interface for the ingest-session persistence layer.

    All operations are org-scoped: cross-org ids are treated as not-found
    (no information leak).
    """

    async def create(
        self,
        org_id: str,
        datastore_id: str,
        user_id: str,
        mode: str,
        idempotency_key: str,
        schema: list[dict[str, str]],
        partition: str | None,
        run_id: str,
        table_name: str = "default",
    ) -> dict[str, Any]:
        """Create a new open session.  Must be idempotent via ``idempotency_key``."""
        raise NotImplementedError

    async def get_by_idempotency_key(
        self,
        org_id: str,
        datastore_id: str,
        idempotency_key: str,
    ) -> dict[str, Any] | None:
        raise NotImplementedError

    async def get(
        self,
        org_id: str,
        datastore_id: str,
        session_id: str,
    ) -> dict[str, Any] | None:
        raise NotImplementedError

    async def transition(
        self,
        org_id: str,
        datastore_id: str,
        session_id: str,
        new_state: str,
        *,
        from_state: str | None = None,
        result: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> dict[str, Any] | None:
        """Transition the session to ``new_state``.

        When ``from_state`` is supplied the update is a compare-and-swap (CAS):
        it only succeeds when the current state equals ``from_state``.  Returns
        ``None`` when the session does not exist or the CAS predicate failed
        (another concurrent caller already won the transition race).
        """
        raise NotImplementedError


# ---------------------------------------------------------------------------
# InMemoryIngestSessionStore
# ---------------------------------------------------------------------------


class InMemoryIngestSessionStore(IngestSessionStore):
    """Dict-backed in-memory implementation for tests and local dev.

    Object-storage sidecar persistence is attempted best-effort on every
    write so the state survives a worker restart when a lake is configured.
    """

    def __init__(self) -> None:
        # session_id → record
        self._sessions: dict[str, dict[str, Any]] = {}
        # (org_id, datastore_id, idempotency_key) → session_id
        self._idem_index: dict[tuple[str, str, str], str] = {}

    def reset(self) -> None:
        self._sessions.clear()
        self._idem_index.clear()

    # ── helpers ──────────────────────────────────────────────────────────────

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    # ── CRUD ─────────────────────────────────────────────────────────────────

    async def create(
        self,
        org_id: str,
        datastore_id: str,
        user_id: str,
        mode: str,
        idempotency_key: str,
        schema: list[dict[str, str]],
        partition: str | None,
        run_id: str,
        table_name: str = "default",
    ) -> dict[str, Any]:
        session_id = str(uuid.uuid4())
        now = self._now()
        record: dict[str, Any] = {
            "id": session_id,
            "org_id": org_id,
            "datastore_id": datastore_id,
            "user_id": user_id,
            "mode": mode,
            "idempotency_key": idempotency_key,
            "schema": list(schema),
            "partition": partition,
            "table_name": table_name,
            "run_id": run_id,
            "state": "open",
            "result": None,
            "error": None,
            "created_at": now,
            "updated_at": now,
        }
        self._sessions[session_id] = record
        self._idem_index[(org_id, datastore_id, idempotency_key)] = session_id
        _sidecar_write(org_id, datastore_id, session_id, record)
        return deepcopy(record)

    async def get_by_idempotency_key(
        self,
        org_id: str,
        datastore_id: str,
        idempotency_key: str,
    ) -> dict[str, Any] | None:
        key = (org_id, datastore_id, idempotency_key)
        session_id = self._idem_index.get(key)
        if session_id is None:
            # Try the object-storage sidecar index on cache-miss.
            session_id = _sidecar_idem_lookup(org_id, datastore_id, idempotency_key)
            if session_id is None:
                return None
        record = self._sessions.get(session_id)
        if record is None:
            record = _sidecar_load(org_id, datastore_id, session_id)
            if record is None:
                return None
            self._sessions[session_id] = record
            self._idem_index[key] = session_id
        if str(record.get("org_id")) != str(org_id):
            return None  # cross-org gate
        if str(record.get("datastore_id")) != str(datastore_id):
            return None
        return deepcopy(record)

    async def get(
        self,
        org_id: str,
        datastore_id: str,
        session_id: str,
    ) -> dict[str, Any] | None:
        record = self._sessions.get(str(session_id))
        if record is None:
            record = _sidecar_load(org_id, datastore_id, session_id)
            if record is None:
                return None
            self._sessions[session_id] = record
        if str(record.get("org_id")) != str(org_id):
            return None
        if str(record.get("datastore_id")) != str(datastore_id):
            return None
        return deepcopy(record)

    async def transition(
        self,
        org_id: str,
        datastore_id: str,
        session_id: str,
        new_state: str,
        *,
        from_state: str | None = None,
        result: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> dict[str, Any] | None:
        record = self._sessions.get(str(session_id))
        if record is None:
            return None
        if str(record.get("org_id")) != str(org_id):
            return None
        if str(record.get("datastore_id")) != str(datastore_id):
            return None
        # CAS check.
        if from_state is not None and record["state"] != from_state:
            return None
        record["state"] = new_state
        record["updated_at"] = self._now()
        if result is not None:
            record["result"] = deepcopy(result)
        if error is not None:
            record["error"] = error
        _sidecar_write(org_id, datastore_id, session_id, record)
        return deepcopy(record)


# ---------------------------------------------------------------------------
# Module singleton
# ---------------------------------------------------------------------------

_store: IngestSessionStore | None = None


def get_ingest_session_store() -> IngestSessionStore:
    """Return the active ingest-session store (lazily constructed)."""
    global _store
    if _store is None:
        _store = InMemoryIngestSessionStore()
    return _store


def set_ingest_session_store(store: IngestSessionStore | None) -> None:
    """Override the active store.  Pass ``None`` to reset to the default."""
    global _store
    _store = store


# ---------------------------------------------------------------------------
# Object-storage sidecar helpers (best-effort durability)
# ---------------------------------------------------------------------------
#
# Layout:
#   <lake-prefix>/_nubi/sessions/<session_id>/session.json
#   <lake-prefix>/_nubi/sessions/_idem_index.json
#
# These helpers swallow all exceptions so a missing / misconfigured lake never
# breaks the in-memory path.


def _lake_storage(org_id: str, datastore_id: str) -> "tuple[Any, str] | None":
    """Return ``(storage_client, lake_key_prefix)`` for one managed datastore.

    Returns ``None`` when no central storage is configured.
    """
    try:
        from app.lakehouse.managed import (  # noqa: PLC0415
            resolve_central_storage,
            lake_prefix,
        )

        central = resolve_central_storage()
        if central is None:
            return None
        if central.scheme == "file":
            from app.storage.local import LocalStorageClient  # noqa: PLC0415

            client = LocalStorageClient(root=central.bucket)
        else:
            from app.storage.base import get_storage_client  # noqa: PLC0415

            client = get_storage_client(central.base_uri(), central.creds or None)
        prefix = lake_prefix(org_id, datastore_id)
        return client, prefix
    except Exception:  # noqa: BLE001
        return None


def _sidecar_write(
    org_id: str, datastore_id: str, session_id: str, record: dict[str, Any]
) -> None:
    """Best-effort: persist *record* as a JSON sidecar under the lake prefix."""
    pair = _lake_storage(org_id, datastore_id)
    if pair is None:
        return
    client, prefix = pair
    key = f"{prefix}_nubi/sessions/{session_id}/session.json"
    try:
        client.upload_bytes(json.dumps(record).encode(), key)
    except Exception:  # noqa: BLE001
        logger.debug("ingest-session: sidecar write failed for %s", session_id, exc_info=True)

    # Update the idempotency index sidecar.
    idem_key = f"{prefix}_nubi/sessions/_idem_index.json"
    try:
        existing: dict[str, str] = {}
        try:
            raw = client.download_bytes(idem_key)
            existing = json.loads(raw.decode())
        except Exception:  # noqa: BLE001
            pass
        ikey = record.get("idempotency_key", "")
        if ikey:
            existing[ikey] = session_id
        client.upload_bytes(json.dumps(existing).encode(), idem_key)
    except Exception:  # noqa: BLE001
        logger.debug("ingest-session: idem-index sidecar write failed", exc_info=True)


def _sidecar_load(
    org_id: str, datastore_id: str, session_id: str
) -> dict[str, Any] | None:
    """Best-effort: load a session record from the object-storage sidecar."""
    pair = _lake_storage(org_id, datastore_id)
    if pair is None:
        return None
    client, prefix = pair
    key = f"{prefix}_nubi/sessions/{session_id}/session.json"
    try:
        raw = client.download_bytes(key)
        return json.loads(raw.decode())
    except Exception:  # noqa: BLE001
        return None


def _sidecar_idem_lookup(
    org_id: str, datastore_id: str, idempotency_key: str
) -> str | None:
    """Best-effort: look up a session_id by idempotency_key from the sidecar."""
    pair = _lake_storage(org_id, datastore_id)
    if pair is None:
        return None
    client, prefix = pair
    idem_key = f"{prefix}_nubi/sessions/_idem_index.json"
    try:
        raw = client.download_bytes(idem_key)
        index: dict[str, str] = json.loads(raw.decode())
        return index.get(idempotency_key)
    except Exception:  # noqa: BLE001
        return None
