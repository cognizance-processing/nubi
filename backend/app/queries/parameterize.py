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

from app.queries.scope_resolve import (
    exposed_projection as _exposed_projection,
    filterable_surface,
    find_filter_target,
    underlying_expr as _underlying_expr,
)


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
    3. ``IN {{ … }}`` → ``IN(NULL)`` right-padded, because ``IN NULL`` is not
       valid SQL and sqlglot silently drops the operand (see below).
    4. Any remaining ``{{ … }}`` → ``NULL`` right-padded to the original width.
       The shortest possible token, ``{{x}}``, is 5 characters, so ``NULL``
       always fits.
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

    # 3a. `IN {{ x }}` needs a LIST, not a bare scalar. `col IN NULL` is not
    #     valid SQL in any dialect; sqlglot parses it leniently and DROPS the
    #     operand, which drags the clause's apparent end offset backwards onto
    #     the column name. The next filter then splices mid-expression
    #     (`AND region <here> IN NULL`) and the parse check refuses it.
    #
    #     That shape is the one this very module emits for multiselect
    #     filters, so without this pass a query can be given exactly ONE
    #     multiselect filter and every later one fails. `IN(NULL)` is eight
    #     characters — the exact width of the shortest possible `IN {{x}}` —
    #     so it always fits and the offsets survive.
    _IN_FILLER = "IN(NULL)"
    for m in re.finditer(r"\bIN\s+\{\{.*?\}\}", "".join(out), re.S | re.I):
        width = m.end() - m.start()
        if width < len(_IN_FILLER):
            continue
        if "\n" in sql[m.start() : m.start() + len(_IN_FILLER)]:
            continue  # would clobber a newline; leave it to the parse check
        for offset, ch in enumerate(_IN_FILLER):
            out[m.start() + offset] = ch
        blank(m.start() + len(_IN_FILLER), m.end())

    # 3b. Remaining output expressions become a literal of identical width.
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


def _scope_start(node: exp.Expression | None) -> int:
    """Lowest source offset covered by *node*'s subtree (0 when unknown).

    Used to bound paren counting to the target scope's OWN text. Parens that
    open before a scope begins are the ones enclosing it — a derived table's
    ``(`` — and stepping over their ``)`` would push the insertion outside the
    subquery being filtered.
    """
    best: int | None = None
    for child in (node.walk() if node is not None else []):
        meta = getattr(child, "meta", None)
        if not meta:
            continue
        start = meta.get("start")
        if isinstance(start, int) and (best is None or start < best):
            best = start
    return best or 0


def _significant(sql: str) -> str:
    """*sql* with string literals and comments blanked to spaces, same length.

    Paren counting must ignore parentheses that live inside a literal
    (``WHERE note = 'a)b'``) or a comment — counting those would mis-locate the
    insertion point in exactly the queries where being wrong is hardest to
    spot. Length is preserved so offsets keep pointing at the original text.
    """
    out = list(sql)
    i, n = 0, len(sql)
    while i < n:
        ch = sql[i]
        if ch in ("'", '"', "`"):
            j = i + 1
            while j < n:
                if sql[j] == "\\":
                    j += 2
                    continue
                if sql[j] == ch:
                    # Doubled quote is an escaped quote, not a terminator.
                    if j + 1 < n and sql[j + 1] == ch:
                        j += 2
                        continue
                    break
                j += 1
            for k in range(i, min(j + 1, n)):
                if out[k] != "\n":
                    out[k] = " "
            i = j + 1
            continue
        if ch == "-" and i + 1 < n and sql[i + 1] == "-":
            j = sql.find("\n", i)
            j = n if j == -1 else j
            for k in range(i, j):
                out[k] = " "
            i = j
            continue
        if ch == "/" and i + 1 < n and sql[i + 1] == "*":
            j = sql.find("*/", i + 2)
            j = n if j == -1 else j + 2
            for k in range(i, j):
                if out[k] != "\n":
                    out[k] = " "
            i = j
            continue
        i += 1
    return "".join(out)


#: Keywords that can only begin the clause AFTER a WHERE, so reaching one
#: means the WHERE clause is over. Word-boundary matched, case-insensitive.
_CLAUSE_TERMINATORS = (
    "group", "order", "having", "limit", "offset", "window", "qualify",
    "union", "intersect", "except", "fetch", "returning", "into", "for",
)


