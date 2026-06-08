"""FlowSpec → Python SDK scaffold codegen.

Converts a validated :class:`~app.flows.spec.FlowSpec` (the canonical IR) into
runnable Python source that uses the Nubi flows SDK (``sdk.py``).  The output,
when traced via ``.compile()``, reproduces a FlowSpec whose ``tasks``,
``kinds``, ``configs``, ``needs``, and ``params`` match the input spec 1:1.

**Fidelity limits (scaffold-grade, not byte-preserving):**

- Canvas layout (``ui.x``/``ui.y``) is NOT emitted; compiled output sets them
  to ``{x: 0, y: 0}`` (canvas concern, not a code concern).
- Variable names are derived from task ``key`` (snake-cased).
- Comments are generated from task keys / kinds.
- Python task ``code`` blocks are preserved verbatim.
- The output is valid, importable Python that traces correctly.

Public API
----------
flow_spec_to_sdk(spec) -> str
    Main entry point.  Accepts a :class:`FlowSpec` and returns Python source.

_task_to_sdk_call(task_dict, inputs_map, map_keys, indent) -> tuple[str, str]
    Emit a ``@task`` decorator + stub (top-level) and a call line (flow body).

_map_task_to_sdk(task_dict, indent) -> str
    Emit a ``@map_node(...)`` decorated inner function for a map task.

_branch_task_to_sdk(task_dict, indent) -> str
    Emit a ``branch_node(...)`` call for a branch task.

Example
-------
Given a FlowSpec named ``"daily_revenue_v2"`` with three tasks (get_regions,
process_each_region as map, aggregate):

.. code-block:: python

    # Auto-generated scaffold from FlowSpec "daily_revenue_v2"
    # Edit task configs; do not restructure the graph here — use the canvas or recompile.

    from nubi.sdk import flow, task, map_node, branch_node

    @task(kind="query", sql="SELECT DISTINCT region FROM sales")
    def get_regions(): pass

    @task(kind="materialize", combine_sql="SELECT * FROM results")
    def aggregate(): pass

    @flow
    def daily_revenue_v2():
        get_regions_handle = get_regions()

        @map_node(
            key="process_each_region",
            item_expr="{{ inputs.get_regions.rows }}",
            item_var="region",
            max_concurrency=4,
            collect_key="transform",
        )
        def process_each_region(region):
            @task(kind="query", sql="SELECT * FROM sales WHERE region = '{{ item.region_code }}'")
            def fetch_data(): pass

            @task(kind="python", code="result = {k: v*2 for k, v in inputs['fetch_data']['rows'][0].items()}")
            def transform(): pass

            fetch_data_handle = fetch_data()
            return transform(fetch_data_handle)

        aggregate(process_each_region.collect())

    spec = daily_revenue_v2.compile()
"""

from __future__ import annotations

from typing import Any

from app.flows.spec import FlowSpec


# ---------------------------------------------------------------------------
# Topological sort helper
# ---------------------------------------------------------------------------


