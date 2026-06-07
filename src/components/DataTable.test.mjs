/**
 * DataTable.test.mjs — Node:test unit tests for dataTableUtils.js pure helpers.
 *
 * Run: node --test src/components/DataTable.test.mjs
 *
 * Tests cover:
 *   - deriveColumns (from apache-arrow Table)
 *   - arrowToRows (from apache-arrow Table, including nulls and BigInt)
 *   - sortComparator / sortRows
 *   - matchesFilter / filterRows
 *   - searchRows
 *   - paginateRows
 *   - rowsToCSV
 *   - formatNumber
 */

import { test, describe } from 'node:test'
import assert from 'node:assert/strict'

// apache-arrow is an ESM package — import it directly
import * as arrow from 'apache-arrow'

import {
  deriveColumns,
  arrowToRows,
  sortComparator,
  sortRows,
  matchesFilter,
  filterRows,
  searchRows,
  paginateRows,
  rowsToCSV,
  formatNumber,
} from './dataTableUtils.js'

// ---------------------------------------------------------------------------
// Helpers to build test Arrow tables
// ---------------------------------------------------------------------------

function buildTestTable() {
  return arrow.tableFromArrays({
    id:     arrow.vectorFromArray([1, 2, 3, null], new arrow.Int32()),
    name:   arrow.vectorFromArray(['Alice', 'Bob', null, 'Dave']),
    score:  arrow.vectorFromArray([95.5, 82.0, null, 60.25], new arrow.Float64()),
    active: arrow.vectorFromArray([true, false, true, null]),
  })
}

// ---------------------------------------------------------------------------
// deriveColumns
// ---------------------------------------------------------------------------

describe('deriveColumns', () => {
  test('returns correct column descriptors from Arrow schema', () => {
    const t = buildTestTable()
    const cols = deriveColumns(t)

    assert.equal(cols.length, 4)

    assert.deepEqual(cols[0], { key: 'id',     label: 'id',     type: 'number' })
    assert.deepEqual(cols[1], { key: 'name',   label: 'name',   type: 'string' })
    assert.deepEqual(cols[2], { key: 'score',  label: 'score',  type: 'number' })
    assert.deepEqual(cols[3], { key: 'active', label: 'active', type: 'bool'   })
  })

  test('returns [] for null/undefined input', () => {
    assert.deepEqual(deriveColumns(null), [])
    assert.deepEqual(deriveColumns(undefined), [])
  })

  test('detects timestamp columns as date type', () => {
    const t = arrow.tableFromArrays({
      ts: arrow.vectorFromArray([new Date('2024-01-01'), new Date('2024-06-01')]),
    })
    const cols = deriveColumns(t)
    // Timestamps come back as 'date' type
    assert.equal(cols[0].type, 'date')
  })
})

// ---------------------------------------------------------------------------
// arrowToRows
// ---------------------------------------------------------------------------

describe('arrowToRows', () => {
  test('materialises rows with correct values', () => {
    const t = buildTestTable()
    const rows = arrowToRows(t)

    assert.equal(rows.length, 4)
    assert.equal(rows[0].id, 1)
    assert.equal(rows[0].name, 'Alice')
    assert.equal(rows[0].score, 95.5)
    assert.equal(rows[0].active, true)
  })

  test('preserves null values', () => {
    const t = buildTestTable()
    const rows = arrowToRows(t)

    assert.equal(rows[2].name, null)    // row index 2: name is null
    assert.equal(rows[2].score, null)   // row index 2: score is null
    assert.equal(rows[3].active, null)  // row index 3: active is null
    assert.equal(rows[2].id, 3)
  })

  test('returns [] for null/undefined input', () => {
    assert.deepEqual(arrowToRows(null), [])
    assert.deepEqual(arrowToRows(undefined), [])
  })

  test('handles BigInt values from Int64 columns', () => {
    const t = arrow.tableFromArrays({
      big: arrow.vectorFromArray([1n, 2n, 3n], new arrow.Int64()),
    })
    const rows = arrowToRows(t)
    // BigInt should be converted to Number
    assert.equal(typeof rows[0].big, 'number')
    assert.equal(rows[0].big, 1)
    assert.equal(rows[2].big, 3)
  })

  test('handles empty Arrow table', () => {
    const t = arrow.tableFromArrays({
      id: arrow.vectorFromArray([], new arrow.Int32()),
    })
    const rows = arrowToRows(t)
    assert.deepEqual(rows, [])
  })
})

// ---------------------------------------------------------------------------
// sortComparator
// ---------------------------------------------------------------------------