def _clause_end(sql: str, scope_start: int, anchor: int) -> int:
    """The offset just past the end of the clause anchored at *anchor*.

    ``_max_end`` can only see as far as the last token sqlglot attached a
    source offset to, and it does not attach one to every node — bare keyword
    operands (``NULL``, ``TRUE``) and the ``)`` that closes a subquery carry no
    offset at all. A WHERE clause ending in either therefore reports an end
    somewhere in its middle, and splicing there lands mid-expression.

    So the token offset is a starting point, not an answer. From there this
    walks the SOURCE text forward — over remaining tokens, balanced
    parentheses, and any Jinja tags belonging to filters already present —
    and stops at the first keyword that can only start the next clause, or at
    a ``)`` that would take us out of the scope being filtered.

    Scanning the source rather than the tree is also what keeps an existing
    ``{% endif %}`` on the correct side of the insertion: a second filter must
    be appended AFTER the first one's guard closes, not inside it.
    """
    n = len(sql)
    pos = min(max(anchor + 1, 0), n)
    depth = 0  # parens opened since scope_start and still open
    scan = _significant(sql)
    for i in range(max(scope_start, 0), pos):
        if scan[i] == "(":
            depth += 1
        elif scan[i] == ")":
            depth = max(depth - 1, 0)

    end = pos
    while pos < n:
        if sql[pos].isspace():
            pos += 1
            continue
        # Comments belong to whatever follows them, so step over without
        # moving `end` — a trailing comment must not be cut in half.
        if sql.startswith("--", pos):
            nl = sql.find("\n", pos)
            pos = n if nl == -1 else nl
            continue
        if sql.startswith("/*", pos):
            close = sql.find("*/", pos + 2)
            pos = n if close == -1 else close + 2
            continue
        # Jinja tags: part of the clause (an existing filter's guard).
        if sql.startswith("{%", pos) or sql.startswith("{{", pos):
            close = sql.find("%}" if sql[pos + 1] == "%" else "}}", pos + 2)
            if close == -1:
                break
            pos = end = close + 2
            continue
        ch = sql[pos]
        if ch == "(":
            depth += 1
            pos = end = pos + 1
            continue
        if ch == ")":
            if depth == 0:
                break  # this ``)`` closes the scope itself — stop before it
            depth -= 1
            pos = end = pos + 1
            continue
        if ch in ("'", '"', "`"):
            j = pos + 1
            while j < n:
                if sql[j] == "\\":
                    j += 2
                    continue
                if sql[j] == ch:
                    if j + 1 < n and sql[j + 1] == ch:
                        j += 2
                        continue
                    break
                j += 1
            pos = end = min(j + 1, n)
            continue
        word = re.match(r"[A-Za-z_][A-Za-z0-9_$]*", sql[pos:])
        if word:
            # Only at paren depth 0 does a keyword end the clause; inside a
            # subquery an ORDER BY / LIMIT is that subquery's own business.
            if depth == 0 and word.group(0).lower() in _CLAUSE_TERMINATORS:
                break
            pos = end = pos + word.end()
            continue
        pos = end = pos + 1

    return end


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


def _base_table_for(scope, node: exp.Expression, depth: int = 0):
    """Follow *node* back to the real table it reads from.

    Returns ``(table, column)`` — the ``exp.Table`` the value ultimately comes
    from and the column name on it — or ``(None, None)`` when the value is not
    a plain column of a resolvable table (a CASE expression, a function call,
    an unresolvable star).

    Real warehouse queries stack CTEs and derived tables, so the column a
    dashboard author picks is often two or three hops from a physical table.
    Each hop is followed by matching the output name against the inner scope's
    projections, exactly as a reader would trace it by eye.
    """
    if depth > 5 or scope is None or not isinstance(node, exp.Column):
        return None, None

    source = None
    qualifier = node.table
    sources = getattr(scope, "sources", {}) or {}
    if qualifier:
        source = sources.get(qualifier)
    elif len(sources) == 1:
        # Unqualified and only one source — unambiguous.
        source = next(iter(sources.values()))
    if source is None:
        # Unqualified across a join: the column could come from either side and
        # guessing would produce a plausible-looking wrong list.
        return None, "__ambiguous__"

    if isinstance(source, exp.Table):
        return source, node.name

    inner_expr = getattr(source, "expression", None)
    if not isinstance(inner_expr, exp.Select):
        return None, None
    inner_proj = _exposed_projection(inner_expr, node.name)
    if inner_proj is None:
        return None, None
    inner_node = inner_proj.this if isinstance(inner_proj, exp.Alias) else inner_proj
    return _base_table_for(source, inner_node, depth + 1)


@dataclass
class OptionsQuery:
    """A generated "what are the choices?" query for a dropdown filter."""

    ok: bool
    sql: str | None = None
    #: Rendered name of the table the choices are read from, for the UI.
    source: str | None = None
    reason: str | None = None


