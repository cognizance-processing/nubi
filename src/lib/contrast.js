/**
 * contrast.js — small color helpers for keeping text readable on tiles that
 * carry an explicit background color.
 *
 * Dashboards migrated from other BI tools routinely arrive with a per-widget
 * background plus a single text color applied to every tile (a converter
 * default rather than a per-tile design choice). That combination can land at
 * ~2.5:1 contrast on the darker tiles, which reads as a washed-out number.
 * `ensureReadable` keeps the author's color whenever it is legible and only
 * substitutes an ink when it genuinely is not.
 *
 * Luminance/contrast follow WCAG 2.1 (relative luminance with sRGB
 * linearisation, ratio = (L1 + 0.05) / (L2 + 0.05)).
 */

/** Ink colors used when an author color is unreadable (or absent). */
export const INK_ON_DARK = 'rgba(255,255,255,0.92)'
export const INK_ON_LIGHT = 'rgba(15,23,42,0.88)'

/** Softer variants for secondary text (labels). */
export const MUTED_INK_ON_DARK = 'rgba(255,255,255,0.75)'
export const MUTED_INK_ON_LIGHT = 'rgba(30,41,59,0.72)'

/**
 * Parse a hex color into {r,g,b} (0-255). Accepts `#rgb` and `#rrggbb`,
 * with or without the leading `#`. Returns null for anything else — callers
 * treat null as "no explicit background", i.e. keep theme defaults.
 *
 * @param {unknown} hex
 * @returns {{r:number,g:number,b:number}|null}
 */
export function parseHex(hex) {
  if (typeof hex !== 'string') return null
  const h = hex.trim().replace(/^#/, '')
  if (/^[0-9a-fA-F]{3}$/.test(h)) {
    return {
      r: parseInt(h[0] + h[0], 16),
      g: parseInt(h[1] + h[1], 16),
      b: parseInt(h[2] + h[2], 16),
    }
  }
  if (/^[0-9a-fA-F]{6}$/.test(h)) {
    return {
      r: parseInt(h.slice(0, 2), 16),
      g: parseInt(h.slice(2, 4), 16),
      b: parseInt(h.slice(4, 6), 16),
    }
  }
  return null
}

/**
 * WCAG relative luminance (0 = black, 1 = white).
 * @param {{r:number,g:number,b:number}} rgb
 * @returns {number}
 */
export function relativeLuminance({ r, g, b }) {
  const chan = (v) => {
    const s = v / 255
    return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4
  }
  return 0.2126 * chan(r) + 0.7152 * chan(g) + 0.0722 * chan(b)
}

/**
 * WCAG contrast ratio between two colors (1 … 21). Returns null when either
 * color cannot be parsed as hex.
 *
 * @param {string} a
 * @param {string} b
 * @returns {number|null}
 */
export function contrastRatio(a, b) {
  const ca = parseHex(a)
  const cb = parseHex(b)
  if (!ca || !cb) return null
  const la = relativeLuminance(ca)
  const lb = relativeLuminance(cb)
  const hi = Math.max(la, lb)
  const lo = Math.min(la, lb)
  return (hi + 0.05) / (lo + 0.05)
}

/**
 * True when `bg` is a dark color (so light ink belongs on it).
 * @param {string} bg
 * @returns {boolean|null} null when unparseable
 */
export function isDarkBackground(bg) {
  const rgb = parseHex(bg)
  if (!rgb) return null
  return relativeLuminance(rgb) < 0.4
}

/**
 * Pick readable ink for a background.
 * @param {string} bg
 * @param {boolean} [muted] use the softer secondary-text variant
 * @returns {string|undefined} undefined when bg is not an explicit hex color
 */
export function readableInk(bg, muted = false) {
  const dark = isDarkBackground(bg)
  if (dark === null) return undefined
  if (dark) return muted ? MUTED_INK_ON_DARK : INK_ON_DARK
  return muted ? MUTED_INK_ON_LIGHT : INK_ON_LIGHT
}

/**
 * Keep `color` if it reads acceptably on `bg`; otherwise return readable ink.
 *
 * Returns `color` unchanged when there is no explicit background, when the
 * color is not a plain hex (gradients, css vars, currentColor — the caller's
 * own concern), or when it already clears `minRatio`.
 *
 * @param {string|undefined} color  author-specified text color
 * @param {string|undefined} bg     tile background
 * @param {{minRatio?: number, muted?: boolean}} [opts]
 * @returns {string|undefined}
 */
export function ensureReadable(color, bg, opts = {}) {
  const { minRatio = 3, muted = false } = opts
  if (!parseHex(bg)) return color
  if (!parseHex(color)) return color ?? readableInk(bg, muted)
  const ratio = contrastRatio(color, bg)
  if (ratio != null && ratio >= minRatio) return color
  return readableInk(bg, muted)
}
