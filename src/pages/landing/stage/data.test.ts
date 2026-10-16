/** The landing's displayed numbers must equal the values derived from the data (storyboard §7.1). */
import { test } from 'node:test'
import assert from 'node:assert/strict'
import * as D from './data'

test('14-day KPI strings are derived from the series', () => {
  const k = D.kpis()
  assert.equal(D.REV_TOTAL_K, 5045)
  assert.equal(k[0].value, '5.05M')
  assert.equal(D.ORD_TOTAL_K, 79.5)
  assert.equal(k[1].value, '79.5k')
  assert.equal(k[2].value, '63.46')
  assert.equal(D.FULFIL_MEAN_S, 224)
  assert.equal(k[3].value, '3:44')
  assert.equal(k[0].full, 'Revenue · 14 days: $5,045,000')
})

test('day-10 KPI strings (index 9) match the storyboard', () => {
  const k = D.kpis(9)
  assert.deepEqual(k.map(x => x.value), ['362k', '5.7k', '63.51', '3:41'])
  assert.deepEqual(D.regionRevK(9), [145, 105, 76, 36])
})

test('region split and byte shares', () => {
  assert.deepEqual(D.REGION_REV_K, D.REGION_SHARE.map(s => Math.round(5045 * s)))
  assert.equal(D.fmtPct(D.fieldByteShare(6)), '28.6%')
  assert.equal(D.POLICY_ROWS_PER_REGION / D.POLICY_ROWS, 0.25)
  assert.equal(D.fmtInt(D.viewers(1)), '1,900')
})

test('series lengths agree', () => {
  for (const s of [D.REV_K, D.ORD_K, D.FULFIL_S]) assert.equal(s.length, D.DAYS)
  assert.equal(D.FIELDS.length, D.FIELD_BYTES.length)
  assert.equal(D.FIELDS.length, D.FIELD_SHORT.length)
})
