/**
 * pricing.warehouse.test.mjs — warehouse pricing helpers: estimateWarehouseCu,
 * competitor models, and the recommendNubi minTierId floor used by the
 * "vs Warehouse / OLAP" calculator tab.
 *
 * Run: npm run test:dash  (node --test 'src/**\/*.test.mjs')
 */

import test from 'node:test'
import assert from 'node:assert/strict'
import {
  estimateWarehouseCu,
  recommendNubi,
  FALLBACK_COMPETITORS_WAREHOUSE,
  WAREHOUSE_CU_MULTIPLIER,
} from './pricing.js'

test('estimateWarehouseCu bills scan-seconds at the warehouse multiplier', () => {
  // 1000 queries × 2 GB ≈ 2 s each → 2000 s × 4 = 8000 CU
  assert.equal(
    estimateWarehouseCu({ queries_per_month: 1000, avg_gb_scanned: 2 }),
    1000 * 2 * WAREHOUSE_CU_MULTIPLIER,
  )
})

test('estimateWarehouseCu floors tiny scans at 0.1 s per query', () => {
  assert.equal(
    estimateWarehouseCu({ queries_per_month: 1000, avg_gb_scanned: 0.01 }),
    Math.ceil(1000 * 0.1 * WAREHOUSE_CU_MULTIPLIER),
  )
})

test('recommendNubi minTierId floors the recommendation at pro', () => {
  const rec = recommendNubi(
    { storage_gb: 1, compute_units: 10, embedded_sessions: 0, agent_runs: 0, connectors: 1, flow_runs_per_month: 0 },
    null,
    { minTierId: 'pro' },
  )
  assert.equal(rec.tier.id, 'pro')
})

test('recommendNubi without a floor still recommends free for tiny usage', () => {
  const rec = recommendNubi(
    { storage_gb: 1, compute_units: 10, embedded_sessions: 0, agent_runs: 0, connectors: 1, flow_runs_per_month: 0 },
    null,
  )
  assert.equal(rec.tier.id, 'free')
})

test('every warehouse competitor returns a finite positive USD estimate', () => {
  const usage = { data_gb: 100, queries_per_month: 5000, avg_gb_scanned: 2 }
  for (const comp of FALLBACK_COMPETITORS_WAREHOUSE) {
    const usd = comp.model(usage)
    assert.ok(Number.isFinite(usd), `${comp.id} returned ${usd}`)
    assert.ok(usd > 0, `${comp.id} returned ${usd}`)
  }
})

test('BigQuery on-demand gives the first scanned TB and 10 GB storage free', () => {
  const bq = FALLBACK_COMPETITORS_WAREHOUSE.find((c) => c.id === 'bigquery_ondemand')
  // 512 queries × 1 GB = 0.5 TB scanned → only storage beyond 10 GB is billed
  const usd = bq.model({ data_gb: 100, queries_per_month: 512, avg_gb_scanned: 1 })
  assert.ok(Math.abs(usd - (100 - 10) * 0.02) < 1e-9, `expected storage-only cost, got ${usd}`)
})

test('ClickHouse model is idle-aware (fair): free when idle, always-on under steady traffic', () => {
  const ch = FALLBACK_COMPETITORS_WAREHOUSE.find((c) => c.id === 'clickhouse_cloud')
  // Zero queries → service idles; only storage is billed.
  assert.equal(ch.model({ data_gb: 0, queries_per_month: 0, avg_gb_scanned: 0 }), 0)
  // Steady traffic (5000 q/mo, ~15-min idle window each) keeps it awake
  // around the clock → ~730 h × $0.40 ≈ $292 + storage.
  const busy = ch.model({ data_gb: 100, queries_per_month: 5000, avg_gb_scanned: 2 })
  assert.ok(busy > 290 && busy < 300, `expected ~$292+storage, got ${busy}`)
})
