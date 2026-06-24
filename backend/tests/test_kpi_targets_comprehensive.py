"""Comprehensive edge-case tests for KPI target compilation.

These tests extend test_metrics_kpi_targets.py with boundary / edge-case coverage.
They are pure unit tests — no async, no HTTP, no DB, no fixtures imported from conftest.
"""

from __future__ import annotations

import pytest

from app.metrics.compile import compile_metric
from app.metrics.models import (
    DerivedMeasure,
    Dimension,
    Measure,
    MetricDefinition,
    MetricTarget,
    MetricQuery,
    TimeDimension,
)


# ---------------------------------------------------------------------------
# Helpers (self-contained — no conftest dependency)
# ---------------------------------------------------------------------------


def _revenue_metric(**overrides) -> MetricDefinition:
    """Revenue metric over orders — no target by default."""
    kwargs = dict(
        id="revenue",
        name="Revenue",
        measure=Measure(name="revenue", agg="sum", expr="amount"),
        base_table="orders",
        dimensions=(Dimension(name="region"),),
    )
    kwargs.update(overrides)
    return MetricDefinition(**kwargs)


def _sql_up(metric: MetricDefinition, **mq_kwargs) -> str:
    mq = MetricQuery(metric_id=metric.id, **mq_kwargs)
    sql, _ = compile_metric(metric, mq)
    return sql.upper()


def _sql_params(metric: MetricDefinition, **mq_kwargs):
    mq = MetricQuery(metric_id=metric.id, **mq_kwargs)
    return compile_metric(metric, mq)


# ---------------------------------------------------------------------------
# RAG boundary: parametrize amber_threshold values (higher_is_better)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "amber_threshold,expected_fragment",
    [
        (0.8, "0.8"),
        (0.5, "0.5"),
        (1.0, "1.0"),
        (0.0, "0.0"),
        (0.9, "0.9"),
        (0.75, "0.75"),
    ],
)
def test_higher_is_better_amber_threshold_in_sql(
    amber_threshold: float, expected_fragment: str
) -> None:
    """The amber_threshold literal must appear verbatim in the compiled SQL."""
    target = MetricTarget(
        value="1000",
        direction="higher_is_better",
        amber_threshold=amber_threshold,
    )
    m = _revenue_metric(target=target)
    sql = _sql_up(m)
    assert expected_fragment in sql, (
        f"Expected amber_threshold fragment {expected_fragment!r} in SQL"
    )


# ---------------------------------------------------------------------------
# RAG boundary: parametrize amber_threshold values (lower_is_better)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "amber_threshold,expected_fragment",
    [
        (0.9, "0.9"),
        (0.7, "0.7"),
        (0.5, "0.5"),
        (0.8, "0.8"),
    ],
)
def test_lower_is_better_amber_threshold_in_sql(
    amber_threshold: float, expected_fragment: str
) -> None:
    """lower_is_better: amber_threshold literal must appear in the SQL."""
    target = MetricTarget(
        value="500",
        direction="lower_is_better",
        amber_threshold=amber_threshold,
    )
    m = _revenue_metric(target=target)
    sql = _sql_up(m)
    assert expected_fragment in sql, (
        f"Expected amber_threshold fragment {expected_fragment!r} in SQL (lower_is_better)"
    )


# ---------------------------------------------------------------------------
# pct_to_goal with target=0 — NULLIF guard
# ---------------------------------------------------------------------------


def test_pct_to_goal_target_zero_has_nullif() -> None:
    """MetricTarget(value='0') must still compile and emit NULLIF to guard /0."""
    target = MetricTarget(value="0")
    m = _revenue_metric(target=target)
    sql, params = _sql_params(m)
    assert "NULLIF" in sql.upper(), "NULLIF guard must be present even when target=0"


def test_pct_to_goal_target_zero_compiles_without_error() -> None:
    """Compiling with target=0 must not raise."""
    target = MetricTarget(value="0")
    m = _revenue_metric(target=target)
    sql, _ = compile_metric(m, MetricQuery(metric_id="revenue"))
    assert "REVENUE_PCT_TO_GOAL" in sql.upper()


