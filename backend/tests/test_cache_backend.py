"""Tests for the pluggable query cache (app.connectors.cache).

Coverage
--------
In-memory backend (``ContentAddressedCache``):
  * put with tags + invalidate(tag) evicts ONLY tagged entries
  * invalidate_all clears everything (returns count)
  * stats()/size() shape, hit/miss accounting, tag count
  * TTL expiry still works (lazy eviction on get)
  * LRU eviction at capacity still works (and the tag index stays consistent)
  * backward compat: put(key, value) with no tags behaves as before

Redis backend (``RedisCacheBackend``) WITHOUT a server:
  * a small dict-backed fake redis (setex/get/sadd/smembers/delete/keys/ping)
    is monkeypatched in via app.cache.redis_client.get_redis so the Redis code
    path is exercised in-process — redis is not installed in CI.
  * get/put/invalidate(tag)/invalidate_all over the fake
  * get_cache() selects memory when redis_available() is False and the Redis
    backend when get_redis() returns the fake

Routes (/cache/stats, /cache/invalidate):
  * authenticated first-party request → 200 with expected shape
  * unauthenticated request → 401
  * an embed-style (non-first-party) token is rejected (current_user only
    accepts first-party HS256 access tokens)
  * invalidate with neither tag nor all → 400
"""

from __future__ import annotations

import time
import uuid
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.auth.jwt import mint_access_token
from app.connectors.cache import (
    ContentAddressedCache,
    RedisCacheBackend,
    get_cache,
    reset_cache_for_tests,
)


# ===========================================================================
# In-memory backend
# ===========================================================================


def test_put_get_roundtrip_no_tags_backward_compatible():
    """put(key, value) with no tags still works (existing call sites)."""
    cache = ContentAddressedCache()
    cache.put("k1", b"hello")
    assert cache.get("k1") == b"hello"
    assert cache.get("missing") is None
    stats = cache.stats()
    assert stats["entries"] == 1
    assert stats["hits"] == 1
    assert stats["misses"] == 1
    assert stats["tags"] == 0


def test_invalidate_tag_evicts_only_tagged_entries():
    cache = ContentAddressedCache()
    cache.put("a", b"A", tags=["org:1"])
    cache.put("b", b"B", tags=["org:1", "datastore:9"])
    cache.put("c", b"C", tags=["org:2"])
    cache.put("d", b"D")  # untagged

    removed = cache.invalidate("org:1")
    assert removed == 2
    assert cache.get("a") is None
    assert cache.get("b") is None
    # Other tenant + untagged survive.
    assert cache.get("c") == b"C"
    assert cache.get("d") == b"D"

    # The org:1 tag is gone; datastore:9 (which only b carried) is gone too.
    stats = cache.stats()
    assert "org:1" not in cache._tag_index  # noqa: SLF001 — internal assert
    assert "datastore:9" not in cache._tag_index  # noqa: SLF001
    assert "org:2" in cache._tag_index  # noqa: SLF001
    assert stats["tags"] == 1


def test_invalidate_unknown_tag_returns_zero():
    cache = ContentAddressedCache()
    cache.put("a", b"A", tags=["org:1"])
    assert cache.invalidate("nope") == 0
    assert cache.get("a") == b"A"


def test_invalidate_all_clears_and_returns_count():
    cache = ContentAddressedCache()
    cache.put("a", b"A", tags=["org:1"])
    cache.put("b", b"B", tags=["org:2"])
    cache.put("c", b"C")
    removed = cache.invalidate_all()
    assert removed == 3
    assert cache.size() == 0
    assert cache.stats()["tags"] == 0
    assert cache.get("a") is None


def test_put_same_key_updates_tags():
    """Re-putting a key replaces its tag associations."""
    cache = ContentAddressedCache()
    cache.put("a", b"v1", tags=["org:1"])
    cache.put("a", b"v2", tags=["org:2"])
    assert cache.get("a") == b"v2"
    # Old tag no longer maps to the key.
    assert cache.invalidate("org:1") == 0
    assert cache.get("a") == b"v2"
    # New tag does.
    assert cache.invalidate("org:2") == 1
    assert cache.get("a") is None


def test_ttl_expiry_lazy_eviction():
    cache = ContentAddressedCache(ttl=0.05)
    cache.put("a", b"A", tags=["org:1"])
    assert cache.get("a") == b"A"
    time.sleep(0.08)
    # Expired → miss, lazily evicted, and dropped from the tag index.
    assert cache.get("a") is None
    assert cache.size() == 0
    assert "org:1" not in cache._tag_index  # noqa: SLF001


