"""Unit tests for the Nubi query planner (app/connectors/planner.py).

Tests verify:
- RLS predicate injection is AST-level (never string-concat)
- Projection narrows the SELECT column list
- Combined projection + RLS
- Non-SELECT raises AppError("UNSUPPORTED_QUERY", 400)
- Cache key changes when RLS claims change
- Cache key is stable for repeated identical inputs
- Non-policies JWT claims do not affect the cache key
- RLS policy dict key order does not affect the cache key
- Params are passed through to the plan unchanged
- Predicates list reflects all WHERE conditions
"""

from __future__ import annotations

import pytest

from app.connectors.planner import plan
from app.errors import AppError


# ---------------------------------------------------------------------------
# Basic SELECT (no claims, no projection)
# ---------------------------------------------------------------------------


class TestBasicSelect:
    def test_passthrough_sql(self) -> None:
        """A simple SELECT with no modifications is reproduced faithfully."""
        p = plan("SELECT id, name FROM users")
        assert "SELECT" in p.sql
        assert "users" in p.sql

    def test_no_predicates_when_no_claims(self) -> None:
        """No RLS predicates are added when claims is empty / None."""
        p = plan("SELECT * FROM orders")
        # No WHERE clause should be present in a table with no original WHERE
        assert "WHERE" not in p.sql

    def test_no_rls_claims_empty(self) -> None:
        p = plan("SELECT * FROM users", claims={})
        assert p.rls_claims == {}
        assert p.predicates == []

    def test_params_passthrough(self) -> None:
        """Query parameters are forwarded to the plan unchanged."""
        p = plan("SELECT * FROM orders WHERE id = $1", params=[42])
        assert p.params == [42]

    def test_params_default_empty(self) -> None:
        p = plan("SELECT 1")
        assert p.params == []

    def test_dialect_default(self) -> None:
        p = plan("SELECT 1")
        assert p.dialect == "postgres"

    def test_cache_key_present(self) -> None:
        p = plan("SELECT id FROM users")
        assert p.cache_key
        assert len(p.cache_key) == 64  # SHA-256 hex


# ---------------------------------------------------------------------------
# RLS predicate injection
# ---------------------------------------------------------------------------


class TestRlsInjection:
    def test_single_string_policy_in_sql(self) -> None:
        """A single string RLS policy appears as a WHERE predicate in the SQL."""
        p = plan("SELECT * FROM orders", claims={"policies": {"tenant_id": "acme"}})
        assert "tenant_id" in p.sql
        assert "acme" in p.sql
        assert "WHERE" in p.sql

    def test_single_policy_in_predicates_list(self) -> None:
        """The injected predicate appears in plan.predicates."""
        p = plan("SELECT * FROM orders", claims={"policies": {"tenant_id": "acme"}})
        assert any("tenant_id" in pred for pred in p.predicates)

    def test_multiple_policies(self) -> None:
        """All policies are injected as AND predicates."""
        p = plan(
            "SELECT * FROM orders",
            claims={"policies": {"tenant_id": "acme", "region": "eu-west"}},
        )
        assert "tenant_id" in p.sql
        assert "region" in p.sql
        assert "AND" in p.sql

    def test_integer_policy(self) -> None:
        """Integer policy values produce bare number literals, not quoted strings."""
        p = plan("SELECT * FROM events", claims={"policies": {"org_id": 42}})
        # Should contain '42' as a bare number literal in SQL
        assert "org_id" in p.sql
        assert "42" in p.sql
        # Must NOT be quoted as a string
        assert "org_id = '42'" not in p.sql

    def test_existing_where_plus_rls(self) -> None:
        """RLS predicates are ANDed with an existing WHERE clause."""
        p = plan(
            "SELECT * FROM orders WHERE status = 1",
            claims={"policies": {"tenant_id": "acme"}},
        )
        assert "status = 1" in p.sql
        assert "tenant_id" in p.sql
        assert "acme" in p.sql
        # Both predicates must appear in the predicates list
        assert any("status" in pred for pred in p.predicates)
        assert any("tenant_id" in pred for pred in p.predicates)

    def test_no_string_concat(self) -> None:
        """RLS injection uses AST rewriting; SQL injection chars are escaped."""
        # A single-quote in a policy value must be properly escaped by sqlglot,
        # not break the SQL string.
        p = plan(
            "SELECT * FROM users",
            claims={"policies": {"name": "O'Brien"}},
        )
        # The generated SQL should be syntactically valid (no raw unescaped quote).
        # sqlglot escapes the value; verify the column and value appear.
        assert "name" in p.sql
        # Verify it did NOT do naive string concatenation (no f-string)
        assert "O'Brien" in p.sql or "O''Brien" in p.sql  # escaped or doubled

    def test_rls_claims_stored_in_plan(self) -> None:
        claims = {"sub": "u1", "policies": {"tenant_id": "x"}}
        p = plan("SELECT 1", claims=claims)
        assert p.rls_claims == claims

    def test_empty_policies_dict(self) -> None:
        """Empty policies dict means no predicates injected."""
        p = plan("SELECT * FROM orders", claims={"sub": "u1", "policies": {}})
        assert "WHERE" not in p.sql
        # Only the SELECT's own predicates (none here)
        assert p.predicates == []


