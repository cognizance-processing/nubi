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
    """advance_readiness must call list_task_runs fewer than 4 times per invocation.

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

    # The original implementation would have called list_task_runs 4 times.
    # The fixed implementation calls it at most 2 (usually 1 for no-map flows).
    assert store.list_task_runs_count < 4, (
        f"advance_readiness called list_task_runs {store.list_task_runs_count} times; "
        f"expected fewer than 4 (N+1 regression)."
    )
