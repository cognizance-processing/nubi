# Roadmap — Embedding, Snapshots & Reporting/Presentations

Consolidates everything discussed: embed connector override, frozen snapshots,
public export, scheduled reports, and a unified Dashboard / PDF / PPT surface.

For the architecture rationale and billing economics behind every shipped
feature, see [Architecture & Economics](architecture-and-economics.md).

**Hard constraint (applies to every task): NO degradation to the dashboarding
system.** The dashboard grid keeps its exact engine and flexibility; the schema
split is additive and migrates existing boards 1:1, gated behind a migration +
parity test before anything builds on it.

---

## ✅ Already shipped (prior fan-outs)

- **Mode 2 — id-based connector override.** Host-signed `datastore` claim →
  whole-dashboard connector override, org-scoped, RLS untouched. (`query.py`,
  `auth/verify.py`, `routes/embed.py`)
- **Mode 3a — frozen DuckDB snapshot + scheduled refresh.** Sidecar `.duckdb`,
  `snapshot_refresh` Flows task. (`app/embedding/snapshot.py`, `routes/snapshot.py`)
- **Mode 3b — gated public/CDN static export.** Double-gated (kill switch +
  per-org flag), audit-logged, loud UNSAFE banner. (`app/embedding/public_export.py`)
- **Shared:** `collect_board_data()` (`app/dashboards/collect.py`), currency
  selector, pricing-calculator fairness fixes, LiteLLM provider unification.

---

## Design decisions (locked)

1. **One widget library, three layout containers.** `board.widgets[]` (content,
   position-free) + `board.surfaces.{grid,report,slides}` (per-surface layout
   referencing widget ids). Dashboard = responsive grid; Report = paginated doc
   flow; Presentation = fixed-aspect slides.
2. **SVG is the common vector intermediate.** Each widget → SVG; a page/slide is
   an SVG scene. Fan out to PDF (vector) and PPTX (native SVG) from one source.
   **No screenshots** except genuine WebGL widgets (rasterize at high DPI).
3. **Server-side charts via echarts SSR** (`renderToSVGString`, Node, no browser)
   so scheduled reports need no headless Chromium. Chromium `page.pdf()` stays an
   OPTIONAL pixel-exact fallback only.
4. **PDF = SVG→PDF via cairosvg/svglib** (vector, selectable text). **PPTX =
   native SVG embed via python-pptx** (+ PNG fallback for old clients).
5. **Reports are a Flows orchestration** composing dashboarding primitives:
   snapshot (data) → render (SVG→PDF/PPT/HTML) → deliver (`app/notify`). Converge
   the existing `app/jobs/report.py` onto a Flows `report_send` task.
6. **Per-board/per-widget export-layout config** (include flag, order,
   page/slide break, caption, title slide, header/footer) — shared across PDF/PPT.

---

## Wave 1 — Foundation + Export engine + Reports (backend-heavy, fully testable)

> Delivers: export ANY dashboard to vector PDF/PPT + scheduled report sending,
> without the new editor. The editor (Wave 2) only adds bespoke report/slide layouts.

- **T1 — Schema split (GATE, serial).** Lift widget positions into
  `surfaces.grid`; migrate existing boards 1:1 (read new location, fall back to
  legacy inline for un-migrated). Backend `app/dashboards/spec.py` + frontend
  `src/editor` read path. **Migration + parity tests; full existing
  dashboard/editor/spec suites must stay green.** No grid behavior change.
- **T2 — Server-side SVG render.** Node echarts-SSR worker (widget+data → SVG)
  + Python glue + an SVG page/slide composer (place widget-SVGs + text boxes in a
  coordinate space). WebGL widgets → high-DPI PNG fallback.
- **T3 — PDF renderer.** Composed SVG → PDF via cairosvg/svglib (vector,
  selectable, paginated via `@page`-equivalent breaks).
- **T4 — PPTX renderer.** Slides-surface (or auto-grid) → python-pptx, native
  SVG embed + PNG fallback, one widget/group per slide.
- **T5 — Export-layout config.** Schema + defaults for the per-board/per-widget
  report/presentation hints; minimal inspector hooks.
- **T6 — Report sending → Flows.** Converge `app/jobs/report.py` onto a
  `report_send` Flows task; reuse T2–T4 renderers + `app/notify` delivery;
  per-recipient RLS; schedulable (daily/cron).
- **T7 — Export menu + docs.** Wire HTML/PDF/PPT/CSV/public into one Export &
  Share surface fed by the snapshot; document the modes (unsafe ones loud).

## Wave 2 — Unified editor surfaces (frontend, visual iteration)

> Sequenced AFTER T1 lands & is verified. Needs visual verification — done as a
> focused, screenshot-driven follow-up, not blind autonomy.

- **T8 — Editor shell + surface switch** (Dashboard | Report | Presentation),
  shared chrome (palette, inspector, insert ribbon, theme = master).
