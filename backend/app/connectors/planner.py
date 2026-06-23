"""Query planner — compile ``(sql, claims, projection)`` into a ``PhysicalPlan``.

Pipeline (M2-A extended)
------------------------
1. Parse the SQL with sqlglot (AST, no string manipulation).
2. Reject non-SELECT statements immediately (security: no arbitrary DML/DDL).
3. Optionally narrow the column list to ``projection`` via ``optimize.prune_projection``.
4. Push any caller-supplied ``predicates`` into WHERE via ``optimize.push_predicates``.
5. Inject RLS predicates from ``claims["policies"]`` as AST-level ``col = value``
   equalities added to the WHERE clause.  NEVER string-concat.
6. Optionally push a ``limit`` via ``optimize.push_limit``.
7. Re-generate SQL from the modified AST.
8. Compute the cache key via ``cache_key.compute_cache_key`` (uses final SQL).
9. Return a frozen ``PhysicalPlan``.

Backwards-compatibility guarantee (M1 conformance)
---------------------------------------------------
When called with no ``limit`` and no ``predicates`` arguments the pipeline
produces EXACTLY the same SQL and cache_key as the M1 planner.  Steps 4 and 6
are no-ops in that case, so the transform order and output are unchanged.

RLS contract (M1 — equality-only)
-----------------------------------
``claims["policies"]`` is a ``{column: value}`` dict.  Each entry becomes an
``AND col = 'value'`` predicate added to the WHERE clause.  The cache key
derives from exactly this same dict so the two are always in sync.

Thread safety
-------------
The planner is stateless; ``plan()`` is safe to call concurrently.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterator

import jinja2
import sqlglot
import sqlglot.expressions as exp

from app.connectors.cache_key import compute_cache_key
from app.connectors.optimize import prune_projection, push_limit, push_predicates
from app.connectors.plan import PhysicalPlan
from app.connectors.query_log import compute_groupby_sig
from app.connectors.sql_parse import parse_sql_cached
from app.connectors.template import render_sql_template
from app.errors import AppError

# Default dialect for SQL generation.  Overridable per-call.
_DEFAULT_DIALECT: str = "postgres"

# Regex kept for reference; actual substitution is now handled by the Jinja2
# engine in template.py — this is no longer used directly in resolve_named_params.
_NAMED_PLACEHOLDER_RE = re.compile(r"\{\{([A-Za-z_][A-Za-z0-9_]*)\}\}")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _make_eq_predicate(column: str, value: Any) -> exp.EQ:
    """Build an AST ``column = value`` equality node for a single RLS claim.

    Parameters
    ----------
    column:
        The column name (unquoted identifier).
    value:
        The scalar value.  Typed mapping:
        - ``bool``  → ``exp.Boolean``
        - ``int`` / ``float`` → ``exp.Literal.number``
        - anything else → ``exp.Literal.string(str(value))``

    Returns
    -------
    exp.EQ
        A sqlglot EQ expression node ready to be added to a WHERE clause.
    """
    lhs = exp.Column(this=exp.Identifier(this=column, quoted=False))

    if isinstance(value, bool):
        rhs: exp.Expression = exp.Boolean(this=value)
    elif isinstance(value, (int, float)):
        rhs = exp.Literal.number(value)
    else:
        rhs = exp.Literal.string(str(value))

    return exp.EQ(this=lhs, expression=rhs)


def _collect_predicates(node: exp.Expression | None, dialect: str) -> list[str]:
    """Walk an AND-tree and collect individual predicate strings.

    Parameters
    ----------
    node:
        The WHERE condition node (may be a nested AND tree).
    dialect:
        sqlglot dialect for SQL generation.

    Returns
    -------
    list[str]
        Each leaf predicate as a SQL string.
    """
    if node is None:
        return []

    def _walk(n: exp.Expression) -> Iterator[str]:
        if isinstance(n, exp.And):
            yield from _walk(n.left)
            yield from _walk(n.right)
        else:
            yield n.sql(dialect=dialect)

    return list(_walk(node))


# ---------------------------------------------------------------------------
# Named-param resolution (M13-A)
# ---------------------------------------------------------------------------


def resolve_named_params(
    sql: str,
    named_values: dict[str, Any],
    dialect: str = _DEFAULT_DIALECT,
) -> tuple[str, list[Any]]:
    """Resolve ``{{name}}`` placeholders in *sql* to positional ``$N`` bindings.

    Algorithm
    ---------
    1. Scan *sql* for ``{{name}}`` tokens in order of appearance.
    2. For each unique name encountered (in order), assign the next ``$N`` slot
       and append the resolved value from *named_values* to the params list.
    3. Replace every occurrence of ``{{name}}`` with the assigned ``$N`` string.
    4. Return ``(rewritten_sql, positional_params_list)``.

    The result feeds directly into the planner as ``(sql, params=...)``.
    Values are NEVER string-concatenated — they are bound positionally so the
    connector's parameterised query interface (asyncpg ``$N``, DuckDB ``$N``)
    handles quoting and type casting safely.

    Parameters
    ----------
    sql:
        The raw SQL string from the registry, which may contain ``{{name}}``
        placeholders.
    named_values:
        Mapping of placeholder name → resolved value.  All names that appear in
        *sql* MUST be present; missing keys raise ``KeyError``.
    dialect:
        sqlglot dialect (unused in this helper — included for symmetry with
        other planner helpers so callers can pass it through).

    Returns
    -------
    tuple[str, list[Any]]
        ``(rewritten_sql, positional_params)`` where every ``{{name}}`` has been
        replaced by ``$N`` and the corresponding value appended to the list.

    Notes
    -----
    Each unique *name* in the SQL maps to exactly ONE ``$N`` slot.  If the same
    name appears multiple times in the SQL, all occurrences are replaced with the
    SAME ``$N`` and the value appears only once in the params list.  This mirrors
    asyncpg's parameterised query semantics where ``$1`` can appear multiple times
    but refers to the same bound value.
    """
    # Delegate to the Jinja2-based template engine (template.py).
    #
    # render_sql_template handles:
    #   - Simple {{ name }} placeholders (backward-compatible with old regex)
    #   - Conditional blocks: {% if region %} AND region = {{ region }} {% endif %}
    #   - Loops: {% for x in items %} … {% endfor %}
    #   - IN clauses: {{ ids | inclause }} → ($1, $2, $3)
    #   - Raw SQL escape hatch: {{ val | sqlsafe }} (trusted values only)
    #
    # All {{ expr }} outputs are bound as positional parameters — raw values
    # are NEVER interpolated into the SQL string.
    #
    # Backward-compatibility note:
    #   The old regex assigned a single $N slot per unique name (so the same
    #   {{name}} appearing twice → same $1).  The Jinja2 engine evaluates each
    #   {{ }} independently, so two occurrences of {{ name }} produce TWO
    #   parameter slots with the same value.  Both styles are functionally
    #   equivalent when executed by asyncpg / DuckDB (the value is bound twice,
    #   but the result is identical).  For the common case of a name referenced
    #   exactly once the behaviour is identical.
    try:
        rewritten, params = render_sql_template(sql, named_values, dialect=dialect)
    except jinja2.UndefinedError as exc:
        # Convert Jinja2's UndefinedError to KeyError so callers that relied on
        # the old regex engine's KeyError behaviour (e.g. existing tests) still
        # receive the same exception type for a missing placeholder name.
        # Extract the variable name from the message when possible.
        msg = str(exc)
        # Jinja2 UndefinedError message format: "'name' is undefined"
        raise KeyError(msg) from exc
    return rewritten, params


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def plan(
    sql: str,
    claims: dict[str, Any] | None = None,
    projection: list[str] | None = None,
    dialect: str = _DEFAULT_DIALECT,
    params: list[Any] | None = None,
    limit: int | None = None,
    predicates: list[Any] | None = None,
) -> PhysicalPlan:
    """Compile a logical SQL query and optional claims into a ``PhysicalPlan``.

    Parameters
    ----------
    sql:
        The logical SQL query string (SELECT only — anything else raises
        ``AppError("UNSUPPORTED_QUERY", 400)``).
    claims:
        JWT / auth claims dict.  Only ``claims["policies"]`` is used for RLS
        predicate injection; all other claims are ignored by the planner (they
        may be checked by the auth layer before planning).
    projection:
        If given, the SELECT list is replaced with exactly these column names.
        Useful for push-down projection to reduce bytes over the wire.
    dialect:
        sqlglot dialect for parsing and generation.  Default ``"postgres"``.
    params:
        Positional query parameters (passed through unchanged into the plan).
        Default ``[]``.
    limit:
        Optional row limit to push down into the query.  If the SQL already
        contains a LIMIT that is smaller than *limit*, the existing (smaller)
        LIMIT is kept.  ``None`` means no limit is pushed (M1-compatible path).
    predicates:
        Optional list of extra predicates to push into WHERE before RLS injection.
        Each element may be a ``(col, op, value)`` tuple, a single-key ``{col:
        value}`` dict, or a raw SQL string (trusted; not user-supplied).
        ``None`` means no extra predicates are pushed (M1-compatible path).

    Returns
    -------
    PhysicalPlan
        A frozen, JSON-serialisable plan ready for the executor.

    Raises
    ------
    app.errors.AppError
        ``code="UNSUPPORTED_QUERY"`` (status 400) if the SQL is not a SELECT
        statement.
        ``code="INVALID_SQL"`` (status 400) if sqlglot cannot parse the SQL.
    """
    if params is None:
        params = []
    if claims is None:
        claims = {}

    # ── 1. Parse ────────────────────────────────────────────────────────────
    try:
        tree = parse_sql_cached(sql, dialect=dialect)
    except sqlglot.errors.SqlglotError as exc:
        raise AppError("INVALID_SQL", f"Failed to parse SQL: {exc}", status=400) from exc

    # ── 2. Reject non-SELECT (but allow Union by wrapping it) ───────────────
    # The metric compiler emits UNION ALL for top_n.other queries.  A bare
    # exp.Union cannot have WHERE predicates added to it (there is no single
    # WHERE to inject into), so we wrap it in a SELECT * FROM (...) subquery
    # first.  RLS predicates then land on the wrapper SELECT, which is correct:
    # the wrapper filters after the union, equivalent to filtering both arms.
    # We still reject any non-Select that is NOT a Union (no DML/DDL/etc).
    if isinstance(tree, exp.Union):
        tree = exp.select("*").from_(
            exp.Subquery(this=tree, alias=exp.TableAlias(this=exp.to_identifier("_union_wrapper")))
        )
    elif not isinstance(tree, exp.Select):
        raise AppError(
            "UNSUPPORTED_QUERY",
            "Only SELECT statements are supported by the Nubi planner.",
            status=400,
        )

    # ── 3. Apply projection (via optimizer) ─────────────────────────────────
    if projection:
        tree = prune_projection(tree, projection)

    # ── 4. Push extra caller-supplied predicates (M2-A; no-op when None) ────
    if predicates:
        tree = push_predicates(tree, predicates)

    # ── 5. Inject RLS predicates (AST-level, NEVER string-concat) ───────────
    policies: dict[str, Any] = {}
    raw_policies = claims.get("policies", {})
    if isinstance(raw_policies, dict):
        policies = raw_policies

    injected_predicates: list[str] = []
    for col_name, col_value in sorted(policies.items()):
        pred_node = _make_eq_predicate(col_name, col_value)
        tree = tree.where(pred_node)
        injected_predicates.append(pred_node.sql(dialect=dialect))

    # ── 6. Push LIMIT (M2-A; no-op when None) ───────────────────────────────
    if limit is not None:
        tree = push_limit(tree, limit)

    # ── 7. Collect all predicates (original + extra + injected) ─────────────
    where_node = tree.args.get("where")
    all_predicates = _collect_predicates(
        where_node.this if where_node else None, dialect=dialect
    )

    # ── 8. Re-generate SQL ───────────────────────────────────────────────────
    rewritten_sql: str = tree.sql(dialect=dialect)

    # ── 9. Compute cache key from the FINAL SQL + params + RLS claims ────────
    cache_key = compute_cache_key(
        sql=rewritten_sql,
        params=params,
        rls_claims=claims,
    )

    # ── 10. Build and return the plan ────────────────────────────────────────
    return PhysicalPlan(
        dialect=dialect,
        sql=rewritten_sql,
        params=params,
        projection=projection,
        predicates=all_predicates,
        rls_claims=claims,
        cache_key=cache_key,
    )


# ---------------------------------------------------------------------------
# M2-C: Rollup routing (opt-in; does NOT change plan() behaviour)
# ---------------------------------------------------------------------------


def route_to_rollup(plan: PhysicalPlan, registry: Any) -> PhysicalPlan:
    """Rewrite *plan* to query a registered rollup table when one is available.

    This function is opt-in and completely separate from ``plan()``.  Call it
    after ``plan()`` if you want rollup routing; do **not** call it if you want
    the original plan (e.g. in conformance tests).

    Algorithm
    ---------
    1. Compute the ``groupby_sig`` of ``plan.sql`` using the same normalisation
       helper as ``query_log.compute_groupby_sig`` (single source of truth).
    2. Look up the sig in *registry* (a ``RollupRegistry`` instance).
    3. If a rollup table is registered:
       a. Parse ``plan.sql`` with sqlglot.
       b. Find all ``From`` / ``Join`` table references and replace the primary
          table's name with the rollup table name via AST rewrite (no string
          manipulation).
       c. Re-generate SQL and recompute the cache key.
       d. Return a new ``PhysicalPlan`` with the rewritten SQL + new cache key
          (all other fields preserved).
    4. If no rollup is registered, return *plan* unchanged (same object,
       same ``cache_key``).

    Parameters
    ----------
    plan:
        The ``PhysicalPlan`` produced by ``plan()``.
    registry:
        A ``RollupRegistry`` instance (use ``preagg.get_registry()`` for the
        singleton or pass a fresh instance in tests).

    Returns
    -------
    PhysicalPlan
        Either the rewritten plan (rollup hit) or the original plan (no match).

    Notes
    -----
    - The original ``plan()`` is never touched; M1 conformance keys are
      unchanged because ``route_to_rollup`` is only called when a rollup is
      registered, which tests control explicitly.
    - RLS claims are preserved in the returned plan so downstream audit/logging
      is not affected.
    """
    sig = compute_groupby_sig(plan.sql, dialect=plan.dialect)
    if sig is None:
        return plan  # No GROUP BY — nothing to route.

    rollup_table = registry.lookup(sig)
    if rollup_table is None:
        return plan  # No registered rollup for this pattern.

    # ── Rewrite the FROM clause via sqlglot AST ──────────────────────────────
    try:
        tree = parse_sql_cached(plan.sql, dialect=plan.dialect)
    except Exception:
        return plan  # Parse failure — return original plan unchanged.

    if not isinstance(tree, exp.Select):
        return plan

    # Replace all Table nodes whose name matches the base_table in the sig.
    # The base_table is the first component of the sig (before the first "|").
    base_table = sig.split("|")[0].split(",")[0]  # primary table name (first)

    # Walk all From and Join table references and replace the primary table.
    _replaced = False
    for table_node in tree.find_all(exp.Table):
        if table_node.name.lower() == base_table.lower():
            # Replace in-place on the AST node.
            table_node.set("this", exp.Identifier(this=rollup_table, quoted=False))
            _replaced = True
            break  # Only replace the first (primary) table occurrence.

    if not _replaced:
        return plan  # Could not find the table to replace.

    rewritten_sql = tree.sql(dialect=plan.dialect)

    new_cache_key = compute_cache_key(
        sql=rewritten_sql,
        params=plan.params,
        rls_claims=plan.rls_claims,
    )

    return PhysicalPlan(
        dialect=plan.dialect,
        sql=rewritten_sql,
        params=list(plan.params),
        projection=plan.projection,
        predicates=list(plan.predicates),
        rls_claims=dict(plan.rls_claims),
        cache_key=new_cache_key,
    )


# ---------------------------------------------------------------------------
# Auto pre-aggregations: conservative SOUND superset-rewrite router
# ---------------------------------------------------------------------------

# Aggregate functions that are *re-aggregable* over a pre-grouped rollup, and
# the function used to roll partial results back up.  AVG / COUNT(DISTINCT) /
# MEDIAN / PERCENTILE are deliberately absent — they are NOT derivable from a
# coarser pre-aggregate, so a query using them is left untouched (sound by
# omission).
_REAGG: dict[str, str] = {
    "sum": "SUM",
    "count": "SUM",   # COUNT rolls up by SUMming the partial counts
    "min": "MIN",
    "max": "MAX",
}


@dataclass(frozen=True)
class RollupRouteResult:
    """Outcome of :func:`route_to_rollup_shape`.

    Attributes
    ----------
    plan:
        The (possibly rewritten) plan.  Identical object to the input when no
        sound rewrite was found.
    routed:
        ``True`` when the query was rewritten to read a rollup (a HIT).
    rollup_id:
        The id of the rollup that was used (``None`` when not routed).
    reason:
        Human-readable explanation (for observability / debugging).
    pruned_partitions:
        Partition columns the query's WHERE clause constrains AND the routed
        rollup is partitioned by.  Surfaced for observability so the executor /
        layout layer can range-prune the Parquet partitions it scans.  Empty
        when the query is not routed or carries no partition predicate.  This is
        an *informational* field — pruning never weakens RLS (the WHERE clause,
        including any injected RLS predicate, is always preserved verbatim).
    """

    plan: PhysicalPlan
    routed: bool
    rollup_id: str | None = None
    reason: str = ""
    pruned_partitions: tuple[str, ...] = ()


def _measure_alias(func: str, col: str) -> str:
    """Rollup column alias for a ``func(col)`` measure (mirror of preagg)."""
    if col == "*":
        return f"{func.lower()}_all"
    return f"{func.lower()}_{col}"


def _extract_layered_cte(
    sql: str, dialect: str
) -> tuple[str, str] | None:
    """Detect a single-CTE ``WITH __base AS (<inner>) <outer>`` form.

    Returns ``(inner_sql, outer_sql)`` if the SQL matches this exact form,
    or ``None`` otherwise (flat query or multi-CTE — leave untouched).

    Conservative: any parse failure, unexpected CTE count, or non-``__base``
    CTE name returns ``None`` (caller treats as non-routable).
    """
    try:
        tree = parse_sql_cached(sql, dialect=dialect)
    except Exception:
        return None
    if not isinstance(tree, exp.Select):
        return None

    # sqlglot wraps WITH … SELECT as a exp.Subquery with .with_ or the
    # entire thing is a Select that has `with` in its args.
    # Check for a WITH clause on the outermost SELECT.
    # sqlglot uses "with_" (trailing underscore) as the key in Select.args.
    with_node = tree.args.get("with_") or tree.args.get("with")
    if with_node is None:
        return None

    ctes = with_node.expressions  # list[exp.CTE]
    if len(ctes) != 1:
        return None  # multi-CTE — too complex, leave untouched.

    cte = ctes[0]
    cte_alias = cte.alias_or_name
    if cte_alias.lower() != "__base":
        return None  # not our compiler's CTE — don't touch.

    # Extract the inner SELECT from the CTE body.
    inner_node = cte.this
    if not isinstance(inner_node, exp.Select):
        return None

    # The outer SELECT references __base; strip the WITH clause from the tree
    # to get the standalone outer SELECT.
    outer_tree = tree.copy()
    # sqlglot uses "with_" (trailing underscore) as the key; set to None to strip.
    outer_tree.args["with_"] = None
    outer_tree.args["with"] = None  # belt-and-suspenders for other sqlglot versions

    inner_sql = inner_node.sql(dialect=dialect)
    outer_sql = outer_tree.sql(dialect=dialect)
    return inner_sql, outer_sql


def _outer_filter_columns(outer_sql: str, dialect: str) -> set[str]:
    """Columns referenced in the OUTER SELECT's WHERE clause(s), lower-cased.

    For a layered ``WITH __base AS (<inner>) <outer>`` query the RLS predicate
    injected by ``plan()`` lands on the OUTER SELECT (``WHERE policy_col = val``),
    NOT on the inner aggregation.  When the inner is re-pointed at a rollup, the
    outer filter only resolves correctly if ``policy_col`` is a column produced by
    the rollup-backed ``__base`` — i.e. it must be present in the rollup grain.

    This helper extracts exactly those outer filter columns so the router can
    GUARANTEE the rollup grain carries them before routing (fix-22 RLS-soundness
    extended to the layered outer-RLS case).  Any parse failure returns an empty
    set, but callers must treat an *unparseable* outer as non-routable separately
    (here we conservatively fall back via the caller's own guard).
    """
    try:
        tree = parse_sql_cached(outer_sql, dialect=dialect)
    except Exception:
        return set()
    cols: set[str] = set()
    for where_node in tree.find_all(exp.Where):
        for col in where_node.find_all(exp.Column):
            cols.add(col.name.lower())
    return cols


def _inner_output_columns(inner_sql: str, dialect: str) -> set[str]:
    """Output column names the inner ``__base`` SELECT exposes, lower-cased.

    These are the ONLY columns the rollup-backed ``__base`` can offer to the
    outer SELECT after the rewrite: each SELECT-list item's output name (its
    alias when present, else the bare column name).  An aggregate without an
    alias has no stable output name and is ignored (it cannot be the target of
    an outer RLS predicate anyway).

    Used to GUARANTEE the outer RLS/policy column is actually produced by
    ``__base`` (not merely present in the rollup grain): a grain column that the
    inner SELECT never projects is invisible to the outer WHERE.
    """
    try:
        tree = parse_sql_cached(inner_sql, dialect=dialect)
    except Exception:
        return set()
    if not isinstance(tree, exp.Select):
        return set()
    out: set[str] = set()
    for sel_item in tree.expressions:
        if isinstance(sel_item, exp.Alias):
            out.add(sel_item.alias_or_name.lower())
        elif isinstance(sel_item, exp.Column):
            out.add(sel_item.name.lower())
    return out


def route_to_rollup_shape(
    plan: PhysicalPlan,
    registry: Any,
    *,
    org_id: str | None = None,
) -> RollupRouteResult:
    """Conservatively rewrite *plan* to read a built rollup when SOUND.

    This is the auto-pre-aggregation router.  Unlike the legacy exact-sig
    :func:`route_to_rollup`, it performs a *superset* rewrite: a single rollup
    grouped on a superset of dimensions can serve many narrower queries by
    re-aggregating the partial measures.

    Soundness rules (ALL must hold, else the plan is returned untouched)
    -------------------------------------------------------------------
    1. The query is a single-table aggregation parseable to a routable
       ``QueryShape`` (no joins / derived grains / expression measures).
       For layered ``WITH __base AS (<inner>) <outer>`` queries, the
       router inspects the INNER aggregation (the outer window fns / derived
       formulas compute over the base and pass through unchanged).
    2. A built rollup exists for the same base table AND the same org scope.
    3. The query's GROUP BY columns ⊆ the rollup's dimensions (so re-grouping
       the rollup reproduces the query's grain).
    4. Every query measure ``func(col)`` is:
         a. re-aggregable (``func`` in :data:`_REAGG` — SUM/COUNT/MIN/MAX), and
         b. materialized by the rollup (the rollup computed ``func(col)``).
    5. Every WHERE-clause column is present in the rollup (so the predicate —
       including any RLS predicate injected upstream — still applies).

    Tenant-isolation (org-scoping)
    --------------------------------
    *org_id* scopes the registry lookup so only rollups built for the calling
    organisation are ever considered.  A rollup built for org A is **never** a
    candidate for org B's queries, even when both share the same base table and
    query shape.  RLS predicates in the plan act as defence-in-depth, but the
    org-scoped registry lookup is the primary isolation guard.

    When *org_id* is ``None`` (unscoped / legacy path) only rollups that are
    themselves unscoped (``org_id=None``) are returned — a scoped rollup is
    never served to an unscoped caller.

    Layered (windowed / derived) metric queries
    --------------------------------------------
    When the SQL is a single-CTE ``WITH __base AS (<inner>) <outer>`` form
    (as emitted by the metric compiler for derived or windowed metrics), the
    router tries to rewrite the INNER aggregation to hit a rollup and then
    rebuilds the layered query with the rewritten inner.  The outer transform
    layer (window functions, derived formulas) computes over the rollup-served
    base — the results are identical to running over the raw fact table because
    the base measures are re-aggregated additively.  If the inner is not
    provably routable the plan is returned untouched.

    When all hold, the SELECT/GROUP BY/FROM are rewritten via the sqlglot AST:
    aggregates become ``SUM(<rollup measure col>)`` (etc.), the FROM target
    becomes the rollup table, and the WHERE clause is preserved verbatim.  The
    cache key is recomputed from the rewritten SQL.

    Be conservative: anything unproven leaves the plan EXACTLY as-is (same
    object, same cache_key) so RLS + cache behaviour is preserved on the
    non-routed path.

    Parameters
    ----------
    plan:
        The ``PhysicalPlan`` produced by ``plan()``, including any RLS-injected
        predicates.
    registry:
        A ``RollupRegistry`` instance.
    org_id:
        The organisation that owns the query.  Pass the org from the verified
        token / identity; ``None`` for unscoped / legacy paths.  Rollup
        candidates are filtered to this org so cross-tenant misrouting is
        impossible at the registry level (in addition to RLS).
    """
    # PERF (hot path): the rollup registry is empty in the common/default case
    # (no built rollups). Short-circuit BEFORE the sqlglot parse so every plain
    # query doesn't pay extract_shape()'s pure-Python parse cost for nothing.
    if not registry.all_rollups():
        return RollupRouteResult(plan, False, reason="no rollups registered")

    # Lazy import to avoid a cycle (preagg imports query_log; planner imports
    # query_log; preagg imports planner only at call sites).
    from app.connectors.query_log import extract_shape  # noqa: PLC0415

    # ── Detect layered CTE (windowed / derived metric queries) ───────────────
    # When the SQL is a single-CTE WITH __base AS (<inner>) <outer>, we extract
    # the inner aggregation and route that.  If routed, we rebuild the WITH
    # using the rewritten inner — the outer transform layer is preserved verbatim.
    cte_parts = _extract_layered_cte(plan.sql, dialect=plan.dialect)
    if cte_parts is not None:
        inner_sql, outer_sql = cte_parts
        inner_shape = extract_shape(inner_sql, dialect=plan.dialect)
        if inner_shape is None or not inner_shape.routable or inner_shape.base_table is None:
            return RollupRouteResult(plan, False, reason="layered: inner not routable")

        # Pass org_id so only this org's rollups are considered (tenant isolation).
        rollups = registry.candidates_for_table(inner_shape.base_table, org_id=org_id)
        if not rollups:
            return RollupRouteResult(plan, False, reason="layered: no rollup for base table")

        q_dims = set(inner_shape.dimensions)
        q_filter_cols = set(inner_shape.filter_columns)

        # ── RLS-soundness (fix-21 interaction) ───────────────────────────────
        # fix-21 hoists every active policy column into the layered __base grain
        # (so __base GROUPs BY it AND the top-N membership subquery can correlate
        # per-tenant on it).  These policy-hoisted dims carry the per-tenant
        # correlation: if the layered query is re-pointed at a rollup whose grain
        # does NOT include them, the inner aggregation would re-group at a coarser
        # grain that has already collapsed across the policy column — silently
        # dropping the per-tenant correlation the membership subquery relies on.
        # Therefore a rollup is only RLS-sound for this layered query when its
        # FULL grain (dimensions ∪ rls_keys) is a SUPERSET of the policy-hoisted
        # dims.  We derive those dims from the verified token claims (NEVER from
        # the SQL text) and intersect with the inner grain so we only require the
        # columns that were actually hoisted into __base.
        policy_hoisted_dims = {
            col.lower()
            for col in (plan.rls_claims.get("policies") or {})
        } & {d.lower() for d in inner_shape.dimensions}

        # ── Outer-RLS soundness (fix-22, layered case) ───────────────────────
        # The RLS predicate that plan() injects for a layered query lands on the
        # OUTER SELECT (WHERE policy_col = val), filtering the rollup-backed
        # __base.  That outer filter only resolves if policy_col is a column the
        # rollup grain produces.  policy_hoisted_dims above only covers policy
        # columns that ALSO appear in the inner GROUP BY; an outer RLS column
        # that is NOT an inner dim (so policy_hoisted_dims stays empty) would
        # slip through and reference a column absent from the rollup-backed
        # __base.  Collect every column the outer WHERE filters on so we can
        # GUARANTEE the rollup grain carries each one before routing.
        outer_filter_cols = _outer_filter_columns(outer_sql, dialect=plan.dialect)
        # Columns the rollup-backed __base will actually expose to the outer
        # SELECT (its projected output names).  The outer RLS column must be in
        # BOTH this set AND the rollup grain — grain-membership alone is not
        # enough because a grain column the inner never projects is invisible to
        # the outer WHERE.
        inner_output_cols = _inner_output_columns(inner_sql, dialect=plan.dialect)

        # Build a temporary inner plan to reuse _rewrite_to_rollup.
        inner_plan = PhysicalPlan(
            dialect=plan.dialect,
            sql=inner_sql,
            params=list(plan.params),
            projection=None,
            predicates=[],
            rls_claims=dict(plan.rls_claims),
            cache_key="",
        )

        for rollup in rollups:
            roll_dims = set(rollup.dimensions)
            # RLS keys are physically present in the rollup (they're grouped on too),
            # so they count as valid GROUP BY targets when checking the soundness rule.
            roll_all_dims = roll_dims | set(rollup.rls_keys)
            if not q_dims.issubset(roll_all_dims):
                continue
            # RLS-soundness check (fix-21): refuse to route the layered query to
            # a rollup whose grain does not carry every policy-hoisted dim.  This
            # is checked case-insensitively against the rollup's FULL grain so a
            # rollup that drops the per-tenant correlation column can never serve
            # this query — we fall back to the base instead of leaking across
            # tenants in the top-N membership subquery.
            roll_all_dims_lc = {d.lower() for d in roll_all_dims}
            if not policy_hoisted_dims.issubset(roll_all_dims_lc):
                continue
            # fix-22 (layered outer-RLS): every column the OUTER SELECT filters
            # on — which includes the RLS/policy column hoisted onto the outer
            # WHERE by plan() — MUST be carried by the rollup grain.  Otherwise
            # the rollup-backed __base would not expose that column and the outer
            # RLS predicate would reference a missing column (or, worse, be
            # silently dropped), leaking rows across tenants.  Refuse to route
            # (fall back to base) when any outer filter column is absent from the
            # rollup grain OR is not projected by the rollup-backed __base.
            if not outer_filter_cols.issubset(roll_all_dims_lc):
                continue
            if not outer_filter_cols.issubset(inner_output_cols):
                continue
            roll_measures = rollup.measure_funcs
            ok_measures = True
            for func, col in inner_shape.measures:
                col_key = col if col is not None else "*"
                if func not in _REAGG or (func, col_key) not in roll_measures:
                    ok_measures = False
                    break
            if not ok_measures:
                continue
            roll_cols = roll_all_dims
            if not q_filter_cols.issubset(roll_cols):
                continue

            # All soundness rules pass → rewrite the inner aggregation.
            rewritten_inner = _rewrite_to_rollup(inner_plan, rollup)
            if rewritten_inner is None:
                continue

            # Rebuild the layered query with the rewritten inner CTE.
            rewritten_sql = f"WITH __base AS ({rewritten_inner}) {outer_sql}"

            new_cache_key = compute_cache_key(
                sql=rewritten_sql,
                params=plan.params,
                rls_claims=plan.rls_claims,
            )
            new_plan = PhysicalPlan(
                dialect=plan.dialect,
                sql=rewritten_sql,
                params=list(plan.params),
                projection=plan.projection,
                predicates=list(plan.predicates),
                rls_claims=dict(plan.rls_claims),
                cache_key=new_cache_key,
            )
            roll_partitions = _rollup_partition_cols(rollup)
            pruned = tuple(
                sorted(c for c in roll_partitions if c in roll_cols and c in q_filter_cols)
            )
            reason = f"layered: sound superset rewrite onto {rollup.table}"
            if pruned:
                reason += f" (partition-pruned on {', '.join(pruned)})"
            return RollupRouteResult(
                new_plan, True, rollup_id=rollup.rollup_id,
                reason=reason, pruned_partitions=pruned,
            )

        return RollupRouteResult(plan, False, reason="layered: no sound rollup match")

    # ── Flat path ────────────────────────────────────────────────────────────
    shape = extract_shape(plan.sql, dialect=plan.dialect)
    if shape is None or not shape.routable or shape.base_table is None:
        return RollupRouteResult(plan, False, reason="not a routable aggregation")

    # Pass org_id so only this org's rollups are considered (tenant isolation).
    rollups = registry.candidates_for_table(shape.base_table, org_id=org_id)
    if not rollups:
        return RollupRouteResult(plan, False, reason="no rollup for base table")

    q_dims = set(shape.dimensions)
    q_filter_cols = set(shape.filter_columns)

    for rollup in rollups:
        roll_dims = set(rollup.dimensions)
        # RLS keys are physically present in the rollup (they are grouped on too),
        # so they count as valid GROUP BY targets when checking soundness.
        # FIX [LOW soundness]: Rule 3 must use the FULL grain (roll_dims ∪ rls_keys)
        # to match how the layered/CTE path already treats rls_keys.  Using only
        # roll_dims caused a query grouping by an rls_key (a physically-present
        # grain column) to be wrongly rejected (missed routing).
        roll_all_dims = roll_dims | set(rollup.rls_keys)
        # Rule 3: query group-by ⊆ rollup full grain (dims + rls_keys).
        if not q_dims.issubset(roll_all_dims):
            continue
        # Rule 4: every measure re-aggregable AND materialized by the rollup.
        roll_measures = rollup.measure_funcs  # set of (func, col)
        ok_measures = True
        for func, col in shape.measures:
            col_key = col if col is not None else "*"
            if func not in _REAGG or (func, col_key) not in roll_measures:
                ok_measures = False
                break
        if not ok_measures:
            continue
        # Rule 5: every filtered column present in the rollup GRAIN.
        # SECURITY/CORRECTNESS: only grain columns (dims + RLS keys) survive the
        # rollup verbatim. A measure's SOURCE column (e.g. `amount` behind
        # `sum_amount`) does NOT exist in the rollup, so a `WHERE amount > 100`
        # would reference a missing column and produce wrong results / an error.
        # Such a filter is never sound post-rollup → leave the plan untouched.
        roll_cols = roll_all_dims  # already includes rls_keys
        if not q_filter_cols.issubset(roll_cols):
            continue

        # ── Rule 6 (partition pruning awareness, additive): if the rollup is
        # partitioned by a column that the rollup ALSO carries in its grain,
        # detect which of those partition columns the query's WHERE constrains.
        # The predicate is preserved verbatim by the rewrite below, so DuckDB /
        # the lakehouse layout can range-prune those partitions.  Soundness
        # guard: a partition column MUST be in the rollup grain (else pruning a
        # column the rollup does not carry would be incorrect) — partition
        # columns not in roll_cols are simply ignored (no pruning claimed),
        # never used to drop rows.
        roll_partitions = _rollup_partition_cols(rollup)
        pruned = tuple(
            sorted(c for c in roll_partitions if c in roll_cols and c in q_filter_cols)
        )

        # ── All rules hold → perform the AST rewrite. ────────────────────────
        rewritten = _rewrite_to_rollup(plan, rollup)
        if rewritten is None:
            continue  # rewrite failed defensively — try the next rollup.

        new_cache_key = compute_cache_key(
            sql=rewritten,
            params=plan.params,
            rls_claims=plan.rls_claims,
        )
        new_plan = PhysicalPlan(
            dialect=plan.dialect,
            sql=rewritten,
            params=list(plan.params),
            projection=plan.projection,
            predicates=list(plan.predicates),
            rls_claims=dict(plan.rls_claims),
            cache_key=new_cache_key,
        )
        reason = f"sound superset rewrite onto {rollup.table}"
        if pruned:
            reason += f" (partition-pruned on {', '.join(pruned)})"
        return RollupRouteResult(
            new_plan, True, rollup_id=rollup.rollup_id,
            reason=reason, pruned_partitions=pruned,
        )

    return RollupRouteResult(plan, False, reason="no sound rollup match")


def _rollup_partition_cols(rollup: Any) -> set[str]:
    """Return the partition columns a rollup is laid out by (lower-cased).

    Read defensively off the rollup object so the planner stays decoupled from
    the rollup dataclass shape: a rollup may declare ``partition_by`` /
    ``partition_columns`` / ``partition_column`` (list or scalar).  Absent →
    empty set (no partition awareness, identical to the prior behaviour).
    """
    for attr in ("partition_by", "partition_columns", "partition_column"):
        val = getattr(rollup, attr, None)
        if val is None:
            continue
        if isinstance(val, str):
            return {val.lower()} if val else set()
        try:
            return {str(c).lower() for c in val if c}
        except TypeError:
            continue
    return set()


def _agg_is_inside_subquery(node: exp.Expression, root: exp.Expression) -> bool:
    """Return True if *node* is nested inside an ``exp.Subquery`` within *root*."""
    p = node.parent
    while p is not None and p is not root:
        if isinstance(p, exp.Subquery):
            return True
        p = getattr(p, "parent", None)
    return False


def _rewrite_to_rollup(plan: PhysicalPlan, rollup: Any) -> str | None:
    """Rewrite ``plan.sql`` to read *rollup* (re-aggregating measures).

    Only aggregates in the TOP-LEVEL SELECT projection are rewritten.
    Aggregates nested inside WHERE/HAVING subqueries are left untouched —
    but if any such subquery-aggregate exists the rewrite is refused
    (return ``None``) because we cannot guarantee the subquery will still
    evaluate correctly against the rollup table.

    Returns the rewritten SQL string, or ``None`` if the rewrite could not be
    performed safely (caller then leaves the plan untouched).
    """
    try:
        tree = parse_sql_cached(plan.sql, dialect=plan.dialect)
    except Exception:
        return None
    if not isinstance(tree, exp.Select):
        return None

    # Guard: if any aggregate exists inside a Subquery within the WHERE or
    # HAVING clauses, we cannot safely rewrite — the subquery may reference
    # raw source columns that no longer exist in the rollup.  Refuse to route.
    for clause_key in ("where", "having"):
        clause_node = tree.args.get(clause_key)
        if clause_node is None:
            continue
        for agg in clause_node.find_all(exp.AggFunc):
            if _agg_is_inside_subquery(agg, clause_node):
                return None  # subquery aggregate in WHERE/HAVING — refuse routing.

    # 1. Rewrite each aggregate in the TOP-LEVEL SELECT projection only.
    #    We iterate over tree.expressions (the SELECT list), collecting every
    #    AggFunc that is NOT nested inside a Subquery within that projection
    #    item.  Subquery-level aggregates in the SELECT (e.g. scalar subqueries)
    #    are also refused — they cannot be safely rewritten.
    for sel_expr in list(tree.expressions):
        for node in list(sel_expr.find_all(exp.AggFunc)):
            if _agg_is_inside_subquery(node, sel_expr):
                # An aggregate inside a scalar subquery in the SELECT list —
                # we cannot rewrite it safely; refuse routing.
                return None
            func_name = type(node).__name__.lower()
            # Defence-in-depth: COUNT(DISTINCT col) is NOT re-aggregable from
            # partial counts. sqlglot models it as exp.Count(this=exp.Distinct(…)),
            # which means func_name == 'count' — the same as plain COUNT(col).
            # Without this guard, _REAGG['count'] == 'SUM' would be applied to a
            # COUNT(DISTINCT) node, silently producing wrong results.  Bail the
            # entire rewrite so the caller falls through to the un-routed path.
            if isinstance(node, exp.Count) and isinstance(node.this, exp.Distinct):
                return None  # COUNT(DISTINCT …) is non-re-aggregable — abort.
            # Determine source column / star.
            col = _agg_source_col(node)
            if func_name not in _REAGG or col is None:
                return None  # measure not derivable — abort (sound by omission).
            reagg = _REAGG[func_name]
            partial_col = _measure_alias(func_name, col)
            new_node = sqlglot.parse_one(
                f'{reagg}("{partial_col}")', dialect=plan.dialect
            )
            # sqlglot replace() swaps only this aggregate node in-place;
            # any surrounding Alias wrapper (e.g. SUM(revenue) AS total)
            # is preserved automatically — no special-casing needed.
            node.replace(new_node)

    # 2. Swap the FROM target to the rollup table.
    replaced = False
    for table_node in tree.find_all(exp.Table):
        if table_node.name.lower() == rollup.source_table.lower():
            table_node.set("this", exp.Identifier(this=rollup.table, quoted=True))
            replaced = True
    if not replaced:
        return None

    return tree.sql(dialect=plan.dialect)


def _agg_source_col(node: exp.Expression) -> str | None:
    """Bare source column of an aggregate, ``"*"`` for COUNT(*), else None."""
    if isinstance(node, exp.Count):
        inner = node.this
        if inner is None or isinstance(inner, exp.Star):
            return "*"
    arg = getattr(node, "this", None)
    if arg is None:
        return "*"
    if isinstance(arg, exp.Star):
        return "*"
    if isinstance(arg, exp.Column):
        return arg.name.lower()
    if isinstance(arg, exp.Distinct):
        exprs = arg.expressions
        if len(exprs) == 1 and isinstance(exprs[0], exp.Column):
            return exprs[0].name.lower()
    return None