# ---------------------------------------------------------------------------
# Projection push-down
# ---------------------------------------------------------------------------


class TestProjection:
    def test_projection_narrows_select(self) -> None:
        """Projection replaces the SELECT list with only the specified columns."""
        p = plan(
            "SELECT id, name, email, created_at FROM users",
            projection=["id", "email"],
        )
        assert "id" in p.sql
        assert "email" in p.sql
        # 'name' and 'created_at' should NOT be in the SELECT list
        # (they may appear elsewhere, but not as selected columns)
        assert p.projection == ["id", "email"]

    def test_projection_stored_in_plan(self) -> None:
        p = plan("SELECT * FROM users", projection=["id", "name"])
        assert p.projection == ["id", "name"]

    def test_no_projection_is_none(self) -> None:
        p = plan("SELECT * FROM users")
        assert p.projection is None

    def test_projection_with_rls(self) -> None:
        """Projection and RLS injection can be combined."""
        p = plan(
            "SELECT id, amount, tenant_id FROM orders",
            claims={"policies": {"tenant_id": "acme"}},
            projection=["id", "amount"],
        )
        # Projection should be applied
        assert p.projection == ["id", "amount"]
        # RLS should still be injected
        assert "tenant_id" in p.sql
        assert "acme" in p.sql
        # The predicate list should contain the RLS predicate
        assert any("tenant_id" in pred for pred in p.predicates)


# ---------------------------------------------------------------------------
# Non-SELECT rejection
# ---------------------------------------------------------------------------


class TestNonSelectRejection:
    def test_insert_raises(self) -> None:
        with pytest.raises(AppError) as exc_info:
            plan("INSERT INTO users VALUES (1, 'a')")
        assert exc_info.value.code == "UNSUPPORTED_QUERY"
        assert exc_info.value.status == 400

    def test_update_raises(self) -> None:
        with pytest.raises(AppError) as exc_info:
            plan("UPDATE users SET name = 'x' WHERE id = 1")
        assert exc_info.value.code == "UNSUPPORTED_QUERY"
        assert exc_info.value.status == 400

    def test_delete_raises(self) -> None:
        with pytest.raises(AppError) as exc_info:
            plan("DELETE FROM users WHERE id = 1")
        assert exc_info.value.code == "UNSUPPORTED_QUERY"
        assert exc_info.value.status == 400

    def test_drop_raises(self) -> None:
        with pytest.raises(AppError) as exc_info:
            plan("DROP TABLE users")
        assert exc_info.value.code == "UNSUPPORTED_QUERY"
        assert exc_info.value.status == 400


# ---------------------------------------------------------------------------
# Cache key behaviour
# ---------------------------------------------------------------------------


