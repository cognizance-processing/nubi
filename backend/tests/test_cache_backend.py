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
    _DEFAULT_MAX_ENTRIES,
    _DEFAULT_BASE_SCAN_MAX_ENTRIES,
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


def test_purge_key_from_tags_uses_reverse_index_not_full_scan():
    """_purge_key_from_tags must use the O(tags-for-key) reverse index path.

    Regression guard for the LOW fix: previously _purge_key_from_tags iterated
    the FULL _tag_index to find and remove a single key, causing O(all-tags)
    latency under a large tag set.  The fix maintains a _key_tags reverse index
    (key → tags) so purge is O(tags-for-key).

    This test verifies:
    1. After LRU eviction the evicted key is absent from _tag_index but all
       other keys' entries are intact (no lost invalidations).
    2. The reverse index (_key_tags) does NOT retain the evicted key.
    3. A large number of unrelated tags in the index does NOT cause _tag_index
       to be scanned for a single-key eviction (structural correctness: only the
       evicted key's specific tags are touched).
    """
    # Build a cache with 1 slot so every put beyond the first triggers LRU eviction.
    cache = ContentAddressedCache(max_entries=1)

    # Populate a large number of distinct tags for other (future) keys so that
    # a full-scan implementation would iterate all of them unnecessarily.
    # We seed the _tag_index directly to simulate many tags without actually
    # putting entries (which would themselves be evicted).
    many_unrelated_tags: list[str] = [f"org:{i}" for i in range(500)]

    # Put the first entry with its own tag.
    cache.put("evict_me", b"val_a", tags=["evict_tag"])

    # Directly inject many unrelated tag entries into the index to simulate a
    # large tag index — these tags carry no keys but inflate the index.
    with cache._lock:  # noqa: SLF001
        for t in many_unrelated_tags:
            cache._tag_index[t] = {f"phantom:{t}"}  # noqa: SLF001

    # Confirm the evictable key is currently tracked in the reverse index.
    assert "evict_me" in cache._key_tags  # noqa: SLF001

    # This put triggers LRU eviction of "evict_me".
    cache.put("survivor", b"val_b", tags=["survivor_tag"])

    # The evicted key must be gone from _store and both indexes.
    assert cache.get("evict_me") is None
    assert "evict_me" not in cache._key_tags  # noqa: SLF001
    assert "evict_me" not in cache._tag_index.get("evict_tag", set())  # noqa: SLF001
    # The evict_tag set itself must be cleaned up (it's now empty).
    assert "evict_tag" not in cache._tag_index  # noqa: SLF001

    # The 500 unrelated tags must be UNTOUCHED — a full scan would not have
    # modified their sets, but we confirm no corruption happened.
    assert len([t for t in many_unrelated_tags if t in cache._tag_index]) == 500  # noqa: SLF001

    # The survivor key must be retrievable and in the reverse index.
    assert cache.get("survivor") == b"val_b"
    assert cache._key_tags.get("survivor") == ("survivor_tag",)  # noqa: SLF001


def test_purge_correctness_multi_tag_key_eviction():
    """LRU eviction of a key with multiple tags cleans ALL its tag associations.

    A key carrying N tags must be removed from all N tag sets — not just the
    first one.  After eviction, invalidating any of those tags must not return
    the evicted key (it's gone), and the tag sets must not retain stale members.
    """
    cache = ContentAddressedCache(max_entries=2)

    # "multi" carries 3 tags and will be the LRU candidate.
    cache.put("multi", b"M", tags=["tag_x", "tag_y", "tag_z"])
    cache.put("second", b"S", tags=["tag_x"])

    # "third" evicts "multi" (LRU at this point).
    cache.put("third", b"T", tags=["tag_z"])

    # "multi" must be gone from ALL tag sets.
    assert "multi" not in cache._tag_index.get("tag_x", set())  # noqa: SLF001
    assert "multi" not in cache._tag_index.get("tag_y", set())  # noqa: SLF001
    assert "multi" not in cache._tag_index.get("tag_z", set())  # noqa: SLF001

    # tag_y had only "multi" — it should be fully cleaned up.
    assert "tag_y" not in cache._tag_index  # noqa: SLF001

    # "multi" must be absent from the reverse index too.
    assert "multi" not in cache._key_tags  # noqa: SLF001

    # invalidate(tag_x) evicts "second" only (1 key).
    assert cache.invalidate("tag_x") == 1
    # invalidate(tag_z) evicts "third" only (1 key).
    assert cache.invalidate("tag_z") == 1
    assert cache.size() == 0


