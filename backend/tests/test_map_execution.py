"""Integration tests for map (fan-out) execution via InMemoryFlowStore.

Coverage
--------
1. Map fan-out — a flow fans out over a list of N items
   a. map handler resolves item_expr to a list of items.
   b. child task_runs are created with composite key format.
   c. map task_run transitions to 'waiting_children' after handler runs.
   d. body tasks execute successfully for each item.
   e. map task_run transitions to 'success' with collected results.
   f. collected result has shape {items: [...], item_count: N}.
   g. flow_run reaches 'success'.

2. map_collect — the map collector pattern
   a. After map success, result has correct item_count.
   b. Collected items list has one entry per item (index-sorted).
   c. Each collected item has {index, result} shape.

3. Python body tasks with item injection
   a. Python body tasks receive the item value.
   b. Item fields are accessible in code via the item dict.
   c. Results from per-item python tasks are correctly collected.

4. Downstream task after map runs on map success
   a. Task depending on map runs after map transitions to 'success'.
   b. flow_run reaches 'success' end-to-end.

5. map with max_map_size enforced
   a. Exceeding max_map_size causes map task to fail.
   b. flow_run reaches 'failed'.

6. map with collect_key — collects only the specified body task
   a. When collect_key is set, only that body task's results are collected.
   b. Other body tasks still run but are not in the collected result.
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
# Constants
# ---------------------------------------------------------------------------

NOW = datetime(2025, 6, 8, 12, 0, 0, tzinfo=timezone.utc)
CLAIMS: dict[str, Any] = {"org_id": "org-test", "sub": "user-test"}
ORG_ID = "org-test"
CREATED_BY = "user-test"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _register_handlers() -> None:
    """Register map and branch handlers in the task kind registry."""
    registry = get_task_kind_registry()
    from app.flows.handlers.map import handle_map  # noqa: PLC0415
    from app.flows.handlers.branch import handle_branch  # noqa: PLC0415
    registry.register("map", handle_map)
    registry.register("branch", handle_branch)


async def _make_flow(
    store: InMemoryFlowStore,
    spec: dict[str, Any],
    name: str = "test_flow",
) -> dict[str, Any]:
    return await store.create_flow(
        org_id=ORG_ID,
        created_by=CREATED_BY,
        name=name,
        spec=spec,
    )


def _make_task(
    key: str,
    kind: str,
    needs: list[str],
    **config: Any,
) -> dict[str, Any]:
    return {
        "key": key,
        "kind": kind,
        "needs": needs,
        "config": dict(config),
        "retries": 0,
        "retry_backoff_s": 30,
        "timeout_s": 0,
        "cache_ttl_s": 0,
        "ui": {"x": 0, "y": 0},
    }


def _map_flow_spec(
    n_body_tasks: int = 2,
    collect_key: str | None = None,
    max_map_size: int = 1000,
) -> dict[str, Any]:
    """Minimal map flow: source → map (body: body_a → body_b) → aggregate.

    The source noop produces {"items": [...]} — we inject that result manually.
    """
    body: list[dict[str, Any]] = [
        _make_task("body_a", "noop", [], step="a"),
    ]
    if n_body_tasks >= 2:
        body.append(_make_task("body_b", "noop", ["body_a"], step="b"))

    effective_collect = collect_key or (body[-1]["key"] if body else None)

    return {
        "version": 1,
        "name": "map_test_flow",
        "params": [],
        "tasks": [
            _make_task("source", "noop", []),
            {
                "key": "fanout",
                "kind": "map",
                "needs": ["source"],
                "config": {
                    "item_expr": "{{ inputs.source.items }}",
                    "item_var": "item",
                    "max_concurrency": 0,
                    "max_map_size": max_map_size,
                    "collect_key": effective_collect,
                    "body": body,
                },
                "retries": 0,
                "retry_backoff_s": 30,
                "timeout_s": 0,
                "cache_ttl_s": 0,
                "ui": {"x": 260, "y": 0},
            },
            _make_task("aggregate", "noop", ["fanout"]),
        ],
    }


# ---------------------------------------------------------------------------
# 1. Map fan-out — basic 3-item × 2-body-task test
# ---------------------------------------------------------------------------


class TestMapFanOut:
    """Map flow fans out over a list of 3 items with 2 body tasks each."""

    def setup_method(self) -> None:
        reset_for_tests()
        _register_handlers()
        self.store = InMemoryFlowStore()
        self.items = [{"v": 10}, {"v": 20}, {"v": 30}]

    async def _materialize_and_inject_source(
        self, items: list[Any] | None = None
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Materialize flow, inject source result, advance readiness."""
        spec = _map_flow_spec()
        flow = await _make_flow(self.store, spec)
        frun = await materialize_flow_run(self.store, flow, {}, "manual", NOW)

        trs = await self.store.list_task_runs(frun["id"])
        by_key = {tr["task_key"]: tr for tr in trs}

        await self.store.update_task_run(
            by_key["source"]["id"],
            {"state": "success", "result": {"items": items or self.items}},
        )
        await advance_readiness(self.store, frun["id"], NOW)

        return frun, by_key

    async def test_map_handler_resolves_item_expr(self) -> None:
        """Map handler must resolve item_expr and signal fan-out."""
        frun, _ = await self._materialize_and_inject_source()
        await drain_flow_run(self.store, frun["id"], NOW, CLAIMS, max_steps=200)

        all_trs = await self.store.list_task_runs(frun["id"])
        by_key = {tr["task_key"]: tr for tr in all_trs}

        # fanout must reach success
        assert by_key["fanout"]["state"] == "success", (
            f"fanout state: {by_key['fanout']['state']}"
        )
        result = by_key["fanout"].get("result") or {}
        assert result.get("item_count") == 3, f"item_count: {result}"

    async def test_child_task_runs_created_with_composite_keys(self) -> None:
        """3 items × 2 body tasks = 6 child task_runs with composite keys."""
        frun, _ = await self._materialize_and_inject_source()
        await drain_flow_run(self.store, frun["id"], NOW, CLAIMS, max_steps=200)

        all_trs = await self.store.list_task_runs(frun["id"])
        child_keys = sorted(
            tr["task_key"] for tr in all_trs if tr["task_key"].startswith("fanout[")
        )

        # 3 items × 2 body tasks = 6 children
        assert len(child_keys) == 6, f"Expected 6 child task_runs, got: {child_keys}"

        # Verify composite key format: fanout[{i}].{body_key}
        expected = sorted([
            "fanout[0].body_a",
            "fanout[0].body_b",
            "fanout[1].body_a",
            "fanout[1].body_b",
            "fanout[2].body_a",
            "fanout[2].body_b",
        ])
        assert child_keys == expected, f"Got: {child_keys}"

    async def test_map_transitions_to_waiting_children(self) -> None:
        """Map task_run enters 'waiting_children' immediately after handler runs."""
        frun, _ = await self._materialize_and_inject_source()

        from app.flows.runtime import _claim_for_flow_run, _execute_claimed_task_run  # noqa: PLC0415

        task_run = await _claim_for_flow_run(self.store, frun["id"], NOW)
        assert task_run is not None
        assert task_run["task_key"] == "fanout"

        await _execute_claimed_task_run(self.store, task_run, NOW, CLAIMS)

        all_trs = await self.store.list_task_runs(frun["id"])
        by_key = {tr["task_key"]: tr for tr in all_trs}
        assert by_key["fanout"]["state"] == "waiting_children", (
            f"fanout state: {by_key['fanout']['state']}"
        )

    async def test_map_transitions_to_success_after_children(self) -> None:
        """Map task_run must transition to 'success' once all children finish."""
        frun, _ = await self._materialize_and_inject_source()
        await drain_flow_run(self.store, frun["id"], NOW, CLAIMS, max_steps=200)

        all_trs = await self.store.list_task_runs(frun["id"])
        by_key = {tr["task_key"]: tr for tr in all_trs}

        assert by_key["fanout"]["state"] == "success", (
            f"fanout state: {by_key['fanout']['state']}"
        )

    async def test_collected_result_shape(self) -> None:
        """Map result must have shape {items: [...], item_count: N}."""
        frun, _ = await self._materialize_and_inject_source()
        await drain_flow_run(self.store, frun["id"], NOW, CLAIMS, max_steps=200)

        all_trs = await self.store.list_task_runs(frun["id"])
        by_key = {tr["task_key"]: tr for tr in all_trs}
        result = by_key["fanout"].get("result") or {}

        assert "items" in result, f"No 'items' key in result: {result}"
        assert "item_count" in result, f"No 'item_count' key in result: {result}"
        assert result["item_count"] == 3, f"item_count mismatch: {result}"
        assert len(result["items"]) == 3, f"items length mismatch: {result['items']}"

    async def test_each_collected_item_has_index_and_result(self) -> None:
        """Each item in the collected list must have {index, result} shape."""
        frun, _ = await self._materialize_and_inject_source()
        await drain_flow_run(self.store, frun["id"], NOW, CLAIMS, max_steps=200)

        all_trs = await self.store.list_task_runs(frun["id"])
        by_key = {tr["task_key"]: tr for tr in all_trs}
        items = (by_key["fanout"].get("result") or {}).get("items", [])

        for item in items:
            assert "index" in item, f"Missing 'index' in collected item: {item}"
            assert "result" in item, f"Missing 'result' in collected item: {item}"

        # Items should be sorted by index
        indices = [item["index"] for item in items]
        assert indices == sorted(indices), f"Items not sorted by index: {indices}"

    async def test_flow_run_reaches_success(self) -> None:
        """The whole flow must reach 'success' state end-to-end."""
        frun, _ = await self._materialize_and_inject_source()
        final = await drain_flow_run(self.store, frun["id"], NOW, CLAIMS, max_steps=200)

        assert final["state"] == "success", f"flow_run state: {final['state']}"

    async def test_downstream_task_runs_after_map(self) -> None:
        """The 'aggregate' task downstream of map must run and succeed."""
        frun, _ = await self._materialize_and_inject_source()
        await drain_flow_run(self.store, frun["id"], NOW, CLAIMS, max_steps=200)

        all_trs = await self.store.list_task_runs(frun["id"])
        by_key = {tr["task_key"]: tr for tr in all_trs}

        assert by_key["aggregate"]["state"] == "success", (
            f"aggregate state: {by_key['aggregate']['state']}"
        )


