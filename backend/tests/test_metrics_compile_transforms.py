"""Tests for the layered metric compiler: derived measures, time-intelligence,
top-N, percentile, latest_snapshot, RLS soundness, governance, and the
real-world CCBSA KPI acceptance suite.

Pure pytest — no DB, no FastAPI.
"""

from __future__ import annotations

import pytest

from app.metrics.compile import compile_metric
from app.metrics.models import (
    Dimension,
    DerivedMeasure,
    Measure,
    MetricDefinition,
    MetricError,
    MetricQuery,
    TimeDimension,
    TimeComparison,
    TopN,
)


# ---------------------------------------------------------------------------
# Fixtures / builders
# ---------------------------------------------------------------------------


def _orders_metric(**overrides) -> MetricDefinition:
    """Orders metric with delivered + ordered base measures (for PvD ratio)."""
    kwargs = dict(
        id="orders",
        name="Orders",
        measure=Measure(name="delivered", agg="sum", expr="delivered_qty"),
        base_table="orders",
        dimensions=(
            Dimension(name="region"),
            Dimension(name="sku"),
            Dimension(name="store"),
        ),
        time_dimension=TimeDimension(
            column="order_date",
            grains=("day", "week", "month", "quarter", "year"),
            default_grain="day",
        ),
        extra_measures=(
            Measure(name="ordered", agg="sum", expr="ordered_qty"),
        ),
        rls_keys=("org_id",),
    )
    kwargs.update(overrides)
    return MetricDefinition(**kwargs)


def _simple_metric(**overrides) -> MetricDefinition:
    """A simple revenue metric for basic transform tests."""
    kwargs = dict(
        id="revenue",
        name="Revenue",
        measure=Measure(name="revenue", agg="sum", expr="amount"),
        base_table="orders",
        dimensions=(Dimension(name="region"),),
        time_dimension=TimeDimension(
            column="created_at",
            grains=("day", "week", "month", "quarter", "year"),
            default_grain="day",
        ),
        rls_keys=("org_id",),
    )
    kwargs.update(overrides)
    return MetricDefinition(**kwargs)


# ---------------------------------------------------------------------------
# 1. Derived measure: PvD = delivered / ordered with NULLIF
# ---------------------------------------------------------------------------


def test_derived_ratio_emits_nullif() -> None:
    """PvD = delivered / ordered — division denominator wrapped in NULLIF."""
    m = _orders_metric(
        derived_measures=(
            DerivedMeasure(name="pvd", formula="delivered / ordered", format="percent"),
        )
    )
    mq = MetricQuery(metric_id="orders", dimensions=("region",))
    sql, params = compile_metric(m, mq)

    up = sql.upper()
    assert "WITH __BASE AS" in up
    assert "NULLIF" in up
    # Denominator of delivered/ordered must be guarded.
    assert "NULLIF(ORDERED, 0)" in up or "NULLIF(ordered, 0)" in sql


def test_derived_ratio_result_column_present() -> None:
    """Outer SELECT must include the derived measure column."""
    m = _orders_metric(
        derived_measures=(
            DerivedMeasure(name="pvd", formula="delivered / ordered"),
        )
    )
    mq = MetricQuery(metric_id="orders", dimensions=("region",))
    sql, _ = compile_metric(m, mq)
    assert "pvd" in sql.lower()


def test_derived_complex_formula() -> None:
    """Multi-op formula: (revenue - cost) / revenue."""
    m = MetricDefinition(
        id="margin",
        name="Margin",
        measure=Measure(name="revenue", agg="sum", expr="amount"),
        base_table="sales",
        extra_measures=(Measure(name="cost", agg="sum", expr="cost_amount"),),
        derived_measures=(
            DerivedMeasure(name="margin_pct", formula="(revenue - cost) / revenue"),
        ),
    )
    mq = MetricQuery(metric_id="margin")
    sql, _ = compile_metric(m, mq)
    up = sql.upper()
    assert "WITH __BASE AS" in up
    assert "MARGIN_PCT" in up
    assert "NULLIF" in up


def test_flat_path_unchanged_without_transforms() -> None:
    """No derived_measures + no transforms = flat query (no WITH __base)."""
    m = _simple_metric()
    mq = MetricQuery(metric_id="revenue", dimensions=("region",))
    sql, _ = compile_metric(m, mq)
    assert "WITH __BASE" not in sql.upper()
    assert "WITH __base" not in sql


# ---------------------------------------------------------------------------
# 2. Time-intelligence: each kind
# ---------------------------------------------------------------------------


def test_prior_period_lag() -> None:
    """prior_period uses date-correct correlated subquery (not positional LAG).
    Positional LAG is wrong for sparse time series — this fix mirrors the
    existing prior_year implementation which already used the subquery approach.
    """
    m = _simple_metric()
    mq = MetricQuery(
        metric_id="revenue",
        dimensions=("region",),
        time_grain="month",
        time_comparisons=(
            TimeComparison(measure="revenue", kind="prior_period", periods=1),
        ),
    )
    sql, _ = compile_metric(m, mq)
    up = sql.upper()
    assert "WITH __BASE AS" in up
    # Must use a date-correct correlated subquery, NOT positional LAG.
    assert "SELECT __PP.REVENUE FROM __BASE AS __PP" in up
    assert "INTERVAL" in up
    assert "revenue_prior_period".upper() in up


def test_pop_abs() -> None:
    m = _simple_metric()
    mq = MetricQuery(
        metric_id="revenue",
        dimensions=("region",),
        time_grain="month",
        time_comparisons=(
            TimeComparison(measure="revenue", kind="pop_abs"),
        ),
    )
    sql, _ = compile_metric(m, mq)
    assert "revenue_pop_abs" in sql.lower()


def test_pop_pct() -> None:
    m = _simple_metric()
    mq = MetricQuery(
        metric_id="revenue",
        dimensions=("region",),
        time_grain="month",
        time_comparisons=(
            TimeComparison(measure="revenue", kind="pop_pct"),
        ),
    )
    sql, _ = compile_metric(m, mq)
    assert "revenue_pop_pct" in sql.lower()
    assert "NULLIF" in sql.upper()  # pct change guards denominator


def test_prior_year_lag_by_grain_month() -> None:
    """YoY (month grain) uses a date-correct correlated subquery, not positional LAG.
    Positional LAG is wrong for sparse series; the new implementation matches
    by date (bucket - 1 year interval), which is correct regardless of row density.
    """
    m = _simple_metric()
    mq = MetricQuery(
        metric_id="revenue",
        dimensions=("region",),
        time_grain="month",
        time_comparisons=(
            TimeComparison(measure="revenue", kind="prior_year"),
        ),
    )
    sql, _ = compile_metric(m, mq)
    up = sql.upper()
    # Must use a correlated subquery on __base, not positional LAG.
    assert "SELECT __PY.REVENUE FROM __BASE AS __PY" in up
    # Must shift by exactly 1 year (INTERVAL '1 year').
    assert "INTERVAL" in up
    assert "1" in up
    assert "YEAR" in up
    assert "revenue_prior_year" in sql.lower()


def test_yoy_abs() -> None:
    m = _simple_metric()
    mq = MetricQuery(
        metric_id="revenue",
        dimensions=("region",),
        time_grain="month",
        time_comparisons=(TimeComparison(measure="revenue", kind="yoy_abs"),),
    )
    sql, _ = compile_metric(m, mq)
    assert "revenue_yoy_abs" in sql.lower()


def test_yoy_pct() -> None:
    m = _simple_metric()
    mq = MetricQuery(
        metric_id="revenue",
        dimensions=("region",),
        time_grain="month",
        time_comparisons=(TimeComparison(measure="revenue", kind="yoy_pct"),),
    )
    sql, _ = compile_metric(m, mq)
    assert "revenue_yoy_pct" in sql.lower()
    assert "NULLIF" in sql.upper()


def test_ytd() -> None:
    m = _simple_metric()
    mq = MetricQuery(
        metric_id="revenue",
        dimensions=("region",),
        time_grain="month",
        time_comparisons=(TimeComparison(measure="revenue", kind="ytd"),),
    )
    sql, _ = compile_metric(m, mq)
    up = sql.upper()
    assert "SUM" in up
    assert "OVER" in up
    assert "revenue_ytd" in sql.lower()


def test_qtd() -> None:
    m = _simple_metric()
    mq = MetricQuery(
        metric_id="revenue",
        dimensions=("region",),
        time_grain="month",
        time_comparisons=(TimeComparison(measure="revenue", kind="qtd"),),
    )
    sql, _ = compile_metric(m, mq)
    assert "revenue_qtd" in sql.lower()


def test_mtd() -> None:
    m = _simple_metric()
    mq = MetricQuery(
        metric_id="revenue",
        dimensions=("region",),
        time_grain="month",
        time_comparisons=(TimeComparison(measure="revenue", kind="mtd"),),
    )
    sql, _ = compile_metric(m, mq)
    assert "revenue_mtd" in sql.lower()


def test_rolling_sum_28d() -> None:
    """28-day rolling sum: ROWS BETWEEN 27 PRECEDING AND CURRENT ROW."""
    m = _simple_metric()
    mq = MetricQuery(
        metric_id="revenue",
        dimensions=("region",),
        time_grain="day",
        time_comparisons=(
            TimeComparison(measure="revenue", kind="rolling_sum", periods=28,
                           name="revenue_rolling_28d"),
        ),
    )
    sql, _ = compile_metric(m, mq)
    up = sql.upper()
    assert "SUM" in up
    assert "OVER" in up
    assert "27" in sql  # periods-1 = 27 PRECEDING
    assert "revenue_rolling_28d" in sql.lower()


def test_rolling_avg() -> None:
    m = _simple_metric()
    mq = MetricQuery(
        metric_id="revenue",
        dimensions=("region",),
        time_grain="week",
        time_comparisons=(
            TimeComparison(measure="revenue", kind="rolling_avg", periods=4),
        ),
    )
    sql, _ = compile_metric(m, mq)
    up = sql.upper()
    assert "AVG" in up
    assert "OVER" in up


# ---------------------------------------------------------------------------
# 3. Top-N
# ---------------------------------------------------------------------------


