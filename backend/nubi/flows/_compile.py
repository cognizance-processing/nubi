"""FlowSpec → Python SDK source code generator (scaffold-grade codegen).

This module implements ``flow_spec_to_sdk``, which converts a FlowSpec dict
(or ``FlowSpec`` Pydantic model) into Python source code using the Nubi SDK
DSL.  The generated code, when traced via ``.compile()``, must reproduce a
``FlowSpec`` whose ``tasks``, ``kinds``, ``configs``, ``needs``, and
``params`` match the input 1:1.

**Scaffold-grade guarantees:**
- The generated code is valid Python that traces correctly.
- Variable names are derived from task keys (snake_case).
- ``ui.x``/``ui.y`` are NOT emitted — they default to ``{x:0, y:0}`` in
  compiled output, which is acceptable per the blueprint.
- Code comments are generated from task keys and kinds.
- The output is NOT byte-preserving: spacing, comment styles, and variable
  names may differ from any human-authored source.

Public API
----------
flow_spec_to_sdk(spec) -> str
    Main entry point.  Accepts a dict or a ``FlowSpec`` Pydantic instance.

_task_to_sdk_call(task_dict, inputs_map) -> str
    Emit a single ``@task`` decorator + function stub + call expression.

_map_task_to_sdk(task_dict) -> str
    Emit a ``@map_node`` decorated inner function for a map TaskSpec.

_branch_task_to_sdk(task_dict, handle_map) -> str
    Emit a ``branch_node(...)`` call for a branch TaskSpec.
"""

from __future__ import annotations

import textwrap
from typing import Any


# ---------------------------------------------------------------------------
# Topological sort (Kahn's algorithm)
# ---------------------------------------------------------------------------


