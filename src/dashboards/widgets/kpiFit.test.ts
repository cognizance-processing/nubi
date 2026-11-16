import { test } from 'node:test'
import assert from 'node:assert/strict'
import { fitValueFontSize } from './kpiFit.js'

test('the authored size is the most the value can reach', () => {
  assert.match(fitValueFontSize('1,234', 28), /min\(28px,/)
})

test('a longer value needs more width per unit of font size', () => {
  const divisor = (text: string) => Number(/100cqw \/ ([\d.]+)/.exec(fitValueFontSize(text, 28))?.[1])
  assert.ok(divisor('708,007,293') > divisor('627'))
})

test('punctuation counts as narrower than digits', () => {
  assert.match(fitValueFontSize('1,234', 28), /100cqw \/ 2\.70/)
})

test('a value never shrinks below a readable floor', () => {
  assert.match(fitValueFontSize('1,234,567,890,123,456', 28), /^max\(12px,/)
})

test('an empty value still gives valid CSS', () => {
  assert.match(fitValueFontSize('', 28), /100cqw \/ 0\.60/)
})
