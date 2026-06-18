# Exports & Scheduled Reports

See also [Architecture & Economics](architecture-and-economics.md) for how
each export action maps to a COGS line and why viewers are always free.

Nubi has three ways to get data out:

1. **Download exports** — CSV, JSON, high-fidelity PDF, and PowerPoint via the Export & Share toolbar.
2. **Scheduled reports** — Flows tasks that render a board on a cron schedule and deliver it by email (or other notify channel).
3. **Unsafe public exports** — frozen static HTML + DuckDB sidecar published to a CDN (opt-in, loud, auth-gated).

---

## Export & Share Endpoints

All download endpoints are **org-scoped** and require a first-party Bearer token.
The server-side paths are:

| Endpoint | Returns | Notes |
|----------|---------|-------|
| `GET /api/v1/boards/{id}/export.csv` | `text/csv` | Multi-widget CSV; `?query_id=<id>` for a single widget. |
| `GET /api/v1/boards/{id}/export.json` | `application/json` | Same data as CSV but JSON; handy for client-side SheetJS. |
| `GET /api/v1/boards/{id}/export.pdf` | `application/pdf` | High-fidelity **vector** PDF via the T2→T3 pipeline (see below). |
| `GET /api/v1/boards/{id}/export.pptx` | `application/vnd.openxmlformats-officedocument.presentationml.presentation` | Native-SVG PowerPoint via the T2→T4 pipeline (see below). |
| `POST /api/v1/boards/{id}/share` | `application/json` | Embed descriptor + RLS model + mint instructions for a host-signed embed JWT. |

The **Share** endpoint gives you everything you need to embed the board — the embed URL, a copy-paste `<nubi-dashboard>` snippet, and the exact JWT claim shape the host must RS256/ES256-sign. Nubi never mints embed tokens itself. See [Embedding](/docs/embedding) for the full embed and RLS model.

### CSV and JSON exports

`export.csv` and `export.json` resolve each widget's `query_id`, run the query server-side through the same planner path used for interactive queries (RLS predicates injected from `policies: {}` — editor view, no filtering), and stream the result. A widget whose query cannot run is emitted as an inline `# error:` comment (CSV) or an `error` key (JSON); the rest continues.

Pass `?query_id=<id>` to export only that widget's data.

### High-fidelity PDF export (`export.pdf`)

The PDF pipeline:

1. **T5 export config** — reads `spec.export` for `page_size` (A4 / Letter / 16:9), `header`, `footer`, `title_slide`, and per-widget `widget_hints`. Pass `?page_size=Letter` to override.
2. **T2 SVG render** — collects widget data server-side and renders each widget to SVG via the Node.js echarts-SSR script (`scripts/render/echarts-ssr.mjs`). Widgets are composed into a full-page SVG by `scripts/render/svg-composer.mjs`.
3. **T3 PDF render** — converts the composed SVG to a vector `%PDF-1.x` document with one page per composed SVG. Header/footer text and an optional title slide are added in pure PDF drawing operations (no rasterization, fully selectable text).

Rendering backends (tried in order, lazy-imported):
- `cairosvg` (preferred — requires the `libcairo` system library).
- `svglib + reportlab` (pure-Python fallback; no system library needed).

When neither is available the endpoint returns **503** with `{"error": {"code": "pdf_backend_missing", ...}}` and clear install instructions.

```
GET /api/v1/boards/{id}/export.pdf
GET /api/v1/boards/{id}/export.pdf?page_size=Letter
```

### High-fidelity PPTX export (`export.pptx`)

The PPTX pipeline:

1. Same T5 export config and T2 SVG render as above.
2. **T4 PPTX render** — builds a `.pptx` file with one slide per widget using `python-pptx`. Each slide embeds the SVG natively (PowerPoint 2016+ vector rendering) plus a PNG raster fallback (via `cairosvg`) for older clients. Per-widget captions, a title slide, header/footer text boxes, and explicit slide ordering all come from the T5 export config.

Required Python packages (lazy-imported; absent → 503):
- `python-pptx` — PPTX construction.
- `cairosvg` — PNG raster fallback inside each slide.

