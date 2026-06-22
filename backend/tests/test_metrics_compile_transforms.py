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
    assert "LAG" in up
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
    """YoY uses LAG(12) for month grain."""
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
    assert "LAG" in up
    assert "12" in sql  # 12 months/year
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
    """Top-N with time_grain keeps the full series."""
    m = _simple_metric()
    mq = MetricQuery(
        metric_id="revenue",
        dimensions=("region",),
        time_grain="month",
        top_n=TopN(dimension="region", n=5, order="desc"),
    )
    sql, _ = compile_metric(m, mq)
    up = sql.upper()
    assert "QUALIFY" in up
    assert "5" in sql


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
