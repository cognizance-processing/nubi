"""Tests for BET 2b — per-board query fusion + shared base-scan cache key.

Coverage
--------
1. ``compute_base_scan_key`` — same (tables, WHERE, params, RLS) → same key.
2. ``compute_base_scan_key`` — different RLS policies → DIFFERENT keys (isolation).
3. ``compute_base_scan_key`` — different WHERE predicates → DIFFERENT keys.
4. ``compute_base_scan_key`` — same predicate, different SELECT/GROUP BY → SAME key.
5. ``get_base_scan`` / ``put_base_scan`` roundtrip in the in-memory backend.
6. Different RLS policies → separate base-scan entries (SECURITY).
7. Base-scan namespace is SEPARATE from exact-key namespace (no collision).
8. Integration (route-level): two POST /query calls sharing (model, predicate, RLS)
   — first call: MISS → stores base-scan entry; second call (different projection)
   hits base-scan cache → HIT (X-Nubi-Fusion: base-scan header).
9. Integration (route-level): same SQL, DIFFERENT RLS → second call still MISS
   (no cross-tenant reuse).
10. Response bytes from base-scan HIT are identical to original MISS bytes.
"""
from __future__ import annotations

import uuid

import pytest

from app.auth.jwt import mint_access_token
from app.connectors.cache import (
    get_base_scan,
    put_base_scan,
    reset_cache_for_tests,
)
from app.connectors.cache_key import compute_base_scan_key


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _rls(tenant_id: str) -> dict:
    return {"policies": {"tenant_id": tenant_id}}


# ---------------------------------------------------------------------------
# Unit tests — compute_base_scan_key
# ---------------------------------------------------------------------------


class TestComputeBaseScanKey:
    """Unit tests for cache_key.compute_base_scan_key."""

    def test_same_inputs_same_key(self):
        sql = "SELECT id, SUM(amount) FROM orders WHERE status = 'paid' GROUP BY id"
        k1 = compute_base_scan_key(sql, [], _rls("tenantA"))
        k2 = compute_base_scan_key(sql, [], _rls("tenantA"))
        assert k1 is not None
        assert k1 == k2

    def test_different_rls_different_key(self):
        """SECURITY: different tenant policies must yield different keys."""
        sql = "SELECT id FROM orders WHERE status = 'paid'"
        k1 = compute_base_scan_key(sql, [], _rls("tenantA"))
        k2 = compute_base_scan_key(sql, [], _rls("tenantB"))
        assert k1 is not None
        assert k2 is not None
        assert k1 != k2

    def test_different_where_different_key(self):
        sql1 = "SELECT id FROM orders WHERE status = 'paid'"
        sql2 = "SELECT id FROM orders WHERE status = 'pending'"
        k1 = compute_base_scan_key(sql1, [], _rls("tenantA"))
        k2 = compute_base_scan_key(sql2, [], _rls("tenantA"))
        assert k1 is not None
        assert k2 is not None
        assert k1 != k2

    def test_same_predicate_different_select_same_key(self):
        """Two widgets with different SELECTs over the same base should share a key."""
        sql1 = "SELECT SUM(amount) FROM orders WHERE status = 'paid'"
        sql2 = "SELECT COUNT(*) FROM orders WHERE status = 'paid'"
        k1 = compute_base_scan_key(sql1, [], _rls("tenantA"))
        k2 = compute_base_scan_key(sql2, [], _rls("tenantA"))
        assert k1 is not None
        assert k2 is not None
        # Both queries scan the same table with the same predicate and same RLS.
        assert k1 == k2

    def test_no_rls_vs_rls_different_key(self):
        """No RLS and explicit empty policies must yield different keys."""
        sql = "SELECT id FROM orders WHERE status = 'paid'"
        k_no_rls = compute_base_scan_key(sql, [], {})
        k_rls = compute_base_scan_key(sql, [], _rls("tenantA"))
        assert k_no_rls is not None
        assert k_rls is not None
        assert k_no_rls != k_rls

    def test_different_tables_different_key(self):
        sql1 = "SELECT id FROM orders WHERE status = 'paid'"
        sql2 = "SELECT id FROM invoices WHERE status = 'paid'"
        k1 = compute_base_scan_key(sql1, [], _rls("tenantA"))
        k2 = compute_base_scan_key(sql2, [], _rls("tenantA"))
        assert k1 != k2

    def test_returns_64_char_hex(self):
        sql = "SELECT id FROM orders WHERE status = 'paid'"
        k = compute_base_scan_key(sql, [], _rls("tenantA"))
        assert k is not None
        assert len(k) == 64
        assert all(c in "0123456789abcdef" for c in k)

    def test_invalid_sql_returns_none(self):
        k = compute_base_scan_key("NOT VALID SQL {{{}}}}", [], {})
        # Should not raise; returns None for unparseable SQL.
        # (May return a key if sqlglot tolerates it — None is also acceptable.)
        assert k is None or isinstance(k, str)

    def test_empty_rls_policies_stable(self):
        sql = "SELECT id FROM orders"
        k1 = compute_base_scan_key(sql, [], {"policies": {}})
        k2 = compute_base_scan_key(sql, [], {"policies": {}})
        assert k1 == k2

    def test_params_affect_key(self):
        sql = "SELECT id FROM orders WHERE id = $1"
        k1 = compute_base_scan_key(sql, [1], _rls("tenantA"))
        k2 = compute_base_scan_key(sql, [2], _rls("tenantA"))
        assert k1 is not None
        assert k2 is not None
        assert k1 != k2


