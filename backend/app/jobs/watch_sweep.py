"""Watch-sweep job engine — nightly (or on-demand) org-scoped watch evaluation.

When the scheduler fires a ``watch_sweep`` job, this module:

1. Iterates ALL enabled watches for the job's owning org (from the in-process
   registry + a best-effort DB hydrate for registry misses).
2. Evaluates each watch via the SAME ``run_watch`` path used by
   ``POST /watches/{id}/evaluate`` (compile → plan → execute → scalar reduce →
   threshold check → explain + fire on breach).
3. Emits a ``WATCH_BREACH`` outbound webhook for each breached watch (via
   ``app.webhooks.events.emit_watch_breach`` inside ``fire_watch``).
4. Returns a summary dict with per-watch results so the scheduler can record it
   in the job_runs table.

Design notes
------------
- **Org-scoped** — the sweep only touches watches whose ``org_id`` matches the
  job's ``org_id``.  No cross-org leakage.
- **Best-effort per-watch** — a single watch failing (metric not found, DuckDB
  error, etc.) is caught and recorded as ``state='error'``; the sweep continues.
- **Deterministic ``now``** — the caller passes ``now`` (from the scheduler tick
  or the run-now path); no hidden ``datetime.now()`` inside this module.
- **No new tables** — the existing ``jobs``, ``job_runs``, and ``watches``
  tables are reused without alteration.

Public API
----------
``run_watch_sweep(org_id, now, *, claims=None) -> dict``
    Async: evaluate all enabled watches for *org_id* and return a summary dict.

``execute_watch_sweep_sync(job, now) -> tuple[int, str]``
    Sync wrapper (runs the async coroutine via a thread pool) so
    ``execute_job`` (which is intentionally sync) can drive the sweep.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("nubi.jobs.watch_sweep")


# ---------------------------------------------------------------------------
# Async core
# ---------------------------------------------------------------------------


async def run_watch_sweep(
    org_id: str,
    now: datetime,
    *,
    claims: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Evaluate all enabled watches for *org_id* and return a summary dict.

    Parameters
    ----------
    org_id:
        The org whose watches are swept.  Only watches whose ``org_id`` stamp
        matches this value are evaluated (tenant isolation).
    now:
        Reference UTC datetime.  Passed for deterministic logging; the metric
        execution path uses the DB server time internally.
    claims:
        RLS claims threaded into the metric execution path.  Defaults to
        ``{"policies": [], "org_id": org_id}`` (system/scheduler context, no
        row-level restrictions).

    Returns
    -------
    dict
        ``{"org_id": str, "swept_at": iso8601,
           "evaluated": int, "breached": int, "errors": int,
           "watches": [per-watch summary dicts]}``
    """
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    if claims is None:
        claims = {"policies": [], "org_id": str(org_id)}
    elif "org_id" not in claims:
        claims = {**claims, "org_id": str(org_id)}

    # ── Collect all enabled watches for this org ─────────────────────────────
    from app.routes.watches import _registry_all, _watch_from_record  # noqa: PLC0415
    from app.routes.watches import _resolve_metric_for_watch  # noqa: PLC0415
    from app.ai.watch import run_watch  # noqa: PLC0415

    all_records = _registry_all()
    org_records = [r for r in all_records if str(r.get("org_id")) == str(org_id)]

    results: list[dict[str, Any]] = []
    breached_count = 0
    error_count = 0

    for record in org_records:
        watch = _watch_from_record(record)
        if not watch.enabled:
            logger.debug("watch_sweep: skipping disabled watch %s", watch.id)
            continue

        try:
            metric = await _resolve_metric_for_watch(watch.metric_id, org_id)
            summary = await run_watch(watch, metric, claims)
            summary["id"] = watch.id
            summary["name"] = watch.name
            results.append(summary)
            if summary.get("breached"):
                breached_count += 1
        except Exception as exc:  # noqa: BLE001 — never abort the sweep.
            logger.warning(
                "watch_sweep: org=%s watch=%s failed: %s",
                org_id,
                watch.id,
                exc,
            )
            results.append({
                "id": watch.id,
                "name": watch.name,
                "state": "error",
                "breached": False,
                "error": str(exc),
            })
            error_count += 1

    return {
        "org_id": str(org_id),
        "swept_at": now.isoformat(),
        "evaluated": len(results),
        "breached": breached_count,
        "errors": error_count,
        "watches": results,
    }


# ---------------------------------------------------------------------------
# Sync wrapper for the executor (execute_job is intentionally sync)
# ---------------------------------------------------------------------------


def execute_watch_sweep_sync(
    job: dict[str, Any],
    now: datetime,
) -> tuple[int, str]:
    """Synchronous entry point called by ``execute_job`` for ``kind='watch_sweep'``.

    Runs ``run_watch_sweep`` in a dedicated thread so it is safe to call from
    the sync executor regardless of whether the caller is inside an asyncio
    event loop or not.

    Returns
    -------
    tuple[int, str]
        ``(breached_count, message)`` on success.

    Raises
    ------
    Any exception from ``run_watch_sweep`` is propagated; the executor's
    try/except converts it to an error run.
    """
    import concurrent.futures  # noqa: PLC0415

    org_id: str = str(job.get("org_id") or "")
    if not org_id:
        raise ValueError("watch_sweep job missing org_id")

    async def _run() -> dict[str, Any]:
        return await run_watch_sweep(org_id, now)

    # Always run in a dedicated thread so asyncio.run() starts a fresh event
    # loop — safe both when there IS an outer event loop (FastAPI request
    # handler calling run_now → execute_job → here) and when there is not
    # (the background scheduler tick, which IS async, never calls execute_job
    # directly — it calls run_due_jobs which calls execute_job in the loop).
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(asyncio.run, _run())
        summary = future.result()

    breached = summary.get("breached", 0)
    evaluated = summary.get("evaluated", 0)
    errors = summary.get("errors", 0)
    message = (
        f"Watch sweep complete: org={org_id!r}, "
        f"evaluated={evaluated}, breached={breached}, errors={errors}."
    )
    return breached, message
