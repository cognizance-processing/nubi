# OUTSTANDING — Nubi implement-and-audit tracker

Drives the 15-min autonomous loop (cron `53cae62f`, started 2026-06-11). Each
iteration: pick the top unchecked item → implement properly with disjoint-file
agents → verify (backend pytest + `npm run build` + mcp pytest) → commit green →
update this file. **Never leave the tree red.**

Baseline at audit: commit `16f31d0` (Wave C). Waves A/B/C done. Verify commands:
- backend: `cd backend && python -m pytest -q`
- frontend: `npm run build`
- mcp: `cd mcp && python -m pytest -q`

---

## P0 — critical path

- [x] **Wave D1 — real provider tool-use loop** (`app/ai/agent.py`, `tools.py`): iterative
  tool loop + `list_metrics`/`query_metric` governed tools, RLS via claims. ✅ `47e5556`.
- [x] **Wave D2 — ask→pin** (`POST /ai/pin`, `routes/ai.py`): answer→validated widget,
  structured-error validation, append-or-create board. ✅ `47e5556`.
- [x] **Wave D3 — watch** (`app/ai/watch.py`, `routes/watches.py`, `0009_watches.sql`):
  metric threshold → AI explanation (deterministic under NullProvider) → notify channel;
  CRUD + `/evaluate` + `/tick`. ✅ `47e5556` (backend 3405 / mcp 67 / build green).
- [x] **Wave E1 — Redis cache + invalidation** (`app/connectors/cache.py`, `app/cache/redis_client.py`,
  `routes/cache.py`, `ratelimit.py`): pluggable Redis backend + tag invalidation + `/cache/stats` +
  `/cache/invalidate`; rate-limiter uses an atomic Redis token-bucket when REDIS_URL is set (fixes the
  multi-machine gap), in-process fallback otherwise. ✅ `04d2ce1` (backend 3428 passed).
- [ ] **Wave E4 — headless preview** (`GET /boards/{id}/preview.png?env=dev`): ⚠️ BLOCKED on an
  infra decision — Playwright is NOT installed (only system Chrome present), and a *proper* render
  needs (a) a browser binary in the prod image, (b) serving the built frontend so charts (echarts/JS)
  actually paint, (c) a short-lived internal embed token to auth the render. Don't ship a layout-only
  stub. Needs a infra/dependency call before implementing — deferred past widget-binding.

## P1 — high value

- [x] **usage_events(org_id, created_at) index** — ✅ already exists (`0006_platform.sql:53`);
  audit false-positive. No action.
- [x] **Wave E2 — SLOs/observability**: in-process latency percentiles (`app/observability/`),
  `LatencyMiddleware`, `GET /ops/stats`, `docs/observability.md` with published SLO targets +
  documented rate limits. ✅ `df41c15` (backend 3455). *Follow-up:* cross-process aggregation.
- [x] **Wave E3 — compliance posture**: `docs/compliance.md` — honest SOC 2 / POPIA / GDPR
  posture mapping real controls (planner RLS, AES-256-GCM/Fernet at rest, HttpOnly cookies,
  CASCADE erasure) + an explicit gap list (DPA, sub-processor list, IR runbook, pen-test) +
  sub-processors. Not a certification claim. ✅ (docs-only).
- [x] **Widget→metric binding** (Wave C "later"): `Widget.metric` binding (metric_id + dims/grain
  + filters) in `dashboards/spec.py`; `runMetricQuery` + Chart/Table/Kpi widgets fetch from
  `/metrics/{id}/query`; `/ai/pin` metric path. ✅ `7f4434a` (backend 3442, build green).
  *Follow-up:* embed web-component (`<nubi-chart metric-id=…>`) consumption of the emitted attrs.
- [ ] **Landing-page glow-up** (`src/pages/LandingPage.jsx`, `src/components/illustrations/*`):
  bolder/less-bland copy, real product screenshots replacing weak SVG illustrations. Use the
  **nubi-illustrations** skill's render→screenshot→critique loop (orchestrator-driven, both modes).
- [ ] **Security medium/low residue**: B2 stale-snapshot — re-validate owner policies at tick
  time vs current membership (`flows/runtime.py` ~1859); narrow remaining hot-path SQL re-parse
  (`planner.py`/`query_log.py` — partly done via registry short-circuit); document estimate
  quota consumption.

## P2 — follow-ups

- [ ] **Real scanned-bytes counters**: replace the Arrow-IPC-length proxy in `routes/query.py`
  with DuckDB post-pruning byte counters (parquet_metadata/httpfs) before `$5/TiB` is a public price.
- [ ] **Legacy `app/git/remote.py` argv-token push** (B5 low): delete/refactor to askpass like
  `remotes.py`; add a CI grep guard against `user:token@`/`git push <authed_url>` in argv.
- [ ] **FlyMachineExecutor wake/sleep** (`app/compute/serverless_exec.py`): real Fly Machines
  API wake/sleep (default HeavyPoolExecutor already works; this is explicit control).
- [ ] **Lakehouse optimizer deeper bits** (`app/lakehouse/optimizer.py`): write rollups to R2
  Parquet (not local DuckDB), auto incremental refresh, HLL/t-digest sketches.
- [ ] **Real-remote git push/pull verification** for the PAT/askpass path (needs a live remote;
  can't fully automate — document a manual test).
- [ ] **Provider model routing** (`routes/ai.py` TODO): thread the requested model through to
  the provider/agent.
- [ ] **Dashboard variable URL sync** (`DashboardViewPage.jsx` TODO M14-C-sync): URLSyncProvider.

---

## Log
- 2026-06-11 — audit established this tracker; Wave D (D1/D2/D3) implemented, verifying + committing.
