"""Content-addressed in-memory LRU cache for Arrow IPC bytes.

The cache is keyed by the ``cache_key`` field of a ``PhysicalPlan`` (a
SHA-256 hex digest of the canonical plan inputs).  Values are raw Arrow IPC
stream bytes produced by ``arrow_io.table_to_ipc_bytes``.

Design
------
- In-memory only for M1/M2.  The interface is deliberately thin so that a
  future Redis-backed implementation can drop in without touching call sites.
- LRU eviction: the entry that was least recently **accessed** (get or put) is
  evicted when the cache reaches its maximum size.
- Per-entry TTL: each entry carries an ``expires_at`` timestamp; expired entries
  are treated as misses and evicted lazily on access.
- Hit/miss counters: ``get()`` increments ``_hits`` on a live hit and
  ``_misses`` on a miss (including expiry).  ``stats()`` exposes these.
- Thread-safe via a simple ``threading.Lock``.  The GIL makes dict ops atomic
  in CPython, but explicit locking ensures correctness in all implementations.
- Module-level singleton: ``get_cache()`` returns the shared instance.
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from typing import NamedTuple


_DEFAULT_MAX_ENTRIES: int = 256
_DEFAULT_TTL_SECONDS: float = 300.0  # 5 minutes


class _CacheEntry(NamedTuple):
    """Internal storage cell for a single cached result."""

    value: bytes
    expires_at: float  # monotonic clock seconds


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
        self._lock = threading.Lock()
        # Hit/miss counters (protected by _lock).
        self._hits: int = 0
        self._misses: int = 0

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
                # Lazy eviction of expired entry.
                del self._store[key]
                self._misses += 1
                return None
            # Live hit — move to MRU end.
            self._store.move_to_end(key, last=True)
            self._hits += 1
            return entry.value

    def put(self, key: str, value: bytes) -> None:
        """Insert or update *key* → *value* in the cache.

        If the cache is at capacity the least-recently-used entry is evicted
        before inserting the new one.  The TTL clock resets on every ``put``.

        Parameters
        ----------
        key:
            The plan cache key (SHA-256 hex string).
        value:
            Arrow IPC stream bytes to cache.
        """
        expires_at = time.monotonic() + self._ttl
        entry = _CacheEntry(value=value, expires_at=expires_at)
        with self._lock:
            if key in self._store:
                # Refresh position to MRU end and reset TTL.
                self._store.move_to_end(key, last=True)
                self._store[key] = entry
            else:
                if len(self._store) >= self._max_entries:
                    # Evict the least-recently-used entry (left end).
                    self._store.popitem(last=False)
                self._store[key] = entry

    def size(self) -> int:
        """Return the current number of entries in the cache (including expired)."""
        with self._lock:
            return len(self._store)

    def clear(self) -> None:
        """Remove all entries and reset counters.  Useful in tests."""
        with self._lock:
            self._store.clear()
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
        """
        with self._lock:
            hits = self._hits
            misses = self._misses
            entries = len(self._store)
        total = hits + misses
        hit_rate = hits / total if total > 0 else 0.0
        return {
            "entries": entries,
            "hits": hits,
            "misses": misses,
            "hit_rate": hit_rate,
        }


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_cache_instance: ContentAddressedCache | None = None
_cache_lock = threading.Lock()


def get_cache(
    max_entries: int = _DEFAULT_MAX_ENTRIES,
    ttl: float = _DEFAULT_TTL_SECONDS,
) -> ContentAddressedCache:
    """Return the module-level ``ContentAddressedCache`` singleton.

    The singleton is created lazily on first call.  Subsequent calls with
    different arguments are ignored (the first caller wins).

    Parameters
    ----------
    max_entries:
        Maximum entries for the singleton (honoured only on the first call).
    ttl:
        Per-entry TTL in seconds (honoured only on the first call).

    Returns
    -------
    ContentAddressedCache
        The shared cache instance.
    """
    global _cache_instance
    if _cache_instance is None:
        with _cache_lock:
            if _cache_instance is None:
                _cache_instance = ContentAddressedCache(
                    max_entries=max_entries,
                    ttl=ttl,
                )
    return _cache_instance
