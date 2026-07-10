# Flows & variables

> Part of the [API reference](/docs/api-reference) — see it for conventions, error codes, and the audit log.

## Flows

All flows endpoints require first-party access tokens. Flows are org-scoped;
cross-org access returns `404`.

### `POST /flows`

Create a new flow.

**Auth:** Writer role.

**Request body:**
```json
{
  "name": "Daily revenue rollup",
  "spec": {
    "version": 1,
    "tasks": [
      {
        "key": "pull_orders",
        "kind": "query",
        "sql": "SELECT * FROM orders WHERE order_date >= '{{params.start_date}}'",
        "needs": []
      },
      {
        "key": "aggregate",
        "kind": "python",
        "code": "result = {'revenue': sum(r['amount'] for r in inputs['pull_orders']['rows'])}",
        "needs": ["pull_orders"],
        "cpu_cores": 1.0,
        "mem_mb": 512,
        "timeout_s": 60
      }
    ]
  },
  "schedule": "0 6 * * *",
  "enabled": true
}
```

**Response `201`:** Flow record `{id, org_id, name, spec, version, enabled, schedule, next_run_at, ...}`.

---

### `GET /flows`

List all flows for the caller's org.

**Response `200`:** Array of flow records.

---

### `GET /flows/{id}`

Retrieve a single flow.

**Response `200`:** Flow record.

---

### `PUT /flows/{id}`

Update a flow's name, spec, enabled state, or schedule.

**Auth:** Writer role.

**Request body (all fields optional):**
```json
{ "name": "New name", "spec": { ... }, "enabled": false, "schedule": "0 9 * * 1-5" }
```

**Response `200`:** Updated flow record.

---

### `DELETE /flows/{id}`

Delete a flow.

**Auth:** Writer role.

**Response `204`:** No content.

---

### `POST /flows/validate`

Validate a flow spec without running it.

**Request body:**
```json
{ "spec": { ... } }
```

**Response `200`:**
```json
{ "valid": true, "issues": [] }
```

---

### `POST /flows/{id}/run`

Trigger a durable run of the flow.

**Auth:** Writer role.

**Request body (optional):**
```json
{ "params": { "start_date": "2026-01-01", "region": "EMEA" }, "env": "prod" }
```

**Response `200`:** Flow run record with `task_runs` array.

---

### `GET /flows/{id}/runs`

List all runs of a flow (newest first).

**Response `200`:** Array of `{id, flow_id, state, trigger, params, started_at, finished_at, duration_s, error}`.

---

### `GET /flows/runs/{run_id}`

Retrieve a full run record including all task runs.

**Query parameters:**

| Param | Default | Description |
|---|---|---|
| `include_results` | `0` | Set to `1` to include full task result blobs (default: stubs for blobs > 64 KiB). |
| `task_runs_limit` | `2000` | Max task_run rows included (ceiling: 10 000). |

**Response `200`:**
```json
{
  "id": "...",
  "flow_id": "...",
  "state": "success",
  "trigger": "manual",
  "params": {},
  "started_at": "2026-06-01T06:00:00Z",
  "finished_at": "2026-06-01T06:00:45Z",
  "duration_s": 45.2,
  "task_runs": [
    {
      "task_key": "pull_orders",
      "state": "success",
      "attempt": 1,
      "started_at": "...",
      "finished_at": "...",
      "result": { "rows": [...], "row_count": 1200 }
    }
  ],
  "task_runs_truncated": false
}
```

---

### `POST /flows/{flow_id}/sweep`

Run a parameter sweep — one flow run per param set in a grid or explicit list.

**Auth:** Writer role.

**Request body:**
```json
{
  "grid": { "region": ["EMEA", "APAC", "AMER"], "model": ["v1", "v2"] },
  "max_cells": 50
}
```

Or with an explicit list:

```json
{
  "param_sets": [
    { "region": "EMEA", "model": "v1" },
    { "region": "APAC", "model": "v1" }
  ],
  "max_cells": 50
}
```

Supply `grid` (Cartesian product expanded) or `param_sets` (explicit). Max
cells is capped server-side at `MAX_SWEEP_CELLS` (default 50, configurable
via `MAX_SWEEP_CELLS` env var). The sweep times out at `SWEEP_TIMEOUT_S`
(default 300 s).

**Response `200`:**
```json
{
  "sweep_id": "...",
  "flow_id": "...",
  "total": 6,
  "succeeded": 6,
  "failed": 0,
  "diff_surface": [
    { "index": 0, "params": { "region": "EMEA", "model": "v1" }, "outputs": { ... } }
  ],
  "cells": [
    { "index": 0, "params": {...}, "run_id": "...", "state": "success", "error": null }
  ]
}
```

**Errors:**
- `400 bad_request` — neither `grid` nor `param_sets` supplied.
- `400 bad_request` — `param_sets` length exceeds the server cap.
- `504 sweep_timeout` — wall-clock limit exceeded.

---

### `POST /flows/{flow_id}/backfill`

Re-run a flow over a historical date range, one run per time window.

**Auth:** Writer role.

**Request body:**
```json
{
  "start": "2026-01-01T00:00:00Z",
  "end": "2026-06-01T00:00:00Z",
  "window": "7d",
  "params": { "region": "EMEA" }
}
```

`window` accepts `Nd` (days), `Nw` (weeks), `Nh` (hours). The maximum number
of windows is capped at `MAX_BACKFILL_WINDOWS` (default 500).

---

### `POST /flows/triggers`

Register a new flow trigger.

**Auth:** Writer role.

**Request body:**
```json
{
  "flow_id": "flow-uuid",
  "kind": "event",
  "source": "order.completed",
  "enabled": true,
  "secret": null,
  "extra": {}
}
```

