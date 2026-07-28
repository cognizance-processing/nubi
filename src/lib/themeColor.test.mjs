import test from 'node:test'
import assert from 'node:assert/strict'
import {
  isLightNeutral,
  adaptBackgroundColor,
  resolveEffectiveBgHex,
} from './themeColor.js'

test('isLightNeutral: near-white/off-white converter defaults are neutral', () => {
  assert.equal(isLightNeutral('#FFFFFF'), true)
  assert.equal(isLightNeutral('#ffffff'), true)
  assert.equal(isLightNeutral('#FAFAFA'), true)
  assert.equal(isLightNeutral('#f5f5f5'), true)
})

test('isLightNeutral: dark and chromatic colors are NOT neutral', () => {
  assert.equal(isLightNeutral('#000000'), false)
  assert.equal(isLightNeutral('#1a1a2e'), false)   // deliberate dark hero tile
  assert.equal(isLightNeutral('#7AC79B'), false)    // brand green
  assert.equal(isLightNeutral('#2456a6'), false)    // brand blue
})

test('isLightNeutral: unparseable / missing input is false, not a throw', () => {
  assert.equal(isLightNeutral(undefined), false)
  assert.equal(isLightNeutral(null), false)
  assert.equal(isLightNeutral('var(--surface)'), false)
  assert.equal(isLightNeutral('transparent'), false)
})

test('adaptBackgroundColor: light-neutral becomes the surface token', () => {
  const light = adaptBackgroundColor('#FFFFFF', 'light')
  assert.equal(light.css, 'var(--surface)')
  assert.equal(light.hex, '#ffffff')

  const dark = adaptBackgroundColor('#FFFFFF', 'dark')
  assert.equal(dark.css, 'var(--surface)')
  assert.equal(dark.hex, '#111a2e')
})

test('adaptBackgroundColor: chromatic/dark colors pass through unchanged (identity)', () => {
  const result = adaptBackgroundColor('#7AC79B', 'dark')
  assert.equal(result.css, '#7AC79B')
  assert.equal(result.hex, '#7AC79B')
})

test('adaptBackgroundColor: same theme as authored is visually a no-op', () => {
  // A light-neutral card viewed in light mode renders var(--surface), which
  // resolves to the exact same #ffffff it already was — no visible change.
  const { hex } = adaptBackgroundColor('#ffffff', 'light')
  assert.equal(hex, '#ffffff')
})

test('adaptBackgroundColor: non-color / empty input passes through with null hex', () => {
  assert.deepEqual(adaptBackgroundColor(undefined), { css: undefined, hex: null })
  assert.deepEqual(adaptBackgroundColor(''), { css: '', hex: null })
  const gradient = adaptBackgroundColor('linear-gradient(90deg, #fff, #000)')
  assert.equal(gradient.css, 'linear-gradient(90deg, #fff, #000)')
  assert.equal(gradient.hex, null)
})

test('resolveEffectiveBgHex: resolves the surface token per theme', () => {
  assert.equal(resolveEffectiveBgHex({ background: 'var(--surface)' }, 'light'), '#ffffff')
  assert.equal(resolveEffectiveBgHex({ background: 'var(--surface)' }, 'dark'), '#111a2e')
})

test('resolveEffectiveBgHex: prefers backgroundColor over background, falls back to plain hex', () => {
  assert.equal(resolveEffectiveBgHex({ background: '#111111', backgroundColor: '#7AC79B' }), '#7AC79B')
  assert.equal(resolveEffectiveBgHex({ background: '#7AC79B' }), '#7AC79B')
})

test('resolveEffectiveBgHex: null for gradients/unresolvable values', () => {
  assert.equal(resolveEffectiveBgHex({ background: 'linear-gradient(90deg, #fff, #000)' }), null)
  assert.equal(resolveEffectiveBgHex({}), null)
})
