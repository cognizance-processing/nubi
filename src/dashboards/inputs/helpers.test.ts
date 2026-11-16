/**
 * helpers.test.mjs — Unit tests for the pure input-primitive helpers
 * (multiselect value-shape normalisation + date-range presets).
 *
 * Framework-free pure module — runs with bare `node --test`.
 *
 *   node --test src/dashboards/inputs/helpers.test.mjs
 *   # or via the project script:
 *   npm run test:dash
 */

import { test } from 'node:test'
import assert from 'node:assert/strict'

import {
  normMulti,
  isExclude,
  isFilterApplied,
  valuesOf,
  modeOf,
  makeMulti,
  toISODate,
  resolvePreset,
  resolveDateRange,
  DEFAULT_PRESETS,
  PRESET_LABELS,
} from './helpers.js'

// ---------------------------------------------------------------------------
// normMulti — value-shape normalisation
// ---------------------------------------------------------------------------

test('normMulti: null/undefined collapse to {mode:"all"}', () => {
  assert.deepEqual(normMulti(null), { mode: 'all', values: [] })
  assert.deepEqual(normMulti(undefined), { mode: 'all', values: [] })
})

test('normMulti: empty array collapses to "all"', () => {
  assert.deepEqual(normMulti([]), { mode: 'all', values: [] })
})

test('normMulti: non-empty array → include (values stringified)', () => {
  assert.deepEqual(normMulti(['a', 1, true]), { mode: 'include', values: ['a', '1', 'true'] })
})

test('normMulti: explicit include object', () => {
  assert.deepEqual(normMulti({ mode: 'include', values: ['x'] }), { mode: 'include', values: ['x'] })
})

test('normMulti: exclude object preserved', () => {
  assert.deepEqual(normMulti({ mode: 'exclude', values: ['x', 'y'] }), { mode: 'exclude', values: ['x', 'y'] })
})

test('normMulti: explicit {mode:"all"} drops values', () => {
  assert.deepEqual(normMulti({ mode: 'all', values: ['ignored'] }), { mode: 'all', values: [] })
})

test('normMulti: empty include collapses to "all"', () => {
  assert.deepEqual(normMulti({ mode: 'include', values: [] }), { mode: 'all', values: [] })
})

test('normMulti: empty exclude stays exclude (all-but-none)', () => {
  // exclude with no values is a valid representation (excludes nothing).
  assert.deepEqual(normMulti({ mode: 'exclude', values: [] }), { mode: 'exclude', values: [] })
})

test('normMulti: unknown mode falls back to include', () => {
  assert.deepEqual(normMulti({ mode: 'frobnicate', values: ['z'] }), { mode: 'include', values: ['z'] })
})

test('normMulti: scalar → single include value', () => {
  assert.deepEqual(normMulti('solo'), { mode: 'include', values: ['solo'] })
  assert.deepEqual(normMulti(7), { mode: 'include', values: ['7'] })
})

// ---------------------------------------------------------------------------
// isExclude / valuesOf / modeOf accessors
// ---------------------------------------------------------------------------

test('isExclude reflects exclude mode', () => {
  assert.equal(isExclude({ mode: 'exclude', values: ['a'] }), true)
  assert.equal(isExclude(['a']), false)
  assert.equal(isExclude(null), false)
})

test('valuesOf returns the selected values regardless of mode', () => {
  assert.deepEqual(valuesOf(['a', 'b']), ['a', 'b'])
  assert.deepEqual(valuesOf({ mode: 'exclude', values: ['c'] }), ['c'])
  assert.deepEqual(valuesOf(null), [])
})

test('modeOf returns the canonical mode', () => {
  assert.equal(modeOf(['a']), 'include')
  assert.equal(modeOf({ mode: 'exclude', values: ['c'] }), 'exclude')
  assert.equal(modeOf(null), 'all')
})

// ---------------------------------------------------------------------------
// makeMulti — build a backward-compatible representation
// ---------------------------------------------------------------------------

test('makeMulti: include with values → plain array (legacy)', () => {
  assert.deepEqual(makeMulti('include', ['a', 'b']), ['a', 'b'])
})

test('makeMulti: include with none → empty array (== all)', () => {
  assert.deepEqual(makeMulti('include', []), [])
})

test('makeMulti: non-exclude modes pass values through stringified', () => {
  // makeMulti only special-cases 'exclude'; any other mode returns the
  // stringified values array (empty == all for legacy consumers).
  assert.deepEqual(makeMulti('all', ['a', 1]), ['a', '1'])
})

test('makeMulti: exclude with values → tagged object', () => {
  assert.deepEqual(makeMulti('exclude', ['a', 'b']), { mode: 'exclude', values: ['a', 'b'] })
})

test('makeMulti: exclude with none collapses to [] (all)', () => {
  assert.deepEqual(makeMulti('exclude', []), [])
})

test('makeMulti: values are stringified', () => {
  assert.deepEqual(makeMulti('exclude', [1, 2]), { mode: 'exclude', values: ['1', '2'] })
})

test('makeMulti round-trips through normMulti for include', () => {
  const v = makeMulti('include', ['x', 'y'])
  assert.deepEqual(normMulti(v), { mode: 'include', values: ['x', 'y'] })
})

test('makeMulti round-trips through normMulti for exclude', () => {
  const v = makeMulti('exclude', ['x'])
  assert.deepEqual(normMulti(v), { mode: 'exclude', values: ['x'] })
})

// ---------------------------------------------------------------------------
// toISODate
// ---------------------------------------------------------------------------