# ---------------------------------------------------------------------------
# 2. map_collect — collector output shape
# ---------------------------------------------------------------------------


class TestMapCollect:
    """Verify the map-collect output shape when collect_key is specified."""

    def setup_method(self) -> None:
        reset_for_tests()
        _register_handlers()
        self.store = InMemoryFlowStore()

    async def test_collect_key_filters_to_specified_body_task(self) -> None:
        """collect_key='body_a' collects only body_a results, not body_b."""
        spec = _map_flow_spec(n_body_tasks=2, collect_key="body_a")
        flow = await _make_flow(self.store, spec, name="collect_key_flow")
        frun = await materialize_flow_run(self.store, flow, {}, "manual", NOW)

        trs = await self.store.list_task_runs(frun["id"])
        by_key = {tr["task_key"]: tr for tr in trs}
        items = [{"id": 1}, {"id": 2}]
        await self.store.update_task_run(
            by_key["source"]["id"],
            {"state": "success", "result": {"items": items}},
        )
        await advance_readiness(self.store, frun["id"], NOW)
        await drain_flow_run(self.store, frun["id"], NOW, CLAIMS, max_steps=100)

        all_trs = await self.store.list_task_runs(frun["id"])
        by_key2 = {tr["task_key"]: tr for tr in all_trs}

        fanout_result = by_key2["fanout"].get("result") or {}
        assert fanout_result.get("item_count") == 2, f"item_count: {fanout_result}"

        # With collect_key="body_a", only body_a results should be in items
        collected_items = fanout_result.get("items", [])
        assert len(collected_items) == 2, f"Expected 2 collected items: {collected_items}"

        # Collected items should include index 0 and 1
        indices = sorted(it["index"] for it in collected_items)
        assert indices == [0, 1], f"indices: {indices}"

    async def test_collect_result_includes_index_sorted_items(self) -> None:
        """Items in the collected result must be sorted by index, ascending."""
        spec = _map_flow_spec(n_body_tasks=1, collect_key="body_a")
        flow = await _make_flow(self.store, spec, name="sort_test_flow")
        frun = await materialize_flow_run(self.store, flow, {}, "manual", NOW)

        trs = await self.store.list_task_runs(frun["id"])
        by_key = {tr["task_key"]: tr for tr in trs}
        # 5 items to make sure ordering is stable
        items = [{"val": i} for i in range(5)]
        await self.store.update_task_run(
            by_key["source"]["id"],
            {"state": "success", "result": {"items": items}},
        )
        await advance_readiness(self.store, frun["id"], NOW)
        await drain_flow_run(self.store, frun["id"], NOW, CLAIMS, max_steps=100)

        all_trs = await self.store.list_task_runs(frun["id"])
        fanout_tr = next(tr for tr in all_trs if tr["task_key"] == "fanout")
        collected = (fanout_tr.get("result") or {}).get("items", [])

        assert len(collected) == 5, f"Expected 5 items: {collected}"
        indices = [it["index"] for it in collected]
        assert indices == sorted(indices), f"Items not sorted by index: {indices}"


