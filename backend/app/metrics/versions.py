"""Metric spec version history — in-memory store (tests) + Pg helper (prod).

Mirrors the shape of ``InMemoryFlowStore``'s flow spec version methods:
  - ``add_metric_version``   — snapshot a new spec version (monotonic per metric_id)
  - ``list_metric_versions`` — all versions for a metric, ordered by version ASC
  - ``get_metric_version``   — one specific version by integer version number

``InMemoryMetricVersionStore`` is the dict-backed store used in tests.
``pg_add_metric_version`` / ``pg_list_metric_versions`` / ``pg_get_metric_version``
are thin async helpers that talk to the ``metric_spec_versions`` table
(migration 0023) — called best-effort from routes; silently swallowed on no-DB
paths exactly like ``_persist_metric`` does for metric saves.

Module-level singleton
----------------------
``get_metric_version_store()`` returns the configured singleton.
``set_metric_version_store(store)`` lets tests inject a fresh instance.
"""
from __future__ import annotations

import uuid
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any


# ---------------------------------------------------------------------------
# InMemoryMetricVersionStore
# ---------------------------------------------------------------------------


class InMemoryMetricVersionStore:
    """Dict-backed metric version store for unit tests.

    Thread-safety: asyncio is single-threaded; no locking needed.
    """

    def __init__(self) -> None:
        self._versions: dict[str, dict] = {}          # version_id → record
        self._index: dict[str, list[str]] = {}         # metric_id → [version_id, ...]

    async def add_metric_version(
        self,
        metric_id: str,
        org_id: str,
        spec: dict[str, Any],
        created_by: str | None = None,
        note: str | None = None,
    ) -> dict[str, Any]:
        """Snapshot *spec* as the next version for *metric_id*.

        Version numbers are monotonically increasing per metric_id (1-based).
        Returns the stored version record.
        """
        version_id = str(uuid.uuid4())
        ids = self._index.setdefault(str(metric_id), [])
        version_num = len(ids) + 1
        now = datetime.now(timezone.utc)
        record: dict[str, Any] = {
            "id": version_id,
            "org_id": str(org_id),
            "metric_id": str(metric_id),
            "version": version_num,
            "spec": deepcopy(spec),
            "created_by": str(created_by) if created_by else None,
            "created_at": now,
            "note": note,
        }
        self._versions[version_id] = record
        ids.append(version_id)
        return deepcopy(record)

    async def list_metric_versions(self, metric_id: str) -> list[dict[str, Any]]:
        """Return all versions for *metric_id*, ordered by version ASC."""
        ids = self._index.get(str(metric_id), [])
        return [
            deepcopy(self._versions[vid])
            for vid in ids
            if vid in self._versions
        ]

    async def get_metric_version(
        self, metric_id: str, version: int
    ) -> dict[str, Any] | None:
        """Return the version record for *version*, or ``None`` if not found."""
        ids = self._index.get(str(metric_id), [])
        for vid in ids:
            rec = self._versions.get(vid)
            if rec and rec["version"] == version:
                return deepcopy(rec)
        return None


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_store: InMemoryMetricVersionStore | None = None


def get_metric_version_store() -> InMemoryMetricVersionStore:
    """Return (or create) the module-level singleton."""
    global _store
    if _store is None:
        _store = InMemoryMetricVersionStore()
    return _store


def set_metric_version_store(store: InMemoryMetricVersionStore | None) -> None:
    """Replace the singleton (test injection hook)."""
    global _store
    _store = store


def reset_metric_version_store_for_tests() -> None:
    """Reset the singleton to a fresh empty store (called by conftest)."""
    global _store
    _store = InMemoryMetricVersionStore()


# ---------------------------------------------------------------------------
# Pg helpers (best-effort; called from routes)
# ---------------------------------------------------------------------------


async def pg_add_metric_version(
    metric_id: str,
    org_id: str,
    spec: dict[str, Any],
    created_by: str | None = None,
    note: str | None = None,
) -> None:
    """Insert a new version row into ``metric_spec_versions``.

    Derives the next version number from ``MAX(version)`` for this metric_id.
    Best-effort: swallowed silently on no-DB paths.
    """
    import json  # noqa: PLC0415

    try:
        from app.db import execute, fetchrow  # noqa: PLC0415

        row = await fetchrow(
            "SELECT COALESCE(MAX(version), 0) AS max_v "
            "FROM metric_spec_versions WHERE metric_id = $1",
            str(metric_id),
        )
        next_v = (row["max_v"] if row and row.get("max_v") is not None else 0) + 1
        await execute(
            """
            INSERT INTO metric_spec_versions
                (id, org_id, metric_id, version, spec, created_by, note)
            VALUES ($1::uuid, $2::uuid, $3, $4, $5::jsonb, $6, $7)
            """,
            str(uuid.uuid4()),
            str(org_id),
            str(metric_id),
            next_v,
            json.dumps(spec),
            str(created_by) if created_by else None,
            note,
        )
    except Exception:  # noqa: BLE001 — persistence is best-effort
        pass


async def pg_list_metric_versions(
    metric_id: str, org_id: str
) -> list[dict[str, Any]]:
    """Return all version rows for *metric_id* scoped to *org_id*, ASC."""
    try:
        from app.db import fetch  # noqa: PLC0415

        rows = await fetch(
            "SELECT id, org_id, metric_id, version, spec, created_by, created_at, note "
            "FROM metric_spec_versions "
            "WHERE metric_id = $1 AND org_id = $2::uuid "
            "ORDER BY version ASC",
            str(metric_id),
            str(org_id),
        )
        result = []
        for r in rows:
            d = dict(r)
            for key in ("id", "org_id", "created_by"):
                if d.get(key) is not None and not isinstance(d[key], str):
                    d[key] = str(d[key])
            val = d.get("created_at")
            if isinstance(val, datetime) and val.tzinfo is None:
                d["created_at"] = val.replace(tzinfo=timezone.utc)
            if d.get("spec") is not None and not isinstance(d["spec"], dict):
                import json  # noqa: PLC0415
                d["spec"] = json.loads(d["spec"])
            result.append(d)
        return result
    except Exception:  # noqa: BLE001
        return []


async def pg_get_metric_version(
    metric_id: str, org_id: str, version: int
) -> dict[str, Any] | None:
    """Return a single version row, org-scoped, or ``None``."""
    try:
        from app.db import fetchrow  # noqa: PLC0415

        row = await fetchrow(
            "SELECT id, org_id, metric_id, version, spec, created_by, created_at, note "
            "FROM metric_spec_versions "
            "WHERE metric_id = $1 AND org_id = $2::uuid AND version = $3",
            str(metric_id),
            str(org_id),
            version,
        )
        if row is None:
            return None
        d = dict(row)
        for key in ("id", "org_id", "created_by"):
            if d.get(key) is not None and not isinstance(d[key], str):
                d[key] = str(d[key])
        val = d.get("created_at")
        if isinstance(val, datetime) and val.tzinfo is None:
            d["created_at"] = val.replace(tzinfo=timezone.utc)
        if d.get("spec") is not None and not isinstance(d["spec"], dict):
            import json  # noqa: PLC0415
            d["spec"] = json.loads(d["spec"])
        return d
    except Exception:  # noqa: BLE001
        return None
