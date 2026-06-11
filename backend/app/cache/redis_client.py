"""Optional Redis client — shared, lazily-created, soft-dependency.

Both the query cache and the rate limiter use a SHARED, cross-process store when
one is configured, and otherwise fall back to per-process in-memory state. This
module is the single place that decides whether a shared store is available.

Contract
--------
``get_redis()`` returns a connected ``redis.Redis`` client when BOTH:
  * the ``redis`` package is importable, AND
  * ``REDIS_URL`` is set in the environment (e.g. ``redis://host:6379/0``),
and the connection check succeeds. Otherwise it returns ``None`` and callers use
their in-process fallback. The client is created once and cached.

Design notes
------------
- We read ``REDIS_URL`` directly from the environment (like the NUBI_RATELIMIT_*
  vars in ratelimit.py) rather than threading it through pydantic settings — the
  store is infra config, not request config, and this keeps it dependency-light.
- ``decode_responses=False``: the query cache stores raw Arrow IPC ``bytes``, so
  the client must return bytes, not str. Callers that want text decode locally.
- A failed connection is treated as "no shared store" (return ``None``) — the app
  must never hard-fail because Redis is down; it degrades to in-process state.
"""

from __future__ import annotations

import logging
import os
import threading
from typing import Any

logger = logging.getLogger("nubi.cache.redis")

_client: Any | None = None
_resolved: bool = False
_lock = threading.Lock()


def _build_client() -> Any | None:
    """Construct + ping a redis client, or return None if unavailable."""
    url = (os.getenv("REDIS_URL") or "").strip()
    if not url:
        return None
    try:
        import redis  # noqa: PLC0415 — soft dependency, may be absent
    except Exception:  # noqa: BLE001 — library not installed
        logger.info("redis: REDIS_URL set but the redis package is not installed")
        return None
    try:
        client = redis.Redis.from_url(
            url,
            decode_responses=False,  # query cache stores raw Arrow IPC bytes
            socket_connect_timeout=2.0,
            socket_timeout=2.0,
        )
        client.ping()
    except Exception as exc:  # noqa: BLE001 — unreachable / auth / etc.
        logger.warning("redis: could not connect to REDIS_URL (%s); using in-process fallback", exc)
        return None
    logger.info("redis: shared store connected (cache + rate limiter will use it)")
    return client


def get_redis() -> Any | None:
    """Return the shared redis client, or ``None`` when no shared store is available.

    Lazily created and cached on first call. Safe to call on every request — it
    returns the cached client (or cached ``None``) after the first resolution.
    """
    global _client, _resolved
    if _resolved:
        return _client
    with _lock:
        if not _resolved:
            _client = _build_client()
            _resolved = True
    return _client


def redis_available() -> bool:
    """True when a shared Redis store is connected and usable."""
    return get_redis() is not None


def reset_redis() -> None:
    """Drop the cached client so the next ``get_redis()`` re-resolves (tests)."""
    global _client, _resolved
    with _lock:
        _client = None
        _resolved = False
