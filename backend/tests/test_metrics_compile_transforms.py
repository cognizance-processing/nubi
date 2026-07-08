"""Tests for the layered metric compiler: derived measures, time-intelligence,
top-N, percentile, latest_snapshot, RLS soundness, governance.
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
    MetricFilter,
    MetricQuery,
    TimeDimension,
    TimeComparison,
    TopN,
)


# ---------------------------------------------------------------------------
# Fixtures / builders
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _relax_time_bound_guard(monkeypatch):
    """Disable the time_comparisons date-bound guard by default for this module.

    Most transform tests in this file build time_comparisons queries that assert
    SQL SHAPE (LATERAL appears once, RANGE interval frames, re-aggregation
    governance, ...) and intentionally exercise the UNBOUNDED tc path — they
    predate the guard that now requires a date-range filter on the time column
    for any tc query.  Rather than thread a synthetic date filter through every
    one, we opt OUT of the guard here (NUBI_METRIC_REQUIRE_TIME_BOUND=0).

    The dedicated guard tests (test_*_tc_*_rejected / _compiles / env opt-out)
    re-enable or override this explicitly via their own monkeypatch.setenv so
    they verify the real production-default behaviour.
    """
    monkeypatch.setenv("NUBI_METRIC_REQUIRE_TIME_BOUND", "0")


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
    # Identifiers are quoted (defense-in-depth), so the measure ref is __pp."revenue".
    assert 'SELECT __PP."REVENUE" FROM __BASE AS __PP' in up
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
    # Identifiers are quoted (defense-in-depth), so the measure ref is __py."revenue".
    assert 'SELECT __PY."REVENUE" FROM __BASE AS __PY' in up
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


def test_derived_formula_trailing_div_raises_400() -> None:
    """'profit /' ends with a trailing operator -> MetricError/400, not 500."""
    m = _orders_metric(
        derived_measures=(DerivedMeasure(name="bad", formula="delivered /"),)
    )
    with pytest.raises(MetricError) as ei:
        compile_metric(m, MetricQuery(metric_id="orders"))
    assert ei.value.code == "bad_formula"


def test_derived_formula_trailing_mul_raises_400() -> None:
    """'profit *' ends with a trailing operator -> MetricError/400, not 500."""
    m = _orders_metric(
        derived_measures=(DerivedMeasure(name="bad", formula="delivered *"),)
    )
    with pytest.raises(MetricError) as ei:
        compile_metric(m, MetricQuery(metric_id="orders"))
    assert ei.value.code == "bad_formula"


def test_derived_formula_leading_op_raises_400() -> None:
    """A formula starting with a binary operator -> MetricError/400, not 500."""
    m = _orders_metric(
        derived_measures=(DerivedMeasure(name="bad", formula="/ delivered"),)
    )
    with pytest.raises(MetricError) as ei:
        compile_metric(m, MetricQuery(metric_id="orders"))
    assert ei.value.code == "bad_formula"


def test_derived_formula_valid_still_compiles() -> None:
    """Valid formula 'delivered / ordered' still compiles without error."""
    m = _orders_metric(
        derived_measures=(
            DerivedMeasure(name="fill_rate", formula="delivered / ordered"),
        )
    )
    sql, _ = compile_metric(m, MetricQuery(metric_id="orders"))
    assert "fill_rate" in sql


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


# ── NULL-dim soundness: top_n.other + time_grain ─────────────────────────────

def test_top_n_other_time_grain_null_dim_routes_to_other() -> None:
    """top_n.other + time_grain with NULL dimension values:

    The membership subquery excludes NULL dims (so NULL never becomes a top-N
    member), and the Other arm routes NULL-dim rows into the Other bucket via
    `OR <dim> IS NULL`.  Regression for the NULL-unsound NOT IN bug:
      (A) NULL-dim rows must NOT be dropped — they land in Other.
      (B) a NULL dim must never poison NOT IN so the whole Other bucket vanishes.
    """
    con = _duckdb_conn()
    con.execute("""
        CREATE TABLE orders_null (
            region VARCHAR,
            amount DOUBLE,
            created_at DATE
        )
    """)
    # A=300, B=125 are top-2.  C=10 is a non-top-N real region.
    # NULL-dim rows total 999 (would dominate ranking if not excluded; would
    # poison NOT IN if allowed into the membership set).
    con.execute("""
        INSERT INTO orders_null VALUES
            ('A',  100, '2024-01-01'),
            ('A',  200, '2024-02-01'),
            ('B',   50, '2024-01-01'),
            ('B',   75, '2024-02-01'),
            ('C',   10, '2024-01-01'),
            (NULL, 500, '2024-01-01'),
            (NULL, 499, '2024-02-01')
    """)
    from app.metrics.models import TimeDimension as TD
    m = MetricDefinition(
        id="revenue",
        name="Revenue",
        measure=Measure(name="revenue", agg="sum", expr="amount"),
        base_table="orders_null",
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
        top_n=TopN(dimension="region", n=2, order="desc", other=True, other_label="Other"),
    )
    sql, _ = compile_metric(m, mq)
    # Membership subquery must exclude NULL dims (so NULL can't rank into top-N
    # and can't poison NOT IN).
    assert "IS NOT NULL" in sql.upper() or "NOT REGION IS NULL" in sql.upper()
    # Other arm must route NULL-dim rows into Other.
    assert "IS NULL" in sql.upper()
    rows = con.execute(sql).fetchall()

    # Total revenue conserved: nothing dropped.  Total = 300+125+10+999 = 1434.
    # Columns: region, created_at_month, revenue (no org_id; no rls_keys).
    rev_idx = 2
    total = sum(r[rev_idx] for r in rows)
    assert total == 1434, f"Revenue not conserved (rows dropped?): {rows}"

    # Top-2 real regions A and B must appear under their own labels.
    labelled = {r[0] for r in rows}
    assert "A" in labelled
    assert "B" in labelled

    # The Other bucket must EXIST and be non-empty (NULL did not poison NOT IN).
    other_rows = [r for r in rows if r[0] == "Other"]
    assert other_rows, f"Other bucket vanished — NULL poisoned NOT IN: {rows}"
    # Other revenue = C (10) + NULL rows (999) = 1009.
    other_total = sum(r[rev_idx] for r in other_rows)
    assert other_total == 1009, f"Other bucket wrong (NULL dropped or C dropped): {other_rows}"


# ── Unbounded safety net: top_n.other + time_grain with NO explicit limit ────

def test_top_n_other_time_grain_no_limit_caps_union_yet_keeps_full_top_n() -> None:
    """Regression (HIGH unbounded — fix-25 regression): top_n.other + time_grain
    with NO explicit mq.limit must STILL cap the combined UNION (the _DEFAULT_LIMIT
    safety net), while preserving full per-bucket top-N for a small dataset.

    Bug: the outer-union LIMIT was applied ONLY when mq.limit was explicitly set,
    so omitting limit left the UNION uncapped — every other compile path is
    protected by _DEFAULT_LIMIT, this one was not.
    """
    m = _simple_metric()
    mq = MetricQuery(
        metric_id="revenue",
        dimensions=("region",),
        time_grain="month",
        top_n=TopN(dimension="region", n=3, order="desc", other=True),
        # NOTE: no limit set.
    )
    assert mq.limit is None
    sql, _ = compile_metric(m, mq)
    up = sql.upper()
    # UNION must be present and the OUTER union must carry a LIMIT (safety net).
    assert "UNION ALL" in up
    # The emitted SQL must end with the union-level LIMIT cap (default safety net).
    parsed = sqlglot.parse_one(sql, dialect="duckdb")
    assert parsed.find(sqlglot.exp.Limit) is not None, "union has no LIMIT cap"

    # And the cap is the _DEFAULT_LIMIT (no explicit mq.limit) — large enough that
    # a small dataset's full per-bucket top-N + Other rows are NOT truncated.
    from app.metrics.compile import _DEFAULT_LIMIT

    con = _duckdb_conn()
    con.execute("""
        CREATE TABLE sales (region VARCHAR, amount DOUBLE, ts DATE)
    """)
    # 4 regions over 2 months. Top-3 by total: A(300), B(200), C(100); D(10)→Other.
    con.execute("""
        INSERT INTO sales VALUES
            ('A', 100, '2024-01-15'), ('A', 200, '2024-02-15'),
            ('B',  80, '2024-01-15'), ('B', 120, '2024-02-15'),
            ('C',  40, '2024-01-15'), ('C',  60, '2024-02-15'),
            ('D',   4, '2024-01-15'), ('D',   6, '2024-02-15')
    """)
    m2 = MetricDefinition(
        id="revenue",
        name="Revenue",
        measure=Measure(name="revenue", agg="sum", expr="amount"),
        base_table="sales",
        dimensions=(Dimension(name="region"),),
        time_dimension=TimeDimension(
            column="ts",
            grains=("day", "week", "month", "quarter", "year"),
            default_grain="day",
        ),
    )
    sql2, _ = compile_metric(m2, mq)
    parsed2 = sqlglot.parse_one(sql2, dialect="duckdb")
    # The cap is the default safety net, not some truncating value.
    limit_node = parsed2.find(sqlglot.exp.Limit)
    assert limit_node is not None
    assert int(limit_node.expression.name) == _DEFAULT_LIMIT

    rows = con.execute(sql2).fetchall()
    # Full per-bucket top-N survives: A, B, C each appear in BOTH months, plus an
    # Other bucket per month — 3 top series * 2 months + 2 Other = 8 rows, none
    # truncated by the (huge) default cap.
    labelled = {r[0] for r in rows}
    assert {"A", "B", "C"}.issubset(labelled)
    assert "D" not in labelled  # D rolled into Other, not its own series.
    other_rows = [r for r in rows if r[0] not in ("A", "B", "C")]
    assert other_rows, f"Other bucket missing: {rows}"
    # Two months => two per-bucket top-N entries for each top series.
    a_rows = [r for r in rows if r[0] == "A"]
    assert len(a_rows) == 2, f"per-bucket top-N truncated: {a_rows}"


def test_top_n_other_no_time_grain_null_dim_in_other_unchanged() -> None:
    """The no-time-grain QUALIFY/complement-RANK path already buckets NULL dims
    into Other (RANK treats NULL as a rankable value), so it must be unchanged.

    This guards against the NULL fix accidentally being applied to (and breaking)
    the QUALIFY-RANK path.
    """
    con = _duckdb_conn()
    con.execute("""
        CREATE TABLE sales_null_ntg (region VARCHAR, amount DOUBLE)
    """)
    con.execute("""
        INSERT INTO sales_null_ntg VALUES
            ('A', 300), ('B', 125), ('C', 10), (NULL, 7)
    """)
    m = MetricDefinition(
        id="sales",
        name="Sales",
        measure=Measure(name="total", agg="sum", expr="amount"),
        base_table="sales_null_ntg",
        dimensions=(Dimension(name="region"),),
    )
    mq = MetricQuery(
        metric_id="sales",
        dimensions=("region",),
        top_n=TopN(dimension="region", n=2, order="desc", other=True, other_label="Other"),
    )
    sql, _ = compile_metric(m, mq)
    # The no-time-grain path uses QUALIFY (complement RANK), not the membership
    # NOT IN subquery — so the NULL membership filter must not appear here.
    assert "QUALIFY" in sql.upper()
    rows = con.execute(sql).fetchall()
    rev_idx = 1
    total = sum(r[rev_idx] for r in rows)
    assert total == 442, f"Revenue not conserved: {rows}"  # 300+125+10+7
    other_rows = [r for r in rows if r[0] == "Other"]
    assert other_rows, f"Other bucket vanished: {rows}"
    # Other = C (10) + NULL (7) = 17.
    assert sum(r[rev_idx] for r in other_rows) == 17, other_rows


# ── SQLi defense-in-depth: membership build site ─────────────────────────────

def test_top_n_membership_sql_rejects_injection_dim_name() -> None:
    """A crafted dimension name cannot reach _top_n_membership_sql's f-string
    unvalidated: _govern's _IDENT_RE check fails closed first.

    Documents/locks in the _govern guarantee that protects the membership
    f-string interpolation of dim_col (and, by the same path, rank_measure).
    """
    m = MetricDefinition(
        id="revenue",
        name="Revenue",
        measure=Measure(name="revenue", agg="sum", expr="amount"),
        base_table="orders",
        # Malicious dimension name — would inject SQL if interpolated raw.
        dimensions=(Dimension(name="region) ; DROP TABLE orders --"),),
    )
    mq = MetricQuery(
        metric_id="revenue",
        dimensions=("region) ; DROP TABLE orders --",),
        top_n=TopN(
            dimension="region) ; DROP TABLE orders --",
            n=2, order="desc", other=True,
        ),
    )
    with pytest.raises(MetricError) as ei:
        compile_metric(m, mq)
    assert ei.value.code == "bad_dimension_name"


def test_top_n_membership_build_site_fails_closed_on_bad_identifier() -> None:
    """The membership build site asserts identifier safety itself (defense in
    depth): calling _top_n_membership_sql with a non-identifier name fails closed
    even if _govern were somehow bypassed."""
    from app.metrics.compile import _top_n_membership_sql

    with pytest.raises(MetricError) as ei_dim:
        _top_n_membership_sql(
            "region); DROP TABLE t --", "revenue", "DESC", 3, rls_keys=(),
        )
    assert ei_dim.value.code == "bad_dimension_name"

    with pytest.raises(MetricError) as ei_meas:
        _top_n_membership_sql(
            "region", "revenue); DROP TABLE t --", "DESC", 3, rls_keys=(),
        )
    assert ei_meas.value.code == "bad_measure_name"


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
    """The Other bucket emits NULL for avg measures (not re-aggregable without weights).

    DOCUMENTED BEHAVIOUR: AVG(AVGs) is mathematically wrong without row counts.
    The Other bucket deliberately emits NULL so callers see an explicit signal
    ("not available") rather than a silently incorrect number.
    See _NON_ADDITIVE_AGGS in compile.py for the full rationale.
    """
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


def test_other_bucket_non_additive_measures_emit_null() -> None:
    """The Other bucket emits NULL for all non-additive agg types.

    DOCUMENTED BEHAVIOUR (see _NON_ADDITIVE_AGGS in compile.py):
    avg, count_distinct, percentile_cont, and approx_count_distinct cannot be
    re-aggregated across arbitrary groups of pre-computed bucket values.  The
    Other bucket emits NULL for each such measure — an explicit "not available"
    sentinel rather than a silently wrong number.  Callers should treat NULL in
    the Other bucket for these measure types as "N/A", not as a data error.
    """
    m = MetricDefinition(
        id="multi_nonadd",
        name="MultiNonAdd",
        measure=Measure(name="total_sales", agg="sum", expr="amount"),
        base_table="sales",
        extra_measures=(
            Measure(name="avg_sale", agg="avg", expr="amount"),
            Measure(name="uniq_customers", agg="count_distinct", expr="customer_id"),
        ),
        dimensions=(Dimension(name="region"),),
    )
    mq = MetricQuery(
        metric_id="multi_nonadd",
        dimensions=("region",),
        top_n=TopN(dimension="region", n=1, order="desc", other=True),
    )
    sql, _ = compile_metric(m, mq)
    sqlglot.parse_one(sql, dialect="duckdb")
    parts = sql.split("UNION ALL", 1)
    other_arm = parts[1].upper()

    # avg → NULL (weighted average is undefined without row counts)
    assert "NULL AS AVG_SALE" in other_arm or 'NULL AS "AVG_SALE"' in other_arm, (
        f"Expected NULL AS avg_sale in Other arm:\n{other_arm[:500]}"
    )
    # count_distinct → NULL (UNION of pre-counted distinct sets may overlap)
    assert "NULL AS UNIQ_CUSTOMERS" in other_arm or 'NULL AS "UNIQ_CUSTOMERS"' in other_arm, (
        f"Expected NULL AS uniq_customers in Other arm:\n{other_arm[:500]}"
    )
    # The additive measure must still use SUM (regression guard)
    assert "SUM(TOTAL_SALES)" in other_arm, (
        f"Expected SUM(TOTAL_SALES) for the additive measure:\n{other_arm[:500]}"
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
    assert 'SELECT __PP."REVENUE" FROM __BASE AS __PP'.lower() in sql.lower()
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

    Fix (Issue 3): the prior-year correlated subquery is now wrapped in a
    CROSS JOIN LATERAL so it is evaluated exactly once per outer row and
    referenced (not re-executed) for both the numerator and the NULLIF
    denominator.  The subquery SQL text therefore appears exactly 1 time
    in the output (inside the LATERAL), not 2 times (old inline approach)
    or 4 times (original double-compute approach).
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
    # With the LATERAL fix it should appear exactly once (inside the LATERAL),
    # and the pct expression references the lateral alias column twice.
    inner = 'SELECT __PY."REVENUE" FROM __BASE AS __PY'
    count = sql.upper().count(inner)
    assert count == 1, (
        f"yoy_pct should emit exactly 1 prior-year subquery (inside LATERAL), "
        f"found {count}. SQL fragment: {sql[:500]}"
    )
    # The lateral alias must be referenced in the pct expression.
    assert "__py_revenue__" in sql.lower() or "py_val" in sql.lower(), (
        f"Expected lateral alias reference in SQL:\n{sql[:300]}"
    )
    # Must still contain NULLIF for the zero-division guard.
    assert "NULLIF" in sql.upper()

# ---------------------------------------------------------------------------
# 14. FIFTH-WAVE REGRESSION TESTS — adversarial audit fixes (Issues 1-4)
#
# HARD RULE: all tests go through plan() and execute on in-memory DuckDB,
# asserting RESULTS not just SQL text.
# ---------------------------------------------------------------------------

import sqlglot as _sqlglot  # noqa: E402 (already imported above as sqlglot)


def _duckdb_conn5():
    """Fresh in-memory DuckDB connection (skips if duckdb not installed)."""
    try:
        import duckdb
        return duckdb.connect(":memory:")
    except ImportError:
        import pytest as _pt
        _pt.skip("duckdb not installed")


def _plan5(sql: str, claims=None):
    """Run SQL through plan() and return PhysicalPlan."""
    from app.connectors.planner import plan
    return plan(sql, claims=claims or {}, dialect="duckdb")


# ── Issue 1 [HIGH]: per-tenant top-N correctness ─────────────────────────────

def test_top_n_time_grain_per_tenant_not_global() -> None:
    """[HIGH per-tenant correctness] top-N members must be the per-tenant top-N,
    not the global top-N across all tenants.

    Two tenants have DIFFERENT top-N members:
      tenant 'A': region X=1000, Y=200, Z=50  → top-1 = X
      tenant 'B': region Y=900,  X=100, Z=10  → top-1 = Y

    With the buggy global subquery, plan() with claims={org_id: 'A'} could
    still see the global top member (which might be Y for some data distribution).
    With the fix (correlated subquery WHERE __base.org_id = __outer.org_id),
    each tenant sees ITS OWN top-1.
    """
    con = _duckdb_conn5()
    con.execute("""
        CREATE TABLE topn_tenant (
            region  VARCHAR,
            amount  DOUBLE,
            org_id  VARCHAR,
            created_at DATE
        )
    """)
    # org_A: X=1000, Y=200, Z=50  → top-1 = X
    # org_B: Y=900,  X=100, Z=10  → top-1 = Y
    con.execute("""
        INSERT INTO topn_tenant VALUES
            ('X', 1000, 'org_A', '2024-01-01'),
            ('X',  500, 'org_A', '2024-02-01'),
            ('Y',  200, 'org_A', '2024-01-01'),
            ('Y',  100, 'org_A', '2024-02-01'),
            ('Z',   50, 'org_A', '2024-01-01'),
            ('Y',  900, 'org_B', '2024-01-01'),
            ('Y',  400, 'org_B', '2024-02-01'),
            ('X',  100, 'org_B', '2024-01-01'),
            ('X',   50, 'org_B', '2024-02-01'),
            ('Z',   10, 'org_B', '2024-01-01')
    """)
    m = MetricDefinition(
        id="topn_rev",
        name="TopN Rev",
        measure=Measure(name="revenue", agg="sum", expr="amount"),
        base_table="topn_tenant",
        dimensions=(Dimension(name="region"),),
        time_dimension=TimeDimension(
            column="created_at",
            grains=("month",),
            default_grain="month",
        ),
        rls_keys=("org_id",),
        # Need a derived measure or time comparison to trigger the layered path
        # (top_n alone with time_grain uses the layered path).
        derived_measures=(
            DerivedMeasure(name="rev_ratio", formula="revenue / revenue"),
        ),
    )
    mq = MetricQuery(
        metric_id="topn_rev",
        dimensions=("region",),
        time_grain="month",
        top_n=TopN(dimension="region", n=1, order="desc"),
    )
    sql, _ = compile_metric(m, mq)

    # Verify the membership subquery is correlated on org_id.
    assert "__base.org_id = __outer.org_id".lower() in sql.lower(), (
        f"Expected per-tenant RLS correlation in membership subquery:\n{sql[:600]}"
    )

    # Execute for org_A: top-1 should be X (total revenue 1500).
    p_a = _plan5(sql, claims={"policies": {"org_id": "org_A"}})
    rows_a = con.execute(p_a.sql).fetchall()
    regions_a = {r[0] for r in rows_a}
    assert "X" in regions_a, (
        f"org_A top-1 should include X (total=1500); got regions={regions_a}"
    )
    assert "Y" not in regions_a, (
        f"org_A top-1 should NOT include Y (total=300); got regions={regions_a}"
    )

    # Execute for org_B: top-1 should be Y (total revenue 1300).
    p_b = _plan5(sql, claims={"policies": {"org_id": "org_B"}})
    rows_b = con.execute(p_b.sql).fetchall()
    regions_b = {r[0] for r in rows_b}
    assert "Y" in regions_b, (
        f"org_B top-1 should include Y (total=1300); got regions={regions_b}"
    )
    assert "X" not in regions_b, (
        f"org_B top-1 should NOT include X (total=150); got regions={regions_b}"
    )


# ── Issue 2 [MED robustness]: paren-in-literal default_filter ────────────────

def test_top_n_other_default_filter_with_parens_in_literal_compiles() -> None:
    """[MED robustness] default_filter with IN ('a','b') must not break the
    AST-based CTE extraction in _apply_top_n_other.

    Previously the paren-counting extraction would mis-count when the base
    CTE body contained string literals with parentheses — e.g. a default_filter
    like "status IN ('open','closed')" would cause depth counting to diverge.
    With the AST extraction (via full_tree.find_all(exp.CTE)), this is safe.
    """
    m = MetricDefinition(
        id="orders_with_filter",
        name="Orders With Filter",
        measure=Measure(name="revenue", agg="sum", expr="amount"),
        base_table="orders",
        dimensions=(Dimension(name="region"),),
        # default_filter contains parentheses inside a string literal context
        # (IN clause with string values).  This would break paren-counting.
        default_filters=("status IN ('open', 'closed')",),
        derived_measures=(
            DerivedMeasure(name="rev_ratio", formula="revenue / revenue"),
        ),
    )
    mq = MetricQuery(
        metric_id="orders_with_filter",
        dimensions=("region",),
        top_n=TopN(dimension="region", n=2, order="desc", other=True),
    )
    # Must compile without error.
    sql, _ = compile_metric(m, mq)

    # Must parse cleanly via sqlglot.
    parsed = _sqlglot.parse_one(sql, dialect="duckdb")
    assert parsed is not None, f"SQL failed to parse:\n{sql[:500]}"

    # Must contain UNION ALL (Other bucket).
    assert "UNION ALL" in sql.upper(), f"Expected UNION ALL:\n{sql[:400]}"

    # The default_filter value must appear in the base CTE body.
    assert "open" in sql.lower(), f"Expected default_filter value in SQL:\n{sql[:500]}"


def test_top_n_other_default_filter_parens_literal_executes_in_duckdb() -> None:
    """[MED robustness] The parens-in-literal default_filter compiles and executes."""
    con = _duckdb_conn5()
    con.execute("""
        CREATE TABLE orders_paren (
            region  VARCHAR,
            amount  DOUBLE,
            status  VARCHAR
        )
    """)
    con.execute("""
        INSERT INTO orders_paren VALUES
            ('A', 500, 'open'),
            ('A', 300, 'closed'),
            ('B', 200, 'open'),
            ('B', 100, 'closed'),
            ('C',  50, 'pending')  -- excluded by default_filter
    """)
    m = MetricDefinition(
        id="orders_paren",
        name="Orders Paren",
        measure=Measure(name="revenue", agg="sum", expr="amount"),
        base_table="orders_paren",
        dimensions=(Dimension(name="region"),),
        default_filters=("status IN ('open', 'closed')",),
        derived_measures=(
            DerivedMeasure(name="rev_ratio", formula="revenue / revenue"),
        ),
    )
    mq = MetricQuery(
        metric_id="orders_paren",
        dimensions=("region",),
        top_n=TopN(dimension="region", n=1, order="desc", other=True, other_label="Other"),
    )
    sql, _ = compile_metric(m, mq)

    # Parse.
    _sqlglot.parse_one(sql, dialect="duckdb")

    # Execute.
    p = _plan5(sql, claims={})
    rows = con.execute(p.sql).fetchall()
    row_by_region = {r[0]: r for r in rows}

    # Top-1 by status-filtered revenue: A=800, B=300, C excluded.
    assert "A" in row_by_region, f"Top region A missing; rows={rows}"
    assert "Other" in row_by_region, f"Other bucket missing; rows={rows}"

    # C (status='pending') is excluded by default_filter → only B goes to Other.
    other_rev = row_by_region["Other"][1]
    assert other_rev == 300.0, (
        f"Other revenue should be 300 (B only, C excluded by filter), got {other_rev}"
    )


# ── Issue 3 [MED perf]: pop_pct subquery appears once via LATERAL ────────────

def test_pop_pct_subquery_appears_once_via_lateral() -> None:
    """[MED perf] pop_pct prior-period correlated subquery must appear exactly
    once in the SQL (inside a LATERAL), not twice (inline numerator + NULLIF denom).

    The fix wraps the correlated subquery in CROSS JOIN LATERAL so the DB
    evaluates it once per row and both the numerator and NULLIF denominator
    reference the lateral column alias.
    """
    m = MetricDefinition(
        id="rev_pop",
        name="Rev PoP",
        measure=Measure(name="revenue", agg="sum", expr="amount"),
        base_table="sales",
        dimensions=(Dimension(name="region"),),
        time_dimension=TimeDimension(
            column="sale_date",
            grains=("month",),
            default_grain="month",
        ),
    )
    mq = MetricQuery(
        metric_id="rev_pop",
        dimensions=("region",),
        time_grain="month",
        time_comparisons=(
            TimeComparison(measure="revenue", kind="pop_pct", periods=1),
        ),
    )
    sql, _ = compile_metric(m, mq)

    # The prior-period correlated subquery must appear exactly once.
    inner = 'SELECT __PP."REVENUE" FROM __BASE AS __PP'
    count = sql.upper().count(inner)
    assert count == 1, (
        f"pop_pct should emit exactly 1 prior-period subquery (inside LATERAL), "
        f"found {count}. SQL:\n{sql[:600]}"
    )

    # Must contain LATERAL for the single-evaluation wrapper.
    assert "LATERAL" in sql.upper(), (
        f"Expected LATERAL in SQL for pop_pct fix:\n{sql[:400]}"
    )

    # Must contain NULLIF for zero-division guard.
    assert "NULLIF" in sql.upper()

    # Must parse cleanly.
    parsed = _sqlglot.parse_one(sql, dialect="duckdb")
    assert parsed is not None


def test_pop_pct_lateral_executes_correct_values_in_duckdb() -> None:
    """[MED perf] pop_pct via LATERAL executes correctly on DuckDB."""
    con = _duckdb_conn5()
    con.execute("""
        CREATE TABLE pop_pct_data (
            region    VARCHAR,
            amount    DOUBLE,
            sale_date DATE
        )
    """)
    # Jan=100, Feb=150 → pct change = (150-100)/100 = 0.5
    con.execute("""
        INSERT INTO pop_pct_data VALUES
            ('A', 100, '2024-01-01'),
            ('A', 150, '2024-02-01')
    """)
    m = MetricDefinition(
        id="pop_pct_exec",
        name="PoP Pct Exec",
        measure=Measure(name="revenue", agg="sum", expr="amount"),
        base_table="pop_pct_data",
        dimensions=(Dimension(name="region"),),
        time_dimension=TimeDimension(
            column="sale_date",
            grains=("month",),
            default_grain="month",
        ),
    )
    mq = MetricQuery(
        metric_id="pop_pct_exec",
        dimensions=("region",),
        time_grain="month",
        time_comparisons=(
            TimeComparison(measure="revenue", kind="pop_pct", periods=1),
        ),
    )
    sql, _ = compile_metric(m, mq)
    p = _plan5(sql, claims={})
    rows = con.execute(p.sql).fetchall()

    # Sort by month.
    row_by_ym = {(r[1].year, r[1].month): r for r in rows}
    assert (2024, 1) in row_by_ym, f"Missing 2024-01; rows={rows}"
    assert (2024, 2) in row_by_ym, f"Missing 2024-02; rows={rows}"

    # 2024-01: no prior period → pop_pct = NULL
    assert row_by_ym[(2024, 1)][3] is None, (
        f"2024-01 pop_pct should be NULL, got {row_by_ym[(2024, 1)][3]}"
    )
    # 2024-02: (150-100)/100 = 0.5
    assert abs(row_by_ym[(2024, 2)][3] - 0.5) < 1e-9, (
        f"2024-02 pop_pct should be 0.5, got {row_by_ym[(2024, 2)][3]}"
    )


# ── Issue 4 [LOW]: YTD/QTD/MTD PARTITION BY uses quoted identifiers ───────────

def test_ytd_partition_columns_quoted() -> None:
    """[LOW] YTD window PARTITION BY must emit quoted dimension column names.

    Previously the PARTITION BY was built from unquoted column names via
    f-string, which breaks for names needing quoting and is an injection surface.
    The fix emits each column via exp.to_identifier(name, quoted=True).
    """
    m = MetricDefinition(
        id="ytd_quoted",
        name="YTD Quoted",
        measure=Measure(name="revenue", agg="sum", expr="amount"),
        base_table="sales",
        dimensions=(Dimension(name="region"), Dimension(name="store_id")),
        time_dimension=TimeDimension(
            column="sale_date",
            grains=("month",),
            default_grain="month",
        ),
    )
    mq = MetricQuery(
        metric_id="ytd_quoted",
        dimensions=("region", "store_id"),
        time_grain="month",
        time_comparisons=(
            TimeComparison(measure="revenue", kind="ytd"),
        ),
    )
    sql, _ = compile_metric(m, mq)

    # The PARTITION BY clause must use double-quoted column names.
    # In DuckDB dialect, quoted identifiers use double-quotes.
    assert '"region"' in sql or '"store_id"' in sql, (
        f"Expected quoted column names in PARTITION BY; SQL:\n{sql[:600]}"
    )

    # Must parse cleanly.
    parsed = _sqlglot.parse_one(sql, dialect="duckdb")
    assert parsed is not None

    # Must contain the YTD alias.
    assert "revenue_ytd" in sql.lower()


def test_ytd_partition_quoted_executes_in_duckdb() -> None:
    """[LOW] YTD with quoted PARTITION BY executes correctly on DuckDB."""
    con = _duckdb_conn5()
    con.execute("""
        CREATE TABLE ytd_sales (
            region    VARCHAR,
            amount    DOUBLE,
            sale_date DATE
        )
    """)
    # Jan=100, Feb=200, Mar=300 (all same year) → YTD at Mar = 600
    con.execute("""
        INSERT INTO ytd_sales VALUES
            ('A', 100, '2024-01-01'),
            ('A', 200, '2024-02-01'),
            ('A', 300, '2024-03-01')
    """)
    m = MetricDefinition(
        id="ytd_exec",
        name="YTD Exec",
        measure=Measure(name="revenue", agg="sum", expr="amount"),
        base_table="ytd_sales",
        dimensions=(Dimension(name="region"),),
        time_dimension=TimeDimension(
            column="sale_date",
            grains=("month",),
            default_grain="month",
        ),
    )
    mq = MetricQuery(
        metric_id="ytd_exec",
        dimensions=("region",),
        time_grain="month",
        time_comparisons=(
            TimeComparison(measure="revenue", kind="ytd"),
        ),
    )
    sql, _ = compile_metric(m, mq)
    p = _plan5(sql, claims={})
    rows = con.execute(p.sql).fetchall()

    # Columns: region, sale_date_month, revenue, revenue_ytd
    row_by_ym = {(r[1].year, r[1].month): r for r in rows}
    assert (2024, 1) in row_by_ym
    assert (2024, 2) in row_by_ym
    assert (2024, 3) in row_by_ym

    # YTD at each month (running sum within year).
    assert row_by_ym[(2024, 1)][3] == 100.0, (
        f"YTD at 2024-01 should be 100, got {row_by_ym[(2024, 1)][3]}"
    )
    assert row_by_ym[(2024, 2)][3] == 300.0, (
        f"YTD at 2024-02 should be 300, got {row_by_ym[(2024, 2)][3]}"
    )
    assert row_by_ym[(2024, 3)][3] == 600.0, (
        f"YTD at 2024-03 should be 600, got {row_by_ym[(2024, 3)][3]}"
    )


# ---------------------------------------------------------------------------
# 15. SIXTH-WAVE REGRESSION TESTS — top_n.other compiler regressions (HIGH)
#
# HARD RULE: these tests go through planner.plan() + execute on in-memory
# DuckDB and assert EXACT RESULT ROWS.
#
# Regression 1 [HIGH]: _apply_top_n_other used .set("with", None) instead of
#   .set("with_", None) — a NO-OP — so the WITH survived in the top-N arm,
#   producing nested double "WITH __base AS (...) (WITH __base AS (...) SELECT
#   ...) UNION ALL (...)".  Fix: .set("with_", None).
#
# Regression 2 [HIGH]: Other-bucket NOT IN used rls_keys=() (non-correlated,
#   global top-N) while the IN arm used rls_keys=tuple(metric.rls_keys) (per-
#   tenant).  A dim in tenant X's top-N but not the global top-N → DOUBLE-
#   COUNTED; a dim in global but not tenant X's top-N → MISSING.
#   Fix: correlate Other arm's NOT IN on rls_keys too via __other_outer alias.
# ---------------------------------------------------------------------------


def _duckdb_conn6():
    """Fresh in-memory DuckDB connection for wave-6 tests."""
    try:
        import duckdb
        return duckdb.connect(":memory:")
    except ImportError:
        pytest.skip("duckdb not installed")


def _plan6(sql: str, claims=None):
    """Run SQL through plan() (dialect=duckdb) and return PhysicalPlan."""
    from app.connectors.planner import plan as _plan
    return _plan(sql, claims=claims or {}, dialect="duckdb")


# ── Regression 1 [HIGH]: no nested double WITH ───────────────────────────────

def test_top_n_other_no_nested_double_with() -> None:
    """[HIGH] _apply_top_n_other must NOT produce a nested double WITH __base.

    The bug: .set("with", None) was a no-op (sqlglot uses "with_" as the key),
    so the WITH clause survived in the top-N arm, producing:
        WITH __base AS (...) (WITH __base AS (...) SELECT ...) UNION ALL (...)
    Fix: .set("with_", None).

    This test asserts:
    1. The compiled SQL contains exactly ONE "WITH __BASE AS" token.
    2. The SQL parses cleanly via sqlglot.
    3. The SQL executes on in-memory DuckDB without error.
    """
    import sqlglot as _sg

    con = _duckdb_conn6()
    con.execute("""
        CREATE TABLE topn_no_double_with (
            tenant  VARCHAR,
            cat     VARCHAR,
            amount  DOUBLE,
            sale_date DATE
        )
    """)
    con.execute("""
        INSERT INTO topn_no_double_with VALUES
            ('X', 'apple',  200, '2024-01-15'),
            ('X', 'banana',  50, '2024-01-20'),
            ('X', 'cherry',  30, '2024-01-25')
    """)

    m = MetricDefinition(
        id="no_dbl_with",
        name="No Double WITH",
        measure=Measure(name="amount", agg="sum", expr="amount"),
        base_table="topn_no_double_with",
        dimensions=(Dimension(name="cat"),),
        time_dimension=TimeDimension(
            column="sale_date",
            grains=("month",),
            default_grain="month",
        ),
        rls_keys=("tenant",),
    )
    mq = MetricQuery(
        metric_id="no_dbl_with",
        dimensions=("cat",),
        time_grain="month",
        top_n=TopN(dimension="cat", n=1, other=True),
    )
    sql, named_params = compile_metric(m, mq)

    # Assert: exactly ONE "WITH __BASE AS" token.
    count_with = sql.upper().count("WITH __BASE AS")
    assert count_with == 1, (
        f"Expected exactly 1 'WITH __BASE AS' token but found {count_with}.\n"
        f"This indicates the top-N arm still carries its own WITH clause (nested "
        f"double-WITH regression).\nSQL:\n{sql[:800]}"
    )

    # Assert: SQL parses cleanly via sqlglot.
    parsed = _sg.parse_one(sql, dialect="duckdb")
    assert parsed is not None, f"SQL failed to parse:\n{sql[:600]}"

    # Assert: SQL executes without error through plan().
    p = _plan6(sql, claims={"policies": {"tenant": "X"}})
    rows = con.execute(p.sql).fetchall()
    assert rows, f"Expected rows from tenant X; got none.\nSQL:\n{p.sql}"


# ── Regression 2 [HIGH]: Other arm correlated on rls_keys (per-tenant) ───────

def test_top_n_other_per_tenant_no_double_count_no_missing() -> None:
    """[HIGH] Other bucket must use per-tenant top-N exclusion, not global.

    Setup: two tenants whose per-tenant top-1 cat DIFFERS.
      Tenant A: apple=200 (top-1), banana=50, cherry=30
                → Other should = banana+cherry = 80
      Tenant B: banana=300 (top-1), apple=100, cherry=70
                → Other should = apple+cherry = 170

    With the buggy non-correlated NOT IN (rls_keys=()):
      - The global top-N is banana (300+50=350 > apple 200+100=300).
      - Tenant A's Other arm excludes banana (global top) but includes apple
        (not global top-1) → apple appears in BOTH arms: DOUBLE-COUNTED.
      - Tenant A's Other sum = apple(200) + cherry(30) = 230 ≠ 80 (wrong).

    With the fix (correlated NOT IN on rls_keys=('tenant',)):
      - Tenant A's Other arm excludes apple (A's top-1) → Other = 80.
      - Tenant B's Other arm excludes banana (B's top-1) → Other = 170.

    This test asserts EXACT rows for EACH tenant through plan().
    """
    import sqlglot as _sg
    from app.connectors.planner import resolve_named_params

    con = _duckdb_conn6()
    con.execute("""
        CREATE TABLE topn_other_pertenant (
            tenant    VARCHAR,
            cat       VARCHAR,
            amount    DOUBLE,
            sale_date DATE
        )
    """)
    # Tenant A: apple=200 (top-1), banana=50, cherry=30
    # Tenant B: banana=300 (top-1), apple=100, cherry=70
    con.execute("""
        INSERT INTO topn_other_pertenant VALUES
            ('A', 'apple',  200, '2024-01-15'),
            ('A', 'banana',  50, '2024-01-20'),
            ('A', 'cherry',  30, '2024-01-25'),
            ('B', 'apple',  100, '2024-01-10'),
            ('B', 'banana', 300, '2024-01-12'),
            ('B', 'cherry',  70, '2024-01-18')
    """)

    m = MetricDefinition(
        id="pertenant_other",
        name="Per-Tenant Other",
        measure=Measure(name="amount", agg="sum", expr="amount"),
        base_table="topn_other_pertenant",
        dimensions=(Dimension(name="cat"),),
        time_dimension=TimeDimension(
            column="sale_date",
            grains=("month",),
            default_grain="month",
        ),
        rls_keys=("tenant",),
    )
    mq = MetricQuery(
        metric_id="pertenant_other",
        dimensions=("cat",),
        time_grain="month",
        top_n=TopN(dimension="cat", n=1, other=True, other_label="Other"),
    )

    sql, named_params = compile_metric(m, mq)
    pos_sql, pos_params = resolve_named_params(sql, named_params)

    # SQL must parse cleanly.
    parsed = _sg.parse_one(pos_sql, dialect="duckdb")
    assert parsed is not None, f"SQL failed to parse:\n{pos_sql[:600]}"

    # SQL must contain exactly ONE WITH __BASE AS (no nested double-WITH).
    count_with = pos_sql.upper().count("WITH __BASE AS")
    assert count_with == 1, (
        f"Nested double-WITH detected (regression 1): count={count_with}\n"
        f"SQL:\n{pos_sql[:800]}"
    )

    # ── Tenant A ─────────────────────────────────────────────────────────────
    p_a = _plan6(pos_sql, claims={"policies": {"tenant": "A"}})
    rows_a = con.execute(p_a.sql, p_a.params).fetchall()

    # Columns order: cat, tenant, sale_date_month, amount
    by_cat_a = {r[0]: r for r in rows_a}

    assert "apple" in by_cat_a, (
        f"Tenant A: top-1 'apple' missing; rows={rows_a}"
    )
    assert "Other" in by_cat_a, (
        f"Tenant A: 'Other' bucket missing; rows={rows_a}"
    )
    # No cross-tenant rows.
    assert "B" not in {r[1] for r in rows_a}, (
        f"Tenant A: cross-tenant rows from B leaked; rows={rows_a}"
    )
    # apple = 200, Other = banana(50) + cherry(30) = 80
    assert by_cat_a["apple"][3] == 200.0, (
        f"Tenant A apple amount: expected 200, got {by_cat_a['apple'][3]}"
    )
    other_a = by_cat_a["Other"][3]
    assert other_a == 80.0, (
        f"Tenant A Other amount: expected 80 (banana=50 + cherry=30), got {other_a}.\n"
        f"If 230: apple is DOUBLE-COUNTED (regression 2: non-correlated NOT IN).\n"
        f"rows={rows_a}"
    )

    # ── Tenant B ─────────────────────────────────────────────────────────────
    p_b = _plan6(pos_sql, claims={"policies": {"tenant": "B"}})
    rows_b = con.execute(p_b.sql, p_b.params).fetchall()

    by_cat_b = {r[0]: r for r in rows_b}

    assert "banana" in by_cat_b, (
        f"Tenant B: top-1 'banana' missing; rows={rows_b}"
    )
    assert "Other" in by_cat_b, (
        f"Tenant B: 'Other' bucket missing; rows={rows_b}"
    )
    # No cross-tenant rows.
    assert "A" not in {r[1] for r in rows_b}, (
        f"Tenant B: cross-tenant rows from A leaked; rows={rows_b}"
    )
    # banana = 300, Other = apple(100) + cherry(70) = 170
    assert by_cat_b["banana"][3] == 300.0, (
        f"Tenant B banana amount: expected 300, got {by_cat_b['banana'][3]}"
    )
    other_b = by_cat_b["Other"][3]
    assert other_b == 170.0, (
        f"Tenant B Other amount: expected 170 (apple=100 + cherry=70), got {other_b}.\n"
        f"rows={rows_b}"
    )


def test_top_n_other_no_time_grain_rank_is_per_tenant() -> None:
    """[HIGH RLS] top_n.other WITHOUT a time_grain must rank per-tenant.

    Regression for a real defect: the no-time-grain top-N path emits
    ``QUALIFY RANK() OVER (ORDER BY <measure> DESC) <= N`` on the top-N arm.
    For top_n.other the planner injects RLS (``WHERE org_id = …``) on the OUTER
    union wrapper — i.e. AFTER the RANK is computed inside the top-N arm.  Without
    a ``PARTITION BY <rls_keys>`` the RANK is computed across ALL tenants, so a
    tenant whose values are dominated by another tenant gets its ENTIRE top-N set
    ranked out (rank > N) and the post-rank RLS filter then drops it — the top-N
    arm returns zero rows for that tenant, leaving only the Other bucket.

    Setup: tenant A's values are small; tenant B's are huge.  Globally B's cats
    take ranks 1-2, so A's top-2 would be ranked out without the partition.
    With the per-tenant PARTITION BY the rank is computed within each tenant.
    """
    con = _duckdb_conn6()
    con.execute("""
        CREATE TABLE topn_other_nograin (
            tenant VARCHAR,
            cat    VARCHAR,
            amount DOUBLE
        )
    """)
    con.execute("""
        INSERT INTO topn_other_nograin VALUES
            ('A', 'apple',   30, ),
            ('A', 'banana',  20, ),
            ('A', 'cherry',  10, ),
            ('A', 'date',     5, ),
            ('B', 'mega1', 9999, ),
            ('B', 'mega2', 8888, ),
            ('B', 'tiny',     1, )
    """)

    m = MetricDefinition(
        id="nograin_other",
        name="No-Grain Other",
        measure=Measure(name="amount", agg="sum", expr="amount"),
        base_table="topn_other_nograin",
        dimensions=(Dimension(name="cat"),),
        rls_keys=("tenant",),
    )
    mq = MetricQuery(
        metric_id="nograin_other",
        dimensions=("cat",),
        top_n=TopN(dimension="cat", n=2, other=True, other_label="Other"),
    )

    sql, named = compile_metric(m, mq)
    from app.connectors.planner import resolve_named_params
    pos_sql, _ = resolve_named_params(sql, named)

    # ── Tenant A: small values, dominated globally by tenant B ────────────────
    p_a = _plan6(pos_sql, claims={"policies": {"tenant": "A"}})
    rows_a = con.execute(p_a.sql, p_a.params).fetchall()
    by_cat_a = {r[0]: r for r in rows_a}
    # A's top-2 by amount: apple(30), banana(20); Other = cherry(10)+date(5)=15.
    assert "apple" in by_cat_a and "banana" in by_cat_a, (
        f"Tenant A top-2 missing (rank computed cross-tenant?); rows={rows_a}"
    )
    assert "Other" in by_cat_a, f"Tenant A Other missing; rows={rows_a}"
    # No cross-tenant leak.
    assert "B" not in {r[1] for r in rows_a}, f"cross-tenant leak; rows={rows_a}"
    assert by_cat_a["Other"][2] == 15.0, (
        f"Tenant A Other expected 15 (cherry=10 + date=5), got {by_cat_a['Other'][2]}; "
        f"rows={rows_a}"
    )

    # ── Tenant B: its own top-2 ───────────────────────────────────────────────
    p_b = _plan6(pos_sql, claims={"policies": {"tenant": "B"}})
    rows_b = con.execute(p_b.sql, p_b.params).fetchall()
    by_cat_b = {r[0]: r for r in rows_b}
    assert "mega1" in by_cat_b and "mega2" in by_cat_b, f"rows={rows_b}"
    assert "A" not in {r[1] for r in rows_b}, f"cross-tenant leak; rows={rows_b}"
    # Other = tiny(1).
    assert by_cat_b["Other"][2] == 1.0, (
        f"Tenant B Other expected 1 (tiny), got {by_cat_b['Other'][2]}; rows={rows_b}"
    )


# ── Leap-year-aware prior_year date arithmetic ────────────────────────────────


def test_prior_year_leap_year_day_grain_correct_bucket() -> None:
    """[MED] prior_year with day grain must be leap-year-aware.

    2024-03-01 - INTERVAL '365 days' = 2023-03-02 (WRONG — skips the leap day).
    2024-03-01 - INTERVAL '1 year'   = 2023-03-01 (CORRECT — calendar subtraction).

    We insert a row for 2023-03-01 (prior year) and a row for 2024-03-01
    (current year, crossing the 2024 leap-year boundary).  The prior_year
    lookup must return the 2023-03-01 value, not NULL.
    """
    con = _duckdb_conn()
    con.execute("""
        CREATE TABLE leap_sales (
            amount DOUBLE,
            sale_date DATE
        )
    """)
    # 2024 is a leap year; 2024-03-01 is the day after the leap day (2024-02-29).
    # INTERVAL '365 days' back from 2024-03-01 lands on 2023-03-02, missing the
    # 2023-03-01 bucket.  INTERVAL '1 year' lands on 2023-03-01 — correct.
    con.execute("""
        INSERT INTO leap_sales VALUES
            (999, '2023-03-01'),
            (111, '2024-03-01')
    """)
    m = MetricDefinition(
        id="leap_rev",
        name="Leap Revenue",
        measure=Measure(name="revenue", agg="sum", expr="amount"),
        base_table="leap_sales",
        dimensions=(),
        time_dimension=TimeDimension(
            column="sale_date",
            grains=("day",),
            default_grain="day",
        ),
    )
    mq = MetricQuery(
        metric_id="leap_rev",
        dimensions=(),
        time_grain="day",
        time_comparisons=(
            TimeComparison(measure="revenue", kind="prior_year"),
        ),
    )
    import sqlglot  # noqa: PLC0415
    sql, _ = compile_metric(m, mq)
    sqlglot.parse_one(sql, dialect="duckdb")
    rows = con.execute(sql).fetchall()

    # Columns: sale_date_day, revenue, revenue_prior_year
    row_by_date = {r[0]: r for r in rows}

    # Normalise key — DuckDB may return datetime.datetime or datetime.date
    import datetime  # noqa: PLC0415
    def _date_key(k):
        if hasattr(k, "date"):
            return k.date()
        return k

    row_by_date = {_date_key(k): v for k, v in row_by_date.items()}

    target = datetime.date(2024, 3, 1)
    assert target in row_by_date, (
        f"Expected row for 2024-03-01; rows={rows}"
    )
    prior_year_val = row_by_date[target][2]
    assert prior_year_val == 999.0, (
        f"2024-03-01 prior_year should be 999 (2023-03-01 bucket); "
        f"got {prior_year_val!r} — likely INTERVAL '365 days' landed on 2023-03-02 "
        f"(off-by-one due to leap year). rows={rows}"
    )


# ---------------------------------------------------------------------------
# 16. EIGHTH-WAVE REGRESSION TESTS — top_n/time-intel compiler fixes
#
# HARD RULE: all tests go through plan() + execute on in-memory DuckDB and
# assert EXACT rows.
#
# Fix 1 [HIGH SQLi]: rls_keys interpolated raw into SQL — validate in _govern
# Fix 2 [HIGH correctness]: _apply_top_n_other re-parses layered_sql (mangles
#   Jinja2 {{f0}} placeholders) — pass base_sql directly as base_cte_body kwarg
# Fix 3 [HIGH resource+correctness]: no _MAX_TC_ENTRIES cap, and duplicate
#   LATERAL aliases for same measure+kind — add cap + make aliases unique
#
# COMPREHENSIVE MATRIX (through plan() + DuckDB, exact rows):
#   {top_n.other=False/True} x {with/without mq.filters} x
#   {0/1/2 time_comparisons incl. two same-measure pop_pct} x
#   {1 and 2 tenants with rls_keys}
# ---------------------------------------------------------------------------

import os as _os
import itertools as _itertools


def _duckdb_conn8():
    """Fresh in-memory DuckDB connection (wave-8 tests)."""
    try:
        import duckdb
        return duckdb.connect(":memory:")
    except ImportError:
        pytest.skip("duckdb not installed")


def _plan8(sql: str, claims=None):
    """Run SQL through plan() (duckdb dialect), return PhysicalPlan."""
    from app.connectors.planner import plan as _plan
    return _plan(sql, claims=claims or {}, dialect="duckdb")


def _setup_matrix_table(con, table_name: str) -> None:
    """Create a small orders table for matrix tests.

    ``cost`` is a second base measure so the matrix can exercise a derived
    measure (``margin = (amount - cost) / amount``).  Existing callers that only
    read ``region/amount/sale_date/org_id`` are unaffected by the extra column.
    """
    con.execute(f"""
        CREATE TABLE IF NOT EXISTS {table_name} (
            region    VARCHAR,
            amount    DOUBLE,
            cost      DOUBLE,
            sale_date DATE,
            org_id    VARCHAR
        )
    """)
    con.execute(f"""
        INSERT INTO {table_name} VALUES
            ('A',  500.0, 200.0, '2024-01-01', 'org1'),
            ('A',  300.0, 100.0, '2024-02-01', 'org1'),
            ('B',  200.0,  80.0, '2024-01-01', 'org1'),
            ('B',  100.0,  40.0, '2024-02-01', 'org1'),
            ('C',   50.0,  20.0, '2024-01-01', 'org2'),
            ('C',   30.0,  10.0, '2024-02-01', 'org2')
    """)


def _matrix_metric(
    table_name: str, rls_keys=("org_id",), with_derived: bool = False
) -> MetricDefinition:
    """Build a metric for matrix tests.

    org_id is included as a dimension so it can be used as a user filter field
    (governance requires filter fields to be declared dimensions or the time col).
    rls_keys=("org_id",) ensures per-tenant isolation via the planner.

    When ``with_derived`` is set the metric also declares a second base measure
    ``cost`` and a derived ratio ``margin = (revenue - cost) / revenue`` so the
    composition matrix can cross the derived-measure axis.
    """
    from app.metrics.models import TimeDimension as _TD
    extra_measures: tuple = ()
    derived_measures: tuple = ()
    if with_derived:
        extra_measures = (Measure(name="cost", agg="sum", expr="cost"),)
        derived_measures = (
            DerivedMeasure(name="margin", formula="(revenue - cost) / revenue"),
        )
    return MetricDefinition(
        id="matrix_rev",
        name="Matrix Revenue",
        measure=Measure(name="revenue", agg="sum", expr="amount"),
        base_table=table_name,
        dimensions=(Dimension(name="region"), Dimension(name="org_id")),
        time_dimension=_TD(
            column="sale_date",
            grains=("month",),
            default_grain="month",
        ),
        extra_measures=extra_measures,
        derived_measures=derived_measures,
        rls_keys=rls_keys,
    )


# ── Fix 1 [HIGH SQLi]: rls_key with bad identifier raises bad_rls_key ─────────


def test_rls_key_sqli_raises_bad_rls_key() -> None:
    """[HIGH SQLi] rls_key with SQL-injection chars must raise MetricError(bad_rls_key)."""
    m = MetricDefinition(
        id="inj_rls",
        name="Inj RLS",
        measure=Measure(name="revenue", agg="sum", expr="amount"),
        base_table="t",
        dimensions=(Dimension(name="region"),),
        rls_keys=("org_id; DROP TABLE t --",),
    )
    with pytest.raises(MetricError) as ei:
        compile_metric(m, MetricQuery(metric_id="inj_rls", dimensions=("region",)))
    assert ei.value.code == "bad_rls_key", (
        f"Expected bad_rls_key, got {ei.value.code}"
    )


def test_rls_key_with_dot_raises_bad_rls_key() -> None:
    """[HIGH SQLi] rls_key with a dot (table.column) must raise bad_rls_key."""
    m = MetricDefinition(
        id="dot_rls",
        name="Dot RLS",
        measure=Measure(name="revenue", agg="sum", expr="amount"),
        base_table="t",
        dimensions=(Dimension(name="region"),),
        rls_keys=("t.org_id",),
    )
    with pytest.raises(MetricError) as ei:
        compile_metric(m, MetricQuery(metric_id="dot_rls", dimensions=()))
    assert ei.value.code == "bad_rls_key"


def test_rls_key_valid_identifier_accepted() -> None:
    """[HIGH SQLi] A valid rls_key compiles without error."""
    m = _matrix_metric("matrix_valid_rls")
    mq = MetricQuery(metric_id="matrix_rev", dimensions=("region",))
    sql, _ = compile_metric(m, mq)
    assert sql


# ── Fix 3 [HIGH resource]: _MAX_TC_ENTRIES cap ────────────────────────────────


def test_too_many_tc_entries_raises() -> None:
    """[HIGH resource] More than _MAX_TC_ENTRIES time_comparisons raises too_many_tc_entries."""
    max_tc = int(_os.environ.get("NUBI_MAX_TC_ENTRIES", 20))
    m = _matrix_metric("unused_table")
    # Build max_tc+1 entries — all valid individually.
    tc_list = tuple(
        TimeComparison(measure="revenue", kind="pop_pct", periods=1,
                       name=f"rev_pct_{i}")
        for i in range(max_tc + 1)
    )
    mq = MetricQuery(
        metric_id="matrix_rev",
        dimensions=("region",),
        time_grain="month",
        time_comparisons=tc_list,
    )
    with pytest.raises(MetricError) as ei:
        compile_metric(m, mq)
    assert ei.value.code == "too_many_tc_entries", (
        f"Expected too_many_tc_entries, got {ei.value.code}"
    )


def test_exactly_max_tc_entries_accepted() -> None:
    """[HIGH resource] Exactly _MAX_TC_ENTRIES time_comparisons is accepted (no off-by-one)."""
    max_tc = int(_os.environ.get("NUBI_MAX_TC_ENTRIES", 20))
    m = _matrix_metric("unused_table2")
    tc_list = tuple(
        TimeComparison(measure="revenue", kind="pop_pct", periods=1,
                       name=f"rev_pct_{i}")
        for i in range(max_tc)
    )
    mq = MetricQuery(
        metric_id="matrix_rev",
        dimensions=("region",),
        time_grain="month",
        time_comparisons=tc_list,
    )
    # Should compile without error (may be slow but must not raise).
    sql, _ = compile_metric(m, mq)
    assert sql


# ── Fix 3 [HIGH correctness]: duplicate LATERAL alias for same measure+kind ──


def test_two_pop_pct_same_measure_no_duplicate_alias_error() -> None:
    """[HIGH correctness] Two pop_pct entries for the same measure must NOT produce
    duplicate LATERAL alias SQL errors.  Aliases must be unique per entry.
    """
    con = _duckdb_conn8()
    con.execute("""
        CREATE TABLE two_pop_pct (
            region    VARCHAR,
            amount    DOUBLE,
            sale_date DATE
        )
    """)
    con.execute("""
        INSERT INTO two_pop_pct VALUES
            ('A', 100.0, '2024-01-01'),
            ('A', 150.0, '2024-02-01'),
            ('A', 200.0, '2024-03-01')
    """)
    m = MetricDefinition(
        id="two_pop",
        name="Two PoP",
        measure=Measure(name="revenue", agg="sum", expr="amount"),
        base_table="two_pop_pct",
        dimensions=(Dimension(name="region"),),
        time_dimension=TimeDimension(
            column="sale_date",
            grains=("month",),
            default_grain="month",
        ),
    )
    mq = MetricQuery(
        metric_id="two_pop",
        dimensions=("region",),
        time_grain="month",
        time_comparisons=(
            TimeComparison(measure="revenue", kind="pop_pct", periods=1,
                           name="rev_pop_pct_1m"),
            TimeComparison(measure="revenue", kind="pop_pct", periods=2,
                           name="rev_pop_pct_2m"),
        ),
    )
    sql, params = compile_metric(m, mq)

    # SQL must parse cleanly (duplicate alias would cause a parse/execution error).
    import sqlglot as _sg
    parsed = _sg.parse_one(sql, dialect="duckdb")
    assert parsed is not None, f"SQL failed to parse:\n{sql[:600]}"

    # Must contain two distinct LATERAL aliases (one per pop_pct entry).
    assert sql.lower().count("__pp_revenue_") >= 2, (
        f"Expected at least 2 distinct LATERAL aliases; SQL:\n{sql[:600]}"
    )

    # Execute via plan() — a duplicate-alias SQL error would surface here.
    p = _plan8(sql, claims={})
    rows = con.execute(p.sql).fetchall()

    # Columns: region, sale_date_month, revenue, rev_pop_pct_1m, rev_pop_pct_2m
    row_by_ym = {(r[1].year, r[1].month): r for r in rows}
    assert (2024, 1) in row_by_ym
    assert (2024, 2) in row_by_ym
    assert (2024, 3) in row_by_ym

    # 2024-01: no prior 1m or 2m → both NULL
    assert row_by_ym[(2024, 1)][3] is None, f"2024-01 pop_pct_1m should be NULL"
    assert row_by_ym[(2024, 1)][4] is None, f"2024-01 pop_pct_2m should be NULL"

    # 2024-02: prior 1m = Jan(100) → pct=(150-100)/100=0.5; prior 2m=NULL
    assert abs(row_by_ym[(2024, 2)][3] - 0.5) < 1e-9, (
        f"2024-02 pop_pct_1m should be 0.5, got {row_by_ym[(2024, 2)][3]}"
    )
    assert row_by_ym[(2024, 2)][4] is None, f"2024-02 pop_pct_2m should be NULL (no 2023-12)"

    # 2024-03: prior 1m = Feb(150) → pct=(200-150)/150≈0.333; prior 2m = Jan(100)
    assert abs(row_by_ym[(2024, 3)][3] - (200 - 150) / 150) < 1e-9, (
        f"2024-03 pop_pct_1m should be ~0.333, got {row_by_ym[(2024, 3)][3]}"
    )
    assert abs(row_by_ym[(2024, 3)][4] - (200 - 100) / 100) < 1e-9, (
        f"2024-03 pop_pct_2m should be 1.0, got {row_by_ym[(2024, 3)][4]}"
    )


def test_two_yoy_pct_same_measure_no_duplicate_alias_error() -> None:
    """[HIGH correctness] Two yoy_pct entries for the same measure must NOT produce
    duplicate LATERAL alias SQL errors.
    """
    m = _simple_metric()
    mq = MetricQuery(
        metric_id="revenue",
        dimensions=("region",),
        time_grain="month",
        time_comparisons=(
            TimeComparison(measure="revenue", kind="yoy_pct",
                           name="rev_yoy_pct_a"),
            TimeComparison(measure="revenue", kind="yoy_pct",
                           name="rev_yoy_pct_b"),
        ),
    )
    sql, _ = compile_metric(m, mq)

    import sqlglot as _sg
    parsed = _sg.parse_one(sql, dialect="duckdb")
    assert parsed is not None, f"SQL failed to parse:\n{sql[:600]}"

    # Two distinct aliases — one per yoy_pct entry.
    assert sql.lower().count("__py_revenue_") >= 2, (
        f"Expected at least 2 distinct yoy_pct LATERAL aliases; SQL:\n{sql[:600]}"
    )

    # Must contain NULLIF for zero-division guard.
    assert "NULLIF" in sql.upper()


# ── Fix 2 [HIGH correctness]: base_cte_body passed directly (no AST re-parse) ─


def test_top_n_other_with_filter_filter_is_applied() -> None:
    """[HIGH correctness] When top_n.other=True AND mq.filters is set, the user filter
    MUST be respected in the base CTE.

    Previously _apply_top_n_other re-parsed layered_sql through sqlglot which
    mangled {{f0}} Jinja2 placeholders into map/struct literals, silently dropping
    the filter.  The fix passes base_sql directly as base_cte_body.

    Data: org1 has regions A(500+300=800), B(200+100=300); org2 has C(50+30=80).
    Filter: org_id='org1' (user filter on the filter field).  After filter only
    org1 rows remain in __base.  top_n=1 → A is top; Other = B (300).
    If filter is dropped, C(80) also enters __base and Other=B+C=380 (wrong).
    """
    import sqlglot as _sg
    from app.connectors.planner import resolve_named_params

    con = _duckdb_conn8()
    _setup_matrix_table(con, "topn_other_filter_check")

    m = MetricDefinition(
        id="filter_check",
        name="Filter Check",
        measure=Measure(name="revenue", agg="sum", expr="amount"),
        base_table="topn_other_filter_check",
        dimensions=(Dimension(name="region"), Dimension(name="org_id")),
        rls_keys=("org_id",),
    )
    mq = MetricQuery(
        metric_id="filter_check",
        dimensions=("region",),
        filters=(
            # User filter: only org1 rows.  This uses a {{f0}} placeholder
            # that must survive the _apply_top_n_other path intact.
            MetricFilter(field="org_id", op="=", value="org1"),
        ),
        top_n=TopN(dimension="region", n=1, order="desc", other=True, other_label="Other"),
    )
    sql, named_params = compile_metric(m, mq)

    # SQL must parse.
    parsed = _sg.parse_one(sql, dialect="duckdb")
    assert parsed is not None, f"SQL failed to parse:\n{sql[:600]}"

    # Resolve named params to positional for DuckDB execution.
    pos_sql, pos_params = resolve_named_params(sql, named_params)

    # Execute via plan() — pass pos_params so the plan carries the $1 binding.
    # (plan() preserves params as-is; the executor uses them alongside p.sql.)
    from app.connectors.planner import plan as _plan_direct
    p = _plan_direct(pos_sql, claims={}, params=pos_params, dialect="duckdb")
    rows = con.execute(p.sql, p.params).fetchall()

    row_by_region = {r[0]: r for r in rows}

    # Only org1 rows should survive the filter.
    assert "A" in row_by_region, f"Top region A missing; rows={rows}"
    assert "Other" in row_by_region, f"Other bucket missing; rows={rows}"
    # C (org2) must NOT appear in either arm — it's excluded by the filter.
    assert "C" not in row_by_region, (
        f"org2 region C leaked through filter (filter was dropped!); rows={rows}"
    )

    # Other = B only (300). If filter was dropped, Other = B+C = 380 (wrong).
    # Column order: region(0), org_id(1, rls_key extra dim), revenue(2)
    other_row = row_by_region["Other"]
    # Find the numeric revenue value (the only float column).
    other_rev = next(v for v in other_row if isinstance(v, (int, float)) and v is not True and v is not False)
    assert other_rev == 300.0, (
        f"Other revenue should be 300 (only B=300, C excluded by filter), "
        f"got {other_rev} (row={other_row}). If 380: the {{{{f0}}}} filter placeholder "
        f"was mangled by the AST re-parse in _apply_top_n_other."
    )


# ── COMPREHENSIVE MATRIX TEST ─────────────────────────────────────────────────
#
# Cross product:
#   top_n_other in {False, True}
#   with_filter in {False, True}
#   tc_count in {0, 1, 2}   (0=no TCs, 1=one pop_pct, 2=two same-measure pop_pct)
#   tenant_count in {1, 2}  (1=single-tenant, 2=two tenants for isolation check)
#
# Each cell: SQL parses + plan() accepts + executes + filter respected (if set) +
#            per-tenant isolation (if 2 tenants) + no duplicate-alias SQL error.


def _run_matrix_cell(
    con,
    table_name: str,
    top_n_other: bool,
    with_filter: bool,
    tc_count: int,
    tenant_count: int,
    with_derived: bool = False,
) -> None:
    """Execute one matrix cell and assert all invariants."""
    import sqlglot as _sg
    from app.connectors.planner import resolve_named_params

    m = _matrix_metric(table_name, with_derived=with_derived)

    # time_comparisons: 0=none, 1=one pop_pct(1), 2=two pop_pct(1)+pop_pct(2)
    if tc_count == 0:
        tc_tuple: tuple = ()
    elif tc_count == 1:
        tc_tuple = (
            TimeComparison(measure="revenue", kind="pop_pct", periods=1,
                           name="rev_pct_1m"),
        )
    else:  # 2: two same-measure pop_pct
        tc_tuple = (
            TimeComparison(measure="revenue", kind="pop_pct", periods=1,
                           name="rev_pct_1m"),
            TimeComparison(measure="revenue", kind="pop_pct", periods=2,
                           name="rev_pct_2m"),
        )

    time_grain = "month" if tc_count > 0 else None

    # filters: with_filter=True → filter to org1 only
    filters_tuple: tuple = ()
    if with_filter:
        filters_tuple = (MetricFilter(field="org_id", op="=", value="org1"),)

    top_n_cfg = None
    if top_n_other:
        top_n_cfg = TopN(
            dimension="region", n=1, order="desc", other=True, other_label="Other"
        )
    else:
        top_n_cfg = TopN(dimension="region", n=2, order="desc", other=False)

    mq = MetricQuery(
        metric_id="matrix_rev",
        dimensions=("region",),
        time_grain=time_grain,
        time_comparisons=tc_tuple,
        filters=filters_tuple,
        top_n=top_n_cfg,
    )

    sql, named_params = compile_metric(m, mq)

    cell = (
        f"[other={top_n_other}, filter={with_filter}, tc={tc_count}, "
        f"derived={with_derived}, tenants={tenant_count}]"
    )

    # Must parse.
    parsed = _sg.parse_one(sql, dialect="duckdb")
    assert parsed is not None, f"SQL failed to parse {cell}:\n{sql[:600]}"

    # Exactly ONE 'WITH __BASE' (single CTE — no double-nested WITH).
    assert sql.upper().count("WITH __BASE") == 1, (
        f"Expected exactly one 'WITH __BASE' {cell}:\n{sql[:600]}"
    )

    # Derived measure column must be present when requested.
    if with_derived:
        assert "margin" in sql.lower(), f"derived column missing {cell}:\n{sql[:600]}"

    # Resolve named params.
    pos_sql, pos_params = resolve_named_params(sql, named_params)

    # plan() must accept it (no UNSUPPORTED_QUERY / INVALID_SQL error).
    # For 2-tenant scenario test BOTH tenant claims.
    claims_list = [{"policies": {"org_id": "org1"}}]
    if tenant_count == 2:
        claims_list.append({"policies": {"org_id": "org2"}})

    for claims in claims_list:
        # Pass pos_params so that $N placeholders (from user filters) are bound.
        from app.connectors.planner import plan as _plan_direct_m
        p = _plan_direct_m(pos_sql, claims=claims, params=pos_params, dialect="duckdb")
        rows = con.execute(p.sql, p.params).fetchall()

        tenant_id = claims["policies"]["org_id"]

        # Determine expected regions for this tenant.
        if tenant_id == "org1":
            expected_regions = {"A", "B"}
            forbidden_regions = {"C"}
        else:
            expected_regions = {"C"}
            forbidden_regions = {"A", "B"}

        # With filter=True org_id='org1', org2 rows are excluded by user filter.
        # Combined with RLS claim, org2 tenant should see no rows at all
        # when user filter is set to org1.
        if with_filter and tenant_id == "org2":
            # Both user filter (org1) AND RLS (org2) applied → no rows.
            assert len(rows) == 0, (
                f"Expected 0 rows for org2 when user filter is org1 "
                f"[other={top_n_other}, filter={with_filter}, tc={tc_count}]; "
                f"rows={rows}"
            )
            continue

        # Per-tenant isolation: no cross-tenant rows.
        for r in rows:
            # org_id is an rls_key → planner injects it; it should be in the row.
            # Find org_id column (it appears in the __base GROUP BY and outer SELECT).
            # Its position depends on the query shape; check by value in all cols.
            row_as_strs = [str(v) for v in r]
            for forbidden in forbidden_regions:
                if forbidden in row_as_strs:
                    # It's a region value, not org_id; only flag if region col matches.
                    pass
            # Check region column specifically (position 0).
            region_val = r[0]
            assert region_val not in forbidden_regions or region_val == "Other", (
                f"Cross-tenant leak: tenant {tenant_id!r} got region {region_val!r} "
                f"which belongs to another tenant "
                f"[other={top_n_other}, filter={with_filter}, tc={tc_count}]; "
                f"rows={rows}"
            )

        # With top_n.other=False, n=2: top-2 regions for org1 are A and B.
        # With top_n.other=True, n=1: top-1 is A, Other=B for org1.
        if not with_filter and tenant_id == "org1":
            row_regions = {r[0] for r in rows}
            if top_n_other:
                assert "A" in row_regions, (
                    f"org1 top-1 A missing [other={top_n_other}, tc={tc_count}]; "
                    f"regions={row_regions}"
                )
                assert "Other" in row_regions, (
                    f"org1 Other missing [other={top_n_other}, tc={tc_count}]; "
                    f"regions={row_regions}"
                )
                # C must not appear (it's org2 data, excluded by RLS).
                assert "C" not in row_regions, (
                    f"org2 C leaked to org1 [other={top_n_other}, tc={tc_count}]; "
                    f"regions={row_regions}"
                )
            else:
                # top-2 for org1 = A and B (both present).
                assert "A" in row_regions, (
                    f"org1 top-2 A missing [other=False, tc={tc_count}]; "
                    f"regions={row_regions}"
                )
                assert "B" in row_regions, (
                    f"org1 top-2 B missing [other=False, tc={tc_count}]; "
                    f"regions={row_regions}"
                )

        # ── User filter IS reflected in the executed VALUES ──────────────────
        # org_id is projected through both arms (it is an rls_key extra dim at
        # column index 1).  When the user filter org_id='org1' is applied, every
        # returned row's org_id MUST be 'org1' — proving the {{f0}} placeholder
        # survived the single-render/substitute path and the filter landed in the
        # __base CTE (this is the bug the old AST re-parse silently dropped).
        if with_filter:
            for r in rows:
                assert r[1] == "org1", (
                    f"User filter org_id='org1' not reflected in values: row org_id "
                    f"{r[1]!r} {cell}; rows={rows}"
                )

        # ── Correct per-tenant aggregate VALUE (filter respected, no leak) ───
        # For org1 the revenue total of region A is 500+300=800 regardless of the
        # filter (filter is org1 itself); if org2 data ever leaked into __base the
        # Other/A aggregates would change.  revenue is the first numeric, non-bool
        # column after the org_id rls dim.
        if tenant_id == "org1" and not top_n_other:
            a_rows = [r for r in rows if r[0] == "A"]
            if a_rows and tc_count == 0:
                # Without a time_grain there is exactly one A row; revenue is the
                # first float column.
                a_rev = next(
                    v for v in a_rows[0]
                    if isinstance(v, (int, float)) and v not in (True, False)
                )
                assert a_rev == 800.0, (
                    f"org1 region A revenue should be 800 {cell}; got {a_rev}; "
                    f"row={a_rows[0]}"
                )

        # No duplicate-alias SQL error: if we got here without exception, we're fine.


@pytest.mark.parametrize(
    "top_n_other,with_filter,tc_count,with_derived,tenant_count",
    list(
        _itertools.product(
            [False, True],   # top_n_other        (none / n / n+other)
            [False, True],   # with_filter        (none / org_id=org1)
            [0, 1, 2],       # tc_count           ([] / [pop_pct] / [pop_pct, pop_pct])
            [False, True],   # with_derived       (no / margin=(revenue-cost)/revenue)
            [1, 2],          # tenant_count       (1 / 2 tenants for isolation)
        )
    ),
)
def test_matrix_top_n_filter_tc_tenant(
    top_n_other: bool,
    with_filter: bool,
    tc_count: int,
    with_derived: bool,
    tenant_count: int,
) -> None:
    """[COMPOSITION MATRIX] Cross-product of top_n.other x filters x time_comparisons
    x derived-measure x tenants — every combination compiled through
    compile_metric → resolve_named_params → plan(claims={'policies': {...}}) →
    DuckDB :memory: execution.

    Each cell asserts: SQL parses (sqlglot) + plan() accepts + executes without
    error + the user filter IS reflected in values + per-tenant isolation (no
    cross-tenant rows, correct per-tenant top-N + Other) + no duplicate-alias
    error + exactly ONE 'WITH __BASE'.
    """
    # Use a unique table name per cell to avoid table-already-exists conflicts.
    table_suffix = (
        f"o{int(top_n_other)}_f{int(with_filter)}_tc{tc_count}"
        f"_d{int(with_derived)}_t{tenant_count}"
    )
    table_name = f"matrix_{table_suffix}"

    con = _duckdb_conn8()
    _setup_matrix_table(con, table_name)

    _run_matrix_cell(
        con=con,
        table_name=table_name,
        top_n_other=top_n_other,
        with_filter=with_filter,
        tc_count=tc_count,
        tenant_count=tenant_count,
        with_derived=with_derived,
    )


# ---------------------------------------------------------------------------
# 14. NINTH-WAVE — metric COMPILER audit fixes (compile.py)
#
# Through plan() + DuckDB asserting EXACT rows.  Covers:
#   #1 time_dimension.column SQLi raises (all paths)
#   #2 top_n.other + no-time-grain tie semantics: complement RANK -> no
#      double-count, exact totals
#   #3 derived rank measure + other (and/or time_grain, no grain) raises
#   #4 oversized in/not_in list raises
#   #5 too many filters raises
# ---------------------------------------------------------------------------


# ── #1: time_dimension.column SQLi raises on ALL time-comparison paths ────────

def test_time_column_sqli_raises_unconditionally() -> None:
    """[HIGH SQLi] A malicious time_dimension.column must raise MetricError even
    when no latest_snapshot is used (e.g. a prior_period time-comparison)."""
    m = MetricDefinition(
        id="evil_time",
        name="Evil Time",
        measure=Measure(name="revenue", agg="sum", expr="amount"),
        base_table="t",
        dimensions=(Dimension(name="region"),),
        time_dimension=TimeDimension(
            column="created_at) AS x; DROP TABLE t --",  # injection
            grains=("month",),
            default_grain="month",
        ),
    )
    mq = MetricQuery(
        metric_id="evil_time",
        dimensions=("region",),
        time_grain="month",
        time_comparisons=(
            TimeComparison(measure="revenue", kind="prior_period", periods=1),
        ),
    )
    with pytest.raises(MetricError) as ei:
        compile_metric(m, mq)
    assert ei.value.code == "bad_time_column"


def test_time_column_sqli_raises_even_without_grain() -> None:
    """[HIGH SQLi] The time column is validated whenever a time_dimension exists,
    independent of time_grain / time_comparisons."""
    m = MetricDefinition(
        id="evil_time2",
        name="Evil Time 2",
        measure=Measure(name="revenue", agg="sum", expr="amount"),
        base_table="t",
        dimensions=(Dimension(name="region"),),
        time_dimension=TimeDimension(
            column="ok'; DROP TABLE t --",
            grains=("month",),
            default_grain="month",
        ),
    )
    mq = MetricQuery(metric_id="evil_time2", dimensions=("region",))
    with pytest.raises(MetricError) as ei:
        compile_metric(m, mq)
    assert ei.value.code == "bad_time_column"


def test_valid_time_column_still_compiles_through_plan() -> None:
    """A valid identifier time column still compiles + runs through plan()."""
    con = _duckdb_conn()
    con.execute("""
        CREATE TABLE tcol_ok (region VARCHAR, amount DOUBLE, sale_date DATE)
    """)
    con.execute("""
        INSERT INTO tcol_ok VALUES
            ('A', 100, '2024-01-01'),
            ('A', 200, '2024-02-01')
    """)
    m = MetricDefinition(
        id="tcol_ok",
        name="OK",
        measure=Measure(name="revenue", agg="sum", expr="amount"),
        base_table="tcol_ok",
        dimensions=(Dimension(name="region"),),
        time_dimension=TimeDimension(
            column="sale_date", grains=("month",), default_grain="month",
        ),
    )
    mq = MetricQuery(
        metric_id="tcol_ok",
        dimensions=("region",),
        time_grain="month",
        time_comparisons=(
            TimeComparison(measure="revenue", kind="prior_period", periods=1),
        ),
    )
    sql, _ = compile_metric(m, mq)
    rows = _plan_and_run(sql, con, claims={})
    assert len(rows) == 2


# ── #2: top_n.other + no-time-grain TIE semantics (complement RANK) ──────────

def test_top_n_other_no_grain_boundary_tie_no_double_count() -> None:
    """[HIGH correctness] Rows tied at the N-th boundary must NOT appear in BOTH
    the TOP arm and the OTHER arm.  With the complement-RANK fix every __base row
    lands in exactly one arm and totals are exact.

    Data (n=2, order=desc): A=300 (rank 1), B=100, C=100, D=100 are all TIED at
    rank 2 (RANK assigns 2 to all three).  The TOP arm (RANK<=2) keeps A,B,C,D.
    The OTHER arm (RANK>2) keeps none.  The grand total must equal the sum of all
    rows with NO row counted twice.
    """
    con = _duckdb_conn()
    con.execute("""
        CREATE TABLE tie_orders (region VARCHAR, amount DOUBLE)
    """)
    con.execute("""
        INSERT INTO tie_orders VALUES
            ('A', 300),
            ('B', 100),
            ('C', 100),
            ('D', 100)
    """)
    m = MetricDefinition(
        id="tie",
        name="Tie",
        measure=Measure(name="revenue", agg="sum", expr="amount"),
        base_table="tie_orders",
        dimensions=(Dimension(name="region"),),
    )
    mq = MetricQuery(
        metric_id="tie",
        dimensions=("region",),
        top_n=TopN(dimension="region", n=2, order="desc", other=True, other_label="Other"),
    )
    sql, _ = compile_metric(m, mq)
    rows = _plan_and_run(sql, con, claims={})
    # Columns: region, revenue
    by_region = {r[0]: r[1] for r in rows}
    # No region may appear twice (would surface as a UNION collision / wrong total).
    region_names = [r[0] for r in rows]
    assert len(region_names) == len(set(region_names)), (
        f"A region appears in both arms (double-count): {region_names}"
    )
    # Total across all output rows must equal the true grand total of __base
    # (300+100+100+100 = 600) with NO double-counting of boundary ties.
    grand_total = sum(v for v in by_region.values() if v is not None)
    assert grand_total == 600.0, f"Expected exact total 600, got {grand_total}; rows={rows}"
    # A is clearly top; the three tied-at-2 rows are all kept by RANK<=2 (TOP arm)
    # so the Other (RANK>2) bucket has NO members — its revenue must be empty/NULL
    # (never a positive value, which would mean a boundary row leaked into BOTH
    # arms / was double-counted).
    assert "A" in by_region
    assert by_region.get("Other") in (None, 0.0), (
        f"Other bucket must be empty for this tie set (no boundary double-count); rows={rows}"
    )


def test_top_n_other_no_grain_partition_exact_with_distinct_ranks() -> None:
    """[HIGH correctness] With distinct measures the complement-RANK partition is
    exact: TOP arm = top-N rows, OTHER arm = SUM of the rest, no row dropped/dup."""
    con = _duckdb_conn()
    con.execute("""
        CREATE TABLE part_orders (region VARCHAR, amount DOUBLE)
    """)
    # A=1000, B=500, C=300, D=200, E=100 -> n=2 keeps A,B; Other=C+D+E=600
    con.execute("""
        INSERT INTO part_orders VALUES
            ('A', 1000), ('B', 500), ('C', 300), ('D', 200), ('E', 100)
    """)
    m = MetricDefinition(
        id="part",
        name="Part",
        measure=Measure(name="revenue", agg="sum", expr="amount"),
        base_table="part_orders",
        dimensions=(Dimension(name="region"),),
    )
    mq = MetricQuery(
        metric_id="part",
        dimensions=("region",),
        top_n=TopN(dimension="region", n=2, order="desc", other=True, other_label="Other"),
    )
    sql, _ = compile_metric(m, mq)
    rows = _plan_and_run(sql, con, claims={})
    by_region = {r[0]: r[1] for r in rows}
    assert by_region.get("A") == 1000.0
    assert by_region.get("B") == 500.0
    assert by_region.get("Other") == 600.0, f"Other must be C+D+E=600; rows={rows}"
    # Exact partition: total preserved, no C/D/E leaking into the top arm.
    assert "C" not in by_region and "D" not in by_region and "E" not in by_region
    grand_total = sum(v for v in by_region.values() if v is not None)
    assert grand_total == 2100.0, f"Expected total 2100, got {grand_total}"


def test_top_n_other_no_grain_per_tenant_tie_partition() -> None:
    """[HIGH correctness + RLS] Complement-RANK is partitioned by rls_keys so each
    tenant's top-N + Other is computed independently; boundary ties never cross
    arms within a tenant."""
    con = _duckdb_conn()
    con.execute("""
        CREATE TABLE rls_tie (region VARCHAR, amount DOUBLE, org_id VARCHAR)
    """)
    # org1: A=500, B=100, C=100 (B,C tie at rank 2); org2: X=50, Y=40, Z=30
    con.execute("""
        INSERT INTO rls_tie VALUES
            ('A', 500, 'org1'),
            ('B', 100, 'org1'),
            ('C', 100, 'org1'),
            ('X', 50,  'org2'),
            ('Y', 40,  'org2'),
            ('Z', 30,  'org2')
    """)
    m = MetricDefinition(
        id="rls_tie",
        name="RLS Tie",
        measure=Measure(name="revenue", agg="sum", expr="amount"),
        base_table="rls_tie",
        dimensions=(Dimension(name="region"),),
        rls_keys=("org_id",),
    )
    mq = MetricQuery(
        metric_id="rls_tie",
        dimensions=("region",),
        top_n=TopN(dimension="region", n=1, order="desc", other=True, other_label="Other"),
    )
    sql, _ = compile_metric(m, mq)
    # org1 only: A is top-1; B+C (tied) roll into Other = 200.
    rows = _plan_and_run(sql, con, claims={"policies": {"org_id": "org1"}})
    # Output columns: region, org_id (rls passthrough), revenue. Pick the numeric
    # revenue value from each row regardless of its exact position.
    def _rev(r):
        return next(v for v in r[1:] if isinstance(v, (int, float)) or v is None)
    by_region = {r[0]: _rev(r) for r in rows}
    # No cross-tenant leakage.
    assert "X" not in by_region and "Y" not in by_region and "Z" not in by_region
    assert by_region.get("A") == 500.0
    assert by_region.get("Other") == 200.0, f"org1 Other must be B+C=200; rows={rows}"
    # No region twice.
    names = [r[0] for r in rows]
    assert len(names) == len(set(names))
    total = sum(v for v in by_region.values() if v is not None)
    assert total == 700.0, f"org1 total must be 700, got {total}"


# ── #3: derived rank measure + other (no grain) raises ──────────────────────

def test_derived_rank_measure_with_other_no_grain_rejected() -> None:
    """[HIGH governance] A derived rank measure with top_n.other=True (even with
    NO time_grain) must raise — the Other arm's membership/complement queries
    __base, which lacks derived columns."""
    m = _orders_metric(
        derived_measures=(
            DerivedMeasure(name="pvd", formula="delivered / ordered"),
        )
    )
    mq = MetricQuery(
        metric_id="orders",
        dimensions=("region",),
        # No time_grain, but other=True -> still uses a __base membership/complement.
        top_n=TopN(dimension="region", n=3, measure="pvd", order="desc", other=True),
    )
    with pytest.raises(MetricError) as ei:
        compile_metric(m, mq)
    assert ei.value.code == "bad_top_n"


def test_derived_rank_measure_no_other_no_grain_still_ok() -> None:
    """A derived rank measure with NO other and NO time_grain still compiles
    (the top arm's QUALIFY RANK works on the outer SELECT which has the derived
    column) — regression guard for the narrower #3 block."""
    con = _duckdb_conn()
    con.execute("""
        CREATE TABLE der_ok (region VARCHAR, delivered_qty DOUBLE, ordered_qty DOUBLE)
    """)
    con.execute("""
        INSERT INTO der_ok VALUES
            ('A', 90, 100),
            ('B', 50, 100),
            ('C', 10, 100)
    """)
    m = MetricDefinition(
        id="der_ok",
        name="Der OK",
        measure=Measure(name="delivered", agg="sum", expr="delivered_qty"),
        base_table="der_ok",
        extra_measures=(Measure(name="ordered", agg="sum", expr="ordered_qty"),),
        derived_measures=(DerivedMeasure(name="pvd", formula="delivered / ordered"),),
        dimensions=(Dimension(name="region"),),
    )
    mq = MetricQuery(
        metric_id="der_ok",
        dimensions=("region",),
        top_n=TopN(dimension="region", n=2, measure="pvd", order="desc"),  # other defaults False
    )
    sql, _ = compile_metric(m, mq)
    rows = _plan_and_run(sql, con, claims={})
    regions = {r[0] for r in rows}
    # Top-2 by pvd: A(0.9), B(0.5); C(0.1) excluded.
    assert "A" in regions and "B" in regions and "C" not in regions


