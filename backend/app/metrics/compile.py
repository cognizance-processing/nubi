"""Metric → SQL compiler — the governed translation of a :class:`MetricQuery`.

This is the C2 compiler from ``METRICS_LAYER.md``.  It takes a governed
:class:`~app.metrics.models.MetricDefinition` plus a caller's
:class:`~app.metrics.models.MetricQuery` and produces ``(sql, params)`` ready to
be handed to ``app.connectors.planner.plan(sql, params, claims)``.

Contract with the downstream planner
-------------------------------------
The planner resolves ``{{name}}`` placeholders to positional binds (asyncpg /
DuckDB ``$N``) and injects RLS predicates as AST ``col = value`` filters.  So
THIS compiler:

* **MUST** emit ``{{name}}`` placeholders for every user-supplied filter value
  and return those values in ``params`` — it never binds or concatenates them.
* **MUST NOT** inject RLS itself — that is the planner's job.  It only assumes
  the invariant (documented below) that ``metric.rls_keys`` are real columns of
  the base source so the planner's ``col = value`` predicate lands.
* For ``in`` / ``not_in`` filters it emits ``{{name | inclause}}`` — the exact
  Jinja2 filter the planner's template engine
  (``app.connectors.template._filter_inclause``) understands: it binds each list
  element separately and expands to ``($1, $2, …)``.  We therefore emit a single
  placeholder name whose value is the list; the planner handles the expansion.

``default_filters`` are author-trusted WHERE fragments inlined VERBATIM (parsed
as sqlglot conditions and AND-ed in).  Only user filter VALUES are placeholdered.

Time-bucket alias convention
-----------------------------
When ``time_grain`` is set we add ``DATE_TRUNC('<grain>', <time_col>) AS
<time_col>_<grain>`` (e.g. ``created_at_month``).  That alias is also a legal
``order_by`` target.

Layered compilation
-------------------
When the query has NO transforms AND the metric declares NO derived_measures the
compiler emits the FLAT query (identical to the old single-SELECT path).  When
either is present it emits a two-level CTE:

    WITH __base AS (
        <flat base aggregation: dims, time-bucket, BASE measures, WHERE, GROUP BY>
    )
    SELECT
        <dims>,
        <time bucket alias>,
        <base measures passthrough>,
        <derived measures: formula over base cols, every '/' denominator wrapped
         in NULLIF(<denom>, 0)>,
        <time-intel window fns over __base: PARTITION BY non-time dims, ORDER BY
         time bucket alias>
    FROM __base
    [QUALIFY / outer top-N]

RLS on the layered path
-----------------------
The downstream planner injects ``WHERE <rls_key> = <claim>`` on the OUTERMOST
select.  For that to be sound the ``__base`` CTE MUST include every
``metric.rls_keys`` column in its SELECT + GROUP BY (so partial aggregates never
mix tenants) AND the outer SELECT MUST carry those columns through so the
injected predicate has a real column to land on.  The compiler enforces this
automatically: it adds each rls_key as an extra dimension in __base (if not
already in mq.dimensions) and projects it through to the outer SELECT.  If for
any reason an rls_key cannot be resolved the compiler raises
:class:`MetricError` with code ``rls_not_projectable`` (fail-closed — never
emit a layered query that could leak cross-tenant data).

latest_snapshot capability
---------------------------
``TimeComparison(kind="latest_snapshot", measure="<entity_col>")`` deduplicates
the base source to the single latest row per entity before the base aggregation.
The ``measure`` field is repurposed here as the entity column name and the
``periods`` field (default 0) identifies which time column to use for ordering
(0 = metric's time_dimension.column; ignored if it's the same).

Design rationale: reusing ``TimeComparison`` (vs a new definition-level flag)
keeps the contract minimal while allowing the pattern to be requested per-query.
The QUALIFY ROW_NUMBER() OVER (PARTITION BY <entity> ORDER BY <time> DESC) = 1
is injected into the base subquery (wrapping base_table/base_sql so the dedup
happens before the aggregation).  Requires the metric to have a time_dimension.

Percentile / approx aggregates
--------------------------------
``Measure(agg="percentile_cont", expr="<col>", format="<pN>")`` emits
``PERCENTILE_CONT(<p>) WITHIN GROUP (ORDER BY <col>)`` where ``<p>`` is read
from ``format`` (e.g. ``"p50"`` → 0.5, ``"p95"`` → 0.95; default 0.5).

``Measure(agg="approx_count_distinct", expr="<col>")`` emits
``APPROX_COUNT_DISTINCT(<col>)`` (DuckDB built-in).

Purity
------
No DB, no FastAPI, no I/O.  Governance violations raise
:class:`~app.metrics.models.MetricError` with a machine ``code``.
"""

from __future__ import annotations

import os
import re
import tokenize
import io
from typing import Any, get_args

import sqlglot
import sqlglot.expressions as exp

from app.metrics.models import (
    FilterOp,
    MetricDefinition,
    MetricError,
    MetricQuery,
    TimeGrain,
    YEAR_LAG_BY_GRAIN,
    DerivedMeasure,
)

# ── Resource caps (env-overridable) ─────────────────────────────────────────
_MAX_TC_PERIODS: int = int(os.environ.get("NUBI_MAX_TC_PERIODS", 3650))
_MAX_TOP_N: int = int(os.environ.get("NUBI_MAX_TOP_N", 1000))
_MAX_QUERY_LIMIT: int = int(os.environ.get("NUBI_MAX_QUERY_LIMIT", 100_000))
# Default row cap applied when mq.limit is None (callers wanting all rows must
# set an explicit high limit, up to NUBI_MAX_QUERY_LIMIT).
_DEFAULT_LIMIT: int = int(os.environ.get("NUBI_METRIC_DEFAULT_LIMIT", 100_000))
# Cap on the total number of time_comparisons in a single MetricQuery.
# Each non-window entry adds a correlated subquery/LATERAL scanning __base,
# so an unbounded list is an O(N_tc × rows) resource risk.
_MAX_TC_ENTRIES: int = int(os.environ.get("NUBI_MAX_TC_ENTRIES", 20))
# Cap on the number of elements in a single in/not_in filter value list.
# Each element becomes a separate bind parameter via the planner's inclause
# expansion, so an unbounded list is a parameter-explosion resource risk.
_MAX_IN_LIST: int = int(os.environ.get("NUBI_MAX_IN_LIST", 1000))
# Cap on the total number of filters in a single MetricQuery.
_MAX_FILTERS: int = int(os.environ.get("NUBI_MAX_FILTERS", 50))
# Cap on the number of requested dimensions in a single MetricQuery.
# Each dimension is an extra GROUP BY column AND (on the layered path) an extra
# PARTITION BY / LATERAL fan-out term, so an unbounded count is an
# O(N_dims × N_tc) resource + cardinality-explosion risk BEFORE the LIMIT lands.
_MAX_DIMS: int = int(os.environ.get("NUBI_MAX_DIMS", 20))

# Valid SQL identifier pattern (for entity/time columns in latest_snapshot)
_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

_DEFAULT_DIALECT = "duckdb"

# Known FilterOp vocabulary (mirrors models.FilterOp Literal).
_FILTER_OPS: frozenset[str] = frozenset(get_args(FilterOp))
_LIST_OPS: frozenset[str] = frozenset({"in", "not_in"})
_SCALAR_OPS: frozenset[str] = _FILTER_OPS - _LIST_OPS

# Known TimeGrain vocabulary (used to validate ``time_grain`` is even a grain).
_TIME_GRAINS: frozenset[str] = frozenset(get_args(TimeGrain))

# agg → SQL aggregate function name.
_AGG_SQL: dict[str, str] = {
    "sum": "SUM",
    "count": "COUNT",
    "count_distinct": "COUNT",          # rendered as COUNT(DISTINCT …)
    "min": "MIN",
    "max": "MAX",
    "avg": "AVG",
    "percentile_cont": "PERCENTILE_CONT",   # rendered specially
    "approx_count_distinct": "APPROX_COUNT_DISTINCT",
}

# Non-additive aggregation functions: these cannot be safely re-aggregated
# across arbitrary row groups.
#
# Two consequences, both intentional:
#
# 1. top_n rank measure governance: using them as a top_n rank measure with a
#    time_grain (which requires SUM(rank_measure) in the membership subquery) is
#    semantically wrong.  We block them in _govern with a MetricError.
#
# 2. top_n.other re-aggregation: the Other bucket rolls up the non-top-N rows
#    of the ranked dimension.  For additive aggs (sum, count, min, max) we can
#    re-aggregate the pre-computed __base values directly; for non-additive aggs
#    there is no correct re-aggregation (AVG of AVGs is wrong without row counts,
#    COUNT(DISTINCT …) across groups double-counts shared values, percentiles
#    require the full micro-data).  The Other bucket therefore emits NULL for
#    every non-additive measure — callers must treat NULL in the Other bucket for
#    these measure types as "not available / undefined", not as a data error.
#    This is INTENTIONAL and documented behaviour; see the tests in
#    test_metrics_compile_transforms.py (test_other_bucket_avg_emits_null,
#    test_other_bucket_non_additive_measures_emit_null).
_NON_ADDITIVE_AGGS: frozenset[str] = frozenset({
    "avg", "count_distinct", "percentile_cont", "approx_count_distinct",
})

# Allowed token types in a derived-measure formula (post-parse check).
_FORMULA_ALLOWED_TOKTYPE = frozenset({
    tokenize.OP, tokenize.NUMBER, tokenize.NAME, tokenize.NEWLINE,
    tokenize.NL, tokenize.COMMENT, tokenize.ENCODING, tokenize.ENDMARKER,
})
_FORMULA_ALLOWED_OPS = frozenset({"+", "-", "*", "/", "(", ")"})


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def compile_metric(
    metric: MetricDefinition,
    mq: MetricQuery,
    *,
    dialect: str = _DEFAULT_DIALECT,
    policy_cols: tuple[str, ...] = (),
) -> tuple[str, dict[str, Any]]:
    """Compile *metric* + *mq* into ``(sql, params)``.

    ``sql`` contains ``{{name}}`` placeholders for user-supplied filter values;
    ``params`` maps those names → values.  Governance violations raise
    :class:`MetricError`.

    See module docstring for the time-bucket alias and in/not_in conventions.

    ``policy_cols`` — the RLS policy column NAMES the downstream planner will
    inject as ``WHERE <col> = <claim>`` predicates at query time (i.e. the keys
    of ``claims['policies']``).  These are threaded into the layered top-N path
    so the membership / ranking subqueries correlate on them EVEN WHEN
    ``metric.rls_keys`` is empty and the only projected dimension is the ranked
    dimension.  Without this the membership subquery would compute the GLOBAL
    top-N across tenants and the planner's outer per-tenant RLS filter would then
    yield a WRONG / empty per-tenant result (a tenant's true top member excluded
    because another tenant's member ranks higher globally).  Empty (the default)
    preserves the prior single-tenant behaviour exactly.
    """
    # ── 1. GOVERN (everything BEFORE we build any SQL) ──────────────────────
    time_alias = _govern(metric, mq)

    # ── 2. Decide path: FLAT vs LAYERED ─────────────────────────────────────
    needs_layer = bool(metric.derived_measures) or mq.has_transforms()

    if not needs_layer:
        return _compile_flat(metric, mq, time_alias, dialect)
    else:
        return _compile_layered(metric, mq, time_alias, dialect, policy_cols)


def compile_metric_sql(
    metric: MetricDefinition,
    mq: MetricQuery,
    *,
    dialect: str = _DEFAULT_DIALECT,
) -> str:
    """Like :func:`compile_metric` but return only the SQL (for ``/sql`` dry runs)."""
    sql, _ = compile_metric(metric, mq, dialect=dialect)
    return sql


# ---------------------------------------------------------------------------
# Flat path (no transforms, no derived measures)
# ---------------------------------------------------------------------------


def _compile_flat(
    metric: MetricDefinition,
    mq: MetricQuery,
    time_alias: str | None,
    dialect: str,
) -> tuple[str, dict[str, Any]]:
    """Emit the flat single-SELECT query (original behaviour, byte-stable)."""
    params: dict[str, Any] = {}

    select_exprs: list[exp.Expression] = []
    group_exprs: list[exp.Expression] = []

    # 2a. requested dimensions: <sql_expr> AS <name>
    for dim_name in mq.dimensions:
        dim = metric.dimension(dim_name)
        assert dim is not None  # guaranteed by _govern
        col_expr = _parse_expr(dim.sql_expr(), dialect)
        select_exprs.append(exp.alias_(col_expr.copy(), dim.name))
        group_exprs.append(col_expr.copy())

    # 2b. time bucket: DATE_TRUNC('<grain>', <col>) AS <col>_<grain>
    if mq.time_grain is not None:
        td = metric.time_dimension
        assert td is not None  # guaranteed by _govern
        bucket = _date_trunc(mq.time_grain, td.column, dialect)
        select_exprs.append(exp.alias_(bucket.copy(), time_alias))
        group_exprs.append(bucket.copy())

    # 2c. measures: <AGG>(<expr>) AS <name>  (primary + extras)
    for measure in metric.measures():
        select_exprs.append(_measure_expr(measure, dialect))

    # 2d. FROM: exactly one of base_table / base_sql.
    from_expr = _source_expr(metric, dialect)

    # 2e. assemble the SELECT.
    select = exp.Select().select(*select_exprs).from_(from_expr)

    # 2f. GROUP BY underlying expressions (not aliases) for dialect safety.
    if group_exprs:
        select = select.group_by(*group_exprs)

    # 2g. WHERE = default_filters (verbatim, trusted) AND user filters (params).
    subs: dict[str, str] = {}
    where_node = _build_where(metric, mq, params, subs, dialect)
    if where_node is not None:
        select = select.where(where_node)

    # 2h. ORDER BY (alias refs are fine) and LIMIT.
    # When mq.limit is None we apply a default cap to prevent unbounded scans.
    # Callers wanting all rows must set an explicit high limit (up to
    # NUBI_MAX_QUERY_LIMIT).
    for field, direction in mq.order_by:
        select = select.order_by(
            exp.Ordered(this=exp.column(field), desc=(direction == "desc"))
        )
    select = select.limit(mq.limit if mq.limit is not None else _DEFAULT_LIMIT)

    # ── 3. Render, then swap sentinels for the real {{name}} placeholders. ───
    sql = select.sql(dialect=dialect)
    for sentinel, placeholder in subs.items():
        sql = sql.replace(sentinel, placeholder)
    return sql, params


