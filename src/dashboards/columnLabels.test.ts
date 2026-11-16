/**
 * columnLabels.test.mjs — how a result column becomes a table header.
 *
 * Run: npm run test:dash
 */

import { test, describe } from 'node:test'
import assert from 'node:assert/strict'

import { splitWords, humaniseColumn, columnLabel } from '../../embed/widgets/column-labels.js'

describe('splitWords', () => {
  test('splits snake, camel and kebab alike', () => {
    assert.deepEqual(splitWords('net_sales'), ['net', 'sales'])
    assert.deepEqual(splitWords('grossMarginPct'), ['gross', 'margin', 'pct'])
    assert.deepEqual(splitWords('service-seconds'), ['service', 'seconds'])
    assert.deepEqual(splitWords('avg_ticket_2024'), ['avg', 'ticket', '2024'])
  })

  test('survives the shapes a driver can actually return', () => {
    assert.deepEqual(splitWords(''), [])
    assert.deepEqual(splitWords(undefined), [])
    assert.deepEqual(splitWords('__weird__'), ['weird'])
  })
})

describe('humaniseColumn', () => {
  test('reads as a label, in sentence case', () => {
    assert.equal(humaniseColumn('net_sales'), 'Net sales')
    assert.equal(humaniseColumn('service_seconds'), 'Service seconds')
    assert.equal(humaniseColumn('store'), 'Store')
  })

  test('expands the abbreviations that read worse spelled out', () => {
    assert.equal(humaniseColumn('gross_margin_pct'), 'Gross margin %')
    assert.equal(humaniseColumn('avg_ticket'), 'Average ticket')
    assert.equal(humaniseColumn('order_id'), 'Order ID')
    assert.equal(humaniseColumn('qty'), 'Quantity')
  })

  test('capitalises the first word that has letters, not a leading symbol', () => {
    // 'pct_of_sales' maps its first word to '%', which cannot take a capital;
    // the capital has to land on the next real word instead.
    assert.equal(humaniseColumn('pct_of_sales'), '% Of sales')
  })

  test('leaves an unknown column recognisable rather than mangling it', () => {
    assert.equal(humaniseColumn('nsv'), 'Nsv')
    assert.equal(humaniseColumn(''), '')
  })
})

describe('columnLabel', () => {
  test('an explicit label always wins', () => {
    assert.equal(columnLabel('gross_margin_pct', { gross_margin_pct: 'GM%' }), 'GM%')
    assert.equal(columnLabel('net_sales', { other: 'x' }), 'Net sales')
    assert.equal(columnLabel('net_sales', null), 'Net sales')
  })

  test('an explicit empty label hides the header, rather than falling back', () => {
    // Falling back here would make it impossible to blank a header at all.
    assert.equal(columnLabel('store', { store: '' }), '')
  })
})
