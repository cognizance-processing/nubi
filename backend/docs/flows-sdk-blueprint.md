# Nubi Flows SDK — Consolidated Blueprint

**Status:** Sign-off ready  
**Date:** 2026-06-08  
**Scope:** SDK DSL, FlowSpec IR extensions (map + branch), execution semantics, round-trip + codegen, implementation task graph.

---

## 0. Conflict Resolution Log

Before the design, a summary of conflicts between the five research artifacts and the decisions made:

| Conflict | Winning approach | Rationale |
|---|---|---|
| Branch `if_true`/`if_false` list vs. `then`/`else_` string | **`then`/`else_` as string keys** (single downstream per arm) | Lists are over-generalised for v1; the canvas renders two labeled edges, not N. Multiple downstream tasks after a branch should use a `noop` fan-out. Simpler spec, simpler canvas. |
| Branch `condition` as eval()-Python vs. `{{ }}` template-resolved string | **`{{ }}` template string with explicit falsy set** | Consistent with every other config string in executor.py; no new `eval()` surface; `_resolve_string` already exists. The handler calls `_resolve_string` then checks falsy-set. |
| `skipped` in `_BLOCKING_STATES` | **Remove `skipped` from `_BLOCKING_STATES`** | Current code note says "kept for compat with older flow_runs" — that is the only reason. New runs should not have skipped propagate as blocking. Downstream tasks with a mix of success + skipped deps should become ready, not upstream_failed. Old runs are unaffected (the state data in DB is already terminal). |
| Map fan-in via `{map_key}__collect` task_run vs. aggregate on the map coordinator itself | **Collector task_run (`kind='noop'`, auto-inserted)** | Makes the fan-in a visible node in advance_readiness without engine special-casing. The coordinator sets `awaiting_children` state; the collector transitions `pending → ready` only after all child iterations are terminal. |
| Map state: new `awaiting_children` state vs. marking coordinator `success` immediately | **`awaiting_children` is NOT a new state.** The coordinator is marked **`success`** immediately after `expand_map_node` returns. Its `result` carries `{"item_count": N, "status": "expanded"}`. The collector's `depends_on` includes all last-body-task keys of every iteration, so the fan-in is driven entirely by existing `advance_readiness` logic. | Avoids a new state in the state machine, avoiding changes to `_TERMINAL_STATES`, finalization logic, and all store queries. The coordinator finishes; its children handle themselves. |
| Codegen location: SDK package vs. `app/flows/codegen.py` | **`backend/app/flows/codegen.py`** | The codegen input is a `FlowSpec` (backend type). No SDK import needed. Can be called from the REST API export endpoint without circular imports. |
| SDK `after=` keyword vs. positional arg dependencies | **`after=` keyword** for explicit deps; implicit deps from `source=`/`sources=` still create edges | Matches codebase style (kwargs everywhere). Explicit is better than positional for complex dependency graphs. |
| `_meta.source` in spec dict vs. DB column | **DB column on `flows` table** (`source TEXT NOT NULL DEFAULT 'canvas'`) | The spec's Pydantic model uses `extra='ignore'` mode effectively (unknown keys are dropped on load). Keeping ownership metadata out of the spec avoids it leaking into `validate_flow_spec`, hash checks, or LLM prompts. |

---

## 1. Final SDK API Surface

### 1.1 Package layout

```
backend/nubi/flows/
  __init__.py        # re-exports everything below
  _builder.py        # FlowBuilder (contextvars), NodeHandle
  _nodes.py          # @flow decorator + all node constructors
  _combinators.py    # map(), branch(), collect()
  _compile.py        # compile(builder) -> FlowSpec dict
  _run.py            # run_local() -> dict  (dev/test shim)
  _keygen.py         # make_key(base, builder) -> str
```

### 1.2 `FlowBuilder` and `NodeHandle` (`_builder.py`)

```python
import contextvars
from dataclasses import dataclass, field
from typing import Any

_current_builder: contextvars.ContextVar["FlowBuilder | None"] = \
    contextvars.ContextVar("_current_builder", default=None)


@dataclass
class FlowBuilder:
    name: str
    params: list[Any] = field(default_factory=list)   # list[FlowParam]
    _tasks: list[Any] = field(default_factory=list)   # list[TaskSpec]
    _key_counters: dict[str, int] = field(default_factory=dict)
    _in_map_body: bool = False                         # nested-map guard

    def _add_task(self, spec: Any) -> None: ...
    def _next_suffix(self, base: str) -> str: ...


class NodeHandle:
    """Symbolic reference to a recorded task node. Not executable."""

    def __init__(self, key: str, kind: str) -> None: ...

    @property
    def key(self) -> str: ...

    @property
    def kind(self) -> str: ...

    def __getattr__(self, name: str) -> Any:
        # Raises TypeError with helpful message on accidental .rows/.result access.
        ...

    # Sugar: handle.map(...) delegates to _combinators.map()
    def map(
        self,
        body_fn: "Callable[[_ItemHandle], NodeHandle | None]",
        *,
        over: str = "rows",
        key: str | None = None,
        concurrency: int = 0,
        retries: int = 0,
        retry_backoff_s: int = 30,
        timeout_s: int = 300,
        ui: "TaskUi | None" = None,
    ) -> "NodeHandle": ...
```

### 1.3 `@flow` decorator (`_nodes.py`)

```python
def flow(
    name: str | None = None,
    *,
    params: list["FlowParam"] | None = None,
) -> Callable[[F], F]:
    """
    Traces the decorated function ONCE at decoration time.
    Attaches .spec (FlowSpec dict) and .compile() to the function.
    Calling the decorated function outside a tracing context calls run_local().
    The function body must have no required positional args.
    """
```

**Tracing mechanism:** The decorator calls `fn()` with zero args inside a `_current_builder` context-var token. Task constructors called inside the body call `_record_task()`, which reads `_current_builder.get()` and appends to it. Returns a `NodeHandle`. This is the Prefect 1.x / Dagster `@graph` pattern adapted to context-vars (thread-safe for concurrent compile calls).

### 1.4 Node constructors

All constructors share this internal helper:

```python
def _record_task(
    kind: str,
    config: dict[str, Any],
    needs: list[NodeHandle],
    key: str,
    *,
    retries: int = 0,
    retry_backoff_s: int = 30,
    timeout_s: int = 60,
    cache_ttl_s: int = 0,
    ui: "TaskUi | None" = None,
) -> NodeHandle:
    """Append a TaskSpec to the current FlowBuilder and return a NodeHandle."""
```

#### Public constructors

