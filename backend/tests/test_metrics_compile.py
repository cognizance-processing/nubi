"""Tests for the metric → SQL compiler (``app.metrics.compile``).

Coverage
--------
1. basic            — measure + one dimension → SELECT … GROUP BY.
2. time grain       — DATE_TRUNC present with the right alias; a grain not in
                      ``grains`` raises MetricError("bad_time_grain"); a grain with
                      no time dimension raises MetricError("no_time_dimension").
3. governance       — unknown dimension / unknown filter field / bad order_by /
                      bad filter op / list-vs-scalar value / no source all raise
                      MetricError with the right ``code``.
4. filters (SAFE)   — a user VALUE appears ONLY as a ``{{param}}`` placeholder +
                      in the params dict, never inlined into the SQL string;
                      ``default_filters`` ARE inlined verbatim.
5. aggregates       — count_distinct → COUNT(DISTINCT …); count "*" → COUNT(*).
6. subquery source  — ``base_sql`` wraps as ``(…) AS base`` derived table.
7. in / not_in      — list filters emit the planner's ``{{ name | inclause }}``
                      filter and bind the whole list as one param name.

Pure pytest — ``compile_metric`` is a pure function (no DB, no FastAPI).
"""

from __future__ import annotations

import pytest

from app.metrics.compile import compile_metric, compile_metric_sql
from app.metrics.models import (
    Dimension,
    Measure,
    MetricDefinition,
    MetricError,
    MetricFilter,
    MetricQuery,
    TimeDimension,
)


# ---------------------------------------------------------------------------
# Fixtures / builders
# ---------------------------------------------------------------------------


def _revenue_metric(**overrides) -> MetricDefinition:
    """A revenue metric over ``orders`` with two dims + a time dimension."""
    kwargs = dict(
        id="revenue",
        name="Revenue",
        measure=Measure(name="revenue", agg="sum", expr="amount"),
        base_table="orders",
        dimensions=(Dimension(name="region"), Dimension(name="status")),
        time_dimension=TimeDimension(
            column="created_at", grains=("day", "month"), default_grain="day"
        ),
        default_filters=("is_test = FALSE",),
        rls_keys=("org_id",),
    )
    kwargs.update(overrides)
    return MetricDefinition(**kwargs)


# ---------------------------------------------------------------------------
# 1. Basic
# ---------------------------------------------------------------------------


def test_basic_measure_and_dimension() -> None:
    m = _revenue_metric()
    sql, params = compile_metric(m, MetricQuery(metric_id="revenue", dimensions=("region",)))

    up = sql.upper()
    assert "REGION AS REGION" in up
    assert "SUM(AMOUNT) AS REVENUE" in up
    assert "FROM ORDERS" in up
    assert "GROUP BY REGION" in up
    # No user filters → params empty.
    assert params == {}


def test_compile_metric_sql_returns_only_sql() -> None:
    m = _revenue_metric()
    mq = MetricQuery(metric_id="revenue", dimensions=("region",))
    assert compile_metric_sql(m, mq) == compile_metric(m, mq)[0]


# ---------------------------------------------------------------------------
# 2. Time grain
# ---------------------------------------------------------------------------


def test_time_grain_emits_date_trunc_with_alias() -> None:
    m = _revenue_metric()
    sql, _ = compile_metric(
        m, MetricQuery(metric_id="revenue", dimensions=("region",), time_grain="month")
    )
    up = sql.upper()
    assert "DATE_TRUNC('MONTH', CREATED_AT)" in up
    # Documented alias convention: <column>_<grain>.
    assert "AS CREATED_AT_MONTH" in up
    # The bucket is grouped by its underlying expression, not the alias.
    assert "GROUP BY REGION, DATE_TRUNC('MONTH', CREATED_AT)" in up


def test_time_grain_not_in_grains_rejected() -> None:
    m = _revenue_metric()  # grains = day, month
    with pytest.raises(MetricError) as ei:
        compile_metric(m, MetricQuery(metric_id="revenue", time_grain="year"))
    assert ei.value.code == "bad_time_grain"


def test_time_grain_without_time_dimension_rejected() -> None:
    m = _revenue_metric(time_dimension=None)
    with pytest.raises(MetricError) as ei:
        compile_metric(m, MetricQuery(metric_id="revenue", time_grain="day"))
    assert ei.value.code == "no_time_dimension"


# ---------------------------------------------------------------------------
# 3. Governance
# ---------------------------------------------------------------------------


