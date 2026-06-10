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

    Extended for map/branch:
    - map nodes in 'waiting_children': when all child task_runs are terminal,
      collect results and transition to 'success'/'failed'.
    - branch nodes in 'success': activate matching downstream tasks ('ready'),
      deactivate non-matching ones ('upstream_failed').

run_one_ready_task(store, now, claims, worker_id, lease_seconds) -> task_run | None
    Claim one ready task_run via ``claim_ready_task_run(now)``.  If none is
    available, return None.  Build a ``TaskContext`` from upstream results
    (including ``secrets`` populated via ``secret_store.resolve_all``), execute
    the task, update the task_run to 'success' or handle retry/failed logic,
    then call ``advance_readiness``.  Return the updated task_run.

    Lease safety: after claiming, the lease is extended to cover the task's
    configured ``timeout_s`` (+ grace) so a long-but-bounded task is never
    reaped mid-execution.  Execution runs in a thread with an asyncio
    heartbeat alongside that periodically re-extends the lease (via
    ``store.extend_task_lease``), protecting unbounded (``timeout_s == 0``)
    tasks as well.  The heartbeat stops if the lease is lost to another worker.

drain_flow_run(store, flow_run_id, now, claims, max_steps=200) -> flow_run
    Loop ``run_one_ready_task`` until no ready tasks remain within this
    flow_run or ``max_steps`` is reached.  Returns the final flow_run dict.
    Used by POST /flows/{id}/run for synchronous execution.

flow_tick(store, now, claims=None) -> dict
    Scheduler-only tick:
    (a) Materialise due scheduled flows (next_run_at <= now), advancing
        next_run_at via ``app.jobs.schedule.next_run``.
    (b) Reap expired worker leases (transitions stuck 'running' tasks back
        to 'ready'/'retrying').
    (c) Finalise any flow_runs whose task_runs are all in terminal states.
    Does NOT execute tasks — task execution is handled by ``run_worker_pool``.
    Returns a summary dict: ``{materialised, reaped}``.

run_worker_pool(concurrency, poll_interval, claims, worker_id, lease_seconds) -> None
    Async coroutine that runs ``concurrency`` concurrent worker loops.  Each
    worker claims a ready task_run, builds a TaskContext with secrets resolved
    via ``get_secret_store().resolve_all(org_id)``, executes the task, writes
    results, and calls ``advance_readiness``.  Loops until cancelled.

start_flow_worker(app) / stop_flow_worker()
    asyncio background task lifecycle, mirroring ``app/jobs/runtime.py``.
    Gated by ``FLOWS_WORKER_ENABLED`` and ``FLOWS_WORKER_INTERVAL_S`` settings
    (accessed via ``getattr`` with defaults so this module imports even before
    config is updated).

Task states
-----------
``pending``          — waiting for upstream deps.
``ready``            — deps satisfied; eligible for claiming.
``running``          — currently executing.
``retrying``         — failed but retries remain; re-queued with backoff.
``success``          — completed successfully.
``failed``           — exhausted retries (or no retries configured).
``timed_out``        — exceeded ``timeout_s``; treated as a failed terminal state.
``cancelled``        — manually cancelled (reserved; not set by the engine yet).
``upstream_failed``  — an upstream dep failed/timed_out; this task will not run.
``waiting_children`` — map fan-out launched; waiting for child task_runs to finish.
                       NOT a terminal state — transitions to 'success'/'failed'
                       once all children are terminal.  Not eligible for claiming.
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
# ``waiting_children`` is NOT terminal — it is an intermediate map-fan-out state.
_TERMINAL_STATES = frozenset({"success", "failed", "timed_out", "upstream_failed", "skipped", "cancelled"})
# States that cause dependents to be marked upstream_failed.
# ``skipped`` is kept here so old runs with skipped tasks propagate correctly.
_BLOCKING_STATES = frozenset({"failed", "timed_out", "upstream_failed", "skipped", "cancelled"})
# States that count as a REAL failure for the flow run itself.
# ``upstream_failed``, ``skipped``, and ``cancelled`` are expected terminal states
# (e.g. the non-taken arm of a branch), so they do NOT cause the flow_run to fail.
_FLOW_FAIL_STATES = frozenset({"failed", "timed_out"})

# Default worker lease duration in seconds (matches claim_ready_task_run).
_DEFAULT_LEASE_SECONDS = 300
# Grace added on top of a task's ``timeout_s`` when deriving the claim lease,
# covering spec-resolution/result-write overhead around the bounded execution.
_LEASE_TIMEOUT_GRACE_S = 60


# ---------------------------------------------------------------------------
# materialize_flow_run
# ---------------------------------------------------------------------------


async def _resolve_env(override: str | None, flow: dict[str, Any] | None) -> str:
    """Resolve the active environment for a flow execution.

    PINNED order: explicit *override* → the flow's project DEFAULT environment
    key (``is_default``, via the env store) → the literal ``"prod"`` fallback
    (no project on the flow / no env store reachable).  The flow SPEC is never
    consulted — specs carry no ``env`` field.
    """
    if override and str(override).strip():
        return str(override).strip()
    project_id = (flow or {}).get("project_id")
    if project_id:
        try:
            from app.environments.store import get_env_store  # noqa: PLC0415

            envs = await get_env_store().list_environments(str(project_id))
            for env in envs:
                if env.get("is_default"):
                    return str(env["key"])
        except Exception:  # noqa: BLE001 — env store unavailable → fallback
            pass
    return "prod"


