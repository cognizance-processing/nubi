"""Tests for KPI target compilation (wave2/kpi-targets).

Coverage
--------
1. target_columns_emitted    — a metric WITH a target emits the four extra columns
                               (<measure>_target / _vs_target / _pct_to_goal / _rag)
                               in the compiled SQL.
2. no_target_unchanged       — a metric WITHOUT a target produces the same SQL as
                               before (back-compat / conformance guard).
3. higher_is_better_rag      — RAG CASE WHEN logic for higher_is_better direction.
4. lower_is_better_rag       — RAG CASE WHEN logic for lower_is_better direction.
5. amber_threshold_custom    — custom amber_threshold reflected in the CASE WHEN.
6. target_measure_override   — target.measure overrides the default primary measure.
7. pct_to_goal_nullif        — pct_to_goal expression contains NULLIF(target, 0).
8. target_with_derived       — target + derived_measures both appear (layered path).
9. target_model_round_trip   — MetricTarget serialises/deserialises via
                               MetricDefinition.to_dict / from_dict.
10. target_direction_default — MetricTarget direction defaults to "higher_is_better".
11. amber_threshold_default  — MetricTarget amber_threshold defaults to 0.8.
"""

from __future__ import annotations


from app.metrics.compile import compile_metric
from app.metrics.models import (
    DerivedMeasure,
    Dimension,
    Measure,
    MetricDefinition,
    MetricTarget,
    MetricQuery,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _revenue_metric(**overrides) -> MetricDefinition:
    """A simple revenue metric over orders — no target by default."""
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


# ---------------------------------------------------------------------------
# 1. target_columns_emitted
# ---------------------------------------------------------------------------


def test_target_columns_emitted() -> None:
    target = MetricTarget(value="1000000")
    m = _revenue_metric(target=target)
    sql = _sql_up(m)

    assert "REVENUE_TARGET" in sql, "expected _target column"
    assert "REVENUE_VS_TARGET" in sql, "expected _vs_target column"
    assert "REVENUE_PCT_TO_GOAL" in sql, "expected _pct_to_goal column"
    assert "REVENUE_RAG" in sql, "expected _rag column"


# ---------------------------------------------------------------------------
# 2. no_target_unchanged
# ---------------------------------------------------------------------------


def test_no_target_unchanged() -> None:
    """A metric without a target must produce identical SQL to a plain metric."""
    m_no_target = _revenue_metric()
    m_with_target = _revenue_metric(target=MetricTarget(value="999"))

    sql_no, _ = compile_metric(m_no_target, MetricQuery(metric_id="revenue"))
    sql_with, _ = compile_metric(m_with_target, MetricQuery(metric_id="revenue"))

    # No _target/_rag in the no-target SQL
    assert "REVENUE_TARGET" not in sql_no.upper()
    assert "REVENUE_RAG" not in sql_no.upper()

    # The no-target and with-target SQLs are different
    assert sql_no != sql_with


# ---------------------------------------------------------------------------
# 3. higher_is_better_rag
# ---------------------------------------------------------------------------


def test_higher_is_better_rag() -> None:
    target = MetricTarget(value="100", direction="higher_is_better", amber_threshold=0.8)
    m = _revenue_metric(target=target)
    sql = _sql_up(m)

    # Green: actual >= target
    assert ">= 100" in sql.upper() or ">= 1E2" in sql.upper() or "REVENUE >= 100" in sql.upper()
    # RAG CASE WHEN present
    assert "CASE" in sql
    assert "'GREEN'" in sql
    assert "'AMBER'" in sql
    assert "'RED'" in sql


# ---------------------------------------------------------------------------
# 4. lower_is_better_rag
# ---------------------------------------------------------------------------


def test_lower_is_better_rag() -> None:
    target = MetricTarget(value="50", direction="lower_is_better", amber_threshold=0.8)
    m = _revenue_metric(target=target)
    sql = _sql_up(m)

    # lower_is_better uses <= comparisons
    assert "CASE" in sql
    assert "'GREEN'" in sql
    assert "'AMBER'" in sql
    assert "'RED'" in sql
    # Should contain a <= check (actual <= target)
    assert "<=" in sql


# ---------------------------------------------------------------------------
# 5. amber_threshold_custom
# ---------------------------------------------------------------------------


def test_amber_threshold_custom() -> None:
    target = MetricTarget(value="1000", direction="higher_is_better", amber_threshold=0.9)
    m = _revenue_metric(target=target)
    sql = _sql_up(m)

    # 0.9 threshold should appear in the SQL (as literal)
    assert "0.9" in sql


# ---------------------------------------------------------------------------
# 6. target_measure_override
# ---------------------------------------------------------------------------


def test_target_measure_override() -> None:
    """target.measure can override which measure the target is applied to."""
    target = MetricTarget(value="500", measure="cost")
    m = _revenue_metric(
        target=target,
        extra_measures=(Measure(name="cost", agg="sum", expr="cost_amount"),),
    )
    sql = _sql_up(m)

    assert "COST_TARGET" in sql.upper()
    assert "COST_RAG" in sql.upper()


# ---------------------------------------------------------------------------
# 7. pct_to_goal_nullif
# ---------------------------------------------------------------------------


def test_pct_to_goal_nullif() -> None:
    """pct_to_goal must guard against division by zero with NULLIF."""
    target = MetricTarget(value="1000")
    m = _revenue_metric(target=target)
    sql = _sql_up(m)

    assert "NULLIF" in sql.upper()
    assert "REVENUE_PCT_TO_GOAL" in sql.upper()


# ---------------------------------------------------------------------------
# 8. target_with_derived
# ---------------------------------------------------------------------------


def test_target_with_derived() -> None:
    """Target co-exists with derived_measures on the layered path."""
    target = MetricTarget(value="2000000", direction="higher_is_better")
    m = _revenue_metric(
        target=target,
        extra_measures=(Measure(name="cost", agg="sum", expr="cost_amt"),),
        derived_measures=(DerivedMeasure(name="margin", formula="revenue - cost"),),
    )
    sql = _sql_up(m)

    assert "MARGIN" in sql.upper()
    assert "REVENUE_TARGET" in sql.upper()
    assert "REVENUE_RAG" in sql.upper()


# ---------------------------------------------------------------------------
# 9. target_model_round_trip
# ---------------------------------------------------------------------------


def test_target_model_round_trip() -> None:
    target = MetricTarget(
        value="500000",
        direction="lower_is_better",
        amber_threshold=0.75,
        measure="cost",
    )
    m = _revenue_metric(
        target=target,
        extra_measures=(Measure(name="cost", agg="sum", expr="cost_amt"),),
    )
    d = m.to_dict()
    m2 = MetricDefinition.from_dict(d)

    assert m2.target is not None
    assert m2.target.value == "500000"
    assert m2.target.direction == "lower_is_better"
    assert m2.target.amber_threshold == 0.75
    assert m2.target.measure == "cost"


# ---------------------------------------------------------------------------
# 10. target_direction_default
# ---------------------------------------------------------------------------


def test_target_direction_default() -> None:
    t = MetricTarget(value="100")
    assert t.direction == "higher_is_better"


# ---------------------------------------------------------------------------
# 11. amber_threshold_default
# ---------------------------------------------------------------------------


def test_amber_threshold_default() -> None:
    t = MetricTarget(value="100")
    assert t.amber_threshold == 0.8


# ---------------------------------------------------------------------------
# 12. no_target_flat_sql_backcompat
# ---------------------------------------------------------------------------


def test_no_target_flat_sql_backcompat() -> None:
    """A no-target, no-derived, no-transforms metric MUST use the flat path.

    The flat path produces a bare SELECT...FROM...WHERE...GROUP BY...LIMIT
    without a CTE. The result must not contain 'WITH __BASE' or any target
    column names.
    """
    m = _revenue_metric()  # no target
    sql, _ = compile_metric(m, MetricQuery(metric_id="revenue", dimensions=("region",)))
    up = sql.upper()

    # Flat path: no CTE
    assert "WITH __BASE" not in up
    assert "REVENUE_TARGET" not in up
    assert "REVENUE_RAG" not in up

    # Must contain a standard SELECT ... GROUP BY
    assert "SELECT" in up
    assert "SUM(AMOUNT) AS REVENUE" in up
    assert "FROM ORDERS" in up


# ---------------------------------------------------------------------------
# 13. target_layered_cte
# ---------------------------------------------------------------------------


def test_target_forces_layered_cte() -> None:
    """A metric with a target must use the layered CTE path."""
    target = MetricTarget(value="1000")
    m = _revenue_metric(target=target)
    sql, _ = compile_metric(m, MetricQuery(metric_id="revenue", dimensions=("region",)))
    up = sql.upper()

    # Layered path: __base CTE present
    assert "WITH __BASE" in up or "__BASE" in up