```
GET /api/v1/boards/{id}/export.pptx
GET /api/v1/boards/{id}/export.pptx?page_size=16:9
```

Both PDF and PPTX export use the **editor view** (no RLS filtering) — the same policy as `export.csv`. For per-viewer filtered exports, drive the export through a scheduled report flow with the appropriate `policies` claim.

---

## Scheduled Jobs

Jobs automate query execution and report delivery. Three kinds are supported:

| `kind` | `target` type | Description |
|--------|---------------|-------------|
| `query` | `string` — registered query ID | Execute a query on schedule; record the row count. |
| `python` | `string` — Python source | Run Python in the server kernel; metered against the org's compute quota. |
| `report` | `object` — `ReportTarget` | Render a board as CSV or PDF and email it to a list of recipients. |

### REST Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/v1/jobs` | Create a job. Returns 201 with the created job. |
| `GET` | `/api/v1/jobs` | List all jobs for the caller's org. |
| `GET` | `/api/v1/jobs/{id}` | Get a single job. Returns 404 on cross-org or missing. |
| `DELETE` | `/api/v1/jobs/{id}` | Delete job and all its runs. Returns 204. |
| `POST` | `/api/v1/jobs/{id}/run` | Run the job immediately (outside the schedule). Returns the run record. |
| `GET` | `/api/v1/jobs/{id}/runs` | List run history for a job, oldest first. |

All endpoints require a valid first-party Bearer token. Jobs are org-scoped — callers can only access jobs belonging to their own org.

---

## Schedule Format

Schedules accept two syntaxes:

**Cron** — standard 5-field expression (`minute hour dom month dow`):

| Example | Meaning |
|---------|---------|
| `0 7 * * 1-5` | Every weekday at 07:00 UTC |
| `0 6 * * *` | Every day at 06:00 UTC |
| `*/15 * * * *` | Every 15 minutes |
| `0 9 1 * *` | First of every month at 09:00 UTC |

**Interval** — plain duration shorthand:

| Example | Meaning |
|---------|---------|
| `interval:30s` | Every 30 seconds |
| `interval:5m` | Every 5 minutes |
| `interval:1h` | Every hour |

Invalid schedule strings are rejected at creation time with HTTP 400.

---

## Report Jobs

Report jobs (`kind='report'`) resolve a board's widget queries, render the results to CSV or PDF, and email the output to a list of recipients.

### Create a Report Job

```
POST /api/v1/jobs
Authorization: Bearer <jwt>
Content-Type: application/json
```

```json
{
  "name":     "Daily Revenue Report",
  "kind":     "report",
  "schedule": "0 7 * * 1-5",
  "enabled":  true,
  "target": {
    "board_id":               "board-uuid",
    "format":                 "pdf",
    "recipients":             ["alice@example.com", "bob@example.com"],
    "subject":                "Daily Revenue — {{date}}",
    "body":                   "Please find today's revenue report attached.",
    "params":                 { "region": "EMEA" },
    "apply_user_permissions": false,
    "locked_params":          {}
  }
}
```

### `ReportTarget` Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `board_id` | `string` | yes | UUID of the board to render. |
| `format` | `string` | no | `csv` or `pdf`. Default: `csv`. |
| `recipients` | `array` | yes | At least one email address. |
| `subject` | `string` | no | Email subject line. Default: `"Nubi Report"`. |
| `body` | `string` | no | Plain-text email body. |
| `params` | `object` | no | Named param overrides applied to all widget queries. |
| `apply_user_permissions` | `bool` | no | When `true`, renders a separate report per recipient using `locked_params`. Default: `false`. |
| `locked_params` | `object` | no | `{email: {param_name: value}}` — per-recipient param overrides. Only used when `apply_user_permissions=true`. |

### Report Formats

**CSV** — the executor walks the board's `spec.widgets`, resolves each widget's `query_id` from the query registry, runs the query through the same planner path used by interactive queries (named params are never string-concatenated into SQL), and writes a multi-section CSV. Each widget gets a `# Widget: <id>` comment header. Widgets whose query cannot be resolved or returns no rows are skipped with an inline comment; the rest of the report continues.

