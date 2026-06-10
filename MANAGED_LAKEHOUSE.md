# Managed Lakehouse — DuckDB optimizer + universal pre-agg + bytes-scanned billing

**Status:** design · **Engine decision: DuckDB only** (browser + server, no chDB/DataFusion —
parity with the browser kernel is the moat). **Storage: Cloudflare R2** (free egress).

## Thesis
Make Nubi feel like BigQuery — *you never touch the physical layer* — while keeping the
"no viewer tax" wedge. One self-managing optimizer owns the mapping from **logical tables you
query** → **physical structures it maintains** (layout, materializations, rewrite). Automatic by
default, customizable when you care. The same system sits in front of **every connector** —
including paid warehouses — so repeated reads collapse to ~$0.

## 1. The optimizer is application logic on DuckDB (not a new engine)
Pre-agg splits into three parts; only the third is per-connector:
1. **Rewrite/routing** — `route_to_rollup_shape` in the planner (sqlglot). Connector-agnostic:
   "this GROUP-BY shape is covered by rollup R → read R, preserve RLS." Doesn't care where base data lives.
2. **Materialization** — ALWAYS lands in the lakehouse (Parquet in R2, queried by DuckDB), regardless of source.
3. **Refresh** — the ONLY per-connector bit, and it already exists: run the aggregate via `connector.execute()`.

So one miner + one rewriter + one store, used uniformly. Adding a connector contributes ZERO pre-agg code.

## 2. Universal pre-agg across ALL connectors (removes redundancy)
- **Observe:** the rollup miner reads the query log (source-agnostic).
- **Decide:** rank candidate rollups by `frequency × estimated-bytes-saved`. `Connector.estimate` (A8)
  is MOST valuable here — for a warehouse it gives the real $ a base query costs, so build rollups
  exactly where pushdown is expensive.
- **Maintain:** incremental refresh via the connector (`WHERE ts > watermark`); `MaterializedConfig`
  already has incremental/watermark. Lambda freshness (serve-stale + async refresh) so dashboards never block.
- **Rewrite:** `route_to_rollup_shape`, universal. Rollup-or-pushdown fallback (uncovered → pushdown).
- **Invariants:** RLS filter columns stay IN the rollup grain (so per-tenant filtering survives the rewrite —
  `materialize.py` already checks rls_keys); sketches (HLL for distinct, t-digest for percentiles) for
  non-additive measures so grains compose. Only the hot tail gets rolled up; cold ad-hoc → pushdown.
- **Value:** a BigQuery-backed dashboard stops re-billing per viewer — warehouse hit only on refresh.

## 3. Scale-to-zero execution (kills idle cost)
- Execute heavy/batch queries on **scale-to-zero compute** (Fly Machines wake/sleep — `auto_stop_machines`
  already in fly.toml) → bill query-seconds, not VM-hours. Keep a small **always-warm tier** for interactive
  latency (cold-start guard). The existing standard-vs-heavy-pool split models this.
- **Pruning:** Iceberg/Delta manifest pruning + sorted/partitioned/zstd Parquet with column stats + httpfs
  range requests → a filtered query on a 600 GB table reads MBs. "Scale" = a layout problem, not compute.
- Truly enormous all-pairs joins still → **pushdown** to the customer's warehouse (top of the ladder).

## 4. Auto partition/cluster (BigQuery feel)
Declarative defaults + auto-maintain (posture C+A): auto-detect a time column → partition by day/month +
high-selectivity filter columns → cluster; auto-build only materializations whose estimated savings clear a
threshold; everything rewritten automatically. Per-table override surface in `nubi.toml`/UI
(`partition_by`, `cluster_by`, `materialize`, `freshness`, `auto_optimize: on/off`).

## 5. Bytes-scanned billing (grounded — see tiers.py)
Switch the billed METRIC from compute-seconds×4 to **bytes-scanned** (BigQuery-comparable + the optimizer
visibly shrinks the bill + free-viewer wedge shows up). Grounded math (CU = 1 compute-second @ R0.10/CU,
77% margin → COGS R0.023/CU; 1 TiB ≈ ~1000 compute-seconds):
- **Query: ~$5/TiB scanned, first 1 TiB/mo free** (BQ is $6.25). Margin 72% (conservative COGS) to ~98%
  (marginal). DROP the 4× warehouse multiplier — "warehouse vs standard" disappears from the invoice.
- **Storage: ~$0.02/GB-mo (R2)** — TODAY Nubi charges R1.50/GB = $0.09 (4.5× BQ); own COGS is $0.0145 →
  cut to ~R0.33/GB for BQ parity, still margin-positive. **This is the real "we're higher than everyone" fix.**
- **Pre-run estimate** = BQ dry-run parity: wire `Connector.estimate` → `/query/estimate` + a UI chip.
- Free-tier + `maximum_bytes_billed`-style per-org cap (runaway guard). USD-anchored, ZAR @ FX, billing in ee/.

## WAVE 4 task breakdown (agents)
- **W4-A [core]** meter bytes-scanned: capture post-pruning Parquet bytes read per query (DuckDB
  `parquet_metadata`/httpfs counters) → `record_usage(kind="query_scan", units=bytes)` (core metering).
- **W4-B [ee]** `ee/billing/tiers` + reconcile: add `$/TiB` scan rate, drop WAREHOUSE_CU_MULTIPLIER, cut
  storage to R2 parity; reconcile sums query_scan bytes.
- **W4-C [core]** `/query/estimate` route (reuse the extracted plan/connector resolver) + the UI estimate chip.
- **W4-D [core]** universal rollup refresh-via-connector: generalize the materialize path so any connector's
  aggregate lands as a rollup in R2; extend `route_to_rollup_shape` to also prune partitions.
- **W4-E [core]** scale-to-zero query executor harness on Fly Machines + warm tier (interactive).
- **W4-F [core]** auto partition/cluster manager + `nubi.toml` override surface; Iceberg/Delta read + Parquet
  layout (sorted/zstd/stats).
- **W4-G [frontend]** cascading/circular reactive filter graph (deferred from Wave 3) — edges from
  `{{vars.*}}`/filter refs, topo-walk dirty subgraph, cycle detection at graph-build (reject); option-queries
  ride the same pre-agg/edge-cache.

## Invariants
DuckDB only. RLS in rollup grain + preserved through rewrite. Bound params never concatenated. Open-core:
billing in ee/, metering hook in core. Secrets never in synced files.
