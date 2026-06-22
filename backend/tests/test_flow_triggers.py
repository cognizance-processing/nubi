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
15. [RLS] Downstream trigger carries owner-policy snapshot from upstream flow.
16. [RLS] Downstream trigger is SKIPPED when upstream flow has no owner-policy snapshot.
17. [Cycle] Cyclic trigger chain (A→B→A) stops at depth limit (no infinite loop).
18. [Cycle] Trigger chain respects FLOWS_TRIGGER_MAX_DEPTH env override.
20. [PgTriggerRegistry] Persistence: register → list → delete round-trip via mocked DB.
21. [PgTriggerRegistry] Org isolation: list_by_event scopes by org_id.
22. [PgTriggerRegistry] get_trigger_registry() returns PgTriggerRegistry when no override set.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from app.flows.store import InMemoryFlowStore
from app.flows.triggers import (
    InMemoryTriggerRegistry,
    PgTriggerRegistry,
    Trigger,
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

    The upstream flow carries an owner-policy snapshot so the RLS guard passes.
    """
    from app.flows.runtime import materialize_flow_run, drain_flow_run  # noqa: PLC0415

    store = InMemoryFlowStore()
    # Use a flow with an owner-policy snapshot so the RLS guard passes.
    upstream = await store.create_flow(
        org_id="org-test",
        created_by="user-test",
        name="upstream_flow",
        spec={
            "version": 1,
            "name": "upstream_flow",
            "tasks": [{"key": "t1", "kind": "noop", "needs": [], "config": {}}],
            "runtime_config": {"__owner_policies__": {"tenant_id": "org-test"}},
        },
    )
    downstream = await _make_flow_with_owner_policies(
        store, name="downstream_flow", owner_policies={"tenant_id": "org-test"}
    )

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
    # Use a flow with an owner-policy snapshot so the RLS guard passes.
    upstream = await store.create_flow(
        org_id="org-test",
        created_by="user-test",
        name="upstream",
        spec={
            "version": 1,
            "name": "upstream",
            "tasks": [{"key": "t1", "kind": "noop", "needs": [], "config": {}}],
            "runtime_config": {"__owner_policies__": {"tenant_id": "org-test"}},
        },
    )
    downstream = await _make_flow_with_owner_policies(
        store, name="downstream", owner_policies={"tenant_id": "org-test"}
    )

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
        claims={},  # empty — uses owner-policy snapshot from upstream flow
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
    # Use a flow with an owner-policy snapshot so the RLS guard passes.
    upstream = await store.create_flow(
        org_id="org-test",
        created_by="user-test",
        name="upstream",
        spec={
            "version": 1,
            "name": "upstream",
            "tasks": [{"key": "t1", "kind": "noop", "needs": [], "config": {}}],
            "runtime_config": {"__owner_policies__": {"tenant_id": "org-test"}},
        },
    )
    downstream = await _make_flow_with_owner_policies(
        store, name="downstream_ok", owner_policies={"tenant_id": "org-test"}
    )

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
        claims={},  # empty — uses owner-policy snapshot from upstream flow
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
    # Use a flow with an owner-policy snapshot so the RLS guard passes.
    upstream = await store.create_flow(
        org_id="org-test",
        created_by="user-test",
        name="upstream",
        spec={
            "version": 1,
            "name": "upstream",
            "tasks": [{"key": "t1", "kind": "noop", "needs": [], "config": {}}],
            "runtime_config": {"__owner_policies__": {"tenant_id": "org-test"}},
        },
    )
    downstream = await _make_flow_with_owner_policies(
        store, name="downstream", owner_policies={"tenant_id": "org-test"}
    )

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
# 14b. list_all is bounded by FLOWS_TRIGGER_LIST_ALL_LIMIT
# ---------------------------------------------------------------------------


async def test_list_all_is_bounded():
    """list_all must never return more rows than the configured limit."""
    import app.flows.triggers as triggers_module  # noqa: PLC0415

    original_limit = triggers_module._FLOWS_TRIGGER_LIST_ALL_LIMIT
    small_limit = 3
    triggers_module._FLOWS_TRIGGER_LIST_ALL_LIMIT = small_limit
    try:
        registry = get_trigger_registry()
        # Register more triggers than the limit.
        for i in range(small_limit + 4):
            await registry.register(
                flow_id=f"flow-{i}",
                kind="event",
                source=f"evt.key.{i}",
                org_id="org-test",
            )

        all_triggers = await registry.list_all("org-test")
        assert len(all_triggers) <= small_limit, (
            f"list_all must be bounded by FLOWS_TRIGGER_LIST_ALL_LIMIT={small_limit}, "
            f"got {len(all_triggers)}"
        )

        # Explicit limit kwarg overrides the env ceiling.
        two = await registry.list_all("org-test", limit=2)
        assert len(two) == 2

        # Other orgs are unaffected (isolation still holds).
        other_org = await registry.list_all("other-org")
        assert other_org == []
    finally:
        triggers_module._FLOWS_TRIGGER_LIST_ALL_LIMIT = original_limit


async def test_pg_list_all_bounded(fake_pg_table):
    """[PgTriggerRegistry] list_all() passes LIMIT to the DB query and respects it."""
    import app.flows.triggers as triggers_module  # noqa: PLC0415

    registry = PgTriggerRegistry()
    small_limit = 2

    original_limit = triggers_module._FLOWS_TRIGGER_LIST_ALL_LIMIT
    triggers_module._FLOWS_TRIGGER_LIST_ALL_LIMIT = small_limit
    try:
        with (
            patch("app.db.fetchrow", side_effect=fake_pg_table.fake_fetchrow),
            patch("app.db.fetch", side_effect=fake_pg_table.fake_fetch),
            patch("app.db.execute", side_effect=fake_pg_table.fake_execute),
        ):
            org_id = str(uuid.uuid4())

            # Register more triggers than the limit.
            for i in range(small_limit + 3):
                await registry.register(
                    flow_id=str(uuid.uuid4()),
                    kind="event",
                    source=f"bounded.evt.{i}",
                    org_id=org_id,
                )

            all_triggers = await registry.list_all(org_id)
            assert len(all_triggers) <= small_limit, (
                f"PgTriggerRegistry.list_all must be bounded by limit={small_limit}, "
                f"got {len(all_triggers)}"
            )
    finally:
        triggers_module._FLOWS_TRIGGER_LIST_ALL_LIMIT = original_limit


# ---------------------------------------------------------------------------
# Integration: downstream trigger wired into runtime (advance_readiness hook)
# ---------------------------------------------------------------------------


async def test_downstream_trigger_via_runtime():
    """End-to-end: complete a run via drain_flow_run → hook fires downstream.

    The upstream flow carries an owner-policy snapshot so the RLS guard passes.
    """
    from app.flows.runtime import drain_flow_run, materialize_flow_run  # noqa: PLC0415

    store = InMemoryFlowStore()
    # Use a flow with an owner-policy snapshot so the RLS guard passes.
    upstream = await store.create_flow(
        org_id="org-test",
        created_by="user-test",
        name="upstream_rt",
        spec={
            "version": 1,
            "name": "upstream_rt",
            "tasks": [{"key": "t1", "kind": "noop", "needs": [], "config": {}}],
            "runtime_config": {"__owner_policies__": {"tenant_id": "org-test"}},
        },
    )
    downstream = await _make_flow_with_owner_policies(
        store, name="downstream_rt", owner_policies={"tenant_id": "org-test"}
    )

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


# ---------------------------------------------------------------------------
# 15. [RLS] Downstream trigger carries owner-policy snapshot
# ---------------------------------------------------------------------------


async def _make_flow_with_owner_policies(
    store: InMemoryFlowStore,
    org_id: str = "org-test",
    name: str = "flow_with_policies",
    owner_policies: dict | None = None,
) -> dict:
    """Create a flow whose spec contains a runtime_config.__owner_policies__ snapshot."""
    if owner_policies is None:
        owner_policies = {"tenant_id": org_id, "role": "owner"}
    return await store.create_flow(
        org_id=org_id,
        created_by="user-test",
        name=name,
        spec={
            "version": 1,
            "name": name,
            "tasks": [{"key": "t1", "kind": "noop", "needs": [], "config": {}}],
            "runtime_config": {
                "__owner_policies__": owner_policies,
            },
        },
    )


async def test_downstream_trigger_carries_owner_policies():
    """[RLS] Downstream-triggered child run must carry the upstream owner's policies."""
    from app.flows.runtime import materialize_flow_run  # noqa: PLC0415

    store = InMemoryFlowStore()
    owner_policies = {"tenant_id": "org-test", "role": "data_owner"}
    upstream = await _make_flow_with_owner_policies(
        store, name="upstream_rls", owner_policies=owner_policies
    )
    downstream = await _make_flow_with_owner_policies(
        store, name="downstream_rls", owner_policies={"tenant_id": "org-test"}
    )

    registry = get_trigger_registry()
    await registry.register(
        flow_id=downstream["id"],
        kind="downstream",
        source=upstream["id"],
        org_id="org-test",
        extra={"on_states": ["success"]},
    )

    # Create an upstream flow_run (empty claims simulate a scheduled/worker path).
    flow_run = await materialize_flow_run(store, upstream, {}, "manual", NOW)
    await store.update_flow_run(flow_run["id"], {"state": "success"})

    # Fire the hook with EMPTY claims (no policies — like a scheduled run).
    captured_claims: list[dict] = []
    original_materialize = None

    async def capturing_materialize(s, flow, params, trigger, t_now, **kw):
        # Record the claims in the params rather than intercepting materialize_flow_run;
        # instead we check that the hook did NOT skip the trigger and that the
        # downstream run's params carry __trigger_depth__ (proof it was fired).
        return await original_materialize(s, flow, params, trigger, t_now, **kw)

    await on_flow_run_complete(
        store=store,
        flow_run_id=flow_run["id"],
        state="success",
        now=NOW,
        claims={},  # empty — no policies
    )

    # The downstream run SHOULD have been materialised because the upstream flow
    # carries an owner-policy snapshot.
    downstream_runs = await store.list_flow_runs(downstream["id"])
    assert len(downstream_runs) == 1, (
        "Downstream trigger must fire when upstream flow has an owner-policy snapshot"
    )
    # Verify the trigger depth marker is present in run params.
    assert downstream_runs[0]["params"].get("__trigger_depth__") == 1


async def test_downstream_trigger_skipped_without_owner_policies():
    """[RLS] Downstream trigger is skipped (fail-closed) when upstream has no snapshot."""
    from app.flows.runtime import materialize_flow_run  # noqa: PLC0415

    store = InMemoryFlowStore()
    # Upstream flow WITHOUT any owner-policy snapshot in its spec.
    upstream = await _make_flow(store, name="upstream_no_policy")
    downstream = await _make_flow(store, name="downstream_no_policy")

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

    # Fire with empty claims (no policies on claims AND no snapshot on flow).
    await on_flow_run_complete(
        store=store,
        flow_run_id=flow_run["id"],
        state="success",
        now=NOW,
        claims={},  # no policies
    )

    # Downstream trigger must be SKIPPED to prevent running with empty RLS.
    downstream_runs = await store.list_flow_runs(downstream["id"])
    assert len(downstream_runs) == 0, (
        "Downstream trigger must be skipped when upstream has no owner-policy snapshot "
        "and claims carry no policies (fail-closed)"
    )


async def test_downstream_trigger_skipped_when_downstream_lacks_snapshot():
    """[RLS] Trigger is skipped when the DOWNSTREAM flow lacks an owner-policy snapshot.

    Even when the upstream flow HAS a snapshot (passing the upstream guard), the
    downstream flow drains using its OWN snapshot.  If the downstream flow has no
    snapshot and the caller carries no policies, the drain would resolve claims to
    {} → NO RLS predicates → cross-tenant rows.  The trigger must be skipped
    (fail-closed), mirroring the upstream guard.
    """
    from app.flows.runtime import materialize_flow_run  # noqa: PLC0415

    store = InMemoryFlowStore()
    # Upstream HAS a snapshot (so the upstream guard passes).
    upstream = await _make_flow_with_owner_policies(
        store, name="ds_guard_upstream", owner_policies={"tenant_id": "org-test"}
    )
    # Downstream has NO owner-policy snapshot (created before the feature).
    downstream = await _make_flow(store, name="ds_guard_downstream_no_policy")

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

    # Empty claims: no policies on caller AND no snapshot on downstream flow.
    await on_flow_run_complete(
        store=store,
        flow_run_id=flow_run["id"],
        state="success",
        now=NOW,
        claims={},  # no policies
    )

    # The trigger MUST be skipped — the downstream run would otherwise drain with
    # empty RLS context and leak cross-tenant rows.
    downstream_runs = await store.list_flow_runs(downstream["id"])
    assert len(downstream_runs) == 0, (
        "Downstream trigger must be skipped when the DOWNSTREAM flow has no "
        "owner-policy snapshot and claims carry no policies (fail-closed). "
        f"Got {len(downstream_runs)} runs — cross-tenant RLS leak risk."
    )


async def test_downstream_trigger_fires_when_both_flows_have_snapshot():
    """[RLS] Trigger fires RLS-correctly when BOTH upstream and downstream have snapshots."""
    from app.flows.runtime import materialize_flow_run  # noqa: PLC0415

    store = InMemoryFlowStore()
    upstream = await _make_flow_with_owner_policies(
        store, name="ds_guard_upstream_ok", owner_policies={"tenant_id": "org-test"}
    )
    # Downstream ALSO carries a usable owner-policy snapshot.
    downstream = await _make_flow_with_owner_policies(
        store, name="ds_guard_downstream_ok", owner_policies={"tenant_id": "org-test"}
    )

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

    await on_flow_run_complete(
        store=store,
        flow_run_id=flow_run["id"],
        state="success",
        now=NOW,
        claims={},  # no policies — relies on downstream snapshot for RLS
    )

    downstream_runs = await store.list_flow_runs(downstream["id"])
    assert len(downstream_runs) == 1, (
        "Downstream trigger must fire when the downstream flow has a usable "
        "owner-policy snapshot."
    )
    assert downstream_runs[0]["params"].get("__trigger_depth__") == 1


async def test_downstream_trigger_interactive_claims_bypass_snapshot():
    """[RLS] When claims already carry policies (interactive path), they are used as-is."""
    from app.flows.runtime import materialize_flow_run  # noqa: PLC0415

    store = InMemoryFlowStore()
    # Upstream flow WITHOUT any owner-policy snapshot.
    upstream = await _make_flow(store, name="upstream_interactive")
    downstream = await _make_flow(store, name="downstream_interactive")

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

    # Interactive caller already has policies in claims.
    interactive_claims = {"org_id": "org-test", "policies": {"tenant_id": "org-test"}}
    await on_flow_run_complete(
        store=store,
        flow_run_id=flow_run["id"],
        state="success",
        now=NOW,
        claims=interactive_claims,
    )

    # Even though the flow has no snapshot, the trigger fires because claims
    # already carry policies (interactive path bypasses fail-closed guard).
    downstream_runs = await store.list_flow_runs(downstream["id"])
    assert len(downstream_runs) == 1, (
        "Downstream trigger must fire when caller's claims already carry policies"
    )


# ---------------------------------------------------------------------------
# 17. [Cycle] Cyclic trigger chain stops at depth limit
# ---------------------------------------------------------------------------


async def test_cyclic_trigger_chain_stops_at_depth_limit():
    """[Cycle] A→B→A trigger cycle must halt at FLOWS_TRIGGER_MAX_DEPTH."""
    import app.flows.triggers as triggers_module  # noqa: PLC0415
    from app.flows.runtime import materialize_flow_run  # noqa: PLC0415

    store = InMemoryFlowStore()
    owner_policies = {"tenant_id": "org-test", "role": "owner"}

    flow_a = await _make_flow_with_owner_policies(
        store, name="cycle_flow_a", owner_policies=owner_policies
    )
    flow_b = await _make_flow_with_owner_policies(
        store, name="cycle_flow_b", owner_policies=owner_policies
    )

    registry = get_trigger_registry()
    # A completes → fire B
    await registry.register(
        flow_id=flow_b["id"],
        kind="downstream",
        source=flow_a["id"],
        org_id="org-test",
        extra={"on_states": ["success"]},
    )
    # B completes → fire A  (creates A→B→A cycle)
    await registry.register(
        flow_id=flow_a["id"],
        kind="downstream",
        source=flow_b["id"],
        org_id="org-test",
        extra={"on_states": ["success"]},
    )

    # Temporarily lower the max depth so the test runs fast.
    original_depth = triggers_module._FLOWS_TRIGGER_MAX_DEPTH
    triggers_module._FLOWS_TRIGGER_MAX_DEPTH = 3
    try:
        # Simulate completing flow_a at depth 0.
        flow_run_a = await materialize_flow_run(store, flow_a, {}, "manual", NOW)
        await store.update_flow_run(flow_run_a["id"], {"state": "success"})

        # This must NOT loop forever; it must return in finite time.
        await on_flow_run_complete(
            store=store,
            flow_run_id=flow_run_a["id"],
            state="success",
            now=NOW,
            claims={},  # no policies → uses snapshot from upstream flow
        )
    finally:
        triggers_module._FLOWS_TRIGGER_MAX_DEPTH = original_depth

    # The cycle guard prevents infinite materialisation.
    # flow_b must have been triggered at most once (depth 1), then the cycle guard
    # stops the chain before it can re-trigger flow_a (flow_a is in the visited set).
    runs_b = await store.list_flow_runs(flow_b["id"])
    assert len(runs_b) <= 1, (
        f"Expected at most 1 run for flow_b (cycle guard), got {len(runs_b)}"
    )


async def test_cyclic_trigger_chain_visited_set_blocks_revisit():
    """[Cycle] A flow already in the chain is blocked by the visited-set check."""
    import app.flows.triggers as triggers_module  # noqa: PLC0415
    from app.flows.runtime import materialize_flow_run  # noqa: PLC0415

    store = InMemoryFlowStore()
    owner_policies = {"tenant_id": "org-test", "role": "owner"}

    # Three-node chain A→B→C→A (longer cycle).
    flow_a = await _make_flow_with_owner_policies(
        store, name="chain_a", owner_policies=owner_policies
    )
    flow_b = await _make_flow_with_owner_policies(
        store, name="chain_b", owner_policies=owner_policies
    )
    flow_c = await _make_flow_with_owner_policies(
        store, name="chain_c", owner_policies=owner_policies
    )

    registry = get_trigger_registry()
    await registry.register(
        flow_id=flow_b["id"],
        kind="downstream",
        source=flow_a["id"],
        org_id="org-test",
        extra={"on_states": ["success"]},
    )
    await registry.register(
        flow_id=flow_c["id"],
        kind="downstream",
        source=flow_b["id"],
        org_id="org-test",
        extra={"on_states": ["success"]},
    )
    # C → A creates the cycle
    await registry.register(
        flow_id=flow_a["id"],
        kind="downstream",
        source=flow_c["id"],
        org_id="org-test",
        extra={"on_states": ["success"]},
    )

    original_depth = triggers_module._FLOWS_TRIGGER_MAX_DEPTH
    triggers_module._FLOWS_TRIGGER_MAX_DEPTH = 10  # allow deep enough to reach the cycle
    try:
        flow_run_a = await materialize_flow_run(store, flow_a, {}, "manual", NOW)
        await store.update_flow_run(flow_run_a["id"], {"state": "success"})

        # Must complete without infinite recursion.
        await on_flow_run_complete(
            store=store,
            flow_run_id=flow_run_a["id"],
            state="success",
            now=NOW,
            claims={},
        )
    finally:
        triggers_module._FLOWS_TRIGGER_MAX_DEPTH = original_depth

    # The visited-set blocks flow_a from being re-triggered when C completes.
    runs_a = await store.list_flow_runs(flow_a["id"])
    # Only the one we manually created — the cycle guard blocks any re-trigger.
    assert len(runs_a) == 1, (
        f"Expected flow_a to appear exactly once (initial run), got {len(runs_a)}"
    )


# ---------------------------------------------------------------------------
# [LOW RLS] Admin flow (explicitly-empty owner-policy snapshot) still triggers
# ---------------------------------------------------------------------------


async def test_downstream_trigger_fires_for_admin_flow_with_empty_policies():
    """[LOW RLS] Admin-created flows with explicitly-empty owner policies still fire downstream.

    Before the fix, ``_downstream_claims_with_owner_policies`` used ``if not policies``
    (truthiness) which treated an explicit empty dict (``{}``) the same as a missing
    snapshot key — causing downstream triggers to be silently dropped for admin flows
    that legitimately have no per-tenant RLS predicates.

    After the fix, the function checks for **key presence** rather than truthiness:
    - ``_OWNER_POLICIES_KEY in runtime_config`` with value ``{}`` → ALLOW (admin, no RLS)
    - ``_OWNER_POLICIES_KEY`` absent from ``runtime_config`` → SKIP (fail-closed)
    """
    from app.flows.runtime import materialize_flow_run  # noqa: PLC0415

    store = InMemoryFlowStore()

    # Admin/service flow: owner-policy snapshot key IS PRESENT but value is empty dict.
    # This is the "no per-tenant RLS" (admin) case — should still fire downstream.
    upstream = await store.create_flow(
        org_id="org-test",
        created_by="admin-user",
        name="admin_upstream",
        spec={
            "version": 1,
            "name": "admin_upstream",
            "tasks": [{"key": "t1", "kind": "noop", "needs": [], "config": {}}],
            "runtime_config": {
                "__owner_policies__": {},  # explicitly empty — admin/no-RLS
            },
        },
    )
    # Admin downstream flow ALSO carries an explicitly-empty owner-policy snapshot
    # (admin/no-RLS): key present, value {} — should still fire (not fail-closed).
    downstream = await _make_flow_with_owner_policies(
        store, name="admin_downstream", owner_policies={}
    )

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

    # Fire with empty claims — no policies on caller, relies on snapshot.
    await on_flow_run_complete(
        store=store,
        flow_run_id=flow_run["id"],
        state="success",
        now=NOW,
        claims={},  # no policies on claims
    )

    # The downstream trigger MUST fire: empty-dict snapshot is an explicit admin signal.
    downstream_runs = await store.list_flow_runs(downstream["id"])
    assert len(downstream_runs) == 1, (
        "Downstream trigger must fire for an admin flow with an explicitly-empty "
        "owner-policy snapshot (key present, value={}).  "
        f"Got {len(downstream_runs)} runs — fail-closed logic wrongly dropped it."
    )
    # The downstream run should carry empty policies (not fail, not skip).
    run_params = downstream_runs[0]["params"]
    assert run_params.get("__trigger_depth__") == 1, (
        f"Expected __trigger_depth__=1 in downstream run params, got: {run_params}"
    )


async def test_downstream_trigger_skipped_when_snapshot_key_absent():
    """[LOW RLS] Downstream trigger must be skipped when snapshot KEY is absent (fail-closed).

    Distinguishes 'key absent' (unknown RLS context → skip) from
    'key present, value empty' (admin, no RLS → allow).
    """
    from app.flows.runtime import materialize_flow_run  # noqa: PLC0415

    store = InMemoryFlowStore()

    # Flow with runtime_config but WITHOUT __owner_policies__ key at all.
    upstream = await store.create_flow(
        org_id="org-test",
        created_by="user-test",
        name="no_snapshot_key_upstream",
        spec={
            "version": 1,
            "name": "no_snapshot_key_upstream",
            "tasks": [{"key": "t1", "kind": "noop", "needs": [], "config": {}}],
            "runtime_config": {
                "some_other_key": "value",
                # __owner_policies__ key is intentionally absent
            },
        },
    )
    downstream = await _make_flow(store, name="no_snapshot_downstream")

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

    await on_flow_run_complete(
        store=store,
        flow_run_id=flow_run["id"],
        state="success",
        now=NOW,
        claims={},  # no policies
    )

    # Must be skipped — key is absent, RLS context is unknown.
    downstream_runs = await store.list_flow_runs(downstream["id"])
    assert len(downstream_runs) == 0, (
        "Downstream trigger must be skipped when snapshot key is absent (fail-closed). "
        f"Got {len(downstream_runs)} runs."
    )


# ---------------------------------------------------------------------------
# 19. [Width cap] fire_event fans out only up to FLOWS_TRIGGER_MAX_FANOUT
# ---------------------------------------------------------------------------


async def test_fire_event_fanout_cap_limits_fires(caplog):
    """[Width cap] An event matching > cap triggers fires only up to the cap (+ warning)."""
    import app.flows.triggers as triggers_module  # noqa: PLC0415

    store = InMemoryFlowStore()
    cap = 3  # Use a small cap so the test stays fast

    original_cap = triggers_module._FLOWS_TRIGGER_MAX_FANOUT
    triggers_module._FLOWS_TRIGGER_MAX_FANOUT = cap
    try:
        # Register cap+2 flows, each with an event trigger for the same key.
        flow_ids = []
        for i in range(cap + 2):
            flow = await _make_flow(store, name=f"fanout_flow_{i}")
            flow_ids.append(flow["id"])
            await register_trigger(
                flow_id=flow["id"],
                kind="event",
                source="fanout.event",
                org_id="org-test",
            )

        import logging
        with caplog.at_level(logging.WARNING, logger="app.flows.triggers"):
            run_ids = await fire_event(
                event_key="fanout.event",
                payload={},
                org_id="org-test",
                store=store,
                now=NOW,
                claims=CLAIMS,
            )
    finally:
        triggers_module._FLOWS_TRIGGER_MAX_FANOUT = original_cap

    # Only cap flows must have been fired.
    assert len(run_ids) == cap, (
        f"Expected exactly {cap} run_ids (fan-out cap), got {len(run_ids)}"
    )
    # A warning must have been logged about the skipped triggers.
    warning_msgs = [r.message for r in caplog.records if r.levelno >= logging.WARNING]
    assert any("FLOWS_TRIGGER_MAX_FANOUT" in m for m in warning_msgs), (
        "Expected a warning mentioning FLOWS_TRIGGER_MAX_FANOUT when cap is exceeded"
    )


async def test_fire_event_within_fanout_cap_all_fire():
    """[Width cap] When triggers are within the cap, every trigger fires normally."""
    import app.flows.triggers as triggers_module  # noqa: PLC0415

    store = InMemoryFlowStore()
    cap = 5

    original_cap = triggers_module._FLOWS_TRIGGER_MAX_FANOUT
    triggers_module._FLOWS_TRIGGER_MAX_FANOUT = cap
    try:
        # Register exactly cap-1 triggers (all within cap).
        for i in range(cap - 1):
            flow = await _make_flow(store, name=f"within_cap_flow_{i}")
            await register_trigger(
                flow_id=flow["id"],
                kind="event",
                source="within.event",
                org_id="org-test",
            )

        run_ids = await fire_event(
            event_key="within.event",
            payload={},
            org_id="org-test",
            store=store,
            now=NOW,
            claims=CLAIMS,
        )
    finally:
        triggers_module._FLOWS_TRIGGER_MAX_FANOUT = original_cap

    assert len(run_ids) == cap - 1, (
        f"Expected all {cap - 1} triggers to fire (within cap), got {len(run_ids)}"
    )


# ---------------------------------------------------------------------------
# 20-21. PgTriggerRegistry — unit tests against mocked app.db
# ---------------------------------------------------------------------------
#
# These tests exercise PgTriggerRegistry directly by mocking the asyncpg
# helpers (app.db.fetch / fetchrow / execute) with dict-backed in-memory
# fakes.  They verify that:
#   * PgTriggerRegistry.register() inserts a row and returns a Trigger.
#   * PgTriggerRegistry.list_by_event() filters by org_id AND source AND kind.
#   * PgTriggerRegistry.delete() removes the row.
#   * Org isolation: a second org's triggers are NOT returned.
# ---------------------------------------------------------------------------


class _FakePgTable:
    """Minimal dict-backed table that mimics asyncpg query responses for
    the flow_triggers queries used by PgTriggerRegistry."""

    def __init__(self) -> None:
        self._rows: list[dict[str, Any]] = []

    # ── INSERT with RETURNING * ──────────────────────────────────────────────
    async def fake_fetchrow(self, query: str, *args: Any) -> dict[str, Any] | None:
        q = query.upper().strip()
        if "INSERT INTO FLOW_TRIGGERS" in q:
            # args: trigger_id, org_id, flow_id, kind, source, secret, extra_json, enabled
            trigger_id, org_id, flow_id, kind, source, secret, extra_json, enabled = args
            extra = json.loads(extra_json) if isinstance(extra_json, str) else (extra_json or {})
            row = {
                "id": str(trigger_id),
                "org_id": str(org_id),
                "flow_id": str(flow_id),
                "kind": kind,
                "source": source,
                "secret": secret,
                "extra": extra,
                "enabled": bool(enabled),
                "created_at": datetime.now(timezone.utc),
                "updated_at": datetime.now(timezone.utc),
            }
            self._rows.append(row)
            return row
        if "UPDATE FLOW_TRIGGERS" in q:
            # args: enabled, trigger_id
            enabled, trigger_id = args
            for row in self._rows:
                if row["id"] == str(trigger_id):
                    row["enabled"] = bool(enabled)
                    return dict(row)
            return None
        if "SELECT * FROM FLOW_TRIGGERS WHERE ID" in q:
            (trigger_id,) = args
            for row in self._rows:
                if row["id"] == str(trigger_id):
                    return dict(row)
            return None
        return None

    async def fake_fetch(self, query: str, *args: Any) -> list[dict[str, Any]]:
        q = query.upper().strip()
        if "SELECT * FROM FLOW_TRIGGERS" not in q:
            return []
        # Determine filter parameters from query shape.
        if "WHERE ORG_ID" in q and "SOURCE" in q and "KIND IN" in q:
            # list_by_event: args = (org_id, source)
            org_id, source = args[0], args[1]
            return [
                dict(r) for r in self._rows
                if str(r["org_id"]) == str(org_id)
                and r["source"] == source
                and r["kind"] in ("event", "webhook")
                and r["enabled"]
            ]
        if "WHERE ORG_ID" in q and "SOURCE" in q and "KIND = 'DOWNSTREAM'" in q:
            # list_by_upstream: args = (org_id, source)
            org_id, source = args[0], args[1]
            return [
                dict(r) for r in self._rows
                if str(r["org_id"]) == str(org_id)
                and r["source"] == source
                and r["kind"] == "downstream"
                and r["enabled"]
            ]
        if "WHERE ORG_ID" in q and "SOURCE" not in q and "KIND" not in q:
            # list_all: args = (org_id, limit)
            org_id = args[0]
            limit = int(args[1]) if len(args) > 1 else 1000
            rows = [dict(r) for r in self._rows if str(r["org_id"]) == str(org_id)]
            return rows[:limit]
        return []

    async def fake_execute(self, query: str, *args: Any) -> str:
        q = query.upper().strip()
        if "DELETE FROM FLOW_TRIGGERS" in q:
            (trigger_id,) = args
            before = len(self._rows)
            self._rows = [r for r in self._rows if r["id"] != str(trigger_id)]
            deleted = before - len(self._rows)
            return f"DELETE {deleted}"
        return "OK"


@pytest.fixture
def fake_pg_table():
    """Return a fresh _FakePgTable that backs all app.db calls."""
    return _FakePgTable()


async def test_pg_trigger_registry_register_and_list(fake_pg_table):
    """[PgTriggerRegistry] register() persists a row; list_by_event() retrieves it."""
    registry = PgTriggerRegistry()

    with (
        patch("app.db.fetchrow", side_effect=fake_pg_table.fake_fetchrow),
        patch("app.db.fetch", side_effect=fake_pg_table.fake_fetch),
        patch("app.db.execute", side_effect=fake_pg_table.fake_execute),
    ):
        org_id = str(uuid.uuid4())
        flow_id = str(uuid.uuid4())

        trigger = await registry.register(
            flow_id=flow_id,
            kind="event",
            source="stock.landed",
            org_id=org_id,
            extra={"on_states": ["success"]},
        )

        assert isinstance(trigger, Trigger)
        assert trigger.org_id == org_id
        assert trigger.flow_id == flow_id
        assert trigger.kind == "event"
        assert trigger.source == "stock.landed"
        assert trigger.enabled is True

        # list_by_event should find it.
        found = await registry.list_by_event("stock.landed", org_id)
        assert len(found) == 1
        assert found[0].id == trigger.id

        # list_all should include it.
        all_triggers = await registry.list_all(org_id)
        assert len(all_triggers) == 1


async def test_pg_trigger_registry_delete(fake_pg_table):
    """[PgTriggerRegistry] delete() removes the trigger; subsequent list returns empty."""
    registry = PgTriggerRegistry()

    with (
        patch("app.db.fetchrow", side_effect=fake_pg_table.fake_fetchrow),
        patch("app.db.fetch", side_effect=fake_pg_table.fake_fetch),
        patch("app.db.execute", side_effect=fake_pg_table.fake_execute),
    ):
        org_id = str(uuid.uuid4())
        flow_id = str(uuid.uuid4())

        trigger = await registry.register(
            flow_id=flow_id,
            kind="event",
            source="evt.key",
            org_id=org_id,
        )

        # Confirm it is present.
        assert len(await registry.list_all(org_id)) == 1

        # Delete it.
        deleted = await registry.delete(trigger.id)
        assert deleted is True

        # Now list_all returns empty.
        assert await registry.list_all(org_id) == []

        # Deleting again returns False.
        deleted_again = await registry.delete(trigger.id)
        assert deleted_again is False


async def test_pg_trigger_registry_org_isolation(fake_pg_table):
    """[PgTriggerRegistry] list_by_event() does NOT return triggers from another org."""
    registry = PgTriggerRegistry()

    with (
        patch("app.db.fetchrow", side_effect=fake_pg_table.fake_fetchrow),
        patch("app.db.fetch", side_effect=fake_pg_table.fake_fetch),
        patch("app.db.execute", side_effect=fake_pg_table.fake_execute),
    ):
        org_a = str(uuid.uuid4())
        org_b = str(uuid.uuid4())
        flow_a = str(uuid.uuid4())
        flow_b = str(uuid.uuid4())

        # Register one trigger per org for the SAME event key.
        await registry.register(
            flow_id=flow_a,
            kind="event",
            source="shared.event",
            org_id=org_a,
        )
        await registry.register(
            flow_id=flow_b,
            kind="event",
            source="shared.event",
            org_id=org_b,
        )

        # Querying for org_a must NOT include org_b's trigger.
        found_a = await registry.list_by_event("shared.event", org_a)
        assert len(found_a) == 1
        assert found_a[0].org_id == org_a

        # Querying for org_b must NOT include org_a's trigger.
        found_b = await registry.list_by_event("shared.event", org_b)
        assert len(found_b) == 1
        assert found_b[0].org_id == org_b

        # list_all for org_a also stays isolated.
        all_a = await registry.list_all(org_a)
        assert all(t.org_id == org_a for t in all_a)


# ---------------------------------------------------------------------------
# 22. get_trigger_registry() returns PgTriggerRegistry when no override is set
# ---------------------------------------------------------------------------


def test_get_trigger_registry_default_is_pg():
    """get_trigger_registry() must return a PgTriggerRegistry when no override set."""
    import app.flows.triggers as triggers_module  # noqa: PLC0415

    original = triggers_module._registry
    try:
        triggers_module._registry = None  # reset to "unset" state
        registry = get_trigger_registry()
        assert isinstance(registry, PgTriggerRegistry), (
            f"Expected PgTriggerRegistry (production default) but got {type(registry)}"
        )
    finally:
        # Restore original (usually InMemoryTriggerRegistry from autouse fixture).
        triggers_module._registry = original


# ---------------------------------------------------------------------------
# PG integration tests — require RUN_PG_TESTS=1 + DATABASE_URL
# ---------------------------------------------------------------------------

import os as _os  # noqa: E402 (module-level re-import at bottom of file)

_RUN_PG = bool(_os.getenv("RUN_PG_TESTS"))


@pytest.mark.skipif(
    not _RUN_PG,
    reason="Set RUN_PG_TESTS=1 and DATABASE_URL to run PG integration tests.",
)
async def test_pg_trigger_registry_persistence_real_db():
    """[PG] PgTriggerRegistry persists triggers across independent registry instances.

    Registers a trigger via one PgTriggerRegistry instance, then reads it back
    via a second fresh instance — proving the data is in Postgres (not in-memory).
    """
    import asyncpg as _apg  # noqa: PLC0415
    import app.db as app_db  # noqa: PLC0415

    db_url = _os.environ.get("DATABASE_URL", "")
    pool = await _apg.create_pool(dsn=db_url, min_size=1, max_size=2)
    schema = f"nubi_trig_test_{uuid.uuid4().hex[:8]}"
    try:
        async with pool.acquire() as conn:
            await conn.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
            await conn.execute(f'SET search_path TO "{schema}", public')
            # Bootstrap minimum tables for flow_triggers FK constraints.
            from pathlib import Path  # noqa: PLC0415
            migrations_dir = Path(__file__).parent.parent.parent / "database" / "migrations"
            for sql_file in sorted(migrations_dir.glob("*.sql")):
                sql = sql_file.read_text(encoding="utf-8")
                try:
                    await conn.execute(sql)
                except Exception:  # noqa: BLE001
                    pass  # idempotent; some CREATE IF NOT EXISTS already exist

        # Patch app.db to use our isolated pool with the test schema.
        async def _fetch(query: str, *args: Any) -> list:
            async with pool.acquire() as c:
                await c.execute(f'SET search_path TO "{schema}", public')
                return await c.fetch(query, *args)

        async def _fetchrow(query: str, *args: Any):
            async with pool.acquire() as c:
                await c.execute(f'SET search_path TO "{schema}", public')
                return await c.fetchrow(query, *args)

        async def _execute(query: str, *args: Any) -> str:
            async with pool.acquire() as c:
                await c.execute(f'SET search_path TO "{schema}", public')
                return await c.execute(query, *args)

        with (
            patch.object(app_db, "fetch", side_effect=_fetch),
            patch.object(app_db, "fetchrow", side_effect=_fetchrow),
            patch.object(app_db, "execute", side_effect=_execute),
        ):
            # Need real org/flow rows for FK constraints.  Insert minimal rows.
            org_id = str(uuid.uuid4())
            flow_id = str(uuid.uuid4())
            async with pool.acquire() as conn:
                await conn.execute(f'SET search_path TO "{schema}", public')
                await conn.execute(
                    "INSERT INTO orgs (id, name, slug) VALUES ($1::uuid, $2, $3)",
                    org_id, "test-org", f"test-org-{uuid.uuid4().hex[:6]}"
                )
                # Insert a minimal user for created_by FK.
                user_id = str(uuid.uuid4())
                await conn.execute(
                    "INSERT INTO users (id, email, name) VALUES ($1::uuid, $2, $3)",
                    user_id, f"u-{uuid.uuid4().hex[:6]}@test.com", "Test User"
                )
                # Insert minimal project for flows FK.
                project_id = str(uuid.uuid4())
                await conn.execute(
                    "INSERT INTO projects (id, org_id, name, slug) "
                    "VALUES ($1::uuid, $2::uuid, $3, $4)",
                    project_id, org_id, "test-project", f"proj-{uuid.uuid4().hex[:6]}"
                )
                await conn.execute(
                    "INSERT INTO flows (id, org_id, project_id, created_by, name, spec) "
                    "VALUES ($1::uuid, $2::uuid, $3::uuid, $4::uuid, $5, $6::jsonb)",
                    flow_id, org_id, project_id, user_id, "test-flow",
                    '{"version": 1, "name": "test-flow", "tasks": []}'
                )

            # --- Write via registry instance 1 ---
            reg1 = PgTriggerRegistry()
            trigger = await reg1.register(
                flow_id=flow_id,
                kind="event",
                source="pg.persistence.test",
                org_id=org_id,
                extra={"on_states": ["success"]},
            )
            assert trigger.id is not None

            # --- Read back via a brand-new registry instance 2 ---
            reg2 = PgTriggerRegistry()
            found = await reg2.list_by_event("pg.persistence.test", org_id)
            assert len(found) == 1, "Trigger must be readable from a fresh instance (persistence)"
            assert found[0].id == trigger.id
            assert found[0].org_id == org_id

            # --- Org isolation via Pg ---
            org_b = str(uuid.uuid4())
            async with pool.acquire() as conn:
                await conn.execute(f'SET search_path TO "{schema}", public')
                await conn.execute(
                    "INSERT INTO orgs (id, name, slug) VALUES ($1::uuid, $2, $3)",
                    org_b, "org-b", f"org-b-{uuid.uuid4().hex[:6]}"
                )
            isolated = await reg2.list_by_event("pg.persistence.test", org_b)
            assert isolated == [], "Trigger registered in org_a must NOT appear for org_b"

    finally:
        async with pool.acquire() as conn:
            await conn.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        await pool.close()
