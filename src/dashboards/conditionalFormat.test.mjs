/**
 * conditionalFormat.test.mjs — Unit tests for evalRules() and formatValue().
 *
 * Run with:
 *   node --test src/dashboards/conditionalFormat.test.mjs
 *   # or via the project script:
 *   npm run test:dash
 */

import { test } from 'node:test'
import assert from 'node:assert/strict'

import {
  evalRules,
  formatValue,
} from './widgets/conditionalFormat.js'

// ---------------------------------------------------------------------------
// evalRules — no rules / empty
// ---------------------------------------------------------------------------

test('no rules → empty cellStyles and null rowStyle', () => {
  const result = evalRules([], { amount: 500 }, ['amount'])
  assert.deepEqual(result.cellStyles, {})
  assert.equal(result.rowStyle, null)
})

test('null rules → empty cellStyles and null rowStyle', () => {
  const result = evalRules(null, { amount: 500 }, ['amount'])
  assert.deepEqual(result.cellStyles, {})
  assert.equal(result.rowStyle, null)
})

// ---------------------------------------------------------------------------
// evalRules — gt (cell scope)
// ---------------------------------------------------------------------------

test('gt rule: value above threshold → cell gets backgroundColor', () => {
  const rules = [
    {
      column: 'amount',
      op: 'gt',
      value: 100,
      style: { backgroundColor: '#ff0000', color: '#fff' },
      scope: 'cell',
    },
  ]
  const row = { amount: 500 }
  const { cellStyles, rowStyle } = evalRules(rules, row, ['amount'])

  assert.equal(rowStyle, null)
  assert.deepEqual(cellStyles.amount, { backgroundColor: '#ff0000', color: '#fff' })
})

test('gt rule: value at threshold (not strictly greater) → no style', () => {
  const rules = [
    {
      column: 'amount',
      op: 'gt',
      value: 500,
      style: { backgroundColor: '#ff0000' },
      scope: 'cell',
    },
  ]
  const { cellStyles, rowStyle } = evalRules(rules, { amount: 500 }, ['amount'])
  assert.deepEqual(cellStyles, {})
  assert.equal(rowStyle, null)
})

test('gt rule: value below threshold → no style', () => {
  const rules = [
    {
      column: 'amount',
      op: 'gt',
      value: 100,
      style: { backgroundColor: '#ff0000' },
      scope: 'cell',
    },
  ]
  const { cellStyles } = evalRules(rules, { amount: 50 }, ['amount'])
  assert.deepEqual(cellStyles, {})
})

// ---------------------------------------------------------------------------
// evalRules — between (cell scope)
// ---------------------------------------------------------------------------

test('between rule: value in range → cell style applied', () => {
  const rules = [
    {
      column: 'score',
      op: 'between',
      value: 70,
      value2: 90,
      style: { backgroundColor: '#fde68a', fontWeight: 'bold' },
      scope: 'cell',
    },
  ]
  const { cellStyles } = evalRules(rules, { score: 80 }, ['score'])
  assert.deepEqual(cellStyles.score, { backgroundColor: '#fde68a', fontWeight: 'bold' })
})

test('between rule: value exactly at lower bound → style applied', () => {
  const rules = [
    {
      column: 'score',
      op: 'between',
      value: 70,
      value2: 90,
      style: { backgroundColor: '#fde68a' },
      scope: 'cell',
    },
  ]
  const { cellStyles } = evalRules(rules, { score: 70 }, ['score'])
  assert.deepEqual(cellStyles.score, { backgroundColor: '#fde68a' })
})

test('between rule: value exactly at upper bound → style applied', () => {
  const rules = [
    {
      column: 'score',
      op: 'between',
      value: 70,
      value2: 90,
      style: { backgroundColor: '#fde68a' },
      scope: 'cell',
    },
  ]
  const { cellStyles } = evalRules(rules, { score: 90 }, ['score'])
  assert.deepEqual(cellStyles.score, { backgroundColor: '#fde68a' })
})

test('between rule: value outside range → no style', () => {
  const rules = [
    {
      column: 'score',
      op: 'between',
      value: 70,
      value2: 90,
      style: { backgroundColor: '#fde68a' },
      scope: 'cell',
    },
  ]
  const { cellStyles } = evalRules(rules, { score: 91 }, ['score'])
  assert.deepEqual(cellStyles, {})
})