# ---------------------------------------------------------------------------
# Layered path (derived measures and/or query transforms)
# ---------------------------------------------------------------------------


def _compile_layered(
    metric: MetricDefinition,
    mq: MetricQuery,
    time_alias: str | None,
    dialect: str,
    policy_cols: tuple[str, ...] = (),
) -> tuple[str, dict[str, Any]]:
    """Emit the two-level CTE query with derived + time-intel + top-N layers.

    Everything is assembled as a SINGLE sqlglot AST: the ``__base`` aggregation
    is attached as a CTE (via :meth:`exp.Select.with_`) to the top-level
    statement (the outer SELECT, or — for ``top_n.other`` — an :class:`exp.Union`
    of the top-N arm and the Other-bucket arm).  The whole statement is rendered
    to SQL **exactly once** and the sentinel→``{{name}}`` substitution is applied
    **exactly once** on that final string.  No intermediate ``.sql()`` renders are
    re-embedded into f-strings, and no already-rendered SQL is re-parsed —
    eliminating the placeholder-corruption / double-WITH / paren-counting bugs of
    the previous string-assembly path.
    """
    params: dict[str, Any] = {}
    subs: dict[str, str] = {}

    # ── RLS soundness check: resolve every rls_key ──────────────────────────
    # In the layered form the outer SELECT must carry every rls_key column so
    # the planner's injected predicate has a real column to land on.  We add
    # them as extra GROUP BY columns in __base.  Fail-closed on unresolvable.
    extra_rls_dims: list[str] = []
    for rls_key in metric.rls_keys:
        if rls_key not in mq.dimensions:
            # The rls_key must be a column of the source (we cannot verify that
            # from a table name alone — we trust the metric author per the
            # documented invariant) but we CAN verify it is not shadowed by a
            # dimension expression that remaps it.  The simplest safe approach:
            # add it as a bare column to both __base and the outer SELECT.
            extra_rls_dims.append(rls_key)

    # FIX (MED correctness/RLS — single-ranked-dim top-N tenant leak): when the
    # metric declares NO rls_keys but the REQUEST carries RLS policies (the
    # planner will inject ``WHERE <policy_col> = <claim>`` on the outer select),
    # the top-N membership subquery must correlate on those policy columns so the
    # top-N set is computed PER TENANT.  fix-13 handled this when a non-ranked
    # dimension exists (the membership correlates on it); but when the metric has
    # rls_keys=[] AND the ONLY projected dim IS the ranked dim, there is no other
    # column to correlate on — the membership computes the GLOBAL top-N and the
    # outer per-tenant RLS filter then yields a wrong/empty result.  We therefore
    # hoist every active policy column into the grain here (so __base groups by it
    # and the outer SELECT projects it — giving the planner a real column to land
    # its predicate on AND giving the membership subquery a column to correlate
    # on).  These hoisted columns are passed to _membership_correlation_keys via
    # all_dim_names below so they are picked up even when rls_keys=[].  Empty
    # policy_cols (single-tenant) is a no-op — prior behaviour is byte-stable.
    for policy_col in policy_cols:
        if not _IDENT_RE.fullmatch(policy_col):
            # Defense-in-depth: these names are interpolated unquoted into the
            # membership f-string.  Skip any name that is not a safe identifier
            # rather than risk an injection (the planner still applies its own
            # RLS predicate; correlation is an additional soundness measure).
            continue
        if policy_col not in mq.dimensions and policy_col not in extra_rls_dims:
            extra_rls_dims.append(policy_col)

    # ── Detect latest_snapshot comparisons ─────────────────────────────────
    snapshot_specs = [tc for tc in mq.time_comparisons if tc.kind == "latest_snapshot"]
    regular_comparisons = [tc for tc in mq.time_comparisons if tc.kind != "latest_snapshot"]

    # ── Build the __base CTE ─────────────────────────────────────────────────
    base_select_exprs: list[exp.Expression] = []
    base_group_exprs: list[exp.Expression] = []

    # (a) requested dimensions
    for dim_name in mq.dimensions:
        dim = metric.dimension(dim_name)
        assert dim is not None
        col_expr = _parse_expr(dim.sql_expr(), dialect)
        base_select_exprs.append(exp.alias_(col_expr.copy(), dim.name))
        base_group_exprs.append(col_expr.copy())

    # (b) extra rls_key columns (bare column refs)
    for rls_key in extra_rls_dims:
        col_expr = _parse_expr(rls_key, dialect)
        base_select_exprs.append(exp.alias_(col_expr.copy(), rls_key))
        base_group_exprs.append(col_expr.copy())

    # (c) time bucket
    if mq.time_grain is not None:
        td = metric.time_dimension
        assert td is not None
        bucket = _date_trunc(mq.time_grain, td.column, dialect)
        base_select_exprs.append(exp.alias_(bucket.copy(), time_alias))
        base_group_exprs.append(bucket.copy())

    # (d) base measures
    for measure in metric.measures():
        base_select_exprs.append(_measure_expr(measure, dialect))

    # (e) FROM source (possibly wrapped with snapshot QUALIFY)
    if snapshot_specs:
        # Wrap source in a subquery with QUALIFY to dedupe to latest row per entity
        td = metric.time_dimension
        if td is None:
            raise MetricError(
                "snapshot_no_time",
                "latest_snapshot requires the metric to declare a time_dimension.",
            )
        entity_col = snapshot_specs[0].measure  # repurposed: entity column name
        time_col = td.column
        # Both columns are validated by _govern via _IDENT_RE; emit as quoted
        # identifiers to prevent any residual injection.
        entity_col_sql = exp.to_identifier(entity_col, quoted=True).sql(dialect=dialect)
        time_col_sql = exp.to_identifier(time_col, quoted=True).sql(dialect=dialect)
        # Build: SELECT * FROM <source> QUALIFY ROW_NUMBER() OVER
        #        (PARTITION BY <entity> ORDER BY <time> DESC) = 1
        raw_source = _source_expr(metric, dialect)
        inner_select = exp.Select().select(exp.Star()).from_(raw_source)
        # Build QUALIFY expression as a raw SQL fragment (sqlglot supports QUALIFY)
        qualify_sql = (
            f"ROW_NUMBER() OVER (PARTITION BY {entity_col_sql} "
            f"ORDER BY {time_col_sql} DESC) = 1"
        )
        qualify_expr = sqlglot.parse_one(qualify_sql, dialect=dialect)
        inner_select = inner_select.qualify(qualify_expr)
        from_expr: exp.Expression = exp.Subquery(
            this=inner_select,
            alias=exp.TableAlias(this=exp.to_identifier("__snap")),
        )
    else:
        from_expr = _source_expr(metric, dialect)

    base_select = exp.Select().select(*base_select_exprs).from_(from_expr)

    if base_group_exprs:
        base_select = base_select.group_by(*base_group_exprs)

    # (f) WHERE
    where_node = _build_where(metric, mq, params, subs, dialect)
    if where_node is not None:
        base_select = base_select.where(where_node)

    # ── Build the outer SELECT over __base ──────────────────────────────────
    outer_select_exprs: list[exp.Expression] = []

    # All dimension columns (including rls extras)
    all_dim_names = list(mq.dimensions) + extra_rls_dims

    # (a) passthrough dimension columns
    for dim_name in all_dim_names:
        outer_select_exprs.append(
            exp.alias_(exp.column(dim_name), dim_name)
        )

    # (b) passthrough time alias
    if time_alias is not None:
        outer_select_exprs.append(
            exp.alias_(exp.column(time_alias), time_alias)
        )

    # (c) passthrough base measures
    for m in metric.measures():
        outer_select_exprs.append(
            exp.alias_(exp.column(m.name), m.name)
        )

    # (d) derived measures: formula with NULLIF-guarded division
    base_measure_names = set(metric.measure_names())
    for dm in metric.derived_measures:
        formula_sql = _compile_derived_formula(dm, base_measure_names)
        formula_expr = sqlglot.parse_one(formula_sql, dialect=dialect)
        outer_select_exprs.append(exp.alias_(formula_expr, dm.name))

    # (e) time-intelligence window functions (non-snapshot)
    # Partition by non-time dims (requested dims + rls extras, no time alias).
    partition_cols = [exp.column(d) for d in all_dim_names]
    order_col = exp.column(time_alias) if time_alias else None

    # Lateral join entries for prior-period / prior-year pct expressions.
    # Each entry is (lateral_alias, lateral_sql) — the lateral SELECT computes
    # the prior value once; the pct expression references the alias column.
    # This ensures the correlated subquery text appears in the SQL exactly ONCE
    # (not twice as numerator AND NULLIF denominator), so the DB only evaluates
    # it once per row.
    lateral_joins: list[tuple[str, str]] = []
    # FIX (Issue 3 duplicate-alias): each LATERAL alias must be unique even when
    # the same measure+kind is used more than once (e.g. two pop_pct entries for
    # the same measure with different periods).  Include the list index so that
    # two identical (measure, kind, periods) entries still produce distinct aliases.
    _tc_index: int = 0

    for tc in regular_comparisons:
        m_col = exp.column(tc.measure)
        out_name = tc.out_name()

        if tc.kind in ("prior_period", "pop_abs", "pop_pct"):
            # Date-correct correlated subquery instead of positional LAG.
            # LAG(measure, N) is wrong for sparse time series (missing buckets
            # shift the row offset).  We match by DATE arithmetic instead.
            # Governance already enforces time_grain is set for these kinds.
            #
            # FIX (perf): ALL of prior_period / pop_abs / pop_pct now materialise
            # the prior-period value EXACTLY ONCE via a CROSS JOIN LATERAL.
            # Previously prior_period / pop_abs emitted the correlated scalar
            # subquery directly in the outer SELECT (and pop_abs emitted it inside
            # `m_col - (subquery)`), so the planner re-scanned __base once per
            # outer row — O(rows x TC).  Routing through the same LATERAL pattern
            # as pop_pct makes the subquery text appear once and the DB evaluate
            # it once per outer row.  Unique alias per (measure, periods, index).
            pp_expr_sql = _prior_period_subquery_sql(
                tc.measure, mq.time_grain or "day", tc.periods,
                time_alias, all_dim_names, dialect
            )
            m_col_sql = m_col.sql(dialect=dialect)
            lat_alias = f"__pp_{tc.measure}_{tc.periods}_{_tc_index}__"
            lat_col = f"{lat_alias}.pp_val"
            lateral_joins.append((lat_alias, pp_expr_sql))
            if tc.kind == "prior_period":
                pp_ref_expr = sqlglot.parse_one(lat_col, dialect=dialect)
                outer_select_exprs.append(exp.alias_(pp_ref_expr, out_name))
            elif tc.kind == "pop_abs":
                diff_sql = f"({m_col_sql} - {lat_col})"
                diff_expr = sqlglot.parse_one(diff_sql, dialect=dialect)
                outer_select_exprs.append(exp.alias_(diff_expr, out_name))
            else:  # pop_pct
                pct_sql = (
                    f"({m_col_sql} - {lat_col}) "
                    f"/ NULLIF({lat_col}, 0)"
                )
                pct_expr = sqlglot.parse_one(pct_sql, dialect=dialect)
                outer_select_exprs.append(exp.alias_(pct_expr, out_name))

        elif tc.kind in ("prior_year", "yoy_abs", "yoy_pct"):
            # Date-correct prior-year lookup via correlated scalar subquery.
            # Positional LAG is WRONG for sparse time series; the correlated
            # subquery matches by date (bucket - 1 year), correct for any sparsity.
            #
            # FIX (perf): ALL of prior_year / yoy_abs / yoy_pct now materialise
            # the prior-year value EXACTLY ONCE via a CROSS JOIN LATERAL (same
            # rationale as the prior_period block above).  Unique alias per
            # (measure, index).
            py_expr_sql = _prior_year_subquery_sql(
                tc.measure, mq.time_grain or "day", time_alias, all_dim_names, dialect
            )
            m_col_sql = m_col.sql(dialect=dialect)
            lat_alias = f"__py_{tc.measure}_{_tc_index}__"
            lat_col = f"{lat_alias}.py_val"
            lateral_joins.append((lat_alias, py_expr_sql))
            if tc.kind == "prior_year":
                py_ref_expr = sqlglot.parse_one(lat_col, dialect=dialect)
                outer_select_exprs.append(exp.alias_(py_ref_expr, out_name))
            elif tc.kind == "yoy_abs":
                diff_sql = f"({m_col_sql} - {lat_col})"
                diff_expr = sqlglot.parse_one(diff_sql, dialect=dialect)
                outer_select_exprs.append(exp.alias_(diff_expr, out_name))
            else:  # yoy_pct
                pct_sql = (
                    f"({m_col_sql} - {lat_col}) "
                    f"/ NULLIF({lat_col}, 0)"
                )
                pct_expr = sqlglot.parse_one(pct_sql, dialect=dialect)
                outer_select_exprs.append(exp.alias_(pct_expr, out_name))

        elif tc.kind in ("ytd", "qtd", "mtd"):
            trunc_unit = {"ytd": "year", "qtd": "quarter", "mtd": "month"}[tc.kind]
            # Partition by trunc(time, unit) + non-time dims; ORDER BY time alias;
            # ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW.
            # FIX (Issue 4): emit partition columns as quoted identifiers to avoid
            # injection from names that need quoting and to prevent SQL injection.
            partition_parts_quoted = [
                exp.to_identifier(d, quoted=True).sql(dialect=dialect)
                for d in all_dim_names
            ]
            if time_alias:
                time_alias_quoted = exp.to_identifier(time_alias, quoted=True).sql(dialect=dialect)
                partition_parts_sql = ", ".join(
                    [*partition_parts_quoted, f"DATE_TRUNC('{trunc_unit}', {time_alias_quoted})"]
                )
                order_part_sql = time_alias_quoted
            else:
                partition_parts_sql = ", ".join(partition_parts_quoted) if partition_parts_quoted else "1"
                order_part_sql = "1"
            win_sql = (
                f"SUM({tc.measure}) OVER ("
                f"PARTITION BY {partition_parts_sql} "
                f"ORDER BY {order_part_sql} "
                f"ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)"
            )
            win_expr = sqlglot.parse_one(win_sql, dialect=dialect)
            outer_select_exprs.append(exp.alias_(win_expr, out_name))

        elif tc.kind in ("rolling_sum", "rolling_avg"):
            agg_fn = "SUM" if tc.kind == "rolling_sum" else "AVG"
            rows_back = tc.periods - 1
            # FIX (Issue 4): emit partition columns as quoted identifiers.
            partition_parts_quoted = [
                exp.to_identifier(d, quoted=True).sql(dialect=dialect)
                for d in all_dim_names
            ]
            partition_parts_sql = ", ".join(partition_parts_quoted) if partition_parts_quoted else "1"
            if time_alias:
                time_alias_quoted = exp.to_identifier(time_alias, quoted=True).sql(dialect=dialect)
                order_part_sql = time_alias_quoted
                # FIX (LOW correctness — time-interval rolling frame): a rolling
                # window over a TIME series must measure its lookback in CALENDAR
                # time, not physical rows.  ``ROWS BETWEEN N PRECEDING`` counts N
                # prior rows, so a rolling 28-day window over data with missing /
                # sparse days actually spans far MORE than 28 calendar days (it
                # walks back 28 *populated* buckets).  Emit ``RANGE BETWEEN
                # INTERVAL 'N <unit>' PRECEDING AND CURRENT ROW`` ordered by the
                # time bucket so gaps are handled correctly: the frame is the set
                # of rows whose ordering value falls within the time interval,
                # regardless of how many physical rows that is.  DuckDB and PG
                # both support RANGE with an INTERVAL offset.  ``rows_back``
                # (periods - 1) is the interval magnitude so the frame
                # [bucket - (N-1) units, bucket] spans exactly N grain units
                # inclusive, matching the prior ROWS semantics on dense data.
                interval_unit = _ROLLING_INTERVAL_UNIT.get(
                    mq.time_grain or "day", "day"
                )
                win_sql = (
                    f"{agg_fn}({tc.measure}) OVER ("
                    f"PARTITION BY {partition_parts_sql} "
                    f"ORDER BY {order_part_sql} "
                    f"RANGE BETWEEN INTERVAL '{rows_back} {interval_unit}' "
                    f"PRECEDING AND CURRENT ROW)"
                )
            else:
                # Non-time rolling window (no grain/order column): keep the
                # physical ROWS frame — there is no time axis to range over.
                win_sql = (
                    f"{agg_fn}({tc.measure}) OVER ("
                    f"ROWS BETWEEN {rows_back} PRECEDING AND CURRENT ROW)"
                )
            win_expr = sqlglot.parse_one(win_sql, dialect=dialect)
            outer_select_exprs.append(exp.alias_(win_expr, out_name))

        _tc_index += 1

    # ── Assemble the outer SELECT ────────────────────────────────────────────
    # Use "__base AS __outer" so that correlated subqueries (e.g. prior-year)
    # can unambiguously qualify the outer row's columns as "__outer.<col>",
    # avoiding the DuckDB binding ambiguity when both the outer and inner CTEs
    # are named "__base".
    outer_select = exp.Select().select(*outer_select_exprs).from_(
        exp.alias_(
            exp.Table(this=exp.to_identifier("__base")),
            "__outer",
        )
    )

    # FIX (Issue 3): add CROSS JOIN LATERAL for any pop_pct / yoy_pct columns.
    # Each lateral computes the prior value once per row (as `pp_val` / `py_val`)
    # so the correlated subquery SQL text appears exactly once in the output,
    # and the DB evaluates it only once per outer row.
    # DuckDB supports: CROSS JOIN LATERAL (SELECT expr) AS alias
    for lat_alias, lat_inner_sql in lateral_joins:
        # Determine the inner column name from the alias prefix.
        if lat_alias.startswith("__pp_"):
            lat_col_name = "pp_val"
        else:
            lat_col_name = "py_val"
        # Build via AST: parse a dummy full SELECT to extract the join node,
        # then attach it to the outer_select.  The inner SELECT uses the
        # correlated subquery as its sole expression.
        inner_select_sql = f"SELECT {lat_inner_sql} AS {lat_col_name}"
        inner_select_expr = sqlglot.parse_one(inner_select_sql, dialect=dialect)
        lat_node = exp.Lateral(
            this=exp.Subquery(this=inner_select_expr),
            view=False,
            outer=False,
            alias=exp.TableAlias(this=exp.to_identifier(lat_alias)),
        )
        lat_join = exp.Join(this=lat_node, kind="CROSS")
        joins_list = outer_select.args.get("joins") or []
        joins_list.append(lat_join)
        outer_select.set("joins", joins_list)

    # ORDER BY and LIMIT (on the outer).
    # When mq.limit is None we apply a default cap to prevent unbounded scans
    # and window-function materialisation over the full table.  Callers wanting
    # all rows must set an explicit high limit (up to NUBI_MAX_QUERY_LIMIT).
    #
    # FIX (LOW #1 — QUALIFY/LIMIT interaction): the no-time-grain top_n path adds
    # a ``QUALIFY RANK() <= N`` to THIS outer SELECT.  Standard/DuckDB semantics
    # evaluate QUALIFY (a window-stage filter) BEFORE the outer LIMIT, so the
    # top-N rows are selected first and only then capped.  That is correct ONLY
    # if the default LIMIT is >= the QUALIFY result size; if a deployment lowered
    # NUBI_METRIC_DEFAULT_LIMIT below N the LIMIT would truncate the legitimate
    # top-N.  We therefore floor the default LIMIT at N for any top_n request so
    # the QUALIFY result can never be silently truncated by the default cap.
    #
    # FIX (LOW #2 — per-tenant truncation): the default LIMIT is a bare outer cap.
    # The compiler emits per-tenant window/RANK partitions (PARTITION BY rls_keys)
    # and the downstream planner injects ``WHERE <rls_key> = <claim>`` so each
    # REQUEST is scoped to exactly ONE tenant before this LIMIT applies.  Under
    # that invariant the limit only ever caps a single tenant's rows, so it can
    # never starve one tenant in favour of another within a request.  The default
    # is a per-request row cap, not a cross-tenant cap; callers wanting more rows
    # for a single tenant must paginate or raise mq.limit (up to NUBI_MAX_QUERY_LIMIT).
    for field, direction in mq.order_by:
        outer_select = outer_select.order_by(
            exp.Ordered(this=exp.column(field), desc=(direction == "desc"))
        )
    if mq.limit is not None:
        effective_limit = mq.limit
    else:
        effective_limit = _DEFAULT_LIMIT
        if mq.top_n is not None:
            # Never let the default cap fall below the requested top-N size.
            effective_limit = max(effective_limit, mq.top_n.n)

    # FIX (audit-24 HIGH correctness): for ``top_n.other`` WITH a time_grain the
    # top-N arm spans ``n * num_time_buckets`` rows (one per top series per
    # bucket).  A per-arm LIMIT on the top-N arm caps it at ``effective_limit``
    # while the Other arm stays uncapped -> asymmetric UNION / corrupted stacked
    # time-series.  In that case we must NOT put the LIMIT on the per-arm
    # ``outer_select``; instead we apply it (only if EXPLICITLY provided) on the
    # OUTER union below, capping the combined result rather than per-series length.
    other_timeseries = (
        mq.top_n is not None and mq.top_n.other and time_alias is not None
    )
    if not other_timeseries:
        outer_select = outer_select.limit(effective_limit)

    # ── Top-N restriction on the outer SELECT ────────────────────────────────
    # top_n (no Other) → WHERE dim IN (membership) or QUALIFY RANK() <= N.
    # top_n.other      → the top-N arm of a UNION (membership/QUALIFY) UNIONed
    #                    with an Other-bucket arm; both share the one __base CTE.
    # PERF FIX (LOW): the ``top_n.other`` + ``time_grain`` arm previously built
    # TWO correlated membership subqueries (one in _apply_top_n for the top arm's
    # ``dim IN (...)``, one in _build_other_select for the Other arm's
    # ``dim NOT IN (...)``) — each re-aggregating __base.  When BOTH arms exist
    # (other=True) AND there is a time_grain, compute the per-tenant top-N
    # membership ONCE in a shared __topn_members CTE and have both arms do a cheap
    # correlated lookup into it.  Single-arm (other=False) and no-time-grain
    # (complement-RANK) paths are unchanged — they never built two subqueries.
    share_members = (
        mq.top_n is not None and mq.top_n.other and time_alias is not None
    )

    if mq.top_n is not None:
        outer_select = _apply_top_n(
            outer_select, mq, metric, time_alias, all_dim_names, dialect,
            use_shared_members=share_members,
        )

    # ── Choose the top-level statement (outer SELECT or UNION) ───────────────
    top_stmt: exp.Expression
    if mq.top_n is not None and mq.top_n.other:
        other_select = _build_other_select(
            mq, metric, time_alias, all_dim_names, dialect,
            use_shared_members=share_members,
        )
        # Wrap each arm in parentheses (exp.Subquery) so per-arm ORDER BY / QUALIFY
        # / LIMIT are unambiguous — required by DuckDB / standard SQL for a UNION
        # whose arms carry those clauses.
        union_stmt = exp.Union(
            this=exp.Subquery(this=outer_select),
            expression=exp.Subquery(this=other_select),
            distinct=False,
        )
        # FIX (audit-24 HIGH correctness): with a time_grain the per-arm LIMIT was
        # stripped above; honour an EXPLICIT mq.limit by capping the COMBINED union
        # (top-N + Other rows) here.
        #
        # FIX (HIGH unbounded — regression from fix-25): ALWAYS cap the union.  The
        # earlier code applied the cap ONLY when mq.limit was explicitly set, so a
        # caller that omitted limit produced an UNCAPPED union — skipping the
        # _DEFAULT_LIMIT safety net that protects every other compile path.  We now
        # always cap with the explicit limit when present, else ``effective_limit``
        # (which already encodes _DEFAULT_LIMIT / max(_DEFAULT_LIMIT, top_n.n)).  For
        # a small dataset effective_limit comfortably exceeds n*num_buckets + Other,
        # so the full per-bucket top-N still survives (no truncation, preserving the
        # fix-25 correctness); the cap is purely an unbounded-scan safety net.
        if other_timeseries:
            union_stmt = union_stmt.limit(
                mq.limit if mq.limit is not None else effective_limit
            )
        top_stmt = union_stmt
    else:
        top_stmt = outer_select

    # ── Attach the __base CTE to the SINGLE top-level statement ──────────────
    # .with_ on a Select attaches a WITH clause; on a Union it attaches the WITH
    # at the union level so BOTH arms can reference __base.  Either way the whole
    # statement renders with exactly one ``WITH __base AS (...)``.
    top_stmt = top_stmt.with_("__base", as_=base_select, dialect=dialect)

    # PERF FIX (LOW): when the top arm + Other arm share the membership, attach
    # the per-tenant top-N membership CTE (after __base, which it reads from).
    # Both arms' correlated lookups reference __topn_members instead of each
    # re-aggregating __base — the redundant per-bucket membership is computed
    # ONCE, not twice.
    if share_members:
        tn = mq.top_n
        assert tn is not None
        members_corr_keys = _membership_correlation_keys(
            metric, all_dim_names, tn.dimension
        )
        members_sql = _shared_members_cte_sql(
            tn.dimension,
            tn.measure or metric.measure.name,
            "DESC" if tn.order == "desc" else "ASC",
            tn.n,
            members_corr_keys,
            dialect,
        )
        members_select = sqlglot.parse_one(members_sql, dialect=dialect)
        top_stmt = top_stmt.with_(
            _MEMBERS_CTE, as_=members_select, dialect=dialect
        )

    # ── Render ONCE, substitute sentinels ONCE ───────────────────────────────
    sql = top_stmt.sql(dialect=dialect)
    for sentinel, placeholder in subs.items():
        sql = sql.replace(sentinel, placeholder)

    return sql, params


