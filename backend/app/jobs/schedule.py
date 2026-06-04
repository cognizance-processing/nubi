"""Scheduling logic for M11-A — deterministic, clock-free core.

Functions
---------
next_run(schedule, after) -> datetime
    Compute the next run time for a schedule string.  Accepts two formats:

    ``'interval:Ns'`` / ``'interval:Nm'`` / ``'interval:Nh'``
        Plain interval: add N seconds / minutes / hours to *after*.

    Any other string
        Treated as a cron expression; parsed via ``croniter`` (lazy import so
        tests that skip cron paths do not need it installed).

    Raises ``AppError("bad_schedule", 400)`` if the schedule string is not
    recognised or is syntactically invalid.

run_due_jobs(store, now, executor) -> list[dict]
    Run all enabled jobs whose ``next_run_at`` is ``<= now`` (or None).
    *now* is an explicit parameter — **no hidden ``datetime.now()`` inside
    core logic** so tests can inject arbitrary timestamps for determinism.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from app.errors import AppError

# ---------------------------------------------------------------------------
# Interval pattern:  interval:<N><unit>   e.g. interval:30s, interval:5m, interval:1h
# ---------------------------------------------------------------------------

_INTERVAL_RE = re.compile(r"^interval:(\d+)([smh])$", re.IGNORECASE)

_UNIT_SECONDS: dict[str, int] = {
    "s": 1,
    "m": 60,
    "h": 3600,
}


def next_run(schedule: str, after: datetime) -> datetime:
    """Return the next scheduled run time after *after*.

    Parameters
    ----------
    schedule:
        A schedule string in one of two forms:

        * ``'interval:Ns'``, ``'interval:Nm'``, ``'interval:Nh'`` — add N
          seconds / minutes / hours to *after*.
        * A cron expression (5 or 6 fields) — next occurrence after *after*
          computed via ``croniter``.

    after:
        The reference datetime (usually the last run time or ``now``).
        Should be timezone-aware; if naive it is treated as UTC.

    Returns
    -------
    datetime
        Timezone-aware UTC datetime of the next scheduled run.

    Raises
    ------
    AppError("bad_schedule", 400)
        If the schedule string is not a recognised interval or valid cron.
    """
    # ── Normalise to UTC ─────────────────────────────────────────────────────
    if after.tzinfo is None:
        after = after.replace(tzinfo=timezone.utc)

    # ── 1. Interval format ───────────────────────────────────────────────────
    m = _INTERVAL_RE.match(schedule.strip())
    if m:
        n = int(m.group(1))
        unit = m.group(2).lower()
        delta = timedelta(seconds=n * _UNIT_SECONDS[unit])
        return after + delta

    # ── 2. Cron format (lazy croniter import) ────────────────────────────────
    try:
        from croniter import croniter  # type: ignore[import]
    except ImportError as exc:
        raise AppError(
            "bad_schedule",
            "croniter is required for cron schedules.  "
            "Add 'croniter' to requirements.txt.",
            400,
        ) from exc

    try:
        # croniter works with naive datetimes; strip tzinfo, then restore UTC.
        after_naive = after.replace(tzinfo=None)
        cron = croniter(schedule.strip(), after_naive)
        next_naive: datetime = cron.get_next(datetime)
        return next_naive.replace(tzinfo=timezone.utc)
    except (ValueError, KeyError, TypeError) as exc:
        raise AppError(
            "bad_schedule",
            f"Invalid schedule expression {schedule!r}: {exc}",
            400,
        ) from exc


def run_due_jobs(
    store: Any,
    now: datetime,
    executor: Callable[[Any], dict[str, Any]],
) -> list[dict[str, Any]]:
    """Run all enabled jobs that are due as of *now*.

    A job is *due* if ``enabled == True`` AND (``next_run_at is None`` OR
    ``next_run_at <= now``).

    After each job runs:

    1. The run dict returned by *executor* is stored via ``store.add_run``.
    2. ``last_run_at`` is set to *now*.
    3. ``next_run_at`` is advanced to ``next_run(job["schedule"], now)``.
       If the schedule is invalid the job's ``next_run_at`` is left unchanged
       and the error is swallowed (the run result will already capture any
       executor-level error).

    Parameters
    ----------
    store:
        An ``InMemoryJobStore`` (or compatible) instance.
    now:
        The reference UTC datetime.  **Must be passed explicitly — no hidden
        ``datetime.now()`` call inside this function.**
    executor:
        A callable ``(job) -> run_dict``.  Called once per due job.  Must
        return a dict with at least ``{id, job_id, status, started_at,
        finished_at, row_count, message, created_at}``.

    Returns
    -------
    list[dict]
        The run dicts produced for each due job (in iteration order).
    """
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    runs: list[dict[str, Any]] = []

    # InMemoryJobStore stores all jobs in _jobs dict regardless of org_id.
    jobs_dict: dict[str, Any] = getattr(store, "_jobs", {})

    for job in list(jobs_dict.values()):
        if not job.get("enabled", True):
            continue

        next_run_at = job.get("next_run_at")
        if next_run_at is not None:
            # Normalise stored datetime to UTC if naive
            if next_run_at.tzinfo is None:
                next_run_at = next_run_at.replace(tzinfo=timezone.utc)
            if next_run_at > now:
                continue

        # ── Execute ──────────────────────────────────────────────────────────
        run = executor(job)
        store.add_run(job["id"], run)
        runs.append(run)

        # ── Advance timestamps ───────────────────────────────────────────────
        try:
            new_next = next_run(job["schedule"], now)
        except AppError:
            new_next = None  # leave next_run_at unchanged on bad schedule

        update_fields: dict[str, Any] = {"last_run_at": now}
        if new_next is not None:
            update_fields["next_run_at"] = new_next

        store.update_job(job["id"], update_fields)

    return runs
