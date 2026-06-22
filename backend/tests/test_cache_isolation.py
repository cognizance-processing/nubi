"""Cross-tenant cache-isolation tests.

HIGH finding: two orgs whose admins have policies={} (no RLS predicates) running
the same SQL used to produce an IDENTICAL plan cache_key and would be served each
other's cached Arrow bytes.

Fix: routes/query.py and routes/metrics.py now compute a scoped key via
``cache_key.scope_cache_key(plan_key, org_id, effective_datastore_id)`` before
every ``cache.get`` / ``cache.put`` call.

Tests
-----
1. Unit: ``scope_cache_key`` — same org+ds+plan → same scoped key (cache HIT).
2. Unit: ``scope_cache_key`` — different orgs, same plan → DIFFERENT keys.
3. Unit: ``scope_cache_key`` — same org, different datastores → DIFFERENT keys.
4. Unit: ``scope_cache_key`` — None org / None datastore handled (no crash).
5. Integration (route-level): two orgs, policies={}, same SQL → DIFFERENT cache keys,
   second org still gets a MISS and is NOT served org A's result bytes.
6. Integration (route-level): same org + same SQL → second request is a HIT.
"""
from __future__ import annotations

import hashlib
import uuid

import pytest

from app.connectors.cache import reset_cache_for_tests
from app.connectors.cache_key import scope_cache_key


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fake_plan_key() -> str:
    """Return a deterministic 64-char hex plan key."""
    return hashlib.sha256(b"SELECT id FROM demo").hexdigest()


def _make_token_for_user(user_id: str, policies: dict | None = None) -> str:
    """Mint a first-party HS256 access token for a specific user_id."""
    from app.auth.jwt import mint_access_token

    extra: dict = {}
    if policies is not None:
        extra["policies"] = policies
    return mint_access_token(user_id=user_id, extra_claims=extra if extra else None)


def _seed_user_in_org(user_id: str, org_id: str) -> None:
    """Register a user→org mapping, ensuring an InMemoryRepo is active.

    The conftest autouse fixture resets the repo to None between tests, so the
    first call here installs a fresh InMemoryRepo.  Subsequent calls reuse the
    same instance (which is the same singleton the route handlers use).
    """
    from app.repos import InMemoryRepo
    from app.repos.provider import get_repo, set_repo

    repo = get_repo()
    if not hasattr(repo, "seed_org_member"):
        # Swap in an InMemoryRepo so routes can resolve user→org lookups.
        repo = InMemoryRepo()
        set_repo(repo)
    repo.seed_org_member(org_id=org_id, user_id=user_id)


# ---------------------------------------------------------------------------
# Unit tests — scope_cache_key
# ---------------------------------------------------------------------------


class TestScopeCacheKey:
    """Unit tests for cache_key.scope_cache_key."""

    def test_same_inputs_same_scoped_key(self):
        """Same plan_key + org + datastore → same scoped key (enables cache HITs)."""
        plan_key = _fake_plan_key()
        k1 = scope_cache_key(plan_key, "org-A", "ds-1")
        k2 = scope_cache_key(plan_key, "org-A", "ds-1")
        assert k1 == k2

    def test_different_org_different_scoped_key(self):
        """SECURITY: different org_id → different scoped key, never share bytes."""
        plan_key = _fake_plan_key()
        k_a = scope_cache_key(plan_key, "org-A", "ds-1")
        k_b = scope_cache_key(plan_key, "org-B", "ds-1")
        assert k_a != k_b

    def test_different_datastore_different_scoped_key(self):
        """Different datastore_id → different scoped key."""
        plan_key = _fake_plan_key()
        k1 = scope_cache_key(plan_key, "org-A", "ds-1")
        k2 = scope_cache_key(plan_key, "org-A", "ds-2")
        assert k1 != k2

    def test_none_org_does_not_crash(self):
        """None org_id is accepted (maps to empty-string sentinel)."""
        plan_key = _fake_plan_key()
        k = scope_cache_key(plan_key, None, "ds-1")
        assert isinstance(k, str) and len(k) == 64

    def test_none_datastore_does_not_crash(self):
        """None datastore_id is accepted (maps to __demo__ sentinel)."""
        plan_key = _fake_plan_key()
        k = scope_cache_key(plan_key, "org-A", None)
        assert isinstance(k, str) and len(k) == 64

    def test_none_org_and_none_datastore_stable(self):
        """None org + None datastore → stable, reproducible key."""
        plan_key = _fake_plan_key()
        k1 = scope_cache_key(plan_key, None, None)
        k2 = scope_cache_key(plan_key, None, None)
        assert k1 == k2

    def test_none_org_differs_from_empty_string(self):
        """None org and empty-string org are treated identically (both → empty str)."""
        plan_key = _fake_plan_key()
        k_none = scope_cache_key(plan_key, None, "ds-1")
        k_empty = scope_cache_key(plan_key, "", "ds-1")
        # Both map to the empty string sentinel — keys must be equal.
        assert k_none == k_empty

    def test_returns_64_char_hex(self):
        plan_key = _fake_plan_key()
        k = scope_cache_key(plan_key, "org-A", "ds-1")
        assert len(k) == 64
        assert all(c in "0123456789abcdef" for c in k)

    def test_different_plan_key_different_scoped_key(self):
        """Even with the same org+ds, different plan content → different scoped key."""
        pk1 = hashlib.sha256(b"SELECT id FROM a").hexdigest()
        pk2 = hashlib.sha256(b"SELECT id FROM b").hexdigest()
        k1 = scope_cache_key(pk1, "org-A", "ds-1")
        k2 = scope_cache_key(pk2, "org-A", "ds-1")
        assert k1 != k2

    def test_scope_key_differs_from_plan_key(self):
        """The scoped key must NOT equal the raw plan key (they are different namespaces)."""
        plan_key = _fake_plan_key()
        scoped = scope_cache_key(plan_key, "org-A", "ds-1")
        assert scoped != plan_key


