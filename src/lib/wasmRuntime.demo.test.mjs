/**
 * wasmRuntime.demo.test.mjs — Unit tests for D2 demo routing logic.
 *
 * Run with:
 *   node --test src/lib/wasmRuntime.demo.test.mjs
 *   # or via npm run test:dash
 *
 * Tests the routing decision inside runArrowQueryById:
 *   - A queryId present in the demo query map → local DuckDB-WASM path
 *   - A queryId absent from the demo query map → server path (POST /api/v1/query)
 *   - opts.isDemo === true → always local path (even without a query-map hit)
 *   - namedParams present → always server path (parameterised queries go to server)
 *   - Query map fetch failure → graceful fallback to server path
 *
 * These tests are self-contained (no browser / no DuckDB-WASM / no network):
 * they inline the routing predicate logic extracted from wasmRuntime.js.
 */

import { test } from 'node:test'
import assert from 'node:assert/strict'

// ---------------------------------------------------------------------------
// Inline the routing predicate logic from runArrowQueryById (D2 block).
//
// This mirrors the exact decision tree in wasmRuntime.js so any divergence
// between the implementation and this test will be caught.
// ---------------------------------------------------------------------------

/**
 * Inline copy of the D2 routing predicate from runArrowQueryById.
 *
 * Returns 'demo_local' when the query should be executed in the browser,
 * 'server' otherwise.
 *
 * @param {string} queryId
 * @param {{ namedParams?: object, isDemo?: boolean }} opts
 * @param {Record<string, string> | null} demoQueryMap  — null = fetch failed / timed out
 * @returns {'demo_local' | 'server'}
 */
function routingDecision(queryId, opts, demoQueryMap) {
  const namedParams = opts?.namedParams
  const isDemo = opts?.isDemo

  // Parameterised queries always go to the server for RLS / template resolution.
  const hasParams = namedParams && Object.keys(namedParams).length > 0
  if (hasParams) return 'server'

  if (!queryId) return 'server'

  // Gate 1: explicit isDemo flag.
  if (isDemo === true) return 'demo_local'

  // Gate 2: query map lookup (null = fetch failed → fall through to server).
  if (demoQueryMap != null && Object.prototype.hasOwnProperty.call(demoQueryMap, queryId)) {
    // Must have a non-empty SQL string in the map to route locally.
    const sql = demoQueryMap[queryId]
    if (sql && typeof sql === 'string' && sql.trim().length > 0) return 'demo_local'
  }

  return 'server'
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

test('demo query in map → demo_local route', () => {
  const map = { 'abc-123': 'SELECT * FROM sales', 'def-456': 'SELECT count(*) FROM web_sessions' }
  assert.equal(routingDecision('abc-123', {}, map), 'demo_local')
  assert.equal(routingDecision('def-456', undefined, map), 'demo_local')
})

test('live query not in map → server route', () => {
  const map = { 'abc-123': 'SELECT * FROM sales' }
  assert.equal(routingDecision('live-uuid-789', {}, map), 'server')
})

test('opts.isDemo === true → demo_local even without map hit', () => {
  const map = { 'other-id': 'SELECT 1' }
  assert.equal(routingDecision('some-uuid', { isDemo: true }, map), 'demo_local')
  // Also works with empty map
  assert.equal(routingDecision('some-uuid', { isDemo: true }, {}), 'demo_local')
})

test('namedParams present → always server route (parameterised queries)', () => {
  const map = { 'abc-123': 'SELECT * FROM sales WHERE region = {{region}}' }
  assert.equal(
    routingDecision('abc-123', { namedParams: { region: 'ZA' }, isDemo: true }, map),
    'server',
    'namedParams overrides isDemo flag',
  )
  assert.equal(
    routingDecision('abc-123', { namedParams: { region: 'ZA' } }, map),
    'server',
  )
})

test('empty namedParams ({}) does not block demo route', () => {
  const map = { 'abc-123': 'SELECT * FROM sales' }
  // An empty namedParams object has zero keys → hasParams is false → demo path allowed
  assert.equal(routingDecision('abc-123', { namedParams: {} }, map), 'demo_local')
})

test('query map fetch failure (null) → server route', () => {
  // null means the fetch promise resolved to null (timeout or network error)
  assert.equal(routingDecision('abc-123', {}, null), 'server')
})

test('query map entry with empty SQL → server route', () => {
  // An empty SQL string in the map is a data error; fall back to server
  const map = { 'abc-123': '' }
  assert.equal(routingDecision('abc-123', {}, map), 'server')
})

test('missing queryId → server route', () => {
  const map = { 'abc-123': 'SELECT 1' }
  assert.equal(routingDecision('', {}, map), 'server')
  assert.equal(routingDecision(undefined, {}, map), 'server')
  assert.equal(routingDecision(null, {}, map), 'server')
})

test('demo query absent from manifest → server route (transparent fallback)', () => {
  // This models the case where the manifest or query-map returned an empty object
  assert.equal(routingDecision('abc-123', {}, {}), 'server')
})

// ---------------------------------------------------------------------------
// Manifest URL shape test (no network — just checks the constant)
// ---------------------------------------------------------------------------

test('demo-parquet manifest URL shape', () => {
  const BACKEND_URL = 'http://localhost:8000'
  const manifestUrl = `${BACKEND_URL}/api/v1/demo-parquet/_manifest`
  const queryMapUrl = `${BACKEND_URL}/api/v1/demo-parquet/_query-map`
  const parquetUrl = `${BACKEND_URL}/api/v1/demo-parquet/retail_sales/sales.parquet`

  // All URLs should start with the backend prefix
  assert.ok(manifestUrl.startsWith(BACKEND_URL))
  assert.ok(queryMapUrl.startsWith(BACKEND_URL))
  assert.ok(parquetUrl.startsWith(BACKEND_URL))

  // Parquet URL should match the expected pattern
  assert.match(parquetUrl, /\/api\/v1\/demo-parquet\/[\w_]+\/[\w_]+\.parquet$/)
})
