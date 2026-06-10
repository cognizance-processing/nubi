# Nubi Build Plan — consolidated agent task breakdown

**Base:** `main` @ 84282c3 (verified: backend 3248 pass, build green). Worktree agents
branch from `main` — keep `main` current (FF after each verified wave) so worktrees
always have the latest base. **Storage backend = Cloudflare R2** (free egress).

**Process:** waves run sequentially; agents within a wave run in parallel **git worktrees**
on **disjoint file sets** (the collision lesson). Backend agents self-verify with pytest in
the worktree; frontend agents can't `npm build` in a worktree → orchestrator builds after
merge. Orchestrator reviews every diff, merges, runs full suite + build, FFs `main`, then
launches the next wave.

---

## What's already done (don't redo)
- **Variables:** store + CRUD + `{{vars.*}}` read in /query, flow SQL, python cells; `set_var(persist=True)` flush. (A5)
- **Output-shape:** `RegisteredQuery.output_schema` + post-exec validation (warn header / 422 strict). (A4 backend)
- **Dashboard tabs T1:** `Tab` model + `spec.tabs` + `widget.tab_id` validated; `Variable.mode` scan/slice.
- **Dashboard tabs T2:** `TabBar.jsx` standalone accessible component.
- **File-view backend (A7):** `GET /git/files` + `/git/files/content` + path guard; `gitenv.js` helpers.
- **Estimates (A8):** `Connector.estimate` (BigQuery dry-run, DuckDB EXPLAIN) — scaffolding, no route/caller yet.
- **flows-as-files (A3):** `git/flow_files.py` lossless serialize/load — BUILT BUT UNWIRED into any sync path.
- **Grid-width fix; pricing-grid responsive fix; code-splitting.**

---

## WAVE 1 — independent, ready, disjoint-file (parallel worktrees)

### W1-A · Pricing page → BigQuery-comparable model  [frontend]
Files: `src/pages/PricingPage.jsx`, `src/lib/pricing.js`, `src/components/pricing/PricingCalculator.jsx`.
Reframe the lakehouse/warehouse pricing to the model we settled on:
- Headline: **pay per TB scanned (~$5/TiB, first 1 TiB/mo free) + storage (~$0.02/GB-mo, R2) — cheaper than BigQuery, and dashboard *views* are free** (browser kernel).
- **Drop the "warehouse" framing as a competitor** — reposition as "BI-scale, zero-ops; outgrow it → push down to your own warehouse." Pull warehouse out of any head-to-head price table.
- Calculator: model `bytes_scanned × $/TiB + storage_gb × $/GB`, show "viewers free", show pre-run estimate concept.
Acceptance: page reflects $/TiB + storage + free-viewers; no "4× warehouse" penalty language; build green.

### W1-B · Variables ephemeral run-overlay (set_var persist=False)  [backend]
Files: `backend/app/flows/runtime.py`, `backend/app/flows/executor.py`, `backend/tests/test_flow_set_var.py`.
DONE: `set_var(persist=True)` flushes to store; outcome carries `set_vars`. MISSING: the
**non-persist, run-scoped overlay** — a `set_var(x, v)` (no persist) must be visible to LATER cells in
the SAME synchronous drain without hitting the store. Thread a `run_var_overlay` dict through
`drain_flow_run` → the per-cell `TaskContext` build: `vars = {**load_vars_namespace(...), **run_var_overlay}`;
after each cell merge `outcome["set_vars"]` (value-only) into the overlay. CAREFUL: the site already passes
`vars=await load_vars_namespace(...)` — REPLACE it, don't add a 2nd `vars=` kwarg. Scope: synchronous drain
only (distributed claim path = documented follow-up). Add a test: cell A `set_var('d','2024')`, cell B reads
`vars['d']` in the same run, store NOT written.

