"""Map and branch combinator decorators for the flows tracing DSL.

This module provides the two higher-order combinators that produce compound
node types in the FlowSpec IR:

- ``map_node`` — fan-out combinator.  Produces a ``kind="map"`` task node
  whose ``config.body`` is a sub-DAG traced from the decorated inner
  function.  Returns a ``MapBodyHandle`` whose ``.collect()`` method emits
  the fan-in ``NodeHandle``.

- ``branch_node`` — conditional routing.  Produces a ``kind="branch"``
  task node.  Returns a ``NodeHandle`` that downstream tasks can list in
  their ``needs``.

Both combinators must be called inside an active ``@flow`` tracing context
(i.e. inside a ``@flow``-decorated function body or a ``compile()`` call).

Design decisions (from blueprint)
----------------------------------
- Q1: ``branch`` ``else_`` is OPTIONAL — ``default=None`` is a no-op on
  the falsy path (no default; unmatched branch raises at runtime).
- Q2: single task key per branch arm; multiple downstream via a noop.
- Q3: map collector is a dedicated ``map_collect`` handler (``collect_key``
  points to the final body task, defaults to last body node).
- Q4: ``@flow`` body takes NO non-default positional args.
"""

from __future__ import annotations

from typing import Any, Callable

from nubi.flows._builder import _TRACE_CTX, _TraceContext
from nubi.flows._nodes import MapBodyHandle, NodeHandle


# ---------------------------------------------------------------------------
# map_node
# ---------------------------------------------------------------------------


def map_node(
    *,
    key: str,
    item_expr: str,
    item_var: str = "item",
    max_concurrency: int = 0,
    max_map_size: int = 1000,
    collect_key: str | None = None,
) -> Callable[[Callable], MapBodyHandle]:
    """Decorator factory for declaring a map (fan-out) node inside a ``@flow``.

    The decorated inner function is traced once (with a fresh ``_TraceContext``)
    to produce the body sub-DAG.  It receives a sentinel ``item`` NodeHandle
    that represents the current loop item.  Tasks inside the body may
    reference ``{{ item.<field> }}`` in their config strings.

    Usage
    -----
    ::

        @flow
        def my_flow():
            regions = get_regions()

            @map_node(
                key="per_region",
                item_expr="{{ inputs.get_regions.rows }}",
                item_var="region",
                max_concurrency=4,
                collect_key="transform",
            )
            def per_region(item):
                fetch_handle = fetch_data()
                return transform(fetch_handle)

            aggregate(per_region.collect())

    Parameters
    ----------
    key:
        Task key for the map node in the parent flow (e.g. ``"per_region"``).
    item_expr:
        Template expression resolving to the iterable at runtime (e.g.
        ``"{{ inputs.get_regions.rows }}"``).
    item_var:
        Variable namespace for item fields inside body task configs.
        Body configs may use ``{{ item.<field> }}`` to reference the current
        item.  Defaults to ``"item"``.
    max_concurrency:
        Maximum simultaneous child item executions.  ``0`` means unlimited.
    max_map_size:
        Hard cap on the number of items.  Runtime raises if exceeded.
    collect_key:
        Which body task key's result is collected into the fan-in output
        list.  Defaults to the last node emitted by the body trace if
        not specified.

    Returns
    -------
    Callable
        A decorator that accepts the inner body function and returns a
        ``MapBodyHandle``.  Call ``.collect()`` on the handle to get the
        fan-in edge for downstream tasks.

    Raises
    ------
    RuntimeError
        If used outside an active ``@flow`` tracing context.
    ValueError
        If the body function emits no task nodes.

    Examples
    --------
    Three-task map with collect (from the blueprint worked example)::

        @task(kind="query", sql="SELECT DISTINCT region FROM sales")
        def get_regions(): pass

        @task(kind="materialize", combine_sql="SELECT * FROM results")
        def aggregate(): pass

        @flow
        def daily_revenue_v2():
            regions_handle = get_regions()

            @map_node(
                key="process_each_region",
                item_expr="{{ inputs.get_regions.rows }}",
                item_var="region",
                max_concurrency=4,
                collect_key="transform",
            )
            def process_each_region(item):
                @task(kind="query",
                      sql="SELECT * FROM sales WHERE region='{{ item.region_code }}'")
                def fetch_data(): pass

                @task(kind="python",
                      code="result = {k: v*2 for k, v in inputs['fetch_data']['rows'][0].items()}")
                def transform(): pass

                fetch_handle = fetch_data()
                return transform(fetch_handle)

            aggregate(process_each_region.collect())

        spec = daily_revenue_v2.compile()
    """

    def decorator(fn: Callable) -> MapBodyHandle:
        parent_ctx = _TRACE_CTX.get()
        if parent_ctx is None:
            raise RuntimeError("map_node() used outside a @flow tracing context.")

        # Trace the inner body with a fresh context so inner task calls
        # don't pollute the parent node list.
        inner_ctx = _TraceContext()
        token = _TRACE_CTX.set(inner_ctx)
        try:
            fn(NodeHandle(key="__item__"))  # sentinel item handle
        finally:
            _TRACE_CTX.reset(token)

        if not inner_ctx.nodes:
            raise ValueError(
                f"map_node {key!r}: body function emitted no task nodes. "
                "Ensure your body function calls at least one @task."
            )

        body_tasks: list[dict[str, Any]] = [
            {
                "key":             node["key"],
                "kind":            node["kind"],
                "needs":           node["needs"],
                "config":          node["config"],
                "retries":         0,
                "retry_backoff_s": 30,
                "timeout_s":       60,
                "cache_ttl_s":     0,
                "ui":              {"x": 0.0, "y": 0.0},
            }
            for node in inner_ctx.nodes
        ]

        # Derive effective_collect: last body node key if not specified.
        effective_collect = collect_key or body_tasks[-1]["key"]

        parent_ctx.nodes.append({
            "key":   key,
            "kind":  "map",
            "needs": [],  # map node needs are expressed via item_expr, not direct edges
            "config": {
                "item_expr":       item_expr,
                "item_var":        item_var,
                "max_concurrency": max_concurrency,
                "max_map_size":    max_map_size,
                "collect_key":     effective_collect,
                "body":            body_tasks,
            },
        })
        return MapBodyHandle(key=key)

    return decorator


