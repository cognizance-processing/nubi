/**
 * Font size for a KPI value that must stay whole inside its card: the authored
 * size, shrinking with the card's width (`cqw`, so the element's ancestor must be
 * a size container) when the text wouldn't otherwise fit. Purely CSS, so it follows
 * a resize without measuring anything.
 */
const GLYPH_EM = 0.6
const PUNCTUATION_EM = 0.3
const SMALLEST_PX = 12

export function fitValueFontSize(text: string, sizePx: number): string {
  const glyphs = [...String(text)]
  const narrow = glyphs.filter((c) => /[\s.,:;']/.test(c)).length
  const widthEm = Math.max((glyphs.length - narrow) * GLYPH_EM + narrow * PUNCTUATION_EM, GLYPH_EM)
  return `max(${SMALLEST_PX}px, min(${sizePx}px, calc(100cqw / ${widthEm.toFixed(2)})))`
}
