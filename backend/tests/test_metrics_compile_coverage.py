"""Additional coverage tests for app/metrics/compile.py (was at 53%).

Target missing lines / branches:
- Layered path: derived_measures force layered, time_comparisons path
- Governance: too_many_filters, too_many_tc_entries, in_list_too_large,
  time_range_required guard, bad_tc_name, unknown_dimension_in_query
- Edge cases: percentile_cont, approx_count_distinct, avg agg
- _default_filters_bound_time: bound filter in default_filters
- Policy-col hoisting for RLS soundness (layered top-N)
- top_n with other=True (union path)
- Time-comparison kinds: prior_period, prior_year, ytd, qtd, mtd, rolling_sum
"""

from __future__ import annotations

import os
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
# Helpers
# ---------------------------------------------------------------------------

def _revenue_metric(**overrides) -> MetricDefinition:
    kwargs = dict(
        id="revenue",
        name="Revenue",
        measure=Measure(name="revenue", agg="sum", expr="amount"),
        base_table="orders",
        dimensions=(Dimension(name="region"), Dimension(name="status")),
        time_dimension=TimeDimension(
            column="created_at",
            grains=("day", "month", "year", "week", "quarter", "hour"),
            default_grain="day",
        ),
        default_filters=("is_test = FALSE",),
        rls_keys=("org_id",),
    )
    kwargs.update(overrides)
    return MetricDefinition(**kwargs)


def _mq(**overrides) -> MetricQuery:
    return MetricQuery(metric_id="revenue", **overrides)


# ---------------------------------------------------------------------------
# Governance: resource caps
# ---------------------------------------------------------------------------

class TestGovernanceResourceCaps:
    def test_too_many_filters_raises(self):
        metric = _revenue_metric()
        filters = tuple(
            MetricFilter(field="region", op="=", value=f"r{i}")
            for i in range(60)  # default cap is 50
        )
        with pytest.raises(MetricError) as exc_info:
            compile_metric(metric, _mq(filters=filters))
        assert exc_info.value.code == "too_many_filters"

    def test_in_list_too_large_raises(self):
        metric = _revenue_metric()
        big_list = [f"val_{i}" for i in range(1100)]  # default cap is 1000
        f = MetricFilter(field="region", op="in", value=big_list)
        with pytest.raises(MetricError) as exc_info:
            compile_metric(metric, _mq(filters=(f,)))
        assert exc_info.value.code == "in_list_too_large"

    def test_too_many_tc_entries_raises(self, monkeypatch):
        """Exceed the _MAX_TC_ENTRIES cap."""
        monkeypatch.setenv("NUBI_MAX_TC_ENTRIES", "2")
        import importlib
        import app.metrics.compile as compile_mod
        importlib.reload(compile_mod)
        metric = _revenue_metric()
        # Reimport compile_metric after reload
        from app.metrics.compile import compile_metric as _cm
        comparisons = tuple(
            TimeComparison(measure="revenue", kind="prior_period")
            for _ in range(3)
        )
        mq = _mq(
            time_grain="month",
            time_comparisons=comparisons,
            filters=(MetricFilter(field="created_at", op=">=", value="2024-01-01"),),
        )
        with pytest.raises(MetricError) as exc_info:
            _cm(metric, mq)
        assert exc_info.value.code == "too_many_tc_entries"
        importlib.reload(compile_mod)  # restore

    def test_top_n_exceeds_max_raises(self, monkeypatch):
        monkeypatch.setenv("NUBI_MAX_TOP_N", "5")
        import importlib
        import app.metrics.compile as compile_mod
        importlib.reload(compile_mod)
        from app.metrics.compile import compile_metric as _cm
        metric = _revenue_metric()
        mq = _mq(
            dimensions=("region",),
            top_n=TopN(dimension="region", n=10),
        )
        with pytest.raises(MetricError) as exc_info:
            _cm(metric, mq)
        assert exc_info.value.code == "bad_top_n"
        importlib.reload(compile_mod)  # restore


# ---------------------------------------------------------------------------
# Governance: time range required for time_comparisons
# ---------------------------------------------------------------------------