# ---------------------------------------------------------------------------
# Top-N helper
# ---------------------------------------------------------------------------


def _membership_correlation_keys(
    metric: MetricDefinition,
    all_dim_names: list[str],
    dim_col: str,
) -> tuple[str, ...]:
    """Columns the top-N membership subquery must correlate on for RLS soundness.

    The layered top-N MEMBERSHIP subquery (``SELECT dim FROM __base GROUP BY dim
    ORDER BY SUM(measure) LIMIT N``) reads ``FROM __base`` and is correlated on
    these columns to the outer row so the top-N set is computed PER TENANT, not
    globally across all tenants.

    CRITICAL (MED tenant-correctness): the downstream planner injects its RLS
    predicate (``WHERE <policy_col> = val``) ONLY on the OUTERMOST select — it
    does NOT recurse into the ``__base`` CTE or this membership subquery.  So the
    membership reflects the RLS scope ONLY via the correlation we emit here.

    * When ``metric.rls_keys`` is DECLARED we correlate on exactly those keys
      (the proven, byte-stable behaviour): they are the columns the planner is
      contracted to filter on, and __base projects them.

    * When ``metric.rls_keys`` is EMPTY the metric declares no tenant scoping,
      yet a request may still carry RLS policies.  For a policy to LAND on the
      outer select its column must be projected there — and in the layered form
      the only non-measure, non-time projected columns are the requested
      DIMENSIONS.  So any RLS policy that can actually filter this query must
      target one of the projected dimensions.  We therefore correlate the
      membership on EVERY projected non-ranked dimension (the ranked dim itself
      is the GROUP BY key and must remain free to vary).  This makes the
      membership tenant-scoped whenever the planner filters on a dimension,
      eliminating the cross-tenant top-N leak — while remaining a no-op for the
      single-dimension (ranked-dim-only) global queries that carry no policy.

    The ranked ``dim_col`` is always excluded (it is the membership's GROUP BY
    key); the time bucket is never a correlation target and is not in
    ``all_dim_names``.
    """
    if metric.rls_keys:
        return tuple(metric.rls_keys)
    return tuple(d for d in all_dim_names if d != dim_col)


