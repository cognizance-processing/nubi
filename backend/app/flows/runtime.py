"""Flows engine runtime — materializer, advance_readiness, worker, tick.

All core scheduling / state-machine functions accept an explicit ``now``
parameter — **never** call ``datetime.now()`` inside core logic (tests inject
the clock).

Public API
----------
materialize_flow_run(store, flow, params, trigger, now) -> flow_run
    Validate the flow spec, create a flow_run (state='running'), and bulk-insert
    one task_run per task.  Root tasks (no needs) are set to state='ready' with
    scheduled_at=now; other tasks default to state='pending'.  Returns the
    created flow_run dict.

advance_readiness(store, flow_run_id, now) -> None
    For each 'pending' task_run: if all depends_on task_runs are 'success',
    transition to 'ready' (scheduled_at=now); if any dep is in a failed/terminal
    non-success state, transition to 'upstream_failed'.  After processing pending
    tasks, check whether all task_runs are in a terminal state and if so finalise
    the flow_run (state='success'|'failed', finished_at=now).

run_one_ready_task(store, now, claims) -> task_run | None
    Claim one ready task_run via ``claim_ready_task_run(now)``.  If none is
    available, return None.  Build a ``TaskContext`` from upstream results,
    execute the task, update the task_run to 'success' or handle retry/failed
    logic, then call ``advance_readiness``.  Return the updated task_run.

drain_flow_run(store, flow_run_id, now, claims, max_steps=200) -> flow_run
    Loop ``run_one_ready_task`` until no ready tasks remain within this
    flow_run or ``max_steps`` is reached.  Returns the final flow_run dict.
    Used by POST /flows/{id}/run for synchronous execution.

flow_tick(store, now, claims=None) -> dict
    (a) Materialise due scheduled flows (next_run_at <= now), advancing
    next_run_at via ``app.jobs.schedule.next_run``.
    (b) Drain a bounded number of ready task_runs across all running flow_runs.
    Returns a summary dict.

start_flow_worker(app) / stop_flow_worker()
    asyncio background task lifecycle, mirroring ``app/jobs/runtime.py``.
    Gated by ``FLOWS_WORKER_ENABLED`` and ``FLOWS_WORKER_INTERVAL_S`` settings
    (accessed via ``getattr`` with defaults so this module imports even before
    config is updated).

Task states
-----------
``pending``         — waiting for upstream deps.
``ready``           — deps satisfied; eligible for claiming.
``running``         — currently executing.
``retrying``        — failed but retries remain; re-queued with backoff.
``success``         — completed successfully.
``failed``          — exhausted retries (or no retries configured).
``timed_out``       — exceeded ``timeout_s``; treated as a failed terminal state.
``cancelled``       — manually cancelled (reserved; not set by the engine yet).
``upstream_failed`` — an upstream dep failed/timed_out; this task will not run.
"""

from __future__ import annotations

import asyncio
import logging
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger(__name__)

# Terminal states for task_runs (engine will not re-queue).
# ``skipped`` is kept for backward compat with older flow_runs that used it.
_TERMINAL_STATES = frozenset({"success", "failed", "timed_out", "upstream_failed", "skipped", "cancelled"})
# States that cause dependents to be marked upstream_failed.
# ``skipped`` is kept here so old runs with skipped tasks propagate correctly.
_BLOCKING_STATES = frozenset({"failed", "timed_out", "upstream_failed", "skipped", "cancelled"})


# ---------------------------------------------------------------------------
# materialize_flow_run
# ---------------------------------------------------------------------------


