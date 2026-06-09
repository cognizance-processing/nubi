# Exports & Scheduled Reports

Nubi gives you two ways to get data out: **one-click exports** from the dashboard toolbar, and **scheduled jobs** that deliver CSV or PDF reports by email on a cron schedule.

---

## Dashboard Exports (UI)

Every dashboard has an **Export & Share** button in the top bar. From there you can:

| Format | How it works |
|--------|--------------|
| **PNG** | Client-side screenshot of the rendered dashboard (html2canvas). |
| **PDF** | Client-side PDF of the rendered dashboard (html2canvas + jsPDF). |
| **CSV** | Fetches each widget's data from the server (`GET /api/v1/boards/{id}/export.json`) and writes a multi-section CSV — one section per widget, labelled with the widget id. |

The Share tab on the same menu gives you the embed URL and a ready-to-paste `<nubi-dashboard>` snippet. See [Embedding](/docs/embedding) for the full embed and row-level security details.

---

## Job Types

Scheduled jobs automate query execution and report delivery. Three kinds are supported:

| Kind | `target` | Description |
|------|----------|-------------|
| `query` | `string` — registered query ID | Execute a query on schedule; record the row count. |
| `python` | `string` — Python source code | Run arbitrary Python in the server kernel (first-party tokens only). |
| `report` | `object` — `ReportTarget` | Render a dashboard as CSV or PDF and email it to a list of recipients. |

---

## Scheduled Report Jobs

Report jobs (`kind='report'`) render a board's widget queries, format the results, and deliver the output by email.

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
    "board_id":              "board-uuid",
    "format":                "pdf",
    "recipients":            ["alice@example.com", "bob@example.com"],
    "subject":               "Daily Revenue — {{date}}",
    "body":                  "Please find today's revenue report attached.",
    "params":                { "region": "EMEA" },
    "apply_user_permissions": false,
    "locked_params":          {}
  }
}
```

### `ReportTarget` Fields

| Field | Type | Description |
|-------|------|-------------|
| `board_id` | `string` | UUID of the board to render. |
| `format` | `string` | `csv` or `pdf`. |
| `recipients` | `array` | At least one email address required. |
| `subject` | `string` | Email subject line. |
| `body` | `string` | Plain-text email body. |
| `params` | `object` | Named param overrides passed to the board's widget queries. |
| `apply_user_permissions` | `bool` | When `true`, injects per-recipient `locked_params` before rendering. |
| `locked_params` | `object` | `{email: {param_name: value}}` — per-recipient param overrides. Only used when `apply_user_permissions=true`. |

### Report Formats

**CSV** — The executor runs each widget's registered query and produces a multi-section CSV attachment. Each widget generates a separate section labelled with the widget id. Widgets whose query cannot be resolved are skipped with an inline comment rather than failing the whole report.

**PDF** — The executor renders the board's queries and produces a PDF attachment. PDF rendering is currently handled server-side; the output is attached to the email exactly as CSV is.

### Per-Recipient Locked Params

When `apply_user_permissions=true`, each recipient sees only their own data slice:

```json
{
  "apply_user_permissions": true,
  "locked_params": {
    "alice@example.com": { "region": "EMEA",    "tenant_id": "acme"   },
    "bob@example.com":   { "region": "US-West", "tenant_id": "globex" }
  }
}
```

The executor injects each recipient's locked params before rendering so the report is per-viewer isolated, matching the same security model as embedded dashboards.

---

## Schedule Format

Schedules use standard 5-field cron syntax (`minute hour dom month dow`):

| Example | Meaning |
|---------|---------|
| `0 7 * * 1-5` | Every weekday at 07:00 UTC |
| `0 6 * * *` | Every day at 06:00 UTC |
| `*/15 * * * *` | Every 15 minutes |
| `0 9 1 * *` | First of every month at 09:00 UTC |

You can also use interval syntax for simple recurring jobs: `interval:30s`, `interval:5m`, `interval:1h`.

Invalid schedule strings are rejected with HTTP 400.

---

## Jobs REST Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/v1/jobs` | Create a job. Returns 201. |
| `GET` | `/api/v1/jobs` | List all jobs for the caller's org. |
| `GET` | `/api/v1/jobs/{id}` | Get a single job. Returns 404 on cross-org or missing. |
| `DELETE` | `/api/v1/jobs/{id}` | Delete job and all its runs. Returns 204. |
| `POST` | `/api/v1/jobs/{id}/run` | Run the job immediately (outside of schedule). Returns the job run record. |
| `GET` | `/api/v1/jobs/{id}/runs` | List run history for a job (oldest first). |

All endpoints require a valid first-party Bearer token. Jobs are org-scoped — callers can only access jobs belonging to their own org.

---

## Job Run Response

```json
{
  "id":          "run-uuid",
  "job_id":      "job-uuid",
  "status":      "success",
  "started_at":  "2024-01-15T07:00:01.234Z",
  "finished_at": "2024-01-15T07:00:03.456Z",
  "row_count":   1234,
  "message":     "",
  "created_at":  "2024-01-15T07:00:01.000Z"
}
```

`status` is `success` or `error`. On error, `message` contains the error detail.

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

The `target` is a registered query id. The executor runs the query and records the resulting row count in the job run. Useful for health checks and data freshness monitoring.

---

## Pre-Aggregation Suggestions via MCP

The MCP tool `propose_materialized_view` analyses the query log collected from executed jobs to suggest pre-aggregation rollups. See [AI, Chat & MCP](/docs/ai-and-mcp) for the full MCP tool list.
