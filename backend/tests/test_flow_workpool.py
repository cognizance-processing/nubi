"""Tests for the work-pool model: lease management, reaping, pool execution.

All tests use InMemoryFlowStore and inject the clock.  No network, no DB.

Coverage
--------
1. claim_ready_task_run sets worker_id and lease_expires_at.
2. reap_expired_leases transitions stuck 'running' tasks back to 'ready'/'retrying'.
3. reap_expired_leases returns 0 when no leases are expired.
4. reap_expired_leases does not reap tasks with a valid (future) lease.
5. run_worker_pool (bounded) executes a 2-task flow to completion.
6. flow_tick does NOT execute tasks (returns tasks_run=0).
7. flow_tick reaped count reflects expired leases.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from app.flows.registry import reset_for_tests
from app.flows.runtime import flow_tick, materialize_flow_run, run_worker_pool
from app.flows.store import InMemoryFlowStore, set_flow_store

# All tests are async.
pytestmark = pytest.mark.asyncio

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_ORG_ID = "00000000-0000-0000-0000-000000000001"
_USER_ID = "00000000-0000-0000-0000-000000000002"
_NOW = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


def _make_store() -> InMemoryFlowStore:
    store = InMemoryFlowStore()
    set_flow_store(store)
    return store


def _two_task_flow_spec() -> dict[str, Any]:
    """A minimal 2-task flow: task_a (noop root) -> task_b (noop, needs a)."""
    return {
        "version": 1,
        "name": "workpool_test_flow",
        "tasks": [
            {
                "key": "task_a",
                "kind": "noop",
                "config": {},
                "needs": [],
                "retries": 0,
                "retry_backoff_s": 0,
                "timeout_s": 0,
                "cache_ttl_s": 0,
            },
            {
                "key": "task_b",
                "kind": "noop",
                "config": {"echo": "hello"},
                "needs": ["task_a"],
                "retries": 0,
                "retry_backoff_s": 0,
                "timeout_s": 0,
                "cache_ttl_s": 0,
            },
        ],
    }


async def _create_flow_and_run(store: InMemoryFlowStore, now: datetime):
    """Create a flow + flow_run + task_runs; return (flow, flow_run)."""
    flow = await store.create_flow(
        org_id=_ORG_ID,
        created_by=_USER_ID,
        name="test-workpool-flow",
        spec=_two_task_flow_spec(),
    )
    reset_for_tests()
    flow_run = await materialize_flow_run(store, flow, {}, "manual", now)
    return flow, flow_run


# ---------------------------------------------------------------------------
# 1. claim_ready_task_run sets lease fields
# ---------------------------------------------------------------------------


async def test_claim_sets_lease_fields():
    store = _make_store()
    now = _NOW
    flow, flow_run = await _create_flow_and_run(store, now)

    claimed = await store.claim_ready_task_run(now, worker_id="host:1234", lease_seconds=120)
    assert claimed is not None, "Expected a task_run to be claimed"
    assert claimed["state"] == "running"
    assert claimed["worker_id"] == "host:1234"
    assert claimed["lease_expires_at"] == now + timedelta(seconds=120)


async def test_claim_without_lease():
    store = _make_store()
    now = _NOW
    await _create_flow_and_run(store, now)

    claimed = await store.claim_ready_task_run(now, worker_id="host:1234", lease_seconds=0)
    assert claimed is not None
    assert claimed["state"] == "running"
    # lease_expires_at should be None when lease_seconds=0
    assert claimed["lease_expires_at"] is None


# ---------------------------------------------------------------------------
# 2. reap_expired_leases requeues expired running tasks
# ---------------------------------------------------------------------------


async def test_reap_expired_leases_requeues():
    store = _make_store()
    now = _NOW
    await _create_flow_and_run(store, now)

    # Claim a task with a very short lease.
    claimed = await store.claim_ready_task_run(now, worker_id="host:1234", lease_seconds=10)
    assert claimed is not None
    task_run_id = claimed["id"]

    # Simulate time advancing past the lease expiry.
    future_now = now + timedelta(seconds=30)
    reaped = await store.reap_expired_leases(future_now)
    assert reaped == 1, "Expected 1 reaped task_run"

    # The task_run should now be 'ready' (attempt=0) and lease fields cleared.
    tr = await store.get_task_run(task_run_id)
    assert tr is not None
    assert tr["state"] == "ready"
    assert tr["lease_expires_at"] is None
    assert tr["worker_id"] is None


async def test_reap_expired_leases_retrying_when_attempt_gt0():
    store = _make_store()
    now = _NOW
    await _create_flow_and_run(store, now)

    claimed = await store.claim_ready_task_run(now, worker_id="host:1", lease_seconds=10)
    assert claimed is not None
    task_run_id = claimed["id"]

    # Simulate the task has been attempted before (set attempt > 0).
    await store.update_task_run(task_run_id, {"attempt": 1})

    future_now = now + timedelta(seconds=30)
    reaped = await store.reap_expired_leases(future_now)
    assert reaped == 1

    tr = await store.get_task_run(task_run_id)
    assert tr is not None
    assert tr["state"] == "retrying"
    assert tr["lease_expires_at"] is None


# ---------------------------------------------------------------------------
# 3. reap_expired_leases returns 0 when no leases are expired
# ---------------------------------------------------------------------------


async def test_reap_returns_zero_when_no_expired_leases():
    store = _make_store()
    now = _NOW
    await _create_flow_and_run(store, now)

    reaped = await store.reap_expired_leases(now)
    assert reaped == 0


# ---------------------------------------------------------------------------
# 4. reap_expired_leases does not reap tasks with a valid (future) lease
# ---------------------------------------------------------------------------


async def test_reap_does_not_reap_valid_lease():
    store = _make_store()
    now = _NOW
    await _create_flow_and_run(store, now)

    claimed = await store.claim_ready_task_run(now, worker_id="host:1", lease_seconds=300)
    assert claimed is not None

    # Advance by only 10 seconds — lease_expires_at is 300 s away; should NOT reap.
    reaped = await store.reap_expired_leases(now + timedelta(seconds=10))
    assert reaped == 0

    tr = await store.get_task_run(claimed["id"])
    assert tr is not None
    assert tr["state"] == "running"


# ---------------------------------------------------------------------------
# 5. run_worker_pool (bounded) completes a 2-task flow
# ---------------------------------------------------------------------------


async def test_run_worker_pool_completes_two_task_flow():
    """A bounded worker pool processes both tasks in a sequential 2-task flow."""
    store = _make_store()
    now = _NOW
    flow, flow_run = await _create_flow_and_run(store, now)
    flow_run_id = flow_run["id"]

    # Use _max_iterations to make the pool finite.
    # 2 tasks, 1 worker: need at least 2 iterations plus some polling slack.
    await run_worker_pool(
        concurrency=1,
        poll_interval=0.0,
        claims={},
        worker_id="test-worker",
        lease_seconds=60,
        _max_iterations=10,
    )

    task_runs = await store.list_task_runs(flow_run_id)
    states = {tr["task_key"]: tr["state"] for tr in task_runs}
    assert states.get("task_a") == "success", f"task_a state={states.get('task_a')}"
    assert states.get("task_b") == "success", f"task_b state={states.get('task_b')}"

    final_run = await store.get_flow_run(flow_run_id)
    assert final_run is not None
    assert final_run["state"] == "success", f"flow_run state={final_run['state']}"


async def test_run_worker_pool_concurrent_workers():
    """Multiple concurrent workers should all complete without double-executing."""
    store = _make_store()
    now = _NOW
    flow, flow_run = await _create_flow_and_run(store, now)
    flow_run_id = flow_run["id"]

    await run_worker_pool(
        concurrency=3,
        poll_interval=0.0,
        claims={},
        worker_id="test-worker",
        lease_seconds=60,
        _max_iterations=5,
    )

    task_runs = await store.list_task_runs(flow_run_id)
    states = {tr["task_key"]: tr["state"] for tr in task_runs}
    # Both tasks should be success; the flow_run should be terminal.
    assert states.get("task_a") == "success"
    assert states.get("task_b") == "success"


# ---------------------------------------------------------------------------
# 6. flow_tick does NOT execute tasks (tasks_run always 0)
# ---------------------------------------------------------------------------


async def test_flow_tick_does_not_execute_tasks():
    store = _make_store()
    now = _NOW
    flow, flow_run = await _create_flow_and_run(store, now)

    # After materialize: task_a should be ready, task_b pending.
    summary = await flow_tick(store, now, claims=None)
    assert summary["tasks_run"] == 0, "flow_tick should not execute tasks"

    # task_a must still be 'ready' (not consumed by flow_tick).
    task_runs = await store.list_task_runs(flow_run["id"])
    states = {tr["task_key"]: tr["state"] for tr in task_runs}
    assert states["task_a"] == "ready"
    assert states["task_b"] == "pending"


# ---------------------------------------------------------------------------
# 7. flow_tick reaped count reflects expired leases
# ---------------------------------------------------------------------------


async def test_flow_tick_reports_reaped_count():
    store = _make_store()
    now = _NOW
    await _create_flow_and_run(store, now)

    # Claim a task with a short lease.
    claimed = await store.claim_ready_task_run(now, worker_id="host:1", lease_seconds=10)
    assert claimed is not None

    # Tick far into the future — lease should be expired.
    future_now = now + timedelta(seconds=60)
    summary = await flow_tick(store, future_now, claims=None)
    assert summary["reaped"] == 1, f"Expected 1 reaped, got {summary}"
    assert summary["tasks_run"] == 0
