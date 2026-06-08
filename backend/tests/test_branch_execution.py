"""Integration tests for branch (conditional routing) execution via InMemoryFlowStore.

Coverage
--------
1. Branch — true arm taken
   a. Branch evaluates the first matching condition.
   b. Matching downstream task becomes 'ready' → 'success'.
   c. Non-matching downstream tasks become 'upstream_failed'.
   d. flow_run reaches 'success'.
   e. Branch result contains 'branch_taken' label.

2. Branch — else_ is optional (Q1)
   a. When no condition matches and default=[] (empty), branch task fails.
   b. Downstream task that depends on the branch becomes 'upstream_failed'.
   c. flow_run reaches 'failed'.

3. Branch — default path when no condition matches
   a. Unrecognised value → default tasks activated.
   b. All non-default downstream tasks become 'upstream_failed'.
   c. flow_run reaches 'success'.

4. Branch rejoin (diamond merge)
   a. Both arms list the same downstream task key.
   b. That task runs regardless of which arm is taken.

5. Branch — second condition matches (elif semantics)
   a. First condition false, second condition true → second arm taken.
   b. Tasks in the taken arm succeed; tasks in other arms are upstream_failed.

6. Regression — linear flow still works after branch runtime changes.
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


def _branch_flow_spec(
    conditions: list[dict[str, Any]],
    default: list[str] | None = None,
    downstream_keys: list[str] | None = None,
) -> dict[str, Any]:
    """Build a branch flow spec.

    Flow shape: classify (noop) → route (branch) → {downstream tasks}.
    Each downstream task listed in downstream_keys needs 'route'.
    """
    if downstream_keys is None:
        downstream_keys = ["enrich", "archive"]

    tasks: list[dict[str, Any]] = [
        _make_task("classify", "noop", []),
        {
            "key": "route",
            "kind": "branch",
            "needs": ["classify"],
            "config": {
                "conditions": conditions,
                "default": default or [],
            },
            "retries": 0,
            "retry_backoff_s": 30,
            "timeout_s": 0,
            "cache_ttl_s": 0,
            "ui": {"x": 260, "y": 0},
        },
    ]
    for dk in downstream_keys:
        tasks.append(_make_task(dk, "noop", ["route"]))

    return {
        "version": 1,
        "name": "branch_test_flow",
        "params": [],
        "tasks": tasks,
    }


async def _run_branch_flow(
    store: InMemoryFlowStore,
    spec: dict[str, Any],
    classify_result: dict[str, Any],
    name: str = "branch_run",
    source_task_key: str = "classify",
) -> dict[str, dict[str, Any]]:
    """Materialize, inject classify result, drain, return task_runs by key.

    Parameters
    ----------
    source_task_key:
        The task_key of the upstream source task whose result is injected.
        Defaults to ``"classify"`` for backwards compatibility.
    """
    flow = await _make_flow(store, spec, name)
    frun = await materialize_flow_run(store, flow, {}, "manual", NOW)

    trs = await store.list_task_runs(frun["id"])
    by_key = {tr["task_key"]: tr for tr in trs}

    await store.update_task_run(
        by_key[source_task_key]["id"],
        {"state": "success", "result": classify_result},
    )
    await advance_readiness(store, frun["id"], NOW)
    await drain_flow_run(store, frun["id"], NOW, CLAIMS, max_steps=50)

    all_trs = await store.list_task_runs(frun["id"])
    return {tr["task_key"]: tr for tr in all_trs}


# ---------------------------------------------------------------------------
# 1. Branch — true arm taken
# ---------------------------------------------------------------------------


class TestBranchTrueArm:
    """First matching condition activates the correct tasks."""

    def setup_method(self) -> None:
        reset_for_tests()
        _register_handlers()
        self.store = InMemoryFlowStore()

    async def test_matching_condition_activates_enrich(self) -> None:
        """When label='high', enrich should succeed."""
        spec = _branch_flow_spec(
            conditions=[
                {"when": "'{{ inputs.classify.label }}' == 'high'", "next": ["enrich"]},
                {"when": "'{{ inputs.classify.label }}' == 'low'", "next": ["archive"]},
            ],
        )
        by_key = await _run_branch_flow(
            self.store, spec, {"label": "high"}, name="branch_high"
        )
        assert by_key["enrich"]["state"] == "success", (
            f"enrich: {by_key['enrich']['state']}"
        )

    async def test_matching_condition_deactivates_archive(self) -> None:
        """When label='high', archive (not taken) should be upstream_failed."""
        spec = _branch_flow_spec(
            conditions=[
                {"when": "'{{ inputs.classify.label }}' == 'high'", "next": ["enrich"]},
                {"when": "'{{ inputs.classify.label }}' == 'low'", "next": ["archive"]},
            ],
        )
        by_key = await _run_branch_flow(
            self.store, spec, {"label": "high"}, name="branch_high_2"
        )
        assert by_key["archive"]["state"] == "upstream_failed", (
            f"archive: {by_key['archive']['state']}"
        )

    async def test_low_label_activates_archive(self) -> None:
        """When label='low', archive should succeed."""
        spec = _branch_flow_spec(
            conditions=[
                {"when": "'{{ inputs.classify.label }}' == 'high'", "next": ["enrich"]},
                {"when": "'{{ inputs.classify.label }}' == 'low'", "next": ["archive"]},
            ],
        )
        by_key = await _run_branch_flow(
            self.store, spec, {"label": "low"}, name="branch_low"
        )
        assert by_key["archive"]["state"] == "success", (
            f"archive: {by_key['archive']['state']}"
        )

    async def test_low_label_deactivates_enrich(self) -> None:
        """When label='low', enrich (not taken) should be upstream_failed."""
        spec = _branch_flow_spec(
            conditions=[
                {"when": "'{{ inputs.classify.label }}' == 'high'", "next": ["enrich"]},
                {"when": "'{{ inputs.classify.label }}' == 'low'", "next": ["archive"]},
            ],
        )
        by_key = await _run_branch_flow(
            self.store, spec, {"label": "low"}, name="branch_low_2"
        )
        assert by_key["enrich"]["state"] == "upstream_failed", (
            f"enrich: {by_key['enrich']['state']}"
        )

    async def test_flow_run_reaches_success(self) -> None:
        """Flow must reach 'success' when a branch condition matches."""
        spec = _branch_flow_spec(
            conditions=[
                {"when": "'{{ inputs.classify.label }}' == 'high'", "next": ["enrich"]},
                {"when": "'{{ inputs.classify.label }}' == 'low'", "next": ["archive"]},
            ],
        )
        flow = await _make_flow(self.store, spec, name="branch_success")
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

    async def test_branch_result_has_branch_taken(self) -> None:
        """Branch task_run result must include 'branch_taken' label."""
        spec = _branch_flow_spec(
            conditions=[
                {"when": "'{{ inputs.classify.label }}' == 'high'", "next": ["enrich"]},
                {"when": "'{{ inputs.classify.label }}' == 'low'", "next": ["archive"]},
            ],
        )
        by_key = await _run_branch_flow(
            self.store, spec, {"label": "high"}, name="branch_label"
        )
        route_result = by_key["route"].get("result") or {}
        assert "branch_taken" in route_result, f"route result: {route_result}"
        assert route_result["branch_taken"] == "condition_0", (
            f"branch_taken: {route_result['branch_taken']}"
        )

    async def test_branch_taken_second_condition(self) -> None:
        """When first condition is false and second is true, second arm is taken."""
        spec = _branch_flow_spec(
            conditions=[
                {"when": "'{{ inputs.classify.label }}' == 'high'", "next": ["enrich"]},
                {"when": "'{{ inputs.classify.label }}' == 'low'", "next": ["archive"]},
            ],
        )
        by_key = await _run_branch_flow(
            self.store, spec, {"label": "low"}, name="branch_second_cond"
        )
        route_result = by_key["route"].get("result") or {}
        assert route_result.get("branch_taken") == "condition_1", (
            f"branch_taken: {route_result.get('branch_taken')}"
        )


# ---------------------------------------------------------------------------
# 2. Branch — else_ is optional (Q1): no default + no match → branch fails
# ---------------------------------------------------------------------------


class TestBranchNoDefault:
    """When no condition matches and default=[], branch task fails (Q1)."""

    def setup_method(self) -> None:
        reset_for_tests()
        _register_handlers()
        self.store = InMemoryFlowStore()

    async def test_branch_fails_when_no_match_no_default(self) -> None:
        """No match + empty default → branch task state='failed'."""
        spec = {
            "version": 1,
            "name": "no_default_flow",
            "params": [],
            "tasks": [
                _make_task("classify", "noop", []),
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
                _make_task("x_task", "noop", ["route"]),
            ],
        }
        by_key = await _run_branch_flow(
            self.store, spec, {"label": "unknown"}, name="no_default"
        )
        assert by_key["route"]["state"] == "failed", (
            f"route state: {by_key['route']['state']}"
        )

    async def test_flow_fails_when_branch_has_no_match_no_default(self) -> None:
        """flow_run must be 'failed' when branch fails due to no match."""
        spec = {
            "version": 1,
            "name": "no_default_flow2",
            "params": [],
            "tasks": [
                _make_task("classify", "noop", []),
                {
                    "key": "route",
                    "kind": "branch",
                    "needs": ["classify"],
                    "config": {
                        "conditions": [
                            {"when": "'{{ inputs.classify.label }}' == 'y'", "next": ["y_task"]},
                        ],
                        "default": [],
                    },
                    "retries": 0,
                    "retry_backoff_s": 30,
                    "timeout_s": 0,
                    "cache_ttl_s": 0,
                    "ui": {"x": 260, "y": 0},
                },
                _make_task("y_task", "noop", ["route"]),
            ],
        }
        flow = await _make_flow(self.store, spec, name="no_default2")
        frun = await materialize_flow_run(self.store, flow, {}, "manual", NOW)

        trs = await self.store.list_task_runs(frun["id"])
        by_key = {tr["task_key"]: tr for tr in trs}
        await self.store.update_task_run(
            by_key["classify"]["id"],
            {"state": "success", "result": {"label": "unknown"}},
        )
        await advance_readiness(self.store, frun["id"], NOW)
        final = await drain_flow_run(self.store, frun["id"], NOW, CLAIMS, max_steps=20)
        assert final["state"] == "failed", f"flow_run: {final['state']}"

    async def test_downstream_upstream_failed_when_branch_fails(self) -> None:
        """Downstream tasks are upstream_failed when branch task itself fails."""
        spec = {
            "version": 1,
            "name": "no_default_flow3",
            "params": [],
            "tasks": [
                _make_task("classify", "noop", []),
                {
                    "key": "route",
                    "kind": "branch",
                    "needs": ["classify"],
                    "config": {
                        "conditions": [
                            {"when": "'{{ inputs.classify.label }}' == 'z'", "next": ["z_task"]},
                        ],
                        "default": [],
                    },
                    "retries": 0,
                    "retry_backoff_s": 30,
                    "timeout_s": 0,
                    "cache_ttl_s": 0,
                    "ui": {"x": 260, "y": 0},
                },
                _make_task("z_task", "noop", ["route"]),
            ],
        }
        by_key = await _run_branch_flow(
            self.store, spec, {"label": "unknown"}, name="no_default3"
        )
        # z_task depends on route; since route failed, z_task should be upstream_failed
        assert by_key["z_task"]["state"] == "upstream_failed", (
            f"z_task state: {by_key['z_task']['state']}"
        )


# ---------------------------------------------------------------------------
# 3. Branch — default path when no condition matches
# ---------------------------------------------------------------------------


class TestBranchDefaultPath:
    """Default tasks are activated when no condition matches."""

    def setup_method(self) -> None:
        reset_for_tests()
        _register_handlers()
        self.store = InMemoryFlowStore()

    async def test_default_path_activated_when_no_condition_matches(self) -> None:
        """Unrecognised label → default='fallback' tasks activated."""
        spec = {
            "version": 1,
            "name": "default_path_flow",
            "params": [],
            "tasks": [
                _make_task("classify", "noop", []),
                {
                    "key": "route",
                    "kind": "branch",
                    "needs": ["classify"],
                    "config": {
                        "conditions": [
                            {"when": "'{{ inputs.classify.label }}' == 'premium'", "next": ["premium"]},
                        ],
                        "default": ["fallback"],
                    },
                    "retries": 0,
                    "retry_backoff_s": 30,
                    "timeout_s": 0,
                    "cache_ttl_s": 0,
                    "ui": {"x": 260, "y": 0},
                },
                _make_task("premium", "noop", ["route"]),
                _make_task("fallback", "noop", ["route"]),
            ],
        }
        by_key = await _run_branch_flow(
            self.store, spec, {"label": "standard"}, name="default_path"
        )
        assert by_key["fallback"]["state"] == "success", (
            f"fallback: {by_key['fallback']['state']}"
        )
        assert by_key["premium"]["state"] == "upstream_failed", (
            f"premium: {by_key['premium']['state']}"
        )

    async def test_flow_succeeds_when_default_path_taken(self) -> None:
        """flow_run succeeds when the default path is taken."""
        spec = {
            "version": 1,
            "name": "default_success_flow",
            "params": [],
            "tasks": [
                _make_task("classify", "noop", []),
                {
                    "key": "route",
                    "kind": "branch",
                    "needs": ["classify"],
                    "config": {
                        "conditions": [
                            {"when": "'{{ inputs.classify.label }}' == 'yes'", "next": ["yes_task"]},
                        ],
                        "default": ["no_task"],
                    },
                    "retries": 0,
                    "retry_backoff_s": 30,
                    "timeout_s": 0,
                    "cache_ttl_s": 0,
                    "ui": {"x": 260, "y": 0},
                },
                _make_task("yes_task", "noop", ["route"]),
                _make_task("no_task", "noop", ["route"]),
            ],
        }
        by_key = await _run_branch_flow(
            self.store, spec, {"label": "no"}, name="default_success"
        )
        flow_runs = await self.store.list_flow_runs(
            # find the flow_run for this test by finding the run just created
            # We just check by_key directly since we need the flow_run state
            # Let's re-run and check by querying the store
            list(self.store._flow_run_index.keys())[-1]
        )
        # Check final state from the most recent flow_run
        last_run = flow_runs[0]  # newest first
        assert last_run["state"] == "success", f"flow_run: {last_run['state']}"

    async def test_branch_result_has_default_label(self) -> None:
        """Branch result must say 'branch_taken': 'default' when default is used."""
        spec = {
            "version": 1,
            "name": "default_label_flow",
            "params": [],
            "tasks": [
                _make_task("classify", "noop", []),
                {
                    "key": "route",
                    "kind": "branch",
                    "needs": ["classify"],
                    "config": {
                        "conditions": [
                            {"when": "'{{ inputs.classify.label }}' == 'a'", "next": ["a_task"]},
                        ],
                        "default": ["fallback"],
                    },
                    "retries": 0,
                    "retry_backoff_s": 30,
                    "timeout_s": 0,
                    "cache_ttl_s": 0,
                    "ui": {"x": 260, "y": 0},
                },
                _make_task("a_task", "noop", ["route"]),
                _make_task("fallback", "noop", ["route"]),
            ],
        }
        by_key = await _run_branch_flow(
            self.store, spec, {"label": "other"}, name="default_label"
        )
        route_result = by_key["route"].get("result") or {}
        assert route_result.get("branch_taken") == "default", (
            f"branch_taken: {route_result.get('branch_taken')}"
        )


# ---------------------------------------------------------------------------
# 4. Branch rejoin (diamond merge) — same downstream task in multiple arms
# ---------------------------------------------------------------------------


class TestBranchRejoin:
    """Both branch arms list the same downstream task (rejoin/diamond merge)."""

    def setup_method(self) -> None:
        reset_for_tests()
        _register_handlers()
        self.store = InMemoryFlowStore()

    async def test_rejoin_task_runs_on_first_arm(self) -> None:
        """A downstream task listed in both arms runs when the first arm is taken."""
        spec = {
            "version": 1,
            "name": "rejoin_flow",
            "params": [],
            "tasks": [
                _make_task("classify", "noop", []),
                {
                    "key": "route",
                    "kind": "branch",
                    "needs": ["classify"],
                    "config": {
                        "conditions": [
                            {"when": "'{{ inputs.classify.label }}' == 'a'",
                             "next": ["arm_a", "shared"]},
                            {"when": "'{{ inputs.classify.label }}' == 'b'",
                             "next": ["arm_b", "shared"]},
                        ],
                        "default": ["shared"],
                    },
                    "retries": 0,
                    "retry_backoff_s": 30,
                    "timeout_s": 0,
                    "cache_ttl_s": 0,
                    "ui": {"x": 260, "y": 0},
                },
                _make_task("arm_a", "noop", ["route"]),
                _make_task("arm_b", "noop", ["route"]),
                _make_task("shared", "noop", ["route"]),
            ],
        }
        by_key = await _run_branch_flow(
            self.store, spec, {"label": "a"}, name="rejoin_a"
        )
        # shared is in arm_a's next list, so it should run
        assert by_key["shared"]["state"] == "success", (
            f"shared: {by_key['shared']['state']}"
        )
        # arm_b is not in arm_a's next list
        assert by_key["arm_b"]["state"] == "upstream_failed", (
            f"arm_b: {by_key['arm_b']['state']}"
        )

    async def test_rejoin_task_runs_on_second_arm(self) -> None:
        """A downstream task listed in both arms runs when the second arm is taken."""
        spec = {
            "version": 1,
            "name": "rejoin_flow_b",
            "params": [],
            "tasks": [
                _make_task("classify", "noop", []),
                {
                    "key": "route",
                    "kind": "branch",
                    "needs": ["classify"],
                    "config": {
                        "conditions": [
                            {"when": "'{{ inputs.classify.label }}' == 'a'",
                             "next": ["arm_a", "shared"]},
                            {"when": "'{{ inputs.classify.label }}' == 'b'",
                             "next": ["arm_b", "shared"]},
                        ],
                        "default": ["shared"],
                    },
                    "retries": 0,
                    "retry_backoff_s": 30,
                    "timeout_s": 0,
                    "cache_ttl_s": 0,
                    "ui": {"x": 260, "y": 0},
                },
                _make_task("arm_a", "noop", ["route"]),
                _make_task("arm_b", "noop", ["route"]),
                _make_task("shared", "noop", ["route"]),
            ],
        }
        by_key = await _run_branch_flow(
            self.store, spec, {"label": "b"}, name="rejoin_b"
        )
        assert by_key["shared"]["state"] == "success", (
            f"shared: {by_key['shared']['state']}"
        )
        assert by_key["arm_a"]["state"] == "upstream_failed", (
            f"arm_a: {by_key['arm_a']['state']}"
        )


# ---------------------------------------------------------------------------
# 5. Branch condition evaluation — second condition matches
# ---------------------------------------------------------------------------


class TestBranchSecondCondition:
    """First condition is false; second condition is true."""

    def setup_method(self) -> None:
        reset_for_tests()
        _register_handlers()
        self.store = InMemoryFlowStore()

    async def test_second_condition_taken_when_first_false(self) -> None:
        """When first condition is false, second (true) condition is taken."""
        spec = {
            "version": 1,
            "name": "second_cond_flow",
            "params": [],
            "tasks": [
                _make_task("score", "noop", []),
                {
                    "key": "route",
                    "kind": "branch",
                    "needs": ["score"],
                    "config": {
                        "conditions": [
                            {"when": "'{{ inputs.score.tier }}' == 'platinum'", "next": ["platinum"]},
                            {"when": "'{{ inputs.score.tier }}' == 'gold'", "next": ["gold"]},
                            {"when": "'{{ inputs.score.tier }}' == 'silver'", "next": ["silver"]},
                        ],
                        "default": ["other"],
                    },
                    "retries": 0,
                    "retry_backoff_s": 30,
                    "timeout_s": 0,
                    "cache_ttl_s": 0,
                    "ui": {"x": 260, "y": 0},
                },
                _make_task("platinum", "noop", ["route"]),
                _make_task("gold", "noop", ["route"]),
                _make_task("silver", "noop", ["route"]),
                _make_task("other", "noop", ["route"]),
            ],
        }
        by_key = await _run_branch_flow(
            self.store, spec, {"tier": "gold"}, name="second_cond",
            source_task_key="score",
        )
        assert by_key["gold"]["state"] == "success", (
            f"gold: {by_key['gold']['state']}"
        )
        assert by_key["platinum"]["state"] == "upstream_failed"
        assert by_key["silver"]["state"] == "upstream_failed"
        assert by_key["other"]["state"] == "upstream_failed"

        route_result = by_key["route"].get("result") or {}
        assert route_result.get("branch_taken") == "condition_1", (
            f"branch_taken: {route_result.get('branch_taken')}"
        )


# ---------------------------------------------------------------------------
# 6. Regression — linear flow still works
# ---------------------------------------------------------------------------


class TestLinearFlowRegression:
    """Ensure existing linear flows work correctly after branch runtime changes."""

    def setup_method(self) -> None:
        reset_for_tests()
        _register_handlers()
        self.store = InMemoryFlowStore()

    async def test_linear_3_task_flow_succeeds(self) -> None:
        """Simple linear noop → noop → noop still reaches 'success'."""
        spec = {
            "version": 1,
            "name": "linear_regression",
            "tasks": [
                _make_task("a", "noop", []),
                _make_task("b", "noop", ["a"]),
                _make_task("c", "noop", ["b"]),
            ],
        }
        flow = await _make_flow(self.store, spec)
        frun = await materialize_flow_run(self.store, flow, {}, "manual", NOW)
        final = await drain_flow_run(self.store, frun["id"], NOW, CLAIMS)
        assert final["state"] == "success", f"flow_run: {final['state']}"

    async def test_branch_downstream_task_using_python_result(self) -> None:
        """Branch evaluates conditions based on a python task result."""
        spec = {
            "version": 1,
            "name": "python_branch_flow",
            "params": [],
            "tasks": [
                {
                    "key": "compute",
                    "kind": "python",
                    "needs": [],
                    "config": {"code": "result = {'score': 0.8, 'label': 'high'}"},
                    "retries": 0,
                    "retry_backoff_s": 30,
                    "timeout_s": 30,
                    "cache_ttl_s": 0,
                    "ui": {"x": 0, "y": 0},
                },
                {
                    "key": "route",
                    "kind": "branch",
                    "needs": ["compute"],
                    "config": {
                        "conditions": [
                            {
                                "when": "'{{ inputs.compute.label }}' == 'high'",
                                "next": ["enrich"],
                            },
                            {
                                "when": "'{{ inputs.compute.label }}' == 'low'",
                                "next": ["archive"],
                            },
                        ],
                        "default": ["other"],
                    },
                    "retries": 0,
                    "retry_backoff_s": 30,
                    "timeout_s": 0,
                    "cache_ttl_s": 0,
                    "ui": {"x": 260, "y": 0},
                },
                _make_task("enrich", "noop", ["route"]),
                _make_task("archive", "noop", ["route"]),
                _make_task("other", "noop", ["route"]),
            ],
        }
        flow = await _make_flow(self.store, spec, name="python_branch")
        frun = await materialize_flow_run(self.store, flow, {}, "manual", NOW)
        final = await drain_flow_run(self.store, frun["id"], NOW, CLAIMS, max_steps=50)

        assert final["state"] == "success", f"flow_run: {final['state']}"

        all_trs = await self.store.list_task_runs(frun["id"])
        by_key = {tr["task_key"]: tr for tr in all_trs}
        assert by_key["enrich"]["state"] == "success"
        assert by_key["archive"]["state"] == "upstream_failed"
        assert by_key["other"]["state"] == "upstream_failed"
