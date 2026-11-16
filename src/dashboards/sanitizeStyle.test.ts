// HTML widgets are authored content shown to other people. Their inline styles
// may not take the element out of its card (to cover the app's own UI), and
// widget elements may not be pointed at another backend or handed a token.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { sanitizeInlineStyle, WIDGET_ATTRS, FORBIDDEN_WIDGET_ATTRS } from './sanitizeStyle.js'

test('positioning and stacking declarations are removed', () => {
  assert.equal(sanitizeInlineStyle('color: red; position: fixed; top: 0; z-index: 99999'), 'color: red; top: 0')
  assert.equal(sanitizeInlineStyle('POSITION : absolute ;inset:0;width:100%'), 'inset:0; width:100%')
  assert.equal(sanitizeInlineStyle('z-index:2147483647'), '')
  assert.equal(sanitizeInlineStyle('position:/**/fixed'), '')
})

test('declarations that load remote content or run code are removed', () => {
  assert.equal(sanitizeInlineStyle('background:url(https://evil.example/x.png); color:blue'), 'color:blue')
  assert.equal(sanitizeInlineStyle('width: expression(alert(1))'), '')
  assert.equal(sanitizeInlineStyle('behavior: url(x.htc)'), '')
  assert.equal(sanitizeInlineStyle('background:u\\72l(https://evil.example)'), '')
})

test('ordinary styling is untouched', () => {
  const style = 'display:flex; gap: 8px; padding: 12px 16px; font-weight: 600; color: #1e293b'
  assert.equal(sanitizeInlineStyle(style), 'display:flex; gap: 8px; padding: 12px 16px; font-weight: 600; color: #1e293b')
  assert.equal(sanitizeInlineStyle(''), '')
  assert.equal(sanitizeInlineStyle(undefined as any), '')
})

test('credential and backend attributes are not allowed on widget elements', () => {
  for (const attr of ['backend', 'token', 'get-token']) {
    assert.ok(!WIDGET_ATTRS.includes(attr), attr)
    assert.ok(FORBIDDEN_WIDGET_ATTRS.includes(attr), attr)
  }
  assert.ok(WIDGET_ATTRS.includes('query-id'))
})