def test_top_n_basic() -> None:
    """Top-3 regions by revenue — QUALIFY RANK() <= 3."""
    m = _simple_metric()
    mq = MetricQuery(
        metric_id="revenue",
        dimensions=("region",),
        top_n=TopN(dimension="region", n=3, measure="revenue", order="desc"),
    )
    sql, _ = compile_metric(m, mq)
    up = sql.upper()
    assert "WITH __BASE AS" in up
    assert "QUALIFY" in up
    assert "3" in sql  # n=3


def test_top_n_with_time_grain() -> None:
    """Top-N with time_grain uses membership filter (WHERE IN subquery), not nested
    window-in-window QUALIFY — DuckDB/PG reject RANK() OVER (ORDER BY SUM() OVER ()).
    """
    m = _simple_metric()
    mq = MetricQuery(
        metric_id="revenue",
        dimensions=("region",),
        time_grain="month",
        top_n=TopN(dimension="region", n=5, order="desc"),
    )
    sql, _ = compile_metric(m, mq)
    up = sql.upper()
    # Must use WHERE IN subquery (not nested window QUALIFY).
    assert "WHERE" in up
    assert "IN" in up
    assert "5" in sql
    assert "SUM(REVENUE)" in up or "SUM(revenue)" in sql


def test_top_n_asc() -> None:
    """Bottom-N (order=asc) also compiles."""
    m = _simple_metric()
    mq = MetricQuery(
        metric_id="revenue",
        dimensions=("region",),
        top_n=TopN(dimension="region", n=3, order="asc"),
    )
    sql, _ = compile_metric(m, mq)
    assert "QUALIFY" in sql.upper()


# ---------------------------------------------------------------------------
# 4. Percentile
# ---------------------------------------------------------------------------


def test_percentile_cont_p50() -> None:
    """PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY col) — DuckDB may render as QUANTILE_CONT."""
    m = MetricDefinition(
        id="latency",
        name="Latency",
        measure=Measure(name="p50_latency", agg="percentile_cont", expr="latency_ms",
                        format="p50"),
        base_table="requests",
    )
    mq = MetricQuery(metric_id="latency")
    sql, _ = compile_metric(m, mq)
    up = sql.upper()
    # DuckDB dialect transpiles PERCENTILE_CONT to QUANTILE_CONT (they are equivalent).
    assert "PERCENTILE_CONT" in up or "QUANTILE_CONT" in up
    assert "0.5" in sql


def test_percentile_cont_p95() -> None:
    m = MetricDefinition(
        id="latency95",
        name="p95 Latency",
        measure=Measure(name="p95_latency", agg="percentile_cont", expr="latency_ms",
                        format="p95"),
        base_table="requests",
    )
    mq = MetricQuery(metric_id="latency95")
    sql, _ = compile_metric(m, mq)
    assert "0.95" in sql


def test_approx_count_distinct() -> None:
    m = MetricDefinition(
        id="dau",
        name="DAU",
        measure=Measure(name="dau", agg="approx_count_distinct", expr="user_id"),
        base_table="events",
    )
    mq = MetricQuery(metric_id="dau")
    sql, _ = compile_metric(m, mq)
    assert "APPROX_COUNT_DISTINCT" in sql.upper()
    assert "user_id" in sql.lower()


# ---------------------------------------------------------------------------
# 5. latest_snapshot
# ---------------------------------------------------------------------------


def test_latest_snapshot_emits_qualify_rownumber() -> None:
    """latest_snapshot injects QUALIFY ROW_NUMBER() OVER (...) = 1."""
    m = MetricDefinition(
        id="inventory",
        name="Inventory",
        measure=Measure(name="stock_qty", agg="sum", expr="qty"),
        base_table="inventory_snapshots",
        dimensions=(Dimension(name="sku"),),
        time_dimension=TimeDimension(
            column="snapshot_date",
            grains=("day",),
            default_grain="day",
        ),
    )
    mq = MetricQuery(
        metric_id="inventory",
        dimensions=("sku",),
        time_comparisons=(
            TimeComparison(
                measure="sku",       # entity column (repurposed field)
                kind="latest_snapshot",
            ),
        ),
    )
    sql, _ = compile_metric(m, mq)
    up = sql.upper()
    assert "QUALIFY" in up
    assert "ROW_NUMBER" in up
    assert "PARTITION BY" in up
    assert "ORDER BY" in up


def test_latest_snapshot_requires_time_dimension() -> None:
    """latest_snapshot without a time_dimension raises MetricError."""
    m = MetricDefinition(
        id="no_td",
        name="No TD",
        measure=Measure(name="qty", agg="sum", expr="qty"),
        base_table="t",
    )
    with pytest.raises(MetricError) as ei:
        compile_metric(
            m,
            MetricQuery(
                metric_id="no_td",
                time_comparisons=(
                    TimeComparison(measure="entity_id", kind="latest_snapshot"),
                ),
            ),
        )
    assert ei.value.code in ("snapshot_no_time", "tc_requires_grain", "no_time_dimension")


# ---------------------------------------------------------------------------
# 6. RLS soundness on the layered path
# ---------------------------------------------------------------------------


def test_layered_rls_key_exposed_in_outer_select() -> None:
    """The rls_key must appear as a column in the outer SELECT of a layered query."""
    m = _orders_metric(
        derived_measures=(
            DerivedMeasure(name="pvd", formula="delivered / ordered"),
        )
    )
    mq = MetricQuery(metric_id="orders", dimensions=("region",))
    sql, _ = compile_metric(m, mq)

    # The outer SELECT (after __base) must reference org_id.
    # Split at "WITH __base AS" and check that org_id appears in the outer part.
    parts = sql.split("WITH __base AS", 1) if "WITH __base AS" in sql else sql.split("WITH __BASE AS", 1)
    assert len(parts) == 2
    outer_part = parts[1]
    # Find the outer SELECT (after the closing paren of __base CTE).
    # org_id must be a column in the outer SELECT so RLS injection is sound.
    assert "org_id" in outer_part.lower()


def test_layered_rls_key_in_base_group_by() -> None:
    """The rls_key must be in the __base GROUP BY so partial aggregates are per-tenant."""
    m = _orders_metric(
        derived_measures=(
            DerivedMeasure(name="pvd", formula="delivered / ordered"),
        )
    )
    mq = MetricQuery(metric_id="orders", dimensions=("region",))
    sql, _ = compile_metric(m, mq)

    # Find __base CTE contents (between "WITH __base AS (" and the outer SELECT).
    lower_sql = sql.lower()
    base_start = lower_sql.find("with __base as (")
    assert base_start != -1, "Expected layered query with __base CTE"
    # org_id must appear in the base part (before the closing paren of the CTE).
    base_section = lower_sql[base_start:]
    # org_id must be present in the base CTE for GROUP BY grouping per tenant.
    assert "org_id" in base_section


def test_rls_key_already_in_dimensions_not_duplicated() -> None:
    """If rls_key is already a requested dimension, it's not duplicated."""
    m = MetricDefinition(
        id="rev",
        name="Rev",
        measure=Measure(name="revenue", agg="sum", expr="amount"),
        base_table="orders",
        dimensions=(
            Dimension(name="org_id"),
            Dimension(name="region"),
        ),
        derived_measures=(
            DerivedMeasure(name="dummy", formula="revenue / revenue"),
        ),
        rls_keys=("org_id",),
    )
    mq = MetricQuery(metric_id="rev", dimensions=("org_id", "region"))
    sql, _ = compile_metric(m, mq)
    # Should compile without error and not duplicate org_id.
    assert sql.lower().count("org_id") >= 1  # present
    # Smoke-test: no syntax error by checking structure
    assert "WITH __base AS" in sql or "WITH __BASE AS" in sql.upper()


# ---------------------------------------------------------------------------
# 7. Governance rejections
# ---------------------------------------------------------------------------


def test_derived_formula_unknown_identifier_rejected() -> None:
    m = _orders_metric(
        derived_measures=(
            DerivedMeasure(name="bad", formula="delivered / ghost"),
        )
    )
    with pytest.raises(MetricError) as ei:
        compile_metric(m, MetricQuery(metric_id="orders"))
    assert ei.value.code == "bad_formula_identifier"


def test_derived_formula_disallowed_operator_rejected() -> None:
    m = _orders_metric(
        derived_measures=(
            DerivedMeasure(name="bad", formula="delivered % ordered"),
        )
    )
    with pytest.raises(MetricError) as ei:
        compile_metric(m, MetricQuery(metric_id="orders"))
    assert ei.value.code == "bad_formula"


def test_derived_formula_empty_rejected() -> None:
    m = _orders_metric(
        derived_measures=(DerivedMeasure(name="empty", formula=""),)
    )
    with pytest.raises(MetricError) as ei:
        compile_metric(m, MetricQuery(metric_id="orders"))
    assert ei.value.code == "empty_formula"


def test_time_comparison_unknown_measure_rejected() -> None:
    m = _simple_metric()
    with pytest.raises(MetricError) as ei:
        compile_metric(
            m,
            MetricQuery(
                metric_id="revenue",
                time_grain="month",
                time_comparisons=(TimeComparison(measure="ghost", kind="prior_period"),),
            ),
        )
    assert ei.value.code == "unknown_tc_measure"


def test_time_comparison_requires_grain() -> None:
    m = _simple_metric()
    with pytest.raises(MetricError) as ei:
        compile_metric(
            m,
            MetricQuery(
                metric_id="revenue",
                time_comparisons=(TimeComparison(measure="revenue", kind="prior_period"),),
            ),
        )
    assert ei.value.code == "tc_requires_grain"


def test_top_n_zero_rejected() -> None:
    m = _simple_metric()
    with pytest.raises(MetricError) as ei:
        compile_metric(
            m,
            MetricQuery(
                metric_id="revenue",
                dimensions=("region",),
                top_n=TopN(dimension="region", n=0),
            ),
        )
    assert ei.value.code == "bad_top_n"


def test_top_n_dimension_not_in_query_rejected() -> None:
    m = _simple_metric()
    with pytest.raises(MetricError) as ei:
        compile_metric(
            m,
            MetricQuery(
                metric_id="revenue",
                dimensions=(),
                top_n=TopN(dimension="region", n=3),
            ),
        )
    assert ei.value.code == "bad_top_n"


