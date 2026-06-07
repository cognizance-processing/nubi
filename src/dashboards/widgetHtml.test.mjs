import { test } from 'node:test'
import assert from 'node:assert/strict'
import { backgroundToCss, styleToCss } from '../../src/dashboards/widgetHtml.js'

test('backgroundToCss: transparent type → background:transparent', () => {
  assert.deepEqual(backgroundToCss({ type: 'transparent' }), { background: 'transparent' })
})

test('backgroundToCss: existing types unchanged (regression)', () => {
  assert.deepEqual(backgroundToCss({ type: 'solid', color: '#fff' }), { background: '#fff' })
  assert.equal(backgroundToCss(undefined), undefined)
  assert.equal(backgroundToCss({ type: 'none' }), undefined)
})

test('styleToCss: transparent background descriptor flows through', () => {
  assert.deepEqual(
    styleToCss({ background: { type: 'transparent' } }),
    { background: 'transparent' },
  )
})

test('styleToCss: string background still works (regression)', () => {
  assert.deepEqual(styleToCss({ background: '#123456' }), { background: '#123456' })
})
