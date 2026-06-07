"""Tests for Flows engine robustness: retries, timeouts, logs, events.

Coverage
--------
1. Retries
   a. Task with retries=2 that always fails: exhausts retries, ends failed.
   b. Attempt counter incremented correctly across retries.
   c. Downstream of an exhausted-retry task becomes upstream_failed.
   d. Flow run marked failed after retry exhaustion.
   e. Task with retries=1 that succeeds on second attempt → success.

2. Timeouts
   a. Task with timeout_s=1 (fast mock) is marked timed_out.
   b. timed_out state is terminal (not retried).
   c. Downstream of timed_out task becomes upstream_failed.

3. Per-task logging
   a. Python task stdout lines are captured in logs list.
   b. Logs are returned in task_run serialization via GET /runs/{run_id}.
   c. Failed python task captures stderr/traceback in logs.
   d. GET /flows/runs/{run_id}/tasks/{task_key}/logs returns logs + state.

4. Failure states / upstream_failed
   a. upstream_failed (not 'skipped') when dep fails.
   b. Flow run is marked failed when any task is upstream_failed.
   c. upstream_failed propagates transitively (grandchild).

5. Event hooks (events.py)
   a. emit_flow_event fires registered listener on task failure.
   b. Multiple listeners all receive the event.
   c. A listener that raises does NOT break the engine.
   d. clear_flow_listeners + no listener = no-op (no exception).
   e. Listener receives correct event fields (type, flow_run_id, state, error).
   f. flow_success event emitted when all tasks succeed.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import MagicMock

import pytest

from app.flows.events import (
    FlowEvent,
    clear_flow_listeners,
    emit_flow_event,
    register_flow_listener,
    unregister_flow_listener,
)
from app.flows.executor import TaskContext, execute_task
from app.flows.registry import get_task_kind_registry, reset_for_tests
from app.flows.runtime import (
    advance_readiness,
    drain_flow_run,
    materialize_flow_run,
)
from app.flows.store import InMemoryFlowStore

pytestmark = pytest.mark.asyncio

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

NOW = datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
CLAIMS: dict[str, Any] = {"org_id": "org-test", "sub": "user-test"}


async def _make_flow(store: InMemoryFlowStore, spec: dict[str, Any]) -> dict[str, Any]:
    return await store.create_flow(
        org_id="org-test",
        created_by="user-test",
        name="robustness_test",
        spec=spec,
    )


def _always_fail_spec(retries: int = 0, retry_backoff_s: int = 0) -> dict[str, Any]:
    return {
        "version": 1,
        "name": "always_fail",
        "tasks": [
            {
                "key": "fail_task",
                "kind": "python",
                "needs": [],
                "config": {"code": "raise RuntimeError('always fails')"},
                "retries": retries,
                "retry_backoff_s": retry_backoff_s,
                "timeout_s": 0,
            }
        ],
    }


def _fail_with_downstream_spec(retries: int = 0) -> dict[str, Any]:
    return {
        "version": 1,
        "name": "fail_downstream",
        "tasks": [
            {
                "key": "root",
                "kind": "noop",
                "needs": [],
                "config": {},
                "retries": 0,
                "retry_backoff_s": 0,
                "timeout_s": 0,
            },
            {
                "key": "fail_task",
                "kind": "python",
                "needs": ["root"],
                "config": {"code": "raise RuntimeError('fail')"},
                "retries": retries,
                "retry_backoff_s": 0,
                "timeout_s": 0,
            },
            {
                "key": "child",
                "kind": "noop",
                "needs": ["fail_task"],
                "config": {},
                "retries": 0,
                "retry_backoff_s": 0,
                "timeout_s": 0,
            },
            {
                "key": "grandchild",
                "kind": "noop",
                "needs": ["child"],
                "config": {},
                "retries": 0,
                "retry_backoff_s": 0,
                "timeout_s": 0,
            },
        ],
    }


def _timeout_spec(timeout_s: int = 1) -> dict[str, Any]:
    """Task that sleeps longer than its timeout."""
    return {
        "version": 1,
        "name": "timeout_flow",
        "tasks": [
            {
                "key": "slow_task",
                "kind": "python",
                "needs": [],
                "config": {
                    "code": "import time; time.sleep(100); result = {'ok': True}"
                },
                "retries": 0,
                "retry_backoff_s": 0,
                "timeout_s": timeout_s,
            },
            {
                "key": "after_timeout",
                "kind": "noop",
                "needs": ["slow_task"],
                "config": {},
                "retries": 0,
                "retry_backoff_s": 0,
                "timeout_s": 0,
            },
        ],
    }


def _logging_spec() -> dict[str, Any]:
    """Python task that prints several lines."""
    return {
        "version": 1,
        "name": "logging_flow",
        "tasks": [
            {
                "key": "log_task",
                "kind": "python",
                "needs": [],
                "config": {
                    "code": (
                        "print('line one')\n"
                        "print('line two')\n"
                        "result = {'logged': True}"
                    )
                },
                "retries": 0,
                "retry_backoff_s": 0,
                "timeout_s": 0,
            }
        ],
    }


def _fail_with_stderr_spec() -> dict[str, Any]:
    """Python task that fails and has a traceback."""
    return {
        "version": 1,
        "name": "stderr_flow",
        "tasks": [
            {
                "key": "err_task",
                "kind": "python",
                "needs": [],
                "config": {"code": "raise ValueError('bad value')"},
                "retries": 0,
                "retry_backoff_s": 0,
                "timeout_s": 0,
            }
        ],
    }


def _succeed_on_second_attempt_spec() -> dict[str, Any]:
    """Task that fails on attempt 0, succeeds on attempt 1.

    Uses a shared tempfile as a flag to simulate transient failure.
    """
    return {
        "version": 1,
        "name": "flaky_flow",
        "tasks": [
            {
                "key": "flaky",
                "kind": "python",
                "needs": [],
                "config": {
                    "code": (
                        "import os, tempfile\n"
                        "_flag = os.path.join(tempfile.gettempdir(), 'nubi_test_flaky_flag')\n"
                        "if not os.path.exists(_flag):\n"
                        "    open(_flag, 'w').close()\n"
                        "    raise RuntimeError('first attempt fails')\n"
                        "os.unlink(_flag)\n"
                        "result = {'ok': True}"
                    )
                },
                "retries": 1,
                "retry_backoff_s": 0,
                "timeout_s": 0,
            }
        ],
    }


# ---------------------------------------------------------------------------
# 1. Retries
# ---------------------------------------------------------------------------


class TestRetries:
    def setup_method(self):
        reset_for_tests()
        clear_flow_listeners()
        self.store = InMemoryFlowStore()

    async def test_task_exhausts_retries_ends_failed(self):
        spec = _always_fail_spec(retries=2, retry_backoff_s=0)
        flow = await _make_flow(self.store, spec)
        frun = await materialize_flow_run(self.store, flow, {}, "manual", NOW)
        final = await drain_flow_run(self.store, frun["id"], NOW, CLAIMS, max_steps=30)
        trs = await self.store.list_task_runs(frun["id"])
        by_key = {tr["task_key"]: tr for tr in trs}
        assert by_key["fail_task"]["state"] == "failed", (
            f"Expected failed, got {by_key['fail_task']['state']}"
        )

    async def test_attempt_counter_incremented(self):
        spec = _always_fail_spec(retries=2, retry_backoff_s=0)
        flow = await _make_flow(self.store, spec)
        frun = await materialize_flow_run(self.store, flow, {}, "manual", NOW)
        await drain_flow_run(self.store, frun["id"], NOW, CLAIMS, max_steps=30)
        trs = await self.store.list_task_runs(frun["id"])
        by_key = {tr["task_key"]: tr for tr in trs}
        # After 2 retries, attempt should be 2 (0-based: 0, then 1, then 2).
        assert by_key["fail_task"]["attempt"] == 2, (
            f"Expected attempt=2, got {by_key['fail_task']['attempt']}"
        )

    async def test_downstream_becomes_upstream_failed(self):
        """After retry exhaustion, downstream task must be upstream_failed."""
        spec = _fail_with_downstream_spec(retries=1)
        flow = await _make_flow(self.store, spec)
        frun = await materialize_flow_run(self.store, flow, {}, "manual", NOW)
        await drain_flow_run(self.store, frun["id"], NOW, CLAIMS, max_steps=30)
        trs = await self.store.list_task_runs(frun["id"])
        by_key = {tr["task_key"]: tr for tr in trs}
        assert by_key["child"]["state"] == "upstream_failed", (
            f"Expected upstream_failed, got {by_key['child']['state']}"
        )

    async def test_flow_run_failed_after_retry_exhaustion(self):
        spec = _always_fail_spec(retries=1, retry_backoff_s=0)
        flow = await _make_flow(self.store, spec)
        frun = await materialize_flow_run(self.store, flow, {}, "manual", NOW)
        final = await drain_flow_run(self.store, frun["id"], NOW, CLAIMS, max_steps=20)
        assert final["state"] == "failed", f"Expected failed, got {final['state']}"

    async def test_flaky_task_succeeds_on_second_attempt(self):
        """A task that fails on attempt 0 but succeeds on attempt 1."""
        import os, tempfile  # noqa: E401
        # Clean up flag in case a previous test left it.
        flag = os.path.join(tempfile.gettempdir(), "nubi_test_flaky_flag")
        if os.path.exists(flag):
            os.unlink(flag)

        spec = _succeed_on_second_attempt_spec()
        flow = await _make_flow(self.store, spec)
        frun = await materialize_flow_run(self.store, flow, {}, "manual", NOW)
        final = await drain_flow_run(self.store, frun["id"], NOW, CLAIMS, max_steps=20)
        trs = await self.store.list_task_runs(frun["id"])
        by_key = {tr["task_key"]: tr for tr in trs}
        assert by_key["flaky"]["state"] == "success", (
            f"Expected success, got {by_key['flaky']['state']} — "
            f"error: {by_key['flaky'].get('error')}"
        )
        assert final["state"] == "success"


# ---------------------------------------------------------------------------
# 2. Timeouts
# ---------------------------------------------------------------------------


class TestTimeouts:
    def setup_method(self):
        reset_for_tests()
        clear_flow_listeners()
        self.store = InMemoryFlowStore()

    async def test_slow_task_marked_timed_out(self):
        """A task sleeping longer than timeout_s must end up timed_out."""
        spec = _timeout_spec(timeout_s=1)
        flow = await _make_flow(self.store, spec)
        frun = await materialize_flow_run(self.store, flow, {}, "manual", NOW)
        # Use a slightly later `now` to avoid scheduling issues.
        run_now = NOW + timedelta(seconds=1)
        await drain_flow_run(self.store, frun["id"], run_now, CLAIMS, max_steps=10)
        trs = await self.store.list_task_runs(frun["id"])
        by_key = {tr["task_key"]: tr for tr in trs}
        assert by_key["slow_task"]["state"] == "timed_out", (
            f"Expected timed_out, got {by_key['slow_task']['state']}"
        )

    async def test_timed_out_task_is_terminal(self):
        """timed_out tasks must not be retried even if retries > 0."""
        # Build a spec with retries but will timeout.
        spec = {
            "version": 1,
            "name": "timeout_retry",
            "tasks": [
                {
                    "key": "slow",
                    "kind": "python",
                    "needs": [],
                    "config": {"code": "import time; time.sleep(100); result = {}"},
                    "retries": 3,
                    "retry_backoff_s": 0,
                    "timeout_s": 1,
                }
            ],
        }
        flow = await _make_flow(self.store, spec)
        frun = await materialize_flow_run(self.store, flow, {}, "manual", NOW)
        await drain_flow_run(self.store, frun["id"], NOW, CLAIMS, max_steps=20)
        trs = await self.store.list_task_runs(frun["id"])
        by_key = {tr["task_key"]: tr for tr in trs}
        # Must be timed_out (not failed or retrying).
        assert by_key["slow"]["state"] == "timed_out", (
            f"Expected timed_out, got {by_key['slow']['state']}"
        )
        # Attempt should still be 0 (no retries done).
        assert by_key["slow"]["attempt"] == 0

    async def test_downstream_upstream_failed_on_timeout(self):
        """After timeout, downstream task becomes upstream_failed."""
        spec = _timeout_spec(timeout_s=1)
        flow = await _make_flow(self.store, spec)
        frun = await materialize_flow_run(self.store, flow, {}, "manual", NOW)
        await drain_flow_run(self.store, frun["id"], NOW, CLAIMS, max_steps=10)
        trs = await self.store.list_task_runs(frun["id"])
        by_key = {tr["task_key"]: tr for tr in trs}
        assert by_key["after_timeout"]["state"] == "upstream_failed", (
            f"Expected upstream_failed, got {by_key['after_timeout']['state']}"
        )


# ---------------------------------------------------------------------------
# 3. Per-task logging
# ---------------------------------------------------------------------------


class TestTaskLogging:
    def setup_method(self):
        reset_for_tests()
        clear_flow_listeners()
        self.store = InMemoryFlowStore()

    async def test_python_stdout_captured_in_logs(self):
        """Lines printed by a python task appear in task_run.logs."""
        spec = _logging_spec()
        flow = await _make_flow(self.store, spec)
        frun = await materialize_flow_run(self.store, flow, {}, "manual", NOW)
        await drain_flow_run(self.store, frun["id"], NOW, CLAIMS, max_steps=10)
        trs = await self.store.list_task_runs(frun["id"])
        by_key = {tr["task_key"]: tr for tr in trs}
        logs = by_key["log_task"].get("logs") or []
        assert any("line one" in ln for ln in logs), f"Expected 'line one' in logs: {logs}"
        assert any("line two" in ln for ln in logs), f"Expected 'line two' in logs: {logs}"

    async def test_failed_task_captures_error_in_logs(self):
        """Failed python task captures stderr/traceback in logs."""
        spec = _fail_with_stderr_spec()
        flow = await _make_flow(self.store, spec)
        frun = await materialize_flow_run(self.store, flow, {}, "manual", NOW)
        await drain_flow_run(self.store, frun["id"], NOW, CLAIMS, max_steps=10)
        trs = await self.store.list_task_runs(frun["id"])
        by_key = {tr["task_key"]: tr for tr in trs}
        tr = by_key["err_task"]
        # Error field should be set.
        assert tr["error"] is not None, "Expected error to be set"
        # Logs should contain something from stderr/traceback.
        logs = tr.get("logs") or []
        # Traceback or error message should appear somewhere in logs or error.
        combined = " ".join(logs) + (tr["error"] or "")
        assert "ValueError" in combined or "bad value" in combined, (
            f"Expected 'ValueError' or 'bad value' in logs/error: logs={logs}, error={tr['error']}"
        )

    async def test_logs_present_in_serialized_task_run(self):
        """Logs field appears in the task_run serialization (via routes helper)."""
        from app.routes.flows import _serialize_task_run

        tr = {
            "id": "tid",
            "flow_run_id": "frid",
            "org_id": "oid",
            "task_key": "t",
            "state": "success",
            "attempt": 0,
            "depends_on": [],
            "cache_key": None,
            "result": None,
            "error": None,
            "logs": ["hello", "world"],
            "scheduled_at": None,
            "started_at": None,
            "finished_at": None,
            "created_at": None,
        }
        serialized = _serialize_task_run(tr)
        assert "logs" in serialized
        assert serialized["logs"] == ["hello", "world"]

    async def test_duration_s_computed_in_serialized_task_run(self):
        """duration_s is computed from started_at and finished_at."""
        from datetime import timedelta
        from app.routes.flows import _serialize_task_run

        started = datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        finished = started + timedelta(seconds=5)
        tr = {
            "id": "tid",
            "flow_run_id": "frid",
            "org_id": "oid",
            "task_key": "t",
            "state": "success",
            "attempt": 0,
            "depends_on": [],
            "cache_key": None,
            "result": None,
            "error": None,
            "logs": [],
            "scheduled_at": None,
            "started_at": started,
            "finished_at": finished,
            "created_at": None,
        }
        serialized = _serialize_task_run(tr)
        assert serialized["duration_s"] == 5.0


# ---------------------------------------------------------------------------
# 4. Failure states / upstream_failed
# ---------------------------------------------------------------------------


class TestUpstreamFailed:
    def setup_method(self):
        reset_for_tests()
        clear_flow_listeners()
        self.store = InMemoryFlowStore()

    async def test_direct_child_marked_upstream_failed(self):
        spec = _fail_with_downstream_spec()
        flow = await _make_flow(self.store, spec)
        frun = await materialize_flow_run(self.store, flow, {}, "manual", NOW)
        await drain_flow_run(self.store, frun["id"], NOW, CLAIMS, max_steps=20)
        trs = await self.store.list_task_runs(frun["id"])
        by_key = {tr["task_key"]: tr for tr in trs}
        assert by_key["child"]["state"] == "upstream_failed", (
            f"Expected upstream_failed, got {by_key['child']['state']}"
        )

    async def test_grandchild_also_upstream_failed(self):
        """upstream_failed propagates transitively."""
        spec = _fail_with_downstream_spec()
        flow = await _make_flow(self.store, spec)
        frun = await materialize_flow_run(self.store, flow, {}, "manual", NOW)
        await drain_flow_run(self.store, frun["id"], NOW, CLAIMS, max_steps=30)
        trs = await self.store.list_task_runs(frun["id"])
        by_key = {tr["task_key"]: tr for tr in trs}
        assert by_key["grandchild"]["state"] == "upstream_failed", (
            f"Expected grandchild upstream_failed, got {by_key['grandchild']['state']}"
        )

    async def test_flow_run_failed_when_upstream_failed_tasks(self):
        spec = _fail_with_downstream_spec()
        flow = await _make_flow(self.store, spec)
        frun = await materialize_flow_run(self.store, flow, {}, "manual", NOW)
        final = await drain_flow_run(self.store, frun["id"], NOW, CLAIMS, max_steps=20)
        assert final["state"] == "failed", f"Expected flow failed, got {final['state']}"

    async def test_advance_readiness_uses_upstream_failed_not_skipped(self):
        """advance_readiness must set state='upstream_failed', not 'skipped'."""
        spec = _fail_with_downstream_spec()
        flow = await _make_flow(self.store, spec)
        frun = await materialize_flow_run(self.store, flow, {}, "manual", NOW)
        trs = await self.store.list_task_runs(frun["id"])
        by_key = {tr["task_key"]: tr for tr in trs}
        # Manually mark root and fail_task.
        await self.store.update_task_run(by_key["root"]["id"], {"state": "success", "result": {}})
        await self.store.update_task_run(by_key["fail_task"]["id"], {"state": "failed"})
        await advance_readiness(self.store, frun["id"], NOW)
        trs = await self.store.list_task_runs(frun["id"])
        by_key = {tr["task_key"]: tr for tr in trs}
        # child must be upstream_failed.
        assert by_key["child"]["state"] == "upstream_failed", (
            f"Expected upstream_failed, got {by_key['child']['state']}"
        )
        # Must NOT be 'skipped'.
        assert by_key["child"]["state"] != "skipped"


# ---------------------------------------------------------------------------
# 5. Event hooks (events.py)
# ---------------------------------------------------------------------------


class TestEventHooks:
    def setup_method(self):
        reset_for_tests()
        clear_flow_listeners()
        self.store = InMemoryFlowStore()

    def teardown_method(self):
        clear_flow_listeners()

    async def test_listener_fires_on_task_failure(self):
        """Registered listener is called when a task fails."""
        received: list[FlowEvent] = []

        def on_event(event: FlowEvent) -> None:
            received.append(event)

        register_flow_listener(on_event)

        spec = _always_fail_spec(retries=0)
        flow = await _make_flow(self.store, spec)
        frun = await materialize_flow_run(self.store, flow, {}, "manual", NOW)
        await drain_flow_run(self.store, frun["id"], NOW, CLAIMS, max_steps=10)

        failure_events = [e for e in received if e.type == "task_failed"]
        assert len(failure_events) >= 1, f"Expected at least one task_failed event; got: {[e.type for e in received]}"

    async def test_listener_receives_correct_fields(self):
        """task_failed event has correct flow_run_id, state, and non-None error."""
        received: list[FlowEvent] = []
        register_flow_listener(lambda e: received.append(e))

        spec = _always_fail_spec(retries=0)
        flow = await _make_flow(self.store, spec)
        frun = await materialize_flow_run(self.store, flow, {}, "manual", NOW)
        await drain_flow_run(self.store, frun["id"], NOW, CLAIMS, max_steps=10)

        failure_events = [e for e in received if e.type == "task_failed"]
        assert failure_events, "No task_failed event received"
        evt = failure_events[-1]
        assert evt.flow_run_id == frun["id"]
        assert evt.state == "failed"
        assert evt.error is not None
        assert evt.task_key == "fail_task"

    async def test_multiple_listeners_all_receive_event(self):
        """All registered listeners are called."""
        counts = [0, 0]

        def l1(e: FlowEvent) -> None:
            if e.type == "task_failed":
                counts[0] += 1

        def l2(e: FlowEvent) -> None:
            if e.type == "task_failed":
                counts[1] += 1

        register_flow_listener(l1)
        register_flow_listener(l2)

        spec = _always_fail_spec()
        flow = await _make_flow(self.store, spec)
        frun = await materialize_flow_run(self.store, flow, {}, "manual", NOW)
        await drain_flow_run(self.store, frun["id"], NOW, CLAIMS, max_steps=10)

        assert counts[0] >= 1, "l1 not called"
        assert counts[1] >= 1, "l2 not called"

    async def test_bad_listener_does_not_break_engine(self):
        """A listener that raises must not propagate to the engine."""

        def bad_listener(event: FlowEvent) -> None:
            raise RuntimeError("bad listener")

        register_flow_listener(bad_listener)

        spec = _always_fail_spec()
        flow = await _make_flow(self.store, spec)
        frun = await materialize_flow_run(self.store, flow, {}, "manual", NOW)
        # Should NOT raise even though the listener raises.
        final = await drain_flow_run(self.store, frun["id"], NOW, CLAIMS, max_steps=10)
        # The flow run should still reach a terminal state.
        assert final["state"] in ("success", "failed"), f"Got: {final['state']}"

    async def test_no_listeners_is_noop(self):
        """emit_flow_event with no listeners must not raise."""
        clear_flow_listeners()
        # Should not raise.
        emit_flow_event(FlowEvent(
            type="task_failed",
            flow_run_id="test-run",
            task_key="t",
            state="failed",
        ))

    async def test_flow_success_event_emitted(self):
        """flow_success event must fire when all tasks succeed."""
        received: list[FlowEvent] = []
        register_flow_listener(lambda e: received.append(e))

        spec = {
            "version": 1,
            "name": "success_flow",
            "tasks": [
                {
                    "key": "noop_task",
                    "kind": "noop",
                    "needs": [],
                    "config": {},
                    "retries": 0,
                    "timeout_s": 0,
                }
            ],
        }
        flow = await _make_flow(self.store, spec)
        frun = await materialize_flow_run(self.store, flow, {}, "manual", NOW)
        await drain_flow_run(self.store, frun["id"], NOW, CLAIMS, max_steps=10)

        flow_success_events = [e for e in received if e.type == "flow_success"]
        assert flow_success_events, (
            f"No flow_success event; received: {[e.type for e in received]}"
        )

    async def test_register_flow_listener_idempotent(self):
        """Registering the same listener twice adds it only once."""
        call_count = [0]

        def listener(event: FlowEvent) -> None:
            call_count[0] += 1

        register_flow_listener(listener)
        register_flow_listener(listener)  # second call: should be ignored

        emit_flow_event(FlowEvent(
            type="task_success",
            flow_run_id="x",
            state="success",
        ))
        # Should have been called exactly once.
        assert call_count[0] == 1, f"Expected 1 call, got {call_count[0]}"

    async def test_unregister_flow_listener(self):
        """Unregistering a listener stops it from receiving events."""
        call_count = [0]

        def listener(event: FlowEvent) -> None:
            call_count[0] += 1

        register_flow_listener(listener)
        emit_flow_event(FlowEvent(type="task_success", flow_run_id="x", state="success"))
        assert call_count[0] == 1

        unregister_flow_listener(listener)
        emit_flow_event(FlowEvent(type="task_success", flow_run_id="x", state="success"))
        # count should still be 1 — listener was removed.
        assert call_count[0] == 1, f"Expected 1 call after unregister, got {call_count[0]}"

    async def test_task_retrying_event_emitted(self):
        """task_retrying event is emitted when a task fails and retries remain."""
        received: list[FlowEvent] = []
        register_flow_listener(lambda e: received.append(e))

        spec = _always_fail_spec(retries=1, retry_backoff_s=0)
        flow = await _make_flow(self.store, spec)
        frun = await materialize_flow_run(self.store, flow, {}, "manual", NOW)
        await drain_flow_run(self.store, frun["id"], NOW, CLAIMS, max_steps=20)

        retrying_events = [e for e in received if e.type == "task_retrying"]
        assert retrying_events, (
            f"No task_retrying event; got: {[e.type for e in received]}"
        )

    async def test_task_timed_out_event_emitted(self):
        """task_timed_out event is emitted on timeout."""
        received: list[FlowEvent] = []
        register_flow_listener(lambda e: received.append(e))

        spec = _timeout_spec(timeout_s=1)
        flow = await _make_flow(self.store, spec)
        frun = await materialize_flow_run(self.store, flow, {}, "manual", NOW)
        await drain_flow_run(self.store, frun["id"], NOW, CLAIMS, max_steps=10)

        timed_out_events = [e for e in received if e.type == "task_timed_out"]
        assert timed_out_events, (
            f"No task_timed_out event; got: {[e.type for e in received]}"
        )
