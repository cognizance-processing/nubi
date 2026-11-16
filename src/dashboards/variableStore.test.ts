/**
 * variableStore.test.mjs — Unit tests for the resolveParams pure function.
 *
 * Run with:
 *   node --test src/dashboards/variableStore.test.mjs
 *   # or via the project test:dash script
 *
 * Only the pure resolveParams function is tested here — no React rendering,
 * no JSDOM required.  React-context behaviour is tested at integration level
 * when DashboardViewPage is wired up in M14-C.
 *
 * Behaviour contract for resolveParams(widgetParams, variables):
 *   - {ref: '<varName>'}  →  variables[varName]
 *   - literal             →  passed through as-is
 *   - unknown ref         →  undefined  (caller must treat undefined gracefully)
 *   - null/undefined widgetParams  →  {}
 *   - null/undefined variables     →  treats as empty map (refs → undefined)
 */

import { test } from 'node:test'
import assert from 'node:assert/strict'

// ---------------------------------------------------------------------------
// Import the pure function.
// VariableStore.jsx is a JSX file; Node won't execute JSX natively.
// We re-export resolveParams as a plain .mjs snippet to avoid needing
// a full transpile step.  The canonical implementation lives in
// VariableStore.jsx; this stub duplicates the logic so the test is
// self-contained and runnable with bare `node --test`.
// ---------------------------------------------------------------------------

/**
 * Inline copy of resolveParams from VariableStore.jsx.
 * If the logic in VariableStore.jsx changes, update here too.
 */