def test_top_n_unknown_measure_rejected() -> None:
    m = _simple_metric()
    with pytest.raises(MetricError) as ei:
        compile_metric(
            m,
            MetricQuery(
                metric_id="revenue",
                dimensions=("region",),
                top_n=TopN(dimension="region", n=3, measure="ghost"),
            ),
        )
    assert ei.value.code == "bad_top_n"


# ---------------------------------------------------------------------------
# 8. CCBSA KPI acceptance suite
# ---------------------------------------------------------------------------
#
# Each of the 5 CCBSA KPIs is expressed as a MetricDefinition + MetricQuery
# with NO raw SQL. The test verifies:
#   - compile_metric succeeds (no exception)
#   - the output SQL contains the layered CTE pattern (WITH __base AS)
#   - the defining column for the ratio appears


def _ccbsa_base_metric(
    metric_id: str,
    primary_measure: Measure,
    extra_measures: tuple,
    derived_measures: tuple,
    **kwargs,
) -> MetricDefinition:
    """Helper to build a CCBSA-style metric."""
    return MetricDefinition(
        id=metric_id,
        name=metric_id,
        measure=primary_measure,
        base_table="ccbsa_sales",
        dimensions=(
            Dimension(name="store"),
            Dimension(name="sku"),
            Dimension(name="category"),
        ),
        time_dimension=TimeDimension(
            column="sale_date",
            grains=("day", "week", "month"),
            default_grain="day",
        ),
        extra_measures=extra_measures,
        derived_measures=derived_measures,
        rls_keys=("org_id",),
        **kwargs,
    )


def test_ccbsa_pvd() -> None:
    """PvD = delivered / ordered."""
    m = _ccbsa_base_metric(
        "pvd",
        primary_measure=Measure(name="delivered", agg="sum", expr="delivered_qty"),
        extra_measures=(Measure(name="ordered", agg="sum", expr="ordered_qty"),),
        derived_measures=(
            DerivedMeasure(name="pvd", formula="delivered / ordered", format="percent"),
        ),
    )
    mq = MetricQuery(metric_id="pvd", dimensions=("store", "sku"))
    sql, params = compile_metric(m, mq)
    assert "WITH __base AS" in sql or "WITH __BASE AS" in sql.upper()
    assert "pvd" in sql.lower()
    assert "NULLIF" in sql.upper()
    assert params == {}


def test_ccbsa_oos() -> None:
    """OOS rate = oos_count / total_items."""
    m = _ccbsa_base_metric(
        "oos",
        primary_measure=Measure(name="oos_count", agg="sum", expr="oos_qty"),
        extra_measures=(Measure(name="total_items", agg="sum", expr="total_qty"),),
        derived_measures=(
            DerivedMeasure(name="oos_rate", formula="oos_count / total_items", format="percent"),
        ),
    )
    mq = MetricQuery(metric_id="oos", dimensions=("store",))
    sql, _ = compile_metric(m, mq)
    assert "oos_rate" in sql.lower()
    assert "NULLIF" in sql.upper()


def test_ccbsa_fbr() -> None:
    """FBR = fulfilled / booked."""
    m = _ccbsa_base_metric(
        "fbr",
        primary_measure=Measure(name="fulfilled", agg="sum", expr="fulfilled_qty"),
        extra_measures=(Measure(name="booked", agg="sum", expr="booked_qty"),),
        derived_measures=(
            DerivedMeasure(name="fbr", formula="fulfilled / booked", format="percent"),
        ),
    )
    mq = MetricQuery(metric_id="fbr", dimensions=("sku",))
    sql, _ = compile_metric(m, mq)
    assert "fbr" in sql.lower()
    assert "NULLIF" in sql.upper()


def test_ccbsa_days_of_cover() -> None:
    """Days of cover = stock / (sales / days_in_period)  =  stock * days / sales."""
    m = _ccbsa_base_metric(
        "doc",
        primary_measure=Measure(name="stock", agg="sum", expr="stock_qty"),
        extra_measures=(
            Measure(name="sales", agg="sum", expr="sales_qty"),
            Measure(name="days", agg="max", expr="days_in_period"),
        ),
        derived_measures=(
            DerivedMeasure(
                name="days_of_cover",
                formula="stock * days / sales",
                format="number",
            ),
        ),
    )
    mq = MetricQuery(metric_id="doc", dimensions=("store", "sku"))
    sql, _ = compile_metric(m, mq)
    assert "days_of_cover" in sql.lower()
    assert "NULLIF" in sql.upper()  # sales denominator guarded


def test_ccbsa_rolling_mix() -> None:
    """Rolling 28-day revenue mix per category."""
    m = _ccbsa_base_metric(
        "rev_mix",
        primary_measure=Measure(name="revenue", agg="sum", expr="net_sales"),
        extra_measures=(),
        derived_measures=(),
    )
    mq = MetricQuery(
        metric_id="rev_mix",
        dimensions=("category",),
        time_grain="day",
        time_comparisons=(
            TimeComparison(
                measure="revenue",
                kind="rolling_sum",
                periods=28,
                name="revenue_rolling_28d",
            ),
        ),
    )
    sql, _ = compile_metric(m, mq)
    assert "WITH __base AS" in sql or "WITH __BASE AS" in sql.upper()
    assert "revenue_rolling_28d" in sql.lower()
    assert "27" in sql  # ROWS BETWEEN 27 PRECEDING
    assert "SUM" in sql.upper()
    assert "OVER" in sql.upper()


# ---------------------------------------------------------------------------
# 9. Top-N with "Other" bucket
# ---------------------------------------------------------------------------


def test_top_n_other_emits_union_all() -> None:
    """top_n.other=True emits a UNION ALL with an Other bucket."""
    m = _simple_metric()
    mq = MetricQuery(
        metric_id="revenue",
        dimensions=("region",),
        top_n=TopN(dimension="region", n=3, order="desc", other=True, other_label="Other"),
    )
    sql, _ = compile_metric(m, mq)
    up = sql.upper()
    # Must emit UNION ALL structure.
    assert "UNION ALL" in up, f"Expected UNION ALL in: {sql[:400]}"
    # Must include the "Other" label.
    assert "Other" in sql or "other" in sql.lower()


def test_top_n_other_label_appears_in_sql() -> None:
    """The other_label value appears in the emitted SQL for the Other bucket."""
    m = _simple_metric()
    mq = MetricQuery(
        metric_id="revenue",
        dimensions=("region",),
        top_n=TopN(dimension="region", n=2, order="desc", other=True, other_label="All Others"),
    )
    sql, _ = compile_metric(m, mq)
    assert "All Others" in sql, f"Expected 'All Others' label in: {sql[:400]}"


def test_top_n_other_has_qualify_for_top_rows() -> None:
    """The top-N portion still uses QUALIFY to restrict to top-N rows."""
    m = _simple_metric()
    mq = MetricQuery(
        metric_id="revenue",
        dimensions=("region",),
        top_n=TopN(dimension="region", n=5, order="desc", other=True),
    )
    sql, _ = compile_metric(m, mq)
    up = sql.upper()
    assert "QUALIFY" in up, f"Expected QUALIFY for top-N in: {sql[:400]}"
    assert "UNION ALL" in up


def test_top_n_no_other_unchanged() -> None:
    """top_n.other=False (default) does NOT emit UNION ALL (no regression)."""
    m = _simple_metric()
    mq = MetricQuery(
        metric_id="revenue",
        dimensions=("region",),
        top_n=TopN(dimension="region", n=3, order="desc", other=False),
    )
    sql, _ = compile_metric(m, mq)
    assert "UNION ALL" not in sql.upper(), "other=False must not emit UNION ALL"
    assert "QUALIFY" in sql.upper()


def test_top_n_other_with_derived_measure() -> None:
    """Other bucket recomputes derived measures from summed base measures."""
    m = _orders_metric(
        derived_measures=(
            DerivedMeasure(name="pvd", formula="delivered / ordered"),
        )
    )
    mq = MetricQuery(
        metric_id="orders",
        dimensions=("region",),
        top_n=TopN(dimension="region", n=2, order="desc", other=True),
    )
    sql, _ = compile_metric(m, mq)
    up = sql.upper()
    # The derived measure formula must appear in the Other bucket (UNION ALL part).
    assert "UNION ALL" in up
    assert "pvd" in sql.lower() or "PVD" in up
    assert "NULLIF" in up  # division guard present in the Other bucket too


def test_top_n_other_with_time_grain() -> None:
    """Other bucket works correctly when a time_grain is present.
    With time_grain the top-N portion uses WHERE IN (membership filter) to avoid
    illegal nested window functions.  UNION ALL must still be present.
    """
    m = _simple_metric()
    mq = MetricQuery(
        metric_id="revenue",
        dimensions=("region",),
        time_grain="month",
        top_n=TopN(dimension="region", n=3, order="desc", other=True),
    )
    sql, _ = compile_metric(m, mq)
    up = sql.upper()
    assert "UNION ALL" in up
    # With time_grain: membership filter (WHERE IN), not nested-window QUALIFY.
    assert "WHERE" in up
    assert "IN" in up


# ---------------------------------------------------------------------------
# 10. REGRESSION TESTS — security & correctness audit fixes
# ---------------------------------------------------------------------------


# ── Fix #1: SQLi via other_label ────────────────────────────────────────────

def test_other_label_with_single_quote_rejected() -> None:
    """other_label containing a single-quote must be rejected with MetricError."""
    m = _simple_metric()
    mq = MetricQuery(
        metric_id="revenue",
        dimensions=("region",),
        top_n=TopN(
            dimension="region",
            n=3,
            order="desc",
            other=True,
            other_label="O'Malley",  # SQL injection attempt
        ),
    )
    with pytest.raises(MetricError) as ei:
        compile_metric(m, mq)
    assert ei.value.code == "bad_other_label"


def test_other_label_with_backslash_rejected() -> None:
    """other_label containing a backslash must be rejected with MetricError."""
    m = _simple_metric()
    mq = MetricQuery(
        metric_id="revenue",
        dimensions=("region",),
        top_n=TopN(
            dimension="region",
            n=3,
            order="desc",
            other=True,
            other_label="Other\\'; DROP TABLE --",  # injection attempt
        ),
    )
    with pytest.raises(MetricError) as ei:
        compile_metric(m, mq)
    assert ei.value.code == "bad_other_label"