def _topo_sort(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return tasks in topological (dependency-first) order.

    Uses Kahn's algorithm on the ``needs`` graph.  Tasks with no needs come
    first.  The relative order within a tier follows the original list order.

    Parameters
    ----------
    tasks:
        List of task dicts, each with a ``"key"`` and ``"needs"`` list.

    Returns
    -------
    list[dict]
        Topologically sorted task list.  Cycles are silently broken by
        skipping already-visited nodes (spec.py validation catches real cycles).
    """
    key_to_task = {t["key"]: t for t in tasks}
    in_degree: dict[str, int] = {t["key"]: 0 for t in tasks}
    successors: dict[str, list[str]] = {t["key"]: [] for t in tasks}
    original_order = {t["key"]: i for i, t in enumerate(tasks)}

    for t in tasks:
        for dep in (t.get("needs") or []):
            if dep in in_degree:
                in_degree[t["key"]] += 1
                successors[dep].append(t["key"])

    queue: list[str] = sorted(
        (k for k, deg in in_degree.items() if deg == 0),
        key=lambda k: original_order.get(k, 0),
    )

    result: list[dict[str, Any]] = []
    while queue:
        k = queue.pop(0)
        result.append(key_to_task[k])
        newly_zero = sorted(
            (c for c in successors[k] if in_degree.__setitem__(c, in_degree[c] - 1) is None
             and in_degree[c] == 0),
            key=lambda x: original_order.get(x, 0),
        )
        queue = newly_zero + queue

    # Append any remaining tasks (cycle guard — shouldn't happen post-validation).
    seen = {t["key"] for t in result}
    for t in tasks:
        if t["key"] not in seen:
            result.append(t)

    return result


# ---------------------------------------------------------------------------
# Python repr helpers
# ---------------------------------------------------------------------------


def _repr_value(v: Any) -> str:
    """Return a Python-literal representation of *v*.

    Strings use double-quotes.  Lists, dicts, ints, floats, bools, and None
    are represented naturally.  Nested structures are handled recursively.

    Parameters
    ----------
    v:
        Arbitrary Python value.

    Returns
    -------
    str
        Python source literal for the value.
    """
    if isinstance(v, bool):
        return "True" if v else "False"
    if v is None:
        return "None"
    if isinstance(v, str):
        # Use repr; replace single outer quotes with double quotes for style.
        r = repr(v)
        if r.startswith("'") and r.endswith("'"):
            inner = r[1:-1].replace("\\'", "'").replace('"', '\\"')
            return f'"{inner}"'
        return r
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, list):
        if not v:
            return "[]"
        items = ", ".join(_repr_value(i) for i in v)
        return f"[{items}]"
    if isinstance(v, dict):
        if not v:
            return "{}"
        pairs = ", ".join(f"{_repr_value(k)}: {_repr_value(val)}" for k, val in v.items())
        return "{" + pairs + "}"
    # Fallback for any other type.
    return repr(v)


def _config_to_kwargs(
    config: dict[str, Any],
    skip_keys: set[str] | None = None,
) -> str:
    """Serialize *config* dict to keyword-argument string.

    Parameters
    ----------
    config:
        Task config dict (kind-specific fields).
    skip_keys:
        Config keys to omit (e.g. ``"body"`` for map nodes — emitted separately).

    Returns
    -------
    str
        Comma-separated ``key=value`` pairs, or empty string if nothing to emit.
    """
    skip = skip_keys or set()
    parts: list[str] = []
    for k, v in config.items():
        if k in skip:
            continue
        parts.append(f"{k}={_repr_value(v)}")
    return ", ".join(parts)


def _make_task_decorator(kind: str, config: dict[str, Any], base_indent: str = "") -> str:
    """Build a ``@task(kind=..., ...)`` decorator line, wrapping if > 88 chars.

    Parameters
    ----------
    kind:
        The task kind string.
    config:
        The task config dict (kind-specific fields).
    base_indent:
        Leading whitespace to prepend to every line.

    Returns
    -------
    str
        One or more decorator lines (without the trailing ``def`` line).
    """
    kwargs_str = _config_to_kwargs(config)
    if kwargs_str:
        single_line = f'@task(kind="{kind}", {kwargs_str})'
    else:
        single_line = f'@task(kind="{kind}")'

    if len(base_indent) + len(single_line) <= 88:
        return base_indent + single_line

    # Multi-line form.
    kw_parts = [f'{base_indent}    kind="{kind}"']
    for k, v in config.items():
        kw_parts.append(f"{base_indent}    {k}={_repr_value(v)}")
    return base_indent + "@task(\n" + ",\n".join(kw_parts) + ",\n" + base_indent + ")"


# ---------------------------------------------------------------------------
# Regular task emitter
# ---------------------------------------------------------------------------


def _task_to_sdk_call(
    task_dict: dict[str, Any],
    inputs_map: dict[str, list[str]],
    map_keys: set[str],
    indent: str = "    ",
) -> tuple[str, str]:
    """Emit a ``@task`` decorator + function stub and the flow-body call line.

    Returns a 2-tuple:
    - ``stub``: top-level ``@task`` + ``def`` lines (no indent).
    - ``call``: flow-body assignment line (with *indent*).

    Parameters
    ----------
    task_dict:
        Task serialised as a dict (``key``, ``kind``, ``needs``, ``config``).
    inputs_map:
        Mapping from task_key → list[upstream_task_key].
    map_keys:
        Set of task keys that are ``kind == "map"`` in the parent flow.
        Used to emit ``.collect()`` on map node handles.
    indent:
        Indentation string for the flow body call line.

    Returns
    -------
    tuple[str, str]
        ``(stub, call_line)``
    """
    key: str = task_dict["key"]
    kind: str = task_dict["kind"]
    config: dict[str, Any] = task_dict.get("config") or {}
    needs: list[str] = task_dict.get("needs") or []

    decorator = _make_task_decorator(kind, config, base_indent="")
    stub = f"{decorator}\ndef {key}(): pass"

    # Build flow-body call: upstream args use handle vars.
    # Map nodes are bound as the plain function name (via @map_node decorator),
    # so use "{u}.collect()" for map upstreams and "{u}_handle" for regular ones.
    upstream_args = ", ".join(
        f"{u}.collect()" if u in map_keys else f"{u}_handle"
        for u in needs
        if u in inputs_map
    )
    call = f"{indent}{key}_handle = {key}({upstream_args})"
    return stub, call


# ---------------------------------------------------------------------------
# Map task emitter
# ---------------------------------------------------------------------------


def _map_task_to_sdk(
    task_dict: dict[str, Any],
    indent: str = "    ",
) -> str:
    """Emit a ``@map_node(...)`` decorated inner function for a map task.

    The inner function body contains ``@task`` stubs and call expressions for
    the body sub-DAG, followed by ``return <collect_key_handle>``.

    Parameters
    ----------
    task_dict:
        Task dict with ``kind == "map"``.
    indent:
        Indentation prefix for lines inside the flow body.

    Returns
    -------
    str
        Multi-line Python source for the map block (no leading blank line).
    """
    key: str = task_dict["key"]
    config: dict[str, Any] = task_dict.get("config") or {}

    item_expr: str = config.get("item_expr", "")
    item_var: str = config.get("item_var", "item")
    max_concurrency: int = int(config.get("max_concurrency") or 0)
    max_map_size: int = int(config.get("max_map_size") or 1000)
    collect_key: str | None = config.get("collect_key")
    body_tasks: list[dict[str, Any]] = config.get("body") or []

    # Build @map_node(...) decorator arguments.
    map_node_arg_parts: list[str] = [
        f'    key="{key}"',
        f'    item_expr="{item_expr}"',
        f'    item_var="{item_var}"',
    ]
    if max_concurrency:
        map_node_arg_parts.append(f"    max_concurrency={max_concurrency}")
    if max_map_size != 1000:
        map_node_arg_parts.append(f"    max_map_size={max_map_size}")
    if collect_key:
        map_node_arg_parts.append(f'    collect_key="{collect_key}"')

    # Assemble decorator with per-line indent.
    dec_lines: list[str] = [f"{indent}@map_node("]
    for arg in map_node_arg_parts:
        dec_lines.append(f"{indent}{arg},")
    dec_lines.append(f"{indent})")
    dec_lines.append(f"{indent}def {key}({item_var}):")

    # Body: topo-sort body tasks, emit stubs + calls inside inner function.
    sorted_body = _topo_sort(body_tasks)
    body_inputs_map: dict[str, list[str]] = {t["key"]: (t.get("needs") or []) for t in body_tasks}
    inner_indent = indent + "    "

    inner_lines: list[str] = []

    # Emit @task stubs for body tasks (inside the inner function).
    for bt in sorted_body:
        bkey = bt["key"]
        bkind = bt["kind"]
        bconfig = bt.get("config") or {}

        dec = _make_task_decorator(bkind, bconfig, base_indent=inner_indent)
        inner_lines.append(dec)
        inner_lines.append(f"{inner_indent}def {bkey}(): pass")
        inner_lines.append("")

    # Emit call lines in topo order (no map nodes in body — spec prohibits it).
    last_handle_var: str = ""
    for bt in sorted_body:
        bkey = bt["key"]
        bneeds = bt.get("needs") or []
        upstream_args = ", ".join(
            f"{u}_handle" for u in bneeds if u in body_inputs_map
        )
        handle_var = f"{bkey}_handle"
        inner_lines.append(f"{inner_indent}{handle_var} = {bkey}({upstream_args})")
        last_handle_var = handle_var

    # Return the collect_key handle or the last handle.
    return_var = f"{collect_key}_handle" if collect_key else last_handle_var
    if return_var:
        inner_lines.append(f"{inner_indent}return {return_var}")
    else:
        inner_lines.append(f"{inner_indent}pass")

    return "\n".join(dec_lines + inner_lines)


# ---------------------------------------------------------------------------
# Branch task emitter
# ---------------------------------------------------------------------------


def _branch_task_to_sdk(
    task_dict: dict[str, Any],
    indent: str = "    ",
) -> str:
    """Emit a ``branch_node(...)`` call for a branch task.

    Parameters
    ----------
    task_dict:
        Task dict with ``kind == "branch"``.
    indent:
        Indentation prefix for the flow body lines.

    Returns
    -------
    str
        One or more Python source lines for the branch_node call + assignment.
    """
    key: str = task_dict["key"]
    needs: list[str] = task_dict.get("needs") or []
    config: dict[str, Any] = task_dict.get("config") or {}
    conditions: list[dict[str, Any]] = config.get("conditions") or []
    default: list[str] = config.get("default") or []

    # First upstream is the condition source.
    upstream_arg = f"{needs[0]}_handle" if needs else "None"

    # Build conditions list repr — one dict per line.
    inner = indent + "        "
    cond_reprs: list[str] = []
    for c in conditions:
        when = _repr_value(c.get("when", ""))
        next_val = _repr_value(c.get("next") or [])
        cond_reprs.append(f'{inner}{{"when": {when}, "next": {next_val}}}')

    conditions_str = (
        "[\n" + ",\n".join(cond_reprs) + f",\n{indent}    ]"
    )

    lines: list[str] = [
        f"{indent}{key}_handle = branch_node(",
        f"{indent}    {upstream_arg},",
        f'{indent}    key="{key}",',
        f"{indent}    conditions={conditions_str},",
    ]
    if default:
        lines.append(f"{indent}    default={_repr_value(default)},")
    lines.append(f"{indent})")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def flow_spec_to_sdk(spec: FlowSpec) -> str:
    """Generate scaffold-grade Python SDK source from a FlowSpec.

    The generated source, when traced (compiled via ``.compile()``), must
    produce a FlowSpec whose ``tasks``, ``kinds``, ``configs``, ``needs``, and
    ``params`` match the input spec 1:1.  Layout (``ui.x``/``ui.y``) is NOT
    preserved in the generated code (it is a canvas concern, not a code concern).

    This function is scaffold-grade, NOT byte-preserving:

    - Variable names, import aliases, and spacing may differ from the original
      author's source.
    - Comments and docstrings are generated from task keys / kinds.
    - The output is valid, runnable Python that traces correctly.

    Parameters
    ----------
    spec:
        A validated :class:`~app.flows.spec.FlowSpec` instance.

    Returns
    -------
    str
        Python source code string.

    Example
    -------
    See the module docstring for a full example.
    """
    tasks_as_dicts: list[dict[str, Any]] = [t.model_dump() for t in spec.tasks]
    params_as_dicts: list[dict[str, Any]] = [p.model_dump() for p in spec.params]

    # Map: task_key → list[upstream_key] (from needs).
    inputs_map: dict[str, list[str]] = {t["key"]: (t.get("needs") or []) for t in tasks_as_dicts}

    # Set of task keys that are map nodes (so downstream tasks can use .collect()).
    map_keys: set[str] = {t["key"] for t in tasks_as_dicts if t["kind"] == "map"}

    # Topologically sort for deterministic output order.
    sorted_tasks = _topo_sort(tasks_as_dicts)

    # Regular tasks get top-level stubs; map/branch are inline in the flow body.
    regular_tasks = [t for t in sorted_tasks if t["kind"] not in ("map", "branch")]

    # ── Header ────────────────────────────────────────────────────────────
    lines: list[str] = [
        f'# Auto-generated scaffold from FlowSpec "{spec.name}"',
        "# Edit task configs; do not restructure the graph here"
        " — use the canvas or recompile.",
        "",
        "from nubi.sdk import flow, task, map_node, branch_node",
        "",
    ]

    # ── Top-level @task stubs (non-map, non-branch) ────────────────────────
    for td in regular_tasks:
        stub, _call = _task_to_sdk_call(td, inputs_map, map_keys)
        lines.append(stub)
        lines.append("")

    # ── @flow function ─────────────────────────────────────────────────────
    lines.append("")
    lines.append("@flow")
    lines.append(f"def {spec.name}():")

    if not sorted_tasks:
        lines.append("    pass")
    else:
        indent = "    "
        for td in sorted_tasks:
            kind = td["kind"]

            if kind == "map":
                lines.append("")
                lines.append(_map_task_to_sdk(td, indent=indent))

            elif kind == "branch":
                lines.append("")
                lines.append(_branch_task_to_sdk(td, indent=indent))

            else:
                # Regular task: emit the call line only (stub is top-level).
                _stub, call_line = _task_to_sdk_call(td, inputs_map, map_keys, indent=indent)
                lines.append(call_line)

    # ── compile() call ─────────────────────────────────────────────────────
    lines.append("")
    lines.append("")

    if params_as_dicts:
        param_parts: list[str] = []
        for p in params_as_dicts:
            name = p["name"]
            default = p.get("default")
            ptype = p.get("type", "text")
            required = p.get("required", False)
            if ptype == "text" and not required and default is not None:
                param_parts.append(f"{name}={_repr_value(default)}")
            else:
                # Emit as FlowParam dict.
                pdict: dict[str, Any] = {"type": ptype}
                if default is not None:
                    pdict["default"] = default
                if required:
                    pdict["required"] = True
                param_parts.append(f"{name}={_repr_value(pdict)}")
        lines.append(f"spec = {spec.name}.compile({', '.join(param_parts)})")
    else:
        lines.append(f"spec = {spec.name}.compile()")

    lines.append("")

    return "\n".join(lines)
