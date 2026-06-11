"""Pluggable content-addressed cache for Arrow IPC bytes.

The cache is keyed by the ``cache_key`` field of a ``PhysicalPlan`` (a
SHA-256 hex digest of the canonical plan inputs).  Values are raw Arrow IPC
stream bytes produced by ``arrow_io.table_to_ipc_bytes``.

Backends
--------
Two interchangeable backends implement the SAME thin public surface
(``get`` / ``put`` / ``size`` / ``clear`` / ``stats`` / ``invalidate`` /
``invalidate_all``) so call sites are backend-agnostic:

- :class:`ContentAddressedCache` — the in-process LRU + TTL store (default;
  used when no shared Redis store is configured).
- :class:`RedisCacheBackend` — a cross-process store backed by the shared
  Redis client from ``app.cache.redis_client`` (used automatically when
  ``redis_available()`` is true).

``get_cache()`` lazily selects and caches the appropriate backend:
``RedisCacheBackend`` when a Redis store is connected, otherwise the
in-process ``ContentAddressedCache`` singleton.  The selection is cached so
repeated calls are cheap; ``reset_cache_for_tests()`` drops it.

Tag-based invalidation
----------------------
``put(key, value, tags=...)`` may attach a list of opaque tag strings to an
entry.  ``invalidate(tag)`` evicts every entry carrying that tag (returning the
count); ``invalidate_all()`` clears the whole cache.  This lets an operator
invalidate, e.g., one tenant's cached results (``tag="org:<id>"``) without
touching others.  Untagged puts (``tags=None``) behave exactly as before, so
existing call sites are unaffected.

Design (in-memory backend)
--------------------------
- LRU eviction: the entry least recently **accessed** (get or put) is evicted
  when the cache reaches its maximum size.
- Per-entry TTL: each entry carries an ``expires_at`` timestamp; expired
  entries are treated as misses and evicted lazily on access.
- Hit/miss counters: ``get()`` increments ``_hits`` on a live hit and
  ``_misses`` on a miss (including expiry).  ``stats()`` exposes these.
- A tag→keys index (``dict[str, set[str]]``) is maintained on put / evict /
  expire so ``invalidate(tag)`` is O(members of that tag).
- Thread-safe via a simple ``threading.Lock``.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import OrderedDict
from typing import Iterable, NamedTuple

logger = logging.getLogger("nubi.connectors.cache")


_DEFAULT_MAX_ENTRIES: int = 256
_DEFAULT_TTL_SECONDS: float = 300.0  # 5 minutes

# Redis key namespacing.  Value keys: ``nubi:cache:<key>``.  Tag set keys:
# ``nubi:cache:tag:<tag>`` (a Redis SET holding the member value-keys for that
# tag, used to fan out an invalidate(tag)).
_REDIS_KEY_PREFIX: str = "nubi:cache:"
_REDIS_TAG_PREFIX: str = "nubi:cache:tag:"


class _CacheEntry(NamedTuple):
    """Internal storage cell for a single cached result."""

    value: bytes
    expires_at: float  # monotonic clock seconds
    tags: tuple[str, ...]  # opaque tag strings attached at put time


class ContentAddressedCache:
    """In-memory LRU cache keyed by Arrow plan cache keys.

    Parameters
    ----------
    max_entries:
        Maximum number of entries before LRU eviction kicks in.
        Default: 256.
    ttl:
        Time-to-live in seconds for each entry.  Entries are treated as
        misses (and lazily evicted) after this many seconds from insertion.
        Default: 300 s (5 minutes).

    Notes
    -----
    Values are Arrow IPC stream bytes (``bytes``).  The cache is deliberately
    type-agnostic (stores ``bytes``) so that the interface survives a switch to
    a Redis backend that serialises values as byte strings.
    """

    def __init__(
        self,
        max_entries: int = _DEFAULT_MAX_ENTRIES,
        ttl: float = _DEFAULT_TTL_SECONDS,
    ) -> None:
        if max_entries < 1:
            raise ValueError(f"max_entries must be >= 1, got {max_entries}")
        if ttl <= 0:
            raise ValueError(f"ttl must be > 0, got {ttl}")
        self._max_entries = max_entries
        self._ttl = ttl
        # OrderedDict preserves insertion order; we move accessed items to the
        # right so the left end is always the LRU entry.
        self._store: OrderedDict[str, _CacheEntry] = OrderedDict()
        # tag → set of keys carrying that tag.  Maintained on put / evict /
        # expire so invalidate(tag) is O(members).
        self._tag_index: dict[str, set[str]] = {}
        self._lock = threading.Lock()
        # Hit/miss counters (protected by _lock).
        self._hits: int = 0
        self._misses: int = 0

    # ------------------------------------------------------------------
    # Internal helpers (caller must hold _lock)
    # ------------------------------------------------------------------

    def _index_tags(self, key: str, tags: Iterable[str]) -> None:
        """Register *key* under each tag in *tags* (caller holds _lock)."""
        for tag in tags:
            self._tag_index.setdefault(tag, set()).add(key)

    def _deindex_key(self, key: str, tags: Iterable[str]) -> None:
        """Remove *key* from each of *tags*' member sets (caller holds _lock)."""
        for tag in tags:
            members = self._tag_index.get(tag)
            if members is None:
                continue
            members.discard(key)
            if not members:
                self._tag_index.pop(tag, None)

    def _evict_key(self, key: str) -> None:
        """Remove *key* from the store and the tag index (caller holds _lock)."""
        entry = self._store.pop(key, None)
        if entry is not None:
            self._deindex_key(key, entry.tags)

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def get(self, key: str) -> bytes | None:
        """Return the cached bytes for *key*, or ``None`` on a miss/expiry.

        A cache hit moves *key* to the most-recently-used position and
        increments the hit counter.  A miss (absent or expired) increments the
        miss counter and, if the entry has expired, removes it from the store.

        Parameters
        ----------
        key:
            The plan cache key (SHA-256 hex string).

        Returns
        -------
        bytes | None
            The cached Arrow IPC bytes, or ``None`` if *key* is not present or
            has expired.
        """
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                self._misses += 1
                return None
            # Check expiry.
            if time.monotonic() >= entry.expires_at:
                # Lazy eviction of expired entry (also drops it from tag index).
                self._evict_key(key)
                self._misses += 1
                return None
            # Live hit — move to MRU end.
            self._store.move_to_end(key, last=True)
            self._hits += 1
            return entry.value

    def put(self, key: str, value: bytes, tags: list[str] | None = None) -> None:
        """Insert or update *key* → *value* in the cache.

        If the cache is at capacity the least-recently-used entry is evicted
        before inserting the new one.  The TTL clock resets on every ``put``.

        Parameters
        ----------
        key:
            The plan cache key (SHA-256 hex string).
        value:
            Arrow IPC stream bytes to cache.
        tags:
            Optional list of opaque tag strings to attach to this entry so it
            can later be bulk-invalidated via :meth:`invalidate`.  ``None``
            (the default) attaches no tags — existing call sites are unaffected.
        """
        normalized_tags: tuple[str, ...] = tuple(tags) if tags else ()
        expires_at = time.monotonic() + self._ttl
        entry = _CacheEntry(value=value, expires_at=expires_at, tags=normalized_tags)
        with self._lock:
            existing = self._store.get(key)
            if existing is not None:
                # Drop the old tag associations before re-indexing (tags may
                # have changed across puts of the same key).
                self._deindex_key(key, existing.tags)
                self._store[key] = entry
                self._store.move_to_end(key, last=True)
            else:
                if len(self._store) >= self._max_entries:
                    # Evict the least-recently-used entry (left end) and its
                    # tag associations.
                    lru_key, _ = self._store.popitem(last=False)
                    # popitem already removed it from _store; clean tag index.
                    # (We can't read the popped entry's tags after popitem, so
                    # re-pop via a peek-free path: reconstruct via _tag_index.)
                    self._purge_key_from_tags(lru_key)
                self._store[key] = entry
            self._index_tags(key, normalized_tags)

    def _purge_key_from_tags(self, key: str) -> None:
        """Remove *key* from every tag member set (caller holds _lock).

        Used after an LRU ``popitem`` where the evicted entry's tags are no
        longer readable; we scan the (small) tag index instead.
        """
        empty: list[str] = []
        for tag, members in self._tag_index.items():
            members.discard(key)
            if not members:
                empty.append(tag)
        for tag in empty:
            self._tag_index.pop(tag, None)

    def invalidate(self, tag: str) -> int:
        """Evict every entry carrying *tag*.  Return the number evicted.

        O(members of *tag*).  Unknown tags evict nothing and return 0.
        """
        with self._lock:
            members = self._tag_index.pop(tag, None)
            if not members:
                return 0
            count = 0
            for key in list(members):
                entry = self._store.pop(key, None)
                if entry is None:
                    continue
                count += 1
                # Remove this key from any OTHER tags it also carried.
                for other in entry.tags:
                    if other == tag:
                        continue
                    others = self._tag_index.get(other)
                    if others is not None:
                        others.discard(key)
                        if not others:
                            self._tag_index.pop(other, None)
            return count

    def invalidate_all(self) -> int:
        """Clear the whole cache.  Return the number of entries removed."""
        with self._lock:
            count = len(self._store)
            self._store.clear()
            self._tag_index.clear()
            return count

    def size(self) -> int:
        """Return the current number of entries in the cache (including expired)."""
        with self._lock:
            return len(self._store)

    def clear(self) -> None:
        """Remove all entries and reset counters.  Useful in tests."""
        with self._lock:
            self._store.clear()
            self._tag_index.clear()
            self._hits = 0
            self._misses = 0

    def stats(self) -> dict:
        """Return cache statistics.

        Returns
        -------
        dict
            A dict with the following keys:

            ``entries``
                Current number of entries in the store (may include expired
                entries not yet lazily evicted).
            ``hits``
                Cumulative number of successful cache hits since the cache was
                created or last cleared.
            ``misses``
                Cumulative number of cache misses (absent + expired) since
                creation or last clear.
            ``hit_rate``
                ``hits / (hits + misses)`` as a float in ``[0.0, 1.0]``, or
                ``0.0`` when no requests have been made yet.
            ``tags``
                Number of distinct tags currently indexed.
        """
        with self._lock:
            hits = self._hits
            misses = self._misses
            entries = len(self._store)
            tags = len(self._tag_index)
        total = hits + misses
        hit_rate = hits / total if total > 0 else 0.0
        return {
            "entries": entries,
            "hits": hits,
            "misses": misses,
            "hit_rate": hit_rate,
            "tags": tags,
        }