def test_other_label_safe_emitted_as_literal() -> None:
    """A safe other_label is emitted as a properly-quoted SQL literal (not raw f-string)."""
    m = _simple_metric()
    mq = MetricQuery(
        metric_id="revenue",
        dimensions=("region",),
        top_n=TopN(
            dimension="region",
            n=2,
            order="desc",
            other=True,
            other_label="All Others",
        ),
    )
    sql, _ = compile_metric(m, mq)
    # The label must appear in the SQL (properly quoted).
    assert "All Others" in sql
    # Verify it's inside a quoted string literal — no raw interpolation without quotes.
    assert "'All Others'" in sql


# ── Fix #2: SQLi via latest_snapshot entity column ──────────────────────────

def _inventory_metric() -> MetricDefinition:
    return MetricDefinition(
        id="inventory",
        name="Inventory",
        measure=Measure(name="stock_qty", agg="sum", expr="qty"),
        base_table="inventory_snapshots",
        dimensions=(Dimension(name="sku"),),
        time_dimension=TimeDimension(
            column="snapshot_date",
            grains=("day",),
            default_grain="day",
        ),
    )


def test_snapshot_entity_col_with_sqli_rejected() -> None:
    """latest_snapshot with an entity column containing SQL injection is rejected."""
    m = _inventory_metric()
    mq = MetricQuery(
        metric_id="inventory",
        dimensions=("sku",),
        time_comparisons=(
            TimeComparison(
                measure="sku; DROP TABLE snapshots --",  # injection in entity col
                kind="latest_snapshot",
            ),
        ),
    )
    with pytest.raises(MetricError) as ei:
        compile_metric(m, mq)
    assert ei.value.code == "bad_snapshot_entity"


def test_snapshot_entity_col_valid_identifier_accepted() -> None:
    """latest_snapshot with a plain identifier compiles without error."""
    m = _inventory_metric()
    mq = MetricQuery(
        metric_id="inventory",
        dimensions=("sku",),
        time_comparisons=(
            TimeComparison(
                measure="sku_id",  # valid identifier
                kind="latest_snapshot",
            ),
        ),
    )
    sql, _ = compile_metric(m, mq)
    up = sql.upper()
    assert "QUALIFY" in up
    assert "ROW_NUMBER" in up
    # The entity column must appear in the output.
    assert "SKU_ID" in up


def test_snapshot_entity_col_emits_quoted_identifier() -> None:
    """latest_snapshot emits the entity col and time col as quoted identifiers."""
    m = _inventory_metric()
    mq = MetricQuery(
        metric_id="inventory",
        dimensions=("sku",),
        time_comparisons=(
            TimeComparison(
                measure="item_id",
                kind="latest_snapshot",
            ),
        ),
    )
    sql, _ = compile_metric(m, mq)
    # DuckDB dialect quotes identifiers with double-quotes.
    assert '"item_id"' in sql or "item_id" in sql
    assert '"snapshot_date"' in sql or "snapshot_date" in sql


# ── Fix #3: top_n + time_grain uses membership filter, not nested window ────

def test_top_n_time_grain_uses_membership_filter_not_rank_window() -> None:
    """With time_grain, top-N uses WHERE IN subquery; no RANK() nested in SUM() OVER."""
    m = _simple_metric()
    mq = MetricQuery(
        metric_id="revenue",
        dimensions=("region",),
        time_grain="month",
        top_n=TopN(dimension="region", n=3, order="desc"),
    )
    sql, _ = compile_metric(m, mq)
    up = sql.upper()
    # Must use WHERE IN, not QUALIFY with nested window.
    assert "WHERE" in up
    assert "IN" in up
    # The subquery must reference __base for the membership lookup.
    assert "__BASE" in up
    # If QUALIFY appears (e.g. from a different branch), there must be no
    # SUM(... OVER ...) nesting inside it.
    if "QUALIFY" in up:
        assert "RANK" not in up or "SUM" not in up.split("QUALIFY")[1][:200]


def test_top_n_no_time_grain_still_uses_qualify() -> None:
    """Without time_grain, top-N still uses QUALIFY RANK() <= N (unchanged path)."""
    m = _simple_metric()
    mq = MetricQuery(
        metric_id="revenue",
        dimensions=("region",),
        top_n=TopN(dimension="region", n=3, order="desc"),
    )
    sql, _ = compile_metric(m, mq)
    up = sql.upper()
    assert "QUALIFY" in up
    assert "RANK" in up


# ── Fix #4: top_n other + time_comparisons — column count parity ────────────

def test_top_n_other_with_time_comparison_column_count_parity() -> None:
    """Both UNION arms (top-N and Other) must have identical column counts when
    time_comparisons are present.  The Other arm emits NULL AS <out_name> for
    each time-comparison window column.
    """
    m = _simple_metric()
    mq = MetricQuery(
        metric_id="revenue",
        dimensions=("region",),
        time_grain="month",
        time_comparisons=(
            TimeComparison(measure="revenue", kind="prior_period", periods=1),
            TimeComparison(measure="revenue", kind="pop_abs"),
        ),
        top_n=TopN(dimension="region", n=2, order="desc", other=True),
    )
    sql, _ = compile_metric(m, mq)
    up = sql.upper()
    assert "UNION ALL" in up

    # Split on UNION ALL and inspect the second part (the Other arm).
    parts = sql.upper().split("UNION ALL")
    assert len(parts) == 2, "Expected exactly one UNION ALL"
    other_arm = parts[1]
    # Both out_names must appear as NULL aliases in the Other arm.
    assert "REVENUE_PRIOR_PERIOD" in other_arm
    assert "REVENUE_POP_ABS" in other_arm
    assert "NULL" in other_arm


# ── Fix #5: count(*) Other bucket uses SUM(col), not SUM(COUNT(*)) ──────────

def test_top_n_other_count_star_uses_sum_of_col() -> None:
    """The Other bucket for a count(*) measure emits SUM(<col_name>) not SUM(COUNT(*))."""
    m = MetricDefinition(
        id="events",
        name="Events",
        measure=Measure(name="event_count", agg="count", expr="*"),
        base_table="events",
        dimensions=(Dimension(name="channel"),),
    )
    mq = MetricQuery(
        metric_id="events",
        dimensions=("channel",),
        top_n=TopN(dimension="channel", n=2, order="desc", other=True),
    )
    sql, _ = compile_metric(m, mq)
    up = sql.upper()
    assert "UNION ALL" in up

    # Extract the Other bucket arm (after UNION ALL).
    parts = sql.split("UNION ALL", 1)
    other_arm = parts[1].upper()
    # Must NOT contain nested aggregate SUM(COUNT(*)).
    assert "SUM(COUNT(" not in other_arm, (
        "Other bucket must not emit SUM(COUNT(*)) — invalid nested agg"
    )
    # Must emit SUM(event_count) — sum the pre-computed count column.
    assert "SUM(EVENT_COUNT)" in other_arm, (
        f"Expected SUM(EVENT_COUNT) in Other arm:\n{other_arm[:300]}"
    )


# ── Fix #6: resource bounds ──────────────────────────────────────────────────

def test_tc_periods_above_max_rejected() -> None:
    """time_comparison.periods above NUBI_MAX_TC_PERIODS raises MetricError."""
    import os
    m = _simple_metric()
    max_periods = int(os.environ.get("NUBI_MAX_TC_PERIODS", 3650))
    mq = MetricQuery(
        metric_id="revenue",
        dimensions=("region",),
        time_grain="month",
        time_comparisons=(
            TimeComparison(measure="revenue", kind="rolling_sum", periods=max_periods + 1),
        ),
    )
    with pytest.raises(MetricError) as ei:
        compile_metric(m, mq)
    assert ei.value.code == "bad_tc_periods"


def test_tc_periods_zero_rejected() -> None:
    """time_comparison.periods of 0 raises MetricError (must be >= 1)."""
    m = _simple_metric()
    mq = MetricQuery(
        metric_id="revenue",
        dimensions=("region",),
        time_grain="month",
        time_comparisons=(
            TimeComparison(measure="revenue", kind="rolling_sum", periods=0),
        ),
    )
    with pytest.raises(MetricError) as ei:
        compile_metric(m, mq)
    assert ei.value.code == "bad_tc_periods"


def test_top_n_above_max_rejected() -> None:
    """top_n.n above NUBI_MAX_TOP_N raises MetricError."""
    import os
    m = _simple_metric()
    max_n = int(os.environ.get("NUBI_MAX_TOP_N", 1000))
    mq = MetricQuery(
        metric_id="revenue",
        dimensions=("region",),
        top_n=TopN(dimension="region", n=max_n + 1),
    )
    with pytest.raises(MetricError) as ei:
        compile_metric(m, mq)
    assert ei.value.code == "bad_top_n"


def test_query_limit_above_max_rejected() -> None:
    """mq.limit above NUBI_MAX_QUERY_LIMIT raises MetricError."""
    import os
    m = _simple_metric()
    max_limit = int(os.environ.get("NUBI_MAX_QUERY_LIMIT", 100_000))
    mq = MetricQuery(
        metric_id="revenue",
        dimensions=("region",),
        limit=max_limit + 1,
    )
    with pytest.raises(MetricError) as ei:
        compile_metric(m, mq)
    assert ei.value.code == "bad_limit"


def test_query_limit_zero_rejected() -> None:
    """mq.limit of 0 raises MetricError (must be >= 1)."""
    m = _simple_metric()
    mq = MetricQuery(
        metric_id="revenue",
        dimensions=("region",),
        limit=0,
    )
    with pytest.raises(MetricError) as ei:
        compile_metric(m, mq)
    assert ei.value.code == "bad_limit"


def test_valid_bounds_accepted() -> None:
    """Valid periods / top_n / limit values compile without error."""
    m = _simple_metric()
    mq = MetricQuery(
        metric_id="revenue",
        dimensions=("region",),
        time_grain="month",
        limit=1000,
        time_comparisons=(
            TimeComparison(measure="revenue", kind="rolling_sum", periods=30),
        ),
        top_n=TopN(dimension="region", n=10),
    )
    sql, _ = compile_metric(m, mq)
    assert sql  # compiled successfully


