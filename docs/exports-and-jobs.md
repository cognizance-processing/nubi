# Exports & Scheduled Reports

Nubi has two ways to get data out: **one-click dashboard exports** via the toolbar, and **scheduled jobs** that render CSV or PDF reports and deliver them by email on a cron schedule.

---

## Dashboard Exports (UI)

Every dashboard has an **Export & Share** button in the top bar. The menu offers three export formats:

| Format | How it works |
|--------|--------------|
| **PNG** | Client-side capture of the rendered dashboard DOM via `html2canvas`. |
| **PDF** | Client-side capture via `html2canvas + jsPDF`. The browser renders the visual layout to PDF — this is distinct from the server-side report PDF described below. |
| **CSV** | Per-widget data fetched from `GET /api/v1/boards/{id}/export.json`, then assembled into a multi-section CSV in the browser. Each widget produces one section labelled `# widget: <id>`. |

The **Share** tab on the same menu gives you the embed URL and a `<nubi-dashboard>` snippet. See [Embedding](/docs/embedding) for the full embed and row-level security details.

You can also request the raw data directly:

```
GET /api/v1/boards/{id}/export.csv        # multi-widget CSV (server-side)
GET /api/v1/boards/{id}/export.json       # same data as JSON
```

Both endpoints are org-scoped and require a first-party Bearer token. Pass `?query_id=<id>` to limit the export to a single widget.

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

## Related Docs

- [Queries & Params](/docs/queries-and-params) — registered queries, named params, defaults
- [Dashboards](/docs/dashboards) — board specs and widget configuration
- [Embedding](/docs/embedding) — embed tokens and row-level security
- [AI, Chat & MCP](/docs/ai-and-mcp) — MCP tool `propose_materialized_view` for query log analysis