def _has_extra_non_ranked_dims(
    all_dim_names: list[str],
    dim_col: str,
    corr_keys: tuple[str, ...],
) -> bool:
    """True iff a projected dim is NEITHER the ranked dim NOR a correlation key.

    FIX (MED engine-correctness — no-time-grain multi-dim top_n ranks per-row
    not per-dim-total): the no-time-grain top_n path normally restricts via
    ``QUALIFY RANK() OVER (PARTITION BY corr_keys ORDER BY measure) <= N`` (the
    Other arm uses the complement ``RANK() > N``).  That is correct ONLY when
    ``__base`` has exactly ONE row per ``(corr_keys, ranked_dim)`` — i.e. the
    projected dimensions are just the ranked dim plus the rank PARTITION BY
    columns (``corr_keys``).  Then each per-row measure IS the per-ranked-dim
    total within its partition, so RANK over rows == RANK over per-dim totals.

    When an ADDITIONAL dimension is projected that is NEITHER the ranked dim NOR
    in ``corr_keys`` (e.g. ranked dim is ``category``, corr_keys is the declared
    rls_key ``org_id``, but the query ALSO groups by ``region``), ``__base`` has
    MULTIPLE rows per ``(corr_keys, ranked_dim)`` — one per (category, region).
    The RANK window then ranks INDIVIDUAL per-(category, region) rows by their
    per-row measure, NOT the per-CATEGORY totals.  Result: a category whose
    per-region rows each rank low individually is wrongly excluded from the top-N
    even though its TOTAL (summed over regions) is high, and the Other bucket is
    correspondingly wrong.

    When this returns True the no-time-grain top_n must take the SAME correlated
    membership-subquery path the time_grain branch uses (``dim IN (SELECT dim
    FROM __base WHERE corr correlated GROUP BY dim ORDER BY SUM(measure) ...
    LIMIT N)``), which aggregates per ranked-dim TOTAL before selecting the
    top-N — correct for any number of extra dims.

    ``corr_keys`` are the rank PARTITION BY columns (see
    _membership_correlation_keys): the declared rls_keys, or — when rls_keys is
    empty — EVERY projected non-ranked dim.  In the empty-rls_keys case every
    non-ranked dim is therefore a corr_key, so this returns False (each
    (dim_col, corr_keys) tuple is unique → per-row == per-dim-total) and the
    existing complement-RANK / PARTITION BY path is preserved byte-identically.
    The bug case only arises when rls_keys IS declared and a further analytical
    dim is projected beyond ranked + rls_keys.  Returns False for the
    single-ranked-dim (and ranked-dim + corr_keys-only) shapes, so those stay
    byte-identical to the prior QUALIFY RANK() emission.
    """
    corr = set(corr_keys)
    return any(d != dim_col and d not in corr for d in all_dim_names)


# Name of the shared per-tenant top-N membership CTE used by the
# ``top_n.other`` + ``time_grain`` path (see _shared_members_cte_sql).
_MEMBERS_CTE = "__topn_members"


def _shared_members_cte_sql(
    dim_col: str,
    rank_measure: str,
    order_dir: str,
    n: int,
    corr_keys: tuple[str, ...],
    dialect: str,
) -> str:
    """Build the shared per-tenant top-N membership CTE body (``top_n.other`` + grain).

    PERF FIX (LOW): the ``top_n.other`` + ``time_grain`` path previously emitted
    TWO separate correlated membership subqueries against ``__base`` — one for the
    top-N arm's ``dim IN (...)`` and one for the Other arm's ``dim NOT IN (...)``.
    Each re-scanned and re-aggregated ``__base`` (``GROUP BY corr_keys, dim`` +
    ``SUM(rank_measure)``) once PER OUTER ROW — redundant work computed twice.

    This helper computes the per-tenant top-N membership set EXACTLY ONCE as a
    small CTE.  Both arms then do a cheap correlated lookup into this tiny
    pre-computed set (n rows per tenant) instead of re-aggregating ``__base``.

    Correctness (must stay byte-identical in RESULT to the prior two subqueries):

    * The membership computed here is the SAME set the prior
      ``_top_n_membership_sql`` produced — ``GROUP BY [corr_keys,] dim`` ordered
      by ``SUM(rank_measure) <dir>, dim ASC`` keeping the first ``n`` PER TENANT.
      We use ``ROW_NUMBER() OVER (PARTITION BY corr_keys ORDER BY
      SUM(rank_measure) <dir>, dim ASC) <= n``.  Because ``dim`` is the GROUP BY
      key (unique within each tenant partition) the ``..., dim ASC`` tiebreaker
      is a TOTAL order, so ROW_NUMBER ≤ n selects the IDENTICAL n rows the prior
      ``ORDER BY ... LIMIT n`` did — same tie semantics, same per-tenant scope.
    * NULL-dim rows are excluded here (``dim IS NOT NULL``), exactly as the prior
      membership did, so NULL never becomes a member and the Other arm's
      ``OR dim IS NULL`` continues to route NULL-dim rows into Other.
    * ``corr_keys`` carries the per-tenant correlation columns (rls_keys when
      declared; otherwise the projected non-ranked dims — see
      _membership_correlation_keys).  When empty (single-tenant) there is no
      PARTITION BY, matching the prior uncorrelated ``LIMIT n``.

    The CTE projects ``corr_keys`` + ``dim_col`` so each arm can correlate its
    lookup on the tenant columns.

    SQLi defense-in-depth: dim_col, rank_measure and every corr_key are
    interpolated UNQUOTED here; all are _IDENT_RE-validated upstream (see
    _top_n_membership_sql for the provenance chain; corr_keys come from
    metric.rls_keys / projected dimensions / _IDENT_RE-filtered policy_cols).
    Assert defensively so this build site fails closed.
    """
    if not _IDENT_RE.fullmatch(dim_col):
        raise MetricError(
            "bad_dimension_name",
            f"top_n dimension {dim_col!r} is not a valid SQL identifier.",
        )
    if not _IDENT_RE.fullmatch(rank_measure):
        raise MetricError(
            "bad_measure_name",
            f"top_n rank measure {rank_measure!r} is not a valid SQL identifier.",
        )
    for k in corr_keys:
        if not _IDENT_RE.fullmatch(k):
            raise MetricError(
                "bad_dimension_name",
                f"top_n correlation column {k!r} is not a valid SQL identifier.",
            )
    select_cols = ", ".join([*corr_keys, dim_col])
    partition_clause = (
        f"PARTITION BY {', '.join(corr_keys)} " if corr_keys else ""
    )
    # Inner aggregation: one row per (corr_keys, dim) with the ranked sum, then
    # rank within each tenant partition and keep the top n.
    return (
        f"SELECT {select_cols} FROM ("
        f"SELECT {select_cols}, "
        f"ROW_NUMBER() OVER ({partition_clause}"
        f"ORDER BY SUM({rank_measure}) {order_dir}, {dim_col} ASC) AS __rn "
        f"FROM __base "
        f"WHERE {dim_col} IS NOT NULL "
        f"GROUP BY {select_cols}"
        f") AS __ranked WHERE __rn <= {n}"
    )


def _members_lookup_sql(
    dim_col: str, corr_keys: tuple[str, ...], outer_alias: str
) -> str:
    """Correlated lookup of dim members from the shared ``__topn_members`` CTE.

    Replaces the per-arm re-aggregating membership subquery: instead of scanning
    ``__base`` it reads the tiny pre-ranked CTE, correlated on the tenant columns
    to the outer row (so the per-tenant scoping is identical).  When there are no
    correlation columns the lookup is uncorrelated (global membership), matching
    the prior uncorrelated ``LIMIT n``.
    """
    if corr_keys:
        preds = " AND ".join(
            f"{_MEMBERS_CTE}.{k} = {outer_alias}.{k}" for k in corr_keys
        )
        return (
            f"SELECT {dim_col} FROM {_MEMBERS_CTE} WHERE {preds}"
        )
    return f"SELECT {dim_col} FROM {_MEMBERS_CTE}"


def _top_n_membership_sql(
    dim_col: str,
    rank_measure: str,
    order_dir: str,
    n: int,
    rls_keys: tuple[str, ...],
    outer_alias: str = "__outer",
) -> str:
    """Build the per-tenant top-N membership subquery SQL.

    When ``rls_keys`` (here: the membership *correlation columns*, see
    :func:`_membership_correlation_keys`) is non-empty the subquery is correlated
    on every such column so the top-N set is computed per-tenant (per RLS policy
    value), not globally across all tenants.

    The outer SELECT already uses ``FROM __base AS <outer_alias>`` so each
    correlation column is unambiguously qualified as ``<outer_alias>.<col>``.

    Emits:
        SELECT <dim_col> FROM __base
        [WHERE __base.<col1> = <outer_alias>.<col1> [AND ...]]
        GROUP BY <dim_col>
        ORDER BY SUM(<rank_measure>) <order_dir>, <dim_col> ASC
        LIMIT <n>

    DETERMINISM FIX: a STABLE secondary tiebreaker (``<dim_col> ASC``) is
    appended to the ORDER BY so that, at a tie on ``SUM(<rank_measure>)``, the
    membership set is selected deterministically.  The top-N arm and the Other
    arm both build their membership via this same helper (differing only by the
    outer alias), so without a tiebreaker a boundary tie could resolve
    differently in the two arms — double-counting or dropping a tied member.
    The dim column is the GROUP BY key (unique per row here), so it is a total,
    stable ordering of the tied group.
    """
    # SQLi defense-in-depth: dim_col and rank_measure are interpolated UNQUOTED
    # into this f-string.  _govern guarantees both are _IDENT_RE-safe before any
    # caller reaches here: dim_col == tn.dimension is validated to be in
    # mq.dimensions (line ~1465), every requested dimension must be an allowed
    # metric dimension (line ~1295), and every metric dimension name is
    # _IDENT_RE-validated (line ~1244); rank_measure == (tn.measure or
    # metric.measure.name) is validated to be a base/derived measure (line
    # ~1472), and every base/derived measure name is _IDENT_RE-validated (lines
    # ~1251/~1258).  Assert it here so this build site fails closed should any
    # future path reach it without governance.
    if not _IDENT_RE.fullmatch(dim_col):
        raise MetricError(
            "bad_dimension_name",
            f"top_n dimension {dim_col!r} is not a valid SQL identifier.",
        )
    if not _IDENT_RE.fullmatch(rank_measure):
        raise MetricError(
            "bad_measure_name",
            f"top_n rank measure {rank_measure!r} is not a valid SQL identifier.",
        )
    # NULL-soundness: the membership subquery feeds an `<dim> IN (...)` (top-N
    # arm) and `<dim> NOT IN (...)` (Other arm) test.  A NULL member poisons
    # SQL's three-valued logic: `x NOT IN (..., NULL, ...)` is NULL (never TRUE)
    # for every x, which would silently empty the ENTIRE Other bucket.  Exclude
    # NULL-dim rows from membership so NULL can never become a member; NULL-dim
    # rows are then routed into the Other bucket by _build_other_select's
    # `OR <dim> IS NULL` clause.
    null_filter = f"{dim_col} IS NOT NULL"
    if rls_keys:
        rls_predicates = " AND ".join(
            f"__base.{k} = {outer_alias}.{k}" for k in rls_keys
        )
        where_clause = f"WHERE {rls_predicates} AND {null_filter} "
    else:
        where_clause = f"WHERE {null_filter} "
    return (
        f"SELECT {dim_col} FROM __base "
        f"{where_clause}"
        f"GROUP BY {dim_col} "
        f"ORDER BY SUM({rank_measure}) {order_dir}, {dim_col} ASC "
        f"LIMIT {n}"
    )


def _apply_top_n(
    outer_select: exp.Select,
    mq: MetricQuery,
    metric: MetricDefinition,
    time_alias: str | None,
    all_dim_names: list[str],
    dialect: str,
    use_shared_members: bool = False,
) -> exp.Select:
    """Wrap the outer select with a top-N filter.

    With a time_grain the outer SELECT has one row per (dim, time_bucket).
    A nested window-in-window (RANK OVER (ORDER BY SUM() OVER ())) is invalid
    in DuckDB/PG, so instead we use a membership filter:
        WHERE <dim_col> IN (
            SELECT <dim_col> FROM __base
            [WHERE __base.<rls_key> = __outer.<rls_key> [AND ...]]
            GROUP BY <dim_col>
            ORDER BY SUM(<rank_measure>) DESC
            LIMIT <n>
        )
    The membership subquery is correlated on every metric.rls_keys column so
    the top-N set is computed per-tenant, not globally across all tenants.
    Without a time_grain the per-row measure value is already the aggregate,
    so the simple QUALIFY RANK() OVER (ORDER BY measure) <= N is valid.
    """
    tn = mq.top_n
    assert tn is not None

    rank_measure = tn.measure or metric.measure.name
    dim_col = tn.dimension
    # Membership/ranking correlation columns (rls_keys when declared; otherwise
    # the projected non-ranked dimensions — see _membership_correlation_keys).
    corr_keys = _membership_correlation_keys(metric, all_dim_names, dim_col)

    # FIX (MED engine-correctness — no-time-grain multi-dim top_n): take the
    # correlated membership-subquery path (which ranks by per-ranked-dim TOTALS)
    # whenever there is a time_grain OR a projected non-ranked dim that is not an
    # rls_key.  In the latter case __base has multiple rows per
    # (rls_key, ranked_dim), so the QUALIFY RANK() path would rank individual
    # per-all-dims rows rather than per-ranked-dim totals (wrong membership +
    # wrong Other bucket).  Single-ranked-dim (and ranked-dim + rls_keys-only)
    # shapes keep the byte-identical QUALIFY RANK() emission.
    use_membership = time_alias is not None or _has_extra_non_ranked_dims(
        all_dim_names, dim_col, corr_keys
    )

    if use_membership:
        # With time grain (or extra non-ranked dims): membership filter via
        # correlated subquery on __base.
        order_dir = "DESC" if tn.order == "desc" else "ASC"
        if use_shared_members:
            # PERF FIX (LOW): reference the shared per-tenant membership CTE
            # (computed ONCE in _compile_layered) instead of re-aggregating
            # __base here.  The lookup is correlated on corr_keys to __outer so
            # the per-tenant scope is identical to the prior subquery.
            membership_sql = _members_lookup_sql(dim_col, corr_keys, "__outer")
        else:
            membership_sql = _top_n_membership_sql(
                dim_col, rank_measure, order_dir, tn.n,
                corr_keys, outer_alias="__outer",
            )
        membership_expr = sqlglot.parse_one(membership_sql, dialect=dialect)
        # wrap in exp.Subquery so sqlglot renders "IN (SELECT ...)" not "IN SELECT ...".
        where_cond = exp.In(
            this=exp.column(dim_col),
            query=exp.Subquery(this=membership_expr),
        )
        outer_select = outer_select.where(where_cond)
    else:
        # No time grain: simple QUALIFY RANK() <= N.
        # RLS soundness: the rank MUST be partitioned by every rls_key.  For the
        # top_n.other path the planner injects RLS on the OUTER union wrapper —
        # i.e. AFTER this RANK is computed inside the top-N arm — so without a
        # PARTITION BY the rank would be computed across ALL tenants and the
        # post-rank RLS filter could drop a tenant's entire top-N set (or admit
        # another tenant's members).  Partitioning by rls_keys makes the rank
        # per-tenant, matching the time_grain path's per-tenant correlated
        # membership subquery.  For the non-other path RLS lands on this same
        # select (WHERE before window), so the partition is a harmless no-op
        # (a single partition per tenant).
        rank_m_col = exp.column(rank_measure)
        rank_partition = [exp.column(k) for k in corr_keys]
        rank_over = exp.Window(
            this=exp.func("RANK", dialect=dialect),
            partition_by=rank_partition or None,
            order=exp.Order(
                expressions=[
                    exp.Ordered(
                        this=rank_m_col,
                        desc=(tn.order == "desc"),
                    )
                ]
            ),
        )
        qualify_cond = exp.LTE(
            this=rank_over,
            expression=exp.Literal.number(tn.n),
        )
        outer_select = outer_select.qualify(qualify_cond)
    return outer_select