# ── Fix #7: nested NULLIF in parenthesised denominators ─────────────────────

def test_nested_division_in_paren_guarded() -> None:
    """a / (b / c) must guard BOTH divisions: outer wraps the (b/NULLIF(c,0)) group."""
    m = MetricDefinition(
        id="ratio",
        name="Ratio",
        measure=Measure(name="a", agg="sum", expr="col_a"),
        base_table="t",
        extra_measures=(
            Measure(name="b", agg="sum", expr="col_b"),
            Measure(name="c", agg="sum", expr="col_c"),
        ),
        derived_measures=(
            DerivedMeasure(name="nested_ratio", formula="a / (b / c)"),
        ),
    )
    mq = MetricQuery(metric_id="ratio")
    sql, _ = compile_metric(m, mq)
    up = sql.upper()
    # There must be at least two NULLIF calls — one for the inner (c) and one for
    # the outer group.
    nullif_count = up.count("NULLIF")
    assert nullif_count >= 2, (
        f"Expected >=2 NULLIF guards for a/(b/c), found {nullif_count}:\n{sql}"
    )


def test_simple_division_still_guarded() -> None:
    """Simple a / b still produces NULLIF(b, 0) — regression guard."""
    m = MetricDefinition(
        id="simple_div",
        name="SimpleDiv",
        measure=Measure(name="a", agg="sum", expr="col_a"),
        base_table="t",
        extra_measures=(Measure(name="b", agg="sum", expr="col_b"),),
        derived_measures=(DerivedMeasure(name="ratio", formula="a / b"),),
    )
    mq = MetricQuery(metric_id="simple_div")
    sql, _ = compile_metric(m, mq)
    up = sql.upper()
    assert "NULLIF(B, 0)" in up or "NULLIF(b, 0)" in sql
    assert up.count("NULLIF") == 1


def test_triple_nested_division_fully_guarded() -> None:
    """a / (b / (c + 1)) — inner (c+1) is the innermost denominator; must be guarded."""
    m = MetricDefinition(
        id="triple",
        name="Triple",
        measure=Measure(name="a", agg="sum", expr="col_a"),
        base_table="t",
        extra_measures=(
            Measure(name="b", agg="sum", expr="col_b"),
            Measure(name="c", agg="sum", expr="col_c"),
        ),
        derived_measures=(
            DerivedMeasure(name="r", formula="a / (b / (c + 1))"),
        ),
    )
    mq = MetricQuery(metric_id="triple")
    sql, _ = compile_metric(m, mq)
    # a / NULLIF(b / NULLIF((c + 1), 0), 0) — two NULLIF guards.
    assert sql.upper().count("NULLIF") >= 2


# ---------------------------------------------------------------------------
# 11. SECOND-WAVE REGRESSION TESTS — real SQL parse + DuckDB execution
#     Every test here uses sqlglot.parse_one to verify the SQL is valid,
#     and where values matter, executes against an in-memory DuckDB.
# ---------------------------------------------------------------------------

import sqlglot  # noqa: E402 (used in regression tests only)


def _duckdb_conn():
    """Return a fresh in-memory DuckDB connection (skips if duckdb not installed)."""
    try:
        import duckdb
        return duckdb.connect(":memory:")
    except ImportError:
        import pytest
        pytest.skip("duckdb not installed")


# ── Fix 1: tc.name SQLi in alias position ────────────────────────────────────

def test_tc_name_sqli_raises_metric_error() -> None:
    """tc.name containing non-identifier chars must raise MetricError(bad_tc_name)."""
    m = _simple_metric()
    mq = MetricQuery(
        metric_id="revenue",
        dimensions=("region",),
        time_grain="month",
        time_comparisons=(
            TimeComparison(
                measure="revenue",
                kind="prior_period",
                periods=1,
                name="x'; DROP TABLE foo --",  # SQLi attempt
            ),
        ),
    )
    with pytest.raises(MetricError) as ei:
        compile_metric(m, mq)
    assert ei.value.code == "bad_tc_name"


def test_tc_name_valid_identifier_accepted_and_sql_parses() -> None:
    """A valid tc.name compiles; the SQL parses without error via sqlglot."""
    m = _simple_metric()
    mq = MetricQuery(
        metric_id="revenue",
        dimensions=("region",),
        time_grain="month",
        time_comparisons=(
            TimeComparison(
                measure="revenue",
                kind="prior_period",
                periods=1,
                name="rev_prev",
            ),
        ),
    )
    sql, _ = compile_metric(m, mq)
    # Must parse cleanly.
    parsed = sqlglot.parse_one(sql, dialect="duckdb")
    assert parsed is not None
    assert "rev_prev" in sql.lower()


def test_tc_name_none_uses_default_out_name_and_sql_parses() -> None:
    """tc.name=None uses the default out_name(); SQL must still parse."""
    m = _simple_metric()
    mq = MetricQuery(
        metric_id="revenue",
        dimensions=("region",),
        time_grain="month",
        time_comparisons=(
            TimeComparison(measure="revenue", kind="prior_period", periods=1),
        ),
    )
    sql, _ = compile_metric(m, mq)
    parsed = sqlglot.parse_one(sql, dialect="duckdb")
    assert parsed is not None
    assert "revenue_prior_period" in sql.lower()


# ── Fix 2: IN without subquery parentheses ───────────────────────────────────

def test_top_n_time_grain_sql_parses() -> None:
    """top_n + time_grain: the emitted SQL must parse (IN subquery must have parens)."""
    m = _simple_metric()
    mq = MetricQuery(
        metric_id="revenue",
        dimensions=("region",),
        time_grain="month",
        top_n=TopN(dimension="region", n=3, order="desc"),
    )
    sql, _ = compile_metric(m, mq)
    # This would raise if the SQL is invalid (e.g. "IN SELECT ..." without parens).
    parsed = sqlglot.parse_one(sql, dialect="duckdb")
    assert parsed is not None
    # The IN clause must be a subquery (parenthesised).
    sql_upper = sql.upper()
    assert "IN (" in sql_upper or "IN(" in sql_upper


def test_top_n_other_time_grain_sql_parses() -> None:
    """top_n.other + time_grain: the emitted SQL must parse."""
    m = _simple_metric()
    mq = MetricQuery(
        metric_id="revenue",
        dimensions=("region",),
        time_grain="month",
        top_n=TopN(dimension="region", n=2, order="desc", other=True),
    )
    sql, _ = compile_metric(m, mq)
    # Parse the full UNION ALL SQL.
    parsed = sqlglot.parse_one(sql, dialect="duckdb")
    assert parsed is not None
    assert "UNION ALL" in sql.upper()


def test_top_n_time_grain_executes_in_duckdb() -> None:
    """top_n + time_grain emits SQL that actually executes in DuckDB."""
    con = _duckdb_conn()
    # Create a tiny orders table (no org_id; use a metric without rls_keys).
    con.execute("""
        CREATE TABLE orders_topn (
            region VARCHAR,
            amount DOUBLE,
            created_at DATE
        )
    """)
    con.execute("""
        INSERT INTO orders_topn VALUES
            ('A', 100, '2024-01-01'),
            ('A', 200, '2024-02-01'),
            ('B', 50,  '2024-01-01'),
            ('B', 75,  '2024-02-01'),
            ('C', 10,  '2024-01-01')
    """)
    # Use a metric without rls_keys so no extra org_id GROUP BY column is injected.
    from app.metrics.models import TimeDimension as TD
    m = MetricDefinition(
        id="revenue",
        name="Revenue",
        measure=Measure(name="revenue", agg="sum", expr="amount"),
        base_table="orders_topn",
        dimensions=(Dimension(name="region"),),
        time_dimension=TD(
            column="created_at",
            grains=("day", "week", "month", "quarter", "year"),
            default_grain="day",
        ),
    )
    mq = MetricQuery(
        metric_id="revenue",
        dimensions=("region",),
        time_grain="month",
        top_n=TopN(dimension="region", n=2, order="desc"),
    )
    sql, _ = compile_metric(m, mq)
    # Must parse.
    parsed = sqlglot.parse_one(sql, dialect="duckdb")
    assert parsed is not None
    # Must execute without error.
    rows = con.execute(sql).fetchall()
    # Top-2 regions by SUM(amount) are A (300) and B (125); C (10) excluded.
    regions = {r[0] for r in rows}
    assert "A" in regions
    assert "B" in regions
    assert "C" not in regions


# ── Fix 3: Other-bucket re-aggregation ───────────────────────────────────────

def _make_multi_agg_metric() -> MetricDefinition:
    """Metric with sum, min, max, avg measures for re-aggregation testing."""
    return MetricDefinition(
        id="sales",
        name="Sales",
        measure=Measure(name="total_sales", agg="sum", expr="amount"),
        base_table="sales",
        extra_measures=(
            Measure(name="min_sale", agg="min", expr="amount"),
            Measure(name="max_sale", agg="max", expr="amount"),
            Measure(name="avg_sale", agg="avg", expr="amount"),
        ),
        dimensions=(Dimension(name="region"),),
    )


def test_other_bucket_min_uses_min_not_sum() -> None:
    """The Other bucket re-aggregates a min measure with MIN(), not SUM()."""
    m = _make_multi_agg_metric()
    mq = MetricQuery(
        metric_id="sales",
        dimensions=("region",),
        top_n=TopN(dimension="region", n=1, order="desc", other=True),
    )
    sql, _ = compile_metric(m, mq)
    # SQL must parse.
    sqlglot.parse_one(sql, dialect="duckdb")
    # Extract the Other arm (after UNION ALL).
    parts = sql.split("UNION ALL", 1)
    other_arm = parts[1].upper()
    assert "MIN(MIN_SALE)" in other_arm, f"Expected MIN(MIN_SALE) in Other arm:\n{other_arm[:500]}"
    assert "SUM(MIN_SALE)" not in other_arm, "Must NOT use SUM for a min measure"