### W1-C · Wire flows-as-files into sync + output-shape sidecar  [backend]
Files: `backend/app/git/env_sync.py`, `backend/app/git/sync.py`, `backend/app/portability.py`, `backend/tests/`.
1. Wire `git/flow_files.py` (`serialize_flow_files`/`load_flow_files`) into the git push+pull path so a flow
   round-trips as `flows/<slug>__<id8>/flow.toml + cells/NN_<key>.{sql,py,md}` (it's currently unused). Pick ONE
   canonical layout and reconcile the legacy .yaml/.json (document the choice).
2. Output-shape sidecar: when a query syncs, also emit `queries/<id>.json` = its `output_schema`
   (`[{name,type}]`); on pull, load it back onto the query. Validate on import.
Acceptance: a flow with sql+python+md cells round-trips through git as files losslessly; query output_schema
syncs as a sidecar json; tests green.

### W1-D · File viewer panel (portal)  [frontend, NEW file]
Files: NEW `src/components/app/GitFilesPanel.jsx` (+ a mount you can reach; reuse `gitenv.js`
`listGitFiles`/`getGitFileContent` which already exist). A read-only tree browser: left pane folder tree
(queries/ dashboards/ flows/), right pane file content with a ref/branch selector. Degrade gracefully (null →
"no synced repo"). Standalone component; do NOT wire into shared shells yet (Wave 2 does mounting).

---

## WAVE 2 — dashboard tabs integration + unified git topbar (shared frontend; sequential)
- **T3** SpecRenderer: partition widgets by active tab + render `TabBar`. **T4** `DashboardViewPage`: `_tab` URL param.
  **T5** `EditorPage`: tab strip (add/rename/reorder/delete, move-widget-to-tab). **T6** tests.
- **Unified Git surface:** new `GitSyncPanel` (status + git graph + commit/push/pull, reusing `GitGraphDialog`
  + `gitenv.js`) mounted as a **right-hand panel / topbar control in `AppShell`**, reachable from
  dashboards + queries + flows (one component, one project context). Mount `GitFilesPanel` (W1-D) here too.
  This is the "one component on topbar, one context, links to CLI sync" ask.
- These touch SpecRenderer/DashboardViewPage/EditorPage/AppShell (shared) → run as a SMALL sequential batch
  or orchestrator-led to avoid collisions.

## WAVE 3 — Filter / variable input overhaul (Track F)
- **F1** input primitives (`src/dashboards/inputs/` new) + dropdown-clipping fix (SpecRenderer overflow-hidden).
- **Filter placement toggle** (sidebar drawer vs on-grid) in the inspector; **edit-mode filter button + drawer +
  dark-mode toggle** cluster. **F2** select-all/clear/invert/exclude. **F3** query-backed autocomplete.
  **F4** daterange presets. **F5** customization surface. **F6/F7** spec validation + tests.
- **Cascading/circular filters** (reactive DAG): edges derived from `{{vars.*}}`/filter refs; topo-walk dirty
  subgraph; cycle detection at graph-build (reject). Rides the variables substrate + pre-agg.

## WAVE 4 — DuckDB managed lakehouse / optimizer + billing  (DESIGN-DOC FIRST)
The big architectural one — orchestrator writes `MANAGED_LAKEHOUSE.md` design doc first, then agents:
- **Universal pre-agg across ALL connectors** (remove redundancy): one miner + one `route_to_rollup_shape`
  rewrite + materialize-into-R2-lakehouse; the only per-connector bit is refresh = `connector.execute`. Most
  valuable in front of paid warehouses (collapse repeated reads → ~$0). RLS keys stay in the rollup grain.
- **Scale-to-zero DuckDB execution** on Fly Machines (wake/sleep) → bill query-seconds, warm tier for interactive.
- **Auto partition/cluster** (declarative defaults + auto-maintain; lambda freshness).
- **Bytes-scanned billing:** meter post-pruning bytes (DuckDB) → `query_scan` usage event (core) →
  `ee/billing/tiers` `$/TiB` rate, DROP the 4× warehouse multiplier, storage → R2 parity (~$0.02/GB).
  Wire `Connector.estimate` to `/query/estimate` + pre-run UI chip (BigQuery dry-run parity).
- Engine = **DuckDB only**, browser + server (settled). Iceberg/Delta + sorted/partitioned/zstd Parquet for pruning.

---

## Invariants (every agent)
- Open-core: billing/cloud stays in `ee/`; **secrets never written to synced files**.
- RLS preserved through any rewrite; bound params never string-concatenated.
- Tests gate; frontend build-verified after merge. Touch ONLY your task's files.
