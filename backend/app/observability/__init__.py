"""Lightweight, zero-dependency observability for Nubi.

This package provides *in-process* (per-worker / per-machine) request-latency
metrics with NO external dependencies (``prometheus_client`` is deliberately
NOT used).  It is the companion of the ``/ops/stats`` ops endpoint and the
``LatencyMiddleware`` request timer.

Per-process scope (important)
-----------------------------
Everything here lives in ONE OS process.  Nubi runs ``uvicorn --workers N`` and
Fly scales to multiple machines, so the recorder's snapshot reflects only the
worker that served the ``/ops/stats`` request — exactly like the rate-limiter's
in-process bucket store and the cache's per-worker hit/miss counters.
Cross-process aggregation (push to Redis / scrape at the edge) is a documented
follow-up; see ``docs/observability.md``.
"""

from app.observability.latency import LatencyRecorder, get_recorder

__all__ = ["LatencyRecorder", "get_recorder"]
