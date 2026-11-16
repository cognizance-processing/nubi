/**
 * textColors.test.mjs — the per-role text-colour fallback chain.
 *
 * The rules that matter here are the ones a board silently depends on: an
 * unset role must mean "inherit the theme" (never black), a single authored
 * colour must still produce a readable hierarchy, and an authored colour that
 * lands unreadable on a card's own background must be corrected.
 *
 * Run: npm run test:dash
 */

import { test, describe } from 'node:test'
import assert from 'node:assert/strict'

import { resolveTextColors, softenColor, colorStyle } from './textColors.js'

describe('resolveTextColors — no authored colours', () => {
  test('every role is null so the theme decides', () => {
    const t = resolveTextColors(undefined)
    assert.deepEqual(t, { title: null, body: null, muted: null })
  })

  test('an empty style object behaves the same as none', () => {
    assert.deepEqual(resolveTextColors({}), { title: null, body: null, muted: null })
  })

  test('a non-string colour is ignored rather than stringified', () => {
    // A background descriptor object once leaked into `color`; painting
    // "[object Object]" as a colour silently produced black text.
    const t = resolveTextColors({ color: { type: 'solid' } })
    assert.equal(t.body, null)
  })
})

describe('resolveTextColors — one authored colour', () => {
  test('body drives heading, and secondary is a softened body', () => {
    const t = resolveTextColors({ color: '#112233' })
    assert.equal(t.body, '#112233')
    assert.equal(t.title, '#112233', 'heading inherits body when unset')
    assert.match(t.muted, /color-mix/, 'secondary is derived, not identical')
    assert.ok(t.muted.includes('#112233'))
  })

  test('the hierarchy survives — secondary is not the same value as body', () => {
    // The old single-colour behaviour flattened caption and value to one shade.
    const t = resolveTextColors({ color: '#112233' })
    assert.notEqual(t.muted, t.body)
  })
})

describe('resolveTextColors — explicit roles', () => {
  test('each role wins over the fallback chain', () => {
    const t = resolveTextColors({ titleColor: '#aa0000', color: '#00bb00', mutedColor: '#0000cc' })
    assert.equal(t.title, '#aa0000')
    assert.equal(t.body, '#00bb00')
    assert.equal(t.muted, '#0000cc')
  })

  test('a heading colour alone leaves body and secondary to the theme', () => {
    const t = resolveTextColors({ titleColor: '#aa0000' })
    assert.equal(t.title, '#aa0000')
    assert.equal(t.body, null)
    assert.equal(t.muted, null, 'nothing to soften and no tile background')
  })

  test('a secondary colour alone does not leak into the other roles', () => {
    const t = resolveTextColors({ mutedColor: '#0000cc' })
    assert.equal(t.muted, '#0000cc')
    assert.equal(t.title, null)
    assert.equal(t.body, null)
  })
})

describe('resolveTextColors — against a card background', () => {
  test('with no authored colours, secondary is derived from the tile', () => {
    // Historical behaviour for tiles that bake a background: theme muted is
    // unreadable on a pastel or dark tile, so ink is derived from the tile.
    const t = resolveTextColors({}, { background: '#101010' })
    assert.ok(t.muted, 'a tile with a background always gets a muted ink')
    assert.equal(t.body, null, 'body still inherits the theme')
  })

  test('an unreadable authored colour is corrected against the tile', () => {
    const t = resolveTextColors({ color: '#111111' }, { background: '#000000' })
    assert.notEqual(t.body, '#111111', 'near-black on black must not be kept')
  })

  test('a legible authored colour is passed through untouched', () => {
    const t = resolveTextColors({ color: '#ffffff' }, { background: '#000000' })
    assert.equal(t.body, '#ffffff')
  })
})

describe('softenColor / colorStyle', () => {
  test('softenColor handles any CSS colour form, including tokens', () => {
    assert.match(softenColor('var(--fg)'), /color-mix\(in srgb, var\(--fg\)/)
    assert.match(softenColor('rebeccapurple'), /rebeccapurple/)
  })

  test('softenColor returns null for a missing colour', () => {
    assert.equal(softenColor(undefined), null)
    assert.equal(softenColor(''), null)
    assert.equal(softenColor(42), null)
  })

  test('colorStyle omits the style object entirely when there is no colour', () => {
    // `style={{ color: undefined }}` on every text node would be noise; more
    // importantly a null must never be painted.
    assert.equal(colorStyle(null), undefined)
    assert.deepEqual(colorStyle('#fff'), { color: '#fff' })
  })
})
