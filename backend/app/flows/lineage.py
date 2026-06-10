"""Column-level lineage across SQL cells in a FlowSpec / NotebookSpec.

Public API
----------
extract_column_lineage(sql, dialect, sources, schema)
    Column-level lineage edges for a single SQL cell.  Each edge maps an
    output column to the (table, column) it was derived from.  Returns []
    on parse failure — never raises.

build_cell_lineage_graph(spec)
    Walk every ``query`` task in a FlowSpec, resolve upstream SQL from
    earlier cells, and build a cross-cell column dependency graph.

    Returns a ``CellLineageGraph`` dataclass with:
    - ``nodes``  — per-cell summary (output columns, input edges)
    - ``edges``  — flat list of all cross-cell column dependency edges
    - ``column_flow`` — ``"cell_key:output_col"`` → downstream ``"cell_key:col"``

lineage_plan(spec, changed_cell_key)
    Given a FlowSpec and the key of a cell that is about to change, return:
    - validation issues (reuses ``validate_flow_spec``)
    - the full ``CellLineageGraph``
    - ``downstream_impact`` — ordered list of downstream cell keys that
      would be affected, each with a ``change_type`` (``"breaking"`` if the
      column is used in WHERE/GROUP BY/JOIN; ``"non_breaking"`` otherwise)

Notes
-----
- Only ``query`` tasks are analysed for SQL lineage; python/noop/etc. are
  included in the graph only if they are referenced by downstream SQL cells.
- ``sources`` for sqlglot.lineage are built by mapping each ``cell_key`` to
  the resolved SQL of the upstream task whose output table is named
  ``cell_<key>`` (the DuckDB-WASM cross-cell naming convention).
- RLS injection is not performed here — this module operates on raw SQL
  strings for static analysis only.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Core column lineage extractor
# ---------------------------------------------------------------------------


def extract_column_lineage(
    sql: str,
    dialect: str = "duckdb",
    sources: dict[str, str] | None = None,
    schema: dict | None = None,
) -> list[dict[str, Any]]:
    """Column-level lineage edges for one SQL statement.

    Parameters
    ----------
    sql:
        The SQL string to analyse.  Should be a SELECT statement.
    dialect:
        sqlglot dialect for parsing (default ``"duckdb"``).
    sources:
        Optional mapping of virtual table name → SQL string for upstream
        cells.  Enables cross-cell lineage: ``{"cell_revenue": upstream_sql}``.
    schema:
        Optional schema dict passed to sqlglot.lineage for column type info.

    Returns
    -------
    list[dict]
        Each element is an edge dict::

            {
              "output_col":  str,            # output column of this cell
              "from_table":  str | None,     # source table (cell key or real table)
              "from_col":    str,            # source column name
              "source_name": str,            # "" for leaf real table; cell key for CTE
            }

        Returns ``[]`` on parse failure (never raises).
    """
    try:
        from sqlglot.lineage import lineage as _sg_lineage  # noqa: PLC0415
        import sqlglot.expressions as exp  # noqa: PLC0415
    except ImportError:
        logger.warning("sqlglot not available — column lineage disabled")
        return []

    if not sql or not sql.strip():
        return []

    try:
        nodes_by_col = _sg_lineage(
            column=None,
            sql=sql,
            dialect=dialect,
            sources=sources or {},
            schema=schema,
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("column lineage parse failure: %s", exc)
        return []

    if not isinstance(nodes_by_col, dict):
        return []

    edges: list[dict[str, Any]] = []
    seen: set[tuple[str, str | None, str, str]] = set()

    for output_col, root_node in nodes_by_col.items():
        # Walk all immediate downstream dependencies of the root node.
        for dep_node in root_node.downstream:
            # Determine the source table and column from this dependency node.
            from_col = _col_from_node_name(dep_node.name)
            source_name = dep_node.source_name or ""

            if source_name:
                # Node resolves into a virtual source (another cell's SQL).
                from_table = source_name
            elif isinstance(dep_node.source, exp.Table):
                # Leaf physical table.
                from_table = dep_node.source.name or None
            else:
                # Sub-select or unknown — use whatever is in the name.
                parts = dep_node.name.split(".", 1)
                from_table = parts[0] if len(parts) == 2 else None

            key = (output_col, from_table, from_col, source_name)
            if key not in seen:
                seen.add(key)
                edges.append({
                    "output_col": output_col,
                    "from_table": from_table,
                    "from_col": from_col,
                    "source_name": source_name,
                })

    return edges


def _col_from_node_name(name: str) -> str:
    """Extract column name from a sqlglot lineage node name.

    Node names look like ``"table.column"`` or just ``"column"``.
    """
    if "." in name:
        return name.rsplit(".", 1)[-1]
    return name


# ---------------------------------------------------------------------------
# Cross-cell graph structures
# ---------------------------------------------------------------------------


@dataclass
class CellColumnEdge:
    """A single column-level dependency across cells.

    Attributes
    ----------
    from_cell:
        Key of the upstream cell (``None`` for physical table references).
    from_col:
        Column name in the upstream cell / table.
    to_cell:
        Key of the downstream cell that consumes the column.
    to_col:
        Output column name in the downstream cell.
    from_table:
        Physical table name when ``from_cell`` is ``None``.
    """

    from_cell: str | None
    from_col: str
    to_cell: str
    to_col: str
    from_table: str | None = None


@dataclass
class CellLineageGraph:
    """Column-level lineage graph across all SQL cells in a FlowSpec.

    Attributes
    ----------
    nodes:
        Per-cell detail keyed by task ``key``.  Each value is::

            {
              "key":          str,
              "kind":         str,
              "sql":          str | None,
              "outputs":      list[str],       # output column names
              "input_edges":  list[dict],      # edges feeding INTO this cell
            }

    edges:
        Flat list of all ``CellColumnEdge`` objects across the graph.

    column_flow:
        Inverted index — ``"cell_key:output_col"`` → list of downstream
        ``"cell_key:input_col"`` strings.  Useful for impact analysis.
    """

    nodes: dict[str, dict[str, Any]] = field(default_factory=dict)
    edges: list[CellColumnEdge] = field(default_factory=list)
    column_flow: dict[str, list[str]] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Graph builder
# ---------------------------------------------------------------------------


def build_cell_lineage_graph(spec: Any) -> CellLineageGraph:
    """Build a cross-cell column lineage graph for a FlowSpec.

    The function iterates over tasks in topological order (using the ``needs``
    edges), accumulates the SQL source for each cell under its ``cell_<key>``
    name, then calls ``extract_column_lineage`` with the accumulated
    ``sources`` map so that cross-cell column tracing is possible.

    Parameters
    ----------
    spec:
        A ``FlowSpec`` (or any object with a ``tasks: list[TaskSpec]``
        attribute).  Typically produced by ``validate_flow_spec(data)[0]``.

    Returns
    -------
    CellLineageGraph
        Fully populated graph.  Nodes are present for every task even if
        lineage extraction failed (e.g. non-SQL tasks get empty outputs).
    """
    from app.flows.spec import TaskSpec  # noqa: PLC0415

    graph = CellLineageGraph()

    # Build a task index and compute topological order.
    task_by_key: dict[str, TaskSpec] = {t.key: t for t in spec.tasks}
    ordered_keys = _topological_sort(spec.tasks)

    # Accumulate ``sources`` as we walk in topo order — each SQL cell's
    # effective SQL is registered under the virtual table name ``cell_<key>``
    # so that downstream cells can reference it in their sqlglot sources.
    sources: dict[str, str] = {}

    for key in ordered_keys:
        task = task_by_key[key]
        sql = _resolve_task_sql(task)

        node_detail: dict[str, Any] = {
            "key": key,
            "kind": task.kind,
            "sql": sql,
            "outputs": [],
            "input_edges": [],
        }
        graph.nodes[key] = node_detail

        if task.kind not in ("query",) or not sql:
            # Non-SQL tasks: register nothing in sources, no lineage edges.
            continue

        # Determine the relevant subset of sources: only cells that this task
        # transitively depends on (via needs) to keep the sqlglot lineage call
        # focused and fast.
        relevant_sources = _relevant_sources(task, task_by_key, sources)

        # Extract column-level lineage edges.
        raw_edges = extract_column_lineage(
            sql,
            dialect="duckdb",
            sources=relevant_sources,
        )

        # Derive output column names from the edges (the set of output_col values).
        output_cols: set[str] = {e["output_col"] for e in raw_edges}
        # Fallback: use extract_lineage outputs if sqlglot.lineage returned nothing.
        if not output_cols:
            try:
                from app.lineage.extract import extract_lineage  # noqa: PLC0415
                info = extract_lineage(sql, dialect="duckdb")
                output_cols = set(info.get("outputs", []))
            except Exception:  # noqa: BLE001
                pass
        node_detail["outputs"] = sorted(output_cols)

        # Map raw edges to CellColumnEdge objects and register in the graph.
        for raw in raw_edges:
            from_table = raw.get("from_table")
            source_name = raw.get("source_name", "")

            # Determine whether this edge crosses cells.
            if source_name and source_name in task_by_key:
                # Direct cell reference via source_name.
                from_cell = source_name
                from_col = raw["from_col"]
            elif from_table and from_table in task_by_key:
                # The from_table matches a cell key (virtual table name).
                from_cell = from_table
                from_col = raw["from_col"]
            else:
                # Physical table reference (not a cell).
                from_cell = None
                from_col = raw["from_col"]

            edge = CellColumnEdge(
                from_cell=from_cell,
                from_col=from_col,
                to_cell=key,
                to_col=raw["output_col"],
                from_table=from_table if from_cell is None else None,
            )
            graph.edges.append(edge)
            node_detail["input_edges"].append({
                "from_cell": from_cell,
                "from_col": from_col,
                "from_table": edge.from_table,
                "to_col": raw["output_col"],
            })

            # Update the column_flow inverted index.
            if from_cell is not None:
                flow_key = f"{from_cell}:{from_col}"
                flow_val = f"{key}:{raw['output_col']}"
                graph.column_flow.setdefault(flow_key, [])
                if flow_val not in graph.column_flow[flow_key]:
                    graph.column_flow[flow_key].append(flow_val)

        # Register this cell's SQL in sources under its virtual table name
        # so downstream cells can reference it.
        virtual_name = key  # matches the DuckDB table name convention (cell_<key>)
        sources[virtual_name] = sql

    return graph


def _topological_sort(tasks: list[Any]) -> list[str]:
    """Return task keys in topological order (Kahn's BFS).

    Falls back to declaration order on cycle (which validate_flow_spec will
    already have reported as an error).
    """
    adjacency: dict[str, list[str]] = {t.key: [] for t in tasks}
    in_degree: dict[str, int] = {t.key: 0 for t in tasks}
    task_keys = {t.key for t in tasks}

    for task in tasks:
        for dep in task.needs:
            if dep in task_keys:
                adjacency[dep].append(task.key)
                in_degree[task.key] += 1

    queue = [k for k, deg in in_degree.items() if deg == 0]
    order: list[str] = []
    while queue:
        node = queue.pop(0)
        order.append(node)
        for child in adjacency[node]:
            in_degree[child] -= 1
            if in_degree[child] == 0:
                queue.append(child)

    # Append any remaining (cycle guard).
    for task in tasks:
        if task.key not in order:
            order.append(task.key)

    return order


def _resolve_task_sql(task: Any) -> str | None:
    """Return the inline SQL for a query task, or None."""
    if task.kind not in ("query",):
        return None
    cfg = task.config or {}
    return cfg.get("sql") or None


def _relevant_sources(
    task: Any,
    task_by_key: dict[str, Any],
    accumulated_sources: dict[str, str],
) -> dict[str, str]:
    """Return the subset of accumulated_sources relevant to this task.

    We walk the full transitive ``needs`` closure so that sqlglot can resolve
    column references through multiple cell hops.
    """
    relevant: dict[str, str] = {}

    def _walk(key: str) -> None:
        t = task_by_key.get(key)
        if t is None:
            return
        for dep_key in t.needs:
            # Include the SQL source for this dependency if it exists.
            if dep_key in accumulated_sources:
                relevant[dep_key] = accumulated_sources[dep_key]
            _walk(dep_key)

    _walk(task.key)
    return relevant


# ---------------------------------------------------------------------------
# Plan helper
# ---------------------------------------------------------------------------


@dataclass
class CellImpact:
    """Impact of changing a cell on a downstream cell.

    Attributes
    ----------
    cell_key:
        The downstream cell that would be affected.
    change_type:
        ``"breaking"`` if the column is used in WHERE / GROUP BY / JOIN;
        ``"non_breaking"`` if it only appears in the SELECT list.
    affected_columns:
        Output columns in this downstream cell that trace back through the
        changed cell.
    """

    cell_key: str
    change_type: str  # "breaking" | "non_breaking"
    affected_columns: list[str]


def lineage_plan(
    spec: Any,
    changed_cell_key: str,
) -> dict[str, Any]:
    """Return a plan showing the lineage and downstream impact of changing a cell.

    Parameters
    ----------
    spec:
        A ``FlowSpec`` (already validated) or a raw dict (will be validated
        inline via ``validate_flow_spec``).
    changed_cell_key:
        The task key of the cell being changed.

    Returns
    -------
    dict with keys:
        ``valid``            — bool; True when the spec has no hard errors.
        ``issues``           — list[str]; hard errors + soft warnings.
        ``lineage``          — serialised CellLineageGraph.
        ``downstream_impact``— list[CellImpact] for cells downstream of
                               ``changed_cell_key``.
    """
    from app.flows.spec import FlowSpec, flow_spec_is_valid, validate_flow_spec  # noqa: PLC0415

    # Accept either a FlowSpec object or a raw dict.
    if isinstance(spec, FlowSpec):
        validated_spec: FlowSpec | None = spec
        issues: list[str] = []
    else:
        validated_spec, issues = validate_flow_spec(spec)

    valid = validated_spec is not None and flow_spec_is_valid(issues)

    if validated_spec is None:
        return {
            "valid": False,
            "issues": issues,
            "lineage": None,
            "downstream_impact": [],
        }

    # Build the lineage graph.
    graph = build_cell_lineage_graph(validated_spec)

    # Compute downstream impact.
    impact = _compute_downstream_impact(
        changed_cell_key=changed_cell_key,
        graph=graph,
        spec=validated_spec,
    )

    return {
        "valid": valid,
        "issues": issues,
        "lineage": _serialise_graph(graph),
        "downstream_impact": [
            {
                "cell_key": ci.cell_key,
                "change_type": ci.change_type,
                "affected_columns": ci.affected_columns,
            }
            for ci in impact
        ],
    }


def _compute_downstream_impact(
    changed_cell_key: str,
    graph: CellLineageGraph,
    spec: Any,
) -> list[CellImpact]:
    """Walk the column_flow index to find cells downstream of ``changed_cell_key``.

    For each affected downstream cell, classify as ``breaking`` if the
    changed column feeds into a non-SELECT usage in that cell (WHERE / GROUP BY
    / JOIN / HAVING).  Currently we perform a conservative check: any column
    that propagates beyond the immediate SELECT list is classified as breaking.
    """
    if changed_cell_key not in graph.nodes:
        return []

    # Collect all output columns of the changed cell.
    changed_outputs = set(graph.nodes[changed_cell_key].get("outputs", []))

    # BFS over column_flow to find all downstream cells + which columns are affected.
    downstream: dict[str, list[str]] = {}  # cell_key → [affected output cols]
    frontier: list[tuple[str, str]] = []  # (cell_key, col_name)

    for col in changed_outputs:
        flow_key = f"{changed_cell_key}:{col}"
        for downstream_ref in graph.column_flow.get(flow_key, []):
            cell_key, col_name = downstream_ref.split(":", 1)
            frontier.append((cell_key, col_name))

    visited: set[tuple[str, str]] = set()
    while frontier:
        cell_key, col_name = frontier.pop(0)
        if (cell_key, col_name) in visited:
            continue
        visited.add((cell_key, col_name))
        downstream.setdefault(cell_key, [])
        if col_name not in downstream[cell_key]:
            downstream[cell_key].append(col_name)

        # Continue BFS: propagate the column further downstream.
        flow_key = f"{cell_key}:{col_name}"
        for next_ref in graph.column_flow.get(flow_key, []):
            next_cell, next_col = next_ref.split(":", 1)
            frontier.append((next_cell, next_col))

    # Classify each downstream cell.
    impacts: list[CellImpact] = []
    task_by_key = {t.key: t for t in spec.tasks}

    for cell_key, affected_cols in downstream.items():
        change_type = _classify_impact(cell_key, affected_cols, task_by_key)
        impacts.append(CellImpact(
            cell_key=cell_key,
            change_type=change_type,
            affected_columns=sorted(affected_cols),
        ))

    # Sort by topological order so the response is deterministic.
    topo = _topological_sort(spec.tasks)
    impacts.sort(key=lambda ci: topo.index(ci.cell_key) if ci.cell_key in topo else 999)
    return impacts


def _classify_impact(
    cell_key: str,
    affected_cols: list[str],
    task_by_key: dict[str, Any],
) -> str:
    """Classify whether the impact on *cell_key* is breaking or non-breaking.

    Breaking: any column from an upstream cell is used in a WHERE / GROUP BY /
    HAVING / JOIN condition of the downstream SQL.  This includes columns that
    appear only in filter clauses and not in SELECT output.

    The classification strategy:
    1. Parse the downstream cell's SQL.
    2. Collect all columns referenced in non-SELECT clauses (WHERE / GROUP BY /
       HAVING / JOIN ON).
    3. If any such column is attributed to an upstream table (via alias or
       unqualified in a single-table FROM), the impact is ``"breaking"``.
    4. If the output columns that flow through (``affected_cols``) appear in
       non-SELECT clauses, it is also ``"breaking"``.
    """
    task = task_by_key.get(cell_key)
    if task is None or task.kind != "query":
        return "non_breaking"

    sql = _resolve_task_sql(task)
    if not sql:
        return "non_breaking"

    try:
        import sqlglot  # noqa: PLC0415
        import sqlglot.expressions as exp  # noqa: PLC0415

        tree = sqlglot.parse_one(sql, dialect="duckdb")
        if not isinstance(tree, exp.Select):
            return "non_breaking"

        # Build a set of source table names / aliases referenced in the FROM clause.
        # These are the upstream cell virtual table names.
        source_tables: set[str] = set()
        for tbl_node in tree.find_all(exp.Table):
            name = tbl_node.name
            if not name:
                continue
            source_tables.add(name.lower())
            alias_node = tbl_node.args.get("alias")
            if alias_node:
                alias = alias_node.name if hasattr(alias_node, "name") else str(alias_node)
                if alias:
                    source_tables.add(alias.lower())

        # Collect all column references in non-SELECT clauses.
        filtering_cols: set[str] = set()

        for clause_type in (exp.Where, exp.Group, exp.Having):
            clause = tree.args.get(clause_type.__name__.lower())
            if clause:
                for col in clause.find_all(exp.Column):
                    filtering_cols.add(col.name.lower())

        # JOIN ON conditions.
        for join in tree.find_all(exp.Join):
            on_clause = join.args.get("on")
            if on_clause:
                for col in on_clause.find_all(exp.Column):
                    filtering_cols.add(col.name.lower())

        if not filtering_cols:
            return "non_breaking"

        # Check 1: any of the flowing output columns appear in filter clauses.
        affected_lower = {c.lower() for c in affected_cols}
        if affected_lower & filtering_cols:
            return "breaking"

        # Check 2: any column in a filter clause is from an upstream table.
        # If the downstream cell reads from exactly one upstream table/cell
        # and there are any filter columns, those columns come from that upstream
        # → the change is breaking.
        if len(source_tables) == 1 and filtering_cols:
            return "breaking"

        # Check 3: multi-table FROM — check if any filter column is qualified
        # with an upstream cell table reference.
        for clause_type in (exp.Where, exp.Group, exp.Having):
            clause = tree.args.get(clause_type.__name__.lower())
            if clause:
                for col in clause.find_all(exp.Column):
                    tbl = col.table
                    if tbl and tbl.lower() in source_tables:
                        return "breaking"

        for join in tree.find_all(exp.Join):
            on_clause = join.args.get("on")
            if on_clause:
                for col in on_clause.find_all(exp.Column):
                    tbl = col.table
                    if tbl and tbl.lower() in source_tables:
                        return "breaking"

    except Exception:  # noqa: BLE001
        # If we can't parse, be conservative.
        return "breaking"

    return "non_breaking"


def _serialise_graph(graph: CellLineageGraph) -> dict[str, Any]:
    """Convert a CellLineageGraph to a JSON-safe dict."""
    return {
        "nodes": graph.nodes,
        "edges": [
            {
                "from_cell": e.from_cell,
                "from_col": e.from_col,
                "to_cell": e.to_cell,
                "to_col": e.to_col,
                "from_table": e.from_table,
            }
            for e in graph.edges
        ],
        "column_flow": graph.column_flow,
    }
