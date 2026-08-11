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

// ---------------------------------------------------------------------------
// Theme adaptation (ctx param) — see src/lib/themeColor.js for the policy.
// ---------------------------------------------------------------------------

test('styleToCss/backgroundToCss: omitting ctx is a byte-identical no-op', () => {
  // The exact case that used to freeze white converter-default cards: with no
  // ctx, a light-neutral background must render EXACTLY as before.
  assert.deepEqual(styleToCss({ background: '#FFFFFF', color: '#000000' }), {
    background: '#FFFFFF', color: '#000000',
  })
  assert.deepEqual(backgroundToCss({ type: 'solid', color: '#ffffff' }), { background: '#ffffff' })
})

test('styleToCss: with ctx, a light-neutral background becomes the surface token', () => {
  const out = styleToCss({ background: '#FFFFFF', color: '#000000' }, { theme: 'dark' })
  assert.equal(out.background, 'var(--surface)')
  // Author's black text would be unreadable on the dark surface it now renders on.
  assert.notEqual(out.color, '#000000')
})

test('styleToCss: with ctx, same-theme viewing is visually a no-op (resolves to the same hex)', () => {
  const out = styleToCss({ background: '#ffffff', color: '#0e1729' }, { theme: 'light' })
  assert.equal(out.background, 'var(--surface)') // == #ffffff in light mode
  assert.equal(out.color, '#0e1729') // already readable, untouched
})

test('styleToCss: with ctx, a deliberately dark tile is left untouched', () => {
  const out = styleToCss({ background: '#1a1a2e', color: '#ffffff' }, { theme: 'light' })
  assert.equal(out.background, '#1a1a2e')
  assert.equal(out.color, '#ffffff')
})

test('styleToCss: with ctx, a chromatic brand background is left untouched', () => {
  const out = styleToCss({ background: '#7AC79B', color: '#000000' }, { theme: 'dark' })
  assert.equal(out.background, '#7AC79B')
})

test('backgroundToCss: with ctx, solid light-neutral becomes the surface token', () => {
  assert.deepEqual(
    backgroundToCss({ type: 'solid', color: '#FAFAFA' }, { theme: 'dark' }),
    { background: 'var(--surface)' },
  )
})
