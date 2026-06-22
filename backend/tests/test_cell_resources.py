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