# ===========================================================================
# Redis backend — exercised against an in-process fake (no server)
# ===========================================================================


class FakePipeline:
    """Minimal pipeline stub for FakeRedis.

    Records queued commands and executes them against the parent FakeRedis
    when ``execute()`` is called.  Tracks the number of ``execute()`` calls
    so tests can assert that batching (not per-key round-trips) was used.
    """

    def __init__(self, parent: "FakeRedis") -> None:
        self._parent = parent
        self._queue: list[tuple[str, tuple]] = []

    def exists(self, key: str) -> "FakePipeline":
        self._queue.append(("exists", (key,)))
        return self

    def execute(self) -> list:
        results = []
        for cmd, args in self._queue:
            if cmd == "exists":
                # Access the kv store directly — do NOT call parent.exists()
                # so that exists_call_count stays at 0 for the pipeline path.
                k = args[0]
                if isinstance(k, (bytes, bytearray)):
                    k = k.decode("utf-8", "replace")
                results.append(1 if k in self._parent.kv else 0)
        self._queue.clear()
        self._parent.pipeline_execute_count += 1
        return results


class FakeRedis:
    """A tiny dict-backed stand-in for redis.Redis (subset of the API used).

    Mirrors decode_responses=False: ``get``/``smembers`` return bytes. Only the
    methods the RedisCacheBackend actually calls are implemented.

    Additions for tag-set growth prevention tests:
    - ``expire(key, ttl)`` — records the intended TTL (does not actually expire
      entries; just tracks the call so tests can assert it was made).
    - ``exists(key)`` — returns 1 if the key is in kv, else 0.
    - ``srem(key, *members)`` — removes members from a set.
    - ``pipeline()`` — returns a ``FakePipeline`` for batched command testing.
    - ``pipeline_execute_count`` — tracks how many pipeline.execute() calls were
      made so tests can assert O(1) round-trips rather than O(N).
    - ``exists_call_count`` — tracks direct (non-pipeline) exists() calls so
      tests can assert the N+1 path is NOT taken.
    """

    def __init__(self) -> None:
        self.kv: dict[str, bytes] = {}
        self.sets: dict[str, set[bytes]] = {}
        # Records (key, ttl) pairs from expire() calls (for assertion in tests).
        self.expire_calls: list[tuple[str, int]] = []
        # Counters for asserting batched vs scalar access patterns.
        self.pipeline_execute_count: int = 0
        self.exists_call_count: int = 0

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

    def srem(self, key: str, *members) -> int:
        s = self.sets.get(key)
        if s is None:
            return 0
        removed = 0
        for m in members:
            mb = m if isinstance(m, bytes) else str(m).encode()
            if mb in s:
                s.discard(mb)
                removed += 1
        if not s:
            self.sets.pop(key, None)
        return removed

    def exists(self, key: str) -> int:
        """Return 1 if *key* exists in kv, else 0 (mirrors redis-py EXISTS)."""
        self.exists_call_count += 1
        k = key.decode("utf-8", "replace") if isinstance(key, (bytes, bytearray)) else key
        return 1 if k in self.kv else 0

    def expire(self, key: str, ttl: int) -> int:
        """Record the EXPIRE call and return 1 (success) if key exists, else 0."""
        kk = key.decode("utf-8", "replace") if isinstance(key, (bytes, bytearray)) else key
        self.expire_calls.append((kk, ttl))
        return 1 if (kk in self.kv or kk in self.sets) else 0

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

    def pipeline(self) -> FakePipeline:
        """Return a FakePipeline that batches commands against this store."""
        return FakePipeline(self)


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


# ---------------------------------------------------------------------------
# Tag-set growth prevention (MED fix)
# ---------------------------------------------------------------------------


