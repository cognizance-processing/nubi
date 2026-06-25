"""Model/Dataset DAG -- inter-model dependency graph (A.2).

Public API
----------
build_dag(queries, metrics) -> DependencyDAG
    Analyse every registered query and metric, extract the tables/columns each
    one produces (outputs) and consumes (input tables/columns), then wire up
    producer->consumer edges: if node B reads a table/output that node A produces,
    add edge A->B.

DependencyDAG
    Holds nodes (queries + metrics + bare tables) and edges.  Use
    ``upstream(node_id, hops)`` / ``downstream(node_id, hops)`` to walk the
    DAG N hops in either direction.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Any

from app.lineage.extract import extract_lineage
from app.metrics.models import MetricDefinition
from app.queries.registry import RegisteredQuery


# ---------------------------------------------------------------------------
# Node / Edge types
# ---------------------------------------------------------------------------

NodeType = str  # "query" | "metric" | "table"


@dataclass
class DAGNode:
    """A single node in the dependency DAG."""

    id: str
    type: NodeType
    name: str
    tables: list[str] = field(default_factory=list)
    outputs: list[str] = field(default_factory=list)
    columns: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "id": self.id,
            "type": self.type,
            "name": self.name,
            "tables": self.tables,
            "outputs": self.outputs,
            "columns": self.columns,
        }
        if self.error is not None:
            d["error"] = self.error
        return d


@dataclass
class DAGEdge:
    """A directed dependency edge: from_id -> to_id via a table/output name."""

    from_id: str
    to_id: str
    via: str

    def to_dict(self) -> dict[str, Any]:
        return {"from": self.from_id, "to": self.to_id, "via": self.via}


# ---------------------------------------------------------------------------
# DependencyDAG
# ---------------------------------------------------------------------------


@dataclass
class DependencyDAG:
    """Full inter-model dependency graph."""

    nodes: dict[str, DAGNode] = field(default_factory=dict)
    edges: list[DAGEdge] = field(default_factory=list)

    def upstream(self, node_id: str, hops: int = 3) -> list[str]:
        """Return sorted ids of nodes upstream of node_id within hops."""
        hops = min(hops, 20)
        rev: dict[str, set[str]] = {}
        for edge in self.edges:
            rev.setdefault(edge.to_id, set()).add(edge.from_id)

        visited: set[str] = set()
        q: deque[tuple[str, int]] = deque([(node_id, 0)])
        while q:
            cur, depth = q.popleft()
            if depth >= hops:
                continue
            for parent in rev.get(cur, set()):
                if parent not in visited and parent != node_id:
                    visited.add(parent)
                    q.append((parent, depth + 1))
        return sorted(visited)

    def downstream(self, node_id: str, hops: int = 3) -> list[str]:
        """Return sorted ids of nodes downstream of node_id within hops."""
        hops = min(hops, 20)
        fwd: dict[str, set[str]] = {}
        for edge in self.edges:
            fwd.setdefault(edge.from_id, set()).add(edge.to_id)

        visited: set[str] = set()
        q: deque[tuple[str, int]] = deque([(node_id, 0)])
        while q:
            cur, depth = q.popleft()
            if depth >= hops:
                continue
            for child in fwd.get(cur, set()):
                if child not in visited and child != node_id:
                    visited.add(child)
                    q.append((child, depth + 1))
        return sorted(visited)

    def to_dict(self) -> dict[str, Any]:
        return {
            "nodes": [n.to_dict() for n in self.nodes.values()],
            "edges": [e.to_dict() for e in self.edges],
        }


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------


def _node_for_query(rq: RegisteredQuery) -> DAGNode:
    """Extract lineage for a registered query and return a DAGNode."""
    lineage = extract_lineage(rq.sql)
    node = DAGNode(
        id=rq.id,
        type="query",
        name=rq.name,
        tables=lineage["tables"],
        outputs=lineage["outputs"],
        columns=lineage["columns"],
    )
    if "error" in lineage:
        node.error = lineage["error"]
    return node


def _node_for_metric(metric: MetricDefinition) -> DAGNode:
    """Extract lineage for a metric and return a DAGNode."""
    if metric.base_sql:
        lineage = extract_lineage(metric.base_sql)
        tables = lineage["tables"]
        columns = lineage["columns"]
        outputs = lineage["outputs"]
        error: str | None = lineage.get("error")
    elif metric.base_table:
        tables = [metric.base_table]
        columns = []
        outputs = []
        error = None
    else:
        tables = []
        columns = []
        outputs = []
        error = "no_source"

    # A metric exposes its measure/dimension names as logical outputs so
    # downstream queries that reference this metric by name can be wired up.
    measure_outputs = [m.name for m in metric.measures()]
    derived_outputs = [dm.name for dm in metric.derived_measures]
    dim_outputs = [d.name for d in metric.dimensions]
    all_outputs = sorted(
        set(outputs) | set(measure_outputs) | set(derived_outputs) | set(dim_outputs)
    )

    return DAGNode(
        id=metric.id,
        type="metric",
        name=metric.name,
        tables=tables,
        outputs=all_outputs,
        columns=columns,
        error=error,
    )


def build_dag(
    queries: list[RegisteredQuery],
    metrics: list[MetricDefinition],
) -> DependencyDAG:
    """Build the inter-model dependency DAG.

    Algorithm
    ---------
    1. Build a DAGNode per query and metric.
    2. Collect all physical tables consumed by any node -> bare ``type="table"``
       leaf nodes.
    3. Wire producer->consumer edges:
       - If node A's *outputs* contain a name that node B *consumes as a table*,
         add edge A->B (derived/virtual dependency).
       - If node B consumes a bare physical table T with no model producer,
         add edge T->B (physical table dependency).

    Parameters
    ----------
    queries:
        List of ``RegisteredQuery`` objects visible to the caller's org.
    metrics:
        List of ``MetricDefinition`` objects visible to the caller's org.

    Returns
    -------
    DependencyDAG
        Fully populated DAG.
    """
    dag = DependencyDAG()

    # 1. Build model nodes
    for rq in queries:
        node = _node_for_query(rq)
        dag.nodes[node.id] = node

    for metric in metrics:
        node = _node_for_metric(metric)
        dag.nodes[node.id] = node

    # 2. Build output-producer index: output_name -> [producer_node_id, ...]
    output_producers: dict[str, list[str]] = {}
    for node in dag.nodes.values():
        for out in node.outputs:
            output_producers.setdefault(out, []).append(node.id)

    # 3. Collect all consumed tables; add bare table leaf nodes
    all_consumed: set[str] = set()
    for node in dag.nodes.values():
        for t in node.tables:
            all_consumed.add(t)

    for table_name in sorted(all_consumed):
        if table_name not in dag.nodes and table_name not in output_producers:
            dag.nodes[table_name] = DAGNode(
                id=table_name,
                type="table",
                name=table_name,
            )

    # 4. Wire edges
    seen_edges: set[tuple[str, str, str]] = set()

    def _add_edge(from_id: str, to_id: str, via: str) -> None:
        key = (from_id, to_id, via)
        if key not in seen_edges:
            seen_edges.add(key)
            dag.edges.append(DAGEdge(from_id=from_id, to_id=to_id, via=via))

    for consumer in dag.nodes.values():
        if consumer.type == "table":
            continue
        for consumed_table in consumer.tables:
            # Case A: a model node produces this name as an output
            producers = output_producers.get(consumed_table, [])
            for producer_id in producers:
                if producer_id != consumer.id:
                    _add_edge(producer_id, consumer.id, consumed_table)
            # Case B: bare physical table leaf node
            if not producers and consumed_table in dag.nodes:
                _add_edge(consumed_table, consumer.id, consumed_table)

    return dag


# ---------------------------------------------------------------------------
# Metric input-column resolver (A.4)
# ---------------------------------------------------------------------------


def resolve_metric_lineage(metric: MetricDefinition) -> dict[str, Any]:
    """Resolve a metric's full lineage: input columns + upstream source tables.

    Uses ``extract_lineage`` on ``base_sql`` (when present) or derives the
    single-column lineage from ``base_table`` + measure ``expr``.

    Returns
    -------
    dict
        ``{metric_id, name, measure, formula, input_columns, upstream}``
    """
    if metric.base_sql:
        lineage = extract_lineage(metric.base_sql)
        upstream_tables: list[str] = lineage["tables"]
        input_columns: list[dict[str, Any]] = lineage["columns"]
        parse_error: str | None = lineage.get("error")
    elif metric.base_table:
        upstream_tables = [metric.base_table]
        measure_expr = metric.measure.expr
        if measure_expr and measure_expr != "*":
            input_columns = [{"table": metric.base_table, "column": measure_expr}]
        else:
            input_columns = []
        parse_error = None
    else:
        upstream_tables = []
        input_columns = []
        parse_error = "no_source"

    formula = [
        {"name": dm.name, "formula": dm.formula}
        for dm in metric.derived_measures
    ]

    result: dict[str, Any] = {
        "metric_id": metric.id,
        "name": metric.name,
        "measure": {
            "name": metric.measure.name,
            "agg": metric.measure.agg,
            "expr": metric.measure.expr,
        },
        "formula": formula,
        "input_columns": input_columns,
        "upstream": upstream_tables,
    }
    if parse_error:
        result["error"] = parse_error
    return result
