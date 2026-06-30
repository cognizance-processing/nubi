/**
 * nubi-explain.test.js — unit tests for the <nubi-explain> widget pure logic.
 *
 * Converted from node:test (nubi-explain.test.mjs) to vitest so the full
 * embed test suite runs under a single `npm run test:embed` command.
 *
 * Tests the pure logic helpers (_fmtNumber, _fmtPct, _makeSampleData) without
 * needing the full browser/custom-element stack.
 *
 * Run with:
 *   npm run test:embed
 */

import { test, describe, expect } from 'vitest'

// Compatibility shim: assert.equal → expect().toBe, assert.ok → expect().toBeTruthy
const assert = {
  ok:    (val, msg)  => expect(val, msg).toBeTruthy(),
  equal: (a, b, msg) => expect(a, msg).toBe(b),
}

// ---------------------------------------------------------------------------
// Inline the pure logic under test (no browser globals needed)
// ---------------------------------------------------------------------------

function _fmtNumber(n) {
  if (n === null || n === undefined || isNaN(n)) return '—'
  const abs = Math.abs(n)
  const sign = n < 0 ? '-' : n > 0 ? '+' : ''
  if (abs >= 1_000_000) return sign + (abs / 1_000_000).toFixed(1) + 'M'
  if (abs >= 1_000) return sign + (abs / 1_000).toFixed(1) + 'K'
  return sign + new Intl.NumberFormat(undefined, { maximumFractionDigits: 1 }).format(abs)
}

function _fmtPct(share) {
  if (share === null || share === undefined || isNaN(share)) return ''
  const pct = Math.abs(share * 100)
  return pct.toFixed(1) + '%'
}

function _fmtSigned(n) {
  if (n === null || n === undefined || isNaN(n)) return '—'
  const abs = Math.abs(n)
  const sign = n < 0 ? '-' : n > 0 ? '+' : ''
  if (abs >= 1_000_000) return sign + (abs / 1_000_000).toFixed(2) + 'M'
  if (abs >= 1_000) return sign + (abs / 1_000).toFixed(2) + 'K'
  const digits = abs >= 1 ? 2 : 3
  return sign + new Intl.NumberFormat(undefined, { maximumFractionDigits: digits }).format(abs)
}

function _fmtValue(n) {
  if (n === null || n === undefined || isNaN(n)) return '—'
  const abs = Math.abs(n)
  if (abs >= 1_000_000) return (n / 1_000_000).toFixed(2) + 'M'
  if (abs >= 1_000) return (n / 1_000).toFixed(2) + 'K'
  return new Intl.NumberFormat(undefined, { maximumFractionDigits: 3 }).format(n)
}

function _makeSampleModelAttribution() {
  return {
    kind: 'model_attribution',
    model: 'demand_forecast_v3',
    prediction: { label: 'units_next_week', value: 168 },
    base_value: 120,
    features: [
      { feature: 'promo_active',      value: 'yes',   contribution: 38 },
      { feature: 'avg_temp_c',        value: 27.5,    contribution: 21 },
      { feature: 'price',             value: 14.99,   contribution: -18 },
      { feature: 'day_of_week',       value: 'Sat',   contribution: 12 },
      { feature: 'competitor_promo',  value: 'yes',   contribution: -9 },
      { feature: 'stock_on_hand',     value: 320,     contribution: 4 },
    ],
    summary: null,
  }
}

// Mirrors the widget's feature sort: by |contribution| descending.
function _sortFeatures(features) {
  return features.slice().sort((a, b) => Math.abs(b.contribution || 0) - Math.abs(a.contribution || 0))
}

function _makeSampleData() {
  return {
    metric_id: 'demo',
    measure: 'revenue',
    delta_total: 12500,
    current_total: 87500,
    comparison_total: 75000,
    dimensions: [
      {
        dimension: 'region',
        members: [
          { member: 'North', current: 42000, comparison: 34000, delta: 8000, share: 0.64, direction: 'up' },
          { member: 'South', current: 28000, comparison: 25000, delta: 3000, share: 0.24, direction: 'up' },
          { member: 'West',  current: 17500, comparison: 16000, delta: 1500, share: 0.12, direction: 'up' },
        ],
        other: null,
        coverage: 1.0,
        explanatory_power: 1.0,
      },
      {
        dimension: 'category',
        members: [
          { member: 'Software', current: 50000, comparison: 40000, delta: 10000, share: 0.80, direction: 'up' },
          { member: 'Hardware', current: 25000, comparison: 27500, delta: -2500, share: -0.20, direction: 'down' },
          { member: 'Services', current: 12500, comparison: 7500, delta: 5000, share: 0.40, direction: 'up' },
        ],
        other: null,
        coverage: 1.0,
        explanatory_power: 0.95,
      },
    ],
    summary: null,
  }
}

