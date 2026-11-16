/**
 * legacyWidgetText.test.mjs — pure-logic tests for the presentational widgets
 * carried over from the legacy estate: the drill-path breadcrumb and the
 * reporting-period caption.
 *
 * Only the label/caption derivation is tested here — that is where the real
 * edge cases live (a multiselect holds an array, an exclude-mode multiselect
 * holds {mode, values}, a daterange holds {from,to} or {preset}). Rendering is
 * covered by the widget-render e2e pass, not by node:test.
 *
 * Run: npm run test:dash
 */

import { test, describe } from 'node:test'
import assert from 'node:assert/strict'

import { chipLabel, formatDateValue, captionForVariable } from './legacyWidgetText.js'

// ---------------------------------------------------------------------------
// BreadcrumbWidget.chipLabel
// ---------------------------------------------------------------------------

describe('breadcrumb chipLabel', () => {
  test('an unset variable produces no label (and so no crumb)', () => {
    assert.equal(chipLabel(null), '')
    assert.equal(chipLabel(undefined), '')
    assert.equal(chipLabel(''), '')
    assert.equal(chipLabel([]), '')
    assert.equal(chipLabel({}), '')
  })

  test('scalars render verbatim', () => {
    assert.equal(chipLabel('West'), 'West')
    assert.equal(chipLabel(42), '42')
  })

  test('a short multiselect lists its values, a long one is summarised', () => {
    assert.equal(chipLabel(['a']), 'a')
    assert.equal(chipLabel(['a', 'b']), 'a, b')
    assert.equal(chipLabel(['a', 'b', 'c', 'd']), 'a, b +2')
  })

  test('an exclude-mode multiselect says so', () => {
    assert.equal(chipLabel({ mode: 'exclude', values: ['a', 'b'] }), 'not a, b')
    assert.equal(chipLabel({ mode: 'include', values: ['a'] }), 'a')
  })

  test('a daterange renders as a preset name or a from–to pair', () => {
    assert.equal(chipLabel({ preset: 'last_30d' }), 'last 30d')
    assert.equal(chipLabel({ from: '2025-01-01', to: '2025-03-31' }), '2025-01-01 – 2025-03-31')
  })

  test("a 'custom' preset falls through to its explicit bounds", () => {
    // The picker writes preset:'custom' alongside the real dates; showing the
    // word "custom" as the crumb would tell the viewer nothing.
    assert.equal(
      chipLabel({ preset: 'custom', from: '2025-01-01', to: '2025-01-31' }),
      '2025-01-01 – 2025-01-31',
    )
  })

  test('nulls inside a multiselect are dropped, not rendered', () => {
    assert.equal(chipLabel([null, 'a', '']), 'a')
  })
})

// ---------------------------------------------------------------------------
// DateDisplayWidget.formatDateValue
// ---------------------------------------------------------------------------

describe('date display formatting', () => {
  test("'iso' trims a timestamp to its day part and leaves a day string alone", () => {
    assert.equal(formatDateValue('2025-06-15T10:30:00Z', 'iso'), '2025-06-15')
    assert.equal(formatDateValue('2025-06-15', 'iso'), '2025-06-15')
  })

  test("'raw' never parses — a pre-formatted column survives intact", () => {
    assert.equal(formatDateValue('Jan–Mar FY25', 'raw'), 'Jan–Mar FY25')
  })

  test('an unparseable value is shown verbatim rather than as Invalid Date', () => {
    assert.equal(formatDateValue('not a date', 'medium'), 'not a date')
  })

  test('an empty value formats to an empty string, not a fake date', () => {
    assert.equal(formatDateValue(null, 'medium'), '')
    assert.equal(formatDateValue('', 'long'), '')
  })

  test('a known style produces a localised date', () => {
    const out = formatDateValue('2025-06-15', 'medium')
    assert.notEqual(out, '2025-06-15')
    assert.ok(out.includes('2025'), `expected a formatted 2025 date, got ${out}`)
  })
})

// ---------------------------------------------------------------------------
// DateDisplayWidget.captionForVariable
// ---------------------------------------------------------------------------

describe('date display caption from a variable', () => {
  test('a daterange becomes a from–to caption', () => {
    assert.equal(
      captionForVariable({ from: '2025-01-01', to: '2025-01-31' }, 'iso', '–'),
      '2025-01-01 – 2025-01-31',
    )
  })

  test('a relative preset is resolved to real dates, not shown as a preset name', () => {
    // This is the whole reason the caption is a widget rather than static text.
    const out = captionForVariable({ preset: 'last_7d' }, 'iso', 'to')
    assert.match(out, /^\d{4}-\d{2}-\d{2} to \d{4}-\d{2}-\d{2}$/)
  })

  test('a half-open range shows the bound it has', () => {
    assert.equal(captionForVariable({ from: '2025-01-01', to: '' }, 'iso', '–'), '2025-01-01')
  })

  test('a plain scalar variable renders as one date', () => {
    assert.equal(captionForVariable('2025-01-01', 'iso', '–'), '2025-01-01')
  })

  test('an array variable is joined with the separator', () => {
    assert.equal(captionForVariable(['2025-01-01', '2025-02-01'], 'iso', '→'), '2025-01-01 → 2025-02-01')
  })

  test('an unset variable yields an empty caption', () => {
    assert.equal(captionForVariable(null, 'iso', '–'), '')
    assert.equal(captionForVariable({}, 'iso', '–'), '')
  })
})
