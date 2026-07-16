/**
 * shared/colorValue.js — colour-value coercion for inspector swatches.
 *
 * Kept as plain .js (no JSX) so the pure parts are testable under `node --test`;
 * inspectorPrimitives.jsx imports it for <ColorSwatch> / <ColorField>.
 *
 * Why this exists
 * ---------------
 * `<input type="color">` only ever *displays* `#rrggbb`. It is more capable
 * than it looks — the browser resolves anything its CSS colour parser accepts,
 * so 'rebeccapurple' → #663399 and '#abc' → #aabbcc all on its own. Do NOT
 * "helpfully" reject those: that throws away a colour the user can legitimately
 * type into the free-text half of a ColorField.
 *
 * What it genuinely cannot resolve, it silently paints BLACK:
 *   'inherit' / 'initial' / 'unset'   — CSS-wide keywords, and precisely what
 *                                       our "inherit (theme default)" fields hold
 *   'var(--fg)'                       — custom properties (no element context)
 *   'transparent'                     — resolves to rgba(0,0,0,0) → black chip
 *   '#1', 'not-a-color'               — half-typed / junk
 * A black chip reads as a real colour choice, so those must show a fallback.
 */

const HEX6 = /^#[0-9a-fA-F]{6}$/
const HEX3 = /^#[0-9a-fA-F]{3}$/
// Channels allow a leading '-' purely so clamp255 is what decides the result;
// getComputedStyle never emits negatives, but a hand-typed rgb() might.
const RGB = /^rgba?\(\s*(-?[\d.]+)[\s,]+(-?[\d.]+)[\s,]+(-?[\d.]+)/i

/**
 * CSS-wide keywords and friends that are valid CSS but meaningless to a swatch
 * (they need an element/context to resolve, and coerce to black without one).
 * `CSS.supports('color', 'inherit')` is TRUE, so this list is what stops those
 * from reaching the input.
 */
export const UNRESOLVABLE = new Set([
  'inherit', 'initial', 'unset', 'revert', 'revert-layer',
  'currentcolor', 'transparent', 'none', '',
])

const clamp255 = n => Math.max(0, Math.min(255, Math.round(n)))
const toHex2 = n => clamp255(n).toString(16).padStart(2, '0')

/**
 * Convert a computed `rgb()` / `rgba()` string to `#rrggbb`.
 * Alpha is dropped — a colour input has no alpha channel.
 * @returns {string|null} hex, or null if `str` isn't an rgb()/rgba() string
 */
export function rgbToHex(str) {
  const m = RGB.exec(String(str ?? '').trim())
  if (!m) return null
  return `#${toHex2(+m[1])}${toHex2(+m[2])}${toHex2(+m[3])}`
}

/**
 * The pure, DOM-free part: normalise the forms we can resolve without a
 * browser — #rgb, #rrggbb and rgb()/rgba().
 *
 * Returns `null` (not a fallback) when the value needs the CSS parser, so
 * callers can decide whether to attempt DOM resolution or give up.
 *
 * @param {unknown} value
 * @returns {string|null} a `#rrggbb` string, or null if not resolvable here
 */
export function toSwatchHex(value) {
  const v = String(value ?? '').trim()
  if (HEX6.test(v)) return v.toLowerCase()
  // #abc → #aabbcc. The input would do this itself, but doing it here keeps the
  // value React holds and the value the DOM reports identical (no controlled-
  // input churn).
  if (HEX3.test(v)) return `#${v.slice(1).split('').map(c => c + c).join('')}`.toLowerCase()
  return rgbToHex(v)
}

/**
 * Resolve any user-typed colour to a hex the swatch can show.
 *
 * Order: pure forms first (no DOM cost), then hand anything else to the
 * browser's own CSS parser so named colours, hsl(), colour functions and
 * whatever CSS gains next all keep working without a change here. Only values
 * the parser rejects — or that are meaningless without context — hit `fallback`.
 *
 * @param {unknown} value
 * @param {string}  fallback — swatch colour when `value` can't be resolved
 * @returns {string} a `#rrggbb` string
 */
export function resolveSwatchHex(value, fallback = '#6366f1') {
  const direct = toSwatchHex(value)
  if (direct) return direct

  const v = String(value ?? '').trim().toLowerCase()
  if (UNRESOLVABLE.has(v)) return fallback

  // No DOM (SSR/tests) → we've already tried everything pure.
  if (typeof document === 'undefined' || typeof CSS === 'undefined' || !CSS.supports) return fallback
  if (!CSS.supports('color', v)) return fallback

  // Let the browser parse it: 'rebeccapurple' / 'hsl(...)' / 'oklch(...)' → rgb().
  const probe = document.createElement('div')
  probe.style.color = v
  // A detached element has no computed style, so it must be in the document.
  probe.style.display = 'none'
  document.body.appendChild(probe)
  const computed = getComputedStyle(probe).color
  probe.remove()

  return rgbToHex(computed) ?? fallback
}