- **T9 — `<SlideCanvas>` + slides rail** (PowerPoint-familiar: thumbnail rail,
  16:9 fixed canvas, absolute drag-resize, speaker notes, present mode).
- **T10 — `<DocCanvas>` + pages rail** (A4 paginated flow, page breaks).
- **T11 — Conversions** (dashboard→slides, dashboard→report generators).
- **T12 — Present mode** (full-screen, keyboard nav) + live data-bound widgets.

---

## Wedge guardrail (non-negotiable architecture invariant)

The wedge = **marginal cost per dashboard view ≈ $0** (browser computes via
DuckDB-WASM). Every feature here must preserve it:

- Server-side render (echarts SSR) fires **only** on explicit export / report /
  snapshot actions — **never** on a normal dashboard view.
- Frozen artifacts (snapshot / public / CDN) are **browser-rendered** — the
  viewer loads the sidecar `.duckdb` via DuckDB-WASM, so per-view stays ~$0.
- Snapshots & reports are **pay-once-per-refresh, not per-viewer** — a
  daily-refreshed public board with 1M viewers = 1 server render/day.

Wave-1 verify must confirm no server render was added to the view path.

## Wave 1.5 — Billing fit + Docs + Landing (after Wave 1 lands)

Map the new server-side actions to EXISTING COGS lines — no per-seat/per-view tax:

| Action | Billable metric |
|---|---|
| Snapshot storage (sidecar `.duckdb`) | storage GB |
| Snapshot/report query | bytes-scanned (`scan_zar_per_tib`) |
| PDF/PPT render · scheduled report run | compute units / flow run (wallet) |
| Public/CDN static file | storage + bandwidth (per-view ≈ free) |
| Live embed view | `embedded_sessions` (already metered) |

- **B1 — Billing model.** Add included quotas (exports/report-sends per tier,
  snapshot storage) + overage wiring in `backend/app/ee/billing/tiers.py` and the
  calculator (`src/lib/pricing.js`). Keep ≥75% margins; viewers stay free.
  Re-run the billing test suites — do NOT reintroduce the calculator/backend drift
  just fixed.
- **B2 — Docs.** Architecture page: the wedge + embedding modes + snapshots +
  reports + the pay-once-per-refresh economics.
- **B3 — Landing.** A dedicated feature/architecture page (e.g. `/embedding` or
  `/reporting`) + a section on the main landing; update the compare page.
  **Visual iteration required** (render → screenshot → critique).

## Metering principle (the billing split that makes the wedge real)

- **Client-computed data is NOT metered.** Read-only / demo / cacheable data is
  downloaded once and queried by DuckDB-WASM in the browser → zero server compute
  → genuinely free, unmetered.
- **Server-computed data IS metered.** Live private warehouses can't ship to the
  browser, so that compute stays server-side and bills (compute/scan).
- Clean story: **Free tier = read-only/demo/cacheable data computed in the
  browser; paid = live private data on the server.**

> HONEST CAVEAT (do not market until wired): today dashboards/widgets `POST
> /api/v1/query` and compute **server-side** — even the demo lakehouse. Browser
> DuckDB-WASM (`wasmRuntime.js: initDuckDB/queryLocal`) exists but only for
> last-mile cross-cell SQL; it does NOT yet download parquet and compute in the
> browser. The "free in-browser demo" claim depends on **D2 below** being shipped.

## Wave 2.5 — Demo-as-file + browser-compute (realizes the free wedge)

- **D1 — Demo = lakehouse only.** Drop the in-memory virtual connector;
  consolidate demo data to static read-only parquet (the lakehouse). (Approved.)
- **D2 — Read-only demo computes in the browser.** Serve the demo parquet to the
  client (CDN / signed URL); route the demo query path through `queryLocal`
  (DuckDB-WASM `read_parquet`) instead of the server `/query`. Result: zero
  server compute on demo → truly free/unmetered. This is what unlocks the wedge
  claim and gates the B3 marketing copy.
- **D3 — Connector creation: demo or blank.** New-connector flow can seed a
  read-only **demo-data** connector (a copied file the client can use + edit) OR
  start **blank**.
- **D4 — New-project seeding.** Creating a project can seed the full demo —
  connector + queries + dashboards — as a starter template (the demo file is
  copied to the client to use and edit).
- **D5 — Metering split wiring.** Mark client-computed/demo/read-only paths
  unmetered; only server-side compute counts toward quota/wallet. Reflect in
  `tiers.py` + the calculator (free demo never bills).

> Dependency: **B3 (marketing page) must not claim "free in-browser demo" until
> D2 ships.** Sequence D1→D2 before/with Wave 1.5's landing copy.

## Verification gate (every wave)

- Existing dashboard / editor / spec / flows suites stay **green** (no degradation).
- New export/report tests pass; `npm run test:dash` green.
- Security spot-check: org-scoping, RLS-at-capture, public-export gating.
- Visual check on the dashboard (Wave 1) and the new surfaces (Wave 2).
