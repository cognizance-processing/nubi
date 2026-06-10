# Nubi AI-Native Plan — agent-authoring, metrics layer, answers-first, ops maturity

**Through-line:** we are not bolting on "AI features." We are making the *existing*
spec / embed / governance / query-registry machinery **legible and safe for agents we
don't control** — the moat the "LLM-writes-HTML" trend can't touch. An external agent
runs one loop: **discover context → generate spec → validate → repair → preview →
publish behind a gate.** Every step must be a first-class, documented API.

**Process (unchanged):** non-worktree agents edit the LIVE tree on **disjoint file
sets**, edit-only (no git/pytest/npm). Orchestrator verifies (`backend: pytest`,
`frontend: npm run build`), commits, FFs `main` after each verified wave. RLS preserved
through any rewrite; secrets never in synced files; billing/cloud stays in `ee/`.

**Grounded state (survey):** `validate_spec` returns `tuple[spec|None, list[str]]`
(plain strings); no `POST /dashboards/validate`; `/ai/dashboard` is one-shot with a
silent `_build_null_spec` fallback; grounding has tables/cols/query-ids but **no params
or output schemas**; `GET /ai/dashboard/schema` + `spec_json_schema()` exist; MCP has 6
tools (no validate/upsert/preview/estimate); OpenAPI on in dev only; **no llms.txt**;
scopes support `action:resource:id` wildcards (so `write:board:dev` slots in); **no
config-hash idempotency**; **no headless preview**; **metrics/semantic layer = green-
field**; agent tool-use loop is **scripted (TODO)**; cache is **in-memory per-process,
no invalidation**.

---

## WAVE A — AI-authoring foundations (highest leverage, mostly exposing internals)

### A1 · `POST /dashboards/validate` + repair-grade structured errors  [backend]
Files: `backend/app/dashboards/errors.py` (NEW), `backend/app/dashboards/spec.py`,
`backend/app/routes/dashboards.py` (route), `backend/tests/test_dashboard_validate.py` (NEW).
- New route: validate a spec **without saving**; reuse `validate_spec`.
- Convert issues from plain strings → `{path, code, message, suggestion, valid_options}`
  (e.g. `widgets[2].encoding.x` + the available columns from that query's output schema +
  a one-line fix). Keep errors-vs-warnings explicit in the response.
- Bar: a mid-tier model fixes any single error in one round-trip.

### A2 · Context API (`GET /ai/context`) + params/output-shapes in grounding  [backend]
Files: `backend/app/ai/grounding.py`, `backend/app/routes/ai.py` (new endpoint),
`backend/tests/test_ai_context.py` (NEW).
- `GET /ai/context`: every registered query as `{id, name, description, params[]
  (name/type/default/required/options_query_id), output_schema[], datastore}` +
  the variables/spec conventions, so ONE call gives an agent everything to author.
- Token-budget aware: `?q=` relevance filter (reuse the grounding scorer) + a compact format.
- Extend `build_catalog`/`ground` to include **params + output_schema** (the main source
  of invalid specs today is agents guessing column names).

### A3 · Repair loop in `/ai/dashboard` (kill the silent fallback)  [backend]
Files: `backend/app/ai/dashboard.py`, `backend/tests/test_ai_dashboard_repair.py` (NEW).
- Replace one-shot-then-template with **generate → validate → feed structured errors back
  → retry (2–3 rounds)**. Log every repair round (becomes eval data).
- The deterministic `_build_null_spec` stays ONLY for the no-API-key NullProvider path,
  never as a silent swallow of a real model's invalid output (that must surface, loudly).

---

## WAVE B — front doors for other people's AIs

### B1 · Expand the MCP server to the whole loop  [mcp]
Files: `mcp/nubi_mcp/server.py`, `mcp/tests/`.
- Add tools: `get_context`, `get_spec_schema`, `validate_spec`, `upsert_dashboard(env=dev)`,
  `preview_widget` (run query w/ limit), `estimate_query`, `promote`. (Today: 6 read-ish +
  2 write, no validate/upsert/preview.) MCP is the standard "other people's AIs plug in."

