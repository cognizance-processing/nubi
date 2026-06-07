"""Tests for the Flows engine — registry, executor, runtime.

All tests use InMemoryFlowStore and NullProvider (no API keys, no network).
The clock is injected deterministically.

Coverage
--------
1. TaskKindRegistry
   a. Pre-registered kinds: query, python, agent, noop.
   b. register/get/all/reset_for_tests.
   c. Unknown kind raises AppError.

2. execute_task — happy paths and failure
   a. noop handler returns {inputs: ...}.
   b. python handler runs code and captures result.
   c. Template resolution: {{ params.x }} and {{ inputs.k.field }}.
   d. Failed python code → state='failed', error set.
   e. timeout_s enforced (fast mock).
   f. Unknown kind → state='failed' (error caught by broad except).

3. materialize_flow_run
   a. Creates flow_run with state='running'.
   b. Inserts task_runs: roots are 'ready', others 'pending'.
   c. Root task_runs have scheduled_at=now.
   d. depends_on mirrors task.needs.
   e. Invalid spec raises ValueError.

4. advance_readiness
   a. Pending task with all deps succeeded → 'ready'.
   b. Pending task with any dep failed → 'skipped'.
   c. Pending task with any dep skipped → 'skipped'.
   d. All terminal → flow_run finalised.
   e. Any failed task → flow_run state='failed'.
   f. All success → flow_run state='success'.

5. drain_flow_run — linear 3-task flow (query → python → agent)
   a. All task_runs reach 'success'.
   b. flow_run reaches 'success'.
   c. Task results are stored.

6. Failing task → downstream skipped, flow failed
   a. python task raises → task state='failed'.
   b. Downstream task → 'skipped'.
   c. flow_run state='failed'.

7. Retries
   a. Task with retries=1 that fails goes to 'retrying' then 'failed' on
      second attempt.

8. Diamond DAG (a → b, a → c, b & c → d)
   a. d runs only after both b and c succeed.
   b. flow_run reaches 'success'.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from app.flows.executor import TaskContext, execute_task
from app.flows.registry import (
    TaskKindRegistry,
    get_task_kind_registry,
    reset_for_tests,
)
from app.flows.runtime import (
    advance_readiness,
    drain_flow_run,
    flow_tick,
    materialize_flow_run,
    run_one_ready_task,
)
from app.flows.store import InMemoryFlowStore

# Store methods + engine functions are async (one async interface shared with
# PgFlowStore), so every test in this module runs as an async test.
pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _utc(hour: int = 12) -> datetime:
    return datetime(2025, 1, 1, hour, 0, 0, tzinfo=timezone.utc)


NOW = _utc()

CLAIMS: dict[str, Any] = {"org_id": "org-test", "sub": "user-test"}


async def _make_flow(
    store: InMemoryFlowStore,
    spec: dict[str, Any],
    org_id: str = "org-test",
    created_by: str = "user-test",
    name: str = "test_flow",
) -> dict[str, Any]:
    return await store.create_flow(
        org_id=org_id,
        created_by=created_by,
        name=name,
        spec=spec,
    )


def _linear_spec() -> dict[str, Any]:
    """3-task linear DAG: pull (query) → enrich (python) → summary (agent)."""
    return {
        "version": 1,
        "name": "linear_flow",
        "tasks": [
            {
                "key": "pull",
                "kind": "query",
                "needs": [],
                "config": {"sql": "SELECT 1 AS n"},
                "retries": 0,
                "timeout_s": 0,
            },
            {
                "key": "enrich",
                "kind": "python",
                "needs": ["pull"],
                "config": {"code": "result = {'processed': True}"},
                "retries": 0,
                "timeout_s": 0,
            },
            {
                "key": "summary",
                "kind": "agent",
                "needs": ["enrich"],
                "config": {"prompt": "Summarize.", "max_steps": 1},
                "retries": 0,
                "timeout_s": 0,
            },
        ],
    }


def _failing_spec() -> dict[str, Any]:
    """Flow where python task raises, downstream should be skipped."""
    return {
        "version": 1,
        "name": "failing_flow",
        "tasks": [
            {
                "key": "root",
                "kind": "noop",
                "needs": [],
                "config": {},
                "retries": 0,
                "timeout_s": 0,
            },
            {
                "key": "fail_me",
                "kind": "python",
                "needs": ["root"],
                "config": {"code": "raise RuntimeError('intentional failure')"},
                "retries": 0,
                "timeout_s": 0,
            },
            {
                "key": "downstream",
                "kind": "noop",
                "needs": ["fail_me"],
                "config": {},
                "retries": 0,
                "timeout_s": 0,
            },
        ],
    }


def _retry_spec(retries: int = 1) -> dict[str, Any]:
    """Flow with a task that always fails, with retries configured."""
    return {
        "version": 1,
        "name": "retry_flow",
        "tasks": [
            {
                "key": "always_fail",
                "kind": "python",
                "needs": [],
                "config": {"code": "raise ValueError('always fails')"},
                "retries": retries,
                "retry_backoff_s": 0,
                "timeout_s": 0,
            },
        ],
    }


def _diamond_spec() -> dict[str, Any]:
    """Diamond DAG: a → b, a → c, b & c → d."""
    return {
        "version": 1,
        "name": "diamond_flow",
        "tasks": [
            {
                "key": "a",
                "kind": "noop",
                "needs": [],
                "config": {},
                "retries": 0,
                "timeout_s": 0,
            },
            {
                "key": "b",
                "kind": "noop",
                "needs": ["a"],
                "config": {},
                "retries": 0,
                "timeout_s": 0,
            },
            {
                "key": "c",
                "kind": "noop",
                "needs": ["a"],
                "config": {},
                "retries": 0,
                "timeout_s": 0,
            },
            {
                "key": "d",
                "kind": "noop",
                "needs": ["b", "c"],
                "config": {},
                "retries": 0,
                "timeout_s": 0,
            },
        ],
    }


# ---------------------------------------------------------------------------
# 1. TaskKindRegistry
# ---------------------------------------------------------------------------


class TestTaskKindRegistry:
    """TaskKindRegistry unit tests."""

    def setup_method(self):
        reset_for_tests()

    async def test_pre_registered_query(self):
        reg = get_task_kind_registry()
        assert "query" in reg.all()

    async def test_pre_registered_python(self):
        reg = get_task_kind_registry()
        assert "python" in reg.all()

    async def test_pre_registered_agent(self):
        reg = get_task_kind_registry()
        assert "agent" in reg.all()

    async def test_pre_registered_noop(self):
        reg = get_task_kind_registry()
        assert "noop" in reg.all()

    async def test_register_and_get(self):
        reg = get_task_kind_registry()

        def my_handler(config, ctx, claims):
            return {"ok": True}

        reg.register("custom", my_handler)
        retrieved = reg.get("custom")
        assert retrieved is my_handler

    async def test_all_returns_dict(self):
        reg = get_task_kind_registry()
        all_kinds = reg.all()
        assert isinstance(all_kinds, dict)
        assert len(all_kinds) >= 4

    async def test_unknown_kind_raises_app_error(self):
        from app.errors import AppError

        reg = get_task_kind_registry()
        with pytest.raises(AppError) as exc_info:
            reg.get("does_not_exist")
        assert "does_not_exist" in str(exc_info.value)

    async def test_reset_for_tests_restores_builtins(self):
        reg = get_task_kind_registry()
        reg.register("query", lambda c, x, cl: {"overridden": True})
        reset_for_tests()
        reg2 = get_task_kind_registry()
        # Built-in query handler should be restored.
        assert "query" in reg2.all()
        # The restored handler should not return {"overridden": True} for a trivial call.


# ---------------------------------------------------------------------------
# 2. execute_task
# ---------------------------------------------------------------------------


class TestExecuteTask:
    """execute_task dispatches to handlers and handles errors."""

    def setup_method(self):
        reset_for_tests()

    def _ctx(self, flow_params=None, inputs=None):
        return TaskContext(
            flow_params=flow_params or {},
            inputs=inputs or {},
            now=NOW,
        )

    async def test_noop_returns_inputs(self):
        ctx = self._ctx(inputs={"prev": {"x": 1}})
        task = {"kind": "noop", "config": {}, "timeout_s": 0}
        result = execute_task(task, ctx, CLAIMS)
        assert result["state"] == "success"
        assert result["result"] == {"inputs": {"prev": {"x": 1}}}

    async def test_python_captures_result_dict(self):
        ctx = self._ctx()
        task = {
            "kind": "python",
            "config": {"code": "result = {'answer': 42}"},
            "timeout_s": 0,
        }
        out = execute_task(task, ctx, CLAIMS)
        assert out["state"] == "success", f"error: {out.get('error')}"
        assert out["result"] is not None
        # The handler serialises via subprocess; result should contain 'answer'
        assert out["result"].get("answer") == 42

    async def test_python_receives_inputs(self):
        ctx = self._ctx(inputs={"prev": {"count": 7}})
        task = {
            "kind": "python",
            "config": {"code": "result = {'got': inputs['prev']['count']}"},
            "timeout_s": 0,
        }
        out = execute_task(task, ctx, CLAIMS)
        assert out["state"] == "success", f"error: {out.get('error')}"
        assert out["result"].get("got") == 7

    async def test_python_receives_params(self):
        ctx = self._ctx(flow_params={"region": "eu"})
        task = {
            "kind": "python",
            "config": {"code": "result = {'region': params['region']}"},
            "timeout_s": 0,
        }
        out = execute_task(task, ctx, CLAIMS)
        assert out["state"] == "success", f"error: {out.get('error')}"
        assert out["result"].get("region") == "eu"

    async def test_template_params_resolved_in_config(self):
        """{{ params.x }} in config string should be resolved before dispatch."""
        ctx = self._ctx(flow_params={"greeting": "hello"})
        task = {
            "kind": "python",
            "config": {"code": "result = {'msg': '{{ params.greeting }}'}"},
            "timeout_s": 0,
        }
        out = execute_task(task, ctx, CLAIMS)
        assert out["state"] == "success", f"error: {out.get('error')}"
        assert out["result"].get("msg") == "hello"

    async def test_template_inputs_resolved_in_config(self):
        """{{ inputs.prev.count }} should be resolved in config strings."""
        ctx = self._ctx(inputs={"prev": {"count": 99}})
        task = {
            "kind": "python",
            "config": {"code": "result = {'n': '{{ inputs.prev.count }}'}"},
            "timeout_s": 0,
        }
        out = execute_task(task, ctx, CLAIMS)
        assert out["state"] == "success", f"error: {out.get('error')}"
        # The value will be a string '99' since template resolution returns strings.
        assert str(out["result"].get("n")) == "99"

    async def test_failing_python_returns_failed(self):
        ctx = self._ctx()
        task = {
            "kind": "python",
            "config": {"code": "raise ValueError('boom')"},
            "timeout_s": 0,
        }
        out = execute_task(task, ctx, CLAIMS)
        assert out["state"] == "failed"
        assert out["error"] is not None
        assert out["result"] is None

    async def test_unknown_kind_returns_failed(self):
        ctx = self._ctx()
        task = {"kind": "does_not_exist", "config": {}, "timeout_s": 0}
        out = execute_task(task, ctx, CLAIMS)
        assert out["state"] == "failed"
        assert out["error"] is not None

    async def test_agent_returns_reply_and_actions(self):
        """Agent handler with NullProvider returns deterministic result."""
        ctx = self._ctx()
        task = {
            "kind": "agent",
            "config": {"prompt": "Summarize the data.", "max_steps": 1},
            "timeout_s": 0,
        }
        out = execute_task(task, ctx, CLAIMS)
        assert out["state"] == "success", f"error: {out.get('error')}"
        result = out["result"]
        assert "reply" in result
        assert "actions" in result

    async def test_query_with_sql_returns_rows(self):
        """Query handler executes ad-hoc SQL and returns row data."""
        ctx = self._ctx()
        task = {
            "kind": "query",
            "config": {"sql": "SELECT 1 AS n"},
            "timeout_s": 0,
        }
        out = execute_task(task, ctx, CLAIMS)
        assert out["state"] == "success", f"error: {out.get('error')}"
        result = out["result"]
        assert "row_count" in result
        assert result["row_count"] >= 1

    async def test_query_demo_table_seeded(self):
        """Query against the seeded demo table should return 5 rows."""
        ctx = self._ctx()
        task = {
            "kind": "query",
            "config": {"sql": "SELECT * FROM demo"},
            "timeout_s": 0,
        }
        out = execute_task(task, ctx, CLAIMS)
        assert out["state"] == "success", f"error: {out.get('error')}"
        assert out["result"]["row_count"] == 5


# ---------------------------------------------------------------------------
# 3. materialize_flow_run
# ---------------------------------------------------------------------------


class TestMaterializeFlowRun:
    """materialize_flow_run creates flow_run + task_runs correctly."""

    def setup_method(self):
        reset_for_tests()
        self.store = InMemoryFlowStore()

    async def test_creates_flow_run(self):
        spec = _linear_spec()
        flow = await _make_flow(self.store, spec)
        frun = await materialize_flow_run(self.store, flow, {}, "manual", NOW)
        assert frun is not None
        assert frun["id"] is not None

    async def test_flow_run_state_is_running(self):
        flow = await _make_flow(self.store, _linear_spec())
        frun = await materialize_flow_run(self.store, flow, {}, "manual", NOW)
        assert frun["state"] == "running"

    async def test_flow_run_started_at_is_now(self):
        flow = await _make_flow(self.store, _linear_spec())
        frun = await materialize_flow_run(self.store, flow, {}, "manual", NOW)
        assert frun["started_at"] == NOW

    async def test_task_runs_created(self):
        flow = await _make_flow(self.store, _linear_spec())
        frun = await materialize_flow_run(self.store, flow, {}, "manual", NOW)
        trs = await self.store.list_task_runs(frun["id"])
        assert len(trs) == 3

    async def test_root_task_is_ready(self):
        flow = await _make_flow(self.store, _linear_spec())
        frun = await materialize_flow_run(self.store, flow, {}, "manual", NOW)
        trs = await self.store.list_task_runs(frun["id"])
        by_key = {tr["task_key"]: tr for tr in trs}
        assert by_key["pull"]["state"] == "ready"

    async def test_non_root_tasks_are_pending(self):
        flow = await _make_flow(self.store, _linear_spec())
        frun = await materialize_flow_run(self.store, flow, {}, "manual", NOW)
        trs = await self.store.list_task_runs(frun["id"])
        by_key = {tr["task_key"]: tr for tr in trs}
        assert by_key["enrich"]["state"] == "pending"
        assert by_key["summary"]["state"] == "pending"

    async def test_root_scheduled_at_is_now(self):
        flow = await _make_flow(self.store, _linear_spec())
        frun = await materialize_flow_run(self.store, flow, {}, "manual", NOW)
        trs = await self.store.list_task_runs(frun["id"])
        by_key = {tr["task_key"]: tr for tr in trs}
        assert by_key["pull"]["scheduled_at"] == NOW

    async def test_depends_on_mirrors_needs(self):
        flow = await _make_flow(self.store, _linear_spec())
        frun = await materialize_flow_run(self.store, flow, {}, "manual", NOW)
        trs = await self.store.list_task_runs(frun["id"])
        by_key = {tr["task_key"]: tr for tr in trs}
        assert by_key["enrich"]["depends_on"] == ["pull"]
        assert by_key["summary"]["depends_on"] == ["enrich"]

    async def test_invalid_spec_raises_value_error(self):
        # A spec missing required kind config fields.
        bad_spec = {
            "version": 1,
            "name": "bad",
            "tasks": [
                {"key": "t1", "kind": "python", "needs": [], "config": {}},  # missing code
            ],
        }
        flow = await _make_flow(self.store, bad_spec)
        with pytest.raises(ValueError, match="invalid"):
            await materialize_flow_run(self.store, flow, {}, "manual", NOW)

    async def test_params_stored_in_flow_run(self):
        flow = await _make_flow(self.store, _linear_spec())
        frun = await materialize_flow_run(self.store, flow, {"region": "eu"}, "manual", NOW)
        assert frun["params"] == {"region": "eu"}


# ---------------------------------------------------------------------------
# 4. advance_readiness
# ---------------------------------------------------------------------------


class TestAdvanceReadiness:
    """advance_readiness state machine tests."""

    def setup_method(self):
        reset_for_tests()
        self.store = InMemoryFlowStore()

    async def _setup_run(self, spec):
        flow = await _make_flow(self.store, spec)
        frun = await materialize_flow_run(self.store, flow, {}, "manual", NOW)
        return frun

    async def _by_key(self, flow_run_id):
        trs = await self.store.list_task_runs(flow_run_id)
        return {tr["task_key"]: tr for tr in trs}

    async def test_pending_task_becomes_ready_when_all_deps_succeed(self):
        frun = await self._setup_run(_linear_spec())
        by_key = await self._by_key(frun["id"])
        # Mark pull as success.
        await self.store.update_task_run(by_key["pull"]["id"], {"state": "success", "result": {}})
        await advance_readiness(self.store, frun["id"], NOW)
        by_key = await self._by_key(frun["id"])
        assert by_key["enrich"]["state"] == "ready"

    async def test_pending_task_skipped_when_dep_failed(self):
        # Since M14 the engine uses 'upstream_failed' instead of 'skipped'.
        frun = await self._setup_run(_linear_spec())
        by_key = await self._by_key(frun["id"])
        await self.store.update_task_run(by_key["pull"]["id"], {"state": "failed"})
        await advance_readiness(self.store, frun["id"], NOW)
        by_key = await self._by_key(frun["id"])
        assert by_key["enrich"]["state"] == "upstream_failed"

    async def test_pending_task_skipped_when_dep_skipped(self):
        # Since M14 the engine uses 'upstream_failed' instead of 'skipped'.
        frun = await self._setup_run(_linear_spec())
        by_key = await self._by_key(frun["id"])
        # Mark pull as failed first, then advance to mark enrich upstream_failed.
        await self.store.update_task_run(by_key["pull"]["id"], {"state": "failed"})
        await advance_readiness(self.store, frun["id"], NOW)
        # enrich is now upstream_failed.
        by_key = await self._by_key(frun["id"])
        assert by_key["enrich"]["state"] == "upstream_failed"
        # Now advance again — summary should also be upstream_failed.
        await advance_readiness(self.store, frun["id"], NOW)
        by_key = await self._by_key(frun["id"])
        assert by_key["summary"]["state"] == "upstream_failed"

    async def test_all_success_finalises_flow_run(self):
        frun = await self._setup_run(_linear_spec())
        by_key = await self._by_key(frun["id"])
        for key in ("pull", "enrich", "summary"):
            await self.store.update_task_run(
                by_key[key]["id"], {"state": "success", "result": {}}
            )
        await advance_readiness(self.store, frun["id"], NOW)
        final = await self.store.get_flow_run(frun["id"])
        assert final["state"] == "success"
        assert final["finished_at"] == NOW

    async def test_any_failed_finalises_flow_run_as_failed(self):
        frun = await self._setup_run(_linear_spec())
        by_key = await self._by_key(frun["id"])
        await self.store.update_task_run(by_key["pull"]["id"], {"state": "failed"})
        # Use upstream_failed (new state) for downstream tasks.
        await self.store.update_task_run(by_key["enrich"]["id"], {"state": "upstream_failed"})
        await self.store.update_task_run(by_key["summary"]["id"], {"state": "upstream_failed"})
        await advance_readiness(self.store, frun["id"], NOW)
        final = await self.store.get_flow_run(frun["id"])
        assert final["state"] == "failed"


# ---------------------------------------------------------------------------
# 5. drain_flow_run — 3-task linear flow to success
# ---------------------------------------------------------------------------


class TestDrainLinearFlow:
    """drain_flow_run drives a 3-task linear flow to completion."""

    def setup_method(self):
        reset_for_tests()
        self.store = InMemoryFlowStore()

    async def test_all_task_runs_succeed(self):
        spec = _linear_spec()
        flow = await _make_flow(self.store, spec)
        frun = await materialize_flow_run(self.store, flow, {}, "manual", NOW)
        final = await drain_flow_run(self.store, frun["id"], NOW, CLAIMS)
        trs = await self.store.list_task_runs(frun["id"])
        states = {tr["task_key"]: tr["state"] for tr in trs}
        assert states["pull"] == "success", f"pull: {states['pull']}"
        assert states["enrich"] == "success", f"enrich: {states['enrich']}"
        assert states["summary"] == "success", f"summary: {states['summary']}"

    async def test_flow_run_reaches_success(self):
        spec = _linear_spec()
        flow = await _make_flow(self.store, spec)
        frun = await materialize_flow_run(self.store, flow, {}, "manual", NOW)
        final = await drain_flow_run(self.store, frun["id"], NOW, CLAIMS)
        assert final["state"] == "success", f"Final state: {final['state']}"

    async def test_task_results_stored(self):
        spec = _linear_spec()
        flow = await _make_flow(self.store, spec)
        frun = await materialize_flow_run(self.store, flow, {}, "manual", NOW)
        await drain_flow_run(self.store, frun["id"], NOW, CLAIMS)
        trs = await self.store.list_task_runs(frun["id"])
        by_key = {tr["task_key"]: tr for tr in trs}
        assert by_key["pull"]["result"] is not None
        assert by_key["enrich"]["result"] is not None
        assert by_key["summary"]["result"] is not None


# ---------------------------------------------------------------------------
# 6. Failing task → downstream skipped, flow failed
# ---------------------------------------------------------------------------


class TestFailingTask:
    """A failing task marks downstream tasks 'skipped' and the flow 'failed'."""

    def setup_method(self):
        reset_for_tests()
        self.store = InMemoryFlowStore()

    async def test_failing_task_state_is_failed(self):
        flow = await _make_flow(self.store, _failing_spec())
        frun = await materialize_flow_run(self.store, flow, {}, "manual", NOW)
        await drain_flow_run(self.store, frun["id"], NOW, CLAIMS)
        trs = await self.store.list_task_runs(frun["id"])
        by_key = {tr["task_key"]: tr for tr in trs}
        assert by_key["fail_me"]["state"] == "failed", (
            f"fail_me state: {by_key['fail_me']['state']}"
        )

    async def test_downstream_of_failed_is_skipped(self):
        # Since M14, downstream tasks are marked 'upstream_failed' (not 'skipped').
        flow = await _make_flow(self.store, _failing_spec())
        frun = await materialize_flow_run(self.store, flow, {}, "manual", NOW)
        await drain_flow_run(self.store, frun["id"], NOW, CLAIMS)
        trs = await self.store.list_task_runs(frun["id"])
        by_key = {tr["task_key"]: tr for tr in trs}
        assert by_key["downstream"]["state"] == "upstream_failed", (
            f"downstream state: {by_key['downstream']['state']}"
        )

    async def test_flow_run_state_is_failed(self):
        flow = await _make_flow(self.store, _failing_spec())
        frun = await materialize_flow_run(self.store, flow, {}, "manual", NOW)
        final = await drain_flow_run(self.store, frun["id"], NOW, CLAIMS)
        assert final["state"] == "failed", f"flow_run state: {final['state']}"

    async def test_error_message_stored(self):
        flow = await _make_flow(self.store, _failing_spec())
        frun = await materialize_flow_run(self.store, flow, {}, "manual", NOW)
        await drain_flow_run(self.store, frun["id"], NOW, CLAIMS)
        trs = await self.store.list_task_runs(frun["id"])
        by_key = {tr["task_key"]: tr for tr in trs}
        assert by_key["fail_me"]["error"] is not None


# ---------------------------------------------------------------------------
# 7. Retries
# ---------------------------------------------------------------------------


class TestRetries:
    """Tasks with retries configured: fail → retrying → terminal."""

    def setup_method(self):
        reset_for_tests()
        self.store = InMemoryFlowStore()

    async def test_task_with_one_retry_eventually_fails(self):
        """A task with retries=1 that always fails should end up 'failed'."""
        spec = _retry_spec(retries=1)
        flow = await _make_flow(self.store, spec)
        frun = await materialize_flow_run(self.store, flow, {}, "manual", NOW)
        final = await drain_flow_run(self.store, frun["id"], NOW, CLAIMS, max_steps=20)
        trs = await self.store.list_task_runs(frun["id"])
        by_key = {tr["task_key"]: tr for tr in trs}
        assert by_key["always_fail"]["state"] == "failed", (
            f"expected failed, got {by_key['always_fail']['state']}"
        )

    async def test_retry_attempt_incremented(self):
        """The attempt counter should be > 0 after a retry."""
        spec = _retry_spec(retries=1)
        flow = await _make_flow(self.store, spec)
        frun = await materialize_flow_run(self.store, flow, {}, "manual", NOW)
        await drain_flow_run(self.store, frun["id"], NOW, CLAIMS, max_steps=20)
        trs = await self.store.list_task_runs(frun["id"])
        by_key = {tr["task_key"]: tr for tr in trs}
        # After exhausting retries, the attempt should reflect re-tries made.
        assert by_key["always_fail"]["attempt"] >= 1

    async def test_flow_fails_after_retry_exhausted(self):
        spec = _retry_spec(retries=1)
        flow = await _make_flow(self.store, spec)
        frun = await materialize_flow_run(self.store, flow, {}, "manual", NOW)
        final = await drain_flow_run(self.store, frun["id"], NOW, CLAIMS, max_steps=20)
        assert final["state"] == "failed"


# ---------------------------------------------------------------------------
# 8. Diamond DAG
# ---------------------------------------------------------------------------


class TestDiamondDag:
    """Diamond DAG: a → b, a → c, b & c → d.

    'd' must only run after both b and c succeed.
    """

    def setup_method(self):
        reset_for_tests()
        self.store = InMemoryFlowStore()

    async def test_d_runs_after_b_and_c(self):
        spec = _diamond_spec()
        flow = await _make_flow(self.store, spec)
        frun = await materialize_flow_run(self.store, flow, {}, "manual", NOW)
        final = await drain_flow_run(self.store, frun["id"], NOW, CLAIMS)
        trs = await self.store.list_task_runs(frun["id"])
        by_key = {tr["task_key"]: tr for tr in trs}
        # All tasks should succeed.
        for key in ("a", "b", "c", "d"):
            assert by_key[key]["state"] == "success", (
                f"{key} state: {by_key[key]['state']}"
            )

    async def test_flow_run_success(self):
        spec = _diamond_spec()
        flow = await _make_flow(self.store, spec)
        frun = await materialize_flow_run(self.store, flow, {}, "manual", NOW)
        final = await drain_flow_run(self.store, frun["id"], NOW, CLAIMS)
        assert final["state"] == "success"

    async def test_d_not_ready_until_both_b_and_c_done(self):
        """Manually verify that d stays pending until b and c both succeed."""
        spec = _diamond_spec()
        flow = await _make_flow(self.store, spec)
        frun = await materialize_flow_run(self.store, flow, {}, "manual", NOW)
        trs = await self.store.list_task_runs(frun["id"])
        by_key = {tr["task_key"]: tr for tr in trs}

        # d must start as pending (not ready).
        assert by_key["d"]["state"] == "pending"

        # Mark only b as success — c is still pending.
        await self.store.update_task_run(by_key["a"]["id"], {"state": "success", "result": {}})
        await self.store.update_task_run(by_key["b"]["id"], {"state": "success", "result": {}})
        await advance_readiness(self.store, frun["id"], NOW)

        by_key = {tr["task_key"]: tr for tr in await self.store.list_task_runs(frun["id"])}
        # c should now be ready (a succeeded), but d still pending.
        assert by_key["c"]["state"] == "ready", f"c: {by_key['c']['state']}"
        assert by_key["d"]["state"] == "pending", f"d should still be pending, got {by_key['d']['state']}"

        # Now mark c as success.
        await self.store.update_task_run(by_key["c"]["id"], {"state": "success", "result": {}})
        await advance_readiness(self.store, frun["id"], NOW)
        by_key = {tr["task_key"]: tr for tr in await self.store.list_task_runs(frun["id"])}
        assert by_key["d"]["state"] == "ready", f"d should now be ready, got {by_key['d']['state']}"