async def materialize_flow_run(
    store: Any,
    flow: dict[str, Any],
    params: dict[str, Any],
    trigger: str,
    now: datetime,
) -> dict[str, Any]:
    """Create a flow_run and its task_runs; return the flow_run dict.

    Parameters
    ----------
    store:
        An ``InMemoryFlowStore`` or ``PgFlowStore`` instance.
    flow:
        A flow dict as returned by ``store.get_flow`` / ``store.create_flow``.
    params:
        Flow-level parameter values supplied by the caller (merged with spec
        defaults by the executor at task-run time).
    trigger:
        One of ``'manual'``, ``'schedule'``, ``'event'``, ``'agent'``.
    now:
        Injected clock datetime (UTC, tz-aware).

    Returns
    -------
    dict
        The created flow_run dict (state='running').

    Raises
    ------
    ValueError
        If the flow spec is invalid (hard errors from ``validate_flow_spec``).
    """
    from app.flows.events import FlowEvent, emit_flow_event  # noqa: PLC0415
    from app.flows.spec import flow_spec_is_valid, validate_flow_spec  # noqa: PLC0415

    spec_data = flow.get("spec") or {}
    flow_spec, issues = validate_flow_spec(spec_data)

    if not flow_spec_is_valid(issues):
        hard = [i for i in issues if not i.startswith("[warn]")]
        raise ValueError(f"Flow spec is invalid: {'; '.join(hard)}")

    # Create the flow_run (state starts as 'pending' from the store constructor,
    # then we immediately transition it to 'running').
    flow_run = await store.create_flow_run(
        flow_id=flow["id"],
        org_id=flow["org_id"],
        params=params,
        trigger=trigger,
        scheduled_at=None,
    )
    flow_run = await store.update_flow_run(
        flow_run["id"],
        {"state": "running", "started_at": now},
    )

    # Emit flow_started event.
    emit_flow_event(FlowEvent(
        type="flow_started",
        flow_run_id=flow_run["id"],
        state="running",
        timestamp=now,
    ))

    # Build task_run dicts.
    task_runs_to_insert: list[dict[str, Any]] = []
    tasks = flow_spec.tasks if flow_spec else []

    for task in tasks:
        is_root = len(task.needs) == 0
        tr: dict[str, Any] = {
            "task_key": task.key,
            "org_id": flow["org_id"],
            "state": "ready" if is_root else "pending",
            "depends_on": list(task.needs),
            "attempt": 0,
            # Embed spec fields so the executor can read them.
            "kind": task.kind,
            "config": dict(task.config),
            "retries": task.retries,
            "retry_backoff_s": task.retry_backoff_s,
            "timeout_s": task.timeout_s,
            "cache_ttl_s": task.cache_ttl_s,
        }
        if is_root:
            tr["scheduled_at"] = now
        task_runs_to_insert.append(tr)

    await store.add_task_runs(flow_run["id"], task_runs_to_insert)

    return flow_run


# ---------------------------------------------------------------------------
# advance_readiness
# ---------------------------------------------------------------------------


async def advance_readiness(
    store: Any,
    flow_run_id: str,
    now: datetime,
) -> None:
    """Transition pending task_runs to ready/upstream_failed; finalise flow_run if done.

    Parameters
    ----------
    store:
        Flow store instance.
    flow_run_id:
        UUID string of the flow_run to advance.
    now:
        Injected clock datetime.
    """
    from app.flows.events import FlowEvent, emit_flow_event  # noqa: PLC0415

    task_runs = await store.list_task_runs(flow_run_id)

    # Build a lookup: task_key → state.
    state_by_key: dict[str, str] = {tr["task_key"]: tr["state"] for tr in task_runs}

    for tr in task_runs:
        if tr["state"] != "pending":
            continue  # only advance pending tasks

        deps: list[str] = tr.get("depends_on") or []

        if not deps:
            # Root task with no dependencies — should already be ready.
            # Defensively mark it ready in case it was missed.
            await store.update_task_run(tr["id"], {"state": "ready", "scheduled_at": now})
            state_by_key[tr["task_key"]] = "ready"
            continue

        dep_states = [state_by_key.get(dep, "pending") for dep in deps]

        if any(s in _BLOCKING_STATES for s in dep_states):
            # At least one upstream is in a blocking (non-success terminal) state.
            await store.update_task_run(tr["id"], {"state": "upstream_failed", "finished_at": now})
            state_by_key[tr["task_key"]] = "upstream_failed"
            emit_flow_event(FlowEvent(
                type="task_upstream_failed",
                flow_run_id=flow_run_id,
                task_key=tr["task_key"],
                state="upstream_failed",
                timestamp=now,
            ))

        elif all(s == "success" for s in dep_states):
            # All upstream succeeded → ready to run.
            await store.update_task_run(tr["id"], {"state": "ready", "scheduled_at": now})
            state_by_key[tr["task_key"]] = "ready"

        # Otherwise some deps are still running/pending — leave as pending.

    # ── Finalise flow_run if all task_runs are terminal ───────────────────────
    all_states = list(state_by_key.values())

    if all_states and all(s in _TERMINAL_STATES for s in all_states):
        # Determine final flow_run state.
        has_failure = any(s in _BLOCKING_STATES for s in all_states)
        flow_run_state = "failed" if has_failure else "success"
        await store.update_flow_run(
            flow_run_id,
            {"state": flow_run_state, "finished_at": now},
        )
        emit_flow_event(FlowEvent(
            type="flow_failed" if has_failure else "flow_success",
            flow_run_id=flow_run_id,
            state=flow_run_state,
            timestamp=now,
        ))

        # Prefect-style outbound alert (best-effort — never breaks the run).
        await _fire_flow_alert(store, flow_run_id, flow_run_state, task_runs, now)


