# Flows SDK Blueprint — Sign-Off Document

**Status:** DRAFT — pending human sign-off on Open Questions before implementation begins.

This document consolidates the Engine Architecture Research and Current-State Ground Truth artifacts into a single, implementation-ready specification. It covers:

1. Final SDK API surface
2. FlowSpec schema diff (map / branch)
3. Execution semantics
4. Round-trip and codegen plan
5. Open questions requiring human decision
6. Implementation task graph

---

## 1. Final SDK API Surface

### 1.1 Design Decisions (resolved conflicts)

**Conflict: `item_expr` (string template) vs `over` (dot-path) for the map source**

The research artifact used `item_expr`; the engine-ground-truth document used both `item_expr` and `over`. Decision: use `item_expr` as the **string template expression** evaluated against `ctx.inputs` and `ctx.flow_params`, consistent with the existing `{{ inputs.x.y }}` templating already in the executor. The config key is `item_expr` everywhere. Rationale: the executor's `_resolve_any` already handles dot-path resolution; an `item_expr` string of `"{{ inputs.get_regions.rows }}"` is directly resolvable with zero new infrastructure.

**Conflict: branch config shape — `conditions: list` vs `branches: dict`**

The research used `branches: dict[label, {next: list}]`; the ground-truth used `conditions: list[{expr, next}]`. Decision: use `conditions: list[{when, next}]` with a separate `default` key. Rationale: a list preserves order for deterministic first-match evaluation; a dict implies unordered keys. This matches Python's `if/elif/else` mental model and is directly serialisable to JSON without relying on key-ordering guarantees.

**Conflict: branch rejoin (diamond merge) — allowed or not?**

Decision: **allowed**. Multiple branches may name the same `next` task key. The engine will mark a task `ready` when its single `branch` upstream reaches `success` regardless of which branch was taken — all branches' `next` tasks must appear in the flat `tasks` list with `needs: ["<branch_key>"]`. The branch node emits a result `{branch_taken: "<label>"}` so downstream tasks can read `{{ inputs.route.branch_taken }}`. Tasks in inactive branches are set to `upstream_failed` by `advance_readiness`.

**Conflict: multi-output tasks (named ports on NodeHandle)**

Decision: **deferred** to a follow-up. The current `needs: list[str]` schema supports single-output tasks. Named ports (e.g., `"task_key.port_name"`) require a schema change to `needs` and a new `NodeHandle(key, port)` type. This is a separable enhancement; the initial SDK will use single-output tasks only. The `NodeHandle` struct carries a `port` field but it defaults to `"default"` and the spec never serialises it.

**Conflict: map child task_run storage — siblings vs nested flow_run**

Decision: **siblings with `parent_task_run_id`**. Child task_runs for each map item are stored in the same `flow_run`, identified by composite key `"{map_key}[{i}].{child_task_key}"`. This enables per-item observability in the canvas and matches Airflow's mapped task instance model. A nested flow_run approach would require a separate `materialize_flow_run` call, double the event overhead, and break `advance_readiness` (which must see all task_runs for a flow_run in one query).

**Conflict: static fan-out vs dynamic fan-out for map**

Decision: **dynamic** — items are resolved at execution time, not at `materialize_flow_run` time. Rationale: the item list comes from an upstream task result, which does not exist until that task runs. The `map` task_run is created at materialize time as a single `pending` task_run; when it is claimed, the handler resolves `item_expr`, creates child task_runs for each item, and marks itself `waiting_children` (a new internal state). `advance_readiness` detects all children terminal and transitions the map task_run to `success`. The flat tasks list in the spec is not expanded at materialize time.

### 1.2 SDK Package Structure

```
backend/app/flows/
  sdk.py          ← NEW: @flow / @task / map_node / branch_node tracing DSL
  codegen.py      ← NEW: flow_spec_to_sdk(spec: FlowSpec) -> str
  handlers/
    map.py        ← NEW: _handle_map
    branch.py     ← NEW: _handle_branch
```

### 1.3 Core SDK Signatures