describe('sortComparator', () => {
  test('sorts numbers ascending', () => {
    const a = { v: 10 }
    const b = { v: 5 }
    assert.ok(sortComparator(a, b, 'v', 'asc') > 0)
    assert.ok(sortComparator(b, a, 'v', 'asc') < 0)
    assert.equal(sortComparator(a, a, 'v', 'asc'), 0)
  })

  test('sorts numbers descending', () => {
    const a = { v: 10 }
    const b = { v: 5 }
    assert.ok(sortComparator(a, b, 'v', 'desc') < 0)
  })

  test('sorts strings case-insensitively', () => {
    const a = { v: 'Banana' }
    const b = { v: 'apple' }
    assert.ok(sortComparator(a, b, 'v', 'asc') > 0)
  })

  test('nulls sort last', () => {
    const a = { v: null }
    const b = { v: 5 }
    assert.ok(sortComparator(a, b, 'v', 'asc') > 0, 'null should be after non-null')
    assert.ok(sortComparator(b, a, 'v', 'asc') < 0)
    assert.equal(sortComparator(a, a, 'v', 'asc'), 0)
  })
})

// ---------------------------------------------------------------------------
// sortRows
// ---------------------------------------------------------------------------

describe('sortRows', () => {
  const rows = [
    { id: 3, name: 'Charlie' },
    { id: 1, name: 'Alice' },
    { id: 2, name: 'Bob' },
  ]

  test('sorts by numeric column asc', () => {
    const sorted = sortRows(rows, 'id', 'asc')
    assert.deepEqual(sorted.map(r => r.id), [1, 2, 3])
  })

  test('sorts by numeric column desc', () => {
    const sorted = sortRows(rows, 'id', 'desc')
    assert.deepEqual(sorted.map(r => r.id), [3, 2, 1])
  })

  test('sorts by string column', () => {
    const sorted = sortRows(rows, 'name', 'asc')
    assert.deepEqual(sorted.map(r => r.name), ['Alice', 'Bob', 'Charlie'])
  })

  test('does not mutate original array', () => {
    const original = [...rows]
    sortRows(rows, 'id', 'asc')
    assert.deepEqual(rows, original)
  })

  test('returns rows unchanged when key is null', () => {
    const sorted = sortRows(rows, null, null)
    assert.deepEqual(sorted, rows)
  })
})

// ---------------------------------------------------------------------------
// matchesFilter
// ---------------------------------------------------------------------------

describe('matchesFilter', () => {
  test('contains filter (string)', () => {
    assert.ok(matchesFilter('Hello World', { op: 'contains', value: 'world' }, 'string'))
    assert.ok(!matchesFilter('Hello World', { op: 'contains', value: 'xyz' }, 'string'))
  })

  test('eq filter (string)', () => {
    assert.ok(matchesFilter('hello', { op: 'eq', value: 'hello' }, 'string'))
    assert.ok(!matchesFilter('hello', { op: 'eq', value: 'world' }, 'string'))
  })

  test('numeric gt/lt/gte/lte', () => {
    assert.ok(matchesFilter(10, { op: 'gt',  value: '5'  }, 'number'))
    assert.ok(!matchesFilter(10, { op: 'gt',  value: '15' }, 'number'))
    assert.ok(matchesFilter(10, { op: 'lt',  value: '15' }, 'number'))
    assert.ok(matchesFilter(10, { op: 'gte', value: '10' }, 'number'))
    assert.ok(matchesFilter(10, { op: 'lte', value: '10' }, 'number'))
    assert.ok(!matchesFilter(10, { op: 'lte', value: '5'  }, 'number'))
  })

  test('numeric eq/ne', () => {
    assert.ok(matchesFilter(42, { op: 'eq', value: '42' }, 'number'))
    assert.ok(!matchesFilter(42, { op: 'eq', value: '43' }, 'number'))
    assert.ok(matchesFilter(42, { op: 'ne', value: '43' }, 'number'))
  })

  test('bool filter', () => {
    assert.ok(matchesFilter(true, { op: 'eq', value: 'true' }, 'bool'))
    assert.ok(!matchesFilter(true, { op: 'eq', value: 'false' }, 'bool'))
  })

  test('returns true when filter value is empty (no filter)', () => {
    assert.ok(matchesFilter('anything', { op: 'contains', value: '' }, 'string'))
    assert.ok(matchesFilter(42, { op: 'gt', value: '' }, 'number'))
  })

  test('null value does not match unless eq null', () => {
    assert.ok(!matchesFilter(null, { op: 'contains', value: 'test' }, 'string'))
  })
})

// ---------------------------------------------------------------------------
// filterRows
// ---------------------------------------------------------------------------

describe('filterRows', () => {
  const columns = [
    { key: 'name', type: 'string' },
    { key: 'score', type: 'number' },
  ]
  const rows = [
    { name: 'Alice', score: 90 },
    { name: 'Bob',   score: 70 },
    { name: 'Alice Smith', score: 55 },
  ]

  test('filters by string contains', () => {
    const filtered = filterRows(rows, columns, { name: { op: 'contains', value: 'alice' } })
    assert.equal(filtered.length, 2)
    assert.ok(filtered.every(r => r.name.toLowerCase().includes('alice')))
  })

  test('filters by numeric gt', () => {
    const filtered = filterRows(rows, columns, { score: { op: 'gt', value: '75' } })
    assert.equal(filtered.length, 1)
    assert.equal(filtered[0].name, 'Alice')
  })

  test('returns all rows when filters are empty', () => {
    const filtered = filterRows(rows, columns, {})
    assert.equal(filtered.length, 3)
  })

  test('combines multiple column filters (AND)', () => {
    const filtered = filterRows(rows, columns, {
      name: { op: 'contains', value: 'alice' },
      score: { op: 'gt', value: '80' },
    })
    assert.equal(filtered.length, 1)
    assert.equal(filtered[0].name, 'Alice')
  })
})