// ---------------------------------------------------------------------------
// _fmtNumber
// ---------------------------------------------------------------------------

describe('_fmtNumber', () => {
  test('formats null as —', () => {
    assert.equal(_fmtNumber(null), '—')
  })

  test('formats undefined as —', () => {
    assert.equal(_fmtNumber(undefined), '—')
  })

  test('formats NaN as —', () => {
    assert.equal(_fmtNumber(NaN), '—')
  })

  test('formats millions with M suffix', () => {
    assert.equal(_fmtNumber(1_500_000), '+1.5M')
    assert.equal(_fmtNumber(-2_000_000), '-2.0M')
  })

  test('formats thousands with K suffix', () => {
    assert.equal(_fmtNumber(12_500), '+12.5K')
    assert.equal(_fmtNumber(-3_000), '-3.0K')
  })

  test('formats zero as + prefix omitted (sign is empty string)', () => {
    // n === 0: sign is '' so result should not have a sign
    const result = _fmtNumber(0)
    assert.ok(!result.startsWith('+') && !result.startsWith('-'), `expected no sign for 0, got: ${result}`)
  })

  test('formats positive small number with + prefix', () => {
    const result = _fmtNumber(42)
    assert.ok(result.startsWith('+'), `expected +, got: ${result}`)
  })

  test('formats negative small number with - prefix', () => {
    const result = _fmtNumber(-7)
    assert.ok(result.startsWith('-'), `expected -, got: ${result}`)
  })
})

// ---------------------------------------------------------------------------
// _fmtPct
// ---------------------------------------------------------------------------

describe('_fmtPct', () => {
  test('formats null as empty string', () => {
    assert.equal(_fmtPct(null), '')
  })

  test('formats undefined as empty string', () => {
    assert.equal(_fmtPct(undefined), '')
  })

  test('formats NaN as empty string', () => {
    assert.equal(_fmtPct(NaN), '')
  })

  test('formats 0.5 as 50.0%', () => {
    assert.equal(_fmtPct(0.5), '50.0%')
  })

  test('formats negative share as positive percentage', () => {
    // Uses Math.abs so -0.2 → 20.0%
    assert.equal(_fmtPct(-0.2), '20.0%')
  })

  test('formats 1.0 as 100.0%', () => {
    assert.equal(_fmtPct(1.0), '100.0%')
  })

  test('formats small fraction', () => {
    assert.equal(_fmtPct(0.001), '0.1%')
  })
})

// ---------------------------------------------------------------------------
// _makeSampleData
// ---------------------------------------------------------------------------

describe('_makeSampleData', () => {
  test('returns an object with expected keys', () => {
    const data = _makeSampleData()
    assert.ok('metric_id'       in data)
    assert.ok('measure'         in data)
    assert.ok('delta_total'     in data)
    assert.ok('current_total'   in data)
    assert.ok('comparison_total' in data)
    assert.ok(Array.isArray(data.dimensions))
  })

  test('delta_total equals current_total - comparison_total', () => {
    const data = _makeSampleData()
    assert.equal(data.delta_total, data.current_total - data.comparison_total)
  })

  test('has at least 2 dimensions', () => {
    const data = _makeSampleData()
    assert.ok(data.dimensions.length >= 2)
  })

  test('each dimension has members array', () => {
    const data = _makeSampleData()
    for (const dim of data.dimensions) {
      assert.ok(Array.isArray(dim.members))
      assert.ok('dimension'         in dim)
      assert.ok('coverage'          in dim)
      assert.ok('explanatory_power' in dim)
    }
  })

  test('each member has required fields', () => {
    const data = _makeSampleData()
    for (const dim of data.dimensions) {
      for (const m of dim.members) {
        assert.ok('member'    in m)
        assert.ok('delta'     in m)
        assert.ok('share'     in m)
        assert.ok('direction' in m)
      }
    }
  })

  test('member directions are valid', () => {
    const data = _makeSampleData()
    const validDirs = new Set(['up', 'down', 'flat'])
    for (const dim of data.dimensions) {
      for (const m of dim.members) {
        assert.ok(validDirs.has(m.direction), `invalid direction: ${m.direction}`)
      }
    }
  })

  test('regions coverage is 1.0', () => {
    const data = _makeSampleData()
    const region = data.dimensions.find(d => d.dimension === 'region')
    assert.ok(region)
    assert.equal(region.coverage, 1.0)
  })
})

