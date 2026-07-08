"""Rate limiting middleware (token-bucket) — Redis-backed when available.

Design notes
------------
This is an *application-level* limiter — a soft guard for runaway clients and
misconfigured scripts.  It is NOT a replacement for the edge limiter.

    !!! The authoritative limit SHOULD still be enforced at the edge (Fly.io's
        TCP proxy rate-limit / Cloudflare / Nginx).  This module complements it.

Store selection (Redis when available, in-process otherwise)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
The limit is enforced against ONE of two token-bucket stores, chosen per
request:

  * **Shared Redis store** — when ``app.cache.redis_client.redis_available()``
    (i.e. ``REDIS_URL`` is set and the ``redis`` client connects).  The bucket
    state (``tokens``, ``last_ts``) lives in a Redis hash and is mutated by an
    ATOMIC Lua script (``_LUA_TOKEN_BUCKET``), so the cap is enforced GLOBALLY
    across every worker and every Fly machine.  This is the production path and
    it closes the multi-machine / multi-worker gap below.

  * **In-process ``_buckets`` dict** — the fallback used when no shared Redis
    store is configured (the default in CI and local dev).  This path is
    byte-for-byte the original best-effort per-process limiter and carries the
    caveat below.

The two stores implement the SAME token-bucket math (capacity =
``burst_factor × rpm``, refill = ``rpm / 60`` tokens/s) keyed on the same
``(identity, route_class)`` pair, so the Redis path is the same limit the
in-process path approximates — only now global rather than per-process.

Redis-outage degradation (never 500)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
A Redis exception mid-request must NEVER crash the request.  When the Lua
evaluation raises (connection dropped, server gone, etc.) we catch it and fall
back to the in-process bucket for THAT request, so a Redis outage degrades to
the per-process best-effort guard rather than returning HTTP 500.

Per-process caveat (applies ONLY to the no-Redis fallback)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
When no shared Redis store is configured, the ``_buckets`` store lives in one
OS process.  The app runs ``uvicorn --workers 2`` (see Dockerfile / fly.toml)
and Fly scales to multiple machines, so the true ceiling of the FALLBACK path
is ``workers × machines × rpm`` and is non-deterministic (load-balancing
decides which worker/machine sees a request).  To partially compensate we
divide the configured rpm by an *estimate* of the local worker count
(``WEB_CONCURRENCY`` / ``UVICORN_WORKERS`` env var, default 1 if unset) so each
worker's cap approximates ``rpm / workers``.  This only accounts for workers in
THIS machine — cross-machine multiplication remains and is intentionally left
to the edge limiter.  With ``REDIS_URL`` set, this caveat does NOT apply: the
cap is enforced globally.

    NOTE: the rpm/worker division is applied to the fallback's config too.  On
    the Redis path the global cap is therefore ``rpm / workers`` enforced
    globally — consistent with the fallback so behaviour does not jump when
    Redis flaps.  Set WEB_CONCURRENCY=1 if you want the Redis cap to equal the
    raw configured rpm.

Architecture
~~~~~~~~~~~~
A single ``_buckets`` dict (``dict[(str, str), _Bucket]``) is shared across
all async request handlers in one process.  Because Python's GIL protects
simple attribute reads/writes we do not need an explicit asyncio.Lock for the
fast path; the ``_cleanup`` sweep (run rarely) holds a threading.Lock.

Route classes
~~~~~~~~~~~~~
Requests are classified into one of four buckets (or SKIP):

    auth        /api/v1/auth/*
    query       /api/v1/query*  and  /api/v1/metrics/*/query|sql
    flow-run    /api/v1/flows/*/run  or  /api/v1/flows/run-cell
    chat        /api/v1/chat/stream, /api/v1/ai/chat*, /api/v1/ai/ask,
                /api/v1/ai/dashboard, /api/v1/ai/sql
    (skip)      /health, /api/v1/health, /embed/*, /assets/*
                and everything else (no-op)

Identity key  (FINDING 6B — org-keyed + embed exemption)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
The limiting key is derived from a CRYPTOGRAPHICALLY VERIFIED token when one
is present, falling back to the trusted client IP when not.

Verification at the limiter
    The Bearer JWT (if present) is verified at the limiter using the same
    ``verify_token`` path used by route auth — HS256 signature check for
    first-party tokens; JWKS signature check for embed RS256/ES256 tokens.
    Verification FAILURES (bad sig, expired, unknown issuer, no token) fall
    back to ``ip:<client>`` — a forged org claim CANNOT mint a fresh bucket or
    bypass the limiter.

Key selection
    * Verified first-party (``kind="access"``): ``org:<verified_org>`` when the
      token carries an ``org`` claim; else ``ip:<client>`` (no org claim is
      unusual but safe to fall back).
    * Verified embed (``kind="embed"``): exempt on the read paths below, so no
      key is computed; elsewhere the same ``org:<verified_org>`` or IP fallback.
    * Unverified / no token: ``ip:<client>`` (trusted TCP peer, or rightmost XFF).
    * No peer at all: ``"unknown"`` shared bucket (never skipped — FINDING 3).

SECURITY: the ``org`` value read from the token is the value DECODED BY
``verify_token`` after full signature verification — not from an unverified
header or body claim.  A forged ``org`` field inside an unsigned/wrong-signature
JWT will trigger a ``PyJWTError``/``AppError`` and fall back to IP key.

Embed exemption (cockpit tiles — FINDING 6B)
    A cockpit dashboard fires N concurrent metric tile queries.  Keying by IP
    or even by org would throttle tiles from a single-tenant install behind one
    IP.  Embed tokens (``kind="embed"``) are VERIFIED short-lived JWTs signed by
    the host; they carry hard per-tenant RLS constraints and are already
    restricted to the registered-query allowlist.  The server-side resource cost
    of each tile query is bounded and metered separately (query planner, DuckDB
    memory cap, etc.).

    Paths exempted for VERIFIED embed tokens only:
        POST /api/v1/metrics/{id}/query
        POST /api/v1/metrics/{id}/sql
        POST /api/v1/query  (and /api/v1/query/*)

    First-party (kind="access") tokens on these paths remain subject to the
    per-org query bucket.  The existing ``/embed/*`` static-asset skip is
    preserved.  A forged/invalid embed token falls back to IP key and is NOT
    exempted.

Trusted IP fallback (unchanged from original)
    * ``request.client.host`` — the real TCP peer (preferred).
    * RIGHT-most ``X-Forwarded-For`` — appended by the trusted proxy.
    * ``"unknown"`` — single shared conservative bucket when no peer is available.
    The LEFT-most XFF is NEVER used (attacker-controlled — FINDING 1).

Configuration (NUBI_RATELIMIT_* env vars)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    NUBI_RATELIMIT_ENABLED          true/false, default true
    NUBI_RATELIMIT_AUTH_RPM         auth route RPM cap, default 30
    NUBI_RATELIMIT_QUERY_RPM        query route RPM cap, default 120
    NUBI_RATELIMIT_FLOWRUN_RPM      flow-run route RPM cap, default 60
    NUBI_RATELIMIT_CHAT_RPM         chat/AI route RPM cap, default 20
    NUBI_RATELIMIT_BURST_FACTOR     burst multiplier (bucket depth = cap * factor),
                                    default 1.5 — allow short bursts above the
                                    steady-state rate before throttling kicks in

All defaults are conservative for a typical SaaS API.  Set to higher values
or disable globally (NUBI_RATELIMIT_ENABLED=false) for development/tests.

Response format (HTTP 429)
~~~~~~~~~~~~~~~~~~~~~~~~~~
    HTTP 429 Too Many Requests
    Retry-After: <seconds_until_refill>
    Content-Type: application/json

    {"error": {"code": "RATE_LIMIT_EXCEEDED",
               "message": "Rate limit exceeded. Retry after <N> seconds."}}
"""