function resolveParams(widgetParams: Record<string, any>, variables: Record<string, any>): Record<string, any> {
  if (!widgetParams || typeof widgetParams !== 'object' || Array.isArray(widgetParams)) {
    return {}
  }
  if (!variables || typeof variables !== 'object') {
    variables = {}
  }

  const resolved: Record<string, any> = {}
  for (const [paramName, paramValue] of Object.entries(widgetParams)) {
    if (
      paramValue !== null &&
      typeof paramValue === 'object' &&
      !Array.isArray(paramValue) &&
      Object.prototype.hasOwnProperty.call(paramValue, 'ref')
    ) {
      resolved[paramName] = variables[paramValue.ref]
    } else {
      resolved[paramName] = paramValue
    }
  }
  return resolved
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

test('resolves a {ref} to the matching variable value', () => {
  const params    = { region: { ref: 'selectedRegion' } }
  const variables = { selectedRegion: 'EMEA' }
  const result    = resolveParams(params, variables)
  assert.equal(result.region, 'EMEA')
})

test('passes literal values through unchanged', () => {
  const params    = { limit: 100, format: 'percent' }
  const variables = {}
  const result    = resolveParams(params, variables)
  assert.equal(result.limit, 100)
  assert.equal(result.format, 'percent')
})

test('resolves mixed {ref} and literal params in the same object', () => {
  const params = {
    tenant:  { ref: 'currentTenant' },
    maxRows: 50,
    label:   'Revenue',
  }
  const variables = { currentTenant: 'acme' }
  const result    = resolveParams(params, variables)
  assert.equal(result.tenant,  'acme')
  assert.equal(result.maxRows, 50)
  assert.equal(result.label,   'Revenue')
})

test('unknown ref resolves to undefined', () => {
  const params    = { filter: { ref: 'doesNotExist' } }
  const variables = { otherVar: 'something' }
  const result    = resolveParams(params, variables)
  assert.equal(result.filter, undefined)
})

test('multiple unknown refs all resolve to undefined', () => {
  const params    = { a: { ref: 'x' }, b: { ref: 'y' } }
  const variables = {}
  const result    = resolveParams(params, variables)
  assert.equal(result.a, undefined)
  assert.equal(result.b, undefined)
})

test('null widgetParams returns an empty object', () => {
  const result = resolveParams(null, { foo: 'bar' })
  assert.deepEqual(result, {})
})

test('undefined widgetParams returns an empty object', () => {
  const result = resolveParams(undefined, { foo: 'bar' })
  assert.deepEqual(result, {})
})

test('array widgetParams (invalid shape) returns an empty object', () => {
  const result = resolveParams(['a', 'b'], { a: 1 })
  assert.deepEqual(result, {})
})

test('null variables treats all refs as undefined', () => {
  const params = { x: { ref: 'someVar' } }
  const result = resolveParams(params, null)
  assert.equal(result.x, undefined)
})

test('undefined variables treats all refs as undefined', () => {
  const params = { x: { ref: 'someVar' } }
  const result = resolveParams(params, undefined)
  assert.equal(result.x, undefined)
})

test('{ref} with explicit null value in variables resolves to null', () => {
  const params    = { filter: { ref: 'activeFilter' } }
  const variables = { activeFilter: null }
  const result    = resolveParams(params, variables)
  assert.equal(result.filter, null)
})

test('{ref} with 0 (falsy) value in variables resolves to 0, not undefined', () => {
  const params    = { offset: { ref: 'pageOffset' } }
  const variables = { pageOffset: 0 }
  const result    = resolveParams(params, variables)
  assert.equal(result.offset, 0)
})

test('{ref} with false value in variables resolves to false, not undefined', () => {
  const params    = { enabled: { ref: 'featureEnabled' } }
  const variables = { featureEnabled: false }
  const result    = resolveParams(params, variables)
  assert.equal(result.enabled, false)
})

test('literal array value is passed through as-is', () => {
  const params    = { tags: ['alpha', 'beta'] }
  const variables = {}
  const result    = resolveParams(params, variables)
  assert.deepEqual(result.tags, ['alpha', 'beta'])
})

test('literal null value is passed through as-is (not treated as {ref})', () => {
  const params    = { filter: null }
  const variables = { filter: 'should-not-be-used' }
  const result    = resolveParams(params, variables)
  assert.equal(result.filter, null)
})

test('empty widgetParams returns an empty object', () => {
  const result = resolveParams({}, { foo: 'bar' })
  assert.deepEqual(result, {})
})

test('result object is a new object (not the same reference as input)', () => {
  const params = { x: 1 }
  const result = resolveParams(params, {})
  assert.notEqual(result, params)
})

// ---------------------------------------------------------------------------
// resolveFilters — inline copy (same JSX-avoidance reason as resolveParams
// above: VariableStore.tsx transitively imports wasmRuntime.js -> api.js,
// which reads import.meta.env at module scope and cannot load under plain
// node --test). If the logic in VariableStore.tsx changes, update here too.
// ---------------------------------------------------------------------------

type NormMulti = { mode: 'all' | 'include' | 'exclude'; values: string[] }

function normMulti(value: unknown): NormMulti {
  if (value == null) return { mode: 'all', values: [] }
  if (Array.isArray(value)) {
    const values = value.map(String)
    return values.length === 0 ? { mode: 'all', values: [] } : { mode: 'include', values }
  }
  if (typeof value === 'object') {
    const v = value as Record<string, any>
    const mode = v.mode === 'exclude' ? 'exclude' : v.mode === 'all' ? 'all' : 'include'
    const values = Array.isArray(v.values) ? v.values.map(String) : []
    if (mode === 'all') return { mode: 'all', values: [] }
    if (values.length === 0 && mode === 'include') return { mode: 'all', values: [] }
    return { mode, values }
  }
  return { mode: 'include', values: [String(value)] }
}

function resolveDateRange(value: any): { from: string; to: string } {
  if (!value) return { from: '', to: '' }
  if (typeof value === 'object' && value.preset && value.preset !== 'custom') {
    // Presets resolve relative to "now"; irrelevant to these tests, which
    // only exercise the absolute {from, to} and unset shapes.
    return { from: '', to: '' }
  }
  return { from: value.from ?? '', to: value.to ?? '' }
}

function resolveFilters(
  widgetFilters: Record<string, string> | undefined,
  variables: Record<string, any>,
): Array<{ column: string; value: unknown }> {
  if (!widgetFilters || typeof widgetFilters !== 'object') return []
  if (!variables || typeof variables !== 'object') variables = {}

  const out: Array<{ column: string; value: unknown }> = []
  for (const [varName, column] of Object.entries(widgetFilters)) {
    if (typeof column !== 'string' || !column) continue
    const rawValue = variables[varName]
    if (rawValue === undefined || rawValue === null) continue

    if (Array.isArray(rawValue)) {
      if (rawValue.length === 0) continue
      out.push({ column, value: rawValue })
      continue
    }
    if (typeof rawValue === 'object' && ('mode' in rawValue || 'values' in rawValue)) {
      const normed = normMulti(rawValue)
      if (normed.mode === 'all') continue
      if (normed.mode === 'exclude') {
        if (normed.values.length > 0) out.push({ column, value: { not_in: normed.values } })
        continue
      }
      if (normed.values.length === 0) continue
      out.push({ column, value: normed.values })
      continue
    }
    if (typeof rawValue === 'object' && ('from' in rawValue || 'to' in rawValue || 'preset' in rawValue)) {
      const { from, to } = resolveDateRange(rawValue)
      if (!from && !to) continue
      const range: Record<string, string> = {}
      if (from) range.gte = from
      if (to) range.lte = to
      out.push({ column, value: range })
      continue
    }
    out.push({ column, value: rawValue })
  }
  return out
}

test('resolveFilters: unset variable is omitted (not sent as null)', () => {
  const result = resolveFilters({ month: 'month_col' }, {})
  assert.deepEqual(result, [])
})

test('resolveFilters: scalar variable becomes an equality filter', () => {
  const result = resolveFilters({ region: 'region_col' }, { region: 'Gauteng' })
  assert.deepEqual(result, [{ column: 'region_col', value: 'Gauteng' }])
})

test('resolveFilters: empty array is a no-op, not "match nothing"', () => {
  const result = resolveFilters({ region: 'region_col' }, { region: [] })
  assert.deepEqual(result, [])
})

test('resolveFilters: plain array becomes an IN filter', () => {
  const result = resolveFilters({ region: 'region_col' }, { region: ['GP', 'WC'] })
  assert.deepEqual(result, [{ column: 'region_col', value: ['GP', 'WC'] }])
})

test('resolveFilters: include-mode structured value becomes an IN filter', () => {
  const value = { mode: 'include', values: ['GP', 'WC'] }
  const result = resolveFilters({ region: 'region_col' }, { region: value })
  assert.deepEqual(result, [{ column: 'region_col', value: ['GP', 'WC'] }])
})

test('resolveFilters: exclude-mode becomes a NOT_IN filter, never an approximated IN', () => {
  const value = { mode: 'exclude', values: ['GP'] }
  const result = resolveFilters({ region: 'region_col' }, { region: value })
  assert.deepEqual(result, [{ column: 'region_col', value: { not_in: ['GP'] } }])
})

test('resolveFilters: mode "all" is a no-op regardless of stale values', () => {
  const value = { mode: 'all', values: ['GP'] }
  const result = resolveFilters({ region: 'region_col' }, { region: value })
  assert.deepEqual(result, [])
})

test('resolveFilters: absolute date range becomes an inclusive gte/lte filter', () => {
  const value = { from: '2024-01-01', to: '2024-01-31' }
  const result = resolveFilters({ month: 'order_date' }, { month: value })
  assert.deepEqual(result, [{ column: 'order_date', value: { gte: '2024-01-01', lte: '2024-01-31' } }])
})

test('resolveFilters: unset date range ({from:"",to:""}) is a no-op', () => {
  const value = { from: '', to: '' }
  const result = resolveFilters({ month: 'order_date' }, { month: value })
  assert.deepEqual(result, [])
})

test('resolveFilters: a variable with no matching filters entry is ignored', () => {
  const result = resolveFilters({}, { region: 'Gauteng' })
  assert.deepEqual(result, [])
})

test('resolveFilters: multiple filter bindings resolve independently', () => {
  const result = resolveFilters(
    { region: 'region_col', plan: 'plan_col' },
    { region: 'Gauteng', plan: ['pro', 'enterprise'] },
  )
  assert.deepEqual(result, [
    { column: 'region_col', value: 'Gauteng' },
    { column: 'plan_col', value: ['pro', 'enterprise'] },
  ])
})
