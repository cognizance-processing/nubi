"""Tests for the Nubi SDK tracing DSL (nubi.flows package).

Coverage
--------
1. NodeHandle / MapBodyHandle
   a. NodeHandle is a dataclass with key and port fields.
   b. MapBodyHandle.collect() returns a NodeHandle with port="collected".

2. _keygen
   a. slugify converts to snake_case identifiers.
   b. slugify prepends t_ for digit-leading names.
   c. make_unique_key returns base when not in existing.
   d. make_unique_key appends _2, _3, ... for collisions.

3. @task decorator
   a. task() outside @flow raises RuntimeError.
   b. task() inside @flow returns NodeHandle with correct key.
   c. Passing a NodeHandle as arg records it in needs.
   d. Multiple upstream handles recorded correctly.

4. @flow decorator
   a. Attaches .compile() method to the function.
   b. compile() returns a valid FlowSpec dict (version=1, name, params, tasks).
   c. Linear 3-task flow: needs chain is correct.
   d. Flow with no tasks returns empty tasks list.
   e. compile() with FlowParam produces typed param entry.
   f. compile() with plain scalar value produces type="text" param entry.
   g. compile() with dict param containing "type" key passes through verbatim.

5. map_node combinator
   a. @map_node outside @flow raises RuntimeError.
   b. @map_node inside @flow produces a kind="map" node in the spec.
   c. Body tasks are stored in config.body.
   d. collect_key defaults to last body task.
   e. Explicit collect_key is preserved.
   f. MapBodyHandle.collect() returns NodeHandle with port="collected".
   g. map_node body with empty tasks raises ValueError.
   h. map node config fields: item_expr, item_var, max_concurrency, max_map_size.

6. branch_node combinator
   a. branch_node outside @flow raises RuntimeError.
   b. branch_node inside @flow produces a kind="branch" node.
   c. upstream.key appears in needs.
   d. conditions list is stored verbatim in config.
   e. default=None produces config.default=[].
   f. Explicit default is preserved.

7. Worked examples (from blueprint)
   a. Linear 3-task pipeline spec shape.
   b. map flow (daily_revenue_v2 style) spec shape.
   c. branch flow (conditional_routing style) spec shape.

8. flow_spec_to_sdk codegen
   a. Simple spec → generated source contains @flow, @task.
   b. Generated source is valid Python (exec without error).
   c. Round-trip: compile generated code and compare spec.

9. _run module
   a. run_local raises RuntimeError when called from inside a running loop.
"""

from __future__ import annotations

import asyncio
import sys
import types
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Imports under test
# ---------------------------------------------------------------------------

sys.path.insert(0, "/Users/pc/code/exo/nubi/backend")

from nubi.flows._keygen import make_unique_key, slugify
from nubi.flows._nodes import MapBodyHandle, NodeHandle
from nubi.flows._builder import FlowParam, _TraceContext, _TRACE_CTX, flow, task
from nubi.flows._combinators import branch_node, map_node
from nubi.flows._compile import flow_spec_to_sdk


# ===========================================================================
# 1. NodeHandle / MapBodyHandle
# ===========================================================================


class TestNodeHandle:
    def test_default_port(self):
        h = NodeHandle(key="pull")
        assert h.key == "pull"
        assert h.port == "default"

    def test_explicit_port(self):
        h = NodeHandle(key="pull", port="output")
        assert h.port == "output"

    def test_map_body_handle_collect(self):
        h = MapBodyHandle(key="per_region")
        collected = h.collect()
        assert isinstance(collected, NodeHandle)
        assert collected.key == "per_region"
        assert collected.port == "collected"

    def test_map_body_handle_inherits_node_handle(self):
        h = MapBodyHandle(key="foo")
        assert isinstance(h, NodeHandle)


# ===========================================================================
# 2. _keygen
# ===========================================================================


class TestKeygen:
    def test_slugify_basic(self):
        assert slugify("Get Regions!") == "get_regions"

    def test_slugify_hyphen(self):
        assert slugify("hello-world  foo") == "hello_world_foo"

    def test_slugify_digit_start(self):
        assert slugify("123start") == "t_123start"

    def test_slugify_already_snake(self):
        assert slugify("fetch_data") == "fetch_data"

    def test_slugify_empty_fallback(self):
        assert slugify("!!!") == "task"

    def test_make_unique_key_no_collision(self):
        assert make_unique_key("fetch", {"pull", "transform"}) == "fetch"

    def test_make_unique_key_collision(self):
        assert make_unique_key("transform", {"transform"}) == "transform_2"

    def test_make_unique_key_multiple_collisions(self):
        existing = {"transform", "transform_2", "transform_3"}
        assert make_unique_key("transform", existing) == "transform_4"


