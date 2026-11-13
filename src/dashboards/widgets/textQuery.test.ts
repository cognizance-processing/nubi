import { test } from 'node:test'
import assert from 'node:assert/strict'
import { textQueryRow, textQueryValue } from './textQuery.js'

test('textQueryRow: default, negative, non-integer → 0', () => {
  assert.equal(textQueryRow(undefined), 0)
  assert.equal(textQueryRow(-3), 0)
  assert.equal(textQueryRow(1.5), 0)
  assert.equal(textQueryRow('2'), 0)
  assert.equal(textQueryRow(4), 4)
})

test('textQueryValue: wraps the cell in prefix/suffix', () => {
  assert.equal(textQueryValue('Acme', 'Client: ', ' Ltd'), 'Client: Acme Ltd')
  assert.equal(textQueryValue(42, '', '%'), '42%')
  assert.equal(textQueryValue('  x  '), 'x')
})

test('textQueryValue: empty cell yields no affixes', () => {
  assert.equal(textQueryValue(null, 'a', 'b'), '')
  assert.equal(textQueryValue(undefined, 'a', 'b'), '')
  assert.equal(textQueryValue('   ', 'a', 'b'), '')
})

test('textQueryValue: non-string affixes are ignored', () => {
  assert.equal(textQueryValue('v', 5, null), 'v')
})
