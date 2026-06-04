"""Job store implementations — InMemoryJobStore (tests) + PgJobStore (prod).

``InMemoryJobStore`` is a dict-backed store for :class:`Job` and
:class:`JobRun` records.  It is the primary store used in tests.

``PgJobStore`` is the asyncpg-backed production store that maps each method to
a parameterised SQL query against the ``jobs`` and ``job_runs`` tables (from
migration 0007).  Rows are converted to plain dicts; jsonb and datetime values
match the shape produced by ``InMemoryJobStore``.

Provider
--------
``get_job_store()`` returns the configured singleton store.  By default it
returns a ``PgJobStore`` (suitable for production); tests inject an
``InMemoryJobStore`` via ``set_job_store(store)``.  This mirrors the pattern
used in ``repos/provider.py``.

Design
------
- All mutation methods use ``uuid.uuid4()`` and ``datetime.now(timezone.utc)``
  **at call time only** — never at module/class import time.
- ``set_job_store()`` lets tests (or routes in the test client) swap the
  singleton for an injected store without touching route signatures.
"""

from __future__ import annotations

import uuid
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any


# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------

Job = dict[str, Any]
JobRun = dict[str, Any]


# ---------------------------------------------------------------------------
# InMemoryJobStore
# ---------------------------------------------------------------------------


class InMemoryJobStore:
    """Dict-backed store for jobs and job_runs.

    All timestamps are ``datetime`` objects with UTC timezone.

    Job shape
    ---------
    ``{id, org_id, created_by, name, kind, target, schedule, enabled,
    next_run_at, last_run_at, created_at, updated_at}``

    JobRun shape
    ------------
    ``{id, job_id, status, started_at, finished_at, row_count, message,
    created_at}``
    """

    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._runs: dict[str, list[JobRun]] = {}  # job_id -> list of runs

    # ------------------------------------------------------------------
    # Job operations
    # ------------------------------------------------------------------

    def create_job(
        self,
        org_id: str,
        created_by: str,
        name: str,
        kind: str,
        target: str,
        schedule: str,
        enabled: bool = True,
        next_run_at: datetime | None = None,
    ) -> Job:
        """Create and store a new job; return the stored dict."""
        job_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc)
        job: Job = {
            "id": job_id,
            "org_id": str(org_id),
            "created_by": str(created_by),
            "name": name,
            "kind": kind,
            "target": target,
            "schedule": schedule,
            "enabled": enabled,
            "next_run_at": next_run_at,
            "last_run_at": None,
            "created_at": now,
            "updated_at": now,
        }
        self._jobs[job_id] = job
        self._runs[job_id] = []
        return deepcopy(job)

    def get_job(self, job_id: str) -> Job | None:
        """Return a copy of the job, or ``None`` if not found."""
        job = self._jobs.get(str(job_id))
        return deepcopy(job) if job is not None else None

    def list_jobs(self, org_id: str) -> list[Job]:
        """Return all jobs belonging to *org_id*, sorted by created_at."""
        rows = [
            deepcopy(j)
            for j in self._jobs.values()
            if str(j["org_id"]) == str(org_id)
        ]
        rows.sort(key=lambda r: r["created_at"])
        return rows

    def update_job(self, job_id: str, fields: dict[str, Any]) -> Job | None:
        """Update mutable fields on a job in-place; return the updated copy.

        Returns ``None`` if the job does not exist.
        """
        job = self._jobs.get(str(job_id))
        if job is None:
            return None
        for key, val in fields.items():
            job[key] = val
        job["updated_at"] = datetime.now(timezone.utc)
        return deepcopy(job)

    def delete_job(self, job_id: str) -> bool:
        """Delete a job and all its runs; return ``True`` if deleted."""
        job_id = str(job_id)
        if job_id not in self._jobs:
            return False
        del self._jobs[job_id]
        self._runs.pop(job_id, None)
        return True

    # ------------------------------------------------------------------
    # Run operations
    # ------------------------------------------------------------------

    def add_run(self, job_id: str, run: JobRun) -> JobRun:
        """Append *run* to the job's run list; return a copy of the run.

        *run* should already have a unique ``id`` set by the caller.
        If the job does not exist, a new run list is created (defensive).
        """
        job_id = str(job_id)
        if job_id not in self._runs:
            self._runs[job_id] = []
        stored = deepcopy(run)
        self._runs[job_id].append(stored)
        return deepcopy(stored)

    def list_runs(self, job_id: str) -> list[JobRun]:
        """Return all runs for *job_id*, oldest first."""
        return [deepcopy(r) for r in self._runs.get(str(job_id), [])]


