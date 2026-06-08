# Pre-Aggregations (Auto Rollups)

Pre-aggregations are materialized rollup tables that Nubi builds from your **own query log**. Instead of asking you to hand-define cubes, Nubi mines the queries that actually ran, finds the hot `GROUP BY` shapes, ranks them by cost, and materializes the winners. Matching queries are then transparently routed to the rollup — fewer scanned bytes, faster reads — while RLS still holds because the rollup keeps its tenant key columns.

This is the same wedge as [materialized blends](/docs/flows#blends--cheap-reads-vs-live-federation): pay the aggregation cost once on a schedule, serve cheap reads forever.

---

## How It Works

1. **Mine** — every executed query is logged with its parsed shape (base table, dimensions, measures, filters) and an estimate of bytes scanned. The miner clusters compatible shapes (same base table + dimension set) and ranks each cluster by `score = frequency × scanned-bytes`. The hottest, most expensive patterns float to the top.
2. **Build** — for a chosen shape, Nubi materializes a rollup: `SELECT <dimensions>, <measures> FROM <table> GROUP BY <dimensions>`, with the declared `rls_keys` preserved (and grouped on) so read-time `WHERE <key> = <claim>` injection stays sound per tenant. The rollup is written through the DuckDB write path and registered.
3. **Route** — the registered rollup is consulted by the planner-level router. A query whose dimensions are a subset of a built rollup's dimensions (superset routing) can be answered from the rollup. Each routed query increments the rollup's **HIT** count.

A candidate must clear a `min_hits` threshold (default `3`) before it is surfaced or built, so one-off ad-hoc queries never trigger a rollup.

---

## REST API

All endpoints require a valid first-party Bearer token and are org-scoped (the caller's `org_id` is resolved, honouring the `X-Org-Id` header). The mined query log and rollup registry are currently process-wide singletons.

Base path: `/api/v1/preagg`

### Suggestions — ranked candidates

```
GET /api/v1/preagg/suggestions?min_hits=3
Authorization: Bearer <jwt>
```

Returns ranked rollup candidates mined from the query log, highest score first:

```json
[
  {
    "table":        "orders",
    "dimensions":   ["region", "day"],
    "measures":     ["sum(amount)", "count(*)"],
    "filters":      [],
    "score":        4820000,
    "sample_count": 41,
    "est_bytes":    117560,
    "cluster_key":  "orders|day,region"
  }
]
```

| Field | Meaning |
|-------|---------|
| `score` | Rank key: `sample_count × est_bytes` (frequency × scanned-bytes). |
| `sample_count` | How many logged queries matched this cluster. |
| `est_bytes` | Estimated bytes scanned by the cluster. |
| `cluster_key` | Stable id for the candidate — pass it to `POST /preagg/build`. |

`min_hits` (default `3`) sets the minimum `sample_count` for a candidate to appear.

### Build — materialize a rollup

```
POST /api/v1/preagg/build
Authorization: Bearer <jwt>
Content-Type: application/json
```

Supply a mined candidate by `cluster_key`, or specify the shape explicitly:

```json
{
  "cluster_key":     "orders|day,region",
  "rls_keys":        ["tenant_id"],
  "source_database": "/abs/path/to/warehouse.duckdb",
  "datastore_id":    "ds-uuid"
}
```

| Field | Required | Description |
|-------|----------|-------------|
| `cluster_key` | One of `cluster_key` / `table` | Select a mined candidate by its `cluster_key`. Its `table`/`dimensions`/`measures` are used (and may be omitted below). |
| `table` | One of `cluster_key` / `table` | Base fact table to roll up. |
| `dimensions` | No | GROUP BY columns. |
| `measures` | Required (if no `cluster_key`) | `func(col)` measure strings, e.g. `["sum(amount)", "count(*)"]`. At least one. |
| `rls_keys` | No | RLS-key columns that must be preserved and grouped on so per-tenant predicate injection stays sound. |
| `source_database` | No | Absolute path to the DuckDB file holding the base table. Omit for the test/demo path. |
| `datastore_id` | No | Datastore the materialized rollup is served through. |

Response `201`: the built-rollup manifest — `{rollup_id, table, source_table, dimensions, measures, rls_keys, database, datastore_id, query_id, hits, ...}`. Returns `404 rollup_candidate_not_found` for an unknown `cluster_key`, or `400 invalid_rollup_request` if neither a `cluster_key` nor a `table` (with at least one measure) is provided.

The same endpoint is schedulable: a cron caller that POSTs to it on a schedule materializes rollups on a cadence.

### List — built rollups + HIT counts

```
GET /api/v1/preagg
Authorization: Bearer <jwt>
```

Returns the rollups that have been built, each including its `hits` count — how many incoming queries have been routed to it. Use this to see which rollups are earning their keep.

---

## Refreshing on a Schedule

The suggest → build pass can run automatically inside a flow via the [`preagg_refresh` task kind](/docs/flows#preagg_refresh--refresh-auto-pre-aggregations). The task mines the org's query log and builds every candidate above `min_hits` in one pass, returning `{org_id, candidates_found, rollups_built, rollup_ids, errors}`. Attach a schedule to the flow to keep rollups fresh as query patterns shift.

---

## RLS Safety

A rollup is only sound for multi-tenant data if it keeps the tenant key columns. The builder preserves declared `rls_keys` (grouping the rollup on them) so the planner can inject `WHERE <key> = <claim>` against the rollup exactly as it would against the base table. Routing a query to a rollup never widens the data a viewer can see.

---

## Related

- The MCP tool `propose_materialized_view` surfaces the same rollup suggestions to LLM agents. See [AI, Chat & MCP](/docs/ai-and-mcp).
- For multi-source materialization (joins across sources), see [Materialized Blends](/docs/flows#materialized-blends).