class TestTimeRangeRequired:
    def test_tc_without_time_bound_raises(self):
        metric = _revenue_metric()
        mq = _mq(
            time_grain="month",
            time_comparisons=(
                TimeComparison(measure="revenue", kind="prior_period"),
            ),
            # No time-bound filter → should raise
        )
        with pytest.raises(MetricError) as exc_info:
            compile_metric(metric, mq)
        assert exc_info.value.code == "time_range_required"

    def test_tc_with_time_bound_filter_passes(self):
        metric = _revenue_metric()
        mq = _mq(
            time_grain="month",
            time_comparisons=(
                TimeComparison(measure="revenue", kind="prior_period"),
            ),
            filters=(MetricFilter(field="created_at", op=">=", value="2024-01-01"),),
        )
        # Should not raise
        sql, params = compile_metric(metric, mq)
        assert "__base" in sql.upper() or "WITH" in sql.upper()

    def test_tc_disabled_via_env_skips_guard(self, monkeypatch):
        monkeypatch.setenv("NUBI_METRIC_REQUIRE_TIME_BOUND", "0")
        metric = _revenue_metric()
        mq = _mq(
            time_grain="month",
            time_comparisons=(
                TimeComparison(measure="revenue", kind="prior_period"),
            ),
            # No time bound filter — but guard is disabled
        )
        # Should not raise
        sql, params = compile_metric(metric, mq)
        assert sql

    def test_tc_with_eq_filter_satisfies_bound(self):
        metric = _revenue_metric()
        mq = _mq(
            time_grain="month",
            time_comparisons=(
                TimeComparison(measure="revenue", kind="ytd"),
            ),
            filters=(MetricFilter(field="created_at", op="=", value="2024-06-01"),),
        )
        sql, params = compile_metric(metric, mq)
        assert sql

    def test_default_filters_with_time_bound_satisfies_guard(self):
        """When default_filters includes a bounding predicate on the time col,
        the time_range_required guard should be skipped."""
        metric = _revenue_metric(
            default_filters=("created_at >= '2024-01-01'",),
        )
        mq = _mq(
            time_grain="month",
            time_comparisons=(
                TimeComparison(measure="revenue", kind="prior_period"),
            ),
            # No explicit request-level time filter; default_filter provides the bound
        )
        # Should not raise
        sql, params = compile_metric(metric, mq)
        assert sql


# ---------------------------------------------------------------------------
# Governance: bad_tc_name
# ---------------------------------------------------------------------------

class TestBadTcName:
    def test_invalid_tc_name_raises(self):
        metric = _revenue_metric()
        tc = TimeComparison(
            measure="revenue",
            kind="prior_period",
            name="bad name with spaces",  # not a valid identifier
        )
        mq = _mq(
            time_grain="month",
            time_comparisons=(tc,),
            filters=(MetricFilter(field="created_at", op=">=", value="2024-01-01"),),
        )
        with pytest.raises(MetricError) as exc_info:
            compile_metric(metric, mq)
        assert exc_info.value.code == "bad_tc_name"

    def test_valid_tc_name_passes(self):
        metric = _revenue_metric()
        tc = TimeComparison(
            measure="revenue",
            kind="prior_period",
            name="revenue_prev",
        )
        mq = _mq(
            time_grain="month",
            time_comparisons=(tc,),
            filters=(MetricFilter(field="created_at", op=">=", value="2024-01-01"),),
        )
        sql, params = compile_metric(metric, mq)
        assert "revenue_prev" in sql


# ---------------------------------------------------------------------------
# Layered path: derived_measures
# ---------------------------------------------------------------------------

class TestLayeredPathDerivedMeasures:
    def test_derived_measure_forces_layered_path(self):
        dm = DerivedMeasure(name="revenue_per_order", formula="revenue / order_count")
        m_order_count = Measure(name="order_count", agg="count", expr="*")
        metric = _revenue_metric(
            extra_measures=(m_order_count,),
            derived_measures=(dm,),
        )
        sql, params = compile_metric(metric, _mq())
        # Layered path includes __base CTE
        assert "__base" in sql

    def test_derived_measure_with_division_uses_nullif(self):
        dm = DerivedMeasure(name="avg_order", formula="revenue / order_count")
        m_count = Measure(name="order_count", agg="count", expr="*")
        metric = _revenue_metric(
            extra_measures=(m_count,),
            derived_measures=(dm,),
        )
        sql, params = compile_metric(metric, _mq())
        # Division denominator should be wrapped with NULLIF
        assert "NULLIF" in sql.upper()

    def test_derived_measure_governance_bad_identifier_in_formula(self):
        dm = DerivedMeasure(name="bad", formula="revenue + nonexistent_measure")
        metric = _revenue_metric(derived_measures=(dm,))
        with pytest.raises(MetricError) as exc_info:
            compile_metric(metric, _mq())
        assert exc_info.value.code == "bad_formula_identifier"

    def test_derived_measure_governance_bad_token_in_formula(self):
        """A formula containing a SQL keyword injection attempt is rejected."""
        dm = DerivedMeasure(name="evil", formula="revenue; DROP TABLE orders --")
        metric = _revenue_metric(derived_measures=(dm,))
        with pytest.raises(MetricError) as exc_info:
            compile_metric(metric, _mq())
        assert exc_info.value.code in ("bad_formula", "bad_formula_identifier", "bad_formula_token")