# ---------------------------------------------------------------------------
# Top-N "Other" bucket
# ---------------------------------------------------------------------------


def _build_other_select(
    mq: "MetricQuery",
    metric: "MetricDefinition",
    time_alias: str | None,
    all_dim_names: list[str],
    dialect: str,
    use_shared_members: bool = False,
) -> exp.Select:
    """Build the Other-bucket arm of a ``top_n.other`` UNION as a single AST node.

    The returned :class:`exp.Select` reads ``FROM __base`` (the shared CTE) and
    rolls every non-top-N member of the ranked dimension into one bucket labelled
    ``tn.other_label``.  Its column list (names + order) is IDENTICAL to the
    top-N arm so the UNION is well-formed:

        ranked dim            → other_label literal AS <dim>
        other dims            → passthrough
        time alias            → passthrough
        base measures         → re-aggregated per agg (sum/count→SUM, min→MIN,
                                 max→MAX, avg/count_distinct/percentile/approx→NULL)
        derived measures      → recomputed from the re-aggregated base measures
        time-comparison cols  → NULL AS <out_name> (windows can't span the bucket)

    Members are excluded via ``WHERE <dim> NOT IN (<membership>)`` where the
    membership subquery is the SAME per-tenant-correlated top-N set as the top-N
    arm (correlated on every rls_key when a time_grain is set), so a dim is never
    double-counted nor dropped.

    Everything is AST: the membership subquery and the re-aggregation/derived
    fragments are parsed into nodes and embedded — no string concatenation, no
    re-parse of already-rendered SQL.
    """
    tn = mq.top_n
    assert tn is not None and tn.other

    rank_measure = tn.measure or metric.measure.name
    dim_col = tn.dimension
    order_dir = "DESC" if tn.order == "desc" else "ASC"

    # ── Membership / complement-rank exclusion ────────────────────────────────
    # FIX (Issue 2 tie semantics): the TOP arm and OTHER arm MUST partition every
    # __base row into exactly one bucket.  The no-time-grain TOP arm uses
    # QUALIFY RANK() OVER (PARTITION BY rls_keys ORDER BY measure) <= N.  RANK
    # keeps ALL rows tied at the N-th boundary, but a LIMIT-N membership subquery
    # keeps exactly N — so boundary-tie rows would land in BOTH arms (double
    # count).  We therefore make the no-time-grain OTHER arm use the COMPLEMENT of
    # the same RANK window (RANK(...) > N): every row whose rank is <= N is in the
    # TOP arm, every row whose rank is > N is in the OTHER arm — a clean partition
    # with no double-counting and no dropped rows, identical RANK computation on
    # both sides.  The time-grain path keeps the correlated membership subquery
    # (the outer SELECT there has one row per (dim, bucket) so a per-bucket RANK
    # would not match the per-dim top-N semantics).
    # Correlation columns MUST be identical to the TOP arm (_apply_top_n) so the
    # two arms partition every __base row into exactly one bucket — see
    # _membership_correlation_keys (rls_keys when declared; otherwise the
    # projected non-ranked dims, so an empty-rls_keys metric queried with a
    # dimension-targeted RLS policy still gets a per-tenant Other bucket).
    _other_rls_keys = _membership_correlation_keys(metric, all_dim_names, dim_col)
    not_in_cond: exp.Expression | None = None
    complement_qualify: exp.Expression | None = None

    # FIX (MED engine-correctness — no-time-grain multi-dim top_n): mirror
    # _apply_top_n.  When there is a time_grain OR a projected non-ranked dim that
    # is not an rls_key, the TOP arm uses the correlated membership subquery
    # (ranking by per-ranked-dim TOTALS), so the OTHER arm MUST use the exact
    # complement of that SAME membership (``<dim> NOT IN (membership)``), NOT the
    # per-row complement-RANK window — otherwise the two arms would partition
    # __base by different criteria (per-dim-total vs per-row) and a row could land
    # in both or neither.  Only the single-ranked-dim (and ranked-dim +
    # rls_keys-only) no-time-grain shape keeps the complement-RANK path
    # (byte-identical to before).
    use_membership = time_alias is not None or _has_extra_non_ranked_dims(
        all_dim_names, dim_col, _other_rls_keys
    )

    if not use_membership:
        # Complement RANK window — mirror _apply_top_n's no-time-grain arm.
        rank_m_col = exp.column(rank_measure)
        rank_partition = [exp.column(k) for k in _other_rls_keys]
        rank_over = exp.Window(
            this=exp.func("RANK", dialect=dialect),
            partition_by=rank_partition or None,
            order=exp.Order(
                expressions=[
                    exp.Ordered(
                        this=rank_m_col,
                        desc=(tn.order == "desc"),
                    )
                ]
            ),
        )
        complement_qualify = exp.GT(
            this=rank_over,
            expression=exp.Literal.number(tn.n),
        )
        from_node: exp.Expression = exp.Table(this=exp.to_identifier("__base"))
    else:
        # Membership path (time-grain OR extra non-ranked dims): per-tenant
        # correlated membership subquery (NOT IN), ranking by per-ranked-dim
        # TOTALS so the Other bucket is the exact complement of the TOP arm's
        # per-dim-total membership.
        # PERF FIX (LOW): when use_shared_members is set the membership has
        # already been computed ONCE in the shared __topn_members CTE; this arm
        # does a cheap correlated lookup into it instead of re-aggregating
        # __base a SECOND time.  The membership SET is identical to the top-N
        # arm's (both read the same CTE), so the TOP/NOT-IN partition, the
        # per-tenant scope and the tie semantics are byte-identical in RESULT.
        if _other_rls_keys:
            if use_shared_members:
                membership_sql = _members_lookup_sql(
                    dim_col, _other_rls_keys, "__other_outer"
                )
            else:
                membership_sql = _top_n_membership_sql(
                    dim_col, rank_measure, order_dir, tn.n,
                    rls_keys=_other_rls_keys, outer_alias="__other_outer",
                )
            from_node = exp.alias_(
                exp.Table(this=exp.to_identifier("__base")), "__other_outer"
            )
        else:
            if use_shared_members:
                membership_sql = _members_lookup_sql(dim_col, (), "__other_outer")
            else:
                membership_sql = _top_n_membership_sql(
                    dim_col, rank_measure, order_dir, tn.n, rls_keys=(),
                )
            from_node = exp.Table(this=exp.to_identifier("__base"))
        membership_expr = sqlglot.parse_one(membership_sql, dialect=dialect)
        # NULL-soundness: the membership subquery now excludes NULL-dim rows
        # (see _top_n_membership_sql), so the top-N set never contains NULL and
        # `<dim> NOT IN (...)` is no longer poisoned to NULL.  But a NULL-dim row
        # itself still yields `NULL NOT IN (...)` => NULL (never TRUE), so it
        # would be dropped from the Other bucket too.  Route NULL-dim rows into
        # Other explicitly with `OR <dim> IS NULL` so no row is ever dropped.
        not_in_cond = exp.Or(
            this=exp.Not(
                this=exp.paren(
                    exp.In(
                        this=exp.column(dim_col),
                        query=exp.Subquery(this=membership_expr),
                    )
                )
            ),
            expression=exp.Is(
                this=exp.column(dim_col),
                expression=exp.Null(),
            ),
        )

    # ── Projection (column order MUST match the top-N arm) ───────────────────
    select_exprs: list[exp.Expression] = []

    # (a) dimension columns: ranked dim → label literal; others → passthrough.
    for d in all_dim_names:
        if d == dim_col:
            select_exprs.append(
                exp.alias_(exp.Literal.string(tn.other_label), dim_col)
            )
        else:
            select_exprs.append(exp.alias_(exp.column(d), d))

    # (b) time alias passthrough.
    if time_alias is not None:
        select_exprs.append(exp.alias_(exp.column(time_alias), time_alias))

    # (c) base measures: re-aggregate per agg type (build via AST).
    #   sum/count → SUM(name)   — additive: rolling up partials is exact.
    #   min       → MIN(name)   — additive: min-of-mins is still the global min.
    #   max       → MAX(name)   — additive: max-of-maxes is still the global max.
    #   _NON_ADDITIVE_AGGS      → NULL     — INTENTIONAL: these aggs (avg,
    #       count_distinct, percentile_cont, approx_count_distinct) cannot be
    #       correctly re-aggregated from pre-computed bucket values.  Emitting
    #       NULL makes the "not available" semantics explicit to callers rather
    #       than returning a silently wrong number.  Callers should surface this
    #       as "N/A" or omit the column for the Other bucket.
    #       See _NON_ADDITIVE_AGGS docstring and test_other_bucket_avg_emits_null.
    bare_name_to_agg_node: dict[str, exp.Expression] = {}
    for m in metric.measures():
        if m.agg == "min":
            agg_node: exp.Expression = exp.func("MIN", exp.column(m.name), dialect=dialect)
        elif m.agg == "max":
            agg_node = exp.func("MAX", exp.column(m.name), dialect=dialect)
        elif m.agg in ("sum", "count"):
            agg_node = exp.func("SUM", exp.column(m.name), dialect=dialect)
        else:
            # Non-additive agg (member of _NON_ADDITIVE_AGGS): emit NULL.
            # Re-aggregation is mathematically undefined for this agg type;
            # NULL is the correct sentinel for "Other bucket value unavailable".
            assert m.agg in _NON_ADDITIVE_AGGS, f"Unexpected agg {m.agg!r} not in _NON_ADDITIVE_AGGS"
            agg_node = exp.Null()
        bare_name_to_agg_node[m.name] = agg_node
        select_exprs.append(exp.alias_(agg_node.copy(), m.name))

    # (d) derived measures: recompute from the re-aggregated base measures.
    # Compile the formula to SQL (NULLIF-guarded), parse to an AST, then replace
    # every bare base-measure column reference with its aggregate node so the
    # GROUP BY query is valid (bare measure names are not in the GROUP BY).
    base_measure_names_set = set(metric.measure_names())
    for dm in metric.derived_measures:
        formula_sql = _compile_derived_formula(dm, base_measure_names_set)
        formula_expr = sqlglot.parse_one(formula_sql, dialect=dialect)

        def _replace_col(node: exp.Expression) -> exp.Expression:
            if isinstance(node, exp.Column) and node.name in bare_name_to_agg_node:
                return bare_name_to_agg_node[node.name].copy()
            return node

        formula_expr = formula_expr.transform(_replace_col)
        select_exprs.append(exp.alias_(formula_expr, dm.name))

    # (e) time-comparison columns → NULL AS <out_name> (column-count parity).
    regular_comparisons = [
        tc for tc in mq.time_comparisons if tc.kind != "latest_snapshot"
    ]
    for tc in regular_comparisons:
        select_exprs.append(exp.alias_(exp.Null(), tc.out_name()))

    # ── GROUP BY: time alias (if any) + every non-ranked dim ─────────────────
    group_exprs: list[exp.Expression] = []
    if time_alias is not None:
        group_exprs.append(exp.column(time_alias))
    for d in all_dim_names:
        if d != dim_col:
            group_exprs.append(exp.column(d))

    # ── Assemble ──────────────────────────────────────────────────────────────
    # No-time-grain (complement RANK) path: the QUALIFY RANK(...) > N must be
    # evaluated on individual __base rows BEFORE this arm's roll-up aggregation.
    # We therefore pre-filter __base in a subquery (SELECT * FROM __base QUALIFY
    # RANK(...) > N) AS __other_base and aggregate over it.  This guarantees every
    # __base row whose rank exceeds N (the exact complement of the TOP arm's
    # rank <= N) is rolled into the Other bucket — boundary ties included exactly
    # once, never double-counted.
    if complement_qualify is not None:
        complement_base = (
            exp.Select()
            .select(exp.Star())
            .from_(exp.Table(this=exp.to_identifier("__base")))
            .qualify(complement_qualify)
        )
        complement_from = exp.Subquery(
            this=complement_base,
            alias=exp.TableAlias(this=exp.to_identifier("__other_base")),
        )
        other_select = exp.Select().select(*select_exprs).from_(complement_from)
    else:
        other_select = exp.Select().select(*select_exprs).from_(from_node)
        assert not_in_cond is not None
        other_select = other_select.where(not_in_cond)
    if group_exprs:
        other_select = other_select.group_by(*group_exprs)
    return other_select


# ---------------------------------------------------------------------------
# Derived formula compiler
# ---------------------------------------------------------------------------


def _compile_derived_formula(dm: DerivedMeasure, base_names: set[str]) -> str:
    """Translate a DerivedMeasure formula to SQL, guarding division denominators.

    Division ``a / b`` becomes ``a / NULLIF(b, 0)``.  Identifiers must be
    base measure names (governance check already done in _govern; this is the
    mechanical translation).
    """
    formula = dm.formula.strip()
    # Tokenize to replace NAME tokens with column refs (no-op here since names
    # are already valid SQL column names) and to track division denominators.
    # We do this with a simple recursive-descent approach using the token stream.
    tokens = _tokenize_formula(formula)
    result_tokens = _guard_divisions(tokens)
    return " ".join(result_tokens)