# ===========================================================================
# 3. @task decorator
# ===========================================================================


class TestTaskDecorator:
    def test_task_outside_flow_raises(self):
        @task(kind="noop")
        def my_task(): pass

        with pytest.raises(RuntimeError, match="outside a @flow"):
            my_task()

    def test_task_inside_flow_returns_node_handle(self):
        @task(kind="noop")
        def hello(): pass

        ctx = _TraceContext()
        token = _TRACE_CTX.set(ctx)
        try:
            result = hello()
        finally:
            _TRACE_CTX.reset(token)

        assert isinstance(result, NodeHandle)
        assert result.key == "hello"

    def test_task_records_node(self):
        @task(kind="query", sql="SELECT 1")
        def pull(): pass

        ctx = _TraceContext()
        token = _TRACE_CTX.set(ctx)
        try:
            pull()
        finally:
            _TRACE_CTX.reset(token)

        assert len(ctx.nodes) == 1
        node = ctx.nodes[0]
        assert node["key"] == "pull"
        assert node["kind"] == "query"
        assert node["config"] == {"sql": "SELECT 1"}
        assert node["needs"] == []

    def test_task_records_upstream_from_handle_arg(self):
        @task(kind="query", sql="SELECT 1")
        def pull(): pass

        @task(kind="python", code="result = {}")
        def transform(): pass

        ctx = _TraceContext()
        token = _TRACE_CTX.set(ctx)
        try:
            h = pull()
            transform(h)
        finally:
            _TRACE_CTX.reset(token)

        transform_node = next(n for n in ctx.nodes if n["key"] == "transform")
        assert transform_node["needs"] == ["pull"]

    def test_task_multiple_upstream_handles(self):
        @task(kind="noop")
        def a(): pass

        @task(kind="noop")
        def b(): pass

        @task(kind="noop")
        def join(): pass

        ctx = _TraceContext()
        token = _TRACE_CTX.set(ctx)
        try:
            ha = a()
            hb = b()
            join(ha, hb)
        finally:
            _TRACE_CTX.reset(token)

        join_node = next(n for n in ctx.nodes if n["key"] == "join")
        assert set(join_node["needs"]) == {"a", "b"}


# ===========================================================================
# 4. @flow decorator
# ===========================================================================


class TestFlowDecorator:
    def test_compile_attaches_method(self):
        @flow
        def my_flow():
            pass

        assert hasattr(my_flow, "compile")
        assert callable(my_flow.compile)

    def test_compile_returns_flow_spec_shape(self):
        @flow
        def empty_flow():
            pass

        spec = empty_flow.compile()
        assert spec["version"] == 1
        assert spec["name"] == "empty_flow"
        assert "tasks" in spec
        assert "params" in spec

    def test_compile_linear_chain(self):
        @task(kind="noop")
        def step_a(): pass

        @task(kind="noop")
        def step_b(): pass

        @task(kind="noop")
        def step_c(): pass

        @flow
        def pipeline():
            ha = step_a()
            hb = step_b(ha)
            step_c(hb)

        spec = pipeline.compile()
        assert len(spec["tasks"]) == 3

        by_key = {t["key"]: t for t in spec["tasks"]}
        assert by_key["step_a"]["needs"] == []
        assert by_key["step_b"]["needs"] == ["step_a"]
        assert by_key["step_c"]["needs"] == ["step_b"]

    def test_compile_with_flow_param_instance(self):
        @task(kind="noop")
        def noop_task(): pass

        @flow
        def parameterised():
            noop_task()

        spec = parameterised.compile(region=FlowParam(type="select", default="us-east"))
        assert len(spec["params"]) == 1
        p = spec["params"][0]
        assert p["name"] == "region"
        assert p["type"] == "select"
        assert p["default"] == "us-east"

    def test_compile_with_scalar_param(self):
        @task(kind="noop")
        def noop_task2(): pass

        @flow
        def parameterised2():
            noop_task2()

        spec = parameterised2.compile(limit=100)
        p = spec["params"][0]
        assert p["name"] == "limit"
        assert p["type"] == "text"
        assert p["default"] == 100

    def test_compile_with_dict_param(self):
        @task(kind="noop")
        def noop_task3(): pass

        @flow
        def parameterised3():
            noop_task3()

        spec = parameterised3.compile(start={"type": "date", "default": "2024-01-01"})
        p = spec["params"][0]
        assert p["name"] == "start"
        assert p["type"] == "date"
        assert p["default"] == "2024-01-01"

    def test_compile_task_fields_present(self):
        @task(kind="query", sql="SELECT 1")
        def q_task(): pass

        @flow
        def spec_check():
            q_task()

        spec = spec_check.compile()
        t = spec["tasks"][0]
        assert "retries" in t
        assert "retry_backoff_s" in t
        assert "timeout_s" in t
        assert "cache_ttl_s" in t
        assert "ui" in t
        assert t["ui"] == {"x": 0.0, "y": 0.0}


