"""Nubi Python SDK — public package root.

Exposes the flows tracing DSL at the top level for convenience:

    from nubi.flows import flow, task, map_node, branch_node, FlowParam

Or import the sub-package directly:

    from nubi import flows
    spec = flows.compile_flow(my_flow_fn)
"""

from __future__ import annotations

from nubi.flows import (  # noqa: F401
    FlowParam,
    MapBodyHandle,
    NodeHandle,
    branch_node,
    flow,
    map_node,
    task,
)

__version__ = "0.1.0"
__all__ = [
    "flow",
    "task",
    "map_node",
    "branch_node",
    "NodeHandle",
    "MapBodyHandle",
    "FlowParam",
]