def options_query_sql(
    sql: str,
    column: str,
    dialect: str = "postgres",
    limit: int = 1000,
    table_columns: dict[str, list[str]] | None = None,
) -> OptionsQuery:
    """Build the query that lists a dropdown filter's choices.

    A ``select`` / ``multiselect`` filter needs somewhere to get its options
    from, and until now that was the author's problem: the picker could only
    choose an existing query, so adding a filter to a column nobody had built
    a lookup for left a working filter with an empty dropdown. This derives it
    from the same column the filter was just attached to.

    Built as a sqlglot AST and rendered with the target *dialect*, so identifier
    quoting, row limiting and schema qualification come out native for whichever
    engine the query runs against — the same ``dialect_for(connector_type)``
    the planner uses. Nothing here is engine-specific.

    ``ok`` False means the choices cannot be derived (the column is computed,
    or comes from an ambiguous join) and the author should pick or write a
    query themselves — which is a better outcome than a plausible-looking list
    that is subtly wrong.
    """
    if not sql or not sql.strip():
        return OptionsQuery(False, reason="The query has no SQL to read.")
    if not column or not column.strip():
        return OptionsQuery(False, reason="A column is required.")

    tree, parse_error = _parse(sql, dialect)
    if tree is None:
        return OptionsQuery(
            False,
            reason=f"This query's SQL could not be parsed: {parse_error or 'unknown error'}",
        )

    target = find_filter_target(tree, column, dialect=dialect, table_columns=table_columns)
    if target is None:
        return OptionsQuery(
            False, reason=f"No part of this query exposes a {column!r} column."
        )

    if target.base_table is not None:
        # Schema match: the column is a real column on a base table this
        # scope reads from directly, not something projected and then traced
        # back through possibly-nested derived tables — we already have the
        # table, no need to chase.
        table, col_name = target.base_table, target.base_column
    else:
        node = target.projection.this if isinstance(target.projection, exp.Alias) else target.projection
        table, col_name = _base_table_for(target.scope, node)
    if col_name == "__ambiguous__":
        return OptionsQuery(
            False,
            reason=(
                f"{column!r} is not qualified with a table and this query joins "
                "more than one, so which table it comes from is ambiguous. "
                "Qualify it in the SQL, or pick an options query yourself."
            ),
        )
    if table is None or not col_name:
        return OptionsQuery(
            False,
            reason=(
                f"{column!r} is computed inside this query rather than read "
                "straight from a table, so its list of choices cannot be "
                "derived automatically. Pick an existing options query, or "
                "write one returning value, label."
            ),
        )

    # Read from the base table directly rather than through the original query:
    # the choices should not themselves be narrowed by whatever that query
    # filters on, or the dropdown would hide values the filter can still match.
    return _render_options_query(table, col_name, dialect, limit)


def _render_options_query(
    table: exp.Table, col_name: str, dialect: str, limit: int
) -> OptionsQuery:
    """Render ``SELECT DISTINCT col AS value, col AS label FROM table`` for *dialect*.

    The single generator behind both entry points — deriving from an existing
    query's SQL, and building from a table/column the author picked directly.
    Two callers producing two spellings of the same query would defeat the
    de-duplication that keeps one options query per column.
    """
    src = table.copy()
    src.set("joins", None)
    # Always alias to the table's own base name, DISCARDING whatever a source
    # query called it. Two reasons: a bare multi-part name is not a valid column
    # qualifier on every engine (`analytics.public.invoices.plan` is not), and
    # the generated text must be identical for the same physical column however
    # the query that prompted it happened to alias its tables — otherwise four
    # widgets filtering one column leave four identical-but-differently-spelled
    # options queries behind.
    alias = src.name
    src.set("alias", exp.TableAlias(this=exp.to_identifier(alias)))
    value = exp.column(col_name, alias)

    select = (
        exp.select(
            exp.alias_(value.copy(), "value"),
            exp.alias_(value.copy(), "label"),
        )
        .distinct()
        .from_(src)
        .where(exp.Not(this=exp.Is(this=value.copy(), expression=exp.Null())))
        .order_by(value.copy())
        .limit(limit)
    )

    try:
        rendered = select.sql(dialect=dialect)
    except Exception as exc:  # noqa: BLE001 — a generation failure is an answer
        return OptionsQuery(False, reason=f"Could not generate the options query: {exc}")

    return OptionsQuery(True, sql=rendered, source=src.sql(dialect=dialect))


