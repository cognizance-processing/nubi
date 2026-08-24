"""SQL table/column reference extraction via sqlglot AST walking.

Public API
----------
extract_table_columns(sql, dialect="postgres") -> dict

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
        - If a column is unqualified AND exactly one source is joined in ITS
          OWN scope, the column is attributed to that source (scope-correct:
          a column inside a 3-way-joined subquery is never attributed to an
          unrelated single table two scopes up).
        - If a column is qualified by a DERIVED-TABLE alias (a subquery or
          CTE, not a real table) — e.g. ``b.billed`` where ``b`` is
          ``(SELECT month, SUM(amount) AS billed FROM fin_invoices GROUP BY
          month) b`` — the reference is traced back through the derived
          table's own projection to whatever it ultimately reads from,
          recursively (capped depth), the same way a person would trace it by
          eye. ``b.billed`` resolves through to nothing further (it is a
          computed ``SUM``, not a passthrough column) and stays unattributed;
          ``b.month`` resolves through to ``fin_invoices.month``. Without
          this, a column read only through a derived-table join never gets
          attributed to its real table at all — which starves any estate-wide
          catalog built from this function of exactly the columns a query is
          plainly using, for every query that computes a subtotal before
          joining it (a very common shape).
        - Otherwise ``table`` is ``None`` (ambiguous join, or the column
          traces back to a computed expression rather than a physical column).
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
- Column attribution walks sqlglot's ``Scope`` tree (``traverse_scope``) so
  each column is resolved using its OWN scope's sources — an unqualified
  column or an alias that points at a derived table is chased back to a real
  table without being confused by an unrelated table two scopes up. This
  needs no schema information and no pre-qualification pass.
- CTE names are excluded from the ``tables`` list (they are not real tables).
- Subquery aliases are excluded from the ``tables`` list.
- Jinja templating (``{% if %}`` / ``{{ }}``) is neutralised before parsing —
  sqlglot cannot parse it, and on this codebase's converted boards most
  registered queries carry at least one ``{% if param %}`` filter block.
  Without this, a query drops out of the catalog entirely the moment it
  gains its first filter — exactly backwards, since that is when its columns
  matter most to every OTHER query's "what can I filter on?" picker.

This is a generic SQL-introspection utility used by the AI grounding catalog
(``app.ai.grounding``), notebook cell auto-dependency inference
(``app.flows.notebook``), and the health/estate graph enrichment
(``app.routes.health``).  It is deliberately NOT part of a "lineage"
governance feature — those DAG/column-lineage endpoints were removed.
"""

from __future__ import annotations

import logging
from typing import Any

import sqlglot
import sqlglot.expressions as exp
from sqlglot.optimizer import qualify
from sqlglot.optimizer.scope import Scope, traverse_scope

from app.queries.parameterize import neutralize_jinja
from app.queries.scope_resolve import exposed_projection

logger = logging.getLogger(__name__)

#: Cap on how many derived-table hops a single column reference is chased
#: through (mirrors app.queries.parameterize._base_table_for) — real queries
#: nest two or three deep; anything past this is treated as unresolvable
#: rather than risking runaway recursion on a pathological query.
_MAX_CHASE_DEPTH = 5


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


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
    # Found via find_all rather than tree.args.get("with") — the with-clause's
    # arg key has changed across sqlglot versions (with_ vs with); walking for
    # exp.CTE nodes directly needs no dependency on that key's exact spelling.
    names: set[str] = set()
    for cte in tree.find_all(exp.CTE):
        alias = cte.alias
        if alias:
            names.add(alias.lower())
    return names


def _resolve_column_origin(
    scope: Scope, col_node: exp.Column, depth: int = 0
) -> str | None:
    """Chase *col_node* back to the real table it ultimately reads from.

    Mirrors ``app.queries.parameterize._base_table_for``'s recursive descent:
    look up the column's qualifier (or the sole source, if unqualified and
    unambiguous) in *scope*'s own sources. A real table ends the chase. A
    derived table (subquery or CTE — anything that isn't an ``exp.Table``)
    means the column is itself a reference into ANOTHER scope: find the
    matching projection there and recurse one level deeper with that scope
    and that projection's underlying expression.

    Returns ``None`` when the chase can't complete: the source is ambiguous
    (unqualified column, more than one join source), the referenced
    projection doesn't exist, or it resolves to a computed expression
    (``SUM(amount) AS billed`` is not a passthrough of a physical column).
    """
    if depth > _MAX_CHASE_DEPTH or scope is None:
        return None
    col_name = col_node.name
    if not col_name or col_name == "*":
        return None

    sources = getattr(scope, "sources", {}) or {}
    qualifier = col_node.table
    source = None
    if qualifier:
        source = sources.get(qualifier)
    elif len(sources) == 1:
        source = next(iter(sources.values()))
    if source is None:
        return None

    if isinstance(source, exp.Table):
        return (source.name or "").lower() or None

    # A Scope (subquery or CTE) — the column is read from whatever THAT
    # scope's own matching projection ultimately is.
    inner_select = getattr(source, "expression", None)
    if not isinstance(inner_select, exp.Select):
        return None
    inner_proj = exposed_projection(inner_select, col_name)
    if inner_proj is None:
        return None
    inner_node = inner_proj.this if isinstance(inner_proj, exp.Alias) else inner_proj
    if not isinstance(inner_node, exp.Column):
        # Computed expression — the chase ends here, unresolved.
        return None
    return _resolve_column_origin(source, inner_node, depth + 1)


def _collect_column_refs(tree: exp.Expression) -> list[dict[str, str | None]]:
    """Walk every scope's own columns and resolve each to its real table.

    Parameters
    ----------
    tree:
        Parsed expression tree.

    Returns
    -------
    list[dict]
        De-duplicated list of ``{"table": str|None, "column": str}`` dicts,
        sorted by (table, column). Resolution is scope-aware throughout —
        see :func:`_resolve_column_origin` — so an unqualified column is
        attributed only when its OWN scope has exactly one source, and an
        alias pointing at a derived table is chased back to a physical
        column rather than left keyed by the alias.
    """
    seen: set[tuple[str | None, str]] = set()
    results: list[dict[str, str | None]] = []

    for scope in traverse_scope(tree):
        for col_node in scope.columns:
            col_name = col_node.name
            if not col_name or col_name == "*":
                continue

            table_part = _resolve_column_origin(scope, col_node)

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


def extract_table_columns(sql: str, dialect: str = "postgres") -> dict[str, Any]:
    """Extract table/column references from a SQL SELECT statement.

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

    # ── 1. Parse (Jinja neutralised first — see module docstring) ───────────
    try:
        tree = sqlglot.parse_one(neutralize_jinja(sql), dialect=dialect)
    except Exception as exc:
        logger.debug("SQL table/column extraction parse failure: %s", exc)
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

    # ── 4. Collect real tables ───────────────────────────────────────────────
    real_tables = _collect_real_tables(qualified_tree, cte_names)

    # ── 5. Collect column references (scope-aware — see _collect_column_refs) ─
    columns = _collect_column_refs(qualified_tree)

    # ── 7. Collect output names ──────────────────────────────────────────────
    outputs = _collect_outputs(qualified_tree)

    return {"tables": real_tables, "columns": columns, "outputs": outputs}
