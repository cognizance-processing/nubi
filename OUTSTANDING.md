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
  tool loop + `list_metrics`/`query_metric` governed tools, RLS via claims. *Committed pending.*
- [x] **Wave D2 — ask→pin** (`POST /ai/pin`, `routes/ai.py`): answer→validated widget,
  structured-error validation, append-or-create board. *Committed pending.*
- [x] **Wave D3 — watch** (`app/ai/watch.py`, `routes/watches.py`, `0009_watches.sql`):
  metric threshold → AI explanation (deterministic under NullProvider) → notify channel;
  CRUD + `/evaluate` + `/tick`. *Committed pending verification.*
- [ ] **Wave E1 — Redis cache + invalidation** (`app/connectors/cache.py`): pluggable
  shared backend (Redis) behind the current in-process cache, TTL-per-query, explicit
  invalidation endpoint. **Also back the rate-limiter buckets with the same store** to close
  the multi-machine gap. Keep in-process as a fallback when no REDIS_URL. *Biggest ops debt.*
- [ ] **Wave E4 — headless preview** (`GET /boards/{id}/preview.png?env=dev`): server-side
  Playwright render; also unlocks PNG/PDF export + vision-agent self-check.

## P1 — high value

- [ ] **usage_events(org_id, created_at) index** — add a migration; reconcile
  (`ee/billing/reconcile.py`) currently has no covering index → full scans at volume.
- [ ] **Wave E2 — SLOs/observability**: latency percentiles + a status surface + documented
  rate limits (instrument query path; expose a metrics endpoint).
- [ ] **Wave E3 — compliance posture**: `docs/compliance.md` (SOC 2 / POPIA / GDPR checklist,
  data-handling, RLS audit-trail) — hard gate for embedded sales.
- [ ] **Widget→metric binding** (Wave C "later"): let a `Widget` bind `metric_id` + dims/grain
  (not just `query_id`) in `dashboards/spec.py` + `SpecRenderer.jsx`; wire `/ai/pin` metric path.
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
