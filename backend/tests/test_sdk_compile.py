"""Integration tests for the Nubi flows SDK — compile, validate, and execute.

This module tests the full authoring → execution → codegen round-trip using
the InMemoryFlowStore and the drain_flow_run path.

Coverage
--------
1. SDK compile — all three blueprint example flows via the nubi DSL
   a. Example 1: linear 3-task pipeline (query → python → materialize).
   b. Example 2: map (fan-out) flow with body tasks (daily_revenue_v2 style).
   c. Example 3: branch (conditional routing) flow.
   Each must:
   - Produce a valid FlowSpec dict (validate_flow_spec passes, no hard errors).
   - Have correct task kinds, needs graphs, and config fields.

2. validate_flow_spec on SDK-compiled specs
   a. validate_flow_spec accepts 'map' and 'branch' in TaskSpec.kind.
   b. No hard errors for the three example specs.
   c. branch conditions cross-reference check passes.

3. End-to-end execution via drain path
   a. A compiled noop-only linear flow runs to 'success'.
   b. A compiled flow with map runs to 'success' (with item injection).
   c. A compiled flow with branch runs to 'success' (branch taken correctly).

4. Codegen round-trip — flow_spec_to_sdk then compile
   a. flow_spec_to_sdk(spec) produces valid Python source.
   b. Executing the generated source (exec) and calling compile() on the
      resulting flow function yields a spec whose tasks, kinds, configs, needs,
      and params match the original spec (modulo ui coords).
   c. Round-trip for a map flow preserves body tasks and collect_key.
   d. Round-trip for a branch flow preserves conditions and default.
"""

from __future__ import annotations

import sys
import types
from datetime import datetime, timezone
from typing import Any

import pytest

pytestmark = pytest.mark.asyncio

# ---------------------------------------------------------------------------
# Ensure the nubi package is importable
# ---------------------------------------------------------------------------

sys.path.insert(0, "/Users/pc/code/exo/nubi/backend")

from nubi.flows import (
    FlowParam,
    MapBodyHandle,
    NodeHandle,
    _TRACE_CTX,
    _TraceContext,
    branch_node,
    flow,
    flow_spec_to_sdk,
    map_node,
    task,
)
from nubi.flows._run import arun
from app.flows.spec import validate_flow_spec, flow_spec_is_valid
from app.flows.runtime import (
    advance_readiness,
    drain_flow_run,
    materialize_flow_run,
)
from app.flows.store import InMemoryFlowStore
from app.flows.registry import get_task_kind_registry, reset_for_tests

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


def _validate_no_hard_errors(spec_dict: dict[str, Any]) -> Any:
    """Validate a spec dict; assert no hard errors; return the FlowSpec."""
    spec, issues = validate_flow_spec(spec_dict)
    hard = [i for i in issues if not i.startswith("[warn]")]
    assert not hard, f"Hard errors in spec: {hard}"
    assert spec is not None, "validate_flow_spec returned None unexpectedly"
    return spec


def _compare_tasks(original: list[dict], regenerated: list[dict]) -> None:
    """Assert regenerated tasks match original in kind, config, and needs."""
    orig_by_key = {t["key"]: t for t in original}
    regen_by_key = {t["key"]: t for t in regenerated}

    assert set(orig_by_key.keys()) == set(regen_by_key.keys()), (
        f"Task key mismatch: original={set(orig_by_key.keys())} "
        f"regenerated={set(regen_by_key.keys())}"
    )

    for key, orig in orig_by_key.items():
        regen = regen_by_key[key]
        assert orig["kind"] == regen["kind"], (
            f"Task {key!r}: kind mismatch {orig['kind']} != {regen['kind']}"
        )
        assert set(orig.get("needs") or []) == set(regen.get("needs") or []), (
            f"Task {key!r}: needs mismatch {orig.get('needs')} != {regen.get('needs')}"
        )
        # Config comparison: only check non-internal keys
        orig_cfg = {k: v for k, v in (orig.get("config") or {}).items()
                    if not k.startswith("__")}
        regen_cfg = {k: v for k, v in (regen.get("config") or {}).items()
                     if not k.startswith("__")}
        for cfg_key, cfg_val in orig_cfg.items():
            if cfg_key == "body":
                # Recursively compare body tasks for map nodes
                _compare_tasks(cfg_val, regen_cfg.get("body") or [])
            elif cfg_key == "conditions":
                # Compare branch conditions
                assert cfg_val == regen_cfg.get("conditions"), (
                    f"Task {key!r}: conditions mismatch"
                )
            else:
                assert regen_cfg.get(cfg_key) == cfg_val, (
                    f"Task {key!r}: config.{cfg_key} mismatch "
                    f"{regen_cfg.get(cfg_key)!r} != {cfg_val!r}"
                )


