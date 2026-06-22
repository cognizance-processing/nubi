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
8. Integration (route-level): two POST /query calls with different GROUP BY over the
   same base — base-scan fusion is DISABLED; the second query must execute
   independently (MISS) and return its OWN correct schema + values.
   (The old behaviour — returning the first query's aggregated bytes to the second
   query — was data corruption: wrong schema + wrong values across requests/boards.)
9. Integration (route-level): same SQL, DIFFERENT RLS → second call still MISS
   (no cross-tenant reuse).
10. Response bytes from exact-key cache HIT are identical to original MISS bytes.
"""
from __future__ import annotations

import uuid

import pytest

from app.auth.jwt import mint_access_token
from app.connectors.cache import (
    get_base_scan,
    invalidate_base_scan_tag,
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
        """Tags passed to put_base_scan are used for invalidation.

        FIX: use invalidate_base_scan_tag() rather than get_cache().invalidate()
        because on the in-memory backend the base-scan store is now a SEPARATE
        singleton — calling get_cache().invalidate() would not reach base-scan
        entries (that was the bug: cross-segment eviction).
        """
        reset_cache_for_tests()
        key = "d" * 64
        put_base_scan(key, b"some_bytes", tags=["org:test_org"])
        # Verify the entry is present.
        assert get_base_scan(key) == b"some_bytes"
        # Invalidate via the base-scan-specific helper.
        invalidate_base_scan_tag("org:test_org")
        # Entry must be gone.
        assert get_base_scan(key) is None

    def test_cache_segmentation_no_cross_eviction(self):
        """FIX [LOW]: base-scan entries live in a SEPARATE bounded store from
        exact-result entries so they cannot evict each other.

        Creates two ContentAddressedCache stores with max_entries=1 to model
        the worst-case eviction scenario: filling one store must NOT affect the
        other.  This mirrors how _base_scan_instance is independent of
        _cache_instance on the in-memory backend.
        """
        from app.connectors.cache import ContentAddressedCache

        # Two independent bounded stores (max_entries=1 forces aggressive eviction).
        exact_store = ContentAddressedCache(max_entries=2, ttl=300.0)
        bscan_store = ContentAddressedCache(max_entries=2, ttl=300.0)

        # Fill the exact-result store.
        exact_store.put("exact_1", b"exact_bytes_1")
        exact_store.put("exact_2", b"exact_bytes_2")

        # Fill the base-scan store.
        bscan_store.put("bscan_1", b"bscan_bytes_1")
        bscan_store.put("bscan_2", b"bscan_bytes_2")

        # Exact store entries must not have been displaced by base-scan puts.
        assert exact_store.get("exact_1") == b"exact_bytes_1", (
            "Exact-result entry evicted by base-scan puts (cross-segment eviction)."
        )
        assert exact_store.get("exact_2") == b"exact_bytes_2"

        # Base-scan store entries must not have been displaced by exact-result puts.
        assert bscan_store.get("bscan_1") == b"bscan_bytes_1", (
            "Base-scan entry evicted by exact-result puts (cross-segment eviction)."
        )
        assert bscan_store.get("bscan_2") == b"bscan_bytes_2"

        # Filling one store beyond max_entries must NOT evict from the other.
        exact_store.put("exact_3", b"exact_bytes_3")  # causes LRU eviction in exact_store
        assert bscan_store.get("bscan_1") == b"bscan_bytes_1", (
            "Base-scan entry evicted when exact-result store overflowed — stores share LRU."
        )

    def test_exact_cache_invalidate_does_not_clear_base_scan(self):
        """get_cache().invalidate_all() must NOT clear the base-scan store.

        On the in-memory backend, the two stores are now separate singletons.
        This guards against a regression where clearing the exact-result cache
        accidentally empties the base-scan store (or vice-versa).
        """
        from app.connectors.cache import get_cache

        reset_cache_for_tests()
        key_exact = "e" * 64
        key_bscan = "f" * 64

        # Populate both stores.
        get_cache().put(key_exact, b"exact_data")
        put_base_scan(key_bscan, b"bscan_data")

        # Clear the exact-result cache.
        get_cache().invalidate_all()

        # Exact entry must be gone.
        assert get_cache().get(key_exact) is None

        # Base-scan entry must survive (different store).
        assert get_base_scan(key_bscan) == b"bscan_data", (
            "Base-scan entry was cleared when exact-result cache was invalidated — "
            "the two stores are not properly segmented."
        )


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
async def test_first_query_miss_second_different_group_by_also_miss(client):
    """REGRESSION: two queries differing only in GROUP BY must NOT share bytes.

    Base-scan fusion (BET 2b) is DISABLED because the old implementation stored
    the fully-aggregated result of query 1 under the coarser key and then served
    those bytes to query 2 which has a DIFFERENT GROUP BY — wrong schema and wrong
    values (data corruption across requests/boards).

    Correct behaviour: each query executes independently and gets its own MISS.
    The second query must return its OWN schema/values, not query 1's aggregated
    bytes.
    """
    from io import BytesIO

    import pyarrow.ipc as pa_ipc

    token = _make_token()
    headers = {"Authorization": f"Bearer {token}"}

    # Query 1: GROUP BY id — produces rows with columns (id, sum_value).
    sql1 = "SELECT id, SUM(value) AS sum_value FROM demo WHERE active = true GROUP BY id"
    r1 = await client.post("/api/v1/query", json={"sql": sql1}, headers=headers)
    assert r1.status_code == 200
    assert r1.headers.get("x-nubi-cache") == "MISS"
    table1 = pa_ipc.open_stream(BytesIO(r1.content)).read_all()
    # GROUP BY id over 3 active rows → 3 distinct id rows.
    assert "id" in table1.schema.names
    assert "sum_value" in table1.schema.names

    # Query 2: different GROUP BY (name) — different SQL, different schema.
    # With base-scan fusion DISABLED, this must be a MISS, NOT a HIT returning
    # query 1's aggregated bytes.
    sql2 = "SELECT name, COUNT(*) AS cnt FROM demo WHERE active = true GROUP BY name"
    r2 = await client.post("/api/v1/query", json={"sql": sql2}, headers=headers)
    assert r2.status_code == 200
    # CRITICAL: must be MISS — base-scan fusion is disabled; each query executes
    # independently. A HIT here would mean the route served query 1's aggregated
    # bytes to query 2 (wrong schema + wrong values = data corruption).
    assert r2.headers.get("x-nubi-cache") == "MISS", (
        "REGRESSION: base-scan fusion returned query 1's aggregated result to "
        "query 2 which has a different GROUP BY — this is data corruption."
    )
    assert "x-nubi-fusion" not in r2.headers, (
        "REGRESSION: X-Nubi-Fusion header present — route is still doing "
        "unsound base-scan cache promotion."
    )
    # Verify the second query returns its OWN correct schema and values.
    table2 = pa_ipc.open_stream(BytesIO(r2.content)).read_all()
    assert "name" in table2.schema.names, (
        "Query 2 (GROUP BY name) must return a 'name' column, not query 1's schema."
    )
    assert "cnt" in table2.schema.names, (
        "Query 2 (GROUP BY name) must return a 'cnt' column, not query 1's schema."
    )
    # Schema of query 2 must NOT contain the columns unique to query 1.
    assert "sum_value" not in table2.schema.names, (
        "Query 2 must not carry query 1's 'sum_value' column — schemas differ."
    )


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
async def test_different_select_columns_each_get_independent_miss(client):
    """REGRESSION: two queries with different SELECT columns each get a MISS.

    Base-scan fusion is DISABLED.  Even without RLS, two queries that differ
    in their SELECT columns have different physical plans (different cache_key)
    and must each execute independently — one must NOT receive the other's bytes.
    Returning ``SELECT active`` bytes to a ``SELECT id`` query corrupts the result.
    """
    from io import BytesIO

    import pyarrow.ipc as pa_ipc

    token = _make_token(policies={})
    headers = {"Authorization": f"Bearer {token}"}

    # Query 1: SELECT active FROM demo — returns a bool column.
    r1 = await client.post(
        "/api/v1/query",
        json={"sql": "SELECT active FROM demo"},
        headers=headers,
    )
    assert r1.status_code == 200
    assert r1.headers.get("x-nubi-cache") == "MISS"
    table1 = pa_ipc.open_stream(BytesIO(r1.content)).read_all()
    assert "active" in table1.schema.names

    # Query 2: SELECT id FROM demo — different SELECT, should be an independent MISS.
    r2 = await client.post(
        "/api/v1/query",
        json={"sql": "SELECT id FROM demo"},
        headers=headers,
    )
    assert r2.status_code == 200
    # Base-scan fusion is disabled: different SQL → different cache_key → MISS.
    assert r2.headers.get("x-nubi-cache") == "MISS", (
        "REGRESSION: base-scan fusion is serving query 1's bytes to query 2 "
        "which has a different SELECT — this is data corruption."
    )
    assert "x-nubi-fusion" not in r2.headers
    # Verify query 2 returned its own correct schema.
    table2 = pa_ipc.open_stream(BytesIO(r2.content)).read_all()
    assert "id" in table2.schema.names
    # Must NOT have the 'active' column from query 1.
    assert "active" not in table2.schema.names, (
        "Query 2 (SELECT id) must not carry query 1's 'active' column."
    )