# ---------------------------------------------------------------------------
# PgJobStore — asyncpg-backed production implementation
# ---------------------------------------------------------------------------


def _row_to_job(row: Any) -> Job:
    """Convert an asyncpg Record (or dict) to a Job dict.

    Ensures:
    - All UUIDs are strings.
    - ``datetime`` values retain their timezone info (asyncpg returns
      timezone-aware datetimes for ``timestamptz`` columns).
    - ``None`` values for nullable columns are preserved.
    """
    d = dict(row)
    # Coerce UUID objects to strings
    for key in ("id", "org_id", "created_by"):
        if key in d and d[key] is not None and not isinstance(d[key], str):
            d[key] = str(d[key])
    # Ensure datetime columns carry UTC tzinfo (asyncpg usually does this)
    for key in ("next_run_at", "last_run_at", "created_at", "updated_at"):
        val = d.get(key)
        if isinstance(val, datetime) and val.tzinfo is None:
            d[key] = val.replace(tzinfo=timezone.utc)
    return d


def _row_to_run(row: Any) -> JobRun:
    """Convert an asyncpg Record (or dict) to a JobRun dict."""
    d = dict(row)
    for key in ("id", "job_id"):
        if key in d and d[key] is not None and not isinstance(d[key], str):
            d[key] = str(d[key])
    for key in ("started_at", "finished_at", "created_at"):
        val = d.get(key)
        if isinstance(val, datetime) and val.tzinfo is None:
            d[key] = val.replace(tzinfo=timezone.utc)
    return d