async def materialize_flow_run(
    store: Any,
    flow: dict[str, Any],
    params: dict[str, Any],
    trigger: str,
    now: datetime,
    env: str | None = None,
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
    env:
        Optional trigger-time environment override.  Resolution order (PINNED):
        explicit ``env`` → the flow's project default environment → ``"prod"``.
        The resolved env is stored on the flow_run so materialize tasks
        namespace their targets.

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

    resolved_env = await _resolve_env(env, flow)

    # Create the flow_run (state starts as 'pending' from the store constructor,
    # then we immediately transition it to 'running').
    flow_run = await store.create_flow_run(
        flow_id=flow["id"],
        org_id=flow["org_id"],
        params=params,
        trigger=trigger,
        scheduled_at=None,
        env=resolved_env,
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

    # Effective deps = union(explicit needs, inferred SQL sibling refs).
    # Computed once over the full key set so a SQL cell that references a sibling
    # by name (e.g. SELECT * FROM other_cell) orders after it without an explicit
    # edge.  Inferred refs are never persisted to the spec — only to depends_on.
    from app.flows.deps import effective_needs  # noqa: PLC0415

    all_keys: set[str] = {t.key for t in tasks}

    for task in tasks:
        depends_on = effective_needs(
            {
                "key": task.key,
                "kind": task.kind,
                "needs": list(task.needs),
                "config": dict(task.config),
            },
            all_keys,
        )
        is_root = len(depends_on) == 0
        tr: dict[str, Any] = {
            "task_key": task.key,
            "org_id": flow["org_id"],
            "state": "ready" if is_root else "pending",
            "depends_on": depends_on,
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

    # Build a lookup: task_key → result dict (used to detect branch nodes).
    # A task is a branch node if its result contains the '__branch_next__' sentinel
    # (InMemoryFlowStore does not persist 'kind', so we detect via result instead).
    result_by_key: dict[str, dict] = {
        tr["task_key"]: (tr.get("result") or {})
        for tr in task_runs
    }

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
            # But if any blocking dep is a branch node (has __branch_next__ in its
            # result), skip here — the branch activation block will mark this task
            # upstream_failed with proper semantics.
            has_branch_dep = any(
                "__branch_next__" in result_by_key.get(dep, {})
                for dep in deps
            )
            if has_branch_dep:
                # Leave as pending; branch activation handles it.
                continue
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
            # All upstream succeeded → ready to run, UNLESS one of the deps is a
            # branch node.  Branch-controlled tasks must wait for the branch
            # activation block (below) to selectively activate/deactivate them.
            is_branch_controlled = any(
                "__branch_next__" in result_by_key.get(dep, {})
                for dep in deps
            )
            if is_branch_controlled:
                # Defer to branch activation block — leave as pending.
                continue
            await store.update_task_run(tr["id"], {"state": "ready", "scheduled_at": now})
            state_by_key[tr["task_key"]] = "ready"

        # Otherwise some deps are still running/pending — leave as pending.

    # ── Map fan-in: detect waiting_children nodes whose children are all done ──
    # Reload task_runs to capture any new child task_runs just added.
    task_runs = await store.list_task_runs(flow_run_id)
    # Rebuild state_by_key to include children (child keys contain '[i].' syntax).
    state_by_key = {tr["task_key"]: tr["state"] for tr in task_runs}

    for tr in task_runs:
        if tr["state"] != "waiting_children":
            continue

        map_key = tr["task_key"]
        map_task_run_id = tr["id"]

        # Collect all child task_runs for this map node.
        # Child task keys follow the pattern "{map_key}[{i}].{child_task_key}".
        # The key prefix is unique per map node within a flow (different map nodes
        # have different map_key values).  Use parent_task_run_id when available
        # (set by PgFlowStore); fall back to prefix-only
        # matching for InMemoryFlowStore which does not persist the column.
        _parent_id_available = any(
            "parent_task_run_id" in c for c in task_runs
        )
        if _parent_id_available:
            children = [
                c for c in task_runs
                if c.get("parent_task_run_id") == map_task_run_id
            ]
        else:
            children = [
                c for c in task_runs
                if c["task_key"].startswith(f"{map_key}[")
                and c["id"] != map_task_run_id
            ]
        if not children:
            # No children yet (can happen on a stale advance call) — leave waiting.
            continue

        child_states = [c["state"] for c in children]
        if not all(s in _TERMINAL_STATES for s in child_states):
            # Not all children are terminal yet.
            continue

        has_child_failure = any(s in _BLOCKING_STATES for s in child_states)
        if has_child_failure:
            await store.update_task_run(map_task_run_id, {
                "state": "failed",
                "finished_at": now,
                "error": "One or more map child tasks failed.",
            })
            state_by_key[map_key] = "failed"
            _emit_task_event("task_failed", flow_run_id, map_key, "failed",
                             "One or more map child tasks failed.", 0, now)
        else:
            # Collect results from the body's collect_key tasks.
            # config is stored on the map task_run itself.
            map_config = tr.get("config") or {}
            collect_key: str = map_config.get("collect_key") or ""
            collected = _collect_map_results(children, map_key, collect_key)
            await store.update_task_run(map_task_run_id, {
                "state": "success",
                "result": {
                    "items": collected,
                    "item_count": len(collected),
                    "collect_key": collect_key,
                },
                "finished_at": now,
            })
            state_by_key[map_key] = "success"
            _emit_task_event("task_success", flow_run_id, map_key, "success", None, 0, now)

    # ── Branch activation: for each 'success' branch node, activate/deactivate
    # downstream pending tasks based on __branch_next__ in the result.
    # Branch task_runs are identified by the presence of '__branch_next__' in
    # their result (since InMemoryFlowStore does not persist the 'kind' column).
    for tr in task_runs:
        if tr["state"] != "success":
            continue
        tr_result = tr.get("result") or {}
        if "__branch_next__" not in tr_result:
            continue

        branch_key = tr["task_key"]
        branch_result = tr.get("result") or {}
        active_next: list[str] = branch_result.get("__branch_next__") or []

        # Find all pending task_runs that have this branch key in their depends_on.
        for dep_tr in task_runs:
            if dep_tr["state"] != "pending":
                continue
            dep_depends_on: list[str] = dep_tr.get("depends_on") or []
            if branch_key not in dep_depends_on:
                continue

            dep_task_key = dep_tr["task_key"]

            if dep_task_key in active_next:
                # Active branch path: check if all other deps also succeeded.
                other_deps = [d for d in dep_depends_on if d != branch_key]
                if all(state_by_key.get(d) == "success" for d in other_deps):
                    await store.update_task_run(dep_tr["id"], {
                        "state": "ready",
                        "scheduled_at": now,
                    })
                    state_by_key[dep_task_key] = "ready"
            else:
                # Inactive branch path — mark upstream_failed.
                await store.update_task_run(dep_tr["id"], {
                    "state": "upstream_failed",
                    "finished_at": now,
                })
                state_by_key[dep_task_key] = "upstream_failed"
                emit_flow_event(FlowEvent(
                    type="task_upstream_failed",
                    flow_run_id=flow_run_id,
                    task_key=dep_task_key,
                    state="upstream_failed",
                    timestamp=now,
                ))

    # ── Second pending-task pass: re-advance after map fan-in / branch ───────
    # Map fan-in and branch activation may have changed upstream states.
    # Run a second pass to unblock any pending tasks that are now eligible.
    task_runs = await store.list_task_runs(flow_run_id)
    state_by_key = {tr["task_key"]: tr["state"] for tr in task_runs}

    for tr in task_runs:
        if tr["state"] != "pending":
            continue

        deps: list[str] = tr.get("depends_on") or []
        if not deps:
            await store.update_task_run(tr["id"], {"state": "ready", "scheduled_at": now})
            state_by_key[tr["task_key"]] = "ready"
            continue

        dep_states = [state_by_key.get(dep, "pending") for dep in deps]

        if any(s in _BLOCKING_STATES for s in dep_states):
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
            await store.update_task_run(tr["id"], {"state": "ready", "scheduled_at": now})
            state_by_key[tr["task_key"]] = "ready"

    # ── Finalise flow_run if all task_runs are terminal ───────────────────────
    # Reload state_by_key after all the above mutations.
    task_runs = await store.list_task_runs(flow_run_id)
    state_by_key = {tr["task_key"]: tr["state"] for tr in task_runs}
    all_states = list(state_by_key.values())

    if all_states and all(s in _TERMINAL_STATES for s in all_states):
        # Determine final flow_run state.
        # Only 'failed' and 'timed_out' count as real failures for the flow run.
        # 'upstream_failed' is an expected outcome for non-taken branch arms (and
        # downstream tasks after a failure), so it does NOT make the flow run fail.
        has_failure = any(s in _FLOW_FAIL_STATES for s in all_states)
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
# Map fan-out helpers
# ---------------------------------------------------------------------------


def _expand_map_children(
    flow_run_id: str,
    org_id: str,
    map_task_run_id: str,
    map_task_key: str,
    items: list[Any],
    body_tasks: list[dict[str, Any]],
    item_var: str,
    now: datetime,
    max_concurrency: int = 0,
) -> list[dict[str, Any]]:
    """Build child task_run dicts for a map fan-out.

    Creates one task_run for each (item_index × body_task) combination.
    The task_run ``task_key`` uses the composite format
    ``"{map_key}[{i}].{child_task_key}"``.

    Root body tasks (no ``needs``) are set to ``state='ready'``;
    non-root body tasks are ``state='pending'`` with ``depends_on`` set to
    the composite keys of their upstream body tasks.

    The item value is injected into each task_run's config as
    ``config["__item__"]`` so the python handler can expose it as a local
    variable named by ``item_var``.  Non-python handlers can access it from
    ``config["__item__"]`` directly.

    Parameters
    ----------
    flow_run_id:
        The parent flow run id (shared by all children).
    org_id:
        Org id (copied onto each child task_run).
    map_task_run_id:
        The id of the map task_run (stored as ``parent_task_run_id``).
    map_task_key:
        The map node's task key (e.g. ``"process_each_region"``).
    items:
        The resolved list of items.
    body_tasks:
        List of TaskSpec dicts for the body sub-DAG.
    item_var:
        Variable name used to namespace item fields in body configs
        (stored in config; handled by executor per §3.5).
    now:
        Injected clock datetime (used as ``scheduled_at`` for root tasks).
    max_concurrency:
        Reserved — currently not enforced at the task_run level.
        Pass ``0`` for unlimited.

    Returns
    -------
    list[dict]
        Flat list of task_run dicts, ready for ``store.add_task_runs``.
    """
    child_runs: list[dict[str, Any]] = []

    for i, item in enumerate(items):
        for body_task in body_tasks:
            child_key = f"{map_task_key}[{i}].{body_task['key']}"

            # Map body task needs → composite child keys for the same item index.
            child_depends_on: list[str] = [
                f"{map_task_key}[{i}].{need}"
                for need in (body_task.get("needs") or [])
            ]

            is_root = len(child_depends_on) == 0

            # Copy config and inject the item value.
            child_config: dict[str, Any] = dict(body_task.get("config") or {})
            child_config["__item__"] = item
            child_config["__item_var__"] = item_var
            child_config["__item_index__"] = i

            tr: dict[str, Any] = {
                "task_key": child_key,
                "org_id": org_id,
                "state": "ready" if is_root else "pending",
                "depends_on": child_depends_on,
                "attempt": 0,
                "kind": body_task.get("kind", "noop"),
                "config": child_config,
                "retries": body_task.get("retries", 0),
                "retry_backoff_s": body_task.get("retry_backoff_s", 30),
                "timeout_s": body_task.get("timeout_s", 60),
                "cache_ttl_s": body_task.get("cache_ttl_s", 0),
                "parent_task_run_id": map_task_run_id,
            }
            if is_root:
                tr["scheduled_at"] = now

            child_runs.append(tr)

    return child_runs


def _collect_map_results(
    children: list[dict[str, Any]],
    map_key: str,
    collect_key: str,
) -> list[dict[str, Any]]:
    """Collect results from map child task_runs for the collector task.

    Returns a list of ``{"index": i, "result": {...}}`` dicts, one per item
    index, in ascending index order.  Only children whose ``task_key`` ends
    with ``".<collect_key>"`` are included (the collect_key body task).

    If ``collect_key`` is empty, falls back to collecting all terminal
    non-failed children (last body task heuristic).

    Parameters
    ----------
    children:
        All child task_runs for the map node (any body task, any index).
    map_key:
        The map node task key prefix (e.g. ``"process_each_region"``).
    collect_key:
        Which body task key's results to collect (e.g. ``"transform"``).

    Returns
    -------
    list[dict]
        ``[{"index": int, "result": dict}, ...]`` sorted by index.
    """
    import re  # noqa: PLC0415

    collected: dict[int, Any] = {}

    for child in children:
        key = child["task_key"]
        # Parse composite key: "{map_key}[{i}].{child_key}"
        m = re.match(r"^.+\[(\d+)\]\.(.+)$", key)
        if not m:
            continue
        idx = int(m.group(1))
        child_task_key = m.group(2)

        # Filter to the collect_key task only.
        if collect_key and child_task_key != collect_key:
            continue

        if child.get("state") == "success":
            collected[idx] = child.get("result") or {}

    # Sort by index and build the output list.
    return [
        {"index": idx, "result": result}
        for idx, result in sorted(collected.items())
    ]


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
# Lease safety helpers — timeout-derived leases + worker heartbeat
# ---------------------------------------------------------------------------


async def _extend_lease_for_timeout(
    store: Any,
    task_run: dict[str, Any],
    task_spec: dict[str, Any],
    now: datetime,
    lease_seconds: int,
    grace_s: int = _LEASE_TIMEOUT_GRACE_S,
) -> None:
    """Extend a freshly-claimed task_run's lease to cover its ``timeout_s``.

    The claim sets ``lease_expires_at = now + lease_seconds``, which is too
    short for a task whose configured ``timeout_s`` exceeds the lease — the
    reaper would re-queue (and double-execute) it mid-run.  When the task
    spec carries ``timeout_s > 0`` we extend the lease to
    ``now + max(lease_seconds, timeout_s + grace_s)`` via
    ``store.extend_task_lease`` (conditional on the worker still owning the
    lease).  Tasks with ``timeout_s == 0`` (unbounded) keep the default lease
    and rely on the worker heartbeat instead.

    No-op when leasing is disabled (``lease_seconds == 0``) or the store does
    not implement ``extend_task_lease``.  Best-effort: extension failures are
    logged, never raised.
    """
    if not lease_seconds:
        return
    extender = getattr(store, "extend_task_lease", None)
    if extender is None:
        return
    timeout_s = int(task_spec.get("timeout_s", 0) or 0)
    if timeout_s <= 0:
        return
    effective = max(lease_seconds, timeout_s + grace_s)
    if effective <= lease_seconds:
        return
    try:
        extended = await extender(
            task_run["id"],
            task_run.get("worker_id"),
            now + timedelta(seconds=effective),
        )
        if not extended:
            logger.warning(
                "Could not extend timeout-derived lease for task_run %s (lease lost?).",
                task_run["id"],
            )
    except Exception:  # noqa: BLE001
        logger.exception(
            "Failed to extend timeout-derived lease for task_run %s.", task_run["id"]
        )


async def _heartbeat_task_lease(
    store: Any,
    task_run_id: str,
    worker_id: str | None,
    lease_seconds: int,
    interval_s: float | None = None,
) -> None:
    """Periodically extend a running task_run's lease (worker heartbeat).

    Loops forever — the caller cancels this coroutine when execution
    finishes.  Every *interval_s* (default ``lease_seconds / 3``, floored at
    1 s) the lease is pushed out to ``wall-clock now + lease_seconds`` via
    ``store.extend_task_lease``.  This protects tasks that run longer than
    the lease (notably ``timeout_s == 0`` tasks, whose claim lease is not
    timeout-derived) from being reaped and double-executed.

    Uses the real clock (``datetime.now``) rather than an injected ``now``:
    the heartbeat advances in real time alongside the executing task.

    Stops (returns) when ``extend_task_lease`` reports the lease is no longer
    owned — the task was reaped and re-claimed by another worker; there is no
    point heartbeating a stolen lease.  Store errors are logged and retried
    on the next beat.
    """
    if interval_s is None:
        interval_s = max(lease_seconds / 3.0, 1.0)
    while True:
        await asyncio.sleep(interval_s)
        beat_now = datetime.now(timezone.utc)
        try:
            extended = await store.extend_task_lease(
                task_run_id,
                worker_id,
                beat_now + timedelta(seconds=lease_seconds),
            )
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            logger.exception(
                "Heartbeat failed to extend lease for task_run %s; retrying.",
                task_run_id,
            )
            continue
        if not extended:
            logger.warning(
                "Heartbeat lost lease for task_run %s (worker %s) — stopping.",
                task_run_id,
                worker_id,
            )
            return


async def _execute_with_heartbeat(
    store: Any,
    full_task: dict[str, Any],
    ctx: Any,
    claims: dict[str, Any],
    *,
    task_run_id: str,
    worker_id: str | None,
    lease_seconds: int,
) -> dict[str, Any]:
    """Execute a claimed task in a thread while heartbeating its lease.

    ``execute_task`` is synchronous (it enforces ``timeout_s`` internally via
    a ThreadPoolExecutor), so we run it with ``asyncio.to_thread`` to keep
    the event loop free.  Alongside it, an asyncio heartbeat task
    (``_heartbeat_task_lease``) periodically extends the lease so the
    scheduler's reaper never re-queues a task that is still executing.  The
    heartbeat is cancelled as soon as execution finishes (success or error).

    When leasing is disabled (``lease_seconds == 0``) or the store lacks
    ``extend_task_lease``, execution still runs in a thread but without a
    heartbeat.
    """
    from app.flows.executor import execute_task  # noqa: PLC0415

    heartbeat: asyncio.Task[None] | None = None
    if lease_seconds and getattr(store, "extend_task_lease", None) is not None:
        heartbeat = asyncio.create_task(
            _heartbeat_task_lease(store, task_run_id, worker_id, lease_seconds),
            name=f"nubi-flow-lease-heartbeat-{task_run_id}",
        )
    try:
        return await asyncio.to_thread(execute_task, full_task, ctx, claims)
    finally:
        if heartbeat is not None:
            heartbeat.cancel()
            try:
                await heartbeat
            except asyncio.CancelledError:
                pass


# ---------------------------------------------------------------------------
# run_one_ready_task
# ---------------------------------------------------------------------------


async def run_one_ready_task(
    store: Any,
    now: datetime,
    claims: dict[str, Any] | None = None,
    worker_id: str | None = None,
    lease_seconds: int = 300,
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
    worker_id:
        Opaque identifier for the claiming worker (hostname + pid).  Stored on
        the task_run row so lease reaping can be audited.
    lease_seconds:
        Duration of the worker lease.  The task_run's ``lease_expires_at`` is
        set to ``now + lease_seconds``.  Pass 0 to disable leasing.

    Returns
    -------
    dict | None
        The updated task_run dict (terminal or retrying state), or None if
        no eligible task_run was available.
    """
    from app.flows.executor import TaskContext  # noqa: PLC0415
    from app.vars.store import load_vars_namespace  # noqa: PLC0415

    if claims is None:
        claims = {}

    # Claim the oldest eligible ready task_run (atomic in InMemory, FOR UPDATE in Pg).
    task_run = await store.claim_ready_task_run(now, worker_id=worker_id, lease_seconds=lease_seconds)
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

    # ── Lease safety: cover the task's configured timeout (+ grace) ───────────
    # A bounded task (timeout_s > 0) gets a lease of at least timeout_s + grace
    # so the reaper cannot re-queue it while it is legitimately still running.
    # Unbounded tasks (timeout_s == 0) keep the default lease and are protected
    # by the heartbeat in _execute_with_heartbeat below.
    await _extend_lease_for_timeout(store, task_run, task_spec, now, lease_seconds)

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
    org_id: str = (flow_run.get("org_id") or "") if flow_run else ""

    # ── Resolve secrets for this org ──────────────────────────────────────────
    secrets: dict[str, str] = await _resolve_secrets(org_id)

    # ── Resolve env / flow / incremental watermark ─────────────────────────────
    env, flow_dict, watermark = await _resolve_run_env_context(
        store, flow_run, task_run, task_spec
    )

    ctx = TaskContext(
        flow_params=flow_params,
        inputs=inputs,
        now=now,
        secrets=secrets,
        org_id=org_id or None,
        vars=await load_vars_namespace(org_id, (flow_dict or {}).get("project_id")),
        env=env,
        flow=flow_dict,
        watermark=watermark,
    )

    # ── Execute ────────────────────────────────────────────────────────────────
    # Merge task_run with task_spec so execute_task sees kind/config/timeout.
    full_task = {**task_run, **task_spec}
    # for_each cells are rewritten into a legacy 'map' task so the existing
    # fan-out machinery runs unchanged.
    _apply_for_each_rewrite(full_task, task_spec)
    # Execute in a thread with an asyncio heartbeat extending the lease, so a
    # task running longer than lease_seconds is never reaped mid-execution.
    outcome = await _execute_with_heartbeat(
        store,
        full_task,
        ctx,
        claims,
        task_run_id=task_run_id,
        worker_id=task_run.get("worker_id"),
        lease_seconds=lease_seconds,
    )

    attempt: int = int(task_run.get("attempt", 0))
    retries: int = int(task_spec.get("retries", 0))
    _raw_backoff_r = task_spec.get("retry_backoff_s")
    retry_backoff_s: int = int(_raw_backoff_r) if _raw_backoff_r is not None else 30

    outcome_state = outcome["state"]
    outcome_logs = outcome.get("logs") or []
    task_key = task_run.get("task_key", "")

    if outcome_state == "skipped":
        # run_when gate evaluated False — terminal, no retry, no watermark.
        skipped_tr = await store.update_task_run(
            task_run_id,
            {
                "state": "skipped",
                "result": None,
                "finished_at": now,
                "error": None,
                "logs": outcome_logs,
            },
        )
        _emit_task_event("task_skipped", flow_run_id, task_key, "skipped", None, attempt, now)
        await advance_readiness(store, flow_run_id, now)
        return skipped_tr

    if outcome_state == "success":
        # ── Map fan-out: expand child task_runs before transitioning state ────
        full_kind = full_task.get("kind", "")
        map_items = outcome["result"].get("__map_items__") if outcome["result"] else None

        if full_kind == "map" and map_items is not None:
            body_tasks = full_task.get("config", {}).get("body", [])
            item_var = full_task.get("config", {}).get("item_var", "item")
            max_concurrency = int(full_task.get("config", {}).get("max_concurrency", 0) or 0)

            child_runs = _expand_map_children(
                flow_run_id=flow_run_id,
                org_id=org_id,
                map_task_run_id=task_run_id,
                map_task_key=task_key,
                items=map_items,
                body_tasks=body_tasks,
                item_var=item_var,
                now=now,
                max_concurrency=max_concurrency,
            )
            await store.add_task_runs(flow_run_id, child_runs)

            # Transition map task_run to waiting_children (NOT terminal).
            # Store items in result so _get_task_spec can look up item values for children.
            await store.update_task_run(
                task_run_id,
                {
                    "state": "waiting_children",
                    "result": {
                        "item_count": len(map_items),
                        "__map_items__": map_items,
                        "item_var": item_var,
                    },
                    "finished_at": None,
                    "error": None,
                    "logs": outcome_logs,
                },
            )
            await advance_readiness(store, flow_run_id, now)
            result_tr = await store.get_task_run(task_run_id)
            return result_tr

        # Standard success (non-map).
        # Persist a SQL cell's SELECT result per config.materialized (full/
        # incremental) and merge the manifest (incl. new_watermark) into result.
        _maybe_persist_materialized_cell(
            task_spec, outcome["result"], env=env, flow=flow_dict, watermark=watermark, now=now,
        )
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
        # Persist an advanced incremental watermark, if the handler returned one.
        await _persist_watermark(store, flow_run, task_run, task_spec, outcome["result"])
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


def preview_cell(
    tasks: list[dict[str, Any]],
    target_key: str,
    claims: dict[str, Any] | None = None,
    flow_params: dict[str, Any] | None = None,
    preview_limit: int = 500,
    org_id: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Execute one cell (and all its transitive upstream cells) in preview mode.

    This is the **interactive/preview runtime**: no ``FlowStore``, no
    ``flow_run`` row is persisted, and no work-pool leasing occurs.  The
    function runs synchronously (inside a thread-safe context; not async) and
    is intended for direct use by HTTP route handlers.

    Only tasks listed in *tasks* whose keys appear in the transitive dependency
    chain of *target_key* are executed.  Each task is executed in topological
    order; the result of every completed task is collected into ``inputs`` so
    downstream tasks can reference upstream outputs via ``{{ inputs.key.field }}``.

    The executing ``TaskContext`` has ``preview_mode=True``, so:
    - ``query`` tasks automatically receive ``LIMIT <preview_limit>`` from the
      planner (no data beyond the cap is fetched from the warehouse).
    - The Python→SQL bridge is active: any upstream Python cell result with
      ``rows`` is registered as an in-memory DuckDB table before downstream
      SQL cells run.

    Parameters
    ----------
    tasks:
        Ordered list of task spec dicts (each has ``key``, ``kind``,
        ``needs``, ``config``).  Only tasks reachable from *target_key*
        are executed; extra tasks are ignored.
    target_key:
        Key of the cell to execute (the "run up to here" target).
    claims:
        Caller auth claims for RLS injection.
    flow_params:
        Flow-level parameter values (merged into template resolution).
    preview_limit:
        Maximum rows returned by SQL cells (default 500).
    org_id:
        Org context for connector resolution (may be ``None`` in tests).
    now:
        Injected clock datetime.  Defaults to UTC now.

    Returns
    -------
    dict
        ``{"state": "success"|"failed", "result": dict|None, "error": str|None,
           "logs": list[str], "cell_results": dict[str, dict]}``

        ``cell_results`` maps each executed cell key to its result dict so
        callers can display intermediate outputs.  The ``result`` key at the
        top level is the result of *target_key* specifically.
    """
    from app.flows.executor import TaskContext, execute_task  # noqa: PLC0415
    from app.vars.store import load_vars_namespace  # noqa: PLC0415

    if claims is None:
        claims = {}
    if flow_params is None:
        flow_params = {}
    if now is None:
        now = datetime.now(timezone.utc)

    # ── Build dependency graph ────────────────────────────────────────────────
    # task_by_key: key → task dict
    task_by_key: dict[str, dict[str, Any]] = {t["key"]: t for t in tasks if "key" in t}

    # Effective deps walk: union(explicit needs, inferred SQL sibling refs), so
    # "run up to here" pulls SQL-referenced upstream cells even when the user
    # never drew an edge.
    from app.flows.deps import effective_needs  # noqa: PLC0415

    all_keys: set[str] = set(task_by_key.keys())

    def _upstream(key: str, visited: set[str] | None = None) -> list[str]:
        """Return keys in execution order (topological) needed by *key*."""
        if visited is None:
            visited = set()
        if key in visited:
            return []
        visited.add(key)
        task = task_by_key.get(key)
        if task is None:
            return []
        result: list[str] = []
        for dep in effective_needs(task, all_keys):
            result.extend(_upstream(dep, visited))
        result.append(key)
        return result

    # Deduplicate while preserving order.
    seen: set[str] = set()
    ordered_keys: list[str] = []
    for k in _upstream(target_key):
        if k not in seen:
            seen.add(k)
            ordered_keys.append(k)

    # ── Execute each cell in order ────────────────────────────────────────────
    inputs: dict[str, Any] = {}
    cell_results: dict[str, dict[str, Any]] = {}
    all_logs: list[str] = []

    for cell_key in ordered_keys:
        task = task_by_key.get(cell_key)
        if task is None:
            continue

        ctx = TaskContext(
            flow_params=flow_params,
            inputs=dict(inputs),
            now=now,
            secrets={},
            org_id=org_id,
            # NOTE: this runtime preview helper is sync; {{ vars.* }} for the
            # interactive path is supplied by the async preview route (flows.py).
            preview_mode=True,
            preview_limit=preview_limit,
        )

        outcome = execute_task(task, ctx, claims)
        all_logs.extend(outcome.get("logs") or [])

        if outcome["state"] != "success":
            # Fail fast — return immediately with error info.
            return {
                "state": outcome["state"],
                "result": None,
                "error": outcome.get("error"),
                "logs": all_logs,
                "cell_results": cell_results,
                "failed_cell": cell_key,
            }

        cell_result = outcome["result"] or {}
        inputs[cell_key] = cell_result
        cell_results[cell_key] = cell_result

    target_result = cell_results.get(target_key)
    return {
        "state": "success",
        "result": target_result,
        "error": None,
        "logs": all_logs,
        "cell_results": cell_results,
    }


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

    Extended to support map child task_run composite keys of the form
    ``"{map_key}[{i}].{child_key}"``.  When a direct match is not found, the
    key is parsed and the body sub-DAG of the parent map task is searched.

    Returns the matching task dict from spec.tasks (or body), or an empty dict
    on error.  For map child tasks, the config is augmented with ``__item__``
    from the task_run's own config (set by ``_expand_map_children``).
    """
    import re  # noqa: PLC0415

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

    # ── Direct match (existing behaviour) ────────────────────────────────────
    for task in tasks:
        if task.get("key") == task_key:
            return task

    # ── Map child match: key is "{map_key}[{i}].{child_key}" ─────────────────
    m = re.match(r"^(.+)\[(\d+)\]\.(.+)$", task_key)
    if m:
        map_key, idx_str, child_key = m.group(1), m.group(2), m.group(3)
        idx = int(idx_str)

        # ── for_each synthetic-map child: "{cell}[{i}].__self__" ─────────────
        # A for_each cell is rewritten into a legacy map at run time; its single
        # body task is the cell itself keyed '__self__'.  The parent cell is a
        # query/python task (NOT kind=='map'), so reconstruct the synthetic body
        # deterministically via to_map_config (no store persistence).
        from app.flows.for_each import SELF_BODY_KEY, get_for_each, to_map_config  # noqa: PLC0415

        if child_key == SELF_BODY_KEY:
            for task in tasks:
                if task.get("key") == map_key and get_for_each(task):
                    map_config = to_map_config(task)
                    item_var = map_config.get("item_var", "item")
                    body = map_config.get("body", [])
                    if body:
                        body_task = body[0]
                        spec = dict(body_task)
                        merged_config = dict(body_task.get("config") or {})
                        tr_config = task_run.get("config") or {}
                        if "__item__" in tr_config:
                            merged_config["__item__"] = tr_config["__item__"]
                            merged_config["__item_var__"] = tr_config.get("__item_var__", item_var)
                            merged_config["__item_index__"] = tr_config.get("__item_index__", idx)
                        else:
                            item = await _lookup_map_item(store, flow_run_id, map_key, idx)
                            merged_config["__item__"] = item
                            merged_config["__item_var__"] = item_var
                            merged_config["__item_index__"] = idx
                        spec["config"] = merged_config
                        return spec

        for task in tasks:
            if task.get("key") == map_key and task.get("kind") == "map":
                body = task.get("config", {}).get("body", [])
                item_var = task.get("config", {}).get("item_var", "item")

                for body_task in body:
                    if body_task.get("key") == child_key:
                        spec = dict(body_task)
                        merged_config = dict(body_task.get("config") or {})

                        # Look up the item value.  Prefer the task_run config (set
                        # by _expand_map_children for stores that persist it).
                        # Fall back to the parent map task_run's stored result.
                        tr_config = task_run.get("config") or {}
                        if "__item__" in tr_config:
                            merged_config["__item__"] = tr_config["__item__"]
                            merged_config["__item_var__"] = tr_config.get("__item_var__", item_var)
                            merged_config["__item_index__"] = tr_config.get("__item_index__", idx)
                        else:
                            # Look up from the parent map task_run's stored result.
                            item = await _lookup_map_item(store, flow_run_id, map_key, idx)
                            merged_config["__item__"] = item
                            merged_config["__item_var__"] = item_var
                            merged_config["__item_index__"] = idx

                        spec["config"] = merged_config
                        return spec

    return {}


async def _lookup_map_item(
    store: Any,
    flow_run_id: str,
    map_key: str,
    idx: int,
) -> Any:
    """Look up item[idx] from the parent map task_run's stored result.

    The map task_run stores ``result.__map_items__`` when it transitions to
    ``waiting_children``.  This is used by ``_get_task_spec`` to inject the
    item into child task_run configs for stores that don't persist ``config``.
    """
    all_trs = await store.list_task_runs(flow_run_id)
    for tr in all_trs:
        if tr["task_key"] == map_key and tr.get("state") in ("waiting_children", "running"):
            items = (tr.get("result") or {}).get("__map_items__")
            if items and 0 <= idx < len(items):
                return items[idx]
    return {}


def _is_incremental_materialize(task_spec: dict[str, Any]) -> bool:
    """Return ``True`` when *task_spec* persists a full/incremental materialization.

    Covers both the legacy ``materialize`` task and a ``query`` (SQL) CELL that
    carries ``config.materialized`` with kind ``full`` / ``incremental`` — so
    the existing pre-run watermark READ and post-run WRITE plumbing applies to
    SQL cells with zero new wiring.
    """
    kind = task_spec.get("kind")
    if kind not in ("materialize", "query"):
        return False
    mat = (task_spec.get("config") or {}).get("materialized")
    if not isinstance(mat, dict):
        return False
    return str(mat.get("kind") or "view").lower() in ("full", "incremental")


async def _resolve_run_env_context(
    store: Any,
    flow_run: dict[str, Any] | None,
    task_run: dict[str, Any],
    task_spec: dict[str, Any],
) -> tuple[str, dict[str, Any] | None, str | None]:
    """Resolve (env, flow_dict, watermark) for a task execution.

    - ``env``: from ``flow_run['env']`` → the flow's project default env →
      ``"prod"``.
    - ``flow_dict``: the flow dict (for materialize base-uri resolution).
    - ``watermark``: the stored incremental watermark for this (flow, model,
      env), read BEFORE the handler runs — only for persisted materialize tasks.
    """
    flow_dict: dict[str, Any] | None = None
    flow_id: str | None = None
    if flow_run:
        flow_id = flow_run.get("flow_id")
    if flow_id:
        try:
            flow_dict = await store.get_flow(flow_id)
        except Exception:  # noqa: BLE001
            flow_dict = None

    env = (flow_run or {}).get("env")
    if not env:
        env = await _resolve_env(None, flow_dict)
    env = str(env or "prod")

    watermark: str | None = None
    if _is_incremental_materialize(task_spec) and flow_id:
        getter = getattr(store, "get_watermark", None)
        if getter is not None:
            try:
                watermark = await getter(flow_id, task_run.get("task_key", ""), env)
            except Exception:  # noqa: BLE001
                watermark = None

    return env, flow_dict, watermark


async def _persist_watermark(
    store: Any,
    flow_run: dict[str, Any] | None,
    task_run: dict[str, Any],
    task_spec: dict[str, Any],
    result: dict[str, Any] | None,
) -> None:
    """Persist an advanced incremental watermark after a successful materialize.

    No-op unless the task is a persisted materialize and the handler returned a
    ``new_watermark`` in its result.
    """
    if not _is_incremental_materialize(task_spec):
        return
    if not isinstance(result, dict):
        return
    new_wm = result.get("new_watermark")
    if new_wm is None:
        return
    setter = getattr(store, "set_watermark", None)
    if setter is None:
        return
    flow_id = (flow_run or {}).get("flow_id")
    if not flow_id:
        return
    env = str((flow_run or {}).get("env") or result.get("env") or "prod")
    try:
        await setter(flow_id, task_run.get("task_key", ""), env, str(new_wm))
    except Exception:  # noqa: BLE001
        logger.debug("Failed to persist watermark for flow %s task %s", flow_id, task_run.get("task_key"))


def _apply_for_each_rewrite(
    full_task: dict[str, Any],
    task_spec: dict[str, Any],
) -> None:
    """Rewrite a for_each cell into a legacy ``map`` task IN PLACE.

    Detects a usable ``config.for_each`` block on *task_spec* and, if present,
    mutates *full_task* so its ``kind`` becomes ``'map'`` and its ``config``
    becomes the synthetic-map config (``to_map_config``).  The EXISTING map
    fan-out machinery then runs unchanged.  No-op when there is no for_each.

    Skipped for tasks already keyed as a map child (``"...[i]....``) — those are
    the body executions and must NOT recurse into another fan-out.
    """
    if "[" in full_task.get("task_key", ""):
        return
    from app.flows.for_each import get_for_each, to_map_config  # noqa: PLC0415

    if get_for_each(task_spec):
        full_task["kind"] = "map"
        full_task["config"] = to_map_config(task_spec)


def _maybe_persist_materialized_cell(
    task_spec: dict[str, Any],
    result: dict[str, Any] | None,
    *,
    env: str,
    flow: dict[str, Any] | None,
    watermark: str | None,
    now: datetime,
) -> None:
    """Persist a SQL cell's SELECT result per ``config.materialized`` IN PLACE.

    No-op unless *task_spec* is a ``query`` cell with a full/incremental
    ``config.materialized`` block and *result* carries ``rows``.  Merges the
    persistence manifest (incl. ``new_watermark``) into *result* so the existing
    ``_persist_watermark`` stores the advanced watermark.  Materialized is
    IGNORED in preview (this is only called on the durable success path).
    """
    if task_spec.get("kind") != "query":
        return
    mat = (task_spec.get("config") or {}).get("materialized")
    from app.flows.cell_materialize import is_persisted, persist_query_result  # noqa: PLC0415

    if not is_persisted(mat):
        return
    if not isinstance(result, dict):
        return
    rows = result.get("rows")
    if rows is None:
        return
    columns = result.get("columns")
    manifest = persist_query_result(
        rows,
        columns,
        mat,
        env=env,
        flow=flow,
        watermark=watermark,
        now=now,
    )
    result.update(manifest)


async def _resolve_secrets(org_id: str) -> dict[str, str]:
    """Resolve all secrets for *org_id* from the secret store.

    Returns an empty dict when the secret store is unavailable (e.g. during
    tests that do not configure it), so execution can still proceed.

    The secret store contract (CoreWiringAgent SEAM):
        ``get_secret_store().resolve_all(org_id) -> {name: plaintext}``
    """
    try:
        from app.secrets.store import get_secret_store  # noqa: PLC0415

        secret_store = get_secret_store()
        return await secret_store.resolve_all(org_id)
    except Exception:  # noqa: BLE001
        logger.debug("Secret store unavailable for org %s; proceeding with empty secrets.", org_id)
        return {}


async def _execute_claimed_task_run(
    store: Any,
    task_run: dict[str, Any],
    now: datetime,
    claims: dict[str, Any],
    secrets: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Execute an already-claimed (state='running') task_run and update state.

    This is the inner body of ``run_one_ready_task`` minus the claim step,
    reused by ``drain_flow_run``.

    The task spec (kind, config, retries, etc.) is resolved from the flow spec
    because the TaskRun shape only stores run-time state fields.

    Parameters
    ----------
    store:
        Flow store instance.
    task_run:
        The claimed task_run dict (state='running').
    now:
        Injected clock datetime.
    claims:
        Caller's auth claims.
    secrets:
        Pre-resolved secrets dict ``{name: plaintext}``.  When ``None``, they
        are resolved lazily via ``_resolve_secrets``.
    """
    from app.flows.executor import TaskContext, execute_task  # noqa: PLC0415
    from app.vars.store import load_vars_namespace  # noqa: PLC0415

    task_run_id = task_run["id"]
    flow_run_id = task_run["flow_run_id"]
    task_key = task_run.get("task_key", "")

    # Emit task_started.
    _emit_task_event("task_started", flow_run_id, task_key, "running", None,
                     int(task_run.get("attempt", 0)), now)

    # Resolve the task spec from the flow spec (TaskRun doesn't store kind/config).
    task_spec = await _get_task_spec(store, task_run)

    # Lease safety: the embedded synchronous drain path executes inline (no
    # heartbeat), so at minimum extend the claim lease to cover the task's
    # configured timeout (+ grace) — the reaper must not re-queue a bounded
    # task that is still legitimately running.
    await _extend_lease_for_timeout(
        store, task_run, task_spec, now, _DEFAULT_LEASE_SECONDS
    )

    # Build TaskContext.
    all_task_runs = await store.list_task_runs(flow_run_id)
    inputs: dict[str, Any] = {}
    for tr in all_task_runs:
        if tr["state"] == "success" and tr.get("result") is not None:
            inputs[tr["task_key"]] = tr["result"]

    flow_run = await store.get_flow_run(flow_run_id)
    flow_params: dict[str, Any] = (flow_run.get("params") or {}) if flow_run else {}
    org_id: str = (flow_run.get("org_id") or "") if flow_run else ""

    # Resolve secrets (use pre-resolved if provided, else lazy resolution).
    resolved_secrets: dict[str, str] = secrets if secrets is not None else await _resolve_secrets(org_id)

    # ── Resolve env / flow / incremental watermark ─────────────────────────────
    env, flow_dict, watermark = await _resolve_run_env_context(
        store, flow_run, task_run, task_spec
    )

    ctx = TaskContext(
        flow_params=flow_params,
        inputs=inputs,
        now=now,
        secrets=resolved_secrets,
        org_id=org_id or None,
        vars=await load_vars_namespace(org_id, (flow_dict or {}).get("project_id")),
        env=env,
        flow=flow_dict,
        watermark=watermark,
    )

    # Merge task_run fields with task_spec so execute_task sees kind/config/timeout.
    full_task = {**task_run, **task_spec}
    # for_each cells are rewritten into a legacy 'map' task so the existing
    # fan-out machinery runs unchanged.
    _apply_for_each_rewrite(full_task, task_spec)

    outcome = execute_task(full_task, ctx, claims)

    attempt: int = int(task_run.get("attempt", 0))
    retries: int = int(task_spec.get("retries", 0))
    # retry_backoff_s of 0 means immediate retry — do NOT fall back to 30.
    _raw_backoff = task_spec.get("retry_backoff_s")
    retry_backoff_s: int = int(_raw_backoff) if _raw_backoff is not None else 30

    outcome_state = outcome["state"]
    outcome_logs = outcome.get("logs") or []

    if outcome_state == "skipped":
        # run_when gate evaluated False — terminal, no retry, no watermark.
        result_tr = await store.update_task_run(
            task_run_id,
            {
                "state": "skipped",
                "result": None,
                "finished_at": now,
                "error": None,
                "logs": outcome_logs,
            },
        )
        _emit_task_event("task_skipped", flow_run_id, task_key, "skipped", None, attempt, now)
        await advance_readiness(store, flow_run_id, now)
        return result_tr or task_run

    if outcome_state == "success":
        # ── Map fan-out: expand child task_runs before transitioning state ────
        full_kind = full_task.get("kind", "")
        map_items = outcome["result"].get("__map_items__") if outcome["result"] else None

        if full_kind == "map" and map_items is not None:
            body_tasks = full_task.get("config", {}).get("body", [])
            item_var = full_task.get("config", {}).get("item_var", "item")
            max_concurrency = int(full_task.get("config", {}).get("max_concurrency", 0) or 0)

            # Build child task_runs for all item × body-task combinations.
            child_runs = _expand_map_children(
                flow_run_id=flow_run_id,
                org_id=org_id,
                map_task_run_id=task_run_id,
                map_task_key=task_key,
                items=map_items,
                body_tasks=body_tasks,
                item_var=item_var,
                now=now,
                max_concurrency=max_concurrency,
            )
            await store.add_task_runs(flow_run_id, child_runs)

            # Transition map task_run to waiting_children (NOT yet terminal).
            # Store the items list in the result so _get_task_spec can resolve
            # item values for child tasks (InMemoryFlowStore does not persist
            # the config column on child task_runs).
            await store.update_task_run(
                task_run_id,
                {
                    "state": "waiting_children",
                    "result": {
                        "item_count": len(map_items),
                        "__map_items__": map_items,
                        "item_var": item_var,
                    },
                    "finished_at": None,
                    "error": None,
                    "logs": outcome_logs,
                },
            )
            _emit_task_event("task_started", flow_run_id, task_key, "waiting_children", None, attempt, now)
            await advance_readiness(store, flow_run_id, now)
            result_tr = await store.get_task_run(task_run_id)
            return result_tr or task_run

        # Standard success path (non-map).
        # Persist a SQL cell's SELECT result per config.materialized (full/
        # incremental) and merge the manifest (incl. new_watermark) into result.
        _maybe_persist_materialized_cell(
            task_spec, outcome["result"], env=env, flow=flow_dict, watermark=watermark, now=now,
        )
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
        # Persist an advanced incremental watermark, if the handler returned one.
        await _persist_watermark(store, flow_run, task_run, task_spec, outcome["result"])
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
    """Scheduler-only tick: materialise scheduled flows + reap expired leases.

    This function acts as the *scheduler* — it does NOT execute tasks.  Task
    execution is handled by the worker pool (``run_worker_pool``).  Keeping the
    two concerns separate allows the scheduler and worker(s) to run
    independently (e.g. an external scheduler ticking the app, workers on
    separate machines).

    For backward compatibility the returned dict still contains ``tasks_run``
    (always 0) so existing callers that only check ``materialised`` keep working.

    Parameters
    ----------
    store:
        Flow store instance.
    now:
        Injected clock datetime.
    claims:
        Accepted but unused (kept for API compatibility).

    Returns
    -------
    dict
        Summary: ``{materialised: int, reaped: int, tasks_run: int}``.
        ``tasks_run`` is always 0; ``reaped`` counts task_runs whose expired
        leases were cleared and re-queued.
    """
    from app.jobs.schedule import next_run as compute_next_run  # noqa: PLC0415

    if claims is None:
        claims = {}

    materialised = 0

    # ── (a) Materialise scheduled flows that are due ──────────────────────────
    # Multi-instance safety (N app instances): for each due flow we ATOMICALLY claim
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

    # ── (b) Reap expired worker leases ───────────────────────────────────────
    # Re-queue task_runs stuck in 'running' with an expired lease so another
    # worker can pick them up.
    reaped = 0
    reaper = getattr(store, "reap_expired_leases", None)
    if reaper is not None:
        try:
            reaped = await reaper(now)
            if reaped:
                logger.info("flow_tick: reaped %d expired task_run lease(s).", reaped)
        except Exception:  # noqa: BLE001
            logger.exception("flow_tick: reap_expired_leases raised an error.")

    return {"materialised": materialised, "reaped": reaped, "tasks_run": 0}


# ---------------------------------------------------------------------------
# run_worker_pool
# ---------------------------------------------------------------------------


async def run_worker_pool(
    concurrency: int = 4,
    poll_interval: float = 1.0,
    claims: dict[str, Any] | None = None,
    worker_id: str | None = None,
    lease_seconds: int = 300,
    _max_iterations: int | None = None,
) -> None:
    """Run a pool of N concurrent async task workers.

    Each worker claims a ready task_run from the global store, executes it
    (with secrets resolved via the secret store), and loops.  Workers back off
    with ``poll_interval`` when no tasks are available.

    This coroutine runs until cancelled (or until ``_max_iterations`` is
    reached, which is used in tests for bounded execution).

    Parameters
    ----------
    concurrency:
        Number of concurrent worker coroutines.
    poll_interval:
        Seconds to sleep between poll attempts when no task is found.
    claims:
        Auth claims passed to execute_task handlers.
    worker_id:
        Opaque identifier for this worker process (e.g. ``hostname:pid``).
        Defaults to ``socket.gethostname():os.getpid()``.
    lease_seconds:
        Worker lease duration in seconds.  task_runs with an expired lease
        are reaped by the scheduler tick and re-queued for another worker.
    _max_iterations:
        Internal: stop each worker after this many claim attempts.  ``None``
        means run forever (production).  Set to a small integer in tests.
    """
    import os  # noqa: PLC0415
    import socket  # noqa: PLC0415

    from app.flows.store import get_flow_store  # noqa: PLC0415

    if claims is None:
        claims = {}

    if worker_id is None:
        try:
            worker_id = f"{socket.gethostname()}:{os.getpid()}"
        except Exception:  # noqa: BLE001
            worker_id = "unknown"

    async def _single_worker(worker_index: int) -> None:
        """Inner loop for a single worker."""
        tagged_id = f"{worker_id}:w{worker_index}"
        iteration = 0
        while _max_iterations is None or iteration < _max_iterations:
            iteration += 1
            now = datetime.now(timezone.utc)
            store = get_flow_store()
            try:
                result = await run_one_ready_task(
                    store,
                    now,
                    claims=claims,
                    worker_id=tagged_id,
                    lease_seconds=lease_seconds,
                )
                if result is None:
                    # No task available — back off.
                    await asyncio.sleep(poll_interval)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                logger.exception(
                    "Worker %s: unhandled error in run_one_ready_task.", tagged_id
                )
                await asyncio.sleep(poll_interval)

    logger.info(
        "run_worker_pool starting: concurrency=%d poll_interval=%.1fs lease=%ds",
        concurrency,
        poll_interval,
        lease_seconds,
    )
    workers = [asyncio.create_task(_single_worker(i), name=f"nubi-flow-worker-{i}") for i in range(concurrency)]
    try:
        await asyncio.gather(*workers)
    except asyncio.CancelledError:
        for w in workers:
            w.cancel()
        await asyncio.gather(*workers, return_exceptions=True)
        raise


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
_scheduler_task: asyncio.Task[None] | None = None


async def _worker_loop(interval_s: int) -> None:
    """Background asyncio task that calls ``flow_tick`` (scheduler) on a fixed interval.

    In the embedded (single-process) mode the same process runs both the
    scheduler tick and the worker pool.  The scheduler tick materialises due
    flows and reaps expired leases; task execution is handled by the worker
    pool started alongside it.
    """
    from app.flows.store import get_flow_store  # noqa: PLC0415

    logger.info("Flow scheduler loop started (interval=%ds).", interval_s)
    while True:
        await asyncio.sleep(interval_s)
        try:
            now = datetime.now(timezone.utc)
            store = get_flow_store()
            summary = await flow_tick(store, now, claims=None)
            if summary.get("materialised") or summary.get("reaped"):
                logger.info(
                    "Flow scheduler tick: materialised=%d reaped=%d",
                    summary["materialised"],
                    summary.get("reaped", 0),
                )
        except Exception:  # noqa: BLE001
            logger.exception("Flow scheduler tick raised an unhandled exception; continuing.")


def start_flow_worker(app: Any = None) -> None:
    """Start the background flow scheduler and worker pool asyncio tasks.

    Reads settings (via ``getattr`` with safe defaults so this module imports
    even before config is updated):

    - ``FLOWS_SCHEDULER_ENABLED`` / ``FLOWS_WORKER_ENABLED`` — master switch.
    - ``FLOWS_WORKER_INTERVAL_S``  — scheduler tick interval (default 5 s).
    - ``FLOWS_WORKER_CONCURRENCY`` — worker pool concurrency (default 4).

    Two asyncio tasks are started:
    1. The *scheduler* (``_worker_loop``) — ticks ``flow_tick`` to materialise
       scheduled flows and reap expired leases.
    2. The *worker pool* (``run_worker_pool``) — N concurrent workers that
       claim and execute ready task_runs.

    Parameters
    ----------
    app:
        The FastAPI application instance (unused; accepted for signature
        compatibility with lifespan integration).
    """
    global _worker_task, _scheduler_task
    if _worker_task is not None and not _worker_task.done():
        logger.debug("start_flow_worker() called but tasks are already running.")
        return

    try:
        from app.config import get_settings  # noqa: PLC0415

        settings = get_settings()
        # FLOWS_SCHEDULER_ENABLED is the canonical switch.
        enabled = getattr(settings, "FLOWS_SCHEDULER_ENABLED", None)
        if enabled is None:
            enabled = getattr(settings, "FLOWS_WORKER_ENABLED", False)
        enabled = bool(enabled)
        interval_s: int = int(getattr(settings, "FLOWS_WORKER_INTERVAL_S", 5) or 5)
        concurrency: int = int(getattr(settings, "FLOWS_WORKER_CONCURRENCY", 4) or 4)
    except Exception:  # noqa: BLE001
        enabled = False
        interval_s = 5
        concurrency = 4

    if not enabled:
        logger.debug("Flow worker disabled (FLOWS_SCHEDULER_ENABLED=false).")
        return

    # Scheduler task.
    _scheduler_task = asyncio.create_task(
        _worker_loop(interval_s),
        name="nubi-flow-scheduler",
    )
    # Worker pool task.
    _worker_task = asyncio.create_task(
        run_worker_pool(concurrency=concurrency, poll_interval=max(1.0, interval_s / 2)),
        name="nubi-flow-worker-pool",
    )
    logger.info(
        "Flow worker started: scheduler interval=%ds, worker pool concurrency=%d.",
        interval_s,
        concurrency,
    )


async def stop_flow_worker() -> None:
    """Cancel the background flow scheduler and worker pool tasks gracefully."""
    global _worker_task, _scheduler_task

    for task, label in ((_worker_task, "worker pool"), (_scheduler_task, "scheduler")):
        if task is None or task.done():
            continue
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        logger.info("Flow %s task stopped.", label)

    _worker_task = None
    _scheduler_task = None