### B2 · OpenAPI descriptions + llms.txt + single-file spec reference  [docs]
Files: `backend/main.py` (prod-safe OpenAPI + rich tags), endpoint docstrings,
`public/llms.txt` (NEW), `docs/dashboard-spec-reference.md` (NEW, 8–10 complete example
specs: one per widget type, one cascading-filter, one tabs), `.claude/skills/` or a Cursor
rules file. Optimize the reference to be pasted into a context window.

### B3 · Governance as the agent sandbox  [backend]
Files: `backend/app/auth/scopes.py`, `backend/app/repos/*` (idempotency), resource CRUD
routes, `backend/tests/`.
- **Agent-scoped write tokens:** `write:board:dev` — create/update in `dev` only, never
  promote. Promote-to-prod stays the human gate (env system already 80% there).
- **AI attribution:** stamp versions with authoring identity (agent vs human) on
  `created_by`; surface "AI-authored, promoted by X" in UI.
- **Idempotency:** config-hash / `If-None-Match` on resource CRUD so a retried agent
  upsert doesn't create duplicate noise (versions table has a hash; the route doesn't).

---

## WAVE C — metrics / semantic layer (the biggest architectural gap)  [DESIGN DOC FIRST]
Write `METRICS_LAYER.md` first. The unit of reuse today is a registered SQL query; there
is no place where "revenue" / "active customer" is defined once and referenced everywhere
— so two dashboards can silently disagree, and an agent answers from hallucinated SQL
rather than a governed definition (with owner, grain, allowed dimensions). This is the
layer that makes AI authoring **consistent**, not merely valid (Looker/LookML, dbt, Cube).
- **C1** Metric model + store + CRUD: `metric = query/base + measure + allowed dimensions +
  time grain + owner`. Grow on the query-registry + output-shape substrate.
- **C2** Metric → SQL compilation (sqlglot), RLS keys preserved in the grain; reuse the
  preagg rollup path so hot metrics collapse to ~$0.
- **C3** Expose metrics in `/ai/context`, MCP, and the validator (widgets can bind to a
  metric, not raw SQL); governance + attribution.

## WAVE D — answers-first: ask → pin → watch  [DESIGN DOC + real tool-use]
Consumption is inverting: users ask a question, get a governed answer + chart; the
dashboard becomes the curated cache for recurring questions.
- **D1** Real provider tool-use loop in `app/ai/agent.py` (today scripted/TODO): grounded
  in the metrics layer, RLS-enforced.
- **D2** **Pin:** an answer → a dashboard widget (reuse spec + validate).
- **D3** **Watch:** answer → monitored metric; threshold alert (primitive exists in flows)
  + an agent that **explains** the alert ("revenue −12% WoW, driven by region X") pushed to
  a notify channel. The sleeper differentiator — proactive insight, not passive dashboards.

## WAVE E — operational maturity (the embedded-analytics buying gate)
- **E1** Redis-backed query cache: TTL-per-query + explicit invalidation endpoint (cache is
  in-memory/per-process today). **Same shared store fixes the rate-limiter multi-machine
  gap** from the security review (one investment, two fixes).
- **E2** Performance SLOs: query latency percentiles, uptime/status, documented rate limits.
- **E3** Compliance posture (SOC 2 / POPIA / GDPR) doc + checklist — a hard gate for
  embedded, since we touch the customer's customers' data. SDK: obsess over time-to-first-
  embed < 30 min from docs alone.
- **E4** Headless preview endpoint (`GET /boards/{id}/preview.png?env=dev`, server-side
  Playwright): vision agents self-check layouts; humans see what they're promoting; also
  unlocks the PNG/PDF export gap (one investment, two features).

---

## Sequencing
1. **Wave A** (validate + structured errors + context API + repair loop) — everything else
   depends on these; mostly exposing existing internals.
2. **Wave B** (MCP + OpenAPI + llms.txt + agent-sandbox tokens/idempotency) — external front door.
3. **Wave C** metrics layer (design-doc-first) — the consistency moat.
4. **Wave D** answers-first (ask→pin→watch + real tool-use).
5. **Wave E** ops maturity (Redis cache + SLOs + compliance + headless preview), ongoing.

## Invariants (every agent)
RLS preserved through any rewrite; bound params never concatenated; billing/cloud in `ee/`;
secrets never in synced files; tests gate; frontend build-verified; touch ONLY your task's files.
