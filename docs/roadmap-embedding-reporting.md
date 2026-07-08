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

## ✅ Already shipped

- **Mode 2 — id-based connector override.** Host-signed `datastore` claim →
  whole-dashboard connector override, org-scoped, RLS untouched. (`query.py`,
  `auth/verify.py`, `routes/embed.py`)
- **Mode 3a — frozen DuckDB snapshot + scheduled refresh.** Sidecar `.duckdb`,
  `snapshot_refresh` Flows task. (`app/embedding/snapshot.py`, `routes/snapshot.py`)
- **Mode 3b — gated public/CDN static export.** Double-gated (kill switch +
  per-org flag), audit-logged, loud UNSAFE banner. (`app/embedding/public_export.py`)
- **Shared:** `collect_board_data()` (`app/dashboards/collect.py`), currency
  selector, pricing-calculator fairness fixes, LiteLLM provider unification.
- **Wave 1 (T1–T7):** Schema split, SVG export engine (echarts SSR), PDF renderer, PPTX renderer, export-layout config, `report_send` Flows task, Export & Share menu — all shipped. See Wave 1 section below.
- **Wave 2 (T8–T12):** Unified editor (EditorShell), DocCanvas (report), SlideCanvas (presentation + present mode), surface generators — all shipped. See Wave 2 section below.

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

## Wave 1 — Foundation + Export engine + Reports ✅ Shipped

- **T1 — Schema split.** ✅ `app/dashboards/spec.py`: `surfaces.{grid,report,slides}` live; `migrate_spec_to_surfaces` + backward-compatible accessor; parity tests green.
- **T2 — Server-side SVG render.** ✅ `app/dashboards/svg_render.py` + `scripts/render/echarts-ssr.mjs` + `scripts/render/svg-composer.mjs`.
- **T3 — PDF renderer.** ✅ `app/embedding/render_pdf.py` — cairosvg (preferred) / svglib+reportlab fallback; vector PDF with selectable text.
- **T4 — PPTX renderer.** ✅ `app/embedding/render_pptx.py` — python-pptx with native SVG + PNG fallback per slide.
- **T5 — Export-layout config.** ✅ `app/dashboards/spec.py::ExportConfig` / `get_export_config` — page size, header/footer, title slide, per-widget hints.
- **T6 — Report sending → Flows.** ✅ `app/flows/handlers/report_send.py` — `report_send` task kind; per-recipient RLS; Slack/Teams notify channels.
- **T7 — Export menu + docs.** ✅ `src/components/ExportShareMenu.jsx`; `GET /boards/{id}/export.{csv,json,pdf,pptx}` and `POST /boards/{id}/export/public`.

## Wave 2 — Unified editor surfaces (frontend, visual iteration) ✅ Shipped

- **T8 — Editor shell + surface switch** ✅ `src/editor/EditorShell.jsx` — Dashboard | Report | Presentation tabs, shared chrome, wired into `EditorPage` via `/editor` route.
- **T9 — `<SlideCanvas>` + slides rail** ✅ `src/editor/SlideCanvas.jsx` — thumbnail rail, 16:9 fixed canvas, absolute drag-resize, speaker notes, present mode (F5/Ctrl+Shift+P, Esc to exit), keyboard nav.
- **T10 — `<DocCanvas>` + pages rail** ✅ `src/editor/DocCanvas.jsx` — A4/Letter paginated flow, page breaks, export layout hints (header/footer/title slide) rendered visually.
- **T11 — Conversions** ✅ `src/dashboards/surfaceGenerators.js` — `gridSpecToSlides` / `gridSpecToReport` auto-generate slides or doc pages from the grid layout.
- **T12 — Present mode** ✅ Full-screen overlay in `SlideCanvas`, arrow-key nav, Esc to exit, live data-bound widgets on each slide.

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

## Wave 2.5 — Demo-as-file + browser-compute ✅ Shipped

- **D1 — Demo = browser-WASM only.** ✅ Demo data is static Parquet served at `GET /api/v1/demo-parquet/*`. The `__demo__` virtual connector points at these files. `app/sample.py` seeds new projects with demo data via `provision_demo_parquet`.
- **D2 — Read-only demo computes in the browser.** ✅ `src/lib/wasmRuntime.js::runArrowQueryById` transparently routes demo queries (those in the demo query map) through DuckDB-WASM `read_parquet` — zero `POST /api/v1/query` calls for demo data. The "free in-browser demo" wedge is live.
- **D3 — Connector creation: demo or blank.** ✅ New-connector flow includes the `__demo__` option.
- **D4 — New-project seeding.** ✅ `app/sample.py::provision_demo_parquet` seeds connector + queries + dashboards as a starter template.
- **D5 — Metering split wiring.** ✅ Demo/read-only paths are explicitly unmetered in `app/ee/billing/tiers.py`.

## Verification gate (every wave)

- Existing dashboard / editor / spec / flows suites stay **green** (no degradation).
- New export/report tests pass; `npm run test:dash` green.
- Security spot-check: org-scoping, RLS-at-capture, public-export gating.
- Visual check on the dashboard (Wave 1) and the new surfaces (Wave 2).