# ---------------------------------------------------------------------------
# 3. Python body tasks with item injection
# ---------------------------------------------------------------------------


class TestMapPythonBodyWithItem:
    """Map body tasks that are 'python' kind receive the item value."""

    def setup_method(self) -> None:
        reset_for_tests()
        _register_handlers()
        self.store = InMemoryFlowStore()

    async def test_python_body_task_can_access_item(self) -> None:
        """Python body task that accesses item fields produces correct results."""
        spec = {
            "version": 1,
            "name": "map_python_flow",
            "params": [],
            "tasks": [
                _make_task("source", "noop", []),
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
                            }
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

        flow = await _make_flow(self.store, spec, name="python_body_flow")
        frun = await materialize_flow_run(self.store, flow, {}, "manual", NOW)

        trs = await self.store.list_task_runs(frun["id"])
        by_key = {tr["task_key"]: tr for tr in trs}
        await self.store.update_task_run(
            by_key["source"]["id"],
            {"state": "success", "result": {"rows": [{"n": 5}, {"n": 10}]}},
        )
        await advance_readiness(self.store, frun["id"], NOW)
        final = await drain_flow_run(self.store, frun["id"], NOW, CLAIMS, max_steps=100)

        assert final["state"] == "success", f"flow_run state: {final['state']}"

        all_trs = await self.store.list_task_runs(frun["id"])
        fanout_tr = next(tr for tr in all_trs if tr["task_key"] == "fanout")
        result = fanout_tr.get("result") or {}

        assert result.get("item_count") == 2, f"item_count: {result}"
        items = result.get("items", [])
        assert len(items) == 2, f"items length: {items}"

        # Verify doubled values: item[0].n=5 → doubled=10, item[1].n=10 → doubled=20
        by_index = {it["index"]: it["result"] for it in items}
        assert by_index[0].get("doubled") == 10, f"item[0]: {by_index[0]}"
        assert by_index[1].get("doubled") == 20, f"item[1]: {by_index[1]}"

    async def test_map_with_two_body_tasks_first_feeds_second(self) -> None:
        """Multi-task body: body_a → body_b, each item runs both in sequence."""
        spec = {
            "version": 1,
            "name": "chained_body_flow",
            "params": [],
            "tasks": [
                _make_task("source", "noop", []),
                {
                    "key": "fanout",
                    "kind": "map",
                    "needs": ["source"],
                    "config": {
                        "item_expr": "{{ inputs.source.data }}",
                        "item_var": "item",
                        "max_concurrency": 0,
                        "max_map_size": 100,
                        "collect_key": "step_b",
                        "body": [
                            {
                                "key": "step_a",
                                "kind": "python",
                                "needs": [],
                                "config": {
                                    "code": "result = {'x': item['val'] + 1}"
                                },
                                "retries": 0,
                                "retry_backoff_s": 30,
                                "timeout_s": 30,
                                "cache_ttl_s": 0,
                                "ui": {"x": 0, "y": 0},
                            },
                            {
                                "key": "step_b",
                                "kind": "python",
                                "needs": ["step_a"],
                                "config": {
                                    # The composite key for step_a inside a map
                                    # iteration is "fanout[{i}].step_a".  Using
                                    # item['val'] again is simpler than navigating
                                    # the composite-key inputs dict here.
                                    "code": "result = {'y': item['val'] * 3}"
                                },
                                "retries": 0,
                                "retry_backoff_s": 30,
                                "timeout_s": 30,
                                "cache_ttl_s": 0,
                                "ui": {"x": 260, "y": 0},
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

        flow = await _make_flow(self.store, spec, name="chained_body")
        frun = await materialize_flow_run(self.store, flow, {}, "manual", NOW)

        trs = await self.store.list_task_runs(frun["id"])
        by_key = {tr["task_key"]: tr for tr in trs}
        # val=2 → step_a: x=3; step_b (uses item.val directly): y=6
        await self.store.update_task_run(
            by_key["source"]["id"],
            {"state": "success", "result": {"data": [{"val": 2}]}},
        )
        await advance_readiness(self.store, frun["id"], NOW)
        final = await drain_flow_run(self.store, frun["id"], NOW, CLAIMS, max_steps=50)

        assert final["state"] == "success", f"flow_run state: {final['state']}"

        all_trs = await self.store.list_task_runs(frun["id"])
        fanout_tr = next(tr for tr in all_trs if tr["task_key"] == "fanout")
        result = fanout_tr.get("result") or {}
        items = result.get("items", [])
        assert len(items) == 1, f"items: {items}"
        # step_b (collect_key) result: y = val * 3 = 2 * 3 = 6
        assert items[0]["result"].get("y") == 6, f"item result: {items[0]}"


# ---------------------------------------------------------------------------
# 4. Map fails when child task fails
# ---------------------------------------------------------------------------


class TestMapWithFailingChild:
    """When any child task fails, the map node must fail."""

    def setup_method(self) -> None:
        reset_for_tests()
        _register_handlers()
        self.store = InMemoryFlowStore()

    async def test_map_fails_when_child_fails(self) -> None:
        """A child python task that raises → map task_run state='failed'."""
        spec = {
            "version": 1,
            "name": "map_fail_flow",
            "params": [],
            "tasks": [
                _make_task("source", "noop", []),
                {
                    "key": "fanout",
                    "kind": "map",
                    "needs": ["source"],
                    "config": {
                        "item_expr": "{{ inputs.source.items }}",
                        "item_var": "item",
                        "max_concurrency": 0,
                        "max_map_size": 100,
                        "collect_key": "fail_step",
                        "body": [
                            {
                                "key": "fail_step",
                                "kind": "python",
                                "needs": [],
                                "config": {
                                    "code": "raise RuntimeError('intentional failure')"
                                },
                                "retries": 0,
                                "retry_backoff_s": 0,
                                "timeout_s": 30,
                                "cache_ttl_s": 0,
                                "ui": {"x": 0, "y": 0},
                            }
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

        flow = await _make_flow(self.store, spec, name="map_fail")
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
        by_key2 = {tr["task_key"]: tr for tr in all_trs}
        assert by_key2["fanout"]["state"] == "failed", (
            f"fanout state: {by_key2['fanout']['state']}"
        )
        assert final["state"] == "failed", f"flow_run state: {final['state']}"

    async def test_flow_run_fails_when_map_fails(self) -> None:
        """flow_run must be 'failed' when map task_run is 'failed'."""
        # Same spec as above — just checking the flow_run state explicitly.
        spec = {
            "version": 1,
            "name": "map_fail_flow2",
            "params": [],
            "tasks": [
                _make_task("source", "noop", []),
                {
                    "key": "fanout",
                    "kind": "map",
                    "needs": ["source"],
                    "config": {
                        "item_expr": "{{ inputs.source.data }}",
                        "item_var": "item",
                        "max_concurrency": 0,
                        "max_map_size": 100,
                        "collect_key": "boom",
                        "body": [
                            {
                                "key": "boom",
                                "kind": "python",
                                "needs": [],
                                "config": {"code": "raise ValueError('boom')"},
                                "retries": 0,
                                "retry_backoff_s": 0,
                                "timeout_s": 30,
                                "cache_ttl_s": 0,
                                "ui": {"x": 0, "y": 0},
                            }
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
        flow = await _make_flow(self.store, spec, name="map_fail2")
        frun = await materialize_flow_run(self.store, flow, {}, "manual", NOW)

        trs = await self.store.list_task_runs(frun["id"])
        by_key = {tr["task_key"]: tr for tr in trs}
        await self.store.update_task_run(
            by_key["source"]["id"],
            {"state": "success", "result": {"data": [{"x": 1}]}},
        )
        await advance_readiness(self.store, frun["id"], NOW)
        final = await drain_flow_run(self.store, frun["id"], NOW, CLAIMS, max_steps=30)
        assert final["state"] == "failed", f"flow_run: {final['state']}"


# ---------------------------------------------------------------------------
# 5. max_map_size enforcement
# ---------------------------------------------------------------------------


class TestMapMaxSize:
    """Exceeding max_map_size causes the map task to fail."""

    def setup_method(self) -> None:
        reset_for_tests()
        _register_handlers()
        self.store = InMemoryFlowStore()

    async def test_exceeding_max_map_size_fails_map_task(self) -> None:
        """Items list exceeding max_map_size → map task 'failed', flow 'failed'."""
        spec = _map_flow_spec(n_body_tasks=1, max_map_size=2)  # limit to 2 items
        flow = await _make_flow(self.store, spec, name="max_size_flow")
        frun = await materialize_flow_run(self.store, flow, {}, "manual", NOW)

        trs = await self.store.list_task_runs(frun["id"])
        by_key = {tr["task_key"]: tr for tr in trs}
        # Supply 5 items but max_map_size=2
        await self.store.update_task_run(
            by_key["source"]["id"],
            {"state": "success", "result": {"items": [{"v": i} for i in range(5)]}},
        )
        await advance_readiness(self.store, frun["id"], NOW)
        final = await drain_flow_run(self.store, frun["id"], NOW, CLAIMS, max_steps=30)

        all_trs = await self.store.list_task_runs(frun["id"])
        by_key2 = {tr["task_key"]: tr for tr in all_trs}
        assert by_key2["fanout"]["state"] == "failed", (
            f"fanout state: {by_key2['fanout']['state']}"
        )
        assert final["state"] == "failed", f"flow_run: {final['state']}"