// ---------------------------------------------------------------------------
// Structural validation of ExplainResponse shape
// ---------------------------------------------------------------------------

describe('ExplainResponse shape validation', () => {
  test('validates a well-formed response', () => {
    const data = _makeSampleData()
    // All shares in a dimension should be numeric
    for (const dim of data.dimensions) {
      for (const m of dim.members) {
        assert.equal(typeof m.share, 'number')
        assert.equal(typeof m.delta, 'number')
      }
    }
  })

  test('top dimension has highest explanatory_power', () => {
    const data = _makeSampleData()
    // Sample data is already sorted; verify order
    const powers = data.dimensions.map(d => d.explanatory_power)
    for (let i = 0; i < powers.length - 1; i++) {
      assert.ok(powers[i] >= powers[i + 1], `dimension ${i} power ${powers[i]} < ${powers[i + 1]}`)
    }
  })
})

// ---------------------------------------------------------------------------
// Model-attribution payload (host-supplied, second distinct payload)
// ---------------------------------------------------------------------------

describe('_fmtSigned (model-attribution contributions)', () => {
  test('formats null/undefined/NaN as —', () => {
    assert.equal(_fmtSigned(null), '—')
    assert.equal(_fmtSigned(undefined), '—')
    assert.equal(_fmtSigned(NaN), '—')
  })

  test('signs positive and negative contributions', () => {
    assert.ok(_fmtSigned(38).startsWith('+'))
    assert.ok(_fmtSigned(-18).startsWith('-'))
  })

  test('keeps decimals for small SHAP-like values', () => {
    assert.equal(_fmtSigned(0.123), '+0.123')
    assert.equal(_fmtSigned(-0.05), '-0.05')
  })

  test('uses K/M suffixes for large magnitudes', () => {
    assert.equal(_fmtSigned(12_500), '+12.50K')
    assert.equal(_fmtSigned(-2_000_000), '-2.00M')
  })
})

describe('_fmtValue (base value / prediction value)', () => {
  test('formats null as —', () => {
    assert.equal(_fmtValue(null), '—')
  })

  test('no forced sign for positive values', () => {
    const r = _fmtValue(120)
    assert.ok(!r.startsWith('+'), `expected no + sign, got ${r}`)
  })
})

describe('_makeSampleModelAttribution', () => {
  test('has the model_attribution discriminator', () => {
    const a = _makeSampleModelAttribution()
    assert.equal(a.kind, 'model_attribution')
  })

  test('exposes base_value, prediction and a features array', () => {
    const a = _makeSampleModelAttribution()
    assert.equal(typeof a.base_value, 'number')
    assert.ok(a.prediction && 'value' in a.prediction)
    assert.ok(Array.isArray(a.features))
  })

  test('each feature has feature name + numeric contribution', () => {
    const a = _makeSampleModelAttribution()
    for (const f of a.features) {
      assert.ok('feature' in f)
      assert.equal(typeof f.contribution, 'number')
    }
  })

  test('contains both positive and negative contributions (directional)', () => {
    const a = _makeSampleModelAttribution()
    assert.ok(a.features.some(f => f.contribution > 0))
    assert.ok(a.features.some(f => f.contribution < 0))
  })

  test('base_value + sum(contributions) reconstructs the prediction', () => {
    const a = _makeSampleModelAttribution()
    const sum = a.features.reduce((acc, f) => acc + f.contribution, 0)
    assert.equal(a.base_value + sum, a.prediction.value)
  })
})

describe('feature sort by |contribution|', () => {
  test('orders features by descending absolute contribution', () => {
    const a = _makeSampleModelAttribution()
    const sorted = _sortFeatures(a.features)
    for (let i = 0; i < sorted.length - 1; i++) {
      assert.ok(
        Math.abs(sorted[i].contribution) >= Math.abs(sorted[i + 1].contribution),
        `feature ${i} |${sorted[i].contribution}| < |${sorted[i + 1].contribution}|`,
      )
    }
  })

  test('top feature is the largest-magnitude driver', () => {
    const a = _makeSampleModelAttribution()
    const sorted = _sortFeatures(a.features)
    assert.equal(sorted[0].feature, 'promo_active')
  })
})
