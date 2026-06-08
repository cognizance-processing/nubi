"""Map (fan-out) handler for the Flows engine.

Implements the ``'map'`` task kind.  The handler resolves the item expression,
validates the item count, and returns a sentinel dict that the runtime
(Agent C/D) uses to expand child task_runs for each item × body task.

The handler itself is synchronous and does NOT create task_runs directly.
The runtime detects ``'__map_items__'`` in the result and performs the fan-out.

Public API
----------
handle_map(config, ctx, claims) -> dict
    Resolve ``config['item_expr']`` against ``ctx`` and return
    ``{"__map_items__": items, "item_count": N}``.

    The runtime must:
    - detect ``kind == 'map'`` and result containing ``'__map_items__'``
    - create child task_runs for each item × each body task
    - transition this task_run to ``'waiting_children'``
    - call ``advance_readiness`` to process the child task_runs
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.flows.executor import TaskContext


# ---------------------------------------------------------------------------
# Internal: native value resolution (returns the Python object, not str)
# ---------------------------------------------------------------------------

#: Matches a single ``{{ some.dotted.path }}`` expression (full-string match).
_FULL_TEMPLATE_RE = re.compile(r"^\{\{\s*([\w.]+)\s*\}\}$")


def _resolve_native(expr: str, ctx: "TaskContext") -> Any:
    """Resolve a template expression to its native Python value.

    Unlike ``_resolve_string`` in executor.py (which coerces everything to
    ``str``), this function returns the raw Python object — a list, dict, int,
    etc. — so the map handler can iterate over it.

    Only single ``{{ path }}`` expressions (filling the whole string) return
    their native value.  Composite strings like ``"{{ a }} and {{ b }}"`` are
    resolved as strings (``str`` coercion applies).

    Parameters
    ----------
    expr:
        A template string such as ``"{{ inputs.get_regions.rows }}"``.
    ctx:
        The task execution context.

    Returns
    -------
    Any
        The native Python value, or the string-resolved form for composites.
    """
    from app.flows.executor import _resolve_string  # noqa: PLC0415

    m = _FULL_TEMPLATE_RE.match(expr.strip())
    if not m:
        # Composite or plain string — fall back to string resolution.
        return _resolve_string(expr, ctx)

    path = m.group(1)
    parts = path.split(".")
    if not parts:
        return None

    namespace = parts[0]
    rest = parts[1:]

    if namespace == "params":
        if not rest:
            return None
        val: Any = ctx.flow_params.get(rest[0])
        for key in rest[1:]:
            if isinstance(val, dict):
                val = val.get(key)
            else:
                return val
        return val

    if namespace == "inputs":
        if not rest:
            return None
        task_key = rest[0]
        val = ctx.inputs.get(task_key, {})
        for key in rest[1:]:
            if isinstance(val, dict):
                val = val.get(key)
            else:
                return val
        return val

    # Unknown namespace — return empty string (soft failure).
    return ""


# ---------------------------------------------------------------------------
# handle_map
# ---------------------------------------------------------------------------


def handle_map(
    config: dict[str, Any],
    ctx: "TaskContext",
    claims: dict[str, Any],
) -> dict[str, Any]:
    """Resolve the item expression and return the items list.

    The runtime (``_execute_claimed_task_run``) detects the ``'map'`` kind
    and expands child task_runs from this result.

    Parameters
    ----------
    config:
        Resolved task config dict.  Must contain ``'item_expr'`` and
        ``'body'`` keys.  Optional: ``'max_map_size'`` (default 1000).
    ctx:
        Task execution context.
    claims:
        Caller's auth claims (not used by this handler directly).

    Returns
    -------
    dict
        ``{"__map_items__": items, "item_count": N}``

        The runtime reads ``__map_items__`` and creates child task_runs.
        The sentinel key is stripped from the final task_run result after
        the runtime processes it.

    Raises
    ------
    ValueError
        If ``item_expr`` does not resolve to a list/tuple.
    ValueError
        If the resolved item count exceeds ``max_map_size``.
    """
    item_expr: str = config.get("item_expr", "")
    if not item_expr:
        raise ValueError("map handler: 'item_expr' must be set in config.")

    # Resolve the expression to its native Python value.
    resolved = _resolve_native(item_expr, ctx)

    # If resolution returned a string (e.g. a composite expr), try to parse
    # it as a JSON list.  This handles edge cases where the template is
    # embedded in a larger string.
    if isinstance(resolved, str):
        import json  # noqa: PLC0415
        try:
            resolved = json.loads(resolved)
        except (json.JSONDecodeError, ValueError):
            pass

    if not isinstance(resolved, (list, tuple)):
        raise ValueError(
            f"map handler: 'item_expr' must resolve to a list; "
            f"got {type(resolved).__name__!r} (value: {resolved!r})."
        )

    items = list(resolved)
    max_map_size: int = int(config.get("max_map_size", 1000) or 1000)
    if len(items) > max_map_size:
        raise ValueError(
            f"map handler: fan-out of {len(items)} items exceeds "
            f"max_map_size={max_map_size}."
        )

    return {"__map_items__": items, "item_count": len(items)}