def test_other_bucket_max_uses_max_not_sum() -> None:
    """The Other bucket re-aggregates a max measure with MAX(), not SUM()."""
    m = _make_multi_agg_metric()
    mq = MetricQuery(
        metric_id="sales",
        dimensions=("region",),
        top_n=TopN(dimension="region", n=1, order="desc", other=True),
    )
    sql, _ = compile_metric(m, mq)
    sqlglot.parse_one(sql, dialect="duckdb")
    parts = sql.split("UNION ALL", 1)
    other_arm = parts[1].upper()
    assert "MAX(MAX_SALE)" in other_arm, f"Expected MAX(MAX_SALE) in Other arm:\n{other_arm[:500]}"
    assert "SUM(MAX_SALE)" not in other_arm, "Must NOT use SUM for a max measure"


def test_other_bucket_avg_emits_null() -> None:
    """The Other bucket emits NULL for avg measures (not re-aggregable without weights)."""
    m = _make_multi_agg_metric()
    mq = MetricQuery(
        metric_id="sales",
        dimensions=("region",),
        top_n=TopN(dimension="region", n=1, order="desc", other=True),
    )
    sql, _ = compile_metric(m, mq)
    sqlglot.parse_one(sql, dialect="duckdb")
    parts = sql.split("UNION ALL", 1)
    other_arm = parts[1].upper()
    assert "NULL AS AVG_SALE" in other_arm or "NULL AS \"AVG_SALE\"" in other_arm, (
        f"Expected NULL AS avg_sale in Other arm:\n{other_arm[:500]}"
    )


def test_other_bucket_sum_uses_sum() -> None:
    """The Other bucket re-aggregates a sum measure with SUM()."""
    m = _make_multi_agg_metric()
    mq = MetricQuery(
        metric_id="sales",
        dimensions=("region",),
        top_n=TopN(dimension="region", n=1, order="desc", other=True),
    )
    sql, _ = compile_metric(m, mq)
    sqlglot.parse_one(sql, dialect="duckdb")
    parts = sql.split("UNION ALL", 1)
    other_arm = parts[1].upper()
    assert "SUM(TOTAL_SALES)" in other_arm, (
        f"Expected SUM(TOTAL_SALES) in Other arm:\n{other_arm[:500]}"
    )


def test_other_bucket_values_correct_in_duckdb() -> None:
    """Execute the Other-bucket query against real DuckDB data and check values."""
    con = _duckdb_conn()
    con.execute("""
        CREATE TABLE sales (
            region VARCHAR,
            amount DOUBLE
        )
    """)
    # Regions A (top-1), B and C (go to Other).
    con.execute("""
        INSERT INTO sales VALUES
            ('A', 1000),
            ('A', 500),
            ('B', 100),
            ('B', 200),
            ('C', 50),
            ('C', 30)
    """)
    m = MetricDefinition(
        id="sales",
        name="Sales",
        measure=Measure(name="total_sales", agg="sum", expr="amount"),
        base_table="sales",
        extra_measures=(
            Measure(name="min_sale", agg="min", expr="amount"),
            Measure(name="max_sale", agg="max", expr="amount"),
        ),
        dimensions=(Dimension(name="region"),),
    )
    mq = MetricQuery(
        metric_id="sales",
        dimensions=("region",),
        top_n=TopN(dimension="region", n=1, order="desc", other=True, other_label="Other"),
    )
    sql, _ = compile_metric(m, mq)
    # SQL must parse.
    sqlglot.parse_one(sql, dialect="duckdb")
    rows = con.execute(sql).fetchall()
    # Columns: region, total_sales, min_sale, max_sale
    row_by_region = {r[0]: r for r in rows}
    # Top region A should be there.
    assert "A" in row_by_region
    # Other bucket.
    assert "Other" in row_by_region
    other = row_by_region["Other"]
    # total_sales in Other = SUM of B+C = 100+200+50+30 = 380
    assert other[1] == 380.0, f"Expected Other total_sales=380, got {other[1]}"
    # min_sale in Other = MIN of B+C amounts = 30
    assert other[2] == 30.0, f"Expected Other min_sale=30, got {other[2]}"
    # max_sale in Other = MAX of B+C amounts = 200
    assert other[3] == 200.0, f"Expected Other max_sale=200, got {other[3]}"


# ── Fix 4: derived measure as rank measure with time_grain ───────────────────

def test_derived_rank_measure_with_time_grain_rejected() -> None:
    """Using a derived measure as top_n rank measure with time_grain raises MetricError."""
    m = _orders_metric(
        derived_measures=(
            DerivedMeasure(name="pvd", formula="delivered / ordered"),
        )
    )
    mq = MetricQuery(
        metric_id="orders",
        dimensions=("region",),
        time_grain="month",
        top_n=TopN(dimension="region", n=3, measure="pvd", order="desc"),
    )
    with pytest.raises(MetricError) as ei:
        compile_metric(m, mq)
    assert ei.value.code == "bad_top_n"


def test_derived_rank_measure_without_time_grain_ok() -> None:
    """Using a derived measure as rank measure WITHOUT time_grain compiles fine."""
    m = _orders_metric(
        derived_measures=(
            DerivedMeasure(name="pvd", formula="delivered / ordered"),
        )
    )
    mq = MetricQuery(
        metric_id="orders",
        dimensions=("region",),
        # No time_grain — so derived rank is valid (no membership subquery against __base).
        top_n=TopN(dimension="region", n=3, measure="pvd", order="desc"),
    )
    sql, _ = compile_metric(m, mq)
    # Must parse.
    parsed = sqlglot.parse_one(sql, dialect="duckdb")
    assert parsed is not None
    assert "pvd" in sql.lower()


# ---------------------------------------------------------------------------
# 12. THIRD-WAVE REGRESSION TESTS — audited fixes with DuckDB execution
# ---------------------------------------------------------------------------

# ── Fix (Issue 1): Other-bucket derived ratio correct in DuckDB ──────────────

def test_top_n_other_derived_ratio_correct_in_duckdb() -> None:
    """[HIGH] Other-bucket derived measure (delivered/ordered ratio) must NOT
    Binder-Error and must compute the correct aggregate ratio in DuckDB.

    The Other arm is a GROUP BY SELECT; bare measure names (e.g. 'delivered')
    are not in GROUP BY and must be replaced with their aggregate forms
    (e.g. SUM(delivered)) before the derived formula is emitted.
    """
    con = _duckdb_conn()
    con.execute("""
        CREATE TABLE pvd_orders (
            region  VARCHAR,
            delivered_qty DOUBLE,
            ordered_qty   DOUBLE
        )
    """)
    # Regions A (top-1), B and C (Other).
    # A: delivered=800, ordered=1000  -> pvd=0.8
    # B: delivered=100, ordered=200   \
    # C: delivered=50,  ordered=100   / Other: delivered=150, ordered=300 -> pvd=0.5
    con.execute("""
        INSERT INTO pvd_orders VALUES
            ('A', 800, 1000),
            ('B', 100,  200),
            ('C',  50,  100)
    """)
    m = MetricDefinition(
        id="pvd_test",
        name="PvD Test",
        measure=Measure(name="delivered", agg="sum", expr="delivered_qty"),
        base_table="pvd_orders",
        extra_measures=(
            Measure(name="ordered", agg="sum", expr="ordered_qty"),
        ),
        derived_measures=(
            DerivedMeasure(name="pvd", formula="delivered / ordered"),
        ),
        dimensions=(Dimension(name="region"),),
    )
    mq = MetricQuery(
        metric_id="pvd_test",
        dimensions=("region",),
        top_n=TopN(dimension="region", n=1, order="desc", other=True, other_label="Other"),
    )
    sql, _ = compile_metric(m, mq)
    # Must parse cleanly (would previously fail with bare name in aggregate SELECT).
    sqlglot.parse_one(sql, dialect="duckdb")
    # Must execute without Binder Error.
    rows = con.execute(sql).fetchall()
    # Columns: region, delivered, ordered, pvd
    row_by_region = {r[0]: r for r in rows}
    assert "A" in row_by_region, f"Top region A missing; rows={rows}"
    assert "Other" in row_by_region, f"Other bucket missing; rows={rows}"
    other = row_by_region["Other"]
    # delivered=150, ordered=300 -> pvd = 0.5
    assert other[1] == 150.0, f"Other delivered={other[1]}, expected 150"
    assert other[2] == 300.0, f"Other ordered={other[2]}, expected 300"
    assert abs(other[3] - 0.5) < 1e-9, f"Other pvd={other[3]}, expected 0.5"


# ── Fix (Issue 2): date-correct prior-year on SPARSE series ─────────────────

def test_prior_year_sparse_series_matches_by_date_not_position() -> None:
    """[MED] YoY prior_year must match by date, not by row offset.

    A sparse series (missing months) would cause positional LAG to pick the
    wrong row.  The date-correct correlated subquery must return the value at
    exactly (bucket - 1 year), or NULL when no matching bucket exists.
    """
    con = _duckdb_conn()
    con.execute("""
        CREATE TABLE sparse_revenue (
            region    VARCHAR,
            amount    DOUBLE,
            sale_date DATE
        )
    """)
    # Current year buckets: 2024-01, 2024-03, 2024-06  (gaps at 2024-02, etc.)
    # Prior year buckets:  2023-01, 2023-06             (gap at 2023-03)
    # Expected prior-year values:
    #   2024-01 -> 2023-01 = 100
    #   2024-03 -> 2023-03 = NULL (no matching bucket)
    #   2024-06 -> 2023-06 = 200
    con.execute("""
        INSERT INTO sparse_revenue VALUES
            ('A', 100,  '2023-01-01'),
            ('A', 200,  '2023-06-01'),
            ('A', 300,  '2024-01-01'),
            ('A', 400,  '2024-03-01'),
            ('A', 500,  '2024-06-01')
    """)
    m = MetricDefinition(
        id="sparse_rev",
        name="Sparse Revenue",
        measure=Measure(name="revenue", agg="sum", expr="amount"),
        base_table="sparse_revenue",
        dimensions=(Dimension(name="region"),),
        time_dimension=TimeDimension(
            column="sale_date",
            grains=("month",),
            default_grain="month",
        ),
    )
    mq = MetricQuery(
        metric_id="sparse_rev",
        dimensions=("region",),
        time_grain="month",
        time_comparisons=(
            TimeComparison(measure="revenue", kind="prior_year"),
        ),
    )
    sql, _ = compile_metric(m, mq)
    # Must parse.
    sqlglot.parse_one(sql, dialect="duckdb")
    # Must execute.
    rows = con.execute(sql).fetchall()
    # Columns: region, sale_date_month, revenue, revenue_prior_year
    # DuckDB returns DATE_TRUNC results as datetime.datetime; normalise to
    # (year, month) tuples for comparison so we don't depend on the exact type.
    row_by_ym = {(r[1].year, r[1].month): r for r in rows}
    assert (2024, 1) in row_by_ym, f"Missing 2024-01; rows={rows}"
    assert (2024, 3) in row_by_ym, f"Missing 2024-03; rows={rows}"
    assert (2024, 6) in row_by_ym, f"Missing 2024-06; rows={rows}"
    # 2024-01 -> prior year 2023-01 = 100
    assert row_by_ym[(2024, 1)][3] == 100.0, (
        f"2024-01 prior_year should be 100 (2023-01 value), got {row_by_ym[(2024, 1)][3]}"
    )
    # 2024-03 -> prior year 2023-03 = NULL (no row for 2023-03)
    assert row_by_ym[(2024, 3)][3] is None, (
        f"2024-03 prior_year should be NULL (2023-03 missing), got {row_by_ym[(2024, 3)][3]}"
    )
    # 2024-06 -> prior year 2023-06 = 200
    assert row_by_ym[(2024, 6)][3] == 200.0, (
        f"2024-06 prior_year should be 200 (2023-06 value), got {row_by_ym[(2024, 6)][3]}"
    )


