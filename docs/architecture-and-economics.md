# Architecture & Economics

This document explains the structural decisions behind Nubi's compute model,
embedding modes, demo/read-only path, export and reporting pipeline, and
billing — with an emphasis on *why* each design choice maps to an existing cost
line and how the "viewers are free" wedge is achieved without any accounting
tricks.

Related docs:
- [Embedding](embedding.md) — JWT issuers, RLS, embed component
- [Exports & scheduled reports](exports-and-jobs.md) — export endpoints and
  report flow tasks
- [Embedding & reporting roadmap](roadmap-embedding-reporting.md) — shipped
  features and design decisions

---

## The wedge: browser-side compute as the default

Dashboard **view** queries run in the visitor's browser using DuckDB-WASM.
The Nubi server never runs a query on behalf of a dashboard viewer. The browser
receives Arrow IPC data (or reads a frozen `.duckdb` sidecar) and executes the
DuckDB kernel locally.

This means:

- **Marginal COGS per dashboard view ≈ R0.** No server scan, no server compute,
  no billed egress per view.
- **Viewer seats are permanently free at every tier.** Adding 1,000 viewers
  costs Nubi one extra DB row and one auth check per session (~R0.001/user/month).
  There is no per-seat pricing at any tier.
- **Server compute only fires on events that have a real COGS line**: snapshot
  refreshes, export renders, scheduled report sends, and live private warehouse
  queries.

The billing model is a direct consequence: Nubi meters *what the server does*
(storage written, bytes scanned, compute seconds consumed), not who views the
result.

---

## Embedding modes

### Mode 1 — live private warehouse (server-side, metered)

The default path for private data. The `<nubi-dashboard>` component sends the
viewer's host-signed embed JWT to `/api/v1/query`. The backend verifies the
signature, injects the `policies` dict as AST-level `WHERE` predicates, and
executes the query against the live data warehouse. Results stream back as Arrow
IPC.

Billing: bytes scanned against the warehouse hit the `scan_zar_per_tib` meter
(first 1 TiB/month free, then R83/TiB at the reference rate). This is the only
mode that generates ongoing server compute cost per query execution.

### Mode 2 — id-based connector override

A host-signed `datastore` claim in the embed JWT overrides the whole-board data
connector for that embed session. The override is org-scoped and RLS is
unchanged. Implemented in `app/routes/embed.py` and `app/auth/verify.py`.

Billing: same as Mode 1 — any query that runs server-side is metered normally.
The override does not change the billing model; it changes *which* data source
the query runs against.

### Mode 3a — frozen DuckDB snapshot + scheduled refresh

`app/embedding/snapshot.py` — `create_snapshot` / `refresh_snapshot`

A snapshot captures the rows behind every data widget on a board at a point in
time and writes them as a single sidecar artifact: a `.duckdb` file stored on
object storage (Cloudflare R2 in production, a local `file://` path in
development). The artifact path is:

```
<base>/snapshots/<org_id>/<board_id>/<snapshot_id>.duckdb
```

The file contains one `widget_<id>` table per data widget plus a
`_nubi_snapshot_meta` row for introspection. An embed pointing at the frozen
sidecar renders entirely in the browser — no live warehouse connection.

RLS is frozen at capture time under the `policies` from the capturing token.
Every holder of the artifact sees the same rows. For multi-tenant data, capture
one snapshot per policy view and distribute each artifact only to entitled
holders. The captured policy fingerprint is recorded in the metadata.

Scheduled refresh is a `snapshot_refresh` Flows task kind
(`register_snapshot_refresh_task`). The daily/cron tick re-runs
`collect_board_data`, rewrites the same artifact URI in place, and bumps
`refreshed_at`. The task config carries the `policies` dict because a cron tick
has no live JWT.

Billing for each snapshot create/refresh:
- **Storage**: the `.duckdb` sidecar written to Cloudflare R2 →
  `storage_zar_per_gb_month` (R0.33/GB; COGS ~R0.24/GB at R2 parity).
- **Scan**: `collect_board_data` executes the board's widget queries against
  the live warehouse → `scan_zar_per_tib` (same bytes-scanned meter as any
  live query).

Viewing the board against the frozen snapshot is free — DuckDB-WASM reads the
sidecar in the browser.

### Mode 3b — gated public/CDN static export (UNSAFE, opt-in)

`app/embedding/public_export.py`

Produces a self-contained static HTML file that loads the Mode 3a snapshot
sidecar from a public URL and queries it client-side with DuckDB-WASM. The
exported artifact is **public with no authentication and no expiry**. This is
made explicit with a loud UNSAFE banner rendered in the HTML and repeated as an
HTML comment, `<meta>` tag, and WARNING-level log entry.

Two interlocks must both be satisfied before any bytes are written:

1. `ALLOW_UNSAFE_PUBLIC_EXPORTS` deployment-wide env var is `True` (defaults
   `False`).
