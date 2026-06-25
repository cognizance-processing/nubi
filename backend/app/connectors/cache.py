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
- A key→tags reverse index (``dict[str, tuple[str, ...]]``) is maintained in
  parallel so LRU eviction via ``popitem`` can look up a key's tags in O(1)
  instead of scanning the full tag index.  This reduces ``_purge_key_from_tags``
  from O(all-tags) to O(tags-for-key).
- Thread-safe via a simple ``threading.Lock``.

Redis tag-set growth prevention
---------------------------------
When value keys expire (TTL), their ids remain in the tag SET unless
explicitly removed — causing tag sets to grow without bound over time and
making invalidation scans progressively slower.  Two mitigations are applied:

1. **Tag-set TTL**: after every ``SADD``, ``EXPIRE`` is called on the tag SET
   with a lifetime of ``_REDIS_TAG_SET_TTL_MULTIPLIER * ttl`` seconds.  This
   means a tag set that receives no new members is automatically reaped by
   Redis well after all its entries have expired, putting a hard upper bound
   on how long stale member ids can linger.
2. **Stale-member pruning on invalidate**: ``invalidate(tag)`` checks which
   member value-keys actually exist in Redis (``EXISTS``), removes stale
   members from the set via ``SREM``, and only counts/deletes live keys.
   This keeps the count accurate and avoids spurious DELETE calls for
   already-expired entries.