```python
# backend/app/flows/sdk.py

from __future__ import annotations
import contextvars
from dataclasses import dataclass, field
from typing import Any, Callable

# ---------------------------------------------------------------------------
# Context variable (thread/coroutine-safe, unlike a module-level global)
# ---------------------------------------------------------------------------

_TRACE_CTX: contextvars.ContextVar["_TraceContext | None"] = \
    contextvars.ContextVar("_flow_trace_ctx", default=None)


@dataclass
class NodeHandle:
    """Symbolic reference to a task output during flow tracing.

    During tracing, task calls return NodeHandle objects instead of real data.
    Passing a NodeHandle as an argument to another task call records an edge.

    Attributes
    ----------
    key:
        The task key that produced this output.
    port:
        Output port name (always "default" until named multi-output support lands).
    """
    key: str
    port: str = "default"


@dataclass
class _TraceContext:
    nodes: list[dict[str, Any]] = field(default_factory=list)
    # (upstream_key, downstream_key, port) — port is "default" for now
    edges: list[tuple[str, str, str]] = field(default_factory=list)


class task:
    """Decorator that turns a function stub into a traceable task node.

    Usage
    -----
    ::

        @task(kind="query", sql="{{ params.sql }}")
        def pull_sales(): pass

        @task(kind="python", code="result = {}")
        def transform(): pass

    When called inside a @flow-traced function, returns a NodeHandle and
    records the node + edges in the active _TraceContext.

    When called outside a @flow context, raises RuntimeError.

    Parameters
    ----------
    kind:
        One of the TaskSpec.kind values: query, python, agent, materialize,
        noop, extract, bucket_load, map, branch.
    **config:
        Kind-specific config fields passed verbatim into TaskSpec.config.
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

    def __call__(self, *args: Any, **kwargs: Any) -> NodeHandle:
        ctx = _TRACE_CTX.get()
        if ctx is None:
            raise RuntimeError(
                "task() called outside a @flow tracing context. "
                "Ensure this call is inside a function decorated with @flow."
            )
        # Collect NodeHandle arguments as upstream edges.
        needs: list[str] = []
        for v in list(args) + list(kwargs.values()):
            if isinstance(v, NodeHandle):
                ctx.edges.append((v.key, self.key, v.port))
                needs.append(v.key)
        ctx.nodes.append({
            "key": self.key,
            "kind": self.kind,
            "needs": needs,
            "config": dict(self.config),
        })
        return NodeHandle(key=self.key)


def flow(fn: Callable) -> Callable:
    """Decorator that enables .compile() on a flow-definition function.

    Usage
    -----
    ::

        @flow
        def daily_revenue():
            data = pull_sales()
            summary = transform(data)

        spec_dict = daily_revenue.compile()

    The decorated function is executed once during .compile() in a special
    tracing context.  Task calls inside the function body record nodes and
    edges instead of executing.  The resulting FlowSpec dict is returned.

    Parameters
    ----------
    fn:
        A zero-argument function whose body consists of @task calls.

    Returns
    -------
    Callable
        The original function with a .compile(**params) method attached.
    """
    def compile(**flow_params: Any) -> dict[str, Any]:
        """Trace the flow function body and return a FlowSpec dict.

        Parameters
        ----------
        **flow_params:
            Declared flow-level parameters.  Each key becomes a FlowParam
            with type="text" and default=value.  Override types by passing
            FlowParam dicts instead.

        Returns
        -------
        dict
            A valid FlowSpec dict (version 1).
        """
        ctx = _TraceContext()
        token = _TRACE_CTX.set(ctx)
        try:
            fn()
        finally:
            _TRACE_CTX.reset(token)

        # Build params list from flow_params kwargs.
        params = []
        for name, val in flow_params.items():
            if isinstance(val, dict) and "type" in val:
                params.append({"name": name, **val})
            else:
                params.append({"name": name, "type": "text", "default": val})

        # Build tasks list from traced nodes.
        tasks = []
        for node in ctx.nodes:
            tasks.append({
                "key":              node["key"],
                "kind":             node["kind"],
                "needs":            node["needs"],
                "config":           node["config"],
                "retries":          0,
                "retry_backoff_s":  30,
                "timeout_s":        60,
                "cache_ttl_s":      0,
                "ui":               {"x": 0.0, "y": 0.0},
            })

        return {
            "version": 1,
            "name":    fn.__name__,
            "params":  params,
            "tasks":   tasks,
        }

    fn.compile = compile  # type: ignore[attr-defined]
    return fn


def map_node(
    *,
    key: str,
    item_expr: str,
    item_var: str = "item",
    max_concurrency: int = 0,
    max_map_size: int = 1000,
    collect_key: str | None = None,
) -> Callable[[Callable], "MapBodyHandle"]:
    """Decorator factory for declaring a map (fan-out) node inside a @flow.

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
                data = fetch_data()   # can reference {{ item.region_code }}
                return transform(data)

            aggregate(per_region.collect())

    The decorated inner function is traced once (with a fresh _TraceContext)
    to produce the body sub-DAG.  It receives a sentinel `item` NodeHandle.

    Parameters
    ----------
    key:
        The task key for this map node in the parent flow.
    item_expr:
        Template expression resolving to the iterable (e.g.
        ``"{{ inputs.get_regions.rows }}"``).  Evaluated at runtime.
    item_var:
        Name bound as ``{{ item.<field> }}`` in body task configs.
    max_concurrency:
        Maximum simultaneous child item executions (0 = unlimited).
    max_map_size:
        Hard cap on the number of items.  Validation raises if exceeded.
    collect_key:
        Which body task key's result is collected into the list output.
        Defaults to the last node in the body if not specified.

    Returns
    -------
    Callable
        A decorator that returns a MapBodyHandle (a NodeHandle with a
        .collect() method that also returns a NodeHandle).
    """
    def decorator(fn: Callable) -> "MapBodyHandle":
        ctx = _TRACE_CTX.get()
        if ctx is None:
            raise RuntimeError("map_node() used outside a @flow tracing context.")

        # Trace the inner body with a fresh context.
        inner_ctx = _TraceContext()
        token = _TRACE_CTX.set(inner_ctx)
        try:
            fn(NodeHandle(key="__item__"))   # sentinel item handle
        finally:
            _TRACE_CTX.reset(token)

        body_tasks = [
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

        # Derive collect_key: last node if not given.
        effective_collect = collect_key or (body_tasks[-1]["key"] if body_tasks else None)

        ctx.nodes.append({
            "key":  key,
            "kind": "map",
            "needs": [],   # edges below
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


@dataclass
class MapBodyHandle(NodeHandle):
    """NodeHandle for a map node; adds .collect() to signal fan-in."""
    def collect(self) -> NodeHandle:
        """Return a NodeHandle representing the collected results list."""
        return NodeHandle(key=self.key, port="collected")


def branch_node(
    upstream: NodeHandle,
    *,
    key: str,
    conditions: list[dict[str, Any]],
    default: list[str] | None = None,
) -> NodeHandle:
    """Declare a branch (conditional routing) node inside a @flow.

    Usage
    -----
    ::

        @flow
        def my_flow():
            score = classify()
            route = branch_node(
                score,
                key="route",
                conditions=[
                    {"when": "{{ inputs.classify.label == 'high' }}", "next": ["enrich"]},
                    {"when": "{{ inputs.classify.label == 'low' }}",  "next": ["archive"]},
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
    key:
        The task key for this branch node in the parent flow.
    conditions:
        Ordered list of ``{when: <template_expr>, next: [task_key, ...]}``
        dicts.  First matching condition wins.
    default:
        Task keys to activate when no condition matches.
        If None, unmatched branches raise at runtime.

    Returns
    -------
    NodeHandle
        A handle for the branch node.  Downstream tasks should list this
        key in their `needs` (via the handle); the branch runtime ensures
        only matching-branch tasks are actually activated.
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
```