# ===========================================================================
# 5. map_node combinator
# ===========================================================================


class TestMapNode:
    def test_map_node_outside_flow_raises(self):
        with pytest.raises(RuntimeError, match="outside a @flow"):

            @map_node(key="foo", item_expr="{{ inputs.src.rows }}")
            def foo(item):
                pass

    def test_map_node_produces_map_kind(self):
        @task(kind="noop")
        def body_task(): pass

        @flow
        def flow_with_map():
            @map_node(
                key="each",
                item_expr="{{ inputs.src.rows }}",
            )
            def each(item):
                body_task()

        spec = flow_with_map.compile()
        map_task = next(t for t in spec["tasks"] if t["kind"] == "map")
        assert map_task["key"] == "each"

    def test_map_node_body_stored(self):
        @task(kind="python", code="result = {}")
        def body_t(): pass

        @flow
        def flow_map2():
            @map_node(
                key="mapper",
                item_expr="{{ inputs.x.rows }}",
            )
            def mapper(item):
                body_t()

        spec = flow_map2.compile()
        map_task = next(t for t in spec["tasks"] if t["key"] == "mapper")
        body = map_task["config"]["body"]
        assert len(body) == 1
        assert body[0]["key"] == "body_t"
        assert body[0]["kind"] == "python"

    def test_map_node_collect_key_defaults_to_last(self):
        @task(kind="noop")
        def first(): pass

        @task(kind="noop")
        def last(): pass

        @flow
        def flow_collect_default():
            @map_node(key="m", item_expr="{{ inputs.src.rows }}")
            def m(item):
                h = first()
                last(h)

        spec = flow_collect_default.compile()
        map_task = next(t for t in spec["tasks"] if t["key"] == "m")
        assert map_task["config"]["collect_key"] == "last"

    def test_map_node_explicit_collect_key(self):
        @task(kind="noop")
        def a_task(): pass

        @task(kind="noop")
        def b_task(): pass

        @flow
        def flow_explicit_collect():
            @map_node(
                key="m2",
                item_expr="{{ inputs.src.rows }}",
                collect_key="a_task",
            )
            def m2(item):
                ha = a_task()
                b_task(ha)

        spec = flow_explicit_collect.compile()
        map_task = next(t for t in spec["tasks"] if t["key"] == "m2")
        assert map_task["config"]["collect_key"] == "a_task"

    def test_map_body_handle_collect(self):
        @task(kind="noop")
        def item_proc(): pass

        @task(kind="noop")
        def final_agg(): pass

        @flow
        def flow_with_collect():
            @map_node(key="fan_out", item_expr="{{ inputs.src.rows }}")
            def fan_out(item):
                item_proc()

            final_agg(fan_out.collect())

        spec = flow_with_collect.compile()
        agg_task = next(t for t in spec["tasks"] if t["key"] == "final_agg")
        # final_agg should have no needs recorded (collect() returns a NodeHandle
        # with key="fan_out", port="collected" — the edge is recorded in ctx.edges
        # but needs are only populated from NodeHandle args).
        # The fan_out MapBodyHandle.collect() returns NodeHandle(key="fan_out", port="collected").
        assert "fan_out" in agg_task["needs"]

    def test_map_node_config_fields(self):
        @task(kind="noop")
        def body_x(): pass

        @flow
        def flow_config_check():
            @map_node(
                key="mx",
                item_expr="{{ inputs.src.rows }}",
                item_var="row",
                max_concurrency=8,
                max_map_size=500,
            )
            def mx(item):
                body_x()

        spec = flow_config_check.compile()
        map_task = next(t for t in spec["tasks"] if t["key"] == "mx")
        cfg = map_task["config"]
        assert cfg["item_expr"] == "{{ inputs.src.rows }}"
        assert cfg["item_var"] == "row"
        assert cfg["max_concurrency"] == 8
        assert cfg["max_map_size"] == 500

    def test_map_node_empty_body_raises(self):
        ctx = _TraceContext()
        token = _TRACE_CTX.set(ctx)
        try:
            with pytest.raises(ValueError, match="no task nodes"):

                @map_node(key="empty_map", item_expr="{{ inputs.x }}")
                def empty_map(item):
                    pass  # no task calls
        finally:
            _TRACE_CTX.reset(token)


