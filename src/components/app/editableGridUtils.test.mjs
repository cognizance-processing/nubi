/**
 * editableGridUtils.test.mjs — node:test unit tests for the editable grid's
 * pure logic (type classification, coercion, pk handling, formatting, sort,
 * search, read-only gating).
 *
 * Run: node --test src/components/app/editableGridUtils.test.mjs
 */

import { test, describe } from 'node:test'
import assert from 'node:assert/strict'

import {
  classifyType,
  baseKind,
  editorKind,
  normalizeColumnMeta,
  columnIsEditable,
  isReadOnly,
  formatCell,
  toEditString,
  coerceInput,
  pkObject,
  rowMatchesPk,
  sortComparator,
  sortRows,
  searchRows,
  matchFilter,
  filterRows,
  baseColumnWidth,
  distributeColumnWidths,
  moveSelection,
  copyCellText,
} from './editableGridUtils.js'

// ---------------------------------------------------------------------------
describe('classifyType', () => {
  test('integers', () => {
    for (const t of ['INTEGER', 'BIGINT', 'int4', 'HUGEINT', 'serial'])
      assert.equal(classifyType(t), 'int')
  })
  test('floats', () => {
    for (const t of ['DECIMAL(10,2)', 'double', 'real', 'numeric', 'float8'])
      assert.equal(classifyType(t), 'float')
  })
  test('uuid', () => {
    assert.equal(classifyType('uuid'), 'uuid')
    assert.equal(classifyType('GUID'), 'uuid')
  })
  test('int/float collapse to number behaviour', () => {
    assert.equal(baseKind('int'), 'number')
    assert.equal(baseKind('float'), 'number')
    assert.equal(baseKind('uuid'), 'string')
    assert.equal(baseKind('bool'), 'bool')
  })
  test('bool', () => {
    assert.equal(classifyType('BOOLEAN'), 'bool')
    assert.equal(classifyType('bool'), 'bool')
  })
  test('date wins over int substring', () => {
    assert.equal(classifyType('timestamp'), 'date')
    assert.equal(classifyType('TIMESTAMPTZ'), 'date')
    assert.equal(classifyType('date'), 'date')
    assert.equal(classifyType('time'), 'date')
  })
  test('json/struct/array', () => {
    assert.equal(classifyType('JSONB'), 'json')
    assert.equal(classifyType('struct<a int>'), 'json')
    assert.equal(classifyType('int[]'), 'json')
  })
  test('fallback string', () => {
    assert.equal(classifyType('varchar'), 'string')
    assert.equal(classifyType('TEXT'), 'string')
    assert.equal(classifyType(null), 'string')
    assert.equal(classifyType(undefined), 'string')
  })
})

describe('editorKind', () => {
  test('maps kinds to widgets', () => {
    assert.equal(editorKind('number'), 'number')
    assert.equal(editorKind('bool'), 'checkbox')
    assert.equal(editorKind('date'), 'date')
    assert.equal(editorKind('json'), 'textarea')
    assert.equal(editorKind('string'), 'text')
  })
})

// ---------------------------------------------------------------------------
describe('normalizeColumnMeta', () => {
  test('new write-contract shape', () => {
    const m = normalizeColumnMeta({
      writable: true,
      primary_key: ['id'],
      columns: [
        { name: 'id', type: 'integer', nullable: false, editable: false },
        { name: 'name', type: 'varchar', nullable: true, editable: true },
        { name: 'active', type: 'boolean', nullable: false, editable: true },
      ],
    })
    assert.equal(m.writable, true)
    assert.deepEqual(m.primaryKey, ['id'])
    assert.equal(m.columns.length, 3)
    const [id, name, active] = m.columns
    assert.equal(id.pk, true)
    assert.equal(id.editable, false)
    assert.equal(name.kind, 'string')
    assert.equal(name.editable, true)
    assert.equal(active.kind, 'bool')
  })

  test('PK column is non-editable even if backend omits editable flag', () => {
    const m = normalizeColumnMeta({
      writable: true,
      primary_key: ['id'],
      columns: [{ name: 'id', type: 'int' }, { name: 'x', type: 'int' }],
    })
    assert.equal(m.columns[0].editable, false) // pk
    assert.equal(m.columns[1].editable, true)
  })

  test('legacy pk flag + missing writable degrades read-only', () => {
    const m = normalizeColumnMeta({
      columns: [{ name: 'id', type: 'int', pk: true }, { name: 'v', type: 'text' }],
    })
    assert.equal(m.writable, false)
    assert.deepEqual(m.primaryKey, ['id'])
  })

  test('bare string list', () => {
    const m = normalizeColumnMeta(['a', 'b'])
    assert.equal(m.columns.length, 2)
    assert.equal(m.columns[0].name, 'a')
    assert.equal(m.writable, false)
  })
})

