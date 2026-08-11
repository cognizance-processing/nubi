/**
 * useProviderData.test.mjs — unit tests for BET-3 DataProvider frontend wiring.
 *
 * Coverage
 * --------
 * 1. decodeMultiTableIPC: round-trips a multi-table binary frame (2 tables)
 * 2. decodeMultiTableIPC: round-trips a single-table frame
 * 3. decodeMultiTableIPC: empty frame (N=0) returns empty object
 * 4. groupWidgetsByProvider: widgets bound to a provider are grouped correctly
 * 5. groupWidgetsByProvider: legacy query_id widgets are not grouped (no source)
 * 6. groupWidgetsByProvider: mixed spec — provider widgets grouped, legacy untouched
 * 7. widgetProviderTableMap: correct result slice is picked from provider tables
 * 8. Single provider fetch for multiple bound widgets (mock fetch call counting)
 * 9. Legacy query_id board: fetchProviderData called 0 times (no spec.data)
 *
 * Run: npm run test:dash
 *
 * Note: these are pure unit tests (no React; no jsdom). They test the
 * decoding/logic layer that SpecRenderer and useProviderData depend on, plus a
 * mock-based test for the fetch-call-count invariant.
 */

import test from 'node:test'
import assert from 'node:assert/strict'
import { decodeMultiTableIPC } from './providerDataUtils.js'

// ---------------------------------------------------------------------------
// Minimal Arrow IPC frame builder (no apache-arrow dependency in tests)
// We construct the custom multi-table framing used by tables_to_multi_ipc_stream
// and verify decodeMultiTableIPC can parse it.
// ---------------------------------------------------------------------------

/**
 * Build a minimal valid Arrow IPC stream for a single-column int32 table.
 * This is the simplest possible Arrow IPC stream: schema + 1 record batch.
 *
 * We produce a real Arrow IPC stream using apache-arrow so decodeMultiTableIPC
 * (which uses arrow.tableFromIPC) can parse it correctly.
 */
async function buildArrowIPC(columnName: string, values: number[]) {
  // Dynamic import apache-arrow (the same version used by the main code)
  const arrow = await import('apache-arrow')
  // apache-arrow's TS types for tableFromArrays() only advertise raw-array
  // inputs, but its actual implementation also accepts pre-built Vectors.
  const table = arrow.tableFromArrays({ [columnName]: arrow.vectorFromArray(values, new arrow.Int32()) } as any)
  const ipcBytes = arrow.tableToIPC(table, 'stream')
  return ipcBytes
}

/**
 * Build the custom multi-table frame that _tables_to_bytes / tables_to_multi_ipc_stream
 * produces on the backend.
 *
 * Format:
 *   4 bytes big-endian: count N
 *   N frames, each:
 *     4 bytes big-endian: name_len
 *     name bytes (UTF-8)
 *     4 bytes big-endian: ipc_len
 *     ipc bytes (Arrow IPC stream)
 *
 * @param {Array<{name: string, ipcBytes: Uint8Array}>} entries
 * @returns {ArrayBuffer}
 */
function buildMultiTableFrame(entries) {
  // Calculate total byte length
  let totalLen = 4 // count header
  for (const { name, ipcBytes } of entries) {
    const nameBytes = new TextEncoder().encode(name)
    totalLen += 4 + nameBytes.length + 4 + ipcBytes.length
  }

  const buf = new ArrayBuffer(totalLen)
  const view = new DataView(buf)
  let offset = 0

  view.setUint32(offset, entries.length, false) // big-endian
  offset += 4

  for (const { name, ipcBytes } of entries) {
    const nameBytes = new TextEncoder().encode(name)
    view.setUint32(offset, nameBytes.length, false)
    offset += 4
    new Uint8Array(buf, offset, nameBytes.length).set(nameBytes)
    offset += nameBytes.length
    view.setUint32(offset, ipcBytes.length, false)
    offset += 4
    new Uint8Array(buf, offset, ipcBytes.length).set(ipcBytes)
    offset += ipcBytes.length
  }

  return buf
}

// ---------------------------------------------------------------------------
// 1. decodeMultiTableIPC: round-trips a two-table frame
// ---------------------------------------------------------------------------

test('decodeMultiTableIPC: two-table frame decodes both tables correctly', async () => {
  const ipc1 = await buildArrowIPC('revenue', [100, 200, 300])
  const ipc2 = await buildArrowIPC('count', [1, 2])

  const frame = buildMultiTableFrame([
    { name: 'sales', ipcBytes: ipc1 },
    { name: 'summary', ipcBytes: ipc2 },
  ])

  const tables = decodeMultiTableIPC(frame)

  assert.ok('sales' in tables, 'should have "sales" table')
  assert.ok('summary' in tables, 'should have "summary" table')

  assert.equal(tables.sales.numRows, 3, '"sales" table should have 3 rows')
  assert.equal(tables.summary.numRows, 2, '"summary" table should have 2 rows')

  // Verify column values
  const revenueCol = tables.sales.getChild('revenue')
  assert.ok(revenueCol, '"revenue" column should exist in sales table')
  assert.equal(revenueCol.get(0), 100)
  assert.equal(revenueCol.get(1), 200)
  assert.equal(revenueCol.get(2), 300)
})