# ===========================================================================
# 6. branch_node combinator
# ===========================================================================


class TestBranchNode:
    def test_branch_node_outside_flow_raises(self):
        h = NodeHandle(key="upstream")
        with pytest.raises(RuntimeError, match="outside a @flow"):
            branch_node(
                h,
                key="route",
                conditions=[{"when": "True", "next": ["a"]}],
            )

    def test_branch_node_produces_branch_kind(self):
        @task(kind="python", code="result = {'label': 'high'}")
        def classify_task(): pass

        @task(kind="noop")
        def enrich_task(): pass

        @task(kind="noop")
        def archive_task(): pass

        @flow
        def flow_branch():
            score = classify_task()
            route = branch_node(
                score,
                key="route",
                conditions=[
                    {"when": "{{ inputs.classify_task.label }} == 'high'",
                     "next": ["enrich_task"]},
                ],
                default=["archive_task"],
            )
            enrich_task(route)
            archive_task(route)

        spec = flow_branch.compile()
        branch_task = next(t for t in spec["tasks"] if t["kind"] == "branch")
        assert branch_task["key"] == "route"

    def test_branch_node_upstream_in_needs(self):
        @task(kind="noop")
        def up_task(): pass

        @task(kind="noop")
        def down_a(): pass

        @task(kind="noop")
        def down_b(): pass

        @flow
        def flow_needs_check():
            h = up_task()
            r = branch_node(
                h,
                key="b",
                conditions=[{"when": "True", "next": ["down_a"]}],
                default=["down_b"],
            )
            down_a(r)
            down_b(r)

        spec = flow_needs_check.compile()
        branch_task = next(t for t in spec["tasks"] if t["key"] == "b")
        assert "up_task" in branch_task["needs"]

    def test_branch_node_conditions_stored(self):
        @task(kind="noop")
        def c_task(): pass

        @task(kind="noop")
        def x_task(): pass

        @task(kind="noop")
        def y_task(): pass

        conditions = [
            {"when": "{{ inputs.c_task.score }} > 0.5", "next": ["x_task"]},
            {"when": "{{ inputs.c_task.score }} <= 0.5", "next": ["y_task"]},
        ]

        @flow
        def flow_conditions():
            h = c_task()
            r = branch_node(h, key="r", conditions=conditions)
            x_task(r)
            y_task(r)

        spec = flow_conditions.compile()
        branch_task = next(t for t in spec["tasks"] if t["key"] == "r")
        assert branch_task["config"]["conditions"] == conditions

    def test_branch_node_default_none_produces_empty_list(self):
        @task(kind="noop")
        def src(): pass

        @task(kind="noop")
        def dst(): pass

        @flow
        def flow_no_default():
            h = src()
            r = branch_node(
                h,
                key="br",
                conditions=[{"when": "True", "next": ["dst"]}],
                default=None,
            )
            dst(r)

        spec = flow_no_default.compile()
        branch_task = next(t for t in spec["tasks"] if t["key"] == "br")
        assert branch_task["config"]["default"] == []

    def test_branch_node_explicit_default(self):
        @task(kind="noop")
        def src2(): pass

        @task(kind="noop")
        def fallback(): pass

        @flow
        def flow_explicit_default():
            h = src2()
            r = branch_node(
                h,
                key="br2",
                conditions=[{"when": "False", "next": []}],
                default=["fallback"],
            )
            fallback(r)

        spec = flow_explicit_default.compile()
        branch_task = next(t for t in spec["tasks"] if t["key"] == "br2")
        assert branch_task["config"]["default"] == ["fallback"]


# ===========================================================================
# 7. Worked examples from blueprint
# ===========================================================================