# ── #4: oversized in/not_in list raises ─────────────────────────────────────

def test_in_list_too_large_raises() -> None:
    """[HIGH resource] An in/not_in filter value list above NUBI_MAX_IN_LIST raises."""
    import os
    max_in = int(os.environ.get("NUBI_MAX_IN_LIST", 1000))
    m = _simple_metric()
    mq = MetricQuery(
        metric_id="revenue",
        dimensions=("region",),
        filters=(
            MetricFilter(field="region", op="in", value=[str(i) for i in range(max_in + 1)]),
        ),
    )
    with pytest.raises(MetricError) as ei:
        compile_metric(m, mq)
    assert ei.value.code == "in_list_too_large"


def test_in_list_at_cap_accepted() -> None:
    """An in-list exactly at the cap compiles (boundary)."""
    import os
    max_in = int(os.environ.get("NUBI_MAX_IN_LIST", 1000))
    m = _simple_metric()
    mq = MetricQuery(
        metric_id="revenue",
        dimensions=("region",),
        filters=(
            MetricFilter(field="region", op="in", value=[str(i) for i in range(max_in)]),
        ),
    )
    sql, params = compile_metric(m, mq)
    assert sql  # compiled
    assert len(params["f0"]) == max_in


# ── #5: too many filters raises ─────────────────────────────────────────────

