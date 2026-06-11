"""In-process request-latency recorder (percentiles) — zero dependencies.

Design
------
A thread-safe, fixed-memory latency store.  Samples (milliseconds) are bucketed
by an opaque string key (the *route class*: ``auth`` / ``query`` / ``flow-run``
/ ``other`` — see :func:`app.middleware.latency`).  Each bucket keeps:

  * a **ring buffer** of the last ``ring_size`` samples (default 1000), used to
    compute percentiles, and
  * a **total-observed counter** that is monotonic and never bounded by the ring
    (so ``count`` reflects all-time traffic, not just the retained window).

Percentiles are computed by copying the ring under a lock, sorting the copy, and
indexing with the *nearest-rank* method (``p`` → element at
``ceil(p/100 * n) - 1``).  This is O(n log n) on the (bounded) ring per
``snapshot()`` call and is intentionally simple — no histogram buckets, no
streaming-quantile sketch.  For a 1000-element ring this is microseconds.

Memory is O(buckets × ring_size).  The number of buckets is bounded
(``max_buckets``, default 64): once the cap is reached, samples for a *new* key
are folded into the shared ``other`` bucket rather than allocating a new ring,
so a pathological caller cannot grow memory without bound.  The synthetic
``all`` bucket aggregates every sample regardless of key.

Per-process scope
-----------------
This recorder is a module-level singleton living in ONE process.  See the
package docstring and ``docs/observability.md`` for the per-worker caveat and
the cross-process-aggregation follow-up.  It mirrors the rate-limiter's
in-process bucket store and the cache's per-worker hit/miss counters.
"""

from __future__ import annotations

import math
import threading
from collections import deque
from typing import Deque, Dict

# The synthetic bucket that aggregates EVERY sample, regardless of route class.
ALL_BUCKET = "all"

# Overflow bucket: once ``max_buckets`` distinct keys exist, further new keys
# fold into this shared bucket instead of allocating more rings.
OTHER_BUCKET = "other"

_DEFAULT_RING_SIZE = 1000
_DEFAULT_MAX_BUCKETS = 64


class _Bucket:
    """Per-key ring of recent samples plus an all-time observed counter."""

    __slots__ = ("ring", "count")

    def __init__(self, ring_size: int) -> None:
        # ``deque(maxlen=…)`` is itself a fixed-size ring: appending past the cap
        # drops the oldest element in O(1).
        self.ring: Deque[float] = deque(maxlen=ring_size)
        # Total samples ever observed for this bucket (NOT bounded by the ring).
        self.count: int = 0

    def add(self, ms: float) -> None:
        self.ring.append(ms)
        self.count += 1


def _percentile(sorted_samples: list[float], pct: float) -> float:
    """Nearest-rank percentile of an already-sorted, non-empty list.

    ``pct`` is in ``[0, 100]``.  Returns the element at rank
    ``ceil(pct/100 * n)`` (1-based), i.e. index ``ceil(pct/100 * n) - 1``.
    p0 maps to the minimum; p100 maps to the maximum.
    """
    n = len(sorted_samples)
    if n == 1:
        return sorted_samples[0]
    rank = math.ceil((pct / 100.0) * n)
    idx = min(max(rank - 1, 0), n - 1)
    return sorted_samples[idx]


class LatencyRecorder:
    """Thread-safe, bounded-memory latency recorder.

    Parameters
    ----------
    ring_size:
        Number of recent samples retained per bucket for percentile math.
    max_buckets:
        Maximum number of distinct keyed buckets (excluding the synthetic
        ``all`` bucket).  New keys beyond the cap fold into ``other``.
    """

    def __init__(
        self,
        ring_size: int = _DEFAULT_RING_SIZE,
        max_buckets: int = _DEFAULT_MAX_BUCKETS,
    ) -> None:
        if ring_size < 1:
            raise ValueError(f"ring_size must be >= 1, got {ring_size}")
        if max_buckets < 1:
            raise ValueError(f"max_buckets must be >= 1, got {max_buckets}")
        self._ring_size = ring_size
        self._max_buckets = max_buckets
        self._buckets: Dict[str, _Bucket] = {}
        self._lock = threading.Lock()

    def record(self, bucket: str, ms: float) -> None:
        """Record a single latency sample (milliseconds) under *bucket*.

        Always also folds the sample into the synthetic ``all`` bucket.  Cheap
        and exception-free for the normal path; never raises on a valid float.
        """
        key = bucket or OTHER_BUCKET
        with self._lock:
            # Always feed the global aggregate.
            self._get_or_fold_locked(ALL_BUCKET).add(ms)
            if key == ALL_BUCKET:
                # Caller used the reserved key explicitly — already counted above.
                return
            self._get_or_fold_locked(key).add(ms)

    def _get_or_fold_locked(self, key: str) -> _Bucket:
        """Return the bucket for *key*, creating it or folding into ``other``.

        Caller holds ``self._lock``.  The ``all`` and ``other`` buckets are
        always allowed; other new keys are only created while under the
        ``max_buckets`` cap (counting keyed buckets, i.e. excluding ``all``).
        """
        existing = self._buckets.get(key)
        if existing is not None:
            return existing
        if key in (ALL_BUCKET, OTHER_BUCKET):
            b = _Bucket(self._ring_size)
            self._buckets[key] = b
            return b
        # Count keyed buckets only (exclude the synthetic ``all`` aggregate).
        keyed = sum(1 for k in self._buckets if k != ALL_BUCKET)
        if keyed >= self._max_buckets:
            # At capacity — fold into the shared overflow bucket.
            other = self._buckets.get(OTHER_BUCKET)
            if other is None:
                other = _Bucket(self._ring_size)
                self._buckets[OTHER_BUCKET] = other
            return other
        b = _Bucket(self._ring_size)
        self._buckets[key] = b
        return b

    def snapshot(self) -> dict[str, dict[str, float | int]]:
        """Return ``{bucket: {count, p50, p95, p99, max, mean}}`` for all buckets.

        ``count`` is the all-time observed total (not the ring size); the
        percentiles/max/mean are computed over the retained ring window.  Empty
        buckets are omitted.  Percentiles are nearest-rank (see
        :func:`_percentile`).
        """
        # Copy ring contents under the lock, then compute outside it.
        with self._lock:
            copies: dict[str, tuple[list[float], int]] = {
                key: (list(b.ring), b.count) for key, b in self._buckets.items()
            }

        out: dict[str, dict[str, float | int]] = {}
        for key, (samples, count) in copies.items():
            if not samples:
                continue
            samples.sort()
            n = len(samples)
            out[key] = {
                "count": count,
                "p50": round(_percentile(samples, 50), 3),
                "p95": round(_percentile(samples, 95), 3),
                "p99": round(_percentile(samples, 99), 3),
                "max": round(samples[-1], 3),
                "mean": round(sum(samples) / n, 3),
            }
        return out

    def reset(self) -> None:
        """Drop all buckets and counters (tests only)."""
        with self._lock:
            self._buckets.clear()


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_recorder: LatencyRecorder | None = None
_recorder_lock = threading.Lock()


def get_recorder() -> LatencyRecorder:
    """Return the process-wide :class:`LatencyRecorder` singleton."""
    global _recorder
    if _recorder is not None:
        return _recorder
    with _recorder_lock:
        if _recorder is None:
            _recorder = LatencyRecorder()
    return _recorder
