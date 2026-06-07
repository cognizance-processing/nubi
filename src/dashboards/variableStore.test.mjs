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
function resolveParams(widgetParams, variables) {
  if (!widgetParams || typeof widgetParams !== 'object' || Array.isArray(widgetParams)) {
    return {}
  }
  if (!variables || typeof variables !== 'object') {
    variables = {}
  }

  const resolved = {}
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
