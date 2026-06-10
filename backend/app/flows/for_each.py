"""``for_each`` synthetic-map adapter for the Flows engine.

A SQL or Python cell may carry a ``config.for_each`` block::

    config.for_each = {
        "items": "<expr or upstream ref>",  # must resolve to a list
        "var": "item",                       # default 'item'
        "max_concurrency": 0,                # default 0 = unlimited
    }

This REPLACES the standalone ``map`` kind for authoring: the cell's OWN body
(its ``sql`` / ``code``) IS the per-item body — there is no separate ``body``
array.  At run time the runtime rewrites the cell into a legacy ``map`` task so
the EXISTING fan-out machinery (``handle_map`` + ``_expand_map_children`` +
``_collect_map_results``) runs unchanged.

The synthetic map config maps the ``for_each`` fields onto the legacy map
config and synthesises a single body task keyed ``'__self__'`` that is the cell
itself (minus the ``for_each`` block, to avoid recursive fan-out)::

    {
        "item_expr": for_each.items,
        "item_var": for_each.var,
        "max_concurrency": for_each.max_concurrency,
        "max_map_size": 1000,
        "collect_key": "__self__",
        "body": [{"key": "__self__", "kind": <cell kind>, "config": <cell config minus for_each>, ...}],
    }

Child task keys become ``"{cell}[{i}].__self__"``; the runtime reconstructs the
synthetic body deterministically (no store persistence) when resolving those
child task specs.

Public API
----------
get_for_each(task) -> dict | None
    Return the ``for_each`` config block if present and non-empty, else ``None``.

to_map_config(task) -> dict
    Build the legacy-``map`` config from a cell carrying ``for_each``.
"""

from __future__ import annotations

from typing import Any

#: The synthetic body task key (and collect_key) for a for_each cell.
SELF_BODY_KEY = "__self__"

#: Default fan-out ceiling (mirrors the legacy map ``max_map_size`` default).
DEFAULT_MAX_MAP_SIZE = 1000


def get_for_each(task: dict[str, Any]) -> dict[str, Any] | None:
    """Return the ``for_each`` config block if present and usable, else ``None``.

    A block is "usable" when it is a dict carrying a non-empty ``items``
    expression.  An absent / empty / malformed block ⇒ ``None`` (the cell runs
    normally with no fan-out).
    """
    config = task.get("config") or {}
    fe = config.get("for_each")
    if not isinstance(fe, dict):
        return None
    items = fe.get("items")
    if not items or not isinstance(items, str) or not items.strip():
        return None
    return fe


def to_map_config(task: dict[str, Any]) -> dict[str, Any]:
    """Build a legacy-``map`` config dict from a cell carrying ``for_each``.

    The cell's own kind + config (minus the ``for_each`` block) becomes the
    single ``'__self__'`` body task.  The ``for_each`` fields map onto the
    legacy map config keys.

    Parameters
    ----------
    task:
        The full task dict (cell) — must carry a usable ``config.for_each``
        block and the cell's own ``kind`` + ``config`` (``sql`` / ``code``).

    Returns
    -------
    dict
        A legacy-map config dict suitable for ``handle_map`` +
        ``_expand_map_children``.
    """
    config: dict[str, Any] = dict(task.get("config") or {})
    fe: dict[str, Any] = dict(config.get("for_each") or {})

    items = str(fe.get("items") or "")
    item_var = str(fe.get("var") or "item") or "item"
    try:
        max_concurrency = int(fe.get("max_concurrency", 0) or 0)
    except (TypeError, ValueError):
        max_concurrency = 0

    # The body task is the cell itself, minus the for_each block (so the
    # synthesised body does not recursively fan out).  run_when is also dropped
    # from the body — the gate is evaluated once on the parent cell before
    # fan-out, never per item.
    body_config = {
        k: v for k, v in config.items() if k not in ("for_each", "run_when")
    }

    body_task: dict[str, Any] = {
        "key": SELF_BODY_KEY,
        "kind": task.get("kind", "noop"),
        "needs": [],
        "config": body_config,
        "retries": task.get("retries", 0),
        "retry_backoff_s": task.get("retry_backoff_s", 30),
        "timeout_s": task.get("timeout_s", 60),
        "cache_ttl_s": task.get("cache_ttl_s", 0),
    }

    map_config: dict[str, Any] = {
        "item_expr": items,
        "item_var": item_var,
        "max_concurrency": max_concurrency,
        "max_map_size": DEFAULT_MAX_MAP_SIZE,
        "collect_key": SELF_BODY_KEY,
        "body": [body_task],
    }

    # Preserve the cell's run_when at the synthetic-map TOP LEVEL so the gate
    # still fires ONCE before fan-out (precedence: run_when → for_each →
    # materialized).  It is dropped from the per-item body (above) so each item
    # is not re-gated.
    run_when = config.get("run_when")
    if run_when is not None and str(run_when).strip():
        map_config["run_when"] = run_when

    return map_config
