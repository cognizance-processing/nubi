"""Turn a plain SELECT into a filterable one — without the author writing SQL.

Nubi's filter contract is three-sided: a filter widget names a *variable*, the
board declares it, and a data widget binds one of its query's declared
*params* to it. That last link is the wall: a query with no ``{{param}}``
placeholder can never be connected to a filter, so "add a filter to this
dashboard" used to mean "go hand-edit the SQL of every widget you want it to
control" — which most dashboard authors cannot or should not do.

This module closes that gap. Given a query's SQL, a param name and the column
to filter on, it produces the same guarded block a human would write::

    {% if region %} AND br.description IN {{ region | inclause }} {% endif %}

Two design decisions make this safe enough to run against a stranger's SQL.

**Inject at the innermost scope that exposes the column, not at the top.**
Appending a predicate to the outermost SELECT only works when the column
survives into the output — but real dashboard queries aggregate the dimension
away long before that (``SELECT call_date, SUM(...) ... GROUP BY call_date``
exposes no ``region`` at all, while a subquery six levels down does). So we
walk the scope tree innermost-first and inject where the column actually
lives, which is also where a human would put it: filtering the base rows
*before* they are rolled up. When the column is a projection alias we filter
on the underlying expression (``br.description``, not ``Region``) — a SELECT
alias is not referenceable from its own WHERE in MySQL or Postgres.

**Splice text; never regenerate.** Round-tripping real legacy SQL through a
generator reformats it wholesale (one query here went 6,058 → 7,889
characters), which destroys the author's formatting and comments and makes
the diff unreviewable. Instead we use the source offsets sqlglot records on
parsed nodes to insert the new block at one exact position, leaving every
other byte untouched.

Correctness is NOT assumed from either of those. The rewrite is a proposal;
the caller (see ``app.routes.query``'s parameterize endpoint) must verify it
by executing the rewritten query with the filter unset and confirming the
result is identical to the original's. A rewrite that changes unfiltered
output is rejected rather than persisted — the failure mode this guards
against is not an error but a *silently different number*, which is far worse
on a dashboard than a filter that refuses to attach.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import sqlglot
from sqlglot import exp
from sqlglot.optimizer.scope import traverse_scope


@dataclass
class InjectionResult:
    """Outcome of an attempted parameterisation.

    ``ok`` False means we could not find a safe place to put the filter; the
    caller should surface ``reason`` to the author rather than guessing.
    """

    ok: bool
    sql: str | None = None
    #: The expression the predicate filters on, e.g. ``br.description``.
    column_expr: str | None = None
    #: Human-readable explanation when ``ok`` is False.
    reason: str | None = None


#: Filter subtypes → the operator shape their guarded block uses.
MULTI_SUBTYPES = ("multiselect", "list")


def neutralize_jinja(sql: str) -> str:
    """Replace Jinja templating with SQL-safe filler of the SAME length.

    A query that already has one filter is exactly the query an author wants
    to add a second filter to — but sqlglot cannot parse ``{% if %}`` /
    ``{{ }}``, so analysing such a query raw fails and the feature refuses
    precisely where it is most wanted. (On this codebase's converted boards
    that is most of the estate: nearly every query carries a
    ``{% if country_description %}`` block.)

    Length is preserved byte-for-byte so every source offset sqlglot reports
    still points at the right character in the ORIGINAL text — which is what
    lets the caller splice into the original rather than a rewritten copy.

    Three substitutions, in order:

    1. ``{% else %}…{% endif %}`` → spaces. The else-branch must go entirely:
       keeping its body would leave two juxtaposed expressions
       (``br.country_id = NULL TRUE``) that do not parse.
    2. Remaining ``{% … %}`` tags → spaces, keeping the if-branch body, so
       ``{% if x %} AND c IN {{x}} {% endif %}`` reads as ``AND c IN NULL``.
    3. ``{{ … }}`` → ``NULL`` right-padded to the original width. The shortest
       possible token, ``{{x}}``, is 5 characters, so ``NULL`` always fits.
    """
    if "{%" not in sql and "{{" not in sql:
        return sql

    out = list(sql)

    def blank(start: int, end: int) -> None:
        for i in range(start, end):
            if out[i] != "\n":  # keep line structure for readable errors
                out[i] = " "

    # 1. else-branches (including their tags) disappear wholesale.
    for m in re.finditer(r"\{%-?\s*else\s*-?%\}.*?\{%-?\s*endif\s*-?%\}", sql, re.S | re.I):
        blank(m.start(), m.end())

    # 2. Every remaining control tag becomes whitespace, keeping its body.
    for m in re.finditer(r"\{%.*?%\}", "".join(out), re.S):
        blank(m.start(), m.end())

    # 3. Output expressions become a literal of identical width.
    for m in re.finditer(r"\{\{.*?\}\}", "".join(out), re.S):
        width = m.end() - m.start()
        filler = ("NULL" + " " * (width - 4)) if width >= 4 else " " * width
        for offset, ch in enumerate(filler):
            out[m.start() + offset] = ch

    return "".join(out)


def _parse(sql: str, dialect: str):
    """Parse *sql* for analysis, tolerating Jinja templating.

    Returns ``(tree, None)`` or ``(None, reason)``.
    """
    try:
        return sqlglot.parse_one(neutralize_jinja(sql), dialect=dialect), None
    except Exception as exc:  # noqa: BLE001 — a parse failure is an answer, not a crash
        return None, str(exc)


def _from_clause(select: exp.Select) -> exp.Expression | None:
    """The FROM clause of *select*, across sqlglot arg-name spellings.

    sqlglot stores this under ``from_`` in 30.x but ``from`` in earlier
    releases; reading only one spelling silently yields ``None`` on the other,
    which here would look like "this query has no FROM clause" and make every
    rewrite refuse. ``Select.args`` is checked directly rather than via a
    helper property so both layouts resolve.
    """
    return select.args.get("from_") or select.args.get("from")


def _max_end(node: exp.Expression | None) -> int | None:
    """Highest source offset (inclusive) covered by *node*'s subtree.

    sqlglot records ``start``/``end`` on the tokens it parsed, so the largest
    ``end`` under a clause is the last character that clause occupies in the
    original text — which is where the next clause may be inserted.
    """
    if node is None:
        return None
    best: int | None = None
    for child in node.walk():
        meta = getattr(child, "meta", None)
        if not meta:
            continue
        end = meta.get("end")
        if isinstance(end, int) and (best is None or end > best):
            best = end
    return best


def _exposed_projection(scope_expr: exp.Select, column: str) -> exp.Expression | None:
    """The projection in *scope_expr* that outputs *column*, if any.

    Matches on the projection's output name (its alias when aliased, else the
    column name), case-insensitively — that is the name a dashboard author
    sees and would pick from a column list.
    """
    target = column.strip().lower()
    for proj in scope_expr.expressions:
        if isinstance(proj, exp.Star):
            # A star may or may not carry the column; we cannot tell without
            # full schema resolution, so this scope is not a safe target.
            continue
        name = proj.alias_or_name
        if name and name.lower() == target:
            return proj
    return None


def _underlying_expr(projection: exp.Expression, dialect: str) -> str:
    """SQL text to filter on for *projection*.

    For ``br.description AS Region`` this is ``br.description``: the alias
    exists only in the SELECT list and cannot be referenced from the same
    scope's WHERE clause.
    """
    node = projection.this if isinstance(projection, exp.Alias) else projection
    return node.sql(dialect=dialect)


def build_filter_block(param: str, column_expr: str, subtype: str = "multiselect") -> str:
    """The guarded Jinja block for one filter param.

    An unset multiselect defaults to ``[]``, which is falsy in Jinja, so the
    whole block disappears and the query means "no filter" — this is what
    makes adding a filter a no-op until someone actually picks a value, and
    therefore what lets the caller verify the rewrite by comparing unfiltered
    output against the original.

    Values are always bound (``{{ x }}`` / ``| inclause``), never interpolated
    — ``| sqlsafe`` is deliberately not used here.
    """
    if subtype in MULTI_SUBTYPES:
        return f"{{% if {param} %}} AND {column_expr} IN {{{{ {param} | inclause }}}} {{% endif %}}"
    if subtype == "daterange":
        return (
            f"{{% if {param} %}} AND {column_expr} >= {{{{ {param}.from }}}} "
            f"AND {column_expr} < {{{{ {param}.to }}}} {{% endif %}}"
        )
    return f"{{% if {param} %}} AND {column_expr} = {{{{ {param} }}}} {{% endif %}}"


def parameterize_sql(
    sql: str,
    param: str,
    column: str,
    dialect: str = "mysql",
    subtype: str = "multiselect",
) -> InjectionResult:
    """Add a guarded filter on *column* to *sql*, bound to *param*.

    Returns an :class:`InjectionResult`; ``ok`` False carries a ``reason``
    suitable for showing an author. The returned SQL is a PROPOSAL — the
    caller must verify it executes and leaves unfiltered results unchanged
    before persisting it.
    """
    if not sql or not sql.strip():
        return InjectionResult(False, reason="The query has no SQL to modify.")
    if not param or not param.strip():
        return InjectionResult(False, reason="A parameter name is required.")
    if not column or not column.strip():
        return InjectionResult(False, reason="A column to filter on is required.")

    tree, parse_error = _parse(sql, dialect)
    if tree is None:
        return InjectionResult(
            False,
            reason=(
                "This query's SQL could not be parsed, so it cannot be modified "
                f"safely: {parse_error or 'unknown parse error'}"
            ),
        )

    # Innermost-first: traverse_scope yields children before parents, so the
    # first match is the deepest scope that exposes the column — the point
    # where filtering still happens before any roll-up above it.
    target_scope = None
    projection = None
    for scope in traverse_scope(tree):
        expr = scope.expression
        if not isinstance(expr, exp.Select):
            continue
        found = _exposed_projection(expr, column)
        if found is not None:
            target_scope = expr
            projection = found
            break

    if target_scope is None or projection is None:
        return InjectionResult(
            False,
            reason=(
                f"No part of this query exposes a {column!r} column, so there is "
                "nothing to filter on. Pick a column this query actually selects."
            ),
        )

    column_expr = _underlying_expr(projection, dialect)
    block = build_filter_block(param, column_expr, subtype)

    where = target_scope.args.get("where")
    if where is not None:
        anchor = _max_end(where)
        insert_text = f" {block}"
    else:
        # No WHERE in this scope — open one after its FROM/JOIN clauses. Any
        # GROUP BY / HAVING / ORDER BY that follows stays after the new WHERE,
        # which is the correct clause order.
        anchor = _max_end(_from_clause(target_scope))
        for join in target_scope.args.get("joins") or []:
            j_end = _max_end(join)
            if j_end is not None and (anchor is None or j_end > anchor):
                anchor = j_end
        insert_text = f" WHERE 1=1 {block}"

    if anchor is None:
        return InjectionResult(
            False,
            reason=(
                "Could not locate a safe insertion point in this query's SQL "
                "(no source positions available). Add the filter by hand."
            ),
        )

    # `end` is inclusive, so the insertion point is one past it.
    cut = anchor + 1
    new_sql = sql[:cut] + insert_text + sql[cut:]

    # Cheap self-check: the spliced result must still parse. Neutralise ALL
    # the Jinja, not just the block we added — the query may already carry
    # filters of its own, and leaving those in place would fail the parse for
    # a reason that has nothing to do with this edit.
    probe, probe_error = _parse(new_sql, dialect)
    if probe is None:
        return InjectionResult(
            False,
            reason=f"The modified SQL did not parse cleanly, so it was not applied: {probe_error}",
        )

    return InjectionResult(True, sql=new_sql, column_expr=column_expr)


def filterable_columns(sql: str, dialect: str = "mysql") -> list[dict[str, Any]]:
    """Columns anywhere in *sql* that a filter could be attached to.

    Returns one entry per distinct output name across every scope, innermost
    first, each with the underlying expression and whether it survives into
    the query's final output. The dashboard editor uses this to offer the
    author a real column list instead of asking them to type one.
    """
    tree, _ = _parse(sql, dialect)
    if tree is None:
        return []

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
        expr = scope.expression
        if not isinstance(expr, exp.Select):
            continue
        for proj in expr.expressions:
            if isinstance(proj, exp.Star):
                continue
            name = proj.alias_or_name
            if not name:
                continue
            key = name.lower()
            if key in seen:
                continue
            # Aggregates are not filterable dimensions — filtering on a SUM
            # would need HAVING, and offering it here would mislead.
            node = proj.this if isinstance(proj, exp.Alias) else proj
            if isinstance(node, exp.AggFunc) or node.find(exp.AggFunc) is not None:
                continue
            seen.add(key)
            out.append(
                {
                    "name": name,
                    "expr": _underlying_expr(proj, dialect),
                    "in_output": key in outer_names,
                }
            )
    return out