def test_lru_eviction_keeps_tag_index_consistent():
    cache = ContentAddressedCache(max_entries=2)
    cache.put("a", b"A", tags=["t"])
    cache.put("b", b"B", tags=["t"])
    # Inserting c evicts the LRU (a).
    cache.put("c", b"C", tags=["t"])
    assert cache.get("a") is None
    assert cache.get("b") == b"B"
    assert cache.get("c") == b"C"
    # Tag index no longer references the evicted key.
    assert "a" not in cache._tag_index.get("t", set())  # noqa: SLF001
    # invalidate(t) should now evict exactly the 2 surviving members.
    assert cache.invalidate("t") == 2


# ===========================================================================
# Redis backend — exercised against an in-process fake (no server)
# ===========================================================================


class FakeRedis:
    """A tiny dict-backed stand-in for redis.Redis (subset of the API used).

    Mirrors decode_responses=False: ``get``/``smembers`` return bytes. Only the
    methods the RedisCacheBackend actually calls are implemented.
    """

    def __init__(self) -> None:
        self.kv: dict[str, bytes] = {}
        self.sets: dict[str, set[bytes]] = {}

    def ping(self) -> bool:
        return True

    def setex(self, key: str, ttl: int, value: bytes) -> None:
        assert isinstance(ttl, int) and ttl >= 1
        self.kv[key] = bytes(value)

    def get(self, key: str):
        return self.kv.get(key)

    def sadd(self, key: str, *members: bytes) -> int:
        s = self.sets.setdefault(key, set())
        before = len(s)
        for m in members:
            s.add(m if isinstance(m, bytes) else str(m).encode())
        return len(s) - before

    def smembers(self, key: str) -> set[bytes]:
        return set(self.sets.get(key, set()))

    def delete(self, *keys: str) -> int:
        n = 0
        for k in keys:
            kk = k.decode() if isinstance(k, bytes) else k
            if kk in self.kv:
                del self.kv[kk]
                n += 1
            if kk in self.sets:
                del self.sets[kk]
                n += 1
        return n

    def keys(self, pattern: str = "*"):
        # Only the trailing-* prefix form is used by the backend.
        prefix = pattern[:-1] if pattern.endswith("*") else pattern
        all_keys = list(self.kv.keys()) + list(self.sets.keys())
        return [k.encode() for k in all_keys if k.startswith(prefix)]


@pytest.fixture
def fake_redis(monkeypatch) -> FakeRedis:
    """Patch app.cache.redis_client.get_redis to return a shared FakeRedis."""
    fake = FakeRedis()
    monkeypatch.setattr("app.cache.redis_client.get_redis", lambda: fake)
    monkeypatch.setattr("app.cache.redis_client.redis_available", lambda: True)
    return fake


def test_redis_backend_put_get_roundtrip(fake_redis):
    backend = RedisCacheBackend(ttl=300)
    backend.put("k1", b"hello", tags=["org:1"])
    assert backend.get("k1") == b"hello"
    assert backend.get("missing") is None
    # Value stored under the namespaced key.
    assert "nubi:cache:k1" in fake_redis.kv
    # Tag set holds the namespaced value key.
    assert b"nubi:cache:k1" in fake_redis.smembers("nubi:cache:tag:org:1")


def test_redis_backend_invalidate_tag(fake_redis):
    backend = RedisCacheBackend(ttl=300)
    backend.put("a", b"A", tags=["org:1"])
    backend.put("b", b"B", tags=["org:1"])
    backend.put("c", b"C", tags=["org:2"])

    removed = backend.invalidate("org:1")
    assert removed == 2
    assert backend.get("a") is None
    assert backend.get("b") is None
    assert backend.get("c") == b"C"
    # Tag set itself is deleted.
    assert "nubi:cache:tag:org:1" not in fake_redis.sets


def test_redis_backend_invalidate_all(fake_redis):
    backend = RedisCacheBackend(ttl=300)
    backend.put("a", b"A", tags=["org:1"])
    backend.put("b", b"B")
    # invalidate_all counts value keys only (not tag sets).
    removed = backend.invalidate_all()
    assert removed == 2
    assert backend.get("a") is None
    assert backend.get("b") is None
    assert fake_redis.kv == {}


def test_redis_backend_stats_and_size(fake_redis):
    backend = RedisCacheBackend(ttl=300)
    backend.put("a", b"A", tags=["org:1"])
    backend.get("a")        # hit
    backend.get("missing")  # miss
    stats = backend.stats()
    assert stats["entries"] == 1
    assert stats["hits"] == 1
    assert stats["misses"] == 1
    assert stats["stats_scope"] == "per_worker"