# ---------------------------------------------------------------------------
# Target referencing a non-primary measure (target.measure override)
# ---------------------------------------------------------------------------


def test_target_measure_override_emits_correct_prefix() -> None:
    """When target.measure='cost', columns should be COST_TARGET, not REVENUE_TARGET."""
    target = MetricTarget(value="500", measure="cost")
    m = _revenue_metric(
        target=target,
        extra_measures=(Measure(name="cost", agg="sum", expr="cost_amount"),),
    )
    sql = _sql_up(m)
    assert "COST_TARGET" in sql
    assert "COST_RAG" in sql
    assert "COST_VS_TARGET" in sql
    assert "COST_PCT_TO_GOAL" in sql


def test_target_measure_override_no_primary_target_columns() -> None:
    """When target.measure='cost', REVENUE_TARGET must NOT appear in the SQL."""
    target = MetricTarget(value="500", measure="cost")
    m = _revenue_metric(
        target=target,
        extra_measures=(Measure(name="cost", agg="sum", expr="cost_amount"),),
    )
    sql = _sql_up(m)
    assert "REVENUE_TARGET" not in sql
    assert "REVENUE_RAG" not in sql


# ---------------------------------------------------------------------------
# No-target metric produces identical SQL with target=None
# ---------------------------------------------------------------------------


def test_no_target_explicit_none_byte_identical() -> None:
    """Explicit target=None must produce the same SQL as the default (no target key)."""
    m_default = _revenue_metric()
    m_explicit_none = _revenue_metric(target=None)
    sql_default, _ = compile_metric(m_default, MetricQuery(metric_id="revenue"))
    sql_explicit_none, _ = compile_metric(m_explicit_none, MetricQuery(metric_id="revenue"))
    assert sql_default == sql_explicit_none, (
        "target=None (explicit) must produce byte-identical SQL to the default no-target metric"
    )


# ---------------------------------------------------------------------------
# Target round-trips through serialize/deserialize (multiple round-trips)
# ---------------------------------------------------------------------------


def test_target_round_trip_basic_fields() -> None:
    """All MetricTarget fields must survive a to_dict/from_dict round-trip."""
    target = MetricTarget(
        value="12345",
        direction="lower_is_better",
        amber_threshold=0.75,
        measure="cost",
    )
    m = _revenue_metric(
        target=target,
        extra_measures=(Measure(name="cost", agg="sum", expr="cost_amt"),),
    )
    m2 = MetricDefinition.from_dict(m.to_dict())
    assert m2.target is not None
    assert m2.target.value == "12345"
    assert m2.target.direction == "lower_is_better"
    assert m2.target.amber_threshold == pytest.approx(0.75)
    assert m2.target.measure == "cost"


def test_target_round_trip_measure_none() -> None:
    """measure=None must round-trip as None (not become 'None' string)."""
    target = MetricTarget(value="999", direction="higher_is_better", amber_threshold=0.8, measure=None)
    m = _revenue_metric(target=target)
    m2 = MetricDefinition.from_dict(m.to_dict())
    assert m2.target is not None
    assert m2.target.measure is None


def test_target_round_trip_amber_threshold_stays_float() -> None:
    """amber_threshold must survive as a float, not become a string."""
    target = MetricTarget(value="100", amber_threshold=0.65)
    m = _revenue_metric(target=target)
    m2 = MetricDefinition.from_dict(m.to_dict())
    assert m2.target is not None
    assert isinstance(m2.target.amber_threshold, float)
    assert m2.target.amber_threshold == pytest.approx(0.65)


def test_target_double_round_trip() -> None:
    """Multiple round-trips must preserve all fields."""
    target = MetricTarget(
        value="500000",
        direction="lower_is_better",
        amber_threshold=0.85,
        measure=None,
    )
    m = _revenue_metric(target=target)
    # Round-trip twice
    m2 = MetricDefinition.from_dict(m.to_dict())
    m3 = MetricDefinition.from_dict(m2.to_dict())
    assert m3.target is not None
    assert m3.target.value == "500000"
    assert m3.target.direction == "lower_is_better"
    assert m3.target.amber_threshold == pytest.approx(0.85)
    assert m3.target.measure is None