def _tokenize_formula(formula: str) -> list[tuple[int, str]]:
    """Tokenize an arithmetic formula, returning (toktype, string) pairs."""
    tokens: list[tuple[int, str]] = []
    try:
        for tok in tokenize.generate_tokens(io.StringIO(formula).readline):
            if tok.type in (tokenize.NEWLINE, tokenize.NL, tokenize.COMMENT,
                            tokenize.ENCODING, tokenize.ENDMARKER):
                continue
            tokens.append((tok.type, tok.string))
    except tokenize.TokenError:
        pass
    return tokens


def _guard_divisions(tokens: list[tuple[int, str]]) -> list[str]:
    """Walk the token list and wrap every immediate '/' denominator in NULLIF(..., 0).

    This handles simple cases like ``a / b`` and ``a / (b + c)``.

    FIX #7: Also recurse into parenthesised groups so that a/(b/c) guards the
    inner b/c expression before wrapping the outer group in NULLIF.
    """
    result: list[str] = []
    i = 0
    while i < len(tokens):
        tok_type, tok_str = tokens[i]
        if tok_str == "/" and i + 1 < len(tokens):
            result.append("/")
            # Collect the denominator (next atom or parenthesised group).
            i += 1
            denom_token_tuples, advance = _collect_atom(tokens, i)
            # FIX #7: recurse into the denominator to guard any nested divisions.
            inner_guarded = _guard_divisions(denom_token_tuples)
            denom_sql = " ".join(inner_guarded)
            result.append(f"NULLIF({denom_sql}, 0)")
            i += advance
        else:
            result.append(tok_str)
            i += 1
    return result


def _collect_atom(
    tokens: list[tuple[int, str]], start: int
) -> tuple[list[tuple[int, str]], int]:
    """Collect the next arithmetic atom (name, number, or parenthesised group).

    Returns (token_tuples, count_consumed).  Returning tuples (not bare strings)
    lets _guard_divisions recurse safely into parenthesised groups (Fix #7).
    """
    if start >= len(tokens):
        return [], 0

    # FIX (NULLIF unary-minus): a division denominator may be a unary-signed
    # atom, e.g. ``a / -b``, ``a / -(b)``, ``a / +b``, or even ``a / - -b``.
    # Tokenization yields the sign as a separate OP token, so collecting only the
    # next single atom would mis-wrap (``NULLIF(-, 0) b``).  Consume any run of
    # leading unary +/- signs FIRST, then the atom they apply to, and return the
    # whole signed expression so NULLIF wraps the full denominator.
    sign_tokens: list[tuple[int, str]] = []
    i = start
    while i < len(tokens) and tokens[i][1] in ("-", "+"):
        sign_tokens.append(tokens[i])
        i += 1
    if sign_tokens:
        atom_tokens, atom_advance = _collect_atom(tokens, i)
        return sign_tokens + atom_tokens, len(sign_tokens) + atom_advance

    tok_type, tok_str = tokens[start]
    if tok_str == "(":
        # Collect until matching close paren — return raw (type, str) tuples so
        # the caller can recurse with _guard_divisions on the inner group.
        depth = 0
        collected: list[tuple[int, str]] = []
        i = start
        while i < len(tokens):
            tt, ts = tokens[i]
            collected.append((tt, ts))
            if ts == "(":
                depth += 1
            elif ts == ")":
                depth -= 1
                if depth == 0:
                    return collected, i - start + 1
            i += 1
        return collected, i - start
    else:
        return [(tok_type, tok_str)], 1


# ---------------------------------------------------------------------------
# Governance
# ---------------------------------------------------------------------------


def _time_alias(metric: MetricDefinition, grain: str) -> str:
    """Stable alias for the time bucket: ``<column>_<grain>``."""
    assert metric.time_dimension is not None
    return f"{metric.time_dimension.column}_{grain}"


