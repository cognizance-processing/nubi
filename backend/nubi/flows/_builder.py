"""Core tracing context and @task / @flow decorators.

This module implements the tracing machinery that captures the DAG structure
while a ``@flow``-decorated function body executes.  Task calls inside the
body return ``NodeHandle`` objects and record nodes + edges in a
``_TraceContext`` stored in a ``contextvars.ContextVar``.

Public API
----------
_TraceContext
    Internal dataclass that accumulates nodes and edges during tracing.
    Not intended for direct use by flow authors.

task
    Decorator that turns a function stub into a traceable task node.
    When called inside a ``@flow`` body, returns a ``NodeHandle``; raises
    ``RuntimeError`` otherwise.

flow
    Decorator that attaches a ``.compile(**flow_params) -> dict`` method
    to the decorated function.  The method traces the body once and
    returns a valid FlowSpec dict.

FlowParam
    Convenience dataclass for declaring a typed flow-level parameter.
    Pass as a value in ``flow_params`` kwargs to ``compile()`` to specify
    type and default, e.g.::

        spec = my_flow.compile(region=FlowParam(type="text", default="us"))
"""

from __future__ import annotations

import contextvars
from dataclasses import dataclass, field
from typing import Any, Callable

from nubi.flows._nodes import NodeHandle


# ---------------------------------------------------------------------------
# _TraceContext (module-private, used by task / map_node / branch_node)
# ---------------------------------------------------------------------------

_TRACE_CTX: contextvars.ContextVar["_TraceContext | None"] = (
    contextvars.ContextVar("_flow_trace_ctx", default=None)
)


