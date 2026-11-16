import { test } from 'node:test'
import assert from 'node:assert/strict'
import { textWidgetLook } from './textStyle.js'

test('a plain text widget keeps its padding and scrolls long content', () => {
  const { className, style } = textWidgetLook({})
  assert.match(className, /overflow-y-auto/)
  assert.match(className, /py-3/)
  assert.deepEqual(style, {})
})

test('a heading has no vertical padding and never scrolls', () => {
  const { className } = textWidgetLook({ heading: true })
  assert.match(className, /overflow-hidden/)
  assert.doesNotMatch(className, /overflow-y-auto/)
  assert.doesNotMatch(className, /py-/)
})

test('size and weight apply to the block and reach markdown elements', () => {
  const { className, style } = textWidgetLook({ fontSize: 28, fontWeight: 700, align: 'right' })
  assert.equal(style.fontSize, 28)
  assert.equal(style.fontWeight, 700)
  assert.equal(style.textAlign, 'right')
  assert.match(className, /font-size:inherit/)
  assert.match(className, /font-weight:inherit/)
})

test('unset typography leaves markdown elements their own styles', () => {
  const { className } = textWidgetLook({ heading: true })
  assert.doesNotMatch(className, /inherit/)
})