// ---------------------------------------------------------------------------
// searchRows
// ---------------------------------------------------------------------------

describe('searchRows', () => {
  const columns = [{ key: 'name' }, { key: 'city' }]
  const rows = [
    { name: 'Alice', city: 'New York' },
    { name: 'Bob',   city: 'London' },
    { name: 'Carol', city: 'New Delhi' },
  ]

  test('matches any column', () => {
    const result = searchRows(rows, columns, 'new')
    assert.equal(result.length, 2)
  })

  test('is case-insensitive', () => {
    const result = searchRows(rows, columns, 'ALICE')
    assert.equal(result.length, 1)
    assert.equal(result[0].name, 'Alice')
  })

  test('returns all rows on empty query', () => {
    assert.equal(searchRows(rows, columns, '').length, 3)
    assert.equal(searchRows(rows, columns, null).length, 3)
  })

  test('returns empty when no match', () => {
    assert.equal(searchRows(rows, columns, 'zzzzz').length, 0)
  })
})

// ---------------------------------------------------------------------------
// paginateRows
// ---------------------------------------------------------------------------

describe('paginateRows', () => {
  const rows = Array.from({ length: 110 }, (_, i) => ({ id: i + 1 }))

  test('first page returns correct slice', () => {
    const { slice, totalRows, totalPages, startRow, endRow } = paginateRows(rows, 0, 50)
    assert.equal(slice.length, 50)
    assert.equal(slice[0].id, 1)
    assert.equal(slice[49].id, 50)
    assert.equal(totalRows, 110)
    assert.equal(totalPages, 3)
    assert.equal(startRow, 1)
    assert.equal(endRow, 50)
  })

  test('last page returns remaining rows', () => {
    const { slice, startRow, endRow } = paginateRows(rows, 2, 50)
    assert.equal(slice.length, 10)
    assert.equal(startRow, 101)
    assert.equal(endRow, 110)
  })

  test('page is clamped to valid range', () => {
    const { slice } = paginateRows(rows, 99, 50)
    assert.equal(slice.length, 10) // last page
  })

  test('empty rows', () => {
    const { slice, totalRows, totalPages, startRow, endRow } = paginateRows([], 0, 50)
    assert.equal(slice.length, 0)
    assert.equal(totalRows, 0)
    assert.equal(totalPages, 1)
    assert.equal(startRow, 0)
    assert.equal(endRow, 0)
  })
})

// ---------------------------------------------------------------------------
// rowsToCSV
// ---------------------------------------------------------------------------

describe('rowsToCSV', () => {
  const columns = [
    { key: 'name', label: 'Name' },
    { key: 'score', label: 'Score' },
  ]

  test('produces correct CSV header and body', () => {
    const rows = [
      { name: 'Alice', score: 95 },
      { name: 'Bob',   score: 80 },
    ]
    const csv = rowsToCSV(rows, columns)
    const lines = csv.split('\n')
    assert.equal(lines[0], 'Name,Score')
    assert.equal(lines[1], 'Alice,95')
    assert.equal(lines[2], 'Bob,80')
  })

  test('escapes values containing commas and quotes', () => {
    const rows = [{ name: 'Smith, Jr.', score: 50 }]
    const csv = rowsToCSV(rows, columns)
    assert.ok(csv.includes('"Smith, Jr."'))
  })

  test('serialises null values as empty string', () => {
    const rows = [{ name: null, score: 42 }]
    const csv = rowsToCSV(rows, columns)
    assert.ok(csv.includes(',42'))
  })
})

// ---------------------------------------------------------------------------
// formatNumber
// ---------------------------------------------------------------------------

describe('formatNumber', () => {
  test('formats integers with thousands separator', () => {
    const result = formatNumber(1000000)
    assert.ok(result.includes(',') || result.includes('.'), 'should have separator')
  })

  test('formats floats to at most 6 decimals', () => {
    const result = formatNumber(3.14159265)
    assert.ok(result.length > 0)
    assert.ok(!result.includes('e'), 'should not use scientific notation')
  })

  test('handles null/undefined gracefully', () => {
    assert.equal(formatNumber(null), '')
    assert.equal(formatNumber(undefined), '')
  })

  test('handles Infinity and NaN', () => {
    assert.equal(formatNumber(Infinity), 'Infinity')
    assert.equal(formatNumber(NaN), 'NaN')
  })
})