```python
def query(
    *,
    query_id: str | None = None,
    sql: str | None = None,
    named_params: dict[str, Any] | None = None,
    after: "NodeHandle | list[NodeHandle] | None" = None,
    key: str | None = None,
    retries: int = 0,
    retry_backoff_s: int = 30,
    timeout_s: int = 60,
    cache_ttl_s: int = 0,
    ui: "TaskUi | None" = None,
) -> NodeHandle:
    """Exactly one of query_id or sql required."""

def python(
    name: str,
    code: str,
    *,
    after: "NodeHandle | list[NodeHandle] | None" = None,
    key: str | None = None,
    retries: int = 0,
    retry_backoff_s: int = 30,
    timeout_s: int = 60,
    cache_ttl_s: int = 0,
    ui: "TaskUi | None" = None,
) -> NodeHandle:
    """code runs in subprocess; inputs, params, item (map body only) injected."""

def agent(
    name: str,
    prompt: str,
    *,
    max_steps: int = 4,
    after: "NodeHandle | list[NodeHandle] | None" = None,
    key: str | None = None,
    retries: int = 0,
    retry_backoff_s: int = 30,
    timeout_s: int = 120,
    cache_ttl_s: int = 0,
    ui: "TaskUi | None" = None,
) -> NodeHandle: ...

def extract(
    name: str,
    dest_uri: str,
    *,
    source_uri: str | None = None,
    source: "NodeHandle | None" = None,
    secret: str | None = None,
    fmt: str = "auto",
    after: "NodeHandle | list[NodeHandle] | None" = None,
    key: str | None = None,
    retries: int = 0,
    retry_backoff_s: int = 30,
    timeout_s: int = 300,
    cache_ttl_s: int = 0,
    ui: "TaskUi | None" = None,
) -> NodeHandle:
    """Exactly one of source_uri or source required. source NodeHandle creates
    an implicit needs edge AND stores source.key in config."""

def bucket_load(
    name: str,
    uri: str,
    source: "NodeHandle",
    *,
    fmt: str = "csv",
    mode: str = "overwrite",
    secret: str | None = None,
    after: "NodeHandle | list[NodeHandle] | None" = None,
    key: str | None = None,
    retries: int = 0,
    retry_backoff_s: int = 30,
    timeout_s: int = 300,
    cache_ttl_s: int = 0,
    ui: "TaskUi | None" = None,
) -> NodeHandle:
    """source is required; creates implicit needs edge."""

def materialize(
    name: str,
    combine_sql: str,
    sources: "list[NodeHandle]",
    *,
    rls_keys: "list[str] | None" = None,
    table: str = "blend",
    database: str,
    datastore_id: str,
    query_id: str,
    key: str | None = None,
    retries: int = 0,
    retry_backoff_s: int = 30,
    timeout_s: int = 600,
    cache_ttl_s: int = 0,
    ui: "TaskUi | None" = None,
) -> NodeHandle:
    """All source NodeHandles create implicit needs edges."""

def noop(
    name: str = "join",
    *,
    after: "NodeHandle | list[NodeHandle] | None" = None,
    key: str | None = None,
    ui: "TaskUi | None" = None,
) -> NodeHandle: ...
```

### 1.5 Combinators (`_combinators.py`)

```python
class _ItemHandle(NodeHandle):
    """Symbolic handle for the current map iteration item.
    key='__item__', kind='item'. Used only inside map body tracing."""
    def __init__(self) -> None: ...


def map(
    upstream: NodeHandle,
    body_fn: "Callable[[_ItemHandle], NodeHandle | None]",
    *,
    over: str = "rows",
    key: str | None = None,
    concurrency: int = 0,
    retries: int = 0,
    retry_backoff_s: int = 30,
    timeout_s: int = 300,
    ui: "TaskUi | None" = None,
) -> NodeHandle:
    """Fan-out. body_fn traced ONCE in a fresh child FlowBuilder.
    Nested map inside map raises ValueError immediately.
    Emits kind='map' TaskSpec with config.body = [child TaskSpec dicts].
    Returns a NodeHandle pointing to the map node key."""


def branch(
    condition_handle: NodeHandle,
    *,
    then: NodeHandle,
    else_: NodeHandle,
    key: str | None = None,
    ui: "TaskUi | None" = None,
) -> NodeHandle:
    """Conditional gate. Emits kind='branch' TaskSpec.
    Retroactively patches then.needs and else_.needs to include the branch key.
    Both then and else_ must already be recorded in the current builder."""


def collect(
    map_handle: NodeHandle,
    *,
    key: str | None = None,
    ui: "TaskUi | None" = None,
) -> NodeHandle:
    """Fan-in barrier. Emits kind='noop' TaskSpec with needs=[map_handle.key].
    Downstream tasks should needs this collect node, not the map node directly."""
```

### 1.6 `compile()` and `run_local()`

```python
def compile(builder: FlowBuilder) -> dict[str, Any]:
    """Convert a FlowBuilder to a validated FlowSpec dict.
    Calls validate_flow_spec and raises ValueError on hard errors.
    This is called automatically by @flow on decoration."""


def run_local(
    flow_fn: Any,
    params: dict[str, Any] | None = None,
    *,
    claims: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Execute a @flow function locally using InMemoryFlowStore.
    Uses asyncio.run(); fails if called from within a running event loop.
    Returns the final flow_run dict with task_runs attached."""
```

### 1.7 Key generation (`_keygen.py`)

```python
def make_key(base: str, builder: FlowBuilder) -> str:
    """Slugify base (lowercase, spaces/hyphens → underscores, strip non-alnum_).
    If slug already used in this builder, append _1, _2, ... until unique.
    Position-counter based (not content-hash) for diff stability."""
```

Default base slugs per constructor:

| Constructor | Base slug |
|---|---|
| `query(query_id=X)` | `query_{X}` |
| `query(sql=...)` | `sql_query` |
| `python(name=N)` | `N` |
| `agent(name=N)` | `N` |
| `extract(name=N)` | `N` |
| `bucket_load(name=N)` | `N` |
| `materialize(name=N)` | `N` |
| `noop(name=N)` | `N` |
| `map(key=K)` | `K` or `{upstream.key}_map` |
| `branch(key=K)` | `K` or `{condition.key}_branch` |
| `collect(key=K)` | `K` or `{map.key}_collect` |

### 1.8 `__init__.py` exports

```python
from nubi.flows._builder import FlowBuilder, NodeHandle
from nubi.flows._nodes import (
    flow, query, python, agent,
    extract, bucket_load, materialize, noop,
)
from nubi.flows._combinators import map, branch, collect
from nubi.flows._compile import compile
from nubi.flows._run import run_local
from app.flows.spec import FlowParam, TaskUi  # re-exported, not redefined

__all__ = [
    "flow", "FlowBuilder", "NodeHandle", "FlowParam", "TaskUi",
    "query", "python", "agent", "extract", "bucket_load", "materialize", "noop",
    "map", "branch", "collect",
    "compile", "run_local",
]
```