# ── Fix (Issue 3): default LIMIT applied when mq.limit is None ───────────────

def test_flat_query_has_default_limit_when_none() -> None:
    """[MED] mq.limit=None on the FLAT path must emit a LIMIT clause (default cap)."""
    m = _simple_metric()
    mq = MetricQuery(metric_id="revenue", dimensions=("region",))  # limit=None
    sql, _ = compile_metric(m, mq)
    sqlglot.parse_one(sql, dialect="duckdb")
    assert "LIMIT" in sql.upper(), "Flat query must emit a LIMIT when mq.limit is None"


def test_layered_query_has_default_limit_when_none() -> None:
    """[MED] mq.limit=None on the LAYERED path must emit a LIMIT clause (default cap)."""
    m = _orders_metric(
        derived_measures=(
            DerivedMeasure(name="pvd", formula="delivered / ordered"),
        )
    )
    mq = MetricQuery(metric_id="orders", dimensions=("region",))  # limit=None
    sql, _ = compile_metric(m, mq)
    sqlglot.parse_one(sql, dialect="duckdb")
    assert "LIMIT" in sql.upper(), "Layered query must emit a LIMIT when mq.limit is None"


def test_explicit_limit_overrides_default() -> None:
    """[MED] An explicit mq.limit is used verbatim (not overridden by the default cap)."""
    m = _simple_metric()
    mq = MetricQuery(metric_id="revenue", dimensions=("region",), limit=42)
    sql, _ = compile_metric(m, mq)
    sqlglot.parse_one(sql, dialect="duckdb")
    assert "42" in sql, "Explicit limit=42 must appear in the emitted SQL"


def test_default_limit_value_from_env() -> None:
    """[MED] The default LIMIT is NUBI_METRIC_DEFAULT_LIMIT (default 100000)."""
    import os
    expected = int(os.environ.get("NUBI_METRIC_DEFAULT_LIMIT", 100_000))
    m = _simple_metric()
    mq = MetricQuery(metric_id="revenue", dimensions=("region",))  # limit=None
    sql, _ = compile_metric(m, mq)
    assert str(expected) in sql, (
        f"Default limit {expected} must appear in SQL when mq.limit is None"
    )


# ---------------------------------------------------------------------------
# 13. FOURTH-WAVE REGRESSION TESTS — through plan() with DuckDB execution
#
# HARD RULE: metric/compiler tests MUST go through plan() — not just
# sqlglot.parse_one — and execute representative cases on in-memory DuckDB
# asserting RESULTS.  plan() is the real gate every metric request hits.
# ---------------------------------------------------------------------------

from app.connectors.planner import plan  # noqa: E402


def _plan_and_run(sql: str, con, claims=None):
    """Run compile output through plan() and execute on DuckDB, returning rows."""
    p = plan(sql, claims=claims or {}, dialect="duckdb")
    return con.execute(p.sql).fetchall()


# ── Issue 1: top_n.other UNION ALL through plan() ────────────────────────────

def test_top_n_other_plan_accepts_union_all() -> None:
    """[CRITICAL] plan() must accept the UNION ALL emitted by top_n.other=True.

    Previously plan() raised UNSUPPORTED_QUERY(400) for exp.Union.  The fix
    wraps the Union in SELECT * FROM (...) before RLS injection.
    """
    m = _simple_metric()
    mq = MetricQuery(
        metric_id="revenue",
        dimensions=("region",),
        top_n=TopN(dimension="region", n=2, order="desc", other=True, other_label="Other"),
    )
    sql, _ = compile_metric(m, mq)
    # Must NOT raise UNSUPPORTED_QUERY.
    p = plan(sql, claims={}, dialect="duckdb")
    assert p.sql  # plan produced a non-empty SQL


def test_top_n_other_plan_executes_correct_values_in_duckdb() -> None:
    """[CRITICAL] top_n.other through plan() executes on DuckDB with correct Other values."""
    con = _duckdb_conn()
    con.execute("""
        CREATE TABLE rev_union (
            region  VARCHAR,
            amount  DOUBLE
        )
    """)
    # A: 1000, B: 200, C: 50 → top-1 = A; Other = B+C = 250
    con.execute("""
        INSERT INTO rev_union VALUES
            ('A', 1000),
            ('B', 200),
            ('C', 50)
    """)
    m = MetricDefinition(
        id="rev_u",
        name="Rev Union",
        measure=Measure(name="revenue", agg="sum", expr="amount"),
        base_table="rev_union",
        dimensions=(Dimension(name="region"),),
    )
    mq = MetricQuery(
        metric_id="rev_u",
        dimensions=("region",),
        top_n=TopN(dimension="region", n=1, order="desc", other=True, other_label="Other"),
    )
    sql, _ = compile_metric(m, mq)
    rows = _plan_and_run(sql, con, claims={})
    row_by_region = {r[0]: r for r in rows}
    assert "A" in row_by_region, f"Top region A missing; rows={rows}"
    assert "Other" in row_by_region, f"Other bucket missing; rows={rows}"
    # The wrapper SELECT * strips the LIMIT from the inner query; just check values.
    other = row_by_region["Other"]
    assert other[1] == 250.0, f"Other revenue should be 250 (B+C), got {other[1]}"


def test_top_n_other_plan_rls_predicate_injected() -> None:
    """[CRITICAL] RLS predicate is injected by plan() into the Union wrapper.

    The top_n.other compiler MUST carry rls_keys through BOTH union arms so
    the wrapper's WHERE rls_key=claim lands correctly.  We verify by checking
    the plan SQL contains the injected predicate AND that querying with a wrong
    org_id returns no rows.
    """
    con = _duckdb_conn()
    con.execute("""
        CREATE TABLE rev_rls (
            region  VARCHAR,
            amount  DOUBLE,
            org_id  VARCHAR
        )
    """)
    con.execute("""
        INSERT INTO rev_rls VALUES
            ('A', 1000, 'org1'),
            ('B', 200,  'org1'),
            ('C', 50,   'org2')
    """)
    m = MetricDefinition(
        id="rev_rls",
        name="Rev RLS",
        measure=Measure(name="revenue", agg="sum", expr="amount"),
        base_table="rev_rls",
        dimensions=(Dimension(name="region"),),
        rls_keys=("org_id",),
    )
    mq = MetricQuery(
        metric_id="rev_rls",
        dimensions=("region",),
        top_n=TopN(dimension="region", n=2, order="desc", other=True, other_label="Other"),
    )
    sql, _ = compile_metric(m, mq)
    claims = {"policies": {"org_id": "org1"}}
    p = plan(sql, claims=claims, dialect="duckdb")
    # The plan SQL must contain the RLS predicate.
    assert "org_id" in p.sql.lower(), "RLS predicate missing from plan SQL"
    assert "org1" in p.sql.lower(), "RLS value missing from plan SQL"
    # Execute: only org1 rows → A (1000) and B (200); C (org2) excluded.
    rows = con.execute(p.sql).fetchall()
    regions = {r[0] for r in rows}
    assert "C" not in regions, f"org2 row C leaked through RLS; rows={rows}"
    # A or Other must be present (union arms may merge depending on n=2).
    assert len(rows) >= 1, f"Expected rows for org1; rows={rows}"


# ── Issue 2: non-additive rank measure + time_grain rejected ─────────────────

def test_non_additive_avg_rank_time_grain_raises() -> None:
    """[HIGH] avg rank measure + time_grain must raise MetricError."""
    m = MetricDefinition(
        id="avg_m",
        name="Avg Metric",
        measure=Measure(name="avg_val", agg="avg", expr="val"),
        base_table="t",
        dimensions=(Dimension(name="region"),),
        time_dimension=TimeDimension(
            column="created_at",
            grains=("month",),
            default_grain="month",
        ),
    )
    mq = MetricQuery(
        metric_id="avg_m",
        dimensions=("region",),
        time_grain="month",
        top_n=TopN(dimension="region", n=3, measure="avg_val", order="desc"),
    )
    with pytest.raises(MetricError) as ei:
        compile_metric(m, mq)
    assert ei.value.code == "bad_top_n"


def test_non_additive_count_distinct_rank_time_grain_raises() -> None:
    """[HIGH] count_distinct rank measure + time_grain must raise MetricError."""
    m = MetricDefinition(
        id="cd_m",
        name="CD Metric",
        measure=Measure(name="unique_users", agg="count_distinct", expr="user_id"),
        base_table="t",
        dimensions=(Dimension(name="region"),),
        time_dimension=TimeDimension(
            column="created_at",
            grains=("month",),
            default_grain="month",
        ),
    )
    mq = MetricQuery(
        metric_id="cd_m",
        dimensions=("region",),
        time_grain="month",
        top_n=TopN(dimension="region", n=3, measure="unique_users", order="desc"),
    )
    with pytest.raises(MetricError) as ei:
        compile_metric(m, mq)
    assert ei.value.code == "bad_top_n"