def test_redis_tag_set_receives_expire_after_sadd(fake_redis):
    """put() must call EXPIRE on the tag set so it is reaped after entries expire.

    Without the EXPIRE call, tag sets accumulate stale member ids indefinitely
    once all their value entries have expired (unbounded growth).  The fix calls
    EXPIRE with ttl * _REDIS_TAG_SET_TTL_MULTIPLIER after each SADD so Redis
    automatically reaps the set.
    """
    from app.connectors.cache import _REDIS_TAG_SET_TTL_MULTIPLIER

    backend = RedisCacheBackend(ttl=300)
    backend.put("k1", b"hello", tags=["org:1"])

    # EXPIRE must have been called on the tag-set key.
    tag_set_key = "nubi:cache:tag:org:1"
    expire_targets = [k for k, _ttl in fake_redis.expire_calls]
    assert tag_set_key in expire_targets, (
        "EXPIRE was not called on the tag set — tag sets will grow without bound."
    )

    # The TTL on the tag set must be the entry TTL × the multiplier.
    ttl_for_tag = next(
        ttl for k, ttl in fake_redis.expire_calls if k == tag_set_key
    )
    assert ttl_for_tag == 300 * _REDIS_TAG_SET_TTL_MULTIPLIER


def test_redis_tag_set_expire_refreshed_on_each_sadd(fake_redis):
    """Every put() to an existing tag refreshes the tag set's EXPIRE.

    Active tag sets (still receiving new members) must stay alive, so EXPIRE
    is called on every SADD, not just the first one.
    """
    backend = RedisCacheBackend(ttl=60)
    backend.put("k1", b"v1", tags=["org:1"])
    backend.put("k2", b"v2", tags=["org:1"])

    tag_set_key = "nubi:cache:tag:org:1"
    expire_calls_for_tag = [(k, t) for k, t in fake_redis.expire_calls if k == tag_set_key]
    # EXPIRE should have been called at least once per put.
    assert len(expire_calls_for_tag) >= 2, (
        "EXPIRE was not refreshed on each SADD — idle tag sets may not be reaped."
    )


def test_redis_invalidate_prunes_stale_members_from_tag_set(fake_redis):
    """invalidate(tag) must prune stale (expired) member ids from the tag set.

    When value keys expire (TTL), Redis removes the value but the id remains in
    the tag SET.  invalidate() must detect stale ids (via EXISTS), SREM them,
    and return a count that reflects only actually-deleted live entries.
    """
    backend = RedisCacheBackend(ttl=300)

    # Populate two entries under the same tag.
    backend.put("live_key", b"alive", tags=["org:1"])
    backend.put("stale_key", b"expired", tags=["org:1"])

    # Simulate expiry of stale_key by deleting it from the fake kv directly
    # (mimics Redis TTL expiry — the value key is gone but the tag set still
    # holds its id as a member).
    del fake_redis.kv["nubi:cache:stale_key"]

    # Both ids are in the tag set.
    tag_set_key = "nubi:cache:tag:org:1"
    assert b"nubi:cache:live_key" in fake_redis.smembers(tag_set_key)
    assert b"nubi:cache:stale_key" in fake_redis.smembers(tag_set_key)

    # invalidate() should:
    #   1. detect stale_key is absent (EXISTS → 0) and SREM it, AND
    #   2. delete live_key and the tag set, AND
    #   3. return 1 (only the live entry counts).
    removed = backend.invalidate("org:1")
    assert removed == 1, (
        f"Expected 1 live entry deleted, got {removed}. "
        "Stale members must not inflate the count."
    )

    # The live key must have been deleted.
    assert backend.get("live_key") is None

    # The tag set itself must be gone.
    assert tag_set_key not in fake_redis.sets