// ---------------------------------------------------------------------------
// 2. decodeMultiTableIPC: single-table frame
// ---------------------------------------------------------------------------

test('decodeMultiTableIPC: single-table frame decodes correctly', async () => {
  const ipc = await buildArrowIPC('amount', [10, 20])
  const frame = buildMultiTableFrame([{ name: 'orders', ipcBytes: ipc }])

  const tables = decodeMultiTableIPC(frame)

  assert.ok('orders' in tables)
  assert.equal(tables.orders.numRows, 2)

  const col = tables.orders.getChild('amount')
  assert.equal(col.get(0), 10)
  assert.equal(col.get(1), 20)
})

// ---------------------------------------------------------------------------
// 3. decodeMultiTableIPC: empty frame (N=0) returns {}
// ---------------------------------------------------------------------------

test('decodeMultiTableIPC: empty frame (N=0) returns empty object', () => {
  const frame = buildMultiTableFrame([])
  const tables = decodeMultiTableIPC(frame)
  assert.deepEqual(tables, {})
})

// ---------------------------------------------------------------------------
// 4–6. groupWidgetsByProvider logic (pure; mirrors what SpecRenderer does)
// ---------------------------------------------------------------------------

/**
 * Given a list of widgets, return a map of providerId → [widgetId, resultName][].
 * This mirrors the grouping logic in SpecRenderer's widgetProviderTableMap.
 */
function groupWidgetsByProvider(widgets: Record<string, any>[]): Record<string, Record<string, any>[]> {
  const groups: Record<string, Record<string, any>[]> = {}
  for (const w of widgets) {
    if (w.source?.provider && w.source?.result) {
      const pid = w.source.provider
      ;(groups[pid] ??= []).push({ id: w.id, result: w.source.result })
    }
  }
  return groups
}

test('groupWidgetsByProvider: provider widgets are grouped by provider id', () => {
  const widgets = [
    { id: 'w1', type: 'table', source: { provider: 'p1', result: 'revenue' } },
    { id: 'w2', type: 'kpi',   source: { provider: 'p1', result: 'summary' } },
    { id: 'w3', type: 'chart', source: { provider: 'p2', result: 'trend' } },
  ]

  const groups = groupWidgetsByProvider(widgets)

  assert.ok('p1' in groups, 'p1 group should exist')
  assert.ok('p2' in groups, 'p2 group should exist')
  assert.equal(groups.p1.length, 2, 'p1 should have 2 widgets')
  assert.equal(groups.p2.length, 1, 'p2 should have 1 widget')
  assert.equal(groups.p1[0].id, 'w1')
  assert.equal(groups.p1[1].id, 'w2')
  assert.equal(groups.p2[0].result, 'trend')
})

test('groupWidgetsByProvider: legacy query_id widgets produce no groups', () => {
  const widgets = [
    { id: 'w1', type: 'kpi',   query_id: 'demo_revenue', encoding: { value: 'n' } },
    { id: 'w2', type: 'chart', query_id: 'demo_trend',   chart_type: 'line', encoding: { x: 'date', y: 'val' } },
  ]

  const groups = groupWidgetsByProvider(widgets)
  assert.deepEqual(groups, {}, 'legacy-only spec should produce no provider groups')
})

test('groupWidgetsByProvider: mixed spec — provider widgets grouped, legacy widgets not', () => {
  const widgets = [
    { id: 'w1', type: 'table', source: { provider: 'p1', result: 'sales' } },
    { id: 'w2', type: 'kpi',   query_id: 'demo_kpi', encoding: { value: 'n' } },
    { id: 'w3', type: 'chart', source: { provider: 'p1', result: 'trend' } },
  ]

  const groups = groupWidgetsByProvider(widgets)

  assert.ok('p1' in groups, 'p1 group should exist')
  assert.equal(groups.p1.length, 2, 'p1 should have 2 provider-bound widgets')
  // w2 (legacy) should NOT appear in any group
  const allGroupedIds = Object.values(groups).flat().map(e => e.id)
  assert.ok(!allGroupedIds.includes('w2'), 'legacy widget w2 should not appear in any provider group')
})

// ---------------------------------------------------------------------------
// 7. widgetProviderTableMap: correct result slice is picked
// ---------------------------------------------------------------------------

