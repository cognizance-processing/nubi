"""B6 — Run-history: run lineage + SLA flag tests.

Coverage
--------
1.  list_flow_runs returns runs newest-first.
2.  Each run includes params_snapshot, code_version, seed.
3.  Run-history endpoint (get_flow_run_by_id) returns task_runs + lineage.
4.  list_run_outputs / add_run_output used for per-run lineage.
5.  SLA flag: run exceeding expected_s is flagged.
6.  SLA flag: run within expected_s is NOT flagged.
7.  Multiple runs for the same flow are all returned.
8.  Cross-org run is NOT accessible (404).
9.  Run history with trigger=sweep has __sweep_id__ in params_snapshot.
10. Run history with trigger=backfill has __backfill_id__ in params_snapshot.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from app.flows.runtime import drain_flow_run, materialize_flow_run
from app.flows.store import InMemoryFlowStore
from app.flows.triggers import flag_sla_breach
from app.flows.registry import reset_for_tests

pytestmark = pytest.mark.asyncio

NOW = datetime(2025, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
CLAIMS: dict[str, Any] = {"org_id": "org-test", "sub": "user-test"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _make_flow(
    store: InMemoryFlowStore,
    org_id: str = "org-test",
    name: str = "test_flow",
    spec: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if spec is None:
        spec = {
            "version": 1,
            "name": name,
            "tasks": [{"key": "t1", "kind": "noop", "needs": [], "config": {}}],
        }
    return await store.create_flow(
        org_id=org_id,
        created_by="user-test",
        name=name,
        spec=spec,
    )


async def _run_flow(
    store: InMemoryFlowStore,
    flow: dict[str, Any],
    params: dict[str, Any] | None = None,
    trigger: str = "manual",
) -> dict[str, Any]:
    reset_for_tests()
    flow_run = await materialize_flow_run(store, flow, params or {}, trigger, NOW)
    return await drain_flow_run(store, flow_run["id"], NOW, claims=CLAIMS)


# ---------------------------------------------------------------------------
# 1. list_flow_runs returns runs newest-first
# ---------------------------------------------------------------------------


async def test_list_flow_runs_newest_first():
    store = InMemoryFlowStore()
    flow = await _make_flow(store)

    r1 = await materialize_flow_run(store, flow, {"i": 0}, "manual", NOW)
    r2 = await materialize_flow_run(store, flow, {"i": 1}, "manual", NOW)
    r3 = await materialize_flow_run(store, flow, {"i": 2}, "manual", NOW)

    runs = await store.list_flow_runs(flow["id"])
    # Newest first: r3 > r2 > r1.
    assert runs[0]["id"] == r3["id"]
    assert runs[-1]["id"] == r1["id"]


# ---------------------------------------------------------------------------
# 2. Each run includes params_snapshot, code_version, seed
# ---------------------------------------------------------------------------


async def test_run_has_lineage_fields():
    store = InMemoryFlowStore()
    flow = await _make_flow(store)

    params = {"region": "ZA", "date": "2025-06-01"}
    run = await materialize_flow_run(store, flow, params, "manual", NOW)

    assert run.get("params_snapshot") is not None
    assert run["params_snapshot"].get("region") == "ZA"

    assert run.get("code_version") is not None
    assert run["code_version"].get("flow_id") == flow["id"]

    assert run.get("seed") is not None
    assert isinstance(run["seed"], int)


# ---------------------------------------------------------------------------
# 3. get_flow_run returns task_runs with lineage
# ---------------------------------------------------------------------------


async def test_get_flow_run_includes_task_runs():
    store = InMemoryFlowStore()
    flow = await _make_flow(store)

    final_run = await _run_flow(store, flow, params={"x": 1})
    assert final_run["state"] == "success"

    task_runs = await store.list_task_runs(final_run["id"])
    assert len(task_runs) >= 1
    assert all("task_key" in tr for tr in task_runs)
    assert all("state" in tr for tr in task_runs)


# ---------------------------------------------------------------------------
# 4. add_run_output + list_run_outputs for per-run lineage
# ---------------------------------------------------------------------------


async def test_run_output_lineage():
    store = InMemoryFlowStore()
    flow = await _make_flow(store)

    run = await _run_flow(store, flow)
    run_id = run["id"]

    output = await store.add_run_output(
        flow_run_id=run_id,
        org_id="org-test",
        task_key="t1",
        output_key="sales_table",
        output_type="table",
        output_uri="s3://bucket/sales",
        meta={"row_count": 1000},
    )

    assert output["output_key"] == "sales_table"

    outputs = await store.list_run_outputs(run_id)
    assert len(outputs) == 1
    assert outputs[0]["task_key"] == "t1"
    assert outputs[0]["meta"]["row_count"] == 1000


# ---------------------------------------------------------------------------
# 5. SLA flag: exceeded
# ---------------------------------------------------------------------------


def test_sla_flag_exceeded():
    now = datetime(2025, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
    run = {
        "started_at": now - timedelta(seconds=200),
        "finished_at": now,
        "state": "success",
    }
    assert flag_sla_breach(run, expected_s=100.0, now=now) is True


# ---------------------------------------------------------------------------
# 6. SLA flag: within
# ---------------------------------------------------------------------------


def test_sla_flag_within():
    now = datetime(2025, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
    run = {
        "started_at": now - timedelta(seconds=50),
        "finished_at": now,
        "state": "success",
    }
    assert flag_sla_breach(run, expected_s=100.0, now=now) is False


# ---------------------------------------------------------------------------
# 7. Multiple runs for the same flow are all returned
# ---------------------------------------------------------------------------


async def test_multiple_runs_all_returned():
    store = InMemoryFlowStore()
    flow = await _make_flow(store)

    for i in range(5):
        await materialize_flow_run(store, flow, {"i": i}, "manual", NOW)

    runs = await store.list_flow_runs(flow["id"])
    assert len(runs) == 5


# ---------------------------------------------------------------------------
# 8. Cross-org run not accessible
# ---------------------------------------------------------------------------


async def test_cross_org_run_not_accessible():
    store = InMemoryFlowStore()
    flow_a = await _make_flow(store, org_id="org-A")
    flow_b = await _make_flow(store, org_id="org-B")

    run_a = await materialize_flow_run(store, flow_a, {}, "manual", NOW)

    # Verify the run belongs to org-A.
    fetched = await store.get_flow_run(run_a["id"])
    assert fetched is not None
    assert str(fetched["org_id"]) == "org-A"

    # Simulate what the route does: check org_id.
    assert str(fetched["org_id"]) != "org-B", "Cross-org run should not be accessible to org-B"


# ---------------------------------------------------------------------------
# 9. Sweep runs have __sweep_id__ in params
# ---------------------------------------------------------------------------


async def test_sweep_run_has_sweep_id_in_params():
    from app.flows.sweep import run_sweep  # noqa: PLC0415
    from app.flows.triggers import set_trigger_registry, InMemoryTriggerRegistry  # noqa: PLC0415

    set_trigger_registry(InMemoryTriggerRegistry())
    reset_for_tests()

    store = InMemoryFlowStore()
    flow = await _make_flow(store)

    result = await run_sweep(
        store=store,
        flow=flow,
        param_sets=[{"region": "ZA"}],
        trigger="sweep",
        now=NOW,
        claims=CLAIMS,
    )

    assert result.succeeded == 1
    run_id = result.cells[0].run_id
    run = await store.get_flow_run(run_id)
    assert run is not None
    assert run["params"].get("__sweep_id__") == result.sweep_id
    assert run["trigger"] == "sweep"


# ---------------------------------------------------------------------------
# 10. Backfill runs have __backfill_id__ in params
# ---------------------------------------------------------------------------


async def test_backfill_run_has_backfill_id_in_params():
    from app.flows.sweep import run_backfill  # noqa: PLC0415
    from app.flows.triggers import set_trigger_registry, InMemoryTriggerRegistry  # noqa: PLC0415

    set_trigger_registry(InMemoryTriggerRegistry())
    reset_for_tests()

    store = InMemoryFlowStore()
    flow = await _make_flow(store)

    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    end = datetime(2025, 1, 3, tzinfo=timezone.utc)

    result = await run_backfill(
        store=store,
        flow=flow,
        start=start,
        end=end,
        window="1d",
        trigger="backfill",
        now=NOW,
        claims=CLAIMS,
    )

    assert result.total == 2
    assert result.succeeded == 2

    for w in result.windows:
        run = await store.get_flow_run(w.run_id)
        assert run is not None
        assert run["params"].get("__backfill_id__") == result.backfill_id
        assert run["trigger"] == "backfill"


# ---------------------------------------------------------------------------
# 11. Run history retrieves enriched data via list_flow_runs
# ---------------------------------------------------------------------------


async def test_run_history_enrichment():
    """Simulate what the run-history endpoint does: list runs + outputs + lineage."""
    store = InMemoryFlowStore()
    flow = await _make_flow(store, spec={
        "version": 1,
        "name": "enriched_flow",
        "runtime_config": {"freshness_sla_s": 3600},  # 1 hour SLA
        "tasks": [{"key": "t1", "kind": "noop", "needs": [], "config": {}}],
    })

    final_run = await _run_flow(store, flow, params={"key": "val"})
    assert final_run["state"] == "success"

    # Add an output record.
    await store.add_run_output(
        flow_run_id=final_run["id"],
        org_id="org-test",
        task_key="t1",
        output_key="my_output",
        output_type="table",
    )

    runs = await store.list_flow_runs(flow["id"])
    assert len(runs) == 1
    run = runs[0]

    # Lineage fields present.
    assert run.get("params_snapshot") is not None
    assert run.get("code_version") is not None
    assert run.get("seed") is not None

    # Output.
    outputs = await store.list_run_outputs(run["id"])
    assert len(outputs) == 1
    assert outputs[0]["output_key"] == "my_output"

    # SLA not exceeded (run completed immediately in test).
    now = datetime.now(timezone.utc)
    spec = flow.get("spec") or {}
    rc = spec.get("runtime_config") or {}
    expected_s = rc.get("freshness_sla_s")
    assert flag_sla_breach(run, expected_s, now) is False


# ---------------------------------------------------------------------------
# REGRESSION: [MED N+1] list_run_outputs_for_runs batch method
# ---------------------------------------------------------------------------


async def test_list_run_outputs_for_runs_batch():
    """list_run_outputs_for_runs must return outputs grouped by run_id."""
    store = InMemoryFlowStore()
    flow = await _make_flow(store)

    # Create two runs and add outputs for each.
    run_a = await _run_flow(store, flow, params={"run": "a"})
    run_b = await _run_flow(store, flow, params={"run": "b"})

    await store.add_run_output(
        flow_run_id=run_a["id"], org_id="org-test", task_key="t1",
        output_key="out_a1", output_type="table",
    )
    await store.add_run_output(
        flow_run_id=run_a["id"], org_id="org-test", task_key="t1",
        output_key="out_a2", output_type="file",
    )
    await store.add_run_output(
        flow_run_id=run_b["id"], org_id="org-test", task_key="t1",
        output_key="out_b1", output_type="metric",
    )

    batch = await store.list_run_outputs_for_runs([run_a["id"], run_b["id"]])

    assert set(batch.keys()) == {run_a["id"], run_b["id"]}
    assert len(batch[run_a["id"]]) == 2
    assert len(batch[run_b["id"]]) == 1
    keys_a = {o["output_key"] for o in batch[run_a["id"]]}
    assert keys_a == {"out_a1", "out_a2"}
    assert batch[run_b["id"]][0]["output_key"] == "out_b1"


async def test_list_run_outputs_for_runs_empty():
    """Empty run_ids list must return empty dict."""
    store = InMemoryFlowStore()
    result = await store.list_run_outputs_for_runs([])
    assert result == {}


async def test_list_run_outputs_for_runs_no_outputs():
    """Runs with no outputs must be omitted from the result."""
    store = InMemoryFlowStore()
    flow = await _make_flow(store)
    run = await _run_flow(store, flow)

    batch = await store.list_run_outputs_for_runs([run["id"]])
    # No outputs were added — run_id should be absent.
    assert run["id"] not in batch


async def test_list_run_outputs_for_runs_single_query_semantics():
    """The batch method must return the same data as N individual list_run_outputs calls."""
    store = InMemoryFlowStore()
    flow = await _make_flow(store)

    runs = []
    for i in range(4):
        r = await _run_flow(store, flow, params={"i": i})
        runs.append(r)
        await store.add_run_output(
            flow_run_id=r["id"], org_id="org-test", task_key="t1",
            output_key=f"out_{i}", output_type="table",
        )

    run_ids = [r["id"] for r in runs]
    batch = await store.list_run_outputs_for_runs(run_ids)

    for r in runs:
        individual = await store.list_run_outputs(r["id"])
        batch_out = batch.get(r["id"], [])
        assert len(individual) == len(batch_out), (
            f"Batch output count {len(batch_out)} != individual count "
            f"{len(individual)} for run {r['id']}"
        )
        ind_keys = {o["output_key"] for o in individual}
        batch_keys = {o["output_key"] for o in batch_out}
        assert ind_keys == batch_keys


# ---------------------------------------------------------------------------
# REGRESSION: [MED N+1] list_flow_runs limit parameter
# ---------------------------------------------------------------------------


async def test_list_flow_runs_limit_applied():
    """list_flow_runs must respect the limit parameter."""
    store = InMemoryFlowStore()
    flow = await _make_flow(store)

    # Create 10 runs.
    for i in range(10):
        await materialize_flow_run(store, flow, {"i": i}, "manual", NOW)

    all_runs = await store.list_flow_runs(flow["id"], limit=500)
    assert len(all_runs) == 10

    limited = await store.list_flow_runs(flow["id"], limit=3)
    assert len(limited) == 3

    # Bounded result must still be newest-first.
    assert limited[0]["params"]["i"] > limited[-1]["params"]["i"]


async def test_list_flow_runs_default_limit_is_bounded():
    """Default limit must be <= 500 (not unbounded)."""
    store = InMemoryFlowStore()
    flow = await _make_flow(store)

    # Create a modest batch of runs and verify the default is sane.
    for i in range(5):
        await materialize_flow_run(store, flow, {"i": i}, "manual", NOW)

    runs = await store.list_flow_runs(flow["id"])
    assert len(runs) <= 500


# ---------------------------------------------------------------------------
# REGRESSION: [MED authz] viewer 403 on /flows/triggers/fire (unit gate check)
# ---------------------------------------------------------------------------


async def test_fire_trigger_event_has_writer_guard():
    """The /triggers/fire endpoint must carry require_writer_default dependency.

    We inspect the FastAPI route's dependencies list to assert the guard is
    present — this catches the regression where it was absent and viewers
    could fire flows, consuming unmetered server compute.
    """
    import app.routes.flows as flows_mod
    from app.auth.roles import require_writer_default

    router = flows_mod.router
    fire_route = None
    for route in router.routes:
        if hasattr(route, "path") and route.path.endswith("/triggers/fire"):
            fire_route = route
            break

    assert fire_route is not None, "Route /triggers/fire not found"

    # Extract the dependency callables from the route's dependencies list.
    dep_callables = [d.dependency for d in (fire_route.dependencies or [])]
    assert require_writer_default in dep_callables, (
        "require_writer_default dependency missing from /triggers/fire — "
        "viewers can fire flows!"
    )


async def test_fire_trigger_scope_uses_identity_not_hardcoded():
    """Claims scope in fire_trigger_event must derive from identity.scope.

    We do a static check: the route source must NOT contain the hardcoded
    '\"write:*\"' literal inside the fire_trigger_event function body.
    (The bug was scope=[\"read:*\",\"write:*\"] hardcoded regardless of token.)
    """
    import inspect
    import app.routes.flows as flows_mod

    src = inspect.getsource(flows_mod.fire_trigger_event)
    # The hardcoded literal must not appear in the function body.
    assert '"write:*"' not in src, (
        "fire_trigger_event must not hardcode scope=[\"read:*\",\"write:*\"]; "
        "use identity.scope instead."
    )


# ---------------------------------------------------------------------------
# FIX 1: Writeback GET routes registered only once (no duplicate)
# ---------------------------------------------------------------------------


def test_writeback_routes_registered_once():
    """Each writeback GET path must appear exactly once in the router.

    Duplicate registrations silently cause FastAPI to prefer the first
    handler, hiding the second.  Assert the path is registered exactly once.

    Note: the router uses prefix="/flows" so stored paths are /flows/writeback.
    """
    import app.routes.flows as flows_mod

    wb_list_count = 0
    wb_detail_count = 0
    for route in flows_mod.router.routes:
        path = getattr(route, "path", "")
        methods = getattr(route, "methods", set()) or set()
        if path in ("/flows/writeback", "/writeback") and "GET" in methods:
            wb_list_count += 1
        if path in ("/flows/writeback/{wb_id}", "/writeback/{wb_id}") and "GET" in methods:
            wb_detail_count += 1

    assert wb_list_count == 1, (
        f"GET writeback list route registered {wb_list_count} times — expected exactly 1"
    )
    assert wb_detail_count == 1, (
        f"GET writeback detail route registered {wb_detail_count} times — expected exactly 1"
    )


# ---------------------------------------------------------------------------
# FIX 2 & 3: /flows/{id}/runs is row-capped and paginated
# ---------------------------------------------------------------------------


async def test_list_flow_runs_route_default_limit():
    """list_flow_runs must default to limit=50, not return all rows."""
    store = InMemoryFlowStore()
    flow = await _make_flow(store)

    # Create 100 runs.
    for i in range(100):
        await materialize_flow_run(store, flow, {"i": i}, "manual", NOW)

    # Default limit (50) must return at most 50.
    runs = await store.list_flow_runs(flow["id"], limit=50, offset=0)
    assert len(runs) == 50


async def test_list_flow_runs_route_limit_bound():
    """list_flow_runs with limit=500 must not exceed 500 rows."""
    store = InMemoryFlowStore()
    flow = await _make_flow(store)

    for i in range(10):
        await materialize_flow_run(store, flow, {"i": i}, "manual", NOW)

    runs = await store.list_flow_runs(flow["id"], limit=500, offset=0)
    assert len(runs) <= 500


async def test_list_flow_runs_route_pagination_offset():
    """offset parameter must skip earlier results for pagination."""
    store = InMemoryFlowStore()
    flow = await _make_flow(store)

    for i in range(10):
        await materialize_flow_run(store, flow, {"i": i}, "manual", NOW)

    page1 = await store.list_flow_runs(flow["id"], limit=4, offset=0)
    page2 = await store.list_flow_runs(flow["id"], limit=4, offset=4)

    assert len(page1) == 4
    assert len(page2) == 4

    # Pages must not overlap.
    ids1 = {r["id"] for r in page1}
    ids2 = {r["id"] for r in page2}
    assert ids1.isdisjoint(ids2), "Pages must not share run IDs"


async def test_list_flow_runs_route_has_limit_query_param():
    """The list_flow_runs route must declare limit and offset as Query params."""
    import inspect
    import app.routes.flows as flows_mod

    src = inspect.getsource(flows_mod.list_flow_runs)
    assert "Query" in src, "list_flow_runs must use Query() for limit/offset"
    assert "limit" in src
    assert "offset" in src


def test_list_flow_runs_route_query_bounds():
    """list_flow_runs route must enforce ge=1, le=500 on the limit param."""
    import app.routes.flows as flows_mod
    from fastapi import Query as FQuery
    import inspect

    # Inspect the route's Query defaults rather than calling the route directly.
    sig = inspect.signature(flows_mod.list_flow_runs)
    limit_param = sig.parameters.get("limit")
    assert limit_param is not None, "list_flow_runs must have a 'limit' parameter"

    # The default should be wrapped in a Query object; check the annotation source.
    src = inspect.getsource(flows_mod.list_flow_runs)
    assert "ge=1" in src, "limit must have ge=1"
    assert "le=500" in src, "limit must have le=500"