# ---------------------------------------------------------------------------
# lower_is_better: <= comparisons and inverted amber logic
# ---------------------------------------------------------------------------


def test_lower_is_better_has_lte_comparison() -> None:
    """lower_is_better RAG logic must use <= (not >=) for green and amber."""
    target = MetricTarget(value="50", direction="lower_is_better", amber_threshold=0.8)
    m = _revenue_metric(target=target)
    sql = _sql_up(m)
    assert "<=" in sql, "lower_is_better must emit <= comparisons"
    # Green: actual <= target
    # Amber: actual <= target / NULLIF(amber_threshold, 0)
    assert "NULLIF" in sql  # division guard


def test_lower_is_better_nullif_in_amber() -> None:
    """lower_is_better amber arm: target / NULLIF(amber_threshold, 0) must appear."""
    target = MetricTarget(value="200", direction="lower_is_better", amber_threshold=0.9)
    m = _revenue_metric(target=target)
    sql = _sql_up(m)
    # The lower_is_better amber check is: actual <= target / NULLIF(thresh, 0)
    assert "NULLIF" in sql
    assert "0.9" in sql


def test_lower_is_better_rag_colors_present() -> None:
    """lower_is_better must still emit all three RAG status strings."""
    target = MetricTarget(value="100", direction="lower_is_better")
    m = _revenue_metric(target=target)
    sql = _sql_up(m)
    assert "'GREEN'" in sql
    assert "'AMBER'" in sql
    assert "'RED'" in sql


# ---------------------------------------------------------------------------
# Default amber_threshold=0.8 appears in SQL
# ---------------------------------------------------------------------------


def test_default_amber_threshold_08_in_sql() -> None:
    """Default amber_threshold (0.8) must appear as a literal in the SQL."""
    target = MetricTarget(value="1000")  # amber_threshold defaults to 0.8
    m = _revenue_metric(target=target)
    sql = _sql_up(m)
    assert "0.8" in sql


# ---------------------------------------------------------------------------
# Large target value as literal
# ---------------------------------------------------------------------------


def test_large_target_value_in_sql() -> None:
    """A very large target value string must appear in the compiled SQL."""
    target = MetricTarget(value="1000000000")
    m = _revenue_metric(target=target)
    sql = _sql_up(m)
    # sqlglot may normalize 1000000000 — check the numeric value is present
    assert "1000000000" in sql or "1E9" in sql.upper() or "1.0E9" in sql.upper()


# ---------------------------------------------------------------------------
# Decimal-string target value
# ---------------------------------------------------------------------------


def test_decimal_target_value_in_sql() -> None:
    """A decimal target value like '3.14159' must appear in the SQL."""
    target = MetricTarget(value="3.14159")
    m = _revenue_metric(target=target)
    sql = _sql_up(m)
    assert "3.14159" in sql


# ---------------------------------------------------------------------------
# Target with DerivedMeasure co-existing
# ---------------------------------------------------------------------------


def test_target_with_derived_measure_coexist() -> None:
    """target + derived_measures must both appear in the SQL (layered path)."""
    target = MetricTarget(value="2000000", direction="higher_is_better")
    m = _revenue_metric(
        target=target,
        extra_measures=(Measure(name="cost", agg="sum", expr="cost_amt"),),
        derived_measures=(DerivedMeasure(name="margin", formula="revenue - cost"),),
    )
    sql = _sql_up(m)
    assert "MARGIN" in sql, "derived measure 'margin' must appear"
    assert "REVENUE_TARGET" in sql, "primary target column must appear"
    assert "REVENUE_RAG" in sql, "primary RAG column must appear"


def test_target_with_derived_measure_column_count() -> None:
    """With target + derived, SQL must have all four target columns."""
    target = MetricTarget(value="100")
    m = _revenue_metric(
        target=target,
        extra_measures=(Measure(name="cost", agg="sum", expr="cost_amt"),),
        derived_measures=(DerivedMeasure(name="margin", formula="revenue - cost"),),
    )
    sql = _sql_up(m)
    assert "REVENUE_TARGET" in sql
    assert "REVENUE_VS_TARGET" in sql
    assert "REVENUE_PCT_TO_GOAL" in sql
    assert "REVENUE_RAG" in sql