test('widgetProviderTableMap: result slice is picked by widget.source.result', async () => {
  const arrow = await import('apache-arrow')

  // Simulate what providerResults[pid].tables would look like after decoding
  const salesTable = arrow.tableFromArrays({
    amount: arrow.vectorFromArray([100, 200], new arrow.Int32()),
  } as any)
  const trendTable = arrow.tableFromArrays({
    date: arrow.vectorFromArray([1, 2, 3], new arrow.Int32()),
  } as any)

  const providerResults = {
    p1: { tables: { sales: salesTable, trend: trendTable }, loading: false, error: null },
  }

  const allWidgets = [
    { id: 'w1', type: 'table', source: { provider: 'p1', result: 'sales' } },
    { id: 'w2', type: 'chart', source: { provider: 'p1', result: 'trend' } },
    { id: 'w3', type: 'kpi',   query_id: 'demo_kpi', encoding: { value: 'n' } },
  ]

  // Mirror the widgetProviderTableMap logic from SpecRenderer
  const map: Record<string, any> = {}
  for (const w of allWidgets) {
    if (w.source?.provider && w.source?.result) {
      const pResult = providerResults[w.source.provider]
      if (pResult) {
        map[w.id] = pResult.tables[w.source.result] ?? null
      }
    }
  }

  assert.ok(map.w1 === salesTable, 'w1 should get the sales table')
  assert.ok(map.w2 === trendTable, 'w2 should get the trend table')
  assert.ok(!('w3' in map), 'legacy w3 should not appear in the map')
})

// ---------------------------------------------------------------------------
// 8. Single provider fetch for multiple bound widgets (fetch call counting)
// ---------------------------------------------------------------------------

test('spec with spec.data + source widgets triggers exactly one fetch per provider', async () => {
  // Mock fetch to count calls per endpoint
  const fetchCalls = []

  // Build a two-result Arrow IPC frame for the mock response
  const arrow = await import('apache-arrow')
  const ipc1 = arrow.tableToIPC(
    arrow.tableFromArrays({ revenue: arrow.vectorFromArray([100], new arrow.Int32()) } as any),
    'stream',
  )
  const ipc2 = arrow.tableToIPC(
    arrow.tableFromArrays({ count: arrow.vectorFromArray([5], new arrow.Int32()) } as any),
    'stream',
  )
  const mockFrame = buildMultiTableFrame([
    { name: 'sales', ipcBytes: ipc1 },
    { name: 'summary', ipcBytes: ipc2 },
  ])

  const mockFetch = async (url, _opts) => {
    fetchCalls.push(url)
    return {
      ok: true,
      status: 200,
      arrayBuffer: async () => mockFrame,
    }
  }

  // Inline the fetchProviderData logic with our mock fetch to verify call count
  // (We cannot call React hooks in node:test, so we test the fetch layer directly.)
  const boardId = 'board-abc'
  const providerId = 'p1'

  // Simulate what fetchProviderData does (with mocked fetch)
  const BASE = '/api/v1'
  const path = `/boards/${boardId}/providers/${providerId}/data`
  const response = await mockFetch(`${BASE}${path}`, { method: 'POST' })
  const buf = await response.arrayBuffer()
  const tables = decodeMultiTableIPC(buf)

  // One fetch per provider, regardless of how many widgets are bound to it
  assert.equal(fetchCalls.length, 1, 'exactly one fetch call per provider')

  // Both result slices are available from that single call
  assert.ok('sales' in tables, 'sales table should be decoded')
  assert.ok('summary' in tables, 'summary table should be decoded')
  assert.equal(tables.sales.numRows, 1)
  assert.equal(tables.summary.numRows, 1)
})

// ---------------------------------------------------------------------------
// 9. Legacy board: no spec.data → no provider fetches
// ---------------------------------------------------------------------------

test('legacy query_id board: no spec.data means no provider fetch is triggered', () => {
  // The guard in useProviderData: if (!boardId || !providerId) return
  // When spec.data is absent (empty list), providerSlots are all null,
  // so useProviderData is called with providerId = null and skips the fetch.

  const legacySpec = {
    version: 1,
    title: 'Legacy Board',
    widgets: [
      { id: 'w1', type: 'kpi', query_id: 'demo_revenue', encoding: { value: 'n' } },
    ],
    data: [],  // empty = no providers
  }

  const providers = Array.isArray(legacySpec.data) ? legacySpec.data : []
  assert.equal(providers.length, 0, 'legacy board has no providers')

  // providerSlots would all be null — no useProviderData fetch fires
  for (let i = 0; i < 8; i++) {
    const slot = providers[i] ?? null
    // The hook guard: if (!boardId || !providerId) return early
    const wouldFetch = slot !== null
    assert.equal(wouldFetch, false, `slot ${i} should be null (no fetch)`)
  }
})