# ---------------------------------------------------------------------------
# 1. SDK compile — blueprint Example 1: Linear 3-task pipeline
# ---------------------------------------------------------------------------


class TestSDKCompileLinearFlow:
    """Author Example 1 via the SDK and verify the compiled spec."""

    def setup_method(self) -> None:
        reset_for_tests()
        _register_handlers()

    def test_linear_flow_produces_valid_spec(self) -> None:
        """Linear 3-task flow compiles to a valid FlowSpec."""

        @task(kind="query", sql="SELECT DISTINCT region FROM sales")
        def get_regions(): pass

        @task(
            kind="python",
            code="result = [r['region'] for r in inputs['get_regions']['rows']]",
        )
        def extract_codes(): pass

        @task(kind="materialize", combine_sql="SELECT * FROM results")
        def save(): pass

        @flow
        def linear_pipeline():
            regions = get_regions()
            codes = extract_codes(regions)
            save(codes)

        spec_dict = linear_pipeline.compile()
        _validate_no_hard_errors(spec_dict)

    def test_linear_flow_task_kinds(self) -> None:
        """Each task must have the correct kind."""

        @task(kind="query", sql="SELECT 1")
        def pull(): pass

        @task(kind="python", code="result = {'ok': True}")
        def transform(): pass

        @task(kind="noop")
        def sync(): pass

        @flow
        def kinds_flow():
            h = pull()
            h2 = transform(h)
            sync(h2)

        spec_dict = kinds_flow.compile()
        by_key = {t["key"]: t for t in spec_dict["tasks"]}
        assert by_key["pull"]["kind"] == "query"
        assert by_key["transform"]["kind"] == "python"
        assert by_key["sync"]["kind"] == "noop"

    def test_linear_flow_needs_chain(self) -> None:
        """Needs graph must be pull→transform→sync."""

        @task(kind="query", sql="SELECT 1")
        def step1(): pass

        @task(kind="python", code="result = {}")
        def step2(): pass

        @task(kind="noop")
        def step3(): pass

        @flow
        def chain_flow():
            h1 = step1()
            h2 = step2(h1)
            step3(h2)

        spec_dict = chain_flow.compile()
        by_key = {t["key"]: t for t in spec_dict["tasks"]}
        assert by_key["step1"]["needs"] == []
        assert by_key["step2"]["needs"] == ["step1"]
        assert by_key["step3"]["needs"] == ["step2"]

    def test_linear_flow_config_preserved(self) -> None:
        """Config kwargs must be preserved verbatim in the compiled spec."""

        @task(kind="query", sql="SELECT * FROM sales WHERE region='EU'")
        def regional_query(): pass

        @flow
        def config_test():
            regional_query()

        spec_dict = config_test.compile()
        task_dict = spec_dict["tasks"][0]
        assert task_dict["config"]["sql"] == "SELECT * FROM sales WHERE region='EU'"

    def test_linear_flow_with_params(self) -> None:
        """FlowParam declarations appear in spec.params."""

        @task(kind="query", sql="SELECT 1")
        def q(): pass

        @flow
        def param_flow():
            q()

        spec_dict = param_flow.compile(
            region=FlowParam(type="select", default="us-east"),
        )
        params = {p["name"]: p for p in spec_dict["params"]}
        assert "region" in params
        assert params["region"]["type"] == "select"
        assert params["region"]["default"] == "us-east"


# ---------------------------------------------------------------------------
# 2. SDK compile — blueprint Example 2: Map (fan-out) flow
# ---------------------------------------------------------------------------


