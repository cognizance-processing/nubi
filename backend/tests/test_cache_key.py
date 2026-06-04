"""Unit tests for the cache-key derivation algorithm (app/connectors/cache_key.py).

These tests are the authoritative conformance suite for the cache-key spec
documented in docs/cache-key-spec.md. A future Rust executor must produce
byte-identical hex digests for all test vectors below.

Test categories:
- Documented test vectors (must match the spec document exactly)
- Stability / determinism
- Order independence for RLS policy keys
- JWT-claim isolation (only 'policies' affects the key)
- Type sensitivity (string "42" != integer 42)
- Edge cases (empty params, no policies, bool values)
"""

from __future__ import annotations

import hashlib
import json

import pytest

from app.connectors.cache_key import CACHE_KEY_VERSION, compute_cache_key


# ---------------------------------------------------------------------------
# Canonical JSON helper (mirrors the algorithm for white-box verification)
# ---------------------------------------------------------------------------


def _manual_key(sql: str, params: list, rls: dict) -> str:
    """Manually compute the expected cache key for comparison."""
    canonical = json.dumps(
        {"sql": sql, "params": params, "rls": rls},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Documented test vectors — MUST match docs/cache-key-spec.md
# ---------------------------------------------------------------------------


class TestDocumentedVectors:
    """These vectors are the binding contract between the Python planner and
    any future Rust/WASM executor.  Do NOT change expected values without
    updating docs/cache-key-spec.md and bumping CACHE_KEY_VERSION."""

    def test_vector_1_simple_select_no_params_no_rls(self) -> None:
        """Vector 1: simple SELECT, no params, no RLS claims."""
        key = compute_cache_key(
            sql="SELECT id, name FROM users",
            params=[],
            rls_claims={},
        )
        assert key == "2da34f05b16152c531c0a460dc7b0ec722affc90998d44f4fa663b134f487054"

    def test_vector_2_params_and_multi_key_rls(self) -> None:
        """Vector 2: SELECT with params and multi-key RLS policies.

        Note: sub, exp (non-policies claims) are excluded from rls.
        """
        key = compute_cache_key(
            sql="SELECT * FROM orders",
            params=[1, "active"],
            rls_claims={
                "sub": "user-123",
                "exp": 9999999999,
                "policies": {
                    "tenant_id": "acme",
                    "region": "us-east",
                },
            },
        )
        assert key == "fce38ca3a5d762ab04bfe02ce6f5cfc32dccb4b0ed294cf0feeb24865ed2fd31"

    def test_vector_3a_policy_order_a(self) -> None:
        """Vector 3A: policy keys in 'region first' insertion order."""
        key = compute_cache_key(
            sql="SELECT id FROM events",
            params=[],
            rls_claims={"policies": {"region": "us-east", "tenant_id": "acme"}},
        )
        assert key == "1caaa6835929382919e423ee76bd14566ea8acb9d12b703e1876db27fd66a185"

    def test_vector_3b_policy_order_b_same_as_3a(self) -> None:
        """Vector 3B: same policies in 'tenant_id first' order → identical key."""
        key = compute_cache_key(
            sql="SELECT id FROM events",
            params=[],
            rls_claims={"policies": {"tenant_id": "acme", "region": "us-east"}},
        )
        assert key == "1caaa6835929382919e423ee76bd14566ea8acb9d12b703e1876db27fd66a185"

    def test_vector_4_integer_policy_value(self) -> None:
        """Vector 4: integer policy value — bare number in JSON, not quoted."""
        key = compute_cache_key(
            sql="SELECT * FROM logs WHERE level > 2",
            params=[2],
            rls_claims={"policies": {"org_id": 42}},
        )
        assert key == "fa39b9faa32aa1bf8763fe9adee97046388f3fe070f85c444f3defa17321e32c"


# ---------------------------------------------------------------------------
# Manual computation verification
# ---------------------------------------------------------------------------


class TestManualComputation:
    """Cross-check that compute_cache_key matches the hand-computed algorithm."""

    def test_matches_manual_vector_1(self) -> None:
        computed = compute_cache_key("SELECT id, name FROM users", [], {})
        manual = _manual_key("SELECT id, name FROM users", [], {})
        assert computed == manual

    def test_matches_manual_vector_2(self) -> None:
        computed = compute_cache_key(
            "SELECT * FROM orders",
            [1, "active"],
            {
                "sub": "user-123",
                "exp": 9999999999,
                "policies": {"tenant_id": "acme", "region": "us-east"},
            },
        )
        # Manual: only policies extracted and sorted
        manual = _manual_key(
            "SELECT * FROM orders",
            [1, "active"],
            {"region": "us-east", "tenant_id": "acme"},  # sorted
        )
        assert computed == manual

    def test_matches_manual_no_policies_key(self) -> None:
        """When claims has no 'policies' key, rls is empty dict."""
        computed = compute_cache_key("SELECT 1", [], {"sub": "u1", "exp": 999})
        manual = _manual_key("SELECT 1", [], {})
        assert computed == manual


# ---------------------------------------------------------------------------
# Stability / determinism
# ---------------------------------------------------------------------------


class TestStability:
    def test_same_inputs_same_key(self) -> None:
        k1 = compute_cache_key("SELECT * FROM t", [1], {"policies": {"x": "y"}})
        k2 = compute_cache_key("SELECT * FROM t", [1], {"policies": {"x": "y"}})
        assert k1 == k2

    def test_key_is_64_char_hex(self) -> None:
        k = compute_cache_key("SELECT 1", [], {})
        assert len(k) == 64
        assert all(c in "0123456789abcdef" for c in k)

    def test_different_sql_different_key(self) -> None:
        k1 = compute_cache_key("SELECT * FROM orders", [], {})
        k2 = compute_cache_key("SELECT * FROM users", [], {})
        assert k1 != k2

    def test_different_params_different_key(self) -> None:
        k1 = compute_cache_key("SELECT * FROM t WHERE id = $1", [1], {})
        k2 = compute_cache_key("SELECT * FROM t WHERE id = $1", [2], {})
        assert k1 != k2

    def test_different_rls_value_different_key(self) -> None:
        k1 = compute_cache_key("SELECT * FROM t", [], {"policies": {"tenant_id": "a"}})
        k2 = compute_cache_key("SELECT * FROM t", [], {"policies": {"tenant_id": "b"}})
        assert k1 != k2

    def test_different_rls_column_different_key(self) -> None:
        k1 = compute_cache_key("SELECT * FROM t", [], {"policies": {"col_a": "v"}})
        k2 = compute_cache_key("SELECT * FROM t", [], {"policies": {"col_b": "v"}})
        assert k1 != k2


# ---------------------------------------------------------------------------
# Order independence
# ---------------------------------------------------------------------------


class TestOrderIndependence:
    def test_rls_key_order_irrelevant(self) -> None:
        """Reordering policies dict keys must NOT change the cache key."""
        k1 = compute_cache_key(
            "SELECT * FROM t",
            [],
            {"policies": {"a": "1", "b": "2", "c": "3"}},
        )
        k2 = compute_cache_key(
            "SELECT * FROM t",
            [],
            {"policies": {"c": "3", "a": "1", "b": "2"}},
        )
        assert k1 == k2

    def test_three_policy_keys_sorted(self) -> None:
        """Three policy keys in arbitrary order all hash identically."""
        orders = [
            {"x": "1", "y": "2", "z": "3"},
            {"z": "3", "x": "1", "y": "2"},
            {"y": "2", "z": "3", "x": "1"},
        ]
        keys = [
            compute_cache_key("SELECT 1", [], {"policies": p}) for p in orders
        ]
        assert len(set(keys)) == 1, f"Expected one unique key, got: {keys}"


# ---------------------------------------------------------------------------
# JWT-claim isolation
# ---------------------------------------------------------------------------


class TestJwtClaimIsolation:
    def test_sub_claim_ignored(self) -> None:
        k1 = compute_cache_key("SELECT 1", [], {"sub": "user-1", "policies": {"t": "x"}})
        k2 = compute_cache_key("SELECT 1", [], {"sub": "user-2", "policies": {"t": "x"}})
        assert k1 == k2

    def test_exp_claim_ignored(self) -> None:
        k1 = compute_cache_key("SELECT 1", [], {"exp": 1_000_000, "policies": {"t": "x"}})
        k2 = compute_cache_key("SELECT 1", [], {"exp": 9_999_999, "policies": {"t": "x"}})
        assert k1 == k2

    def test_iat_claim_ignored(self) -> None:
        k1 = compute_cache_key("SELECT 1", [], {"iat": 1, "policies": {"t": "x"}})
        k2 = compute_cache_key("SELECT 1", [], {"iat": 2, "policies": {"t": "x"}})
        assert k1 == k2

    def test_multiple_non_policies_ignored(self) -> None:
        k1 = compute_cache_key(
            "SELECT * FROM t",
            [],
            {"sub": "u1", "exp": 1, "iat": 2, "roles": ["admin"], "policies": {"org": "x"}},
        )
        k2 = compute_cache_key(
            "SELECT * FROM t",
            [],
            {"sub": "u9", "exp": 999, "iat": 888, "roles": ["user"], "policies": {"org": "x"}},
        )
        assert k1 == k2

    def test_no_policies_key_same_as_empty(self) -> None:
        """Claims dict with no 'policies' key == empty policies."""
        k1 = compute_cache_key("SELECT 1", [], {"sub": "u1"})
        k2 = compute_cache_key("SELECT 1", [], {})
        assert k1 == k2


# ---------------------------------------------------------------------------
# Type sensitivity
# ---------------------------------------------------------------------------


class TestTypeSensitivity:
    def test_string_vs_integer_policy_value(self) -> None:
        """String '42' and integer 42 must produce different keys."""
        k_str = compute_cache_key("SELECT 1", [], {"policies": {"x": "42"}})
        k_int = compute_cache_key("SELECT 1", [], {"policies": {"x": 42}})
        assert k_str != k_int

    def test_string_vs_bool_policy_value(self) -> None:
        k_str = compute_cache_key("SELECT 1", [], {"policies": {"active": "true"}})
        k_bool = compute_cache_key("SELECT 1", [], {"policies": {"active": True}})
        assert k_str != k_bool

    def test_integer_param(self) -> None:
        k1 = compute_cache_key("SELECT $1", [1], {})
        k2 = compute_cache_key("SELECT $1", ["1"], {})
        assert k1 != k2


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_empty_sql_still_hashes(self) -> None:
        # Not a valid SQL string but the function should not crash.
        k = compute_cache_key("", [], {})
        assert len(k) == 64

    def test_empty_policies_dict_produces_stable_key(self) -> None:
        k1 = compute_cache_key("SELECT 1", [], {"policies": {}})
        k2 = compute_cache_key("SELECT 1", [], {})
        assert k1 == k2

    def test_bool_policy_value(self) -> None:
        k = compute_cache_key("SELECT 1", [], {"policies": {"active": True}})
        assert len(k) == 64

    def test_none_policies_ignored(self) -> None:
        """If policies value is not a dict it is treated as empty."""
        k1 = compute_cache_key("SELECT 1", [], {"policies": None})
        k2 = compute_cache_key("SELECT 1", [], {})
        assert k1 == k2


# ---------------------------------------------------------------------------
# Version constant
# ---------------------------------------------------------------------------


def test_cache_key_version_is_1() -> None:
    """CACHE_KEY_VERSION must be '1' for the current algorithm."""
    assert CACHE_KEY_VERSION == "1"
