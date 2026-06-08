"""Standalone flows worker entrypoint.

Run with::

    python backend/worker.py
    # or
    python -m backend.worker

Environment variables
---------------------
FLOWS_WORKER_CONCURRENCY  int   Number of concurrent task workers (default: 4).
FLOWS_WORKER_INTERVAL_S   float Scheduler tick + worker poll interval in seconds (default: 5).
FLOWS_WORKER_LEASE_S      int   Worker lease duration in seconds (default: 300).

The worker starts two coroutines:

1. **Scheduler loop** — calls ``flow_tick`` every ``FLOWS_WORKER_INTERVAL_S``
   seconds to materialise due scheduled flows and reap expired leases.
2. **Worker pool** — runs ``FLOWS_WORKER_CONCURRENCY`` concurrent async
   workers that claim and execute ready task_runs, populating
   ``TaskContext.secrets`` from the secret store.

This same entrypoint is used for local development (invoked by the CLI) and
for production worker processes (e.g. a separate Cloud Run worker service).
"""

from __future__ import annotations

import asyncio
import logging
import os
import socket
import sys
from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# Logging bootstrap (before any app imports so the root logger is configured).
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("nubi.worker")


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


async def _main() -> None:
    """Async entry point: scheduler loop + worker pool running concurrently."""
    concurrency: int = int(os.environ.get("FLOWS_WORKER_CONCURRENCY", "4") or 4)
    interval_s: float = float(os.environ.get("FLOWS_WORKER_INTERVAL_S", "5") or 5)
    lease_seconds: int = int(os.environ.get("FLOWS_WORKER_LEASE_S", "300") or 300)

    try:
        worker_id = f"{socket.gethostname()}:{os.getpid()}"
    except Exception:  # noqa: BLE001
        worker_id = f"worker:{os.getpid()}"

    logger.info(
        "Nubi flow worker starting — concurrency=%d interval=%.1fs lease=%ds worker_id=%s",
        concurrency,
        interval_s,
        lease_seconds,
        worker_id,
    )

    # Import after logging is configured so any import-time errors are visible.
    from app.flows.runtime import flow_tick, run_worker_pool  # noqa: PLC0415
    from app.flows.store import get_flow_store  # noqa: PLC0415

    async def _scheduler_loop() -> None:
        """Tick the flow scheduler at a fixed interval."""
        logger.info("Scheduler loop started (interval=%.1fs).", interval_s)
        while True:
            await asyncio.sleep(interval_s)
            try:
                now = datetime.now(timezone.utc)
                store = get_flow_store()
                summary = await flow_tick(store, now, claims=None)
                if summary.get("materialised") or summary.get("reaped"):
                    logger.info(
                        "Scheduler tick: materialised=%d reaped=%d",
                        summary.get("materialised", 0),
                        summary.get("reaped", 0),
                    )
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                logger.exception("Scheduler tick raised an unhandled exception; continuing.")

    scheduler_task = asyncio.create_task(_scheduler_loop(), name="nubi-flow-scheduler")
    worker_task = asyncio.create_task(
        run_worker_pool(
            concurrency=concurrency,
            poll_interval=max(0.5, interval_s / 2),
            claims={},
            worker_id=worker_id,
            lease_seconds=lease_seconds,
        ),
        name="nubi-flow-worker-pool",
    )

    logger.info("Worker pool and scheduler running. Press Ctrl+C to stop.")
    try:
        await asyncio.gather(scheduler_task, worker_task)
    except asyncio.CancelledError:
        pass
    except KeyboardInterrupt:
        pass
    finally:
        for task in (scheduler_task, worker_task):
            if not task.done():
                task.cancel()
        await asyncio.gather(scheduler_task, worker_task, return_exceptions=True)
        logger.info("Nubi flow worker stopped.")


def main() -> None:
    """Synchronous entry point for ``python backend/worker.py``."""
    try:
        asyncio.run(_main())
    except KeyboardInterrupt:
        logger.info("Interrupted.")
        sys.exit(0)


if __name__ == "__main__":
    main()