class TestWorkedExamples:
    def test_example1_linear_three_task(self):
        """Blueprint Example 1: linear 3-task flow."""

        @task(kind="query", sql="SELECT DISTINCT region FROM sales")
        def get_regions_ex1(): pass

        @task(
            kind="python",
            code="result = [r['region'] for r in inputs['get_regions_ex1']['rows']]",
        )
        def extract_codes_ex1(): pass

        @task(kind="materialize", combine_sql="SELECT * FROM results")
        def save_ex1(): pass

        @flow
        def linear_pipeline_ex1():
            regions = get_regions_ex1()
            codes = extract_codes_ex1(regions)
            save_ex1(codes)

        spec = linear_pipeline_ex1.compile()

        assert spec["version"] == 1
        assert spec["name"] == "linear_pipeline_ex1"
        by_key = {t["key"]: t for t in spec["tasks"]}

        assert by_key["get_regions_ex1"]["kind"] == "query"
        assert by_key["get_regions_ex1"]["needs"] == []
        assert by_key["extract_codes_ex1"]["needs"] == ["get_regions_ex1"]
        assert by_key["save_ex1"]["needs"] == ["extract_codes_ex1"]

    def test_example2_map_flow(self):
        """Blueprint Example 2: map (fan-out) flow."""

        @task(kind="query", sql="SELECT DISTINCT region FROM sales")
        def get_regions_ex2(): pass

        @task(kind="materialize", combine_sql="SELECT * FROM all_results")
        def aggregate_ex2(): pass

        @flow
        def daily_revenue_v2_ex2():
            regions_handle = get_regions_ex2()

            @map_node(
                key="process_each_region",
                item_expr="{{ inputs.get_regions_ex2.rows }}",
                item_var="region",
                max_concurrency=4,
                collect_key="transform_ex2",
            )
            def process_each_region(item):
                @task(
                    kind="query",
                    sql="SELECT * FROM sales WHERE region='{{ item.region_code }}'",
                )
                def fetch_data_ex2(): pass

                @task(
                    kind="python",
                    code="result = {k: v*2 for k, v in inputs['fetch_data_ex2']['rows'][0].items()}",
                )
                def transform_ex2(): pass

                fh = fetch_data_ex2()
                return transform_ex2(fh)

            aggregate_ex2(process_each_region.collect())

        spec = daily_revenue_v2_ex2.compile()
        by_key = {t["key"]: t for t in spec["tasks"]}

        assert "process_each_region" in by_key
        assert by_key["process_each_region"]["kind"] == "map"
        body = by_key["process_each_region"]["config"]["body"]
        body_keys = [b["key"] for b in body]
        assert "fetch_data_ex2" in body_keys
        assert "transform_ex2" in body_keys
        assert by_key["process_each_region"]["config"]["collect_key"] == "transform_ex2"
        # aggregate_ex2 should depend on process_each_region (via collect()).
        assert "process_each_region" in by_key["aggregate_ex2"]["needs"]

    def test_example3_branch_flow(self):
        """Blueprint Example 3: branch (conditional routing) flow."""

        @task(kind="python", code="result = {'label': 'high_value'}")
        def classify_ex3(): pass

        @task(kind="python", code="result = {'enriched': True}")
        def enrich_ex3(): pass

        @task(kind="python", code="result = {'archived': True}")
        def archive_ex3(): pass

        @task(kind="noop")
        def log_ex3(): pass

        @flow
        def conditional_routing_ex3():
            score = classify_ex3()
            route = branch_node(
                score,
                key="route",
                conditions=[
                    {
                        "when": "{{ inputs.classify_ex3.label }} == 'high_value'",
                        "next": ["enrich_ex3"],
                    },
                    {
                        "when": "{{ inputs.classify_ex3.label }} == 'low_value'",
                        "next": ["archive_ex3"],
                    },
                ],
                default=["log_ex3"],
            )
            enrich_ex3(route)
            archive_ex3(route)
            log_ex3(route)

        spec = conditional_routing_ex3.compile()
        by_key = {t["key"]: t for t in spec["tasks"]}

        assert "route" in by_key
        assert by_key["route"]["kind"] == "branch"
        assert by_key["route"]["needs"] == ["classify_ex3"]
        conds = by_key["route"]["config"]["conditions"]
        assert len(conds) == 2
        assert conds[0]["next"] == ["enrich_ex3"]
        assert conds[1]["next"] == ["archive_ex3"]
        assert by_key["route"]["config"]["default"] == ["log_ex3"]

        # Downstream tasks should depend on the branch node.
        assert "route" in by_key["enrich_ex3"]["needs"]
        assert "route" in by_key["archive_ex3"]["needs"]
        assert "route" in by_key["log_ex3"]["needs"]


# ===========================================================================
# 8. flow_spec_to_sdk codegen
# ===========================================================================