// ---------------------------------------------------------------------------
// evalRules — row scope
// ---------------------------------------------------------------------------

test('row-scope rule: match → rowStyle set, cellStyles empty', () => {
  const rules = [
    {
      column: 'status',
      op: 'eq',
      value: 'error',
      style: { backgroundColor: '#fee2e2', color: '#991b1b' },
      scope: 'row',
    },
  ]
  const { cellStyles, rowStyle } = evalRules(rules, { status: 'error', amount: 100 }, ['status', 'amount'])

  assert.deepEqual(cellStyles, {})
  assert.deepEqual(rowStyle, { backgroundColor: '#fee2e2', color: '#991b1b' })
})

test('row-scope rule: no match → rowStyle null', () => {
  const rules = [
    {
      column: 'status',
      op: 'eq',
      value: 'error',
      style: { backgroundColor: '#fee2e2' },
      scope: 'row',
    },
  ]
  const { rowStyle } = evalRules(rules, { status: 'ok' }, ['status'])
  assert.equal(rowStyle, null)
})

test('row-scope rules merge (last-writer-wins per property)', () => {
  const rules = [
    {
      column: 'score',
      op: 'gte',
      value: 0,
      style: { backgroundColor: '#d1fae5', color: '#065f46' },
      scope: 'row',
    },
    {
      column: 'score',
      op: 'gt',
      value: 90,
      style: { backgroundColor: '#fef3c7' }, // overrides first backgroundColor
      scope: 'row',
    },
  ]
  const { rowStyle } = evalRules(rules, { score: 95 }, ['score'])
  // second rule wins for backgroundColor, color from first survives
  assert.deepEqual(rowStyle, { backgroundColor: '#fef3c7', color: '#065f46' })
})

// ---------------------------------------------------------------------------
// evalRules — other operators
// ---------------------------------------------------------------------------

test('ne rule: value differs → cell style applied', () => {
  const rules = [{ column: 'flag', op: 'ne', value: 1, style: { color: 'red' }, scope: 'cell' }]
  const { cellStyles } = evalRules(rules, { flag: 0 }, ['flag'])
  assert.deepEqual(cellStyles.flag, { color: 'red' })
})

test('gte rule: value equals threshold → cell style applied', () => {
  const rules = [{ column: 'qty', op: 'gte', value: 10, style: { fontWeight: 'bold' }, scope: 'cell' }]
  const { cellStyles } = evalRules(rules, { qty: 10 }, ['qty'])
  assert.deepEqual(cellStyles.qty, { fontWeight: 'bold' })
})

test('lt rule: value below threshold → cell style applied', () => {
  const rules = [{ column: 'stock', op: 'lt', value: 5, style: { color: 'orange' }, scope: 'cell' }]
  const { cellStyles } = evalRules(rules, { stock: 3 }, ['stock'])
  assert.deepEqual(cellStyles.stock, { color: 'orange' })
})

test('lte rule: value at threshold → cell style applied', () => {
  const rules = [{ column: 'stock', op: 'lte', value: 5, style: { color: 'orange' }, scope: 'cell' }]
  const { cellStyles } = evalRules(rules, { stock: 5 }, ['stock'])
  assert.deepEqual(cellStyles.stock, { color: 'orange' })
})

test('contains rule: substring match (case-insensitive) → cell style applied', () => {
  const rules = [{ column: 'label', op: 'contains', value: 'ERROR', style: { color: 'red' }, scope: 'cell' }]
  const { cellStyles } = evalRules(rules, { label: 'network error' }, ['label'])
  assert.deepEqual(cellStyles.label, { color: 'red' })
})

test('contains rule: no match → no style', () => {
  const rules = [{ column: 'label', op: 'contains', value: 'ERROR', style: { color: 'red' }, scope: 'cell' }]
  const { cellStyles } = evalRules(rules, { label: 'all good' }, ['label'])
  assert.deepEqual(cellStyles, {})
})

// ---------------------------------------------------------------------------
// evalRules — multiple rules on different columns
// ---------------------------------------------------------------------------

