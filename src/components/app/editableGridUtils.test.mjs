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
} from './editableGridUtils.js'

// ---------------------------------------------------------------------------
describe('classifyType', () => {
  test('numbers', () => {
    for (const t of ['INTEGER', 'BIGINT', 'int4', 'DECIMAL(10,2)', 'double', 'real', 'HUGEINT', 'serial'])
      assert.equal(classifyType(t), 'number')
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
