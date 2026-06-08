"""Nubi Flows Python SDK — tracing DSL and codegen.

This package provides a code-first authoring path for Nubi Flows.  Author a
flow as a plain Python function, decorate it with ``@flow``, and call
``.compile()`` to produce a ``FlowSpec`` dict that the Nubi engine can
execute.

Public API
----------
flow
    Decorator that attaches ``.compile()`` to a flow-definition function.
task
    Decorator that turns a function stub into a traceable task node.
map_node
    Decorator factory for declaring a fan-out (map) node.
branch_node
    Function for declaring a conditional-routing (branch) node.
NodeHandle
    Symbolic reference to a task output during flow tracing.
MapBodyHandle
    NodeHandle subclass for map nodes; adds ``.collect()``.
FlowParam
    Typed flow-level parameter declaration.

Runners
-------
run_local(flow_fn, params, *, max_steps, claims) -> dict
    Compile and synchronously run a ``@flow`` via the in-memory store.
    Raises ``RuntimeError`` inside a running event loop — use ``arun()``.

arun(flow_fn, params, *, max_steps, claims) -> Coroutine[dict]
    Async variant of ``run_local``.

Codegen
-------
flow_spec_to_sdk(spec) -> str
    Generate scaffold-grade Python SDK source from a FlowSpec dict or model.

Examples
--------
Linear three-task flow::

    from nubi.flows import flow, task

    @task(kind="query", sql="SELECT DISTINCT region FROM sales")
    def get_regions(): pass

    @task(kind="python", code="result = [r['region'] for r in inputs['get_regions']['rows']]")
    def extract_codes(): pass

    @task(kind="materialize", combine_sql="SELECT * FROM results")
    def save(): pass

    @flow
    def linear_pipeline():
        regions = get_regions()
        codes = extract_codes(regions)
        save(codes)

    spec = linear_pipeline.compile()

Map (fan-out) flow::

    from nubi.flows import flow, task, map_node

    @task(kind="query", sql="SELECT DISTINCT region FROM sales")
    def get_regions(): pass

    @task(kind="materialize", combine_sql="SELECT * FROM results")
    def aggregate(): pass

    @flow
    def daily_revenue_v2():
        regions_handle = get_regions()

        @map_node(
            key="per_region",
            item_expr="{{ inputs.get_regions.rows }}",
            item_var="region",
            max_concurrency=4,
            collect_key="transform",
        )
        def per_region(item):
            @task(kind="query",
                  sql="SELECT * FROM sales WHERE region='{{ item.region_code }}'")
            def fetch_data(): pass

            @task(kind="python",
                  code="result = {k: v*2 for k, v in inputs['fetch_data']['rows'][0].items()}")
            def transform(): pass

            fetch_handle = fetch_data()
            return transform(fetch_handle)

        aggregate(per_region.collect())

    spec = daily_revenue_v2.compile()

Branch (conditional routing) flow::

    from nubi.flows import flow, task, branch_node

    @task(kind="python", code="result = {'label': 'high_value'}")
    def classify(): pass

    @task(kind="python", code="result = {'enriched': True}")
    def enrich(): pass

    @task(kind="python", code="result = {'archived': True}")
    def archive(): pass

    @task(kind="noop")
    def log_task(): pass

    @flow
    def conditional_routing():
        score = classify()
        route = branch_node(
            score,
            key="route",
            conditions=[
                {"when": "{{ inputs.classify.label }} == 'high_value'",
                 "next": ["enrich"]},
                {"when": "{{ inputs.classify.label }} == 'low_value'",
                 "next": ["archive"]},
            ],
            default=["log_task"],
        )
        enrich(route)
        archive(route)
        log_task(route)

    spec = conditional_routing.compile()
"""

from __future__ import annotations

from nubi.flows._builder import FlowParam, _TraceContext, _TRACE_CTX, flow, task
from nubi.flows._combinators import branch_node, map_node
from nubi.flows._compile import flow_spec_to_sdk
from nubi.flows._keygen import make_unique_key, slugify
from nubi.flows._nodes import MapBodyHandle, NodeHandle
from nubi.flows._run import arun, run_local

__all__ = [
    # Decorators / combinators
    "flow",
    "task",
    "map_node",
    "branch_node",
    # Handle types
    "NodeHandle",
    "MapBodyHandle",
    # Param type
    "FlowParam",
    # Runners
    "run_local",
    "arun",
    # Codegen
    "flow_spec_to_sdk",
    # Internals (for advanced use / testing)
    "_TraceContext",
    "_TRACE_CTX",
    # Key utilities
    "slugify",
    "make_unique_key",
]
