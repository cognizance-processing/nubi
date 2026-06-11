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
  BIGQUERY_REFERENCE,
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

// NOTE: the head-to-head WAREHOUSE competitor models (bigquery_ondemand /
// clickhouse_cloud with a `.model()` cost function) were intentionally removed in
// the pricing reframe — FALLBACK_COMPETITORS_WAREHOUSE is now empty and the
// lakehouse is no longer positioned as a warehouse competitor (connect your own
// BigQuery/Snowflake instead). BigQuery is kept only as a REFERENCE object for the
// "~20% cheaper on scan" copy; assert those reference rates instead.

test('BIGQUERY_REFERENCE keeps the pay-per-scan reference rates (Nubi undercuts on scan)', () => {
  assert.equal(BIGQUERY_REFERENCE.id, 'bigquery_ondemand')
  assert.equal(BIGQUERY_REFERENCE.scan_usd_per_tib, 6.25) // Nubi: $5/TiB → ~20% cheaper
  assert.equal(BIGQUERY_REFERENCE.storage_usd_per_gb, 0.02)
  assert.equal(BIGQUERY_REFERENCE.free_scan_tib, 1)
  assert.equal(BIGQUERY_REFERENCE.free_storage_gb, 10)
})

test('the warehouse head-to-head competitor list is empty (reframe: not a warehouse competitor)', () => {
  assert.deepEqual(FALLBACK_COMPETITORS_WAREHOUSE, [])
})