test('multiple rules on different columns → independent cellStyles', () => {
  const rules = [
    { column: 'revenue', op: 'gt', value: 1000, style: { color: 'green' }, scope: 'cell' },
    { column: 'cost',    op: 'gt', value: 500,  style: { color: 'red'   }, scope: 'cell' },
  ]
  const row = { revenue: 2000, cost: 600 }
  const { cellStyles } = evalRules(rules, row, ['revenue', 'cost'])
  assert.deepEqual(cellStyles.revenue, { color: 'green' })
  assert.deepEqual(cellStyles.cost,    { color: 'red'   })
})

// ---------------------------------------------------------------------------
// formatValue — currency
// ---------------------------------------------------------------------------

test('formatValue currency: formats a number as USD', () => {
  const result = formatValue(1234.5, { type: 'currency', currency: 'USD', locale: 'en-US' })
  // Must include '$' and the number
  assert.ok(result.includes('$'), `expected $ in: ${result}`)
  assert.ok(result.includes('1,234') || result.includes('1234'), `expected numeric part in: ${result}`)
})

test('formatValue currency: rounds to 2 decimals by default', () => {
  const result = formatValue(9.9, { type: 'currency', currency: 'USD', locale: 'en-US' })
  assert.ok(result.includes('9.90'), `expected 9.90 in: ${result}`)
})

test('formatValue currency: respects explicit decimals:0', () => {
  const result = formatValue(1234.99, { type: 'currency', currency: 'USD', locale: 'en-US', decimals: 0 })
  assert.ok(!result.includes('.'), `expected no decimal point in: ${result}`)
})

// ---------------------------------------------------------------------------
// formatValue — number
// ---------------------------------------------------------------------------

test('formatValue number: formats with locale grouping', () => {
  const result = formatValue(1234567, { type: 'number', locale: 'en-US' })
  assert.ok(result.includes('1,234,567'), `expected grouped number in: ${result}`)
})

test('formatValue number: respects decimals', () => {
  const result = formatValue(3.14159, { type: 'number', locale: 'en-US', decimals: 2 })
  assert.equal(result, '3.14')
})

// ---------------------------------------------------------------------------
// formatValue — percent
// ---------------------------------------------------------------------------

test('formatValue percent: treats value as ratio (0-1)', () => {
  const result = formatValue(0.875, { type: 'percent', locale: 'en-US' })
  // 0.875 → 87.5% rounded by Intl → "88%" or "87.5%" depending on defaults
  assert.ok(result.includes('%'), `expected % sign in: ${result}`)
  assert.ok(result.includes('88') || result.includes('87'), `expected 87 or 88 in: ${result}`)
})

test('formatValue percent: respects decimals', () => {
  const result = formatValue(0.1234, { type: 'percent', locale: 'en-US', decimals: 1 })
  assert.ok(result.includes('12.3%'), `expected 12.3% in: ${result}`)
})

// ---------------------------------------------------------------------------
// formatValue — date
// ---------------------------------------------------------------------------

test('formatValue date: formats an ISO string', () => {
  const result = formatValue('2024-03-15', { type: 'date', locale: 'en-US' })
  // short date style → 3/15/2024 in en-US
  assert.ok(result.includes('2024') || result.includes('24'), `expected year in: ${result}`)
  assert.ok(result.includes('15') || result.includes('3'), `expected month/day in: ${result}`)
})

test('formatValue date: formats a Date object', () => {
  const d = new Date(2024, 0, 5) // Jan 5 2024
  const result = formatValue(d, { type: 'date', locale: 'en-US' })
  assert.ok(result.includes('2024') || result.includes('24'), `expected year in: ${result}`)
})

test('formatValue date: respects dateStyle:long', () => {
  const result = formatValue('2024-06-01', { type: 'date', locale: 'en-US', dateStyle: 'long' })
  // long date includes month name
  assert.ok(result.includes('June') || result.includes('2024'), `expected long date in: ${result}`)
})

// ---------------------------------------------------------------------------
// formatValue — edge cases
// ---------------------------------------------------------------------------

test('formatValue: null value returns em-dash', () => {
  assert.equal(formatValue(null, { type: 'currency' }), '—')
})

test('formatValue: undefined value returns em-dash', () => {
  assert.equal(formatValue(undefined, { type: 'number' }), '—')
})

test('formatValue: no fmt returns String(value)', () => {
  assert.equal(formatValue(42, null), '42')
  assert.equal(formatValue('hello', undefined), 'hello')
})

test('formatValue: unknown type falls back to String(value)', () => {
  assert.equal(formatValue(99, { type: 'unknown_type' }), '99')
})
