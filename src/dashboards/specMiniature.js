/**
 * specMiniature.js — reduce a DashboardSpec to a tiny, drawable layout model.
 *
 * This is what makes a dashboard card on /dashboards a real "window into the
 * dashboard" rather than a decorative icon: it reads the board's ACTUAL widget
 * positions and types out of the spec that `GET /boards` already returns, and
 * normalises them into a unit box a renderer can draw glyphs into.
 *
 * Why a derived miniature instead of a live render
 * ------------------------------------------------
 * Mounting <SpecRenderer> per card is not viable. Widgets fetch on mount
 * (ChartWidget → runArrowQueryById) and SpecRenderer's `editMode` does NOT
 * suppress that — it only skips filter-option queries and auto-refresh. So a
 * grid of 20 cards would fire ~100+ queries and mount 20 ECharts canvases on a
 * list page. And with fetches suppressed you'd draw empty widgets, which is
 * strictly worse than a truthful wireframe. The miniature costs microseconds,
 * zero requests, and still tells you the honest thing a thumbnail should: what
 * this board is SHAPED like.
 *
 * This module is deliberately plain `.js` with no JSX and no imports that reach
 * `src/lib/api.js` (which reads `import.meta.env` and therefore cannot be
 * imported under `node --test`). See specMiniature.test.mjs.
 */

import { effectiveWidgetPos } from './responsiveLayout.js'
import { effectivePlacement } from '../editor/shared/placementHelpers.js'

/** Default column count when a spec doesn't declare one (mirrors SpecRenderer). */
export const DEFAULT_COLS = 12

/** Default grid row height in px when a spec doesn't declare one (mirrors SpecRenderer). */
export const DEFAULT_ROW_HEIGHT = 60

/**
 * The canvas width, in px, a miniature's aspect is reasoned about at.
 *
 * Grid cells are NOT square: a cell is `canvasWidth / cols` wide but a fixed
 * `row_height` px tall. Ignoring that makes a normal 12×14 board compute as a
 * PORTRAIT box, which is the opposite of how it looks on screen. We can't know
 * the viewer's real canvas width from a card, so we reason at a typical desktop
 * board width — the ratio is what matters, not the absolute size.
 */
export const REFERENCE_CANVAS_W = 1200

/**
 * Glyph kinds a miniature renderer must know how to draw. Kept deliberately
 * small — a thumbnail communicates silhouette, not detail, so the 18 chart
 * types collapse into a handful of recognisable shapes.
 */
export const MINIATURE_KINDS = [
  'bars', 'line', 'area', 'points', 'circle', 'kpi', 'table', 'filter', 'text', 'heading',
]

/**
 * Map a widget to its glyph kind.
 *
 * Chart widgets collapse by silhouette: anything drawn as vertical columns is
 * 'bars', anything radial is 'circle', and so on. Unknown and future chart
 * types fall back to 'bars' — a neutral, obviously-a-chart shape — rather than
 * disappearing from the miniature.
 */
export function widgetKind(widget) {
  const type = widget?.type
  if (type === 'kpi' || type === 'metric') return 'kpi'
  if (type === 'table') return 'table'
  if (type === 'pivot') return 'table'
  if (type === 'filter') return 'filter'
  if (type === 'text') return 'text'
  if (type === 'section') return 'heading'

  // type === 'chart' (or absent but chart_type present) → collapse by silhouette.
  const ct = widget?.chart_type
  switch (ct) {
    case 'line':
    case 'fan':
      return 'line'
    case 'area':
      return 'area'
    case 'scatter':
    case 'bubble':
      return 'points'
    case 'pie':
    case 'donut':
    case 'gauge':
    case 'radar':
      return 'circle'
    default:
      // bar, combo, waterfall, boxplot, candlestick, sankey, funnel, treemap,
      // heatmap, unknown → a columnar silhouette reads as "a chart".
      return 'bars'
  }
}

/**
 * The widgets a miniature should draw: the FIRST tab only, grid-placed only.
 *
 * Mirrors the partition rule SpecRenderer and the editor canvas both use — a
 * widget belongs to the first tab when its tab_id matches it OR is null/absent.
 * A card shows what you'd see on opening the board, so tabs 2..n are out.
 *
 * Drawer widgets are excluded (they're behind a slide-over, not on the canvas).
 * Header widgets ARE included and reported separately so a renderer can draw
 * them as a filter strip above the grid, which is where they actually appear.
 */
