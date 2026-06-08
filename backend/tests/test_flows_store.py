"""Tests for InMemoryFlowStore — CRUD for flows, flow_runs, task_runs,
and the claim_ready_task_run ordering contract.

Coverage
--------
1. Flow CRUD
   a. create_flow returns a flow dict with all required fields.
   b. get_flow returns a copy; returns None for unknown id.
   c. list_flows filters by org_id and sorts by created_at.
   d. update_flow updates fields and bumps updated_at.
   e. delete_flow removes flow and its runs; returns True/False.

2. FlowRun CRUD
   a. create_flow_run returns a flow_run dict with all required fields.
   b. get_flow_run returns a copy; None for unknown id.
   c. list_flow_runs is newest-first.
   d. update_flow_run updates state/fields.

3. TaskRun CRUD
   a. add_task_runs bulk-inserts and returns stored list.
   b. list_task_runs ordered by created_at then task_key.
   c. get_task_run returns a copy; None for unknown id.
   d. update_task_run updates fields.

4. claim_ready_task_run ordering
   a. Returns None when no task_runs exist.
   b. Returns None when all are pending (not ready).
   c. Claims the oldest ready task_run (by scheduled_at, None-first,
      then created_at).
   d. Claimed task_run state is 'running', started_at is set.
   e. Claims task_run with scheduled_at=None over one with future scheduled_at.
   f. Does NOT claim task_run with scheduled_at > now.
   g. After claim, subsequent call claims the next oldest.
   h. deepcopy: mutating returned dict does not affect store.

5. Isolation
   a. Store instances are independent.
   b. Returned dicts are deep copies.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from app.flows.store import InMemoryFlowStore

# All store methods are async (one async interface shared with PgFlowStore),
# so every test in this module is an async test.
pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _utc(year: int = 2025, month: int = 1, day: int = 1, hour: int = 12) -> datetime:
    return datetime(year, month, day, hour, 0, 0, tzinfo=timezone.utc)


async def _make_flow(
    store: InMemoryFlowStore,
    org_id: str = "org-1",
    created_by: str = "user-1",
    name: str = "my_flow",
    spec: dict | None = None,
) -> dict[str, Any]:
    return await store.create_flow(
        org_id=org_id,
        created_by=created_by,
        name=name,
        spec=spec or {"version": 1, "name": name, "tasks": []},
    )


async def _make_flow_run(
    store: InMemoryFlowStore,
    flow_id: str,
    org_id: str = "org-1",
    params: dict | None = None,
    trigger: str = "manual",
    scheduled_at: datetime | None = None,
) -> dict[str, Any]:
    return await store.create_flow_run(
        flow_id=flow_id,
        org_id=org_id,
        params=params or {},
        trigger=trigger,
        scheduled_at=scheduled_at,
    )


def _make_task_run(
    task_key: str,
    org_id: str = "org-1",
    state: str = "pending",
    depends_on: list[str] | None = None,
    scheduled_at: datetime | None = None,
    created_at: datetime | None = None,
) -> dict[str, Any]:
    tr: dict[str, Any] = {
        "task_key": task_key,
        "org_id": org_id,
        "state": state,
        "depends_on": depends_on or [],
    }
    if scheduled_at is not None:
        tr["scheduled_at"] = scheduled_at
    if created_at is not None:
        tr["created_at"] = created_at
    return tr


# ---------------------------------------------------------------------------
# 1. Flow CRUD
# ---------------------------------------------------------------------------


class TestFlowCrud:
    """InMemoryFlowStore CRUD for flows."""

    async def test_create_flow_returns_dict(self):
        store = InMemoryFlowStore()
        flow = await _make_flow(store)
        assert isinstance(flow, dict)

    async def test_create_flow_has_required_fields(self):
        store = InMemoryFlowStore()
        flow = await _make_flow(store, org_id="org-x", created_by="user-y", name="test")
        assert "id" in flow
        assert flow["org_id"] == "org-x"
        assert flow["created_by"] == "user-y"
        assert flow["name"] == "test"
        assert isinstance(flow["spec"], dict)
        assert flow["version"] == 1
        assert flow["enabled"] is True
        assert flow["schedule"] is None
        assert flow["next_run_at"] is None
        assert flow["last_run_at"] is None
        assert "created_at" in flow
        assert "updated_at" in flow

    async def test_create_flow_id_is_string(self):
        store = InMemoryFlowStore()
        flow = await _make_flow(store)
        assert isinstance(flow["id"], str)

    async def test_create_flow_timestamps_are_utc_aware(self):
        store = InMemoryFlowStore()
        flow = await _make_flow(store)
        assert flow["created_at"].tzinfo is not None
        assert flow["updated_at"].tzinfo is not None

    async def test_get_flow_returns_copy(self):
        store = InMemoryFlowStore()
        flow = await _make_flow(store)
        fetched = await store.get_flow(flow["id"])
        assert fetched is not None
        assert fetched["id"] == flow["id"]

    async def test_get_flow_none_for_unknown(self):
        store = InMemoryFlowStore()
        assert await store.get_flow(str(uuid.uuid4())) is None

    async def test_list_flows_filters_by_org(self):
        store = InMemoryFlowStore()
        await _make_flow(store, org_id="org-A", name="f1")
        await _make_flow(store, org_id="org-B", name="f2")
        await _make_flow(store, org_id="org-A", name="f3")

        a_flows = await store.list_flows("org-A")
        assert len(a_flows) == 2
        assert all(f["org_id"] == "org-A" for f in a_flows)
        b_flows = await store.list_flows("org-B")
        assert len(b_flows) == 1

    async def test_list_flows_sorted_by_created_at(self):
        store = InMemoryFlowStore()
        f1 = await _make_flow(store, org_id="org-1", name="first")
        f2 = await _make_flow(store, org_id="org-1", name="second")
        f3 = await _make_flow(store, org_id="org-1", name="third")
        flows = await store.list_flows("org-1")
        ids = [f["id"] for f in flows]
        assert ids[0] == f1["id"]  # oldest first

    async def test_list_flows_empty_for_unknown_org(self):
        store = InMemoryFlowStore()
        await _make_flow(store, org_id="org-A")
        assert await store.list_flows("org-Z") == []

    async def test_update_flow_updates_name(self):
        store = InMemoryFlowStore()
        flow = await _make_flow(store)
        updated = await store.update_flow(flow["id"], {"name": "renamed"})
        assert updated is not None
        assert updated["name"] == "renamed"
        # Persisted
        fetched = await store.get_flow(flow["id"])
        assert fetched["name"] == "renamed"

    async def test_update_flow_bumps_updated_at(self):
        store = InMemoryFlowStore()
        flow = await _make_flow(store)
        original_updated = flow["updated_at"]
        updated = await store.update_flow(flow["id"], {"name": "x"})
        assert updated["updated_at"] >= original_updated

    async def test_update_flow_returns_none_for_unknown(self):
        store = InMemoryFlowStore()
        result = await store.update_flow(str(uuid.uuid4()), {"name": "x"})
        assert result is None

    async def test_update_flow_with_custom_fields(self):
        store = InMemoryFlowStore()
        flow = await _make_flow(store)
        now_dt = _utc()
        updated = await store.update_flow(flow["id"], {
            "enabled": False,
            "schedule": "interval:10m",
            "next_run_at": now_dt,
        })
        assert updated is not None
        assert updated["enabled"] is False
        assert updated["schedule"] == "interval:10m"
        assert updated["next_run_at"] == now_dt

    async def test_delete_flow_returns_true(self):
        store = InMemoryFlowStore()
        flow = await _make_flow(store)
        assert await store.delete_flow(flow["id"]) is True

    async def test_delete_flow_removes_it(self):
        store = InMemoryFlowStore()
        flow = await _make_flow(store)
        await store.delete_flow(flow["id"])
        assert await store.get_flow(flow["id"]) is None

    async def test_delete_flow_returns_false_for_unknown(self):
        store = InMemoryFlowStore()
        assert await store.delete_flow(str(uuid.uuid4())) is False

    async def test_delete_flow_removes_runs(self):
        store = InMemoryFlowStore()
        flow = await _make_flow(store)
        run = await _make_flow_run(store, flow["id"])
        await store.delete_flow(flow["id"])
        assert await store.get_flow_run(run["id"]) is None


# ---------------------------------------------------------------------------
# 2. FlowRun CRUD
# ---------------------------------------------------------------------------


class TestFlowRunCrud:
    """InMemoryFlowStore CRUD for flow_runs."""

    async def test_create_flow_run_returns_dict(self):
        store = InMemoryFlowStore()
        flow = await _make_flow(store)
        run = await _make_flow_run(store, flow["id"])
        assert isinstance(run, dict)

    async def test_create_flow_run_has_required_fields(self):
        store = InMemoryFlowStore()
        flow = await _make_flow(store, org_id="org-1")
        run = await _make_flow_run(
            store, flow["id"], org_id="org-1",
            params={"region": "us"}, trigger="manual",
        )
        assert "id" in run
        assert run["flow_id"] == flow["id"]
        assert run["org_id"] == "org-1"
        assert run["state"] == "pending"
        assert run["params"] == {"region": "us"}
        assert run["trigger"] == "manual"
        assert run["scheduled_at"] is None
        assert run["started_at"] is None
        assert run["finished_at"] is None
        assert run["error"] is None
        assert "created_at" in run

    async def test_create_flow_run_id_is_string(self):
        store = InMemoryFlowStore()
        flow = await _make_flow(store)
        run = await _make_flow_run(store, flow["id"])
        assert isinstance(run["id"], str)

    async def test_create_flow_run_timestamps_utc_aware(self):
        store = InMemoryFlowStore()
        flow = await _make_flow(store)
        run = await _make_flow_run(store, flow["id"])
        assert run["created_at"].tzinfo is not None

    async def test_get_flow_run_returns_copy(self):
        store = InMemoryFlowStore()
        flow = await _make_flow(store)
        run = await _make_flow_run(store, flow["id"])
        fetched = await store.get_flow_run(run["id"])
        assert fetched is not None
        assert fetched["id"] == run["id"]

    async def test_get_flow_run_none_for_unknown(self):
        store = InMemoryFlowStore()
        assert await store.get_flow_run(str(uuid.uuid4())) is None

    async def test_list_flow_runs_newest_first(self):
        store = InMemoryFlowStore()
        flow = await _make_flow(store)
        r1 = await _make_flow_run(store, flow["id"])
        r2 = await _make_flow_run(store, flow["id"])
        r3 = await _make_flow_run(store, flow["id"])
        runs = await store.list_flow_runs(flow["id"])
        # newest first — r3 was created last
        assert runs[0]["id"] == r3["id"]

    async def test_list_flow_runs_empty_for_unknown_flow(self):
        store = InMemoryFlowStore()
        assert await store.list_flow_runs(str(uuid.uuid4())) == []

    async def test_update_flow_run_updates_state(self):
        store = InMemoryFlowStore()
        flow = await _make_flow(store)
        run = await _make_flow_run(store, flow["id"])
        updated = await store.update_flow_run(run["id"], {"state": "running"})
        assert updated is not None
        assert updated["state"] == "running"
        # Persisted
        fetched = await store.get_flow_run(run["id"])
        assert fetched["state"] == "running"

    async def test_update_flow_run_returns_none_for_unknown(self):
        store = InMemoryFlowStore()
        result = await store.update_flow_run(str(uuid.uuid4()), {"state": "running"})
        assert result is None

    async def test_update_flow_run_can_set_timestamps(self):
        store = InMemoryFlowStore()
        flow = await _make_flow(store)
        run = await _make_flow_run(store, flow["id"])
        now_dt = _utc()
        updated = await store.update_flow_run(run["id"], {
            "state": "success",
            "started_at": now_dt,
            "finished_at": now_dt,
        })
        assert updated["started_at"] == now_dt
        assert updated["finished_at"] == now_dt


# ---------------------------------------------------------------------------
# 3. TaskRun CRUD
# ---------------------------------------------------------------------------


class TestTaskRunCrud:
    """InMemoryFlowStore CRUD for task_runs."""

    async def _setup(self):
        store = InMemoryFlowStore()
        flow = await _make_flow(store)
        run = await _make_flow_run(store, flow["id"])
        return store, flow, run

    async def test_add_task_runs_returns_list(self):
        store, _, run = await self._setup()
        tr_dicts = [
            _make_task_run("t1"),
            _make_task_run("t2"),
        ]
        result = await store.add_task_runs(run["id"], tr_dicts)
        assert isinstance(result, list)
        assert len(result) == 2

    async def test_add_task_runs_assigns_ids(self):
        store, _, run = await self._setup()
        result = await store.add_task_runs(run["id"], [_make_task_run("t1")])
        assert "id" in result[0]
        assert isinstance(result[0]["id"], str)

    async def test_add_task_runs_sets_flow_run_id(self):
        store, _, frun = await self._setup()
        result = await store.add_task_runs(frun["id"], [_make_task_run("t1")])
        assert result[0]["flow_run_id"] == frun["id"]

    async def test_add_task_runs_has_required_fields(self):
        store, _, frun = await self._setup()
        result = await store.add_task_runs(frun["id"], [_make_task_run("my_task", state="ready")])
        tr = result[0]
        assert tr["task_key"] == "my_task"
        assert tr["state"] == "ready"
        assert tr["attempt"] == 0
        assert isinstance(tr["depends_on"], list)
        assert "created_at" in tr

    async def test_list_task_runs_ordered_by_created_at_then_key(self):
        store, _, frun = await self._setup()
        now_dt = _utc()
        # Insert with explicit created_at to control order
        await store.add_task_runs(frun["id"], [
            _make_task_run("z_task", created_at=now_dt + timedelta(seconds=2)),
            _make_task_run("a_task", created_at=now_dt + timedelta(seconds=1)),
            _make_task_run("m_task", created_at=now_dt),
        ])
        trs = await store.list_task_runs(frun["id"])
        # Should be sorted by created_at ascending, then by task_key.
        assert trs[0]["task_key"] == "m_task"
        assert trs[1]["task_key"] == "a_task"
        assert trs[2]["task_key"] == "z_task"

    async def test_list_task_runs_empty_for_unknown_run(self):
        store = InMemoryFlowStore()
        assert await store.list_task_runs(str(uuid.uuid4())) == []

    async def test_get_task_run_returns_copy(self):
        store, _, frun = await self._setup()
        trs = await store.add_task_runs(frun["id"], [_make_task_run("t1")])
        tr_id = trs[0]["id"]
        fetched = await store.get_task_run(tr_id)
        assert fetched is not None
        assert fetched["id"] == tr_id

    async def test_get_task_run_none_for_unknown(self):
        store = InMemoryFlowStore()
        assert await store.get_task_run(str(uuid.uuid4())) is None

    async def test_update_task_run_updates_state(self):
        store, _, frun = await self._setup()
        trs = await store.add_task_runs(frun["id"], [_make_task_run("t1")])
        tr_id = trs[0]["id"]
        updated = await store.update_task_run(tr_id, {"state": "success"})
        assert updated is not None
        assert updated["state"] == "success"
        # Persisted
        fetched = await store.get_task_run(tr_id)
        assert fetched["state"] == "success"

    async def test_update_task_run_can_set_result(self):
        store, _, frun = await self._setup()
        trs = await store.add_task_runs(frun["id"], [_make_task_run("t1")])
        tr_id = trs[0]["id"]
        result_data = {"rows": 42, "data": [1, 2, 3]}
        updated = await store.update_task_run(tr_id, {"result": result_data, "state": "success"})
        assert updated["result"] == result_data

    async def test_update_task_run_returns_none_for_unknown(self):
        store = InMemoryFlowStore()
        assert await store.update_task_run(str(uuid.uuid4()), {"state": "success"}) is None

    async def test_depends_on_preserved(self):
        store, _, frun = await self._setup()
        await store.add_task_runs(frun["id"], [_make_task_run("t1")])
        trs = await store.add_task_runs(frun["id"], [
            _make_task_run("t2", depends_on=["t1"]),
        ])
        assert trs[0]["depends_on"] == ["t1"]


# ---------------------------------------------------------------------------
# 4. claim_ready_task_run ordering
# ---------------------------------------------------------------------------


class TestClaimReadyTaskRun:
    """claim_ready_task_run returns the oldest eligible task_run."""

    async def _setup(self):
        store = InMemoryFlowStore()
        flow = await _make_flow(store)
        run = await _make_flow_run(store, flow["id"])
        return store, run

    async def test_returns_none_when_no_task_runs(self):
        store, _ = await self._setup()
        now_dt = _utc()
        result = await store.claim_ready_task_run(now_dt)
        assert result is None

    async def test_returns_none_when_all_pending(self):
        store, frun = await self._setup()
        await store.add_task_runs(frun["id"], [
            _make_task_run("t1", state="pending"),
            _make_task_run("t2", state="pending"),
        ])
        result = await store.claim_ready_task_run(_utc())
        assert result is None

    async def test_claims_only_ready_task_run(self):
        store, frun = await self._setup()
        await store.add_task_runs(frun["id"], [
            _make_task_run("t1", state="pending"),
            _make_task_run("t2", state="ready"),
        ])
        result = await store.claim_ready_task_run(_utc())
        assert result is not None
        assert result["task_key"] == "t2"

    async def test_claimed_state_is_running(self):
        store, frun = await self._setup()
        await store.add_task_runs(frun["id"], [_make_task_run("t1", state="ready")])
        result = await store.claim_ready_task_run(_utc())
        assert result["state"] == "running"

    async def test_claimed_started_at_is_set(self):
        store, frun = await self._setup()
        now_dt = _utc(hour=9)
        await store.add_task_runs(frun["id"], [_make_task_run("t1", state="ready")])
        result = await store.claim_ready_task_run(now_dt)
        assert result["started_at"] == now_dt

    async def test_claimed_state_persisted_in_store(self):
        store, frun = await self._setup()
        trs = await store.add_task_runs(frun["id"], [_make_task_run("t1", state="ready")])
        tr_id = trs[0]["id"]
        await store.claim_ready_task_run(_utc())
        fetched = await store.get_task_run(tr_id)
        assert fetched["state"] == "running"

    async def test_does_not_claim_future_scheduled_at(self):
        """scheduled_at in the future → not eligible."""
        store, frun = await self._setup()
        now_dt = _utc()
        future = now_dt + timedelta(hours=1)
        await store.add_task_runs(frun["id"], [
            _make_task_run("t1", state="ready", scheduled_at=future),
        ])
        result = await store.claim_ready_task_run(now_dt)
        assert result is None

    async def test_claims_task_with_none_scheduled_at_over_future(self):
        """scheduled_at=None (immediate) beats scheduled_at in the future."""
        store, frun = await self._setup()
        now_dt = _utc()
        await store.add_task_runs(frun["id"], [
            _make_task_run("future_task", state="ready",
                           scheduled_at=now_dt + timedelta(hours=1)),
        ])
        await store.add_task_runs(frun["id"], [
            _make_task_run("immediate_task", state="ready", scheduled_at=None),
        ])
        result = await store.claim_ready_task_run(now_dt)
        assert result is not None
        # immediate (None scheduled_at) should be claimed; future should not be eligible
        assert result["task_key"] == "immediate_task"

    async def test_claims_oldest_by_created_at_when_both_immediate(self):
        """Among ready tasks with scheduled_at=None, claim the one with oldest created_at."""
        store, frun = await self._setup()
        now_dt = _utc()
        earlier = now_dt - timedelta(minutes=5)
        later = now_dt - timedelta(minutes=1)

        # Insert with explicit created_at
        await store.add_task_runs(frun["id"], [
            _make_task_run("newer_task", state="ready", created_at=later),
        ])
        await store.add_task_runs(frun["id"], [
            _make_task_run("older_task", state="ready", created_at=earlier),
        ])
        result = await store.claim_ready_task_run(now_dt)
        assert result is not None
        assert result["task_key"] == "older_task"

    async def test_claims_at_scheduled_at_boundary(self):
        """scheduled_at == now is eligible."""
        store, frun = await self._setup()
        now_dt = _utc()
        await store.add_task_runs(frun["id"], [
            _make_task_run("t1", state="ready", scheduled_at=now_dt),
        ])
        result = await store.claim_ready_task_run(now_dt)
        assert result is not None

    async def test_second_claim_gets_next_oldest(self):
        """Two sequential claims return the two oldest eligible tasks."""
        store, frun = await self._setup()
        now_dt = _utc()
        earlier = now_dt - timedelta(minutes=10)
        await store.add_task_runs(frun["id"], [
            _make_task_run("second_task", state="ready", created_at=now_dt - timedelta(minutes=5)),
            _make_task_run("first_task", state="ready", created_at=earlier),
        ])
        first_claim = await store.claim_ready_task_run(now_dt)
        second_claim = await store.claim_ready_task_run(now_dt)
        assert first_claim is not None
        assert second_claim is not None
        assert first_claim["task_key"] == "first_task"
        assert second_claim["task_key"] == "second_task"

    async def test_no_more_claims_after_all_running(self):
        """Once all ready tasks are claimed, subsequent call returns None."""
        store, frun = await self._setup()
        now_dt = _utc()
        await store.add_task_runs(frun["id"], [
            _make_task_run("t1", state="ready"),
        ])
        await store.claim_ready_task_run(now_dt)
        result = await store.claim_ready_task_run(now_dt)
        assert result is None

    async def test_skipped_and_success_tasks_not_claimed(self):
        """Non-claimable states: success, failed, skipped, running, pending.
        Note: 'retrying' with a future scheduled_at is also not claimed yet.
        """
        from datetime import timedelta
        store, frun = await self._setup()
        now_dt = _utc()
        future = now_dt + timedelta(hours=1)
        await store.add_task_runs(frun["id"], [
            _make_task_run("t1", state="success"),
            _make_task_run("t2", state="failed"),
            _make_task_run("t3", state="skipped"),
            _make_task_run("t4", state="running"),
            # retrying with a future scheduled_at must NOT be claimed yet.
            _make_task_run("t5", state="retrying", scheduled_at=future),
        ])
        result = await store.claim_ready_task_run(now_dt)
        assert result is None

    async def test_retrying_task_claimed_when_scheduled_at_due(self):
        """A 'retrying' task with scheduled_at <= now must be claimable."""
        from datetime import timedelta
        store, frun = await self._setup()
        now_dt = _utc()
        past = now_dt - timedelta(seconds=1)
        await store.add_task_runs(frun["id"], [
            _make_task_run("t1", state="retrying", scheduled_at=past),
        ])
        result = await store.claim_ready_task_run(now_dt)
        assert result is not None
        assert result["state"] == "running"
        assert result["task_key"] == "t1"


# ---------------------------------------------------------------------------
# 5. Isolation
# ---------------------------------------------------------------------------


class TestStoreIsolation:
    """Store instances are independent; returned dicts are deep copies."""

    async def test_two_stores_are_independent(self):
        s1 = InMemoryFlowStore()
        s2 = InMemoryFlowStore()
        await _make_flow(s1, org_id="org-1")
        assert await s2.list_flows("org-1") == []

    async def test_returned_flow_dict_is_deep_copy(self):
        store = InMemoryFlowStore()
        flow = await _make_flow(store)
        flow["name"] = "mutated"
        # Store should be unaffected
        fetched = await store.get_flow(flow["id"])
        assert fetched["name"] != "mutated"

    async def test_returned_task_run_dict_is_deep_copy(self):
        store = InMemoryFlowStore()
        flow = await _make_flow(store)
        run = await _make_flow_run(store, flow["id"])
        trs = await store.add_task_runs(run["id"], [_make_task_run("t1", state="ready")])
        tr = trs[0]
        tr["state"] = "mutated"
        fetched = await store.get_task_run(tr["id"])
        assert fetched["state"] == "ready"

    async def test_claim_returns_deep_copy(self):
        store = InMemoryFlowStore()
        flow = await _make_flow(store)
        run = await _make_flow_run(store, flow["id"])
        await store.add_task_runs(run["id"], [_make_task_run("t1", state="ready")])
        claimed = await store.claim_ready_task_run(_utc())
        claimed["state"] = "mutated"
        # Original in store must still be 'running'
        fetched = await store.get_task_run(claimed["id"])
        assert fetched["state"] == "running"


# ---------------------------------------------------------------------------
# 6. Map / branch task_run columns (migration 0020)
# ---------------------------------------------------------------------------


class TestMapBranchTaskRunColumns:
    """InMemoryFlowStore task_run records include parent_task_run_id and
    branch_taken fields introduced in migration 0020."""

    async def _setup(self):
        store = InMemoryFlowStore()
        flow = await _make_flow(store)
        run = await _make_flow_run(store, flow["id"])
        return store, run

    async def test_new_task_run_has_parent_task_run_id_field(self):
        """Every newly created task_run exposes parent_task_run_id (default None)."""
        store, frun = await self._setup()
        trs = await store.add_task_runs(frun["id"], [_make_task_run("t1")])
        assert "parent_task_run_id" in trs[0], "Missing parent_task_run_id field"
        assert trs[0]["parent_task_run_id"] is None

    async def test_new_task_run_has_branch_taken_field(self):
        """Every newly created task_run exposes branch_taken (default None)."""
        store, frun = await self._setup()
        trs = await store.add_task_runs(frun["id"], [_make_task_run("t1")])
        assert "branch_taken" in trs[0], "Missing branch_taken field"
        assert trs[0]["branch_taken"] is None

    async def test_parent_task_run_id_can_be_set(self):
        """Map child task_runs can record their parent map task_run id."""
        store, frun = await self._setup()
        # Create parent map task_run
        parent_trs = await store.add_task_runs(frun["id"], [_make_task_run("map_node")])
        parent_id = parent_trs[0]["id"]
        # Create child task_run referencing parent
        child_tr_dict = {
            "task_key": "map_node[0].fetch_data",
            "org_id": "org-1",
            "state": "pending",
            "depends_on": [],
            "parent_task_run_id": parent_id,
        }
        child_trs = await store.add_task_runs(frun["id"], [child_tr_dict])
        assert child_trs[0]["parent_task_run_id"] == parent_id

    async def test_child_task_run_persisted_in_list_task_runs(self):
        """Child map task_runs share the flow_run_id and appear in list_task_runs."""
        store, frun = await self._setup()
        parent_trs = await store.add_task_runs(frun["id"], [_make_task_run("map_node")])
        parent_id = parent_trs[0]["id"]
        child_dicts = [
            {
                "task_key": f"map_node[{i}].fetch_data",
                "org_id": "org-1",
                "state": "pending",
                "depends_on": [],
                "parent_task_run_id": parent_id,
            }
            for i in range(3)
        ]
        await store.add_task_runs(frun["id"], child_dicts)
        all_trs = await store.list_task_runs(frun["id"])
        # 1 parent + 3 children = 4 total
        assert len(all_trs) == 4
        child_trs = [tr for tr in all_trs if tr["parent_task_run_id"] == parent_id]
        assert len(child_trs) == 3

    async def test_branch_taken_can_be_set_via_update(self):
        """branch_taken can be updated on a branch task_run."""
        store, frun = await self._setup()
        trs = await store.add_task_runs(frun["id"], [_make_task_run("route")])
        tr_id = trs[0]["id"]
        updated = await store.update_task_run(
            tr_id, {"state": "success", "branch_taken": "condition_0"}
        )
        assert updated is not None
        assert updated["branch_taken"] == "condition_0"
        # Persisted
        fetched = await store.get_task_run(tr_id)
        assert fetched["branch_taken"] == "condition_0"

    async def test_waiting_children_state_is_not_claimable(self):
        """A task_run in state 'waiting_children' must never be claimed."""
        store, frun = await self._setup()
        # Insert a map node in waiting_children state + an unrelated ready task.
        now_dt = _utc()
        await store.add_task_runs(frun["id"], [
            {
                "task_key": "map_node",
                "org_id": "org-1",
                "state": "waiting_children",
                "depends_on": [],
            },
            _make_task_run("other_task", state="ready"),
        ])
        claimed = await store.claim_ready_task_run(now_dt)
        # Only the ready task should be claimable.
        assert claimed is not None
        assert claimed["task_key"] == "other_task"
        # Second claim should return None (no more ready tasks).
        second = await store.claim_ready_task_run(now_dt)
        assert second is None

    async def test_waiting_children_alone_not_claimable(self):
        """When only waiting_children tasks exist, claim returns None."""
        store, frun = await self._setup()
        now_dt = _utc()
        await store.add_task_runs(frun["id"], [
            {
                "task_key": "map_node",
                "org_id": "org-1",
                "state": "waiting_children",
                "depends_on": [],
            },
        ])
        result = await store.claim_ready_task_run(now_dt)
        assert result is None

    async def test_parent_task_run_id_defaults_to_none_for_regular_tasks(self):
        """Regular (non-map-child) task_runs always have parent_task_run_id = None."""
        store, frun = await self._setup()
        trs = await store.add_task_runs(frun["id"], [
            _make_task_run("t1"),
            _make_task_run("t2"),
        ])
        for tr in trs:
            assert tr["parent_task_run_id"] is None, (
                f"Expected None parent_task_run_id for {tr['task_key']}"
            )

    async def test_branch_taken_defaults_to_none_for_non_branch_tasks(self):
        """Non-branch task_runs have branch_taken = None by default."""
        store, frun = await self._setup()
        trs = await store.add_task_runs(frun["id"], [
            _make_task_run("q1"),
            _make_task_run("p1"),
        ])
        for tr in trs:
            assert tr["branch_taken"] is None, (
                f"Expected None branch_taken for {tr['task_key']}"
            )
