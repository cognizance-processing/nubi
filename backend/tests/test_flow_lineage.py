"""B2 — Run-lineage & reproducibility tests.

Coverage
--------
1. Run IDs are unique across materialize_flow_run calls.
2. Run seed is captured on flow_run (non-None, deterministic from run_id).
3. Stochastic cell gets seed injected → reproducible output for same seed.
4. Different runs (different run_ids) produce different seeds.
5. params_snapshot and code_version are stored on the flow_run.
6. Data lineage: add_run_output records an output→run link;
   list_run_outputs returns it; get_run_outputs_by_key finds it.
7. _record_run_output is called on the success path for materialized cells
   (end-to-end via drain_flow_run with a task returning __output_key__).
8. Stochastic task bypasses cache (cache_ttl_s > 0 is ignored when stochastic=True).
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

import pytest

from app.flows.executor import TaskContext, execute_task
from app.flows.runtime import (
    advance_readiness,
    drain_flow_run,
    materialize_flow_run,
)
from app.flows.store import InMemoryFlowStore, _seed_from_run_id
from app.flows.registry import reset_for_tests

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _utc() -> datetime:
    return datetime(2025, 6, 1, 12, 0, 0, tzinfo=timezone.utc)


NOW = _utc()
CLAIMS: dict[str, Any] = {"org_id": "org-test", "sub": "user-test"}


async def _make_flow(
    store: InMemoryFlowStore,
    spec: dict[str, Any],
    org_id: str = "org-test",
) -> dict[str, Any]:
    return await store.create_flow(
        org_id=org_id,
        created_by="user-test",
        name="test_flow",
        spec=spec,
    )


def _simple_spec() -> dict[str, Any]:
    """Single noop task."""
    return {
        "version": 1,
        "name": "simple",
        "tasks": [{"key": "t1", "kind": "noop", "needs": [], "config": {}}],
    }


def _stochastic_spec() -> dict[str, Any]:
    """Single python task with stochastic=True."""
    return {
        "version": 1,
        "name": "stochastic_flow",
        "tasks": [
            {
                "key": "rand",
                "kind": "python",
                "needs": [],
                "stochastic": True,
                "config": {
                    "code": (
                        "import random\n"
                        "result = {'value': random.random()}\n"
                    )
                },
                "timeout_s": 0,
            }
        ],
    }


def _output_spec() -> dict[str, Any]:
    """Python task that signals an output key via result."""
    return {
        "version": 1,
        "name": "output_flow",
        "tasks": [
            {
                "key": "write",
                "kind": "python",
                "needs": [],
                "config": {
                    "code": (
                        "result = {"
                        "'__output_key__': 'my_table',"
                        "'__output_uri__': 's3://bucket/my_table',"
                        "'__output_type__': 'table',"
                        "'row_count': 42"
                        "}\n"
                    )
                },
                "timeout_s": 0,
            }
        ],
    }


# ---------------------------------------------------------------------------
# 1. Run IDs are unique
# ---------------------------------------------------------------------------


async def test_run_ids_are_unique():
    store = InMemoryFlowStore()
    flow = await _make_flow(store, _simple_spec())

    run1 = await materialize_flow_run(store, flow, {}, "manual", NOW)
    run2 = await materialize_flow_run(store, flow, {}, "manual", NOW)

    assert run1["id"] != run2["id"], "Each materialize_flow_run must produce a unique run_id"


# ---------------------------------------------------------------------------
# 2. Seed is captured on the flow_run
# ---------------------------------------------------------------------------


async def test_run_seed_is_set():
    store = InMemoryFlowStore()
    flow = await _make_flow(store, _simple_spec())

    run = await materialize_flow_run(store, flow, {}, "manual", NOW)
    assert run["seed"] is not None, "flow_run.seed must be non-None"
    assert isinstance(run["seed"], int), "flow_run.seed must be an integer"
    assert run["seed"] >= 0, "flow_run.seed must be non-negative"


# ---------------------------------------------------------------------------
# 3. _seed_from_run_id is deterministic
# ---------------------------------------------------------------------------


def test_seed_from_run_id_is_deterministic():
    run_id = "550e8400-e29b-41d4-a716-446655440000"
    seed_a = _seed_from_run_id(run_id)
    seed_b = _seed_from_run_id(run_id)
    assert seed_a == seed_b, "_seed_from_run_id must be deterministic"


# ---------------------------------------------------------------------------
# 4. Different run IDs produce different seeds (with high probability)
# ---------------------------------------------------------------------------


def test_different_run_ids_produce_different_seeds():
    import uuid
    seeds = {_seed_from_run_id(str(uuid.uuid4())) for _ in range(20)}
    # With 20 random UUIDs the probability of any collision is negligible.
    assert len(seeds) > 1, "Different run_ids should produce different seeds"


# ---------------------------------------------------------------------------
# 5. params_snapshot and code_version stored on flow_run
# ---------------------------------------------------------------------------


async def test_params_snapshot_and_code_version_stored():
    store = InMemoryFlowStore()
    flow = await _make_flow(store, _simple_spec())
    params = {"region": "ZA", "date": "2025-06-01"}

    run = await materialize_flow_run(store, flow, params, "manual", NOW)

    assert run.get("params_snapshot") is not None, "params_snapshot must be set"
    assert run["params_snapshot"]["region"] == "ZA"

    assert run.get("code_version") is not None, "code_version must be set"
    assert run["code_version"]["flow_id"] == flow["id"]


# ---------------------------------------------------------------------------
# 6. Data lineage: add_run_output / list_run_outputs / get_run_outputs_by_key
# ---------------------------------------------------------------------------


async def test_add_and_list_run_outputs():
    store = InMemoryFlowStore()
    flow = await _make_flow(store, _simple_spec())
    run = await store.create_flow_run(
        flow_id=flow["id"],
        org_id="org-test",
        params={},
        trigger="manual",
    )
    run_id = run["id"]

    rec = await store.add_run_output(
        flow_run_id=run_id,
        org_id="org-test",
        task_key="write",
        output_key="my_table",
        output_type="table",
        output_uri="s3://bucket/my_table",
        meta={"row_count": 42},
    )

    assert rec["flow_run_id"] == run_id
    assert rec["output_key"] == "my_table"
    assert rec["output_type"] == "table"
    assert rec["meta"]["row_count"] == 42

    outputs = await store.list_run_outputs(run_id)
    assert len(outputs) == 1
    assert outputs[0]["id"] == rec["id"]


async def test_get_run_outputs_by_key():
    store = InMemoryFlowStore()
    flow = await _make_flow(store, _simple_spec())

    run1 = await store.create_flow_run(
        flow_id=flow["id"], org_id="org-test", params={}, trigger="manual"
    )
    run2 = await store.create_flow_run(
        flow_id=flow["id"], org_id="org-test", params={}, trigger="manual"
    )

    await store.add_run_output(
        flow_run_id=run1["id"], org_id="org-test",
        task_key="t", output_key="revenue_table", output_type="table",
    )
    await store.add_run_output(
        flow_run_id=run2["id"], org_id="org-test",
        task_key="t", output_key="revenue_table", output_type="table",
    )

    all_outputs = await store.get_run_outputs_by_key("org-test", "revenue_table")
    assert len(all_outputs) == 2
    run_ids = {o["flow_run_id"] for o in all_outputs}
    assert run1["id"] in run_ids
    assert run2["id"] in run_ids


async def test_run_output_different_org_isolated():
    """Outputs from a different org must not appear."""
    store = InMemoryFlowStore()
    flow_a = await _make_flow(store, _simple_spec(), org_id="org-A")
    flow_b = await _make_flow(store, _simple_spec(), org_id="org-B")

    run_a = await store.create_flow_run(
        flow_id=flow_a["id"], org_id="org-A", params={}, trigger="manual"
    )
    run_b = await store.create_flow_run(
        flow_id=flow_b["id"], org_id="org-B", params={}, trigger="manual"
    )

    await store.add_run_output(
        flow_run_id=run_a["id"], org_id="org-A",
        task_key="t", output_key="shared_key",
    )
    await store.add_run_output(
        flow_run_id=run_b["id"], org_id="org-B",
        task_key="t", output_key="shared_key",
    )

    org_a_outputs = await store.get_run_outputs_by_key("org-A", "shared_key")
    assert len(org_a_outputs) == 1
    assert org_a_outputs[0]["org_id"] == "org-A"


# ---------------------------------------------------------------------------
# 7. _record_run_output called on success path (drain_flow_run)
# ---------------------------------------------------------------------------


async def test_lineage_recorded_on_success_path():
    """A task returning __output_key__ triggers a lineage record."""
    reset_for_tests()
    store = InMemoryFlowStore()
    flow = await _make_flow(store, _output_spec())

    run = await materialize_flow_run(store, flow, {}, "manual", NOW)
    final = await drain_flow_run(store, run["id"], NOW, CLAIMS)

    assert final["state"] == "success", f"flow_run did not succeed: {final}"

    outputs = await store.list_run_outputs(run["id"])
    assert len(outputs) == 1, f"Expected 1 lineage output record, got {len(outputs)}"
    assert outputs[0]["output_key"] == "my_table"
    assert outputs[0]["output_uri"] == "s3://bucket/my_table"
    assert outputs[0]["output_type"] == "table"
    assert outputs[0]["meta"]["row_count"] == 42


# ---------------------------------------------------------------------------
# 8. Stochastic task bypasses cache
# ---------------------------------------------------------------------------


async def test_stochastic_task_bypasses_cache():
    """A stochastic task_run with cache_ttl_s > 0 must not use the cache."""
    reset_for_tests()
    store = InMemoryFlowStore()

    # Create a task_run for a task that previously succeeded with a cache key.
    flow = await _make_flow(store, _stochastic_spec())
    run = await materialize_flow_run(store, flow, {}, "manual", NOW)
    flow_run_id = run["id"]

    # Pre-populate a "cached" result with the same cache_key.
    trs = await store.list_task_runs(flow_run_id)
    assert len(trs) >= 1
    the_tr = trs[0]

    # Manually set a cache_key and cache_ttl_s on the task_run to simulate
    # a situation where caching would normally kick in.
    await store.update_task_run(the_tr["id"], {
        "cache_key": "stale-cache-key",
        "state": "ready",  # reset to ready
    })
    # Also set stochastic=True on the task_run dict directly for the test.
    store._task_runs[the_tr["id"]]["stochastic"] = True
    store._task_runs[the_tr["id"]]["cache_ttl_s"] = 300

    # Add a "prior success" that the cache would return if not bypassed.
    prior_run = await store.create_flow_run(
        flow_id=flow["id"], org_id="org-test", params={}, trigger="manual"
    )
    await store.add_task_runs(prior_run["id"], [{
        "task_key": "cached_result",
        "org_id": "org-test",
        "state": "success",
        "depends_on": [],
        "attempt": 0,
        "kind": "noop",
        "config": {},
        "cache_key": "stale-cache-key",
        "cache_ttl_s": 300,
        "result": {"cached": True},
    }])

    # Drain – the stochastic task must execute (not return cached=True).
    final = await drain_flow_run(store, flow_run_id, NOW, CLAIMS)

    assert final["state"] == "success"

    final_trs = await store.list_task_runs(flow_run_id)
    rand_tr = next((t for t in final_trs if t["task_key"] == "rand"), None)
    assert rand_tr is not None
    # The result must NOT be the stale cached value.
    assert rand_tr.get("result") is not None
    result = rand_tr["result"]
    assert result.get("cached") is not True, (
        "Stochastic task must NOT return the cached result"
    )


# ---------------------------------------------------------------------------
# 9. Stochastic cell seed injection makes results reproducible within a run
# ---------------------------------------------------------------------------


def test_stochastic_seed_injection_makes_reproducible():
    """Stochastic python cell with the same seed produces the same result."""
    reset_for_tests()

    code = (
        "import random\n"
        "result = {'value': random.random()}\n"
    )
    task = {
        "key": "stochastic_cell",
        "kind": "python",
        "stochastic": True,
        "config": {"code": code},
        "timeout_s": 0,
    }

    seed = 12345
    ctx = TaskContext(
        flow_params={},
        inputs={},
        now=datetime(2025, 6, 1, tzinfo=timezone.utc),
        seed=seed,
    )

    result1 = execute_task(task, ctx, {})
    result2 = execute_task(task, ctx, {})

    assert result1["state"] == "success", f"Unexpected state: {result1}"
    assert result2["state"] == "success", f"Unexpected state: {result2}"
    # With the same seed, both runs must produce identical values.
    assert result1["result"]["value"] == result2["result"]["value"], (
        "Stochastic cells with the same seed must produce reproducible results"
    )


def test_stochastic_different_seeds_different_results():
    """Different seeds → (almost certainly) different values."""
    reset_for_tests()

    code = (
        "import random\n"
        "result = {'value': random.random()}\n"
    )
    task = {
        "key": "stochastic_cell",
        "kind": "python",
        "stochastic": True,
        "config": {"code": code},
        "timeout_s": 0,
    }

    values = set()
    for s in range(20):
        ctx = TaskContext(
            flow_params={},
            inputs={},
            now=datetime(2025, 6, 1, tzinfo=timezone.utc),
            seed=s,
        )
        result = execute_task(task, ctx, {})
        if result["state"] == "success":
            values.add(result["result"]["value"])

    assert len(values) > 1, "Different seeds should produce different values"


# ---------------------------------------------------------------------------
# REGRESSION Fix 1: _claims_with_owner_policies uses key-presence not truthiness
# ---------------------------------------------------------------------------


def _make_flow_with_owner_policies(policies: dict) -> dict:
    """Build a minimal flow dict containing an owner-policy snapshot."""
    return {
        "id": "flow-rls-1",
        "org_id": "org-test",
        "spec": {
            "tasks": [],
            "runtime_config": {
                "__owner_policies__": policies,
            },
        },
    }


def test_admin_empty_policies_not_overridden_by_owner_snapshot():
    """An admin interactive run (policies={}) must NOT inherit the flow's owner snapshot.

    The bug: `claims.get('policies')` is falsy for `{}`, so an explicit empty-
    policy set was being replaced with the flow's snapshotted tenant policies,
    silently restricting the admin to one tenant.  The fix uses key-presence
    (`'policies' in claims`) instead.
    """
    from app.flows.runtime import _claims_with_owner_policies

    # The flow has a non-empty owner snapshot that should NOT be applied.
    owner_snapshot = {"org_id": "org-owner", "tenant": "owner-tenant"}
    flow = _make_flow_with_owner_policies(owner_snapshot)

    # Admin explicitly supplies an empty policy set (unrestricted).
    admin_claims = {"sub": "admin-user", "policies": {}}

    result = _claims_with_owner_policies(admin_claims, flow, flow_run_id="run-1")

    # The result must keep the admin's explicitly-supplied empty policy set,
    # NOT the owner snapshot.
    assert result["policies"] == {}, (
        f"Admin empty-policy set must not be replaced by owner snapshot; got {result['policies']!r}"
    )
    assert result is admin_claims or result == admin_claims, (
        "Claims with 'policies' key must be returned unchanged (no merge)"
    )


def test_scheduled_run_no_policies_key_gets_owner_snapshot():
    """Scheduled / worker-pool runs (no 'policies' key in claims) get the owner snapshot.

    This is the correct path: the scheduler drains with claims={} (no 'policies'
    key), and the flow's owner-policy snapshot is threaded in so SQL cells
    row-filter to the owner's tenant.
    """
    from app.flows.runtime import _claims_with_owner_policies

    owner_snapshot = {"org_id": "org-owner", "tenant": "owner-tenant"}
    flow = _make_flow_with_owner_policies(owner_snapshot)

    # Scheduled run: no 'policies' key at all.
    sched_claims: dict = {"org_id": "org-test"}

    result = _claims_with_owner_policies(sched_claims, flow, flow_run_id="run-2")

    assert result["policies"] == owner_snapshot, (
        f"Scheduled run must receive owner snapshot; got {result['policies']!r}"
    )


def test_nonempty_policies_in_claims_never_overridden():
    """Non-empty policies already in claims (interactive user) must never be touched."""
    from app.flows.runtime import _claims_with_owner_policies

    owner_snapshot = {"org_id": "org-owner", "tenant": "owner-tenant"}
    flow = _make_flow_with_owner_policies(owner_snapshot)

    user_policies = {"org_id": "org-test", "tenant": "user-tenant"}
    user_claims = {"sub": "user-1", "policies": user_policies}

    result = _claims_with_owner_policies(user_claims, flow, flow_run_id="run-3")

    assert result["policies"] == user_policies, (
        "Non-empty user policies must not be replaced by owner snapshot"
    )


# ---------------------------------------------------------------------------
# REGRESSION Fix 3: advance_readiness query count is reduced (not 4x per call)
# ---------------------------------------------------------------------------


class _CountingStore:
    """Wraps InMemoryFlowStore and counts list_task_runs calls."""

    def __init__(self, inner: InMemoryFlowStore) -> None:
        self._inner = inner
        self.list_task_runs_count = 0

    def __getattr__(self, name: str):
        return getattr(self._inner, name)

    async def list_task_runs(self, flow_run_id: str) -> list:
        self.list_task_runs_count += 1
        return await self._inner.list_task_runs(flow_run_id)


async def test_advance_readiness_query_count_reduced():
    """advance_readiness must call list_task_runs at most 2 times per invocation.

    The original code unconditionally called list_task_runs 4 times on every
    invocation.  The fix reduces this to at most 2 calls (1 initial load +
    1 conditional reload only when map fan-in or branch activation mutates state).
    For a simple flow with no map/branch nodes, only 1 call should be made.
    """
    reset_for_tests()
    inner = InMemoryFlowStore()
    store = _CountingStore(inner)

    # Three-task linear chain: A → B → C.
    flow = await inner.create_flow(
        org_id="org-test",
        created_by="user-test",
        name="chain_flow",
        spec={
            "version": 1,
            "name": "chain",
            "tasks": [
                {"key": "A", "kind": "noop", "needs": [], "config": {}},
                {"key": "B", "kind": "noop", "needs": ["A"], "config": {}},
                {"key": "C", "kind": "noop", "needs": ["B"], "config": {}},
            ],
        },
    )
    run = await materialize_flow_run(inner, flow, {}, "manual", NOW)

    store.list_task_runs_count = 0  # reset counter after setup

    await advance_readiness(store, run["id"], NOW)

    # The fixed implementation calls it at most 2 (usually 1 for no-map flows).
    # The original bug was 4 unconditional calls; the prior fix reduced to < 4;
    # this test now enforces the tighter correct bound of at most 2.
    assert store.list_task_runs_count <= 2, (
        f"advance_readiness called list_task_runs {store.list_task_runs_count} times; "
        f"expected at most 2 (1 initial load + at most 1 conditional map/branch reload)."
    )


async def test_advance_readiness_no_snapshot_bounded_per_call():
    """advance_readiness without a preloaded snapshot issues a bounded (small
    constant) number of list_task_runs per call even over a multi-step drain.

    For a linear chain of N tasks drained one by one, each advance_readiness
    invocation (called without _preloaded_task_runs) must make at most 2
    list_task_runs calls.  This ensures the no-snapshot path is O(N) in total
    round-trips, not O(N^2).
    """
    reset_for_tests()
    N = 8  # enough tasks for the O(N^2) pattern to be visible

    inner = InMemoryFlowStore()
    store = _CountingStore(inner)

    # Build an N-task linear chain: T0 → T1 → ... → T(N-1).
    tasks = [{"key": f"T{i}", "kind": "noop", "needs": [f"T{i - 1}"] if i > 0 else [], "config": {}}
             for i in range(N)]
    flow = await inner.create_flow(
        org_id="org-test",
        created_by="user-test",
        name="long_chain",
        spec={"version": 1, "name": "long_chain", "tasks": tasks},
    )
    run = await materialize_flow_run(inner, flow, {}, "manual", NOW)

    # Simulate draining: for each step, mark one task success and call
    # advance_readiness WITHOUT a preloaded snapshot (the no-snapshot path).
    trs = await inner.list_task_runs(run["id"])
    tr_by_key = {tr["task_key"]: tr for tr in trs}

    for i in range(N):
        key = f"T{i}"
        tr = tr_by_key[key]
        # Claim / mark running.
        await inner.update_task_run(tr["id"], {"state": "running"})
        # Mark success.
        await inner.update_task_run(tr["id"], {"state": "success", "result": {}, "finished_at": NOW})

        count_before = store.list_task_runs_count
        # Call advance_readiness WITHOUT a preloaded snapshot (the targeted path).
        await advance_readiness(store, run["id"], NOW)
        calls_this_step = store.list_task_runs_count - count_before

        assert calls_this_step <= 2, (
            f"advance_readiness (no snapshot) made {calls_this_step} list_task_runs calls "
            f"at step {i} (task {key}); expected at most 2.  "
            f"This indicates an O(N^2) N+1 regression on the no-snapshot path."
        )

        # Refresh tr_by_key for next iteration.
        trs = await inner.list_task_runs(run["id"])
        tr_by_key = {tr["task_key"]: tr for tr in trs}


async def test_advance_readiness_no_snapshot_total_queries_linear():
    """For a drain of N tasks, the total list_task_runs calls across all
    advance_readiness invocations (no snapshot path) must be at most 2*N.

    This is the O(N) bound test: if each call makes at most 2 queries,
    N calls make at most 2N total — linear, not quadratic.
    """
    reset_for_tests()
    N = 10

    inner = InMemoryFlowStore()
    store = _CountingStore(inner)

    tasks = [{"key": f"T{i}", "kind": "noop", "needs": [f"T{i - 1}"] if i > 0 else [], "config": {}}
             for i in range(N)]
    flow = await inner.create_flow(
        org_id="org-test",
        created_by="user-test",
        name="linear_chain",
        spec={"version": 1, "name": "linear_chain", "tasks": tasks},
    )
    run = await materialize_flow_run(inner, flow, {}, "manual", NOW)

    trs = await inner.list_task_runs(run["id"])
    tr_by_key = {tr["task_key"]: tr for tr in trs}

    store.list_task_runs_count = 0  # reset after setup

    for i in range(N):
        key = f"T{i}"
        tr = tr_by_key[key]
        await inner.update_task_run(tr["id"], {"state": "running"})
        await inner.update_task_run(tr["id"], {"state": "success", "result": {}, "finished_at": NOW})
        await advance_readiness(store, run["id"], NOW)

        # Refresh for next step.
        trs = await inner.list_task_runs(run["id"])
        tr_by_key = {tr["task_key"]: tr for tr in trs}

    assert store.list_task_runs_count <= 2 * N, (
        f"Total list_task_runs across {N} advance_readiness calls (no snapshot): "
        f"{store.list_task_runs_count}; expected at most {2 * N} (at most 2 per call).  "
        f"N+1 / O(N^2) regression detected."
    )


# ---------------------------------------------------------------------------
# REGRESSION Fix 4: run_one_ready_task issues list_task_runs at most ONCE
# ---------------------------------------------------------------------------


async def test_run_one_ready_task_single_list_task_runs():
    """run_one_ready_task must issue list_task_runs at most 1 time per task.

    The bug: list_task_runs was called twice — once inside the cache-TTL block
    and again unconditionally for the TaskContext inputs.  The fix fetches the
    list once before the cache block and reuses it for both purposes.
    """
    from app.flows.runtime import run_one_ready_task

    reset_for_tests()
    inner = InMemoryFlowStore()
    store = _CountingStore(inner)

    # Simple one-task flow (noop, no cache).
    flow = await inner.create_flow(
        org_id="org-test",
        created_by="user-test",
        name="single_task_flow",
        spec={
            "version": 1,
            "name": "single_task",
            "tasks": [
                {"key": "T1", "kind": "noop", "needs": [], "config": {}},
            ],
        },
    )
    run = await materialize_flow_run(inner, flow, {}, "manual", NOW)
    await advance_readiness(inner, run["id"], NOW)

    # Reset counter after setup so only the run_one_ready_task call is counted.
    store.list_task_runs_count = 0

    result = await run_one_ready_task(store, NOW, claims=CLAIMS)

    assert result is not None, "Expected a task to be executed"
    # The function body itself must issue at most 1 list_task_runs call (down
    # from 2 before the fix).  advance_readiness adds 1 more call for the
    # simple single-task flow (no map/branch reload needed), so the total
    # is at most 2.  We assert <= 2 to allow for the advance_readiness call
    # while ruling out the pre-fix N+1 of 3+ calls.
    assert store.list_task_runs_count <= 2, (
        f"run_one_ready_task called list_task_runs {store.list_task_runs_count} times; "
        f"expected at most 2 (1 in body + 1 in advance_readiness); N+1 regression was 3+."
    )


async def test_run_one_ready_task_single_list_task_runs_with_cache():
    """run_one_ready_task must still issue list_task_runs at most 2 times when
    cache_ttl_s > 0 and there is no cache hit (the task executes normally).

    Before the fix the cache-miss path issued list_task_runs TWICE in the
    function body alone (once for the cache scan, once for inputs) plus 1 more
    in advance_readiness = 3 total.  After the fix the single pre-fetched list
    is shared so the body contributes only 1, giving at most 2 total.
    """
    from app.flows.runtime import run_one_ready_task

    reset_for_tests()
    inner = InMemoryFlowStore()
    store = _CountingStore(inner)

    flow = await inner.create_flow(
        org_id="org-test",
        created_by="user-test",
        name="cached_task_flow",
        spec={
            "version": 1,
            "name": "cached_task",
            "tasks": [
                {
                    "key": "C1",
                    "kind": "noop",
                    "needs": [],
                    "config": {},
                    "cache_ttl_s": 300,
                    "cache_key": "ck-fixed",
                },
            ],
        },
    )
    run = await materialize_flow_run(inner, flow, {}, "manual", NOW)
    await advance_readiness(inner, run["id"], NOW)

    store.list_task_runs_count = 0

    result = await run_one_ready_task(store, NOW, claims=CLAIMS)

    assert result is not None, "Expected a task to be executed"
    assert store.list_task_runs_count <= 2, (
        f"run_one_ready_task called list_task_runs {store.list_task_runs_count} times "
        f"(cache-miss path); expected at most 2 (1 body + 1 advance_readiness)."
    )


# ---------------------------------------------------------------------------
# REGRESSION Fix 5: admin explicit empty-{} owner snapshot is respected
# ---------------------------------------------------------------------------


def test_admin_empty_owner_snapshot_applied_not_skipped():
    """An owner-policy snapshot of {} (admin, no row-filter) must be APPLIED,
    not silently skipped with a warning.

    The bug: _claims_with_owner_policies used `if not owner_policies:` which
    is truthy for {}, so an admin whose snapshot is {} was treated the same as
    "no snapshot at all" and the warning path was taken instead of merging the
    (empty) policies.  With the fix, key-presence / `is None` is used so an
    explicit empty {} snapshot is merged into claims.
    """
    from app.flows.runtime import _claims_with_owner_policies

    # Flow whose owner is an admin — explicit empty snapshot.
    admin_owner_flow = {
        "id": "flow-admin-owner",
        "org_id": "org-test",
        "spec": {
            "tasks": [],
            "runtime_config": {
                "__owner_policies__": {},  # explicit empty — admin owner
            },
        },
    }

    # Scheduled run: no 'policies' key in claims.
    sched_claims: dict = {"org_id": "org-test"}

    result = _claims_with_owner_policies(sched_claims, admin_owner_flow, flow_run_id="run-admin")

    assert "policies" in result, (
        "Scheduled run against an admin-owner flow must receive 'policies' key "
        "(even when the snapshot is {}); got: " + repr(result)
    )
    assert result["policies"] == {}, (
        f"Admin empty-snapshot must set policies={{}}, not something else; got {result['policies']!r}"
    )


def test_no_snapshot_key_fails_closed_for_scheduled_run():
    """A scheduled run (no 'policies' in claims) against a flow with NO
    __owner_policies__ key at all (pre-B2) must FAIL CLOSED.

    Previously this returned claims unchanged → query cells ran with policies={}
    = no RLS row filter = cross-tenant leak.  The fix raises RuntimeError so the
    flow must be re-saved/re-enabled to capture the owner snapshot, rather than
    silently leaking rows.

    This still distinguishes the two cases that were previously collapsed by the
    truthiness check: 'no snapshot' (raises) vs 'explicit empty snapshot'
    (allowed, applies {}).
    """
    from app.flows.runtime import _claims_with_owner_policies

    # Pre-B2 flow: runtime_config exists but has NO __owner_policies__ key.
    pre_b2_flow = {
        "id": "flow-pre-b2",
        "org_id": "org-test",
        "spec": {
            "tasks": [],
            "runtime_config": {
                "some_other_key": "value",
            },
        },
    }

    sched_claims: dict = {"org_id": "org-test"}

    with pytest.raises(RuntimeError, match="owner-policy snapshot"):
        _claims_with_owner_policies(sched_claims, pre_b2_flow, flow_run_id="run-pre-b2")


# ---------------------------------------------------------------------------
# REGRESSION Fix 6: run_one_ready_task worker path — bounded list_task_runs
# ---------------------------------------------------------------------------


async def test_run_one_ready_task_worker_bounded_list_task_runs():
    """run_one_ready_task (distributed worker path) must issue at most 2
    list_task_runs calls per tick regardless of flow size.

    The pre-fix N+1: every advance_readiness call inside run_one_ready_task
    issued its own unconditional list_task_runs (≥2 calls per tick on a map
    fan-out after a normal task, more on retrying/failed paths).  The fix
    threads _preloaded_task_runs=_patch_snapshot(updated_tr) into every
    advance_readiness call on non-map paths so advance_readiness can skip
    its own initial load, reducing the total to at most 2 per worker tick
    (1 fetch in run_one_ready_task + at most 1 conditional reload inside
    advance_readiness when a map fan-in / branch activates).

    This test runs a 5-task linear chain one task at a time via
    run_one_ready_task and asserts that each individual tick issues at most
    2 list_task_runs calls.
    """
    from app.flows.runtime import run_one_ready_task

    reset_for_tests()
    N = 5
    inner = InMemoryFlowStore()
    store = _CountingStore(inner)

    tasks = [
        {"key": f"W{i}", "kind": "noop", "needs": [f"W{i - 1}"] if i > 0 else [], "config": {}}
        for i in range(N)
    ]
    flow = await inner.create_flow(
        org_id="org-test",
        created_by="user-test",
        name="worker_n1_flow",
        spec={"version": 1, "name": "worker_n1", "tasks": tasks},
    )
    run = await materialize_flow_run(inner, flow, {}, "manual", NOW)
    await advance_readiness(inner, run["id"], NOW)

    for tick in range(N):
        store.list_task_runs_count = 0
        result = await run_one_ready_task(store, NOW, claims=CLAIMS)
        calls_this_tick = store.list_task_runs_count
        assert result is not None, f"Tick {tick}: expected a task to run but got None"
        assert calls_this_tick <= 2, (
            f"Tick {tick} (task {result.get('task_key')!r}): run_one_ready_task issued "
            f"{calls_this_tick} list_task_runs calls; expected at most 2 "
            f"(1 in worker body + at most 1 conditional reload in advance_readiness). "
            f"N+1 regression in the distributed worker path."
        )
        # Advance readiness so the next task becomes ready for the next tick.
        await advance_readiness(inner, run["id"], NOW)
