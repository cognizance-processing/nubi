"""Request-latency timing middleware — zero dependencies, never breaks a request.

Times every request with ``time.perf_counter`` (a monotonic, high-resolution
clock) around ``call_next``, classifies the path to a coarse *route class*
bucket, and records the elapsed milliseconds into the process-wide
:class:`~app.observability.latency.LatencyRecorder`.

Safety contract
---------------
Observability must NEVER affect request handling.  The timing/recording is
wrapped so that:

  * classification and recording run in ``try/except`` — any error is swallowed
    (the recorder is best-effort telemetry, not part of the response path); and
  * the elapsed time is measured around ``call_next`` and recorded in a
    ``finally`` block so a downstream exception is still timed AND re-raised
    unchanged.

Bucketing
---------
The classifier mirrors ``app.middleware.ratelimit._classify`` (auth / query /
flow-run) but is intentionally a small *local* copy to keep coupling light and
to add an explicit ``other`` bucket for everything that is timed but not one of
the three hot classes.  Health / static / docs paths are skipped entirely (they
would pollute the percentiles and are already covered by ``/health``).

Register via :func:`register_latency` from ``main.py:create_app()`` — mirrors
``register_ratelimit``.
"""

from __future__ import annotations

import logging
import time

from fastapi import FastAPI, Request, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from app.observability.latency import OTHER_BUCKET, get_recorder

logger = logging.getLogger(__name__)


# Paths that are NOT timed (mirrors ratelimit._SKIP_PREFIXES): liveness probes,
# static assets, embed bundles, and API docs.  These are either trivial or
# served by Starlette internals and would skew the percentiles.
_SKIP_PREFIXES = (
    "/health",
    "/api/v1/health",
    "/ops/health",
    "/embed/",
    "/assets/",
    "/docs",
    "/redoc",
    "/openapi",
)


def _classify(path: str) -> str | None:
    """Return the route-class bucket for *path*, or ``None`` to skip timing.

    Buckets: ``auth`` / ``query`` / ``flow-run`` / ``other``.  Mirrors the
    rate-limiter's classification but adds the catch-all ``other`` bucket so all
    timed (non-skipped) requests land somewhere.
    """
    for pfx in _SKIP_PREFIXES:
        if path == pfx or path.startswith(pfx):
            return None

    # Flow-run: POST /api/v1/flows/<id>/run  or  /api/v1/flows/run-cell
    if path.startswith("/api/v1/flows/") and (
        path.endswith("/run") or "/run-cell" in path
    ):
        return "flow-run"

    # Auth: anything under /api/v1/auth/
    if path.startswith("/api/v1/auth/") or path == "/api/v1/auth":
        return "auth"

    # Query: POST /api/v1/query (exact or with trailing /registry etc.)
    if path == "/api/v1/query" or path.startswith("/api/v1/query/"):
        return "query"

    return OTHER_BUCKET


class LatencyMiddleware(BaseHTTPMiddleware):
    """Time each request and record the elapsed ms into the latency recorder."""

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        # Classify before timing so a skipped path adds zero overhead beyond the
        # prefix check.  Any classification error must not break the request.
        try:
            bucket = _classify(request.url.path)
        except Exception:  # noqa: BLE001 — telemetry must never break a request
            bucket = None

        if bucket is None:
            return await call_next(request)

        start = time.perf_counter()
        try:
            return await call_next(request)
        finally:
            # Record in a finally so failed requests are still timed; recording
            # is wrapped so it can NEVER turn a handled request into a 500.
            try:
                elapsed_ms = (time.perf_counter() - start) * 1000.0
                get_recorder().record(bucket, elapsed_ms)
            except Exception:  # noqa: BLE001
                logger.debug("latency: failed to record sample", exc_info=True)


def register_latency(app: FastAPI) -> None:
    """Attach :class:`LatencyMiddleware` to *app*.

    Always safe to call once from ``main.py:create_app()`` — mirrors
    ``register_ratelimit``.  The middleware is unconditional (it has no
    enable/disable flag) because its per-request cost is a couple of
    ``perf_counter`` calls plus a bounded deque append.
    """
    app.add_middleware(LatencyMiddleware)
    logger.debug("latency: middleware registered")
