"""Shared cache/coordination primitives (Redis client helper).

This package holds the OPTIONAL Redis client used by the query cache
(app/connectors/cache.py) and the rate limiter (app/middleware/ratelimit.py) to
share state across worker processes and Fly machines. Redis is a soft
dependency: when the ``redis`` library is not installed or ``REDIS_URL`` is not
set, ``get_redis()`` returns ``None`` and every caller falls back to its
in-process implementation. Nothing here is required for the app to run.
"""

from app.cache.redis_client import get_redis, redis_available, reset_redis

__all__ = ["get_redis", "redis_available", "reset_redis"]
