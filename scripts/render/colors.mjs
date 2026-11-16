// Colour validation for server-side renders. Widget colours are written into
// SVG attributes, so anything that is not a plain colour value is dropped.
const COLOR_RE = /^(#[0-9a-f]{3,4}|#[0-9a-f]{6}|#[0-9a-f]{8}|(rgb|hsl)a?\([\d\s.,%/a-z-]{1,60}\)|[a-z]{3,24})$/i
const NOT_A_COLOR = /^(url|expression|var|attr|image|element|calc)$/i

/** The colour unchanged when it is a plain colour value, otherwise undefined. */
export function safeColor(value) {
  if (typeof value !== 'string') return undefined
  const v = value.trim()
  if (!COLOR_RE.test(v) || NOT_A_COLOR.test(v)) return undefined
  return v
}

/** The valid entries of a palette, or the fallback when none are. */
export function safePalette(palette, fallback) {
  if (!Array.isArray(palette)) return fallback
  const ok = palette.map(safeColor).filter(Boolean)
  return ok.length ? ok : fallback
}