def test_redis_backend_get_degrades_to_miss_on_error(monkeypatch):
    """A raising client must NOT crash — get() returns None (a miss)."""

    class Boom:
        def get(self, key):
            raise RuntimeError("redis down")

    monkeypatch.setattr("app.cache.redis_client.get_redis", lambda: Boom())
    backend = RedisCacheBackend(ttl=300)
    assert backend.get("k") is None  # no exception


# ===========================================================================
# Backend selection via get_cache()
# ===========================================================================


def test_get_cache_selects_memory_when_redis_unavailable(monkeypatch):
    reset_cache_for_tests()
    monkeypatch.setattr("app.cache.redis_client.redis_available", lambda: False)
    cache = get_cache()
    assert isinstance(cache, ContentAddressedCache)
    reset_cache_for_tests()


def test_get_cache_selects_redis_when_available(monkeypatch):
    reset_cache_for_tests()
    fake = FakeRedis()
    monkeypatch.setattr("app.cache.redis_client.redis_available", lambda: True)
    monkeypatch.setattr("app.cache.redis_client.get_redis", lambda: fake)
    cache = get_cache()
    assert isinstance(cache, RedisCacheBackend)
    reset_cache_for_tests()


# ===========================================================================
# Routes — /cache/stats + /cache/invalidate
# ===========================================================================


def _auth_headers(user_id: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {mint_access_token(user_id)}"}


def _make_user(user_id: str) -> dict[str, Any]:
    return {
        "id": user_id,
        "email": "cache-tester@example.com",
        "name": "Cache Tester",
        "avatar_url": None,
        "email_verified": True,
        "created_at": "2024-01-01T00:00:00+00:00",
    }


@pytest_asyncio.fixture
async def cache_client(app, fake_db):
    user_id = str(uuid.uuid4())
    fake_db.users[user_id] = _make_user(user_id)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c, user_id


@pytest.mark.asyncio
async def test_cache_stats_route_authenticated(cache_client):
    client, user_id = cache_client
    resp = await client.get("/api/v1/cache/stats", headers=_auth_headers(user_id))
    assert resp.status_code == 200
    body = resp.json()
    assert body["backend"] in ("memory", "redis")
    assert "entries" in body
    assert "hits" in body
    assert "misses" in body


@pytest.mark.asyncio
async def test_cache_stats_route_requires_auth(cache_client):
    client, _user_id = cache_client
    resp = await client.get("/api/v1/cache/stats")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_cache_invalidate_by_tag(cache_client):
    client, user_id = cache_client
    # Seed the in-memory cache directly so the route has something to evict.
    reset_cache_for_tests()
    cache = get_cache()
    cache.put("k1", b"A", tags=["org:t1"])
    cache.put("k2", b"B", tags=["org:t1"])
    cache.put("k3", b"C", tags=["org:t2"])

    resp = await client.post(
        "/api/v1/cache/invalidate",
        headers=_auth_headers(user_id),
        json={"tag": "org:t1"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["invalidated"] == 2
    assert body["backend"] == "memory"
    assert cache.get("k3") == b"C"


@pytest.mark.asyncio
async def test_cache_invalidate_all(cache_client):
    client, user_id = cache_client
    reset_cache_for_tests()
    cache = get_cache()
    cache.put("k1", b"A", tags=["org:t1"])
    cache.put("k2", b"B")

    resp = await client.post(
        "/api/v1/cache/invalidate",
        headers=_auth_headers(user_id),
        json={"all": True},
    )
    assert resp.status_code == 200
    assert resp.json()["invalidated"] == 2
    assert cache.size() == 0


@pytest.mark.asyncio
async def test_cache_invalidate_requires_tag_or_all(cache_client):
    client, user_id = cache_client
    resp = await client.post(
        "/api/v1/cache/invalidate",
        headers=_auth_headers(user_id),
        json={},
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_cache_routes_reject_embed_token(cache_client):
    """Embed (host-signed) tokens cannot decode as first-party access tokens.

    current_user only accepts first-party HS256 access tokens, so a token that
    is not a valid first-party access token (here: a bogus/embed-style bearer)
    is rejected with 401 — operators only, never embed callers.
    """
    client, _user_id = cache_client
    embed_like = {"Authorization": "Bearer not-a-first-party-access-token"}
    r1 = await client.get("/api/v1/cache/stats", headers=embed_like)
    r2 = await client.post(
        "/api/v1/cache/invalidate", headers=embed_like, json={"all": True}
    )
    assert r1.status_code == 401
    assert r2.status_code == 401