### 1.4 Codegen Signatures

```python
# backend/app/flows/codegen.py

from __future__ import annotations
from app.flows.spec import FlowSpec


def flow_spec_to_sdk(spec: FlowSpec) -> str:
    """Generate scaffold-grade Python SDK source from a FlowSpec.

    The generated source, when traced (i.e. compiled via .compile()), must
    produce a FlowSpec whose tasks, kinds, configs, needs, and params match
    the input spec 1:1.  Layout (ui.x/y) is NOT preserved in the generated
    code (it is a canvas concern, not a code concern).

    This function is scaffold-grade, NOT byte-preserving:
    - Variable names, import aliases, and spacing may differ from the
      original author's source.
    - Comments and docstrings are generated from task keys/kinds.
    - The output is valid, runnable Python that traces correctly.

    Parameters
    ----------
    spec:
        A validated FlowSpec instance.

    Returns
    -------
    str
        Python source code string.
    """
    ...


def _task_to_sdk_call(task_dict: dict, inputs_map: dict[str, list[str]]) -> str:
    """Emit a single @task decorator + function stub + call expression.

    Parameters
    ----------
    task_dict:
        A TaskSpec serialised as dict.
    inputs_map:
        Map from task_key to list of upstream task keys (derived from needs).

    Returns
    -------
    str
        Python source fragment for this task.
    """
    ...


def _map_task_to_sdk(task_dict: dict) -> str:
    """Emit a @map_node decorated inner function for a map TaskSpec."""
    ...


def _branch_task_to_sdk(task_dict: dict) -> str:
    """Emit a branch_node(...) call for a branch TaskSpec."""
    ...
```

---

## 2. FlowSpec Schema Diff — map and branch

### 2.1 TaskSpec.kind extension

```python
# spec.py — TaskSpec.kind field change (only this line changes)

kind: Literal[
    "query", "python", "agent", "materialize",
    "noop", "extract", "bucket_load",
    "map", "branch",                            # ← add these two
] = Field(description="Execution kind.")
```

### 2.2 map node — config contract

```json
{
  "key": "process_each_region",
  "kind": "map",
  "needs": ["get_regions"],
  "config": {
    "item_expr":       "{{ inputs.get_regions.rows }}",
    "item_var":        "region",
    "max_concurrency": 4,
    "max_map_size":    1000,
    "collect_key":     "transform",
    "body": [
      {
        "key":     "fetch_data",
        "kind":    "query",
        "needs":   [],
        "config":  { "sql": "SELECT * FROM sales WHERE region = '{{ item.region_code }}'" },
        "retries": 0, "retry_backoff_s": 30, "timeout_s": 60, "cache_ttl_s": 0,
        "ui":      { "x": 0, "y": 0 }
      },
      {
        "key":     "transform",
        "kind":    "python",
        "needs":   ["fetch_data"],
        "config":  { "code": "result = {k: v*2 for k, v in inputs['fetch_data']['rows'][0].items()}" },
        "retries": 0, "retry_backoff_s": 30, "timeout_s": 60, "cache_ttl_s": 0,
        "ui":      { "x": 260, "y": 0 }
      }
    ]
  },
  "retries": 0, "retry_backoff_s": 30, "timeout_s": 0, "cache_ttl_s": 0,
  "ui": { "x": 320, "y": 200 }
}
```

**Config field contract for `kind: "map"`:**

| Field | Required | Type | Default | Description |
|---|---|---|---|---|
| `item_expr` | Yes | string | — | Template expression resolving to the iterable at runtime. E.g. `"{{ inputs.source.rows }}"`. |
| `item_var` | No | string | `"item"` | Variable namespace for item fields inside body configs. `{{ item.field }}` in body configs. |
| `max_concurrency` | No | int | `0` | Max simultaneous child executions. `0` = unlimited. |
| `max_map_size` | No | int | `1000` | Hard cap on item count. Runtime raises if exceeded. |
| `collect_key` | No | string | last body node key | Which body task key's result is collected into the output list. |
| `body` | Yes | list[TaskSpec dict] | — | Nested sub-DAG. Each element is a full TaskSpec dict. Validated recursively. |

**Result shape when map completes successfully:**

```json
{
  "items": [
    { "index": 0, "result": { ... } },
    { "index": 1, "result": { ... } }
  ],
  "item_count": 2,
  "collect_key": "transform"
}
```

**Constraints enforced at validation time:**

- `body` must be non-empty.
- `body` tasks must form a valid DAG internally (no cycles, no missing needs references).
- `body` tasks may not contain another `map` node (no nested maps).
- `body` tasks may reference `{{ item.<field> }}` but the `item_var` substitution is runtime-only.
- `collect_key` must be a key within `body` if specified.

### 2.3 branch node — config contract

```json
{
  "key": "route",
  "kind": "branch",
  "needs": ["classify"],
  "config": {
    "conditions": [
      { "when": "{{ inputs.classify.label == 'high_value' }}", "next": ["enrich"] },
      { "when": "{{ inputs.classify.label == 'low_value' }}",  "next": ["archive"] }
    ],
    "default": ["log_task"]
  },
  "retries": 0, "retry_backoff_s": 30, "timeout_s": 30, "cache_ttl_s": 0,
  "ui": { "x": 320, "y": 200 }
}
```

**Config field contract for `kind: "branch"`:**

| Field | Required | Type | Default | Description |
|---|---|---|---|---|
| `conditions` | Yes | list[{when, next}] | — | Ordered list. First matching `when` expression wins. `when` is a boolean template expression. `next` is a list of task keys to activate. |
| `default` | No | list[string] | `[]` | Task keys to activate when no condition matches. If empty and no condition matches, all dependent tasks receive `upstream_failed`. |

**`when` expression evaluation:**