describe('columnIsEditable / isReadOnly', () => {
  const col = { name: 'x', editable: true, pk: false }
  test('requires writable + pk', () => {
    assert.equal(columnIsEditable(col, true, true), true)
    assert.equal(columnIsEditable(col, false, true), false)
    assert.equal(columnIsEditable(col, true, false), false)
  })
  test('pk + non-editable columns blocked', () => {
    assert.equal(columnIsEditable({ ...col, pk: true }, true, true), false)
    assert.equal(columnIsEditable({ ...col, editable: false }, true, true), false)
  })
  test('isReadOnly reasons', () => {
    assert.deepEqual(isReadOnly({ writable: true, primaryKey: ['id'] }), { readOnly: false, reason: null })
    assert.equal(isReadOnly({ writable: false, primaryKey: ['id'] }).readOnly, true)
    assert.match(isReadOnly({ writable: true, primaryKey: [] }).reason, /primary key/i)
    assert.equal(isReadOnly(null).readOnly, true)
  })
})

// ---------------------------------------------------------------------------
describe('formatCell', () => {
  test('null', () => {
    const r = formatCell(null, 'string')
    assert.equal(r.isNull, true)
    assert.equal(r.display, 'NULL')
  })
  test('number right-aligned + grouped', () => {
    const r = formatCell(1234567, 'number')
    assert.equal(r.align, 'right')
    assert.equal(r.display, '1,234,567')
  })
  test('bool', () => {
    assert.equal(formatCell(true, 'bool').display, 'true')
    assert.equal(formatCell(false, 'bool').display, 'false')
  })
  test('json stringifies objects', () => {
    assert.equal(formatCell({ a: 1 }, 'json').display, '{"a":1}')
  })
  test('null number is right aligned', () => {
    assert.equal(formatCell(null, 'number').align, 'right')
  })
})

describe('toEditString', () => {
  test('null → empty', () => assert.equal(toEditString(null, 'string'), ''))
  test('json pretty-printed', () => assert.equal(toEditString({ a: 1 }, 'json'), '{\n  "a": 1\n}'))
  test('number → string', () => assert.equal(toEditString(42, 'number'), '42'))
})

// ---------------------------------------------------------------------------
describe('coerceInput', () => {
  test('number valid / invalid', () => {
    assert.deepEqual(coerceInput('42', 'number', false), { ok: true, value: 42, error: null })
    assert.equal(coerceInput('abc', 'number', false).ok, false)
  })
  test('number empty → null only if nullable', () => {
    assert.deepEqual(coerceInput('', 'number', true), { ok: true, value: null, error: null })
    assert.equal(coerceInput('', 'number', false).ok, false)
  })
  test('bool parsing', () => {
    assert.equal(coerceInput('TRUE', 'bool', false).value, true)
    assert.equal(coerceInput('0', 'bool', false).value, false)
    assert.equal(coerceInput(true, 'bool', false).value, true)
    assert.equal(coerceInput('maybe', 'bool', false).ok, false)
  })
  test('explicit null sentinel honours nullability', () => {
    assert.deepEqual(coerceInput(null, 'string', true), { ok: true, value: null, error: null })
    assert.equal(coerceInput(null, 'string', false).ok, false)
  })
  test('json parse', () => {
    assert.deepEqual(coerceInput('{"a":1}', 'json', false).value, { a: 1 })
    assert.equal(coerceInput('{bad', 'json', false).ok, false)
  })
  test('string passes through; empty→null if nullable', () => {
    assert.equal(coerceInput('hi', 'string', false).value, 'hi')
    assert.equal(coerceInput('', 'string', true).value, null)
    assert.equal(coerceInput('', 'string', false).value, '')
  })
})

