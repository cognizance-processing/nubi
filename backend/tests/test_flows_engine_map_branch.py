"""Integration tests for map (fan-out) and branch (conditional routing) execution.

Coverage
--------
1. Map execution — dynamic fan-out with 3 items × 2 body tasks
   a. Map handler resolves item_expr to a list.
   b. Child task_runs are created with composite keys.
   c. Map task_run transitions to 'waiting_children'.
   d. Body tasks execute successfully.
   e. Map task_run transitions to 'success' with collected results.
   f. flow_run reaches 'success'.

2. Map with a failing child — map node fails
   a. One child task fails → map task_run transitions to 'failed'.
   b. flow_run reaches 'failed'.

3. Branch — taken condition activates matching tasks
   a. Branch handler evaluates the first matching condition.
   b. Matching downstream task_run becomes 'ready'.
   c. Non-matching downstream task_run becomes 'upstream_failed'.
   d. flow_run reaches 'success'.

4. Branch — default path when no condition matches
   a. No condition matches → default tasks activated.
   b. All non-default downstream tasks become 'upstream_failed'.

5. Branch — else_ optional (no default, no match → branch fails)
   a. RuntimeError propagates as task 'failed'.
   b. flow_run reaches 'failed'.

6. Existing tests still pass (linear, diamond, retries) — regression guard.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest

from app.flows.executor import TaskContext
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


NOW = datetime(2025, 6, 1, 10, 0, 0, tzinfo=timezone.utc)
CLAIMS: dict[str, Any] = {"org_id": "org-test", "sub": "user-test"}


async def _make_flow(
    store: InMemoryFlowStore,
    spec: dict[str, Any],
    name: str = "test_flow",
) -> dict[str, Any]:
    return await store.create_flow(
        org_id="org-test",
        created_by="user-test",
        name=name,
        spec=spec,
    )


def _register_map_branch(registry=None):
    """Register map and branch handlers in the registry for tests."""
    if registry is None:
        registry = get_task_kind_registry()
    from app.flows.handlers.map import handle_map
    from app.flows.handlers.branch import handle_branch
    registry.register("map", handle_map)
    registry.register("branch", handle_branch)


# ---------------------------------------------------------------------------
# 1. Map execution — 3 items × 2 body tasks
# ---------------------------------------------------------------------------


def _map_spec_3_items() -> dict[str, Any]:
    """Flow: source (noop produces items list) → map → noop(collect).

    The map body: body_a (noop) → body_b (noop, depends on body_a).
    collect_key = body_b (last).
    """
    return {
        "version": 1,
        "name": "map_flow",
        "params": [],
        "tasks": [
            {
                "key": "source",
                "kind": "noop",
                "needs": [],
                "config": {},
                "retries": 0,
                "retry_backoff_s": 30,
                "timeout_s": 0,
                "cache_ttl_s": 0,
                "ui": {"x": 0, "y": 0},
            },
            {
                "key": "process_each",
                "kind": "map",
                "needs": ["source"],
                "config": {
                    "item_expr": "{{ inputs.source.items }}",
                    "item_var": "item",
                    "max_concurrency": 0,
                    "max_map_size": 1000,
                    "collect_key": "body_b",
                    "body": [
                        {
                            "key": "body_a",
                            "kind": "noop",
                            "needs": [],
                            "config": {"step": "a"},
                            "retries": 0,
                            "retry_backoff_s": 30,
                            "timeout_s": 0,
                            "cache_ttl_s": 0,
                            "ui": {"x": 0, "y": 0},
                        },
                        {
                            "key": "body_b",
                            "kind": "noop",
                            "needs": ["body_a"],
                            "config": {"step": "b"},
                            "retries": 0,
                            "retry_backoff_s": 30,
                            "timeout_s": 0,
                            "cache_ttl_s": 0,
                            "ui": {"x": 260, "y": 0},
                        },
                    ],
                },
                "retries": 0,
                "retry_backoff_s": 30,
                "timeout_s": 0,
                "cache_ttl_s": 0,
                "ui": {"x": 320, "y": 200},
            },
            {
                "key": "aggregate",
                "kind": "noop",
                "needs": ["process_each"],
                "config": {},
                "retries": 0,
                "retry_backoff_s": 30,
                "timeout_s": 0,
                "cache_ttl_s": 0,
                "ui": {"x": 640, "y": 200},
            },
        ],
    }


class TestMapExecution:
    """Map fan-out: 3 items × 2 body tasks."""

    def setup_method(self):
        reset_for_tests()
        _register_map_branch()
        self.store = InMemoryFlowStore()

    async def test_child_task_runs_created(self):
        """After map executes, 3×2=6 child task_runs should exist."""
        spec = _map_spec_3_items()
        flow = await _make_flow(self.store, spec)

        # Override source to return a list via a python task instead.
        # We'll inject the result directly via the store.
        frun = await materialize_flow_run(self.store, flow, {}, "manual", NOW)

        # Manually give source a result containing items.
        trs = await self.store.list_task_runs(frun["id"])
        by_key = {tr["task_key"]: tr for tr in trs}
        await self.store.update_task_run(
            by_key["source"]["id"],
            {"state": "success", "result": {"items": [{"v": 1}, {"v": 2}, {"v": 3}]}},
        )
        await advance_readiness(self.store, frun["id"], NOW)

        # Now run the map task.
        await drain_flow_run(self.store, frun["id"], NOW, CLAIMS, max_steps=50)

        all_trs = await self.store.list_task_runs(frun["id"])
        child_keys = [
            tr["task_key"] for tr in all_trs
            if tr["task_key"].startswith("process_each[")
        ]
        # 3 items × 2 body tasks = 6 child runs.
        assert len(child_keys) == 6, f"Expected 6 child task_runs, got: {child_keys}"

    async def test_map_transitions_to_waiting_children(self):
        """Map task_run should enter 'waiting_children' after handler runs."""
        spec = _map_spec_3_items()
        flow = await _make_flow(self.store, spec)
        frun = await materialize_flow_run(self.store, flow, {}, "manual", NOW)

        trs = await self.store.list_task_runs(frun["id"])
        by_key = {tr["task_key"]: tr for tr in trs}
        await self.store.update_task_run(
            by_key["source"]["id"],
            {"state": "success", "result": {"items": [{"v": 1}, {"v": 2}]}},
        )
        await advance_readiness(self.store, frun["id"], NOW)

        # Run only the map task (one step after source is already done).
        from app.flows.runtime import _claim_for_flow_run, _execute_claimed_task_run
        task_run = await _claim_for_flow_run(self.store, frun["id"], NOW)
        assert task_run is not None
        assert task_run["task_key"] == "process_each"
        await _execute_claimed_task_run(self.store, task_run, NOW, CLAIMS)

        # Check map task_run is waiting_children.
        map_tr = (await self.store.list_task_runs(frun["id"]))
        map_tr_by_key = {tr["task_key"]: tr for tr in map_tr}
        assert map_tr_by_key["process_each"]["state"] == "waiting_children", (
            f"map state: {map_tr_by_key['process_each']['state']}"
        )

    async def test_map_collects_results_on_child_success(self):
        """After all children succeed, map transitions to 'success' with collected results."""
        spec = _map_spec_3_items()
        flow = await _make_flow(self.store, spec)
        frun = await materialize_flow_run(self.store, flow, {}, "manual", NOW)

        trs = await self.store.list_task_runs(frun["id"])
        by_key = {tr["task_key"]: tr for tr in trs}
        await self.store.update_task_run(
            by_key["source"]["id"],
            {"state": "success", "result": {"items": [{"v": 10}, {"v": 20}]}},
        )
        await advance_readiness(self.store, frun["id"], NOW)

        # Full drain.
        final = await drain_flow_run(self.store, frun["id"], NOW, CLAIMS, max_steps=100)

        all_trs = await self.store.list_task_runs(frun["id"])
        by_key = {tr["task_key"]: tr for tr in all_trs}

        # Map task_run must be 'success'.
        assert by_key["process_each"]["state"] == "success", (
            f"map state: {by_key['process_each']['state']}"
        )
        # Collected result must have items list.
        map_result = by_key["process_each"].get("result") or {}
        assert "items" in map_result, f"map result: {map_result}"
        assert map_result["item_count"] == 2

    async def test_flow_run_reaches_success(self):
        """A map flow should reach 'success' end-to-end."""
        spec = _map_spec_3_items()
        flow = await _make_flow(self.store, spec)
        frun = await materialize_flow_run(self.store, flow, {}, "manual", NOW)

        trs = await self.store.list_task_runs(frun["id"])
        by_key = {tr["task_key"]: tr for tr in trs}
        await self.store.update_task_run(
            by_key["source"]["id"],
            {"state": "success", "result": {"items": [{"v": 1}, {"v": 2}, {"v": 3}]}},
        )
        await advance_readiness(self.store, frun["id"], NOW)

        final = await drain_flow_run(self.store, frun["id"], NOW, CLAIMS, max_steps=200)
        assert final["state"] == "success", f"flow_run state: {final['state']}"

    async def test_child_keys_use_composite_format(self):
        """Child task_run keys must follow '{map_key}[{i}].{child_key}' format."""
        spec = _map_spec_3_items()
        flow = await _make_flow(self.store, spec)
        frun = await materialize_flow_run(self.store, flow, {}, "manual", NOW)

        trs = await self.store.list_task_runs(frun["id"])
        by_key = {tr["task_key"]: tr for tr in trs}
        await self.store.update_task_run(
            by_key["source"]["id"],
            {"state": "success", "result": {"items": [{"v": 1}, {"v": 2}]}},
        )
        await advance_readiness(self.store, frun["id"], NOW)
        await drain_flow_run(self.store, frun["id"], NOW, CLAIMS, max_steps=100)

        all_trs = await self.store.list_task_runs(frun["id"])
        child_keys = sorted(
            tr["task_key"] for tr in all_trs
            if tr["task_key"].startswith("process_each[")
        )
        expected_keys = sorted([
            "process_each[0].body_a",
            "process_each[0].body_b",
            "process_each[1].body_a",
            "process_each[1].body_b",
        ])
        assert child_keys == expected_keys, f"Got: {child_keys}"

    async def test_map_with_python_body_task_injects_item(self):
        """Python body tasks should receive the 'item' variable in their code."""
        spec = {
            "version": 1,
            "name": "map_python_flow",
            "params": [],
            "tasks": [
                {
                    "key": "source",
                    "kind": "noop",
                    "needs": [],
                    "config": {},
                    "retries": 0,
                    "retry_backoff_s": 30,
                    "timeout_s": 0,
                    "cache_ttl_s": 0,
                    "ui": {"x": 0, "y": 0},
                },
                {
                    "key": "fanout",
                    "kind": "map",
                    "needs": ["source"],
                    "config": {
                        "item_expr": "{{ inputs.source.rows }}",
                        "item_var": "item",
                        "max_concurrency": 0,
                        "max_map_size": 100,
                        "collect_key": "process",
                        "body": [
                            {
                                "key": "process",
                                "kind": "python",
                                "needs": [],
                                "config": {
                                    "code": "result = {'doubled': item['n'] * 2}"
                                },
                                "retries": 0,
                                "retry_backoff_s": 30,
                                "timeout_s": 30,
                                "cache_ttl_s": 0,
                                "ui": {"x": 0, "y": 0},
                            },
                        ],
                    },
                    "retries": 0,
                    "retry_backoff_s": 30,
                    "timeout_s": 0,
                    "cache_ttl_s": 0,
                    "ui": {"x": 260, "y": 0},
                },
            ],
        }
        flow = await _make_flow(self.store, spec)
        frun = await materialize_flow_run(self.store, flow, {}, "manual", NOW)

        trs = await self.store.list_task_runs(frun["id"])
        by_key = {tr["task_key"]: tr for tr in trs}
        # Give source rows = [{"n": 5}, {"n": 10}]
        await self.store.update_task_run(
            by_key["source"]["id"],
            {"state": "success", "result": {"rows": [{"n": 5}, {"n": 10}]}},
        )
        await advance_readiness(self.store, frun["id"], NOW)

        final = await drain_flow_run(self.store, frun["id"], NOW, CLAIMS, max_steps=100)
        assert final["state"] == "success", f"flow_run state: {final['state']}"

        all_trs = await self.store.list_task_runs(frun["id"])
        fanout_tr = next(tr for tr in all_trs if tr["task_key"] == "fanout")
        assert fanout_tr["state"] == "success"
        result = fanout_tr.get("result") or {}
        assert result.get("item_count") == 2
        # Items should have doubled values: [{index:0, result:{doubled:10}}, ...]
        items = result.get("items", [])
        assert len(items) == 2
        results_by_index = {it["index"]: it["result"] for it in items}
        assert results_by_index[0].get("doubled") == 10, f"items: {items}"
        assert results_by_index[1].get("doubled") == 20, f"items: {items}"


# ---------------------------------------------------------------------------
# 2. Map with a failing child
# ---------------------------------------------------------------------------


class TestMapChildFailure:
    """When a child task fails, the map node should fail."""

    def setup_method(self):
        reset_for_tests()
        _register_map_branch()
        self.store = InMemoryFlowStore()

    async def test_map_fails_when_child_fails(self):
        """One failing child → map task_run state 'failed'."""
        spec = {
            "version": 1,
            "name": "map_fail_flow",
            "params": [],
            "tasks": [
                {
                    "key": "source",
                    "kind": "noop",
                    "needs": [],
                    "config": {},
                    "retries": 0,
                    "retry_backoff_s": 30,
                    "timeout_s": 0,
                    "cache_ttl_s": 0,
                    "ui": {"x": 0, "y": 0},
                },
                {
                    "key": "fanout",
                    "kind": "map",
                    "needs": ["source"],
                    "config": {
                        "item_expr": "{{ inputs.source.items }}",
                        "item_var": "item",
                        "max_concurrency": 0,
                        "max_map_size": 100,
                        "collect_key": "fail_task",
                        "body": [
                            {
                                "key": "fail_task",
                                "kind": "python",
                                "needs": [],
                                "config": {
                                    "code": "raise RuntimeError('intentional child failure')"
                                },
                                "retries": 0,
                                "retry_backoff_s": 0,
                                "timeout_s": 30,
                                "cache_ttl_s": 0,
                                "ui": {"x": 0, "y": 0},
                            },
                        ],
                    },
                    "retries": 0,
                    "retry_backoff_s": 30,
                    "timeout_s": 0,
                    "cache_ttl_s": 0,
                    "ui": {"x": 260, "y": 0},
                },
            ],
        }
        flow = await _make_flow(self.store, spec)
        frun = await materialize_flow_run(self.store, flow, {}, "manual", NOW)

        trs = await self.store.list_task_runs(frun["id"])
        by_key = {tr["task_key"]: tr for tr in trs}
        await self.store.update_task_run(
            by_key["source"]["id"],
            {"state": "success", "result": {"items": [{"v": 1}, {"v": 2}]}},
        )
        await advance_readiness(self.store, frun["id"], NOW)

        final = await drain_flow_run(self.store, frun["id"], NOW, CLAIMS, max_steps=50)

        all_trs = await self.store.list_task_runs(frun["id"])
        by_key = {tr["task_key"]: tr for tr in all_trs}
        assert by_key["fanout"]["state"] == "failed", (
            f"fanout state: {by_key['fanout']['state']}"
        )
        assert final["state"] == "failed", f"flow_run state: {final['state']}"


# ---------------------------------------------------------------------------
# 3. Branch — taken condition activates matching tasks
# ---------------------------------------------------------------------------


def _branch_spec(default_next: list[str] | None = None) -> dict[str, Any]:
    """Flow: classify (noop) → route (branch) → enrich or archive."""
    return {
        "version": 1,
        "name": "branch_flow",
        "params": [],
        "tasks": [
            {
                "key": "classify",
                "kind": "noop",
                "needs": [],
                "config": {},
                "retries": 0,
                "retry_backoff_s": 30,
                "timeout_s": 0,
                "cache_ttl_s": 0,
                "ui": {"x": 0, "y": 0},
            },
            {
                "key": "route",
                "kind": "branch",
                "needs": ["classify"],
                "config": {
                    "conditions": [
                        {
                            "when": "'{{ inputs.classify.label }}' == 'high'",
                            "next": ["enrich"],
                        },
                        {
                            "when": "'{{ inputs.classify.label }}' == 'low'",
                            "next": ["archive"],
                        },
                    ],
                    "default": default_next or [],
                },
                "retries": 0,
                "retry_backoff_s": 30,
                "timeout_s": 0,
                "cache_ttl_s": 0,
                "ui": {"x": 260, "y": 0},
            },
            {
                "key": "enrich",
                "kind": "noop",
                "needs": ["route"],
                "config": {},
                "retries": 0,
                "retry_backoff_s": 30,
                "timeout_s": 0,
                "cache_ttl_s": 0,
                "ui": {"x": 520, "y": -100},
            },
            {
                "key": "archive",
                "kind": "noop",
                "needs": ["route"],
                "config": {},
                "retries": 0,
                "retry_backoff_s": 30,
                "timeout_s": 0,
                "cache_ttl_s": 0,
                "ui": {"x": 520, "y": 100},
            },
        ],
    }


class TestBranchTakenPath:
    """Branch: first matching condition activates the right tasks."""

    def setup_method(self):
        reset_for_tests()
        _register_map_branch()
        self.store = InMemoryFlowStore()

    async def _run_with_label(self, label: str) -> dict[str, dict[str, Any]]:
        """Run the branch flow with classify.label = label.  Return trs by key."""
        spec = _branch_spec()
        flow = await _make_flow(self.store, spec)
        frun = await materialize_flow_run(self.store, flow, {}, "manual", NOW)

        trs = await self.store.list_task_runs(frun["id"])
        by_key = {tr["task_key"]: tr for tr in trs}
        # Inject classify result.
        await self.store.update_task_run(
            by_key["classify"]["id"],
            {"state": "success", "result": {"label": label}},
        )
        await advance_readiness(self.store, frun["id"], NOW)

        await drain_flow_run(self.store, frun["id"], NOW, CLAIMS, max_steps=50)

        all_trs = await self.store.list_task_runs(frun["id"])
        return {tr["task_key"]: tr for tr in all_trs}

    async def test_high_label_activates_enrich(self):
        by_key = await self._run_with_label("high")
        assert by_key["enrich"]["state"] == "success", (
            f"enrich state: {by_key['enrich']['state']}"
        )

    async def test_high_label_deactivates_archive(self):
        by_key = await self._run_with_label("high")
        assert by_key["archive"]["state"] == "upstream_failed", (
            f"archive state: {by_key['archive']['state']}"
        )

    async def test_low_label_activates_archive(self):
        by_key = await self._run_with_label("low")
        assert by_key["archive"]["state"] == "success", (
            f"archive state: {by_key['archive']['state']}"
        )

    async def test_low_label_deactivates_enrich(self):
        by_key = await self._run_with_label("low")
        assert by_key["enrich"]["state"] == "upstream_failed", (
            f"enrich state: {by_key['enrich']['state']}"
        )

    async def test_flow_run_reaches_success_high(self):
        spec = _branch_spec()
        flow = await _make_flow(self.store, spec)
        frun = await materialize_flow_run(self.store, flow, {}, "manual", NOW)

        trs = await self.store.list_task_runs(frun["id"])
        by_key = {tr["task_key"]: tr for tr in trs}
        await self.store.update_task_run(
            by_key["classify"]["id"],
            {"state": "success", "result": {"label": "high"}},
        )
        await advance_readiness(self.store, frun["id"], NOW)

        final = await drain_flow_run(self.store, frun["id"], NOW, CLAIMS, max_steps=50)
        assert final["state"] == "success", f"flow_run: {final['state']}"

    async def test_branch_result_contains_branch_taken(self):
        """Branch task_run result should contain 'branch_taken' label."""
        by_key = await self._run_with_label("high")
        route_result = by_key["route"].get("result") or {}
        assert route_result.get("branch_taken") == "condition_0", (
            f"route result: {route_result}"
        )


# ---------------------------------------------------------------------------
# 4. Branch — default path
# ---------------------------------------------------------------------------


class TestBranchDefaultPath:
    """Branch falls through to default when no condition matches."""

    def setup_method(self):
        reset_for_tests()
        _register_map_branch()
        self.store = InMemoryFlowStore()

    async def test_default_path_taken_when_no_condition_matches(self):
        """Unrecognised label → default tasks activated."""
        spec = {
            "version": 1,
            "name": "branch_default_flow",
            "params": [],
            "tasks": [
                {
                    "key": "classify",
                    "kind": "noop",
                    "needs": [],
                    "config": {},
                    "retries": 0,
                    "retry_backoff_s": 30,
                    "timeout_s": 0,
                    "cache_ttl_s": 0,
                    "ui": {"x": 0, "y": 0},
                },
                {
                    "key": "route",
                    "kind": "branch",
                    "needs": ["classify"],
                    "config": {
                        "conditions": [
                            {"when": "'{{ inputs.classify.label }}' == 'premium'", "next": ["premium_task"]},
                        ],
                        "default": ["fallback_task"],
                    },
                    "retries": 0,
                    "retry_backoff_s": 30,
                    "timeout_s": 0,
                    "cache_ttl_s": 0,
                    "ui": {"x": 260, "y": 0},
                },
                {
                    "key": "premium_task",
                    "kind": "noop",
                    "needs": ["route"],
                    "config": {},
                    "retries": 0,
                    "retry_backoff_s": 30,
                    "timeout_s": 0,
                    "cache_ttl_s": 0,
                    "ui": {"x": 520, "y": -100},
                },
                {
                    "key": "fallback_task",
                    "kind": "noop",
                    "needs": ["route"],
                    "config": {},
                    "retries": 0,
                    "retry_backoff_s": 30,
                    "timeout_s": 0,
                    "cache_ttl_s": 0,
                    "ui": {"x": 520, "y": 100},
                },
            ],
        }

        flow = await _make_flow(self.store, spec)
        frun = await materialize_flow_run(self.store, flow, {}, "manual", NOW)

        trs = await self.store.list_task_runs(frun["id"])
        by_key = {tr["task_key"]: tr for tr in trs}
        # "standard" label matches no condition → hits default.
        await self.store.update_task_run(
            by_key["classify"]["id"],
            {"state": "success", "result": {"label": "standard"}},
        )
        await advance_readiness(self.store, frun["id"], NOW)

        final = await drain_flow_run(self.store, frun["id"], NOW, CLAIMS, max_steps=50)

        all_trs = await self.store.list_task_runs(frun["id"])
        by_key = {tr["task_key"]: tr for tr in all_trs}

        assert by_key["fallback_task"]["state"] == "success", (
            f"fallback_task: {by_key['fallback_task']['state']}"
        )
        assert by_key["premium_task"]["state"] == "upstream_failed", (
            f"premium_task: {by_key['premium_task']['state']}"
        )
        assert final["state"] == "success", f"flow_run: {final['state']}"


# ---------------------------------------------------------------------------
# 5. Branch — else_ optional (no default, no match → branch fails)
# ---------------------------------------------------------------------------


class TestBranchNoDefaultFails:
    """When no condition matches and default is empty, branch raises → task failed."""

    def setup_method(self):
        reset_for_tests()
        _register_map_branch()
        self.store = InMemoryFlowStore()

    async def test_branch_fails_when_no_match_and_no_default(self):
        """Missing default + no match → branch task 'failed', flow 'failed'."""
        spec = {
            "version": 1,
            "name": "branch_no_default_flow",
            "params": [],
            "tasks": [
                {
                    "key": "classify",
                    "kind": "noop",
                    "needs": [],
                    "config": {},
                    "retries": 0,
                    "retry_backoff_s": 30,
                    "timeout_s": 0,
                    "cache_ttl_s": 0,
                    "ui": {"x": 0, "y": 0},
                },
                {
                    "key": "route",
                    "kind": "branch",
                    "needs": ["classify"],
                    "config": {
                        "conditions": [
                            {"when": "'{{ inputs.classify.label }}' == 'x'", "next": ["x_task"]},
                        ],
                        "default": [],
                    },
                    "retries": 0,
                    "retry_backoff_s": 30,
                    "timeout_s": 0,
                    "cache_ttl_s": 0,
                    "ui": {"x": 260, "y": 0},
                },
                {
                    "key": "x_task",
                    "kind": "noop",
                    "needs": ["route"],
                    "config": {},
                    "retries": 0,
                    "retry_backoff_s": 30,
                    "timeout_s": 0,
                    "cache_ttl_s": 0,
                    "ui": {"x": 520, "y": 0},
                },
            ],
        }
        flow = await _make_flow(self.store, spec)
        frun = await materialize_flow_run(self.store, flow, {}, "manual", NOW)

        trs = await self.store.list_task_runs(frun["id"])
        by_key = {tr["task_key"]: tr for tr in trs}
        await self.store.update_task_run(
            by_key["classify"]["id"],
            {"state": "success", "result": {"label": "unknown"}},
        )
        await advance_readiness(self.store, frun["id"], NOW)

        final = await drain_flow_run(self.store, frun["id"], NOW, CLAIMS, max_steps=20)

        all_trs = await self.store.list_task_runs(frun["id"])
        by_key = {tr["task_key"]: tr for tr in all_trs}

        assert by_key["route"]["state"] == "failed", (
            f"route state: {by_key['route']['state']}"
        )
        assert final["state"] == "failed", f"flow_run: {final['state']}"


# ---------------------------------------------------------------------------
# 6. Regression: existing linear flow still works
# ---------------------------------------------------------------------------


class TestLinearFlowRegression:
    """Ensure existing tests still pass after runtime changes."""

    def setup_method(self):
        reset_for_tests()
        _register_map_branch()
        self.store = InMemoryFlowStore()

    async def test_linear_3_task_flow_succeeds(self):
        spec = {
            "version": 1,
            "name": "linear_flow",
            "tasks": [
                {"key": "a", "kind": "noop", "needs": [], "config": {},
                 "retries": 0, "timeout_s": 0},
                {"key": "b", "kind": "noop", "needs": ["a"], "config": {},
                 "retries": 0, "timeout_s": 0},
                {"key": "c", "kind": "noop", "needs": ["b"], "config": {},
                 "retries": 0, "timeout_s": 0},
            ],
        }
        flow = await _make_flow(self.store, spec)
        frun = await materialize_flow_run(self.store, flow, {}, "manual", NOW)
        final = await drain_flow_run(self.store, frun["id"], NOW, CLAIMS)
        assert final["state"] == "success", f"flow_run: {final['state']}"

    async def test_diamond_dag_succeeds(self):
        spec = {
            "version": 1,
            "name": "diamond_flow",
            "tasks": [
                {"key": "a", "kind": "noop", "needs": [], "config": {},
                 "retries": 0, "timeout_s": 0},
                {"key": "b", "kind": "noop", "needs": ["a"], "config": {},
                 "retries": 0, "timeout_s": 0},
                {"key": "c", "kind": "noop", "needs": ["a"], "config": {},
                 "retries": 0, "timeout_s": 0},
                {"key": "d", "kind": "noop", "needs": ["b", "c"], "config": {},
                 "retries": 0, "timeout_s": 0},
            ],
        }
        flow = await _make_flow(self.store, spec)
        frun = await materialize_flow_run(self.store, flow, {}, "manual", NOW)
        final = await drain_flow_run(self.store, frun["id"], NOW, CLAIMS)
        assert final["state"] == "success", f"flow_run: {final['state']}"