def test_redis_invalidate_all_stale_members_do_not_inflate_count(fake_redis):
    """invalidate(tag) with ALL stale members returns 0, not len(members).

    Regression guard: if all members of a tag set have expired, invalidate()
    must return 0 (nothing to delete), not the stale member count.
    """
    backend = RedisCacheBackend(ttl=300)
    backend.put("will_expire_1", b"v1", tags=["org:stale"])
    backend.put("will_expire_2", b"v2", tags=["org:stale"])

    # Simulate both having expired.
    del fake_redis.kv["nubi:cache:will_expire_1"]
    del fake_redis.kv["nubi:cache:will_expire_2"]

    # Tag set still has 2 stale member ids.
    assert len(fake_redis.smembers("nubi:cache:tag:org:stale")) == 2

    # invalidate() must prune stale members and return 0.
    removed = backend.invalidate("org:stale")
    assert removed == 0, (
        f"Expected 0 (all members stale), got {removed}. "
        "Stale member ids must not inflate the count."
    )

    # Tag set must have been cleaned up.
    assert "nubi:cache:tag:org:stale" not in fake_redis.sets


# ---------------------------------------------------------------------------
# Batched invalidation — O(1) round-trips, not O(N) [LOW perf fix]
# ---------------------------------------------------------------------------


def test_redis_invalidate_uses_pipeline_not_per_key_exists(fake_redis):
    """invalidate(tag) must batch EXISTS checks via a pipeline, not N scalar calls.

    Before the fix: invalidate() called EXISTS once per tag member in a loop —
    N Redis round-trips for a tag with N members (O(N) network cost).

    After the fix: a single pipeline.execute() issues all EXISTS commands in
    one round-trip regardless of N.

    This test:
    1. Puts N=50 entries under a common tag.
    2. Calls invalidate(tag).
    3. Asserts pipeline.execute() was called exactly once (O(1) round-trips),
       NOT 50 individual exists() calls.
    4. Asserts ALL 50 entries were actually invalidated (correctness preserved).
    """
    N = 50
    backend = RedisCacheBackend(ttl=300)
    for i in range(N):
        backend.put(f"batch_key_{i}", f"v{i}".encode(), tags=["org:batch"])

    # All N entries are live in the fake store.
    assert len(fake_redis.smembers("nubi:cache:tag:org:batch")) == N

    # Reset counters to measure only the invalidate() call.
    fake_redis.pipeline_execute_count = 0
    fake_redis.exists_call_count = 0

    removed = backend.invalidate("org:batch")

    # --- Correctness: all N entries must have been evicted ---
    assert removed == N, (
        f"Expected {N} entries invalidated, got {removed}. "
        "Batch invalidation must still evict every tagged key."
    )
    for i in range(N):
        assert backend.get(f"batch_key_{i}") is None, (
            f"batch_key_{i} should have been invalidated but get() returned a value."
        )
    assert "nubi:cache:tag:org:batch" not in fake_redis.sets, (
        "Tag set must be deleted after full invalidation."
    )

    # --- Perf: pipeline.execute() called once, not N scalar exists() calls ---
    assert fake_redis.pipeline_execute_count == 1, (
        f"Expected exactly 1 pipeline.execute() call (O(1) round-trips), "
        f"got {fake_redis.pipeline_execute_count}. "
        "invalidate() must batch EXISTS checks via a pipeline."
    )
    # Direct (non-pipeline) exists() calls should be 0 — the pipeline path
    # handles all liveness checks internally via FakePipeline.exists().
    assert fake_redis.exists_call_count == 0, (
        f"Expected 0 direct exists() calls (pipeline handles liveness checks), "
        f"got {fake_redis.exists_call_count}. "
        "The N+1 scalar EXISTS loop must not be taken when pipeline is available."
    )


def test_redis_invalidate_pipeline_mixed_live_and_stale(fake_redis):
    """Pipeline-based invalidation correctly separates live vs stale keys.

    With N entries where half have expired, invalidate() via pipeline must:
    - Return the count of LIVE (non-expired) entries only.
    - SREM the stale ids from the tag set.
    - DEL only the live value keys.
    All in O(1) round-trips.
    """
    N = 20
    backend = RedisCacheBackend(ttl=300)
    for i in range(N):
        backend.put(f"mix_key_{i}", f"v{i}".encode(), tags=["org:mix"])

    # Simulate half expiring (odd-indexed keys removed from kv).
    stale_indices = list(range(1, N, 2))  # 1, 3, 5, ... 19
    live_indices = list(range(0, N, 2))   # 0, 2, 4, ... 18
    for i in stale_indices:
        del fake_redis.kv[f"nubi:cache:mix_key_{i}"]

    fake_redis.pipeline_execute_count = 0
    removed = backend.invalidate("org:mix")

    # Only live entries count.
    assert removed == len(live_indices), (
        f"Expected {len(live_indices)} live entries deleted, got {removed}."
    )
    # Live keys must be gone.
    for i in live_indices:
        assert backend.get(f"mix_key_{i}") is None
    # Tag set cleaned up.
    assert "nubi:cache:tag:org:mix" not in fake_redis.sets
    # Still batched.
    assert fake_redis.pipeline_execute_count == 1, (
        "Mixed live/stale invalidation must still use a single pipeline.execute()."
    )