class TestSDKCompileMapFlow:
    """Author Example 2 (map/fan-out flow) via the SDK and verify spec."""

    def setup_method(self) -> None:
        reset_for_tests()
        _register_handlers()

    def test_map_flow_spec_is_valid(self) -> None:
        """Map flow compiles to a spec with no hard validation errors."""

        @task(kind="query", sql="SELECT DISTINCT region FROM sales")
        def get_regions_m(): pass

        @task(kind="materialize", combine_sql="SELECT * FROM all_results")
        def aggregate_m(): pass

        @flow
        def daily_revenue_v2():
            regions_handle = get_regions_m()

            @map_node(
                key="process_each_region",
                item_expr="{{ inputs.get_regions_m.rows }}",
                item_var="region",
                max_concurrency=4,
                collect_key="transform_m",
            )
            def process_each_region(item):
                @task(
                    kind="query",
                    sql="SELECT * FROM sales WHERE region='{{ item.region_code }}'",
                )
                def fetch_data_m(): pass

                @task(
                    kind="python",
                    code="result = {k: v*2 for k, v in inputs['fetch_data_m']['rows'][0].items()}",
                )
                def transform_m(): pass

                fh = fetch_data_m()
                return transform_m(fh)

            aggregate_m(process_each_region.collect())

        spec_dict = daily_revenue_v2.compile()
        _validate_no_hard_errors(spec_dict)

    def test_map_task_kind(self) -> None:
        """The map node must have kind='map' in the spec."""

        @task(kind="query", sql="SELECT 1")
        def src_task(): pass

        @flow
        def map_kind_flow():
            @map_node(
                key="fanout",
                item_expr="{{ inputs.src_task.rows }}",
            )
            def fanout(item):
                src_task()

        spec_dict = map_kind_flow.compile()
        map_task = next(t for t in spec_dict["tasks"] if t["kind"] == "map")
        assert map_task is not None
        assert map_task["key"] == "fanout"

    def test_map_body_tasks_in_config(self) -> None:
        """Body tasks must appear in config.body of the map task."""

        @task(kind="python", code="result = {}")
        def body_task_a(): pass

        @task(kind="python", code="result = {}")
        def body_task_b(): pass

        @flow
        def map_body_flow():
            @map_node(
                key="m",
                item_expr="{{ inputs.x.rows }}",
            )
            def m(item):
                ha = body_task_a()
                return body_task_b(ha)

        spec_dict = map_body_flow.compile()
        map_task = next(t for t in spec_dict["tasks"] if t["key"] == "m")
        body = map_task["config"]["body"]
        body_keys = [bt["key"] for bt in body]
        assert "body_task_a" in body_keys
        assert "body_task_b" in body_keys

    def test_map_collect_key_preserved(self) -> None:
        """Explicit collect_key must appear in config.collect_key."""

        @task(kind="noop")
        def step1_b(): pass

        @task(kind="noop")
        def step2_b(): pass

        @flow
        def map_collect_flow():
            @map_node(
                key="mc",
                item_expr="{{ inputs.x.items }}",
                collect_key="step1_b",
            )
            def mc(item):
                h = step1_b()
                return step2_b(h)

        spec_dict = map_collect_flow.compile()
        map_task = next(t for t in spec_dict["tasks"] if t["key"] == "mc")
        assert map_task["config"]["collect_key"] == "step1_b"

    def test_map_downstream_task_needs_map_key(self) -> None:
        """Task downstream of map must list the map key in needs."""

        @task(kind="noop")
        def body_noop_c(): pass

        @task(kind="noop")
        def collector_c(): pass

        @flow
        def map_downstream_flow():
            @map_node(key="mapper_c", item_expr="{{ inputs.x.rows }}")
            def mapper_c(item):
                body_noop_c()

            collector_c(mapper_c.collect())

        spec_dict = map_downstream_flow.compile()
        coll_task = next(t for t in spec_dict["tasks"] if t["key"] == "collector_c")
        assert "mapper_c" in coll_task["needs"]

    def test_map_item_var_config(self) -> None:
        """item_var is stored in config.item_var."""

        @task(kind="noop")
        def body_noop_d(): pass

        @flow
        def map_item_var_flow():
            @map_node(
                key="md",
                item_expr="{{ inputs.x.rows }}",
                item_var="row",
            )
            def md(item):
                body_noop_d()

        spec_dict = map_item_var_flow.compile()
        map_task = next(t for t in spec_dict["tasks"] if t["key"] == "md")
        assert map_task["config"]["item_var"] == "row"


# ---------------------------------------------------------------------------
# 3. SDK compile — blueprint Example 3: Branch (conditional routing) flow
# ---------------------------------------------------------------------------