# ---------------------------------------------------------------------------
# MetricTarget.measure=None → primary measure used as prefix
# ---------------------------------------------------------------------------


def test_target_measure_none_uses_primary_measure() -> None:
    """When target.measure is None, compiler should use the primary measure name."""
    target = MetricTarget(value="500", measure=None)
    m = _revenue_metric(target=target)
    sql = _sql_up(m)
    # Primary measure is 'revenue', so columns must be REVENUE_*
    assert "REVENUE_TARGET" in sql
    assert "REVENUE_RAG" in sql
    assert "REVENUE_VS_TARGET" in sql
    assert "REVENUE_PCT_TO_GOAL" in sql


# ---------------------------------------------------------------------------
# Extra measures without target: no _rag columns for the extra measure
# ---------------------------------------------------------------------------


def test_extra_measure_without_target_has_no_rag_columns() -> None:
    """When target targets 'revenue', extra measure 'cost' must NOT get target columns."""
    target = MetricTarget(value="1000", measure=None)  # targets primary = revenue
    m = _revenue_metric(
        target=target,
        extra_measures=(Measure(name="cost", agg="sum", expr="cost_amount"),),
    )
    sql = _sql_up(m)
    assert "COST_TARGET" not in sql
    assert "COST_RAG" not in sql
    assert "COST_VS_TARGET" not in sql
    assert "COST_PCT_TO_GOAL" not in sql


# ---------------------------------------------------------------------------
# No-target flat path vs target layered path: structural difference
# ---------------------------------------------------------------------------


def test_no_target_flat_no_cte() -> None:
    """Without a target (and no derived measures), SQL must not contain a WITH __BASE CTE."""
    m = _revenue_metric()
    sql, _ = compile_metric(m, MetricQuery(metric_id="revenue", dimensions=("region",)))
    assert "__BASE" not in sql.upper()


def test_target_forces_cte() -> None:
    """With a target, SQL must use the layered CTE path (WITH __BASE)."""
    target = MetricTarget(value="1000")
    m = _revenue_metric(target=target)
    sql, _ = compile_metric(m, MetricQuery(metric_id="revenue", dimensions=("region",)))
    assert "__BASE" in sql.upper()


# ---------------------------------------------------------------------------
# MetricDefinition.from_dict handles missing target gracefully
# ---------------------------------------------------------------------------


def test_from_dict_no_target_key_gives_none() -> None:
    """from_dict without 'target' key must produce target=None."""
    data = {
        "id": "x",
        "name": "X",
        "measure": {"name": "v", "agg": "sum", "expr": "v"},
    }
    m = MetricDefinition.from_dict(data)
    assert m.target is None


def test_from_dict_target_none_value_gives_none() -> None:
    """from_dict with target=None in the dict must produce target=None."""
    data = {
        "id": "y",
        "name": "Y",
        "measure": {"name": "v", "agg": "sum", "expr": "v"},
        "target": None,
    }
    m = MetricDefinition.from_dict(data)
    assert m.target is None


def test_from_dict_target_with_all_fields() -> None:
    """from_dict with a fully specified target dict must reconstruct all fields."""
    data = {
        "id": "z",
        "name": "Z",
        "measure": {"name": "revenue", "agg": "sum", "expr": "amount"},
        "target": {
            "value": "77777",
            "direction": "lower_is_better",
            "amber_threshold": 0.6,
            "measure": "revenue",
        },
    }
    m = MetricDefinition.from_dict(data)
    assert m.target is not None
    assert m.target.value == "77777"
    assert m.target.direction == "lower_is_better"
    assert m.target.amber_threshold == pytest.approx(0.6)
    assert m.target.measure == "revenue"


def test_from_dict_target_defaults_direction_higher_is_better() -> None:
    """from_dict target with missing 'direction' must default to 'higher_is_better'."""
    data = {
        "id": "a",
        "name": "A",
        "measure": {"name": "v", "agg": "sum", "expr": "v"},
        "target": {"value": "100"},
    }
    m = MetricDefinition.from_dict(data)
    assert m.target is not None
    assert m.target.direction == "higher_is_better"