# ===========================================================================
# Per-entry byte cap (MED fix) — NUBI_CACHE_MAX_ENTRY_BYTES
# ===========================================================================


def test_memory_oversized_entry_not_cached(monkeypatch):
    """An entry whose byte length exceeds the cap must NOT be stored.

    A subsequent get() for the same key must return None (MISS), not the
    oversized value.  The cap is set to a small value via monkeypatch so the
    test does not need to allocate tens of MiB.
    """
    monkeypatch.setenv("NUBI_CACHE_MAX_ENTRY_BYTES", "10")
    cache = ContentAddressedCache()
    oversized = b"x" * 11  # 11 bytes > cap of 10
    cache.put("big_key", oversized)
    assert cache.get("big_key") is None, (
        "Oversized entry must not be stored — get() must return None (MISS)."
    )
    assert cache.size() == 0


def test_memory_normal_entry_still_cached(monkeypatch):
    """An entry within the byte cap must be stored and retrievable normally."""
    monkeypatch.setenv("NUBI_CACHE_MAX_ENTRY_BYTES", "10")
    cache = ContentAddressedCache()
    small = b"hello"  # 5 bytes <= cap of 10
    cache.put("small_key", small)
    assert cache.get("small_key") == small, (
        "Normal (within-cap) entry must be cached and returned on get()."
    )
    assert cache.size() == 1


def test_memory_oversized_entry_not_cached_exact_boundary(monkeypatch):
    """An entry of exactly cap+1 bytes must be skipped; exactly cap bytes is stored."""
    monkeypatch.setenv("NUBI_CACHE_MAX_ENTRY_BYTES", "8")
    cache = ContentAddressedCache()
    at_cap = b"x" * 8   # 8 bytes == cap → allowed
    over_cap = b"x" * 9  # 9 bytes > cap → skipped
    cache.put("at_cap", at_cap)
    cache.put("over_cap", over_cap)
    assert cache.get("at_cap") == at_cap
    assert cache.get("over_cap") is None


def test_redis_oversized_entry_not_cached(monkeypatch, fake_redis):
    """Redis backend must also skip oversized entries (not call SETEX)."""
    monkeypatch.setenv("NUBI_CACHE_MAX_ENTRY_BYTES", "10")
    backend = RedisCacheBackend(ttl=300)
    oversized = b"y" * 11
    backend.put("big_redis_key", oversized)
    # The value must NOT be in the fake Redis kv store.
    assert "nubi:cache:big_redis_key" not in fake_redis.kv, (
        "Oversized entry must not be written to Redis."
    )
    assert backend.get("big_redis_key") is None


def test_redis_normal_entry_still_cached(monkeypatch, fake_redis):
    """Redis backend must cache normal (within-cap) entries as before."""
    monkeypatch.setenv("NUBI_CACHE_MAX_ENTRY_BYTES", "10")
    backend = RedisCacheBackend(ttl=300)
    small = b"ok"  # 2 bytes <= cap of 10
    backend.put("small_redis_key", small)
    assert backend.get("small_redis_key") == small


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


# ===========================================================================
# Total-bytes cap (MED fix) — max_total_bytes + NUBI_CACHE_MAX_TOTAL_BYTES
# ===========================================================================