test('toISODate formats local date as YYYY-MM-DD with zero padding', () => {
  const d = new Date(2024, 0, 5) // Jan 5 2024
  assert.equal(toISODate(d), '2024-01-05')
})

test('toISODate pads two-digit month and day', () => {
  const d = new Date(2024, 11, 31) // Dec 31 2024
  assert.equal(toISODate(d), '2024-12-31')
})

// ---------------------------------------------------------------------------
// resolvePreset — injectable clock
// ---------------------------------------------------------------------------

const NOW = new Date(2024, 5, 15, 13, 30) // 2024-06-15 (Saturday), mid-afternoon

test('resolvePreset today → from==to==today', () => {
  assert.deepEqual(resolvePreset('today', NOW), { from: '2024-06-15', to: '2024-06-15' })
})

test('resolvePreset yesterday', () => {
  assert.deepEqual(resolvePreset('yesterday', NOW), { from: '2024-06-14', to: '2024-06-14' })
})

test('resolvePreset last_7d spans 6 days back through today', () => {
  assert.deepEqual(resolvePreset('last_7d', NOW), { from: '2024-06-09', to: '2024-06-15' })
})

test('resolvePreset last_30d', () => {
  assert.deepEqual(resolvePreset('last_30d', NOW), { from: '2024-05-17', to: '2024-06-15' })
})

test('resolvePreset last_90d', () => {
  assert.deepEqual(resolvePreset('last_90d', NOW), { from: '2024-03-18', to: '2024-06-15' })
})

test('resolvePreset mtd → first of month through today', () => {
  assert.deepEqual(resolvePreset('mtd', NOW), { from: '2024-06-01', to: '2024-06-15' })
})

test('resolvePreset qtd → first of quarter (Apr) through today', () => {
  // June is in Q2 (Apr-Jun) → quarter start is April 1.
  assert.deepEqual(resolvePreset('qtd', NOW), { from: '2024-04-01', to: '2024-06-15' })
})

test('resolvePreset ytd → Jan 1 through today', () => {
  assert.deepEqual(resolvePreset('ytd', NOW), { from: '2024-01-01', to: '2024-06-15' })
})

test('resolvePreset custom → null', () => {
  assert.equal(resolvePreset('custom', NOW), null)
})

test('resolvePreset unknown → null', () => {
  assert.equal(resolvePreset('made-up', NOW), null)
})

// ---------------------------------------------------------------------------
// resolveDateRange
// ---------------------------------------------------------------------------

test('resolveDateRange null → empty pair', () => {
  assert.deepEqual(resolveDateRange(null), { from: '', to: '' })
})

test('resolveDateRange relative preset resolves through clock', () => {
  assert.deepEqual(resolveDateRange({ preset: 'mtd' }, NOW), { from: '2024-06-01', to: '2024-06-15' })
})

test('resolveDateRange custom preset falls through to absolute from/to', () => {
  assert.deepEqual(
    resolveDateRange({ preset: 'custom', from: '2024-01-01', to: '2024-02-01' }, NOW),
    { from: '2024-01-01', to: '2024-02-01' },
  )
})

test('resolveDateRange absolute from/to passes through', () => {
  assert.deepEqual(
    resolveDateRange({ from: '2024-03-01', to: '2024-03-31' }, NOW),
    { from: '2024-03-01', to: '2024-03-31' },
  )
})

test('resolveDateRange partial absolute defaults missing side to empty string', () => {
  assert.deepEqual(resolveDateRange({ from: '2024-03-01' }, NOW), { from: '2024-03-01', to: '' })
})

// ---------------------------------------------------------------------------
// Preset metadata
// ---------------------------------------------------------------------------

test('DEFAULT_PRESETS includes custom last and the standard ranges', () => {
  assert.ok(DEFAULT_PRESETS.includes('today'))
  assert.ok(DEFAULT_PRESETS.includes('ytd'))
  assert.equal(DEFAULT_PRESETS[DEFAULT_PRESETS.length - 1], 'custom')
})

test('every default preset has a human label', () => {
  for (const key of DEFAULT_PRESETS) {
    assert.equal(typeof PRESET_LABELS[key], 'string')
    assert.ok(PRESET_LABELS[key].length > 0)
  }
})

// ── isFilterApplied ─────────────────────────────────────────────────────────
// "Has a value" and "is filtering" differ: an untouched multiselect is [],
// a cleared range is {from:'',to:''}. Only the second question is worth
// putting on a badge.

test('isFilterApplied: unset shapes are not applied', () => {
  for (const v of [null, undefined, '', [], {}, { from: '', to: '' }, { mode: 'all', values: [] }]) {
    assert.equal(isFilterApplied(v), false, `expected ${JSON.stringify(v)} to be unapplied`)
  }
})

test('isFilterApplied: multiselect with selections is applied', () => {
  assert.equal(isFilterApplied(['Gauteng']), true)
  assert.equal(isFilterApplied({ mode: 'include', values: ['a'] }), true)
  assert.equal(isFilterApplied({ mode: 'exclude', values: ['a'] }), true)
})

test('isFilterApplied: date range applied when either end or a preset is set', () => {
  assert.equal(isFilterApplied({ from: '2024-01-01', to: '' }), true)
  assert.equal(isFilterApplied({ from: '', to: '2024-01-01' }), true)
  assert.equal(isFilterApplied({ preset: 'last_30d' }), true)
  assert.equal(isFilterApplied({ preset: '' }), false)
})

test('isFilterApplied: scalars', () => {
  assert.equal(isFilterApplied('Gauteng'), true)
  assert.equal(isFilterApplied(0), true)
  assert.equal(isFilterApplied(false), true)
})
