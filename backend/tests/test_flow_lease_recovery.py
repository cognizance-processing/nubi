"""Tests for lease safety: crash recovery, heartbeat, timeout-derived leases.

All tests use InMemoryFlowStore and inject the clock.  No network, no DB.
The only real-time waits are heartbeat beats with a 10 ms interval, bounded
well under a second.

Coverage
--------
1. Crash recovery end-to-end: a claimed task whose worker dies is reaped
   after lease expiry, re-claimed by another worker, and completes; the
   flow_run finishes successfully without consuming retry attempts.
2. An active heartbeat keeps extending the lease so the reaper never
   re-queues a long-running task.
3. extend_task_lease is conditional on worker_id ownership (and 'running'
   state) — a stolen lease cannot be extended by the original worker.
4. The heartbeat loop stops on its own once the lease is lost.
5. Timeout-derived lease: claiming a task with timeout_s=3600 yields
   lease_expires_at >= now + 3600; timeout_s=0 keeps the default lease.
6. Sequential claims for a single ready task: the second claim gets None.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from app.flows.registry import reset_for_tests
from app.flows.runtime import (
    _LEASE_TIMEOUT_GRACE_S,
    _extend_lease_for_timeout,
    _heartbeat_task_lease,
    flow_tick,
    materialize_flow_run,
    run_one_ready_task,
)
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


def _two_task_flow_spec(timeout_s: int = 0) -> dict[str, Any]:
    """A minimal 2-task flow: task_a (noop root) -> task_b (noop, needs a)."""
    return {
        "version": 1,
        "name": "lease_recovery_test_flow",
        "tasks": [
            {
                "key": "task_a",
                "kind": "noop",
                "config": {},
                "needs": [],
                "retries": 0,
                "retry_backoff_s": 0,
                "timeout_s": timeout_s,
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


async def _create_flow_and_run(
    store: InMemoryFlowStore, now: datetime, timeout_s: int = 0
):
    """Create a flow + flow_run + task_runs; return (flow, flow_run)."""
    flow = await store.create_flow(
        org_id=_ORG_ID,
        created_by=_USER_ID,
        name="test-lease-recovery-flow",
        spec=_two_task_flow_spec(timeout_s=timeout_s),
    )
    reset_for_tests()
    flow_run = await materialize_flow_run(store, flow, {}, "manual", now)
    return flow, flow_run


# ---------------------------------------------------------------------------
# 1. Crash recovery end-to-end
# ---------------------------------------------------------------------------


async def test_crash_recovery_after_lease_expiry():
    """Worker A claims task_a and dies; the reaper re-queues it, worker B
    re-claims it, and the flow completes successfully."""
    store = _make_store()
    now = _NOW
    flow, flow_run = await _create_flow_and_run(store, now)
    flow_run_id = flow_run["id"]

    # Worker A claims task_a, then "dies" (never completes, never heartbeats).
    claimed = await store.claim_ready_task_run(now, worker_id="worker-a", lease_seconds=60)
    assert claimed is not None
    assert claimed["task_key"] == "task_a"
    assert claimed["state"] == "running"

    # Advance the injected clock past the lease; the scheduler tick reaps it.
    t1 = now + timedelta(seconds=120)
    summary = await flow_tick(store, t1, claims=None)
    assert summary["reaped"] == 1, f"Expected 1 reaped, got {summary}"

    tr = await store.get_task_run(claimed["id"])
    assert tr is not None
    assert tr["state"] == "ready", "Reaped task must be re-queued as 'ready'"
    assert tr["worker_id"] is None
    assert tr["lease_expires_at"] is None

    # Worker B picks up the re-queued work and drains the flow to completion.
    for _ in range(5):
        result = await run_one_ready_task(
            store, t1, claims={}, worker_id="worker-b", lease_seconds=60
        )
        if result is None:
            break

    task_runs = await store.list_task_runs(flow_run_id)
    states = {r["task_key"]: r["state"] for r in task_runs}
    assert states == {"task_a": "success", "task_b": "success"}

    # Reaping does NOT consume retry attempts — both ran at attempt 0.
    attempts = {r["task_key"]: r["attempt"] for r in task_runs}
    assert attempts == {"task_a": 0, "task_b": 0}

    final_run = await store.get_flow_run(flow_run_id)
    assert final_run is not None
    assert final_run["state"] == "success", f"flow_run state={final_run['state']}"


# ---------------------------------------------------------------------------
# 2. Heartbeat prevents reaping
# ---------------------------------------------------------------------------


async def test_heartbeat_prevents_reaping():
    """A long-running task with an active heartbeat is NOT re-queued by the
    reaper, even after the original claim lease would have expired."""
    store = _make_store()
    now = _NOW
    await _create_flow_and_run(store, now)

    claimed = await store.claim_ready_task_run(now, worker_id="worker-a", lease_seconds=300)
    assert claimed is not None
    original_expiry = claimed["lease_expires_at"]
    assert original_expiry == now + timedelta(seconds=300)

    # Simulate a long-running execution: heartbeat with a tiny interval.
    heartbeat = asyncio.create_task(
        _heartbeat_task_lease(
            store, claimed["id"], "worker-a", lease_seconds=300, interval_s=0.01
        )
    )
    try:
        # Wait (bounded, well under a second) for at least one beat to land.
        for _ in range(50):
            await asyncio.sleep(0.01)
            tr = await store.get_task_run(claimed["id"])
            assert tr is not None
            if tr["lease_expires_at"] != original_expiry:
                break
        else:
            pytest.fail("Heartbeat never extended the lease")
    finally:
        heartbeat.cancel()
        try:
            await heartbeat
        except asyncio.CancelledError:
            pass

    # Reaping at a time past the ORIGINAL lease expiry must be a no-op — the
    # heartbeat pushed lease_expires_at out beyond it.
    reaped = await store.reap_expired_leases(original_expiry + timedelta(seconds=1))
    assert reaped == 0, "Heartbeated task must not be reaped"

    tr = await store.get_task_run(claimed["id"])
    assert tr is not None
    assert tr["state"] == "running"
    assert tr["worker_id"] == "worker-a"


# ---------------------------------------------------------------------------
# 3. extend_task_lease ownership semantics
# ---------------------------------------------------------------------------


async def test_extend_task_lease_worker_mismatch_returns_false():
    """extend_task_lease must refuse to extend when worker_id does not match
    (lease stolen) and must not modify the row."""
    store = _make_store()
    now = _NOW
    await _create_flow_and_run(store, now)

    claimed = await store.claim_ready_task_run(now, worker_id="worker-a", lease_seconds=300)
    assert claimed is not None
    new_expiry = now + timedelta(seconds=9999)

    # Wrong worker — no extension.
    ok = await store.extend_task_lease(claimed["id"], "worker-b", new_expiry)
    assert ok is False
    tr = await store.get_task_run(claimed["id"])
    assert tr is not None
    assert tr["lease_expires_at"] == claimed["lease_expires_at"], "Lease must be unchanged"

    # Matching worker — extension applies.
    ok = await store.extend_task_lease(claimed["id"], "worker-a", new_expiry)
    assert ok is True
    tr = await store.get_task_run(claimed["id"])
    assert tr is not None
    assert tr["lease_expires_at"] == new_expiry


async def test_extend_task_lease_rejects_missing_or_non_running():
    """extend_task_lease returns False for unknown ids and terminal rows."""
    store = _make_store()
    now = _NOW
    await _create_flow_and_run(store, now)

    new_expiry = now + timedelta(seconds=600)

    # Unknown task_run id.
    ok = await store.extend_task_lease("not-a-real-id", "worker-a", new_expiry)
    assert ok is False

    # Claimed then completed — no longer 'running'.
    claimed = await store.claim_ready_task_run(now, worker_id="worker-a", lease_seconds=300)
    assert claimed is not None
    await store.update_task_run(claimed["id"], {"state": "success", "finished_at": now})
    ok = await store.extend_task_lease(claimed["id"], "worker-a", new_expiry)
    assert ok is False


# ---------------------------------------------------------------------------
# 4. Heartbeat stops once the lease is lost
# ---------------------------------------------------------------------------


async def test_heartbeat_stops_when_lease_is_lost():
    """The heartbeat loop returns on its own when extend_task_lease reports
    the lease is no longer owned (reaped + re-claimed by another worker)."""
    store = _make_store()
    now = _NOW
    await _create_flow_and_run(store, now)

    claimed = await store.claim_ready_task_run(now, worker_id="worker-a", lease_seconds=300)
    assert claimed is not None

    # Heartbeat under a worker_id that does NOT own the lease — the first
    # beat fails and the loop must exit (rather than spin forever).
    heartbeat = asyncio.create_task(
        _heartbeat_task_lease(
            store, claimed["id"], "worker-b", lease_seconds=300, interval_s=0.01
        )
    )
    await asyncio.wait_for(heartbeat, timeout=0.5)

    # The real owner's lease is untouched.
    tr = await store.get_task_run(claimed["id"])
    assert tr is not None
    assert tr["worker_id"] == "worker-a"
    assert tr["lease_expires_at"] == claimed["lease_expires_at"]


# ---------------------------------------------------------------------------
# 5. Timeout-derived lease
# ---------------------------------------------------------------------------


async def test_timeout_derived_lease_helper():
    """_extend_lease_for_timeout pushes the lease to now + timeout_s + grace
    when timeout_s exceeds the base lease."""
    store = _make_store()
    now = _NOW
    await _create_flow_and_run(store, now)

    claimed = await store.claim_ready_task_run(now, worker_id="worker-a", lease_seconds=300)
    assert claimed is not None

    await _extend_lease_for_timeout(store, claimed, {"timeout_s": 3600}, now, 300)

    tr = await store.get_task_run(claimed["id"])
    assert tr is not None
    assert tr["lease_expires_at"] == now + timedelta(seconds=3600 + _LEASE_TIMEOUT_GRACE_S)
    assert tr["lease_expires_at"] >= now + timedelta(seconds=3600)


async def test_timeout_zero_keeps_default_lease():
    """timeout_s == 0 (unbounded) keeps the default claim lease — the
    heartbeat protects these tasks instead."""
    store = _make_store()
    now = _NOW
    await _create_flow_and_run(store, now)

    claimed = await store.claim_ready_task_run(now, worker_id="worker-a", lease_seconds=300)
    assert claimed is not None

    await _extend_lease_for_timeout(store, claimed, {"timeout_s": 0}, now, 300)

    tr = await store.get_task_run(claimed["id"])
    assert tr is not None
    assert tr["lease_expires_at"] == now + timedelta(seconds=300)


async def test_run_one_ready_task_applies_timeout_derived_lease():
    """Claiming via run_one_ready_task with a timeout_s=3600 task yields a
    lease_expires_at >= now + 3600 (visible after completion — terminal
    updates do not rewrite the lease fields)."""
    store = _make_store()
    now = _NOW
    flow, flow_run = await _create_flow_and_run(store, now, timeout_s=3600)

    result = await run_one_ready_task(
        store, now, claims={}, worker_id="worker-a", lease_seconds=300
    )
    assert result is not None
    assert result["task_key"] == "task_a"
    assert result["state"] == "success"

    tr = await store.get_task_run(result["id"])
    assert tr is not None
    assert tr["lease_expires_at"] is not None
    assert tr["lease_expires_at"] >= now + timedelta(seconds=3600)


# ---------------------------------------------------------------------------
# 6. Sequential claims for a single ready task
# ---------------------------------------------------------------------------


async def test_second_claim_for_single_ready_task_gets_none():
    """With one ready task (task_b still pending), the first claim wins and
    the second claim returns None — no double-claiming."""
    store = _make_store()
    now = _NOW
    await _create_flow_and_run(store, now)

    first = await store.claim_ready_task_run(now, worker_id="worker-a", lease_seconds=60)
    assert first is not None
    assert first["task_key"] == "task_a"

    second = await store.claim_ready_task_run(now, worker_id="worker-b", lease_seconds=60)
    assert second is None, "Second claim for the only ready task must get None"
