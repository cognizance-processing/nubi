import test from 'node:test'
import assert from 'node:assert/strict'

import {
  normalizeName, isRef, boundParamFor, referencedVars, candidateParam,
  bindParam, unbindParam, unbindVar, autoBindParams, wiringRows,
  connectedCount, unfilledParams,
} from './paramWiring.js'

const P = (...names) => names.map(name => ({ name, type: 'text' }))

// ── name folding ─────────────────────────────────────────────────────────────

test('normalizeName folds case and separators but not word boundaries', () => {
  assert.equal(normalizeName('Region'), 'region')
  assert.equal(normalizeName('store_region'), 'storeregion')
  assert.equal(normalizeName('store-region'), 'storeregion')
  assert.equal(normalizeName('store region'), 'storeregion')
  assert.notEqual(normalizeName('region_id'), normalizeName('region'))
})

// ── binding shape ────────────────────────────────────────────────────────────

test('isRef distinguishes a variable reference from a literal', () => {
  assert.equal(isRef({ ref: 'region' }), true)
  assert.equal(isRef('Gauteng'), false)
  assert.equal(isRef(null), false)
  assert.equal(isRef(0), false)
})

test('boundParamFor finds the param carrying a variable', () => {
  const params = { region: { ref: 'region' }, limit: 50 }
  assert.equal(boundParamFor(params, 'region'), 'region')
  assert.equal(boundParamFor(params, 'city'), null)
  assert.equal(boundParamFor(undefined, 'region'), null)
})

test('boundParamFor matches by variable, not by param name', () => {
  // The param is called `store_region` but carries the `region` variable.
  const params = { store_region: { ref: 'region' } }
  assert.equal(boundParamFor(params, 'region'), 'store_region')
})

test('referencedVars lists each variable once', () => {
  const params = { a: { ref: 'region' }, b: { ref: 'region' }, c: { ref: 'city' }, d: 5 }
  assert.deepEqual(referencedVars(params), ['region', 'city'])
  assert.deepEqual(referencedVars(undefined), [])
})

// ── candidate selection ──────────────────────────────────────────────────────

test('candidateParam prefers an exact name match', () => {
  assert.equal(candidateParam(P('region', 'Region'), 'region'), 'region')
})

test('candidateParam accepts a single normalised match', () => {
  assert.equal(candidateParam(P('Region'), 'region'), 'Region')
  assert.equal(candidateParam(P('store_region'), 'store region'), 'store_region')
})

test('candidateParam refuses to guess between two normalised matches', () => {
  assert.equal(candidateParam(P('Region', 'region_'), 'region'), null)
})

test('candidateParam does not match a merely similar name', () => {
  assert.equal(candidateParam(P('region_id', 'regions'), 'region'), null)
  assert.equal(candidateParam([], 'region'), null)
  assert.equal(candidateParam(undefined, 'region'), null)
})

// ── mutation helpers ─────────────────────────────────────────────────────────

test('bindParam and unbindParam do not mutate the input', () => {
  const params = { limit: 50 }
  const bound = bindParam(params, 'region', 'region')
  assert.deepEqual(params, { limit: 50 })
  assert.deepEqual(bound, { limit: 50, region: { ref: 'region' } })
  assert.deepEqual(unbindParam(bound, 'region'), { limit: 50 })
})

test('unbindVar removes the param carrying that variable, whatever it is called', () => {
  const params = { store_region: { ref: 'region' }, limit: 50 }
  assert.deepEqual(unbindVar(params, 'region'), { limit: 50 })
  // Unknown variable: unchanged copy, never a throw.
  assert.deepEqual(unbindVar(params, 'city'), params)
})

// ── auto-binding ─────────────────────────────────────────────────────────────

test('autoBindParams binds every unambiguous match and reports what it did', () => {
  const { params, added } = autoBindParams({}, P('region', 'city', 'limit'), ['region', 'city'])
  assert.deepEqual(params, { region: { ref: 'region' }, city: { ref: 'city' } })
  assert.deepEqual(added, [
    { param: 'region', variable: 'region' },
    { param: 'city', variable: 'city' },
  ])
})

test('autoBindParams never overwrites an existing binding', () => {
  const existing = { region: 'Gauteng' }        // deliberately a literal
  const { params, added } = autoBindParams(existing, P('region'), ['region'])
  assert.deepEqual(params, { region: 'Gauteng' })
  assert.deepEqual(added, [])
})

test('autoBindParams leaves a variable alone when it is already wired elsewhere', () => {
  const existing = { store_region: { ref: 'region' } }
  const { params, added } = autoBindParams(existing, P('region', 'store_region'), ['region'])
  assert.deepEqual(params, existing)
  assert.deepEqual(added, [])
})

test('autoBindParams is a no-op without variables or declared params', () => {
  assert.deepEqual(autoBindParams({}, P('region'), []).added, [])
  assert.deepEqual(autoBindParams({}, [], ['region']).added, [])
  assert.deepEqual(autoBindParams(undefined, undefined, undefined).params, {})
})