def test_from_dict_target_defaults_amber_threshold_08() -> None:
    """from_dict target with missing 'amber_threshold' must default to 0.8."""
    data = {
        "id": "b",
        "name": "B",
        "measure": {"name": "v", "agg": "sum", "expr": "v"},
        "target": {"value": "200"},
    }
    m = MetricDefinition.from_dict(data)
    assert m.target is not None
    assert m.target.amber_threshold == pytest.approx(0.8)


# ---------------------------------------------------------------------------
# MetricTarget model defaults
# ---------------------------------------------------------------------------


def test_metric_target_direction_default() -> None:
    """MetricTarget.direction must default to 'higher_is_better'."""
    t = MetricTarget(value="1")
    assert t.direction == "higher_is_better"


def test_metric_target_amber_threshold_default() -> None:
    """MetricTarget.amber_threshold must default to 0.8."""
    t = MetricTarget(value="1")
    assert t.amber_threshold == pytest.approx(0.8)


def test_metric_target_measure_default_none() -> None:
    """MetricTarget.measure must default to None."""
    t = MetricTarget(value="1")
    assert t.measure is None


# ---------------------------------------------------------------------------
# params dict: target compilation must not leak unexpected params
# ---------------------------------------------------------------------------


def test_target_compile_no_unexpected_params() -> None:
    """Compiling a target metric with no user filters must return an empty params dict."""
    target = MetricTarget(value="1000")
    m = _revenue_metric(target=target)
    _, params = compile_metric(m, MetricQuery(metric_id="revenue"))
    assert params == {}, f"Expected empty params but got: {params}"


# ---------------------------------------------------------------------------
# Higher_is_better: green >= target, amber >= target * threshold
# ---------------------------------------------------------------------------


def test_higher_is_better_rag_structure() -> None:
    """higher_is_better: SQL must contain >=, and the target value literal."""
    target = MetricTarget(value="100", direction="higher_is_better", amber_threshold=0.8)
    m = _revenue_metric(target=target)
    sql = _sql_up(m)
    assert ">=" in sql, "higher_is_better must emit >= comparisons"
    assert "100" in sql, "target value 100 must appear in SQL"


def test_higher_is_better_amber_threshold_1_0_compiles() -> None:
    """amber_threshold=1.0 (zero-width amber range) must compile without error."""
    target = MetricTarget(value="1000", direction="higher_is_better", amber_threshold=1.0)
    m = _revenue_metric(target=target)
    sql = _sql_up(m)
    assert "1.0" in sql
    assert "'GREEN'" in sql
    assert "'AMBER'" in sql
    assert "'RED'" in sql


def test_higher_is_better_amber_threshold_0_compiles() -> None:
    """amber_threshold=0.0 must compile; amber range covers everything below target."""
    target = MetricTarget(value="1000", direction="higher_is_better", amber_threshold=0.0)
    m = _revenue_metric(target=target)
    sql = _sql_up(m)
    assert "0.0" in sql
    assert "'AMBER'" in sql


# ---------------------------------------------------------------------------
# Target with a time dimension — still emits all target columns
# ---------------------------------------------------------------------------


def test_target_with_time_dimension_emits_target_columns() -> None:
    """Target columns must appear even when a time_dimension and time_grain are used."""
    target = MetricTarget(value="5000")
    m = MetricDefinition(
        id="revenue",
        name="Revenue",
        measure=Measure(name="revenue", agg="sum", expr="amount"),
        base_table="orders",
        dimensions=(Dimension(name="region"),),
        time_dimension=TimeDimension(column="created_at"),
        target=target,
    )
    mq = MetricQuery(metric_id="revenue", time_grain="month")
    sql, _ = compile_metric(m, mq)
    sql_up = sql.upper()
    assert "REVENUE_TARGET" in sql_up
    assert "REVENUE_RAG" in sql_up
    assert "REVENUE_PCT_TO_GOAL" in sql_up
    assert "REVENUE_VS_TARGET" in sql_up
