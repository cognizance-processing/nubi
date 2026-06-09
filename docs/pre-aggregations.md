# Pre-aggregations (rollups)

![Query-log mining turns repeated scans into cached rollup reads](illustration:EdgeCache)

Pre-aggregations are materialized **rollup tables** Nubi builds from your **own query log**. Instead of asking you to hand-define cubes, Nubi watches the queries that actually run, finds the hot `GROUP BY` shapes, ranks them by `frequency × scanned-bytes`, and lets you materialize the winners with one click. Matching queries are then transparently routed to the rollup — fewer bytes scanned, faster dashboards and embeds — while row-level security still holds because the rollup keeps its tenant key columns.

You manage rollups from the **Rollups** panel inside the Queries section. There are no cubes to write and no schema to maintain: suggestions are mined automatically, and you decide which ones are worth materializing.

---

## When to use rollups

Reach for a rollup when the same aggregation runs over and over and the base table is large enough that scanning it every time is wasteful:

- A dashboard KPI like `SUM(amount)` by `region` and `day` that every viewer loads.
- An embedded chart that fans out to many tenants but always groups the same way.
- A report that re-runs on a schedule against a growing fact table.

You do **not** need a rollup for one-off, ad-hoc exploration. A query shape must appear **at least 3 times** in the log before Nubi suggests it, so only genuinely repeated patterns are surfaced.

---

## Opening the Rollups panel

1. Go to the **Queries** section.
2. In the top bar, click the **Rollups** option in the segmented toggle (the other option is **Editor**).
3. The panel loads two lists: **Suggested rollups** (candidates mined from the log) and **Active rollups** (rollups already built).

The toggle does not discard your editor state — switch back to **Editor** at any time.

At the top of the panel is a **Refresh** button (circular-arrows icon). Click it any time to re-mine the query log and reload built rollups — useful after running more queries or after a scheduled build.

---

## Reading a suggestion

Each card under **Suggested rollups** represents one mined candidate, ranked by score (highest first).

| Field | What it means |
|---|---|
| **Table name** | The base fact table the rollup would aggregate. |
| **score** badge | Rank key = `sample_count × estimated bytes scanned`. Higher is more worth building. |
| **N hits in the log** | How many logged queries matched this pattern. |
| **~X scanned** | Estimated bytes the pattern scans against the base table. |
| **group by** chips | The `GROUP BY` columns (dimensions) the rollup would be grouped on. |
| **measures** chips | The aggregates to materialize, e.g. `sum(amount)`, `count(*)`. |
| **filters** chips | Columns seen in `WHERE` clauses across the clustered queries (shown when present). |

Suggestions whose shape already has a built rollup drop off the list automatically, so what remains is always actionable.

If you see **"No suggestions yet"**, run a few aggregating queries from the **Editor** and come back — the miner needs a pattern to repeat at least 3 times before it appears.

### How the miner clusters shapes

Queries that share the same base table and the same `GROUP BY` column set are merged into one candidate, even if they differ in measures or filters. The rollup's measure list becomes the **union** of all clustered queries, so a single rollup can serve all of them.

---

## Building a rollup (one click)

Building materializes the rollup table once and registers it so future matching queries route to it.

1. Open the **Rollups** panel and find the suggestion you want.
2. Click the **Build** button on its card.
3. The button shows **Building…** with a spinner while Nubi materializes the rollup, then flips to **Built** with a checkmark.
4. The new rollup appears immediately under **Active rollups**, and the suggestion disappears from the suggestions list.

No shape to re-enter — Nubi resolves the table, dimensions, and measures from the mined candidate.

### Who can build

Building requires **writer** access. With read-only access, the card shows a **Read-only** label instead of a Build button. Suggestions and active rollups are still fully visible to all roles.

### If a build fails

The card shows an inline error in red beneath it. The most common cause is insufficient permissions. Fix the cause and click **Build** again.

---

## Active rollups

Each card under **Active rollups** is a rollup that has been built and is live.

| Field | What it means |
|---|---|
| **Rollup table name** + **active** badge | The materialized rollup table; the green badge confirms it is serving. |
| **N hits** (lightning icon) | How many incoming queries have been **routed to this rollup**. The headline metric — it tells you the rollup is earning its keep. |
| **from `<source_table>`** | The base table the rollup was built from, plus the datastore it is served through (when set). |
| **group by** chips | The dimensions the rollup is grouped on. |
| **measures** chips | The materialized aggregates. |
| **rls keys** chips | Tenant key columns preserved so per-viewer row-level security still holds (shown when present). |
| Monospace id at the bottom | The rollup's stable `rollup_id`, for reference or support. |