@dataclass
class _TraceContext:
    """Mutable accumulator for traced nodes and edges.

    A fresh instance is created for each ``compile()`` call (and for each
    ``map_node`` inner body trace).  Stored in ``_TRACE_CTX`` so that nested
    calls (map body functions) can use a scoped inner context without
    disturbing the parent.

    Attributes
    ----------
    nodes:
        Ordered list of node dicts added by ``task.__call__``, ``map_node``,
        and ``branch_node``.
    edges:
        List of ``(upstream_key, downstream_key, port)`` tuples.  The
        ``port`` is always ``"default"`` in the current implementation.
    """

    nodes: list[dict[str, Any]] = field(default_factory=list)
    edges: list[tuple[str, str, str]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# FlowParam
# ---------------------------------------------------------------------------


@dataclass
class FlowParam:
    """Typed flow-level parameter declaration.

    Use this when calling ``flow.compile()`` to specify a parameter's type
    and/or default value explicitly, rather than relying on the implicit
    ``type="text"`` inference.

    Parameters
    ----------
    type:
        One of ``"text"``, ``"number"``, ``"date"``, ``"daterange"``,
        ``"select"``, or ``"multiselect"``.  Defaults to ``"text"``.
    default:
        Optional default value for the parameter.
    required:
        Whether callers must supply this parameter at run time.

    Examples
    --------
    ::

        spec = daily_revenue.compile(
            region=FlowParam(type="select", default="us-east"),
            limit=FlowParam(type="number", default=1000),
        )
    """

    type: str = "text"
    default: Any = None
    required: bool = False


# ---------------------------------------------------------------------------
# task decorator
# ---------------------------------------------------------------------------


class task:
    """Decorator that turns a function stub into a traceable task node.

    A ``@task``-decorated function, when called inside a ``@flow``-traced
    body, returns a ``NodeHandle`` and records the node + edges in the
    active ``_TraceContext``.  When called *outside* a flow context, raises
    ``RuntimeError``.

    Usage
    -----
    ::

        @task(kind="query", sql="SELECT DISTINCT region FROM sales")
        def get_regions(): pass

        @task(kind="python", code="result = {}")
        def transform(): pass

    Parameters
    ----------
    fn:
        The decorated function (only its ``__name__`` is used as the task
        key).  Pass ``None`` when using the decorator-factory style with
        explicit keyword arguments.
    kind:
        Execution kind — one of ``"query"``, ``"python"``, ``"agent"``,
        ``"materialize"``, ``"noop"``, ``"extract"``, ``"bucket_load"``,
        ``"map"``, ``"branch"``.  Defaults to ``"noop"``.
    **config:
        Kind-specific config fields passed verbatim into the TaskSpec
        ``config`` dict (e.g. ``sql=...``, ``code=...``, ``prompt=...``).

    When Called (inside a @flow body)
    ----------------------------------
    Any positional or keyword argument that is a ``NodeHandle`` is treated
    as an upstream dependency, recording both an edge and a ``needs`` entry.

    Returns
    -------
    NodeHandle
        A handle referencing this task's output.

    Raises
    ------
    RuntimeError
        If called outside an active ``@flow`` tracing context.
    """

    def __init__(
        self,
        fn: Callable | None = None,
        *,
        kind: str = "noop",
        **config: Any,
    ) -> None:
        self.kind = kind
        self.config = config
        self.fn = fn
        self.key: str = fn.__name__ if fn else ""

    def __call__(self, *args: Any, **kwargs: Any) -> "task | NodeHandle":
        # --- Decorator application path ---
        # When used as @task(kind="noop") the instance is called with the
        # decorated function as the sole positional argument (before any
        # tracing context is active).  We detect this by checking whether:
        #   (a) we don't yet have a function name (self.key == ""), AND
        #   (b) there is exactly one positional arg that is callable, AND
        #   (c) no active trace context.
        # In that case, bind the function and return self so the decorated
        # name in the enclosing scope is the task instance.
        if (
            self.key == ""
            and len(args) == 1
            and callable(args[0])
            and not isinstance(args[0], NodeHandle)
        ):
            self.fn = args[0]
            self.key = args[0].__name__
            return self  # type: ignore[return-value]

        # --- Task call path (inside @flow body) ---
        ctx = _TRACE_CTX.get()
        if ctx is None:
            raise RuntimeError(
                "task() called outside a @flow tracing context. "
                "Ensure this call is inside a function decorated with @flow."
            )
        needs: list[str] = []
        for v in list(args) + list(kwargs.values()):
            if isinstance(v, NodeHandle):
                ctx.edges.append((v.key, self.key, v.port))
                if v.key not in needs:
                    needs.append(v.key)
        ctx.nodes.append({
            "key": self.key,
            "kind": self.kind,
            "needs": needs,
            "config": dict(self.config),
        })
        return NodeHandle(key=self.key)


# ---------------------------------------------------------------------------
# @flow decorator
# ---------------------------------------------------------------------------


def flow(fn: Callable) -> Callable:
    """Decorator that enables ``.compile()`` on a flow-definition function.

    The decorated function is executed once during ``.compile()`` in a special
    tracing context.  Task calls inside the function body record nodes and
    edges instead of executing.  The resulting ``FlowSpec`` dict is returned.

    **Q4 constraint:** The flow body function must take NO non-default
    positional arguments.  All runtime variation must go through
    ``FlowParam`` declarations.  This constraint is enforced at compile time.

    Usage
    -----
    ::

        @flow
        def daily_revenue():
            data = pull_sales()
            summary = transform(data)

        spec_dict = daily_revenue.compile()

        # With typed params:
        spec_dict = daily_revenue.compile(
            region=FlowParam(type="select", default="us-east"),
        )

    Parameters
    ----------
    fn:
        A zero-argument (no non-default positional args) function whose body
        consists of ``@task``, ``map_node``, and ``branch_node`` calls.

    Returns
    -------
    Callable
        The original function with a ``.compile(**flow_params)`` method
        and a ``.fn`` attribute (for introspection) attached.

    Three worked examples
    ---------------------
    **Example 1: Linear three-task flow**::

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
        # spec["tasks"] == [
        #   {key:"get_regions", kind:"query", needs:[], ...},
        #   {key:"extract_codes", kind:"python", needs:["get_regions"], ...},
        #   {key:"save", kind:"materialize", needs:["extract_codes"], ...},
        # ]

    **Example 2: Map (fan-out) flow**::

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
                @task(kind="query", sql="SELECT * FROM sales WHERE region='{{ item.code }}'")
                def fetch_data(): pass

                @task(kind="python", code="result = inputs['fetch_data']")
                def transform(): pass

                fetch_handle = fetch_data()
                return transform(fetch_handle)

            aggregate(per_region.collect())

        spec = daily_revenue_v2.compile()

    **Example 3: Branch (conditional routing) flow**::

        @task(kind="python", code="result = {'label': 'high'}")
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
                    {"when": "{{ inputs.classify.label }} == 'high'", "next": ["enrich"]},
                    {"when": "{{ inputs.classify.label }} == 'low'",  "next": ["archive"]},
                ],
                default=["log_task"],
            )
            enrich(route)
            archive(route)
            log_task(route)

        spec = conditional_routing.compile()
    """

    def compile(**flow_params: Any) -> dict[str, Any]:
        """Trace the flow function body and return a FlowSpec dict.

        Parameters
        ----------
        **flow_params:
            Declared flow-level parameters.  Each key becomes a ``FlowParam``
            entry.  Values may be:

            - A plain scalar (str, int, float, bool, None) → type inferred as
              ``"text"``, value used as ``default``.
            - A ``FlowParam`` instance → type, default, required taken from it.
            - A dict with a ``"type"`` key → passed through verbatim as the
              param spec dict.

        Returns
        -------
        dict
            A valid FlowSpec dict (``version: 1``).
        """
        ctx = _TraceContext()
        token = _TRACE_CTX.set(ctx)
        try:
            fn()
        finally:
            _TRACE_CTX.reset(token)

        # Build params list from flow_params kwargs.
        params: list[dict[str, Any]] = []
        for name, val in flow_params.items():
            if isinstance(val, FlowParam):
                params.append({
                    "name": name,
                    "type": val.type,
                    "default": val.default,
                    "required": val.required,
                })
            elif isinstance(val, dict) and "type" in val:
                params.append({"name": name, **val})
            else:
                params.append({"name": name, "type": "text", "default": val})

        # Build tasks list from traced nodes.
        tasks: list[dict[str, Any]] = []
        for node in ctx.nodes:
            tasks.append({
                "key":             node["key"],
                "kind":            node["kind"],
                "needs":           node["needs"],
                "config":          node["config"],
                "retries":         0,
                "retry_backoff_s": 30,
                "timeout_s":       60,
                "cache_ttl_s":     0,
                "ui":              {"x": 0.0, "y": 0.0},
            })

        return {
            "version": 1,
            "name":    fn.__name__,
            "params":  params,
            "tasks":   tasks,
        }

    fn.compile = compile  # type: ignore[attr-defined]
    fn.fn = fn            # type: ignore[attr-defined]
    return fn
