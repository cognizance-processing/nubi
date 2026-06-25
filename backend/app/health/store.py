"""Freshness registry store — InMemoryFreshnessStore (tests) + PgFreshnessStore (prod).

The registry is a pre-computed, indexed table (``dataset_freshness``) that is
populated by:
  1. Flow-run completion hooks registered via ``app.flows.events``.
  2. An optional scheduled freshness tick that rescans run history.

Every read is a single equality scan on the (org_id, dataset_key) primary key —
O(1), suitable for low-latency UX gates.

Read SLO target: < 5 ms p99 (single indexed-row lookup; see migration 0020).

Provider
--------
``get_freshness_store()`` returns the configured singleton.  Tests inject an
``InMemoryFreshnessStore`` via ``set_freshness_store(store)``.

Status semantics
----------------
``'fresh'``   — last_success_at is within expected_interval_s of now.
``'stale'``   — last_success_at exists but the interval has elapsed.
``'unknown'`` — no run has ever completed (last_success_at is NULL) or no
                expected_interval_s is configured.
"""

from __future__ import annotations

import uuid
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

# ---------------------------------------------------------------------------
# Status helper
# ---------------------------------------------------------------------------

def _compute_status(
    last_success_at: datetime | None,
    expected_interval_s: int | None,
) -> str:
    """Derive freshness status from timestamps.

    Returns ``'fresh'``, ``'stale'``, or ``'unknown'``.
    """
    if last_success_at is None or expected_interval_s is None:
        return "unknown"
    elapsed = (datetime.now(timezone.utc) - last_success_at).total_seconds()
    return "fresh" if elapsed <= expected_interval_s else "stale"


# ---------------------------------------------------------------------------
# Abstract interface (structural, no ABC overhead)
# ---------------------------------------------------------------------------

class FreshnessRecord:
    """Plain value object for a freshness row."""

    __slots__ = (
        "org_id",
        "dataset_key",
        "last_success_at",
        "expected_interval_s",
        "status",
        "updated_at",
    )

    def __init__(
        self,
        org_id: str,
        dataset_key: str,
        last_success_at: datetime | None,
        expected_interval_s: int | None,
        status: str,
        updated_at: datetime,
    ) -> None:
        self.org_id = org_id
        self.dataset_key = dataset_key
        self.last_success_at = last_success_at
        self.expected_interval_s = expected_interval_s
        self.status = status
        self.updated_at = updated_at

    def to_dict(self) -> dict[str, Any]:
        return {
            "org_id": self.org_id,
            "dataset_key": self.dataset_key,
            "last_success_at": (
                self.last_success_at.isoformat() if self.last_success_at else None
            ),
            "expected_interval_s": self.expected_interval_s,
            "status": self.status,
            "updated_at": self.updated_at.isoformat(),
        }


# ---------------------------------------------------------------------------
# In-memory store (used by tests and the flow-event listener in the same process)
# ---------------------------------------------------------------------------

class InMemoryFreshnessStore:
    """Dict-backed freshness registry for tests.

    Keyed by ``(org_id, dataset_key)``.
    """

    def __init__(self) -> None:
        self._records: dict[tuple[str, str], FreshnessRecord] = {}

    def upsert(
        self,
        org_id: str,
        dataset_key: str,
        last_success_at: datetime | None,
        expected_interval_s: int | None = None,
    ) -> FreshnessRecord:
        """Insert or update a freshness row, recomputing status."""
        status = _compute_status(last_success_at, expected_interval_s)
        now = datetime.now(timezone.utc)
        rec = FreshnessRecord(
            org_id=org_id,
            dataset_key=dataset_key,
            last_success_at=last_success_at,
            expected_interval_s=expected_interval_s,
            status=status,
            updated_at=now,
        )
        self._records[(org_id, dataset_key)] = rec
        return deepcopy(rec)

    def get_sync(self, org_id: str, dataset_key: str) -> FreshnessRecord | None:
        """Synchronous get — used by the flow event listener (no event loop)."""
        rec = self._records.get((org_id, dataset_key))
        return deepcopy(rec) if rec else None

    # Async aliases so routes can use the same calling convention as PgFreshnessStore.
    async def get(self, org_id: str, dataset_key: str) -> FreshnessRecord | None:
        return self.get_sync(org_id, dataset_key)

    async def list_for_org(self, org_id: str) -> list[FreshnessRecord]:
        return [
            deepcopy(r)
            for (oid, _), r in self._records.items()
            if oid == org_id
        ]

    async def delete(self, org_id: str, dataset_key: str) -> None:
        self._records.pop((org_id, dataset_key), None)

    def reset(self) -> None:
        self._records.clear()

    # ------------------------------------------------------------------
    # Health score config
    # ------------------------------------------------------------------

    def __init_subclass__(cls, **kwargs: Any) -> None:  # noqa: ANN401
        super().__init_subclass__(**kwargs)

    def _ensure_config_store(self) -> None:
        if not hasattr(self, "_configs"):
            self._configs: dict[tuple[str, str | None], dict[str, float]] = {}

    def get_weights_sync(
        self, org_id: str, dataset_key: str | None = None
    ) -> dict[str, float]:
        """Synchronous weight lookup (listener / tests)."""
        self._ensure_config_store()
        for key in [(org_id, dataset_key), (org_id, None)]:
            if key in self._configs:
                return dict(self._configs[key])
        return {"freshness": 0.50, "completeness": 0.30, "availability": 0.20}

    async def get_weights(
        self, org_id: str, dataset_key: str | None = None
    ) -> dict[str, float]:
        """Async alias — matches PgFreshnessStore signature."""
        return self.get_weights_sync(org_id, dataset_key)

    def set_weights(
        self,
        org_id: str,
        dataset_key: str | None,
        weights: dict[str, float],
    ) -> None:
        self._ensure_config_store()
        self._configs[(org_id, dataset_key)] = dict(weights)