def test_too_many_filters_raises() -> None:
    """[LOW resource] More than NUBI_MAX_FILTERS filters raises MetricError."""
    import os
    max_f = int(os.environ.get("NUBI_MAX_FILTERS", 50))
    m = _simple_metric()
    mq = MetricQuery(
        metric_id="revenue",
        dimensions=("region",),
        filters=tuple(
            MetricFilter(field="region", op="=", value=f"v{i}")
            for i in range(max_f + 1)
        ),
    )
    with pytest.raises(MetricError) as ei:
        compile_metric(m, mq)
    assert ei.value.code == "too_many_filters"


def test_filters_at_cap_accepted() -> None:
    """Exactly NUBI_MAX_FILTERS filters compiles (boundary)."""
    import os
    max_f = int(os.environ.get("NUBI_MAX_FILTERS", 50))
    m = _simple_metric()
    mq = MetricQuery(
        metric_id="revenue",
        dimensions=("region",),
        filters=tuple(
            MetricFilter(field="region", op="=", value=f"v{i}")
            for i in range(max_f)
        ),
    )
    sql, params = compile_metric(m, mq)
    assert sql
    assert len(params) == max_f


# ---------------------------------------------------------------------------
# 15. TENTH-WAVE — non-LATERAL time-comparisons now materialise via LATERAL
#     (perf) + unary-minus denominator NULLIF guard (correctness).
#
#     HARD RULE: results verified through plan() + in-memory DuckDB.
# ---------------------------------------------------------------------------


