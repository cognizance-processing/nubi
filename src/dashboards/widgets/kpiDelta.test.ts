import test from 'node:test'
import assert from 'node:assert/strict'
import { computeTrend } from './kpiDelta'

test('computeTrend shows growth against the baseline with two decimals', () => {
  assert.deepEqual(computeTrend(131, 123), { text: '6.50%', direction: 'up' })
})

test('computeTrend keeps the minus sign on a decline', () => {
  assert.deepEqual(computeTrend(9932438, 12612838), { text: '-21.25%', direction: 'down' })
})

test('computeTrend reports no change as flat', () => {
  assert.deepEqual(computeTrend(5, 5), { text: '0.00%', direction: 'flat' })
})

test('computeTrend does not invent a percentage from a zero baseline', () => {
  assert.deepEqual(computeTrend(5, 0), { text: '—', direction: 'up' })
})

test('computeTrend returns nothing when either side is missing or not numeric', () => {
  assert.equal(computeTrend(null, 1), null)
  assert.equal(computeTrend(1, undefined), null)
  assert.equal(computeTrend('x', 1), null)
})

test('computeTrend honours the requested precision', () => {
  assert.equal(computeTrend(131, 123, 1)?.text, '6.5%')
})