class TestSDKCompileBranchFlow:
    """Author Example 3 (branch/conditional routing) via the SDK and verify spec."""

    def setup_method(self) -> None:
        reset_for_tests()
        _register_handlers()

    def test_branch_flow_spec_is_valid(self) -> None:
        """Branch flow compiles to a spec with no hard validation errors."""

        @task(kind="python", code="result = {'label': 'high_value'}")
        def classify_e3(): pass

        @task(kind="python", code="result = {'enriched': True}")
        def enrich_e3(): pass

        @task(kind="python", code="result = {'archived': True}")
        def archive_e3(): pass

        @task(kind="noop")
        def log_e3(): pass

        @flow
        def conditional_routing_e3():
            score = classify_e3()
            route = branch_node(
                score,
                key="route",
                conditions=[
                    {
                        "when": "{{ inputs.classify_e3.label }} == 'high_value'",
                        "next": ["enrich_e3"],
                    },
                    {
                        "when": "{{ inputs.classify_e3.label }} == 'low_value'",
                        "next": ["archive_e3"],
                    },
                ],
                default=["log_e3"],
            )
            enrich_e3(route)
            archive_e3(route)
            log_e3(route)

        spec_dict = conditional_routing_e3.compile()
        _validate_no_hard_errors(spec_dict)

    def test_branch_task_kind(self) -> None:
        """The branch node must have kind='branch' in the spec."""

        @task(kind="python", code="result = {'go': True}")
        def decide(): pass

        @task(kind="noop")
        def act(): pass

        @task(kind="noop")
        def skip(): pass

        @flow
        def branch_kind_flow():
            h = decide()
            r = branch_node(
                h,
                key="router",
                conditions=[{"when": "True", "next": ["act"]}],
                default=["skip"],
            )
            act(r)
            skip(r)

        spec_dict = branch_kind_flow.compile()
        branch_task = next(t for t in spec_dict["tasks"] if t["kind"] == "branch")
        assert branch_task["key"] == "router"

    def test_branch_upstream_in_needs(self) -> None:
        """Branch node must list its upstream task in needs."""

        @task(kind="noop")
        def upstream_br(): pass

        @task(kind="noop")
        def down_br(): pass

        @flow
        def branch_needs_flow():
            h = upstream_br()
            r = branch_node(
                h,
                key="br",
                conditions=[{"when": "True", "next": ["down_br"]}],
            )
            down_br(r)

        spec_dict = branch_needs_flow.compile()
        br_task = next(t for t in spec_dict["tasks"] if t["key"] == "br")
        assert "upstream_br" in br_task["needs"]

    def test_branch_conditions_preserved(self) -> None:
        """Branch conditions list is preserved verbatim in config.conditions."""
        conds = [
            {"when": "{{ inputs.score.label }} == 'high'", "next": ["high_task"]},
            {"when": "{{ inputs.score.label }} == 'low'", "next": ["low_task"]},
        ]

        @task(kind="noop")
        def score_task(): pass

        @task(kind="noop")
        def high_task(): pass

        @task(kind="noop")
        def low_task(): pass

        @flow
        def branch_conds_flow():
            h = score_task()
            r = branch_node(h, key="router", conditions=conds)
            high_task(r)
            low_task(r)

        spec_dict = branch_conds_flow.compile()
        br_task = next(t for t in spec_dict["tasks"] if t["key"] == "router")
        assert br_task["config"]["conditions"] == conds

    def test_branch_default_none_produces_empty_list(self) -> None:
        """default=None must produce config.default=[] (Q1: else_ optional)."""

        @task(kind="noop")
        def src_br(): pass

        @task(kind="noop")
        def dst_br(): pass

        @flow
        def no_default_flow():
            h = src_br()
            r = branch_node(
                h,
                key="br2",
                conditions=[{"when": "True", "next": ["dst_br"]}],
                default=None,
            )
            dst_br(r)

        spec_dict = no_default_flow.compile()
        br_task = next(t for t in spec_dict["tasks"] if t["key"] == "br2")
        assert br_task["config"]["default"] == []

    def test_branch_cross_reference_validation_passes(self) -> None:
        """Branch next keys must all be declared task keys — validate passes."""

        @task(kind="noop")
        def classify_vld(): pass

        @task(kind="noop")
        def enrich_vld(): pass

        @task(kind="noop")
        def archive_vld(): pass

        @flow
        def cross_ref_flow():
            h = classify_vld()
            r = branch_node(
                h,
                key="route_vld",
                conditions=[
                    {"when": "'{{ inputs.classify_vld.label }}' == 'good'", "next": ["enrich_vld"]},
                ],
                default=["archive_vld"],
            )
            enrich_vld(r)
            archive_vld(r)

        spec_dict = cross_ref_flow.compile()
        spec, issues = validate_flow_spec(spec_dict)
        hard = [i for i in issues if not i.startswith("[warn]")]
        assert not hard, f"Cross-reference validation failed: {hard}"


# ---------------------------------------------------------------------------
# 4. End-to-end execution via drain path
# ---------------------------------------------------------------------------


