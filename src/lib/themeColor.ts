/**
 * themeColor.js — adapts widget chrome colors so they respond to the app's
 * light/dark theme toggle instead of staying frozen at whatever hex a board
 * (or the legacy-dashboard converter) baked in.
 *
 * Scope is deliberately narrow: only near-white/near-gray "neutral" colors —
 * the shape a converter emits when no real design intent was expressed ("no
 * background set" defaults to white) — are swapped for the app's own
 * `--surface` token, so they render correctly in both themes. Anything with
 * real hue (a brand color, an intentionally dark KPI hero tile) is left
 * exactly as authored — those are deliberate design choices, not something
 * that should invert just because the app theme flipped.
 *
 * Text legibility is handled by re-running the existing contrast.js
 * `ensureReadable()` against whichever background actually renders (adapted
 * or not) — no separate text-color heuristic needed.
 *
 * Pure: no React, no DOM. Callers pass in the current theme (from useTheme()).
 */

import { parseHex, relativeLuminance, ensureReadable } from './contrast.js'

// Mirrors the `--surface` token in src/index.css — keep in sync if it changes.
const SURFACE_HEX = { light: '#ffffff', dark: '#111a2e' }

const NEUTRAL_SAT_MAX = 0.12
const LIGHT_LUM_MIN = 0.55

/** Parse a hex or rgb()/rgba() color string into {r,g,b}. Returns null otherwise. */
function parseAnyColor(input) {
  const hex = parseHex(input)
  if (hex) return hex
  if (typeof input !== 'string') return null
  const m = input.trim().match(/^rgba?\(\s*([\d.]+)[,\s]+([\d.]+)[,\s]+([\d.]+)/i)
  if (!m) return null
  return { r: Number(m[1]), g: Number(m[2]), b: Number(m[3]) }
}

/** HSL saturation (0..1) from 0-255 channels. */
function saturation({ r, g, b }) {
  const max = Math.max(r, g, b) / 255
  const min = Math.min(r, g, b) / 255
  if (max === min) return 0
  const l = (max + min) / 2
  const d = max - min
  return l > 0.5 ? d / (2 - max - min) : d / (max + min)
}

/**
 * True for near-white/near-gray colors — the "no real design intent"
 * default a converter (or an author who never touched the background field)
 * produces. Genuinely dark or saturated colors return false and are left
 * untouched by adaptation.
 *
 * @param {unknown} colorStr
 * @returns {boolean}
 */
export function isLightNeutral(colorStr) {
  const rgb = parseAnyColor(colorStr)
  if (!rgb) return false
  return saturation(rgb) <= NEUTRAL_SAT_MAX && relativeLuminance(rgb) >= LIGHT_LUM_MIN
}

/**
 * Adapt a single background color value for the given theme.
 *
 * @param {unknown} colorStr
 * @param {'light'|'dark'} [theme]
 * @returns {{ css: unknown, hex: string|null }}
 *   css — the value to render (unchanged, or `'var(--surface)'` for a
 *         light-neutral color so it follows the app theme).
 *   hex — the concrete color for the CURRENT theme, for contrast math.
 *         null when the input isn't a resolvable color (gradient, keyword,
 *         css var, undefined, ...).
 */
export function adaptBackgroundColor(colorStr, theme = 'light') {
  if (typeof colorStr !== 'string' || !colorStr) {
    return { css: colorStr, hex: null }
  }
  if (isLightNeutral(colorStr)) {
    return { css: 'var(--surface)', hex: SURFACE_HEX[theme] ?? SURFACE_HEX.light }
  }
  return { css: colorStr, hex: parseAnyColor(colorStr) ? colorStr : null }
}

/**
 * Resolve whatever ended up in a (possibly-adapted) style's background /
 * backgroundColor to a concrete hex for contrast math, given the current
 * theme. Returns null when unresolvable (gradient, image, css var this
 * module didn't emit, ...).
 *
 * @param {{ background?: unknown, backgroundColor?: unknown }} out
 * @param {'light'|'dark'} [theme]
 * @returns {string|null}
 */
export function resolveEffectiveBgHex(out, theme = 'light') {
  const bg = typeof out?.backgroundColor === 'string' ? out.backgroundColor : out?.background
  if (typeof bg !== 'string') return null
  if (bg === 'var(--surface)') return SURFACE_HEX[theme] ?? SURFACE_HEX.light
  return parseAnyColor(bg) ? bg : null
}

// Re-exported so callers that only need the contrast guard (not the
// background adaptation) don't need a second import.
export { ensureReadable }