- The expression inside `{{ ... }}` is evaluated as a Python boolean expression against the standard template namespace (`inputs`, `params`, `secrets`).
- First match wins; remaining conditions are not evaluated.
- The branch node result is `{"branch_taken": "<label>", "branch_index": <int>}` where label is `"condition_0"`, `"condition_1"`, …, or `"default"`.

**Downstream task behaviour:**

- Tasks listed in `next` for the matched condition are set to `ready`.
- Tasks listed in `next` for non-matched conditions are set to `upstream_failed`.
- Tasks that list the branch key in their `needs` but are NOT in any `next` list remain `pending` indefinitely — this is a spec authoring error caught by validation.
- **Rejoin is supported**: multiple conditions may list the same downstream task key. The task is activated once (by the first condition that names it and matches).

**Validation additions:**

- `conditions` must be non-empty.
- Every key in every `next` list must exist in the parent flow's task list (hard error).
- Every key in `default` must exist in the parent flow's task list (hard error).
- Any task that lists a `branch` key in its `needs` must appear in at least one `next` or `default` list (hard error — unreachable task guard).

### 2.4 validate_flow_spec additions (spec.py)

```python
# Step 5 additions — after existing elif chain

elif task.kind == "map":
    if not cfg.get("item_expr"):
        issues.append(
            f"Task {task.key!r} (map): config must include 'item_expr'."
        )
    body = cfg.get("body")
    if not body or not isinstance(body, list):
        issues.append(
            f"Task {task.key!r} (map): config must include 'body' (non-empty list of TaskSpec dicts)."
        )
    else:
        # Recursive validation of the sub-DAG body.
        sub_spec_data = {
            "version": 1,
            "name":    f"{task.key}__body",
            "params":  [],
            "tasks":   body,
        }
        sub_spec, sub_issues = validate_flow_spec(sub_spec_data)
        for si in sub_issues:
            prefix = "[warn]" if si.startswith("[warn]") else ""
            issues.append(f"{prefix}Task {task.key!r} body: {si.lstrip('[warn]').strip()}")
        # Prohibit nested map nodes.
        if sub_spec:
            for bt in sub_spec.tasks:
                if bt.kind == "map":
                    issues.append(
                        f"Task {task.key!r} (map): body may not contain another map node "
                        f"(nested fan-out is not supported). Offending key: {bt.key!r}."
                    )
        # Validate collect_key if specified.
        collect_key = cfg.get("collect_key")
        if collect_key and sub_spec:
            body_keys = {bt.key for bt in sub_spec.tasks}
            if collect_key not in body_keys:
                issues.append(
                    f"Task {task.key!r} (map): 'collect_key' {collect_key!r} is not a key in body."
                )

elif task.kind == "branch":
    conditions = cfg.get("conditions")
    if not conditions or not isinstance(conditions, list):
        issues.append(
            f"Task {task.key!r} (branch): config must include 'conditions' (non-empty list)."
        )
    else:
        for i, cond in enumerate(conditions):
            if not isinstance(cond, dict):
                issues.append(
                    f"Task {task.key!r} (branch): condition[{i}] must be a dict with 'when' and 'next'."
                )
                continue
            if not cond.get("when"):
                issues.append(
                    f"Task {task.key!r} (branch): condition[{i}] missing 'when' expression."
                )
            if not cond.get("next") or not isinstance(cond["next"], list):
                issues.append(
                    f"Task {task.key!r} (branch): condition[{i}] missing 'next' list."
                )
    # Cross-reference check: all 'next' keys must be declared task keys.
    # (Deferred to a post-parse pass because declared_keys is in the outer scope.)
```

**Note on cross-reference validation:** The `next` key existence check requires access to `declared_keys` from the outer validation loop. This check must be added as a post-pass (step 5.5) after the loop over tasks, since branch nodes may reference tasks that appear later in the list.

---

## 3. Execution Semantics

### 3.1 map node runtime

**Handler location:** `backend/app/flows/handlers/map.py`

**Handler signature:** `handle_map(config: dict, ctx: TaskContext, claims: dict) -> dict`

**Handler responsibility (synchronous portion):**

The `map` handler does NOT fan out directly — it returns a sentinel that signals the runtime to fan out. This keeps the handler synchronous and lets the runtime manage task_run creation.

```python
# handlers/map.py

def handle_map(config: dict, ctx: TaskContext, claims: dict) -> dict:
    """Resolve the item expression and return the items list.

    The runtime (advance_readiness / _execute_claimed_task_run) detects
    the 'map' kind and expands child task_runs from this result.
    """
    from app.flows.executor import _resolve_any, _resolve_str  # import from executor

    item_expr: str = config.get("item_expr", "")
    resolved = _resolve_str(item_expr, ctx)      # resolves {{ ... }} to Python value
    if not isinstance(resolved, (list, tuple)):
        raise ValueError(
            f"map node 'item_expr' must resolve to a list; got {type(resolved).__name__}."
        )
    items = list(resolved)
    max_map_size: int = int(config.get("max_map_size", 1000) or 1000)
    if len(items) > max_map_size:
        raise ValueError(
            f"map node fan-out of {len(items)} exceeds max_map_size={max_map_size}."
        )
    return {"__map_items__": items, "item_count": len(items)}
```

**Runtime extension in `runtime.py` — `_execute_claimed_task_run`:**

After `execute_task` returns for a `map` node:

```python
# In _execute_claimed_task_run, after outcome = execute_task(full_task, ctx, claims):

if full_task.get("kind") == "map" and outcome["state"] == "success":
    items = outcome["result"].get("__map_items__", [])
    body_tasks = full_task.get("config", {}).get("body", [])
    item_var = full_task.get("config", {}).get("item_var", "item")
    max_concurrency = int(full_task.get("config", {}).get("max_concurrency", 0) or 0)

    # Create child task_runs for each item × each body task.
    child_runs = _expand_map_children(
        flow_run_id=flow_run_id,
        org_id=org_id,
        map_task_run_id=task_run_id,
        map_task_key=task_key,
        items=items,
        body_tasks=body_tasks,
        item_var=item_var,
        now=now,
    )
    await store.add_task_runs(flow_run_id, child_runs)

    # Transition map task_run to waiting_children (not yet terminal).
    await store.update_task_run(
        task_run_id,
        {"state": "waiting_children", "result": {"item_count": len(items)}},
    )
    await advance_readiness(store, flow_run_id, now)
    return await store.get_task_run(task_run_id)
```

