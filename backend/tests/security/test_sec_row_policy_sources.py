"""Row policies: every data source in a query is either filtered or refused.

A caller with row-level policies must never get a plan in which some table is
read without the policy predicate.  Sources the planner cannot attach a
predicate to are refused with ``UNSUPPORTED_QUERY`` rather than passed through.
"""

from __future__ import annotations

import pytest

from app.connectors.planner import plan
from app.errors import AppError

POLICIES = {"policies": {"region": "EU"}}

REFUSED = [
    # statements that read a table but are not a SELECT block
    ("duckdb", "SELECT * FROM (SUMMARIZE orders)"),
    ("duckdb", "SELECT * FROM (SUMMARIZE SELECT * FROM orders) s"),
    ("duckdb", "SELECT * FROM (DESCRIBE orders)"),
    ("duckdb", "SELECT * FROM (PIVOT orders ON region USING sum(amt))"),
    # joins whose joined side is invisible to the block's WHERE
    ("duckdb", "SELECT * FROM customers c SEMI JOIN orders o ON c.id = o.cid"),
    ("duckdb", "SELECT * FROM customers c ANTI JOIN orders o ON c.id = o.cid"),
    ("spark", "SELECT * FROM customers c LEFT SEMI JOIN orders o ON c.id = o.cid"),
    ("spark", "SELECT * FROM customers c LEFT ANTI JOIN orders o ON c.id = o.cid"),
    # table-valued forms that name or reach a table indirectly
    ("snowflake", "SELECT * FROM IDENTIFIER('orders')"),
    ("snowflake", "SELECT * FROM TABLE('orders')"),
    ("snowflake", "SELECT * FROM TABLE(IDENTIFIER('orders'))"),
    ("snowflake", "SELECT * FROM TABLE(my_udtf(1))"),
    ("snowflake", "SELECT * FROM @stage/file"),
    ("bigquery", "SELECT * FROM EXTERNAL_QUERY('conn', 'select * from orders')"),
    ("postgres", "SELECT * FROM a, LATERAL my_fn(a.x) f"),
    ("postgres", "SELECT * FROM my_fn(1) f"),
    ("postgres", "SELECT * FROM ROWS FROM (my_fn(1))"),
    # nested: the refusal applies at any depth
    ("duckdb", "WITH s AS (SELECT * FROM (SUMMARIZE orders)) SELECT * FROM s"),
    ("snowflake", "SELECT (SELECT count(*) FROM IDENTIFIER('orders'))"),
    ("duckdb", "SELECT * FROM a WHERE EXISTS (SELECT 1 FROM b SEMI JOIN orders o ON b.id = o.id)"),
    # a pivot reshapes the columns before the block's WHERE runs
    ("duckdb", "SELECT * FROM orders UNPIVOT (v FOR k IN (a, b))"),
    ("snowflake", "SELECT * FROM orders PIVOT (sum(amt) FOR k IN ('a' AS region))"),
    # an output alias that would capture the policy column in the WHERE
    ("clickhouse", "SELECT 'EU' AS region, amount FROM orders"),
]


@pytest.mark.parametrize(("dialect", "sql"), REFUSED)
def test_unfilterable_source_is_refused_under_policies(dialect: str, sql: str) -> None:
    with pytest.raises(AppError) as exc:
        plan(sql, claims=POLICIES, dialect=dialect)
    assert exc.value.code == "UNSUPPORTED_QUERY"
    assert exc.value.status == 400


FILTERED = [
    ("postgres", "SELECT * FROM orders", 1),
    ("postgres", "WITH t AS (SELECT * FROM orders) SELECT * FROM t JOIN t t2 ON true", 1),
    ("postgres", "SELECT * FROM a WHERE EXISTS (SELECT 1 FROM orders)", 2),
    ("duckdb", "SELECT * FROM customers c SEMI JOIN (SELECT * FROM orders) o ON c.id = o.cid", 2),
    ("duckdb", "SELECT * FROM (SELECT * FROM orders) UNPIVOT (v FOR k IN (a, b))", 1),
    ("snowflake", "SELECT * FROM orders, LATERAL FLATTEN(input => orders.tags) f", 1),
    ("bigquery", "SELECT * FROM orders, UNNEST(tags) t", 1),
    ("postgres", "SELECT * FROM orders o, generate_series(1, 3) g", 1),
    ("postgres", "SELECT * FROM orders o JOIN (VALUES (1), (2)) v(a) ON o.id = v.a", 1),
    ("clickhouse", "SELECT region, amount AS total FROM orders", 1),
    ("clickhouse", "SELECT region AS region, amount FROM orders", 1),
    # one base table beside derived sources: the predicate is pinned to the table
    ("postgres", "SELECT o.* FROM orders o JOIN (SELECT 'EU' AS region) x ON true", 1),
]


