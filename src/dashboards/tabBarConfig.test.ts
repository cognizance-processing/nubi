/**
 * tabBarConfig.test.ts — the renderer must read the tab-bar style the editor
 * saves (`spec.tab_bar`), not only the legacy `tabBar`. Run: npm run test:dash
 */
import test from 'node:test'
import assert from 'node:assert/strict'
import { tabBarConfig, resolveTabBar, tabCustomStyle } from './tabBarConfig.js'

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

test('resolveTabBar: defaults for a missing or empty config', () => {
  for (const raw of [undefined, null, {}, 'junk']) {
    assert.deepEqual(resolveTabBar(raw), { variant: 'underline', accent: null, align: 'start', size: 'md', style: undefined })
  }
})

test('resolveTabBar: keeps the align/size values the editor offers', () => {
  for (const align of ['start', 'center', 'end', 'stretch']) assert.equal(resolveTabBar({ align }).align, align)
  for (const size of ['sm', 'md', 'lg']) assert.equal(resolveTabBar({ size }).size, size)
  const r = resolveTabBar(tabBarConfig({ tab_bar: { variant: 'pills', accent: '#f00', align: 'center', size: 'lg' } }))
  assert.equal(r.variant, 'pills')
  assert.equal(r.accent, '#f00')
  assert.equal(r.align, 'center')
  assert.equal(r.size, 'lg')
})

test('resolveTabBar: unknown align/size fall back to the defaults', () => {
  const r = resolveTabBar({ align: 'justify', size: 'xl' })
  assert.equal(r.align, 'start')
  assert.equal(r.size, 'md')
})

test('resolveTabBar: custom_css becomes a whitelisted inline style', () => {
  const r = resolveTabBar({ custom_css: 'border-radius: 10px; gap: 6px; position: fixed; background: url(javascript:alert(1))' })
  assert.deepEqual(r.style, { borderRadius: '10px', gap: '6px' })
  assert.deepEqual(resolveTabBar({ customCss: 'padding: 4px' }).style, { padding: '4px' })
  assert.equal(resolveTabBar({ custom_css: '   ' }).style, undefined)
  assert.equal(resolveTabBar({ custom_css: 'position: fixed; z-index: 9999' }).style, undefined)
})

test('tabCustomStyle: per-tab custom_css is sanitised the same way', () => {
  assert.deepEqual(tabCustomStyle({ style: { custom_css: 'font-weight: 600; top: 0' } }), { fontWeight: '600' })
  assert.equal(tabCustomStyle({ style: {} }), undefined)
  assert.equal(tabCustomStyle(null), undefined)
})