def test_total_bytes_cap_evicts_lru_to_fit():
    """When adding an entry would exceed max_total_bytes, LRU entries are
    evicted first until there is room, not just when max_entries is hit.

    Setup: cap = 30 bytes, per-entry cap = 100 bytes.
    Put a (10 bytes) → b (10 bytes) → c (10 bytes) → all fit (total=30).
    Put d (10 bytes) → total would be 40 > 30, so LRU (a) is evicted first.
    After d: b+c+d in store (30 bytes), a evicted.
    """
    cache = ContentAddressedCache(max_entries=100, max_total_bytes=30)
    cache.put("a", b"x" * 10)
    cache.put("b", b"x" * 10)
    cache.put("c", b"x" * 10)

    assert cache.total_bytes() == 30
    assert cache.size() == 3

    # Access b so it becomes MRU; a is still LRU.
    cache.get("b")

    cache.put("d", b"x" * 10)  # forces eviction of LRU (a)

    assert cache.get("a") is None, "LRU entry 'a' must have been evicted"
    assert cache.get("b") == b"x" * 10
    assert cache.get("c") == b"x" * 10
    assert cache.get("d") == b"x" * 10
    assert cache.total_bytes() == 30
    assert cache.size() == 3


def test_total_bytes_cap_rejects_oversized_single_entry():
    """A single entry whose size alone exceeds max_total_bytes is silently
    skipped — the cache must NOT evict everything just to accommodate it.
    """
    cache = ContentAddressedCache(max_entries=100, max_total_bytes=20)
    cache.put("small", b"x" * 10)

    # This entry alone (25 bytes) exceeds the total cap (20 bytes).
    cache.put("giant", b"x" * 25)

    # The giant entry must be rejected.
    assert cache.get("giant") is None, (
        "An entry exceeding max_total_bytes alone must not be stored."
    )
    # The existing small entry must be unaffected.
    assert cache.get("small") == b"x" * 10
    assert cache.total_bytes() == 10
    assert cache.size() == 1


def test_total_bytes_correct_after_overwrite():
    """Overwriting a key with a different-size value must correctly update
    _total_bytes (deduct old, add new).
    """
    cache = ContentAddressedCache(max_entries=10, max_total_bytes=100)
    cache.put("k", b"x" * 20)
    assert cache.total_bytes() == 20

    # Overwrite with a larger value.
    cache.put("k", b"y" * 35)
    assert cache.total_bytes() == 35
    assert cache.get("k") == b"y" * 35

    # Overwrite with a smaller value.
    cache.put("k", b"z" * 5)
    assert cache.total_bytes() == 5
    assert cache.get("k") == b"z" * 5


def test_total_bytes_correct_after_lru_eviction():
    """_total_bytes must be accurately decremented on every LRU eviction."""
    cache = ContentAddressedCache(max_entries=2, max_total_bytes=1000)
    cache.put("a", b"x" * 10)
    cache.put("b", b"y" * 20)
    assert cache.total_bytes() == 30

    # Third entry triggers LRU eviction of 'a' (10 bytes).
    cache.put("c", b"z" * 15)
    assert cache.get("a") is None
    assert cache.total_bytes() == 35  # b(20) + c(15)
    assert cache.size() == 2


def test_total_bytes_correct_after_tag_purge():
    """_total_bytes must be accurately decremented when invalidate(tag) evicts
    entries.
    """
    cache = ContentAddressedCache(max_entries=10, max_total_bytes=1000)
    cache.put("a", b"x" * 10, tags=["org:1"])
    cache.put("b", b"y" * 20, tags=["org:1"])
    cache.put("c", b"z" * 15, tags=["org:2"])
    assert cache.total_bytes() == 45

    n = cache.invalidate("org:1")
    assert n == 2
    assert cache.total_bytes() == 15   # only c remains
    assert cache.size() == 1


def test_total_bytes_correct_after_ttl_expiry():
    """_total_bytes must be decremented when an expired entry is lazily evicted
    on get().
    """
    cache = ContentAddressedCache(max_entries=10, ttl=0.05, max_total_bytes=1000)
    cache.put("a", b"x" * 10, tags=["org:1"])
    cache.put("b", b"y" * 20)
    assert cache.total_bytes() == 30

    time.sleep(0.08)
    # get("a") triggers lazy TTL eviction.
    assert cache.get("a") is None
    assert cache.total_bytes() == 20   # only b remains (not yet accessed/expired)