def _govern(metric: MetricDefinition, mq: MetricQuery) -> str | None:
    """Validate *mq* against *metric*; return the time-bucket alias (or None).

    Raises :class:`MetricError` on the first violation.  Performs ALL checks
    before any SQL is built.
    """
    # source: exactly one of base_table / base_sql.
    if bool(metric.base_table) == bool(metric.base_sql):
        raise MetricError(
            "no_source",
            "Metric must declare exactly one of base_table or base_sql.",
        )

    # FIX issue 3: validate all dimension names and derived-measure names are
    # safe SQL identifiers.  These names are interpolated into f-strings in the
    # membership subquery, Other-bucket SELECT, prior_period subquery, etc.
    # A name like "region; DROP TABLE t --" would be a SQL injection vector.
    for dim in metric.dimensions:
        if not _IDENT_RE.fullmatch(dim.name):
            raise MetricError(
                "bad_dimension_name",
                f"Dimension name {dim.name!r} is not a valid SQL identifier "
                f"(must match [A-Za-z_][A-Za-z0-9_]*).",
            )
    for dm in metric.derived_measures:
        if not _IDENT_RE.fullmatch(dm.name):
            raise MetricError(
                "bad_derived_measure_name",
                f"DerivedMeasure name {dm.name!r} is not a valid SQL identifier "
                f"(must match [A-Za-z_][A-Za-z0-9_]*).",
            )
    for m in metric.measures():
        if not _IDENT_RE.fullmatch(m.name):
            raise MetricError(
                "bad_measure_name",
                f"Measure name {m.name!r} is not a valid SQL identifier "
                f"(must match [A-Za-z_][A-Za-z0-9_]*).",
            )

    # FIX (Issue 1 SQLi): validate rls_keys are safe SQL identifiers.
    # rls_keys are interpolated directly (without quoting) into f-string SQL
    # in _top_n_membership_sql (~line 651), _prior_year_subquery_sql (~line
    # 1605-1607), and _prior_period_subquery_sql (~line 1666-1668).
    # An rls_key like "org_id; DROP TABLE t --" would be a SQL injection vector.
    for rls_key in metric.rls_keys:
        if not _IDENT_RE.fullmatch(rls_key):
            raise MetricError(
                "bad_rls_key",
                f"rls_key {rls_key!r} is not a valid SQL identifier "
                f"(must match [A-Za-z_][A-Za-z0-9_]*).",
            )

    # FIX (Issue 1 SQLi): UNCONDITIONALLY validate metric.time_dimension.column
    # is a safe SQL identifier whenever a time_dimension exists.  This column is
    # interpolated UNQUOTED into time_alias f-strings used by the
    # _prior_year_subquery_sql / _prior_period_subquery_sql / ytd/qtd/mtd window
    # helpers (the time_alias is "<column>_<grain>").  Previously it was only
    # validated in the latest_snapshot path, leaving every other time-comparison
    # path open to injection via a crafted time_dimension.column.
    if metric.time_dimension is not None:
        td_col = metric.time_dimension.column
        if not _IDENT_RE.fullmatch(td_col):
            raise MetricError(
                "bad_time_column",
                f"time_dimension.column {td_col!r} is not a valid SQL identifier "
                f"(must match [A-Za-z_][A-Za-z0-9_]*).",
            )

        # FIX (audit-24 HIGH SQLi): every declared grain MUST be in the canonical
        # _TIME_GRAINS set.  Grains are interpolated UNQUOTED into DATE_TRUNC(...)
        # f-strings in the layered compiler (~1414) and in
        # _prior_year_subquery_sql / _prior_period_subquery_sql.  models.py accepts
        # arbitrary strings for grains, so a first-party writer could register a
        # MetricDefinition with a poisoned grain (e.g. one that closes the quote and
        # appends UNION SELECT ...) and then query with it -> SQL injection.  Reject
        # any poisoned definition here so a malicious grain can never reach SQL.
        for g in metric.time_dimension.grains:
            if g not in _TIME_GRAINS:
                raise MetricError(
                    "bad_declared_grain",
                    f"time_dimension grain {g!r} on metric {metric.id!r} is not a "
                    f"known time grain (allowed: "
                    f"{', '.join(sorted(_TIME_GRAINS))}).",
                )

    # FIX (MED resource — unbounded mq.dimensions): cap the COUNT of requested
    # dimensions.  Each requested dim becomes an extra GROUP BY column and, on the
    # layered path, an extra PARTITION BY term and LATERAL correlation column —
    # so an unbounded list is an O(N_dims × N_tc) fan-out + GROUP BY cardinality
    # explosion that compiles and executes BEFORE the LIMIT can cap anything.
    # Reject at compile time with a structured 400 (never a runtime blow-up).
    if len(mq.dimensions) > _MAX_DIMS:
        raise MetricError(
            "too_many_dimensions",
            f"MetricQuery requests {len(mq.dimensions)} dimensions, exceeding the "
            f"maximum of {_MAX_DIMS} (NUBI_MAX_DIMS).",
        )

    # requested dimensions must be allowed.
    for dim_name in mq.dimensions:
        if metric.dimension(dim_name) is None:
            raise MetricError(
                "unknown_dimension",
                f"Dimension {dim_name!r} is not an allowed dimension of "
                f"metric {metric.id!r}.",
            )

    # time grain.
    time_alias: str | None = None
    if mq.time_grain is not None:
        td = metric.time_dimension
        if td is None:
            raise MetricError(
                "no_time_dimension",
                f"Metric {metric.id!r} has no time dimension; cannot apply a "
                f"time_grain.",
            )
        # FIX (audit-24 HIGH SQLi, defense-in-depth): the requested grain MUST be a
        # canonical grain BEFORE we even check it against td.grains.  This guards a
        # MetricQuery constructed without going through a governed definition (or one
        # whose td.grains somehow slipped a bad value) — the grain is interpolated
        # UNQUOTED into DATE_TRUNC f-strings, so it must come from the safe set.
        if mq.time_grain not in _TIME_GRAINS:
            raise MetricError(
                "bad_time_grain",
                f"Time grain {mq.time_grain!r} is not a known time grain "
                f"(allowed: {', '.join(sorted(_TIME_GRAINS))}).",
            )
        if mq.time_grain not in td.grains:
            raise MetricError(
                "bad_time_grain",
                f"Time grain {mq.time_grain!r} is not allowed for metric "
                f"{metric.id!r} (allowed: {', '.join(td.grains)}).",
            )
        time_alias = _time_alias(metric, mq.time_grain)

    # the time column is a legal filter field iff a time dimension exists.
    time_col = metric.time_dimension.column if metric.time_dimension else None

    # FIX (Issue 5 resource): cap the total number of filters to prevent a
    # MetricQuery with an unbounded filter list from inflating the WHERE clause /
    # parameter count.
    if len(mq.filters) > _MAX_FILTERS:
        raise MetricError(
            "too_many_filters",
            f"MetricQuery has {len(mq.filters)} filters which exceeds the maximum "
            f"of {_MAX_FILTERS} (set NUBI_MAX_FILTERS to override).",
        )

    # filters: field allowed, op known, list/scalar value shape correct.
    for f in mq.filters:
        if metric.dimension(f.field) is None and f.field != time_col:
            raise MetricError(
                "unknown_filter_field",
                f"Filter field {f.field!r} is neither an allowed dimension nor "
                f"the time column of metric {metric.id!r}.",
            )
        if f.op not in _FILTER_OPS:
            raise MetricError(
                "bad_filter_op",
                f"Filter op {f.op!r} is not a known operator "
                f"(allowed: {', '.join(sorted(_FILTER_OPS))}).",
            )
        if f.op in _LIST_OPS and not isinstance(f.value, (list, tuple)):
            raise MetricError(
                "bad_filter_value",
                f"Filter op {f.op!r} on {f.field!r} requires a list value.",
            )
        # FIX (Issue 4 resource): cap the length of in/not_in list values to
        # prevent parameter explosion (each element becomes a separate bind).
        if f.op in _LIST_OPS and isinstance(f.value, (list, tuple)):
            if len(f.value) > _MAX_IN_LIST:
                raise MetricError(
                    "in_list_too_large",
                    f"Filter op {f.op!r} on {f.field!r} has {len(f.value)} values "
                    f"which exceeds the maximum of {_MAX_IN_LIST} "
                    f"(set NUBI_MAX_IN_LIST to override).",
                )
        if f.op in _SCALAR_OPS and isinstance(f.value, (list, tuple)):
            raise MetricError(
                "bad_filter_value",
                f"Filter op {f.op!r} on {f.field!r} requires a scalar value.",
            )

    # order_by must reference a SELECTED output column.
    selectable: set[str] = set(mq.dimensions)
    if time_alias is not None:
        selectable.add(time_alias)
    for m in metric.measures():
        selectable.add(m.name)
    # Also allow derived measure names in order_by for the layered path.
    for dm in metric.derived_measures:
        selectable.add(dm.name)
    for field, direction in mq.order_by:
        if field not in selectable:
            raise MetricError(
                "bad_order_by",
                f"order_by field {field!r} is not a selected output column "
                f"(selectable: {', '.join(sorted(selectable))}).",
            )

    # ── derived_measures governance ──────────────────────────────────────────
    base_measure_names = set(metric.measure_names())
    for dm in metric.derived_measures:
        _govern_formula(dm, base_measure_names)

    # ── time_comparisons governance ─────────────────────────────────────────
    if mq.time_comparisons:
        # FIX (Issue 3 resource): cap total time_comparisons to prevent
        # O(N_tc × rows) scans from unbounded correlated-subquery lists.
        if len(mq.time_comparisons) > _MAX_TC_ENTRIES:
            raise MetricError(
                "too_many_tc_entries",
                f"time_comparisons has {len(mq.time_comparisons)} entries which "
                f"exceeds the maximum of {_MAX_TC_ENTRIES} "
                f"(set NUBI_MAX_TC_ENTRIES to override).",
            )

        # FIX (MED resource — unbounded __base time-bucket explosion): the
        # layered compiler builds __base as a GROUP BY over (dims × time
        # buckets); time_comparisons then layer window fns / correlated
        # LATERALs over it, and the outer LIMIT only prunes AFTER that full
        # materialisation.  At the FINEST grain ('hour') the number of time
        # buckets is unbounded by anything but the data's time span, so a
        # time-comparison query at hour grain WITHOUT an explicit bound on the
        # time column can materialise O(dim_cardinality × every_hour) rows
        # before the LIMIT lands.  Require a date-range bound on the time column
        # so the bucket count is governed by the operator's request, not the
        # full table.  Only the finest grain is gated — coarser grains
        # (day/week/...) are inherently bounded enough — so this cannot reject
        # the common day/month time-series + comparison queries.
        if mq.time_grain == "hour" and time_col is not None:
            # A "date-range bound" is any bounding/equality op on the time
            # column: <, <=, >, >= (range), = (single bucket), or in (an
            # explicit finite set of buckets).
            _BOUND_OPS = frozenset({"<", "<=", ">", ">=", "=", "in"})
            has_time_bound = any(
                f.field == time_col and f.op in _BOUND_OPS for f in mq.filters
            )
            if not has_time_bound:
                raise MetricError(
                    "time_range_required",
                    f"Time comparisons at the finest grain ('hour') on metric "
                    f"{metric.id!r} require an explicit date-range filter on the "
                    f"time column {time_col!r} (e.g. >=/<= bounds) to bound the "
                    f"number of time buckets materialised in __base.",
                )

        for tc in mq.time_comparisons:
            # FIX 1: validate tc.name (used as a SQL alias via out_name()) is a safe identifier.
            if tc.name is not None and not _IDENT_RE.fullmatch(tc.name):
                raise MetricError(
                    "bad_tc_name",
                    f"time_comparison.name {tc.name!r} is not a valid SQL identifier "
                    f"(must match [A-Za-z_][A-Za-z0-9_]*).",
                )
            # latest_snapshot repurposes .measure as entity col; skip measure check.
            if tc.kind == "latest_snapshot":
                if metric.time_dimension is None:
                    raise MetricError(
                        "snapshot_no_time",
                        "latest_snapshot requires the metric to declare a time_dimension.",
                    )
                # FIX #2: validate entity_col (repurposed .measure) is a safe identifier.
                entity_col = tc.measure
                if not _IDENT_RE.fullmatch(entity_col):
                    raise MetricError(
                        "bad_snapshot_entity",
                        f"latest_snapshot entity column {entity_col!r} is not a valid "
                        f"SQL identifier (must match [A-Za-z_][A-Za-z0-9_]*).",
                    )
                # FIX #2: validate time_col from the metric's time_dimension.
                time_col_val = metric.time_dimension.column
                if not _IDENT_RE.fullmatch(time_col_val):
                    raise MetricError(
                        "bad_snapshot_time_col",
                        f"time_dimension.column {time_col_val!r} is not a valid "
                        f"SQL identifier (must match [A-Za-z_][A-Za-z0-9_]*).",
                    )
                continue
            if tc.measure not in base_measure_names:
                raise MetricError(
                    "unknown_tc_measure",
                    f"time_comparison measure {tc.measure!r} is not a base "
                    f"measure of metric {metric.id!r} "
                    f"(base measures: {', '.join(sorted(base_measure_names))}).",
                )
            if mq.time_grain is None:
                raise MetricError(
                    "tc_requires_grain",
                    "time_comparisons require a time_grain on the MetricQuery.",
                )
            # FIX #6: cap tc.periods to 1..NUBI_MAX_TC_PERIODS.
            if not (1 <= tc.periods <= _MAX_TC_PERIODS):
                raise MetricError(
                    "bad_tc_periods",
                    f"time_comparison.periods {tc.periods!r} is out of the allowed "
                    f"range 1..{_MAX_TC_PERIODS}.",
                )
            # FIX (HIGH correctness — non-additive measures in re-aggregating
            # time-comparison windows): ytd/qtd/mtd/rolling_sum emit
            # ``SUM(<measure>) OVER (...)`` (and rolling_sum/rolling_avg an
            # AGG OVER) that RE-AGGREGATE the already-bucketed __base measure
            # values across buckets.  Re-aggregating a non-additive agg is
            # mathematically WRONG and returns a silent, plausible-but-incorrect
            # number:
            #   * YTD/QTD/MTD/rolling_sum SUM the per-bucket values — but SUM of
            #     per-bucket AVGs is meaningless, and SUM of per-bucket
            #     COUNT(DISTINCT)/percentiles double-counts entities shared
            #     across buckets (a user active in 3 months is counted 3x in a
            #     YTD count_distinct).  Block ALL of _NON_ADDITIVE_AGGS for these.
            #   * rolling_avg AVGs the per-bucket values.  For an AVG measure
            #     this is AVG-of-bucket-AVGs: well-DEFINED but only APPROXIMATE
            #     (it weights every bucket equally, ignoring per-bucket row
            #     counts).  We allow it (documented as approximate) rather than
            #     block it.  But COUNT(DISTINCT)/percentile/approx_count_distinct
            #     remain wrong even under AVG (averaging distinct-counts /
            #     percentiles across buckets is not a valid distinct-count /
            #     percentile), so those are still blocked for rolling_avg.
            tc_agg = next(
                (m.agg for m in metric.measures() if m.name == tc.measure), None
            )
            if tc.kind in ("ytd", "qtd", "mtd", "rolling_sum") and (
                tc_agg in _NON_ADDITIVE_AGGS
            ):
                raise MetricError(
                    "bad_tc_non_additive",
                    f"time_comparison.kind {tc.kind!r} re-aggregates measure "
                    f"{tc.measure!r} (agg {tc_agg!r}) across time buckets, but "
                    f"{tc_agg!r} is non-additive: SUM of per-bucket "
                    f"averages/distinct-counts/percentiles is mathematically "
                    f"wrong and would return a silently incorrect number. Use a "
                    f"SUM/COUNT base measure for {tc.kind!r}.",
                )
            if tc.kind == "rolling_avg" and tc_agg in (
                "count_distinct",
                "percentile_cont",
                "approx_count_distinct",
            ):
                raise MetricError(
                    "bad_tc_non_additive",
                    f"time_comparison.kind 'rolling_avg' averages per-bucket "
                    f"values of measure {tc.measure!r} (agg {tc_agg!r}); "
                    f"averaging distinct-counts/percentiles across buckets is "
                    f"not a valid distinct-count/percentile and returns a "
                    f"silently incorrect number. Use a SUM/COUNT/AVG base "
                    f"measure for 'rolling_avg'.",
                )

    # ── top_n governance ────────────────────────────────────────────────────
    if mq.top_n is not None:
        tn = mq.top_n
        if tn.n <= 0:
            raise MetricError("bad_top_n", "top_n.n must be > 0.")
        # FIX #6: cap top_n.n to 1..NUBI_MAX_TOP_N.
        if tn.n > _MAX_TOP_N:
            raise MetricError(
                "bad_top_n",
                f"top_n.n {tn.n!r} exceeds the maximum allowed value of {_MAX_TOP_N}.",
            )
        if tn.dimension not in mq.dimensions:
            raise MetricError(
                "bad_top_n",
                f"top_n.dimension {tn.dimension!r} is not in mq.dimensions.",
            )
        rank_measure = tn.measure or metric.measure.name
        all_measure_names = base_measure_names | {dm.name for dm in metric.derived_measures}
        if rank_measure not in all_measure_names:
            raise MetricError(
                "bad_top_n",
                f"top_n.measure {rank_measure!r} is not a base or derived measure.",
            )
        # FIX 4: derived measure as rank measure with time_grain is invalid —
        # the membership subquery would ORDER BY SUM(<derived>) but the derived column
        # is not present in __base (only base measures are in __base).
        derived_measure_names = {dm.name for dm in metric.derived_measures}
        # FIX (Issue 3): block a derived rank measure whenever the rank uses a
        # __base membership subquery — that is, when a time_grain is set OR when
        # top_n.other is True.  Both the time-grain top-N arm and the Other arm's
        # NOT IN membership query rank by ORDER BY SUM(<rank_measure>) against
        # __base, which only contains BASE measures — a derived rank column is not
        # present there and would produce a runtime SQL (Binder) error.
        #
        # FIX (MED compiler — derived-top-N governance gap): fix-47 added a THIRD
        # membership path.  _apply_top_n / _build_other_select take the correlated
        # __base membership subquery (not the QUALIFY RANK() path) ALSO when
        # ``use_membership`` is True via _has_extra_non_ranked_dims — i.e. a
        # projected dimension exists beyond {ranked dim} ∪ correlation keys, even
        # with time_grain=None and other=False.  In that path the membership ranks
        # by SUM(<rank_measure>) against __base too, so a DERIVED rank measure is
        # likewise absent from __base and would raise a runtime binder 500.
        # Detect that case here using the SAME correlation-key derivation as
        # _membership_correlation_keys: corr_keys = metric.rls_keys when declared,
        # else every projected non-ranked dim; _has_extra = any projected dim that
        # is neither the ranked dim nor a corr_key.
        corr_keys_govern: tuple[str, ...] = (
            tuple(metric.rls_keys)
            if metric.rls_keys
            else tuple(d for d in mq.dimensions if d != tn.dimension)
        )
        _corr_set = set(corr_keys_govern)
        has_extra_non_ranked = any(
            d != tn.dimension and d not in _corr_set for d in mq.dimensions
        )
        if (
            (mq.time_grain is not None or tn.other or has_extra_non_ranked)
            and rank_measure in derived_measure_names
        ):
            raise MetricError(
                "bad_top_n",
                f"top_n.measure {rank_measure!r} is a derived measure; derived measures "
                f"cannot be used as the top_n rank measure when time_grain is set, "
                f"top_n.other is True, or an extra non-ranked dimension is projected "
                f"(the membership subquery references __base which only contains base "
                f"measures).",
            )
        # FIX issue 2: non-additive base measure as rank measure with time_grain is
        # wrong — the membership subquery ranks by SUM(rank_measure) across time
        # buckets, but SUM(avg(...)) / SUM(count_distinct(...)) / etc. is not the
        # correct total.  Raise MetricError to prevent silent wrong results.
        if mq.time_grain is not None and rank_measure in base_measure_names:
            # Find the agg of the rank measure.
            rank_agg = next(
                (m.agg for m in metric.measures() if m.name == rank_measure), None
            )
            if rank_agg in _NON_ADDITIVE_AGGS:
                raise MetricError(
                    "bad_top_n",
                    f"top_n.measure {rank_measure!r} uses aggregation {rank_agg!r} which "
                    f"is non-additive; it cannot be used as the rank measure when "
                    f"time_grain is set (the membership subquery ranks by "
                    f"SUM(rank_measure) which is incorrect for non-additive aggs).",
                )
        # FIX #1: validate other_label contains no single-quote or backslash.
        if tn.other and ("'" in tn.other_label or "\\" in tn.other_label):
            raise MetricError(
                "bad_other_label",
                f"top_n.other_label must not contain single-quotes or backslashes.",
            )

    # FIX #6: cap mq.limit to 1..NUBI_MAX_QUERY_LIMIT.
    if mq.limit is not None:
        if not (1 <= mq.limit <= _MAX_QUERY_LIMIT):
            raise MetricError(
                "bad_limit",
                f"limit {mq.limit!r} is out of the allowed range 1..{_MAX_QUERY_LIMIT}.",
            )
        # FIX audit-14 MED: when top_n is set and the caller supplies an explicit
        # limit < top_n.n, the QUALIFY/WHERE-IN admits n rows but the outer LIMIT
        # silently drops members ranked (limit+1)..n from BOTH UNION arms.  Surface
        # the conflict explicitly so the caller can correct the query.
        if mq.top_n is not None and mq.limit < mq.top_n.n:
            raise MetricError(
                "bad_limit",
                f"limit {mq.limit!r} is less than top_n.n {mq.top_n.n!r}; "
                f"this would silently drop members ranked (limit+1)..n from the "
                f"top-N result.  Set limit >= top_n.n or leave limit unset.",
            )

    return time_alias


def _govern_formula(dm: DerivedMeasure, base_names: set[str]) -> None:
    """Validate a derived measure formula; raise MetricError on violation."""
    formula = dm.formula.strip()
    if not formula:
        raise MetricError(
            "empty_formula",
            f"DerivedMeasure {dm.name!r} has an empty formula.",
        )
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(formula).readline))
    except tokenize.TokenError as e:
        raise MetricError(
            "bad_formula",
            f"DerivedMeasure {dm.name!r} formula parse error: {e}",
        ) from e

    # Binary operators that cannot legally appear as the first or last meaningful
    # token in a formula (unary minus/plus as a leading token is unusual here and
    # is also rejected — the formula language is arithmetic over named measures).
    _BINARY_OPS = frozenset({"+", "-", "*", "/"})

    _meaningful_tokens: list[tuple[int, str]] = []

    for tok in tokens:
        if tok.type == tokenize.ERRORTOKEN:
            raise MetricError(
                "bad_formula",
                f"DerivedMeasure {dm.name!r} formula contains illegal token "
                f"{tok.string!r}.",
            )
        if tok.type in (tokenize.NEWLINE, tokenize.NL, tokenize.COMMENT,
                        tokenize.ENCODING, tokenize.ENDMARKER):
            continue
        if tok.type == tokenize.OP:
            if tok.string not in _FORMULA_ALLOWED_OPS:
                raise MetricError(
                    "bad_formula",
                    f"DerivedMeasure {dm.name!r} formula contains disallowed "
                    f"operator {tok.string!r} (allowed: + - * / ( )).",
                )
        elif tok.type == tokenize.NAME:
            if tok.string not in base_names:
                raise MetricError(
                    "bad_formula_identifier",
                    f"DerivedMeasure {dm.name!r} formula references "
                    f"{tok.string!r} which is not a declared base measure "
                    f"(known: {', '.join(sorted(base_names))}).",
                )
        elif tok.type == tokenize.NUMBER:
            pass  # numeric literals are fine
        else:
            raise MetricError(
                "bad_formula",
                f"DerivedMeasure {dm.name!r} formula contains unexpected token "
                f"type {tokenize.tok_name[tok.type]!r}: {tok.string!r}.",
            )
        _meaningful_tokens.append((tok.type, tok.string))

    # Syntactic completeness check: a formula that ends on a binary operator
    # (e.g. "profit /") would pass the per-token type checks above but then fail
    # inside sqlglot.parse_one with an unhandled exception -> 500.  Catch it here
    # and return a governed 400 instead.
    if _meaningful_tokens:
        last_type, last_string = _meaningful_tokens[-1]
        if last_type == tokenize.OP and last_string in _BINARY_OPS:
            raise MetricError(
                "bad_formula",
                f"DerivedMeasure {dm.name!r} formula ends with an operator "
                f"{last_string!r}; incomplete expression.",
            )
        # Leading binary operator check: a formula starting with a binary op
        # (e.g. "/ profit") is also syntactically invalid.
        first_type, first_string = _meaningful_tokens[0]
        if first_type == tokenize.OP and first_string in _BINARY_OPS:
            raise MetricError(
                "bad_formula",
                f"DerivedMeasure {dm.name!r} formula starts with an operator "
                f"{first_string!r}; incomplete expression.",
            )


