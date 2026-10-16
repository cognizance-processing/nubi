import { test } from 'node:test'
import assert from 'node:assert/strict'
import { cellText } from './format.js'

test('cellText never returns a non-string (the audit page crashed on summary: {})', () => {
  assert.equal(cellText({}), '')
  assert.equal(cellText(null), '')
  assert.equal(cellText(undefined), '')
  assert.equal(cellText('board.create'), 'board.create')
  assert.equal(cellText(42), '42')
  assert.equal(cellText({ name: 'Q3', empty: '', nothing: null }), 'name=Q3')
  assert.equal(cellText({ a: { b: 1 } }), 'a={"b":1}')
})

test('cellText truncates long values with an ellipsis', () => {
  const out = cellText('x'.repeat(300), 10)
  assert.equal(out.length, 10)
  assert.ok(out.endsWith('…'))
})
