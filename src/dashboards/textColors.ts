/**
 * textColors.js — resolve a widget's per-role text colours.
 *
 * A widget card has at most three kinds of text, and until now only one of
 * them was authorable: `widget.style.color` painted "the card", and everything
 * a widget considered secondary — a KPI's caption, a section's subtitle, a
 * chart's axis and legend labels, a table's header — was derived from the
 * theme or from the tile background and could not be changed at all.
 *
 * The model is three ROLES rather than a colour per widget per element, so the
 * same three controls mean the same thing on every widget type:
 *
 *   style.titleColor  — headings: chart title, section title, table header,
 *                       filter label, the caption above a date display.
 *   style.color       — the primary/body text: a KPI's number, table cells,
 *                       markdown body. (Unchanged meaning — existing boards
 *                       keep rendering exactly as before.)
 *   style.mutedColor  — supporting text: subtitles, KPI labels, axis and
 *                       legend labels, hints.
 *
 * Fallback chain, per role:
 *   title →  titleColor  →  color  →  (theme)
 *   body  →  color       →  (theme)
 *   muted →  mutedColor  →  a softened `color`  →  (theme / tile-derived ink)
 *
 * "Softened" matters: a card that sets one text colour should still read as a
 * hierarchy rather than a flat block, which is what the previous single-colour
 * behaviour produced when a widget honoured it at all.
 *
 * A `null` in the result means "no override — use the theme", never "black".
 * Callers must treat it that way; painting null as a colour is how a themed
 * board turns unreadable in dark mode.
 */

import { ensureReadable, readableInk } from '../lib/contrast.js'

/** How much of the body colour a derived muted colour keeps. */
const MUTED_MIX = 0.68

/**
 * Blend a colour toward transparency for the muted role.
 *
 * `color-mix` is used rather than an alpha channel because the authored value
 * may be any CSS colour (a token, a named colour, `rgb()`), and mixing works
 * for all of them where string-slicing an alpha on does not.
 */
export function softenColor(color) {
  if (typeof color !== 'string' || !color) return null
  return `color-mix(in srgb, ${color} ${Math.round(MUTED_MIX * 100)}%, transparent)`
}

/**
 * Resolve the three text roles for a widget.
 *
 * @param {object} style   widget.style (may be undefined)
 * @param {{ background?: string|null, adapt?: boolean }} [ctx]
 *        background — the tile's ACTUAL rendered background, used to (a) keep an
 *        authored colour readable on it and (b) derive a muted ink when the
 *        author set no colours at all. Omit for a card with no baked background.
 * @returns {{ title: string|null, body: string|null, muted: string|null }}
 */
export function resolveTextColors(
  style,
  ctx: { background?: string | null } = {},
) {
  const s = style && typeof style === 'object' ? style : {}
  const bg = ctx.background ?? null

  const body = typeof s.color === 'string' && s.color ? s.color : null
  const title = typeof s.titleColor === 'string' && s.titleColor ? s.titleColor : body
  const mutedExplicit = typeof s.mutedColor === 'string' && s.mutedColor ? s.mutedColor : null

  // With no authored muted colour: soften the body colour if there is one,
  // otherwise fall back to ink derived from the tile background (the previous
  // behaviour for tiles that bake a background), otherwise leave it to theme.
  const muted = mutedExplicit
    ?? softenColor(body)
    ?? (bg ? readableInk(bg, true) : null)

  // An authored colour that lands unreadable on an explicit tile background is
  // swapped for legible ink — the same rule KpiWidget already applied to its
  // headline, now applied consistently to every role.
  return {
    title: bg && title ? ensureReadable(title, bg) : title,
    body: bg && body ? ensureReadable(body, bg) : body,
    muted: bg && mutedExplicit ? ensureReadable(mutedExplicit, bg) : muted,
  }
}

/**
 * A `style` object for a text node, or undefined when the role has no override.
 * Keeps callers from writing `style={{ color: undefined }}` everywhere.
 */
export function colorStyle(color) {
  return color ? { color } : undefined
}
