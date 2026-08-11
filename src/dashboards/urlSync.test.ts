/**
 * urlSync.test.mjs — unit tests for the URL ↔ variable sync helpers (M14-C).
 *
 * Run with:
 *   node --test src/dashboards/urlSync.test.mjs
 *   # or via the project test:dash script
 *
 * Pure functions imported directly (urlSync.js is plain JS, no JSX) — these lock
 * in the read (URL → store) and write (filter → URL) directions of the two-way
 * sync that DashboardViewPage wires through SpecRenderer → VariableProvider.
 */

import { test } from 'node:test'
import assert from 'node:assert/strict'

import { extractVarsFromURL, applyVarToSearchParams } from './urlSync.js'

// ── extractVarsFromURL (read: URL → store seed) ──────────────────────────────

test('extractVarsFromURL pulls only declared variable names', () => {
  const sp = new URLSearchParams('region=EU&utm_source=ad&month=2024-05')
  const out = extractVarsFromURL(sp, ['region', 'month'])
  assert.deepEqual(out, { region: 'EU', month: '2024-05' })
})

test('extractVarsFromURL ignores undeclared params (no store pollution)', () => {
  const sp = new URLSearchParams('utm_source=ad&fbclid=xyz')
  assert.deepEqual(extractVarsFromURL(sp, ['region']), {})
})

test('extractVarsFromURL omits absent declared names', () => {
  const sp = new URLSearchParams('region=EU')
  assert.deepEqual(extractVarsFromURL(sp, ['region', 'month']), { region: 'EU' })
})

test('extractVarsFromURL tolerates empty/undefined knownVarNames', () => {
  const sp = new URLSearchParams('region=EU')
  assert.deepEqual(extractVarsFromURL(sp, []), {})
  assert.deepEqual(extractVarsFromURL(sp, undefined), {})
})

// ── applyVarToSearchParams (write: filter → URL) ─────────────────────────────

test('applyVarToSearchParams sets a declared variable', () => {
  const next = applyVarToSearchParams(new URLSearchParams(''), 'region', 'EU', {
    knownVarNames: ['region'],
  })
  assert.equal(next.get('region'), 'EU')
})

test('applyVarToSearchParams deletes the param on an empty value', () => {
  const prev = new URLSearchParams('region=EU')
  for (const empty of ['', null, undefined]) {
    const next = applyVarToSearchParams(prev, 'region', empty, { knownVarNames: ['region'] })
    assert.equal(next.has('region'), false, `value ${String(empty)} should delete`)
  }
})

test('applyVarToSearchParams never writes an embed-locked param', () => {
  const prev = new URLSearchParams('tenant=acme')
  const next = applyVarToSearchParams(prev, 'tenant', 'evil', {
    knownVarNames: ['tenant'],
    lockedParams: { tenant: 'acme' },
  })
  // Unchanged — the embed token is the source of truth, not the URL.
  assert.equal(next.get('tenant'), 'acme')
})

test('applyVarToSearchParams ignores undeclared names', () => {
  const next = applyVarToSearchParams(new URLSearchParams(''), 'rogue', 'x', {
    knownVarNames: ['region'],
  })
  assert.equal(next.has('rogue'), false)
})

test('applyVarToSearchParams does not mutate the input params', () => {
  const prev = new URLSearchParams('region=EU')
  applyVarToSearchParams(prev, 'region', 'US', { knownVarNames: ['region'] })
  assert.equal(prev.get('region'), 'EU') // original untouched
})

test('applyVarToSearchParams coerces non-string values to strings', () => {
  const next = applyVarToSearchParams(new URLSearchParams(''), 'count', 5, {
    knownVarNames: ['count'],
  })
  assert.equal(next.get('count'), '5')
})
