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

resolve_column_lineage(dag, node_id, column, max_hops) -> list[dict]
    Walk the DAG upstream following column provenance across model layers.
    Returns a list of column-path hops:
        [{"node": node_id, "column": col, "alias": bool}, ...]
    from the starting node back to the physical source, handling aliases/renames
    at each hop.  Falls back to table-level edges for SELECT * layers (marked with
    ``"select_star": True``).  Cap: max_hops (default 10, absolute ceiling 20).
"""

from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from app.lineage.extract import extract_lineage
from app.metrics.models import MetricDefinition
from app.queries.registry import RegisteredQuery

logger = logging.getLogger(__name__)


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
    # Raw SQL of the model, used by column-lineage resolver to trace aliases.
    sql: str | None = None
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
        sql=rq.sql,
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
        sql: str | None = metric.base_sql
    elif metric.base_table:
        tables = [metric.base_table]
        columns = []
        outputs = []
        error = None
        sql = None
    else:
        tables = []
        columns = []
        outputs = []
        error = "no_source"
        sql = None

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
        sql=sql,
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
# Cross-model column lineage resolver (Feature A)
# ---------------------------------------------------------------------------

_MAX_HOPS_CEILING = 20


def _build_column_alias_map(sql: str) -> dict[str, str]:
    """Parse *sql* and return a mapping output_alias → source_column.

    E.g. ``SELECT amount AS revenue FROM …`` produces ``{"revenue": "amount"}``.
    For bare column references (no alias) the mapping is identity:
    ``{"amount": "amount"}``.

    Returns an empty dict on any parse failure so callers degrade gracefully.
    """
    try:
        import sqlglot.expressions as exp  # noqa: PLC0415
        import sqlglot  # noqa: PLC0415

        tree = sqlglot.parse_one(sql)
        if not isinstance(tree, exp.Select):
            return {}

        alias_map: dict[str, str] = {}
        for expr in tree.expressions:
            if isinstance(expr, exp.Alias):
                # SELECT <something> AS <alias>
                alias_name = expr.alias
                inner = expr.this
                if isinstance(inner, exp.Column):
                    alias_map[alias_name.lower()] = inner.name.lower()
                elif alias_name:
                    # Complex expression with alias — no reliable source column;
                    # map alias to itself so callers know the column exists.
                    alias_map[alias_name.lower()] = alias_name.lower()
            elif isinstance(expr, exp.Column):
                col_name = expr.name.lower()
                if col_name and col_name != "*":
                    alias_map[col_name] = col_name
            elif isinstance(expr, exp.Star):
                # SELECT * — sentinel value so callers know to fall back to
                # table-level edge.
                alias_map["*"] = "*"
        return alias_map
    except Exception:  # noqa: BLE001
        return {}


def _upstream_nodes_for(dag: DependencyDAG, node_id: str) -> list[str]:
    """Return direct upstream neighbour IDs for *node_id*."""
    return [e.from_id for e in dag.edges if e.to_id == node_id]


def resolve_column_lineage(
    dag: DependencyDAG,
    node_id: str,
    column: str,
    max_hops: int = 10,
) -> list[dict[str, Any]]:
    """Walk the DAG upstream following column provenance across model layers.

    Starting from *node_id* / *column*, trace the column back through each
    upstream model layer, resolving aliases and renames at each hop, until
    reaching a physical source table (type="table") or exhausting *max_hops*.

    Algorithm
    ---------
    At each node we inspect the model's SQL (when available) to map the
    current column name to its source column in the upstream model:

    1.  Parse the node's SQL with ``_build_column_alias_map``.
    2.  If the current column maps to a source column (possibly the same name),
        carry that source column into the upstream node.
    3.  If the node uses ``SELECT *``, fall back to table-level edge (the
        column name is assumed unchanged) and mark the hop as ``select_star=True``.
    4.  If no SQL is available (e.g. a metric with only ``base_table``),
        carry the column name unchanged.

    Cycle guard: a visited set of ``(node_id, column)`` pairs prevents loops.
    Depth guard: the walk stops at ``min(max_hops, _MAX_HOPS_CEILING)`` hops.

    Parameters
    ----------
    dag:
        Fully built ``DependencyDAG`` (from ``build_dag``).
    node_id:
        Starting node id (the downstream model or metric).
    column:
        The output column name to trace from *node_id* backwards.
    max_hops:
        Maximum number of hops upstream (default 10; ceiling 20).

    Returns
    -------
    list[dict]
        Ordered list from the starting node to the source, each entry::

            {
                "node":        str,   # DAG node id
                "column":      str,   # column name at this node
                "select_star": bool,  # True when this hop was a SELECT * pass-through
                "alias":       bool,  # True when the column name differs from the previous hop
            }

        Returns ``[{"node": node_id, "column": column, ...}]`` (single entry)
        when the node has no upstream or is already a physical table.
        Returns ``[]`` when *node_id* is not in the DAG.
    """
    if node_id not in dag.nodes:
        return []

    effective_hops = min(max_hops, _MAX_HOPS_CEILING)

    # Path built up so far (returned to caller).
    path: list[dict[str, Any]] = []

    # Visited guard: set of (node_id, column) tuples.
    visited: set[tuple[str, str]] = set()

    cur_node_id = node_id
    cur_column = column.lower()

    for _ in range(effective_hops + 1):
        if (cur_node_id, cur_column) in visited:
            # Cycle detected — stop.
            break
        visited.add((cur_node_id, cur_column))

        node = dag.nodes.get(cur_node_id)
        if node is None:
            break

        # Determine whether this hop is an alias/rename relative to previous.
        is_alias = (
            len(path) > 0 and path[-1]["column"] != cur_column
        )

        path.append({
            "node": cur_node_id,
            "column": cur_column,
            "select_star": False,
            "alias": is_alias,
        })

        # Physical table — we've reached the source; stop.
        if node.type == "table":
            break

        # Find direct upstream nodes.
        upstream_ids = _upstream_nodes_for(dag, cur_node_id)
        if not upstream_ids:
            # No upstream — this is a root model.
            break

        # Resolve column provenance through this node's SQL.
        if node.sql:
            alias_map = _build_column_alias_map(node.sql)

            if "*" in alias_map and cur_column not in alias_map:
                # SELECT * layer — column passes through unchanged.
                path[-1]["select_star"] = True
                # Take the first upstream node (table-level fallback).
                cur_node_id = upstream_ids[0]
                # cur_column stays the same.
                continue

            source_col = alias_map.get(cur_column)
            if source_col is None:
                # Column not found in this node's SELECT list — stop tracing.
                break

            # Walk into the first upstream node (single-parent assumption;
            # for multi-parent we take the first for determinism).
            cur_column = source_col
            cur_node_id = upstream_ids[0]
        else:
            # No SQL available (e.g. metric with base_table only) — column
            # name is assumed unchanged; walk upstream.
            cur_node_id = upstream_ids[0]

    return path


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