# ---------------------------------------------------------------------------
# Flow-run alert hook (Prefect-style)
# ---------------------------------------------------------------------------


async def _fire_flow_alert(
    store: Any,
    flow_run_id: str,
    flow_run_state: str,
    task_runs: list[dict[str, Any]],
    now: datetime,
) -> None:
    """Send a concise outbound alert when a flow_run finalises.

    Reads the flow's alert config (``flow["spec"]["alerts"]`` /
    ``flow["config"]["alerts"]``) merged with org-level defaults, decides
    whether *flow_run_state* should fire under that config, and if so builds a
    Prefect-style event (flow name, state, duration, link, failed task + error)
    and dispatches it via ``app.chat.notify.notify_flow_run``.

    Best-effort: every step is wrapped so a missing config, a store error, or a
    delivery failure can NEVER break the flow run.  No-op when alerting is not
    configured for the flow/org.

    Parameters
    ----------
    store:
        Flow store instance.
    flow_run_id:
        The finalised flow_run id.
    flow_run_state:
        ``"success"`` or ``"failed"``.
    task_runs:
        The flow's task_runs (used to find the failed task + its error).
    now:
        Injected clock (used as the finish time for duration calc).
    """
    try:
        from app.chat import notify  # noqa: PLC0415

        flow_run = await store.get_flow_run(flow_run_id)
        if flow_run is None:
            return

        flow_id = flow_run.get("flow_id")
        flow = await store.get_flow(flow_id) if flow_id else None

        # Org-level default alert config (settings-driven; safe when absent).
        org_defaults = _org_alert_defaults()
        config = notify.resolve_alert_config(flow, org_defaults)

        if not notify.should_alert(config, flow_run_state):
            return

        # ── Build the event payload ──────────────────────────────────────────
        name = (flow or {}).get("name") or flow_run.get("flow_id") or "flow"
        org_id = flow_run.get("org_id")

        # Duration: started_at → now (finish).
        duration_s: float | None = None
        started_at = flow_run.get("started_at")
        if isinstance(started_at, datetime):
            try:
                duration_s = (now - started_at).total_seconds()
            except Exception:  # noqa: BLE001
                duration_s = None

        # Failed task + error (first blocking task_run, if any).
        failed_task: str | None = None
        error: str | None = None
        if flow_run_state != "success":
            for tr in task_runs:
                if tr.get("state") in _BLOCKING_STATES:
                    failed_task = tr.get("task_key")
                    error = tr.get("error") or flow_run.get("error")
                    break
            if error is None:
                error = flow_run.get("error")

        event = {
            "kind": "flow_run",
            "name": name,
            "state": flow_run_state,
            "flow_run_id": flow_run_id,
            "org_id": org_id,
            "duration_s": duration_s,
            "failed_task": failed_task,
            "error": error,
            "link": _flow_run_link(flow_id, flow_run_id),
        }

        notify.notify_flow_run(event, config=config)
    except Exception as exc:  # noqa: BLE001
        # Alerts are strictly best-effort: log and move on.
        logger.warning(
            "flow-run alert hook failed for flow_run %s: %s", flow_run_id, exc
        )


def _org_alert_defaults() -> dict[str, Any]:
    """Return org-level default alert config from app settings (or ``{}``).

    ``FLOW_ALERTS_DEFAULT_ON`` (comma-separated states, e.g. ``"failed,success"``)
    enables alerting org-wide using the app's configured Slack/WhatsApp targets.
    When unset, returns ``{}`` so only flows with their own ``alerts`` block
    notify.
    """
    try:
        from app.config import get_settings  # noqa: PLC0415

        raw = str(getattr(get_settings(), "FLOW_ALERTS_DEFAULT_ON", "") or "").strip()
    except Exception:  # noqa: BLE001
        return {}
    if not raw:
        return {}
    states = [s.strip().lower() for s in raw.split(",") if s.strip()]
    return {"on": states} if states else {}


def _flow_run_link(flow_id: Any, flow_run_id: str) -> str:
    """Build a UI deep-link to the flow run (best-effort; ``""`` when unknown)."""
    try:
        from app.config import get_settings  # noqa: PLC0415

        base = str(getattr(get_settings(), "FRONTEND_URL", "") or "").rstrip("/")
    except Exception:  # noqa: BLE001
        base = ""
    if not base or not flow_id:
        return ""
    return f"{base}/flows/{flow_id}/runs/{flow_run_id}"