// ---------------------------------------------------------------------------
describe('pkObject / rowMatchesPk', () => {
  const row = { id: 7, org: 'a', name: 'x' }
  test('builds composite pk', () => {
    assert.deepEqual(pkObject(row, ['id', 'org']), { id: 7, org: 'a' })
  })
  test('null when pk col missing', () => {
    assert.equal(pkObject(row, ['nope']), null)
    assert.equal(pkObject(row, []), null)
  })
  test('rowMatchesPk', () => {
    assert.equal(rowMatchesPk(row, { id: 7, org: 'a' }), true)
    assert.equal(rowMatchesPk(row, { id: 8 }), false)
  })
})

// ---------------------------------------------------------------------------
describe('sort + search', () => {
  const rows = [
    { id: 3, name: 'Charlie', score: null },
    { id: 1, name: 'alice', score: 9 },
    { id: 2, name: 'Bob', score: 4 },
  ]
  test('sort numbers asc/desc', () => {
    assert.deepEqual(sortRows(rows, 'id', 'asc').map((r) => r.id), [1, 2, 3])
    assert.deepEqual(sortRows(rows, 'id', 'desc').map((r) => r.id), [3, 2, 1])
  })
  test('nulls last regardless of dir', () => {
    assert.equal(sortRows(rows, 'score', 'asc').at(-1).score, null)
    assert.equal(sortRows(rows, 'score', 'desc').at(-1).score, null)
  })
  test('case-insensitive string sort', () => {
    assert.deepEqual(sortRows(rows, 'name', 'asc').map((r) => r.name), ['alice', 'Bob', 'Charlie'])
  })
  test('null key returns input', () => {
    assert.equal(sortRows(rows, null, 'asc'), rows)
  })
  test('search across columns, ignores nulls', () => {
    const cols = [{ name: 'id' }, { name: 'name' }, { name: 'score' }]
    assert.equal(searchRows(rows, cols, 'bob').length, 1)
    assert.equal(searchRows(rows, cols, '').length, 3)
    assert.equal(searchRows(rows, cols, 'zzz').length, 0)
  })
})

describe('sortComparator direct', () => {
  test('bool ordering', () => {
    assert.ok(sortComparator({ a: false }, { a: true }, 'a', 'asc') < 0)
  })
})

// ---------------------------------------------------------------------------
describe('matchFilter / filterRows', () => {
  test('null operators', () => {
    assert.equal(matchFilter(null, { op: 'is_null' }), true)
    assert.equal(matchFilter(5, { op: 'is_null' }), false)
    assert.equal(matchFilter(null, { op: 'not_null' }), false)
    assert.equal(matchFilter(5, { op: 'not_null' }), true)
  })
  test('numeric comparisons', () => {
    assert.equal(matchFilter(10, { op: 'gt', value: '5' }), true)
    assert.equal(matchFilter(10, { op: 'lte', value: '10' }), true)
    assert.equal(matchFilter(10, { op: 'eq', value: '10' }), true)
    assert.equal(matchFilter(10, { op: 'neq', value: '10' }), false)
  })
  test('string contains / falls back when non-numeric', () => {
    assert.equal(matchFilter('Gauteng', { op: 'contains', value: 'aut' }), true)
    assert.equal(matchFilter('Gauteng', { op: 'eq', value: 'gauteng' }), true)
    assert.equal(matchFilter('Gauteng', { op: 'contains', value: 'xyz' }), false)
  })
  test('null cell with value op fails', () => {
    assert.equal(matchFilter(null, { op: 'eq', value: '5' }), false)
  })
  test('filterRows AND semantics + ignores incomplete filters', () => {
    const rows = [
      { region: 'Gauteng', amount: 100 },
      { region: 'Western Cape', amount: 50 },
      { region: 'Gauteng', amount: 20 },
    ]
    const out = filterRows(rows, [
      { column: 'region', op: 'eq', value: 'Gauteng' },
      { column: 'amount', op: 'gt', value: '30' },
      { column: '', op: 'eq', value: 'x' }, // ignored (no column)
      { column: 'amount', op: 'gt', value: '' }, // ignored (empty value)
    ])
    assert.equal(out.length, 1)
    assert.equal(out[0].amount, 100)
  })
  test('empty filters returns input ref', () => {
    const rows = [{ a: 1 }]
    assert.equal(filterRows(rows, []), rows)
  })
})