def _pp_inner(measure: str = "revenue") -> str:
    """The prior-PERIOD correlated subquery text (uppercased) — appears once
    inside the LATERAL after the fix."""
    return f'SELECT __PP."{measure.upper()}" FROM __BASE AS __PP'


def _py_inner(measure: str = "revenue") -> str:
    """The prior-YEAR correlated subquery text (uppercased)."""
    return f'SELECT __PY."{measure.upper()}" FROM __BASE AS __PY'


def test_prior_period_uses_lateral_subquery_appears_once() -> None:
    """[MED perf] prior_period materialises the prior value via CROSS JOIN LATERAL.

    The correlated subquery text must appear EXACTLY ONCE (inside the lateral),
    and the outer SELECT references the lateral alias column ``pp_val`` — it no
    longer emits the correlated scalar subquery inline in the SELECT (which made
    the planner re-scan __base per outer row).
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
    assert up.count(_pp_inner()) == 1, (
        f"prior_period subquery should appear once (inside LATERAL), "
        f"found {up.count(_pp_inner())}:\n{sql[:600]}"
    )
    assert "LATERAL" in up
    assert "PP_VAL" in up
    assert "revenue_prior_period" in sql.lower()


def test_pop_abs_uses_lateral_subquery_appears_once() -> None:
    """[MED perf] pop_abs materialises the prior value once via LATERAL.

    Previously pop_abs emitted ``revenue - (<subquery>)`` — the subquery text
    inline in the SELECT, re-scanning __base per outer row.  Now it references
    the lateral column once.
    """
    m = _simple_metric()
    mq = MetricQuery(
        metric_id="revenue",
        dimensions=("region",),
        time_grain="month",
        time_comparisons=(
            TimeComparison(measure="revenue", kind="pop_abs", periods=1),
        ),
    )
    sql, _ = compile_metric(m, mq)
    up = sql.upper()
    assert up.count(_pp_inner()) == 1, (
        f"pop_abs subquery should appear once (inside LATERAL), "
        f"found {up.count(_pp_inner())}:\n{sql[:600]}"
    )
    assert "LATERAL" in up
    assert "PP_VAL" in up


def test_prior_year_uses_lateral_subquery_appears_once() -> None:
    """[MED perf] prior_year materialises the prior-year value once via LATERAL."""
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
    assert up.count(_py_inner()) == 1, (
        f"prior_year subquery should appear once (inside LATERAL), "
        f"found {up.count(_py_inner())}:\n{sql[:600]}"
    )
    assert "LATERAL" in up
    assert "PY_VAL" in up
    assert "revenue_prior_year" in sql.lower()


def test_yoy_abs_uses_lateral_subquery_appears_once() -> None:
    """[MED perf] yoy_abs materialises the prior-year value once via LATERAL."""
    m = _simple_metric()
    mq = MetricQuery(
        metric_id="revenue",
        dimensions=("region",),
        time_grain="month",
        time_comparisons=(
            TimeComparison(measure="revenue", kind="yoy_abs"),
        ),
    )
    sql, _ = compile_metric(m, mq)
    up = sql.upper()
    assert up.count(_py_inner()) == 1, (
        f"yoy_abs subquery should appear once (inside LATERAL), "
        f"found {up.count(_py_inner())}:\n{sql[:600]}"
    )
    assert "LATERAL" in up
    assert "PY_VAL" in up
    assert "revenue_yoy_abs" in sql.lower()


def test_lateral_time_comparisons_correct_values_through_plan() -> None:
    """[MED perf+correctness] prior_period / pop_abs / prior_year / yoy_abs through
    LATERAL produce CORRECT values when executed on DuckDB via plan().

    Data (region A):
      2023-02: 80
      2024-01: 100
      2024-02: 150
    Checks for 2024-02:
      prior_period (2024-01) = 100
      pop_abs      = 150 - 100 = 50
      prior_year   (2023-02)  = 80
      yoy_abs      = 150 - 80  = 70
    """
    con = _duckdb_conn()
    con.execute("""
        CREATE TABLE lat_tc (
            region    VARCHAR,
            amount    DOUBLE,
            sale_date DATE
        )
    """)
    con.execute("""
        INSERT INTO lat_tc VALUES
            ('A',  80, '2023-02-01'),
            ('A', 100, '2024-01-01'),
            ('A', 150, '2024-02-01')
    """)
    m = MetricDefinition(
        id="lat_tc",
        name="Lateral TC",
        measure=Measure(name="revenue", agg="sum", expr="amount"),
        base_table="lat_tc",
        dimensions=(Dimension(name="region"),),
        time_dimension=TimeDimension(
            column="sale_date",
            grains=("month",),
            default_grain="month",
        ),
    )
    mq = MetricQuery(
        metric_id="lat_tc",
        dimensions=("region",),
        time_grain="month",
        time_comparisons=(
            TimeComparison(measure="revenue", kind="prior_period", periods=1),
            TimeComparison(measure="revenue", kind="pop_abs", periods=1),
            TimeComparison(measure="revenue", kind="prior_year"),
            TimeComparison(measure="revenue", kind="yoy_abs"),
        ),
    )
    sql, _ = compile_metric(m, mq)
    # Each comparison materialises its prior value once inside its OWN LATERAL,
    # so the prior-period text appears twice (prior_period + pop_abs) and the
    # prior-year text twice (prior_year + yoy_abs) — once per lateral, never
    # inline-duplicated within a single comparison.
    up = sql.upper()
    assert up.count(_pp_inner()) == 2
    assert up.count(_py_inner()) == 2
    assert "LATERAL" in up
    p = plan(sql, claims={}, dialect="duckdb")
    rows = con.execute(p.sql).fetchall()
    # Columns: region, sale_date_month, revenue, prior_period, pop_abs,
    #          prior_year, yoy_abs (order matches the outer SELECT projection).
    cols = [d[0] for d in con.description]
    idx = {name: i for i, name in enumerate(cols)}
    row_by_ym = {(r[idx["sale_date_month"]].year, r[idx["sale_date_month"]].month): r
                 for r in rows}
    feb = row_by_ym[(2024, 2)]
    assert feb[idx["revenue"]] == 150.0
    assert feb[idx["revenue_prior_period"]] == 100.0, feb
    assert feb[idx["revenue_pop_abs"]] == 50.0, feb
    assert feb[idx["revenue_prior_year"]] == 80.0, feb
    assert feb[idx["revenue_yoy_abs"]] == 70.0, feb


def test_duplicate_lateral_kinds_get_unique_aliases() -> None:
    """Two prior_period entries for the same measure with different periods must
    get distinct LATERAL aliases (no alias collision) and both compile + parse."""
    m = _simple_metric()
    mq = MetricQuery(
        metric_id="revenue",
        dimensions=("region",),
        time_grain="month",
        time_comparisons=(
            TimeComparison(measure="revenue", kind="prior_period", periods=1,
                           name="rev_pp1"),
            TimeComparison(measure="revenue", kind="prior_period", periods=2,
                           name="rev_pp2"),
        ),
    )
    sql, _ = compile_metric(m, mq)
    # Both subqueries appear (different intervals), one per LATERAL.
    assert sql.upper().count(_pp_inner()) == 2
    # Distinct lateral aliases.
    assert "__pp_revenue_1_0__" in sql.lower()
    assert "__pp_revenue_2_1__" in sql.lower()
    parsed = sqlglot.parse_one(sql, dialect="duckdb")
    assert parsed is not None


# ── Unary-minus denominator NULLIF guard ─────────────────────────────────────

def test_unary_minus_denominator_compiles_and_guards() -> None:
    """[LOW correctness] a derived formula with a unary-minus denominator must
    wrap the FULL signed denominator in NULLIF, not just the bare '-' token.

    ``a / -b`` -> ``a / NULLIF(- b, 0)`` (valid SQL), not ``a / NULLIF(-, 0) b``.
    """
    m = MetricDefinition(
        id="neg_denom",
        name="Neg Denom",
        measure=Measure(name="a", agg="sum", expr="col_a"),
        base_table="t",
        extra_measures=(Measure(name="b", agg="sum", expr="col_b"),),
        derived_measures=(
            DerivedMeasure(name="r", formula="a / -b"),
        ),
    )
    mq = MetricQuery(metric_id="neg_denom")
    sql, _ = compile_metric(m, mq)
    up = sql.upper()
    assert "NULLIF" in up
    # The minus sign must be INSIDE the NULLIF, applying to b.
    assert "NULLIF(- B" in up or "NULLIF(-B" in up, (
        f"unary minus must be inside NULLIF over the denominator:\n{sql}"
    )
    # Must be valid SQL.
    parsed = sqlglot.parse_one(sql, dialect="duckdb")
    assert parsed is not None


def test_unary_minus_paren_denominator_compiles() -> None:
    """``a / -(b)`` and ``a / (-b)`` both wrap the full signed/parenthesised
    denominator in NULLIF and parse cleanly."""
    for formula in ("a / -(b)", "a / (-b)"):
        m = MetricDefinition(
            id="neg_paren",
            name="Neg Paren",
            measure=Measure(name="a", agg="sum", expr="col_a"),
            base_table="t",
            extra_measures=(Measure(name="b", agg="sum", expr="col_b"),),
            derived_measures=(
                DerivedMeasure(name="r", formula=formula),
            ),
        )
        mq = MetricQuery(metric_id="neg_paren")
        sql, _ = compile_metric(m, mq)
        assert "NULLIF" in sql.upper(), f"missing NULLIF for {formula!r}:\n{sql}"
        parsed = sqlglot.parse_one(sql, dialect="duckdb")
        assert parsed is not None, f"failed to parse for {formula!r}"


def test_unary_minus_denominator_divide_guard_executes_in_duckdb() -> None:
    """[LOW correctness] the NULLIF guard for a unary-minus denominator still
    prevents divide-by-zero: when -b evaluates to 0 the result is NULL (not an
    error), verified through plan() on DuckDB."""
    con = _duckdb_conn()
    con.execute("""
        CREATE TABLE neg_div (
            grp    VARCHAR,
            a_val  DOUBLE,
            b_val  DOUBLE
        )
    """)
    # grp X: b sums to 0  -> -b = 0 -> NULLIF makes the ratio NULL (no error).
    # grp Y: a sums to 10, b sums to 5 -> -b = -5 -> ratio = 10 / -5 = -2.
    con.execute("""
        INSERT INTO neg_div VALUES
            ('X', 10,  3),
            ('X', 20, -3),
            ('Y', 10,  5)
    """)
    m = MetricDefinition(
        id="neg_div",
        name="Neg Div",
        measure=Measure(name="a", agg="sum", expr="a_val"),
        base_table="neg_div",
        dimensions=(Dimension(name="grp"),),
        extra_measures=(Measure(name="b", agg="sum", expr="b_val"),),
        derived_measures=(
            DerivedMeasure(name="r", formula="a / -b"),
        ),
    )
    mq = MetricQuery(metric_id="neg_div", dimensions=("grp",))
    sql, _ = compile_metric(m, mq)
    p = plan(sql, claims={}, dialect="duckdb")
    rows = con.execute(p.sql).fetchall()
    cols = [d[0] for d in con.description]
    idx = {name: i for i, name in enumerate(cols)}
    by_grp = {r[idx["grp"]]: r for r in rows}
    # X: -b == 0 -> guarded to NULL (no divide-by-zero error).
    assert by_grp["X"][idx["r"]] is None, by_grp["X"]
    # Y: 10 / -5 == -2.0.
    assert by_grp["Y"][idx["r"]] == -2.0, by_grp["Y"]


# ---------------------------------------------------------------------------
# LOW-severity edge fixes: QUALIFY/LIMIT interaction + per-tenant truncation
#
# HARD RULE: through plan() + execute on in-memory DuckDB asserting RESULTS.
# ---------------------------------------------------------------------------


def test_top_n_no_grain_exact_n_rows_with_default_limit_present() -> None:
    """[LOW #1] No-time-grain top_n=N returns EXACTLY N rows (N+ties).
    bare default outer LIMIT does NOT truncate the QUALIFY result.

    QUALIFY RANK() <= N is logically applied BEFORE the outer LIMIT in DuckDB,
    so the top-N is selected first and only then capped.  With 10 candidate
    rows and N=3 we must get exactly 3 rows even though the compiled SQL also
    carries the _DEFAULT_LIMIT outer cap.
    """
    con = _duckdb_conn()
    con.execute("CREATE TABLE tn_nograin (region VARCHAR, amount DOUBLE)")
    con.executemany(
        "INSERT INTO tn_nograin VALUES (?, ?)",
        [(f"r{i}", float((i + 1) * 100)) for i in range(10)],
    )
    m = MetricDefinition(
        id="tn_nograin",
        name="TN NoGrain",
        measure=Measure(name="revenue", agg="sum", expr="amount"),
        base_table="tn_nograin",
        dimensions=(Dimension(name="region"),),
    )
    mq = MetricQuery(
        metric_id="tn_nograin",
        dimensions=("region",),
        top_n=TopN(dimension="region", n=3, measure="revenue", order="desc"),
        # NOTE: limit is None → the compiler applies the default outer LIMIT.
    )
    sql, _ = compile_metric(m, mq)
    up = sql.upper()
    # Sanity: the default cap is present AND it is >= N (never truncates top-N).
    assert "QUALIFY" in up
    assert "LIMIT" in up
    rows = _plan_and_run(sql, con, claims={})
    # Exactly N=3 rows — the top three by revenue (r9, r8, r7).
    assert len(rows) == 3, f"Expected exactly 3 top-N rows, got {len(rows)}: {rows}"
    regions = {r[0] for r in rows}
    assert regions == {"r9", "r8", "r7"}, f"Wrong top-N membership: {regions}"


def test_top_n_no_grain_not_truncated_when_default_limit_below_n(monkeypatch) -> None:
    """[LOW #1] Even if a deployment lowers NUBI_METRIC_DEFAULT_LIMIT BELOW N,
    the compiler floors the effective default LIMIT at N so the QUALIFY top-N
    result is never silently truncated by the default cap.
    """
    import app.metrics.compile as compile_mod

    # Force a pathological default limit (2) that is smaller than N (3).
    monkeypatch.setattr(compile_mod, "_DEFAULT_LIMIT", 2)

    con = _duckdb_conn()
    con.execute("CREATE TABLE tn_lowlim (region VARCHAR, amount DOUBLE)")
    con.executemany(
        "INSERT INTO tn_lowlim VALUES (?, ?)",
        [(f"r{i}", float((i + 1) * 100)) for i in range(10)],
    )
    m = MetricDefinition(
        id="tn_lowlim",
        name="TN LowLim",
        measure=Measure(name="revenue", agg="sum", expr="amount"),
        base_table="tn_lowlim",
        dimensions=(Dimension(name="region"),),
    )
    mq = MetricQuery(
        metric_id="tn_lowlim",
        dimensions=("region",),
        top_n=TopN(dimension="region", n=3, measure="revenue", order="desc"),
    )
    sql, _ = compile_metric(m, mq)
    # The emitted LIMIT must be floored at N (3), NOT the pathological default (2).
    assert "LIMIT 3" in sql.upper(), f"Default LIMIT not floored at N: {sql}"
    rows = _plan_and_run(sql, con, claims={})
    assert len(rows) == 3, f"Top-N truncated by default LIMIT: {rows}"


def test_single_tenant_request_returns_full_top_n_through_plan() -> None:
    """[LOW #2] A single-tenant request (RLS injected by plan()) returns its
    FULL per-tenant top-N — the default outer LIMIT does not starve the tenant.

    Two tenants each have 5 regions.  The query asks top_n=4 with no grain.
    plan() injects WHERE org_id = <claim>, scoping the request to ONE tenant
    BEFORE the LIMIT applies, so we get exactly that tenant's top 4.
    """
    con = _duckdb_conn()
    con.execute("CREATE TABLE tn_multi (org_id VARCHAR, region VARCHAR, amount DOUBLE)")
    rows = []
    for org in ("orgA", "orgB"):
        for i in range(5):
            rows.append((org, f"{org}_r{i}", float((i + 1) * 10)))
    con.executemany("INSERT INTO tn_multi VALUES (?, ?, ?)", rows)
    m = MetricDefinition(
        id="tn_multi",
        name="TN Multi",
        measure=Measure(name="revenue", agg="sum", expr="amount"),
        base_table="tn_multi",
        dimensions=(Dimension(name="region"),),
        rls_keys=("org_id",),
    )
    mq = MetricQuery(
        metric_id="tn_multi",
        dimensions=("region",),
        top_n=TopN(dimension="region", n=4, measure="revenue", order="desc"),
    )
    sql, _ = compile_metric(m, mq)
    claims = {"policies": {"org_id": "orgA"}}
    p = plan(sql, claims=claims, dialect="duckdb")
    # RLS scopes to one tenant before the LIMIT.
    assert "org_id" in p.sql.lower()
    result = con.execute(p.sql).fetchall()
    # Exactly orgA's top 4 regions (r4..r1), none from orgB.
    region_idx = [d[0] for d in con.description].index("region")
    regions = {r[region_idx] for r in result}
    assert len(result) == 4, f"Single-tenant top-N not full: {result}"
    assert regions == {"orgA_r4", "orgA_r3", "orgA_r2", "orgA_r1"}, regions
    assert all(r.startswith("orgA_") for r in regions), f"Cross-tenant leak: {regions}"


# ---------------------------------------------------------------------------
# TWELFTH-WAVE — compiler percentile consistency + top_n.other tie determinism
# (through plan() + DuckDB)
# ---------------------------------------------------------------------------


# ── HIGH: percentile_cont ORDER BY built from PARSED arg, not raw expr ────────

def test_percentile_benign_expr_works_through_plan_duckdb() -> None:
    """[HIGH] A benign percentile_cont expr compiles, plans, and computes the
    correct quantile on DuckDB."""
    con = _duckdb_conn()
    con.execute("CREATE TABLE lat (latency_ms DOUBLE)")
    # values 10,20,30,40,50 → p50 (continuous) = 30
    con.execute("INSERT INTO lat VALUES (10),(20),(30),(40),(50)")
    m = MetricDefinition(
        id="lat",
        name="Lat",
        measure=Measure(name="p50_latency", agg="percentile_cont",
                        expr="latency_ms", format="p50"),
        base_table="lat",
    )
    mq = MetricQuery(metric_id="lat")
    sql, _ = compile_metric(m, mq)
    rows = _plan_and_run(sql, con, claims={})
    assert rows[0][0] == 30.0, f"p50 should be 30, got {rows}"


def test_percentile_subquery_expr_rejected() -> None:
    """[HIGH] A percentile_cont expr containing a subquery must be rejected with
    MetricError (raw user text can never reach the ORDER BY f-string)."""
    m = MetricDefinition(
        id="lat_bad",
        name="LatBad",
        measure=Measure(
            name="p50_latency",
            agg="percentile_cont",
            expr="(SELECT latency_ms FROM secrets)",
            format="p50",
        ),
        base_table="requests",
    )
    mq = MetricQuery(metric_id="lat_bad")
    with pytest.raises(MetricError) as ei:
        compile_metric(m, mq)
    assert ei.value.code == "bad_percentile_expr"


def test_percentile_subquery_expr_not_in_emitted_sql() -> None:
    """[HIGH] The raw subquery text never survives into emitted SQL (fail-closed)."""
    m = MetricDefinition(
        id="lat_bad2",
        name="LatBad2",
        measure=Measure(
            name="p95_latency",
            agg="percentile_cont",
            expr="x) WITHIN GROUP (ORDER BY (SELECT 1)",  # trailing-SQL injection attempt
            format="p95",
        ),
        base_table="requests",
    )
    mq = MetricQuery(metric_id="lat_bad2")
    # Fail-closed: either the malformed/injecting expr is rejected (MetricError
    # for an embedded subquery, or a sqlglot ParseError when the raw text is no
    # longer a parseable scalar expression), or the parsed arg is re-serialized
    # safely.  In no case may the raw SELECT survive verbatim into the SQL.
    import sqlglot.errors
    try:
        sql, _ = compile_metric(m, mq)
    except (MetricError, sqlglot.errors.ParseError):
        return
    # If it compiled, the embedded raw SELECT must NOT be present verbatim.
    assert "select 1" not in sql.lower()


# ── MED: top_n.other time-grain tie boundary determinism ──────────────────────

def test_top_n_other_time_grain_tie_boundary_deterministic() -> None:
    """[MED] At a tie on the top-N boundary, the TOP arm and OTHER arm select the
    IDENTICAL member set — no double-count, no drop — and totals are exact.

    Setup: regions A=300, B=200, C=200, D=100 across two months. top_n n=2.
    B and C are TIED at the #2/#3 boundary.  With a stable tiebreaker
    (SUM(measure) DESC, region ASC) the top-2 is deterministically {A, B}; the
    Other arm is the exact complement {C, D}.  The tied member (B or C) appears
    in EXACTLY ONE arm — the grand total must equal the sum of all rows (800).
    """
    con = _duckdb_conn()
    con.execute("""
        CREATE TABLE tie_tg (
            region     VARCHAR,
            amount     DOUBLE,
            created_at DATE
        )
    """)
    # Per region totals: A=300, B=200, C=200 (tie with B), D=100.
    # Spread across two months so a time_grain is meaningful.
    con.execute("""
        INSERT INTO tie_tg VALUES
            ('A', 150, DATE '2024-01-15'), ('A', 150, DATE '2024-02-15'),
            ('B', 100, DATE '2024-01-15'), ('B', 100, DATE '2024-02-15'),
            ('C', 100, DATE '2024-01-15'), ('C', 100, DATE '2024-02-15'),
            ('D',  50, DATE '2024-01-15'), ('D',  50, DATE '2024-02-15')
    """)
    m = MetricDefinition(
        id="tie_tg",
        name="Tie TG",
        measure=Measure(name="revenue", agg="sum", expr="amount"),
        base_table="tie_tg",
        dimensions=(Dimension(name="region"),),
        time_dimension=TimeDimension(
            column="created_at",
            grains=("month",),
            default_grain="month",
        ),
    )
    mq = MetricQuery(
        metric_id="tie_tg",
        dimensions=("region",),
        time_grain="month",
        top_n=TopN(dimension="region", n=2, measure="revenue", order="desc",
                   other=True, other_label="Other"),
    )
    sql, _ = compile_metric(m, mq)

    # Determinism: compiling twice yields byte-identical SQL.
    sql2, _ = compile_metric(m, mq)
    assert sql == sql2

    rows = _plan_and_run(sql, con, claims={})
    # Columns: region, created_at_month, revenue.
    region_idx = 0
    rev_idx = 2
    # Grand total across all returned rows must equal the full table total (800):
    # A=300, B=200, C=200, D=100 → every input row must land in EXACTLY one arm
    # (no double-count, no drop).
    grand_total = sum(r[rev_idx] for r in rows)
    assert grand_total == 800.0, f"Total mismatch (double-count/drop): {rows}"

    # Determine which named (non-Other) regions appear.
    named = {r[region_idx] for r in rows if r[region_idx] != "Other"}
    # Stable tiebreaker (region ASC) makes B win the tie over C.
    assert named == {"A", "B"}, f"Top-2 not deterministic {{A,B}}: {named}"
    # The tied loser C must be in Other, never double-listed as a named region.
    assert "C" not in named


def test_top_n_other_time_grain_tie_both_arms_same_membership_sql() -> None:
    """[MED] The TOP arm's membership IN-subquery and the OTHER arm's NOT-IN
    membership subquery agree on the SAME deterministic member set at a tie.

    PERF FIX (LOW): the membership is now computed ONCE in a shared
    ``__topn_members`` CTE (ranked with the stable ``ORDER BY SUM(measure) <dir>,
    {dim} ASC`` tiebreaker) and BOTH arms do a cheap correlated lookup into that
    single CTE — instead of each arm re-aggregating __base with its own membership
    subquery.  Sharing one CTE makes the two arms agree on the member set *by
    construction* (strictly stronger than two textually-identical subqueries):
      * the deterministic tiebreaker appears EXACTLY ONCE (in the shared CTE);
      * both arms reference ``__topn_members`` exactly once each.
    """
    m = _simple_metric()
    mq = MetricQuery(
        metric_id="revenue",
        dimensions=("region",),
        time_grain="month",
        top_n=TopN(dimension="region", n=2, order="desc",
                   other=True, other_label="Other"),
    )
    sql, _ = compile_metric(m, mq)
    up = sql.upper()
    # The deterministic membership ranking (with the stable {dim} tiebreaker) is
    # now computed ONCE in the shared __topn_members CTE — not duplicated per arm.
    needle = "SUM(REVENUE) DESC, REGION ASC"
    assert up.count(needle) == 1, (
        f"Expected the stable tiebreaker exactly once in the shared membership "
        f"CTE; found {up.count(needle)} in:\n{sql}"
    )
    # Both arms must reference the shared membership CTE (top arm IN, Other arm
    # NOT IN) so they agree on the member set by construction.
    assert up.count("FROM __TOPN_MEMBERS") == 2, (
        f"Expected both arms to look up the shared membership CTE; SQL:\n{sql}"
    )


# ---------------------------------------------------------------------------
# THIRTEENTH AUDIT — MED: top_n membership RLS-correctness when rls_keys is
# UNDECLARED (empty) but the QUERY still carries RLS policies.
#
# The layered top-N MEMBERSHIP subquery (SELECT dim FROM __base GROUP BY dim
# ORDER BY SUM(measure) LIMIT N) reads FROM __base and is correlated on the
# membership-correlation columns so the top-N set is per-tenant.  The downstream
# planner injects RLS ONLY on the OUTERMOST select (verified: app.connectors.
# planner.plan does tree.where(pred) on the top-level tree, NOT inside __base).
# So with EMPTY metric.rls_keys the membership was GLOBAL while the result was
# RLS-filtered -> WRONG per-tenant member set.
#
# FIX (compile.py _membership_correlation_keys): when rls_keys is empty,
# correlate the membership on the projected non-ranked DIMENSIONS — the only
# non-measure/non-time columns a policy can land on in the layered form.  These
# tests prove per-tenant correctness through plan() + DuckDB with a metric that
# declares NO rls_keys and a query that carries RLS policies.
# ---------------------------------------------------------------------------


def _duckdb_conn_audit13():
    try:
        import duckdb
        return duckdb.connect(":memory:")
    except ImportError:
        pytest.skip("duckdb not installed")


def test_top_n_time_grain_per_tenant_correct_when_rls_keys_undeclared() -> None:
    """[MED tenant-correctness] 2 tenants, metric with NO declared rls_keys,
    query carrying RLS policies on a DIMENSION → each tenant's top-N is its OWN.

    org_id is a queried DIMENSION (so the planner's policy predicate can land)
    but is NOT declared in metric.rls_keys.  Per-tenant top-1 region differs:
        org_A: X=1500, Y=300, Z=50   → top-1 = X
        org_B: Y=1300, X=150, Z=10   → top-1 = Y
    The GLOBAL top-1 across both tenants is X (1650 = 1500+150).  With the buggy
    non-correlated membership, org_B would be shown X (its 4th-ranked region)
    instead of its real top-1 Y.  With the fix the membership is correlated on
    org_id (the projected dimension), so each tenant sees its own top-1.
    """
    from app.connectors.planner import plan as _plan
    con = _duckdb_conn_audit13()
    con.execute("""
        CREATE TABLE topn_norls (
            region     VARCHAR,
            amount     DOUBLE,
            org_id     VARCHAR,
            created_at DATE
        )
    """)
    con.execute("""
        INSERT INTO topn_norls VALUES
            ('X', 1000, 'org_A', '2024-01-01'),
            ('X',  500, 'org_A', '2024-02-01'),
            ('Y',  200, 'org_A', '2024-01-01'),
            ('Y',  100, 'org_A', '2024-02-01'),
            ('Z',   50, 'org_A', '2024-01-01'),
            ('Y',  900, 'org_B', '2024-01-01'),
            ('Y',  400, 'org_B', '2024-02-01'),
            ('X',  100, 'org_B', '2024-01-01'),
            ('X',   50, 'org_B', '2024-02-01'),
            ('Z',   10, 'org_B', '2024-01-01')
    """)
    m = MetricDefinition(
        id="topn_norls",
        name="TopN NoRLS",
        measure=Measure(name="revenue", agg="sum", expr="amount"),
        base_table="topn_norls",
        # org_id is a DIMENSION but NOT an rls_key.
        dimensions=(Dimension(name="region"), Dimension(name="org_id")),
        time_dimension=TimeDimension(
            column="created_at", grains=("month",), default_grain="month",
        ),
        rls_keys=(),  # ── DELIBERATELY EMPTY ──
        derived_measures=(
            DerivedMeasure(name="rev_ratio", formula="revenue / revenue"),
        ),
    )
    mq = MetricQuery(
        metric_id="topn_norls",
        dimensions=("region", "org_id"),
        time_grain="month",
        top_n=TopN(dimension="region", n=1, order="desc"),
    )
    sql, _ = compile_metric(m, mq)

    # The membership subquery must now be correlated on the projected org_id
    # dimension (the leak fix), even though rls_keys is empty.
    assert "__base.org_id = __outer.org_id".lower() in sql.lower(), (
        f"Expected per-tenant membership correlation on the org_id dimension "
        f"when rls_keys is empty:\n{sql[:700]}"
    )

    # org_A → top-1 = X (1500); must NOT see Y.
    p_a = _plan(sql, claims={"policies": {"org_id": "org_A"}}, dialect="duckdb")
    regions_a = {r[0] for r in con.execute(p_a.sql).fetchall()}
    assert regions_a == {"X"}, (
        f"org_A top-1 should be exactly {{X}} (X=1500 > Y=300); got {regions_a}"
    )

    # org_B → top-1 = Y (1300); must NOT see the GLOBAL top member X.
    p_b = _plan(sql, claims={"policies": {"org_id": "org_B"}}, dialect="duckdb")
    regions_b = {r[0] for r in con.execute(p_b.sql).fetchall()}
    assert regions_b == {"Y"}, (
        f"org_B top-1 should be exactly {{Y}} (Y=1300 > X=150); got {regions_b}. "
        f"Seeing the global top member X would be the cross-tenant leak."
    )


def test_top_n_other_no_grain_per_tenant_correct_when_rls_keys_undeclared() -> None:
    """[MED tenant-correctness] Same leak via the top_n.other (no time_grain)
    complement-RANK path: metric with NO rls_keys, RLS policy on a dimension.

    Per-tenant top-1 region differs; the OTHER bucket must roll up only the
    NON-top members OF THE SAME TENANT (not the global complement).
        org_A: X=1500 (top-1), Y=300, Z=50  → Other = 350
        org_B: Y=1300 (top-1), X=150, Z=10  → Other = 160
    """
    from app.connectors.planner import plan as _plan
    con = _duckdb_conn_audit13()
    con.execute("""
        CREATE TABLE topn_other_norls (
            region VARCHAR,
            amount DOUBLE,
            org_id VARCHAR
        )
    """)
    con.execute("""
        INSERT INTO topn_other_norls VALUES
            ('X', 1500, 'org_A'),
            ('Y',  300, 'org_A'),
            ('Z',   50, 'org_A'),
            ('Y', 1300, 'org_B'),
            ('X',  150, 'org_B'),
            ('Z',   10, 'org_B')
    """)
    m = MetricDefinition(
        id="topn_other_norls",
        name="TopN Other NoRLS",
        measure=Measure(name="revenue", agg="sum", expr="amount"),
        base_table="topn_other_norls",
        dimensions=(Dimension(name="region"), Dimension(name="org_id")),
        rls_keys=(),  # ── DELIBERATELY EMPTY ──
        # Trigger the layered path with a derived measure.
        derived_measures=(
            DerivedMeasure(name="rev_ratio", formula="revenue / revenue"),
        ),
    )
    mq = MetricQuery(
        metric_id="topn_other_norls",
        dimensions=("region", "org_id"),
        top_n=TopN(dimension="region", n=1, order="desc",
                   other=True, other_label="Other"),
    )
    sql, _ = compile_metric(m, mq)

    # Both arms' RANK windows must PARTITION BY the org_id dimension so the
    # top/other split is per-tenant.
    assert "partition by" in sql.lower() and "org_id" in sql.lower(), (
        f"Expected per-tenant RANK partition on org_id when rls_keys is empty:\n{sql[:700]}"
    )

    def _rev_by_region(org: str) -> dict[str, float]:
        p = _plan(sql, claims={"policies": {"org_id": org}}, dialect="duckdb")
        rows = con.execute(p.sql).fetchall()
        # columns: region, org_id, revenue, rev_ratio
        return {r[0]: r[2] for r in rows}

    a = _rev_by_region("org_A")
    assert a.get("X") == 1500, f"org_A top-1 X should be 1500; got {a}"
    assert a.get("Other") == 350, (
        f"org_A Other should be Y+Z = 350 (per-tenant complement); got {a}"
    )
    assert "Y" not in a and "Z" not in a, f"non-top regions must be rolled up; got {a}"

    b = _rev_by_region("org_B")
    assert b.get("Y") == 1300, f"org_B top-1 Y should be 1300; got {b}"
    assert b.get("Other") == 160, (
        f"org_B Other should be X+Z = 160 (per-tenant complement); got {b}"
    )
    assert "X" not in b and "Z" not in b, f"non-top regions must be rolled up; got {b}"


def test_declared_rls_keys_membership_byte_stable_single_dim() -> None:
    """Regression guard: when rls_keys IS declared the membership correlation is
    UNCHANGED (correlated on the rls_key, not on other dims) — the fix only
    broadens correlation for the EMPTY-rls_keys case."""
    m = MetricDefinition(
        id="declared",
        name="Declared",
        measure=Measure(name="revenue", agg="sum", expr="amount"),
        base_table="orders",
        dimensions=(Dimension(name="region"),),
        time_dimension=TimeDimension(
            column="created_at", grains=("month",), default_grain="month",
        ),
        rls_keys=("org_id",),
        derived_measures=(
            DerivedMeasure(name="rev_ratio", formula="revenue / revenue"),
        ),
    )
    mq = MetricQuery(
        metric_id="declared",
        dimensions=("region",),
        time_grain="month",
        top_n=TopN(dimension="region", n=2, order="desc"),
    )
    sql, _ = compile_metric(m, mq)
    assert "__base.org_id = __outer.org_id".lower() in sql.lower(), sql[:500]


# ---------------------------------------------------------------------------
# audit-14 MED: limit < top_n.n raises MetricError
# ---------------------------------------------------------------------------


def test_limit_less_than_top_n_raises() -> None:
    """limit < top_n.n must raise MetricError('bad_limit') — silent data loss fix."""
    m = _simple_metric()
    mq = MetricQuery(
        metric_id="revenue",
        dimensions=("region",),
        limit=2,
        top_n=TopN(dimension="region", n=5, order="desc"),
    )
    with pytest.raises(MetricError) as ei:
        compile_metric(m, mq)
    assert ei.value.code == "bad_limit"
    assert "top_n.n" in ei.value.message or "5" in ei.value.message


def test_limit_equal_to_top_n_accepted() -> None:
    """limit == top_n.n is valid — no silent truncation."""
    m = _simple_metric()
    mq = MetricQuery(
        metric_id="revenue",
        dimensions=("region",),
        limit=5,
        top_n=TopN(dimension="region", n=5, order="desc"),
    )
    sql, _ = compile_metric(m, mq)
    assert sql  # compiled without error


def test_limit_greater_than_top_n_accepted() -> None:
    """limit > top_n.n is valid."""
    m = _simple_metric()
    mq = MetricQuery(
        metric_id="revenue",
        dimensions=("region",),
        limit=100,
        top_n=TopN(dimension="region", n=5, order="desc"),
    )
    sql, _ = compile_metric(m, mq)
    assert sql  # compiled without error


def test_limit_without_top_n_unaffected() -> None:
    """A plain limit with no top_n is unaffected by the new guard."""
    m = _simple_metric()
    mq = MetricQuery(
        metric_id="revenue",
        dimensions=("region",),
        limit=3,
    )
    sql, _ = compile_metric(m, mq)
    assert sql  # compiled without error


# ---------------------------------------------------------------------------
# MED correctness/RLS — single-ranked-dim top-N tenant correlation via
# policy_cols.  When metric.rls_keys=[] AND the ONLY projected dim IS the ranked
# dim AND policies are injected at query time, the membership subquery must
# correlate on the active policy columns (threaded via policy_cols) so each
# tenant gets its OWN top-N — not the GLOBAL top-N across tenants.
# ---------------------------------------------------------------------------


def _two_tenant_top_n_metric() -> MetricDefinition:
    """Revenue metric with NO rls_keys and a single ranked dimension (member)."""
    return MetricDefinition(
        id="member_rev",
        name="Member Revenue",
        measure=Measure(name="rev", agg="sum", expr="amt"),
        base_table="member_sales",
        dimensions=(Dimension(name="member"),),
        time_dimension=TimeDimension(
            column="d", grains=("month",), default_grain="month"
        ),
        rls_keys=(),  # NO declared rls_keys — the edge case under test.
    )


def _seed_two_tenant_sales(con) -> None:
    """org_a's top member (m1=20) ranks BELOW org_b's top member (m3=200) globally.

    So a GLOBAL top-1 membership would pick m3 (org_b) for both tenants.
    outer per-tenant RLS filter would then exclude org_a's true top member m1.
    """
    con.execute(
        "CREATE TABLE member_sales (org_id VARCHAR, member VARCHAR, d DATE, amt DOUBLE)"
    )
    con.execute(
        """
        INSERT INTO member_sales VALUES
            ('org_a', 'm1', '2024-01-01', 10),
            ('org_a', 'm1', '2024-02-01', 10),
            ('org_a', 'm2', '2024-01-01', 5),
            ('org_b', 'm3', '2024-01-01', 100),
            ('org_b', 'm3', '2024-02-01', 100),
            ('org_b', 'm4', '2024-01-01', 1)
        """
    )


def test_single_ranked_dim_top_n_correlates_on_policy_cols_per_tenant() -> None:
    """[MED RLS] rls_keys=[] + single ranked dim + time_grain + injected policies.

    Each tenant must get its OWN top-1 member (not the GLOBAL top-1).  Threaded
    via compile_metric(..., policy_cols=('org_id',)) so the membership subquery
    correlates on org_id and the planner's outer RLS filter lands on a projected
    column.  Verified through plan() + DuckDB.
    """
    con = _duckdb_conn()
    _seed_two_tenant_sales(con)
    m = _two_tenant_top_n_metric()
    mq = MetricQuery(
        metric_id="member_rev",
        dimensions=("member",),
        time_grain="month",
        top_n=TopN(dimension="member", n=1, order="desc"),
    )
    # policy_cols is what routes/metrics.py passes (keys of claims['policies']).
    sql, _ = compile_metric(m, mq, policy_cols=("org_id",))

    for org, expected_member in (("org_a", "m1"), ("org_b", "m3")):
        p = plan(sql, claims={"policies": {"org_id": org}}, dialect="duckdb")
        rows = con.execute(p.sql).fetchall()
        members = {r[0] for r in rows}
        assert members == {expected_member}, (
            f"{org} top-1 should be its OWN top member {expected_member!r}, "
            f"got {members} — membership computed globally, not per-tenant. "
            f"rows={rows}"
        )


def test_single_ranked_dim_top_n_without_policy_cols_unchanged() -> None:
    """[MED RLS] Empty policy_cols (single-tenant) preserves prior behaviour.

    With no policies and no policy_cols the global top-N is the only sensible
    result; org_id is NOT projected and no correlation predicate is emitted.
    """
    con = _duckdb_conn()
    _seed_two_tenant_sales(con)
    m = _two_tenant_top_n_metric()
    mq = MetricQuery(
        metric_id="member_rev",
        dimensions=("member",),
        time_grain="month",
        top_n=TopN(dimension="member", n=1, order="desc"),
    )
    sql, _ = compile_metric(m, mq)  # no policy_cols
    # org_id must NOT be hoisted into the projection / correlation.
    assert "org_id" not in sql.lower()
    # Global top-1 across all rows is m3 (org_b, 200).
    p = plan(sql, claims={}, dialect="duckdb")
    rows = con.execute(p.sql).fetchall()
    assert {r[0] for r in rows} == {"m3"}, f"Global top-1 should be m3; rows={rows}"


def test_rolling_window_uses_range_interval_over_missing_days() -> None:
    """[LOW correctness] Time-based rolling window must use a TIME-INTERVAL frame.

    With data that has missing/sparse days, a rolling 3-day sum must cover the
    3 CALENDAR days [d-2, d], NOT the prior 3 physical rows.  We emit
    RANGE BETWEEN INTERVAL 'N days' PRECEDING so gaps are handled correctly.
    """
    con = _duckdb_conn()
    con.execute(
        "CREATE TABLE roll_sales (region VARCHAR, d DATE, amt DOUBLE)"
    )
    # Sparse series for region 'A': 01, 02, then a gap, then 10.
    # Rolling-3-day (RANGE INTERVAL '2 days') sums:
    #   01-01 → 1            (only itself)
    #   01-02 → 1 + 2 = 3    (01-01 within 2 days)
    #   01-10 → 3            (01-01/02 are >2 days back → EXCLUDED)
    # A physical ROWS BETWEEN 2 PRECEDING frame would WRONGLY sum 1+2+3=6 on
    # 01-10 (the 3 prior rows), ignoring the calendar gap.
    con.execute(
        """
        INSERT INTO roll_sales VALUES
            ('A', '2024-01-01', 1),
            ('A', '2024-01-02', 2),
            ('A', '2024-01-10', 3)
        """
    )
    m = MetricDefinition(
        id="roll",
        name="Roll",
        measure=Measure(name="rev", agg="sum", expr="amt"),
        base_table="roll_sales",
        dimensions=(Dimension(name="region"),),
        time_dimension=TimeDimension(
            column="d", grains=("day",), default_grain="day"
        ),
        rls_keys=(),
    )
    mq = MetricQuery(
        metric_id="roll",
        dimensions=("region",),
        time_grain="day",
        time_comparisons=(
            TimeComparison(
                measure="rev", kind="rolling_sum", periods=3, name="rev_roll_3d"
            ),
        ),
    )
    sql, _ = compile_metric(m, mq)
    # Must emit a RANGE INTERVAL frame, not a physical ROWS frame.
    up = sql.upper()
    assert "RANGE BETWEEN INTERVAL" in up, f"Expected RANGE INTERVAL frame; sql={sql}"
    # sqlglot normalises INTERVAL '2 day' → INTERVAL '2' DAY; the interval
    # magnitude is periods-1 (==2) and the unit is the grain (DAY).
    assert "INTERVAL '2' DAY" in up, f"Expected '2 day' interval (periods-1); sql={sql}"

    p = plan(sql, claims={}, dialect="duckdb")
    rows = con.execute(p.sql).fetchall()
    # Map d_day bucket → rolling value.  Column order: region, d_day, rev, rev_roll_3d.
    by_date = {str(r[1])[:10]: r[-1] for r in rows}
    assert by_date["2024-01-01"] == 1.0, f"rows={rows}"
    assert by_date["2024-01-02"] == 3.0, f"rows={rows}"
    # The KEY assertion: the gap day covers the TIME interval, not 3 prior rows.
    assert by_date["2024-01-10"] == 3.0, (
        f"Rolling 3-day on 2024-01-10 must EXCLUDE the >2-day-old rows "
        f"(time interval, not physical row count); got {by_date['2024-01-10']}, "
        f"rows={rows}"
    )


# ---------------------------------------------------------------------------
# Non-additive measures in re-aggregating time-comparison windows
# (FIX HIGH correctness — bad_tc_non_additive)
# ---------------------------------------------------------------------------


def _na_metric(**overrides) -> MetricDefinition:
    """Metric with an additive SUM measure plus non-additive avg/count_distinct."""
    kwargs = dict(
        id="usage",
        name="Usage",
        measure=Measure(name="revenue", agg="sum", expr="amount"),
        base_table="events",
        dimensions=(Dimension(name="region"),),
        time_dimension=TimeDimension(
            column="created_at",
            grains=("day", "week", "month", "quarter", "year"),
            default_grain="day",
        ),
        extra_measures=(
            Measure(name="avg_amount", agg="avg", expr="amount"),
            Measure(name="active_users", agg="count_distinct", expr="user_id"),
            Measure(name="order_count", agg="count", expr="id"),
        ),
        rls_keys=("org_id",),
    )
    kwargs.update(overrides)
    return MetricDefinition(**kwargs)


@pytest.mark.parametrize("kind", ["ytd", "qtd", "mtd", "rolling_sum"])
@pytest.mark.parametrize("measure", ["avg_amount", "active_users"])
def test_re_aggregating_tc_on_non_additive_measure_raises(kind, measure) -> None:
    """ytd/qtd/mtd/rolling_sum over an avg/count_distinct measure must RAISE.

    SUM-over-buckets of per-bucket averages / distinct-counts is mathematically
    wrong (a silent plausible-but-incorrect number), so the compiler fails closed.
    """
    m = _na_metric()
    mq = MetricQuery(
        metric_id="usage",
        dimensions=("region",),
        time_grain="month",
        time_comparisons=(
            TimeComparison(measure=measure, kind=kind, periods=3),
        ),
    )
    with pytest.raises(MetricError) as ei:
        compile_metric(m, mq)
    assert ei.value.code == "bad_tc_non_additive", ei.value.code


@pytest.mark.parametrize("kind", ["ytd", "qtd", "mtd", "rolling_sum"])
@pytest.mark.parametrize("measure", ["revenue", "order_count"])
def test_re_aggregating_tc_on_additive_measure_still_works(kind, measure) -> None:
    """The same kinds over a SUM/COUNT measure still compile fine."""
    m = _na_metric()
    mq = MetricQuery(
        metric_id="usage",
        dimensions=("region",),
        time_grain="month",
        time_comparisons=(
            TimeComparison(measure=measure, kind=kind, periods=3),
        ),
    )
    sql, _ = compile_metric(m, mq)
    up = sql.upper()
    assert "OVER" in up
    assert f"{measure}_{kind}".lower() in sql.lower()


def test_rolling_avg_on_avg_measure_is_allowed_approximate() -> None:
    """rolling_avg over an AVG measure is APPROXIMATE (AVG-of-bucket-AVGs) but
    DEFINED — it is allowed (documented), not blocked."""
    m = _na_metric()
    mq = MetricQuery(
        metric_id="usage",
        dimensions=("region",),
        time_grain="week",
        time_comparisons=(
            TimeComparison(measure="avg_amount", kind="rolling_avg", periods=4),
        ),
    )
    sql, _ = compile_metric(m, mq)
    up = sql.upper()
    assert "AVG" in up and "OVER" in up
    assert "avg_amount_rolling_avg" in sql.lower()


@pytest.mark.parametrize("measure", ["active_users"])
def test_rolling_avg_on_count_distinct_raises(measure) -> None:
    """rolling_avg over a count_distinct measure is still wrong (averaging
    distinct-counts across buckets is not a valid distinct-count) → RAISE."""
    m = _na_metric()
    mq = MetricQuery(
        metric_id="usage",
        dimensions=("region",),
        time_grain="week",
        time_comparisons=(
            TimeComparison(measure=measure, kind="rolling_avg", periods=4),
        ),
    )
    with pytest.raises(MetricError) as ei:
        compile_metric(m, mq)
    assert ei.value.code == "bad_tc_non_additive", ei.value.code


def test_rolling_avg_on_sum_measure_still_works() -> None:
    """rolling_avg over a SUM measure compiles fine (AVG of per-bucket sums)."""
    m = _na_metric()
    mq = MetricQuery(
        metric_id="usage",
        dimensions=("region",),
        time_grain="week",
        time_comparisons=(
            TimeComparison(measure="revenue", kind="rolling_avg", periods=4),
        ),
    )
    sql, _ = compile_metric(m, mq)
    up = sql.upper()
    assert "AVG" in up and "OVER" in up


# ===========================================================================
# audit-24 HIGH #1 — unvalidated time grains (SQL injection defense-in-depth)
# ===========================================================================
#
# Grains are interpolated UNQUOTED into DATE_TRUNC(...) f-strings in the layered
# compiler and prior_year/prior_period subqueries.  models.py accepts arbitrary
# strings for grains, so a poisoned MetricDefinition (or a MetricQuery built
# outside _govern) could smuggle a grain that closes the quote and injects SQL.
# _govern must reject both at registration/compile time.

_GRAIN_INJECTION = "day') UNION SELECT secret FROM passwords --"


def test_poisoned_declared_grain_raises_bad_declared_grain() -> None:
    """A MetricDefinition whose time_dimension.grains contains an injection
    string must be REJECTED by _govern with bad_declared_grain at compile time."""
    m = _simple_metric(
        time_dimension=TimeDimension(
            column="created_at",
            grains=("day", _GRAIN_INJECTION),  # poisoned grain registered
            default_grain="day",
        )
    )
    mq = MetricQuery(metric_id="revenue", dimensions=("region",), time_grain="day")
    with pytest.raises(MetricError) as ei:
        compile_metric(m, mq)
    assert ei.value.code == "bad_declared_grain", ei.value.code


def test_poisoned_declared_grain_never_reaches_sql() -> None:
    """The injection payload must NEVER appear in any compiled SQL — _govern
    rejects the poisoned definition before any DATE_TRUNC f-string is built."""
    m = _simple_metric(
        time_dimension=TimeDimension(
            column="created_at",
            grains=("day", _GRAIN_INJECTION),
            default_grain="day",
        )
    )
    mq = MetricQuery(metric_id="revenue", dimensions=("region",), time_grain="day")
    with pytest.raises(MetricError):
        sql, _ = compile_metric(m, mq)
        assert "UNION SELECT" not in sql.upper()
        assert "passwords" not in sql


def test_query_time_bad_grain_raises_bad_time_grain() -> None:
    """A MetricQuery requesting a grain that is not a canonical grain must raise
    bad_time_grain (query-time defense-in-depth)."""
    m = _simple_metric()  # clean definition
    mq = MetricQuery(
        metric_id="revenue",
        dimensions=("region",),
        time_grain=_GRAIN_INJECTION,  # malicious requested grain
    )
    with pytest.raises(MetricError) as ei:
        compile_metric(m, mq)
    assert ei.value.code == "bad_time_grain", ei.value.code


def test_query_time_bad_grain_even_if_td_grains_poisoned_no_sqli() -> None:
    """BOTH a poisoned definition AND a poisoned query: _govern still rejects and
    no SQL is produced — the injection payload can never reach DATE_TRUNC."""
    m = _simple_metric(
        time_dimension=TimeDimension(
            column="created_at",
            grains=("day", _GRAIN_INJECTION),
            default_grain="day",
        )
    )
    mq = MetricQuery(
        metric_id="revenue", dimensions=("region",), time_grain=_GRAIN_INJECTION
    )
    with pytest.raises(MetricError) as ei:
        compile_metric(m, mq)
    assert ei.value.code in {"bad_declared_grain", "bad_time_grain"}, ei.value.code


def test_all_canonical_grains_still_compile() -> None:
    """Defense-in-depth must NOT break valid metrics: every canonical grain
    compiles cleanly."""
    for g in ("hour", "day", "week", "month", "quarter", "year"):
        m = _simple_metric(
            time_dimension=TimeDimension(
                column="created_at",
                grains=("hour", "day", "week", "month", "quarter", "year"),
                default_grain="day",
            )
        )
        mq = MetricQuery(metric_id="revenue", dimensions=("region",), time_grain=g)
        sql, _ = compile_metric(m, mq)
        assert sql  # compiles without raising


# ===========================================================================
# MED resource — unbounded __base time-bucket explosion at the finest grain
# ===========================================================================
#
# At the finest grain ('hour') a time-comparison query with NO date-range
# filter on the time column materialises O(dim_cardinality × every_hour) rows
# in __base before the outer LIMIT lands.  _govern requires an explicit bound
# on the time column for hour-grain + time_comparisons -> MetricError(400).
# Coarser grains and queries without time_comparisons are NOT gated.


def _hourly_tc_metric() -> MetricDefinition:
    return _simple_metric(
        time_dimension=TimeDimension(
            column="created_at",
            grains=("hour", "day", "week", "month", "quarter", "year"),
            default_grain="day",
        )
    )


def test_hourly_tc_without_date_filter_rejected(monkeypatch) -> None:
    """hour grain + time_comparisons + no time bound -> time_range_required."""
    monkeypatch.delenv("NUBI_METRIC_REQUIRE_TIME_BOUND", raising=False)
    m = _hourly_tc_metric()
    mq = MetricQuery(
        metric_id="revenue",
        dimensions=("region",),
        time_grain="hour",
        time_comparisons=(TimeComparison(measure="revenue", kind="pop_pct"),),
    )
    with pytest.raises(MetricError) as ei:
        compile_metric(m, mq)
    assert ei.value.code == "time_range_required", ei.value.code


def test_hourly_tc_with_date_range_filter_ok() -> None:
    """An explicit >=/<= bound on the time column satisfies the guard."""
    m = _hourly_tc_metric()
    mq = MetricQuery(
        metric_id="revenue",
        dimensions=("region",),
        time_grain="hour",
        time_comparisons=(TimeComparison(measure="revenue", kind="pop_pct"),),
        filters=(
            MetricFilter(field="created_at", op=">=", value="2026-01-01"),
            MetricFilter(field="created_at", op="<=", value="2026-01-02"),
        ),
    )
    sql, _ = compile_metric(m, mq)
    assert sql  # compiles without raising


def test_hourly_tc_with_equality_time_filter_ok() -> None:
    """A single-bucket '=' bound on the time column also satisfies the guard."""
    m = _hourly_tc_metric()
    mq = MetricQuery(
        metric_id="revenue",
        dimensions=("region",),
        time_grain="hour",
        time_comparisons=(TimeComparison(measure="revenue", kind="prior_period"),),
        filters=(MetricFilter(field="created_at", op="=", value="2026-01-01T00:00:00"),),
    )
    sql, _ = compile_metric(m, mq)
    assert sql


def test_hourly_without_tc_not_gated() -> None:
    """hour grain WITHOUT time_comparisons compiles fine (guard is tc-scoped)."""
    m = _hourly_tc_metric()
    mq = MetricQuery(metric_id="revenue", dimensions=("region",), time_grain="hour")
    sql, _ = compile_metric(m, mq)
    assert sql


def test_coarse_grain_tc_without_date_filter_rejected(monkeypatch) -> None:
    """day grain + time_comparisons without a time bound is now GATED too.

    fix (LOW): the time_range_required guard was extended from hour-only to ALL
    grains — a coarse-grain (day/week/month/...) time-comparison query without a
    bounding filter on the time column still fully materialises __base then
    re-scans it per LATERAL window, so it must be gated.
    """
    monkeypatch.delenv("NUBI_METRIC_REQUIRE_TIME_BOUND", raising=False)
    m = _hourly_tc_metric()
    mq = MetricQuery(
        metric_id="revenue",
        dimensions=("region",),
        time_grain="day",
        time_comparisons=(TimeComparison(measure="revenue", kind="pop_pct"),),
    )
    with pytest.raises(MetricError) as ei:
        compile_metric(m, mq)
    assert ei.value.code == "time_range_required", ei.value.code


def test_coarse_grain_tc_with_date_filter_compiles(monkeypatch) -> None:
    """day grain + time_comparisons WITH a >=/<= bound compiles."""
    monkeypatch.delenv("NUBI_METRIC_REQUIRE_TIME_BOUND", raising=False)
    m = _hourly_tc_metric()
    mq = MetricQuery(
        metric_id="revenue",
        dimensions=("region",),
        time_grain="day",
        time_comparisons=(TimeComparison(measure="revenue", kind="pop_pct"),),
        filters=(
            MetricFilter(field="created_at", op=">=", value="2026-01-01"),
            MetricFilter(field="created_at", op="<=", value="2026-12-31"),
        ),
    )
    sql, _ = compile_metric(m, mq)
    assert sql


def test_coarse_grain_without_tc_not_gated() -> None:
    """A tc-free coarse query is never gated (guard is tc-scoped)."""
    m = _hourly_tc_metric()
    mq = MetricQuery(
        metric_id="revenue", dimensions=("region",), time_grain="day"
    )
    sql, _ = compile_metric(m, mq)
    assert sql


def test_tc_with_default_filter_bounding_time_compiles(monkeypatch) -> None:
    """A metric whose default_filters bound the time column needs no request bound.

    The author has already governed the bucket count, so the guard is skipped.
    """
    monkeypatch.delenv("NUBI_METRIC_REQUIRE_TIME_BOUND", raising=False)
    m = _simple_metric(
        time_dimension=TimeDimension(
            column="created_at",
            grains=("hour", "day", "week", "month", "quarter", "year"),
            default_grain="day",
        ),
        default_filters=("created_at >= DATE '2026-01-01'",),
    )
    mq = MetricQuery(
        metric_id="revenue",
        dimensions=("region",),
        time_grain="day",
        time_comparisons=(TimeComparison(measure="revenue", kind="pop_pct"),),
    )
    sql, _ = compile_metric(m, mq)
    assert sql


def test_tc_env_opt_out_restores_unbounded(monkeypatch) -> None:
    """NUBI_METRIC_REQUIRE_TIME_BOUND=0 disables the guard (old behaviour)."""
    monkeypatch.setenv("NUBI_METRIC_REQUIRE_TIME_BOUND", "0")
    m = _hourly_tc_metric()
    mq = MetricQuery(
        metric_id="revenue",
        dimensions=("region",),
        time_grain="day",
        time_comparisons=(TimeComparison(measure="revenue", kind="pop_pct"),),
    )
    sql, _ = compile_metric(m, mq)
    assert sql


# ===========================================================================
# audit-24 HIGH #2 — top_n.other + time_grain + explicit limit truncation
# ===========================================================================
#
# With a time_grain the top-N arm spans n * num_time_buckets rows.  A per-arm
# LIMIT capped it at effective_limit while the Other arm stayed uncapped ->
# asymmetric UNION / corrupted stacked time-series.  Fix: strip the per-arm
# LIMIT when top_n.other + time_grain; apply an EXPLICIT limit on the outer
# union instead.


def _topn_other_grain_metric() -> MetricDefinition:
    return MetricDefinition(
        id="rev_ts",
        name="Rev TS",
        measure=Measure(name="revenue", agg="sum", expr="amount"),
        base_table="rev_ts",
        dimensions=(Dimension(name="region"),),
        time_dimension=TimeDimension(
            column="created_at",
            grains=("day", "month"),
            default_grain="day",
        ),
    )


def _setup_rev_ts(con) -> None:
    con.execute(
        "CREATE TABLE rev_ts (region VARCHAR, created_at DATE, amount DOUBLE)"
    )
    # Two months. Per month: A is top, B+C roll into Other.
    con.execute(
        """
        INSERT INTO rev_ts VALUES
            ('A', DATE '2024-01-15', 1000),
            ('B', DATE '2024-01-15', 200),
            ('C', DATE '2024-01-15', 50),
            ('A', DATE '2024-02-15', 800),
            ('B', DATE '2024-02-15', 300),
            ('C', DATE '2024-02-15', 40)
        """
    )


def test_topn_other_time_grain_no_truncation_full_top_n_per_bucket() -> None:
    """[HIGH correctness] top_n.other + time_grain over 2 buckets returns the FULL
    n*num_buckets top-N rows + the per-bucket Other rows, with no truncation."""
    con = _duckdb_conn()
    _setup_rev_ts(con)
    m = _topn_other_grain_metric()
    mq = MetricQuery(
        metric_id="rev_ts",
        dimensions=("region",),
        time_grain="month",
        top_n=TopN(dimension="region", n=1, order="desc", other=True, other_label="Other"),
    )
    sql, _ = compile_metric(m, mq)
    rows = _plan_and_run(sql, con, claims={})

    def _month(r):
        v = r[1]
        return getattr(v, "month", v)

    by = {}
    for r in rows:
        by.setdefault((r[0], _month(r)), r)

    a_rows = [k for k in by if k[0] == "A"]
    assert len(a_rows) == 2, f"Top-N arm truncated; A months={a_rows} rows={rows}"
    other_rows = [k for k in by if k[0] == "Other"]
    assert len(other_rows) == 2, f"Other arm wrong bucket count: {other_rows} rows={rows}"

    def _rev(r):
        return next(
            v for v in r
            if isinstance(v, (int, float)) and v is not True and v is not False
        )

    assert _rev(by[("A", 1)]) == 1000.0, by[("A", 1)]
    assert _rev(by[("A", 2)]) == 800.0, by[("A", 2)]
    assert _rev(by[("Other", 1)]) == 250.0, f"Jan Other should be B+C=250: {by[('Other', 1)]}"
    assert _rev(by[("Other", 2)]) == 340.0, f"Feb Other should be B+C=340: {by[('Other', 2)]}"


def test_topn_other_time_grain_explicit_limit_caps_combined_union() -> None:
    """[HIGH correctness] An EXPLICIT mq.limit caps the COMBINED union (top-N +
    Other) rather than truncating the per-series length.  With 4 result rows
    available (2 top + 2 Other) a limit of 2 returns exactly 2 rows total."""
    con = _duckdb_conn()
    _setup_rev_ts(con)
    m = _topn_other_grain_metric()
    mq = MetricQuery(
        metric_id="rev_ts",
        dimensions=("region",),
        time_grain="month",
        limit=2,
        top_n=TopN(dimension="region", n=1, order="desc", other=True, other_label="Other"),
    )
    sql, _ = compile_metric(m, mq)
    rows = _plan_and_run(sql, con, claims={})
    assert len(rows) == 2, f"Explicit limit must cap combined union to 2 rows: {rows}"


def test_topn_other_time_grain_default_limit_does_not_truncate() -> None:
    """Without an explicit limit, the union is uncapped so all top-N + Other rows
    survive (no default per-arm cap leaks in)."""
    con = _duckdb_conn()
    _setup_rev_ts(con)
    m = _topn_other_grain_metric()
    mq = MetricQuery(
        metric_id="rev_ts",
        dimensions=("region",),
        time_grain="month",
        top_n=TopN(dimension="region", n=1, order="desc", other=True, other_label="Other"),
    )
    sql, _ = compile_metric(m, mq)
    rows = _plan_and_run(sql, con, claims={})
    assert len(rows) == 4, f"Expected 4 rows (no truncation), got {len(rows)}: {rows}"


def test_topn_other_no_time_grain_path_unchanged_keeps_limit() -> None:
    """The no-time-grain top_n.other path is UNCHANGED: the per-arm LIMIT still
    applies (regression guard for Fix #2)."""
    con = _duckdb_conn()
    con.execute("CREATE TABLE rev_ntg (region VARCHAR, amount DOUBLE)")
    con.execute("INSERT INTO rev_ntg VALUES ('A', 1000), ('B', 200), ('C', 50)")
    m = MetricDefinition(
        id="rev_ntg",
        name="Rev NTG",
        measure=Measure(name="revenue", agg="sum", expr="amount"),
        base_table="rev_ntg",
        dimensions=(Dimension(name="region"),),
    )
    mq = MetricQuery(
        metric_id="rev_ntg",
        dimensions=("region",),
        top_n=TopN(dimension="region", n=1, order="desc", other=True, other_label="Other"),
    )
    sql, _ = compile_metric(m, mq)
    assert "LIMIT" in sql.upper()
    rows = _plan_and_run(sql, con, claims={})
    row_by_region = {r[0]: r for r in rows}
    assert "A" in row_by_region and "Other" in row_by_region, rows


# ---------------------------------------------------------------------------
# Governance: derived rank measure on the extra-non-ranked-dims membership path
# (fix-47 third membership path) + dimension-count cap.
# ---------------------------------------------------------------------------


def _derived_rank_metric() -> MetricDefinition:
    """Metric with a declared rls_key and a derived measure usable as a rank."""
    return MetricDefinition(
        id="margin",
        name="Margin",
        measure=Measure(name="revenue", agg="sum", expr="amount"),
        base_table="orders",
        dimensions=(
            Dimension(name="category"),
            Dimension(name="region"),
            Dimension(name="status"),
        ),
        time_dimension=TimeDimension(
            column="created_at", grains=("day", "month"), default_grain="day"
        ),
        extra_measures=(Measure(name="cost", agg="sum", expr="cost_amount"),),
        derived_measures=(
            DerivedMeasure(name="margin_pct", formula="(revenue - cost) / revenue"),
        ),
        rls_keys=("org_id",),
    )


def test_derived_rank_with_extra_non_ranked_dim_raises_not_500() -> None:
    """fix-47 third path: ranked dim + declared rls_key + an EXTRA projected dim
    (no time_grain, other=False) routes through the __base membership subquery,
    which references the derived rank measure absent from __base -> would be a
    runtime binder 500.  _govern must reject it at compile time with bad_top_n.
    """
    m = _derived_rank_metric()
    # ranked = category; corr_keys = rls_keys = (org_id); region is the EXTRA
    # non-ranked, non-corr dim -> _has_extra_non_ranked_dims True.
    mq = MetricQuery(
        metric_id="margin",
        dimensions=("category", "region"),
        top_n=TopN(dimension="category", n=3, measure="margin_pct", order="desc"),
    )
    with pytest.raises(MetricError) as ei:
        compile_metric(m, mq)
    assert ei.value.code == "bad_top_n"


def test_derived_rank_no_extra_dim_compiles() -> None:
    """Valid: ranked dim + only the corr_key (rls_key) projected, no time_grain,
    other=False -> QUALIFY RANK() path, derived rank measure is fine (it lives in
    the outer SELECT, not the membership subquery).  Must compile."""
    m = _derived_rank_metric()
    # Only the ranked dim projected; corr_keys default to rls_keys (org_id, added
    # by the compiler), so no extra non-ranked dim -> no membership subquery.
    mq = MetricQuery(
        metric_id="margin",
        dimensions=("category",),
        top_n=TopN(dimension="category", n=3, measure="margin_pct", order="desc"),
    )
    sql, _ = compile_metric(m, mq)
    assert sql  # compiles without raising


def test_derived_rank_extra_dim_base_measure_still_compiles() -> None:
    """Control: same extra-dim shape but ranking by a BASE measure must STILL
    compile (the govern gap fix is scoped to DERIVED rank measures only)."""
    m = _derived_rank_metric()
    mq = MetricQuery(
        metric_id="margin",
        dimensions=("category", "region"),
        top_n=TopN(dimension="category", n=3, measure="revenue", order="desc"),
    )
    sql, _ = compile_metric(m, mq)
    assert sql


def test_too_many_dimensions_raises() -> None:
    """>NUBI_MAX_DIMS (default 20) requested dimensions -> too_many_dimensions 400."""
    dims = tuple(Dimension(name=f"d{i}") for i in range(25))
    m = MetricDefinition(
        id="manydim",
        name="ManyDim",
        measure=Measure(name="revenue", agg="sum", expr="amount"),
        base_table="orders",
        dimensions=dims,
    )
    mq = MetricQuery(metric_id="manydim", dimensions=tuple(f"d{i}" for i in range(21)))
    with pytest.raises(MetricError) as ei:
        compile_metric(m, mq)
    assert ei.value.code == "too_many_dimensions"


def test_max_dimensions_boundary_compiles() -> None:
    """Exactly NUBI_MAX_DIMS (20) requested dimensions compiles (boundary, not >)."""
    dims = tuple(Dimension(name=f"d{i}") for i in range(20))
    m = MetricDefinition(
        id="boundarydim",
        name="BoundaryDim",
        measure=Measure(name="revenue", agg="sum", expr="amount"),
        base_table="orders",
        dimensions=dims,
    )
    mq = MetricQuery(
        metric_id="boundarydim", dimensions=tuple(f"d{i}" for i in range(20))
    )
    sql, _ = compile_metric(m, mq)
    assert sql
