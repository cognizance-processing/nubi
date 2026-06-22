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

## Semantic layer + smart engine (Bet 1 + Bet 2)

### Semantic layer enrichment

The metrics compiler (`backend/app/metrics/compile.py`) translates a governed
`MetricDefinition` + `MetricQuery` into SQL via two paths:

**Flat path** — when a query needs no time-intelligence transforms and the metric
declares no derived measures, the compiler emits a single `SELECT … GROUP BY`
(identical to the pre-Wave-1 behaviour; byte-stable, pre-agg-routable).

**Layered path** — when either is present the compiler emits a two-level CTE:

```sql
WITH __base AS (
    SELECT <dims>, DATE_TRUNC('<grain>', <time_col>) AS <time_alias>,
           <AGG(expr)> AS <measure>, ...
    FROM   <table | (base_sql) AS base>
    WHERE  <default_filters> AND <user_param_filters>
    GROUP BY <dims>, <time_bucket>
)
SELECT <dims>, <time_alias>,
       <base_measures passthrough>,
       <formula / NULLIF(denom, 0)> AS <derived_measure>,
       LAG(<m>, N) OVER (PARTITION BY <non-time dims> ORDER BY <time_alias>)
           AS <prior_period | yoy_*>,
       SUM(<m>) OVER (PARTITION BY <dims>, DATE_TRUNC('year', <time_alias>)
                      ORDER BY <time_alias>
                      ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS ytd_m,
       ...
FROM __base
[QUALIFY RANK() OVER (...) <= N]
```

This structure is the key to both **governance** and **pre-agg compatibility**:
the `__base` CTE always contains only additive base measures; derived formulas
and window functions are applied in the outer SELECT over `__base`, not over
the raw table. Pre-agg rollups can therefore serve the `__base` layer, and the
outer transforms are recomputed on top.

**RLS soundness on the layered path**: every `rls_keys` column declared in the
metric is projected through `__base` into the outer SELECT so the planner's
injected `WHERE col = claim` predicate always lands on a real column. The
compiler raises `MetricError(rls_not_projectable)` and refuses to emit SQL
if this invariant cannot be satisfied — fail-closed, never a data leak.

Capabilities added in Wave 1:

| Feature | Mechanism |
|---|---|
| Derived / ratio measures | `DerivedMeasure(formula="delivered / ordered")` — division auto-guarded with `NULLIF(denom, 0)` |
| Period-over-period | `TimeComparison(kind="prior_period" / "pop_abs" / "pop_pct", periods=N)` — LAG window |
| Year-over-year | `TimeComparison(kind="yoy_abs" / "yoy_pct")` — LAG offset from `YEAR_LAG_BY_GRAIN` |
| YTD / QTD / MTD | `TimeComparison(kind="ytd" / "qtd" / "mtd")` — running-sum window, UNBOUNDED PRECEDING |
| Rolling window | `TimeComparison(kind="rolling_sum" / "rolling_avg", periods=N)` — N-row trailing window |
| Latest snapshot | `TimeComparison(kind="latest_snapshot", measure="<entity_col>")` — QUALIFY ROW_NUMBER() OVER (PARTITION BY entity ORDER BY time DESC) = 1 dedup before aggregation |
| Dynamic top-N | `TopN(dimension, n, measure, other=True)` — QUALIFY RANK() or UNION with an "Other" rollup bucket |
| Percentile | `Measure(agg="percentile_cont", format="p95")` — `PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY col)` |
| Approx distinct | `Measure(agg="approx_count_distinct")` — `APPROX_COUNT_DISTINCT(col)` |

All user filter values are bound as named parameters — never concatenated into
SQL. `default_filters` (author-trusted) are inlined via sqlglot AST, not string
concatenation.

### Smart engine: pre-agg rollups for derived and windowed metrics

`backend/app/connectors/preagg.py` ships two builder functions:

- `build_rollup(candidate, *, rls_keys, …)` — materializes a rollup from a
  mined `RollupCandidate`, registers it in the `RollupRegistry`, and exposes
  it as a runtime query.
- `build_rollup_for_metric(metric, grains, …)` — metric-driven builder.
  Decomposes the `MetricDefinition` to extract additive base measures
  (SUM/COUNT/MIN/MAX — AVG, percentile, and approx_count_distinct are
  skipped as non-re-aggregable), all declared dimensions, and the raw time
  column. The resulting rollup shape exactly mirrors what `__base` aggregates,
  so the planner router can serve both flat queries and the `__base` layer of
  layered metric queries from the rollup.

The router (`planner.route_to_rollup`) only rewrites when it can prove soundness
from the parsed shape: same base table, query group-by ⊆ rollup dims, every
requested measure derivable from rollup aggregates, every filter column present
in the rollup. Anything it cannot prove sound is left untouched — there is no
cost-based guess.