**Child task_run key format:** `"{map_key}[{i}].{child_task_key}"`

Example: `"process_each_region[0].fetch_data"`, `"process_each_region[0].transform"`, `"process_each_region[1].fetch_data"`, …

**Child task_run `depends_on` construction:**

- Root body tasks: `depends_on = []` (immediately `ready`)
- Non-root body tasks: `depends_on = ["{map_key}[{i}].{upstream_key}" for upstream_key in body_task.needs]`

**Map fan-in in `advance_readiness`:**

```python
# In advance_readiness, after processing pending tasks:

# Detect map nodes in waiting_children state.
for tr in task_runs:
    if tr["state"] != "waiting_children":
        continue
    map_key = tr["task_key"]
    # Collect all child task_runs for this map node.
    children = [
        c for c in task_runs
        if c["task_key"].startswith(f"{map_key}[") and
           c.get("parent_task_run_id") == tr["id"]
    ]
    if not children:
        continue
    child_states = [c["state"] for c in children]
    if all(s in _TERMINAL_STATES for s in child_states):
        # All children terminal — collect results and finish the map node.
        has_child_failure = any(s in _BLOCKING_STATES for s in child_states)
        if has_child_failure:
            await store.update_task_run(tr["id"], {"state": "failed", "finished_at": now})
            state_by_key[map_key] = "failed"
        else:
            collect_key: str = tr.get("config", {}).get("collect_key") or ""
            collected = _collect_map_results(children, map_key, collect_key)
            await store.update_task_run(tr["id"], {
                "state": "success",
                "result": {"items": collected, "item_count": len(collected)},
                "finished_at": now,
            })
            state_by_key[map_key] = "success"
```

**New task_run state: `waiting_children`**

Add `"waiting_children"` to the task_run state set. It is NOT a terminal state and NOT a blocking state. It is not eligible for claiming (the map node itself does not re-execute). It transitions to `success` or `failed` when all children are terminal.

Updated constants in `runtime.py`:

```python
_TERMINAL_STATES = frozenset({
    "success", "failed", "timed_out", "upstream_failed",
    "skipped", "cancelled",
    # NOT waiting_children — it is intermediate
})
_BLOCKING_STATES = frozenset({
    "failed", "timed_out", "upstream_failed", "skipped", "cancelled",
})
```

**`claim_ready_task_run` guard:** The store must never claim a task_run with `kind == "map"` and `state == "waiting_children"`.

### 3.2 branch node runtime

**Handler location:** `backend/app/flows/handlers/branch.py`

**Handler signature:** `handle_branch(config: dict, ctx: TaskContext, claims: dict) -> dict`

```python
# handlers/branch.py

def handle_branch(config: dict, ctx: TaskContext, claims: dict) -> dict:
    """Evaluate conditions against ctx.inputs/params and return the branch taken.

    The runtime reads result['__branch_next__'] and activates those tasks.
    """
    import ast  # noqa: PLC0415
    from app.flows.executor import _resolve_str  # noqa: PLC0415

    conditions: list[dict] = config.get("conditions", [])
    default_next: list[str] = config.get("default", [])

    for i, cond in enumerate(conditions):
        when_expr: str = cond.get("when", "")
        # Resolve template substitutions first (e.g. {{ inputs.x.label }} → 'high')
        resolved_when = _resolve_str(when_expr, ctx)
        try:
            result = bool(ast.literal_eval(str(resolved_when)))
        except (ValueError, SyntaxError):
            # Expression is not a literal — evaluate it as Python
            result = bool(eval(resolved_when, {"__builtins__": {}}, {}))  # noqa: S307
        if result:
            return {
                "branch_taken":  f"condition_{i}",
                "branch_index":  i,
                "__branch_next__": cond.get("next", []),
            }

    # No condition matched — use default.
    if default_next:
        return {
            "branch_taken":  "default",
            "branch_index":  -1,
            "__branch_next__": default_next,
        }

    # No match, no default — fail the branch (prevents silent hangs).
    raise ValueError(
        "branch node: no condition matched and no default is configured. "
        "All downstream tasks will be marked upstream_failed."
    )
```

**Runtime extension in `advance_readiness`:**

After a branch task_run transitions to `success`, activate only the matching `next` tasks; mark all others `upstream_failed`:

```python
# In advance_readiness, after processing pending tasks:

for tr in task_runs:
    if tr.get("kind") != "branch" or tr["state"] != "success":
        continue
    branch_key = tr["task_key"]
    active_next: list[str] = (tr.get("result") or {}).get("__branch_next__") or []

    # Find all pending tasks that have this branch in their depends_on.
    for dep_tr in task_runs:
        if dep_tr["state"] != "pending":
            continue
        if branch_key not in (dep_tr.get("depends_on") or []):
            continue
        dep_key = dep_tr["task_key"]
        if dep_key in active_next:
            # Activate: set ready (if all other deps also succeeded).
            other_deps = [d for d in dep_tr["depends_on"] if d != branch_key]
            if all(state_by_key.get(d) == "success" for d in other_deps):
                await store.update_task_run(dep_tr["id"], {"state": "ready", "scheduled_at": now})
                state_by_key[dep_key] = "ready"
        else:
            # Inactive branch — mark upstream_failed immediately.
            await store.update_task_run(dep_tr["id"], {"state": "upstream_failed", "finished_at": now})
            state_by_key[dep_key] = "upstream_failed"
```

