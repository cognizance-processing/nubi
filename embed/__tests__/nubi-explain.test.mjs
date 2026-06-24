/**
 * nubi-explain.test.mjs — unit tests for the <nubi-explain> widget pure logic.
 *
 * Tests the pure logic helpers (_fmtNumber, _fmtPct, _makeSampleData) without
 * needing the full browser/custom-element stack (which requires a build step
 * for the apache-arrow ESM imports).
 *
 * Run with:
 *   node --test embed/__tests__/nubi-explain.test.mjs
 *
 * The widget integration tests (mount, fetch, events) require jsdom + vitest
 * and are in nubi-explain.test.jsdom.js (vitest-environment jsdom).
 *
 * @vitest-environment jsdom
 */

import { test, describe } from 'node:test'
import assert from 'node:assert/strict'

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

  test('formats NaN as —', () => {
    assert.equal(_fmtNumber(NaN), '—')
  })

  test('formats positive numbers with + sign', () => {
    const result = _fmtNumber(100)
    assert.match(result, /^\+/)
  })

  test('formats negative numbers with - sign', () => {
    const result = _fmtNumber(-100)
    assert.match(result, /^-/)
  })

  test('compacts millions', () => {
    assert.equal(_fmtNumber(2_500_000), '+2.5M')
  })

  test('compacts thousands', () => {
    assert.equal(_fmtNumber(12_500), '+12.5K')
  })

  test('formats negative thousands', () => {
    assert.equal(_fmtNumber(-3_000), '-3.0K')
  })

  test('formats zero', () => {
    const result = _fmtNumber(0)
    // 0 has no sign (n > 0 is false, n < 0 is false → sign='')
    assert.ok(!result.startsWith('+') && !result.startsWith('-'))
  })
})

// ---------------------------------------------------------------------------
// _fmtPct
// ---------------------------------------------------------------------------

describe('_fmtPct', () => {
  test('formats null as empty string', () => {
    assert.equal(_fmtPct(null), '')
  })

  test('formats share=0.64 as 64.0%', () => {
    assert.equal(_fmtPct(0.64), '64.0%')
  })

  test('formats negative share as positive pct (abs)', () => {
    assert.equal(_fmtPct(-0.20), '20.0%')
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
    assert.ok('metric_id' in data)
    assert.ok('measure' in data)
    assert.ok('delta_total' in data)
    assert.ok('current_total' in data)
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
      assert.ok('dimension' in dim)
      assert.ok('coverage' in dim)
      assert.ok('explanatory_power' in dim)
    }
  })

  test('each member has required fields', () => {
    const data = _makeSampleData()
    for (const dim of data.dimensions) {
      for (const m of dim.members) {
        assert.ok('member' in m)
        assert.ok('delta' in m)
        assert.ok('share' in m)
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
      assert.ok(powers[i] >= powers[i + 1], `dimension ${i} power ${powers[i]} < ${powers[i+1]}`)
    }
  })
})