# ---------------------------------------------------------------------------
# run_one_ready_task
# ---------------------------------------------------------------------------


async def run_one_ready_task(
    store: Any,
    now: datetime,
    claims: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Claim and execute one ready task_run.

    Parameters
    ----------
    store:
        Flow store instance.
    now:
        Injected clock datetime.
    claims:
        Caller's auth claims (RLS enforced by query/agent handlers).

    Returns
    -------
    dict | None
        The updated task_run dict (terminal or retrying state), or None if
        no eligible task_run was available.
    """
    from app.flows.executor import TaskContext, execute_task  # noqa: PLC0415

    if claims is None:
        claims = {}

    # Claim the oldest eligible ready task_run (atomic in InMemory, FOR UPDATE in Pg).
    task_run = await store.claim_ready_task_run(now)
    if task_run is None:
        return None

    task_run_id = task_run["id"]
    flow_run_id = task_run["flow_run_id"]

    # ── Cache check ────────────────────────────────────────────────────────────
    # cache_ttl_s > 0 and cache_key set: check for a prior success with same key.
    cache_ttl_s: int = int(task_run.get("cache_ttl_s", 0) or 0)
    cache_key: str | None = task_run.get("cache_key")

    if cache_ttl_s > 0 and cache_key:
        # Look through all task_runs for this flow_run (or could be org-wide in prod).
        all_trs = await store.list_task_runs(flow_run_id)
        for other in all_trs:
            if (
                other["id"] != task_run_id
                and other.get("cache_key") == cache_key
                and other.get("state") == "success"
                and other.get("result") is not None
            ):
                # Cache hit: reuse result.
                finished_tr = await store.update_task_run(
                    task_run_id,
                    {
                        "state": "success",
                        "result": other["result"],
                        "finished_at": now,
                    },
                )
                await advance_readiness(store, flow_run_id, now)
                return finished_tr

    # ── Resolve task spec from flow spec (TaskRun only stores run-time fields) ─
    task_spec = await _get_task_spec(store, task_run)

    # ── Build TaskContext ──────────────────────────────────────────────────────
    # Collect upstream results: task_key → result dict.
    all_task_runs = await store.list_task_runs(flow_run_id)
    inputs: dict[str, Any] = {}
    for tr in all_task_runs:
        if tr["state"] == "success" and tr.get("result") is not None:
            inputs[tr["task_key"]] = tr["result"]

    # Get flow-level params from the flow_run.
    flow_run = await store.get_flow_run(flow_run_id)
    flow_params: dict[str, Any] = (flow_run.get("params") or {}) if flow_run else {}

    ctx = TaskContext(flow_params=flow_params, inputs=inputs, now=now)

    # ── Execute ────────────────────────────────────────────────────────────────
    # Merge task_run with task_spec so execute_task sees kind/config/timeout.
    full_task = {**task_run, **task_spec}
    outcome = execute_task(full_task, ctx, claims)

    attempt: int = int(task_run.get("attempt", 0))
    retries: int = int(task_spec.get("retries", 0))
    _raw_backoff_r = task_spec.get("retry_backoff_s")
    retry_backoff_s: int = int(_raw_backoff_r) if _raw_backoff_r is not None else 30

    outcome_state = outcome["state"]
    outcome_logs = outcome.get("logs") or []
    task_key = task_run.get("task_key", "")

    if outcome_state == "success":
        finished_tr = await store.update_task_run(
            task_run_id,
            {
                "state": "success",
                "result": outcome["result"],
                "finished_at": now,
                "error": None,
                "logs": outcome_logs,
            },
        )
        _emit_task_event("task_success", flow_run_id, task_key, "success", None, attempt, now)
        await advance_readiness(store, flow_run_id, now)
        return finished_tr

    elif outcome_state == "timed_out":
        # Timeouts do not retry — mark terminal immediately.
        timed_out_tr = await store.update_task_run(
            task_run_id,
            {
                "state": "timed_out",
                "error": outcome["error"],
                "finished_at": now,
                "logs": outcome_logs,
            },
        )
        _emit_task_event("task_timed_out", flow_run_id, task_key, "timed_out", outcome["error"], attempt, now)
        await advance_readiness(store, flow_run_id, now)
        return timed_out_tr

    else:
        # Failure path: check if retries remain.
        if attempt < retries:
            # Retry: bump attempt, schedule retry with backoff, emit retrying event.
            retry_at = now + timedelta(seconds=retry_backoff_s)
            await store.update_task_run(
                task_run_id,
                {
                    "state": "retrying",
                    "attempt": attempt + 1,
                    "error": outcome["error"],
                    "scheduled_at": retry_at,
                    "logs": outcome_logs,
                },
            )
            _emit_task_event("task_retrying", flow_run_id, task_key, "retrying", outcome["error"], attempt + 1, now,
                             extra={"retries_left": retries - attempt - 1, "retry_at": retry_at.isoformat()})
            result_tr = await store.get_task_run(task_run_id)
            await advance_readiness(store, flow_run_id, now)
            return result_tr
        else:
            # No retries left → failed.
            failed_tr = await store.update_task_run(
                task_run_id,
                {
                    "state": "failed",
                    "error": outcome["error"],
                    "finished_at": now,
                    "logs": outcome_logs,
                },
            )
            _emit_task_event("task_failed", flow_run_id, task_key, "failed", outcome["error"], attempt, now)
            await advance_readiness(store, flow_run_id, now)
            return failed_tr


# ---------------------------------------------------------------------------
# drain_flow_run
# ---------------------------------------------------------------------------


async def drain_flow_run(
    store: Any,
    flow_run_id: str,
    now: datetime,
    claims: dict[str, Any] | None = None,
    max_steps: int = 200,
) -> dict[str, Any]:
    """Run ready tasks in *flow_run_id* until quiescent or ``max_steps`` hit.

    Parameters
    ----------
    store:
        Flow store instance.
    flow_run_id:
        UUID string of the flow_run to drain.
    now:
        Injected clock datetime.
    claims:
        Caller's auth claims.
    max_steps:
        Safety cap on iteration count.

    Returns
    -------
    dict
        The final flow_run dict.
    """
    if claims is None:
        claims = {}

    steps = 0
    while steps < max_steps:
        # Check if the flow_run itself is already terminal (advance may have done this).
        flow_run = await store.get_flow_run(flow_run_id)
        if flow_run and flow_run.get("state") in ("success", "failed", "cancelled"):
            return flow_run

        # Check if there are any ready task_runs for this specific flow_run.
        task_runs = await store.list_task_runs(flow_run_id)
        # "retrying" tasks with scheduled_at <= now are also eligible (claimed as 'ready').
        ready = [tr for tr in task_runs if tr["state"] == "ready"]
        retrying_due = [
            tr for tr in task_runs
            if tr["state"] == "retrying"
            and (tr.get("scheduled_at") is None or tr["scheduled_at"] <= now)
        ]
        if not ready and not retrying_due:
            break

        # Promote retrying tasks that are due back to ready so claim_ready_task_run picks them up.
        for tr in retrying_due:
            await store.update_task_run(tr["id"], {"state": "ready"})

        # Run one.  Note: claim_ready_task_run is global across the store;
        # we loop until we get one from this flow_run or exhaust ready tasks.
        task_run = await _claim_for_flow_run(store, flow_run_id, now)
        if task_run is None:
            break

        await _execute_claimed_task_run(store, task_run, now, claims)
        steps += 1

    # Re-fetch and return the final state.
    flow_run = await store.get_flow_run(flow_run_id)
    return flow_run or {}


async def _claim_for_flow_run(
    store: Any,
    flow_run_id: str,
    now: datetime,
) -> dict[str, Any] | None:
    """Claim a ready task_run that belongs to *flow_run_id*.

    The global ``claim_ready_task_run`` may claim a task from a different
    flow_run; if so, we release it back to 'ready' and return None so the
    caller can break the drain loop for this specific flow_run.

    In InMemory tests there is only one flow_run so this always matches.
    """
    task_run = await store.claim_ready_task_run(now)
    if task_run is None:
        return None

    if task_run["flow_run_id"] != str(flow_run_id):
        # Belongs to a different flow_run — put it back.
        await store.update_task_run(task_run["id"], {"state": "ready", "started_at": None})
        return None

    return task_run


async def _get_task_spec(store: Any, task_run: dict[str, Any]) -> dict[str, Any]:
    """Look up the task spec dict from the flow spec for a given task_run.

    The task_run only stores TaskRun-shape fields (no kind/config/retries).
    We resolve those by walking: task_run → flow_run → flow → spec → tasks.

    Returns the matching task dict from spec.tasks, or an empty dict on error.
    """
    flow_run_id = task_run.get("flow_run_id")
    task_key = task_run.get("task_key", "")

    if not flow_run_id:
        return {}

    flow_run = await store.get_flow_run(flow_run_id)
    if not flow_run:
        return {}

    flow_id = flow_run.get("flow_id")
    if not flow_id:
        return {}

    flow = await store.get_flow(flow_id)
    if not flow:
        return {}

    spec_data = flow.get("spec") or {}
    tasks = spec_data.get("tasks") or []
    for task in tasks:
        if task.get("key") == task_key:
            return task

    return {}


async def _execute_claimed_task_run(
    store: Any,
    task_run: dict[str, Any],
    now: datetime,
    claims: dict[str, Any],
) -> dict[str, Any]:
    """Execute an already-claimed (state='running') task_run and update state.

    This is the inner body of ``run_one_ready_task`` minus the claim step,
    reused by ``drain_flow_run``.

    The task spec (kind, config, retries, etc.) is resolved from the flow spec
    because the TaskRun shape only stores run-time state fields.
    """
    from app.flows.executor import TaskContext, execute_task  # noqa: PLC0415

    task_run_id = task_run["id"]
    flow_run_id = task_run["flow_run_id"]
    task_key = task_run.get("task_key", "")

    # Emit task_started.
    _emit_task_event("task_started", flow_run_id, task_key, "running", None,
                     int(task_run.get("attempt", 0)), now)

    # Resolve the task spec from the flow spec (TaskRun doesn't store kind/config).
    task_spec = await _get_task_spec(store, task_run)

    # Build TaskContext.
    all_task_runs = await store.list_task_runs(flow_run_id)
    inputs: dict[str, Any] = {}
    for tr in all_task_runs:
        if tr["state"] == "success" and tr.get("result") is not None:
            inputs[tr["task_key"]] = tr["result"]

    flow_run = await store.get_flow_run(flow_run_id)
    flow_params: dict[str, Any] = (flow_run.get("params") or {}) if flow_run else {}

    ctx = TaskContext(flow_params=flow_params, inputs=inputs, now=now)

    # Merge task_run fields with task_spec so execute_task sees kind/config/timeout.
    full_task = {**task_run, **task_spec}

    outcome = execute_task(full_task, ctx, claims)

    attempt: int = int(task_run.get("attempt", 0))
    retries: int = int(task_spec.get("retries", 0))
    # retry_backoff_s of 0 means immediate retry — do NOT fall back to 30.
    _raw_backoff = task_spec.get("retry_backoff_s")
    retry_backoff_s: int = int(_raw_backoff) if _raw_backoff is not None else 30

    outcome_state = outcome["state"]
    outcome_logs = outcome.get("logs") or []

    if outcome_state == "success":
        result_tr = await store.update_task_run(
            task_run_id,
            {
                "state": "success",
                "result": outcome["result"],
                "finished_at": now,
                "error": None,
                "logs": outcome_logs,
            },
        )
        _emit_task_event("task_success", flow_run_id, task_key, "success", None, attempt, now)
        await advance_readiness(store, flow_run_id, now)
        return result_tr or task_run

    elif outcome_state == "timed_out":
        # Timeouts never retry — mark terminal immediately.
        result_tr = await store.update_task_run(
            task_run_id,
            {
                "state": "timed_out",
                "error": outcome["error"],
                "finished_at": now,
                "logs": outcome_logs,
            },
        )
        _emit_task_event("task_timed_out", flow_run_id, task_key, "timed_out", outcome["error"], attempt, now)
        await advance_readiness(store, flow_run_id, now)
        return result_tr or task_run

    else:
        if attempt < retries:
            retry_at = now + timedelta(seconds=retry_backoff_s)
            # Set scheduled_at for the retry delay, bump attempt counter, set state=retrying.
            await store.update_task_run(
                task_run_id,
                {
                    "state": "retrying",
                    "attempt": attempt + 1,
                    "error": outcome["error"],
                    "scheduled_at": retry_at,
                    "logs": outcome_logs,
                },
            )
            _emit_task_event("task_retrying", flow_run_id, task_key, "retrying", outcome["error"], attempt + 1, now,
                             extra={"retries_left": retries - attempt - 1})
            result_tr = await store.get_task_run(task_run_id)
            await advance_readiness(store, flow_run_id, now)
            return result_tr or task_run
        else:
            result_tr = await store.update_task_run(
                task_run_id,
                {
                    "state": "failed",
                    "error": outcome["error"],
                    "finished_at": now,
                    "logs": outcome_logs,
                },
            )
            _emit_task_event("task_failed", flow_run_id, task_key, "failed", outcome["error"], attempt, now)
            await advance_readiness(store, flow_run_id, now)
            return result_tr or task_run


# ---------------------------------------------------------------------------
# Event emission helper
# ---------------------------------------------------------------------------


def _emit_task_event(
    event_type: str,
    flow_run_id: str,
    task_key: str,
    state: str,
    error: str | None,
    attempt: int,
    now: datetime,
    extra: dict | None = None,
) -> None:
    """Emit a task-level flow event (best-effort; exceptions are swallowed)."""
    try:
        from app.flows.events import FlowEvent, emit_flow_event  # noqa: PLC0415

        emit_flow_event(FlowEvent(
            type=event_type,  # type: ignore[arg-type]
            flow_run_id=flow_run_id,
            task_key=task_key,
            state=state,
            error=error,
            attempt=attempt,
            timestamp=now,
            extra=extra or {},
        ))
    except Exception:  # noqa: BLE001
        logger.debug("Failed to emit flow event %s for task %s", event_type, task_key, exc_info=True)


# ---------------------------------------------------------------------------
# flow_tick
# ---------------------------------------------------------------------------


async def flow_tick(
    store: Any,
    now: datetime,
    claims: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Single clock tick: materialise scheduled flows + drain ready tasks.

    Parameters
    ----------
    store:
        Flow store instance.
    now:
        Injected clock datetime.
    claims:
        Optional caller claims (used for draining tasks).

    Returns
    -------
    dict
        Summary: ``{materialised: int, tasks_run: int}``.
    """
    from app.jobs.schedule import next_run as compute_next_run  # noqa: PLC0415

    if claims is None:
        claims = {}

    materialised = 0

    # ── (a) Materialise scheduled flows that are due ──────────────────────────
    # Multi-instance safety (Cloud Run): for each due flow we ATOMICALLY claim
    # its schedule slot via ``store.claim_due_scheduled_flow`` (an
    # ``UPDATE … WHERE next_run_at <= now RETURNING *`` that advances next_run_at
    # in the same statement).  Only the instance that wins the row materialises
    # it — the others get ``None`` and skip — so a due flow runs exactly once
    # per slot even when N instances tick simultaneously.
    due_flows = await _list_due_scheduled_flows(store, now)
    for flow in due_flows:
        schedule = flow.get("schedule")
        if not schedule:
            continue

        # Compute the next slot BEFORE claiming so the claim advances it atomically.
        try:
            new_next = compute_next_run(schedule, now)
        except Exception:  # noqa: BLE001
            new_next = None

        # Atomically claim the slot (advances next_run_at + sets last_run_at).
        claimed = await _claim_due_scheduled_flow(store, flow, now, new_next)
        if claimed is None:
            # Another instance already claimed this slot — skip (no double-run).
            continue

        # We won the slot.  Materialise a scheduled run.
        try:
            await materialize_flow_run(store, claimed, {}, "schedule", now)
            materialised += 1
        except Exception:  # noqa: BLE001
            logger.exception("flow_tick: failed to materialise flow %s", claimed.get("id"))
            continue

    # ── (b) Drain a bounded number of ready tasks across all running runs ──────
    tasks_run = 0
    _MAX_DRAIN = 50

    for _ in range(_MAX_DRAIN):
        task_run = await store.claim_ready_task_run(now)
        if task_run is None:
            # Also check for retrying tasks that are now due.
            break

        try:
            await _execute_claimed_task_run(store, task_run, now, claims)
            tasks_run += 1
        except Exception:  # noqa: BLE001
            logger.exception(
                "flow_tick: unhandled error executing task_run %s", task_run.get("id")
            )
            # Mark as failed so we don't loop forever.
            await store.update_task_run(
                task_run["id"],
                {"state": "failed", "error": "Unhandled tick error.", "finished_at": now},
            )
            await advance_readiness(store, task_run["flow_run_id"], now)

    return {"materialised": materialised, "tasks_run": tasks_run}


async def _claim_due_scheduled_flow(
    store: Any,
    flow: dict[str, Any],
    now: datetime,
    next_run_at: datetime | None,
) -> dict[str, Any] | None:
    """Atomically claim a due scheduled flow's slot (multi-instance safe).

    Prefers ``store.claim_due_scheduled_flow(flow_id, now, next_run_at)`` (both
    stores implement it: Pg via ``UPDATE … RETURNING``, in-memory via a guarded
    re-check).  Falls back to a non-atomic update for any store that predates the
    method (best-effort).
    """
    claimer = getattr(store, "claim_due_scheduled_flow", None)
    if claimer is not None:
        return await claimer(flow["id"], now, next_run_at)

    # Legacy fallback: non-atomic update (single-instance / dev only).
    update_fields: dict[str, Any] = {"last_run_at": now}
    if next_run_at is not None:
        update_fields["next_run_at"] = next_run_at
    await store.update_flow(flow["id"], update_fields)
    return flow


async def _list_due_scheduled_flows(store: Any, now: datetime) -> list[dict[str, Any]]:
    """Return enabled, scheduled flows whose ``next_run_at`` is due (<= now).

    Prefers an async ``store.list_due_scheduled_flows(now)`` method if present
    (PgFlowStore queries the DB directly).  Falls back to scanning the in-memory
    ``_flows`` dict for ``InMemoryFlowStore``.
    """
    lister = getattr(store, "list_due_scheduled_flows", None)
    if lister is not None:
        return await lister(now)

    # In-memory fallback: scan the private _flows dict.
    flows_dict: dict[str, Any] = getattr(store, "_flows", {})
    due: list[dict[str, Any]] = []
    for flow in list(flows_dict.values()):
        if not flow.get("enabled", True):
            continue
        if not flow.get("schedule"):
            continue
        next_run_at = flow.get("next_run_at")
        if next_run_at is not None:
            if hasattr(next_run_at, "tzinfo") and next_run_at.tzinfo is None:
                next_run_at = next_run_at.replace(tzinfo=timezone.utc)
            if next_run_at > now:
                continue
        due.append(deepcopy(flow))
    return due


# ---------------------------------------------------------------------------
# Background worker lifecycle (mirrors app/jobs/runtime.py)
# ---------------------------------------------------------------------------

_worker_task: asyncio.Task[None] | None = None


async def _worker_loop(interval_s: int) -> None:
    """Background asyncio task that calls ``flow_tick`` on a fixed interval."""
    from app.flows.store import get_flow_store  # noqa: PLC0415

    logger.info("Flow worker loop started (interval=%ds).", interval_s)
    while True:
        await asyncio.sleep(interval_s)
        try:
            now = datetime.now(timezone.utc)
            store = get_flow_store()
            summary = await flow_tick(store, now, claims=None)
            if summary.get("materialised") or summary.get("tasks_run"):
                logger.info(
                    "Flow worker tick: materialised=%d tasks_run=%d",
                    summary["materialised"],
                    summary["tasks_run"],
                )
        except Exception:  # noqa: BLE001
            logger.exception("Flow worker tick raised an unhandled exception; continuing.")


def start_flow_worker(app: Any = None) -> None:
    """Start the background flow worker asyncio task.

    Reads ``FLOWS_WORKER_ENABLED`` and ``FLOWS_WORKER_INTERVAL_S`` from
    settings (via ``getattr`` with safe defaults so this module imports even
    before config is updated).

    Parameters
    ----------
    app:
        The FastAPI application instance (unused; accepted for signature
        compatibility with lifespan integration).
    """
    global _worker_task
    if _worker_task is not None and not _worker_task.done():
        logger.debug("start_flow_worker() called but task is already running.")
        return

    try:
        from app.config import get_settings  # noqa: PLC0415

        settings = get_settings()
        # FLOWS_SCHEDULER_ENABLED is the canonical switch (resolved in
        # app.config to inherit from the legacy jobs/flows-worker flags).
        # Fall back to FLOWS_WORKER_ENABLED for safety if it is unset.
        enabled = getattr(settings, "FLOWS_SCHEDULER_ENABLED", None)
        if enabled is None:
            enabled = getattr(settings, "FLOWS_WORKER_ENABLED", False)
        enabled = bool(enabled)
        interval_s: int = getattr(settings, "FLOWS_WORKER_INTERVAL_S", 5)
    except Exception:  # noqa: BLE001
        enabled = False
        interval_s = 5

    if not enabled:
        logger.debug("Flow worker disabled (FLOWS_SCHEDULER_ENABLED=false).")
        return

    _worker_task = asyncio.create_task(
        _worker_loop(interval_s),
        name="nubi-flow-worker",
    )
    logger.info("Flow worker task created (interval=%ds).", interval_s)


async def stop_flow_worker() -> None:
    """Cancel the background flow worker task gracefully."""
    global _worker_task
    if _worker_task is None or _worker_task.done():
        _worker_task = None
        return

    _worker_task.cancel()
    try:
        await _worker_task
    except asyncio.CancelledError:
        pass
    finally:
        _worker_task = None
    logger.info("Flow worker task stopped.")
