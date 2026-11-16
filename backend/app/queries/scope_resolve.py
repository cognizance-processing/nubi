"""Shared scope-aware column resolution for the filter pipeline.

Both ``parameterize.py`` (author-time: "what can this query be filtered
on, and where does a splice go?") and ``planner.py`` (runtime: "inject this
filter into the right subquery scope") need the same answer to one
question: given a parsed query and a target column name, which SELECT scope
actually exposes that column, and what SQL expression identifies it there?

Two sources of truth, checked innermost-scope-first (``traverse_scope``
yields children before parents, so the first hit is the deepest place
filtering can happen before any roll-up above it):

1. **Projection match** — the column is already in that scope's SELECT
   list (aliased or not). Exact: sqlglot's aliasing tells us precisely what
   expression a projected name refers to.
2. **Schema match** — the column isn't projected in this scope at all, but
   it IS a real column on one of the scope's own FROM/JOIN tables, per an
   estate-wide ``{table_name: [column, ...]}`` catalog (see
   ``app.ai.grounding.build_catalog``). This is how a KPI's aggregate
   subquery over ``fin_invoices`` becomes filterable by ``month`` even
   though that particular query never mentions the word.

The schema catalog is a *union of columns other registered queries
reference against that table*, not a live database schema fetch — cheap,
synchronous, and already used elsewhere for the same purpose (AI
grounding). A column no registered query anywhere touches stays invisible;
that is a known, acceptable gap rather than a silent correctness issue (the
splice is verified by re-running the query before it is ever kept — see
``parameterize_sql`` / the ``/parameterize`` route).

A schema match only fires when the scope has at least one DIRECT
``exp.Table`` source (not a CTE/derived-table alias) — chasing a column
through nested derived tables is what ``_base_table_for`` in
parameterize.py already does for the projection-match case; extending that
chase to unprojected columns is future work, not required for the bug this
module was built to fix.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlglot import exp
from sqlglot.optimizer.scope import Scope, traverse_scope


@dataclass(frozen=True)
class FilterTarget:
    """Where and how to filter a column in a parsed query.

    Attributes
    ----------
    scope, select:
        The matched ``Scope`` and its ``exp.Select`` node — the place a
        predicate must be injected (or a splice performed) for the filter to
        apply before any roll-up above it.
    column_expr:
        SQL text to filter on in *select*'s own WHERE clause, e.g.
        ``"amount"`` or ``"b.month"`` (qualified only when the scope joins
        more than one source).
    in_output:
        True when the column also survives into *select*'s own output list.
    projection:
        The matched projection node, when this was a projection match
        (``None`` for a schema match). Callers that need to trace the
        expression back to a physical table/column (e.g. deriving an
        options query) use this.
    base_table:
        The resolved ``exp.Table``, when this was a schema match (``None``
        for a projection match — the caller already has ``projection`` to
        trace instead).
    base_column:
        The real column name on *base_table*, when this was a schema match.
    """

    scope: Scope
    select: exp.Select
    column_expr: str
    in_output: bool
    projection: exp.Expression | None = None
    base_table: exp.Table | None = None
    base_column: str | None = None


def exposed_projection(select: exp.Select, column: str) -> exp.Expression | None:
    """The projection in *select* that outputs *column*, if any.

    Matches on the projection's output name (its alias when aliased, else
    the column name), case-insensitively — that is the name a dashboard
    author sees and would pick from a column list.
    """
    target = column.strip().lower()
    for proj in select.expressions:
        if isinstance(proj, exp.Star):
            # A star may or may not carry the column; we cannot tell without
            # full schema resolution, so this scope is not a safe target.
            continue
        name = proj.alias_or_name
        if name and name.lower() == target:
            return proj
    return None


def underlying_expr(projection: exp.Expression, dialect: str) -> str:
    """SQL text to filter on for *projection*.

    For ``br.description AS Region`` this is ``br.description``: the alias
    exists only in the SELECT list and cannot be referenced from the same
    scope's WHERE clause.
    """
    node = projection.this if isinstance(projection, exp.Alias) else projection
    return node.sql(dialect=dialect)


def scope_base_tables(scope: Scope) -> dict[str, exp.Table]:
    """Alias/name -> ``exp.Table`` for this scope's own FROM/JOIN sources.

    Only direct table sources are included — a nested subquery or CTE is
    itself a different scope (handled by its own ``traverse_scope`` entry),
    and its output columns are not this scope's base-table columns.
    """
    out: dict[str, exp.Table] = {}
    for alias, source in scope.sources.items():
        if isinstance(source, exp.Table):
            out[alias] = source
    return out


def find_all_filter_targets(
    tree: exp.Expression,
    column: str,
    dialect: str = "postgres",
    table_columns: dict[str, list[str]] | None = None,
) -> list[FilterTarget]:
    """Every scope where *column* can independently be filtered.

    Checks projection match first (exact), then schema match (via
    *table_columns*, an estate-wide ``{table: [col, ...]}`` map) against the
    scope's own base tables — per scope, not just once for the whole query.
    Schema match is skipped entirely when *table_columns* is not supplied,
    preserving projection-only behaviour for callers that have no catalog
    handy.

    A single query can expose the same dimension in several INDEPENDENT
    scopes with no data relationship between them — e.g. a headline-metrics
    query with one scalar subquery per KPI, each over a different table that
    happens to share a ``month`` column. A dashboard "Month" filter over such
    a query means "filter every one of those", not just the first one found:
    a runtime injector (the planner) wants every match; an author-time
    splice (one ``{{param}}`` per call) picks the first via
    :func:`find_filter_target`.
    """
    target = column.strip().lower()
    outer_names: set[str] = set()
    if isinstance(tree, exp.Select):
        outer_names = {
            p.alias_or_name.lower()
            for p in tree.expressions
            if not isinstance(p, exp.Star) and p.alias_or_name
        }

    out: list[FilterTarget] = []
    for scope in traverse_scope(tree):
        select = scope.expression
        if not isinstance(select, exp.Select):
            continue

        proj = exposed_projection(select, column)
        if proj is not None:
            out.append(
                FilterTarget(
                    scope=scope,
                    select=select,
                    column_expr=underlying_expr(proj, dialect),
                    in_output=target in outer_names,
                    projection=proj,
                )
            )
            continue

        if not table_columns:
            continue

        base_tables = scope_base_tables(scope)
        matches = [
            (alias, table)
            for alias, table in base_tables.items()
            if any(c.lower() == target for c in table_columns.get(table.name, ()))
        ]
        if len(matches) == 1:
            alias, table = matches[0]
            # Qualify only when the scope joins more than one source — an
            # unqualified reference reads better and is unambiguous when
            # there is exactly one FROM source.
            column_expr = f"{alias}.{column}" if len(base_tables) > 1 else column
            out.append(
                FilterTarget(
                    scope=scope,
                    select=select,
                    column_expr=column_expr,
                    in_output=False,
                    base_table=table,
                    base_column=column,
                )
            )
        # 0 or >=2 matches: no match, or ambiguous across joined tables —
        # either way this scope cannot safely host the filter.

    return out


def find_filter_target(
    tree: exp.Expression,
    column: str,
    dialect: str = "postgres",
    table_columns: dict[str, list[str]] | None = None,
) -> FilterTarget | None:
    """The innermost scope where *column* can be filtered, or ``None``.

    The first entry of :func:`find_all_filter_targets` — for author-time
    callers (splicing a stored ``{{param}}`` block) that need exactly one
    insertion point per call. See that function's docstring for why a
    runtime injector wants every match instead of just this one.
    """
    matches = find_all_filter_targets(tree, column, dialect=dialect, table_columns=table_columns)
    return matches[0] if matches else None


def filterable_surface(
    tree: exp.Expression,
    dialect: str = "mysql",
    table_columns: dict[str, list[str]] | None = None,
) -> list[dict[str, Any]]:
    """Every column across every scope that a filter could attach to.

    One entry per distinct output name, innermost scope first, plus (when
    *table_columns* is supplied) unprojected columns that exist on a
    scope's own base tables. Aggregates are excluded from the
    projection-derived half — filtering a ``SUM`` needs ``HAVING``, not
    ``WHERE`` — schema-derived columns are always raw table columns so this
    exclusion never applies to them.
    """
    outer_names: set[str] = set()
    if isinstance(tree, exp.Select):
        outer_names = {
            p.alias_or_name.lower()
            for p in tree.expressions
            if not isinstance(p, exp.Star) and p.alias_or_name
        }

    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for scope in traverse_scope(tree):
        select = scope.expression
        if not isinstance(select, exp.Select):
            continue

        for proj in select.expressions:
            if isinstance(proj, exp.Star):
                continue
            name = proj.alias_or_name
            if not name:
                continue
            key = name.lower()
            if key in seen:
                continue
            node = proj.this if isinstance(proj, exp.Alias) else proj
            if isinstance(node, exp.AggFunc) or node.find(exp.AggFunc) is not None:
                continue
            seen.add(key)
            out.append(
                {
                    "name": name,
                    "expr": underlying_expr(proj, dialect),
                    "in_output": key in outer_names,
                }
            )

        if not table_columns:
            continue

        base_tables = scope_base_tables(scope)
        single_source = len(base_tables) == 1
        for alias, table in base_tables.items():
            for col_name in table_columns.get(table.name, ()):
                key = col_name.lower()
                if key in seen:
                    continue
                seen.add(key)
                expr = col_name if single_source else f"{alias}.{col_name}"
                out.append({"name": col_name, "expr": expr, "in_output": False})

    return out
