/**
 * reportSorting.test.ts — which way a report table's group columns start sorted:
 * dates and months newest first, everything else A→Z (isDateLikeColumn /
 * reportDefaultSorting in dataTableUtils).
 */
import { test, describe } from 'node:test'
import assert from 'node:assert/strict'

import { isDateLikeColumn, reportDefaultSorting } from './dataTableUtils.js'

describe('isDateLikeColumn', () => {
  test('a date column always counts', () => {
    assert.equal(isDateLikeColumn('FY', 'date', []), true)
    assert.equal(isDateLikeColumn('Anything', 'date', ['2025-05-01']), true)
  })

  test('ISO dates and months held as text count', () => {
    assert.equal(isDateLikeColumn('Reporting Month', 'string', ['2025-05', '2025-04', null, '']), true)
    assert.equal(isDateLikeColumn('Day', 'string', ['2025-05-01', '2025-05-02']), true)
    assert.equal(isDateLikeColumn('At', 'string', ['2025-05-01 14:30:00', '2025-05-01T09:00:00Z']), true)
    assert.equal(isDateLikeColumn('Day', 'string', ['2025/05/01']), true)
  })

  test('text that is not all dates does not count', () => {
    assert.equal(isDateLikeColumn('Area', 'string', ['01 - JHB', '02 - PTA']), false)
    assert.equal(isDateLikeColumn('Mixed', 'string', ['2025-05', 'Total']), false)
    assert.equal(isDateLikeColumn('Code', 'string', ['2025-13']), false)   // no month 13
    assert.equal(isDateLikeColumn('Empty', 'string', [null, undefined, '']), false)
  })

  test('years and year-months count only when the name says so', () => {
    assert.equal(isDateLikeColumn('FinYear', 'number', [2024, 2025]), true)
    assert.equal(isDateLikeColumn('Period', 'number', [202504, 202505]), true)
    assert.equal(isDateLikeColumn('TrnYear', 'string', ['2023', '2024']), true)
    assert.equal(isDateLikeColumn('FY', 'number', [2025n as unknown as number]), true)
    // the same values under an ordinary name are left alone
    assert.equal(isDateLikeColumn('Units', 'number', [2024, 2025]), false)
    assert.equal(isDateLikeColumn('StockCode', 'string', ['2024', '2025']), false)
    // a period-named column of month numbers or counts is not a date
    assert.equal(isDateLikeColumn('Month', 'number', [1, 2, 12]), false)
    assert.equal(isDateLikeColumn('Period', 'number', [202513]), false)
    assert.equal(isDateLikeColumn('Year', 'number', [2025.5]), false)
  })
})

describe('reportDefaultSorting', () => {
  const columns = [
    { key: 'FY_Date', type: 'date' },
    { key: 'Category_Split', type: 'string' },
    { key: 'StockCode', type: 'string' },
  ]

  test('date groups newest first, other groups A→Z, outermost first', () => {
    assert.deepEqual(
      reportDefaultSorting(['FY_Date', 'Category_Split'], columns, []),
      [{ id: 'FY_Date', desc: true }, { id: 'Category_Split', desc: false }],
    )
    assert.deepEqual(reportDefaultSorting(['StockCode'], columns, [{ StockCode: 'AC05' }]), [{ id: 'StockCode', desc: false }])
  })

  test('a text column of months is recognised from its values', () => {
    const rows = [{ M: '2026-11' }, { M: '2026-10' }]
    assert.deepEqual(reportDefaultSorting(['M'], [{ key: 'M', type: 'string' }], rows), [{ id: 'M', desc: true }])
  })

  test('no grouping, no default sort', () => {
    assert.deepEqual(reportDefaultSorting(undefined, columns, []), [])
    assert.deepEqual(reportDefaultSorting([], columns, []), [])
  })
})