from __future__ import annotations

import logging
import math
import os
import threading
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from app.cache.redis_client import get_redis

if TYPE_CHECKING:
    from app.auth.verify import VerifiedIdentity

logger = logging.getLogger(__name__)

# ── Configuration ──────────────────────────────────────────────────────────────


def _bool_env(key: str, default: bool) -> bool:
    val = os.getenv(key, "").strip().lower()
    if not val:
        return default
    return val in ("1", "true", "yes", "on")


def _int_env(key: str, default: int) -> int:
    try:
        return int(os.getenv(key, str(default)).strip())
    except ValueError:
        return default


def _float_env(key: str, default: float) -> float:
    try:
        return float(os.getenv(key, str(default)).strip())
    except ValueError:
        return default


# Evaluated lazily at first request so tests can patch env vars after import.
class _Config:
    """Rate-limit configuration; re-reads env on first access per process."""

    __slots__ = (
        "_loaded",
        "enabled",
        "auth_rpm",
        "query_rpm",
        "flowrun_rpm",
        "chat_rpm",
        "burst_factor",
    )

    def __init__(self) -> None:
        self._loaded = False

    def _load(self) -> None:
        if self._loaded:
            return
        self.enabled = _bool_env("NUBI_RATELIMIT_ENABLED", default=True)
        # FINDING 4 (best-effort): the in-process bucket store is per-worker, so
        # N uvicorn workers on this machine multiply the effective cap by N.
        # Divide the configured rpm by the local worker count so each worker's
        # cap approximates rpm/N.  We read it from the standard env vars used by
        # the launcher (Dockerfile/fly.toml run `uvicorn --workers 2`).  This
        # only corrects for *local* workers — cross-machine multiplication is
        # intentionally left to the edge limiter (Fly/Cloudflare).
        # TODO: when an explicit worker-count is plumbed through (e.g. set
        # WEB_CONCURRENCY alongside `--workers`), this estimate becomes exact.
        workers = max(1, _int_env("WEB_CONCURRENCY", _int_env("UVICORN_WORKERS", 1)))
        self.auth_rpm = max(1, _int_env("NUBI_RATELIMIT_AUTH_RPM", default=30) // workers)
        self.query_rpm = max(1, _int_env("NUBI_RATELIMIT_QUERY_RPM", default=120) // workers)
        self.flowrun_rpm = max(1, _int_env("NUBI_RATELIMIT_FLOWRUN_RPM", default=60) // workers)
        # Chat/AI endpoints are expensive (LLM cost per turn) — default 20 rpm,
        # burst 1.5× (30 burst ceiling).  Set NUBI_RATELIMIT_CHAT_RPM to override.
        self.chat_rpm = max(1, _int_env("NUBI_RATELIMIT_CHAT_RPM", default=20) // workers)
        self.burst_factor = _float_env("NUBI_RATELIMIT_BURST_FACTOR", default=1.5)
        self._loaded = True

    # Allow attribute reads without explicitly calling _load(): trigger the lazy
    # load BEFORE reading (config fields don't exist until _load runs).
    def __getattribute__(self, name: str):  # type: ignore[override]
        if name not in ("_loaded", "_load", "__slots__") and not object.__getattribute__(self, "_loaded"):
            object.__getattribute__(self, "_load")()
        return object.__getattribute__(self, name)


_cfg = _Config()

# ── Token bucket ───────────────────────────────────────────────────────────────


@dataclass
class _Bucket:
    """Continuous token-bucket for a single (identity, route_class) pair.

    ``capacity`` tokens are the burst ceiling.
    ``refill_rate`` tokens are added per second (= rpm / 60.0).
    ``tokens`` starts full.

    Thread-safe for the read-and-decrement fast path via Python's GIL on
    CPython (float assignment is atomic at the bytecode level).  The ``last_ts``
    update and ``tokens`` decrement are not atomically paired but the only
    consequence of a race is a ±1 token inaccuracy — acceptable for a
    best-effort app limiter.
    """

    capacity: float
    refill_rate: float           # tokens per second
    tokens: float = field(init=False)
    last_ts: float = field(init=False)

    def __post_init__(self) -> None:
        self.tokens = self.capacity
        self.last_ts = time.monotonic()

    def consume(self, now: float) -> tuple[bool, int]:
        """Try to consume one token.

        Returns
        -------
        allowed : bool
            True when a token was consumed; False when the bucket is empty.
        retry_after : int
            0 when allowed; seconds until ~1 token refills when denied.
        """
        # Refill elapsed tokens (never exceed capacity).
        elapsed = now - self.last_ts
        self.last_ts = now
        self.tokens = min(self.capacity, self.tokens + elapsed * self.refill_rate)

        if self.tokens >= 1.0:
            self.tokens -= 1.0
            return True, 0

        # Seconds until one token refills.
        retry_after = max(1, math.ceil((1.0 - self.tokens) / self.refill_rate))
        return False, retry_after


# Global bucket store: (identity_key, route_class) -> _Bucket
_buckets: dict[tuple[str, str], _Bucket] = {}
_buckets_lock = threading.Lock()

# Cleanup: evict buckets that have been full (idle) for >10 minutes.
_CLEANUP_INTERVAL_S = 600
_last_cleanup: float = 0.0


def _maybe_cleanup(now: float) -> None:
    global _last_cleanup
    if now - _last_cleanup < _CLEANUP_INTERVAL_S:
        return
    _last_cleanup = now
    with _buckets_lock:
        stale = [
            k
            for k, b in _buckets.items()
            if b.tokens >= b.capacity and (now - b.last_ts) > _CLEANUP_INTERVAL_S
        ]
        for k in stale:
            del _buckets[k]
    if stale:
        logger.debug("ratelimit: evicted %d idle buckets", len(stale))


def _get_or_create_bucket(key: tuple[str, str], rpm: int) -> _Bucket:
    b = _buckets.get(key)
    if b is not None:
        return b
    with _buckets_lock:
        # Double-checked locking (safe under the GIL for CPython).
        b = _buckets.get(key)
        if b is None:
            b = _Bucket(
                # burst ceiling: burst_factor × rpm tokens (e.g. 1.5 × 120 = 180)
                capacity=max(1.0, _cfg.burst_factor * rpm),
                # steady-state: rpm tokens per minute → rpm/60 per second
                refill_rate=rpm / 60.0,
            )
            _buckets[key] = b
    return b


# ── Redis-backed token bucket (global, atomic) ───────────────────────────────────

# Key namespace for the distributed limiter.  Each (identity, route_class) pair
# maps to a Redis HASH holding {tokens, ts}.
_REDIS_KEY_PREFIX = "nubi:rl:"

# Idle buckets self-expire after this many seconds (mirrors the in-process
# cleanup: a bucket sitting full longer than this is indistinguishable from a
# freshly-created full one, so we can safely let Redis evict it).
_REDIS_TTL_S = 600

# Atomic token-bucket in a single Lua script (server-side, so the read-refill-
# decrement sequence is a single atomic step across ALL clients).  This is the
# SAME math as `_Bucket.consume`:
#
#   KEYS[1] = bucket hash key
#   ARGV[1] = capacity         (burst_factor * rpm)
#   ARGV[2] = refill_rate      (rpm / 60, tokens per second)
#   ARGV[3] = now              (unix time, seconds, fractional)
#   ARGV[4] = ttl              (seconds; the key auto-expires when idle)
#
# Returns {allowed, retry_after}:
#   allowed     = 1 when a token was consumed, else 0
#   retry_after = 0 when allowed; ceil((1-tokens)/refill_rate) (>=1) when denied
#
# A missing/expired hash is treated as a full bucket (tokens = capacity), which
# matches `_get_or_create_bucket` starting new buckets full.
_LUA_TOKEN_BUCKET = """
local capacity = tonumber(ARGV[1])
local refill = tonumber(ARGV[2])
local now = tonumber(ARGV[3])
local ttl = tonumber(ARGV[4])

local data = redis.call('HMGET', KEYS[1], 'tokens', 'ts')
local tokens = tonumber(data[1])
local last = tonumber(data[2])
if tokens == nil then
  tokens = capacity
  last = now
end

local elapsed = now - last
if elapsed < 0 then elapsed = 0 end
tokens = math.min(capacity, tokens + elapsed * refill)

local allowed = 0
local retry_after = 0
if tokens >= 1.0 then
  tokens = tokens - 1.0
  allowed = 1
else
  retry_after = math.ceil((1.0 - tokens) / refill)
  if retry_after < 1 then retry_after = 1 end
end

redis.call('HSET', KEYS[1], 'tokens', tokens, 'ts', now)
redis.call('EXPIRE', KEYS[1], ttl)
return {allowed, retry_after}
"""


def _redis_consume(
    client, key: tuple[str, str], rpm: int, now: float
) -> tuple[bool, int]:
    """Consume one token from the GLOBAL (Redis) bucket for *key*.

    Mirrors ``_Bucket.consume`` but the state lives in Redis and the
    read-refill-decrement is executed atomically by ``_LUA_TOKEN_BUCKET``, so
    the cap is enforced across every process and machine sharing the store.

    Raises whatever the redis client raises on a connection/eval error — the
    caller (``dispatch``) catches it and degrades to the in-process bucket.
    """
    identity, route_class = key
    redis_key = f"{_REDIS_KEY_PREFIX}{identity}:{route_class}"
    capacity = max(1.0, _cfg.burst_factor * rpm)
    refill_rate = rpm / 60.0
    result = client.eval(
        _LUA_TOKEN_BUCKET,
        1,
        redis_key,
        capacity,
        refill_rate,
        now,
        _REDIS_TTL_S,
    )
    # redis returns a list of (possibly bytes/str) integers.
    allowed = int(result[0]) == 1
    retry_after = int(result[1])
    return allowed, retry_after


# ── Route classification ───────────────────────────────────────────────────────

# Paths that are always skipped (health checks, static assets, internal ticks).
_SKIP_PREFIXES = (
    "/health",
    "/api/v1/health",
    "/embed/",
    "/assets/",
    "/docs",
    "/redoc",
    "/openapi",
)


def _classify(path: str) -> tuple[str | None, int]:
    """Return (route_class, rpm) or (None, 0) to skip.

    route_class values: 'auth', 'query', 'flow-run', 'chat'
    """
    for pfx in _SKIP_PREFIXES:
        if path == pfx or path.startswith(pfx):
            return None, 0

    # Chat/AI: the streaming chat editor and the AI agentic endpoints.
    # These trigger LLM calls (high cost) so they get their own conservative
    # cap (NUBI_RATELIMIT_CHAT_RPM, default 20/min, burst 1.5×).
    # Covers:
    #   POST /api/v1/chat/stream          — streaming editor chat (SSE)
    #   POST /api/v1/ai/chat              — agentic chat (non-streaming)
    #   POST /api/v1/ai/chat/stream       — agentic chat (streaming SSE)
    #   POST /api/v1/ai/ask               — text-to-SQL
    #   POST /api/v1/ai/dashboard         — dashboard generation
    #   POST /api/v1/ai/sql               — SQL generation
    if path == "/api/v1/chat/stream" or (
        path.startswith("/api/v1/ai/") and path not in (
            "/api/v1/ai/context",
            "/api/v1/ai/dashboard/schema",
        ) and not path.endswith("/schema")
    ):
        return "chat", _cfg.chat_rpm

    # Flow-run: POST /api/v1/flows/<id>/run, /api/v1/flows/run-cell,
    #           /api/v1/flows/<id>/backfill, /api/v1/flows/<id>/sweep
    # Backfill and sweep hold a worker for up to 600 s / 300 s respectively —
    # the same resource footprint as a long flow run — so they share the same
    # rpm bucket to prevent resource exhaustion via the scheduling surface.
    if path.startswith("/api/v1/flows/") and (
        path.endswith("/run")
        or "/run-cell" in path
        or path.endswith("/backfill")
        or path.endswith("/sweep")
    ):
        return "flow-run", _cfg.flowrun_rpm

    # Auth: anything under /api/v1/auth/
    if path.startswith("/api/v1/auth/") or path == "/api/v1/auth":
        return "auth", _cfg.auth_rpm

    # Query: POST /api/v1/query (exact or with trailing /registry etc.)
    # Board provider data: POST /api/v1/boards/<id>/providers/<pid>/data
    # Metrics read surface: /api/v1/metrics/{id}/query|sql
    # Embed tokens never face billing but still drive server compute; classifying
    # these routes into the 'query' bucket gives them the same hard rpm ceiling so a
    # cache-busting embed token cannot trigger unbounded live flow executions.
    # NOTE: VERIFIED embed tokens on these specific paths are EXEMPTED from the
    # limiter entirely (see _extract_verified_identity); first-party tokens are not.
    if path == "/api/v1/query" or path.startswith("/api/v1/query/"):
        return "query", _cfg.query_rpm

    if path.startswith("/api/v1/boards/") and path.endswith("/data"):
        # Match: /api/v1/boards/<board_id>/providers/<pid>/data
        parts = path.split("/")
        # Expected: ['', 'api', 'v1', 'boards', <bid>, 'providers', <pid>, 'data']
        if len(parts) == 8 and parts[5] == "providers":
            return "query", _cfg.query_rpm

    # Metrics read surface (semantic layer execute paths).
    # /api/v1/metrics/<id>/query  — compile + execute (Arrow)
    # /api/v1/metrics/<id>/sql    — dry compile (no execution)
    if path.startswith("/api/v1/metrics/"):
        parts = path.split("/")
        # Expected: ['', 'api', 'v1', 'metrics', <id>, <action>]
        if len(parts) == 6 and parts[5] in ("query", "sql"):
            return "query", _cfg.query_rpm

    return None, 0


# ── Identity resolution ────────────────────────────────────────────────────────


# Shared bucket key used when no trusted client IP is available.  This is a
# SINGLE bucket all such requests share, so anonymous floods stay bounded
# (FINDING 3 — we never skip limiting for these).
_UNKNOWN_IDENTITY = "unknown"

# Paths on which VERIFIED embed tokens are fully exempt from rate limiting.
# These are the cockpit tile read paths that a single dashboard fires N times
# concurrently.  First-party tokens on these paths remain subject to the per-org
# query bucket.  The set is small and checked with a fast startswith/split so
# there is no regex overhead per request.
_EMBED_EXEMPT_PREFIXES = (
    "/api/v1/query",     # covers /api/v1/query and /api/v1/query/*
    "/api/v1/metrics/",  # covers /api/v1/metrics/<id>/query|sql (checked below)
)


def _client_ip(request: Request) -> str | None:
    """Return the *trusted* client IP, or ``None`` if none is available.

    SECURITY (FINDING 1): we must NOT trust client-supplied addressing.

      * ``request.client.host`` is the real TCP peer — the address the socket
        is actually connected to.  Behind the Fly proxy this is the proxy's
        address, which is exactly what we want to throttle on (and for auth
        routes specifically, the real peer is the only acceptable key — a
        spoofable header must never grant a fresh bucket).  It is preferred.

      * The LEFT-most ``X-Forwarded-For`` entry is fully attacker-controlled
        (the client can send any value, and a unique one per request), so we
        NEVER key on it.  Only the RIGHT-most entry — the hop appended by the
        trusted proxy closest to us — is used, and only as a fallback when the
        TCP peer is genuinely unavailable.
    """
    # 1. Real TCP peer (preferred, non-forgeable at this layer).
    if request.client and request.client.host:
        return request.client.host

    # 2. Fallback: RIGHT-most XFF entry (appended by the trusted proxy).
    #    Never the left-most — that is the attacker-controlled value.
    xff = request.headers.get("x-forwarded-for", "")
    if xff:
        entries = [p.strip() for p in xff.split(",") if p.strip()]
        if entries:
            return entries[-1]

    return None


def _is_embed_exempt_path(path: str) -> bool:
    """Return True if *path* is on the embed-exempt read surface.

    Exempt paths (VERIFIED embed tokens skip rate limiting here):
        /api/v1/query         — exact
        /api/v1/query/*       — sub-paths
        /api/v1/metrics/<id>/query
        /api/v1/metrics/<id>/sql
    """
    if path == "/api/v1/query" or path.startswith("/api/v1/query/"):
        return True
    if path.startswith("/api/v1/metrics/"):
        parts = path.split("/")
        # ['', 'api', 'v1', 'metrics', <id>, <action>]
        if len(parts) == 6 and parts[5] in ("query", "sql"):
            return True
    return False


def _extract_verified_identity(request: Request) -> tuple[str | None, bool]:
    """Attempt to cryptographically verify the Bearer token and derive the key.

    SECURITY CONTRACT
    -----------------
    The ``org`` and ``kind`` values returned here come EXCLUSIVELY from
    ``verify_token()`` — which validates the JWT signature (HS256 for
    first-party, RS256/ES256 JWKS for embed).  We NEVER read the raw JWT
    payload directly.  A forged or tampered token raises ``AppError`` /
    ``PyJWTError`` and this function returns ``(None, False)`` so the caller
    falls back to the IP key — a forged ``org`` claim or a forged "embed" kind
    CANNOT mint a fresh bucket or obtain the embed exemption.

    Returns
    -------
    (identity_key, is_verified_embed)
        ``identity_key``      — ``"org:<org>"`` on success, or ``None`` on
                                failure (fall back to IP).
        ``is_verified_embed`` — ``True`` only when the token is a VERIFIED embed
                                token (``kind == "embed"``); always ``False`` on
                                verification failure.
    """
    auth_header = request.headers.get("authorization", "")
    if not auth_header.lower().startswith("bearer "):
        return None, False

    token = auth_header[7:].strip()
    if not token:
        return None, False

    try:
        # Lazy import to avoid circular deps at module load time.  verify_token
        # is the sync path: HS256 first-party + in-process IssuerRegistry for
        # embed RS256/ES256.  The async DB-fallback path (_verify_embed_token_async)
        # is NOT used here so we stay synchronous inside the middleware.
        from app.auth.verify import verify_token  # noqa: PLC0415

        identity = verify_token(token, expected_origin=None)
    except Exception:  # noqa: BLE001 — AppError, PyJWTError, anything → fall back to IP
        return None, False

    org: str | None = identity.org
    is_embed = identity.kind == "embed"

    if org:
        return f"org:{org}", is_embed

    # Verified token but no org claim: fall back to IP (unusual but safe).
    return None, is_embed


def _extract_identity(request: Request) -> str:
    """Return the rate-limiting key — derived only from non-forgeable signals.

    Key derivation (FINDING 6B):
    1. Attempt to cryptographically verify the Bearer token via ``verify_token``.
       On success, key by ``org:<verified_org>`` (users in the same org share one
       bucket; users in different orgs have independent buckets).
    2. Fall back to the trusted client IP when no token is present, verification
       fails, or the verified token carries no ``org`` claim.
    3. Fall back to ``"unknown"`` shared bucket when no peer IP is available.

    A forged ``org`` in an unsigned/badly-signed JWT will cause ``verify_token``
    to raise — we catch all exceptions and fall through to the IP key, so no
    forged claim can influence bucket selection.
    """
    # 1. Try verified-token key (forge-proof via verify_token).
    token_key, _ = _extract_verified_identity(request)
    if token_key is not None:
        return token_key

    # 2. Trusted client IP (real TCP peer; right-most XFF as a last resort).
    ip = _client_ip(request)
    if ip:
        return f"ip:{ip}"

    # 3. No peer available → shared, throttled bucket (FINDING 3 — not skipped).
    return _UNKNOWN_IDENTITY


# ── Middleware ─────────────────────────────────────────────────────────────────


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Starlette/FastAPI middleware that enforces per-(identity, route_class) limits.

    Register via ``register_ratelimit(app)`` — that helper reads the
    NUBI_RATELIMIT_ENABLED flag and is a no-op when limiting is disabled, so
    callers never need a conditional.
    """

    def _consume(self, bucket_key: tuple[str, str], rpm: int) -> tuple[bool, int]:
        """Consume one token for *bucket_key*, picking the store per request.

        Store selection (see module docstring):
          * shared Redis store available → enforce GLOBALLY via the atomic Lua
            token-bucket (``_redis_consume``).  On ANY Redis exception we
            degrade to the in-process bucket for this request — never 500.
          * otherwise → the original in-process ``_buckets`` path (byte-for-byte
            preserved so the no-Redis tests pass unchanged).

        Returns ``(allowed, retry_after)``.
        """
        # Cheap once-resolved check: get_redis() returns the cached client (or
        # cached None) after the first call, so this is effectively a boolean.
        client = get_redis()
        if client is not None:
            try:
                # Wall-clock seconds: shared across machines (monotonic clocks
                # are per-host and meaningless as a cross-process reference).
                return _redis_consume(client, bucket_key, rpm, time.time())
            except Exception as exc:  # noqa: BLE001 — degrade, never crash the request
                logger.warning(
                    "ratelimit: redis limiter error (%s); "
                    "falling back to in-process bucket for this request",
                    exc,
                )
                # fall through to the in-process path below

        now = time.monotonic()
        _maybe_cleanup(now)
        bucket = _get_or_create_bucket(bucket_key, rpm)
        return bucket.consume(now)

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        # Global off-switch (checked every request — cheap).
        if not _cfg.enabled:
            return await call_next(request)

        path = request.url.path
        route_class, rpm = _classify(path)

        if route_class is None:
            # No classification → pass through.
            return await call_next(request)

        # FINDING 6B: attempt to verify the bearer token once (fast, sync).
        # This gives us both the org key (for per-org bucketing) and the
        # is_embed flag (for the cockpit-tile exemption check below).
        token_key, is_verified_embed = _extract_verified_identity(request)

        # Embed exemption: VERIFIED embed tokens on the metrics/query read paths
        # are exempt so a cockpit firing N tiles is never throttled.
        # A forged/invalid embed token returns is_verified_embed=False here and
        # falls through to normal rate-limiting — no exemption for forgeries.
        if is_verified_embed and _is_embed_exempt_path(path):
            return await call_next(request)

        # Identity: use verified org key when available; else IP fallback.
        if token_key is not None:
            identity = token_key
        else:
            ip = _client_ip(request)
            identity = f"ip:{ip}" if ip else _UNKNOWN_IDENTITY

        # FINDING 3: we do NOT pass 'unknown' through unthrottled.  When the
        # caller can't be identified they share a single conservative bucket
        # (identity == _UNKNOWN_IDENTITY) so anonymous floods stay bounded.

        bucket_key = (identity, route_class)
        allowed, retry_after = self._consume(bucket_key, rpm)

        if allowed:
            return await call_next(request)

        logger.info(
            "ratelimit: 429 %s class=%s retry_after=%ds path=%s",
            identity,
            route_class,
            retry_after,
            path,
        )
        return JSONResponse(
            status_code=429,
            headers={"Retry-After": str(retry_after)},
            content={
                "error": {
                    "code": "RATE_LIMIT_EXCEEDED",
                    "message": (
                        f"Rate limit exceeded. Retry after {retry_after} second(s)."
                    ),
                }
            },
        )


def register_ratelimit(app: FastAPI) -> None:
    """Attach ``RateLimitMiddleware`` to *app* when rate-limiting is enabled.

    This is always safe to call — when ``NUBI_RATELIMIT_ENABLED=false`` (or
    the env var is absent and the default is True) the middleware is added but
    exits immediately on every request, adding negligible overhead.

    Designed for a single call from ``main.py:create_app()``.
    """
    app.add_middleware(RateLimitMiddleware)
    logger.debug(
        "ratelimit: middleware registered (enabled=%s auth_rpm=%s "
        "query_rpm=%s flowrun_rpm=%s chat_rpm=%s burst_factor=%s)",
        _cfg.enabled,
        _cfg.auth_rpm,
        _cfg.query_rpm,
        _cfg.flowrun_rpm,
        _cfg.chat_rpm,
        _cfg.burst_factor,
    )