class TestCodegen:
    def _make_simple_spec(self) -> dict[str, Any]:
        return {
            "version": 1,
            "name": "simple_flow",
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
                    "config": {"code": "result = {}"},
                    "retries": 0,
                    "retry_backoff_s": 30,
                    "timeout_s": 60,
                    "cache_ttl_s": 0,
                    "ui": {"x": 260, "y": 0},
                },
            ],
        }

    def test_generated_source_contains_flow(self):
        spec = self._make_simple_spec()
        src = flow_spec_to_sdk(spec)
        assert "@flow" in src
        assert "def simple_flow" in src

    def test_generated_source_contains_task(self):
        spec = self._make_simple_spec()
        src = flow_spec_to_sdk(spec)
        assert "@task" in src
        assert "def pull" in src
        assert "def transform" in src

    def test_generated_source_is_valid_python(self):
        spec = self._make_simple_spec()
        src = flow_spec_to_sdk(spec)
        # Should not raise SyntaxError.
        compile(src, "<generated>", "exec")

    def test_generated_source_round_trip(self):
        """Compile the generated source and compare the resulting spec."""
        spec = self._make_simple_spec()
        src = flow_spec_to_sdk(spec)

        # Execute the generated source in a sandboxed namespace.
        ns: dict[str, Any] = {}
        exec(src, ns)  # noqa: S102 — controlled test code

        # The generated code calls simple_flow.compile() at the end,
        # storing the result in `spec`.
        regenerated = ns.get("spec")
        assert regenerated is not None, "Generated code did not produce `spec`"
        assert regenerated["name"] == "simple_flow"
        assert len(regenerated["tasks"]) == 2
        by_key = {t["key"]: t for t in regenerated["tasks"]}
        assert by_key["pull"]["kind"] == "query"
        assert by_key["transform"]["kind"] == "python"
        assert by_key["transform"]["needs"] == ["pull"]

    def test_codegen_with_params(self):
        spec = {
            "version": 1,
            "name": "parameterised_flow",
            "params": [
                {"name": "region", "type": "select", "default": "us-east", "required": False},
            ],
            "tasks": [
                {
                    "key": "noop_step",
                    "kind": "noop",
                    "needs": [],
                    "config": {},
                    "retries": 0,
                    "retry_backoff_s": 30,
                    "timeout_s": 60,
                    "cache_ttl_s": 0,
                    "ui": {"x": 0, "y": 0},
                },
            ],
        }
        src = flow_spec_to_sdk(spec)
        assert "FlowParam" in src
        assert "region" in src
        assert "select" in src

    def test_codegen_branch_task(self):
        spec = {
            "version": 1,
            "name": "branch_flow",
            "params": [],
            "tasks": [
                {
                    "key": "decide",
                    "kind": "python",
                    "needs": [],
                    "config": {"code": "result = {'go': True}"},
                    "retries": 0, "retry_backoff_s": 30,
                    "timeout_s": 60, "cache_ttl_s": 0,
                    "ui": {"x": 0, "y": 0},
                },
                {
                    "key": "router",
                    "kind": "branch",
                    "needs": ["decide"],
                    "config": {
                        "conditions": [
                            {"when": "True", "next": ["act"]},
                        ],
                        "default": [],
                    },
                    "retries": 0, "retry_backoff_s": 30,
                    "timeout_s": 60, "cache_ttl_s": 0,
                    "ui": {"x": 260, "y": 0},
                },
                {
                    "key": "act",
                    "kind": "noop",
                    "needs": ["router"],
                    "config": {},
                    "retries": 0, "retry_backoff_s": 30,
                    "timeout_s": 60, "cache_ttl_s": 0,
                    "ui": {"x": 520, "y": 0},
                },
            ],
        }
        src = flow_spec_to_sdk(spec)
        assert "branch_node" in src
        compile(src, "<generated>", "exec")


# ===========================================================================
# 9. _run module — RuntimeError in running loop
# ===========================================================================


class TestRunLocal:
    def test_run_local_raises_in_running_loop(self):
        """run_local() must raise RuntimeError when called inside a running loop."""
        from nubi.flows._run import run_local

        @task(kind="noop")
        def hello_r(): pass

        @flow
        def simple_r():
            hello_r()

        # We test this by calling run_local from within an async context.
        async def _inner():
            with pytest.raises(RuntimeError, match="running asyncio event loop"):
                run_local(simple_r)

        asyncio.run(_inner())
