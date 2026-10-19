/**
 * numberInput.test.ts — a typed 0 must survive; '' and junk clear the field.
 * Run: npm run test:dash
 */
import test from 'node:test'
import assert from 'node:assert/strict'
import { parseNumberInput } from './numberInput.js'

test('float fields keep 0, negatives and fractions', () => {
  assert.equal(parseNumberInput(''), undefined)
  assert.equal(parseNumberInput('0'), 0)
  assert.equal(parseNumberInput('12'), 12)
  assert.equal(parseNumberInput('-3'), -3)
  assert.equal(parseNumberInput('1.5'), 1.5)
  assert.equal(parseNumberInput('abc'), undefined)
})

test('integer fields truncate fractions and keep 0', () => {
  const int = { integer: true }
  assert.equal(parseNumberInput('', int), undefined)
  assert.equal(parseNumberInput('0', int), 0)
  assert.equal(parseNumberInput('12', int), 12)
  assert.equal(parseNumberInput('-3', int), -3)
  assert.equal(parseNumberInput('1.5', int), 1)
  assert.equal(parseNumberInput('abc', int), undefined)
})

test('min/max clamp a parsed value but never invent one', () => {
  const opts = { integer: true, min: 1, max: 40 }
  assert.equal(parseNumberInput('0', opts), 1)
  assert.equal(parseNumberInput('-3', opts), 1)
  assert.equal(parseNumberInput('12', opts), 12)
  assert.equal(parseNumberInput('99', opts), 40)
  assert.equal(parseNumberInput('', opts), undefined)
  assert.equal(parseNumberInput('abc', opts), undefined)
})
