/** 3×5 voxel font for KPI numerals (rows top → bottom). Glyph advance = width + 1 pixel. */
export const FONT: Record<string, string[]> = {
  '0': ['111', '101', '101', '101', '111'], '1': ['010', '110', '010', '010', '111'], '2': ['111', '001', '111', '100', '111'],
  '3': ['111', '001', '111', '001', '111'], '4': ['101', '101', '111', '001', '001'], '5': ['111', '100', '111', '001', '111'],
  '6': ['111', '100', '111', '101', '111'], '7': ['111', '001', '001', '010', '010'], '8': ['111', '101', '111', '101', '111'],
  '9': ['111', '101', '111', '001', '111'], '.': ['0', '0', '0', '0', '1'], ',': ['0', '0', '0', '1', '1'], ':': ['0', '1', '0', '1', '0'],
  'M': ['10001', '11011', '10101', '10001', '10001'], 'k': ['100', '101', '110', '101', '101'], '%': ['101', '001', '010', '100', '101'],
  '$': ['011', '110', '010', '011', '110'],
}
export const GLYPHS = Object.keys(FONT).join('')

/** Width of a voxel string in world units at pixel size px (unknown glyphs advance 2 px, like a space). */
export function measureVoxelText(text: string, px = 0.5): number {
  let w = 0
  for (const ch of text) { const g = FONT[ch]; w += g ? (g[0].length + 1) * px : px * 2 }
  return Math.max(0, w - px)
}
