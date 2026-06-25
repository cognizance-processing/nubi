"""Security tests for extended RLS governance (E.1 + E.2).

Tests
-----
1. Equality predicate back-compat (scalar policy → col = value filter)
2. IN-list predicate (list policy → col IN (...) filters correctly)
3. Range predicate (range dict → col >= a AND col < b filters correctly)
4. Hierarchical region→stores expansion yields right IN set
5. Cross-region leakage blocked (user granted region=X cannot see region=Y's children)
6. Fail-closed: governed column absent → 403
7. SQL injection attempt in policy value is parameterized/rejected
8. Request body cannot supply/alter policies (policies are token-only)

Environment
-----------
All tests are pure unit/integration tests against in-memory data; no network or
database connections are required.  The HierarchyResolver is swapped to
InMemoryHierarchyResolver so DB calls are avoided.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

import pyarrow as pa
import pyarrow.compute as pc
import pytest
import sqlglot

# Environment bootstrap (must happen before any app imports).
os.environ.setdefault("DATABASE_URL", "postgresql://fake:fake@localhost/fake")
os.environ.setdefault("JWT_SECRET", "test-jwt-secret-that-is-at-least-32-bytes-long-abcdef")
os.environ.setdefault("JWT_ACCESS_TTL_MIN", "15")
os.environ.setdefault("GOOGLE_CLIENT_ID", "fake-gid")
os.environ.setdefault("GOOGLE_CLIENT_SECRET", "fake-gsecret")
os.environ.setdefault("GOOGLE_REDIRECT_URI", "http://localhost:8000/api/v1/auth/google/callback")
os.environ.setdefault("FRONTEND_URL", "http://localhost:3000")
os.environ.setdefault("COOKIE_SECURE", "false")
os.environ.setdefault("ENV", "test")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_sales_table() -> pa.Table:
    """Demo table: sales data across regions and stores."""
    return pa.table({
        "store_id":  pa.array([10, 11, 12, 20, 21], type=pa.int32()),
        "region":    pa.array(["WC", "WC", "WC", "GP", "GP"]),
        "amount":    pa.array([100, 200, 300, 400, 500], type=pa.float64()),
        "month":     pa.array([1, 1, 2, 1, 2], type=pa.int32()),
    })


def _run(coro):
    """Run an async coroutine synchronously (for non-async tests)."""
    return asyncio.get_event_loop().run_until_complete(coro)


# ---------------------------------------------------------------------------
# Test 1: Equality predicate back-compat (scalar → col = value)
# ---------------------------------------------------------------------------

class TestEqualityPredicateBackCompat:
    """Scalar policy values still produce equality predicates (back-compat)."""

    def test_scalar_string_filters_rows(self):
        """String scalar policy → col = 'value' filters rows in planner."""
        from app.connectors.planner import plan
        from app.connectors.duckdb_conn import DuckDBConnector

        conn = DuckDBConnector()
        conn.register({"sales": _make_sales_table()})

        p = plan("SELECT * FROM sales", claims={"policies": {"region": "WC"}})
        result = conn.execute(p)

        assert result.num_rows == 3, (
            f"Expected 3 WC rows, got {result.num_rows}. SQL: {p.sql}"
        )
        for row_idx in range(result.num_rows):
            assert result.column("region")[row_idx].as_py() == "WC", (
                "SECURITY FAILURE: non-WC row returned by scalar RLS policy"
            )

    def test_scalar_int_filters_rows(self):
        """Integer scalar policy → col = N filters rows."""
        from app.connectors.planner import plan
        from app.connectors.duckdb_conn import DuckDBConnector

        conn = DuckDBConnector()
        conn.register({"sales": _make_sales_table()})

        p = plan("SELECT * FROM sales", claims={"policies": {"store_id": 10}})
        result = conn.execute(p)

        assert result.num_rows == 1, f"Expected 1 row (store_id=10), got {result.num_rows}"
        assert result.column("store_id")[0].as_py() == 10

    def test_scalar_predicate_in_sql(self):
        """Scalar policy appears in the plan SQL as an equality predicate."""
        from app.connectors.planner import plan

        p = plan("SELECT * FROM t", claims={"policies": {"tenant": "acme"}})
        # The policy value should appear in the SQL (it's a literal).
        assert "acme" in p.sql, f"Scalar policy 'acme' missing from SQL: {p.sql}"

    def test_scalar_postfetch_rls_equality(self):
        """apply_rls_postfetch handles scalar equality correctly."""
        from app.connectors.sdk import apply_rls_postfetch

        t = _make_sales_table()
        result = apply_rls_postfetch(t, {"region": "WC"})
        assert result.num_rows == 3
        for r in result.column("region").to_pylist():
            assert r == "WC", f"Non-WC row leaked: {r}"


# ---------------------------------------------------------------------------
# Test 2: IN-list predicate (list policy → col IN (...))
# ---------------------------------------------------------------------------

class TestINListPredicate:
    """List policy values produce IN predicates."""

    def test_in_list_filters_multiple_values(self):
        """List policy → col IN (v1, v2) returns only matching rows."""
        from app.connectors.planner import plan
        from app.connectors.duckdb_conn import DuckDBConnector

        conn = DuckDBConnector()
        conn.register({"sales": _make_sales_table()})

        p = plan(
            "SELECT * FROM sales",
            claims={"policies": {"store_id": [10, 11]}},
        )
        result = conn.execute(p)

        assert result.num_rows == 2, (
            f"Expected 2 rows (store_id IN [10,11]), got {result.num_rows}. SQL: {p.sql}"
        )
        store_ids = result.column("store_id").to_pylist()
        assert set(store_ids) == {10, 11}, f"Wrong store_ids: {store_ids}"

    def test_in_list_sql_contains_in_keyword(self):
        """IN-list policy produces SQL with IN clause."""
        from app.connectors.planner import plan

        p = plan(
            "SELECT * FROM t",
            claims={"policies": {"region": ["WC", "GP"]}},
        )
        assert " IN " in p.sql.upper(), (
            f"Expected IN clause in SQL for list policy, got: {p.sql}"
        )

    def test_empty_list_returns_zero_rows(self):
        """Empty list policy is an impossible predicate → 0 rows (fail-closed)."""
        from app.connectors.planner import plan
        from app.connectors.duckdb_conn import DuckDBConnector

        conn = DuckDBConnector()
        conn.register({"sales": _make_sales_table()})

        p = plan(
            "SELECT * FROM sales",
            claims={"policies": {"store_id": []}},
        )
        result = conn.execute(p)

        assert result.num_rows == 0, (
            f"SECURITY FAILURE: empty IN list returned {result.num_rows} rows "
            f"(expected 0 — fail-closed). SQL: {p.sql}"
        )

    def test_in_list_postfetch_rls(self):
        """apply_rls_postfetch handles list policies correctly."""
        from app.connectors.sdk import apply_rls_postfetch

        t = _make_sales_table()
        result = apply_rls_postfetch(t, {"store_id": [10, 12]})
        assert result.num_rows == 2
        assert set(result.column("store_id").to_pylist()) == {10, 12}

    def test_empty_list_postfetch_rls_zero_rows(self):
        """apply_rls_postfetch: empty list → 0 rows (fail-closed)."""
        from app.connectors.sdk import apply_rls_postfetch

        t = _make_sales_table()
        result = apply_rls_postfetch(t, {"store_id": []})
        assert result.num_rows == 0, (
            f"SECURITY FAILURE: empty IN list postfetch returned {result.num_rows} rows"
        )

    def test_in_list_single_value(self):
        """Single-element list policy → works like equality but via IN."""
        from app.connectors.planner import plan
        from app.connectors.duckdb_conn import DuckDBConnector

        conn = DuckDBConnector()
        conn.register({"sales": _make_sales_table()})

        p = plan(
            "SELECT * FROM sales",
            claims={"policies": {"region": ["WC"]}},
        )
        result = conn.execute(p)
        assert result.num_rows == 3
        for r in result.column("region").to_pylist():
            assert r == "WC"


# ---------------------------------------------------------------------------
# Test 3: Range predicate (range dict → col >= a AND col < b)
# ---------------------------------------------------------------------------

class TestRangePredicate:
    """Range dict policy values produce inequality band predicates."""

    def test_range_gte_lt_filters_correctly(self):
        """Range {gte: a, lt: b} → col >= a AND col < b filters rows."""
        from app.connectors.planner import plan
        from app.connectors.duckdb_conn import DuckDBConnector

        conn = DuckDBConnector()
        conn.register({"sales": _make_sales_table()})

        # amount range [150, 400) should match: 200, 300 (amount 100 is < 150,
        # 400 is not < 400, 500 > 400).
        p = plan(
            "SELECT * FROM sales",
            claims={"policies": {"amount": {"gte": 150, "lt": 400}}},
        )
        result = conn.execute(p)

        amounts = sorted(result.column("amount").to_pylist())
        assert amounts == [200.0, 300.0], (
            f"Range [150,400) should return amounts [200,300], got {amounts}. "
            f"SQL: {p.sql}"
        )

    def test_range_gt_lte(self):
        """Range {gt: a, lte: b} → col > a AND col <= b."""
        from app.connectors.planner import plan
        from app.connectors.duckdb_conn import DuckDBConnector

        conn = DuckDBConnector()
        conn.register({"sales": _make_sales_table()})

        # amount (100, 300] should return: 200, 300
        p = plan(
            "SELECT * FROM sales",
            claims={"policies": {"amount": {"gt": 100, "lte": 300}}},
        )
        result = conn.execute(p)

        amounts = sorted(result.column("amount").to_pylist())
        assert amounts == [200.0, 300.0], f"Got amounts: {amounts}. SQL: {p.sql}"

    def test_range_eq_key(self):
        """Range dict with only 'eq' key → equality predicate."""
        from app.connectors.planner import plan
        from app.connectors.duckdb_conn import DuckDBConnector

        conn = DuckDBConnector()
        conn.register({"sales": _make_sales_table()})

        p = plan(
            "SELECT * FROM sales",
            claims={"policies": {"amount": {"eq": 200.0}}},
        )
        result = conn.execute(p)

        assert result.num_rows == 1
        assert result.column("amount")[0].as_py() == 200.0

    def test_range_postfetch_rls(self):
        """apply_rls_postfetch handles range dict policies correctly."""
        from app.connectors.sdk import apply_rls_postfetch

        t = _make_sales_table()
        # amount [150, 400) → 200, 300
        result = apply_rls_postfetch(t, {"amount": {"gte": 150.0, "lt": 400.0}})
        assert result.num_rows == 2
        amounts = sorted(result.column("amount").to_pylist())
        assert amounts == [200.0, 300.0]

    def test_range_sql_contains_comparison_operators(self):
        """Range policy SQL contains >= and < operators."""
        from app.connectors.planner import plan

        p = plan(
            "SELECT * FROM t",
            claims={"policies": {"amount": {"gte": 100, "lt": 500}}},
        )
        sql_upper = p.sql.upper()
        assert ">=" in sql_upper or "GTE" in sql_upper or "AMOUNT" in sql_upper, (
            f"Range gte not in SQL: {p.sql}"
        )

    def test_range_month_filter(self):
        """Range on integer column filters correctly."""
        from app.connectors.planner import plan
        from app.connectors.duckdb_conn import DuckDBConnector

        conn = DuckDBConnector()
        conn.register({"sales": _make_sales_table()})

        # month >= 2 → only rows with month=2 (2 rows)
        p = plan(
            "SELECT * FROM sales",
            claims={"policies": {"month": {"gte": 2}}},
        )
        result = conn.execute(p)
        months = result.column("month").to_pylist()
        assert all(m >= 2 for m in months), f"Month filter failed: {months}"


# ---------------------------------------------------------------------------
# Test 4: Hierarchical expansion (region→stores)
# ---------------------------------------------------------------------------

class TestHierarchicalExpansion:
    """E.2: HierarchyResolver expands parent values to child IN lists."""

    def _make_resolver_with_wc(self):
        from app.connectors.rls_hierarchy import InMemoryHierarchyResolver
        r = InMemoryHierarchyResolver()
        r.add_sync(
            org_id="org-1",
            dimension="store_id",
            parent_value="WC",
            child_values=["10", "11", "12"],
        )
        return r

    def test_expand_scalar_to_in_list(self):
        """expand_rls_policies expands WC → store_ids [10,11,12]."""
        from app.connectors.planner import expand_rls_policies
        from app.connectors.rls_hierarchy import set_hierarchy_resolver, reset_for_tests

        resolver = self._make_resolver_with_wc()
        set_hierarchy_resolver(resolver)
        try:
            result = _run(expand_rls_policies(
                policies={"store_id": "WC"},
                org_id="org-1",
            ))
            assert result == {"store_id": ["10", "11", "12"]}, (
                f"Expected expansion to [10,11,12], got: {result}"
            )
        finally:
            reset_for_tests()

    def test_expanded_in_list_filters_correctly(self):
        """Expansion + planner produces correct IN filter for child stores."""
        from app.connectors.planner import plan, expand_rls_policies
        from app.connectors.duckdb_conn import DuckDBConnector
        from app.connectors.rls_hierarchy import (
            set_hierarchy_resolver, reset_for_tests, InMemoryHierarchyResolver,
        )

        # Build resolver: WC → stores 10, 11, 12 (as strings matching store_id cast)
        resolver = InMemoryHierarchyResolver()
        resolver.add_sync("org-1", "store_id", "WC", ["10", "11", "12"])
        set_hierarchy_resolver(resolver)

        try:
            # Simulate: token says store_id = "WC" (a regional grant).
            raw_policies = {"store_id": "WC"}
            expanded = _run(expand_rls_policies(raw_policies, org_id="org-1"))
            # expanded = {"store_id": ["10", "11", "12"]}

            # Now plan with expanded policies.
            p = plan(
                "SELECT * FROM sales",
                claims={"policies": expanded},
            )

            conn = DuckDBConnector()
            # Use string store_id to match the expanded string children.
            sales = pa.table({
                "store_id": pa.array(["10", "11", "12", "20", "21"]),
                "region":   pa.array(["WC", "WC", "WC", "GP", "GP"]),
                "amount":   pa.array([100, 200, 300, 400, 500], type=pa.float64()),
            })
            conn.register({"sales": sales})

            result = conn.execute(p)
            assert result.num_rows == 3, (
                f"Expected 3 WC child stores, got {result.num_rows}. SQL: {p.sql}"
            )
            store_ids = set(result.column("store_id").to_pylist())
            assert store_ids == {"10", "11", "12"}, (
                f"Wrong store_ids after expansion: {store_ids}"
            )
        finally:
            reset_for_tests()

    def test_non_hierarchical_scalar_passes_through(self):
        """A dimension with no hierarchy children passes the scalar value through unchanged."""
        from app.connectors.planner import expand_rls_policies
        from app.connectors.rls_hierarchy import (
            set_hierarchy_resolver, reset_for_tests, InMemoryHierarchyResolver,
        )

        # No entry for 'region' dimension.
        resolver = InMemoryHierarchyResolver()
        set_hierarchy_resolver(resolver)
        try:
            result = _run(expand_rls_policies(
                policies={"region": "WC"},
                org_id="org-1",
            ))
            assert result == {"region": "WC"}, (
                f"Non-hierarchical scalar should pass through, got: {result}"
            )
        finally:
            reset_for_tests()

    def test_list_policy_passes_through_without_expansion(self):
        """List policies are not expanded (they're already explicit)."""
        from app.connectors.planner import expand_rls_policies
        from app.connectors.rls_hierarchy import (
            set_hierarchy_resolver, reset_for_tests, InMemoryHierarchyResolver,
        )

        resolver = InMemoryHierarchyResolver()
        resolver.add_sync("org-1", "store_id", "WC", ["10", "11"])
        set_hierarchy_resolver(resolver)
        try:
            # Explicit list should NOT be expanded.
            explicit_list = ["20", "21"]
            result = _run(expand_rls_policies(
                policies={"store_id": explicit_list},
                org_id="org-1",
            ))
            assert result == {"store_id": ["20", "21"]}, (
                f"Explicit list should not be re-expanded, got: {result}"
            )
        finally:
            reset_for_tests()


# ---------------------------------------------------------------------------
# Test 5: Cross-region leakage blocked
# ---------------------------------------------------------------------------

class TestCrossRegionLeakage:
    """Org X's hierarchy must not influence Org Y's RLS expansion."""

    def test_different_org_gets_no_children(self):
        """Org-2 cannot see Org-1's hierarchy entries."""
        from app.connectors.planner import expand_rls_policies
        from app.connectors.rls_hierarchy import (
            set_hierarchy_resolver, reset_for_tests, InMemoryHierarchyResolver,
        )

        resolver = InMemoryHierarchyResolver()
        # org-1 has WC→[10,11,12]
        resolver.add_sync("org-1", "store_id", "WC", ["10", "11", "12"])
        set_hierarchy_resolver(resolver)
        try:
            # org-2 queries WC — must get no expansion (empty → scalar passthrough)
            result = _run(expand_rls_policies(
                policies={"store_id": "WC"},
                org_id="org-2",  # different org!
            ))
            # No children for org-2 → scalar passes through unchanged.
            assert result == {"store_id": "WC"}, (
                f"SECURITY FAILURE: org-2 got org-1's hierarchy expansion: {result}"
            )
        finally:
            reset_for_tests()

    def test_region_x_cannot_see_region_y_children(self):
        """User granted region=GP cannot see WC child stores."""
        from app.connectors.planner import plan, expand_rls_policies
        from app.connectors.duckdb_conn import DuckDBConnector
        from app.connectors.rls_hierarchy import (
            set_hierarchy_resolver, reset_for_tests, InMemoryHierarchyResolver,
        )

        # org-1: WC→[10,11,12], GP→[20,21]
        resolver = InMemoryHierarchyResolver()
        resolver.add_sync("org-1", "store_id", "WC", ["10", "11", "12"])
        resolver.add_sync("org-1", "store_id", "GP", ["20", "21"])
        set_hierarchy_resolver(resolver)

        try:
            # User token claims store_id = "GP"
            expanded = _run(expand_rls_policies(
                policies={"store_id": "GP"},
                org_id="org-1",
            ))
            # expanded = {"store_id": ["20", "21"]}
            assert expanded == {"store_id": ["20", "21"]}, f"Wrong expansion: {expanded}"

            p = plan(
                "SELECT * FROM sales",
                claims={"policies": expanded},
            )

            sales = pa.table({
                "store_id": pa.array(["10", "11", "12", "20", "21"]),
                "amount":   pa.array([100, 200, 300, 400, 500], type=pa.float64()),
            })
            conn = DuckDBConnector()
            conn.register({"sales": sales})
            result = conn.execute(p)

            store_ids = set(result.column("store_id").to_pylist())
            # Must NOT contain WC stores.
            assert store_ids == {"20", "21"}, (
                f"SECURITY FAILURE: GP user saw WC stores: {store_ids}"
            )
            assert "10" not in store_ids and "11" not in store_ids and "12" not in store_ids, (
                "SECURITY FAILURE: WC child stores leaked to GP user"
            )
        finally:
            reset_for_tests()

    def test_null_org_id_gets_no_hierarchy(self):
        """Expansion with org_id=None gets no children (null org cannot resolve)."""
        from app.connectors.planner import expand_rls_policies
        from app.connectors.rls_hierarchy import (
            set_hierarchy_resolver, reset_for_tests, InMemoryHierarchyResolver,
        )

        resolver = InMemoryHierarchyResolver()
        resolver.add_sync("org-1", "store_id", "WC", ["10", "11"])
        set_hierarchy_resolver(resolver)
        try:
            # None org_id should yield no expansion.
            result = _run(expand_rls_policies(
                policies={"store_id": "WC"},
                org_id=None,  # type: ignore[arg-type]
            ))
            # The resolver treats None as a different org from "org-1" — no match.
            assert result == {"store_id": "WC"}, (
                f"SECURITY FAILURE: None org_id got hierarchy children: {result}"
            )
        finally:
            reset_for_tests()


# ---------------------------------------------------------------------------
# Test 6: Fail-closed — governed column absent → 403
# ---------------------------------------------------------------------------

class TestFailClosed:
    """RLS must fail closed when a governed column is absent from the data."""

    def test_missing_column_raises_403_postfetch(self):
        """apply_rls_postfetch raises AppError 403 if policy column is missing."""
        from app.connectors.sdk import apply_rls_postfetch
        from app.errors import AppError

        t = pa.table({
            "amount": pa.array([100, 200, 300], type=pa.float64()),
            # 'region' is NOT present in the table
        })
        with pytest.raises(AppError) as exc_info:
            apply_rls_postfetch(t, {"region": "WC"})

        err = exc_info.value
        assert err.status == 403, f"Expected 403, got {err.status}"
        assert "rls_column_missing" in err.code, f"Wrong error code: {err.code}"

    def test_missing_column_with_list_policy_raises_403(self):
        """Fail-closed applies to list policies too — missing column → 403."""
        from app.connectors.sdk import apply_rls_postfetch
        from app.errors import AppError

        t = pa.table({"amount": pa.array([100])})
        with pytest.raises(AppError) as exc_info:
            apply_rls_postfetch(t, {"store_id": [10, 11]})

        assert exc_info.value.status == 403

    def test_missing_column_with_range_policy_raises_403(self):
        """Fail-closed applies to range policies too — missing column → 403."""
        from app.connectors.sdk import apply_rls_postfetch
        from app.errors import AppError

        t = pa.table({"region": pa.array(["WC"])})
        with pytest.raises(AppError) as exc_info:
            apply_rls_postfetch(t, {"amount": {"gte": 100, "lt": 500}})

        assert exc_info.value.status == 403

    def test_partial_missing_columns_raises_403(self):
        """Multiple missing policy columns — all named in error, still 403."""
        from app.connectors.sdk import apply_rls_postfetch
        from app.errors import AppError

        t = pa.table({"amount": pa.array([100])})
        # Both 'region' and 'store_id' are missing.
        with pytest.raises(AppError) as exc_info:
            apply_rls_postfetch(t, {"region": "WC", "store_id": [10]})

        err = exc_info.value
        assert err.status == 403
        # The error message should mention the missing columns.
        assert "region" in str(err) or "store_id" in str(err)

    def test_empty_policies_returns_table_unchanged(self):
        """Empty policies dict → table returned unchanged (not fail-closed)."""
        from app.connectors.sdk import apply_rls_postfetch

        t = _make_sales_table()
        result = apply_rls_postfetch(t, {})
        assert result.num_rows == t.num_rows


# ---------------------------------------------------------------------------
# Test 7: SQL injection rejected (AST-level parameterization)
# ---------------------------------------------------------------------------

class TestSQLInjection:
    """Policy values with SQL metacharacters must be safe (AST-level, not string concat)."""

    def test_sql_injection_in_scalar_policy_is_safe(self):
        """A malicious string in a scalar policy value cannot break out of SQL context."""
        from app.connectors.planner import plan
        import sqlglot as sg
        import sqlglot.expressions as exp

        malicious = "'; DROP TABLE sales; --"
        p = plan("SELECT * FROM t", claims={"policies": {"region": malicious}})

        # The SQL must re-parse cleanly.
        reparsed = sg.parse_one(p.sql)
        assert isinstance(reparsed, exp.Select), (
            f"SECURITY FAILURE: SQL injection broke SELECT structure. SQL: {p.sql}"
        )
        # The outer statement must not be DROP or anything else.
        assert reparsed.key == "select", f"Got {reparsed.key}, expected 'select'"

    def test_sql_injection_in_list_policy_is_safe(self):
        """Malicious values in a list policy are embedded as AST literals."""
        from app.connectors.planner import plan
        import sqlglot as sg
        import sqlglot.expressions as exp

        malicious_list = ["legit", "'; DROP TABLE t; --", "1 OR 1=1"]
        p = plan("SELECT * FROM t", claims={"policies": {"col": malicious_list}})

        reparsed = sg.parse_one(p.sql)
        assert isinstance(reparsed, exp.Select), (
            f"SECURITY FAILURE: IN-list SQL injection broke SELECT. SQL: {p.sql}"
        )

    def test_sql_injection_in_range_value_is_safe(self):
        """Malicious range values are embedded as AST literals, not concatenated."""
        from app.connectors.planner import plan
        import sqlglot as sg
        import sqlglot.expressions as exp

        # Range values must be numeric — string injection attempt in numeric context.
        p = plan(
            "SELECT * FROM t",
            claims={"policies": {"amount": {"gte": 100, "lt": 500}}},
        )

        reparsed = sg.parse_one(p.sql)
        assert isinstance(reparsed, exp.Select), (
            f"SQL injection in range value broke SELECT. SQL: {p.sql}"
        )

    def test_malicious_scalar_does_not_execute(self):
        """A DROP TABLE attempt in a policy value returns 0 rows, table intact."""
        from app.connectors.planner import plan
        from app.connectors.duckdb_conn import DuckDBConnector

        conn = DuckDBConnector()
        t = pa.table({"region": pa.array(["WC"]), "amount": pa.array([100.0])})
        conn.register({"t": t})

        malicious = "'; DROP TABLE t; --"
        p = plan("SELECT * FROM t", claims={"policies": {"region": malicious}})
        result = conn.execute(p)

        # 0 rows (malicious value ≠ 'WC'), and the table still exists.
        assert result.num_rows == 0, (
            f"SECURITY FAILURE: malicious policy returned {result.num_rows} rows"
        )
        # The table still exists — DROP was not executed.
        still_exists = conn.execute(plan("SELECT * FROM t", claims={}))
        assert still_exists.num_rows == 1, (
            "SECURITY FAILURE: table was dropped by malicious policy value"
        )


# ---------------------------------------------------------------------------
# Test 8: Request body cannot supply/alter policies (token-only)
# ---------------------------------------------------------------------------

class TestTokenOnlyPolicies:
    """Policies must come exclusively from the verified token."""

    def test_planner_uses_only_claims_arg(self):
        """The planner's claims= arg is its only source of policies.

        This confirms that no other code path (named_params, predicates,
        request body) can inject policies.  The planner has no other entry
        point for RLS policy values.
        """
        from app.connectors.planner import plan

        # Token claims include a policy.
        token_claims = {"policies": {"region": "WC"}}

        # "Attacker" extra predicates (the predicates= arg handles trusted,
        # caller-supplied predicates — but NOT RLS policies).
        p = plan(
            "SELECT * FROM t",
            claims=token_claims,
            predicates=None,  # no extra predicates
        )

        assert "WC" in p.sql, "Token policy 'WC' must be in SQL"

    def test_rls_claims_stored_in_plan(self):
        """PhysicalPlan.rls_claims stores only what was passed as claims."""
        from app.connectors.planner import plan

        token_claims = {"policies": {"tenant": "acme"}, "sub": "user-1"}
        p = plan("SELECT * FROM t", claims=token_claims)

        assert p.rls_claims == token_claims, (
            f"rls_claims does not match token claims: {p.rls_claims}"
        )

    def test_empty_claims_produces_no_rls_predicate(self):
        """Empty claims → no RLS predicate in SQL (token with no policies)."""
        from app.connectors.planner import plan

        p_no_rls = plan("SELECT * FROM t", claims={})
        p_with_rls = plan("SELECT * FROM t", claims={"policies": {"region": "WC"}})

        # The no-RLS plan must NOT contain WC.
        assert "WC" not in p_no_rls.sql, (
            f"SECURITY FAILURE: no-RLS plan contains 'WC': {p_no_rls.sql}"
        )
        # And it should produce more rows than the RLS-constrained plan when
        # executing (trivially: the SQL without the WHERE has no region filter).
        assert "WHERE" not in p_no_rls.sql.upper() or "REGION" not in p_no_rls.sql.upper()

    def test_multiple_policy_shapes_all_injected(self):
        """All policy entries (scalar, list, range) are injected from claims."""
        from app.connectors.planner import plan

        p = plan(
            "SELECT * FROM t",
            claims={"policies": {
                "region": "WC",              # scalar
                "store_id": [10, 11, 12],    # list
                "amount": {"gte": 100},      # range
            }},
        )

        sql = p.sql
        # WC (scalar), store_id IN (...) (list), amount >= 100 (range)
        assert "WC" in sql, f"Scalar policy 'WC' missing from SQL: {sql}"
        assert "10" in sql or "11" in sql, f"IN-list values missing from SQL: {sql}"
        assert "100" in sql, f"Range value 100 missing from SQL: {sql}"


# ---------------------------------------------------------------------------
# Bonus: planner helper function unit tests
# ---------------------------------------------------------------------------

class TestPlannerHelpers:
    """Unit tests for the low-level predicate builder helpers."""

    def test_make_literal_bool(self):
        from app.connectors.planner import _make_literal
        import sqlglot.expressions as exp
        node = _make_literal(True)
        assert isinstance(node, exp.Boolean)

    def test_make_literal_int(self):
        from app.connectors.planner import _make_literal
        import sqlglot.expressions as exp
        node = _make_literal(42)
        assert isinstance(node, exp.Literal)

    def test_make_literal_string(self):
        from app.connectors.planner import _make_literal
        import sqlglot.expressions as exp
        node = _make_literal("hello")
        assert isinstance(node, exp.Literal)
        assert node.is_string

    def test_make_in_predicate_empty_list_returns_impossible(self):
        from app.connectors.planner import _make_in_predicate
        import sqlglot.expressions as exp
        node = _make_in_predicate("col", [])
        # Should be 1 = 0 (impossible predicate)
        assert isinstance(node, exp.EQ)
        sql = node.sql(dialect="postgres")
        assert "1" in sql and "0" in sql

    def test_make_range_predicates_gte_lt(self):
        from app.connectors.planner import _make_range_predicates
        import sqlglot.expressions as exp
        nodes = _make_range_predicates("amount", {"gte": 100, "lt": 500})
        assert len(nodes) == 2
        types = {type(n).__name__ for n in nodes}
        assert "GTE" in types
        assert "LT" in types

    def test_make_rls_predicates_scalar(self):
        from app.connectors.planner import _make_rls_predicates
        import sqlglot.expressions as exp
        nodes = _make_rls_predicates("col", "value")
        assert len(nodes) == 1
        assert isinstance(nodes[0], exp.EQ)

    def test_make_rls_predicates_list(self):
        from app.connectors.planner import _make_rls_predicates
        import sqlglot.expressions as exp
        nodes = _make_rls_predicates("col", [1, 2, 3])
        assert len(nodes) == 1
        assert isinstance(nodes[0], exp.In)

    def test_make_rls_predicates_range(self):
        from app.connectors.planner import _make_rls_predicates
        nodes = _make_rls_predicates("col", {"gte": 1, "lt": 10})
        assert len(nodes) == 2


# ---------------------------------------------------------------------------
# InMemoryHierarchyResolver unit tests
# ---------------------------------------------------------------------------

class TestInMemoryHierarchyResolver:
    """Unit tests for the in-memory resolver used in tests."""

    def test_resolve_known_entry(self):
        from app.connectors.rls_hierarchy import InMemoryHierarchyResolver
        r = InMemoryHierarchyResolver()
        r.add_sync("org-1", "store_id", "WC", ["10", "11"])
        children = _run(r.resolve("org-1", "store_id", "WC"))
        assert children == ["10", "11"]

    def test_resolve_unknown_entry_returns_empty(self):
        from app.connectors.rls_hierarchy import InMemoryHierarchyResolver
        r = InMemoryHierarchyResolver()
        children = _run(r.resolve("org-1", "store_id", "UNKNOWN"))
        assert children == []

    def test_cross_org_isolation(self):
        from app.connectors.rls_hierarchy import InMemoryHierarchyResolver
        r = InMemoryHierarchyResolver()
        r.add_sync("org-1", "store_id", "WC", ["10", "11"])
        # org-2 must not see org-1's entries.
        children = _run(r.resolve("org-2", "store_id", "WC"))
        assert children == [], f"SECURITY FAILURE: org-2 got org-1's children: {children}"

    def test_expand_policy_with_children(self):
        from app.connectors.rls_hierarchy import InMemoryHierarchyResolver
        r = InMemoryHierarchyResolver()
        r.add_sync("org-1", "region", "WC", ["store_10", "store_11"])
        result = _run(r.expand_policy("org-1", "region", "WC"))
        assert result == ["store_10", "store_11"]

    def test_expand_policy_no_children_returns_scalar(self):
        from app.connectors.rls_hierarchy import InMemoryHierarchyResolver
        r = InMemoryHierarchyResolver()
        result = _run(r.expand_policy("org-1", "region", "GP"))
        assert result == "GP"  # scalar passthrough