class TestSDKEndToEndExecution:
    """Compiled specs run end-to-end via materialize → drain."""

    def setup_method(self) -> None:
        reset_for_tests()
        _register_handlers()
        self.store = InMemoryFlowStore()

    async def _flow_obj_from_spec(self, spec_dict: dict[str, Any]) -> dict[str, Any]:
        """Wrap a spec dict as a minimal flow object."""
        return await self.store.create_flow(
            org_id=ORG_ID,
            created_by=CREATED_BY,
            name=spec_dict.get("name", "test"),
            spec=spec_dict,
        )

    async def test_linear_noop_flow_succeeds(self) -> None:
        """Compiled linear flow with noop tasks runs to 'success'."""

        @task(kind="noop")
        def step_a(): pass

        @task(kind="noop")
        def step_b(): pass

        @task(kind="noop")
        def step_c(): pass

        @flow
        def linear_exec():
            ha = step_a()
            hb = step_b(ha)
            step_c(hb)

        spec_dict = linear_exec.compile()
        _validate_no_hard_errors(spec_dict)

        flow_obj = await self._flow_obj_from_spec(spec_dict)
        frun = await materialize_flow_run(self.store, flow_obj, {}, "manual", NOW)
        final = await drain_flow_run(self.store, frun["id"], NOW, CLAIMS, max_steps=50)
        assert final["state"] == "success", f"flow_run: {final['state']}"

    async def test_map_flow_executes_end_to_end(self) -> None:
        """Compiled map flow runs to 'success' with item injection."""

        @task(kind="noop")
        def src_map_exec(): pass

        @task(kind="noop")
        def agg_map_exec(): pass

        @flow
        def map_exec_flow():
            # src_map_exec is a source task that provides the items list.
            src_h = src_map_exec()

            @map_node(
                key="fanout_exec",
                item_expr="{{ inputs.src_map_exec.items }}",
                item_var="item",
                collect_key="proc_exec",
            )
            def fanout_exec(item):
                @task(kind="noop")
                def proc_exec(): pass

                return proc_exec()

            agg_map_exec(fanout_exec.collect())

        spec_dict = map_exec_flow.compile()
        _validate_no_hard_errors(spec_dict)

        flow_obj = await self._flow_obj_from_spec(spec_dict)
        frun = await materialize_flow_run(self.store, flow_obj, {}, "manual", NOW)

        # Inject source result so the map node can fan out
        trs = await self.store.list_task_runs(frun["id"])
        by_key = {tr["task_key"]: tr for tr in trs}
        await self.store.update_task_run(
            by_key["src_map_exec"]["id"],
            {"state": "success", "result": {"items": [{"x": 1}, {"x": 2}]}},
        )
        await advance_readiness(self.store, frun["id"], NOW)
        final = await drain_flow_run(self.store, frun["id"], NOW, CLAIMS, max_steps=100)

        assert final["state"] == "success", f"flow_run: {final['state']}"

        all_trs = await self.store.list_task_runs(frun["id"])
        by_key2 = {tr["task_key"]: tr for tr in all_trs}
        assert by_key2["fanout_exec"]["state"] == "success"
        result = by_key2["fanout_exec"].get("result") or {}
        assert result.get("item_count") == 2

    async def test_branch_flow_executes_end_to_end(self) -> None:
        """Compiled branch flow runs to 'success' with the correct arm taken."""

        @task(kind="noop")
        def classify_br_exec(): pass

        @task(kind="noop")
        def enrich_br_exec(): pass

        @task(kind="noop")
        def archive_br_exec(): pass

        @flow
        def branch_exec_flow():
            h = classify_br_exec()
            r = branch_node(
                h,
                key="route_br_exec",
                conditions=[
                    {
                        "when": "'{{ inputs.classify_br_exec.label }}' == 'good'",
                        "next": ["enrich_br_exec"],
                    },
                    {
                        "when": "'{{ inputs.classify_br_exec.label }}' == 'bad'",
                        "next": ["archive_br_exec"],
                    },
                ],
                default=["archive_br_exec"],
            )
            enrich_br_exec(r)
            archive_br_exec(r)

        spec_dict = branch_exec_flow.compile()
        _validate_no_hard_errors(spec_dict)

        flow_obj = await self._flow_obj_from_spec(spec_dict)
        frun = await materialize_flow_run(self.store, flow_obj, {}, "manual", NOW)

        # Inject classify result
        trs = await self.store.list_task_runs(frun["id"])
        by_key = {tr["task_key"]: tr for tr in trs}
        await self.store.update_task_run(
            by_key["classify_br_exec"]["id"],
            {"state": "success", "result": {"label": "good"}},
        )
        await advance_readiness(self.store, frun["id"], NOW)
        final = await drain_flow_run(self.store, frun["id"], NOW, CLAIMS, max_steps=50)

        assert final["state"] == "success", f"flow_run: {final['state']}"

        all_trs = await self.store.list_task_runs(frun["id"])
        by_key2 = {tr["task_key"]: tr for tr in all_trs}
        assert by_key2["enrich_br_exec"]["state"] == "success"
        assert by_key2["archive_br_exec"]["state"] == "upstream_failed"

    async def test_second_linear_noop_flow_succeeds_via_create_flow(self) -> None:
        """Compiled flow stored via create_flow then drained runs to 'success'."""

        @task(kind="noop")
        def hello_cf(): pass

        @task(kind="noop")
        def world_cf(): pass

        @flow
        def simple_cf():
            h = hello_cf()
            world_cf(h)

        spec_dict = simple_cf.compile()
        _validate_no_hard_errors(spec_dict)

        # Use store.create_flow so _get_task_spec can resolve kinds.
        flow_obj = await self.store.create_flow(
            org_id=ORG_ID,
            created_by=CREATED_BY,
            name=spec_dict.get("name", "simple_cf"),
            spec=spec_dict,
        )
        frun = await materialize_flow_run(self.store, flow_obj, {}, "manual", NOW)
        final = await drain_flow_run(self.store, frun["id"], NOW, CLAIMS, max_steps=50)
        assert final["state"] == "success", f"flow_run: {final['state']}"


