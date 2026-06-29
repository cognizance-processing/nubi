# Transformation

This page documents the flow spec version history, revert, environment
pinning, and the `POST /transpile` SQL dialect-conversion endpoint.

For the full flow orchestration API (run, preview, sweep, backfill, etc.) see
[docs/flows.md](flows.md) and [docs/semantic-and-data-apps.md](semantic-and-data-apps.md).

For the named managed-table pattern (hosts without a warehouse, Parquet-backed
queryable projections via a `materialize` cell) see
[docs/materialization.md](materialization.md).

---

## Flow spec version history

Every time a flow's spec is saved (create or PUT update) the previous spec is
snapshot-versioned. Versions are immutable and numbered monotonically from 1.

All version endpoints require a valid first-party Bearer token. Reads are
viewer-accessible; writes require `writer` role.

### `GET /api/v1/flows/{flow_id}/versions`

List all spec versions for a flow, newest first. Specs are omitted from the
list for compactness — fetch a specific version to get the spec.

**Response:**
```json
{
  "flow_id": "uuid",
  "versions": [
    {
      "id": "uuid",
      "version": 3,
      "created_by": "user-uuid",
      "created_at": "2026-06-24T10:30:00+00:00"
    },
    {
      "id": "uuid",
      "version": 2,
      "created_by": "user-uuid",
      "created_at": "2026-06-23T09:00:00+00:00"
    }
  ]
}
```

### `GET /api/v1/flows/{flow_id}/versions/{version_num}`

Fetch a specific spec version.

**Response:**
```json
{
  "id": "uuid",
  "flow_id": "uuid",
  "org_id": "uuid",
  "version": 2,
  "spec": { "version": 1, "name": "my_flow", "tasks": [...] },
  "created_by": "user-uuid",
  "created_at": "2026-06-23T09:00:00+00:00"
}
```

Note: `spec.runtime_config.__owner_policies__` (the internal RLS policy
snapshot) is stripped from all outbound version responses.

### `POST /api/v1/flows/{flow_id}/revert/{version_num}`

Revert a flow's live spec to a prior version. Requires `writer` role.

**Revert is always undoable:** before applying the target version's spec, the
current live spec is first snapshot-versioned (so you can always get back to
where you were). Then the target version's spec is applied and also recorded as
a new version. The result is two new version entries: a snapshot of the current
state and the reverted spec.

**Response:** the updated flow in the same shape as `PUT /flows/{id}`.

---

## Environment pinning

Flows support environment-scoped spec pointers so a single flow can have
different pinned specs in `dev`, `staging`, and `prod`.

### `GET /api/v1/flows/{flow_id}/environments`

List environments and their watermarks for a flow.

**Response:**
```json
{
  "flow_id": "uuid",
  "environments": [
    {
      "key": "production",
      "watermarks": {
        "transform": "2026-06-24T08:00:00+00:00"
      }
    }
  ]
}
```

### Using a pinned env spec

Pass `?env=<key>` on `GET /flows/{id}` or `POST /flows/{id}/run` to read or
execute the spec pinned to that environment:

```
GET  /api/v1/flows/{id}?env=production
POST /api/v1/flows/{id}/run
     { "params": {}, "env": "production" }
```

When no pointer exists for that environment the draft (live) spec is used.

---

## SQL dialect transpilation — `POST /api/v1/transpile`

Pure AST-level SQL dialect translation. No data access, no execution, no I/O.
Uses the same sqlglot engine as the query planner.

**Auth:** any valid Bearer token (no org scope required — this operation is
data-free).

### Request

```json
{
  "sql": "SELECT DATE_TRUNC('month', created_at) AS month, SUM(amount) FROM orders GROUP BY 1",
  "from_dialect": "postgres",
  "to_dialect": "bigquery"
}
```

| Field | Required | Meaning |
|-------|----------|---------|
| `sql` | yes | SQL string to transpile. Must be non-empty. |
| `from_dialect` | yes | Source dialect (see allowlist below). |
| `to_dialect` | yes | Target dialect. |

### Response

```json
{ "sql": "SELECT DATE_TRUNC(created_at, MONTH) AS month, SUM(amount) FROM orders GROUP BY 1" }
```

### Supported dialects

```
bigquery, clickhouse, databricks, duckdb, drill, hive,
mysql, oracle, postgres, postgresql, presto, redshift,
snowflake, spark, spark2, sqlite, trino, tsql
```

(`postgresql` is an alias for `postgres`.)

### Errors

| HTTP | Code | Cause |
|------|------|-------|
| 400 | `unknown_dialect` | `from_dialect` or `to_dialect` not in the allowlist. |
| 400 | `parse_error` | sqlglot failed to parse or transpile the SQL. |
| 400 | `bad_request` | Empty `sql` field. |

### Example — DuckDB → Snowflake

```bash
curl -X POST https://api.example.com/api/v1/transpile \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "sql": "SELECT strftime(created_at, \\"%Y-%m\\") AS month, COUNT(*) FROM events GROUP BY 1",
    "from_dialect": "duckdb",
    "to_dialect": "snowflake"
  }'
```