# ---------------------------------------------------------------------------
# Aggregate types: percentile_cont, approx_count_distinct
# ---------------------------------------------------------------------------

class TestSpecialAggregates:
    def test_percentile_cont_emits_correct_sql(self):
        metric = MetricDefinition(
            id="p50_metric",
            name="P50 Latency",
            measure=Measure(name="p50_latency", agg="percentile_cont", expr="latency_ms", format="p50"),
            base_table="events",
        )
        sql, params = compile_metric(metric, MetricQuery(metric_id="p50_metric"))
        # DuckDB may render as PERCENTILE_CONT or QUANTILE_CONT
        assert "PERCENTILE_CONT" in sql.upper() or "QUANTILE_CONT" in sql.upper()
        assert "0.5" in sql

    def test_approx_count_distinct_emits_correct_sql(self):
        metric = MetricDefinition(
            id="approx_metric",
            name="Approx Distinct Users",
            measure=Measure(name="approx_users", agg="approx_count_distinct", expr="user_id"),
            base_table="events",
        )
        sql, params = compile_metric(metric, MetricQuery(metric_id="approx_metric"))
        assert "APPROX_COUNT_DISTINCT" in sql.upper()

    def test_avg_agg_emits_avg(self):
        metric = MetricDefinition(
            id="avg_metric",
            name="Avg Order Value",
            measure=Measure(name="avg_order", agg="avg", expr="amount"),
            base_table="orders",
        )
        sql, params = compile_metric(metric, MetricQuery(metric_id="avg_metric"))
        assert "AVG" in sql.upper()


# ---------------------------------------------------------------------------
# Time comparisons: various kinds
# ---------------------------------------------------------------------------

class TestTimeComparisonKinds:
    def _mq_with_tc(self, kind, time_grain="month", periods=1):
        return _mq(
            time_grain=time_grain,
            time_comparisons=(
                TimeComparison(measure="revenue", kind=kind, periods=periods),
            ),
            filters=(MetricFilter(field="created_at", op=">=", value="2024-01-01"),),
        )

    def test_prior_period_emits_lateral(self):
        metric = _revenue_metric()
        sql, _ = compile_metric(metric, self._mq_with_tc("prior_period"))
        # LATERAL or correlated subquery reference
        assert "LATERAL" in sql.upper() or "revenue_prior_period" in sql

    def test_pop_abs_emits_difference(self):
        metric = _revenue_metric()
        sql, _ = compile_metric(metric, self._mq_with_tc("pop_abs"))
        assert "revenue_pop_abs" in sql

    def test_pop_pct_emits_division(self):
        metric = _revenue_metric()
        sql, _ = compile_metric(metric, self._mq_with_tc("pop_pct"))
        assert "NULLIF" in sql.upper() or "revenue_pop_pct" in sql

    def test_prior_year_emits_lateral(self):
        metric = _revenue_metric()
        sql, _ = compile_metric(metric, self._mq_with_tc("prior_year"))
        assert "revenue_prior_year" in sql

    def test_yoy_abs(self):
        metric = _revenue_metric()
        sql, _ = compile_metric(metric, self._mq_with_tc("yoy_abs"))
        assert "revenue_yoy_abs" in sql

    def test_yoy_pct(self):
        metric = _revenue_metric()
        sql, _ = compile_metric(metric, self._mq_with_tc("yoy_pct"))
        assert "NULLIF" in sql.upper() or "revenue_yoy_pct" in sql

    def test_ytd_emits_window_function(self):
        metric = _revenue_metric()
        sql, _ = compile_metric(metric, self._mq_with_tc("ytd"))
        assert "SUM" in sql.upper()
        assert "UNBOUNDED" in sql.upper()

    def test_qtd_emits_window_function(self):
        metric = _revenue_metric()
        sql, _ = compile_metric(metric, self._mq_with_tc("qtd"))
        assert "UNBOUNDED" in sql.upper()

    def test_mtd_emits_window_function(self):
        metric = _revenue_metric()
        sql, _ = compile_metric(metric, self._mq_with_tc("mtd"))
        assert "UNBOUNDED" in sql.upper()

    def test_rolling_sum_emits_window(self):
        metric = _revenue_metric()
        sql, _ = compile_metric(metric, self._mq_with_tc("rolling_sum", periods=3))
        assert "revenue_rolling_sum" in sql

    def test_rolling_avg_emits_window(self):
        metric = _revenue_metric()
        sql, _ = compile_metric(metric, self._mq_with_tc("rolling_avg", periods=3))
        assert "revenue_rolling_avg" in sql


