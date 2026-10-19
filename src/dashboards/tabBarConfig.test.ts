/**
 * tabBarConfig.test.ts — the renderer must read the tab-bar style the editor
 * saves (`spec.tab_bar`), not only the legacy `tabBar`. Run: npm run test:dash
 */
import test from 'node:test'
import assert from 'node:assert/strict'
import { tabBarConfig } from './tabBarConfig.js'

test('reads the editor-written tab_bar', () => {
  assert.deepEqual(tabBarConfig({ tab_bar: { variant: 'pill', accent: '#f00' } }), { variant: 'pill', accent: '#f00' })
})

test('falls back to the legacy tabBar key', () => {
  assert.deepEqual(tabBarConfig({ tabBar: { variant: 'underline' } }), { variant: 'underline' })
})

test('tab_bar wins over tabBar; no spec is fine', () => {
  assert.equal(tabBarConfig({ tab_bar: { variant: 'pill' }, tabBar: { variant: 'underline' } }).variant, 'pill')
  assert.equal(tabBarConfig(null), undefined)
})