class PgJobStore:
    """asyncpg-backed job store for production use.

    Uses the ``fetch`` / ``fetchrow`` / ``execute`` helpers from ``app.db``
    (which acquire a connection from the pool automatically).

    All SQL is parameterised with ``$N`` placeholders.  Column names match
    the ``jobs`` and ``job_runs`` tables from migration 0007.

    Rows returned by asyncpg are converted to plain dicts that match the
    shape produced by ``InMemoryJobStore``.
    """

    # ------------------------------------------------------------------
    # Job operations
    # ------------------------------------------------------------------

    async def create_job(
        self,
        org_id: str,
        created_by: str,
        name: str,
        kind: str,
        target: str,
        schedule: str,
        enabled: bool = True,
        next_run_at: datetime | None = None,
    ) -> Job:
        """Insert a new job row and return the stored dict."""
        from app.db import fetchrow as db_fetchrow

        row = await db_fetchrow(
            """
            INSERT INTO jobs (org_id, created_by, name, kind, target, schedule,
                              enabled, next_run_at)
            VALUES ($1::uuid, $2::uuid, $3, $4, $5, $6, $7, $8)
            RETURNING *
            """,
            org_id,
            created_by,
            name,
            kind,
            target,
            schedule,
            enabled,
            next_run_at,
        )
        if row is None:  # pragma: no cover
            raise RuntimeError("INSERT INTO jobs returned no row.")
        return _row_to_job(row)

    async def get_job(self, job_id: str) -> Job | None:
        """Return the job dict, or ``None`` if not found."""
        from app.db import fetchrow as db_fetchrow

        row = await db_fetchrow(
            "SELECT * FROM jobs WHERE id = $1::uuid",
            job_id,
        )
        return _row_to_job(row) if row is not None else None

    async def list_jobs(self, org_id: str) -> list[Job]:
        """Return all jobs belonging to *org_id*, sorted by created_at."""
        from app.db import fetch as db_fetch

        rows = await db_fetch(
            "SELECT * FROM jobs WHERE org_id = $1::uuid ORDER BY created_at ASC",
            org_id,
        )
        return [_row_to_job(r) for r in rows]

    async def update_job(self, job_id: str, fields: dict[str, Any]) -> Job | None:
        """Update allowed fields on a job; return the updated dict or ``None``."""
        from app.db import fetchrow as db_fetchrow

        allowed = {
            "name", "kind", "target", "schedule", "enabled",
            "next_run_at", "last_run_at",
        }
        updates: list[str] = []
        values: list[Any] = []
        param_idx = 1

        for field in (
            "name", "kind", "target", "schedule", "enabled",
            "next_run_at", "last_run_at",
        ):
            if field not in fields or field not in allowed:
                continue
            updates.append(f"{field} = ${param_idx}")
            values.append(fields[field])
            param_idx += 1

        if not updates:
            return await self.get_job(job_id)

        updates.append("updated_at = now()")
        set_clause = ", ".join(updates)
        values.append(job_id)
        id_param = param_idx

        row = await db_fetchrow(
            f"UPDATE jobs SET {set_clause} WHERE id = ${id_param}::uuid RETURNING *",
            *values,
        )
        return _row_to_job(row) if row is not None else None

    async def delete_job(self, job_id: str) -> bool:
        """Delete a job and all its runs (cascade); return ``True`` if deleted."""
        from app.db import execute as db_execute

        status = await db_execute(
            "DELETE FROM jobs WHERE id = $1::uuid",
            job_id,
        )
        try:
            count = int(status.split()[-1])
        except (ValueError, IndexError):
            count = 0
        return count > 0

    # ------------------------------------------------------------------
    # Run operations
    # ------------------------------------------------------------------

    async def add_run(self, job_id: str, run: JobRun) -> JobRun:
        """Insert a job_run row; return the stored dict.

        The caller must supply a run dict with at least:
        ``{id, job_id, status, started_at, finished_at, row_count, message,
        created_at}``.
        """
        from app.db import fetchrow as db_fetchrow

        row = await db_fetchrow(
            """
            INSERT INTO job_runs (id, job_id, status, started_at, finished_at,
                                  row_count, message)
            VALUES ($1::uuid, $2::uuid, $3, $4, $5, $6, $7)
            RETURNING *
            """,
            run["id"],
            run["job_id"],
            run["status"],
            run.get("started_at"),
            run.get("finished_at"),
            run.get("row_count", 0),
            run.get("message", ""),
        )
        if row is None:  # pragma: no cover
            raise RuntimeError("INSERT INTO job_runs returned no row.")
        return _row_to_run(row)

    async def list_runs(self, job_id: str) -> list[JobRun]:
        """Return all runs for *job_id*, oldest first."""
        from app.db import fetch as db_fetch

        rows = await db_fetch(
            "SELECT * FROM job_runs WHERE job_id = $1::uuid ORDER BY created_at ASC",
            job_id,
        )
        return [_row_to_run(r) for r in rows]


# ---------------------------------------------------------------------------
# Module-level singleton / provider
# ---------------------------------------------------------------------------

#: Active singleton — None means "lazily create PgJobStore on first call".
_job_store: InMemoryJobStore | PgJobStore | None = None


def get_job_store() -> InMemoryJobStore | PgJobStore:
    """Return (or lazily create) the module-level job store.

    In production (no override via ``set_job_store``), returns a ``PgJobStore``
    instance.  Tests inject an ``InMemoryJobStore`` via ``set_job_store`` before
    making requests.

    Route handlers depend on this function; they keep working without changes
    since both stores expose the same interface.
    """
    global _job_store
    if _job_store is None:
        _job_store = PgJobStore()
    return _job_store


def set_job_store(store: InMemoryJobStore | PgJobStore | None) -> None:
    """Override the module-level store singleton.

    Pass an ``InMemoryJobStore`` instance to inject a test double.
    Pass ``None`` to reset so the next ``get_job_store()`` call creates a
    fresh ``PgJobStore`` (the production default).
    """
    global _job_store
    _job_store = store
