"""Bridge row storage — org-scoped CRUD over the ``bridges`` table (§M22-A).

Mirrors :mod:`app.auth.bridge_tokens` exactly: an interface class, a
Postgres-backed production implementation (``PgBridgeStore``), and a
dict-backed test double (``InMemoryBridgeStore``) swapped in by
``backend/tests/conftest.py`` (the hand-rolled ``FakeDB`` test fixture has no
concept of a ``bridges`` table, so tests never exercise ``PgBridgeStore`` —
same reasoning as the token store and the API-key store).

Provider pattern
----------------
A module-level singleton via :func:`get_bridge_store` (lazy ``PgBridgeStore``
default). Tests swap in :class:`InMemoryBridgeStore` via
:func:`set_bridge_store_for_tests`.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class BridgeStore:
    """Interface for bridge-row storage. See module docstring for the pattern."""

    async def create(
        self, org_id: str, created_by: str, name: str, config: dict[str, Any]
    ) -> dict[str, Any]:
        raise NotImplementedError

    async def list(self, org_id: str) -> list[dict[str, Any]]:
        raise NotImplementedError

    async def get(self, org_id: str, bridge_id: str) -> dict[str, Any] | None:
        raise NotImplementedError

    async def get_by_id(self, bridge_id: str) -> dict[str, Any] | None:
        """Fetch a bridge row with NO org filter.

        Used only by the WS tunnel handshake, where the agent presents a
        bridge_id + token and no org context — the token itself is the
        authenticator (bound to an org at token-mint time), not org-scoping.
        """
        raise NotImplementedError

    async def delete(self, org_id: str, bridge_id: str) -> bool:
        raise NotImplementedError

    async def heartbeat(self, org_id: str, bridge_id: str) -> dict[str, Any] | None:
        """Org-scoped liveness signal: status='online' + last_seen_at=now()."""
        raise NotImplementedError

    async def set_status(
        self, bridge_id: str, status: str, *, touch_last_seen: bool = False
    ) -> dict[str, Any] | None:
        """By-id (no org scope) status transition for internal callers that

        already know the row is theirs (the WS handler, post-token-auth; the
        token-revoke route, post-ownership-check) — as opposed to
        :meth:`heartbeat`, which IS the org-scope guard for its caller.
        """
        raise NotImplementedError

    def reset(self) -> None:
        """Test helper; no-op for the Pg store, clears state for InMemory."""


# ---------------------------------------------------------------------------
# Postgres implementation
# ---------------------------------------------------------------------------


def _row_to_public(record: Any) -> dict[str, Any]:
    """Convert an asyncpg Record to the plain dict shape ``BridgeOut`` expects."""
    row: dict[str, Any] = dict(record)
    for key, value in row.items():
        if isinstance(value, datetime):
            row[key] = value.isoformat()
        elif isinstance(value, uuid.UUID):
            row[key] = str(value)
    if isinstance(row.get("config"), str):
        try:
            row["config"] = json.loads(row["config"])
        except (ValueError, TypeError):
            row["config"] = {}
    return row


class PgBridgeStore(BridgeStore):
    """asyncpg-backed bridge store over the ``bridges`` table."""

    async def create(
        self, org_id: str, created_by: str, name: str, config: dict[str, Any]
    ) -> dict[str, Any]:
        from app.db import fetchrow  # local import to avoid circular load

        config_json = json.dumps(config)
        row = await fetchrow(
            """
            INSERT INTO bridges (org_id, created_by, name, status, config)
            VALUES ($1::uuid, $2::uuid, $3, 'offline', $4::jsonb)
            RETURNING *
            """,
            org_id,
            created_by,
            name,
            config_json,
        )
        return _row_to_public(row)

    async def list(self, org_id: str) -> list[dict[str, Any]]:
        from app.db import fetch  # local import

        rows = await fetch(
            "SELECT * FROM bridges WHERE org_id = $1::uuid ORDER BY created_at",
            org_id,
        )
        return [_row_to_public(r) for r in rows]

    async def get(self, org_id: str, bridge_id: str) -> dict[str, Any] | None:
        from app.db import fetchrow  # local import

        row = await fetchrow(
            "SELECT * FROM bridges WHERE id = $1::uuid AND org_id = $2::uuid",
            bridge_id,
            org_id,
        )
        return _row_to_public(row) if row is not None else None

    async def get_by_id(self, bridge_id: str) -> dict[str, Any] | None:
        from app.db import fetchrow  # local import

        row = await fetchrow("SELECT * FROM bridges WHERE id = $1::uuid", bridge_id)
        return _row_to_public(row) if row is not None else None

    async def delete(self, org_id: str, bridge_id: str) -> bool:
        from app.db import execute  # local import

        status = await execute(
            "DELETE FROM bridges WHERE id = $1::uuid AND org_id = $2::uuid",
            bridge_id,
            org_id,
        )
        try:
            return int(status.split()[-1]) > 0
        except (IndexError, ValueError, AttributeError):
            return False

    async def heartbeat(self, org_id: str, bridge_id: str) -> dict[str, Any] | None:
        from app.db import fetchrow  # local import

        row = await fetchrow(
            """
            UPDATE bridges
            SET status = 'online', last_seen_at = now(), updated_at = now()
            WHERE id = $1::uuid AND org_id = $2::uuid
            RETURNING *
            """,
            bridge_id,
            org_id,
        )
        return _row_to_public(row) if row is not None else None

    async def set_status(
        self, bridge_id: str, status: str, *, touch_last_seen: bool = False
    ) -> dict[str, Any] | None:
        from app.db import fetchrow  # local import

        if touch_last_seen:
            row = await fetchrow(
                """
                UPDATE bridges
                SET status = $2, last_seen_at = now(), updated_at = now()
                WHERE id = $1::uuid
                RETURNING *
                """,
                bridge_id,
                status,
            )
        else:
            row = await fetchrow(
                """
                UPDATE bridges
                SET status = $2, updated_at = now()
                WHERE id = $1::uuid
                RETURNING *
                """,
                bridge_id,
                status,
            )
        return _row_to_public(row) if row is not None else None


# ---------------------------------------------------------------------------
# In-memory implementation (tests)
# ---------------------------------------------------------------------------


class InMemoryBridgeStore(BridgeStore):
    """Dict-backed store for tests (no DB) — behaviourally equivalent to Pg."""

    def __init__(self) -> None:
        # {bridge_id: bridge_dict}
        self._rows: dict[str, dict[str, Any]] = {}

    def reset(self) -> None:
        """Clear all stored bridges. Called by tests between runs."""
        self._rows.clear()

    async def create(
        self, org_id: str, created_by: str, name: str, config: dict[str, Any]
    ) -> dict[str, Any]:
        from copy import deepcopy

        bridge_id = str(uuid.uuid4())
        now = _now_iso()
        row: dict[str, Any] = {
            "id": bridge_id,
            "org_id": str(org_id),
            "created_by": str(created_by),
            "name": name,
            "status": "offline",
            "last_seen_at": None,
            "config": deepcopy(config),
            "created_at": now,
            "updated_at": now,
        }
        self._rows[bridge_id] = row
        return deepcopy(row)

    async def list(self, org_id: str) -> list[dict[str, Any]]:
        from copy import deepcopy

        rows = [
            deepcopy(r) for r in self._rows.values() if str(r["org_id"]) == str(org_id)
        ]
        rows.sort(key=lambda r: r["created_at"])
        return rows

    async def get(self, org_id: str, bridge_id: str) -> dict[str, Any] | None:
        from copy import deepcopy

        row = self._rows.get(str(bridge_id))
        if row is None or str(row["org_id"]) != str(org_id):
            return None
        return deepcopy(row)

    async def get_by_id(self, bridge_id: str) -> dict[str, Any] | None:
        from copy import deepcopy

        row = self._rows.get(str(bridge_id))
        return deepcopy(row) if row is not None else None

    async def delete(self, org_id: str, bridge_id: str) -> bool:
        row = self._rows.get(str(bridge_id))
        if row is None or str(row["org_id"]) != str(org_id):
            return False
        del self._rows[str(bridge_id)]
        return True

    async def heartbeat(self, org_id: str, bridge_id: str) -> dict[str, Any] | None:
        from copy import deepcopy

        row = self._rows.get(str(bridge_id))
        if row is None or str(row["org_id"]) != str(org_id):
            return None
        row["status"] = "online"
        row["last_seen_at"] = _now_iso()
        row["updated_at"] = _now_iso()
        return deepcopy(row)

    async def set_status(
        self, bridge_id: str, status: str, *, touch_last_seen: bool = False
    ) -> dict[str, Any] | None:
        from copy import deepcopy

        row = self._rows.get(str(bridge_id))
        if row is None:
            return None
        row["status"] = status
        row["updated_at"] = _now_iso()
        if touch_last_seen:
            row["last_seen_at"] = _now_iso()
        return deepcopy(row)


# ---------------------------------------------------------------------------
# Provider singleton
# ---------------------------------------------------------------------------

_store: BridgeStore | None = None


def set_bridge_store_for_tests(store: BridgeStore | None) -> None:
    """Inject a test double (or pass None to restore the default Pg store)."""
    global _store
    _store = store


def get_bridge_store() -> BridgeStore:
    """Return the active :class:`BridgeStore` singleton (lazy Pg default)."""
    global _store
    if _store is None:
        _store = PgBridgeStore()
    return _store


def reset_bridge_store() -> None:
    """Reset the active store — test helper, mirrors the connector registry pattern."""
    get_bridge_store().reset()
