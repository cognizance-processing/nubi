/**
 * boardTheme.ts — a board (or a widget card) decides its own light/dark mode.
 *
 * A board is designed against one background. If its internals took their
 * colors from the viewer's app theme instead, a board designed dark would get
 * light table stripes, headers and inputs the moment the viewer switched the
 * app to light — the author's light text then vanishes into them. So any
 * surface with a resolvable background of its own renders in the mode that
 * background calls for, and the app theme only applies where the author left
 * the background to the theme.
 *
 * This module also lets an author's palette reach the widget internals: a
 * fixed set of theme custom properties (`--surface`, `--text`, …) passes
 * through style objects and CSS strings, and the shades an author rarely sets
 * (`--surface-2`, `--surface-3`) are derived from the ones they did.
 *
 * Pure: no React, no DOM.
 */

import { isDarkBackground, parseHex, readableInk, contrastRatio } from './contrast.js'

export type ColorMode = 'light' | 'dark'

/** Mirrors `--bg` in src/index.css — the page behind a board with no background. */
export const PAGE_BG_HEX: Record<ColorMode, string> = { light: '#f6f8fb', dark: '#0a1020' }

/**
 * Theme custom properties an author may set on a board or widget. Anything
 * else stays out — z-indexes, radii and shadows are the app's business.
 * `--muted` is accepted as an alias of `--text-muted`.
 */
const THEME_PROPS = new Set([
  '--bg', '--surface', '--surface-2', '--surface-3', '--text', '--text-muted', '--text-subtle',
  '--border', '--primary', '--primary-fg', '--accent', '--ring',
])
const ALIASES: Record<string, string> = { '--muted': '--text-muted' }

/** The canonical theme property for *name*, or null when it isn't one. */
export function themeProp(name: string): string | null {
  const key = name.trim().toLowerCase()
  const canonical = ALIASES[key] ?? key
  return THEME_PROPS.has(canonical) ? canonical : null
}