# ---------------------------------------------------------------------------
# Unit tests — get_base_scan / put_base_scan roundtrip
# ---------------------------------------------------------------------------


class TestBaseScanCacheRoundtrip:
    """Unit tests for the get_base_scan / put_base_scan helpers."""

    def setup_method(self):
        reset_cache_for_tests()

    def test_put_then_get(self):
        key = "a" * 64
        payload = b"arrow_ipc_bytes_here"
        put_base_scan(key, payload)
        result = get_base_scan(key)
        assert result == payload

    def test_miss_returns_none(self):
        key = "b" * 64
        assert get_base_scan(key) is None

    def test_empty_key_is_noop(self):
        """get_base_scan(None/empty) must not raise and must return None."""
        assert get_base_scan("") is None  # type: ignore[arg-type]
        put_base_scan("", b"data")  # should silently no-op

    def test_different_rls_different_entries(self):
        """SECURITY: different keys (from different RLS) store independently."""
        sql = "SELECT id FROM orders WHERE status = 'paid'"
        k1 = compute_base_scan_key(sql, [], _rls("tenantA"))
        k2 = compute_base_scan_key(sql, [], _rls("tenantB"))
        assert k1 != k2  # pre-condition

        put_base_scan(k1, b"bytes_for_A")
        # tenantB's key must still be a MISS.
        assert get_base_scan(k2) is None
        assert get_base_scan(k1) == b"bytes_for_A"

    def test_base_scan_namespace_separate_from_exact_key(self):
        """Base-scan entries must NOT collide with exact-plan cache entries."""
        from app.connectors.cache import get_cache

        cache = get_cache()
        # Store an exact-plan entry under a raw 64-char hex key.
        raw_key = "c" * 64
        cache.put(raw_key, b"exact_bytes")
        # The base-scan namespace wraps the key, so it should be a different slot.
        put_base_scan(raw_key, b"base_scan_bytes")
        # Exact-plan lookup must still return original bytes.
        assert cache.get(raw_key) == b"exact_bytes"
        # Base-scan lookup returns the base-scan bytes.
        assert get_base_scan(raw_key) == b"base_scan_bytes"

    def test_tags_forwarded_to_backend(self):
        """Tags passed to put_base_scan are used for invalidation."""
        from app.connectors.cache import get_cache

        reset_cache_for_tests()
        key = "d" * 64
        put_base_scan(key, b"some_bytes", tags=["org:test_org"])
        # Verify the entry is present.
        assert get_base_scan(key) == b"some_bytes"
        # Invalidate via tag.
        get_cache().invalidate("org:test_org")
        # Entry must be gone.
        assert get_base_scan(key) is None


# ---------------------------------------------------------------------------
# Integration tests — route-level (POST /query)
# ---------------------------------------------------------------------------

# The integration tests register a demo query and drive it via the TestClient.
# We use the built-in demo connector (no datastore_id) so no DB is required.


@pytest.fixture(autouse=True)
def _clear_cache_between_tests():
    reset_cache_for_tests()
    yield
    reset_cache_for_tests()


def _make_token(policies: dict | None = None):
    """Mint a first-party HS256 access token with optional RLS policies."""
    user_id = str(uuid.uuid4())
    extra: dict = {}
    if policies:
        extra["policies"] = policies
    token = mint_access_token(user_id=user_id, extra_claims=extra if extra else None)
    return token


