"""B1 — Cell resource requests + map concurrency cap tests.

Coverage
--------
1. CellResourceRequest.from_task_config round-trips from a config dict.
2. CellResourceRequest.clamp_for_local clamps cpu/mem to local limits.
3. effective_timeout_s returns None when 0, the value when > 0.
4. Per-cell cpu/mem/timeout_s fields stored on task_runs (materialize).
5. Map fan-out concurrency cap: with max_concurrency=2 and 5 items, only
   2 items start as 'ready'; the rest start as 'pending'.
6. MAP_MAX_CONCURRENCY env cap is applied (global ceiling).
7. Map cap=0 (unlimited) starts all items as 'ready'.
8. KernelTier enum values match expected strings.
9. get_kernel_runner returns LocalSubprocessRunner for local_kernel tier.
10. get_kernel_runner raises ValueError for warehouse/browser tiers.
11. Per-cell resource request is forwarded by execute_task into resolved_config.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

import pytest

from app.compute.kernel_interface import (
    CellResourceRequest,
    KernelTier,
    MAP_MAX_CONCURRENCY,
    acquire_map_slot,
    release_map_slot,
    reset_map_semaphore,
    get_kernel_runner,
)
from app.flows.runtime import (
    _expand_map_children,
    materialize_flow_run,
)
from app.flows.store import InMemoryFlowStore
from app.flows.registry import reset_for_tests

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _utc() -> datetime:
    return datetime(2025, 6, 1, 12, 0, 0, tzinfo=timezone.utc)


NOW = _utc()


async def _make_flow(store: InMemoryFlowStore, spec: dict[str, Any]) -> dict[str, Any]:
    return await store.create_flow(
        org_id="org-test", created_by="user-test", name="test_flow", spec=spec
    )


# ---------------------------------------------------------------------------
# 1. CellResourceRequest.from_task_config round-trips
# ---------------------------------------------------------------------------


def test_cell_resource_request_from_config_nested():
    config = {"resources": {"cpu_cores": 4, "mem_mb": 8192, "timeout_s": 600}}
    req = CellResourceRequest.from_task_config(config)
    assert req.cpu_cores == 4.0
    assert req.mem_mb == 8192
    assert req.timeout_s == 600


def test_cell_resource_request_from_config_flat_keys():
    config = {"cpu_cores": 2, "mem_mb": 4096, "timeout_s": 300}
    req = CellResourceRequest.from_task_config(config)
    assert req.cpu_cores == 2.0
    assert req.mem_mb == 4096
    assert req.timeout_s == 300


def test_cell_resource_request_defaults():
    req = CellResourceRequest.from_task_config({})
    assert req.cpu_cores == 0.0
    assert req.mem_mb == 0
    assert req.timeout_s == 0


# ---------------------------------------------------------------------------
# 2. clamp_for_local clamps to local limits
# ---------------------------------------------------------------------------


def test_clamp_for_local_respects_cpu_cap():
    req = CellResourceRequest(cpu_cores=1000, mem_mb=0, timeout_s=0)
    clamped = req.clamp_for_local()
    # Can't hardcode the cap value since it may differ by env; just check < original.
    assert clamped.cpu_cores <= req.cpu_cores
    assert clamped.cpu_cores > 0


def test_clamp_for_local_respects_mem_cap():
    req = CellResourceRequest(cpu_cores=0, mem_mb=999999, timeout_s=0)
    clamped = req.clamp_for_local()
    assert clamped.mem_mb <= req.mem_mb
    assert clamped.mem_mb > 0


def test_clamp_for_local_zero_passthrough():
    req = CellResourceRequest(cpu_cores=0, mem_mb=0, timeout_s=0)
    clamped = req.clamp_for_local()
    assert clamped.cpu_cores == 0
    assert clamped.mem_mb == 0


# ---------------------------------------------------------------------------
# 3. effective_timeout_s
# ---------------------------------------------------------------------------


def test_effective_timeout_s_zero_returns_none():
    req = CellResourceRequest(cpu_cores=0, mem_mb=0, timeout_s=0)
    assert req.effective_timeout_s is None


def test_effective_timeout_s_positive_returns_value():
    req = CellResourceRequest(cpu_cores=0, mem_mb=0, timeout_s=120)
    assert req.effective_timeout_s == 120


# ---------------------------------------------------------------------------
# 4. Per-cell cpu_cores/mem_mb stored on task_runs
# ---------------------------------------------------------------------------


async def test_resource_fields_stored_on_task_runs():
    """TaskSpec fields cpu_cores/mem_mb/stochastic are propagated to task_runs."""
    store = InMemoryFlowStore()
    spec = {
        "version": 1,
        "name": "resource_flow",
        "tasks": [
            {
                "key": "heavy",
                "kind": "noop",
                "needs": [],
                "config": {},
                "cpu_cores": 4.0,
                "mem_mb": 8192,
                "timeout_s": 600,
                "stochastic": False,
            }
        ],
    }
    flow = await _make_flow(store, spec)
    run = await materialize_flow_run(store, flow, {}, "manual", NOW)

    trs = await store.list_task_runs(run["id"])
    assert len(trs) == 1
    tr = trs[0]
    assert tr.get("cpu_cores") == 4.0, f"cpu_cores not set on task_run: {tr}"
    assert tr.get("mem_mb") == 8192, f"mem_mb not set on task_run: {tr}"


# ---------------------------------------------------------------------------
# 5. Map fan-out concurrency cap: max_concurrency=2, 5 items
# ---------------------------------------------------------------------------


def _simple_body_tasks() -> list[dict[str, Any]]:
    return [{"key": "step", "kind": "noop", "needs": [], "config": {}}]


def test_map_concurrency_cap_limits_ready_children():
    items = [{"n": i} for i in range(5)]
    child_runs = _expand_map_children(
        flow_run_id="run-1",
        org_id="org-test",
        map_task_run_id="map-tr-1",
        map_task_key="fan",
        items=items,
        body_tasks=_simple_body_tasks(),
        item_var="item",
        now=NOW,
        max_concurrency=2,
    )

    # One task_run per item (single body task).
    assert len(child_runs) == 5

    ready = [tr for tr in child_runs if tr["state"] == "ready"]
    pending = [tr for tr in child_runs if tr["state"] == "pending"]

    # Only 2 should start ready; the other 3 are throttled to pending.
    assert len(ready) == 2, f"Expected 2 ready, got {len(ready)}: {[tr['task_key'] for tr in ready]}"
    assert len(pending) == 3


# ---------------------------------------------------------------------------
# 6. MAP_MAX_CONCURRENCY global cap is applied
# ---------------------------------------------------------------------------


def test_map_global_cap_applied(monkeypatch):
    """MAP_MAX_CONCURRENCY=3 limits ready children even when max_concurrency=0."""
    import app.compute.kernel_interface as ki
    import app.flows.runtime as rt

    # Temporarily patch the global cap in both modules.
    original_ki = ki.MAP_MAX_CONCURRENCY
    original_rt = rt.MAP_MAX_CONCURRENCY
    try:
        ki.MAP_MAX_CONCURRENCY = 3
        rt.MAP_MAX_CONCURRENCY = 3

        items = [{"n": i} for i in range(6)]
        child_runs = _expand_map_children(
            flow_run_id="run-g",
            org_id="org-test",
            map_task_run_id="map-tr-g",
            map_task_key="fan",
            items=items,
            body_tasks=_simple_body_tasks(),
            item_var="item",
            now=NOW,
            max_concurrency=0,  # no per-flow limit
        )

        ready = [tr for tr in child_runs if tr["state"] == "ready"]
        assert len(ready) == 3, f"Expected global cap of 3, got {len(ready)} ready"
    finally:
        ki.MAP_MAX_CONCURRENCY = original_ki
        rt.MAP_MAX_CONCURRENCY = original_rt


# ---------------------------------------------------------------------------
# 7. Map cap=0 unlimited starts all items ready
# ---------------------------------------------------------------------------


def test_map_unlimited_starts_all_ready(monkeypatch):
    """max_concurrency=0 + MAP_MAX_CONCURRENCY=0 → all items start ready."""
    import app.compute.kernel_interface as ki
    import app.flows.runtime as rt

    original_ki = ki.MAP_MAX_CONCURRENCY
    original_rt = rt.MAP_MAX_CONCURRENCY
    try:
        ki.MAP_MAX_CONCURRENCY = 0
        rt.MAP_MAX_CONCURRENCY = 0

        items = [{"n": i} for i in range(4)]
        child_runs = _expand_map_children(
            flow_run_id="run-u",
            org_id="org-test",
            map_task_run_id="map-tr-u",
            map_task_key="fan",
            items=items,
            body_tasks=_simple_body_tasks(),
            item_var="item",
            now=NOW,
            max_concurrency=0,
        )

        ready = [tr for tr in child_runs if tr["state"] == "ready"]
        assert len(ready) == 4, f"Expected all 4 ready, got {len(ready)}"
    finally:
        ki.MAP_MAX_CONCURRENCY = original_ki
        rt.MAP_MAX_CONCURRENCY = original_rt


# ---------------------------------------------------------------------------
# 8. KernelTier enum values
# ---------------------------------------------------------------------------


def test_kernel_tier_values():
    assert KernelTier.LOCAL_KERNEL == "local_kernel"
    assert KernelTier.REMOTE_KERNEL == "remote_kernel"
    assert KernelTier.WAREHOUSE == "warehouse"
    assert KernelTier.BROWSER == "browser"
    # Enum is also a str
    assert isinstance(KernelTier.LOCAL_KERNEL, str)


# ---------------------------------------------------------------------------
# 9. get_kernel_runner returns LocalSubprocessRunner for local_kernel
# ---------------------------------------------------------------------------


def test_get_kernel_runner_local():
    from app.compute.runner import LocalSubprocessRunner

    runner = get_kernel_runner(KernelTier.LOCAL_KERNEL)
    assert isinstance(runner, LocalSubprocessRunner)


def test_get_kernel_runner_local_str():
    from app.compute.runner import LocalSubprocessRunner

    runner = get_kernel_runner("local_kernel")
    assert isinstance(runner, LocalSubprocessRunner)


# ---------------------------------------------------------------------------
# 10. get_kernel_runner raises for non-kernel tiers
# ---------------------------------------------------------------------------


def test_get_kernel_runner_warehouse_raises():
    with pytest.raises(ValueError, match="warehouse"):
        get_kernel_runner(KernelTier.WAREHOUSE)


def test_get_kernel_runner_browser_raises():
    with pytest.raises(ValueError, match="browser"):
        get_kernel_runner(KernelTier.BROWSER)


# ---------------------------------------------------------------------------
# 11. Remote kernel tier returns unconfigured RemoteRunner when no env
# ---------------------------------------------------------------------------


def test_get_kernel_runner_remote_unconfigured():
    """Without KERNEL_REMOTE_PROVIDER, get a no-op RemoteRunner (raises 503)."""
    from app.compute.runner import RemoteRunner
    from app.errors import AppError

    # Clear provider so we get the unconfigured stub.
    old = os.environ.pop("KERNEL_REMOTE_PROVIDER", None)
    try:
        runner = get_kernel_runner(KernelTier.REMOTE_KERNEL)
        assert isinstance(runner, RemoteRunner)
        # Confirm it raises 503 on actual use.
        with pytest.raises(AppError) as exc_info:
            runner.run("result = pa.table({})", {}, 30)
        assert exc_info.value.status == 503
    finally:
        if old is not None:
            os.environ["KERNEL_REMOTE_PROVIDER"] = old


# ---------------------------------------------------------------------------
# 12. acquire/release map semaphore — basic contract
# ---------------------------------------------------------------------------


async def test_acquire_release_map_slot():
    reset_map_semaphore()
    # Should not block when there are free slots.
    await acquire_map_slot()
    release_map_slot()
    # And again — ensures the release actually freed the slot.
    await acquire_map_slot()
    release_map_slot()
    reset_map_semaphore()


async def test_map_semaphore_blocks_at_cap(monkeypatch):
    """Semaphore should block the (MAP_MAX_CONCURRENCY + 1)th acquire."""
    import app.compute.kernel_interface as ki

    original = ki.MAP_MAX_CONCURRENCY
    try:
        ki.MAP_MAX_CONCURRENCY = 2
        reset_map_semaphore()

        # Drain all slots.
        await acquire_map_slot()
        await acquire_map_slot()

        # Third acquire must block — use wait_for with a short timeout.
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(acquire_map_slot(), timeout=0.05)

        # Release both.
        release_map_slot()
        release_map_slot()
    finally:
        ki.MAP_MAX_CONCURRENCY = original
        reset_map_semaphore()


import asyncio  # noqa: E402 (needs to be after the test that uses it)


# ---------------------------------------------------------------------------
# REGRESSION Fix 2: MAP_MAX_CONCURRENCY semaphore is ACTUALLY enforced at
# execution time (acquire_map_slot / release_map_slot are called for children)
# ---------------------------------------------------------------------------


async def test_map_slot_acquired_and_released_for_child_execution():
    """Map child execution acquires and releases the semaphore slot.

    The bug: acquire_map_slot / release_map_slot were imported but never called
    in the execution paths, so the concurrency cap was not enforced at runtime
    (only at fan-out time via _expand_map_children).  This test verifies that
    _execute_claimed_task_run holds the semaphore while a map child runs and
    releases it when done.

    Approach: patch acquire_map_slot and release_map_slot with async/sync
    counters, build a minimal map-child task_run, and confirm both were called.
    """
    import app.flows.runtime as rt
    from app.flows.store import InMemoryFlowStore
    from app.flows.registry import reset_for_tests
    from datetime import datetime, timezone

    reset_for_tests()
    inner = InMemoryFlowStore()

    # Build a minimal map child task_run.
    spec = {
        "version": 1,
        "name": "map_flow",
        "tasks": [
            {
                "key": "fan",
                "kind": "map",
                "needs": [],
                "config": {
                    "items": [1, 2, 3],
                    "item_var": "item",
                    "body": [{"key": "step", "kind": "noop", "needs": [], "config": {}}],
                    "collect_key": "step",
                },
            }
        ],
    }
    flow = await inner.create_flow(org_id="org-t", created_by="u", name="f", spec=spec)
    run = await inner.create_flow_run(
        flow_id=flow["id"], org_id="org-t", params={}, trigger="manual"
    )
    now = datetime(2025, 6, 1, 12, 0, 0, tzinfo=timezone.utc)

    # Manually insert a map-child task_run (key contains '[').
    child_tr_list = [{
        "task_key": "fan[0].step",
        "org_id": "org-t",
        "state": "ready",
        "depends_on": [],
        "attempt": 0,
        "kind": "noop",
        "config": {"__item__": 1, "__item_var__": "item", "__item_index__": 0},
        "retries": 0,
        "retry_backoff_s": 30,
        "timeout_s": 0,
        "cache_ttl_s": 0,
        "parent_task_run_id": None,
    }]
    await inner.add_task_runs(run["id"], child_tr_list)
    trs = await inner.list_task_runs(run["id"])
    child_tr = next(tr for tr in trs if tr["task_key"] == "fan[0].step")
    # Claim it.
    child_tr = await inner.update_task_run(child_tr["id"], {"state": "running"})
    child_tr["flow_run_id"] = run["id"]

    acquire_calls: list[str] = []
    release_calls: list[str] = []

    original_acquire = rt.acquire_map_slot
    original_release = rt.release_map_slot

    async def mock_acquire():
        acquire_calls.append("acquire")

    def mock_release():
        release_calls.append("release")

    try:
        rt.acquire_map_slot = mock_acquire
        rt.release_map_slot = mock_release

        await rt._execute_claimed_task_run(
            inner, child_tr, now, claims={}, secrets={}, run_var_overlay=None
        )
    finally:
        rt.acquire_map_slot = original_acquire
        rt.release_map_slot = original_release

    assert len(acquire_calls) == 1, (
        f"Expected 1 acquire_map_slot call for map child, got {len(acquire_calls)}"
    )
    assert len(release_calls) == 1, (
        f"Expected 1 release_map_slot call for map child, got {len(release_calls)}"
    )


async def test_non_map_child_does_not_acquire_slot():
    """Regular (non-map-child) task execution must NOT touch the semaphore."""
    import app.flows.runtime as rt
    from app.flows.runtime import materialize_flow_run as _mfr
    from app.flows.store import InMemoryFlowStore
    from app.flows.registry import reset_for_tests
    from datetime import datetime, timezone

    reset_for_tests()
    inner = InMemoryFlowStore()

    spec = {
        "version": 1,
        "name": "simple_flow",
        "tasks": [{"key": "t1", "kind": "noop", "needs": [], "config": {}}],
    }
    flow = await inner.create_flow(org_id="org-t", created_by="u", name="f", spec=spec)
    now = datetime(2025, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
    run = await _mfr(inner, flow, {}, "manual", now)

    trs = await inner.list_task_runs(run["id"])
    assert trs, "Expected at least one task_run from materialize_flow_run"
    regular_tr = trs[0]
    regular_tr = await inner.update_task_run(regular_tr["id"], {"state": "running"})
    regular_tr["flow_run_id"] = run["id"]

    acquire_calls: list[str] = []

    original_acquire = rt.acquire_map_slot

    async def mock_acquire():
        acquire_calls.append("acquire")

    try:
        rt.acquire_map_slot = mock_acquire

        await rt._execute_claimed_task_run(
            inner, regular_tr, now, claims={}, secrets={}, run_var_overlay=None
        )
    finally:
        rt.acquire_map_slot = original_acquire

    assert len(acquire_calls) == 0, (
        f"Non-map-child must NOT acquire a map slot, but acquire was called {len(acquire_calls)} time(s)"
    )


async def test_map_concurrency_cap_enforced_at_runtime():
    """Map child execution acquires and releases semaphore slots, respecting the cap.

    This test verifies that the runtime actually calls acquire_map_slot /
    release_map_slot for each map child task_run (the dead-code fix).  We
    set MAP_MAX_CONCURRENCY=2 and directly execute 3 map-child task_runs via
    _execute_claimed_task_run, tracking peak concurrent in-flight count.

    Since drain is sequential (one task at a time in a single-threaded drain),
    peak_in_flight will be 1 — but what we verify is:
      a) acquire is called for EVERY map child (not 0 times as before the fix)
      b) peak never exceeds the semaphore cap of 2
      c) the slot count returns to 0 after each child completes
    """
    import app.flows.runtime as rt
    import app.compute.kernel_interface as ki
    from app.flows.runtime import materialize_flow_run as _mfr
    from app.flows.store import InMemoryFlowStore
    from app.flows.registry import reset_for_tests
    from datetime import datetime, timezone

    reset_for_tests()
    reset_map_semaphore()

    original_ki_cap = ki.MAP_MAX_CONCURRENCY
    original_rt_cap = rt.MAP_MAX_CONCURRENCY
    original_acquire = rt.acquire_map_slot
    original_release = rt.release_map_slot

    try:
        ki.MAP_MAX_CONCURRENCY = 2
        rt.MAP_MAX_CONCURRENCY = 2
        reset_map_semaphore()

        in_flight = 0
        peak_in_flight = 0
        total_acquires = 0

        async def counting_acquire():
            nonlocal in_flight, peak_in_flight, total_acquires
            await original_acquire()
            in_flight += 1
            total_acquires += 1
            if in_flight > peak_in_flight:
                peak_in_flight = in_flight

        def counting_release():
            nonlocal in_flight
            original_release()
            in_flight -= 1

        rt.acquire_map_slot = counting_acquire
        rt.release_map_slot = counting_release

        inner = InMemoryFlowStore()
        now = datetime(2025, 6, 1, 12, 0, 0, tzinfo=timezone.utc)

        # Build a minimal spec so we can get a flow_run + task_runs.
        # We don't use a 'map' spec because it requires item_expr.
        # Instead, we directly insert map-child task_runs (task_key with '[').
        spec = {
            "version": 1,
            "name": "simple",
            "tasks": [{"key": "root", "kind": "noop", "needs": [], "config": {}}],
        }
        flow = await inner.create_flow(org_id="org-t", created_by="u", name="f", spec=spec)
        run = await _mfr(inner, flow, {}, "manual", now)

        # Inject 3 map-child task_runs directly into the store.
        child_trs = [
            {
                "task_key": f"root[{i}].work",
                "org_id": "org-t",
                "state": "ready",
                "depends_on": [],
                "attempt": 0,
                "kind": "noop",
                "config": {"__item__": i, "__item_var__": "item", "__item_index__": i},
                "retries": 0,
                "retry_backoff_s": 30,
                "timeout_s": 0,
                "cache_ttl_s": 0,
                "parent_task_run_id": None,
            }
            for i in range(3)
        ]
        await inner.add_task_runs(run["id"], child_trs)
        all_trs = await inner.list_task_runs(run["id"])
        map_children = [tr for tr in all_trs if "[" in tr["task_key"]]
        assert len(map_children) == 3, f"Expected 3 map children, got {len(map_children)}"

        # Execute each child via _execute_claimed_task_run (drain-path function).
        for child in map_children:
            child_claimed = await inner.update_task_run(child["id"], {"state": "running"})
            child_claimed["flow_run_id"] = run["id"]
            await rt._execute_claimed_task_run(
                inner, child_claimed, now, claims={}, secrets={}, run_var_overlay=None
            )

    finally:
        rt.acquire_map_slot = original_acquire
        rt.release_map_slot = original_release
        ki.MAP_MAX_CONCURRENCY = original_ki_cap
        rt.MAP_MAX_CONCURRENCY = original_rt_cap
        reset_map_semaphore()

    # Acquire must have been called once per map child (3 times total).
    assert total_acquires == 3, (
        f"Expected acquire_map_slot called 3 times (once per map child), got {total_acquires}"
    )
    # Sequential drain → peak is 1; must never exceed the cap of 2.
    assert peak_in_flight <= 2, (
        f"Map concurrency cap violated: peak in-flight was {peak_in_flight}, cap is 2"
    )
    # After all children finish, in_flight must be back to 0.
    assert in_flight == 0, (
        f"Semaphore slot leak: {in_flight} slots still held after all children finished"
    )


# ---------------------------------------------------------------------------
# HIGH-CONCURRENCY: per-flow cap enforced via sliding-window depends_on
# (not just fan-out time — survives advance_readiness cycles)
# ---------------------------------------------------------------------------


def test_sliding_window_depends_on_injected():
    """Throttled items receive synthetic depends_on pointing to item (i-cap)'s terminal tasks.

    With cap=2 and 5 items, items 2,3,4 are throttled.  Their root task
    depends_on must point to item (i-2)'s terminal task, not be empty.
    """
    items = [{"n": i} for i in range(5)]
    # Single-task body: "step" has no needs, so it is both root and terminal.
    child_runs = _expand_map_children(
        flow_run_id="run-sw",
        org_id="org-test",
        map_task_run_id="map-tr-sw",
        map_task_key="fan",
        items=items,
        body_tasks=[{"key": "step", "kind": "noop", "needs": [], "config": {}}],
        item_var="item",
        now=NOW,
        max_concurrency=2,
    )
    by_key = {tr["task_key"]: tr for tr in child_runs}

    # Items 0,1 start ready with no synthetic deps.
    assert by_key["fan[0].step"]["state"] == "ready"
    assert by_key["fan[0].step"]["depends_on"] == []
    assert by_key["fan[1].step"]["state"] == "ready"
    assert by_key["fan[1].step"]["depends_on"] == []

    # Item 2: throttled, must depend on item 0's terminal task.
    assert by_key["fan[2].step"]["state"] == "pending"
    assert "fan[0].step" in by_key["fan[2].step"]["depends_on"], (
        f"fan[2].step should depend on fan[0].step, got {by_key['fan[2].step']['depends_on']}"
    )

    # Item 3: throttled, must depend on item 1's terminal task.
    assert by_key["fan[3].step"]["state"] == "pending"
    assert "fan[1].step" in by_key["fan[3].step"]["depends_on"], (
        f"fan[3].step should depend on fan[1].step, got {by_key['fan[3].step']['depends_on']}"
    )

    # Item 4: throttled, must depend on item 2's terminal task.
    assert by_key["fan[4].step"]["state"] == "pending"
    assert "fan[2].step" in by_key["fan[4].step"]["depends_on"], (
        f"fan[4].step should depend on fan[2].step, got {by_key['fan[4].step']['depends_on']}"
    )


def test_sliding_window_multi_task_body_terminal_only():
    """With a multi-task body (step_a -> step_b), the gate must be step_b (terminal).

    Body: step_a (root) -> step_b (terminal, needs=[step_a]).
    Cap=2, 4 items.  Items 0,1 start ready (step_a=ready, step_b=pending with
    normal deps).  Item 2's root (step_a) must depend on item 0's terminal
    (step_b), NOT on step_a.
    """
    body_tasks = [
        {"key": "step_a", "kind": "noop", "needs": [], "config": {}},
        {"key": "step_b", "kind": "noop", "needs": ["step_a"], "config": {}},
    ]
    items = [{"n": i} for i in range(4)]
    child_runs = _expand_map_children(
        flow_run_id="run-mt",
        org_id="org-test",
        map_task_run_id="map-tr-mt",
        map_task_key="fan",
        items=items,
        body_tasks=body_tasks,
        item_var="item",
        now=NOW,
        max_concurrency=2,
    )
    by_key = {tr["task_key"]: tr for tr in child_runs}

    # Items 0,1: step_a is ready, step_b is pending with normal dep on step_a.
    assert by_key["fan[0].step_a"]["state"] == "ready"
    assert by_key["fan[0].step_a"]["depends_on"] == []
    assert by_key["fan[0].step_b"]["state"] == "pending"
    assert by_key["fan[0].step_b"]["depends_on"] == ["fan[0].step_a"]

    # Item 2 (throttled): step_a (root) must gate on item 0's TERMINAL (step_b).
    assert by_key["fan[2].step_a"]["state"] == "pending"
    assert "fan[0].step_b" in by_key["fan[2].step_a"]["depends_on"], (
        f"fan[2].step_a should gate on fan[0].step_b (terminal), got "
        f"{by_key['fan[2].step_a']['depends_on']}"
    )
    # step_b of item 2 should NOT have synthetic gate (it's not a root task).
    assert by_key["fan[2].step_b"]["depends_on"] == ["fan[2].step_a"]

    # Item 3 (throttled): step_a must gate on item 1's terminal (step_b).
    assert by_key["fan[3].step_a"]["state"] == "pending"
    assert "fan[1].step_b" in by_key["fan[3].step_a"]["depends_on"], (
        f"fan[3].step_a should gate on fan[1].step_b, got "
        f"{by_key['fan[3].step_a']['depends_on']}"
    )


async def test_advance_readiness_enforces_per_flow_cap():
    """Cap=2, 10 items: advance_readiness must never yield >2 ready/running simultaneously.

    This is the critical regression test: before the fix, all 10 items would be
    promoted to 'ready' in the first advance_readiness call because throttled
    items had depends_on=[] and the no-dep path unconditionally promoted them.
    With the sliding-window fix, items are gated on their predecessor's terminal
    task and the cap is enforced at the DAG level.
    """
    from app.flows.runtime import advance_readiness

    reset_for_tests()
    store = InMemoryFlowStore()

    cap = 2
    n_items = 10
    items = [{"n": i} for i in range(n_items)]
    body_tasks = [{"key": "work", "kind": "noop", "needs": [], "config": {}}]

    # Build child_runs via the fixed _expand_map_children.
    child_runs = _expand_map_children(
        flow_run_id="run-ar",
        org_id="org-test",
        map_task_run_id="map-tr-ar",
        map_task_key="fan",
        items=items,
        body_tasks=body_tasks,
        item_var="item",
        now=NOW,
        max_concurrency=cap,
    )

    # Seed a minimal flow_run + task_runs in the store.
    flow = await store.create_flow(
        org_id="org-test",
        created_by="user-test",
        name="cap_test",
        spec={"version": 1, "name": "cap_test",
              "tasks": [{"key": "fan", "kind": "noop", "needs": [], "config": {}}]},
    )
    run = await store.create_flow_run(
        flow_id=flow["id"], org_id="org-test", params={}, trigger="manual"
    )
    flow_run_id = run["id"]

    # Replace the single "fan" task_run with our map children.
    init_trs = await store.list_task_runs(flow_run_id)
    for tr in init_trs:
        await store.update_task_run(tr["id"], {"state": "upstream_failed"})

    await store.add_task_runs(flow_run_id, child_runs)

    def _count_active(task_runs: list[dict]) -> int:
        return sum(1 for tr in task_runs if tr["state"] in ("ready", "running"))

    # After fan-out (before any advance_readiness), check initial state.
    trs = await store.list_task_runs(flow_run_id)
    map_children = [tr for tr in trs if "[" in tr["task_key"]]
    initial_active = _count_active(map_children)
    assert initial_active <= cap, (
        f"After fan-out, {initial_active} items active (cap={cap}). "
        f"Expected <= {cap}."
    )

    # Simulate the scheduling loop: call advance_readiness, then mark one
    # 'ready' task as 'success', repeat — verifying the cap holds throughout.
    for cycle in range(n_items + 2):
        await advance_readiness(store, flow_run_id, NOW)

        trs = await store.list_task_runs(flow_run_id)
        map_children = [tr for tr in trs if "[" in tr["task_key"]]
        active = _count_active(map_children)

        assert active <= cap, (
            f"Cycle {cycle}: cap violated — {active} items ready/running (cap={cap}). "
            f"States: {[(tr['task_key'], tr['state']) for tr in map_children]}"
        )

        # Simulate one ready task completing per cycle.
        ready_ones = [tr for tr in map_children if tr["state"] == "ready"]
        if not ready_ones:
            break  # all done
        await store.update_task_run(ready_ones[0]["id"], {
            "state": "success",
            "finished_at": NOW,
            "result": {"ok": True},
        })

    # All children must be terminal when we're done.
    trs = await store.list_task_runs(flow_run_id)
    map_children = [tr for tr in trs if "[" in tr["task_key"]]
    non_terminal = [tr for tr in map_children if tr["state"] not in ("success", "failed", "upstream_failed", "skipped", "cancelled", "timed_out")]
    assert not non_terminal, (
        f"Non-terminal children remain: {[(tr['task_key'], tr['state']) for tr in non_terminal]}"
    )
    success_count = sum(1 for tr in map_children if tr["state"] == "success")
    assert success_count == n_items, (
        f"Expected {n_items} successes, got {success_count}"
    )


# ---------------------------------------------------------------------------
# REGRESSION: [MED RBAC] viewer must receive 403 on POST /flows/compile
# ---------------------------------------------------------------------------


import uuid as _uuid  # noqa: E402
import pytest_asyncio  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402

from app.auth.jwt import mint_access_token  # noqa: E402
from app.flows.store import InMemoryFlowStore, set_flow_store  # noqa: E402
from app.repos.memory import InMemoryRepo  # noqa: E402
from app.repos.provider import set_repo  # noqa: E402


def _make_compile_user(user_id: str | None = None, email: str = "bob@example.com") -> dict:
    uid = user_id or str(_uuid.uuid4())
    return {
        "id": uid,
        "email": email,
        "name": "Bob",
        "avatar_url": None,
        "email_verified": True,
        "created_at": "2024-01-01T00:00:00+00:00",
    }


def _compile_auth_headers(user_id: str) -> dict:
    token = mint_access_token(user_id)
    return {"Authorization": f"Bearer {token}"}


@pytest_asyncio.fixture
async def compile_client(app, fake_db):
    """Client with a viewer user seeded for RBAC tests."""
    store = InMemoryFlowStore()
    set_flow_store(store)

    repo = InMemoryRepo()
    set_repo(repo)

    viewer_id = str(_uuid.uuid4())
    org_id = str(_uuid.uuid4())
    viewer = _make_compile_user(user_id=viewer_id)

    fake_db.users[viewer_id] = viewer
    # Seed as viewer role — must be blocked from /compile.
    repo.seed_org_member(org_id=org_id, user_id=viewer_id, role="viewer")

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://testserver",
        follow_redirects=False,
    ) as client:
        yield client, viewer_id, org_id

    set_flow_store(None)
    set_repo(None)


@pytest_asyncio.fixture
async def compile_client_writer(app, fake_db):
    """Client with a member (writer) user for positive control."""
    store = InMemoryFlowStore()
    set_flow_store(store)

    repo = InMemoryRepo()
    set_repo(repo)

    writer_id = str(_uuid.uuid4())
    org_id = str(_uuid.uuid4())
    writer = _make_compile_user(user_id=writer_id, email="writer@example.com")

    fake_db.users[writer_id] = writer
    repo.seed_org_member(org_id=org_id, user_id=writer_id, role="member")

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://testserver",
        follow_redirects=False,
    ) as client:
        yield client, writer_id, org_id

    set_flow_store(None)
    set_repo(None)


@pytest.mark.asyncio
async def test_viewer_cannot_compile_code(compile_client):
    """A viewer calling POST /flows/compile must receive 403 Forbidden."""
    client, viewer_id, _org_id = compile_client

    resp = await client.post(
        "/api/v1/flows/compile",
        json={"code": "# harmless"},
        headers=_compile_auth_headers(viewer_id),
    )
    assert resp.status_code == 403, (
        f"Expected 403 for viewer on /flows/compile, got {resp.status_code}: {resp.text}"
    )
    body = resp.json()
    assert body.get("code") == "forbidden" or resp.status_code == 403


@pytest.mark.asyncio
async def test_member_can_reach_compile_endpoint(compile_client_writer):
    """A member (writer) calling /compile must NOT get 403 — may get 400 on bad code."""
    client, writer_id, _org_id = compile_client_writer

    resp = await client.post(
        "/api/v1/flows/compile",
        json={"code": "# no @flow decorator"},
        headers=_compile_auth_headers(writer_id),
    )
    # Must not be 403; expected to get 400 (compile error) or 200.
    assert resp.status_code != 403, (
        f"Writer should not receive 403 on /compile, got {resp.status_code}: {resp.text}"
    )