The CSV attachment is returned as `report.csv`.

**PDF** — produces a real `%PDF-1.4` document using Nubi's dependency-free PDF renderer (`app.pdf` — stdlib only, no reportlab or weasyprint). The output has:

- A branded header band with the board name and a generated-at timestamp.
- One compact data table per widget (header row + up to 30 data rows, zebra-striped, auto-paginating).
- A truncation notice when a widget has more than 30 rows: `… N more rows (full data in the CSV export)`.

The PDF attachment is returned as `report.pdf`. For the full dataset, use `format='csv'` or the direct export endpoints.

Both formats follow the same widget-resolution path, so skipped widgets appear identically in both.

> **High-fidelity export vs. report PDF** — the `report` job's PDF is a compact data table summary (stdlib-only). The `GET /boards/{id}/export.pdf` endpoint produces a full visual render of the board via the T2→T3 pipeline (echarts-SSR → cairosvg / svglib). Use the download endpoint when you need chart visuals; use the job PDF when you need a quick tabular data digest.

### Per-Recipient Locked Params

When `apply_user_permissions=true`, the executor renders a separate report for each recipient with that recipient's locked params injected on top of the base `params`. This lets you send one job to a list of recipients where each person sees only their own data slice:

```json
{
  "apply_user_permissions": true,
  "locked_params": {
    "alice@example.com": { "region": "EMEA",    "tenant_id": "acme"   },
    "bob@example.com":   { "region": "US-West", "tenant_id": "globex" }
  }
}
```

Locked params take precedence over `params` (the same priority order as RLS token claims over body params in embedded dashboards). One email is sent per recipient; the other recipients never receive each other's data.

When `apply_user_permissions=false`, one report is rendered and sent to all recipients.

---

## Email Delivery

Reports are delivered by email when `SMTP_HOST` is configured. The transport uses Python's standard `smtplib` — no external dependencies.

| Env var | Default | Description |
|---------|---------|-------------|
| `SMTP_HOST` | `""` | SMTP server hostname. Leave empty to disable delivery. |
| `SMTP_PORT` | `587` | `587` for STARTTLS, `465` for implicit TLS. |
| `SMTP_USERNAME` | `""` | SMTP auth username (e.g. `"apikey"` for SendGrid). |
| `SMTP_PASSWORD` | `""` | SMTP auth password or API key. |
| `SMTP_USE_TLS` | `true` | Enable STARTTLS (used when port is not 465). |
| `SMTP_FROM` | `""` | From address. Falls back to `BILLING_EMAIL` then `COMPANY_EMAIL`. |

When `SMTP_HOST` is not set, reports are generated and run records are written normally — emails are simply not sent. This means OSS/self-hosted deployments and development environments work without a mail server configured.

---

## Query Jobs

```json
{
  "name":     "hourly_snapshot",
  "kind":     "query",
  "schedule": "0 * * * *",
  "target":   "revenue_by_month"
}
```

`target` is a registered query ID. The executor runs the query against a fresh DuckDB connector and records the resulting row count. Useful for data freshness checks and pipeline health monitoring.

---

## Python Jobs

```json
{
  "name":     "weekly_rollup",
  "kind":     "python",
  "schedule": "0 3 * * 1",
  "target":   "import pyarrow as pa\nresult = pa.table({'n': [1]})"
}
```

`target` is Python source that must assign a `pyarrow.Table` to `result`. The code runs in the server kernel via `LocalSubprocessRunner` with a 60-second timeout. Compute usage is metered against the job's owning org and attributed to the creating user. Python jobs require a first-party token and are not available to embed tokens.

---

## Job Run Records

Every execution (scheduled or manual) produces a run record:

```json
{
  "id":          "run-uuid",
  "job_id":      "job-uuid",
  "status":      "success",
  "started_at":  "2025-06-09T07:00:01.234Z",
  "finished_at": "2025-06-09T07:00:03.456Z",
  "row_count":   4,
  "message":     "Report job completed: board='board-uuid', format='pdf', recipients=2, emails_sent=2.",
  "created_at":  "2025-06-09T07:00:01.000Z"
}
```