def test_unknown_dimension_rejected() -> None:
    m = _revenue_metric()
    with pytest.raises(MetricError) as ei:
        compile_metric(m, MetricQuery(metric_id="revenue", dimensions=("nope",)))
    assert ei.value.code == "unknown_dimension"


def test_unknown_filter_field_rejected() -> None:
    m = _revenue_metric()
    with pytest.raises(MetricError) as ei:
        compile_metric(
            m,
            MetricQuery(
                metric_id="revenue",
                filters=(MetricFilter(field="ghost", op="=", value=1),),
            ),
        )
    assert ei.value.code == "unknown_filter_field"


def test_time_column_is_a_valid_filter_field() -> None:
    m = _revenue_metric()
    sql, params = compile_metric(
        m,
        MetricQuery(
            metric_id="revenue",
            filters=(MetricFilter(field="created_at", op=">=", value="2024-01-01"),),
        ),
    )
    assert "created_at >=" in sql
    assert params == {"f0": "2024-01-01"}


def test_bad_order_by_rejected() -> None:
    m = _revenue_metric()
    with pytest.raises(MetricError) as ei:
        compile_metric(
            m,
            MetricQuery(
                metric_id="revenue",
                dimensions=("region",),
                order_by=(("not_selected", "asc"),),
            ),
        )
    assert ei.value.code == "bad_order_by"


def test_order_by_measure_and_time_alias_allowed() -> None:
    m = _revenue_metric()
    sql, _ = compile_metric(
        m,
        MetricQuery(
            metric_id="revenue",
            dimensions=("region",),
            time_grain="month",
            order_by=(("revenue", "desc"), ("created_at_month", "asc")),
        ),
    )
    up = sql.upper()
    assert "ORDER BY REVENUE DESC, CREATED_AT_MONTH ASC" in up


def test_bad_filter_op_rejected() -> None:
    m = _revenue_metric()
    with pytest.raises(MetricError) as ei:
        compile_metric(
            m,
            MetricQuery(
                metric_id="revenue",
                filters=(MetricFilter(field="region", op="LIKE", value="x"),),  # type: ignore[arg-type]
            ),
        )
    assert ei.value.code == "bad_filter_op"


def test_in_filter_requires_list_value() -> None:
    m = _revenue_metric()
    with pytest.raises(MetricError) as ei:
        compile_metric(
            m,
            MetricQuery(
                metric_id="revenue",
                filters=(MetricFilter(field="region", op="in", value="EU"),),
            ),
        )
    assert ei.value.code == "bad_filter_value"


def test_scalar_filter_rejects_list_value() -> None:
    m = _revenue_metric()
    with pytest.raises(MetricError) as ei:
        compile_metric(
            m,
            MetricQuery(
                metric_id="revenue",
                filters=(MetricFilter(field="region", op="=", value=["EU", "US"]),),
            ),
        )
    assert ei.value.code == "bad_filter_value"


def test_no_source_rejected() -> None:
    m = _revenue_metric(base_table=None, base_sql=None)
    with pytest.raises(MetricError) as ei:
        compile_metric(m, MetricQuery(metric_id="revenue"))
    assert ei.value.code == "no_source"


def test_both_sources_rejected() -> None:
    m = _revenue_metric(base_sql="SELECT * FROM o")  # base_table also set
    with pytest.raises(MetricError) as ei:
        compile_metric(m, MetricQuery(metric_id="revenue"))
    assert ei.value.code == "no_source"


# ---------------------------------------------------------------------------
# 4. Filters — SAFE binding (the headline security property)
# ---------------------------------------------------------------------------


def test_user_value_is_placeholdered_not_inlined() -> None:
    m = _revenue_metric()
    secret = "EU-SECRET-VALUE"
    sql, params = compile_metric(
        m,
        MetricQuery(
            metric_id="revenue",
            dimensions=("region",),
            filters=(MetricFilter(field="region", op="=", value=secret),),
        ),
    )
    # The literal user value must NOT be a substring of the SQL.
    assert secret not in sql
    # It appears as a {{name}} placeholder + in params.
    assert "{{f0}}" in sql
    assert params == {"f0": secret}
    assert "region = {{f0}}" in sql


def test_default_filters_are_inlined_verbatim() -> None:
    # Author-trusted fragments are AND-ed in as literal predicates (re-rendered
    # by sqlglot, so the values appear directly in the SQL — never bound).
    m = _revenue_metric(default_filters=("is_test = FALSE", "amount > 0"))
    sql, params = compile_metric(m, MetricQuery(metric_id="revenue"))
    assert "is_test = FALSE" in sql
    assert "amount > 0" in sql
    # default_filters are trusted → never turned into bound params.
    assert params == {}


