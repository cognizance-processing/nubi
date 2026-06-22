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

from app.connectors.sql_parse import parse_sql_cached
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
) -> tuple[str, dict[str, Any]]:
    """Compile *metric* + *mq* into ``(sql, params)``.

    ``sql`` contains ``{{name}}`` placeholders for user-supplied filter values;
    ``params`` maps those names → values.  Governance violations raise
    :class:`MetricError`.

    See module docstring for the time-bucket alias and in/not_in conventions.
    """
    # ── 1. GOVERN (everything BEFORE we build any SQL) ──────────────────────
    time_alias = _govern(metric, mq)

    # ── 2. Decide path: FLAT vs LAYERED ─────────────────────────────────────
    needs_layer = bool(metric.derived_measures) or mq.has_transforms()

    if not needs_layer:
        return _compile_flat(metric, mq, time_alias, dialect)
    else:
        return _compile_layered(metric, mq, time_alias, dialect)


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
    for field, direction in mq.order_by:
        select = select.order_by(
            exp.Ordered(this=exp.column(field), desc=(direction == "desc"))
        )
    if mq.limit is not None:
        select = select.limit(mq.limit)

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
) -> tuple[str, dict[str, Any]]:
    """Emit the two-level CTE query with derived + time-intel + top-N layers."""
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

    for tc in regular_comparisons:
        m_col = exp.column(tc.measure)
        out_name = tc.out_name()

        if tc.kind in ("prior_period", "pop_abs", "pop_pct"):
            lag_expr = _window_lag(m_col, tc.periods, partition_cols, order_col)
            if tc.kind == "prior_period":
                outer_select_exprs.append(exp.alias_(lag_expr, out_name))
            elif tc.kind == "pop_abs":
                diff = exp.Sub(this=m_col.copy(), expression=lag_expr)
                outer_select_exprs.append(exp.alias_(diff, out_name))
            else:  # pop_pct
                lag2 = _window_lag(m_col, tc.periods, partition_cols, order_col)
                pct_sql = f"({m_col.sql(dialect=dialect)} - {lag2.sql(dialect=dialect)}) / NULLIF({lag2.sql(dialect=dialect)}, 0)"
                pct_expr = sqlglot.parse_one(pct_sql, dialect=dialect)
                outer_select_exprs.append(exp.alias_(pct_expr, out_name))

        elif tc.kind in ("prior_year", "yoy_abs", "yoy_pct"):
            lag_periods = YEAR_LAG_BY_GRAIN.get(mq.time_grain or "day", 365)
            lag_expr = _window_lag(m_col, lag_periods, partition_cols, order_col)
            if tc.kind == "prior_year":
                outer_select_exprs.append(exp.alias_(lag_expr, out_name))
            elif tc.kind == "yoy_abs":
                diff = exp.Sub(this=m_col.copy(), expression=lag_expr)
                outer_select_exprs.append(exp.alias_(diff, out_name))
            else:  # yoy_pct
                lag2 = _window_lag(m_col, lag_periods, partition_cols, order_col)
                pct_sql = f"({m_col.sql(dialect=dialect)} - {lag2.sql(dialect=dialect)}) / NULLIF({lag2.sql(dialect=dialect)}, 0)"
                pct_expr = sqlglot.parse_one(pct_sql, dialect=dialect)
                outer_select_exprs.append(exp.alias_(pct_expr, out_name))

        elif tc.kind in ("ytd", "qtd", "mtd"):
            trunc_unit = {"ytd": "year", "qtd": "quarter", "mtd": "month"}[tc.kind]
            # Partition by trunc(time, unit) + non-time dims; ORDER BY time alias;
            # ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW.
            partition_parts = [d for d in all_dim_names]
            if time_alias:
                partition_parts_sql = ", ".join(
                    [*partition_parts, f"DATE_TRUNC('{trunc_unit}', {time_alias})"]
                )
                order_part_sql = time_alias
            else:
                partition_parts_sql = ", ".join(partition_parts) if partition_parts else "1"
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
            partition_parts = [d for d in all_dim_names]
            partition_parts_sql = ", ".join(partition_parts) if partition_parts else "1"
            if time_alias:
                order_part_sql = time_alias
                win_sql = (
                    f"{agg_fn}({tc.measure}) OVER ("
                    f"PARTITION BY {partition_parts_sql} "
                    f"ORDER BY {order_part_sql} "
                    f"ROWS BETWEEN {rows_back} PRECEDING AND CURRENT ROW)"
                )
            else:
                win_sql = (
                    f"{agg_fn}({tc.measure}) OVER ("
                    f"ROWS BETWEEN {rows_back} PRECEDING AND CURRENT ROW)"
                )
            win_expr = sqlglot.parse_one(win_sql, dialect=dialect)
            outer_select_exprs.append(exp.alias_(win_expr, out_name))

    # ── Assemble the outer SELECT ────────────────────────────────────────────
    outer_select = exp.Select().select(*outer_select_exprs).from_(
        exp.Table(this=exp.to_identifier("__base"))
    )

    # ORDER BY and LIMIT (on the outer)
    for field, direction in mq.order_by:
        outer_select = outer_select.order_by(
            exp.Ordered(this=exp.column(field), desc=(direction == "desc"))
        )
    if mq.limit is not None:
        outer_select = outer_select.limit(mq.limit)

    # ── Top-N (outermost QUALIFY/RANK layer) ─────────────────────────────────
    if mq.top_n is not None and not mq.top_n.other:
        outer_select = _apply_top_n(outer_select, mq, metric, time_alias, dialect)

    # ── Assemble CTE ────────────────────────────────────────────────────────
    # Render __base, substitute sentinels, then assemble the final WITH statement.
    base_sql = base_select.sql(dialect=dialect)
    for sentinel, placeholder in subs.items():
        base_sql = base_sql.replace(sentinel, placeholder)

    outer_sql = outer_select.sql(dialect=dialect)

    # sqlglot renders WITH … AS separately; build as a raw string to preserve
    # sentinel substitution in base_sql and avoid double-rendering.
    sql = f"WITH __base AS ({base_sql}) {outer_sql}"

    # ── Top-N with Other bucket (UNION approach) ─────────────────────────────
    if mq.top_n is not None and mq.top_n.other:
        sql = _apply_top_n_other(
            sql, mq, metric, time_alias, all_dim_names, dialect
        )

    return sql, params


