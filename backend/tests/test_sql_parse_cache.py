"""Unit tests for the bounded copy-on-return sqlglot parse cache.

Pure unit tests — no app fixtures.  They guard the two correctness invariants
the whole optimisation rests on: equivalence with ``sqlglot.parse_one`` and the
copy-safety that lets every caller mutate its result freely.
"""

from __future__ import annotations

import sqlglot
import sqlglot.expressions as exp
import pytest

from app.connectors.sql_parse import (
    clear_parse_cache,
    parse_sql_cached,
)


@pytest.fixture(autouse=True)
def _clean_cache():
    """Each test starts and ends with an empty cache for isolation."""
    clear_parse_cache()
    yield
    clear_parse_cache()


def test_equivalent_to_parse_one():
    sql = "SELECT a, SUM(b) FROM t WHERE c > 1 GROUP BY a"
    expected = sqlglot.parse_one(sql, dialect="postgres")
    got = parse_sql_cached(sql, dialect="postgres")
    # Same rendered SQL == structurally equivalent tree.
    assert got.sql(dialect="postgres") == expected.sql(dialect="postgres")


def test_copy_safety_mutation_does_not_leak():
    sql = "SELECT a, SUM(b) FROM t GROUP BY a"

    first = parse_sql_cached(sql)
    assert first.args.get("where") is None

    # Mutate the returned tree by adding a WHERE clause.
    first = first.where(exp.condition("a > 5"))
    assert first.args.get("where") is not None
    assert "WHERE" in first.sql().upper()

    # A subsequent parse of the SAME sql must be unaffected by that mutation.
    second = parse_sql_cached(sql)
    assert second.args.get("where") is None
    assert "WHERE" not in second.sql().upper()
    assert second.sql() == sqlglot.parse_one(sql).sql()


def test_returns_distinct_objects_each_call():
    sql = "SELECT 1 AS x"
    a = parse_sql_cached(sql)
    b = parse_sql_cached(sql)
    assert a is not b  # fresh copy every time
    assert a.sql() == b.sql()


def test_bad_sql_raises_and_is_not_cached():
    bad = "SELECT FROM WHERE )("
    with pytest.raises(sqlglot.errors.SqlglotError):
        parse_sql_cached(bad)

    # The error must NOT have been cached: a later good parse still works,
    # and re-raising on the bad string still happens.
    good = parse_sql_cached("SELECT 1")
    assert good.sql() == sqlglot.parse_one("SELECT 1").sql()

    with pytest.raises(sqlglot.errors.SqlglotError):
        parse_sql_cached(bad)


def test_dialect_is_part_of_the_key():
    sql = "SELECT a FROM t"
    pg = parse_sql_cached(sql, dialect="postgres")
    my = parse_sql_cached(sql, dialect="mysql")
    # Distinct copies (different cache entries), equivalent content here.
    assert pg is not my
    assert pg.sql(dialect="postgres") == sqlglot.parse_one(sql, dialect="postgres").sql(
        dialect="postgres"
    )


def test_clear_parse_cache_resets():
    sql = "SELECT a FROM t"
    parse_sql_cached(sql)
    from app.connectors.sql_parse import _parse_canonical

    assert _parse_canonical.cache_info().currsize >= 1
    clear_parse_cache()
    assert _parse_canonical.cache_info().currsize == 0
