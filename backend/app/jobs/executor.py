"""Job executor for M11-A.

``execute_job(job, now=None) -> dict``
    Execute a single job synchronously and return a job_run dict.

    kind == 'query'
        Resolve the target from the registered query registry, plan it via
        the planner against a fresh DuckDB connector, and record the row count.

    kind == 'python'
        Run the target source code via ``LocalSubprocessRunner`` with no
        inputs (the job's Python snippet is expected to produce a ``result``
        pyarrow.Table binding).  Metering is recorded after a successful run.

    Any exception is caught and returned as a ``status='error'`` run with
    ``row_count=0`` and the exception message in ``message``.

DETERMINISM NOTE
----------------
``now`` is an optional parameter.  When provided it is used as both the
``started_at`` timestamp AND the reference time passed to ``next_run()`` in
``run_due_jobs()``.  When ``None`` the executor generates its own timestamp
via ``datetime.now(timezone.utc)`` — this is acceptable at the route layer
(``POST /jobs/{id}/run``) where the caller does not care about determinism.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from app.errors import AppError


def execute_job(job: dict[str, Any], now: datetime | None = None) -> dict[str, Any]:
    """Execute *job* synchronously and return a job_run dict.

    Parameters
    ----------
    job:
        A job dict as returned by ``InMemoryJobStore.get_job`` / ``list_jobs``.
        Expected keys: ``id``, ``kind`` (``'query'`` or ``'python'``),
        ``target``.
    now:
        Optional reference timestamp.  Used as ``started_at`` for the run.
        If ``None``, the current UTC time is used.

    Returns
    -------
    dict
        A job_run dict with keys:
        ``{id, job_id, status, started_at, finished_at, row_count, message,
        created_at}``.
        ``status`` is ``'success'`` or ``'error'``.
        ``row_count`` is ``0`` on error.
    """
    if now is None:
        now = datetime.now(timezone.utc)

    run_id = str(uuid.uuid4())
    job_id = str(job["id"])
    kind: str = job.get("kind", "")
    target: str = job.get("target", "")

    started_at = now

    try:
        if kind == "query":
            row_count, message = _run_query_job(target)
        elif kind == "python":
            row_count, message = _run_python_job(target, job_id)
        else:
            raise AppError("bad_job_kind", f"Unknown job kind: {kind!r}", 400)

        finished_at = datetime.now(timezone.utc)
        return _make_run(
            run_id=run_id,
            job_id=job_id,
            status="success",
            started_at=started_at,
            finished_at=finished_at,
            row_count=row_count,
            message=message,
        )

    except Exception as exc:
        finished_at = datetime.now(timezone.utc)
        return _make_run(
            run_id=run_id,
            job_id=job_id,
            status="error",
            started_at=started_at,
            finished_at=finished_at,
            row_count=0,
            message=str(exc),
        )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _make_run(
    run_id: str,
    job_id: str,
    status: str,
    started_at: datetime,
    finished_at: datetime,
    row_count: int,
    message: str,
) -> dict[str, Any]:
    """Build and return a job_run dict."""
    return {
        "id": run_id,
        "job_id": job_id,
        "status": status,
        "started_at": started_at,
        "finished_at": finished_at,
        "row_count": row_count,
        "message": message,
        "created_at": started_at,
    }


def _run_query_job(target: str) -> tuple[int, str]:
    """Execute a registered query job.

    Parameters
    ----------
    target:
        The query_id of a registered query.

    Returns
    -------
    tuple[int, str]
        ``(row_count, message)`` on success.

    Raises
    ------
    AppError
        If the query_id is not registered or DuckDB execution fails.
    """
    from app.queries.registry import get_query_registry
    from app.connectors import planner
    from app.connectors.duckdb_conn import DuckDBConnector

    registry = get_query_registry()
    rq = registry.get(target)
    if rq is None:
        raise AppError(
            "query_not_found",
            f"No registered query with id {target!r}.",
            404,
        )

    # Plan the query (no claims, no projection — plain execution)
    physical_plan = planner.plan(rq.sql)

    # Execute via a fresh in-memory DuckDB connector
    connector = DuckDBConnector()
    table = connector.execute(physical_plan)

    row_count: int = table.num_rows
    return row_count, f"Query '{target}' completed successfully."


def _run_python_job(target: str, job_id: str) -> tuple[int, str]:
    """Execute a Python job via LocalSubprocessRunner.

    Parameters
    ----------
    target:
        Python source code to execute.  The code must assign a
        ``pyarrow.Table`` (or compatible) to the name ``result``.
    job_id:
        Used for metering attribution.

    Returns
    -------
    tuple[int, str]
        ``(row_count, message)`` on success.

    Raises
    ------
    AppError
        On kernel execution failure, timeout, or output-size violation.
    """
    import time
    from app.compute.runner import LocalSubprocessRunner
    from app.compute.metering import record_kernel_usage_safe

    runner = LocalSubprocessRunner()
    start_wall = time.monotonic()
    result = runner.run(code=target, inputs={}, timeout_s=60)
    elapsed_ms = int((time.monotonic() - start_wall) * 1000)

    row_count = 0
    output_bytes = 0
    if result.table is not None:
        row_count = result.table.num_rows
        # Estimate output bytes (Arrow IPC serialisation cost)
        try:
            import pyarrow.ipc as _pa_ipc
            import pyarrow as _pa
            sink = _pa.BufferOutputStream()
            with _pa_ipc.new_stream(sink, result.table.schema) as w:
                for batch in result.table.to_batches():
                    w.write_batch(batch)
            output_bytes = len(sink.getvalue().to_pybytes())
        except Exception:
            output_bytes = 0

    # Record metering (best-effort; sync-safe wrapper since this fn is sync)
    record_kernel_usage_safe(
        user_id=job_id,  # attribute to the job_id for billing purposes
        tier="local_kernel",
        elapsed_ms=elapsed_ms,
        output_bytes=output_bytes,
    )

    return row_count, f"Python job completed. stdout: {result.stdout[:200]}"