# ---------------------------------------------------------------------------
# 5. Codegen round-trip — flow_spec_to_sdk then compile
# ---------------------------------------------------------------------------


class TestCodegenRoundTrip:
    """flow_spec_to_sdk then compile reproduces the original spec."""

    def setup_method(self) -> None:
        reset_for_tests()
        _register_handlers()

    def _exec_generated_source(self, src: str) -> dict[str, Any]:
        """Execute generated source and return the 'spec' variable."""
        ns: dict[str, Any] = {}
        exec(src, ns)  # noqa: S102 — controlled test code, generated source
        regenerated = ns.get("spec")
        assert regenerated is not None, (
            f"Generated code did not produce 'spec'. Source:\n{src}"
        )
        return regenerated

    def test_linear_flow_round_trip(self) -> None:
        """Linear flow: round-trip preserves task kinds, configs, needs."""
        original_spec: dict[str, Any] = {
            "version": 1,
            "name": "rt_linear",
            "params": [],
            "tasks": [
                {
                    "key": "pull",
                    "kind": "query",
                    "needs": [],
                    "config": {"sql": "SELECT 1"},
                    "retries": 0,
                    "retry_backoff_s": 30,
                    "timeout_s": 60,
                    "cache_ttl_s": 0,
                    "ui": {"x": 0, "y": 0},
                },
                {
                    "key": "transform",
                    "kind": "python",
                    "needs": ["pull"],
                    "config": {"code": "result = {'x': 42}"},
                    "retries": 0,
                    "retry_backoff_s": 30,
                    "timeout_s": 60,
                    "cache_ttl_s": 0,
                    "ui": {"x": 260, "y": 0},
                },
                {
                    "key": "sink",
                    "kind": "noop",
                    "needs": ["transform"],
                    "config": {},
                    "retries": 0,
                    "retry_backoff_s": 30,
                    "timeout_s": 60,
                    "cache_ttl_s": 0,
                    "ui": {"x": 520, "y": 0},
                },
            ],
        }

        src = flow_spec_to_sdk(original_spec)
        # Source must be valid Python
        compile(src, "<generated>", "exec")

        regenerated = self._exec_generated_source(src)
        assert regenerated["name"] == "rt_linear"
        _compare_tasks(original_spec["tasks"], regenerated["tasks"])

    def test_map_flow_round_trip(self) -> None:
        """Map flow: round-trip preserves body tasks and collect_key."""
        original_spec: dict[str, Any] = {
            "version": 1,
            "name": "rt_map",
            "params": [],
            "tasks": [
                {
                    "key": "get_items",
                    "kind": "query",
                    "needs": [],
                    "config": {"sql": "SELECT id FROM t"},
                    "retries": 0,
                    "retry_backoff_s": 30,
                    "timeout_s": 60,
                    "cache_ttl_s": 0,
                    "ui": {"x": 0, "y": 0},
                },
                {
                    "key": "proc",
                    "kind": "map",
                    "needs": ["get_items"],
                    "config": {
                        "item_expr": "{{ inputs.get_items.rows }}",
                        "item_var": "row",
                        "max_concurrency": 3,
                        "max_map_size": 500,
                        "collect_key": "xform",
                        "body": [
                            {
                                "key": "xform",
                                "kind": "python",
                                "needs": [],
                                "config": {"code": "result = {'v': item['id']}"},
                                "retries": 0,
                                "retry_backoff_s": 30,
                                "timeout_s": 60,
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
                {
                    "key": "collect_result",
                    "kind": "noop",
                    "needs": ["proc"],
                    "config": {},
                    "retries": 0,
                    "retry_backoff_s": 30,
                    "timeout_s": 60,
                    "cache_ttl_s": 0,
                    "ui": {"x": 520, "y": 0},
                },
            ],
        }

        src = flow_spec_to_sdk(original_spec)
        compile(src, "<generated>", "exec")

        regenerated = self._exec_generated_source(src)
        assert regenerated["name"] == "rt_map"

        # Check task keys present
        regen_by_key = {t["key"]: t for t in regenerated["tasks"]}
        assert "get_items" in regen_by_key
        assert "proc" in regen_by_key
        assert "collect_result" in regen_by_key

        # Map node: kind and collect_key preserved
        proc_regen = regen_by_key["proc"]
        assert proc_regen["kind"] == "map"
        assert proc_regen["config"]["collect_key"] == "xform"
        assert proc_regen["config"]["item_var"] == "row"

        # Body tasks preserved
        body = proc_regen["config"]["body"]
        body_keys = [bt["key"] for bt in body]
        assert "xform" in body_keys

        # collect_result depends on proc
        assert "proc" in regen_by_key["collect_result"]["needs"]

    def test_branch_flow_round_trip(self) -> None:
        """Branch flow: round-trip preserves conditions and default."""
        original_spec: dict[str, Any] = {
            "version": 1,
            "name": "rt_branch",
            "params": [],
            "tasks": [
                {
                    "key": "score",
                    "kind": "python",
                    "needs": [],
                    "config": {"code": "result={'label':'high'}"},
                    "retries": 0,
                    "retry_backoff_s": 30,
                    "timeout_s": 60,
                    "cache_ttl_s": 0,
                    "ui": {"x": 0, "y": 0},
                },
                {
                    "key": "route",
                    "kind": "branch",
                    "needs": ["score"],
                    "config": {
                        "conditions": [
                            {
                                "when": "{{ inputs.score.label == 'high' }}",
                                "next": ["do_high"],
                            },
                            {
                                "when": "{{ inputs.score.label == 'low' }}",
                                "next": ["do_low"],
                            },
                        ],
                        "default": ["fallback"],
                    },
                    "retries": 0,
                    "retry_backoff_s": 30,
                    "timeout_s": 30,
                    "cache_ttl_s": 0,
                    "ui": {"x": 260, "y": 0},
                },
                {
                    "key": "do_high",
                    "kind": "noop",
                    "needs": ["route"],
                    "config": {},
                    "retries": 0,
                    "retry_backoff_s": 30,
                    "timeout_s": 60,
                    "cache_ttl_s": 0,
                    "ui": {"x": 520, "y": -100},
                },
                {
                    "key": "do_low",
                    "kind": "noop",
                    "needs": ["route"],
                    "config": {},
                    "retries": 0,
                    "retry_backoff_s": 30,
                    "timeout_s": 60,
                    "cache_ttl_s": 0,
                    "ui": {"x": 520, "y": 100},
                },
                {
                    "key": "fallback",
                    "kind": "noop",
                    "needs": ["route"],
                    "config": {},
                    "retries": 0,
                    "retry_backoff_s": 30,
                    "timeout_s": 60,
                    "cache_ttl_s": 0,
                    "ui": {"x": 520, "y": 200},
                },
            ],
        }

        src = flow_spec_to_sdk(original_spec)
        compile(src, "<generated>", "exec")

        regenerated = self._exec_generated_source(src)
        assert regenerated["name"] == "rt_branch"

        regen_by_key = {t["key"]: t for t in regenerated["tasks"]}
        assert "score" in regen_by_key
        assert "route" in regen_by_key
        assert "do_high" in regen_by_key
        assert "do_low" in regen_by_key
        assert "fallback" in regen_by_key

        route_regen = regen_by_key["route"]
        assert route_regen["kind"] == "branch"
        assert "score" in route_regen["needs"]

        conds_regen = route_regen["config"]["conditions"]
        assert len(conds_regen) == 2
        # Conditions next lists preserved
        assert conds_regen[0]["next"] == ["do_high"]
        assert conds_regen[1]["next"] == ["do_low"]
        assert route_regen["config"]["default"] == ["fallback"]

    def test_generated_source_is_valid_python(self) -> None:
        """Generated source for all three example flows must be valid Python."""
        specs = [
            # linear
            {
                "version": 1,
                "name": "vp_linear",
                "params": [],
                "tasks": [
                    {
                        "key": "pull",
                        "kind": "query",
                        "needs": [],
                        "config": {"sql": "SELECT 1"},
                        "retries": 0, "retry_backoff_s": 30,
                        "timeout_s": 60, "cache_ttl_s": 0,
                        "ui": {"x": 0, "y": 0},
                    },
                ],
            },
            # map
            {
                "version": 1,
                "name": "vp_map",
                "params": [],
                "tasks": [
                    {
                        "key": "src",
                        "kind": "noop",
                        "needs": [],
                        "config": {},
                        "retries": 0, "retry_backoff_s": 30,
                        "timeout_s": 60, "cache_ttl_s": 0,
                        "ui": {"x": 0, "y": 0},
                    },
                    {
                        "key": "fan",
                        "kind": "map",
                        "needs": ["src"],
                        "config": {
                            "item_expr": "{{ inputs.src.rows }}",
                            "item_var": "item",
                            "max_concurrency": 0,
                            "max_map_size": 100,
                            "collect_key": "proc",
                            "body": [
                                {
                                    "key": "proc",
                                    "kind": "noop",
                                    "needs": [],
                                    "config": {},
                                    "retries": 0, "retry_backoff_s": 30,
                                    "timeout_s": 60, "cache_ttl_s": 0,
                                    "ui": {"x": 0, "y": 0},
                                },
                            ],
                        },
                        "retries": 0, "retry_backoff_s": 30,
                        "timeout_s": 0, "cache_ttl_s": 0,
                        "ui": {"x": 260, "y": 0},
                    },
                ],
            },
        ]
        for spec in specs:
            src = flow_spec_to_sdk(spec)
            try:
                compile(src, "<generated>", "exec")
            except SyntaxError as exc:
                pytest.fail(
                    f"Generated source for {spec['name']!r} has syntax error: {exc}\n"
                    f"Source:\n{src}"
                )

    def test_params_round_trip(self) -> None:
        """Flow params (FlowParam type) survive the round-trip."""
        spec: dict[str, Any] = {
            "version": 1,
            "name": "rt_params",
            "params": [
                {"name": "region", "type": "select", "default": "us-east", "required": False},
            ],
            "tasks": [
                {
                    "key": "q",
                    "kind": "query",
                    "needs": [],
                    "config": {"sql": "SELECT 1"},
                    "retries": 0, "retry_backoff_s": 30,
                    "timeout_s": 60, "cache_ttl_s": 0,
                    "ui": {"x": 0, "y": 0},
                },
            ],
        }

        src = flow_spec_to_sdk(spec)
        compile(src, "<generated>", "exec")
        regenerated = self._exec_generated_source(src)

        params_regen = {p["name"]: p for p in regenerated.get("params", [])}
        assert "region" in params_regen, f"params: {params_regen}"
        # Type is preserved (FlowParam(type='select', ...))
        assert params_regen["region"]["type"] == "select"


# ---------------------------------------------------------------------------
# 6. SDK compile — legacy __env__ kwarg + materialized config block
# ---------------------------------------------------------------------------


class TestSDKEnvAndMaterialized:
    """compile() strips the legacy __env__ kwarg; specs carry no env field."""

    def test_env_kwarg_is_stripped_and_never_a_param(self) -> None:
        @task(kind="noop")
        def step() -> None:  # pragma: no cover - traced, not executed
            pass

        @flow
        def env_flow() -> None:
            step()

        # Legacy __env__ kwarg is accepted but IGNORED (back-compat): the
        # environment is resolved at trigger time, never stored in the spec.
        spec = env_flow.compile(__env__="dev")
        assert "env" not in spec
        # __env__ must NOT leak into params.
        assert all(p["name"] != "__env__" for p in spec["params"])

    def test_env_omitted_has_no_env_key(self) -> None:
        @task(kind="noop")
        def step2() -> None:  # pragma: no cover
            pass

        @flow
        def env_flow2() -> None:
            step2()

        spec = env_flow2.compile()
        assert "env" not in spec
        parsed, issues = validate_flow_spec(spec)
        assert parsed is not None
        assert "env" not in parsed.model_dump()

    def test_materialized_block_round_trips_via_sdk(self) -> None:
        materialized = {
            "kind": "incremental",
            "target": "sales_daily",
            "time_column": "event_ts",
            "unique_key": ["id"],
            "lookback": "3 days",
        }

        @task(kind="query", sql="SELECT * FROM raw")
        def pull() -> None:  # pragma: no cover
            pass

        @task(
            kind="materialize",
            combine_sql="SELECT * FROM pull",
            materialized=materialized,
        )
        def blend() -> None:  # pragma: no cover
            pass

        @flow
        def mat_flow() -> None:
            p = pull()
            blend(p)

        spec = mat_flow.compile(__env__="dev")
        assert "env" not in spec  # legacy kwarg ignored — specs carry no env
        blend_task = [t for t in spec["tasks"] if t["key"] == "blend"][0]
        assert blend_task["config"]["materialized"] == materialized
        # Validates cleanly (incremental requires time_column + target).
        parsed, issues = validate_flow_spec(spec)
        hard = [i for i in issues if not i.startswith("[warn]")]
        assert parsed is not None and not hard, f"validation issues: {hard}"