def test_default_and_user_filters_combine() -> None:
    m = _revenue_metric()
    sql, params = compile_metric(
        m,
        MetricQuery(
            metric_id="revenue",
            filters=(MetricFilter(field="region", op="=", value="EU"),),
        ),
    )
    assert "is_test = FALSE" in sql  # author default, verbatim
    assert "{{f0}}" in sql  # user value, bound
    assert params == {"f0": "EU"}


# ---------------------------------------------------------------------------
# 5. Aggregates
# ---------------------------------------------------------------------------


def test_count_distinct() -> None:
    m = MetricDefinition(
        id="uniques",
        name="Uniques",
        measure=Measure(name="uniques", agg="count_distinct", expr="user_id"),
        base_table="events",
        dimensions=(Dimension(name="page"),),
    )
    sql, _ = compile_metric(m, MetricQuery(metric_id="uniques", dimensions=("page",)))
    assert "COUNT(DISTINCT user_id)" in sql.replace("USER_ID", "user_id")
    assert "COUNT(DISTINCT" in sql.upper()


def test_count_star() -> None:
    m = MetricDefinition(
        id="hits",
        name="Hits",
        measure=Measure(name="hits", agg="count", expr="*"),
        base_table="events",
    )
    sql, _ = compile_metric(m, MetricQuery(metric_id="hits"))
    assert "COUNT(*) AS hits" in sql.replace("HITS", "hits").replace("COUNT", "COUNT")
    assert "COUNT(*)" in sql.upper()


def test_extra_measures_emitted() -> None:
    m = MetricDefinition(
        id="ev",
        name="Events",
        measure=Measure(name="hits", agg="count", expr="*"),
        base_table="events",
        dimensions=(Dimension(name="page"),),
        extra_measures=(Measure(name="uniques", agg="count_distinct", expr="user_id"),),
    )
    sql, _ = compile_metric(m, MetricQuery(metric_id="ev", dimensions=("page",)))
    up = sql.upper()
    assert "COUNT(*) AS HITS" in up
    assert "COUNT(DISTINCT USER_ID) AS UNIQUES" in up


# ---------------------------------------------------------------------------
# 6. Subquery source
# ---------------------------------------------------------------------------


def test_base_sql_wraps_as_derived_table() -> None:
    m = MetricDefinition(
        id="u",
        name="U",
        measure=Measure(name="rev", agg="sum", expr="amount"),
        base_sql="SELECT * FROM events WHERE valid",
        dimensions=(Dimension(name="page"),),
    )
    sql, _ = compile_metric(m, MetricQuery(metric_id="u", dimensions=("page",)))
    up = sql.upper()
    assert "FROM (SELECT * FROM EVENTS WHERE VALID) AS BASE" in up


# ---------------------------------------------------------------------------
# 7. in / not_in — planner inclause convention
# ---------------------------------------------------------------------------


def test_in_filter_uses_inclause_and_binds_whole_list() -> None:
    m = _revenue_metric()
    sql, params = compile_metric(
        m,
        MetricQuery(
            metric_id="revenue",
            filters=(MetricFilter(field="status", op="in", value=["a", "b"]),),
        ),
    )
    # Single placeholder name, planner's inclause filter expands it.
    assert "{{ f0 | inclause }}" in sql
    assert "status IN {{ f0 | inclause }}" in sql
    # The whole list is bound under one param name (planner binds each element).
    assert params == {"f0": ["a", "b"]}
    # No raw list element is inlined.
    assert "'a'" not in sql and "'b'" not in sql


def test_not_in_filter_negates_inclause() -> None:
    m = _revenue_metric()
    sql, params = compile_metric(
        m,
        MetricQuery(
            metric_id="revenue",
            filters=(MetricFilter(field="status", op="not_in", value=["x", "y"]),),
        ),
    )
    assert "NOT (status IN {{ f0 | inclause }})" in sql
    assert params == {"f0": ["x", "y"]}


def test_dimension_with_custom_expr() -> None:
    m = MetricDefinition(
        id="r",
        name="R",
        measure=Measure(name="rev", agg="sum", expr="amount"),
        base_table="orders",
        dimensions=(Dimension(name="hour", expr="date_part('hour', ts)"),),
    )
    sql, _ = compile_metric(m, MetricQuery(metric_id="r", dimensions=("hour",)))
    up = sql.upper()
    assert "DATE_PART('HOUR', TS) AS HOUR" in up
    # Grouped by the underlying expression, not the alias.
    assert "GROUP BY DATE_PART('HOUR', TS)" in up