---

## 2. Final FlowSpec Schema Diff

### 2.1 `spec.py` — `TaskSpec.kind` extension

```python
# BEFORE:
kind: Literal["query", "python", "agent", "materialize", "noop",
              "extract", "bucket_load", "preagg_refresh"]

# AFTER:
kind: Literal["query", "python", "agent", "materialize", "noop",
              "extract", "bucket_load", "preagg_refresh",
              "map",     # fan-out; body is an inline sub-DAG
              "branch",  # conditional gate; activates then or else_ arm
              ]
```

No other model changes. All new data lives inside the existing `config: dict[str, Any]` field.

### 2.2 Map task config schema

```json
{
  "key": "process_regions",
  "kind": "map",
  "needs": ["fetch_regions"],
  "config": {
    "over": "fetch_regions.rows",
    "body": [
      {
        "key": "enrich",
        "kind": "query",
        "needs": [],
        "config": { "sql": "SELECT * FROM t WHERE region='{{ item.code }}'" },
        "retries": 0, "retry_backoff_s": 30, "timeout_s": 60, "cache_ttl_s": 0,
        "ui": { "x": 0, "y": 0 }
      },
      {
        "key": "load",
        "kind": "bucket_load",
        "needs": ["enrich"],
        "config": { "uri": "s3://out/{{ item.code }}.csv", "source": "enrich" },
        "retries": 0, "retry_backoff_s": 30, "timeout_s": 300, "cache_ttl_s": 0,
        "ui": { "x": 260, "y": 0 }
      }
    ],
    "concurrency": 4
  },
  "retries": 0, "retry_backoff_s": 30, "timeout_s": 300, "cache_ttl_s": 0,
  "ui": { "x": 400, "y": 200 }
}
```

**Field contracts:**