def _toposort(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return *tasks* in a valid topological execution order.

    Uses Kahn's BFS algorithm.  Tasks with no ``needs`` come first (roots).
    Ties between siblings are resolved by their original index in *tasks*
    (preserves authoring order where possible).

    Parameters
    ----------
    tasks:
        List of task dicts, each with ``"key"`` and ``"needs"`` fields.

    Returns
    -------
    list[dict]
        Sorted list of task dicts.  May be a subset if the graph has cycles,
        but ``validate_flow_spec`` should have caught those before codegen.
    """
    by_key = {t["key"]: t for t in tasks}
    in_degree: dict[str, int] = {t["key"]: 0 for t in tasks}
    dependents: dict[str, list[str]] = {t["key"]: [] for t in tasks}

    for t in tasks:
        for dep in t.get("needs", []):
            if dep in in_degree:
                in_degree[t["key"]] += 1
                dependents[dep].append(t["key"])

    # Initialise queue with root tasks, preserving original order.
    queue: list[str] = [t["key"] for t in tasks if in_degree[t["key"]] == 0]
    result: list[dict[str, Any]] = []

    while queue:
        key = queue.pop(0)
        result.append(by_key[key])
        for child in dependents[key]:
            in_degree[child] -= 1
            if in_degree[child] == 0:
                queue.append(child)

    # Append any remaining (cycle-involved) tasks in original order.
    emitted = {t["key"] for t in result}
    for t in tasks:
        if t["key"] not in emitted:
            result.append(t)

    return result


# ---------------------------------------------------------------------------
# Config repr helpers
# ---------------------------------------------------------------------------


def _repr_value(v: Any, indent: int = 0) -> str:
    """Return a Python literal repr of *v* suitable for source embedding.

    - Strings use double quotes.
    - Dicts and lists are formatted with one item per line when they contain
      more than one element or contain nested structures.
    - ``None`` → ``None``.
    - Booleans → ``True``/``False``.
    - Numbers → their natural repr.
    """
    if v is None:
        return "None"
    if isinstance(v, bool):
        return "True" if v else "False"
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, str):
        # Use double quotes; escape existing double quotes.
        escaped = v.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    if isinstance(v, list):
        if not v:
            return "[]"
        items = [_repr_value(item, indent + 4) for item in v]
        if len(items) == 1 and "\n" not in items[0]:
            return f"[{items[0]}]"
        pad = " " * (indent + 4)
        close_pad = " " * indent
        inner = (",\n" + pad).join(items)
        return f"[\n{pad}{inner},\n{close_pad}]"
    if isinstance(v, dict):
        if not v:
            return "{}"
        pairs = []
        for k, val in v.items():
            pairs.append(f'"{k}": {_repr_value(val, indent + 4)}')
        if len(pairs) == 1 and "\n" not in pairs[0]:
            return "{" + pairs[0] + "}"
        pad = " " * (indent + 4)
        close_pad = " " * indent
        inner = (",\n" + pad).join(pairs)
        return f"{{\n{pad}{inner},\n{close_pad}}}"
    # Fallback: use Python repr (covers bytes, tuples, etc.)
    return repr(v)


def _config_kwargs(config: dict[str, Any], exclude_keys: set[str] | None = None) -> str:
    """Return a string of ``key=value, ...`` kwargs for a ``@task`` decorator.

    Parameters
    ----------
    config:
        The task config dict.
    exclude_keys:
        Keys to omit (e.g. ``{"body"}`` for map nodes).
    """
    exclude = exclude_keys or set()
    parts: list[str] = []
    for k, v in config.items():
        if k in exclude:
            continue
        parts.append(f"{k}={_repr_value(v)}")
    return ", ".join(parts)


# ---------------------------------------------------------------------------
# Per-task snippet generators
# ---------------------------------------------------------------------------


def _task_to_sdk_call(
    task_dict: dict[str, Any],
    handle_map: dict[str, str],
    indent: str = "    ",
) -> str:
    """Emit a ``@task`` decorator + function stub + call expression.

    Parameters
    ----------
    task_dict:
        A TaskSpec serialised as dict (with ``key``, ``kind``, ``needs``,
        ``config`` fields).
    handle_map:
        Mapping of task_key → Python variable name for its handle.  Used
        to emit the correct handle names in the call expression.
    indent:
        Indentation string for the call line (default 4 spaces for inside a
        ``@flow`` function).

    Returns
    -------
    str
        Python source fragment for this task (decorator + stub + call line).
    """
    key: str = task_dict["key"]
    kind: str = task_dict["kind"]
    config: dict[str, Any] = task_dict.get("config", {})
    needs: list[str] = task_dict.get("needs", [])

    cfg_kwargs = _config_kwargs(config)
    task_decorator = f'@task(kind="{kind}"'
    if cfg_kwargs:
        task_decorator += f", {cfg_kwargs}"
    task_decorator += ")"

    fn_def = f"def {key}(): pass"

    # Build the call expression with upstream handles.
    upstream_handles = [handle_map[n] for n in needs if n in handle_map]
    call_args = ", ".join(upstream_handles)
    handle_var = f"{key}_handle"
    call_line = f"{indent}{handle_var} = {key}({call_args})"

    # The @task decorator and def go OUTSIDE the @flow (top-level).
    # The call line goes INSIDE the @flow body.
    # We return both parts separated by a sentinel so the caller can split them.
    return f"__DECORATOR__\n{task_decorator}\n{fn_def}\n__CALL__\n{call_line}"


def _map_task_to_sdk(
    task_dict: dict[str, Any],
    handle_map: dict[str, str],
    indent: str = "    ",
) -> str:
    """Emit a ``@map_node`` decorated inner function for a map TaskSpec.

    The body tasks are emitted as top-level ``@task`` definitions and inner
    call lines inside the body function.

    Parameters
    ----------
    task_dict:
        The map task dict (``kind="map"``).
    handle_map:
        Outer handle map (for upstream needs of the map node itself — usually
        empty since map nodes express upstream via ``item_expr``).
    indent:
        Indentation for the outer ``@map_node`` block (default 4 spaces).

    Returns
    -------
    str
        Python source fragment.  Contains two sections separated by
        ``__OUTER_DEFS__`` (top-level task decorators for body tasks) and
        ``__MAP_BLOCK__`` (the ``@map_node`` block inside ``@flow``).
    """
    key: str = task_dict["key"]
    config: dict[str, Any] = task_dict.get("config", {})
    item_expr: str = config.get("item_expr", "")
    item_var: str = config.get("item_var", "item")
    max_concurrency: int = config.get("max_concurrency", 0)
    max_map_size: int = config.get("max_map_size", 1000)
    collect_key: str | None = config.get("collect_key")
    body: list[dict[str, Any]] = config.get("body", [])

    # Build map_node decorator kwargs.
    mn_kwargs_parts = [
        f'    key="{key}"',
        f'    item_expr="{item_expr}"',
        f'    item_var="{item_var}"',
        f"    max_concurrency={max_concurrency}",
        f"    max_map_size={max_map_size}",
    ]
    if collect_key:
        mn_kwargs_parts.append(f'    collect_key="{collect_key}"')

    mn_kwargs = ",\n".join(mn_kwargs_parts)

    # Emit top-level @task defs for body tasks.
    body_topo = _toposort(body)
    outer_defs_lines: list[str] = []
    body_handle_map: dict[str, str] = {"__item__": "item"}

    for bt in body_topo:
        bkey = bt["key"]
        bkind = bt["kind"]
        bconfig = bt.get("config", {})
        cfg_kw = _config_kwargs(bconfig)
        dec_line = f'@task(kind="{bkind}"'
        if cfg_kw:
            dec_line += f", {cfg_kw}"
        dec_line += ")"
        outer_defs_lines.append(dec_line)
        outer_defs_lines.append(f"def {bkey}(): pass")
        outer_defs_lines.append("")
        body_handle_map[bkey] = f"{bkey}_handle"

    outer_defs = "\n".join(outer_defs_lines)

    # Emit body function call lines.
    body_indent = indent + "    "
    body_call_lines: list[str] = []
    # Determine the last body task key for the return statement.
    last_body_key = body_topo[-1]["key"] if body_topo else ""

    for bt in body_topo:
        bkey = bt["key"]
        bneeds = bt.get("needs", [])
        ups = [body_handle_map[n] for n in bneeds if n in body_handle_map]
        call_args = ", ".join(ups)
        handle_var = f"{bkey}_handle"
        body_handle_map[bkey] = handle_var
        if bkey == last_body_key:
            body_call_lines.append(f"{body_indent}return {bkey}({call_args})")
        else:
            body_call_lines.append(f"{body_indent}{handle_var} = {bkey}({call_args})")

    body_calls = "\n".join(body_call_lines)

    map_block = (
        f"{indent}@map_node(\n"
        f"{indent}{mn_kwargs},\n"
        f"{indent})\n"
        f"{indent}def {key}(item):\n"
        f"{body_calls}\n"
    )

    return f"__OUTER_DEFS__\n{outer_defs}\n__MAP_BLOCK__\n{map_block}"


def _branch_task_to_sdk(
    task_dict: dict[str, Any],
    handle_map: dict[str, str],
    indent: str = "    ",
) -> str:
    """Emit a ``branch_node(...)`` call for a branch TaskSpec.

    Parameters
    ----------
    task_dict:
        The branch task dict (``kind="branch"``).
    handle_map:
        Outer handle map.  The upstream handle (from ``needs[0]``) must be
        present.
    indent:
        Indentation for the call line.

    Returns
    -------
    str
        Python source fragment (no top-level defs — branch has no stub).
        Contains a single ``__CALL__`` section.
    """
    key: str = task_dict["key"]
    config: dict[str, Any] = task_dict.get("config", {})
    needs: list[str] = task_dict.get("needs", [])
    conditions: list[dict[str, Any]] = config.get("conditions", [])
    default: list[str] = config.get("default", [])

    upstream_var = handle_map.get(needs[0], f"{needs[0]}_handle") if needs else "upstream"
    handle_var = f"{key}_handle"

    conds_repr = _repr_value(conditions, indent=len(indent) + 4)
    default_repr = _repr_value(default)

    call_line = (
        f"{indent}{handle_var} = branch_node(\n"
        f"{indent}    {upstream_var},\n"
        f"{indent}    key={_repr_value(key)},\n"
        f"{indent}    conditions={conds_repr},\n"
        f"{indent}    default={default_repr},\n"
        f"{indent})"
    )

    return f"__CALL__\n{call_line}"


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def flow_spec_to_sdk(spec: Any) -> str:
    """Generate scaffold-grade Python SDK source from a FlowSpec.

    The generated source, when traced (i.e. compiled via ``.compile()``), must
    produce a FlowSpec whose ``tasks``, ``kinds``, ``configs``, ``needs``, and
    ``params`` match the input spec 1:1.  Layout (``ui.x``/``ui.y``) is NOT
    preserved in the generated code (it is a canvas concern, not a code concern).

    This function is scaffold-grade, NOT byte-preserving:

    - Variable names, import aliases, and spacing may differ from the original
      author's source.
    - Comments and docstrings are generated from task keys/kinds.
    - The output is valid, runnable Python that traces correctly.

    Parameters
    ----------
    spec:
        A validated FlowSpec instance (Pydantic model) or a raw dict with
        ``version``, ``name``, ``params``, and ``tasks`` fields.

    Returns
    -------
    str
        Python source code string.

    Examples
    --------
    Round-trip a FlowSpec::

        from nubi.flows._compile import flow_spec_to_sdk

        spec_dict = {
            "version": 1,
            "name": "daily_revenue",
            "params": [],
            "tasks": [
                {"key": "pull", "kind": "query", "needs": [],
                 "config": {"sql": "SELECT 1"},
                 "retries": 0, "retry_backoff_s": 30,
                 "timeout_s": 60, "cache_ttl_s": 0,
                 "ui": {"x": 0, "y": 0}},
            ],
        }
        src = flow_spec_to_sdk(spec_dict)
        # Execute src and call daily_revenue.compile() to get the spec back.
    """
    # Accept both Pydantic FlowSpec and raw dicts.
    if hasattr(spec, "model_dump"):
        spec_dict: dict[str, Any] = spec.model_dump()
    elif hasattr(spec, "dict"):
        spec_dict = spec.dict()
    else:
        spec_dict = dict(spec)

    flow_name: str = spec_dict.get("name", "my_flow")
    params: list[dict[str, Any]] = spec_dict.get("params", []) or []
    tasks: list[dict[str, Any]] = spec_dict.get("tasks", []) or []

    topo_tasks = _toposort(tasks)

    # Build handle_map: task_key → Python variable name.
    handle_map: dict[str, str] = {}
    for t in topo_tasks:
        handle_map[t["key"]] = f"{t['key']}_handle"

    # Categorise tasks.
    map_tasks = {t["key"] for t in topo_tasks if t.get("kind") == "map"}
    branch_tasks = {t["key"] for t in topo_tasks if t.get("kind") == "branch"}

    # ── Collect top-level @task definitions ──────────────────────────────────
    top_level_defs: list[str] = []
    # Track body-task keys to avoid re-emitting them as top-level stubs.
    map_body_keys: set[str] = set()
    for t in topo_tasks:
        if t.get("kind") == "map":
            for bt in t.get("config", {}).get("body", []):
                map_body_keys.add(bt["key"])

    for t in topo_tasks:
        kind = t.get("kind", "noop")
        key = t["key"]
        if key in map_tasks or key in branch_tasks:
            continue  # map/branch don't need top-level stubs
        config = t.get("config", {})
        cfg_kw = _config_kwargs(config)
        dec = f'@task(kind="{kind}"'
        if cfg_kw:
            dec += f", {cfg_kw}"
        dec += ")"
        top_level_defs.append(dec)
        top_level_defs.append(f"def {key}(): pass")
        top_level_defs.append("")

    # ── Collect body-task @task definitions (for map inner bodies) ───────────
    # These are collected inline inside _map_task_to_sdk and returned separately.
    # We gather them here to prepend above the @flow definition.
    map_body_defs: list[str] = []

    # ── Build @flow body lines ────────────────────────────────────────────────
    flow_body_lines: list[str] = []
    indent = "    "

    for t in topo_tasks:
        kind = t.get("kind", "noop")
        key = t["key"]
        needs = t.get("needs", [])

        if key in map_tasks:
            fragment = _map_task_to_sdk(t, handle_map, indent)
            parts = fragment.split("__OUTER_DEFS__\n", 1)
            if len(parts) == 2:
                rest = parts[1]
                outer_def_part, rest2 = rest.split("__MAP_BLOCK__\n", 1)
                map_body_defs.append(outer_def_part.rstrip())
                map_body_defs.append("")
                flow_body_lines.append(rest2.rstrip())
            else:
                flow_body_lines.append(fragment)
            # Register the map handle and the collect handle.
            handle_map[key] = f"{key}"  # the MapBodyHandle is bound as `key`
        elif key in branch_tasks:
            fragment = _branch_task_to_sdk(t, handle_map, indent)
            call_line = fragment.split("__CALL__\n", 1)[-1]
            flow_body_lines.append(call_line.rstrip())
            handle_map[key] = f"{key}_handle"
        else:
            upstream_handles = [handle_map[n] for n in needs if n in handle_map]
            call_args = ", ".join(upstream_handles)
            handle_var = f"{key}_handle"
            handle_map[key] = handle_var
            call = f"{indent}{handle_var} = {key}({call_args})"
            flow_body_lines.append(call)

    if not flow_body_lines:
        flow_body_lines.append(f"{indent}pass")

    # ── Build params for compile() call ──────────────────────────────────────
    param_kwargs_parts: list[str] = []
    for p in params:
        pname = p.get("name", "")
        ptype = p.get("type", "text")
        pdefault = p.get("default")
        prequired = p.get("required", False)
        if ptype == "text" and not prequired:
            param_kwargs_parts.append(f"{pname}={_repr_value(pdefault)}")
        else:
            parts_inner = [f'type="{ptype}"']
            if pdefault is not None:
                parts_inner.append(f"default={_repr_value(pdefault)}")
            if prequired:
                parts_inner.append("required=True")
            param_kwargs_parts.append(f"{pname}=FlowParam({', '.join(parts_inner)})")

    param_kwargs = ", ".join(param_kwargs_parts)
    compile_call = f"spec = {flow_name}.compile({param_kwargs})"

    # ── Assemble the source ───────────────────────────────────────────────────
    lines: list[str] = [
        f"# Auto-generated scaffold from FlowSpec {flow_name!r}",
        "# Edit task configs; do not restructure the graph here"
        " — use the canvas or recompile.",
        "",
        "from nubi.flows import flow, task, map_node, branch_node, FlowParam",
        "",
    ]

    # Top-level @task stubs (non-map, non-branch).
    if top_level_defs:
        lines.extend(top_level_defs)

    # Body-task @task stubs (from map inner bodies).
    if map_body_defs:
        for line in map_body_defs:
            lines.append(line)
        lines.append("")

    # @flow definition.
    lines.append("@flow")
    lines.append(f"def {flow_name}():")
    for fl in flow_body_lines:
        lines.append(fl)

    lines.append("")
    lines.append(compile_call)

    return "\n".join(lines) + "\n"