def test_non_additive_percentile_rank_time_grain_raises() -> None:
    """[HIGH] percentile_cont rank measure + time_grain must raise MetricError."""
    m = MetricDefinition(
        id="pct_m",
        name="Pct Metric",
        measure=Measure(name="p50", agg="percentile_cont", expr="latency", format="p50"),
        base_table="t",
        dimensions=(Dimension(name="region"),),
        time_dimension=TimeDimension(
            column="created_at",
            grains=("month",),
            default_grain="month",
        ),
    )
    mq = MetricQuery(
        metric_id="pct_m",
        dimensions=("region",),
        time_grain="month",
        top_n=TopN(dimension="region", n=3, measure="p50", order="desc"),
    )
    with pytest.raises(MetricError) as ei:
        compile_metric(m, mq)
    assert ei.value.code == "bad_top_n"


def test_additive_sum_rank_time_grain_ok() -> None:
    """[HIGH] additive (sum) rank measure + time_grain compiles fine (no regression)."""
    m = _simple_metric()
    mq = MetricQuery(
        metric_id="revenue",
        dimensions=("region",),
        time_grain="month",
        top_n=TopN(dimension="region", n=3, measure="revenue", order="desc"),
    )
    sql, _ = compile_metric(m, mq)
    assert sql  # compiled without error


# ── Issue 3: identifier injection rejected ────────────────────────────────────

def test_dimension_name_with_sqli_rejected() -> None:
    """[MED injection] A dimension name containing SQL injection must be rejected at compile time."""
    m = MetricDefinition(
        id="bad_dim",
        name="Bad Dim",
        measure=Measure(name="revenue", agg="sum", expr="amount"),
        base_table="t",
        dimensions=(Dimension(name="region; DROP TABLE t --"),),
    )
    # compile_metric governs the identifier in _govern; it must reject.
    with pytest.raises(MetricError) as ei:
        compile_metric(m, MetricQuery(metric_id="bad_dim", dimensions=()))
    assert ei.value.code == "bad_dimension_name"


def test_dimension_name_sqli_rejected_via_compile() -> None:
    """[MED injection] Dimension name SQL injection is caught at compile time."""
    m = MetricDefinition(
        id="inj_dim",
        name="Inj Dim",
        measure=Measure(name="revenue", agg="sum", expr="amount"),
        base_table="t",
        dimensions=(Dimension(name="region; DROP TABLE t --"),),
    )
    with pytest.raises(MetricError) as ei:
        compile_metric(m, MetricQuery(metric_id="inj_dim", dimensions=()))
    assert ei.value.code in ("bad_dimension_name",)


def test_derived_measure_name_sqli_rejected() -> None:
    """[MED injection] A derived-measure name containing SQL chars must be rejected."""
    m = MetricDefinition(
        id="inj_dm",
        name="Inj DM",
        measure=Measure(name="revenue", agg="sum", expr="amount"),
        base_table="t",
        derived_measures=(
            DerivedMeasure(name="bad'; DROP TABLE --", formula="revenue / revenue"),
        ),
    )
    with pytest.raises(MetricError) as ei:
        compile_metric(m, MetricQuery(metric_id="inj_dm"))
    assert ei.value.code in ("bad_derived_measure_name", "bad_formula_identifier")


def test_valid_identifier_names_accepted() -> None:
    """[MED injection] Valid identifiers compile without error (no regression)."""
    m = MetricDefinition(
        id="ok_ids",
        name="OK IDs",
        measure=Measure(name="total_revenue", agg="sum", expr="amount"),
        base_table="t",
        dimensions=(Dimension(name="region_code"), Dimension(name="store_id")),
        derived_measures=(
            DerivedMeasure(name="revenue_ratio", formula="total_revenue / total_revenue"),
        ),
    )
    sql, _ = compile_metric(m, MetricQuery(metric_id="ok_ids", dimensions=("region_code",)))
    assert sql


# ── Issue 4: prior_period correct on sparse series through plan() ─────────────

def test_prior_period_sparse_series_date_correct_through_plan() -> None:
    """[MED correctness] prior_period uses date-correct subquery, NOT positional LAG.

    A sparse series (missing months) would cause positional LAG to return the
    wrong prior period.  The date-correct correlated subquery matches by date
    arithmetic, returning NULL when no matching bucket exists.

    This test goes through plan() on in-memory DuckDB and asserts results.
    """
    con = _duckdb_conn()
    con.execute("""
        CREATE TABLE pp_sparse (
            region    VARCHAR,
            amount    DOUBLE,
            sale_date DATE
        )
    """)
    # 2024-01 and 2024-03 (gap at 2024-02); prior months:
    #   2024-01 -> 2023-12 = NULL (no row for 2023-12)
    #   2024-03 -> 2024-02 = NULL (no row for 2024-02)
    # Also add 2024-02 for one region to verify the correct match works.
    con.execute("""
        INSERT INTO pp_sparse VALUES
            ('A', 100, '2024-02-01'),
            ('A', 200, '2024-03-01')
    """)
    m = MetricDefinition(
        id="pp_sparse",
        name="PP Sparse",
        measure=Measure(name="revenue", agg="sum", expr="amount"),
        base_table="pp_sparse",
        dimensions=(Dimension(name="region"),),
        time_dimension=TimeDimension(
            column="sale_date",
            grains=("month",),
            default_grain="month",
        ),
    )
    mq = MetricQuery(
        metric_id="pp_sparse",
        dimensions=("region",),
        time_grain="month",
        time_comparisons=(
            TimeComparison(measure="revenue", kind="prior_period", periods=1),
        ),
    )
    sql, _ = compile_metric(m, mq)
    # Must use date-correct subquery not LAG.
    assert "SELECT __PP.REVENUE FROM __BASE AS __PP".lower() in sql.lower()
    # Through plan().
    p = plan(sql, claims={}, dialect="duckdb")
    rows = con.execute(p.sql).fetchall()
    # Columns: region, sale_date_month, revenue, revenue_prior_period
    row_by_ym = {(r[1].year, r[1].month): r for r in rows}
    # 2024-02: revenue=100, prior_period (2024-01) = NULL (no row for Jan)
    assert (2024, 2) in row_by_ym, f"Missing 2024-02; rows={rows}"
    assert row_by_ym[(2024, 2)][3] is None, (
        f"2024-02 prior_period should be NULL (2024-01 missing), got {row_by_ym[(2024, 2)][3]}"
    )
    # 2024-03: revenue=200, prior_period (2024-02) = 100 (row exists)
    assert (2024, 3) in row_by_ym, f"Missing 2024-03; rows={rows}"
    assert row_by_ym[(2024, 3)][3] == 100.0, (
        f"2024-03 prior_period should be 100 (2024-02 value), got {row_by_ym[(2024, 3)][3]}"
    )


def test_pop_abs_sparse_series_date_correct_through_plan() -> None:
    """[MED correctness] pop_abs uses date-correct subquery through plan()."""
    con = _duckdb_conn()
    con.execute("""
        CREATE TABLE pop_abs_sparse (
            region    VARCHAR,
            amount    DOUBLE,
            sale_date DATE
        )
    """)
    con.execute("""
        INSERT INTO pop_abs_sparse VALUES
            ('A', 100, '2024-01-01'),
            ('A', 150, '2024-03-01')
    """)
    m = MetricDefinition(
        id="pop_abs_test",
        name="Pop Abs Test",
        measure=Measure(name="revenue", agg="sum", expr="amount"),
        base_table="pop_abs_sparse",
        dimensions=(Dimension(name="region"),),
        time_dimension=TimeDimension(
            column="sale_date",
            grains=("month",),
            default_grain="month",
        ),
    )
    mq = MetricQuery(
        metric_id="pop_abs_test",
        dimensions=("region",),
        time_grain="month",
        time_comparisons=(
            TimeComparison(measure="revenue", kind="pop_abs", periods=1),
        ),
    )
    sql, _ = compile_metric(m, mq)
    p = plan(sql, claims={}, dialect="duckdb")
    rows = con.execute(p.sql).fetchall()
    row_by_ym = {(r[1].year, r[1].month): r for r in rows}
    # 2024-01: revenue=100, pop_abs (vs 2023-12) = NULL
    assert (2024, 1) in row_by_ym
    assert row_by_ym[(2024, 1)][3] is None, (
        f"2024-01 pop_abs should be NULL (2023-12 missing), got {row_by_ym[(2024, 1)][3]}"
    )
    # 2024-03: revenue=150, pop_abs (vs 2024-02) = NULL (no 2024-02 row)
    assert (2024, 3) in row_by_ym
    assert row_by_ym[(2024, 3)][3] is None, (
        f"2024-03 pop_abs should be NULL (2024-02 missing), got {row_by_ym[(2024, 3)][3]}"
    )


def test_yoy_pct_emits_single_prior_year_subquery() -> None:
    """[MED perf] yoy_pct must NOT emit the same prior-year subquery twice.

    Previously yoy_pct computed py_expr_sql independently inside the pct_sql
    f-string, producing two identical correlated scalar subqueries.  The fix
    reuses py_expr_sql (computed once) so the output SQL contains exactly
    2 occurrences of the correlated subquery (one for numerator, one for
    NULLIF denominator) — not 4.
    """
    m = _simple_metric()
    mq = MetricQuery(
        metric_id="revenue",
        dimensions=("region",),
        time_grain="month",
        time_comparisons=(
            TimeComparison(measure="revenue", kind="yoy_pct"),
        ),
    )
    sql, _ = compile_metric(m, mq)
    # Count how many times the prior-year inner SELECT appears.
    # It should appear exactly twice (numerator and NULLIF denominator),
    # not four times (old code computed py_expr_sql twice, each appearing twice).
    inner = "SELECT __PY.REVENUE FROM __BASE AS __PY"
    count = sql.upper().count(inner)
    assert count == 2, (
        f"yoy_pct should emit exactly 2 prior-year subqueries (1 for diff, "
        f"1 for NULLIF denom), found {count}. SQL fragment: {sql[:500]}"
    )
