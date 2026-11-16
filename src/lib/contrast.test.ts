import test from 'node:test'
import assert from 'node:assert/strict'
import {
  parseHex,
  relativeLuminance,
  contrastRatio,
  isDarkBackground,
  readableInk,
  ensureReadable,
  INK_ON_DARK,
  INK_ON_LIGHT,
  MUTED_INK_ON_LIGHT,
} from './contrast.js'

test('parseHex accepts 6-digit, 3-digit, and bare forms', () => {
  assert.deepEqual(parseHex('#4dc7d7'), { r: 0x4d, g: 0xc7, b: 0xd7 })
  assert.deepEqual(parseHex('4dc7d7'), { r: 0x4d, g: 0xc7, b: 0xd7 })
  // #8bc — the 3-digit shorthand real converted boards carry
  assert.deepEqual(parseHex('#8bc'), { r: 0x88, g: 0xbb, b: 0xcc })
})

test('parseHex rejects non-hex values', () => {
  for (const v of [null, undefined, 42, '', 'red', 'var(--x)', 'rgba(0,0,0,1)', '#12345']) {
    assert.equal(parseHex(v), null, String(v))
  }
})

test('relativeLuminance spans black to white', () => {
  assert.equal(relativeLuminance({ r: 0, g: 0, b: 0 }), 0)
  assert.equal(relativeLuminance({ r: 255, g: 255, b: 255 }), 1)
})

test('contrastRatio matches known WCAG endpoints', () => {
  assert.equal(Math.round(contrastRatio('#000000', '#ffffff')), 21)
  assert.equal(contrastRatio('#ffffff', '#ffffff'), 1)
  assert.equal(contrastRatio('#ffffff', 'not-a-color'), null)
})

test('isDarkBackground classifies tiles', () => {
  assert.equal(isDarkBackground('#191919'), true)
  assert.equal(isDarkBackground('#ffffff'), false)
  assert.equal(isDarkBackground('#fbf55c'), false) // legacy yellow adherence tile
  assert.equal(isDarkBackground('nope'), null)
})

test('readableInk picks ink by background', () => {
  assert.equal(readableInk('#191919'), INK_ON_DARK)
  assert.equal(readableInk('#ffffff'), INK_ON_LIGHT)
  assert.equal(readableInk('#ffffff', true), MUTED_INK_ON_LIGHT)
  assert.equal(readableInk('gradient(...)'), undefined)
})

test('ensureReadable keeps a legible author color', () => {
  // #666 on the pale legacy tiles is fine — must not be touched
  assert.equal(ensureReadable('#666666', '#fbf55c'), '#666666')
  assert.equal(ensureReadable('#666666', '#c5e4fe'), '#666666')
})

test('ensureReadable replaces an illegible author color', () => {
  // the converter default #666 on the darker teal tiles is ~2.5:1
  const ratio = contrastRatio('#666666', '#4dc7d7')
  assert.ok(ratio < 3, `expected <3, got ${ratio}`)
  assert.equal(ensureReadable('#666666', '#4dc7d7'), INK_ON_LIGHT)
  assert.equal(ensureReadable('#666666', '#8bc'), INK_ON_LIGHT)
})

test('ensureReadable is inert without an explicit background', () => {
  assert.equal(ensureReadable('#666666', undefined), '#666666')
  assert.equal(ensureReadable(undefined, undefined), undefined)
  assert.equal(ensureReadable('#666666', 'var(--surface)'), '#666666')
})

test('ensureReadable supplies ink when no color is given', () => {
  assert.equal(ensureReadable(undefined, '#191919'), INK_ON_DARK)
  assert.equal(ensureReadable(undefined, '#ffffff'), INK_ON_LIGHT)
})

test('minRatio is configurable', () => {
  // a pairing that clears 3:1 but not 7:1
  const c = '#666666', bg = '#fbf55c'
  assert.equal(ensureReadable(c, bg, { minRatio: 3 }), c)
  assert.equal(ensureReadable(c, bg, { minRatio: 7 }), INK_ON_LIGHT)
})