def options_query_for_table(
    table: str,
    column: str,
    schema: str | None = None,
    dialect: str = "postgres",
    limit: int = 1000,
) -> OptionsQuery:
    """Build a dropdown's choices query from a table and column directly.

    The counterpart to :func:`options_query_sql`: that one infers the source by
    tracing a column back through an existing query, this one is handed the
    source. It backs the query picker's "build one from a column" path, for the
    cases inference cannot serve — a filter not yet connected to any widget, or
    a column that is computed in every query that mentions it.

    Identifiers arrive from an author's picker choice, so they are parsed as
    identifiers via sqlglot rather than interpolated: a name that does not
    round-trip to a single table/column reference is rejected instead of being
    pasted into SQL.
    """
    if not table or not table.strip():
        return OptionsQuery(False, reason="A table is required.")
    if not column or not column.strip():
        return OptionsQuery(False, reason="A column is required.")

    parts = [p for p in ([schema.strip()] if schema and schema.strip() else []) + [table.strip()]]
    try:
        src = exp.to_table(".".join(parts), dialect=dialect)
        col_node = exp.to_column(column.strip(), dialect=dialect)
    except Exception as exc:  # noqa: BLE001
        return OptionsQuery(False, reason=f"Not a usable table or column name: {exc}")

    if not isinstance(src, exp.Table) or not src.name:
        return OptionsQuery(False, reason=f"{table!r} is not a valid table name.")
    if not isinstance(col_node, exp.Column) or col_node.table:
        # A qualified name here would silently disagree with the table above.
        return OptionsQuery(
            False, reason=f"{column!r} must be a plain column name, unqualified."
        )

    # Require an exact round-trip. sqlglot QUOTES a hostile identifier rather
    # than letting it break out — `inv; DROP TABLE x` becomes one quoted name,
    # so this is not the injection boundary — but it also silently REWRITES
    # some input: `inv--x` parses as `inv` plus a trailing comment, and
    # `a.b.c.d` as a four-part path. Either way the object we would query is
    # not the one the author named, and a query against a subtly different
    # object is worse than a refusal.
    if src.name != table.strip() or (src.db or None) != (schema.strip() if schema else None):
        return OptionsQuery(
            False,
            reason=f"{table!r} is not a plain table name; qualify it with the schema field instead.",
        )
    if src.catalog:
        return OptionsQuery(False, reason=f"{table!r} is not a plain table name.")
    if col_node.name != column.strip():
        return OptionsQuery(False, reason=f"{column!r} is not a plain column name.")

    return _render_options_query(src, col_node.name, dialect, limit)


def parameterize_sql(
    sql: str,
    param: str,
    column: str,
    dialect: str = "mysql",
    subtype: str = "multiselect",
    table_columns: dict[str, list[str]] | None = None,
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

    # Innermost-first: find_filter_target walks scopes children-before-parents,
    # so the first match is the deepest scope that exposes the column — the
    # point where filtering still happens before any roll-up above it.
    target = find_filter_target(tree, column, dialect=dialect, table_columns=table_columns)

    if target is None:
        return InjectionResult(
            False,
            reason=(
                f"No part of this query exposes a {column!r} column, so there is "
                "nothing to filter on. Pick a column this query actually selects."
            ),
        )

    target_scope = target.select
    block = build_filter_block(param, target.column_expr, subtype)

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

    # `end` is inclusive, but it is only a lower bound on where the clause
    # really ends — see :func:`_clause_end`.
    cut = _clause_end(sql, _scope_start(target_scope), anchor)
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

    return InjectionResult(True, sql=new_sql, column_expr=target.column_expr)


def filterable_columns(
    sql: str,
    dialect: str = "mysql",
    table_columns: dict[str, list[str]] | None = None,
) -> list[dict[str, Any]]:
    """Columns anywhere in *sql* that a filter could be attached to.

    Returns one entry per distinct output name across every scope, innermost
    first, each with the expression to filter on and whether it survives into
    the query's final output. The dashboard editor uses this to offer the
    author a real column list instead of asking them to type one.

    When *table_columns* is given (an estate-wide ``{table: [col, ...]}``
    catalog — see ``app.ai.grounding.build_catalog``), the list also includes
    columns that exist on a scope's own base tables but are never projected
    or otherwise referenced by this particular query — e.g. a `month` column
    that filters an aggregate-only KPI subquery which happens not to mention
    it. Without a catalog, only columns the SQL itself references are listed
    (the original, narrower behaviour).
    """
    tree, _ = _parse(sql, dialect)
    if tree is None:
        return []
    return filterable_surface(tree, dialect=dialect, table_columns=table_columns)
