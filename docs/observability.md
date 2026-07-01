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

---

## Audit log (`GET /audit`)

The unified action audit log records org-scoped metadata for every mutation
(create / update / delete) across boards, queries, datastores, widgets,
canvases, connectors, MCP servers, and secrets.

**POPIA compliance:** the audit log stores metadata only — no row data, no
SQL text with literals, no credential material. The `summary` field contains
only non-sensitive keys (resource name, connector type, etc.).

**Auth:** `GET /audit` and `GET /audit/{resource_type}/{resource_id}` require
a valid first-party bearer token AND an owner/admin (approver) role.
Unauthenticated → 401. Non-approver → 403.

**Fire-and-forget writes:** `record_audit()` never raises and never blocks the
mutation path it wraps. A DB write failure is logged at WARNING level only.

See [api-reference.md#audit-log](api-reference.md#audit-log) for the full
endpoint specification and response shape.

### Guaranteed mutation coverage — the audit backstop middleware

Individual routes call `record_audit()` explicitly for the richest entries,
but a **backstop middleware** (`app/middleware/audit.py`, `AuditMiddleware`)
guarantees every successful mutation is captured even for routes that never
got an explicit call site:

- Records every **2xx** response to a mutating method (`POST`/`PUT`/`PATCH`/
  `DELETE`) under `/api/v1/*`, deriving `resource_type`/`resource_id` from the
  URL path segments and `action` as `"{resource_type}.{create|update|delete}"`.
- **Deduplicated** — a route that already called `record_audit()` sets
  `request.state.audit_logged = True` before responding; the middleware sees
  the flag and skips its own write, so no request is ever logged twice.
- **Same POPIA contract** as explicit calls: no request body, no query
  params, no PII — the `summary` is only `{method, path, status}`.
- Skips non-mutating methods, non-2xx responses, and a fixed set of
  prefixes (`/health`, `/api/v1/auth/*`, `/embed/*`, `/assets/*`, `/docs`,
  `/redoc`, `/openapi`, `/ops/health`).
- Identity (`actor_user_id`, `actor_kind`, `org_id`) is derived from the same
  Bearer-token verification the rate limiter uses; requests with no resolvable
  `org_id` are skipped (nothing to scope the row to) rather than logged
  ambiguously.
- **Fail-open** — the audit write is wrapped in its own try/except; a failure
  never alters or breaks the original response.

Registered once in `main.py:create_app()` alongside the rate-limit and
latency middlewares — no per-route opt-in required for a mutation to show up
in `GET /audit`.

---

## Nightly Watch Sweep

Nubi includes a **watch-sweep** scheduled job that evaluates an org's metric
watches on a cron and emits `WATCH_BREACH` webhook events so the host
(e.g. an embedding host) can react — keeping all sweep logic inside Nubi.

### How it works

1. The scheduler fires the `watch_sweep` job according to its cron
   (e.g. `0 2 * * *` — every night at 02:00 UTC).
2. The sweep iterates **all enabled watches** whose `org_id` matches the job's
   owning org.  Each watch is evaluated via the SAME engine as
   `POST /watches/{id}/evaluate`:
   - The metric is compiled and executed (DuckDB or the org's bound datastore).
   - The scalar measure is reduced and compared against the threshold (or
     change-over-time) rule.
3. For each **breached** watch:
   - An AI explanation is generated (deterministic template under `NullProvider`).
   - A `WATCH_BREACH` outbound webhook is emitted to every subscribed endpoint
     for the org (via `app.webhooks.events.emit_watch_breach`).
   - The in-app notification feed and configured channels (Slack, WhatsApp) are
     also updated (additive to the webhook).
4. Each watch is **best-effort**: a single watch error is recorded as
   `state='error'` and logged; the sweep continues to the next watch.
5. The job run records `row_count = breached_count` and a summary message in
   the `job_runs` table.

### Scheduling (for the host)

Create a `watch_sweep` job via `POST /api/v1/jobs` (first-party auth required):

```json
{
  "name": "Nightly Watch Sweep",
  "kind": "watch_sweep",
  "target": "",
  "schedule": "0 2 * * *"
}
```

The `schedule` field accepts any cron expression (5 fields, parsed via
`croniter`) or an `interval:Nh/Nm/Ns` string for interval-based runs.
`target` is ignored for this kind — the org is derived from the caller's
identity at creation time.

**Run on-demand** (e.g. during setup / after updating watches):

```
POST /api/v1/jobs/{id}/run
```

### Webhook events consumed by the host

Each breach emits a `watch_breach` event over the org's registered webhook
endpoints:

```jsonc
{
  "type": "watch_breach",
  "id": "<uuid4>",
  "org_id": "<org-uuid>",
  "occurred_at": "2025-06-01T02:01:23.456Z",
  "data": {
    "watch_id":   "<watch-uuid>",
    "name":       "Revenue Watch",
    "metric_id":  "demo_revenue",
    "value":      18500.0,
    "explanation":"Revenue Watch: revenue is 18500, > the 15000 threshold.",
    "labels":     {}          // host-supplied metadata; empty map when not set
  }
}
```

Register a webhook endpoint to receive these:
`POST /api/v1/webhooks/endpoints` with `event_types: ["watch_breach"]`.

### `watch_breach` — `labels` passthrough

Every `watch_breach` payload carries a **`labels`** field: an arbitrary
key-value map attached once per watch definition and passed through verbatim
in `emit_watch_breach`. Subscribers use it to correlate breach events with
their own domain objects without a secondary API call.

```jsonc
// watch_breach with labels
{
  "type": "watch_breach",
  "id": "f47ac10b-58cc-4372-a567-0e02b2c3d479",
  "org_id": "org-uuid",
  "occurred_at": "2026-06-24T02:01:45.123Z",
  "data": {
    "watch_id":    "fill-rate-warning",
    "name":        "Fill Rate Warning",
    "metric_id":   "category_fill_rate",
    "value":       0.71,
    "explanation": "Fill Rate Warning: fill_rate is 0.71, < the 0.80 threshold.",
    "labels": {
      "category_id": "cat-beverages",
      "alert_tier":  "p2"
    }
  }
}
```

**Setting labels** — declare them in the watch definition (YAML or API):

```yaml
# watches/fill_rate_warning.yaml
name: Fill Rate Warning
metric_id: category_fill_rate
threshold:
  op: "<"
  value: 0.80
labels:
  category_id: cat-beverages
  alert_tier: p2
```

Or via the API: include `"labels": {"category_id": "...", ...}` in
`POST /api/v1/watches` or in the `spec` block of an apply bundle envelope.

Labels are stored in `watches.config.labels` (JSONB) and never interpreted by
the server — they are purely a passthrough for the host's subscriber logic.

**POPIA note:** labels are host-supplied identifiers only. Do not store PII
or row-level data here; this field carries metadata about the watch definition,
not about the queried subjects.

### Pairing with the host's work-graph (response §2G)

The watch-sweep job is the **Nubi-side counterpart** to a host's work-graph
responder:

| Nubi side | Host side |
|-----------|-----------|
| Sweep runs on cron, evaluates watches | Receives `watch_breach` webhook |
| Emits `WATCH_BREACH` per breach | Triggers downstream work (e.g. alert, workflow, remediation) |
| Best-effort per-watch; errors logged | Should be idempotent (re-delivery safe) |

The host should treat each `watch_breach` event as an **idempotent trigger**
keyed by `data.watch_id` + `occurred_at` (the envelope `id` is unique per
emission and can serve as a deduplication key).

### Isolation + security

- **Org-scoped** — the sweep only touches watches stamped with the job's org.
  No cross-org leakage even if multiple orgs run sweeps in the same process.
- **System claims** — the sweep evaluates metrics with empty RLS policies
  (scheduler context, no per-user row restrictions).  This matches the behaviour
  of `POST /watches/tick`.
- **No new migrations** — the sweep reuses the existing `jobs`, `job_runs`, and
  `watches` tables unchanged.

### Existing `POST /watches/tick` endpoint

`POST /watches/tick` (shared-secret gated) is a lightweight HTTP trigger that
evaluates **all in-process watches** (not org-scoped, no cron, no job_runs
record). The `watch_sweep` job complements it with org isolation, DB-backed job
history, and proper cron scheduling — and is the recommended production path for
hosts that want a managed nightly sweep.