| Field | Description |
|---|---|
| `kind` | `"event"` / `"webhook"` / `"downstream"` |
| `source` | For `event`/`webhook`: event key string. For `downstream`: upstream flow_id. |
| `secret` | Optional HMAC secret for webhook signature verification. |

**Response `201`:**
```json
{ "id": "...", "flow_id": "...", "kind": "event", "source": "order.completed", "org_id": "...", "enabled": true, "created_at": "..." }
```

---

### `GET /flows/triggers`

List all triggers for the caller's org.

**Response `200`:** Array of trigger records.

---

### `POST /flows/triggers/fire`

Fire a named event, triggering any flows registered on that event key.

**Auth:** Writer role.

**Request body:**
```json
{ "event_key": "order.completed", "params": { "order_id": "12345" } }
```

**Response `200`:**
```json
{ "triggered": 2, "flow_ids": ["flow-uuid-1", "flow-uuid-2"] }
```

---

## Flow versions, revert, and environments

### `GET /flows/{id}/versions`

List all spec versions for a flow (newest first). Specs are excluded from the
list for compactness; fetch a specific version to get the full spec.

**Auth:** Any valid first-party Bearer token. Org-scoped.

**Response `200`:**
```json
{
  "flow_id": "flow-uuid",
  "versions": [
    { "id": "ver-uuid", "version": 3, "created_by": "user-uuid", "created_at": "2026-06-26T10:00:00Z" },
    { "id": "ver-uuid", "version": 2, "created_by": "user-uuid", "created_at": "2026-06-25T09:00:00Z" },
    { "id": "ver-uuid", "version": 1, "created_by": "user-uuid", "created_at": "2026-06-24T08:00:00Z" }
  ]
}
```

**Errors:** `404` — flow not found or cross-org.

---

### `GET /flows/{id}/versions/{v}`

Fetch the full spec snapshot for version `v`.

**Auth:** Any valid first-party Bearer token. Org-scoped.

**Response `200`:**
```json
{
  "id": "ver-uuid",
  "flow_id": "flow-uuid",
  "org_id": "org-uuid",
  "version": 2,
  "spec": { "version": 1, "tasks": [...] },
  "created_by": "user-uuid",
  "created_at": "2026-06-25T09:00:00Z"
}
```

The `spec` field has `__owner_policies__` stripped before returning (security).

**Errors:** `404` — flow not found, cross-org, or version number not found.

---

### `POST /flows/{id}/revert/{v}`

Revert a flow's spec to a prior version. The current spec is snapshotted as a
new version first (so revert is undoable), then the target version's spec is
applied as the live spec. The caller's RLS policies are re-snapshotted onto the
reverted spec.

**Auth:** First-party Bearer token. Writer role required.

**Response `200`:** Updated flow record (same shape as `GET /flows/{id}`).

**Errors:** `404` — flow or version not found.

---

### `GET /flows/{id}/environments`

List environments and their materialisation watermarks for a flow.

**Auth:** Any valid first-party Bearer token. Org-scoped. Viewer-friendly (read-only).

**Response `200`:**
```json
{
  "flow_id": "flow-uuid",
  "environments": [
    {
      "key": "prod",
      "watermarks": { "model/revenue": "2026-06-26T05:00:00Z" }
    },
    {
      "key": "dev",
      "watermarks": {}
    }
  ]
}
```

**Errors:** `404` — flow not found or cross-org.

---

## Variables

Project variables are persistent, org/project-scoped key/value pairs used to
store configuration and runtime state (e.g. dashboard defaults, feature flags,
per-project settings).  They are readable by any authenticated member and
writable by members with the **writer** role.

**Scoping:** Each variable belongs to an org and optionally a project.  The
effective scope is resolved from the `X-Org-Id` header (with membership
check) and `X-Project-Id` / `?project_id=` query parameter.  A project
variable and an org-global variable with the same key never collide.
Cross-org access always returns 404 — no information leak.

---

### `GET /variables`

List all variables for the caller's org in the resolved project scope.

**Auth:** Reader role.

**Response `200`:**
```json
[
  {
    "key": "default_region",
    "value": "us-east-1",
    "org_id": "<org-id>",
    "project_id": "<project-id or null>",
    "updated_by": "<user-id>",
    "updated_at": "2026-01-01T00:00:00Z"
  }
]
```

---

### `GET /variables/{key}`

Fetch a single variable by key within the caller's resolved org + project scope.

**Path parameters:**

| Param | Type | Description |
|---|---|---|
| `key` | string | Variable key (case-sensitive). |

**Response `200`:** Variable row (same shape as the list entry above).

**Errors:**
- `404 not_found` — variable not found in the caller's org+scope (also returned for cross-org keys — no leak).

---

### `PUT /variables/{key}`

Upsert a variable's value.  Creates the variable if it does not exist; updates
the value if it does.  The project scope is resolved from the request body's
`project_id` field (when set) or from the request context (`X-Project-Id` /
`?project_id=` / default project); org-global when none resolves.

**Auth:** Writer role (`require_writer`).

**Path parameters:**

| Param | Type | Description |
|---|---|---|
| `key` | string | Variable key (case-sensitive). |

**Request body:**
```json
{
  "value": <any JSON value>,
  "project_id": "<optional project id — overrides header/query scope>"
}
```

**Response `200`:** Updated variable row.

**Errors:**
- `403 insufficient_role` — caller is a viewer (read-only member).

---

### `DELETE /variables/{key}`

Delete a variable.

**Auth:** Writer role.

**Path parameters:**

| Param | Type | Description |
|---|---|---|
| `key` | string | Variable key to delete. |

**Response:** `204 No Content` on success.

**Errors:**
- `403 insufficient_role` — caller is a viewer.
- `404 not_found` — variable not found in the caller's org+scope.

---
