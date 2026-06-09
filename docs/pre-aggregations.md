# Pre-aggregations (rollups)

Pre-aggregations are materialized **rollup tables** that Nubi builds from your **own query log**. Instead of asking you to hand-define cubes, Nubi watches the queries that actually run, finds the hot `GROUP BY` shapes, ranks them by cost, and lets you materialize the winners with one click. Matching queries are then transparently routed to the rollup — fewer bytes scanned, faster dashboards and embeds — while row-level security still holds because the rollup keeps its tenant key columns.

You manage rollups from the **Rollups** panel inside the query section. There are no cubes to write and no schema to maintain: the suggestions are mined automatically, and you decide which ones are worth materializing.

---

## When to use rollups

Reach for a rollup when the same aggregation runs over and over and the base table is large enough that scanning it every time is wasteful — for example:

- A dashboard KPI like `SUM(amount)` by `region` and `day` that every viewer loads.
- An embedded chart that fans out to many tenants but always groups the same way.
- A report that re-runs on a schedule against a growing fact table.

You do **not** need a rollup for one-off, ad-hoc exploration. A query shape must appear **at least 3 times** in the log before Nubi suggests it, so genuinely repeated patterns are the only ones surfaced.

---

## Opening the Rollups panel

1. Go to the **Queries** section.
2. In the top bar you'll see a segmented toggle with two options: **Editor** and **Rollups**. Click **Rollups**.
3. The panel loads two lists: **Suggested rollups** (candidates mined from the log) and **Active rollups** (rollups you've already built).

The toggle does not lose your editor state — switch back to **Editor** at any time to keep working on SQL.

At the top of the panel is a short explainer and a **Refresh** button (the circular-arrows icon). Click **Refresh** any time to re-mine the query log and re-load the built rollups — useful after running more queries or after a scheduled build.

---

## Reading a suggestion

Each card under **Suggested rollups** represents one mined candidate, ranked by score (highest first). A card shows:

| On the card | What it means |
|---|---|
| **Table name** (database icon) | The base fact table the rollup would aggregate. |
| **score** badge | The rank key. Higher = more worth building. It is `frequency × scanned-bytes`, so it favours patterns that are both *frequent* and *expensive*. |
| **N hits in the log** | How many logged queries matched this pattern. |
| **~X scanned** | Estimated bytes the pattern scans against the base table. |
| **group by** chips | The `GROUP BY` columns (dimensions) the rollup would be grouped on. |
| **measures** chips | The aggregates that would be materialized, e.g. `sum(amount)`, `count(*)`. |
| **filters** chips | Columns Nubi saw in `WHERE` clauses of the clustered queries (shown only when present). |

Suggestions whose shape you've already built drop off the list automatically, so what remains is always still actionable.

If you see **"No suggestions yet"**, run a few aggregating queries from the **Editor** and come back — the miner needs a pattern to repeat (3+ times) before it appears.

---

## Building a rollup (one click)

Building materializes the rollup table once and registers it so future matching queries route to it.

1. Open the **Rollups** panel and find the suggestion you want.
2. Click the **Build** button on its card.
3. The button shows **Building…** with a spinner while Nubi materializes the rollup, then flips to **Built** with a checkmark.
4. The new rollup appears immediately under **Active rollups**, and the suggestion disappears from the suggestions list.

That's it — no shape to re-enter. Nubi resolves the table, dimensions, and measures from the mined candidate for you.

### Who can build

Building requires **writer** access. If you have read-only access, the card shows a **Read-only** label instead of a Build button, and the suggestions and active rollups are still fully visible. Ask a writer (or admin) on your org to build the rollups you need.

### If a build fails

If a build can't complete, the card shows an inline error in red beneath it. The most common cause is insufficient permissions ("You need writer access to build a rollup."). Other failures surface the backend message directly. Fix the cause (e.g. get writer access) and click **Build** again.

---

## Active rollups

Each card under **Active rollups** is a rollup that has been built and is live. A card shows:

| On the card | What it means |
|---|---|
| **Rollup table name** + **active** badge | The materialized rollup table; the green badge confirms it's serving. |
| **N hits** badge (lightning icon) | How many incoming queries have been **routed to this rollup**. This is the headline metric — it tells you the rollup is earning its keep. |
| **from `<source_table>`** | The base table the rollup was built from (and the datastore it's served through, when set). |
| **group by** chips | The dimensions the rollup is grouped on. |
| **measures** chips | The materialized aggregates. |
| **rls keys** chips | Tenant key columns preserved so per-viewer row-level security still holds (shown when present). |
| Monospace id at the bottom | The rollup's stable id, for reference/support. |

### Reading the HIT count

The **hits** number is your signal for whether a rollup is worth keeping:

- **Rising hits** — the rollup is actively accelerating real traffic. Keep it.
- **Zero / flat hits** — nothing is routing to it. Either the matching queries stopped running, or their shape drifted. It's a candidate to ignore or rebuild against the current pattern.

Click **Refresh** to pull the latest hit counts.

---

## How rollups accelerate your queries

Once a rollup is built, you don't change anything in your SQL, your dashboards, or your embeds. Routing is **transparent**:

1. A query comes in.
2. The planner checks the registered rollups. If the query's dimensions are a **subset** of a built rollup's dimensions (superset routing) and its measures are covered, the query can be answered from the rollup instead of the base table.
3. The query reads the small pre-aggregated table — far fewer bytes scanned — and that rollup's **hit** count increments.

Concretely, this means:

- **Dashboards and embeds load faster** — every viewer that hits a matching widget reads the rollup, not the raw fact table.
- **Repeated queries get cheaper** — you pay the aggregation cost once at build time, then serve cheap reads.
- **No query rewrites** — the same registered query or ad-hoc SQL automatically benefits; you never reference the rollup by name.

This pairs naturally with Nubi's content-hashed cache and with [materialized SQL cells in Flows](/docs/flows): pay the aggregation cost once, serve cheap reads forever.

---

## Row-level security stays sound

A rollup is only safe for multi-tenant data if it keeps the tenant key columns. When a rollup is built with **RLS keys**, those columns are preserved and grouped on, so the planner injects `WHERE <key> = <claim>` against the rollup exactly as it would against the base table.

Routing a query to a rollup **never widens** what a viewer can see — the rollup's **rls keys** chips on its Active card tell you which tenant keys are preserved.

---

## Keeping rollups fresh

A rollup is a snapshot of the base table at build time. As new data lands, rebuild to keep it current. Two ways:

- **Manually** — open the **Rollups** panel, click **Refresh**, and **Build** the suggestion again when its shape resurfaces.
- **On a schedule** — create a Flow whose Python cell refreshes the rollups, then attach a schedule to that flow (see [Flows → Scheduling](/docs/flows#scheduling)). The refresh mines the query log and builds every candidate above the threshold in one pass, returning `{org_id, candidates_found, rollups_built, rollup_ids, errors}`, so rollups keep up as query patterns shift.

---

## Tips

- **Build the top of the list first.** Score already weighs frequency against scanned-bytes, so the highest-scoring suggestion is usually the best return on a build.
- **Watch the hits, not just the score.** A high-score suggestion proves the *past* pattern was hot; a high **hits** count on an Active rollup proves it's *still* paying off.
- **Let the log fill up.** Right after a fresh deploy or a new dashboard, run the queries a few times (or wait for real traffic) so the miner has signal. Nothing is suggested below 3 hits.
- **Read-only? You can still plan.** Suggestions and active rollups are visible to everyone — use the panel to decide what to ask a writer to build.

---

## Related

- [Flows](/docs/flows#scheduling) — schedule a Flow whose Python cell refreshes rollups on a cadence.
- [Queries & Parameters](/docs/queries-and-params) — registered queries that rollups transparently accelerate.
- [Dashboards](/docs/dashboards) — the embedded widgets that benefit most from rollups.
- [Materialized cells](/docs/flows#materialized-sql-cells) — multi-source materialization for joins across sources using SQL cells in Flows.