| Field | Type | Required | Notes |
|---|---|---|---|
| `over` | `str` | Yes | Dot-path into an upstream result. Resolved at runtime to a list. E.g. `"fetch.rows"` → `ctx.inputs["fetch"]["rows"]`. |
| `body` | `list[TaskSpec dict]` | Yes | Non-empty. Keys scoped to body; body task `needs` may only reference other body keys. Nested `map` prohibited. |
| `concurrency` | `int` | No (default 0) | Max parallel iterations. 0 = unbounded. Implemented via iteration-level `depends_on` chaining (iteration `i >= concurrency` waits for iteration `i - concurrency`'s last body task). |

**Template variable `{{ item.* }}`:** Available in all body task config strings. Resolved by the executor via the `item` namespace in `_resolve_value`. Body `python` tasks also receive `item` as a subprocess local.

### 2.3 Branch task config schema

```json
{
  "key": "route",
  "kind": "branch",
  "needs": ["check_status"],
  "config": {
    "condition": "{{ inputs.check_status.rows.0.ok }}",
    "then": "send_report",
    "else_": "notify_failure"
  },
  "retries": 0, "retry_backoff_s": 30, "timeout_s": 60, "cache_ttl_s": 0,
  "ui": { "x": 600, "y": 200 }
}
```

**Field contracts:**

| Field | Type | Required | Notes |
|---|---|---|---|
| `condition` | `str` | Yes | `{{ }}` template expression. Resolved by `_resolve_string`. Truthy = non-empty string not in `{"", "false", "0", "null", "none", "no", "off"}` (case-insensitive). |
| `then` | `str` | Yes | Task key of the node to activate when condition is truthy. Must be a declared top-level task key. |
| `else_` | `str` | Yes | Task key of the node to activate when condition is falsy. Must differ from `then`. JSON key is `"else_"` (underscore escapes Python keyword). |

Both `then` and `else_` targets must have the branch node's key in their `needs` list. `branch()` in the SDK retroactively patches this; the canvas validator in `validate_flow_spec` enforces it.

### 2.4 `validate_flow_spec` additions (step 5)

```python
elif task.kind == "map":
    if not cfg.get("over"):
        issues.append(f"Task {task.key!r} (map): config must include 'over'.")
    body = cfg.get("body")
    if not body or not isinstance(body, list) or len(body) == 0:
        issues.append(f"Task {task.key!r} (map): config.body must be a non-empty list.")
    else:
        body_keys: set[str] = set()
        for i, bt in enumerate(body):
            if not isinstance(bt, dict):
                issues.append(f"Task {task.key!r} (map): body[{i}] must be a dict.")
                continue
            bt_key = bt.get("key", "")
            if not bt_key:
                issues.append(f"Task {task.key!r} (map): body[{i}] missing 'key'.")
            elif bt_key in body_keys:
                issues.append(f"Task {task.key!r} (map): duplicate body task key {bt_key!r}.")
            body_keys.add(bt_key)
            if bt.get("kind") == "map":
                issues.append(
                    f"Task {task.key!r} (map): nested 'map' inside body ({bt_key!r}) is not allowed."
                )
        # Body needs must reference only other body keys
        for bt in body:
            for dep in (bt.get("needs") or []):
                if dep not in body_keys:
                    issues.append(
                        f"Task {task.key!r} (map): body task {bt.get('key')!r} "
                        f"needs {dep!r}, which is not a body task key."
                    )
        # Body sub-DAG must be acyclic (reuse _find_cycle logic)
        _validate_body_acyclic(body, task.key, issues)

elif task.kind == "branch":
    if not cfg.get("condition"):
        issues.append(f"Task {task.key!r} (branch): config must include 'condition'.")
    if not cfg.get("then"):
        issues.append(f"Task {task.key!r} (branch): config must include 'then'.")
    if not cfg.get("else_"):
        issues.append(f"Task {task.key!r} (branch): config must include 'else_'.")
    if cfg.get("then") and cfg.get("then") == cfg.get("else_"):
        issues.append(f"Task {task.key!r} (branch): 'then' and 'else_' must be different keys.")
    # Cross-reference: then/else_ must be declared task keys
    for arm_name in ("then", "else_"):
        arm_key = cfg.get(arm_name)
        if arm_key and arm_key not in declared_keys:
            issues.append(
                f"Task {task.key!r} (branch): '{arm_name}' target {arm_key!r} "
                f"is not a declared task key."
            )
    # Both then and else_ must have this branch node in their needs
    for arm_name in ("then", "else_"):
        arm_key = cfg.get(arm_name)
        if arm_key:
            arm_task = next((t for t in spec.tasks if t.key == arm_key), None)
            if arm_task and task.key not in arm_task.needs:
                issues.append(
                    f"Task {arm_key!r} must include {task.key!r} in its needs "
                    f"(it is a branch target of {task.key!r})."
                )
```

### 2.5 Backward compatibility

All existing specs pass unchanged. The two new `kind` values are additive to the `Literal`. Steps 1–6 of `validate_flow_spec` are unmodified for all existing kinds. No existing `task_runs` rows are affected.

---

## 3. Final Execution Semantics

### 3.1 Map execution

**Phase 1 — Static materialization** (`materialize_flow_run`):

For `map` tasks, insert only the coordinator `task_run` (same as any other task). Also insert a placeholder collector:

```python
# Coordinator (exactly as today for non-root tasks):
{
  "task_key": "process_regions",
  "state": "pending" if task.needs else "ready",
  "depends_on": list(task.needs),
  "kind": "map",
  "config": dict(task.config),
  ...
}

# Placeholder collector (inserted at materialize time, not after expansion):
{
  "task_key": "process_regions__collect",
  "state": "pending",
  "depends_on": ["process_regions"],   # depends on coordinator
  "kind": "noop",
  "config": {"__map_collect__": True, "map_parent_key": "process_regions"},
  "map_parent_key": "process_regions",
  "map_index": None,
  ...
}
```

The placeholder collector ensures that if the coordinator fails, `advance_readiness` can correctly propagate `upstream_failed` to downstream tasks (rather than leaving them `pending` forever with a missing dep).

**Phase 2 — Dynamic fan-out** (`expand_map_node`, called from `_execute_claimed_task_run`):

The `_handle_map` handler:
1. Resolves `config["over"]` via dot-path against `ctx.inputs`.
2. Validates the resolved value is a list; raises `AppError` otherwise.
3. Returns `{"__map_item_list__": item_list, "item_count": len(item_list)}`.

After the coordinator's handler returns success, `_execute_claimed_task_run` detects `kind == "map"`, calls `expand_map_node`, then marks the coordinator `success`.

`expand_map_node` (new function in `runtime.py`):
- Checks idempotency: if child task_runs with keys `{map_key}__i0__*` already exist, returns early (re-execution safety).
- For each `item` at index `i` in `item_list`, inserts body task_runs with keys `{map_key}__i{i}__{body_key}`.
- Root body tasks (empty `needs` in body spec) get `depends_on = [map_key]` (the coordinator).
- Non-root body tasks get `depends_on = ["{map_key}__i{i}__{dep}"]` (namespaced sibling deps).
- Concurrency throttling via chaining: if `config["concurrency"] > 0 and i >= concurrency`, root body tasks of iteration `i` get `depends_on = ["{map_key}__i{i - concurrency}__{last_body_key}"]` instead.
- **Replaces** the placeholder collector's `depends_on` with `["{map_key}__i{N-1}__{last_body_key}", ...]` for all iterations.
- Injects `{"__map_item__": item}` into each child body task's `config` (stored on the task_run; stripped from stored result).

**Task key naming:**
```
Coordinator:  process_regions
Collector:    process_regions__collect
Body iter 0:  process_regions__i0__enrich
              process_regions__i0__load
Body iter 1:  process_regions__i1__enrich
              process_regions__i1__load
```

**Item injection:**
- `executor._resolve_value`: add `"item"` namespace. Reads `ctx.inputs.get("__map_item__")` (special synthetic key) and navigates dot-path.
- `_handle_python`: inject `item = json.loads(item_json)` as a third subprocess local alongside `inputs` and `params`. `item_json` comes from `config["__map_item__"]`.
- The runtime, when building `ctx.inputs` for a child task_run, injects `inputs["__map_item__"] = task_run["config"]["__map_item__"]`.

**Fan-in:** When all `{map_key}__i*__{last_body_key}` task_runs are `success`, `advance_readiness` transitions the collector to `ready`. The collector (`kind="noop"`) runs and returns `{"inputs": ctx.inputs}` — or a custom `_handle_map_collect` handler that returns `{"items": [...], "item_count": N}`. Downstream tasks reference `inputs["process_regions__collect"]["items"]`.

**dep resolution shorthand:** In `advance_readiness`, when resolving a task's `depends_on`, if a dep key matches a map coordinator (i.e., `"{dep}__collect"` exists in `state_by_key`), substitute `"{dep}__collect"`. This lets spec authors write `needs: ["process_regions"]` without knowing about the collector's internal key.

```python
def _resolve_dep_key(dep: str, state_by_key: dict[str, str]) -> str:
    collect_key = f"{dep}__collect"
    return collect_key if collect_key in state_by_key else dep
```

### 3.2 Branch execution

**Handler (`_handle_branch`):**

```python
def _handle_branch(config: dict, ctx: TaskContext, claims: dict) -> dict:
    from app.flows.executor import _resolve_string  # noqa: PLC0415
    condition_tmpl: str = config.get("condition", "")
    resolved = _resolve_string(condition_tmpl, ctx)
    falsy = {"", "false", "0", "null", "none", "no", "off"}
    taken = "then" if resolved.strip().lower() not in falsy else "else_"
    return {
        "__branch_result__": True,
        "taken": taken,
        "then": config.get("then"),
        "else_": config.get("else_"),
        "condition_value": resolved,
    }
```

**Post-success hook in `_execute_claimed_task_run`:** After branch task_run is marked `success`, call `_skip_branch_tasks` with the untaken arm's task key, before calling `advance_readiness`.

```python
async def _skip_branch_tasks(
    store: Any,
    flow_run_id: str,
    skip_key: str,     # single key — one arm only (not a list)
    now: datetime,
) -> None:
    """Transition skip_key's task_run from pending/ready → skipped.
    Only affects the named task; advance_readiness cascades to descendants."""
    task_runs = await store.list_task_runs(flow_run_id)
    for tr in task_runs:
        if tr["task_key"] == skip_key and tr["state"] in ("pending", "ready"):
            await store.update_task_run(tr["id"], {"state": "skipped", "finished_at": now})
            break
```

**`advance_readiness` change — `_BLOCKING_STATES`:**

```python
# BEFORE (runtime.py line 84):
_BLOCKING_STATES = frozenset({"failed", "timed_out", "upstream_failed", "skipped", "cancelled"})

# AFTER:
_BLOCKING_STATES = frozenset({"failed", "timed_out", "upstream_failed", "cancelled"})
# skipped removed: a task whose only deps are skipped should itself be skipped,
# not upstream_failed. A task with mixed success+skipped deps should be ready.
```

**New readiness rule in `advance_readiness`:**

```python
# After the existing "if any in _BLOCKING_STATES" block and "elif all == success" block:
elif all(s in _TERMINAL_STATES for s in dep_states):
    # All deps terminal but not all success — mixed success/skipped scenario.
    if all(s == "skipped" for s in dep_states):
        # All deps were skipped → cascade: this task is also skipped.
        await store.update_task_run(tr["id"], {"state": "skipped", "finished_at": now})
        state_by_key[tr["task_key"]] = "skipped"
    else:
        # At least one dep succeeded (others may be skipped) → ready.
        await store.update_task_run(tr["id"], {"state": "ready", "scheduled_at": now})
        state_by_key[tr["task_key"]] = "ready"
```

**Branch result stored on task_run:** `{"taken": "then"|"else_", "condition_value": "..."}` (internal `__branch_result__` key stripped before storage).

**Finalization:** `skipped` remains in `_TERMINAL_STATES`. Finalization check `all(s in _TERMINAL_STATES ...)` continues to work. `has_failure = any(s in _BLOCKING_STATES ...)` now excludes `skipped`, so a flow where one branch ran and the other was skipped finalizes as `success`.

### 3.3 Schema changes (`task_runs` table — migration 0017)

```sql
ALTER TABLE task_runs
    ADD COLUMN IF NOT EXISTS map_parent_key  text,
    ADD COLUMN IF NOT EXISTS map_index       integer;

-- Idempotency constraint (required for expand_map_node retry safety):
ALTER TABLE task_runs
    ADD CONSTRAINT IF NOT EXISTS task_runs_flow_run_task_key_unique
    UNIQUE (flow_run_id, task_key);
```

`InMemoryFlowStore.add_task_runs` and `_row_to_task_run` gain `map_parent_key` and `map_index` fields (both default `None`).

### 3.4 Registry additions

```python
# In _bootstrap(registry):
registry.register("map", _handle_map)
registry.register("branch", _handle_branch)
# map_collect can reuse the noop handler for v1; upgrade later for aggregate result shape
```

### 3.5 State machine (additions only)

Existing states unchanged. The only behavioral change is `skipped` in `advance_readiness`:

```
pending → skipped   (when branch gate skips this arm, OR when all deps are skipped)
skipped             (terminal; does NOT propagate to blocking_states)
```

---

## 4. Final Round-Trip + Codegen Plan

### 4.1 `specToGraph` extension (specGraph.js)

Signature change: `specToGraph(spec, { expandedGroups = new Set() } = {})`

`expandedGroups: Set<string>` — map task keys currently expanded on canvas. Owned by `FlowBuilder` state.

**Map nodes:**
- Collapsed (default): renders as a `mapGroupNode` React Flow type. Same footprint as `taskNode`. Body task count + `over` expression shown as subtitle.
- Expanded: renders as a group container with child nodes. Body task positions are spec-relative (stored in body TaskSpec `ui`); `specToGraph` adds `{MAP_BODY_OFFSET_X, MAP_BODY_OFFSET_Y}` to place them inside the group.
- Body node React Flow ids: `"{mapKey}::{bodyKey}"` (double-colon namespace).
- Body edges (within group): `id = "{mapKey}::{srcKey}→{mapKey}::{tgtKey}"`, `parentNode = mapKey`, `extent = "parent"`.
- Outer edges from/to the map node connect to the group container's handles (not body handles), regardless of expansion state.

**Branch nodes:** Renders as `branchNode` type (diamond shape). Two source handles: `id="then"` (left, green) and `id="else_"` (right, orange). Outgoing edges carry `sourceHandle` and a visual `label`. Inbound edges from `needs` are normal.

**graphToSpec** changes:
- Body nodes (those with `isBodyNode: true` in `data`) are grouped by `data.mapKey`.
- Body needs rebuilt from edges where both `source` and `target` share the same `mapKey` namespace.
- Body `ui` coords: subtract `{MAP_BODY_OFFSET_X, MAP_BODY_OFFSET_Y}` before writing back.
- Branch `config.branches` reconstructed from edges with `sourceHandle` field.
- Branch `then`/`else_` reconstructed from labeled outgoing edge targets.

**Round-trip invariants:**
- Body task `ui` coords are spec-relative throughout; offset added/subtracted symmetrically.
- Branch condition string stored verbatim in `config.condition`; reconstructed from `data.task.config`.
- `graphToSpec` ignores body nodes when building the top-level task list.
- `specToGraph` and `graphToSpec` are pure functions with no side effects.

### 4.2 FlowBuilder.jsx changes

```jsx
// New state:
const [expandedGroups, setExpandedGroups] = useState(new Set())

// Pass to specToGraph:
const { nodes, edges } = useMemo(
  () => specToGraph(spec, { expandedGroups }),
  [spec, expandedGroups]
)

// Toggle handler (passed as data prop to mapGroupNode):
const toggleGroup = useCallback((mapKey) => {
  setExpandedGroups(prev => {
    const next = new Set(prev)
    next.has(mapKey) ? next.delete(mapKey) : next.add(mapKey)
    return next
  })
}, [])
```

**Palette additions:**
```js
{ kind: 'map',    label: 'Map (fan-out)', defaultConfig: { over: '', body: [] } }
{ kind: 'branch', label: 'Branch',        defaultConfig: { condition: '{{ inputs. }}', then: '', else_: '' } }
```

**New node type registrations:**
```jsx
const nodeTypes = {
  taskNode: TaskNode,
  mapGroupNode: MapGroupNode,   // NEW
  branchNode: BranchNode,       // NEW
}
```

### 4.3 NodeInspector.jsx — new config panels

**MapConfig** (when `task.kind === 'map'`):
- `over` field: text input, placeholder `"e.g. fetch.rows"`.
- Body tasks: read-only count badge + note "expand on canvas to edit body tasks."
- `concurrency`: number input, 0 = unlimited.

**BranchConfig** (when `task.kind === 'branch'`):
- `condition` field: text input with `{{ }}` hint.
- `then` / `else_` fields: read-only (auto-populated from canvas edges). Note: "draw edges from the then/else handles."

**Body-node inspector** (when `data.isBodyNode: true`):
- Normal kind-specific config panel, but with a header banner: "Body task of map: {mapKey}. Use `{{ item.* }}` for the current item."
- `needs` shows only body-local deps (not outer task keys).

### 4.4 Codegen — `backend/app/flows/codegen.py`

```python
def codegen(spec: FlowSpec) -> str:
    """FlowSpec → Python scaffold (spec-preserving, not byte-preserving).
    
    Fidelity contract: compile(eval(codegen(spec))) == spec.
    Does not require nubi.flows SDK to be importable; purely a string emitter.
    
    Known fidelity gaps (non-exhaustive):
    - Original comments and docstrings are not recoverable.
    - Variable names are derived from task keys only.
    - canvas ui.x/y coords are not emitted (auto-layout on recompile).
    - Multi-line code in python tasks is embedded as triple-quoted strings.
    """


def fidelity_limits() -> list[str]:
    """Return human-readable list of known codegen fidelity gaps."""
```

**Codegen algorithm:**

1. Emit imports: `from nubi.flows import flow, query, python, ...`
2. Emit `PARAMS = [...]` if spec has params.
3. Emit `@task(kind=..., **exec_options)` + `def {key}()` for all non-map, non-branch tasks in topological order.
4. For each map task: emit body task `@task` functions prefixed `{map_key}_{body_key}`, then a `def _build_{map_key}_body(item):` function.
5. Emit `@flow(name=..., params=PARAMS)` + `def {flow_name}():` body:
   - Each task call: `{key} = {fn}({after_vars})` in topological order.
   - Map tasks: `{key} = {key}.map(_build_{key}_body, over=..., concurrency=...)` on the upstream handle.
   - Branch tasks: `branch({condition_var}, then={then_var}, else_={else_var}, key=...)`.
   - Return last handle.

**API endpoint:** `GET /flows/{id}/codegen` → `{"python": "..."}`. No auth change needed (same permissions as spec view).

### 4.5 Ownership model

**Schema:** `flows` table gains `source TEXT NOT NULL DEFAULT 'canvas'`.

**Values:** `'canvas'` | `'code'` | `'mixed'`

**State transitions:**
- Canvas creates flow: `source = 'canvas'`
- SDK compile (first time): `source = 'code'`
- Canvas edits a `'code'` flow (after user confirms warning): `source = 'mixed'`
- Next SDK compile of a `'mixed'` flow: warns, sets `source = 'code'`

**UI:** FlowBuilder shows an amber banner when `flow.source === 'code'`. "This flow is code-owned. Canvas edits will be overwritten on next SDK compile. [Claim canvas ownership]"

---

## 5. Open Questions (Decisions Needed)

Only items that genuinely require a human judgment call — design-space questions where both options are defensible.

**Q1: branch `else_` required or optional?**  
Current design requires both `then` and `else_`. An optional `else_` (omitted = "do nothing on falsy") would be more ergonomic for many real cases (e.g., "if new data, load it; otherwise skip to done"). Implication: if `else_` is omitted and condition is falsy, no skip needed — the branch just produces no activation and downstream tasks that need the branch must handle "no-op" gracefully. Recommend making `else_` optional and using `None`/"skip" sentinel.

**Q2: branch `then`/`else_` as single key or list?**  
Current design is single string key per arm. A list (fan-out from branch) is expressible but requires more edge complexity on canvas. Single-key is simpler; if multi-downstream is needed, a `noop` fan-out node after the branch achieves it. Confirm single-key is acceptable.

**Q3: map collector result shape.**  
The `noop` collector returns `{"inputs": ctx.inputs}`. Downstream tasks must use `inputs["process_regions__collect"]["inputs"]["process_regions__i0__load"]["rows"]` — deeply nested. A custom `map_collect` handler returning `{"items": [...], "item_count": N}` (one entry per iteration) is cleaner. Adds one registry entry; no schema change. Recommend the custom handler.

**Q4: `@flow` body with required function parameters.**  
Current design calls `fn()` with zero args at decoration time. Flows that need a compile-time parameter (e.g., to select which sub-graph to build) cannot be expressed. Options: (a) prohibit non-default params on `@flow` bodies entirely — all runtime variation must use `FlowParam`; (b) accept `**kwargs` with defaults only. Option (a) is cleaner for the strict round-trip constraint. Confirm.

**Q5: `run_local` async context.**  
`asyncio.run()` fails inside Jupyter or FastAPI test client (running event loop). Should `run_local` detect this via `asyncio.get_event_loop().is_running()` and raise a clear `RuntimeError` with instructions to use `await flow_fn.arun()`? Or silently use `nest_asyncio` as a dep? Recommend explicit `RuntimeError` + document an `arun()` coroutine sibling.

---

## 6. Implementation Task Graph

### Sequencing notes

- The "concurrent engine build" (spec.py / runtime.py / registry.py / store.py) must land before tasks that modify those files.
- Frontend tasks (specGraph.js / FlowBuilder / NodeInspector / TaskNode) can run in parallel with each other but need the spec schema to be finalized.
- The SDK package (nubi/flows/) has no dependencies on other agent tasks — it only reads from `app.flows.spec` which changes in agent A.
- Codegen is independent of the SDK tracing implementation.
- All agents have disjoint file ownership.

### Task graph

```
A (spec.py + store.py) ──────────────────┐
                                          │
B (registry.py handlers) ─── depends A   │
                                          ▼
C (runtime.py expansion) ─── depends A ──► E (integration tests)
                                          ▲
D (SDK package) ─────────────────────────┘
                          depends A only
                          (reads spec.py, not runtime.py)

F (specGraph.js) ──────── depends A ─────► H (FlowBuilder.jsx +
G (NodeInspector.jsx)  ─── depends A ─────►  TaskNode.jsx)
                                             depends F, G
I (codegen.py) ─────────── depends A ─── independent of B/C/D
J (DB migration 0017) ──── depends A ─── independent of B/C
```

### Agent specifications

---

#### Agent A — `spec.py` and `store.py` extensions
**Parallel-safe:** No (is depended on by B, C, D, F, G, I, J)  
**Depends on:** Nothing (first to land)

**Owns files:**
- `backend/app/flows/spec.py`
- `backend/app/flows/store.py`

**Touches engine files:**
- `backend/app/flows/spec.py` — extend `TaskSpec.kind` Literal; add `map`/`branch` validation blocks to `validate_flow_spec` step 5; add `_validate_body_acyclic` helper; add branch `then`/`else_` cross-reference checks.
- `backend/app/flows/store.py` — add `map_parent_key: str | None` and `map_index: int | None` fields to `InMemoryFlowStore` task_run dict shape and `_row_to_task_run`.

**Does NOT touch:** `runtime.py`, `registry.py`, `executor.py`, any frontend files.

**Deliverable:** `validate_flow_spec` correctly hard-errors on invalid map/branch config; `flow_spec_is_valid` correctly passes the new JSON examples in this doc.

---

#### Agent B — `registry.py` handlers
**Parallel-safe:** No (depends on A; B and C can run in parallel after A)  
**Depends on:** Agent A

**Owns files:**
- `backend/app/flows/registry.py`
- `backend/app/flows/handlers/map.py` (new)
- `backend/app/flows/handlers/branch.py` (new)

**Touches engine files:**
- `backend/app/flows/registry.py` — add `registry.register("map", _handle_map)` and `registry.register("branch", _handle_branch)` in `_bootstrap`.
- `backend/app/flows/handlers/map.py` (new) — `_handle_map` handler: resolves `config["over"]` via dot-path against `ctx.inputs`, validates list, returns `{"__map_item_list__": list, "item_count": N}`.
- `backend/app/flows/handlers/branch.py` (new) — `_handle_branch` handler: resolves `config["condition"]` via `_resolve_string`, applies falsy-set, returns `{"__branch_result__": True, "taken": "then"|"else_", "then": ..., "else_": ..., "condition_value": ...}`.

**Does NOT touch:** `spec.py`, `store.py`, `runtime.py`, `executor.py`, any frontend files.

---

#### Agent C — `runtime.py` and `executor.py` extensions
**Parallel-safe:** No (depends on A; B and C can run in parallel after A)  
**Depends on:** Agent A

**Owns files:**
- `backend/app/flows/runtime.py`
- `backend/app/flows/executor.py`

**Touches engine files:**
- `backend/app/flows/runtime.py`:
  - Remove `"skipped"` from `_BLOCKING_STATES`.
  - `materialize_flow_run`: when `task.kind == "map"`, also insert a placeholder collector task_run (`{task.key}__collect`) with `depends_on=[task.key]`, `kind="noop"`, `config={"__map_collect__": True, "map_parent_key": task.key}`.
  - New function `expand_map_node(store, flow_run_id, map_task_run, item_list, map_spec, org_id, now) -> int`: idempotent fan-out (checks existing keys; uses `ON CONFLICT DO NOTHING` via store); handles concurrency chaining; replaces collector `depends_on`.
  - New function `_skip_branch_tasks(store, flow_run_id, skip_key, now)`: transitions single task from pending/ready → skipped.
  - `advance_readiness`: add `_resolve_dep_key` helper; add all-skipped→skipped and mixed-terminal→ready rules (section 3.2); call `_resolve_dep_key` when building `dep_states`.
  - `_execute_claimed_task_run` (post-success hook): call `expand_map_node` for `kind=="map"`; call `_skip_branch_tasks` for `kind=="branch"`.
- `backend/app/flows/executor.py`:
  - `_resolve_value`: add `"item"` namespace that reads `ctx.inputs.get("__map_item__")` and navigates dot-path.
  - `_handle_python` equivalent (or the executor's python dispatch): inject `item` as a third subprocess local from `config["__map_item__"]`.
  - `_execute_claimed_task_run` (build inputs step): inject `inputs["__map_item__"] = task_run["config"].get("__map_item__")` when present.

**Does NOT touch:** `spec.py`, `store.py`, `registry.py`, any frontend files, SDK package.

---

#### Agent D — SDK package (`nubi/flows/`)
**Parallel-safe:** Yes (depends on A only; runs in parallel with B, C)  
**Depends on:** Agent A

**Owns files (all new):**
- `backend/nubi/__init__.py`
- `backend/nubi/flows/__init__.py`
- `backend/nubi/flows/_builder.py`
- `backend/nubi/flows/_nodes.py`
- `backend/nubi/flows/_combinators.py`
- `backend/nubi/flows/_compile.py`
- `backend/nubi/flows/_run.py`
- `backend/nubi/flows/_keygen.py`

**Touches engine files:** None (read-only imports from `app.flows.spec` and `app.flows.runtime`).

**Deliverable:** All three worked examples in Section 1 compile to the specified JSON. `run_local` executes the linear example against `InMemoryFlowStore`.

---

#### Agent E — Integration tests
**Parallel-safe:** No (depends on A, B, C, D all landing)  
**Depends on:** Agents A, B, C, D

**Owns files (all new):**
- `backend/tests/test_map_execution.py`
- `backend/tests/test_branch_execution.py`
- `backend/tests/test_sdk_compile.py`

**Touches engine files:** None (test-only).

**Deliverable:** Tests covering: map fan-out with N=0/1/3 items; concurrency throttling; expand_map_node idempotency; branch then/else_ routing; skipped propagation; advance_readiness new rules; SDK compile of all three worked examples.

---

#### Agent F — `specGraph.js` and `specGraph.test.mjs`
**Parallel-safe:** Yes (depends on A spec only; runs in parallel with G)  
**Depends on:** Agent A (schema finalized)

**Owns files:**
- `src/flows/specGraph.js`
- `src/flows/specGraph.test.mjs`

**Touches engine files:**
- `src/flows/specGraph.js` — add `expandedGroups` parameter; map node collapsed/expanded rendering; body node id namespacing (`::` separator); `parseBodyNodeId`; branch node rendering with two source handles; `graphToSpec` body reconstruction; branch `config.branches` reconstruction from `sourceHandle` edges; `_resolve_dep_key` equivalent for collector display.

**Does NOT touch:** `FlowBuilder.jsx`, `NodeInspector.jsx`, `TaskNode.jsx`, any backend files.

---

#### Agent G — `NodeInspector.jsx` and new node components
**Parallel-safe:** Yes (depends on A; runs in parallel with F)  
**Depends on:** Agent A (schema finalized)

**Owns files:**
- `src/flows/NodeInspector.jsx`
- `src/flows/nodes/MapGroupNode.jsx` (new)
- `src/flows/nodes/BranchNode.jsx` (new)

**Touches engine files:**
- `src/flows/NodeInspector.jsx` — add `MapConfig` panel (over, concurrency, read-only body count); add `BranchConfig` panel (condition input, read-only then/else display); add body-node inspector mode header.
- `src/flows/nodes/MapGroupNode.jsx` (new) — collapsible group container; expand/collapse toggle; body task count badge.
- `src/flows/nodes/BranchNode.jsx` (new) — diamond shape; two source handles (`id="then"`, `id="else_"`); condition preview.

**Does NOT touch:** `specGraph.js`, `FlowBuilder.jsx`, `TaskNode.jsx`, any backend files.

---

#### Agent H — `FlowBuilder.jsx` wiring
**Parallel-safe:** No (depends on F and G)  
**Depends on:** Agents F, G

**Owns files:**
- `src/flows/FlowBuilder.jsx`

**Touches engine files:**
- `src/flows/FlowBuilder.jsx` — add `expandedGroups` state; wire `toggleGroup` into `mapGroupNode.data`; register `mapGroupNode` and `branchNode` in `nodeTypes`; add map/branch palette items; update `MiniMap` `nodeColor` switch.

**Does NOT touch:** `specGraph.js`, `NodeInspector.jsx`, any backend files.

---

#### Agent I — Codegen (`codegen.py` + API endpoint)
**Parallel-safe:** Yes (depends on A only)  
**Depends on:** Agent A

**Owns files:**
- `backend/app/flows/codegen.py` (new)
- Adds one route handler in `backend/app/routes/flows.py` (or wherever flows routes live): `GET /flows/{id}/codegen`

**Touches engine files:**
- `backend/app/flows/codegen.py` — implement `codegen(spec: FlowSpec) -> str` and `fidelity_limits() -> list[str]` per section 4.4.
- `backend/app/routes/` — add `GET /flows/{id}/codegen` endpoint (read `flow.spec`, call `codegen`, return `{"python": "..."}`, same org auth as spec read).

**Does NOT touch:** `spec.py`, `runtime.py`, `registry.py`, SDK files, any specGraph.js.

---

#### Agent J — DB migration 0017
**Parallel-safe:** Yes (depends on A schema, independent of B/C/D)  
**Depends on:** Agent A (schema decisions finalized)

**Owns files:**
- `backend/scripts/migrations/0017_task_runs_map_columns.sql` (or equivalent migration file, matching existing migration naming convention)

**Touches engine files:**
- Migration SQL: `ALTER TABLE task_runs ADD COLUMN IF NOT EXISTS map_parent_key text; ADD COLUMN IF NOT EXISTS map_index integer;`
- Migration SQL: `ALTER TABLE task_runs ADD CONSTRAINT IF NOT EXISTS task_runs_flow_run_task_key_unique UNIQUE (flow_run_id, task_key);`
- Migration SQL: `ALTER TABLE flows ADD COLUMN IF NOT EXISTS source TEXT NOT NULL DEFAULT 'canvas';`

**Does NOT touch:** Any Python or JS files.

---

### Parallel-safe summary

| Agent | Parallel-safe | Depends on |
|---|---|---|
| A — spec.py + store.py | No (root) | — |
| B — registry.py handlers | Yes (after A) | A |
| C — runtime.py + executor.py | Yes (after A; parallel with B, D) | A |
| D — SDK nubi/flows/ | Yes (after A; parallel with B, C) | A |
| E — Integration tests | No | A, B, C, D |
| F — specGraph.js | Yes (after A; parallel with G, I, J) | A |
| G — Node components | Yes (after A; parallel with F, I, J) | A |
| H — FlowBuilder.jsx wiring | No | F, G |
| I — codegen.py | Yes (after A; parallel with B–G) | A |
| J — DB migration | Yes (after A; parallel with B–G) | A |

**Critical path:** A → (B + C + D in parallel) → E → ship backend  
**Frontend critical path:** A → (F + G in parallel) → H → ship frontend

---

## Appendix A — Worked Example Specs

### A.1 Linear flow (for SDK test)

```json
{
  "version": 1,
  "name": "daily_revenue",
  "params": [{"name": "region", "type": "text", "required": true}],
  "tasks": [
    {
      "key": "pull",
      "kind": "query",
      "needs": [],
      "config": {"sql": "SELECT * FROM sales WHERE region = '{{ params.region }}'"},
      "retries": 0, "retry_backoff_s": 30, "timeout_s": 60, "cache_ttl_s": 0,
      "ui": {"x": 60, "y": 200}
    },
    {
      "key": "enrich",
      "kind": "python",
      "needs": ["pull"],
      "config": {"code": "result = {'n': inputs['pull']['row_count']}"},
      "retries": 0, "retry_backoff_s": 30, "timeout_s": 60, "cache_ttl_s": 0,
      "ui": {"x": 320, "y": 200}
    }
  ]
}
```

### A.2 Map flow

```json
{
  "version": 1,
  "name": "process_all_regions",
  "params": [],
  "tasks": [
    {
      "key": "fetch_regions",
      "kind": "query",
      "needs": [],
      "config": {"sql": "SELECT DISTINCT code FROM regions"},
      "retries": 0, "retry_backoff_s": 30, "timeout_s": 60, "cache_ttl_s": 0,
      "ui": {"x": 0, "y": 200}
    },
    {
      "key": "process_regions",
      "kind": "map",
      "needs": ["fetch_regions"],
      "config": {
        "over": "fetch_regions.rows",
        "concurrency": 4,
        "body": [
          {
            "key": "enrich",
            "kind": "python",
            "needs": [],
            "config": {"code": "result = {'code': item['code'], 'done': True}"},
            "retries": 0, "retry_backoff_s": 30, "timeout_s": 60, "cache_ttl_s": 0,
            "ui": {"x": 0, "y": 0}
          }
        ]
      },
      "retries": 0, "retry_backoff_s": 30, "timeout_s": 300, "cache_ttl_s": 0,
      "ui": {"x": 260, "y": 200}
    },
    {
      "key": "summary",
      "kind": "python",
      "needs": ["process_regions"],
      "config": {"code": "result = {'total': inputs.get('process_regions__collect', {}).get('item_count', 0)}"},
      "retries": 0, "retry_backoff_s": 30, "timeout_s": 60, "cache_ttl_s": 0,
      "ui": {"x": 520, "y": 200}
    }
  ]
}
```

Runtime task_runs created (N=2 regions):
```
fetch_regions          → success
process_regions        → success (coordinator; triggers expand_map_node)
process_regions__i0__enrich → pending→ready→success
process_regions__i1__enrich → pending→ready→success
process_regions__collect    → pending→ready (after both iterations)→success
summary                → pending→ready (after collect via _resolve_dep_key)→success
```

### A.3 Branch flow

```json
{
  "version": 1,
  "name": "conditional_load",
  "params": [],
  "tasks": [
    {
      "key": "check",
      "kind": "query",
      "needs": [],
      "config": {"sql": "SELECT count(*) > 0 AS has_data FROM new_data"},
      "retries": 0, "retry_backoff_s": 30, "timeout_s": 30, "cache_ttl_s": 0,
      "ui": {"x": 0, "y": 200}
    },
    {
      "key": "route",
      "kind": "branch",
      "needs": ["check"],
      "config": {
        "condition": "{{ inputs.check.rows.0.has_data }}",
        "then": "load_data",
        "else_": "skip_alert"
      },
      "retries": 0, "retry_backoff_s": 30, "timeout_s": 60, "cache_ttl_s": 0,
      "ui": {"x": 260, "y": 200}
    },
    {
      "key": "load_data",
      "kind": "python",
      "needs": ["route"],
      "config": {"code": "result = {'mode': 'loaded'}"},
      "retries": 0, "retry_backoff_s": 30, "timeout_s": 60, "cache_ttl_s": 0,
      "ui": {"x": 520, "y": 100}
    },
    {
      "key": "skip_alert",
      "kind": "noop",
      "needs": ["route"],
      "config": {},
      "retries": 0, "retry_backoff_s": 30, "timeout_s": 60, "cache_ttl_s": 0,
      "ui": {"x": 520, "y": 300}
    },
    {
      "key": "done",
      "kind": "noop",
      "needs": ["load_data", "skip_alert"],
      "config": {},
      "retries": 0, "retry_backoff_s": 30, "timeout_s": 60, "cache_ttl_s": 0,
      "ui": {"x": 780, "y": 200}
    }
  ]
}
```

State trace (condition truthy):
```
check       → success
route       → success (taken="then"; _skip_branch_tasks("skip_alert"))
load_data   → ready → success
skip_alert  → skipped
done        → ready (load_data=success, skip_alert=skipped → mixed → ready)
            → success
flow_run    → success (no blocking states)
```