test('autoBindParams skips an ambiguous match rather than picking one', () => {
  const { params, added } = autoBindParams({}, P('Region', 'region_'), ['region'])
  assert.deepEqual(params, {})
  assert.deepEqual(added, [])
})

// ── wiring rows ──────────────────────────────────────────────────────────────

const BOARD = [
  { id: 'w1', type: 'chart', title: 'Sales', query_id: 'q1', params: { region: { ref: 'region' } } },
  { id: 'w2', type: 'table', title: 'Detail', query_id: 'q1' },
  { id: 'w3', type: 'kpi', title: 'Total', query_id: 'q2' },                 // params, none matching
  { id: 'w4', type: 'chart', title: 'Static', query_id: 'q3' },              // no params
  { id: 'w5', type: 'chart', title: 'Unbound', query_id: 'q_unloaded' },     // not loaded
  { id: 'w6', type: 'filter', title: 'Region', target_var: 'region' },       // excluded
  { id: 'w7', type: 'text', title: 'Notes' },                                // excluded
]
const PARAMS = new Map([
  ['q1', P('region', 'limit')],
  ['q2', P('store', 'month')],
  ['q3', []],
])

test('wiringRows classifies every data widget and skips the rest', () => {
  const rows = wiringRows({ widgets: BOARD, varName: 'region', paramsByQueryId: PARAMS })
  assert.deepEqual(rows.map(r => r.id), ['w1', 'w2', 'w3', 'w4', 'w5'])
  assert.deepEqual(rows.map(r => r.state), [
    'connected', 'available', 'choose', 'no-param', 'unknown',
  ])
})

test('wiringRows names the param that would be bound on connect', () => {
  const rows = wiringRows({ widgets: BOARD, varName: 'region', paramsByQueryId: PARAMS })
  assert.equal(rows.find(r => r.id === 'w1').paramName, 'region')  // already bound
  assert.equal(rows.find(r => r.id === 'w2').paramName, 'region')  // would bind
  assert.equal(rows.find(r => r.id === 'w3').paramName, null)      // must be chosen
  assert.deepEqual(rows.find(r => r.id === 'w3').options, ['store', 'month'])
})

test('wiringRows carries each row\'s query id', () => {
  // A `no-param` row needs it to offer "add this filter to its query" —
  // without it the only way forward is hand-editing the query's SQL.
  const rows = wiringRows({ widgets: BOARD, varName: 'region', paramsByQueryId: PARAMS })
  assert.equal(rows.find(r => r.id === 'w4').state, 'no-param')
  assert.equal(rows.find(r => r.id === 'w4').queryId, 'q3')
  assert.equal(rows.find(r => r.id === 'w1').queryId, 'q1')
})

test('wiringRows accepts a plain object index as well as a Map', () => {
  const rows = wiringRows({
    widgets: [BOARD[0]], varName: 'region',
    paramsByQueryId: { q1: P('region') },
  })
  assert.equal(rows[0].state, 'connected')
})

test('wiringRows resolves library-reference entries before classifying', () => {
  const entry = { id: 'r1', ref: 'lib1', pos: { x: 1, y: 1, w: 3, h: 3 } }
  const rows = wiringRows({
    widgets: [entry],
    varName: 'region',
    paramsByQueryId: PARAMS,
    resolve: w => (w.ref ? { ...w, type: 'chart', title: 'Shared', query_id: 'q1' } : w),
  })
  assert.equal(rows.length, 1)
  assert.equal(rows[0].state, 'available')
  assert.equal(rows[0].label, 'Shared')
})

test('wiringRows falls back to the widget id when it has no title', () => {
  const rows = wiringRows({
    widgets: [{ id: 'chart_9', type: 'chart', query_id: 'q1' }],
    varName: 'region', paramsByQueryId: PARAMS,
  })
  assert.equal(rows[0].label, 'chart_9')
})

test('connectedCount counts only live connections', () => {
  const rows = wiringRows({ widgets: BOARD, varName: 'region', paramsByQueryId: PARAMS })
  assert.equal(connectedCount(rows), 1)
})

// ── dangling params ──────────────────────────────────────────────────────────

test('unfilledParams reports params no variable can fill', () => {
  const declared = P('region', 'city', 'limit')
  const out = unfilledParams(declared, { limit: 100 }, ['Region'])
  assert.deepEqual(out.map(p => p.name), ['city'])
})

test('unfilledParams is empty when everything is covered', () => {
  assert.deepEqual(unfilledParams(P('region'), {}, ['region']), [])
  assert.deepEqual(unfilledParams(undefined, {}, ['region']), [])
})

// ── label → variable name ────────────────────────────────────────────────────

test('varNameFromLabel slugs a human label into a usable placeholder name', async () => {
  const { varNameFromLabel } = await import('./paramWiring.js')
  assert.equal(varNameFromLabel('Region'), 'region')
  assert.equal(varNameFromLabel('Store group'), 'store_group')
  assert.equal(varNameFromLabel('  Sub-Brand!  '), 'sub_brand')
  assert.equal(varNameFromLabel('2024 target'), 'v_2024_target')
  assert.equal(varNameFromLabel(''), '')
  assert.equal(varNameFromLabel('---'), '')
})