2. The org holds the `public_exports` feature gate.

The HTML embeds the board spec and the public URL of the snapshot sidecar, then
loads DuckDB-WASM from a CDN-pinned bundle
(`@duckdb/duckdb-wasm@1.28.0` via jsDelivr) — no live backend after the
initial export.

Billing for the export event:
- **Storage**: the HTML artifact + the snapshot sidecar written to R2 →
  `storage_zar_per_gb_month`.
- **CDN egress**: each download of the public HTML or sidecar is treated as an
  embedded session event → `embedded_session_zar_per_10k` (R50/10K sessions).

Viewers consume no server compute — the browser runs DuckDB-WASM.

---

## Demo-as-file: read-only data at zero server cost

Nubi ships a built-in demo dataset (retail sales, ~17 tables) as Parquet files
served at `GET /api/v1/demo-parquet/*`.

Routes:

| Endpoint | Auth | Purpose |
|---|---|---|
| `GET /api/v1/demo-parquet/_manifest` | None | Table→URL map for all 17 tables |
| `GET /api/v1/demo-parquet/{dataset}/{table}.parquet` | None | Raw parquet bytes |
| `GET /api/v1/demo-parquet/_query-map` | Bearer token | `{query_uuid: sql}` for demo-connector-backed queries |

The browser fetches the manifest once, registers DuckDB-WASM views via
`read_parquet('<url>')`, and executes all queries locally. **Zero `/query`
calls are made.** The server only serves static bytes.

This covers three entry points:
- **Connector demo / blank connector**: the `__demo__` virtual connector type
  points at these parquet files. Any dashboard wired to it computes in the
  browser.
- **New-project demo seeding**: `app/sample.py` seeds a new project with the
  demo dataset and demo dashboards (`provision_demo_parquet` /
  `export_demo_parquet_local`).
- **Demo bundles**: `app/demo_bundle.py` exports parquet locally or to S3 for
  the seeding path.

Path-traversal protection: `dataset` and `table` are validated against a closed
allowlist (`DATASET_TABLES`) in `app/routes/demo_parquet.py`; the file path is
never constructed from raw user input.

Billing: none. Demo data is public, read-only, and client-computed. It maps to
zero COGS lines. It is explicitly listed in `tiers.py` under "NOT metered".

---

## Reports and exports pipeline

The pipeline is: **one snapshot → SVG (echarts SSR) → {PDF, PPTX} → Flows
delivery**.

### SVG rendering (T2)

`app/dashboards/svg_render.py` — per-widget SVG via server-side echarts SSR
(`scripts/render/echarts-ssr.mjs`, Node.js, no browser). Widgets are composed
into a full-page SVG by `scripts/render/svg-composer.mjs`. The T5 export config
(`app/dashboards/spec.get_export_config`) drives page size (A4/Letter/16:9),
header/footer, title slide, and per-widget hints.

No Chromium is required. Chromium `page.pdf()` is an optional pixel-exact
fallback only.

### PDF export (T3)

`app/embedding/render_pdf.py` — `render_board_pdf(svg_pages, export_cfg)`

Accepts the composed SVG page list and renders a vector `%PDF-1.x` document.
Backends tried in order:

1. **cairosvg** — preferred; produces vector PDF with selectable text. Requires
   the `cairo` system library.
2. **svglib + reportlab** — pure-Python fallback; text remains selectable in
   most viewers.

Header/footer text and optional title slide are drawn in pure PDF operations
(no rasterization). Page sizes: A4, Letter, 16:9.

### PPTX export (T4)

`app/embedding/render_pptx.py` — `render_board_pptx` /
`render_board_pptx_from_data`

Uses `python-pptx` to insert each widget SVG as a native picture shape on a
blank slide layout. A PNG raster fallback (via cairosvg) is embedded for
compatibility with older clients. The T5 export config drives title slide,
caption, per-widget include/exclude, and `page_break_before` behaviour.

### Scheduled report sends

`app/flows/handlers/report_send.py` — `handle(config, ctx, claims)`

Registered as a Flows task kind `report_send`. A daily/cron flow config
specifies `board_id`, `format` (csv/pdf/pptx), `recipients`, `subject`, and an
optional `locked_params` map for per-recipient RLS. The captured `policies` dict
in the task config carries the RLS view (no live JWT at tick time).

For each tick:
1. The board is resolved org-scoped.
2. The report is rendered via `_render_pdf` / `_render_pptx` / `render_report`.
3. The rendered bytes are sent to all recipients via the configured email sender.
4. Optional notify channels (Slack/Teams webhooks) are triggered best-effort.

Per-recipient RLS: when `apply_user_permissions: true` + `locked_params` are
set, one render+send is issued per recipient with their locked params injected.
Otherwise a single render is delivered to all recipients.

