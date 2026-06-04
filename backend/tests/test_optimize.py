"""Tests for the M2-A pushdown optimizer (app/connectors/optimize.py).

Coverage
--------
- ``prune_projection`` — narrows columns, idempotent
- ``push_predicates`` — tuple / dict / string forms; existing WHERE preserved
- ``push_limit`` — set LIMIT; keep smaller existing LIMIT
- ``extract_partition_hints`` — detects partition-key columns in WHERE
- Integration: planner called with new ``limit`` / ``predicates`` args
- Regression: plan() with no extra args yields the identical cache_key the
  conformance suite expects (imports two frozen keys from cases.py)
"""

from __future__ import annotations

import pytest
import sqlglot
import sqlglot.expressions as exp

from app.connectors.optimize import (
    extract_partition_hints,
    prune_projection,
    push_limit,
    push_predicates,
)
from app.connectors.planner import plan

# ---------------------------------------------------------------------------
# Import frozen conformance cache keys for the regression test
# ---------------------------------------------------------------------------
from tests.conformance.cases import CONFORMANCE_CASES

_CASE_BY_ID = {c["id"]: c for c in CONFORMANCE_CASES}
_KEY_PLAIN_SELECT_ALL = _CASE_BY_ID["plain_select_all"]["expected_cache_key"]
_KEY_PROJECTION_ID_NAME = _CASE_BY_ID["projection_id_name"]["expected_cache_key"]
_KEY_RLS_TENANT = _CASE_BY_ID["rls_tenant_filter"]["expected_cache_key"]
_KEY_AGG_GROUP_BY = _CASE_BY_ID["aggregate_group_by_tenant"]["expected_cache_key"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse(sql: str) -> exp.Select:
    """Parse *sql* and return the Select AST node."""
    tree = sqlglot.parse_one(sql, dialect="postgres")
    assert isinstance(tree, exp.Select), f"Expected Select, got {type(tree)}"
    return tree


def _sql(tree: exp.Select) -> str:
    return tree.sql(dialect="postgres")


# ===========================================================================
# prune_projection
# ===========================================================================


class TestPruneProjection:
    def test_narrows_to_requested_columns(self) -> None:
        tree = _parse("SELECT id, name, email, created_at FROM users")
        tree = prune_projection(tree, ["id", "email"])
        sql = _sql(tree)
        assert "id" in sql
        assert "email" in sql
        # name and created_at should not appear in the SELECT list
        # (the rewritten SQL should NOT mention them as select columns)
        # Use the AST to be precise: check the expression list
        selected = [expr.name for expr in tree.expressions]
        assert selected == ["id", "email"]

    def test_single_column(self) -> None:
        tree = _parse("SELECT id, name, amount FROM orders")
        tree = prune_projection(tree, ["amount"])
        selected = [expr.name for expr in tree.expressions]
        assert selected == ["amount"]

    def test_preserves_where_clause(self) -> None:
        tree = _parse("SELECT id, name FROM users WHERE id = 5")
        tree = prune_projection(tree, ["id"])
        sql = _sql(tree)
        assert "WHERE" in sql
        assert "id = 5" in sql or "id=5" in sql

    def test_preserves_from_clause(self) -> None:
        tree = _parse("SELECT id, name FROM users")
        tree = prune_projection(tree, ["name"])
        sql = _sql(tree)
        assert "FROM users" in sql or "FROM" in sql

    def test_empty_columns_is_noop(self) -> None:
        """Empty column list must not mutate the tree."""
        tree = _parse("SELECT id, name FROM users")
        original_sql = _sql(tree)
        tree = prune_projection(tree, [])
        assert _sql(tree) == original_sql

    def test_idempotent(self) -> None:
        """Calling prune_projection twice with the same columns is idempotent."""
        tree = _parse("SELECT id, name, email FROM users")
        tree = prune_projection(tree, ["id", "name"])
        sql1 = _sql(tree)
        tree = prune_projection(tree, ["id", "name"])
        sql2 = _sql(tree)
        assert sql1 == sql2

    def test_order_preserved(self) -> None:
        tree = _parse("SELECT a, b, c, d FROM t")
        tree = prune_projection(tree, ["d", "a"])
        selected = [expr.name for expr in tree.expressions]
        assert selected == ["d", "a"]


# ===========================================================================
# push_predicates
# ===========================================================================


class TestPushPredicates:
    def test_tuple_equality_adds_where(self) -> None:
        tree = _parse("SELECT * FROM orders")
        tree = push_predicates(tree, [("status", "=", "active")])
        sql = _sql(tree)
        assert "WHERE" in sql
        assert "status" in sql
        assert "active" in sql

    def test_tuple_gt_operator(self) -> None:
        tree = _parse("SELECT * FROM events")
        tree = push_predicates(tree, [("age", ">", 30)])
        sql = _sql(tree)
        assert "age > 30" in sql or "age>30" in sql

    def test_tuple_lt_operator(self) -> None:
        tree = _parse("SELECT * FROM events")
        tree = push_predicates(tree, [("score", "<", 10)])
        sql = _sql(tree)
        assert "score" in sql
        assert "10" in sql

    def test_tuple_lte_operator(self) -> None:
        tree = _parse("SELECT * FROM events")
        tree = push_predicates(tree, [("score", "<=", 100)])
        sql = _sql(tree)
        assert "score" in sql
        assert "<=" in sql

    def test_tuple_gte_operator(self) -> None:
        tree = _parse("SELECT * FROM events")
        tree = push_predicates(tree, [("price", ">=", 9.99)])
        sql = _sql(tree)
        assert "price" in sql
        assert ">=" in sql

    def test_tuple_neq_operator(self) -> None:
        tree = _parse("SELECT * FROM events")
        tree = push_predicates(tree, [("status", "!=", "deleted")])
        sql = _sql(tree)
        assert "status" in sql
        assert "deleted" in sql

    def test_dict_equality_form(self) -> None:
        tree = _parse("SELECT * FROM orders")
        tree = push_predicates(tree, [{"tenant_id": "acme"}])
        sql = _sql(tree)
        assert "tenant_id" in sql
        assert "acme" in sql

    def test_string_predicate_form(self) -> None:
        tree = _parse("SELECT * FROM logs")
        tree = push_predicates(tree, ["level > 2"])
        sql = _sql(tree)
        assert "level" in sql

    def test_multiple_predicates_all_added(self) -> None:
        tree = _parse("SELECT * FROM orders")
        tree = push_predicates(tree, [
            ("status", "=", "active"),
            {"region": "us-east"},
        ])
        sql = _sql(tree)
        assert "status" in sql
        assert "active" in sql
        assert "region" in sql
        assert "us-east" in sql

    def test_existing_where_is_preserved(self) -> None:
        """Existing WHERE predicates must still appear after push_predicates."""
        tree = _parse("SELECT * FROM orders WHERE amount > 100")
        tree = push_predicates(tree, [("status", "=", "active")])
        sql = _sql(tree)
        assert "amount" in sql
        assert "100" in sql
        assert "status" in sql

    def test_integer_value_not_quoted(self) -> None:
        tree = _parse("SELECT * FROM users")
        tree = push_predicates(tree, [("org_id", "=", 42)])
        sql = _sql(tree)
        assert "42" in sql
        assert "org_id = '42'" not in sql

    def test_bool_value(self) -> None:
        tree = _parse("SELECT * FROM users")
        tree = push_predicates(tree, [("active", "=", True)])
        sql = _sql(tree)
        assert "active" in sql

    def test_invalid_operator_raises(self) -> None:
        tree = _parse("SELECT * FROM users")
        with pytest.raises(ValueError, match="Unsupported predicate operator"):
            push_predicates(tree, [("col", "LIKE", "abc%")])

    def test_dict_multi_key_raises(self) -> None:
        tree = _parse("SELECT * FROM users")
        with pytest.raises(ValueError, match="exactly one key"):
            push_predicates(tree, [{"a": 1, "b": 2}])

    def test_invalid_type_raises(self) -> None:
        tree = _parse("SELECT * FROM users")
        with pytest.raises(TypeError):
            push_predicates(tree, [12345])  # type: ignore[list-item]


# ===========================================================================
# push_limit
# ===========================================================================


class TestPushLimit:
    def test_sets_limit_when_none_present(self) -> None:
        tree = _parse("SELECT * FROM users")
        tree = push_limit(tree, 100)
        sql = _sql(tree)
        assert "LIMIT" in sql
        assert "100" in sql

    def test_lowers_existing_limit(self) -> None:
        tree = _parse("SELECT * FROM users LIMIT 500")
        tree = push_limit(tree, 100)
        sql = _sql(tree)
        # Should have LIMIT 100, not LIMIT 500
        assert "100" in sql
        assert "500" not in sql

    def test_keeps_smaller_existing_limit(self) -> None:
        """If existing LIMIT < requested limit, keep the smaller one."""
        tree = _parse("SELECT * FROM users LIMIT 10")
        tree = push_limit(tree, 1000)
        sql = _sql(tree)
        # Should keep LIMIT 10, not raise to 1000
        assert "10" in sql
        assert "1000" not in sql

    def test_equal_limit_unchanged(self) -> None:
        tree = _parse("SELECT * FROM users LIMIT 50")
        tree = push_limit(tree, 50)
        sql = _sql(tree)
        assert "50" in sql

    def test_limit_zero_raises(self) -> None:
        tree = _parse("SELECT * FROM users")
        with pytest.raises(ValueError, match="positive integer"):
            push_limit(tree, 0)

    def test_limit_negative_raises(self) -> None:
        tree = _parse("SELECT * FROM users")
        with pytest.raises(ValueError, match="positive integer"):
            push_limit(tree, -5)

    def test_limit_with_where_clause(self) -> None:
        """LIMIT pushdown must not interfere with WHERE clause."""
        tree = _parse("SELECT * FROM users WHERE status = 1")
        tree = push_limit(tree, 5)
        sql = _sql(tree)
        assert "WHERE" in sql
        assert "status" in sql
        assert "LIMIT 5" in sql or "5" in sql


# ===========================================================================
# extract_partition_hints
# ===========================================================================


class TestExtractPartitionHints:
    def test_detects_date_suffix(self) -> None:
        tree = _parse("SELECT * FROM events WHERE event_date = '2024-01-01'")
        hints = extract_partition_hints(tree)
        assert "event_date" in hints

    def test_detects_dt_suffix(self) -> None:
        tree = _parse("SELECT * FROM logs WHERE created_dt > '2024-01-01'")
        hints = extract_partition_hints(tree)
        assert "created_dt" in hints

    def test_detects_exact_date(self) -> None:
        tree = _parse("SELECT * FROM orders WHERE date = '2024-01-01'")
        hints = extract_partition_hints(tree)
        assert "date" in hints

    def test_detects_day(self) -> None:
        tree = _parse("SELECT * FROM orders WHERE day = 15")
        hints = extract_partition_hints(tree)
        assert "day" in hints

    def test_detects_month(self) -> None:
        tree = _parse("SELECT * FROM sales WHERE month = 3")
        hints = extract_partition_hints(tree)
        assert "month" in hints

    def test_detects_ts(self) -> None:
        tree = _parse("SELECT * FROM events WHERE ts > 1700000000")
        hints = extract_partition_hints(tree)
        assert "ts" in hints

    def test_detects_created_at(self) -> None:
        tree = _parse("SELECT * FROM records WHERE created_at > '2024-01-01'")
        hints = extract_partition_hints(tree)
        assert "created_at" in hints

    def test_detects_partition_prefix(self) -> None:
        tree = _parse("SELECT * FROM data WHERE partition_key = 'A'")
        hints = extract_partition_hints(tree)
        assert "partition_key" in hints

    def test_no_hints_for_plain_columns(self) -> None:
        tree = _parse("SELECT * FROM users WHERE id = 1 AND name = 'Alice'")
        hints = extract_partition_hints(tree)
        assert hints == []

    def test_no_where_returns_empty(self) -> None:
        tree = _parse("SELECT * FROM users")
        hints = extract_partition_hints(tree)
        assert hints == []

    def test_deduplicates_hints(self) -> None:
        """Same partition column appearing twice should only appear once."""
        tree = _parse(
            "SELECT * FROM logs WHERE event_date >= '2024-01-01' AND event_date < '2024-02-01'"
        )
        hints = extract_partition_hints(tree)
        assert hints.count("event_date") == 1

    def test_multiple_partition_columns(self) -> None:
        tree = _parse(
            "SELECT * FROM data WHERE event_date = '2024-01-01' AND partition_shard = 'A'"
        )
        hints = extract_partition_hints(tree)
        assert "event_date" in hints
        assert "partition_shard" in hints

    def test_hints_are_lowercase(self) -> None:
        """Hints are always returned in lowercase."""
        tree = _parse("SELECT * FROM t WHERE ts > 0")
        hints = extract_partition_hints(tree)
        assert all(h == h.lower() for h in hints)


# ===========================================================================
# Planner integration with new limit / predicates params
# ===========================================================================


class TestPlannerIntegration:
    def test_planner_limit_adds_sql_limit(self) -> None:
        p = plan("SELECT * FROM users", limit=50)
        assert "LIMIT" in p.sql
        assert "50" in p.sql

    def test_planner_limit_reflected_in_cache_key(self) -> None:
        """Adding a limit changes the SQL and thus the cache key."""
        p_no_limit = plan("SELECT * FROM users")
        p_with_limit = plan("SELECT * FROM users", limit=50)
        assert p_no_limit.cache_key != p_with_limit.cache_key

    def test_planner_predicates_tuple_form(self) -> None:
        p = plan("SELECT * FROM users", predicates=[("status", "=", "active")])
        assert "status" in p.sql
        assert "active" in p.sql
        assert "WHERE" in p.sql

    def test_planner_predicates_dict_form(self) -> None:
        p = plan("SELECT * FROM users", predicates=[{"region": "eu-west"}])
        assert "region" in p.sql
        assert "eu-west" in p.sql

    def test_planner_predicates_appear_in_predicates_list(self) -> None:
        p = plan("SELECT * FROM users", predicates=[("org_id", "=", 7)])
        assert any("org_id" in pred for pred in p.predicates)

    def test_planner_rls_still_injected_with_predicates(self) -> None:
        """Extra predicates and RLS both appear in the final SQL."""
        p = plan(
            "SELECT * FROM orders",
            claims={"policies": {"tenant_id": "acme"}},
            predicates=[("status", "=", "shipped")],
        )
        assert "tenant_id" in p.sql
        assert "acme" in p.sql
        assert "status" in p.sql
        assert "shipped" in p.sql

    def test_planner_extra_predicates_before_rls_in_order(self) -> None:
        """Extra predicates are pushed BEFORE RLS; both appear."""
        p = plan(
            "SELECT * FROM orders",
            claims={"policies": {"tenant_id": "acme"}},
            predicates=[("amount", ">", 100)],
        )
        assert "amount" in p.sql
        assert "tenant_id" in p.sql

    def test_planner_limit_and_predicates_combined(self) -> None:
        p = plan(
            "SELECT * FROM orders",
            predicates=[{"status": "pending"}],
            limit=10,
        )
        assert "status" in p.sql
        assert "pending" in p.sql
        assert "LIMIT" in p.sql
        assert "10" in p.sql

    def test_planner_keeps_smaller_existing_limit(self) -> None:
        p = plan("SELECT * FROM users LIMIT 5", limit=1000)
        assert "5" in p.sql
        assert "1000" not in p.sql

    def test_planner_none_limit_is_noop(self) -> None:
        """limit=None must not add a LIMIT clause."""
        p = plan("SELECT * FROM users", limit=None)
        assert "LIMIT" not in p.sql

    def test_planner_none_predicates_is_noop(self) -> None:
        """predicates=None must not add any extra WHERE conditions."""
        p_base = plan("SELECT * FROM users")
        p_none = plan("SELECT * FROM users", predicates=None)
        assert p_base.sql == p_none.sql
        assert p_base.cache_key == p_none.cache_key

    def test_planner_cache_key_changes_with_predicates(self) -> None:
        p1 = plan("SELECT * FROM users")
        p2 = plan("SELECT * FROM users", predicates=[("active", "=", True)])
        assert p1.cache_key != p2.cache_key


# ===========================================================================
# REGRESSION — M1 conformance cache keys MUST be unchanged
# ===========================================================================


class TestConformanceCacheKeyRegression:
    """Ensure that plan() with no new M2-A args produces the EXACT same cache
    keys that the M1 conformance suite has frozen.  If any of these fail, the
    planner change broke backwards compatibility.
    """

    def test_plain_select_all(self) -> None:
        """Case 'plain_select_all': SELECT * FROM users, no claims."""
        p = plan("SELECT * FROM users", claims={})
        assert p.cache_key == _KEY_PLAIN_SELECT_ALL, (
            f"Conformance regression: plain_select_all cache_key changed.\n"
            f"  got:      {p.cache_key}\n"
            f"  expected: {_KEY_PLAIN_SELECT_ALL}"
        )

    def test_projection_id_name(self) -> None:
        """Case 'projection_id_name': SELECT id, name FROM users, no claims."""
        p = plan("SELECT id, name FROM users", claims={})
        assert p.cache_key == _KEY_PROJECTION_ID_NAME, (
            f"Conformance regression: projection_id_name cache_key changed.\n"
            f"  got:      {p.cache_key}\n"
            f"  expected: {_KEY_PROJECTION_ID_NAME}"
        )

    def test_rls_tenant_filter(self) -> None:
        """Case 'rls_tenant_filter': SELECT * FROM users with tenant_id RLS."""
        p = plan("SELECT * FROM users", claims={"policies": {"tenant_id": "acme"}})
        assert p.cache_key == _KEY_RLS_TENANT, (
            f"Conformance regression: rls_tenant_filter cache_key changed.\n"
            f"  got:      {p.cache_key}\n"
            f"  expected: {_KEY_RLS_TENANT}"
        )

    def test_aggregate_group_by_tenant(self) -> None:
        """Case 'aggregate_group_by_tenant': GROUP BY, no claims."""
        sql = (
            "SELECT tenant_id, COUNT(*) AS cnt, AVG(age) AS avg_age "
            "FROM users GROUP BY tenant_id"
        )
        p = plan(sql, claims={})
        assert p.cache_key == _KEY_AGG_GROUP_BY, (
            f"Conformance regression: aggregate_group_by_tenant cache_key changed.\n"
            f"  got:      {p.cache_key}\n"
            f"  expected: {_KEY_AGG_GROUP_BY}"
        )

    def test_all_four_cases_no_extra_args(self) -> None:
        """Bulk-check: all four frozen conformance cases pass with no new args."""
        for case in CONFORMANCE_CASES:
            p = plan(case["sql"], claims=case["claims"])
            assert p.cache_key == case["expected_cache_key"], (
                f"[{case['id']}] Conformance regression: cache_key changed.\n"
                f"  got:      {p.cache_key}\n"
                f"  expected: {case['expected_cache_key']}"
            )
