/**
 * The `replaceKey` a ChartWidget hands to <EChart>: identity of the chart's
 * SHAPE. EChart merges a new option into the drawn one while the key holds and
 * replaces it (notMerge) when the key changes — so the key must change
 * whenever two options share a chart_type but not a geometry.
 *
 *   map    → `map:<id>`   (each geography is its own geo component)
 *   gauge  → `gauge:needle` | `gauge:ring` (ring's full-circle / hidden
 *            pointer fields would otherwise survive a merge back to needle)
 *   other  → chart_type
 *
 * Colour-only config (e.g. gaugeArc*) deliberately stays out of the key —
 * the builders set those fields explicitly, so merging is safe and cheap.
 */
export function chartReplaceKey(chartType: string, config?: Record<string, any> | null): string {
  if (chartType === 'map') return `map:${config?.map || 'world'}`
  if (chartType === 'gauge') return `gauge:${config?.gaugeStyle ?? 'needle'}`
  return chartType
}