# ---------------------------------------------------------------------------
# branch_node
# ---------------------------------------------------------------------------


def branch_node(
    upstream: NodeHandle,
    *,
    key: str,
    conditions: list[dict[str, Any]],
    default: list[str] | None = None,
) -> NodeHandle:
    """Declare a branch (conditional routing) node inside a ``@flow``.

    The branch node evaluates an ordered list of ``when`` conditions against
    the upstream task's result and activates only the matching ``next`` task
    keys.  Non-matching tasks are marked ``upstream_failed`` by the runtime.

    This corresponds to a Python ``if/elif/else`` mental model — first match
    wins.

    **Q1 (resolved):** ``default=None`` means no-op on the unmatched path.
    If no condition matches and ``default`` is ``None`` or ``[]``, the
    runtime raises a ``ValueError`` so the flow fails visibly rather than
    silently hanging.

    **Q2 (resolved):** Each branch arm must list a single task key.
    Multiple downstream tasks from one arm must be connected via a ``noop``
    join task.

    Usage
    -----
    ::

        @flow
        def conditional_routing():
            score = classify()
            route = branch_node(
                score,
                key="route",
                conditions=[
                    {"when": "{{ inputs.classify.label }} == 'high'", "next": ["enrich"]},
                    {"when": "{{ inputs.classify.label }} == 'low'",  "next": ["archive"]},
                ],
                default=["log_task"],
            )
            enrich(route)
            archive(route)
            log_task(route)

    Parameters
    ----------
    upstream:
        NodeHandle of the task whose result is evaluated by the conditions.
        An edge is automatically recorded from ``upstream.key`` to ``key``.
    key:
        Task key for this branch node in the parent flow.
    conditions:
        Ordered list of ``{when: <template_expr>, next: [task_key, ...]}``
        dicts.  First matching condition wins.  The ``when`` expression is
        evaluated after ``{{ }}`` template resolution — it should be a Python
        boolean expression (e.g. ``"True"``, ``"'high' == 'high'"``, etc.).
    default:
        Task keys to activate when no condition matches.  If ``None`` or
        ``[]``, an unmatched branch raises a ``ValueError`` at runtime.
        This is **Q1 resolved** — ``else_`` is optional / no-op.

    Returns
    -------
    NodeHandle
        A handle for the branch node.  Downstream tasks should list this
        handle as an argument to record their dependency on the branch.

    Raises
    ------
    RuntimeError
        If used outside an active ``@flow`` tracing context.

    Examples
    --------
    Branch with explicit else (from the blueprint worked example)::

        @task(kind="python", code="result = {'label': 'high_value'}")
        def classify(): pass

        @task(kind="python", code="result = {'enriched': True}")
        def enrich(): pass

        @task(kind="python", code="result = {'archived': True}")
        def archive(): pass

        @task(kind="noop")
        def log_task(): pass

        @flow
        def my_flow():
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

        spec = my_flow.compile()
    """
    ctx = _TRACE_CTX.get()
    if ctx is None:
        raise RuntimeError("branch_node() used outside a @flow tracing context.")

    ctx.edges.append((upstream.key, key, "default"))
    ctx.nodes.append({
        "key":   key,
        "kind":  "branch",
        "needs": [upstream.key],
        "config": {
            "conditions": conditions,
            "default":    default or [],
        },
    })
    return NodeHandle(key=key)
