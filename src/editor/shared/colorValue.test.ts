/**
 * colorValue.test.mjs — swatch coercion behind ColorSwatch/ColorField.
 *
 * `<input type="color">` displays only #rrggbb, but resolves any colour its CSS
 * parser accepts on its own. These tests pin the two halves of that:
 *   toSwatchHex      — the DOM-free forms (hex / rgb)
 *   resolveSwatchHex — the full rule, including what must NOT reach the input
 *                      (values that would silently paint the chip black)
 *
 * Named colours ('rebeccapurple') and hsl() are resolved by the browser's CSS
 * parser at runtime; under node there is no DOM, so resolveSwatchHex correctly
 * falls back here. The browser path is covered by the Playwright drive.
 */

import test from 'node:test'
import assert from 'node:assert/strict'
import { toSwatchHex, rgbToHex, resolveSwatchHex, UNRESOLVABLE } from './colorValue.js'

// ── toSwatchHex: the pure forms ────────────────────────────────────────────

test('toSwatchHex passes 6-digit hex through, lowercased', () => {
  assert.equal(toSwatchHex('#161b22'), '#161b22')
  assert.equal(toSwatchHex('#161B22'), '#161b22')
  assert.equal(toSwatchHex('  #161B22  '), '#161b22')
})

test('toSwatchHex expands 3-digit hex to the long form', () => {
  assert.equal(toSwatchHex('#abc'), '#aabbcc')
  assert.equal(toSwatchHex('#ABC'), '#aabbcc')
})

test('toSwatchHex understands computed rgb()/rgba() strings', () => {
  assert.equal(toSwatchHex('rgb(22, 27, 34)'), '#161b22')
  assert.equal(toSwatchHex('rgba(22, 27, 34, 0.5)'), '#161b22', 'alpha is dropped')
  assert.equal(toSwatchHex('rgb(102 51 153)'), '#663399', 'space-separated syntax')
})

test('toSwatchHex returns null when it cannot resolve without a CSS parser', () => {
  for (const v of ['rebeccapurple', 'hsl(0 100% 50%)', 'inherit', 'var(--fg)', '#12345', 'nope', '', null, undefined]) {
    assert.equal(toSwatchHex(v), null, `for ${JSON.stringify(v)}`)
  }
})

// ── rgbToHex ───────────────────────────────────────────────────────────────

test('rgbToHex clamps and rounds out-of-range channels', () => {
  assert.equal(rgbToHex('rgb(-5, 300, 12.6)'), '#00ff0d')
})

test('rgbToHex rejects non-rgb strings', () => {
  assert.equal(rgbToHex('#161b22'), null)
  assert.equal(rgbToHex('rebeccapurple'), null)
})

// ── resolveSwatchHex: the rule that protects the chip ──────────────────────

test('resolveSwatchHex prefers the pure forms', () => {
  assert.equal(resolveSwatchHex('#abc', '#000000'), '#aabbcc')
  assert.equal(resolveSwatchHex('rgb(22,27,34)', '#000000'), '#161b22')
})

test('resolveSwatchHex falls back for values that would paint the chip black', () => {
  // The regression this whole module exists to prevent: these are exactly what
  // "inherit (theme default)" fields hold.
  for (const v of ['inherit', 'initial', 'unset', 'transparent', 'currentColor', '']) {
    assert.equal(resolveSwatchHex(v, '#0b0f1a'), '#0b0f1a', `for ${JSON.stringify(v)}`)
  }
})

test('resolveSwatchHex falls back for junk and half-typed hex', () => {
  for (const v of ['not-a-color', '#1', '#12345', 'var(--fg)', null, undefined, 42]) {
    assert.equal(resolveSwatchHex(v, '#6366f1'), '#6366f1', `for ${JSON.stringify(v)}`)
  }
})

test('resolveSwatchHex uses the default fallback when none is given', () => {
  assert.equal(resolveSwatchHex('inherit'), '#6366f1')
})

test('UNRESOLVABLE covers the CSS-wide keywords CSS.supports() would wave through', () => {
  // CSS.supports('color', 'inherit') is true, so the keyword list is the only
  // thing standing between those values and a black chip.
  for (const k of ['inherit', 'initial', 'unset', 'revert', 'currentcolor', 'transparent']) {
    assert.ok(UNRESOLVABLE.has(k), `${k} must be listed`)
  }
})
