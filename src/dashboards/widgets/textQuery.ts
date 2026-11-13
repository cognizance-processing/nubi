/**
 * Query-mode text value (text widget, props.source === 'query').
 *
 * Same row rule as ImageWidget and svg_render._query_cell: a missing or
 * non-integer row is 0, negative → 0, and a row past the end yields nothing
 * (never another row's value, never the static markdown content).
 */

export function textQueryRow(row: unknown): number {
  return Number.isInteger(row) ? Math.max(row as number, 0) : 0
}

/** prefix + cell + suffix, or '' when the cell is null/undefined/blank. */
export function textQueryValue(cell: unknown, prefix: unknown = '', suffix: unknown = ''): string {
  if (cell === null || cell === undefined) return ''
  const s = String(cell).trim()
  if (!s) return ''
  return `${typeof prefix === 'string' ? prefix : ''}${s}${typeof suffix === 'string' ? suffix : ''}`
}