@pytest.mark.parametrize(("dialect", "sql", "blocks"), FILTERED)
def test_supported_shapes_still_get_the_predicate(dialect: str, sql: str, blocks: int) -> None:
    out = plan(sql, claims=POLICIES, dialect=dialect).sql
    assert out.count("region = 'EU'") == blocks, out


def test_predicate_is_pinned_to_the_base_table_beside_derived_sources() -> None:
    sql = "SELECT o.* FROM orders o JOIN (SELECT 'EU' AS region) x ON true"
    assert "o.region = 'EU'" in plan(sql, claims=POLICIES, dialect="postgres").sql


TABLELESS = [
    ("postgres", "SELECT 1"),
    ("postgres", "SELECT * FROM generate_series(1, 3) g"),
    ("duckdb", "SELECT * FROM range(3)"),
    ("bigquery", "SELECT * FROM UNNEST([1, 2, 3]) x"),
    ("postgres", "SELECT * FROM (VALUES (1), (2)) v(a)"),
]


@pytest.mark.parametrize(("dialect", "sql"), TABLELESS)
def test_queries_that_read_no_table_are_allowed(dialect: str, sql: str) -> None:
    assert "region" not in plan(sql, claims=POLICIES, dialect=dialect).sql


@pytest.mark.parametrize(("dialect", "sql"), [c for c in REFUSED if "SUMMARIZE SELECT" not in c[1]])
def test_callers_without_policies_are_unaffected(dialect: str, sql: str) -> None:
    try:
        plan(sql, claims={}, dialect=dialect)
    except AppError as exc:  # a refusal here must come from the read-only guard, not row policies
        assert "row-level policies" not in exc.message


# A name the planner reads as a reference to a filtered block must be one the
# engine reads the same way: engines fold unquoted names, so a quoted block
# name and an unquoted reference (or the reverse) can be two different things.
AMBIGUOUS_BLOCK_NAMES = [
    ("postgres", 'WITH "ORDERS" AS (SELECT 1 AS x) SELECT * FROM ORDERS'),
    ("snowflake", 'WITH "orders" AS (SELECT 1 AS x) SELECT * FROM orders'),
    ("postgres", 'WITH orders AS (SELECT 1 AS x) SELECT * FROM "orders"'),
    ("duckdb", 'WITH "Orders" AS (SELECT 1 AS x) SELECT * FROM Orders o JOIN customers c ON true'),
    ("postgres", 'SELECT * FROM a WHERE EXISTS (WITH "ORDERS" AS (SELECT 1) SELECT 1 FROM ORDERS)'),
]


@pytest.mark.parametrize(("dialect", "sql"), AMBIGUOUS_BLOCK_NAMES)
def test_a_block_name_that_differs_from_its_reference_by_quoting_is_refused(dialect: str, sql: str) -> None:
    with pytest.raises(AppError) as exc:
        plan(sql, claims=POLICIES, dialect=dialect)
    assert exc.value.code == "UNSUPPORTED_QUERY"


@pytest.mark.parametrize(
    ("dialect", "sql"),
    [
        ("postgres", "WITH t AS (SELECT * FROM orders) SELECT * FROM t"),
        ("postgres", 'WITH "T" AS (SELECT * FROM orders) SELECT * FROM "T"'),
        ("snowflake", "WITH recent AS (SELECT * FROM orders) SELECT * FROM recent r JOIN recent r2 ON true"),
    ],
)
def test_consistently_written_block_names_still_work(dialect: str, sql: str) -> None:
    assert plan(sql, claims=POLICIES, dialect=dialect).sql.count("region = 'EU'") == 1