**Security note on `eval` in branch handler:** The `when` expression is authored by the flow author (an authenticated user) not by end-users. The expression is evaluated with `__builtins__` removed and is already behind the `{{ }}` template resolution which yields simple values (strings, numbers, bools). This is the same trust boundary as the existing `python` task handler which runs arbitrary subprocess code. A sandboxed expression evaluator (e.g. `simpleeval`) can replace `eval` as a hardening step but is not required for the initial implementation.

### 3.3 Store additions (store.py / Pg migration)

**New columns on `task_runs`:**

| Column | Type | Description |
|---|---|---|
| `parent_task_run_id` | `UUID / TEXT` (nullable) | For map child task_runs, points to the parent map task_run. NULL for all other tasks. |
| `branch_taken` | `TEXT` (nullable) | For branch task_runs, stores the branch label that was taken (e.g. `"condition_0"`, `"default"`). NULL for all other tasks. |

**InMemoryFlowStore changes:** Include `parent_task_run_id: None` and `branch_taken: None` in all task_run dicts created by `add_task_runs`. Map child task_runs set `parent_task_run_id` to the map node's task_run id.

**`list_task_runs` must return child task_runs:** Currently `list_task_runs(flow_run_id)` returns all task_runs for a flow_run. This is unchanged — child map task_runs share the same `flow_run_id` and are returned in the same list.

### 3.4 Registry additions (registry.py)

```python
# In _bootstrap(registry):

from app.flows.handlers.map    import handle_map     # noqa: PLC0415
from app.flows.handlers.branch import handle_branch  # noqa: PLC0415

registry.register("map",    handle_map)
registry.register("branch", handle_branch)
```

### 3.5 _get_task_spec for map children

Map child task_runs use composite keys like `"process_each_region[0].fetch_data"`. The existing `_get_task_spec` walks `flow.spec.tasks` matching by `task_key` exactly, and will not find children.

Extension to `_get_task_spec`:

```python
async def _get_task_spec(store: Any, task_run: dict[str, Any]) -> dict[str, Any]:
    task_key = task_run.get("task_key", "")

    # ... existing walk to get spec_data and tasks list ...

    # Direct match (existing behaviour).
    for task in tasks:
        if task.get("key") == task_key:
            return task

    # Map child match: key is "{map_key}[{i}].{child_key}"
    import re  # noqa: PLC0415
    m = re.match(r'^(.+)\[(\d+)\]\.(.+)$', task_key)
    if m:
        map_key, _idx, child_key = m.group(1), m.group(2), m.group(3)
        for task in tasks:
            if task.get("key") == map_key and task.get("kind") == "map":
                body = task.get("config", {}).get("body", [])
                for bt in body:
                    if bt.get("key") == child_key:
                        return dict(bt)

    return {}
```

**Item injection for map child python tasks:** The `_handle_python` subprocess wrapper must receive an additional `item` local variable. The item is stored on the child task_run at creation time as `config["__item__"]`. The python handler checks for this key and injects it:

```python
# In _handle_python (registry.py):
item_json = json.dumps(config.get("__item__", {}))
# Add to wrapper: item = _json.loads(...)
```

Child task_run creation sets `config["__item__"] = items[i]` for each item.

---

## 4. Round-Trip and Codegen Plan

### 4.1 Authoring paths (all converge on FlowSpec)

```
Python SDK (@flow)  ──compile()──►  FlowSpec JSON  ◄──────  Canvas editor
                                         │                (graphToSpec)
                                         ▼
                                  Execution engine
                                  (materialize_flow_run)
                                         │
                                         ▼
                               task_runs in Postgres
```

**The FlowSpec is the canonical artifact.** The Python SDK is one authoring path; the canvas is another. Neither is canonical. Changes made in the canvas update the FlowSpec; changes made in the SDK produce a new FlowSpec via `compile()`. Round-tripping between authoring paths is lossy only for:

- Canvas layout (`ui.x`/`ui.y`) — not in SDK scaffold output, but preserved in FlowSpec.
- Code comments and variable names — not in FlowSpec, lost in scaffold.
- Python task `code` — stored verbatim in FlowSpec and preserved.

### 4.2 specGraph.js round-trip for map and branch

**`specToGraph` extensions:**

```javascript
// For kind === 'map':
// 1. Render as a single collapsed node (type: 'mapNode').
// 2. node.data.expanded = false by default.
// 3. node.data.bodySpec = task.config.body  (kept for drill-in).
// Edges: drawn from needs (same as any other task node).

// For kind === 'branch':
// 1. Render as a diamond node (type: 'branchNode').
// 2. Edges from branch node to each 'next' key carry a label.
// Labels are derived from conditions[i].when (truncated) or 'default'.
// The 'needs' edges remain standard (source → branch).
```

**`graphToSpec` extensions:**

For `map` nodes, the full `config.body` sub-spec is preserved on `node.data.task.config.body`. The `graphToSpec` function writes it back verbatim:

```javascript
config: base.config ?? {},   // already contains config.body for map nodes
```

No special handling is needed because `config` is passed through unmodified.

For `branch` nodes, the `conditions[].next` lists are **not** derived from React Flow edges (they live in `config.conditions`). Canvas edges from a branch node to its downstream tasks are visual only; the authoritative routing is `config.conditions[i].next`. This means:

- Canvas drag-connect from branch → downstream task updates `config.conditions` via the inspector, not via `graphToSpec`.
- `graphToSpec` reads `base.config.conditions` verbatim for branch nodes.

**Canvas layout for map body tasks:**

Map body task `ui` coordinates are relative to the map node's origin. Stored as `task.config.body[i].ui.{x,y}` (relative coords). When expanded in the canvas, these are translated by the parent map node's `position`.

### 4.3 Codegen (FlowSpec → Python scaffold)

**Guarantee:** `flow_spec_to_sdk(spec).compile() == original_spec` (modulo `ui` coords, which are set to `{x:0, y:0}` in compiled output).

**Algorithm:**

