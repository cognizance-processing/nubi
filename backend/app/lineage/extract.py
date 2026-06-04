"""SQL lineage extraction via sqlglot AST walking (M7-A).

Public API
----------
extract_lineage(sql, dialect="postgres") -> dict

    Parse *sql* and return a dict with three keys:

    tables : list[str]
        Sorted, de-duplicated real table names referenced in FROM / JOIN
        clauses (aliases are resolved to their real names; subquery aliases
        are excluded).

    columns : list[dict]
        Sorted, de-duplicated column references as ``{"table": str|None,
        "column": str}``.  Attribution rules:
        - If a column is prefixed by a table alias (``u.id``), the alias is
          resolved to the real table name.
        - If a column is unqualified AND exactly one real table is referenced
          in the query, the column is attributed to that table.
        - Otherwise ``table`` is ``None`` (ambiguous / subquery / CTE).
        - Columns that appear only inside function calls are included when
          they are plain ``Column`` nodes (e.g. ``SUM(amount)`` → amount).
          Non-column expressions (literals, ``*``) are skipped.

    outputs : list[str]
        Sorted, de-duplicated output column names / aliases from the outermost
        SELECT list.  Rules:
        - Explicit alias → alias name.
        - Bare column reference with no alias → column name.
        - Star (``SELECT *``) → skipped.
        - Complex expressions with no alias (e.g. ``1 + 2``) → skipped.

    On parse failure or if the SQL is not a SELECT the function does NOT raise;
    instead it returns ``{"tables": [], "columns": [], "outputs": [],
    "error": "<description>"}`` so callers remain robust to bad SQL.  The
    ``error`` key is absent from successful results.

Notes
-----
- Uses sqlglot's ``qualify`` optimizer pass to expand aliases before walking
  the AST.  If ``qualify`` itself raises (common for DuckDB/generate_series
  idioms) the function falls back to manual alias resolution.
- CTE names are excluded from the ``tables`` list (they are not real tables).
- Subquery aliases are excluded from the ``tables`` list.
"""

from __future__ import annotations

import logging
from typing import Any

import sqlglot
import sqlglot.expressions as exp
from sqlglot.optimizer import qualify

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _build_alias_map(tree: exp.Expression) -> dict[str, str]:
    """Return a mapping from alias → real table name for FROM / JOIN sources.

    Only tables (not subqueries or CTEs) are included.

    Parameters
    ----------
    tree:
        The parsed sqlglot expression tree (typically a ``Select``).

    Returns
    -------
    dict[str, str]
        ``{"alias": "real_table_name", ...}``.  When a table has no alias the
        table name maps to itself (``{"users": "users"}``).
    """
    alias_map: dict[str, str] = {}

    for table_node in tree.find_all(exp.Table):
        # Skip tables that are inside subqueries that are themselves aliased
        # (i.e. the subquery is the table, not a real table reference).
        # A real table has a non-empty name.
        real_name = table_node.name
        if not real_name:
            continue

        alias_node = table_node.args.get("alias")
        if alias_node:
            alias_name = (
                alias_node.name
                if hasattr(alias_node, "name")
                else str(alias_node)
            )
            if alias_name:
                alias_map[alias_name.lower()] = real_name.lower()

        # Always map real name → real name so unaliased columns resolve too.
        alias_map[real_name.lower()] = real_name.lower()

    return alias_map


def _collect_real_tables(tree: exp.Expression, cte_names: set[str]) -> list[str]:
    """Collect real table names referenced in FROM / JOIN (excluding CTEs and subqueries).

    Parameters
    ----------
    tree:
        Parsed expression tree.
    cte_names:
        Set of CTE alias names to exclude.

    Returns
    -------
    list[str]
        Sorted, de-duplicated real table names (lower-case).
    """
    tables: set[str] = set()
    for table_node in tree.find_all(exp.Table):
        name = table_node.name
        if not name:
            continue
        lower = name.lower()
        if lower in cte_names:
            continue
        tables.add(lower)
    return sorted(tables)


def _collect_cte_names(tree: exp.Expression) -> set[str]:
    """Return the set of CTE alias names defined in the query (lower-case)."""
    names: set[str] = set()
    with_clause = tree.args.get("with")
    if with_clause is None:
        return names
    for cte in with_clause.find_all(exp.CTE):
        alias = cte.alias
        if alias:
            names.add(alias.lower())
    return names