# ---------------------------------------------------------------------------
# Integration tests — route-level cross-tenant isolation
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_cache_between_tests():
    reset_cache_for_tests()
    yield
    reset_cache_for_tests()


@pytest.mark.asyncio
async def test_two_orgs_no_rls_policies_different_cache_keys_no_cross_org_hit(client):
    """HIGH SECURITY: two orgs with policies={} running the same SQL must NOT share
    a cache entry.  Org B must get a MISS and its own correct result, never org A's
    cached bytes.

    This exercises the exact bug: plan_cache_key(sql, [], {}) is identical for both
    orgs — without scope_cache_key the second org would get a HIT on org A's data.
    With the fix, each org's scoped key is different so the second org gets a MISS.

    Note: on the demo path (no datastore_id), both requests use the demo connector
    and return the same result data — but the ISOLATION guarantee is that the keys
    are different, so the second org cannot be served from the first org's cache
    slot.  We verify this by checking the second request is a MISS.
    """
    sql = "SELECT id, value FROM demo"

    # Create two distinct users, each in a DIFFERENT org.
    user_id_a = str(uuid.uuid4())
    user_id_b = str(uuid.uuid4())
    org_id_a = str(uuid.uuid4())
    org_id_b = str(uuid.uuid4())

    _seed_user_in_org(user_id_a, org_id_a)
    _seed_user_in_org(user_id_b, org_id_b)

    # Both tokens have no RLS policies (policies={}) — the problematic case.
    token_a = _make_token_for_user(user_id_a, policies={})
    token_b = _make_token_for_user(user_id_b, policies={})

    headers_a = {"Authorization": f"Bearer {token_a}"}
    headers_b = {"Authorization": f"Bearer {token_b}"}

    # Org A — first request: must be a MISS.
    r_a = await client.post("/api/v1/query", json={"sql": sql}, headers=headers_a)
    assert r_a.status_code == 200, f"Org A query failed: {r_a.text}"
    assert r_a.headers.get("x-nubi-cache") == "MISS", (
        "Expected first request (org A) to be a cache MISS."
    )

    # Org B — same SQL, different org: must be a MISS, NOT a HIT.
    # A HIT here would mean org B was served org A's cached result bytes —
    # the cross-tenant data leak this fix addresses.
    r_b = await client.post("/api/v1/query", json={"sql": sql}, headers=headers_b)
    assert r_b.status_code == 200, f"Org B query failed: {r_b.text}"
    assert r_b.headers.get("x-nubi-cache") == "MISS", (
        "REGRESSION: org B got a cache HIT on org A's entry — cross-tenant "
        "data leak!  The scoped cache key is not working correctly."
    )