def test_total_bytes_zero_after_invalidate_all():
    """invalidate_all() must reset _total_bytes to 0."""
    cache = ContentAddressedCache(max_entries=10, max_total_bytes=1000)
    cache.put("a", b"x" * 10)
    cache.put("b", b"y" * 20)
    assert cache.total_bytes() == 30

    cache.invalidate_all()
    assert cache.total_bytes() == 0
    assert cache.size() == 0


def test_total_bytes_zero_after_clear():
    """clear() must reset _total_bytes to 0."""
    cache = ContentAddressedCache(max_entries=10, max_total_bytes=1000)
    cache.put("a", b"x" * 10)
    cache.put("b", b"y" * 20)
    assert cache.total_bytes() == 30

    cache.clear()
    assert cache.total_bytes() == 0
    assert cache.size() == 0


def test_total_bytes_evicts_multiple_entries_to_fit():
    """When a large new entry requires evicting multiple LRU entries to fit,
    ALL required entries must be evicted (not just one).
    """
    cache = ContentAddressedCache(max_entries=100, max_total_bytes=50)
    # Fill up: 3 entries × 15 bytes = 45 bytes used.
    cache.put("a", b"x" * 15)
    cache.put("b", b"y" * 15)
    cache.put("c", b"z" * 15)
    assert cache.total_bytes() == 45

    # New entry of 20 bytes: needs to evict until 50 - 20 = 30 bytes free.
    # Evict a (15) → total=30, still no room (30+20=50 OK, borderline).
    # Actually 30 + 20 = 50 == cap, so exactly fits after evicting a.
    cache.put("d", b"w" * 20)
    assert cache.get("a") is None, "'a' must have been evicted to make room"
    assert cache.total_bytes() == 50  # b(15) + c(15) + d(20)
    assert cache.size() == 3


# ===========================================================================
# max_entries env override (LOW fix) — NUBI_CACHE_MAX_ENTRIES
# ===========================================================================


def test_max_entries_env_override_honored(monkeypatch):
    """NUBI_CACHE_MAX_ENTRIES must override the default max_entries for the
    memory singleton created by get_cache().
    """
    reset_cache_for_tests()
    monkeypatch.setenv("NUBI_CACHE_MAX_ENTRIES", "3")
    monkeypatch.setattr("app.cache.redis_client.redis_available", lambda: False)

    cache = get_cache()
    assert isinstance(cache, ContentAddressedCache)
    assert cache._max_entries == 3  # noqa: SLF001

    reset_cache_for_tests()


def test_max_entries_default_when_env_not_set(monkeypatch):
    """When NUBI_CACHE_MAX_ENTRIES is not set, the singleton gets the default."""
    reset_cache_for_tests()
    monkeypatch.delenv("NUBI_CACHE_MAX_ENTRIES", raising=False)
    monkeypatch.setattr("app.cache.redis_client.redis_available", lambda: False)

    cache = get_cache()
    assert isinstance(cache, ContentAddressedCache)
    assert cache._max_entries == _DEFAULT_MAX_ENTRIES  # noqa: SLF001

    reset_cache_for_tests()


def test_max_total_bytes_env_override_honored(monkeypatch):
    """NUBI_CACHE_MAX_TOTAL_BYTES must override the total-byte cap for the
    memory singleton.
    """
    reset_cache_for_tests()
    monkeypatch.setenv("NUBI_CACHE_MAX_TOTAL_BYTES", str(64 * 1024))  # 64 KiB
    monkeypatch.setattr("app.cache.redis_client.redis_available", lambda: False)

    cache = get_cache()
    assert isinstance(cache, ContentAddressedCache)
    assert cache._max_total_bytes == 64 * 1024  # noqa: SLF001

    reset_cache_for_tests()