### Pay-once-per-refresh, not per-viewer

The key economic property: a snapshot is computed once (on refresh) and read N
times at zero marginal cost. A scheduled report is rendered once and emailed to
all recipients. The cost is proportional to the number of refreshes/renders, not
to the audience size.

---

## Billing: every server action maps to an existing COGS line

No new meters have been introduced for any of the actions above. All server
actions map to existing COGS dimensions defined in
`app/ee/billing/tiers.py`:

| Server action | File | COGS line |
|---|---|---|
| Snapshot create/refresh | `app/embedding/snapshot.py` | `storage_zar_per_gb_month` (sidecar `.duckdb` written to R2) + `scan_zar_per_tib` (collect_board_data widget queries) |
| PDF export render | `app/embedding/render_pdf.py` | `compute_zar_per_1000_cu` (echarts SSR + cairosvg/svglib container CPU) |
| PPTX export render | `app/embedding/render_pptx.py` | `compute_zar_per_1000_cu` (echarts SSR + python-pptx container CPU) |
| Scheduled report send | `app/flows/handlers/report_send.py` | `compute_zar_per_1000_cu` (render run + Flows delivery) |
| Public/CDN static export | `app/embedding/public_export.py` | `storage_zar_per_gb_month` (R2 HTML + sidecar) + `embedded_session_zar_per_10k` (CDN egress) |

**Never metered** (zero marginal COGS, confirmed in `tiers.py`):

- Viewer seats (all tiers): viewing a pre-computed or frozen dashboard
  generates no server scan, no server compute, no billed egress.
- Demo / read-only / client-computed views: DuckDB-WASM runs in the browser.
- Connector count, dashboard count, saved query count, flow definition count:
  each is one DB row (~R0.001/month COGS).

### Overage rates (reference, ZAR-denominated)

All ZAR amounts are derived as `ceil_to_nearest_10(usd * rate * 1.02)` from a
USD anchor, using the daily-refreshed FX rate. Reference amounts below are
the June 2026 baseline at R16.26/USD + 2% buffer.

| Dimension | Rate | COGS basis | Gross margin |
|---|---|---|---|
| `storage_zar_per_gb_month` | R0.33/GB | Cloudflare R2 (~R0.24/GB) | ~27% |
| `scan_zar_per_tib` | R83/TiB (~$5/TiB) | DuckDB CPU + R2 egress (~R15/TiB; PENDING benchmark) | ~82% |
| `compute_zar_per_1000_cu` | R100/1,000 CU | Container/DuckDB compute | ~77% |
| `ai_call_zar_per_call` | R5/call | Anthropic API tokens | ~93% |
| `embedded_session_zar_per_10k` | R50/10K | Egress + CDN compute | ~99% |
| `agent_run_zar_per_run` | R2/run | Remote kernel compute | ~99% |

The first 1 TiB of bytes scanned per org per month is free (matching BigQuery's
free tier). There is no per-seat overage at any tier.

### Gross margins by tier

All paid tiers achieve ≥75% gross margin. The tier floor is 70%; all tiers meet
the 75% target:

| Tier | USD/mo | ZAR/mo (ref) | Total COGS | Gross margin |
|---|---|---|---|---|
| Starter | $9 | R150 | R20.12 | 86.6% |
| Team | $49 | R820 | R117.96 | 85.6% |
| Pro | $149 | R2,480 | R504.57 | 79.7% |
| Enterprise | $1,000 | R16,590 | R4,065.72 | 75.5% |

Enterprise COGS includes SLA monitoring and on-call overhead (~R700/org/month)
and a dedicated CSM allocation (~R700/org/month) on top of hosted infra.

### Currency and FX disclosure

Prices are anchored in USD and converted to ZAR at billing time using a
daily-refreshed FX rate (see `app/ee/billing/fx.py`). The ZAR amount may vary
slightly between billing cycles as the rate moves; the USD anchor is fixed for
the duration of the plan. The customer-facing disclosure copy is defined in
`tiers.py` as `ZAR_DISCLOSURE_COPY`.

---

## What is not yet shipped (roadmap only)

The following items appear in `docs/roadmap-embedding-reporting.md` but are
**not yet implemented**:

- Unified Dashboard / Report / Presentation surfaces in a single editor UI
  (the `board.surfaces.{grid,report,slides}` schema split is designed but not
  deployed).
- The T2 echarts-SSR pipeline is referenced in the design but the
  `scripts/render/echarts-ssr.mjs` Node subprocess integration with the export
  route is not fully wired end-to-end in production; `render_pdf.py` and
  `render_pptx.py` exist and are exercised via `report_send.py`, but the
  full T2→T3/T4 composed SVG path depends on the Node SSR script being present
  in the deployment container.

Do not rely on this document as a feature-completeness guarantee for those
items; refer to the roadmap doc and the test suite.
