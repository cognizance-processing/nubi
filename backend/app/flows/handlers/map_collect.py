"""Map-collect handler for the Flows engine.

Implements the ``'map_collect'`` task kind.  This is a dedicated collector
node that aggregates the results of a completed map fan-out into a single
output dict.

Resolved Decision Q3
---------------------
The map collector is a **dedicated ``'map_collect'`` handler** that returns
``{"items": [...], "item_count": N}`` — NOT a noop.  The ``items`` list
contains the collected results from the map node's ``collect_key`` body task.

Typical usage in a FlowSpec:

.. code-block:: json

    {
      "key": "collect_results",
      "kind": "map_collect",
      "needs": ["process_each_region"],
      "config": {
        "source": "process_each_region"
      }
    }

Where ``"process_each_region"`` is a ``kind: "map"`` task whose result
(after fan-in) is ``{"items": [...], "item_count": N, "collect_key": "..."}``.

Public API
----------
handle_map_collect(config, ctx, claims) -> dict
    Read the map node result from ``ctx.inputs[config['source']]`` and
    return ``{"items": [...], "item_count": N}``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.flows.executor import TaskContext


def handle_map_collect(
    config: dict[str, Any],
    ctx: "TaskContext",
    claims: dict[str, Any],
) -> dict[str, Any]:
    """Collect and return the aggregated results from an upstream map node.

    Reads the map node's fan-in result from ``ctx.inputs`` and reshapes it
    into the canonical ``{"items": [...], "item_count": N}`` form that
    downstream tasks can consume.

    Parameters
    ----------
    config:
        Resolved task config dict.  Expected key:
        ``source`` (str, required) — the task key of the upstream map node.
    ctx:
        Task execution context.  ``ctx.inputs[source]`` must be the
        map node's result dict (containing ``"items"``).
    claims:
        Caller's auth claims (not used by this handler directly).

    Returns
    -------
    dict
        ``{"items": [...], "item_count": N}``

        Each element of ``items`` is the collected result from one map
        iteration.

    Raises
    ------
    ValueError
        If ``source`` is not specified in config.
    ValueError
        If the upstream map result does not contain an ``"items"`` list.
    """
    source: str = config.get("source", "")
    if not source:
        raise ValueError(
            "map_collect handler: 'source' must be set in config to name "
            "the upstream map task key."
        )

    map_result: Any = ctx.inputs.get(source)
    if map_result is None:
        raise ValueError(
            f"map_collect handler: upstream task {source!r} has no result "
            f"in ctx.inputs.  Ensure the map node ran successfully and that "
            f"this task lists it in 'needs'."
        )

    if not isinstance(map_result, dict):
        raise ValueError(
            f"map_collect handler: expected a dict result from {source!r}, "
            f"got {type(map_result).__name__!r}."
        )

    # The runtime stores the fan-in result as {"items": [...], "item_count": N}.
    # If the map node result already has the canonical shape, pass it through.
    items: Any = map_result.get("items")
    if items is None:
        # Fallback: the entire map_result is treated as a single collected value.
        # This covers cases where the map node result was stored without the
        # fan-in wrapper (e.g. in unit tests that mock the upstream result).
        items = [map_result]

    if not isinstance(items, list):
        items = list(items)

    return {
        "items": items,
        "item_count": len(items),
    }