/** A custom-property value is plain data: no urls, no script, no nesting tricks. */
export function safeThemeValue(value: unknown): string | null {
  if (typeof value !== 'string') return null
  const v = value.trim()
  if (!v || v.length > 200) return null
  if (/url\(|expression\(|javascript:|[;{}<>]/i.test(v)) return null
  return v
}

/**
 * Fill in the surface shades an author didn't set, from the ones they did.
 * Tables stripe rows and shade headers with `--surface-2`; an author who only
 * picked a card color would otherwise get the app's own stripe color inside it.
 * Mixing toward the text color works on any surface, including a transparent
 * one (the stripe becomes a faint tint of the ink).
 */
export function withDerivedTokens(out: Record<string, any>): Record<string, any> {
  if (out['--surface'] && !out['--surface-2']) {
    out['--surface-2'] = 'color-mix(in srgb, var(--text) 7%, var(--surface))'
  }
  if (out['--surface'] && !out['--surface-3']) {
    out['--surface-3'] = 'color-mix(in srgb, var(--text) 12%, var(--surface))'
  }
  return out
}

/** Parse `#rgb`, `#rrggbb`, `rgb()` or `rgba()` into channels plus alpha. */
export function parseColor(input: unknown): { r: number; g: number; b: number; a: number } | null {
  if (typeof input !== 'string') return null
  const hex = parseHex(input)
  if (hex) return { ...hex, a: 1 }
  const m = input.trim().match(/^rgba?\(\s*([\d.]+)[,\s]+([\d.]+)[,\s]+([\d.]+)(?:\s*[,/]\s*([\d.]+%?))?\s*\)$/i)
  if (!m) return null
  let a = m[4] == null ? 1 : parseFloat(m[4])
  if (m[4]?.endsWith('%')) a /= 100
  return { r: Number(m[1]), g: Number(m[2]), b: Number(m[3]), a: Math.max(0, Math.min(1, a)) }
}

function toHex({ r, g, b }: { r: number; g: number; b: number }): string {
  const h = (v: number) => Math.round(Math.max(0, Math.min(255, v))).toString(16).padStart(2, '0')
  return `#${h(r)}${h(g)}${h(b)}`
}

/**
 * The opaque color a reader actually sees for *color* laid over *under*.
 * A fully transparent color is just what's underneath; an opaque one ignores it.
 */
export function compositeHex(color: unknown, under: string | null): string | null {
  const c = parseColor(color)
  if (!c) return null
  if (c.a >= 1) return toHex(c)
  const u = under ? parseColor(under) : null
  if (!u) return c.a === 0 ? null : toHex(c)
  return toHex({
    r: c.r * c.a + u.r * (1 - c.a),
    g: c.g * c.a + u.g * (1 - c.a),
    b: c.b * c.a + u.b * (1 - c.a),
  })
}

/** Pull a property's value out of a `prop: value; …` CSS string. */
function cssDecl(css: string, prop: string): string | null {
  for (const decl of css.split(';')) {
    const i = decl.indexOf(':')
    if (i !== -1 && decl.slice(0, i).trim().toLowerCase() === prop) return decl.slice(i + 1).trim()
  }
  return null
}

/**
 * The dominant opaque color of a background descriptor (or a plain color
 * string), or null when it can't be known — an image with no base color, a
 * keyword, a theme variable.
 */
export function backgroundHex(bg: unknown, under: string | null = null): string | null {
  if (typeof bg === 'string') return compositeHex(bg, under)
  if (!bg || typeof bg !== 'object') return null
  const d = bg as Record<string, any>
  switch (d.type) {
    case 'solid':
      return compositeHex(d.color, under)
    case 'gradient': {
      const a = compositeHex(d.from, under)
      const b = compositeHex(d.to, under)
      if (!a || !b) return a ?? b
      const pa = parseHex(a)!
      const pb = parseHex(b)!
      return toHex({ r: (pa.r + pb.r) / 2, g: (pa.g + pb.g) / 2, b: (pa.b + pb.b) / 2 })
    }
    case 'css': {
      if (typeof d.css !== 'string') return null
      let color = cssDecl(d.css, 'background-color') ?? cssDecl(d.css, 'background') ?? cssDecl(d.css, '--bg')
      // `var(--bg)` resolves only when the same CSS defines `--bg`; otherwise
      // it is the app's own token, i.e. the board follows the app theme.
      const ref = color?.match(/^var\(\s*(--[\w-]+)\s*\)$/)
      if (ref) color = cssDecl(d.css, ref[1].toLowerCase())
      return color ? compositeHex(color, under) : null
    }
    default:
      return null
  }
}

/** The mode a background calls for, or null when it can't be told. */
export function modeForHex(hex: string | null): ColorMode | null {
  const dark = isDarkBackground(hex)
  return dark === null ? null : dark ? 'dark' : 'light'
}

/**
 * The mode a whole board renders in: `spec.colorMode` when the author pinned
 * one, else whatever the board background calls for, else null (follow the
 * viewer's app theme — the board left its background to the theme).
 */
export function boardColorMode(spec: Record<string, any> | null | undefined): ColorMode | null {
  if (!spec) return null
  if (spec.colorMode === 'light' || spec.colorMode === 'dark') return spec.colorMode
  return modeForHex(backgroundHex(spec.background))
}

/**
 * Keep *color* if it reads on *bgHex* (3:1, the same bar contrast.ts applies);
 * otherwise swap in readable ink for that background. Colors that can't be
 * measured (gradients, variables) are left to the author.
 */
export function readableOn(color: unknown, bgHex: string | null, minRatio = 3): unknown {
  if (!bgHex || typeof color !== 'string') return color
  const fg = compositeHex(color, bgHex)
  if (!fg) return color
  const ratio = contrastRatio(fg, bgHex)
  if (ratio != null && ratio >= minRatio) return color
  return readableInk(bgHex) ?? color
}
