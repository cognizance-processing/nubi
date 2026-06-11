# Observability, SLOs & Rate Limits

Nubi ships lightweight, **dependency-free** observability: in-process
request-latency percentiles, an ops stats endpoint, and documented service
targets. There is **no** `prometheus_client` dependency — metrics are computed
in pure Python and exposed as JSON.

> **Per-process scope (read this first).** The latency percentiles and the
> cache hit/miss counters are **per worker**. Nubi runs `uvicorn --workers N`
> and Fly scales to multiple machines, so a load-balanced call to `/ops/stats`
> samples *whichever worker served that request*. This mirrors the
> [rate-limiter](#rate-limits) (its in-process bucket store) and the cache
> (its per-worker hit/miss counters). Cross-process aggregation is a
> [documented follow-up](#cross-process-aggregation-follow-up).

## `GET /ops/stats`

First-party auth required (Bearer access token — same gate as `/cache/stats`).
Embed JWTs (RS256/ES256) and unauthenticated requests get `401`.

`/ops/stats` lives under `/ops/*` on purpose: `/metrics` is already the
**semantic-metrics** layer (`routes/metrics.py`), so the observability surface
does not collide with it.

```jsonc
{
  "latency": {
    "all":      { "count": 1284, "p50": 12.4, "p95": 88.1, "p99": 210.5, "max": 540.2, "mean": 24.7 },
    "query":    { "count": 902,  "p50": 18.0, "p95": 120.3, "p99": 260.0, "max": 540.2, "mean": 33.1 },
    "auth":     { "count": 211,  "p50": 4.2,  "p95": 11.0,  "p99": 22.0,  "max": 40.0,  "mean": 6.1 },
    "flow-run": { "count": 41,   "p50": 220.0,"p95": 900.0, "p99": 1500.0,"max": 2100.0,"mean": 310.0 },
    "other":    { "count": 130,  "p50": 6.0,  "p95": 30.0,  "p99": 70.0,  "max": 120.0, "mean": 9.4 }
  },
  "cache":  { "entries": 42, "hits": 318, "misses": 84, "hit_rate": 0.79, "tags": 7, "backend": "memory" },
  "uptime_s": 3725.114,
  "version": "0.1.0",
  "rate_limits": { "auth_rpm": 30, "query_rpm": 120, "flowrun_rpm": 60, "burst_factor": 1.5, "enabled": true }
}
```

### `latency` buckets

Requests are timed with `time.perf_counter` (monotonic) around `call_next` and
bucketed by **route class**, mirroring the rate-limiter's classifier:

| bucket     | matches                                                        |
|------------|----------------------------------------------------------------|
| `auth`     | `/api/v1/auth/*`                                                |
| `query`    | `/api/v1/query`, `/api/v1/query/*`                              |
| `flow-run` | `/api/v1/flows/<id>/run`, `/api/v1/flows/run-cell`             |
| `other`    | every other timed request (catch-all)                          |
| `all`      | synthetic aggregate of **every** sample, regardless of class   |

Skipped (never timed): `/health`, `/api/v1/health`, `/ops/health`, `/embed/*`,
`/assets/*`, `/docs`, `/redoc`, `/openapi*`.

Each bucket reports:

- `count` — **all-time** observed total for this worker (not bounded by the ring).
- `p50` / `p95` / `p99` — nearest-rank percentiles over the retained window.
- `max`, `mean` — over the retained window.

**Method.** Each bucket keeps a fixed-size ring of the last **1000** samples
(a `deque(maxlen=1000)`). `snapshot()` copies the ring under a lock, sorts it,
and indexes with the nearest-rank rule (`p` → `ceil(p/100 · n) − 1`). Memory is
`O(buckets × 1000)`; the number of buckets is capped (64) and overflow keys
fold into `other`, so memory can't grow without bound.

### `cache`

The active backend's `stats()` plus a `backend` field (`memory` | `redis`).
`hits`/`misses`/`hit_rate` are **per-worker** counters (the Redis backend can't
cheaply track per-key hits); `entries` is exact for `memory` and best-effort for
`redis`.

### `rate_limits`

A read-only view of the limiter's **effective** caps (`app.middleware.ratelimit`).
The rpm values already reflect the per-worker division the limiter applies
(`rpm / WEB_CONCURRENCY`); see [Rate limits](#rate-limits).

## `GET /ops/health`

Public, DB-free liveness ping: `{"status": "ok", "uptime_s": <float>}`. The
canonical liveness + DB-reachability probe remains `GET /health` (in `main.py`);
`/ops/health` is just a minimal sibling on the ops surface.

## SLO targets

These are the targets we publish and design to. They are **realistic** for the
current single-region deployment and are measured per-worker via `/ops/stats`
plus the edge (Fly) metrics for availability.

| SLO                                   | Target                          | Source / notes |
|---------------------------------------|---------------------------------|----------------|
| API availability (monthly)            | **99.5%**                       | Edge + `/health`; excludes scheduled maintenance. |
| Interactive query latency (`query`)   | **p95 ≤ 800 ms**, p99 ≤ 2 s     | Warm/cached path; cold scans over large datastores are exempt. |
| Auth latency (`auth`)                 | **p95 ≤ 150 ms**                | Token mint / `/auth/me`. |
| Read endpoints (`other`)              | **p95 ≤ 400 ms**                | Metadata / list / config reads. |
| Flow-run *enqueue* latency (`flow-run`)| **p95 ≤ 1 s**                  | API-side enqueue only; **execution** runs in the worker pool and is not bounded by this SLO. |
| Cache hit-rate (steady state)         | **≥ 60%**                       | `cache.hit_rate` over a representative window; lower right after deploy/restart. |

Caveats:

- Latency SLOs are evaluated on the **interactive** request path. Long-running
  flow *execution* (heavy compute in `backend/worker.py`) is intentionally out
  of scope — those run asynchronously and are governed by quotas, not latency.
- Percentiles are per-worker; aggregate fleet-wide percentiles require the
  follow-up below or the edge's own metrics.

## Rate limits

Application-level, best-effort caps enforced by
`app.middleware.ratelimit`. They **complement** the authoritative edge limiter
(Fly/Cloudflare) — they are not a replacement for it. Keyed by trusted client
IP per route class.

| route class | env var                      | default rpm |
|-------------|------------------------------|-------------|
| `auth`      | `NUBI_RATELIMIT_AUTH_RPM`     | **30**      |
| `query`     | `NUBI_RATELIMIT_QUERY_RPM`    | **120**     |
| `flow-run`  | `NUBI_RATELIMIT_FLOWRUN_RPM`  | **60**      |

Bucket depth allows short bursts above the steady rate:
`capacity = burst_factor × rpm` with `NUBI_RATELIMIT_BURST_FACTOR` (default
**1.5**). Disable globally with `NUBI_RATELIMIT_ENABLED=false`. Over-limit
requests get `429` with a `Retry-After` header.

**Per-process vs Redis-global.** When `REDIS_URL` is set, the limiter enforces
the cap **globally** across all workers/machines via an atomic Lua token bucket.
Without Redis (CI / local dev), the fallback is **per-worker**: the true ceiling
is `workers × machines × rpm`, so the configured rpm is divided by the local
worker count (`WEB_CONCURRENCY` / `UVICORN_WORKERS`) to approximate `rpm/worker`.
The `/ops/stats` `rate_limits` block reports these per-worker effective values.

## Cross-process aggregation (follow-up)

The recorder and cache counters are single-process. To get fleet-wide
percentiles and hit-rates, a follow-up should either:

1. **Push** each worker's `snapshot()` to a shared store (e.g. Redis) on an
   interval and aggregate there, or
2. **Scrape** `/ops/stats` per worker/machine at the edge and merge.

Until then, treat `/ops/stats` numbers as a sample from one worker — useful for
spot-checks and trend direction, not as authoritative fleet-wide aggregates.
This is the same per-process trade-off the rate-limiter (no-Redis fallback) and
the cache (per-worker hit/miss) already make.