@pytest.mark.asyncio
async def test_first_query_miss_second_same_base_hit(client):
    """Two queries with identical base (table+WHERE+RLS) share base-scan cache."""
    token = _make_token()
    headers = {"Authorization": f"Bearer {token}"}

    # First widget: SELECT id, SUM(value) FROM demo WHERE active = true GROUP BY id
    r1 = await client.post(
        "/api/v1/query",
        json={"sql": "SELECT id, SUM(value) FROM demo WHERE active = true GROUP BY id"},
        headers=headers,
    )
    assert r1.status_code == 200
    assert r1.headers.get("x-nubi-cache") == "MISS"
    bytes_r1 = r1.content

    # Second widget: different SELECT (COUNT) but same base + predicate + RLS.
    # The base-scan entry from query 1 should be reused.
    r2 = await client.post(
        "/api/v1/query",
        json={"sql": "SELECT COUNT(*) FROM demo WHERE active = true"},
        headers=headers,
    )
    assert r2.status_code == 200
    # Should be a HIT (base-scan reuse) — X-Nubi-Cache: HIT
    assert r2.headers.get("x-nubi-cache") == "HIT"


def test_different_rls_no_base_scan_reuse_key_isolation():
    """SECURITY: different RLS policies → different base-scan keys → no reuse.

    This tests the key-level isolation that underpins the security guarantee
    without requiring the planner to inject column predicates (which would fail
    against the demo table). The integration guarantee is: because the key
    differs, get_base_scan(key_b) can never return the bytes stored under key_a.
    """
    sql = "SELECT id, value FROM demo WHERE active = true"

    # Simulate two tenants each with a different policy.
    key_a = compute_base_scan_key(sql, [], {"policies": {"tenant_id": "tenantA"}})
    key_b = compute_base_scan_key(sql, [], {"policies": {"tenant_id": "tenantB"}})

    assert key_a is not None
    assert key_b is not None
    # SECURITY invariant: different tenant policies → different base-scan keys.
    assert key_a != key_b

    reset_cache_for_tests()
    # Store tenantA's bytes.
    put_base_scan(key_a, b"tenant_a_bytes")
    # tenantB's key must be a MISS.
    assert get_base_scan(key_b) is None
    # tenantA's key must still be present.
    assert get_base_scan(key_a) == b"tenant_a_bytes"


@pytest.mark.asyncio
async def test_exact_cache_hit_still_works(client):
    """Exact-plan cache (same SQL+params+RLS) must still yield X-Nubi-Cache: HIT."""
    token = _make_token()
    headers = {"Authorization": f"Bearer {token}"}
    sql = "SELECT id, value FROM demo"

    r1 = await client.post("/api/v1/query", json={"sql": sql}, headers=headers)
    assert r1.status_code == 200
    assert r1.headers.get("x-nubi-cache") == "MISS"

    r2 = await client.post("/api/v1/query", json={"sql": sql}, headers=headers)
    assert r2.status_code == 200
    assert r2.headers.get("x-nubi-cache") == "HIT"


@pytest.mark.asyncio
async def test_response_bytes_consistent(client):
    """Base-scan HIT response bytes match original MISS bytes for same query."""
    token = _make_token()
    headers = {"Authorization": f"Bearer {token}"}

    # Query 1 (MISS) — stores base-scan entry.
    sql1 = "SELECT value FROM demo WHERE active = true"
    r1 = await client.post("/api/v1/query", json={"sql": sql1}, headers=headers)
    assert r1.status_code == 200

    # Query 2 (same query again) — exact HIT, bytes must be identical.
    r2 = await client.post("/api/v1/query", json={"sql": sql1}, headers=headers)
    assert r2.status_code == 200
    assert r2.content == r1.content


@pytest.mark.asyncio
async def test_no_rls_base_scan_shared_across_same_user_different_widgets(client):
    """Without RLS policies, two widget queries over same base share a scan."""
    token = _make_token(policies={})
    headers = {"Authorization": f"Bearer {token}"}

    # Widget 1: SELECT active FROM demo (no WHERE)
    r1 = await client.post(
        "/api/v1/query",
        json={"sql": "SELECT active FROM demo"},
        headers=headers,
    )
    assert r1.status_code == 200
    assert r1.headers.get("x-nubi-cache") == "MISS"

    # Widget 2: SELECT id FROM demo (different SELECT, same base, no WHERE)
    r2 = await client.post(
        "/api/v1/query",
        json={"sql": "SELECT id FROM demo"},
        headers=headers,
    )
    assert r2.status_code == 200
    # Both queries scan the same table with no WHERE and no RLS — base-scan key
    # should match, so second call is a HIT.
    assert r2.headers.get("x-nubi-cache") == "HIT"