### Reading the HIT count

The **hits** number is your signal for whether a rollup is worth keeping:

- **Rising hits** — the rollup is actively accelerating real traffic. Keep it.
- **Zero or flat hits** — nothing is routing to it. The matching queries may have stopped running or their shape drifted. Consider rebuilding against the current pattern.

Click **Refresh** to pull the latest hit counts.

---

## How routing works

Once a rollup is built, you change nothing in your SQL, dashboards, or embeds. Routing is **transparent**:

1. A query arrives.
2. The planner checks registered rollups for the query's base table.
3. A rollup is eligible when the query's `GROUP BY` columns are a **subset** of the rollup's dimensions, every required measure is materialized in the rollup, and every `WHERE` column is present in the rollup.
4. When a sound match is found, the query reads the small pre-aggregated table instead of the base table, and that rollup's **hit** count increments.

The router only rewrites when it can prove the rewrite is sound. Anything it cannot prove safe is executed against the base table unchanged.

Concretely:

- **Dashboards and embeds load faster** — every viewer hitting a matching widget reads the rollup, not the raw fact table.
- **Repeated queries get cheaper** — you pay the aggregation cost once at build time, then serve cheap reads.
- **No query rewrites needed** — the same registered query or ad-hoc SQL automatically benefits; you never reference the rollup by name.

This pairs naturally with Nubi's content-hashed query cache and with [materialized SQL cells in Flows](/docs/flows#materialization-sql-cells): pay the aggregation cost once, serve cheap reads thereafter.

---

## Row-level security stays sound

A rollup is only safe for multi-tenant data if it keeps the tenant key columns. When a rollup is built with **RLS keys**, those columns are preserved in the `GROUP BY`, so the planner injects `WHERE <key> = <claim>` against the rollup exactly as it would against the base table.

```sql
-- What the builder materializes (simplified):
SELECT tenant_id, region, day,
       SUM(amount) AS sum_amount,
       COUNT(*) AS count_all
FROM orders
GROUP BY tenant_id, region, day
```

At read time, the planner adds `WHERE tenant_id = '<claim>'` — the rollup never widens what a viewer can see. The **rls keys** chips on an Active card tell you which tenant keys are preserved.

---

## Keeping rollups fresh

A rollup is a snapshot of the base table at build time. As new data lands, rebuild to keep it current. Two ways:

**Manually** — open the **Rollups** panel, click **Refresh**, and click **Build** on the suggestion when its shape resurfaces.

**On a schedule** — Nubi can register a scheduled `preagg_refresh` flow that runs hourly by default. The flow mines the query log and builds every candidate above the threshold in one pass. It returns:

```json
{
  "org_id": "…",
  "candidates_found": 4,
  "rollups_built": 2,
  "rollup_ids": ["rollup_orders_abc123", "rollup_events_def456"],
  "errors": []
}
```

Already-built rollups with identical dimension sets are skipped (idempotent), so running the refresh more often is safe. See [Flows → Scheduling a flow](/docs/flows#scheduling-a-flow) to set the cron cadence.

---

## Tips

- **Build the top of the list first.** Score already weighs frequency against scanned bytes, so the highest-scoring suggestion is usually the best return on a build.
- **Watch the hits, not just the score.** A high score proves the past pattern was hot; a high **hits** count on an Active rollup proves it is still paying off now.
- **Let the log fill up.** Right after a fresh deploy or a new dashboard, run queries a few times (or wait for real traffic) before checking suggestions. Nothing appears below 3 hits.
- **Read-only? You can still plan.** Suggestions and active rollups are visible to everyone — use the panel to decide what to ask a writer to build.

---

## Related

- [Flows → Scheduling a flow](/docs/flows#scheduling-a-flow) — set a cron cadence so rollups rebuild automatically.
- [Queries & Parameters](/docs/queries-and-params) — registered queries that rollups transparently accelerate.
- [Dashboards](/docs/dashboards) — the widgets that benefit most from rollups.
- [Materialized SQL cells](/docs/flows#materialization-sql-cells) — multi-source materialization for joins across connectors using SQL cells in Flows.