1. Emit imports header.
2. For each non-map, non-branch task: emit `@task(kind=..., **config)` decorator + `def {key}(): pass`.
3. Reconstruct topological sort order from `needs` graph.
4. Emit `@flow\ndef {name}():` function.
5. Inside the flow function:
   - For each task in topo order:
     - If `kind == "map"`: emit `@map_node(...)` decorated inner function.
     - If `kind == "branch"`: emit `branch_node(...)` call.
     - Otherwise: emit `{key}_handle = {key}({upstream_handle_args})`.
6. Emit `spec = {name}.compile({params_kwargs})`.

**Example output:**

```python
# Auto-generated scaffold from FlowSpec "daily_revenue_v2"
# Edit task configs; do not restructure the graph here — use the canvas or recompile.

from nubi.sdk import flow, task, map_node, branch_node

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
        @task(kind="query", sql="SELECT * FROM sales WHERE region = '{{ item.region_code }}'")
        def fetch_data(): pass

        @task(kind="python", code="result = {k: v*2 for k, v in inputs['fetch_data']['rows'][0].items()}")
        def transform(): pass

        fetch_handle = fetch_data()
        return transform(fetch_handle)

    aggregate(process_each_region.collect())

spec = daily_revenue_v2.compile()
```

### 4.4 Round-trip fidelity matrix

| Change | FlowSpec updated? | SDK scaffold updated? | Canvas updated? |
|---|---|---|---|
| Edit `config.sql` in canvas inspector | Yes | Scaffold regenerates | Yes |
| Add new task node on canvas | Yes | Scaffold regenerates | Yes |
| Delete edge on canvas | Yes (needs list changes) | Scaffold regenerates | Yes |
| Rewrite `@flow` Python + recompile | Yes | (source is upstream) | Yes |
| Edit `ui.x`/`ui.y` by dragging | Yes (ui field only) | No (ui not in scaffold) | Yes |
| Edit map `body` via inspector JSON | Yes | Scaffold regenerates | No topology change |
| Edit `config.code` in python task | Yes | Yes (code block) | No topology change |

---

## 5. Open Questions (requiring human decision before implementation)

These are genuine branching points where either answer is defensible; the implementation path differs based on the answer.

**OQ-1: `when` expression evaluation security model**

The `branch` handler evaluates `when` after template resolution. The current proposal uses `eval()` with `__builtins__` stripped. Alternative: use the `simpleeval` library (safe expression evaluator). `simpleeval` adds a new dependency but eliminates any concern about `eval` escape even with builtins stripped.

Decision needed: `eval` with stripped builtins (zero new deps, same trust level as `python` task) vs `simpleeval` (adds dep, belt-and-suspenders).

**OQ-2: `waiting_children` — public state or internal-only**

The `waiting_children` state is emitted onto task_run rows in the store and is visible via the API (`GET /runs/{id}`). This means the frontend (FlowRunView, TaskNode) must handle and display it. Alternative: make `waiting_children` an internal runtime concept and expose it as `running` to the API.

Decision needed: expose `waiting_children` as a distinct state in the API (requires frontend update) vs collapse it to `running` (no frontend change but misleading for long map runs).

**OQ-3: Branch node `when` expression format**

Currently proposed: `when` is a full Python boolean expression after template resolution (e.g. `"{{ inputs.classify.label }} == 'high_value'"`). Alternative: split into `left_expr`, `op`, `right_expr` (structured comparison) to avoid any eval.

Decision needed: free-form Python bool after template resolution vs structured comparison triplet.

**OQ-4: Canvas branch wiring — inspector-only vs drag-connect**

The proposal says branch `next` lists are edited only via the NodeInspector (since they live in `config.conditions`), not via canvas drag-connect. This means dragging an edge from a branch node to a downstream task in the canvas does NOT automatically update `conditions[i].next`.

Decision needed: accept inspector-only wiring for branch routing (simpler frontend) vs implement special drag-connect behaviour that prompts "add to which condition?" (richer UX, more frontend work).

**OQ-5: Maximum nesting depth for subflows inside map bodies**

The `body` sub-DAG for a `map` node may contain any non-map task kinds. The proposal prohibits nested `map` nodes in bodies. Should `branch` nodes be allowed inside map bodies? And should a body task be allowed to have `kind: "noop"` pointing to an external (parent flow) task key?

Decision needed: map bodies support `branch` kind inside (more powerful, needs recursive advance_readiness) vs map bodies are restricted to leaf kinds only (query, python, agent, noop, extract, bucket_load).

---

## 6. Implementation Task Graph

The following agents are scoped to disjoint file sets and are sequenced by dependency. All agents must wait for the concurrent engine build (spec.py / runtime.py / store.py plumbing) to land before touching those files.

**Notation:** `engine-files` = files currently being modified by the concurrent engine workflow. An agent listed as "engine-safe" does not touch engine files.

---

### Agent A — `spec-map-branch`

**Owns:** `backend/app/flows/spec.py`

**Touches engine files:** `backend/app/flows/spec.py`

**Parallel safe:** No — must wait for concurrent engine `spec.py` changes to land first, then applies map/branch additions on top.

**Depends on:** Concurrent engine build landing.

**Work:**
- Add `"map"` and `"branch"` to `TaskSpec.kind` Literal.
- Add `validate_flow_spec` step 5 extensions for `map` and `branch` (as specified in §2.4).
- Add post-pass (step 5.5) for branch `next` key cross-reference validation.
- Update `flow_spec_json_schema()` docstring to mention new kinds.
- Add tests for the new validation paths.

---

### Agent B — `handlers-map-branch`

**Owns:**
- `backend/app/flows/handlers/map.py` (new file)
- `backend/app/flows/handlers/branch.py` (new file)

**Touches engine files:** None (new files only).

**Parallel safe:** Yes — can run alongside Agent A.

**Depends on:** Nothing (new files; only imports from `executor.py` which is read-only here).

**Work:**
- Implement `handle_map` per §3.1 handler spec.
- Implement `handle_branch` per §3.2 handler spec.
- Unit tests for both handlers (use mock `TaskContext`).

