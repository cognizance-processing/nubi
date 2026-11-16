import { test } from 'node:test'
import assert from 'node:assert/strict'
import {
  backgroundHex,
  boardColorMode,
  compositeHex,
  readableOn,
  safeThemeValue,
  themeProp,
  withDerivedTokens,
} from './boardTheme.js'
import { styleToCss, backgroundToCss } from '../dashboards/widgetHtml.js'
import { contrastRatio as rawRatio } from './contrast.js'

// Readable inks are rgba(); measure what the reader actually sees.
const contrastRatio = (fg: unknown, bg: string) => rawRatio(compositeHex(fg, bg), bg)

// ── Mode detection ──────────────────────────────────────────────────────────

test('a dark board background calls for dark mode, a light one for light', () => {
  assert.equal(boardColorMode({ background: { type: 'solid', color: '#14143A' } }), 'dark')
  assert.equal(boardColorMode({ background: { type: 'solid', color: '#F0EEE6' } }), 'light')
})

test('a CSS background is read from background-color, then --bg', () => {
  const css = '--bg:#0E0B09; color:#FFF7EE; background-color:#0E0B09; background-image: url(x.jpg)'
  assert.equal(boardColorMode({ background: { type: 'css', css } }), 'dark')
  assert.equal(boardColorMode({ background: { type: 'css', css: '--bg:#FAF9F5;' } }), 'light')
})

test('var(--bg) resolves only when the same CSS defines it', () => {
  assert.equal(
    boardColorMode({ background: { type: 'css', css: '--bg:#101010; background-color: var(--bg)' } }),
    'dark',
  )
  // The app's own token: the board follows the app theme.
  assert.equal(boardColorMode({ background: { type: 'css', css: 'background-color: var(--bg)' } }), null)
})

test('no background, or one that cannot be measured, follows the app', () => {
  assert.equal(boardColorMode({}), null)
  assert.equal(boardColorMode({ background: { type: 'image', imageUrl: 'https://x/y.jpg' } }), null)
  assert.equal(boardColorMode({ background: { type: 'transparent' } }), null)
})

test('an explicit spec.colorMode wins over the background', () => {
  assert.equal(boardColorMode({ colorMode: 'light', background: { type: 'solid', color: '#000000' } }), 'light')
})

test('a gradient is judged by its average', () => {
  assert.equal(backgroundHex({ type: 'gradient', from: '#000000', to: '#202020' }), '#101010')
})

test('translucent colors are composited over what is behind them', () => {
  assert.equal(compositeHex('rgba(255,255,255,0.5)', '#000000'), '#808080')
  assert.equal(compositeHex('rgba(0,0,0,0)', '#123456'), '#123456')
  assert.equal(compositeHex('#abcdef', '#000000'), '#abcdef')
})

// ── Palette pass-through ────────────────────────────────────────────────────

test('only theme custom properties are accepted, with --muted as an alias', () => {
  assert.equal(themeProp('--surface'), '--surface')
  assert.equal(themeProp('--muted'), '--text-muted')
  assert.equal(themeProp('--z-modal'), null)
  assert.equal(themeProp('--anything'), null)
})

test('custom property values cannot smuggle urls or extra declarations', () => {
  assert.equal(safeThemeValue('#25275A'), '#25275A')
  assert.equal(safeThemeValue('url(https://evil/x)'), null)
  assert.equal(safeThemeValue('red; background: url(x)'), null)
  assert.equal(safeThemeValue('expression(alert(1))'), null)
})

test('missing surface shades are derived from the author surface and text', () => {
  const out = withDerivedTokens({ '--surface': '#25275A' })
  assert.match(out['--surface-2'], /color-mix/)
  assert.match(out['--surface-3'], /color-mix/)
  // An author-set shade is kept.
  assert.equal(withDerivedTokens({ '--surface': '#fff', '--surface-2': '#eee' })['--surface-2'], '#eee')
})

test('a widget style carries its palette to the card (previously dropped)', () => {
  const css = styleToCss({ background: '#25275A', color: '#F4F5FF', '--surface': '#25275A', '--text': '#F4F5FF', '--muted': '#A9ABD6', '--z-modal': '1' })
  assert.equal(css?.['--surface'], '#25275A')
  assert.equal(css?.['--text'], '#F4F5FF')
  assert.equal(css?.['--text-muted'], '#A9ABD6')
  assert.ok(css?.['--surface-2'])
  assert.equal(css?.['--z-modal'], undefined)
})

test('a CSS-string board background keeps its custom properties intact', () => {
  const out = backgroundToCss({ type: 'css', css: '--surface:#25275A; --text:#F4F5FF; background-color:#14143A' })
  assert.equal(out?.['--surface'], '#25275A')
  assert.equal(out?.['--text'], '#F4F5FF')
  assert.equal(out?.backgroundColor, '#14143A')
  // Not mangled into a camel-cased non-property.
  assert.equal(out?.['-Surface'], undefined)
})

// ── Contrast guard ──────────────────────────────────────────────────────────

test('text that would vanish against the background is replaced by readable ink', () => {
  const fixed = readableOn('#1b1b1b', '#14143A') as string
  assert.ok((contrastRatio(fixed, '#14143A') ?? 0) >= 3)
  assert.equal(readableOn('#F4F5FF', '#14143A'), '#F4F5FF')
})

test('a transparent card is checked against the board behind it', () => {
  // Dark ink on a transparent card over a dark board — unreadable as authored.
  const css = styleToCss({ background: 'transparent', color: '#222222' }, { theme: 'dark', bgHex: '#0E0B09' })
  assert.ok((contrastRatio(css?.color, '#0E0B09') ?? 0) >= 3)
})

test('a see-through card is checked against the composited color', () => {
  const css = styleToCss({ background: 'rgba(255,255,255,0.08)', color: '#111111' }, { theme: 'dark', bgHex: '#000000' })
  const seen = compositeHex('rgba(255,255,255,0.08)', '#000000')!
  assert.ok((contrastRatio(css?.color, seen) ?? 0) >= 3)
})

test('readable author colors are left exactly as authored', () => {
  const css = styleToCss({ background: 'transparent', color: '#FFF7EE' }, { theme: 'dark', bgHex: '#0E0B09' })
  assert.equal(css?.color, '#FFF7EE')
})