def _collect_column_refs(
    tree: exp.Expression,
    alias_map: dict[str, str],
    real_tables: list[str],
    cte_names: set[str],
) -> list[dict[str, str | None]]:
    """Walk the AST and collect all column references.

    Parameters
    ----------
    tree:
        Parsed expression tree.
    alias_map:
        Alias → real table name mapping.
    real_tables:
        List of real table names (used for single-table attribution).
    cte_names:
        Set of CTE alias names (columns attributed to CTEs get table=None).

    Returns
    -------
    list[dict]
        De-duplicated list of ``{"table": str|None, "column": str}`` dicts,
        sorted by (table, column).
    """
    seen: set[tuple[str | None, str]] = set()
    results: list[dict[str, str | None]] = []

    for col_node in tree.find_all(exp.Column):
        col_name = col_node.name
        if not col_name or col_name == "*":
            continue

        # Determine table attribution.
        table_part: str | None = None
        table_node = col_node.args.get("table")
        if table_node is not None:
            qualifier = (
                table_node.name
                if hasattr(table_node, "name")
                else str(table_node)
            ).lower()
            if qualifier in cte_names:
                # Column comes from a CTE — treat as unresolved.
                table_part = None
            else:
                # Resolve alias → real table name.
                table_part = alias_map.get(qualifier, qualifier)
        else:
            # Unqualified column — attribute to the sole real table if unique.
            if len(real_tables) == 1:
                table_part = real_tables[0]
            else:
                table_part = None

        key = (table_part, col_name.lower())
        if key not in seen:
            seen.add(key)
            results.append({"table": table_part, "column": col_name.lower()})

    # Sort deterministically: (table or "", column)
    results.sort(key=lambda d: (d["table"] or "", d["column"] or ""))
    return results


def _collect_outputs(select: exp.Select) -> list[str]:
    """Extract output column names / aliases from the outermost SELECT list.

    Parameters
    ----------
    select:
        A sqlglot ``Select`` node.

    Returns
    -------
    list[str]
        Sorted, de-duplicated output names.
    """
    outputs: set[str] = set()

    for expr in select.expressions:
        # Explicit alias (AS foo) — use alias.
        if isinstance(expr, exp.Alias):
            alias_name = expr.alias
            if alias_name:
                outputs.add(alias_name.lower())
            continue

        # Bare column reference → use column name.
        if isinstance(expr, exp.Column):
            col_name = expr.name
            if col_name and col_name != "*":
                outputs.add(col_name.lower())
            continue

        # Star → skip.
        if isinstance(expr, exp.Star):
            continue

        # Other expressions without alias → skip.

    return sorted(outputs)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def extract_lineage(sql: str, dialect: str = "postgres") -> dict[str, Any]:
    """Extract table/column lineage from a SQL SELECT statement.

    Parameters
    ----------
    sql:
        The SQL string to analyse.  Should be a SELECT statement.  Non-SELECT
        statements are handled gracefully (returned with an ``error`` key).
    dialect:
        sqlglot dialect for parsing.  Default ``"postgres"``.

    Returns
    -------
    dict
        ``{"tables": [...], "columns": [...], "outputs": [...]}`` on success.
        ``{"tables": [], "columns": [], "outputs": [], "error": "..."}`` on
        failure (parse error, non-SELECT, etc.).  The ``error`` key is absent
        on success.

    Notes
    -----
    The function never raises.  All exceptions are caught and reflected in the
    ``error`` key of the returned dict so callers remain robust.
    """
    empty: dict[str, Any] = {"tables": [], "columns": [], "outputs": []}

    # ── 1. Parse ────────────────────────────────────────────────────────────
    try:
        tree = sqlglot.parse_one(sql, dialect=dialect)
    except Exception as exc:
        logger.debug("lineage parse failure: %s", exc)
        return {**empty, "error": f"parse_error: {exc}"}

    if not isinstance(tree, exp.Select):
        return {**empty, "error": "not_a_select"}

    # ── 2. Optionally run qualify to resolve aliases in the AST ─────────────
    # qualify can fail on non-standard SQL (DuckDB functions, generate_series,
    # etc.), so we catch and fall back.
    qualified_tree = tree
    try:
        # qualify needs schema info for full resolution; without it it still
        # resolves table aliases on Column nodes which is all we need.
        qualified_tree = qualify.qualify(
            tree,
            dialect=dialect,
            schema=None,
            qualify_columns=True,
            qualify_tables=False,
            identify=False,
            validate_qualify_columns=False,
        )
    except Exception:
        # Fall back to unqualified tree; manual alias resolution handles it.
        qualified_tree = tree

    # ── 3. Collect CTEs (to exclude from real-table list) ───────────────────
    cte_names = _collect_cte_names(qualified_tree)

    # ── 4. Build alias map ───────────────────────────────────────────────────
    alias_map = _build_alias_map(qualified_tree)

    # ── 5. Collect real tables ───────────────────────────────────────────────
    real_tables = _collect_real_tables(qualified_tree, cte_names)

    # ── 6. Collect column references ────────────────────────────────────────
    columns = _collect_column_refs(qualified_tree, alias_map, real_tables, cte_names)

    # ── 7. Collect output names ──────────────────────────────────────────────
    outputs = _collect_outputs(qualified_tree)

    return {"tables": real_tables, "columns": columns, "outputs": outputs}
