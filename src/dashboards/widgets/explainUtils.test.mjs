/**
 * explainUtils.test.mjs — unit tests for pure utility logic used by ExplainDrawer.
 *
 * These are extracted / replicated from ExplainDrawer.jsx so they can run under
 * node:test without a DOM / JSX transform.  Tests cover:
 *
 *   1. fmt() — number formatting (compact K/M suffixes, null/NaN handling).
 *   2. Date helpers (today / daysAgo) — basic sanity.
 *   3. direction inference from delta values.
 *
 * Run: npm run test:dash
 */

import test from 'node:test'
import assert from 'node:assert/strict'

// ---------------------------------------------------------------------------
// Replicate the fmt() helper from ExplainDrawer (pure function, no React dep)
// ---------------------------------------------------------------------------

function fmt(n, digits = 2) {
  if (n == null) return '—'
  const v = Number(n)
  if (Number.isNaN(v)) return '—'
  if (Math.abs(v) >= 1_000_000) return `${(v / 1_000_000).toFixed(digits)}M`
  if (Math.abs(v) >= 1_000) return `${(v / 1_000).toFixed(digits)}K`
  return v.toLocaleString(undefined, { maximumFractionDigits: digits })
}

function today() {
  return new Date().toISOString().slice(0, 10)
}

function daysAgo(n) {
  const d = new Date()
  d.setDate(d.getDate() - n)
  return d.toISOString().slice(0, 10)
}

function inferDirection(delta) {
  return delta > 0 ? 'up' : delta < 0 ? 'down' : 'flat'
}

// ---------------------------------------------------------------------------
// 1. fmt() — null/undefined/NaN
// ---------------------------------------------------------------------------

test('fmt: null returns em dash', () => {
  assert.equal(fmt(null), '—')
})

test('fmt: undefined returns em dash', () => {
  assert.equal(fmt(undefined), '—')
})

test('fmt: NaN string returns em dash', () => {
  assert.equal(fmt('not-a-number'), '—')
})

// ---------------------------------------------------------------------------
// 2. fmt() — compact suffixes
// ---------------------------------------------------------------------------

test('fmt: values >= 1M rendered with M suffix', () => {
  assert.equal(fmt(1_500_000), '1.50M')
})

test('fmt: values >= 1K rendered with K suffix', () => {
  assert.equal(fmt(2_500), '2.50K')
})

test('fmt: zero renders as 0', () => {
  const result = fmt(0)
  assert.ok(!result.includes('M'), 'should not have M suffix')
  assert.ok(!result.includes('K'), 'should not have K suffix')
  assert.equal(result, '0')
})

test('fmt: negative values still compact', () => {
  assert.equal(fmt(-1_200_000), '-1.20M')
})

test('fmt: small decimal rendered without suffix', () => {
  const result = fmt(42.7)
  assert.ok(!result.includes('K'))
  assert.ok(!result.includes('M'))
})

// ---------------------------------------------------------------------------
// 3. fmt() — custom digit parameter
// ---------------------------------------------------------------------------

test('fmt: digits=0 rounds to integer for M suffix', () => {
  assert.equal(fmt(2_500_000, 0), '3M')
})

test('fmt: digits=1 for K suffix', () => {
  assert.equal(fmt(3_800, 1), '3.8K')
})

// ---------------------------------------------------------------------------
// 4. Date helpers
// ---------------------------------------------------------------------------

test('today(): returns a YYYY-MM-DD formatted string', () => {
  const t = today()
  assert.match(t, /^\d{4}-\d{2}-\d{2}$/)
})

test('daysAgo(0): same as today', () => {
  assert.equal(daysAgo(0), today())
})

test('daysAgo(7): returns a date 7 days before today', () => {
  const ago = new Date()
  ago.setDate(ago.getDate() - 7)
  assert.equal(daysAgo(7), ago.toISOString().slice(0, 10))
})

test('daysAgo(n): is always before today for n > 0', () => {
  assert.ok(daysAgo(1) < today())
})

// ---------------------------------------------------------------------------
// 5. Direction inference
// ---------------------------------------------------------------------------

test('inferDirection: positive delta → up', () => {
  assert.equal(inferDirection(100), 'up')
})

test('inferDirection: negative delta → down', () => {
  assert.equal(inferDirection(-50), 'down')
})

test('inferDirection: zero delta → flat', () => {
  assert.equal(inferDirection(0), 'flat')
})
