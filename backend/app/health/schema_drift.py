"""Schema-drift detection — compare live columns to the last snapshot.

Usage (best-effort, fire-and-forget)
-------------------------------------
Call :func:`detect_schema_drift` after a successful query/dataset execution
where column metadata is available.  It is designed to be scheduled as an
asyncio background task so it **never** blocks or breaks the calling path::

    import asyncio
    from app.health.schema_drift import detect_schema_drift

    asyncio.ensure_future(
        detect_schema_drift(org_id, dataset_key, current_columns)
    )

Behaviour
---------
- **First observation** (no snapshot exists): insert the snapshot, emit NO
  events.  There is no baseline to compare against, so nothing has "drifted".
- **Unchanged**: columns match the snapshot exactly — skip entirely (O(n) set
  comparison, no writes).
- **Changed**: write ``schema_drift_events`` rows (one per column diff), upsert
  the snapshot, and fire a ``SCHEMA_DRIFT`` webhook event.

Column format
-------------
Each column dict must have at minimum ``name`` (str) and ``type`` (str).
Extra keys are ignored.

Org-scoping
-----------
All reads and writes are scoped by (org_id, dataset_key) so org A's snapshots
can never interfere with org B's.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("nubi.health.schema_drift")


# ---------------------------------------------------------------------------
# In-memory store (used by tests — mirrors InMemoryFreshnessStore pattern)
# ---------------------------------------------------------------------------

class InMemoryDriftStore:
    """Dict-backed schema-drift store for tests (no DB required)."""

    def __init__(self) -> None:
        self._snapshots: dict[tuple[str, str], list[dict]] = {}
        self._events: list[dict] = []

    def get_snapshot(self, org_id: str, dataset_key: str) -> list[dict] | None:
        return self._snapshots.get((org_id, dataset_key))

    def upsert_snapshot(
        self, org_id: str, dataset_key: str, columns: list[dict]
    ) -> None:
        self._snapshots[(org_id, dataset_key)] = list(columns)

    def insert_events(self, events: list[dict]) -> None:
        self._events.extend(events)

    def list_events(
        self,
        org_id: str,
        dataset_key: str | None = None,
        limit: int = 100,
    ) -> list[dict]:
        rows = [e for e in self._events if e["org_id"] == org_id]
        if dataset_key is not None:
            rows = [e for e in rows if e["dataset_key"] == dataset_key]
        return rows[-limit:]

    def reset(self) -> None:
        self._snapshots.clear()
        self._events.clear()


# ---------------------------------------------------------------------------
# Postgres store (prod)
# ---------------------------------------------------------------------------

class PgDriftStore:
    """asyncpg-backed store using dataset_schema_snapshots + schema_drift_events."""

    async def get_snapshot(
        self, org_id: str, dataset_key: str
    ) -> list[dict] | None:
        from app.db import fetchrow  # noqa: PLC0415

        row = await fetchrow(
            """
            SELECT columns
            FROM   dataset_schema_snapshots
            WHERE  org_id = $1::uuid AND dataset_key = $2
            """,
            org_id,
            dataset_key,
        )
        if row is None:
            return None
        cols = row["columns"]
        if isinstance(cols, str):
            import json
            cols = json.loads(cols)
        return list(cols) if cols else []

    async def upsert_snapshot(
        self, org_id: str, dataset_key: str, columns: list[dict]
    ) -> None:
        import json
        from app.db import execute  # noqa: PLC0415

        await execute(
            """
            INSERT INTO dataset_schema_snapshots (org_id, dataset_key, columns, captured_at)
            VALUES ($1::uuid, $2, $3::jsonb, $4)
            ON CONFLICT (org_id, dataset_key) DO UPDATE SET
                columns     = EXCLUDED.columns,
                captured_at = EXCLUDED.captured_at
            """,
            org_id,
            dataset_key,
            json.dumps(columns),
            datetime.now(timezone.utc),
        )

    async def insert_events(self, events: list[dict]) -> None:
        from app.db import execute  # noqa: PLC0415

        for ev in events:
            await execute(
                """
                INSERT INTO schema_drift_events
                    (org_id, dataset_key, change_type, column_name,
                     from_type, to_type, detected_at)
                VALUES ($1::uuid, $2, $3, $4, $5, $6, $7)
                """,
                ev["org_id"],
                ev["dataset_key"],
                ev["change_type"],
                ev["column_name"],
                ev.get("from_type"),
                ev.get("to_type"),
                ev.get("detected_at") or datetime.now(timezone.utc),
            )

    async def list_events(
        self,
        org_id: str,
        dataset_key: str | None = None,
        limit: int = 100,
    ) -> list[dict]:
        from app.db import fetch  # noqa: PLC0415

        if dataset_key is not None:
            rows = await fetch(
                """
                SELECT id, org_id, dataset_key, change_type, column_name,
                       from_type, to_type, detected_at
                FROM   schema_drift_events
                WHERE  org_id = $1::uuid AND dataset_key = $2
                ORDER  BY detected_at DESC
                LIMIT  $3
                """,
                org_id,
                dataset_key,
                limit,
            )
        else:
            rows = await fetch(
                """
                SELECT id, org_id, dataset_key, change_type, column_name,
                       from_type, to_type, detected_at
                FROM   schema_drift_events
                WHERE  org_id = $1::uuid
                ORDER  BY detected_at DESC
                LIMIT  $2
                """,
                org_id,
                limit,
            )
        return [_row_to_event_dict(r) for r in rows]

    async def get_snapshot_public(
        self, org_id: str, dataset_key: str
    ) -> list[dict] | None:
        return await self.get_snapshot(org_id, dataset_key)


def _row_to_event_dict(row: Any) -> dict:
    return {
        "id": str(row["id"]),
        "org_id": str(row["org_id"]),
        "dataset_key": str(row["dataset_key"]),
        "change_type": str(row["change_type"]),
        "column_name": str(row["column_name"]),
        "from_type": row["from_type"],
        "to_type": row["to_type"],
        "detected_at": row["detected_at"].isoformat() if row["detected_at"] else None,
    }


# ---------------------------------------------------------------------------
# Singleton provider
# ---------------------------------------------------------------------------

_store: InMemoryDriftStore | PgDriftStore | None = None


def get_drift_store() -> InMemoryDriftStore | PgDriftStore:
    global _store
    if _store is None:
        _store = PgDriftStore()
    return _store


def set_drift_store(store: InMemoryDriftStore | PgDriftStore | None) -> None:
    global _store
    _store = store


def reset_for_tests() -> None:
    """Reset the singleton (test teardown only)."""
    global _store
    _store = None


# ---------------------------------------------------------------------------
# Core diff logic (pure Python, no I/O)
# ---------------------------------------------------------------------------

def _diff_columns(
    old_cols: list[dict],
    new_cols: list[dict],
) -> list[dict]:
    """Compute the minimal set of drift events between two column lists.

    Parameters
    ----------
    old_cols:
        Previous snapshot columns — list of {name, type} dicts.
    new_cols:
        Current (live) columns — list of {name, type} dicts.

    Returns
    -------
    list[dict]
        One dict per changed column with keys:
        ``change_type``, ``column_name``, ``from_type``, ``to_type``.
    """
    old_map: dict[str, str] = {c["name"]: c["type"] for c in old_cols}
    new_map: dict[str, str] = {c["name"]: c["type"] for c in new_cols}

    events: list[dict] = []

    # Added columns
    for name, typ in new_map.items():
        if name not in old_map:
            events.append(
                {
                    "change_type": "added",
                    "column_name": name,
                    "from_type": None,
                    "to_type": typ,
                }
            )

    # Removed columns
    for name, typ in old_map.items():
        if name not in new_map:
            events.append(
                {
                    "change_type": "removed",
                    "column_name": name,
                    "from_type": typ,
                    "to_type": None,
                }
            )

    # Type-changed columns
    for name in old_map:
        if name in new_map and old_map[name] != new_map[name]:
            events.append(
                {
                    "change_type": "type_changed",
                    "column_name": name,
                    "from_type": old_map[name],
                    "to_type": new_map[name],
                }
            )

    return events


# ---------------------------------------------------------------------------
# Public detection function
# ---------------------------------------------------------------------------

async def detect_schema_drift(
    org_id: str,
    dataset_key: str,
    current_columns: list[dict],
) -> None:
    """Compare *current_columns* to the last snapshot and record any drift.

    This function is designed to be called fire-and-forget (e.g. via
    ``asyncio.ensure_future``).  It NEVER raises — all errors are logged.

    Parameters
    ----------
    org_id:
        The owning organisation (always required; this function is a no-op when
        falsy to keep the demo/anonymous path safe).
    dataset_key:
        The dataset identifier (e.g. ``"raw/orders"``).
    current_columns:
        The live columns as returned by the connector/query result, each a dict
        with at minimum ``name`` (str) and ``type`` (str).
    """
    if not org_id or not dataset_key or current_columns is None:
        return

    # Normalise: keep only name + type to ensure stable comparison.
    cols = [{"name": str(c["name"]), "type": str(c["type"])} for c in current_columns]

    try:
        store = get_drift_store()
        now = datetime.now(timezone.utc)

        # --- get_snapshot may be sync (InMemory) or async (Pg) ---
        _snap_result = store.get_snapshot(org_id, dataset_key)
        if hasattr(_snap_result, "__await__"):
            old_cols = await _snap_result
        else:
            old_cols = _snap_result

        if old_cols is None:
            # First observation — just store the baseline; no events.
            _upsert = store.upsert_snapshot(org_id, dataset_key, cols)
            if hasattr(_upsert, "__await__"):
                await _upsert
            logger.debug(
                "schema_drift: first snapshot for org=%s dataset=%s (%d cols)",
                org_id,
                dataset_key,
                len(cols),
            )
            return

        # Compare.
        changes = _diff_columns(old_cols, cols)
        if not changes:
            # No drift — skip all writes.
            return

        # Build event rows with org context.
        event_rows = [
            {
                "org_id": org_id,
                "dataset_key": dataset_key,
                "detected_at": now,
                **ch,
            }
            for ch in changes
        ]

        # Persist events.
        _insert = store.insert_events(event_rows)
        if hasattr(_insert, "__await__"):
            await _insert

        # Upsert snapshot (must happen AFTER events are written).
        _upsert = store.upsert_snapshot(org_id, dataset_key, cols)
        if hasattr(_upsert, "__await__"):
            await _upsert

        logger.info(
            "schema_drift: %d change(s) for org=%s dataset=%s",
            len(changes),
            org_id,
            dataset_key,
        )

        # Emit webhook (fire-and-forget; never raises).
        try:
            from app.webhooks.events import emit_schema_drift  # noqa: PLC0415

            emit_schema_drift(
                org_id,
                dataset_key=dataset_key,
                changes=changes,
            )
        except Exception as _wh_exc:  # noqa: BLE001
            logger.warning(
                "schema_drift: webhook emit failed for org=%s dataset=%s: %s",
                org_id,
                dataset_key,
                _wh_exc,
            )

    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "schema_drift: detection failed for org=%s dataset=%s: %s",
            org_id,
            dataset_key,
            exc,
        )