@pytest.mark.asyncio
async def test_same_org_same_sql_still_hits_cache(client):
    """Same user/org + same SQL → second request is a cache HIT (regression guard).

    The fix must not break the cache: within the same org the scoped key is
    deterministic so repeated identical queries must still be served from cache.
    """
    sql = "SELECT name, value FROM demo"

    user_id = str(uuid.uuid4())
    org_id = str(uuid.uuid4())
    _seed_user_in_org(user_id, org_id)
    token = _make_token_for_user(user_id, policies={})
    headers = {"Authorization": f"Bearer {token}"}

    # First request: MISS.
    r1 = await client.post("/api/v1/query", json={"sql": sql}, headers=headers)
    assert r1.status_code == 200, f"First request failed: {r1.text}"
    assert r1.headers.get("x-nubi-cache") == "MISS"

    # Second request, same token (same user → same org) + same SQL: HIT.
    r2 = await client.post("/api/v1/query", json={"sql": sql}, headers=headers)
    assert r2.status_code == 200, f"Second request failed: {r2.text}"
    assert r2.headers.get("x-nubi-cache") == "HIT", (
        "Same org + same SQL should yield a cache HIT after the first MISS."
    )

    # Response bytes must be identical.
    assert r1.content == r2.content, (
        "Cached HIT bytes differ from original MISS bytes — cache corruption."
    )


@pytest.mark.asyncio
async def test_two_orgs_both_get_correct_results(client):
    """After the fix each org independently executes and returns correct data.

    Both orgs query the demo table; each should get the same rows (it's the
    public demo) but via separate, isolated cache slots.
    """
    from io import BytesIO

    import pyarrow.ipc as pa_ipc

    sql = "SELECT id, name FROM demo"

    user_id_a = str(uuid.uuid4())
    user_id_b = str(uuid.uuid4())
    org_id_a = str(uuid.uuid4())
    org_id_b = str(uuid.uuid4())
    _seed_user_in_org(user_id_a, org_id_a)
    _seed_user_in_org(user_id_b, org_id_b)

    token_a = _make_token_for_user(user_id_a, policies={})
    token_b = _make_token_for_user(user_id_b, policies={})

    r_a = await client.post(
        "/api/v1/query", json={"sql": sql},
        headers={"Authorization": f"Bearer {token_a}"},
    )
    r_b = await client.post(
        "/api/v1/query", json={"sql": sql},
        headers={"Authorization": f"Bearer {token_b}"},
    )

    assert r_a.status_code == 200
    assert r_b.status_code == 200

    # Both responses contain valid Arrow IPC streams with the correct schema.
    table_a = pa_ipc.open_stream(BytesIO(r_a.content)).read_all()
    table_b = pa_ipc.open_stream(BytesIO(r_b.content)).read_all()

    assert "id" in table_a.schema.names
    assert "name" in table_a.schema.names
    assert "id" in table_b.schema.names
    assert "name" in table_b.schema.names

    # Both orgs see the same demo data (not each other's—they're on the same
    # demo connector, but from isolated cache slots).
    assert table_a.num_rows == table_b.num_rows


@pytest.mark.asyncio
async def test_org_a_second_request_hits_only_own_cache(client):
    """After org A's first MISS and org B's MISS, org A's second request is a HIT.

    This confirms that org A's scoped key is stable and org B's MISS did not
    clobber org A's cache entry.
    """
    sql = "SELECT active FROM demo"

    user_id_a = str(uuid.uuid4())
    user_id_b = str(uuid.uuid4())
    org_id_a = str(uuid.uuid4())
    org_id_b = str(uuid.uuid4())
    _seed_user_in_org(user_id_a, org_id_a)
    _seed_user_in_org(user_id_b, org_id_b)

    token_a = _make_token_for_user(user_id_a, policies={})
    token_b = _make_token_for_user(user_id_b, policies={})

    # Org A: first request → MISS.
    r_a1 = await client.post(
        "/api/v1/query", json={"sql": sql},
        headers={"Authorization": f"Bearer {token_a}"},
    )
    assert r_a1.status_code == 200
    assert r_a1.headers.get("x-nubi-cache") == "MISS"

    # Org B: MISS (isolated, does not fill org A's slot).
    r_b = await client.post(
        "/api/v1/query", json={"sql": sql},
        headers={"Authorization": f"Bearer {token_b}"},
    )
    assert r_b.status_code == 200
    assert r_b.headers.get("x-nubi-cache") == "MISS"

    # Org A: second request → HIT (from its own slot, not confused by org B).
    r_a2 = await client.post(
        "/api/v1/query", json={"sql": sql},
        headers={"Authorization": f"Bearer {token_a}"},
    )
    assert r_a2.status_code == 200
    assert r_a2.headers.get("x-nubi-cache") == "HIT", (
        "Org A's second request should be a HIT from its own cache slot."
    )
    assert r_a2.content == r_a1.content