`status` is `success` or `error`. On error, `message` contains the error detail. For report jobs, `row_count` is the number of emails sent. For query jobs, it is the number of rows returned.

---

## Background Scheduler

The scheduler tick runs every `JOBS_SCHEDULER_INTERVAL_S` seconds (default: 30). It is disabled by default.

| Env var | Default | Description |
|---------|---------|-------------|
| `JOBS_SCHEDULER_ENABLED` | `false` | Set to `true` to activate the background scheduler. |
| `JOBS_SCHEDULER_INTERVAL_S` | `30` | Seconds between scheduler ticks. |

A job is due if `enabled=true` and its `next_run_at` is at or before the current tick time (or is null). After each run the scheduler advances `next_run_at` to the next occurrence and updates `last_run_at`.

---

## Scheduled `report_send` Flow (Flows-based delivery)

Flows (`app/flows/`) are a lower-level task graph that underlies the Jobs system. For advanced cases (custom notify channels, per-tenant reports, conditional delivery) you can compose a `report_send` flow directly instead of using a `report` job.

The canonical task chain for a scheduled visual report is:

```
snapshot_refresh  →  render_board_svg  →  render_board_pdf  →  notify_email
```

Each step reuses the same building blocks as the download endpoints:

| Step | Module | Description |
|------|--------|-------------|
| `snapshot_refresh` | `app.embedding.snapshot` | Collect board data and write a DuckDB sidecar artifact. |
| `render_board_svg` | `app.dashboards.svg_render` | Render per-widget SVGs and compose a page SVG via Node.js echarts-SSR. |
| `render_board_pdf` | `app.embedding.render_pdf` | Convert the composed SVG to a `%PDF` byte string via cairosvg / svglib. |
| `notify_email` | `app.notify.*` | Deliver the PDF as an email attachment via the configured SMTP transport. |

The flow is registered in `app/flows/registry.py` under the `report_send` kind and can be triggered manually (`POST /api/v1/flows`) or on a cron schedule via the Flows scheduler.

The `policies` claim passed at flow trigger time determines the RLS view the snapshot captures. For multi-tenant delivery, trigger one flow run per tenant with that tenant's `policies` — each run produces a separate artifact and email for that tenant's data slice only.

---

## Unsafe Public Export (LOUD — read this before enabling)

`POST /api/v1/boards/{id}/export/public` generates a **no-auth, publicly accessible** static HTML page backed by a frozen DuckDB sidecar artifact.

**This is not a standard export path.** It is opt-in and disabled by default. Before you can use it:

1. Set `ALLOW_UNSAFE_PUBLIC_EXPORTS=true` in the server environment.
2. The org must hold the `public_exports` feature gate.

Even with both conditions met, the endpoint writes a WARNING log entry and persists an audit record on the board (`config.public_export_audit`). Every export is traceable.

**Security properties:**

- Authentication is required to *create* the export (first-party Bearer token).
- The resulting URL is public — **no auth, no expiry, no per-viewer filtering**.
- The data is frozen under the exporter's RLS view (`policies` from the verified token at create time, never from a request body). Every viewer of the URL sees the same data.
- Only export boards whose data is safe to make fully public.

The endpoint reuses the Mode 3a snapshot artifact (`app.embedding.snapshot`). Pass `?snapshot_id=<id>` to publish an existing snapshot, or omit it to capture a fresh one.

```
POST /api/v1/boards/{id}/export/public
Authorization: Bearer <first-party-jwt>
```

Response includes `unsafe: true` in the payload as a constant reminder.

---

## Related Docs

- [Queries & Params](/docs/queries-and-params) — registered queries, named params, defaults
- [Dashboards](/docs/dashboards) — board specs and widget configuration
- [Embedding](/docs/embedding) — embed tokens and row-level security
- [AI, Chat & MCP](/docs/ai-and-mcp) — MCP tool `propose_materialized_view` for query log analysis