# ---------------------------------------------------------------------------
# Postgres store (prod)
# ---------------------------------------------------------------------------

class PgFreshnessStore:
    """asyncpg-backed freshness registry using the dataset_freshness table."""

    async def upsert(
        self,
        org_id: str,
        dataset_key: str,
        last_success_at: datetime | None,
        expected_interval_s: int | None = None,
    ) -> FreshnessRecord:
        from app.db import execute, fetchrow  # lazy import — no circular risk

        status = _compute_status(last_success_at, expected_interval_s)
        now = datetime.now(timezone.utc)

        await execute(
            """
            INSERT INTO dataset_freshness
                (org_id, dataset_key, last_success_at, expected_interval_s, status, updated_at)
            VALUES ($1::uuid, $2, $3, $4, $5, $6)
            ON CONFLICT (org_id, dataset_key) DO UPDATE SET
                last_success_at     = EXCLUDED.last_success_at,
                expected_interval_s = EXCLUDED.expected_interval_s,
                status              = EXCLUDED.status,
                updated_at          = EXCLUDED.updated_at
            """,
            org_id,
            dataset_key,
            last_success_at,
            expected_interval_s,
            status,
            now,
        )

        return FreshnessRecord(
            org_id=org_id,
            dataset_key=dataset_key,
            last_success_at=last_success_at,
            expected_interval_s=expected_interval_s,
            status=status,
            updated_at=now,
        )

    async def get(self, org_id: str, dataset_key: str) -> FreshnessRecord | None:
        from app.db import fetchrow

        row = await fetchrow(
            """
            SELECT org_id, dataset_key, last_success_at,
                   expected_interval_s, status, updated_at
            FROM   dataset_freshness
            WHERE  org_id = $1::uuid AND dataset_key = $2
            """,
            org_id,
            dataset_key,
        )
        return _row_to_record(row) if row else None

    async def list_for_org(self, org_id: str) -> list[FreshnessRecord]:
        from app.db import fetch

        rows = await fetch(
            """
            SELECT org_id, dataset_key, last_success_at,
                   expected_interval_s, status, updated_at
            FROM   dataset_freshness
            WHERE  org_id = $1::uuid
            ORDER  BY dataset_key
            """,
            org_id,
        )
        return [_row_to_record(r) for r in rows]

    async def get_weights(
        self, org_id: str, dataset_key: str | None = None
    ) -> dict[str, float]:
        from app.db import fetchrow

        # Try dataset-specific, then org default, then fall back to hardcoded defaults.
        for dk in ([dataset_key, None] if dataset_key is not None else [None]):
            row = await fetchrow(
                """
                SELECT weight_freshness, weight_completeness, weight_availability
                FROM   health_score_config
                WHERE  org_id = $1::uuid
                  AND  (dataset_key = $2 OR ($2 IS NULL AND dataset_key IS NULL))
                LIMIT  1
                """,
                org_id,
                dk,
            )
            if row:
                return {
                    "freshness": float(row["weight_freshness"]),
                    "completeness": float(row["weight_completeness"]),
                    "availability": float(row["weight_availability"]),
                }
        return {"freshness": 0.50, "completeness": 0.30, "availability": 0.20}


def _row_to_record(row: Any) -> FreshnessRecord:
    return FreshnessRecord(
        org_id=str(row["org_id"]),
        dataset_key=str(row["dataset_key"]),
        last_success_at=row["last_success_at"],
        expected_interval_s=row["expected_interval_s"],
        status=str(row["status"]),
        updated_at=row["updated_at"],
    )


# ---------------------------------------------------------------------------
# Singleton provider
# ---------------------------------------------------------------------------

_store: InMemoryFreshnessStore | PgFreshnessStore | None = None


def get_freshness_store() -> InMemoryFreshnessStore | PgFreshnessStore:
    global _store
    if _store is None:
        _store = PgFreshnessStore()
    return _store


def set_freshness_store(store: InMemoryFreshnessStore | PgFreshnessStore | None) -> None:
    global _store
    _store = store


def reset_for_tests() -> None:
    """Reset the singleton. Intended for test teardown only."""
    global _store
    _store = None
