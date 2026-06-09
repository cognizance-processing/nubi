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
            row_count, message = _run_python_job(target, job)
        elif kind == "report":
            target_dict: dict[str, Any] = job.get("target", {})
            if isinstance(target_dict, str):
                import json as _json
                target_dict = _json.loads(target_dict)
            row_count, message = _run_report_job(target_dict)
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


def _run_report_job(target: dict[str, Any]) -> tuple[int, str]:
    """Execute a report job: render a board and email it to recipients.

    Parameters
    ----------
    target:
        The job's ``target`` dict.  Expected keys (all validated at route
        creation time by ``CreateJobIn``):

        ``board_id``        (str) — the board to render.
        ``params``          (dict) — named param overrides; may be ``{}``.
        ``format``          (str) — ``'csv'`` or ``'pdf'``.
        ``recipients``      (list[str]) — email addresses.
        ``subject``         (str) — email subject.
        ``body``            (str) — email body.
        ``apply_user_permissions`` (bool) — when True, inject per-recipient
                            locked params from ``target["locked_params"]``.

    Returns
    -------
    tuple[int, str]
        ``(emails_sent, message)`` on success.

    Raises
    ------
    AppError / Exception
        Propagated to the caller which records an error run.
    """
    from app.jobs.report import (
        NullSender,
        inject_locked_params,
        render_report,
        resolve_board_sync,
        send_report,
    )
    from app.errors import AppError

    board_id: str = target.get("board_id", "")
    if not board_id:
        raise AppError("bad_report_target", "report target must include 'board_id'.", 400)

    params: dict[str, Any] = target.get("params") or {}
    fmt: str = target.get("format", "csv")
    recipients: list[str] = target.get("recipients") or []
    apply_user_permissions: bool = bool(target.get("apply_user_permissions", False))
    locked_params_map: dict[str, dict[str, Any]] = target.get("locked_params") or {}

    # Resolve the org_id from the target; callers may embed it for the executor.
    # The route layer embeds org_id when constructing the job target dict.
    org_id: str = target.get("org_id", "")

    # Resolve the board via the repo.
    board = resolve_board_sync(board_id, org_id)
    if board is None:
        raise AppError(
            "board_not_found",
            f"Board {board_id!r} not found (org={org_id!r}).",
            404,
        )

    # Build a NullSender (no SMTP config in M17-A; production wires SMTP/SES here).
    sender = NullSender()

    total_sent = 0

    if apply_user_permissions and recipients:
        # Per-recipient render: inject locked params for each recipient separately.
        # TODO: when M13 named-param RLS resolver is merged, replace inject_locked_params
        # with a call to the claims/policies resolver so the full precedence chain is
        # honoured: token/RLS claims (locked) > body.params > query default.
        for recipient in recipients:
            locked = locked_params_map.get(recipient, {})
            effective_params = inject_locked_params(params, locked)
            rendered = render_report(board, effective_params, fmt)
            sent = send_report(sender, {**target, "recipients": [recipient]}, rendered)
            total_sent += sent
    else:
        # Single render for all recipients.
        rendered = render_report(board, params, fmt)
        total_sent = send_report(sender, target, rendered)

    message = (
        f"Report job completed: board={board_id!r}, format={fmt!r}, "
        f"recipients={len(recipients)}, emails_sent={total_sent}."
    )
    return total_sent, message


def _run_python_job(target: str, job: dict[str, Any]) -> tuple[int, str]:
    """Execute a Python job via LocalSubprocessRunner.

    Parameters
    ----------
    target:
        Python source code to execute.  The code must assign a
        ``pyarrow.Table`` (or compatible) to the name ``result``.
    job:
        The full job dict — used for billing attribution: usage is recorded
        against the job's owning org (``org_id``) and creating user
        (``created_by``).  Billing aggregation filters on ``org_id``, so a
        NULL-org event would never reach quota checks or invoices, and the
        ``user_id`` column must hold a real user, not the job id.

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

    # Record metering (best-effort; sync-safe wrapper since this fn is sync).
    # Attribute to the job's owning org + creating user so scheduled-job
    # compute counts toward the org's compute-unit quota and overage billing.
    record_kernel_usage_safe(
        user_id=str(job.get("created_by") or ""),
        tier="local_kernel",
        elapsed_ms=elapsed_ms,
        output_bytes=output_bytes,
        org_id=str(job["org_id"]) if job.get("org_id") else None,
    )

    return row_count, f"Python job completed. stdout: {result.stdout[:200]}"