// ---------------------------------------------------------------------------
describe('baseColumnWidth / distributeColumnWidths', () => {
  test('kinds get sensible base widths', () => {
    assert.ok(baseColumnWidth({ name: 'ok', kind: 'bool' }) <= 100)
    assert.ok(baseColumnWidth({ name: 'created_at', kind: 'date' }) >= 170)
    assert.ok(baseColumnWidth({ name: 'id', kind: 'uuid' }) >= 240)
  })
  test('long names widen the column', () => {
    const a = baseColumnWidth({ name: 'x', kind: 'string' })
    const b = baseColumnWidth({ name: 'a_very_long_column_name_here', kind: 'string' })
    assert.ok(b > a)
  })
  test('distributes slack to fill available width', () => {
    const cols = [
      { name: 'a', kind: 'string' },
      { name: 'b', kind: 'string' },
    ]
    const w = distributeColumnWidths(cols, {}, 1000, 80)
    const sum = w.a + w.b
    assert.equal(sum, 1000) // fills exactly
  })
  test('explicit widths are preserved; slack goes to flexible only', () => {
    const cols = [
      { name: 'a', kind: 'string' },
      { name: 'b', kind: 'string' },
    ]
    const w = distributeColumnWidths(cols, { a: 300 }, 1000, 80)
    assert.equal(w.a, 300) // untouched
    assert.equal(w.b, 700) // absorbs all slack
  })
  test('no shrink below content when available is small', () => {
    const cols = [{ name: 'a', kind: 'string' }]
    const w = distributeColumnWidths(cols, {}, 10, 80)
    assert.ok(w.a >= 80) // never below min, no negative slack applied
  })
  test('respects min width on explicit', () => {
    const cols = [{ name: 'a', kind: 'string' }]
    const w = distributeColumnWidths(cols, { a: 20 }, 0, 80)
    assert.equal(w.a, 80)
  })
})

// ---------------------------------------------------------------------------
describe('moveSelection', () => {
  test('arrows clamp at edges', () => {
    assert.deepEqual(moveSelection({ row: 0, col: 0 }, 'up', 3, 3), { row: 0, col: 0 })
    assert.deepEqual(moveSelection({ row: 0, col: 0 }, 'down', 3, 3), { row: 1, col: 0 })
    assert.deepEqual(moveSelection({ row: 2, col: 2 }, 'down', 3, 3), { row: 2, col: 2 })
    assert.deepEqual(moveSelection({ row: 1, col: 0 }, 'left', 3, 3), { row: 1, col: 0 })
    assert.deepEqual(moveSelection({ row: 1, col: 1 }, 'right', 3, 3), { row: 1, col: 2 })
  })
  test('tab wraps to next row at right edge', () => {
    assert.deepEqual(moveSelection({ row: 0, col: 2 }, 'tab', 3, 3), { row: 1, col: 0 })
    assert.deepEqual(moveSelection({ row: 0, col: 0 }, 'tab', 3, 3), { row: 0, col: 1 })
  })
  test('shiftTab wraps to prev row at left edge', () => {
    assert.deepEqual(moveSelection({ row: 1, col: 0 }, 'shiftTab', 3, 3), { row: 0, col: 2 })
    assert.deepEqual(moveSelection({ row: 1, col: 2 }, 'shiftTab', 3, 3), { row: 1, col: 1 })
  })
  test('empty grid returns input', () => {
    assert.deepEqual(moveSelection({ row: 0, col: 0 }, 'down', 0, 0), { row: 0, col: 0 })
  })
})

describe('copyCellText', () => {
  test('null → empty string', () => assert.equal(copyCellText(null, 'string'), ''))
  test('json object stringified compact', () => assert.equal(copyCellText({ a: 1 }, 'json'), '{"a":1}'))
  test('number → plain', () => assert.equal(copyCellText(1234.5, 'float'), '1234.5'))
  test('uuid passes through as string', () => assert.equal(copyCellText('abc-123', 'uuid'), 'abc-123'))
})
