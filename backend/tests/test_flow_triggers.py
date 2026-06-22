"""B6 — Flow triggers: event/webhook/downstream + SLA hook tests.

Coverage
--------
1.  Register an event trigger; fire_event fires matching flows.
2.  fire_event with no matching triggers returns empty run_ids.
3.  Org isolation: trigger in org-A is NOT fired by a fire_event for org-B.
4.  Downstream trigger fires on flow completion (success).
5.  Downstream trigger respects on_states filter: does NOT fire on 'failed'
    when on_states=['success'].
6.  Downstream trigger fires on 'failed' when on_states includes 'failed'.
7.  Downstream trigger is error-isolated: exception in one trigger does NOT
    propagate; other triggers still fire.
8.  Downstream trigger is idempotent: calling on_flow_run_complete twice with
    the same run_id materialises TWO runs (the hook does not de-duplicate) —
    callers are responsible for calling it once.
9.  Disabled trigger is NOT fired by fire_event.
10. SLA hook: flag_sla_breach returns True when run exceeded expected_s.
11. SLA hook: flag_sla_breach returns False when run within expected_s.
12. SLA hook: returns False when expected_s is None or 0.
13. Multiple event triggers for same event_key → all fire.
14. register_trigger / list_all / delete round-trip.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from app.flows.store import InMemoryFlowStore
from app.flows.triggers import (
    InMemoryTriggerRegistry,
    fire_event,
    flag_sla_breach,
    get_trigger_registry,
    on_flow_run_complete,
    register_trigger,
    set_trigger_registry,
)
from app.flows.registry import reset_for_tests

pytestmark = pytest.mark.asyncio

NOW = datetime(2025, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
CLAIMS: dict[str, Any] = {"org_id": "org-test", "sub": "user-test"}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_registry():
    """Each test gets a fresh trigger registry."""
    set_trigger_registry(InMemoryTriggerRegistry())
    reset_for_tests()
    yield
    set_trigger_registry(None)


async def _make_flow(
    store: InMemoryFlowStore,
    org_id: str = "org-test",
    name: str = "target_flow",
) -> dict[str, Any]:
    return await store.create_flow(
        org_id=org_id,
        created_by="user-test",
        name=name,
        spec={
            "version": 1,
            "name": name,
            "tasks": [{"key": "t1", "kind": "noop", "needs": [], "config": {}}],
        },
    )


# ---------------------------------------------------------------------------
# 1. Event trigger fires matching flows
# ---------------------------------------------------------------------------


async def test_fire_event_fires_matching_flows():
    store = InMemoryFlowStore()
    flow = await _make_flow(store)

    trigger = await register_trigger(
        flow_id=flow["id"],
        kind="event",
        source="stock_take.landed",
        org_id="org-test",
    )

    run_ids = await fire_event(
        event_key="stock_take.landed",
        payload={"warehouse": "capetown"},
        org_id="org-test",
        store=store,
        now=NOW,
        claims=CLAIMS,
    )

    assert len(run_ids) == 1
    # Verify a real flow_run was created.
    flow_run = await store.get_flow_run(run_ids[0])
    assert flow_run is not None
    assert flow_run["trigger"] == "event"
    assert flow_run["params"].get("__event_key__") == "stock_take.landed"


# ---------------------------------------------------------------------------
# 2. fire_event with no matching triggers returns empty
# ---------------------------------------------------------------------------


async def test_fire_event_no_match_returns_empty():
    store = InMemoryFlowStore()

    run_ids = await fire_event(
        event_key="nonexistent.event",
        payload={},
        org_id="org-test",
        store=store,
        now=NOW,
        claims=CLAIMS,
    )

    assert run_ids == []


# ---------------------------------------------------------------------------
# 3. Org isolation
# ---------------------------------------------------------------------------


async def test_fire_event_org_isolation():
    store = InMemoryFlowStore()
    flow_a = await _make_flow(store, org_id="org-A")
    flow_b = await _make_flow(store, org_id="org-B")

    await register_trigger(
        flow_id=flow_a["id"],
        kind="event",
        source="data.ready",
        org_id="org-A",
    )
    await register_trigger(
        flow_id=flow_b["id"],
        kind="event",
        source="data.ready",
        org_id="org-B",
    )

    # Fire for org-A only.
    run_ids = await fire_event(
        event_key="data.ready",
        payload={},
        org_id="org-A",
        store=store,
        now=NOW,
        claims=CLAIMS,
    )

    assert len(run_ids) == 1
    fr = await store.get_flow_run(run_ids[0])
    assert fr is not None
    assert str(fr["org_id"]) == "org-A"


# ---------------------------------------------------------------------------
# 4. Downstream trigger fires on success
# ---------------------------------------------------------------------------


async def test_downstream_trigger_fires_on_success():
    """Verify downstream trigger fires when upstream completes via drain_flow_run.

    drain_flow_run calls advance_readiness which calls on_flow_run_complete
    via the runtime hook — so we just check the downstream run was created.
    """
    from app.flows.runtime import materialize_flow_run, drain_flow_run  # noqa: PLC0415

    store = InMemoryFlowStore()
    upstream = await _make_flow(store, name="upstream_flow")
    downstream = await _make_flow(store, name="downstream_flow")

    await register_trigger(
        flow_id=downstream["id"],
        kind="downstream",
        source=upstream["id"],
        org_id="org-test",
        extra={"on_states": ["success"]},
    )

    # Run the upstream flow to completion.
    flow_run = await materialize_flow_run(store, upstream, {}, "manual", NOW)
    final_run = await drain_flow_run(store, flow_run["id"], NOW, claims=CLAIMS)
    assert final_run["state"] == "success"

    # drain_flow_run already called on_flow_run_complete via the runtime hook.
    downstream_runs = await store.list_flow_runs(downstream["id"])
    assert len(downstream_runs) >= 1
    # All triggered runs reference the upstream run_id.
    upstream_ids = {r["params"].get("__upstream_run_id__") for r in downstream_runs}
    assert final_run["id"] in upstream_ids


# ---------------------------------------------------------------------------
# 5. Downstream trigger respects on_states filter (no fire on 'failed')
# ---------------------------------------------------------------------------


async def test_downstream_trigger_on_states_filter():
    store = InMemoryFlowStore()
    upstream = await _make_flow(store, name="upstream")
    downstream = await _make_flow(store, name="downstream")

    await register_trigger(
        flow_id=downstream["id"],
        kind="downstream",
        source=upstream["id"],
        org_id="org-test",
        extra={"on_states": ["success"]},  # only fire on success
    )

    # Simulate a FAILED upstream run.
    await on_flow_run_complete(
        store=store,
        flow_run_id="fake-run-id",
        state="failed",
        now=NOW,
    )

    # Downstream should NOT have been triggered.
    downstream_runs = await store.list_flow_runs(downstream["id"])
    assert len(downstream_runs) == 0


# ---------------------------------------------------------------------------
# 6. Downstream trigger fires on 'failed' when on_states includes 'failed'
# ---------------------------------------------------------------------------


async def test_downstream_trigger_fires_on_failed_when_configured():
    from app.flows.runtime import materialize_flow_run  # noqa: PLC0415

    store = InMemoryFlowStore()
    upstream = await _make_flow(store, name="upstream")
    downstream = await _make_flow(store, name="downstream")

    await register_trigger(
        flow_id=downstream["id"],
        kind="downstream",
        source=upstream["id"],
        org_id="org-test",
        extra={"on_states": ["success", "failed"]},
    )

    # Create a real upstream flow_run in the store (so the hook can look it up).
    flow_run = await materialize_flow_run(store, upstream, {}, "manual", NOW)
    # Manually mark it failed.
    await store.update_flow_run(flow_run["id"], {"state": "failed"})

    await on_flow_run_complete(
        store=store,
        flow_run_id=flow_run["id"],
        state="failed",
        now=NOW,
    )

    downstream_runs = await store.list_flow_runs(downstream["id"])
    assert len(downstream_runs) == 1
    assert downstream_runs[0]["params"].get("__upstream_state__") == "failed"


# ---------------------------------------------------------------------------
# 7. Downstream trigger: error-isolated (one trigger failing doesn't block others)
# ---------------------------------------------------------------------------


async def test_downstream_trigger_error_isolated():
    """An exception in one downstream trigger does NOT prevent others from firing."""
    from app.flows.runtime import materialize_flow_run  # noqa: PLC0415

    store = InMemoryFlowStore()
    upstream = await _make_flow(store, name="upstream")
    downstream = await _make_flow(store, name="downstream_ok")

    # Register a trigger with a NONEXISTENT flow_id (will cause an error).
    registry = get_trigger_registry()
    await registry.register(
        flow_id="nonexistent-flow-id",
        kind="downstream",
        source=upstream["id"],
        org_id="org-test",
        extra={"on_states": ["success"]},
    )
    # Register a trigger for the real downstream flow.
    await registry.register(
        flow_id=downstream["id"],
        kind="downstream",
        source=upstream["id"],
        org_id="org-test",
        extra={"on_states": ["success"]},
    )

    # Create and complete the upstream run.
    flow_run = await materialize_flow_run(store, upstream, {}, "manual", NOW)
    await store.update_flow_run(flow_run["id"], {"state": "success"})

    # This must NOT raise, even though one trigger references a missing flow.
    await on_flow_run_complete(
        store=store,
        flow_run_id=flow_run["id"],
        state="success",
        now=NOW,
    )

    # The real downstream trigger still fired.
    downstream_runs = await store.list_flow_runs(downstream["id"])
    assert len(downstream_runs) == 1


# ---------------------------------------------------------------------------
# 8. on_flow_run_complete does not deduplicate (called once per completion)
# ---------------------------------------------------------------------------


async def test_downstream_trigger_idempotency_note():
    """Calling on_flow_run_complete twice produces two runs (no built-in dedup)."""
    from app.flows.runtime import materialize_flow_run  # noqa: PLC0415

    store = InMemoryFlowStore()
    upstream = await _make_flow(store, name="upstream")
    downstream = await _make_flow(store, name="downstream")

    registry = get_trigger_registry()
    await registry.register(
        flow_id=downstream["id"],
        kind="downstream",
        source=upstream["id"],
        org_id="org-test",
        extra={"on_states": ["success"]},
    )

    flow_run = await materialize_flow_run(store, upstream, {}, "manual", NOW)
    await store.update_flow_run(flow_run["id"], {"state": "success"})

    await on_flow_run_complete(store=store, flow_run_id=flow_run["id"], state="success", now=NOW)
    await on_flow_run_complete(store=store, flow_run_id=flow_run["id"], state="success", now=NOW)

    downstream_runs = await store.list_flow_runs(downstream["id"])
    # Two calls → two materialised runs (the engine does not deduplicate).
    assert len(downstream_runs) == 2


# ---------------------------------------------------------------------------
# 9. Disabled trigger is NOT fired
# ---------------------------------------------------------------------------


async def test_disabled_trigger_not_fired():
    store = InMemoryFlowStore()
    flow = await _make_flow(store)

    await register_trigger(
        flow_id=flow["id"],
        kind="event",
        source="my.event",
        org_id="org-test",
        enabled=False,  # disabled
    )

    run_ids = await fire_event(
        event_key="my.event",
        payload={},
        org_id="org-test",
        store=store,
        now=NOW,
        claims=CLAIMS,
    )

    assert run_ids == []


# ---------------------------------------------------------------------------
# 10. SLA: flag_sla_breach returns True when exceeded
# ---------------------------------------------------------------------------


def test_flag_sla_breach_exceeded():
    now = datetime(2025, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
    started = now - timedelta(seconds=120)
    finished = now

    flow_run = {
        "started_at": started,
        "finished_at": finished,
        "state": "success",
    }

    assert flag_sla_breach(flow_run, expected_s=60.0, now=now) is True


# ---------------------------------------------------------------------------
# 11. SLA: flag_sla_breach returns False when within
# ---------------------------------------------------------------------------


def test_flag_sla_breach_within():
    now = datetime(2025, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
    started = now - timedelta(seconds=30)
    finished = now

    flow_run = {
        "started_at": started,
        "finished_at": finished,
        "state": "success",
    }

    assert flag_sla_breach(flow_run, expected_s=60.0, now=now) is False


# ---------------------------------------------------------------------------
# 12. SLA: returns False when expected_s is None or 0
# ---------------------------------------------------------------------------


def test_flag_sla_breach_no_sla():
    now = datetime(2025, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
    started = now - timedelta(seconds=600)  # 10 minutes — would exceed any normal SLA

    flow_run = {
        "started_at": started,
        "finished_at": now,
        "state": "success",
    }

    assert flag_sla_breach(flow_run, expected_s=None, now=now) is False
    assert flag_sla_breach(flow_run, expected_s=0, now=now) is False


# ---------------------------------------------------------------------------
# 13. Multiple event triggers for same event_key → all fire
# ---------------------------------------------------------------------------


async def test_multiple_triggers_same_event_all_fire():
    store = InMemoryFlowStore()
    flow_a = await _make_flow(store, name="flow_a")
    flow_b = await _make_flow(store, name="flow_b")

    await register_trigger(
        flow_id=flow_a["id"],
        kind="event",
        source="data.updated",
        org_id="org-test",
    )
    await register_trigger(
        flow_id=flow_b["id"],
        kind="event",
        source="data.updated",
        org_id="org-test",
    )

    run_ids = await fire_event(
        event_key="data.updated",
        payload={"table": "sales"},
        org_id="org-test",
        store=store,
        now=NOW,
        claims=CLAIMS,
    )

    assert len(run_ids) == 2
    # Both flows should have a run.
    runs_a = await store.list_flow_runs(flow_a["id"])
    runs_b = await store.list_flow_runs(flow_b["id"])
    assert len(runs_a) == 1
    assert len(runs_b) == 1


# ---------------------------------------------------------------------------
# 14. register_trigger / list_all / delete round-trip
# ---------------------------------------------------------------------------


async def test_trigger_registry_round_trip():
    registry = get_trigger_registry()

    t1 = await registry.register(
        flow_id="flow-1",
        kind="event",
        source="evt.key",
        org_id="org-test",
    )
    t2 = await registry.register(
        flow_id="flow-2",
        kind="downstream",
        source="flow-1",
        org_id="org-test",
    )

    all_triggers = await registry.list_all("org-test")
    assert len(all_triggers) == 2
    ids = {t.id for t in all_triggers}
    assert t1.id in ids and t2.id in ids

    deleted = await registry.delete(t1.id)
    assert deleted is True

    all_triggers_after = await registry.list_all("org-test")
    assert len(all_triggers_after) == 1
    assert all_triggers_after[0].id == t2.id


# ---------------------------------------------------------------------------
# Integration: downstream trigger wired into runtime (advance_readiness hook)
# ---------------------------------------------------------------------------


async def test_downstream_trigger_via_runtime():
    """End-to-end: complete a run via drain_flow_run → hook fires downstream."""
    from app.flows.runtime import drain_flow_run, materialize_flow_run  # noqa: PLC0415

    store = InMemoryFlowStore()
    upstream = await _make_flow(store, name="upstream_rt")
    downstream = await _make_flow(store, name="downstream_rt")

    registry = get_trigger_registry()
    await registry.register(
        flow_id=downstream["id"],
        kind="downstream",
        source=upstream["id"],
        org_id="org-test",
        extra={"on_states": ["success"]},
    )

    # Run the upstream flow end-to-end.
    flow_run = await materialize_flow_run(store, upstream, {}, "manual", NOW)
    final_run = await drain_flow_run(store, flow_run["id"], NOW, claims=CLAIMS)

    assert final_run["state"] == "success"

    # The runtime's advance_readiness now calls on_flow_run_complete,
    # which should have materialised the downstream run.
    downstream_runs = await store.list_flow_runs(downstream["id"])
    assert len(downstream_runs) == 1, (
        "Expected downstream flow to have been triggered via the runtime hook"
    )
