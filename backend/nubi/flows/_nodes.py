"""Node handle types for the flows tracing DSL.

During flow tracing (inside a ``@flow``-decorated function body), task calls
return ``NodeHandle`` objects instead of real data.  These handles record
the logical data-dependency graph while the function body executes.

Passing a ``NodeHandle`` as an argument to another task call inside the same
``@flow`` body registers a directed edge in the DAG.

Public API
----------
NodeHandle
    Symbolic reference to the output of a task node.  Carries the ``key``
    (task key string) and ``port`` (output port name, always ``"default"``
    until named multi-output support lands).

MapBodyHandle
    Subclass of ``NodeHandle`` for map (fan-out) nodes.  Adds a
    ``.collect()`` method that returns a NodeHandle representing the
    aggregated fan-in result.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class NodeHandle:
    """Symbolic reference to a task output during flow tracing.

    During tracing, task calls return ``NodeHandle`` objects instead of real
    data.  Passing a ``NodeHandle`` as an argument to another task call
    records an edge in the active ``_TraceContext``.

    Attributes
    ----------
    key:
        The task key that produced this output (e.g. ``"get_regions"``).
    port:
        Output port name.  Always ``"default"`` until named multi-output
        support lands in a future version.

    Examples
    --------
    ::

        @flow
        def pipeline():
            data = pull()          # returns NodeHandle(key="pull")
            result = transform(data)  # registers edge pull → transform
    """

    key: str
    port: str = "default"


@dataclass
class MapBodyHandle(NodeHandle):
    """NodeHandle for a map (fan-out) node; adds ``.collect()`` for fan-in.

    A ``MapBodyHandle`` is returned by the ``@map_node`` decorator.  Call
    ``.collect()`` to get a ``NodeHandle`` that represents the aggregated
    results list produced after all map-item child tasks complete.

    Attributes
    ----------
    key:
        The task key of the map node (e.g. ``"per_region"``).
    port:
        Inherited from ``NodeHandle``; always ``"default"`` for the raw map
        handle.

    Examples
    --------
    ::

        @flow
        def my_flow():
            @map_node(key="per_region", item_expr="{{ inputs.src.rows }}")
            def per_region(item):
                return process(item)

            aggregate(per_region.collect())   # fan-in edge
    """

    def collect(self) -> NodeHandle:
        """Return a NodeHandle representing the collected fan-in output.

        The returned handle carries ``port="collected"`` to distinguish the
        aggregated result from any raw intermediate handle.

        Returns
        -------
        NodeHandle
            Handle pointing to this map node's collected output.
        """
        return NodeHandle(key=self.key, port="collected")