### Per-board query fusion and shared cache key

`backend/app/dashboards/board_data.py` implements the `DataProvider` resolver
that serves composite board data:

- **Query fusion**: a `DataProvider` declares multiple result queries that share
  a `base_cte`. The resolver runs them in one round-trip, collects results by
  `result.name`, and returns an `{el_id: rows}` map.
- **Shared cache key**: `(provider_id, frozen_params, rls_hash)` — the
  `rls_hash` is a SHA-256 digest of the full `policies` dict, so tenants never
  share a cache entry even for structurally identical queries.

### Economics of the smart engine

The wedge holds at every tier:

- **Dashboard view**: the browser runs DuckDB-WASM against the Arrow IPC
  result. **Zero server compute per view.**
- **Rollup build** (one-time or scheduled): `compute_zar_per_1000_cu`.
- **Snapshot refresh** (periodic): `scan_zar_per_tib` + `storage_zar_per_gb_month`.
- **Viewer seats**: permanently free at every tier. A rollup built once and
  cached serves N viewers at marginal cost ≈ R0 per view.

The smart engine is exactly the mechanism that makes "viewers are free" scale:
instead of scanning the raw fact table on every dashboard load, a once-built
rollup is read by the browser. The billing model meters the server's work
(rollup builds, snapshot refreshes), not the audience size.

---

## Flows as a data-app engine (Axis B)

Flows is the compute/orchestration layer. Wave 2 extends it to serve the
data-app pattern (compute a decision → write it back → re-trigger on result).

### Per-cell compute resources

`TaskSpec` in `backend/app/flows/spec.py` carries three resource fields:

| Field | Meaning |
|---|---|
| `cpu_cores` | Fractional CPU cores (e.g. `0.5`). Forwarded to the remote kernel; clamped for the local runner. `0` = provider default. |
| `mem_mb` | Memory in MiB. Same forwarding / clamping behaviour. |
| `timeout_s` | Per-attempt timeout in seconds. `0` = no timeout. |

The local runner enforces these via POSIX `rlimit` + process-group SIGKILL on
timeout. The remote kernel tier (E2B / Modal Firecracker microVM) receives the
resource hints and enforces them at the microVM level — the primitives and
interface are in place; cloud provisioning is controlled by the provider's
platform, not provisioned directly by Nubi.

A `map` task (fan-out sub-DAG) carries an additional `map_concurrency` cap that
limits how many concurrent child tasks run in parallel (`backend/app/flows/for_each.py`).

### Run lineage and reproducibility

Every flow run is a row in `flow_runs` carrying:

- `run_id` — UUID; stable across retries of the same logical run.
- `seed` — derived from `run_id` by convention (`int(run_id_hex[:8], 16) & 0x7FFFFFFF`). Injected into stochastic cells so Monte-Carlo results are reproducible across retries of the same run but differ across runs.
- `code_version` — snapshot of the flow spec at run time (free-form dict; useful for diff-ing runs on different spec versions).
- `params_snapshot` — the parameter values the run was invoked with.
- `flow_run_outputs` — lineage index linking `run_id` → task outputs (used by sweep/backfill to group related runs).

`TaskSpec.stochastic = True` bypasses the result cache for that cell, ensuring
stale recommendations from a prior run do not persist. The run-level `seed` is
still injected so within-run retries are deterministic.

### Typed artifact channel

`backend/app/flows/artifacts.py` — the `ArtifactHandle` / `ArtifactStore` system:

- An `ArtifactHandle` is a lightweight JSON-serialisable descriptor (`artifact_id`, `kind`, `uri`, `org_id`, `produced_by_run`). It crosses cell boundaries through the existing rows/JSONB channel.
- `ArtifactKind` ∈ `pickle | joblib | bytes | json`. Cells that produce models or large blobs call `ctx.put_artifact(obj, kind)` and return the handle; downstream cells call `ctx.get_artifact(handle)` to deserialize.
- Artifacts are namespaced under `orgs/<org_id>/` in the object store (file://, s3://, gs://, az://) so cross-tenant access is structurally impossible. `get_artifact` enforces `org_id` match at deserialization time.
- `InMemoryArtifactStore` — used by tests (no I/O). `ObjectStoreArtifactStore` — writes blobs to `ARTIFACTS_BASE_URI`.

### Scenario sweep and backfill

`backend/app/flows/sweep.py`:

- `run_sweep(store, flow, param_sets, …)` — runs the flow over N param sets (the matrix). Each cell is a full flow run with its own `run_id`, `params_snapshot`, and `seed`. Failed cells are recorded but do not abort the matrix. `diff_surface()` returns `{index, params, outputs}` per successful cell.
- Accepts a `grid` dict (`{name: [values]}`) and expands it to the Cartesian product automatically.
- `run_backfill(store, flow, start, end, window, …)` — re-runs the flow over a date range. Each window is a full run; stored watermarks are respected for incremental-aware flows.
- Both runners set `trigger='sweep'` / `trigger='backfill'` and link cells back to the sweep/backfill request via `params.__sweep_id__` / `params.__backfill_id__`.

### Event / webhook / downstream triggers

`backend/app/flows/triggers.py`:

- Three trigger kinds: `event` (internal event key), `webhook` (external HTTP, optional HMAC secret), `downstream` (fires when a named upstream flow completes).
- `fire_event(event_key, payload, org_id, …)` — fires all matching triggers and returns the `run_ids` created.
- `on_flow_run_complete(…)` — completion hook called by the engine on every terminal state. Fires downstream triggers, idempotent (guarded by `__upstream_run_id__` in run params).
- `flag_sla_breach(flow_run, expected_s, now)` — SLA helper: returns `True` if the run exceeded the expected duration.
- Run-history is queryable; SLA breaches are surfaced in the ops UI.

### Governed write-back

`backend/app/connectors/writeback.py` — idempotent, dry-run, RBAC, approval gates:

**State machine**:
```
pending_approval  ──approve/edit──►  committed  ──(error)──►  failed
        └──reject──►  rejected
When approval_required=False:   submitted ──► committed (or failed)
```

**Key capabilities**:

| Feature | Detail |
|---|---|
| Dry-run | `POST /flows/writeback/preview` returns the rows + diff without touching the connector |
| Idempotency | `idempotency_key` (caller-supplied UUID / `flow_run_id+task_key`) — a retry never double-applies |
| RBAC | Writers: `owner / admin / member`. Approvers: `owner / admin`. `viewer` always denied |
| Approval gate | `approval_required=True` holds the write in `pending_approval`; only an approver can commit |
| Transactional | The connector_write result is passed pre-computed; the apply step either fully commits or fails (no partial writes) |
| Audit | Every record carries `org_id`, `state`, `committed_at`, the rows written, and the approving claims |

Cross-org isolation: every record is org-scoped; a lookup with the wrong `org_id` returns not-found (no information leak).

---

## Canvas: HTML-native sibling to Dashboards (Axis C)

Canvas is a first-class document type (`canvases` resource, `config.doc`) whose
source of truth is HTML the author (human or LLM) writes directly.

### What it adds

| Capability | Detail |
|---|---|
| Free-form HTML surface | Any semantic HTML; data comes alive through `<nubi-*>` custom elements and `{{token}}` interpolation |
| Side-binding map | `bindings: {el_id → CanvasBinding}` keyed by `data-el-id` — editor mutates bindings without rewriting the HTML string |
| Three binding kinds | `query` (registered query + optional field extract), `metric` (semantic layer), `api` (HTTP_JSON connector + JSONPath select) |
| Shared data/RLS layer | Reuses `collect_board_data` / `run_query_rows` / `_resolve_connector` unchanged — RLS and org-scoping are identical |
| Scheduled sending | `report_send` flow handler generalised to dispatch on `canvas_id` OR `board_id`; per-recipient RLS, Slack/Teams notify |
| LLM generate / edit / repair | `backend/app/ai/canvas.py` — generate→validate→repair loop with `MAX_DASHBOARD_REPAIR_ROUNDS`; `POST /ai/canvas` + `POST /ai/canvas/edit` |
| Code + visual editor | `/canvas/:id` — split code/visual panes; click-to-select with RHS binding inspector |
| Public viewer | `/c/:id` — same as `/d/:id` but for Canvas; URL ↔ variable sync |

### Security model (unchanged from dashboards)

Canvas HTML passes through `sanitizeDashboardHtml` (DOMPurify allowlist) on the
client **and** through `validate_canvas_doc` (extended `validate_dashboard_html`)
on save. No `<script>`, `on*=`, or `javascript:/data:` URI survives either path.
The extended allowlist adds `nubi-metric`, `nubi-filter`, `nubi-text`,
`nubi-value` to the existing `nubi-kpi`, `nubi-table`, `nubi-chart` set.

Data flows through the same `collect.py` pipeline — per-org RLS predicates and
`source_unsupported_rls` guards apply unchanged.

### Economics

Canvas views share the same browser-compute wedge as dashboards. A Canvas bound
to a frozen snapshot renders entirely in the browser (zero server compute per
view). A Canvas bound to live queries follows the same metering path as a
dashboard widget (`scan_zar_per_tib` for the query execution; zero per view if
served from cache). Scheduled Canvas sends are metered as `compute_zar_per_1000_cu`
(render + Flows delivery), identical to `report_send` for boards.

---

## The close-the-loop architecture

Most BI tools do one thing: **display**. Nubi closes the loop: compute a
decision (Flow), show it (Dashboard or Canvas), act on it (write-back /
approval widget), write back to the source, re-trigger the next compute cycle.

```
┌─────────────────────────────────────────────────────────────────┐
│                     CLOSE-THE-LOOP CYCLE                        │
│                                                                 │
│  ┌───────────┐   writes to    ┌────────────────┐               │
│  │           │──────table────►│  Source table  │               │
│  │  FLOW     │                │  (warehouse /  │               │
│  │ (compute  │◄──re-trigger───│   DuckDB)      │               │
│  │  + decide)│  (downstream   └───────┬────────┘               │
│  └─────┬─────┘   trigger)             │ base_table / base_sql  │
│        │                              ▼                         │
│   artifacts                 ┌──────────────────┐               │
│   sweep/backfill            │  Semantic model  │               │
│        │                    │  (MetricDef:     │               │
│        │                    │   measure, dims, │               │
│        │                    │   time_intel,    │               │
│        │                    │   derived, RLS)  │               │
│        │                    └────────┬─────────┘               │
│        │                             │ compile_metric           │
│        │                             ▼                          │
│        │              ┌──────────────────────────┐             │
│        │              │  Pre-agg rollup (optional)│             │
│        │              │  build_rollup_for_metric  │             │
│        │              │  router: __base CTE aware │             │
│        │              └──────────────┬────────────┘             │
│        │                             │ Arrow IPC                │
│        │                             ▼                          │
│        │              ┌──────────────────────────┐             │
│        │              │  Dashboard / Canvas       │             │
│        │              │  (display + filter)       │             │
│        │              │  - DuckDB-WASM in browser │             │
│        │              │  - $0/view marginal cost  │             │
│        │              └──────────────┬────────────┘             │
│        │                             │ action / approval widget  │
│        │                             ▼                          │
│        │              ┌──────────────────────────┐             │
│        └─────────────►│  Write-back              │             │
│                        │  (idempotent, dry-run,   │             │
│                        │   RBAC, approval gate)   │             │
│                        └──────────────────────────┘             │
└─────────────────────────────────────────────────────────────────┘
```

**The full cycle in one sentence**: a Flow computes a decision and materialises
it to a table; the semantic model governs how that table is queried; a Dashboard
or Canvas displays the result with live cross-filtering; an action or approval
widget triggers a governed write-back; the write-back updates the source table;
a downstream trigger fires the next Flow run.

No step breaks the wedge invariant: browser-side compute handles display, server
compute is metered only for the steps that have a real COGS line.

---

## What is shipped

### Embedding, snapshots, and reporting (Wave 1 + Wave 2)

The following items from `docs/roadmap-embedding-reporting.md` are now shipped:

- **Unified Dashboard / Report / Presentation editor** — `src/editor/EditorShell.jsx`
  wraps the existing dashboard grid with a top-level surface switch. The schema
  split (`board.surfaces.{grid,report,slides}`) is live in `app/dashboards/spec.py`.
  `src/editor/DocCanvas.jsx` (paginated A4/Letter report canvas) and
  `src/editor/SlideCanvas.jsx` (16:9 slides + present mode) are full
  implementations wired into `EditorPage`. The `/editor` route uses `EditorShell`.
- **T2 echarts-SSR SVG render** — `app/dashboards/svg_render.py` + the
  `scripts/render/echarts-ssr.mjs` Node subprocess compose per-widget SVGs into
  full-page layouts for the export pipeline.
- The `render_pdf.py` and `render_pptx.py` renderers are fully wired via the T2
  SVG path and exercised by `report_send.py` and the download export endpoints
  (`GET /boards/{id}/export.pdf`, `GET /boards/{id}/export.pptx`).

### Semantic layer + smart engine + close-the-loop (Waves 1–4)

Shipped across four waves committed to `main`:

| Wave | Commits | What shipped |
|---|---|---|
| Wave 1 | `e444178`, `9c4de46` | `MetricDefinition` derived/ratio measures, time-intelligence compiler, dynamic top-N, `DataProvider` spec, consumption viz |
| Wave 2 | `6091a34` | Pre-agg routes windowed/derived metrics, `__base`-aware router, query fusion + shared cache key, per-cell compute resources + run lineage |
| Wave 3 | `4518488` | `DataProvider` resolver, Canvas resource (`backend/app/dashboards/canvas.py`), flow artifact channel |
| Wave 4 | `813ce8b` | Scenario sweep / backfill, event / webhook / downstream triggers + run-history + SLA, Canvas scheduled send + public viewer |

Do not rely on this document as a feature-completeness guarantee for all
roadmap items; refer to `docs/roadmap-embedding-reporting.md`, `docs/semantic-and-data-apps.md`, and the test
suite for the current status.
