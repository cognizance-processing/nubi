"""Background scheduler for M11-A — production asyncio tick loop.

Public API
----------
start_scheduler(app)
    Create the asyncio background task that calls ``run_due_jobs`` on a
    fixed interval.  Safe to call multiple times (idempotent — a second call
    while a task is already running is a no-op).

stop_scheduler()
    Cancel the background task gracefully.  Safe to call even if the
    scheduler was never started.

scheduler_loop(interval_s, get_now)
    The coroutine that drives the scheduler.  Exposed as a public function
    so tests can call individual ticks directly without sleeping real time.
    ``get_now`` is an injected clock so tests can control the reference time.

Design
------
- Each tick calls ``run_due_jobs(get_job_store(), now, execute_job)``.
- Any exception inside a tick is caught and logged; a failing job (or even a
  crashing tick) must never kill the loop.
- The scheduler does NOT start during tests; ``JOBS_SCHEDULER_ENABLED``
  defaults to ``False``.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Callable

logger = logging.getLogger(__name__)

# Module-level imports of job helpers so they are patchable in tests.
from app.jobs.executor import execute_job  # noqa: E402
from app.jobs.schedule import run_due_jobs  # noqa: E402
from app.jobs.store import get_job_store  # noqa: E402

# ---------------------------------------------------------------------------
# Module-level task handle
# ---------------------------------------------------------------------------

_scheduler_task: asyncio.Task[None] | None = None


# ---------------------------------------------------------------------------
# Core loop coroutine
# ---------------------------------------------------------------------------


async def scheduler_loop(
    interval_s: int,
    get_now: Callable[[], datetime] | None = None,
) -> None:
    """Run ``run_due_jobs`` every *interval_s* seconds.

    Parameters
    ----------
    interval_s:
        Number of seconds to sleep between ticks.
    get_now:
        Optional callable that returns the current UTC datetime.  Defaults to
        ``datetime.now(timezone.utc)``.  Inject a fake clock in tests to call
        a single tick deterministically without sleeping.

    Notes
    -----
    Each tick is wrapped in a broad ``try/except`` so a misbehaving executor or
    broken store implementation cannot kill the background loop.
    """
    if get_now is None:
        def get_now() -> datetime:  # type: ignore[misc]
            return datetime.now(timezone.utc)

    logger.info("Scheduler loop started (interval=%ds).", interval_s)

    while True:
        await asyncio.sleep(interval_s)
        try:
            now = get_now()
            store = get_job_store()
            runs = run_due_jobs(store, now, execute_job)
            if runs:
                logger.info("Scheduler tick: %d job(s) executed.", len(runs))
        except Exception:
            logger.exception("Scheduler tick raised an unhandled exception; continuing.")


# ---------------------------------------------------------------------------
# Lifecycle helpers
# ---------------------------------------------------------------------------


def start_scheduler(app: Any = None) -> None:
    """Start the background scheduler asyncio task.

    Parameters
    ----------
    app:
        The FastAPI application instance (currently unused; accepted for
        signature compatibility with lifespan integration).

    Reads ``JOBS_SCHEDULER_INTERVAL_S`` from settings to determine the tick
    interval.  If the scheduler task is already running, this call is a no-op.
    """
    global _scheduler_task
    if _scheduler_task is not None and not _scheduler_task.done():
        logger.debug("start_scheduler() called but task is already running.")
        return

    from app.config import get_settings

    settings = get_settings()
    interval_s: int = settings.JOBS_SCHEDULER_INTERVAL_S

    _scheduler_task = asyncio.create_task(
        scheduler_loop(interval_s),
        name="nubi-jobs-scheduler",
    )
    logger.info("Scheduler task created (interval=%ds).", interval_s)


async def stop_scheduler() -> None:
    """Cancel the background scheduler task and wait for it to finish.

    Safe to call even if the scheduler was never started.
    """
    global _scheduler_task
    if _scheduler_task is None or _scheduler_task.done():
        _scheduler_task = None
        return

    _scheduler_task.cancel()
    try:
        await _scheduler_task
    except asyncio.CancelledError:
        pass
    finally:
        _scheduler_task = None
    logger.info("Scheduler task stopped.")