class RedisCacheBackend:
    """Cross-process cache backend over the shared Redis client.

    Implements the same public surface as :class:`ContentAddressedCache`
    (``get`` / ``put`` / ``size`` / ``clear`` / ``stats`` / ``invalidate`` /
    ``invalidate_all``) so call sites are backend-agnostic.

    Storage model
    -------------
    - Value keys: ``nubi:cache:<key>`` → raw Arrow IPC bytes, written with
      ``SETEX`` so each entry expires after ``ttl`` seconds (matching the
      in-memory backend's TTL).
    - Tag sets: ``nubi:cache:tag:<tag>`` is a Redis SET of the member value
      *keys* (the un-namespaced cache keys).  ``put`` SADDs the key into each
      tag's set; ``invalidate(tag)`` reads the set, DELs every member value
      key, then DELs the set itself.

    Resilience
    ----------
    Every Redis operation is wrapped in try/except.  On ANY Redis error the
    backend degrades to a *miss* (for ``get``) or a *no-op* (for ``put`` /
    invalidation) and logs at WARNING — a Redis outage NEVER crashes a request.

    Stats caveat (documented)
    -------------------------
    ``hits`` / ``misses`` are tracked **in-process per worker** (Redis has no
    cheap per-key hit counter), so they reflect only this worker's traffic.
    ``entries`` is a best-effort count of live value keys obtained by scanning
    the ``nubi:cache:*`` namespace (excluding tag sets); on any scan error it
    falls back to ``-1`` ("unknown").
    """

    def __init__(self, ttl: float = _DEFAULT_TTL_SECONDS) -> None:
        # Redis SETEX takes an integer number of seconds; round up so a
        # sub-second TTL still yields at least 1s of life.
        self._ttl_seconds = max(1, int(round(ttl)))
        self._lock = threading.Lock()
        self._hits: int = 0
        self._misses: int = 0

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _client():
        """Return the shared redis client, or ``None`` when unavailable."""
        from app.cache.redis_client import get_redis  # noqa: PLC0415

        return get_redis()

    @staticmethod
    def _value_key(key: str) -> str:
        return f"{_REDIS_KEY_PREFIX}{key}"

    @staticmethod
    def _tag_key(tag: str) -> str:
        return f"{_REDIS_TAG_PREFIX}{tag}"

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def get(self, key: str) -> bytes | None:
        """Return cached bytes for *key*, or ``None`` on miss / Redis error."""
        client = self._client()
        if client is None:
            with self._lock:
                self._misses += 1
            return None
        try:
            value = client.get(self._value_key(key))
        except Exception as exc:  # noqa: BLE001 — degrade to a miss
            logger.warning("redis cache: get(%s) failed, treating as miss: %s", key, exc)
            with self._lock:
                self._misses += 1
            return None
        with self._lock:
            if value is None:
                self._misses += 1
            else:
                self._hits += 1
        return value

    def put(self, key: str, value: bytes, tags: list[str] | None = None) -> None:
        """Store *key* → *value* with TTL, and index it under each tag.

        A Redis failure is a no-op (logged at WARNING) — the request proceeds
        uncached rather than erroring.
        """
        client = self._client()
        if client is None:
            return
        try:
            client.setex(self._value_key(key), self._ttl_seconds, value)
            if tags:
                value_key = self._value_key(key)
                for tag in tags:
                    client.sadd(self._tag_key(tag), value_key)
        except Exception as exc:  # noqa: BLE001 — caching is best-effort
            logger.warning("redis cache: put(%s) failed, skipping cache: %s", key, exc)

    def invalidate(self, tag: str) -> int:
        """Evict every entry carrying *tag*.  Return the number evicted.

        Reads the tag's member set, deletes each member value key, then deletes
        the tag set.  Redis errors yield 0 (logged at WARNING).
        """
        client = self._client()
        if client is None:
            return 0
        try:
            tag_key = self._tag_key(tag)
            members = client.smembers(tag_key)
            if not members:
                client.delete(tag_key)
                return 0
            # smembers returns bytes (decode_responses=False); they are the
            # already-namespaced value keys we wrote in put().
            value_keys = [
                m if isinstance(m, (bytes, bytearray)) else str(m).encode()
                for m in members
            ]
            client.delete(*value_keys)
            client.delete(tag_key)
            return len(value_keys)
        except Exception as exc:  # noqa: BLE001 — degrade to no-op
            logger.warning("redis cache: invalidate(%s) failed: %s", tag, exc)
            return 0

    def invalidate_all(self) -> int:
        """Delete every ``nubi:cache:*`` key (values + tag sets).

        Returns the number of keys deleted.  Best-effort; Redis errors yield 0.
        """
        client = self._client()
        if client is None:
            return 0
        try:
            keys = list(self._scan(client, f"{_REDIS_KEY_PREFIX}*"))
            if not keys:
                return 0
            client.delete(*keys)
            # Count only value keys (exclude tag sets) for parity with the
            # in-memory backend, which counts entries.
            return sum(
                1
                for k in keys
                if not _bytes_to_str(k).startswith(_REDIS_TAG_PREFIX)
            )
        except Exception as exc:  # noqa: BLE001 — degrade to no-op
            logger.warning("redis cache: invalidate_all failed: %s", exc)
            return 0

    def size(self) -> int:
        """Best-effort count of live value keys (excludes tag sets)."""
        client = self._client()
        if client is None:
            return 0
        try:
            return sum(
                1
                for k in self._scan(client, f"{_REDIS_KEY_PREFIX}*")
                if not _bytes_to_str(k).startswith(_REDIS_TAG_PREFIX)
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("redis cache: size() failed: %s", exc)
            return -1

    def clear(self) -> None:
        """Drop all cache keys and reset in-process counters."""
        self.invalidate_all()
        with self._lock:
            self._hits = 0
            self._misses = 0

    def stats(self) -> dict:
        """Return best-effort statistics (see class docstring for caveats)."""
        entries = self.size()
        with self._lock:
            hits = self._hits
            misses = self._misses
        total = hits + misses
        hit_rate = hits / total if total > 0 else 0.0
        return {
            "entries": entries,
            "hits": hits,
            "misses": misses,
            "hit_rate": hit_rate,
            # hits/misses are per-worker, in-process counters (see docstring).
            "stats_scope": "per_worker",
        }

    @staticmethod
    def _scan(client, match: str):
        """Yield keys matching *match*, preferring SCAN, falling back to KEYS.

        ``scan_iter`` is the non-blocking cursor walk; the in-process fake redis
        used in tests may only expose ``keys``, so we fall back to that.
        """
        scan_iter = getattr(client, "scan_iter", None)
        if callable(scan_iter):
            yield from scan_iter(match=match)
            return
        keys_fn = getattr(client, "keys", None)
        if callable(keys_fn):
            yield from keys_fn(match)
            return
        return


def _bytes_to_str(value) -> str:
    """Decode a redis key (bytes or str) to str for prefix comparisons."""
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8", "replace")
    return str(value)


# ---------------------------------------------------------------------------
# Backend selection — module-level singleton
# ---------------------------------------------------------------------------

_cache_instance: ContentAddressedCache | None = None
_cache_lock = threading.Lock()

# The selected active backend (memory singleton OR a RedisCacheBackend),
# resolved lazily on first get_cache() and cached thereafter.
_active_backend: ContentAddressedCache | RedisCacheBackend | None = None


def get_cache(
    max_entries: int = _DEFAULT_MAX_ENTRIES,
    ttl: float = _DEFAULT_TTL_SECONDS,
) -> ContentAddressedCache | RedisCacheBackend:
    """Return the active cache backend.

    Selection (lazy + cached):
      * a :class:`RedisCacheBackend` when ``redis_available()`` is true, OR
      * the in-process :class:`ContentAddressedCache` singleton otherwise.

    The in-memory singleton is created lazily on first need; its ``max_entries``
    / ``ttl`` are honoured only on the FIRST call that creates it (later args
    are ignored, preserving the historical singleton contract).  ``ttl`` is
    also passed to the Redis backend so both honour the same TTL.

    Returns
    -------
    ContentAddressedCache | RedisCacheBackend
        The shared cache instance for this process.
    """
    global _active_backend
    if _active_backend is not None:
        return _active_backend
    with _cache_lock:
        if _active_backend is None:
            _active_backend = _select_backend(max_entries=max_entries, ttl=ttl)
    return _active_backend


def _select_backend(
    max_entries: int,
    ttl: float,
) -> ContentAddressedCache | RedisCacheBackend:
    """Pick the Redis backend when a shared store is up, else in-memory.

    Caller holds ``_cache_lock``.
    """
    try:
        from app.cache.redis_client import redis_available  # noqa: PLC0415

        if redis_available():
            return RedisCacheBackend(ttl=ttl)
    except Exception as exc:  # noqa: BLE001 — never fail selection on infra
        logger.warning("redis cache: availability check failed, using memory: %s", exc)
    return _get_memory_singleton(max_entries=max_entries, ttl=ttl)


def _get_memory_singleton(
    max_entries: int = _DEFAULT_MAX_ENTRIES,
    ttl: float = _DEFAULT_TTL_SECONDS,
) -> ContentAddressedCache:
    """Return the in-process ``ContentAddressedCache`` singleton.

    Caller holds ``_cache_lock`` (or accepts the brief race the inner check
    guards against).  Preserved as a distinct singleton so the in-memory cache
    survives even if backend selection later flips.
    """
    global _cache_instance
    if _cache_instance is None:
        _cache_instance = ContentAddressedCache(max_entries=max_entries, ttl=ttl)
    return _cache_instance


def reset_cache_for_tests() -> None:
    """Drop the selected backend and the in-memory singleton (tests only)."""
    global _active_backend, _cache_instance
    with _cache_lock:
        _active_backend = None
        _cache_instance = None