# ---------------------------------------------------------------------------
# SQL building helpers
# ---------------------------------------------------------------------------


def _parse_expr(expr_sql: str, dialect: str) -> exp.Expression:
    """Parse a trusted SQL expression fragment into an AST node."""
    return sqlglot.parse_one(expr_sql, dialect=dialect)


def _date_trunc(grain: str, column: str, dialect: str) -> exp.Expression:
    """Build ``DATE_TRUNC('<grain>', <column>)`` as an AST node."""
    return exp.func(
        "DATE_TRUNC",
        exp.Literal.string(grain),
        _parse_expr(column, dialect),
        dialect=dialect,
    )


def _percentile_p(measure: Any) -> float:
    """Extract percentile fraction from measure.format (e.g. 'p50' → 0.5)."""
    fmt = (measure.format or "p50").lower().lstrip("p")
    try:
        val = float(fmt)
        # If > 1, treat as whole number (p95 → 95 → 0.95).
        return val / 100.0 if val > 1 else val
    except ValueError:
        return 0.5


def _measure_expr(measure: Any, dialect: str) -> exp.Expression:
    """Build ``<AGG>(<expr>) AS <name>`` for a measure.

    ``count`` with expr ``"*"`` emits ``COUNT(*)``; ``count_distinct`` emits
    ``COUNT(DISTINCT <expr>)``; ``percentile_cont`` emits
    ``PERCENTILE_CONT(<p>) WITHIN GROUP (ORDER BY <expr>)``;
    ``approx_count_distinct`` emits ``APPROX_COUNT_DISTINCT(<expr>)``.
    """
    agg = measure.agg
    sql_func = _AGG_SQL.get(agg)
    if sql_func is None:
        raise MetricError(
            "bad_agg",
            f"Measure {measure.name!r} uses unknown aggregation {agg!r}.",
        )

    if agg == "count" and (measure.expr or "*") == "*":
        inner: exp.Expression = exp.Count(this=exp.Star())
    elif agg == "count_distinct":
        arg = _parse_expr(measure.expr, dialect)
        inner = exp.Count(this=exp.Distinct(expressions=[arg]))
    elif agg == "percentile_cont":
        p = _percentile_p(measure)
        # PERCENTILE_CONT(p) WITHIN GROUP (ORDER BY col) — DuckDB also supports
        # quantile_cont(col, p) but PERCENTILE_CONT is standard SQL.
        # CONSISTENCY FIX: parse measure.expr into an AST FIRST, then build the
        # ORDER BY from the PARSED arg (re-serialized) — never f-string the raw
        # measure.expr.  This matches every other aggregate (which uses the
        # parsed AST) so raw user text (e.g. a subquery or trailing SQL) can
        # never survive into the WITHIN GROUP (ORDER BY ...) clause.
        arg = _parse_expr(measure.expr, dialect)
        # Fail-closed: an ORDER BY argument must be a scalar column/expression,
        # not a subquery — reject any embedded SELECT.
        if arg.find(exp.Select) is not None:
            raise MetricError(
                "bad_percentile_expr",
                f"Measure {measure.name!r} percentile_cont expr must be a scalar "
                f"column/expression, not a subquery.",
            )
        arg_sql = arg.sql(dialect=dialect)
        raw = (
            f"PERCENTILE_CONT({p}) WITHIN GROUP (ORDER BY {arg_sql})"
        )
        inner = sqlglot.parse_one(raw, dialect=dialect)
        return exp.alias_(inner, measure.name)
    elif agg == "approx_count_distinct":
        arg = _parse_expr(measure.expr, dialect)
        inner = exp.Anonymous(this="APPROX_COUNT_DISTINCT", expressions=[arg])
    else:
        arg = _parse_expr(measure.expr, dialect)
        # exp.func builds the named aggregate (SUM/MIN/MAX/AVG/COUNT).
        inner = exp.func(sql_func, arg, dialect=dialect)

    return exp.alias_(inner, measure.name)


def _source_expr(metric: MetricDefinition, dialect: str) -> exp.Expression:
    """FROM target: a table ref, or ``(base_sql) AS base`` derived table."""
    if metric.base_table:
        return _parse_expr(metric.base_table, dialect)
    # base_sql guaranteed present by _govern; wrap as a derived table.
    subquery = sqlglot.parse_one(metric.base_sql, dialect=dialect)
    return exp.Subquery(this=subquery, alias=exp.TableAlias(this=exp.to_identifier("base")))


def _field_expr(
    metric: MetricDefinition, field: str, dialect: str
) -> exp.Expression:
    """SQL expression for a filter field: dim.sql_expr() or the raw time column."""
    dim = metric.dimension(field)
    if dim is not None:
        return _parse_expr(dim.sql_expr(), dialect)
    # else it's the time column (validated in _govern).
    return _parse_expr(field, dialect)


# Scalar FilterOp → sqlglot binary-comparison constructor.
_OP_NODE: dict[str, Any] = {
    "=": exp.EQ,
    "!=": exp.NEQ,
    "<": exp.LT,
    "<=": exp.LTE,
    ">": exp.GT,
    ">=": exp.GTE,
}


def _build_where(
    metric: MetricDefinition,
    mq: MetricQuery,
    params: dict[str, Any],
    subs: dict[str, str],
    dialect: str,
) -> exp.Expression | None:
    """AND of author default_filters (verbatim) + user filters (sentinels).

    User filter VALUES are never placed on the AST.  Each user filter's RHS is a
    sentinel column ``__P_fN__`` whose mapping in *subs* names the ``{{name}}``
    placeholder the rendered SQL must carry.  *params* gets ``name -> value``.
    """
    conditions: list[exp.Expression] = []

    # (a) author-trusted default filters — parsed verbatim, AND-ed in.
    for frag in metric.default_filters:
        conditions.append(sqlglot.condition(frag, dialect=dialect))

    # (b) user filters — sentinel RHS, substituted to {{name}} post-render.
    for i, f in enumerate(mq.filters):
        field_node = _field_expr(metric, f.field, dialect)
        pname = f"f{i}"
        params[pname] = f.value
        sentinel = f"__P_{pname}__"

        if f.op in _LIST_OPS:
            cond: exp.Expression = exp.In(
                this=field_node, expressions=[exp.column(sentinel)]
            )
            if f.op == "not_in":
                cond = exp.Not(this=exp.paren(cond))
            subs[f"({sentinel})"] = f"{{{{ {pname} | inclause }}}}"
        else:
            node_cls = _OP_NODE[f.op]
            cond = node_cls(this=field_node, expression=exp.column(sentinel))
            subs[sentinel] = f"{{{{{pname}}}}}"

        conditions.append(cond)

    if not conditions:
        return None

    combined = conditions[0]
    for cond in conditions[1:]:
        combined = exp.and_(combined, cond)
    return combined


# ---------------------------------------------------------------------------
# Window function builders
# ---------------------------------------------------------------------------


def _window_lag(
    col: exp.Expression,
    periods: int,
    partition_cols: list[exp.Expression],
    order_col: exp.Expression | None,
) -> exp.Expression:
    """Build LAG(<col>, <periods>) OVER (PARTITION BY ... ORDER BY ...)."""
    lag_fn = exp.Anonymous(
        this="LAG",
        expressions=[col.copy(), exp.Literal.number(periods)],
    )
    return exp.Window(
        this=lag_fn,
        partition_by=partition_cols,
        order=exp.Order(expressions=[exp.Ordered(this=order_col.copy())]) if order_col else None,
    )


# Time-interval unit per grain for the RANGE-based rolling-window frame.
# A rolling window over a sparse time series must span CALENDAR time, not a
# physical row count, so we emit ``RANGE BETWEEN INTERVAL 'N <unit>' PRECEDING``
# ordered by the time bucket.  The unit is the bucket grain itself, so a rolling
# window of N buckets covers N grain-units of calendar time regardless of gaps.
_ROLLING_INTERVAL_UNIT: dict[str, str] = {
    "hour": "hour",
    "day": "day",
    "week": "week",
    "month": "month",
    "quarter": "quarter",
    "year": "year",
}


# Interval subtraction SQL per grain for the date-correct prior-year lookup.
# These produce the date exactly one year/period earlier, regardless of row
# density (unlike positional LAG which shifts on sparse series).
_PRIOR_YEAR_INTERVAL: dict[str, str] = {
    "month": "INTERVAL '1 year'",
    "quarter": "INTERVAL '1 year'",
    "year": "INTERVAL '1 year'",
    # 52 weeks = 364 days, which is calendar-incorrect for the same weekday one
    # year back across a leap year boundary.  INTERVAL '1 year' in DuckDB/PG is
    # calendar-correct (subtracts 365 or 366 days as needed), so use it here.
    "week": "INTERVAL '1 year'",
    # '365 days' is wrong in leap years (2024-03-01 - 365 days = 2023-03-02).
    # INTERVAL '1 year' is calendar-correct: DuckDB/PG subtract exactly one
    # calendar year, handling leap-year boundary dates correctly.
    "day": "INTERVAL '1 year'",
    # '8760 hours' = 365 * 24 hours — wrong in leap years for the same reason.
    "hour": "INTERVAL '1 year'",
}


def _prior_year_subquery_sql(
    measure_name: str,
    grain: str,
    time_alias: str | None,
    all_dim_names: list[str],
    dialect: str,
) -> str:
    """Build a correlated scalar subquery that returns the prior-year value of
    *measure_name* for the current row's dimension combination and date bucket.

    Unlike positional LAG(measure, N), this is correct for sparse time series
    because it matches by DATE, not by row offset.

    The outer SELECT uses ``FROM __base AS __outer`` so the outer row's columns
    are unambiguously qualified as ``__outer.<col>`` — without this, DuckDB
    can mis-bind column references to the inner ``__base AS __py`` alias when
    both sides use the same CTE.

    Emits:
        (SELECT __py.{measure} FROM __base AS __py
         WHERE __py.{dim1} = __outer.{dim1} [AND ...]
         AND __py.{time_alias} = DATE_TRUNC('{grain}',
                                            __outer.{time_alias} - {interval}))

    When there is no time_alias (no grain) or no dims to join on, we degrade
    gracefully by omitting the respective WHERE clause components.
    """
    interval = _PRIOR_YEAR_INTERVAL.get(grain, "INTERVAL '1 year'")

    # FIX (Issue 1 defense-in-depth): emit every identifier (dims, time_alias,
    # measure) as a quoted identifier so a name that slipped past validation
    # cannot break out of the f-string.  Governance already validates these, but
    # quoting here is a second line of defense.
    def _q(name: str) -> str:
        return exp.to_identifier(name, quoted=True).sql(dialect=dialect)

    measure_q = _q(measure_name)

    # Qualify outer-row column references with __outer. to avoid ambiguity.
    dim_conditions = " AND ".join(
        f"__py.{_q(d)} = __outer.{_q(d)}" for d in all_dim_names
    )

    if time_alias is not None:
        time_alias_q = _q(time_alias)
        time_condition = (
            f"__py.{time_alias_q} = "
            f"DATE_TRUNC('{grain}', __outer.{time_alias_q} - {interval})"
        )
        if dim_conditions:
            where_clause = f"WHERE {dim_conditions} AND {time_condition}"
        else:
            where_clause = f"WHERE {time_condition}"
    else:
        if dim_conditions:
            where_clause = f"WHERE {dim_conditions}"
        else:
            where_clause = ""

    return (
        f"(SELECT __py.{measure_q} FROM __base AS __py {where_clause})"
    )


# Interval subtraction SQL per grain for the date-correct prior-period lookup.
# For a single period back: month->1 month, quarter->1 quarter, etc.
# This is used for prior_period / pop_abs / pop_pct to avoid positional-LAG
# errors on sparse time series.
_PRIOR_PERIOD_INTERVAL_BY_N: dict[str, str] = {
    "month": "INTERVAL '{n} month'",
    "quarter": "INTERVAL '{n} quarter'",
    "year": "INTERVAL '{n} year'",
    "week": "INTERVAL '{n} week'",
    "day": "INTERVAL '{n} day'",
    "hour": "INTERVAL '{n} hour'",
}


def _prior_period_subquery_sql(
    measure_name: str,
    grain: str,
    periods: int,
    time_alias: str | None,
    all_dim_names: list[str],
    dialect: str,
) -> str:
    """Build a date-correct correlated scalar subquery for prior-period lookup.

    This returns the value of *measure_name* N periods prior to the current
    row's time bucket.  Unlike positional LAG(measure, N), this is correct for
    sparse time series because it matches by DATE arithmetic, not row offset.

    Emits (analogous to _prior_year_subquery_sql but with per-grain intervals):
        (SELECT __pp.{measure} FROM __base AS __pp
         WHERE __pp.{dim1} = __outer.{dim1} [AND ...]
         AND __pp.{time_alias} = DATE_TRUNC('{grain}',
                                            __outer.{time_alias} - <interval>))
    """
    interval_tmpl = _PRIOR_PERIOD_INTERVAL_BY_N.get(grain, "INTERVAL '{n} day'")
    interval = interval_tmpl.replace("{n}", str(periods))

    # FIX (Issue 1 defense-in-depth): quote every interpolated identifier.
    def _q(name: str) -> str:
        return exp.to_identifier(name, quoted=True).sql(dialect=dialect)

    measure_q = _q(measure_name)

    dim_conditions = " AND ".join(
        f"__pp.{_q(d)} = __outer.{_q(d)}" for d in all_dim_names
    )

    if time_alias is not None:
        time_alias_q = _q(time_alias)
        time_condition = (
            f"__pp.{time_alias_q} = "
            f"DATE_TRUNC('{grain}', __outer.{time_alias_q} - {interval})"
        )
        if dim_conditions:
            where_clause = f"WHERE {dim_conditions} AND {time_condition}"
        else:
            where_clause = f"WHERE {time_condition}"
    else:
        if dim_conditions:
            where_clause = f"WHERE {dim_conditions}"
        else:
            where_clause = ""

    return (
        f"(SELECT __pp.{measure_q} FROM __base AS __pp {where_clause})"
    )