---

### Agent C — `registry-bootstrap`

**Owns:** `backend/app/flows/registry.py`

**Touches engine files:** `backend/app/flows/registry.py`

**Parallel safe:** No — must wait for concurrent engine `registry.py` changes and Agent B.

**Depends on:** Concurrent engine build landing, Agent B.

**Work:**
- Register `"map"` and `"branch"` handlers in `_bootstrap`.
- Add `"map"` and `"branch"` to kind docstring.

---

### Agent D — `runtime-map-branch`

**Owns:** `backend/app/flows/runtime.py`

**Touches engine files:** `backend/app/flows/runtime.py`

**Parallel safe:** No — must wait for concurrent engine `runtime.py` changes and Agent C.

**Depends on:** Concurrent engine build landing, Agent A, Agent C.

**Work:**
- Add `"waiting_children"` to state constants (NOT in `_TERMINAL_STATES`, NOT in `_BLOCKING_STATES`).
- Extend `_execute_claimed_task_run` to detect `kind == "map"` post-success and fan out child task_runs (§3.1).
- Implement `_expand_map_children` helper.
- Implement `_collect_map_results` helper.
- Extend `advance_readiness` to detect `waiting_children` map nodes and collect fan-in (§3.1).
- Extend `advance_readiness` to activate/deactivate branch downstream tasks (§3.2).
- Extend `_get_task_spec` to resolve map child composite keys (§3.5).
- Integration tests: map with 3 items × 2 body tasks; branch with both taken and default paths.

---

### Agent E — `store-columns`

**Owns:** `backend/app/flows/store.py`

**Touches engine files:** `backend/app/flows/store.py`

**Parallel safe:** No — must wait for concurrent engine `store.py` changes.

**Depends on:** Concurrent engine build landing.

**Work:**
- Add `parent_task_run_id: None` and `branch_taken: None` to all task_run dicts in `InMemoryFlowStore`.
- Add `parent_task_run_id` and `branch_taken` columns to `PgFlowStore` SQL (new migration `0017_map_branch_columns`).
- Ensure `list_task_runs` returns child task_runs (they share `flow_run_id` — this is already correct).
- Update `claim_ready_task_run` guard: skip `state == "waiting_children"` tasks.

---

### Agent F — `sdk-dsl`

**Owns:**
- `backend/app/flows/sdk.py` (new file)
- `backend/app/flows/codegen.py` (new file)

**Touches engine files:** None (new files).

**Parallel safe:** Yes — fully independent. Can run alongside A, B, E.

**Depends on:** Nothing at engine level. Logically depends on Agent A (spec shapes) being finalized.

**Work:**
- Implement `NodeHandle`, `_TraceContext`, `task`, `flow`, `map_node`, `MapBodyHandle`, `branch_node` per §1.3.
- Implement `flow_spec_to_sdk` + helpers per §1.4 codegen signatures.
- Unit tests: trace a 3-task linear flow; trace a flow with a map node; trace a flow with a branch node; verify `compile()` output matches expected FlowSpec dict.
- Codegen round-trip test: `flow_spec_to_sdk(spec)` then `compile()` the result and verify spec equality.

---

### Agent G — `frontend-map-branch`

**Owns:**
- `src/flows/specGraph.js`
- `src/flows/nodes/TaskNode.jsx`
- `src/flows/NodeInspector.jsx`
- `src/flows/FlowBuilder.jsx`

**Touches engine files:** `src/flows/specGraph.js` (engine-adjacent, but frontend-only).

**Parallel safe:** Yes — frontend files are not touched by the concurrent engine build.

**Depends on:** Agent A (spec shapes finalized, especially the config contracts for map/branch).

**Work:**
- Register `"mapNode"` and `"branchNode"` in `NODE_TYPES`.
- `specToGraph`: emit nodes with `type: 'mapNode'` for map tasks, `type: 'branchNode'` for branch tasks.
- `specToGraph`: emit labeled edges from branch nodes to their `conditions[i].next` targets.
- `graphToSpec`: pass `config.body` and `config.conditions` through verbatim.
- Add `MapNode.jsx` (collapsed/expanded toggle, shows item count badge).
- Add `BranchNode.jsx` (diamond shape, labeled outgoing edges).
- Extend `NodeInspector.jsx`: `MapConfig` panel (item_expr, item_var, max_concurrency, body JSON editor); `BranchConfig` panel (conditions list editor).
- Add `"map"` and `"branch"` to `PALETTE_ITEMS` in `FlowBuilder.jsx`.
- Add map and branch states (`waiting_children`) to `TaskNode.jsx` `STATE_DOT` / `STATE_LABEL`.

---

### Dependency graph (ASCII)

```
concurrent-engine-build (external)
    │
    ├──► Agent A (spec.py)
    │        │
    │        ├──► Agent C (registry.py) ◄── Agent B (handlers — parallel)
    │        │        │
    │        │        └──► Agent D (runtime.py)
    │        │
    │        └──► Agent G (frontend — parallel with C, D, E)
    │
    ├──► Agent B (handlers/map.py, handlers/branch.py)  [parallel with A]
    │
    ├──► Agent E (store.py)  [parallel with A, B]
    │
    └──► Agent F (sdk.py, codegen.py)  [parallel with A, B, E]
```

**Critical path:** concurrent-engine-build → A → C → D

**Parallelism available:** B, E, F run concurrently with A. G runs concurrently with C, D, E.

---

## Appendix: Migration SQL

```sql
-- 0017_map_branch_columns.sql

ALTER TABLE task_runs
  ADD COLUMN IF NOT EXISTS parent_task_run_id UUID REFERENCES task_runs(id) ON DELETE CASCADE,
  ADD COLUMN IF NOT EXISTS branch_taken TEXT;

CREATE INDEX IF NOT EXISTS task_runs_parent_idx
  ON task_runs (parent_task_run_id)
  WHERE parent_task_run_id IS NOT NULL;
```