The in-memory backend is unaffected (it maintains an exact tag→keys mapping
via ``_deindex_key`` on every eviction path, so it never accumulates stale
members).
"""

from __future__ import annotations

import logging
import os
import threading
import time
from collections import OrderedDict
from typing import Any, Iterable, NamedTuple

logger = logging.getLogger("nubi.connectors.cache")


_DEFAULT_MAX_ENTRIES: int = 256
_DEFAULT_BASE_SCAN_MAX_ENTRIES: int = 128   # independent default for base-scan cache
_DEFAULT_TTL_SECONDS: float = 300.0  # 5 minutes

# Per-entry byte cap: entries larger than this limit are silently skipped (not
# cached) so a single large result cannot consume a disproportionate share of
# the cache or blow the process memory.  Override with NUBI_CACHE_MAX_ENTRY_BYTES.
_DEFAULT_MAX_ENTRY_BYTES: int = 32 * 1024 * 1024  # 32 MiB

# Total heap byte cap for the exact-result cache (ContentAddressedCache
# singleton) and the base-scan singleton respectively.  When adding an entry
# would push _total_bytes over the limit the LRU entries are evicted first
# until there is room.  A single entry whose size exceeds the total cap is
# skipped entirely (no point evicting everything for one monster entry).
# Override with NUBI_CACHE_MAX_TOTAL_BYTES / NUBI_CACHE_BASE_SCAN_MAX_TOTAL_BYTES.
_DEFAULT_MAX_TOTAL_BYTES: int = 512 * 1024 * 1024   # 512 MiB — exact-result cache
_DEFAULT_BASE_SCAN_MAX_TOTAL_BYTES: int = 256 * 1024 * 1024  # 256 MiB — base-scan cache


def _max_entry_bytes() -> int:
    """Return the per-entry byte cap (env-overridable)."""
    raw = os.environ.get("NUBI_CACHE_MAX_ENTRY_BYTES")
    if raw is not None:
        try:
            return int(raw)
        except ValueError:
            logger.warning(
                "NUBI_CACHE_MAX_ENTRY_BYTES=%r is not a valid integer; using default %d",
                raw,
                _DEFAULT_MAX_ENTRY_BYTES,
            )
    return _DEFAULT_MAX_ENTRY_BYTES


def _max_entries_from_env() -> int:
    """Return the max_entries limit (env-overridable via NUBI_CACHE_MAX_ENTRIES)."""
    raw = os.environ.get("NUBI_CACHE_MAX_ENTRIES")
    if raw is not None:
        try:
            v = int(raw)
            if v >= 1:
                return v
            logger.warning(
                "NUBI_CACHE_MAX_ENTRIES=%r must be >= 1; using default %d",
                raw,
                _DEFAULT_MAX_ENTRIES,
            )
        except ValueError:
            logger.warning(
                "NUBI_CACHE_MAX_ENTRIES=%r is not a valid integer; using default %d",
                raw,
                _DEFAULT_MAX_ENTRIES,
            )
    return _DEFAULT_MAX_ENTRIES


def _base_scan_max_entries_from_env() -> int:
    """Return the base-scan max_entries limit (env-overridable via NUBI_CACHE_BASE_SCAN_MAX_ENTRIES).

    Separate from ``_max_entries_from_env`` so operators can tune the base-scan
    and exact-result caches independently.  Defaults to
    ``_DEFAULT_BASE_SCAN_MAX_ENTRIES`` (128), which is smaller than the
    exact-result default (256) because base-scan entries are typically larger.
    """
    raw = os.environ.get("NUBI_CACHE_BASE_SCAN_MAX_ENTRIES")
    if raw is not None:
        try:
            v = int(raw)
            if v >= 1:
                return v
            logger.warning(
                "NUBI_CACHE_BASE_SCAN_MAX_ENTRIES=%r must be >= 1; using default %d",
                raw,
                _DEFAULT_BASE_SCAN_MAX_ENTRIES,
            )
        except ValueError:
            logger.warning(
                "NUBI_CACHE_BASE_SCAN_MAX_ENTRIES=%r is not a valid integer; using default %d",
                raw,
                _DEFAULT_BASE_SCAN_MAX_ENTRIES,
            )
    return _DEFAULT_BASE_SCAN_MAX_ENTRIES


def _max_total_bytes_from_env(
    env_var: str,
    default: int,
) -> int:
    """Return a total-byte cap read from *env_var* (falling back to *default*)."""
    raw = os.environ.get(env_var)
    if raw is not None:
        try:
            v = int(raw)
            if v >= 1:
                return v
            logger.warning(
                "%s=%r must be >= 1; using default %d",
                env_var,
                raw,
                default,
            )
        except ValueError:
            logger.warning(
                "%s=%r is not a valid integer; using default %d",
                env_var,
                raw,
                default,
            )
    return default

# Redis key namespacing.  Value keys: ``nubi:cache:<key>``.  Tag set keys:
# ``nubi:cache:tag:<tag>`` (a Redis SET holding the member value-keys for that
# tag, used to fan out an invalidate(tag)).
_REDIS_KEY_PREFIX: str = "nubi:cache:"
_REDIS_TAG_PREFIX: str = "nubi:cache:tag:"

# Tag sets are given a TTL of this multiple of the entry TTL so that they are
# automatically reaped by Redis if no new members are added.  A multiplier of
# 2 means the set lives up to 2× the entry TTL after the last SADD, ensuring
# the set is gone well after all its entries have expired.
_REDIS_TAG_SET_TTL_MULTIPLIER: int = 2


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
        Default: 256 (or NUBI_CACHE_MAX_ENTRIES env override).
    ttl:
        Time-to-live in seconds for each entry.  Entries are treated as
        misses (and lazily evicted) after this many seconds from insertion.
        Default: 300 s (5 minutes).
    max_total_bytes:
        Hard cap on the total heap bytes held by the cache across ALL entries.
        When inserting a new entry would exceed this cap, the LRU entries are
        evicted (oldest-first) until there is room or the store is empty.  A
        single entry whose size alone exceeds this cap is silently skipped
        (so one monster entry does not forcibly evict everything).
        Default: 512 MiB (or NUBI_CACHE_MAX_TOTAL_BYTES env override for the
        exact-result singleton; 256 MiB / NUBI_CACHE_BASE_SCAN_MAX_TOTAL_BYTES
        for the base-scan singleton).

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
        max_total_bytes: int = _DEFAULT_MAX_TOTAL_BYTES,
    ) -> None:
        if max_entries < 1:
            raise ValueError(f"max_entries must be >= 1, got {max_entries}")
        if ttl <= 0:
            raise ValueError(f"ttl must be > 0, got {ttl}")
        if max_total_bytes < 1:
            raise ValueError(f"max_total_bytes must be >= 1, got {max_total_bytes}")
        self._max_entries = max_entries
        self._ttl = ttl
        self._max_total_bytes = max_total_bytes
        # OrderedDict preserves insertion order; we move accessed items to the
        # right so the left end is always the LRU entry.
        self._store: OrderedDict[str, _CacheEntry] = OrderedDict()
        # tag → set of keys carrying that tag.  Maintained on put / evict /
        # expire so invalidate(tag) is O(members).
        self._tag_index: dict[str, set[str]] = {}
        # key → tuple of tags (reverse index).  Maintained in parallel with
        # _tag_index so that LRU eviction via popitem() can look up a key's
        # tags in O(1) instead of scanning the full tag index.
        self._key_tags: dict[str, tuple[str, ...]] = {}
        self._lock = threading.Lock()
        # Hit/miss counters (protected by _lock).
        self._hits: int = 0
        self._misses: int = 0
        # Running total of bytes stored across all live entries (protected by
        # _lock).  Deducted on every eviction path (LRU, tag-purge, TTL-expiry,
        # overwrite, invalidate_all).
        self._total_bytes: int = 0

    # ------------------------------------------------------------------
    # Internal helpers (caller must hold _lock)
    # ------------------------------------------------------------------

    def _index_tags(self, key: str, tags: tuple[str, ...]) -> None:
        """Register *key* under each tag in *tags* (caller holds _lock).

        Also updates the reverse index (``_key_tags``) so that LRU eviction can
        look up a key's tags in O(1) without scanning ``_tag_index``.
        """
        self._key_tags[key] = tags
        for tag in tags:
            self._tag_index.setdefault(tag, set()).add(key)

    def _deindex_key(self, key: str, tags: Iterable[str]) -> None:
        """Remove *key* from each of *tags*' member sets (caller holds _lock).

        Also removes the key from the reverse index (``_key_tags``).
        """
        self._key_tags.pop(key, None)
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
            self._total_bytes -= len(entry.value)
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

        Entries larger than the per-entry byte cap (``NUBI_CACHE_MAX_ENTRY_BYTES``,
        default 32 MiB) are silently skipped — they are NOT stored, and a
        subsequent ``get`` for the same key will return ``None``.  This prevents
        a single large result from consuming a disproportionate share of the
        cache or blowing process memory.

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
        cap = _max_entry_bytes()
        if len(value) > cap:
            logger.debug(
                "cache: skipping oversized entry key=%s (%d bytes > cap %d bytes)",
                key,
                len(value),
                cap,
            )
            return
        entry_size = len(value)
        # Skip if a single entry alone exceeds the total cap (evicting
        # everything would still not make room, so don't bother).
        if entry_size > self._max_total_bytes:
            logger.debug(
                "cache: skipping entry key=%s (%d bytes > total cap %d bytes)",
                key,
                entry_size,
                self._max_total_bytes,
            )
            return
        normalized_tags: tuple[str, ...] = tuple(tags) if tags else ()
        expires_at = time.monotonic() + self._ttl
        entry = _CacheEntry(value=value, expires_at=expires_at, tags=normalized_tags)
        with self._lock:
            existing = self._store.get(key)
            if existing is not None:
                # Overwrite: deduct the old entry's bytes, drop old tag
                # associations, then re-index with new tags + bytes.
                self._total_bytes -= len(existing.value)
                self._deindex_key(key, existing.tags)
                self._store[key] = entry
                self._store.move_to_end(key, last=True)
            else:
                # Evict LRU entries until:
                #   (a) the total byte budget has room for the new entry, AND
                #   (b) we are under the max_entries count.
                while self._store and (
                    self._total_bytes + entry_size > self._max_total_bytes
                    or len(self._store) >= self._max_entries
                ):
                    # Evict the least-recently-used entry (left end).
                    lru_key, lru_entry = self._store.popitem(last=False)
                    self._total_bytes -= len(lru_entry.value)
                    self._purge_key_from_tags(lru_key)
                self._store[key] = entry
            self._total_bytes += entry_size
            self._index_tags(key, normalized_tags)

    def _purge_key_from_tags(self, key: str) -> None:
        """Remove *key* from every tag member set (caller holds _lock).

        Used after an LRU ``popitem`` where the evicted entry has already been
        removed from ``_store``.  Uses the reverse index ``_key_tags`` to look
        up only the tags this key carries — O(tags-for-key) instead of the
        previous O(all-tags) full scan.
        """
        tags = self._key_tags.pop(key, ())
        for tag in tags:
            members = self._tag_index.get(tag)
            if members is None:
                continue
            members.discard(key)
            if not members:
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
                self._total_bytes -= len(entry.value)
                # Remove this key from the reverse index.
                self._key_tags.pop(key, None)
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
            self._key_tags.clear()
            self._total_bytes = 0
            return count

    def size(self) -> int:
        """Return the current number of entries in the cache (including expired)."""
        with self._lock:
            return len(self._store)

    def total_bytes(self) -> int:
        """Return the current total byte usage of all live entries."""
        with self._lock:
            return self._total_bytes

    def clear(self) -> None:
        """Remove all entries and reset counters.  Useful in tests."""
        with self._lock:
            self._store.clear()
            self._tag_index.clear()
            self._key_tags.clear()
            self._hits = 0
            self._misses = 0
            self._total_bytes = 0

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
            total_bytes = self._total_bytes
        total = hits + misses
        hit_rate = hits / total if total > 0 else 0.0
        return {
            "entries": entries,
            "hits": hits,
            "misses": misses,
            "hit_rate": hit_rate,
            "tags": tags,
            "total_bytes": total_bytes,
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

        Entries larger than the per-entry byte cap (``NUBI_CACHE_MAX_ENTRY_BYTES``,
        default 32 MiB) are silently skipped — they are NOT stored.

        Tag-set TTL (growth prevention)
        --------------------------------
        After each ``SADD`` we call ``EXPIRE`` on the tag SET with a lifetime
        of ``_REDIS_TAG_SET_TTL_MULTIPLIER * ttl_seconds``.  This resets the
        set's expiry on every write so active tag sets stay alive, while idle
        (fully-expired-member) sets are reaped automatically by Redis within
        ``2 * ttl`` seconds of the last write — preventing unbounded growth.
        """
        cap = _max_entry_bytes()
        if len(value) > cap:
            logger.debug(
                "redis cache: skipping oversized entry key=%s (%d bytes > cap %d bytes)",
                key,
                len(value),
                cap,
            )
            return
        client = self._client()
        if client is None:
            return
        try:
            client.setex(self._value_key(key), self._ttl_seconds, value)
            if tags:
                value_key = self._value_key(key)
                tag_set_ttl = self._ttl_seconds * _REDIS_TAG_SET_TTL_MULTIPLIER
                for tag in tags:
                    tag_key = self._tag_key(tag)
                    client.sadd(tag_key, value_key)
                    # Refresh the tag set's own TTL so it is reaped automatically
                    # once all its value entries have expired.
                    try:
                        client.expire(tag_key, tag_set_ttl)
                    except Exception:  # noqa: BLE001 — EXPIRE is best-effort
                        pass
        except Exception as exc:  # noqa: BLE001 — caching is best-effort
            logger.warning("redis cache: put(%s) failed, skipping cache: %s", key, exc)

    def invalidate(self, tag: str) -> int:
        """Evict every entry carrying *tag*.  Return the number evicted.

        Reads the tag's member set, prunes stale (already-expired) members,
        deletes each live member value key, then deletes the tag set.  Redis
        errors yield 0 (logged at WARNING).

        Stale-member pruning
        --------------------
        When value keys expire (TTL) Redis removes them automatically, but
        their ids remain in the tag SET indefinitely, causing the set to grow
        without bound.  During invalidation we call ``EXISTS`` on all candidate
        keys via a **pipeline** (single round-trip) and ``SREM`` any that have
        already expired before deleting the remaining live ones.  This prevents
        the tag set from accumulating unbounded stale member ids between
        invalidation calls, and ensures the returned count reflects only
        entries that were actually deleted.

        Round-trip complexity
        ---------------------
        The previous implementation called ``EXISTS`` once per tag member (N
        round-trips for a tag with N members).  The batched implementation
        uses a Redis pipeline so the entire EXISTS fan-out is a single
        round-trip regardless of tag set size.  Total round-trips per
        ``invalidate`` call:

        1. ``SMEMBERS tag_key``  — fetch all member ids
        2. ``pipeline(EXISTS key1, EXISTS key2, ...).execute()``  — single
           round-trip for all liveness checks
        3. ``DEL live_key1 live_key2 ... tag_key``  — single bulk delete

        = O(1) round-trips (3 regardless of N).
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
            all_value_keys = [
                m if isinstance(m, (bytes, bytearray)) else str(m).encode()
                for m in members
            ]
            # Batch-check liveness of all member keys in a single pipeline
            # round-trip instead of N individual EXISTS calls.
            key_strs = [
                vk.decode("utf-8", "replace") if isinstance(vk, (bytes, bytearray)) else vk
                for vk in all_value_keys
            ]
            exists_results: list[int] = []
            pipeline = getattr(client, "pipeline", None)
            if callable(pipeline):
                # Preferred path: batch via pipeline (O(1) round-trips).
                pipe = pipeline()
                for ks in key_strs:
                    pipe.exists(ks)
                try:
                    exists_results = pipe.execute()
                except Exception:  # noqa: BLE001 — fall back to scalar path
                    exists_results = []
            if not exists_results:
                # Fallback: scalar EXISTS per key (legacy / no-pipeline client).
                exists_fn = getattr(client, "exists", None)
                if callable(exists_fn):
                    for ks in key_strs:
                        try:
                            exists_results.append(1 if exists_fn(ks) else 0)
                        except Exception:  # noqa: BLE001
                            exists_results.append(0)
                else:
                    # No exists at all — treat all keys as live (safe: spurious
                    # DEL on an absent key is a no-op in Redis).
                    exists_results = [1] * len(all_value_keys)
            live_keys: list[bytes] = []
            stale_keys: list[bytes] = []
            for vk, alive in zip(all_value_keys, exists_results):
                if alive:
                    live_keys.append(vk)
                else:
                    stale_keys.append(vk)
            # Remove stale member ids from the set so it doesn't grow unbounded.
            if stale_keys:
                try:
                    client.srem(tag_key, *stale_keys)
                except Exception:  # noqa: BLE001 — pruning is best-effort
                    pass
            if live_keys:
                client.delete(*live_keys)
            client.delete(tag_key)
            return len(live_keys)
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

# The selected active backend (memory singleton OR RedisCacheBackend, OR an
# EncryptedCache wrapping either).  Resolved lazily on first get_cache() and
# cached thereafter.  Typed as Any to avoid importing EncryptedCache at module
# level (which would create a circular dependency risk); callers rely on duck-
# typing over the get/put/stats/… surface rather than isinstance checks.
_active_backend: "ContentAddressedCache | RedisCacheBackend | Any | None" = None


def get_cache(
    max_entries: int | None = None,
    ttl: float = _DEFAULT_TTL_SECONDS,
) -> "ContentAddressedCache | RedisCacheBackend":
    """Return the active cache backend.

    Selection (lazy + cached):
      * a :class:`RedisCacheBackend` when ``redis_available()`` is true, OR
      * the in-process :class:`ContentAddressedCache` singleton otherwise.

    The in-memory singleton is created lazily on first need; its ``max_entries``
    / ``ttl`` are honoured only on the FIRST call that creates it (later args
    are ignored, preserving the historical singleton contract).  ``ttl`` is
    also passed to the Redis backend so both honour the same TTL.

    ``max_entries`` defaults to the value of ``NUBI_CACHE_MAX_ENTRIES`` (or
    256 if the env var is not set) when not explicitly provided.

    Returns
    -------
    ContentAddressedCache | RedisCacheBackend
        The shared cache instance for this process.
    """
    global _active_backend
    if _active_backend is not None:
        return _active_backend
    resolved_max_entries = max_entries if max_entries is not None else _max_entries_from_env()
    with _cache_lock:
        if _active_backend is None:
            _active_backend = _select_backend(max_entries=resolved_max_entries, ttl=ttl)
    return _active_backend


def _select_backend(
    max_entries: int,
    ttl: float,
) -> ContentAddressedCache | RedisCacheBackend:
    """Pick the Redis backend when a shared store is up, else in-memory.

    If ``cache_encryption_key()`` returns a non-empty key the chosen backend is
    wrapped in an :class:`~app.connectors.cache_encryption.EncryptedCache` so
    every stored entry is AES-256-GCM encrypted at rest.  The wrapping is
    transparent to all callers — the public surface is identical.

    Caller holds ``_cache_lock``.
    """
    try:
        from app.cache.redis_client import redis_available  # noqa: PLC0415

        if redis_available():
            inner = RedisCacheBackend(ttl=ttl)
        else:
            inner = _get_memory_singleton(max_entries=max_entries, ttl=ttl)
    except Exception as exc:  # noqa: BLE001 — never fail selection on infra
        logger.warning("redis cache: availability check failed, using memory: %s", exc)
        inner = _get_memory_singleton(max_entries=max_entries, ttl=ttl)

    # Custody capability 4: transparent query-cache encryption at rest.
    # Lazy import guards against any circular-import risk at module load time.
    #
    # FAIL-CLOSED (Bug 3 fix): when a cache encryption key IS configured but
    # the EncryptedCache wrapper cannot be constructed (bad/short base64 key,
    # import error, etc.), we MUST NOT silently fall back to plaintext — that
    # would defeat the custody guarantee the operator asked for.  Instead we
    # raise a clear RuntimeError naming the misconfig so the process fails to
    # start rather than running with unencrypted cache storage.
    #
    # The "no key configured" path (enc_key is empty / falsy) still returns
    # the plain inner backend — that is correct and intentional.
    enc_key: str = ""
    try:
        from app.lakehouse.custody import cache_encryption_key  # noqa: PLC0415

        enc_key = cache_encryption_key()
    except Exception as exc:  # noqa: BLE001 — import / call failure
        # If we cannot even determine whether a key is set, treat as "no key"
        # (custody module not present → not a custody deployment).
        logger.warning(
            "cache: could not read cache_encryption_key(); treating as no-key: %s",
            exc,
        )
        return inner

    if not enc_key:
        # No encryption key configured — return plain backend (correct path).
        return inner

    # Encryption key IS configured — wrapper must succeed or we fail closed.
    try:
        from app.connectors.cache_encryption import EncryptedCache  # noqa: PLC0415

        logger.info("cache: encryption at rest enabled (AES-256-GCM + per-tenant AAD)")
        return EncryptedCache(inner, enc_key)
    except Exception as exc:
        # Key was set but EncryptedCache could not be initialised (malformed
        # key, wrong length, cryptography library missing, etc.).  Raising here
        # fails the process startup rather than running with a plaintext cache
        # that the operator explicitly opted out of.
        raise RuntimeError(
            f"cache: NUBI_CACHE_ENCRYPTION_KEY is set but the encryption "
            f"wrapper could not be initialised — refusing to start with an "
            f"unencrypted cache.  Fix or remove NUBI_CACHE_ENCRYPTION_KEY.  "
            f"Underlying error: {exc}"
        ) from exc


def _get_memory_singleton(
    max_entries: int = _DEFAULT_MAX_ENTRIES,
    ttl: float = _DEFAULT_TTL_SECONDS,
) -> ContentAddressedCache:
    """Return the in-process ``ContentAddressedCache`` singleton.

    Caller holds ``_cache_lock`` (or accepts the brief race the inner check
    guards against).  Preserved as a distinct singleton so the in-memory cache
    survives even if backend selection later flips.

    The singleton is constructed with the ``NUBI_CACHE_MAX_TOTAL_BYTES`` env
    override for its total-byte cap so the default 512 MiB limit is
    configurable without recompiling.
    """
    global _cache_instance
    if _cache_instance is None:
        max_total_bytes = _max_total_bytes_from_env(
            "NUBI_CACHE_MAX_TOTAL_BYTES",
            _DEFAULT_MAX_TOTAL_BYTES,
        )
        _cache_instance = ContentAddressedCache(
            max_entries=max_entries,
            ttl=ttl,
            max_total_bytes=max_total_bytes,
        )
    return _cache_instance


def reset_cache_for_tests() -> None:
    """Drop the selected backend and the in-memory singleton (tests only)."""
    global _active_backend, _cache_instance, _base_scan_instance
    with _cache_lock:
        _active_backend = None
        _cache_instance = None
        _base_scan_instance = None


# ---------------------------------------------------------------------------
# Base-scan cache (BET 2b) — SEGMENTED base-scan store
# ---------------------------------------------------------------------------
# When a board fires N widget queries over the SAME model + predicate + RLS
# tenant, the per-plan ``cache_key`` will differ for each widget (they select
# different columns / aggregations), but the ``base_scan_key`` (computed by
# ``compute_base_scan_key`` in ``cache_key.py``) will be identical.  We store
# the raw base-scan Arrow IPC bytes here so different widgets within the same
# board reuse the same underlying scan result.
#
# FIX [LOW] Cache segmentation: base-scan entries previously shared the SAME
# 256-entry LRU as exact-result entries (via a ``bscan:`` key prefix), which
# allowed base-scan puts to evict exact-result entries and vice-versa, degrading
# both.  The fix gives base-scan its OWN bounded ``ContentAddressedCache``
# instance (separate from the exact-result singleton).  This means:
#   * In-memory backend: two independent LRUs; no cross-segment eviction.
#   * Redis backend: namespace isolation was already provided by the ``bscan:``
#     prefix and Redis has no fixed-size LRU by default; we use the shared Redis
#     backend there (same as before) since Redis does not have the eviction
#     contention problem of a fixed-size LRU.
#
# SECURITY: ``compute_base_scan_key`` incorporates the full RLS policies dict
# so tenants NEVER share a base-scan entry.  ``get_base_scan()`` / ``put_base_scan()``
# are thin wrappers; the underlying isolation guarantees are in the key computation.

_BASE_SCAN_KEY_PREFIX: str = "bscan:"

# Separate in-memory singleton for base-scan entries.  Only used when the active
# backend is NOT Redis (in-process LRU contention is the problem to avoid).
_base_scan_instance: ContentAddressedCache | None = None


def _get_base_scan_backend() -> ContentAddressedCache | RedisCacheBackend:
    """Return the store to use for base-scan entries.

    * Redis: use the shared ``RedisCacheBackend`` (namespaced keys + no fixed
      LRU capacity, so no eviction contention with exact-result entries).
    * In-memory: return a SEPARATE ``ContentAddressedCache`` singleton so
      base-scan entries live in their own LRU and cannot evict exact-result
      entries (or vice-versa).

    The base-scan singleton respects ``NUBI_CACHE_BASE_SCAN_MAX_ENTRIES``
    (default 128) and ``NUBI_CACHE_BASE_SCAN_MAX_TOTAL_BYTES`` (default 256 MiB),
    both independently tunable from the exact-result cache's
    ``NUBI_CACHE_MAX_ENTRIES`` / ``NUBI_CACHE_MAX_TOTAL_BYTES``.
    """
    active = get_cache()
    if isinstance(active, RedisCacheBackend):
        # Redis: the ``bscan:`` prefix provides namespace isolation; no separate
        # store needed.
        return active
    # In-memory: use (or lazily create) the dedicated base-scan singleton.
    global _base_scan_instance
    if _base_scan_instance is not None:
        return _base_scan_instance
    with _cache_lock:
        if _base_scan_instance is None:
            # Base-scan entries are typically larger (full-table scans) so they
            # get their own, separately-tunable env vars.
            base_scan_max_entries = _base_scan_max_entries_from_env()
            # Use a smaller per-instance default than the exact-result cache.
            # The base-scan entries are large, so 128 entries + 256 MiB is the
            # right balance.  Override with NUBI_CACHE_BASE_SCAN_MAX_ENTRIES /
            # NUBI_CACHE_BASE_SCAN_MAX_TOTAL_BYTES.
            base_scan_max_total_bytes = _max_total_bytes_from_env(
                "NUBI_CACHE_BASE_SCAN_MAX_TOTAL_BYTES",
                _DEFAULT_BASE_SCAN_MAX_TOTAL_BYTES,
            )
            _base_scan_instance = ContentAddressedCache(
                max_entries=base_scan_max_entries,
                ttl=_DEFAULT_TTL_SECONDS,
                max_total_bytes=base_scan_max_total_bytes,
            )
    return _base_scan_instance


def _base_scan_store_key(base_scan_key: str) -> str:
    """Return the namespaced store key for a base-scan entry.

    The ``bscan:`` prefix is retained for Redis (where both base-scan and
    exact-result entries coexist in the same Redis keyspace) and for any
    future shared backend where key-space isolation matters.  For the
    in-memory backend the separate singleton already provides physical
    isolation, but keeping the prefix is harmless and aids debugging.
    """
    return f"{_BASE_SCAN_KEY_PREFIX}{base_scan_key}"


def get_base_scan(base_scan_key: str) -> bytes | None:
    """Look up a cached base-scan result by *base_scan_key*.

    Returns the cached Arrow IPC bytes on a HIT, ``None`` on a MISS.

    Parameters
    ----------
    base_scan_key:
        The key returned by ``compute_base_scan_key``.  ``None`` / empty input
        is safe (treated as a MISS) so callers can pass the result of
        ``compute_base_scan_key`` directly without a None-guard.
    """
    if not base_scan_key:
        return None
    store = _get_base_scan_backend()
    return store.get(_base_scan_store_key(base_scan_key))


def put_base_scan(
    base_scan_key: str,
    value: bytes,
    tags: list[str] | None = None,
) -> None:
    """Store *value* (Arrow IPC bytes) in the base-scan store.

    No-ops when *base_scan_key* is falsy so callers can pass
    ``compute_base_scan_key(...)`` (which may return ``None``) directly.

    Parameters
    ----------
    base_scan_key:
        The key returned by ``compute_base_scan_key``.
    value:
        Arrow IPC stream bytes to cache.
    tags:
        Optional invalidation tags forwarded to the backend ``put`` (e.g.
        ``["org:<id>", "datastore:<id>"]``) so that per-tenant or per-datastore
        invalidation also evicts base-scan entries.
    """
    if not base_scan_key:
        return
    store = _get_base_scan_backend()
    store.put(_base_scan_store_key(base_scan_key), value, tags=tags)


def invalidate_base_scan_tag(tag: str) -> int:
    """Invalidate all base-scan entries carrying *tag*.

    Thin wrapper that forwards to the base-scan backend's ``invalidate``
    method.  Use this instead of ``get_cache().invalidate(tag)`` when you
    want to evict base-scan entries: on the in-memory backend the base-scan
    store is now a SEPARATE singleton from the exact-result cache, so calling
    the latter's ``invalidate`` would not reach base-scan entries.

    Returns the number of entries evicted (0 on a MISS or error).
    """
    store = _get_base_scan_backend()
    return store.invalidate(tag)