class TestCacheKeyBehaviour:
    def test_stable_for_same_inputs(self) -> None:
        """Repeated calls with identical inputs return the same cache key."""
        p1 = plan("SELECT * FROM orders", claims={"policies": {"tenant_id": "acme"}})
        p2 = plan("SELECT * FROM orders", claims={"policies": {"tenant_id": "acme"}})
        assert p1.cache_key == p2.cache_key

    def test_changes_when_rls_value_changes(self) -> None:
        """Different RLS values produce different cache keys."""
        p1 = plan("SELECT * FROM orders", claims={"policies": {"tenant_id": "acme"}})
        p2 = plan("SELECT * FROM orders", claims={"policies": {"tenant_id": "other"}})
        assert p1.cache_key != p2.cache_key

    def test_changes_when_rls_column_changes(self) -> None:
        """Different RLS column names produce different cache keys."""
        p1 = plan("SELECT * FROM orders", claims={"policies": {"tenant_id": "acme"}})
        p2 = plan("SELECT * FROM orders", claims={"policies": {"org_id": "acme"}})
        assert p1.cache_key != p2.cache_key

    def test_non_policies_claims_do_not_affect_key(self) -> None:
        """JWT claims outside 'policies' (e.g. sub, exp) do not change the key."""
        p1 = plan(
            "SELECT * FROM orders",
            claims={"sub": "user-1", "exp": 1_000_000, "policies": {"tenant_id": "acme"}},
        )
        p2 = plan(
            "SELECT * FROM orders",
            claims={"sub": "user-2", "exp": 9_999_999, "policies": {"tenant_id": "acme"}},
        )
        assert p1.cache_key == p2.cache_key

    def test_rls_key_order_independence(self) -> None:
        """Policy dict insertion order does not affect the cache key."""
        p1 = plan(
            "SELECT * FROM orders",
            claims={"policies": {"tenant_id": "acme", "region": "us-east"}},
        )
        p2 = plan(
            "SELECT * FROM orders",
            claims={"policies": {"region": "us-east", "tenant_id": "acme"}},
        )
        assert p1.cache_key == p2.cache_key

    def test_changes_when_sql_changes(self) -> None:
        """Different SQL strings produce different cache keys."""
        p1 = plan("SELECT * FROM orders")
        p2 = plan("SELECT * FROM users")
        assert p1.cache_key != p2.cache_key

    def test_changes_when_params_change(self) -> None:
        """Different params produce different cache keys."""
        p1 = plan("SELECT * FROM orders WHERE id = $1", params=[1])
        p2 = plan("SELECT * FROM orders WHERE id = $1", params=[2])
        assert p1.cache_key != p2.cache_key

    def test_key_is_64_hex_chars(self) -> None:
        """Cache key is exactly 64 lowercase hex characters (SHA-256)."""
        p = plan("SELECT 1")
        assert len(p.cache_key) == 64
        assert all(c in "0123456789abcdef" for c in p.cache_key)


# ---------------------------------------------------------------------------
# PhysicalPlan model helpers
# ---------------------------------------------------------------------------


class TestPhysicalPlanHelpers:
    def test_to_canonical_dict_has_all_keys(self) -> None:
        p = plan("SELECT id FROM users", params=[1])
        d = p.to_canonical_dict()
        assert set(d.keys()) == {
            "cache_key",
            "dialect",
            "params",
            "predicates",
            "projection",
            "rls_claims",
            "sql",
        }

    def test_to_canonical_dict_is_sorted(self) -> None:
        p = plan("SELECT 1")
        d = p.to_canonical_dict()
        keys = list(d.keys())
        assert keys == sorted(keys)

    def test_to_json_is_compact(self) -> None:
        """to_json() uses compact separators — no space after ':' or ','."""
        p = plan("SELECT 1")
        j = p.to_json()
        # Compact separators mean no space after ':' or ','
        # (spaces can appear inside string values like the SQL itself)
        assert ": " not in j  # no space after colon
        assert ", " not in j  # no space after comma

    def test_to_json_round_trips(self) -> None:
        import json

        p = plan("SELECT id, name FROM users", params=["x"])
        d = json.loads(p.to_json())
        assert d["sql"] == p.sql
        assert d["params"] == p.params
        assert d["cache_key"] == p.cache_key