export function miniatureWidgets(spec) {
  const widgets = Array.isArray(spec?.widgets) ? spec.widgets : []
  const tabs = Array.isArray(spec?.tabs) ? spec.tabs : []
  const firstTabId = tabs[0]?.id ?? null

  const inFirstTab = (w) => {
    if (tabs.length === 0) return true
    const t = w?.tab_id ?? null
    return t === firstTabId || t == null
  }

  const grid = []
  const header = []
  for (const w of widgets) {
    if (!w || !inFirstTab(w)) continue
    const placement = effectivePlacement(w)
    if (placement === 'drawer') continue
    if (placement === 'header') header.push(w)
    else grid.push(w)
  }
  return { grid, header }
}

/**
 * Build a normalised miniature model from a spec.
 *
 * Returns null when there is nothing truthful to draw (no spec, no widgets, or
 * no widget carrying a usable position) so callers can fall back to an icon
 * rather than render a misleading empty frame.
 *
 * Geometry: items are emitted in GRID units (x/y/w/h as authored). The renderer
 * owns the projection into a viewBox, because the correct unit height depends on
 * the board's `row_height` — see `unitHeight` below, which this returns so the
 * renderer doesn't have to re-derive it.
 *
 * @param {object} spec
 * @param {{ maxRows?: number }} [opts] maxRows clamps very tall boards so a
 *   40-row board's widgets don't shrink to invisible slivers — the miniature
 *   shows the top of the board, which is what a viewer sees first anyway.
 * @returns {{cols:number, rows:number, unitWidth:number, unitHeight:number,
 *   items:Array, header:Array, truncated:boolean}|null}
 */
export function buildMiniature(spec, { maxRows = 14 } = {}) {
  if (!spec || typeof spec !== 'object') return null

  const cols = Number(spec?.layout?.cols) > 0 ? Number(spec.layout.cols) : DEFAULT_COLS
  const rowHeightPx = Number(spec?.layout?.row_height) > 0
    ? Number(spec.layout.row_height)
    : DEFAULT_ROW_HEIGHT
  const { grid, header } = miniatureWidgets(spec)
  if (grid.length === 0 && header.length === 0) return null

  const items = []
  for (const w of grid) {
    const pos = effectiveWidgetPos(w, spec)
    const x = Number(pos?.x)
    const y = Number(pos?.y)
    const width = Number(pos?.w)
    const height = Number(pos?.h)
    // A widget with no usable geometry can't be placed truthfully — skip it
    // rather than guess a position and draw a lie.
    if (!Number.isFinite(x) || !Number.isFinite(y)) continue
    if (!Number.isFinite(width) || !Number.isFinite(height)) continue
    if (width <= 0 || height <= 0) continue
    // Spec positions are ONE-based (the frontend does `(p.x ?? 1) - 1` in
    // responsiveLayout.posToGridItem, the server-side composer does `(x-1)*colW`,
    // and the backend validator requires x,y >= 1). Emit ZERO-based drawing
    // coordinates. Treating them as already-zero-based shifted every board one
    // cell right/down and pushed a right-edge widget (x=10,w=3 on a 12-col grid)
    // outside the frame.
    items.push({
      id: w.id ?? null,
      kind: widgetKind(w),
      x: Math.max(0, x - 1),
      y: Math.max(0, y - 1),
      w: Math.min(Math.max(1, width), cols),
      h: Math.max(1, height),
    })
  }

  if (items.length === 0 && header.length === 0) return null

  // Content rows = the lowest bottom edge, clamped so tall boards stay legible.
  const contentRows = items.reduce((max, it) => Math.max(max, it.y + it.h), 0)
  const rows = Math.max(1, Math.min(contentRows, maxRows))
  const truncated = contentRows > maxRows

  // Drop anything entirely below the clamp, and trim anything straddling it, so
  // the renderer never draws outside the frame it was handed.
  const visible = []
  for (const it of items) {
    if (it.y >= rows) continue
    visible.push(it.y + it.h > rows ? { ...it, h: rows - it.y } : it)
  }

  // Unit size in a viewBox whose WIDTH is normalised to 100.
  //   unitWidth  = 100 / cols
  //   unitHeight = unitWidth * (rowHeightPx / colWidthPx)
  //              = (100/cols) * (rowHeightPx / (REFERENCE_CANVAS_W/cols))
  //              = 100 * rowHeightPx / REFERENCE_CANVAS_W
  // Note it falls out independent of `cols` — a taller row_height makes every
  // board taller, but adding columns narrows cells in BOTH axes equally.
  const unitWidth = 100 / cols
  const unitHeight = (100 * rowHeightPx) / REFERENCE_CANVAS_W

  return {
    cols,
    rows,
    unitWidth,
    unitHeight,
    items: visible,
    header: header.map(w => ({ id: w.id ?? null, kind: widgetKind(w) })),
    truncated,
  }
}
