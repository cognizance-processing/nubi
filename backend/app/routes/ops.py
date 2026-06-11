"""Operational stats endpoint (``/ops/stats``) — in-process observability.

Endpoints
---------
GET /ops/stats   (first-party auth)
    A single JSON snapshot of this worker's runtime health::

        {
          "latency":     { "<bucket>": {count, p50, p95, p99, max, mean}, ... },
          "cache":       { entries, hits, misses, hit_rate, ..., "backend": "memory"|"redis" },
          "uptime_s":    <float seconds since this process started>,
          "version":     "<app version>",
          "rate_limits": { auth_rpm, query_rpm, flowrun_rpm, enabled, burst_factor }
        }

GET /ops/health  (public, lightweight)
    ``{"status": "ok", "uptime_s": <float>}`` — a dependency-free liveness ping.
    NOTE: the canonical liveness+DB probe is ``GET /health`` (defined in
    ``main.py``); this is a minimal, DB-free sibling for the ops surface.

Path choice
-----------
The ``/metrics`` path is ALREADY the semantic-metrics layer (``routes/metrics``),
so this observability surface lives under ``/ops/*`` to avoid colliding.

Per-process scope
-----------------
``latency`` and ``cache`` hits/misses are this WORKER's numbers only.  Nubi runs
``uvicorn --workers N`` across multiple Fly machines, so a load-balanced
``/ops/stats`` call samples whichever worker served it.  Cross-process roll-up is
a documented follow-up (see ``docs/observability.md``), consistent with the
rate-limiter and cache per-worker stories.

Auth
----
``GET /ops/stats`` requires a first-party Bearer access token via
``current_user`` — the same gate used by ``/cache/stats``.  ``current_user``
only decodes first-party HS256 access tokens, so host-signed embed JWTs
(RS256/ES256) are rejected with 401; unauthenticated requests are 401 too.
"""

from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, Depends

from app.auth.deps import current_user
from app.connectors.cache import (
    ContentAddressedCache,
    RedisCacheBackend,
    get_cache,
)
from app.middleware.ratelimit import _cfg as _ratelimit_cfg
from app.observability.latency import get_recorder

router = APIRouter(prefix="/ops", tags=["ops"])

# Captured once at import time (≈ process start) so uptime is measured from when
# this worker came up.  perf_counter is monotonic; we pair it with time.time()
# only for the wall-clock-agnostic delta below.
_PROCESS_START = time.monotonic()


def _backend_name(cache: Any) -> str:
    """Return ``"redis"`` or ``"memory"`` for the active cache backend."""
    if isinstance(cache, RedisCacheBackend):
        return "redis"
    if isinstance(cache, ContentAddressedCache):
        return "memory"
    return type(cache).__name__.lower()


def _uptime_s() -> float:
    """Seconds since this process captured ``_PROCESS_START``."""
    return round(time.monotonic() - _PROCESS_START, 3)


def _rate_limits() -> dict[str, Any]:
    """Read-only view of the rate-limiter's effective caps (per-worker).

    Reads ``app.middleware.ratelimit._cfg`` (lazily loaded on first access).
    The rpm values already reflect the per-worker division applied by the
    limiter (see ratelimit's module docstring).
    """
    return {
        "auth_rpm": _ratelimit_cfg.auth_rpm,
        "query_rpm": _ratelimit_cfg.query_rpm,
        "flowrun_rpm": _ratelimit_cfg.flowrun_rpm,
        "burst_factor": _ratelimit_cfg.burst_factor,
        "enabled": _ratelimit_cfg.enabled,
    }


@router.get("/stats")
async def ops_stats(
    _user: dict[str, Any] = Depends(current_user),
) -> dict[str, Any]:
    """Return this worker's latency percentiles, cache stats, and rate limits."""
    cache = get_cache()
    cache_stats = {**cache.stats(), "backend": _backend_name(cache)}
    return {
        "latency": get_recorder().snapshot(),
        "cache": cache_stats,
        "uptime_s": _uptime_s(),
        "version": "0.1.0",
        "rate_limits": _rate_limits(),
    }


@router.get("/health")
async def ops_health() -> dict[str, Any]:
    """Lightweight, DB-free liveness ping (public). See also GET /health."""
    return {"status": "ok", "uptime_s": _uptime_s()}