# ---------------------------------------------------------------------------
# Top-N with other=True (union path)
# ---------------------------------------------------------------------------

class TestTopNWithOther:
    def test_top_n_with_other_emits_union(self):
        metric = _revenue_metric()
        mq = _mq(
            dimensions=("region",),
            top_n=TopN(dimension="region", n=3, other=True),
        )
        sql, params = compile_metric(metric, mq)
        # Union path required
        assert "UNION" in sql.upper()

    def test_top_n_other_label_in_sql(self):
        metric = _revenue_metric()
        mq = _mq(
            dimensions=("region",),
            top_n=TopN(dimension="region", n=3, other=True, other_label="Everything Else"),
        )
        sql, params = compile_metric(metric, mq)
        assert "Everything Else" in sql

    def test_top_n_without_other_no_union(self):
        metric = _revenue_metric()
        mq = _mq(
            dimensions=("region",),
            top_n=TopN(dimension="region", n=3, other=False),
        )
        sql, params = compile_metric(metric, mq)
        assert "UNION" not in sql.upper()


# ---------------------------------------------------------------------------
# RLS projectable check in layered path
# ---------------------------------------------------------------------------

class TestRLSProjectable:
    def test_rls_key_added_as_extra_dim_in_layered_path(self):
        """When rls_key is not in mq.dimensions, it must still appear in __base."""
        metric = _revenue_metric(rls_keys=("org_id",))
        dm = DerivedMeasure(name="revenue_fmt", formula="revenue")
        metric = MetricDefinition(
            **{**dict(
                id="revenue",
                name="Revenue",
                measure=Measure(name="revenue", agg="sum", expr="amount"),
                base_table="orders",
                dimensions=(Dimension(name="region"),),
                rls_keys=("org_id",),
                derived_measures=(dm,),
            )}
        )
        mq = MetricQuery(metric_id="revenue", dimensions=("region",))
        sql, params = compile_metric(metric, mq)
        # org_id must appear in the __base projection
        assert "org_id" in sql


# ---------------------------------------------------------------------------
# Edge cases: empty / null inputs
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_no_dimensions_compiles_to_aggregate_only(self):
        metric = MetricDefinition(
            id="total",
            name="Total Revenue",
            measure=Measure(name="revenue", agg="sum", expr="amount"),
            base_table="orders",
        )
        sql, params = compile_metric(metric, MetricQuery(metric_id="total"))
        assert "SUM(amount)" in sql.lower() or "SUM(AMOUNT)" in sql.upper()

    def test_base_sql_wrapped_as_derived_table(self):
        metric = MetricDefinition(
            id="agg",
            name="Aggregated",
            measure=Measure(name="revenue", agg="sum", expr="amount"),
            base_sql="SELECT amount, region FROM raw_orders WHERE is_complete = TRUE",
        )
        sql, params = compile_metric(metric, MetricQuery(metric_id="agg"))
        # Base SQL used as subquery
        assert "raw_orders" in sql.lower()

    def test_limit_applied_in_flat_path(self):
        metric = MetricDefinition(
            id="limited",
            name="Limited",
            measure=Measure(name="revenue", agg="sum", expr="amount"),
            base_table="orders",
        )
        mq = MetricQuery(metric_id="limited", limit=50)
        sql, params = compile_metric(metric, mq)
        assert "50" in sql

    def test_default_limit_applied_when_none(self):
        """When mq.limit is None, the default cap is applied."""
        metric = MetricDefinition(
            id="unlimited",
            name="Unlimited",
            measure=Measure(name="revenue", agg="sum", expr="amount"),
            base_table="orders",
        )
        sql, params = compile_metric(metric, MetricQuery(metric_id="unlimited"))
        # Default limit _DEFAULT_LIMIT should be applied
        assert "LIMIT" in sql.upper()

    def test_scalar_filter_value_becomes_param(self):
        """User filter values must be params, never inlined SQL."""
        metric = _revenue_metric()
        mq = _mq(filters=(MetricFilter(field="region", op="=", value="Western Cape"),))
        sql, params = compile_metric(metric, mq)
        # The literal "Western Cape" must NOT appear raw in the SQL
        assert "Western Cape" not in sql
        # The value must be in params
        assert any(v == "Western Cape" for v in params.values())