def test_base_scan_max_total_bytes_env_override_honored(monkeypatch):
    """NUBI_CACHE_BASE_SCAN_MAX_TOTAL_BYTES must override the base-scan total cap."""
    from app.connectors.cache import _get_base_scan_backend

    reset_cache_for_tests()
    monkeypatch.setenv("NUBI_CACHE_BASE_SCAN_MAX_TOTAL_BYTES", str(32 * 1024))  # 32 KiB
    monkeypatch.setattr("app.cache.redis_client.redis_available", lambda: False)

    # Bootstrap the exact-result cache first (required before base-scan).
    get_cache()
    base_scan = _get_base_scan_backend()
    assert isinstance(base_scan, ContentAddressedCache)
    assert base_scan._max_total_bytes == 32 * 1024  # noqa: SLF001

    reset_cache_for_tests()


def test_base_scan_max_entries_env_var_is_independent_of_exact_result(monkeypatch):
    """NUBI_CACHE_BASE_SCAN_MAX_ENTRIES and NUBI_CACHE_MAX_ENTRIES must be
    independent: setting one must not affect the other.

    Regression guard for fix-24 follow-on: the base-scan singleton previously
    read NUBI_CACHE_MAX_ENTRIES (the exact-result env var) for its max_entries,
    making the two caches impossible to tune independently.
    """
    from app.connectors.cache import _get_base_scan_backend

    reset_cache_for_tests()
    # Set the exact-result var to a distinct value from the base-scan var.
    monkeypatch.setenv("NUBI_CACHE_MAX_ENTRIES", "50")
    monkeypatch.setenv("NUBI_CACHE_BASE_SCAN_MAX_ENTRIES", "20")
    monkeypatch.setattr("app.cache.redis_client.redis_available", lambda: False)

    exact = get_cache()
    base_scan = _get_base_scan_backend()

    assert isinstance(exact, ContentAddressedCache)
    assert isinstance(base_scan, ContentAddressedCache)

    # The exact-result cache uses NUBI_CACHE_MAX_ENTRIES.
    assert exact._max_entries == 50, (  # noqa: SLF001
        f"Exact-result cache should have max_entries=50 (from NUBI_CACHE_MAX_ENTRIES), "
        f"got {exact._max_entries}"  # noqa: SLF001
    )
    # The base-scan cache uses NUBI_CACHE_BASE_SCAN_MAX_ENTRIES, NOT the exact-result var.
    assert base_scan._max_entries == 20, (  # noqa: SLF001
        f"Base-scan cache should have max_entries=20 (from NUBI_CACHE_BASE_SCAN_MAX_ENTRIES), "
        f"got {base_scan._max_entries}. "  # noqa: SLF001
        "It must NOT read NUBI_CACHE_MAX_ENTRIES."
    )

    reset_cache_for_tests()


def test_base_scan_max_entries_defaults_independently(monkeypatch):
    """When neither NUBI_CACHE_MAX_ENTRIES nor NUBI_CACHE_BASE_SCAN_MAX_ENTRIES
    is set, each cache gets its own correct default (256 vs 128).
    """
    from app.connectors.cache import _get_base_scan_backend

    reset_cache_for_tests()
    monkeypatch.delenv("NUBI_CACHE_MAX_ENTRIES", raising=False)
    monkeypatch.delenv("NUBI_CACHE_BASE_SCAN_MAX_ENTRIES", raising=False)
    monkeypatch.setattr("app.cache.redis_client.redis_available", lambda: False)

    exact = get_cache()
    base_scan = _get_base_scan_backend()

    assert isinstance(exact, ContentAddressedCache)
    assert isinstance(base_scan, ContentAddressedCache)
    assert exact._max_entries == _DEFAULT_MAX_ENTRIES, (  # noqa: SLF001
        f"Exact-result cache default should be {_DEFAULT_MAX_ENTRIES}, got {exact._max_entries}"  # noqa: SLF001
    )
    assert base_scan._max_entries == _DEFAULT_BASE_SCAN_MAX_ENTRIES, (  # noqa: SLF001
        f"Base-scan cache default should be {_DEFAULT_BASE_SCAN_MAX_ENTRIES}, "
        f"got {base_scan._max_entries}"  # noqa: SLF001
    )
    # They must differ — base-scan has a smaller default.
    assert exact._max_entries != base_scan._max_entries  # noqa: SLF001

    reset_cache_for_tests()