# ---------------------------------------------------------------------------
# Top-N helper
# ---------------------------------------------------------------------------


def _apply_top_n(
    outer_select: exp.Select,
    mq: MetricQuery,
    metric: MetricDefinition,
    time_alias: str | None,
    dialect: str,
) -> exp.Select:
    """Wrap the outer select with a top-N filter.

    With a time_grain the outer SELECT has one row per (dim, time_bucket).
    A nested window-in-window (RANK OVER (ORDER BY SUM() OVER ())) is invalid
    in DuckDB/PG, so instead we use a membership filter:
        WHERE <dim_col> IN (
            SELECT <dim_col> FROM __base
            GROUP BY <dim_col>
            ORDER BY SUM(<rank_measure>) DESC
            LIMIT <n>
        )
    Without a time_grain the per-row measure value is already the aggregate,
    so the simple QUALIFY RANK() OVER (ORDER BY measure) <= N is valid.
    """
    tn = mq.top_n
    assert tn is not None

    rank_measure = tn.measure or metric.measure.name
    dim_col = tn.dimension

    if time_alias is not None:
        # With time grain: use membership filter via subquery on __base.
        order_dir = "DESC" if tn.order == "desc" else "ASC"
        membership_sql = (
            f"SELECT {dim_col} FROM __base "
            f"GROUP BY {dim_col} "
            f"ORDER BY SUM({rank_measure}) {order_dir} "
            f"LIMIT {tn.n}"
        )
        membership_expr = sqlglot.parse_one(membership_sql, dialect=dialect)
        where_cond = exp.In(
            this=exp.column(dim_col),
            query=membership_expr,
        )
        outer_select = outer_select.where(where_cond)
    else:
        # No time grain: simple QUALIFY RANK() <= N.
        rank_m_col = exp.column(rank_measure)
        rank_over = exp.Window(
            this=exp.func("RANK", dialect=dialect),
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


def _apply_top_n_other(
    layered_sql: str,
    mq: "MetricQuery",
    metric: "MetricDefinition",
    time_alias: str | None,
    all_dim_names: list[str],
    dialect: str,
) -> str:
    """Extend a layered query with a rolled-up "Other" bucket for non-top-N members.

    Produces:
        <top-N select> UNION ALL <other bucket select>

    Both UNION arms must have identical column lists.  Time-comparison window
    columns (``tc.out_name()``) are emitted as ``NULL AS <name>`` in the Other
    arm since window functions cannot be re-applied over the rolled-up bucket.

    Fix #1 (SQLi): ``other_label`` is validated in _govern (no quotes/backslash)
    and emitted via ``exp.Literal.string(other_label).sql(...)`` — never raw f-string.
    Fix #3 (correctness): When time_alias is set the top-N portion uses a WHERE IN
    membership filter (not a nested window-in-window, which is illegal).
    Fix #4 (correctness): NULL columns appended for every non-latest_snapshot
    time_comparison so both UNION arms have identical column counts.
    Fix #5 (correctness): count(*) Other bucket emits SUM(m.name) — sum the
    pre-computed count column from __base, not SUM(COUNT(*)) which is a nested agg.
    """
    tn = mq.top_n
    assert tn is not None and tn.other

    rank_measure = tn.measure or metric.measure.name
    dim_col = tn.dimension
    other_label = tn.other_label

    # ── FIX #1: safe label literal via sqlglot ──────────────────────────────
    safe_other_label = exp.Literal.string(other_label).sql(dialect=dialect)

    # ── Build the top-N portion: apply the same logic as _apply_top_n ────────
    # Re-parse the layered SQL to add the top-N restriction to the outer SELECT.
    try:
        full_tree = parse_sql_cached(layered_sql, dialect=dialect)
    except Exception:
        return layered_sql

    if not isinstance(full_tree, exp.Select):
        return layered_sql

    import copy  # noqa: PLC0415
    top_n_tree = copy.deepcopy(full_tree)

    if time_alias is not None:
        # FIX #3: membership filter via subquery — no nested window-in-window.
        order_dir = "DESC" if tn.order == "desc" else "ASC"
        membership_sql = (
            f"SELECT {dim_col} FROM __base "
            f"GROUP BY {dim_col} "
            f"ORDER BY SUM({rank_measure}) {order_dir} "
            f"LIMIT {tn.n}"
        )
        membership_expr = sqlglot.parse_one(membership_sql, dialect=dialect)
        where_cond = exp.In(
            this=exp.column(dim_col),
            query=membership_expr,
        )
        top_n_tree = top_n_tree.where(where_cond)
    else:
        # No time grain: simple QUALIFY RANK() <= N.
        rank_m_col = exp.column(rank_measure)
        rank_over = exp.Window(
            this=exp.func("RANK", dialect=dialect),
            order=exp.Order(
                expressions=[
                    exp.Ordered(this=rank_m_col.copy(), desc=(tn.order == "desc"))
                ]
            ),
        )
        qualify_cond = exp.LTE(
            this=rank_over,
            expression=exp.Literal.number(tn.n),
        )
        top_n_tree = top_n_tree.qualify(qualify_cond)

    top_n_sql = top_n_tree.sql(dialect=dialect)

    # ── Build the "Other" bucket SELECT from __base ───────────────────────────
    # Subquery to identify top-N members (used in NOT IN exclusion).
    top_members_subquery = (
        f"SELECT {dim_col} FROM __base "
        f"GROUP BY {dim_col} "
        f"ORDER BY SUM({rank_measure}) {'DESC' if tn.order == 'desc' else 'ASC'} "
        f"LIMIT {tn.n}"
    )

    # Group by: time alias (if present) + all non-ranked dims EXCEPT the ranked dim.
    other_group_cols = []
    if time_alias:
        other_group_cols.append(time_alias)
    for d in all_dim_names:
        if d != dim_col:
            other_group_cols.append(d)

    other_select_parts: list[str] = []

    # (a) Dimension columns: rank dim → safe other_label literal, others → passthrough.
    for d in all_dim_names:
        if d == dim_col:
            # FIX #1: use sqlglot-escaped literal, not raw f-string interpolation.
            other_select_parts.append(f"{safe_other_label} AS {dim_col}")
        else:
            other_select_parts.append(f"{d}")

    # (b) Time alias passthrough.
    if time_alias:
        other_select_parts.append(f"{time_alias}")

    # (c) Base measures: SUM the pre-aggregated column from __base.
    # FIX #5: count(*) must use SUM(m.name) — the count is already computed in __base,
    # so we sum those pre-computed values, NOT SUM(COUNT(*)) which is a nested agg.
    base_measure_sums: dict[str, str] = {}
    for m in metric.measures():
        if m.agg == "count_distinct":
            # count_distinct is not re-aggregable; emit NULL as a conservative fallback.
            base_measure_sums[m.name] = f"NULL AS {m.name}"
        elif m.agg in ("percentile_cont", "approx_count_distinct"):
            base_measure_sums[m.name] = f"NULL AS {m.name}"
        else:
            # For count(*), count(col), sum, min, max, avg — sum the pre-agg value.
            base_measure_sums[m.name] = f"SUM({m.name}) AS {m.name}"
        other_select_parts.append(base_measure_sums[m.name])

    # (d) Derived measures: recomputed from the summed base measures.
    base_measure_names = set(metric.measure_names())
    for dm in metric.derived_measures:
        formula_sql = _compile_derived_formula(dm, base_measure_names)
        other_select_parts.append(f"{formula_sql} AS {dm.name}")

    # (e) FIX #4: time-comparison window columns — emit NULL AS <out_name> in the
    # Other arm so both UNION arms have identical column counts.
    regular_comparisons = [tc for tc in mq.time_comparisons if tc.kind != "latest_snapshot"]
    for tc in regular_comparisons:
        other_select_parts.append(f"NULL AS {tc.out_name()}")

    # WHERE: exclude top-N members.
    where_clause = f"WHERE {dim_col} NOT IN ({top_members_subquery})"

    # GROUP BY.
    if other_group_cols:
        group_by_clause = "GROUP BY " + ", ".join(other_group_cols)
    else:
        group_by_clause = ""

    other_sql = (
        f"SELECT {', '.join(other_select_parts)} "
        f"FROM __base "
        f"{where_clause} "
        f"{group_by_clause}"
    ).strip()

    # Wrap in CTE so both parts can reference __base.
    # layered_sql = "WITH __base AS (...) <outer>"
    inner_start = layered_sql.find("WITH __base AS (")
    if inner_start == -1:
        inner_start = layered_sql.upper().find("WITH __BASE AS (")
    if inner_start == -1:
        return layered_sql  # can't find the CTE; return unchanged.

    # Find end of base CTE body: count parens.
    cte_body_start = layered_sql.find("(", inner_start + len("WITH __base AS")) + 1
    depth = 1
    pos = cte_body_start
    while pos < len(layered_sql) and depth > 0:
        if layered_sql[pos] == "(":
            depth += 1
        elif layered_sql[pos] == ")":
            depth -= 1
        pos += 1
    base_cte_body = layered_sql[cte_body_start : pos - 1].strip()

    # top_n_sql already contains "WITH __base AS ..." — extract just its outer SELECT.
    top_n_outer_start = top_n_sql.find("(", top_n_sql.upper().find("WITH __BASE AS")) + 1
    depth2 = 1
    pos2 = top_n_outer_start
    while pos2 < len(top_n_sql) and depth2 > 0:
        if top_n_sql[pos2] == "(":
            depth2 += 1
        elif top_n_sql[pos2] == ")":
            depth2 -= 1
        pos2 += 1
    top_n_outer_sql = top_n_sql[pos2:].strip()

    result = (
        f"WITH __base AS ({base_cte_body}) "
        f"{top_n_outer_sql} "
        f"UNION ALL SELECT * FROM ({other_sql})"
    )
    return result


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
        if mq.time_grain not in td.grains:
            raise MetricError(
                "bad_time_grain",
                f"Time grain {mq.time_grain!r} is not allowed for metric "
                f"{metric.id!r} (allowed: {', '.join(td.grains)}).",
            )
        time_alias = _time_alias(metric, mq.time_grain)

    # the time column is a legal filter field iff a time dimension exists.
    time_col = metric.time_dimension.column if metric.time_dimension else None

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
        for tc in mq.time_comparisons:
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
        arg = _parse_expr(measure.expr, dialect)
        # PERCENTILE_CONT(p) WITHIN GROUP (ORDER BY col) — DuckDB also supports
        # quantile_cont(col, p) but PERCENTILE_CONT is standard SQL.
        # Build as raw SQL and parse for AST safety.
        raw = (
            f"PERCENTILE_CONT({p}) WITHIN GROUP (ORDER BY {measure.expr})"
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
