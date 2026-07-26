/**
 * echarts-ssr.mjs — Server-side SVG renderer for Nubi dashboard widgets.
 *
 * Accepts a JSON payload on stdin describing one or more widgets + their data,
 * and writes a JSON response to stdout with the rendered SVG string per widget.
 *
 * Usage (subprocess):
 *   echo '<payload>' | node scripts/render/echarts-ssr.mjs
 *
 * Payload shape (JSON on stdin):
 *   {
 *     widgets: [
 *       {
 *         id: string,          // widget id
 *         type: 'chart'|'kpi'|'table'|'text'|'filter'|'metric'|'pivot'|'section'|'html',
 *         chart_type?: string, // required for type='chart'
 *         encoding?: {x?,y?,color?,value?},
 *         props?: object,
 *         content?: string,    // for text widgets
 *         columns?: string[],  // data columns
 *         rows?: any[][],      // data rows (row-major)
 *         width?: number,      // pixel width (default: 800)
 *         height?: number,     // pixel height (default: 400)
 *       },
 *       ...
 *     ]
 *   }
 *
 * Response shape (JSON on stdout):
 *   {
 *     widgets: [
 *       {
 *         id: string,
 *         svg: string,         // SVG string, or null on hard error
 *         webgl: false,        // true when the widget type requires raster fallback
 *         error?: string,      // set when svg is null
 *       },
 *       ...
 *     ]
 *   }
 *
 * Coordinate note
 * ---------------
 * The SVG emitted for each widget has an explicit width/height matching the
 * requested pixel dimensions.  The page composer (svg-composer.mjs) places each
 * widget SVG as a nested <svg> at the widget's grid position — no scaling is
 * applied at this stage.
 *
 * WebGL / raster flag
 * -------------------
 * Widget types that require WebGL (e.g. regl-based scatter) are flagged with
 * webgl:true and svg:null — the Python caller is responsible for rasterising
 * them at high DPI and embedding the PNG as a <image> element in the composed
 * page SVG.  Currently no core Nubi widgets hit this path.
 */

import { createRequire } from 'module'

const require = createRequire(import.meta.url)

// ---------------------------------------------------------------------------
// ECharts SSR bootstrap
// ---------------------------------------------------------------------------

// Resolve echarts from node_modules by walking up from __dirname until we find
// a directory that contains node_modules/echarts/core.js.  This is robust when
// the script is invoked from a git worktree (which has no node_modules of its
// own) because it will walk up to the real repo root where node_modules lives.
import { fileURLToPath } from 'url'
import { dirname, resolve } from 'path'
import { existsSync } from 'fs'

const __dirname = dirname(fileURLToPath(import.meta.url))

/**
 * Walk up from `startDir` until we find a directory that has
 * `node_modules/echarts/core.js`, or return null if we reach the filesystem
 * root without finding it.
 *
 * @param {string} startDir
 * @returns {string|null}
 */
function findEchartsRoot(startDir) {
  let dir = startDir
  while (true) {
    if (existsSync(resolve(dir, 'node_modules', 'echarts', 'core.js'))) return dir
    const parent = dirname(dir)
    if (parent === dir) return null  // reached filesystem root
    dir = parent
  }
}

// First candidate: two levels up from scripts/render/ (i.e. repo root in the
// normal checkout layout).  Fall back to the walk-up search so git worktrees
// that have no local node_modules still resolve correctly.
const DIRECT_ROOT = resolve(__dirname, '../..')
const REPO_ROOT = existsSync(resolve(DIRECT_ROOT, 'node_modules', 'echarts', 'core.js'))
  ? DIRECT_ROOT
  : findEchartsRoot(__dirname)

// Dynamic import of echarts — lets us emit a clear install error if missing.
let echartsInit
try {
  if (!REPO_ROOT) throw new Error(
    'node_modules/echarts not found — run `npm install` in the repo root'
  )
  const echarts = await import(resolve(REPO_ROOT, 'node_modules/echarts/core.js'))
  const { SVGRenderer } = await import(resolve(REPO_ROOT, 'node_modules/echarts/renderers.js'))
  const { BarChart, LineChart, ScatterChart, PieChart, HeatmapChart, GaugeChart } =
    await import(resolve(REPO_ROOT, 'node_modules/echarts/charts.js'))
  const {
    GridComponent, TooltipComponent, LegendComponent,
    VisualMapComponent, DataZoomComponent, TitleComponent,
  } = await import(resolve(REPO_ROOT, 'node_modules/echarts/components.js'))

  echarts.use([
    SVGRenderer,
    BarChart, LineChart, ScatterChart, PieChart, HeatmapChart, GaugeChart,
    GridComponent, TooltipComponent, LegendComponent,
    VisualMapComponent, DataZoomComponent, TitleComponent,
  ])
  echartsInit = echarts.init
} catch (err) {
  process.stderr.write(`[echarts-ssr] echarts not available: ${err.message}\n`)
  process.exit(2)
}

// ---------------------------------------------------------------------------
// Palette (mirrors src/viz/chartOption.js)
// ---------------------------------------------------------------------------

const PALETTE = [
  '#6366f1', '#f59e0b', '#10b981', '#ef4444', '#3b82f6',
  '#8b5cf6', '#ec4899', '#14b8a6', '#f97316', '#84cc16',
]

// ---------------------------------------------------------------------------
// Data helpers
// ---------------------------------------------------------------------------

/**
 * Convert row-major data (columns + rows) into a column-major object map.
 * @param {string[]} columns
 * @param {any[][]} rows
 * @returns {Object.<string, any[]>}
 */
function toColumnMap(columns, rows) {
  const map = {}
  for (const col of columns) map[col] = []
  for (const row of rows) {
    for (let i = 0; i < columns.length; i++) {
      map[columns[i]].push(row[i] ?? null)
    }
  }
  return map
}

function toNumbers(arr) {
  return (arr || []).map(v => (v === null || v === undefined ? 0 : Number(v)))
}

function toStrings(arr) {
  return (arr || []).map(v => (v === null || v === undefined ? '' : String(v)))
}

function isNumericCol(arr) {
  for (const v of arr) {
    if (v != null) return typeof v === 'number' || typeof v === 'bigint'
  }
  return false
}

// ---------------------------------------------------------------------------
// Chart option builders (subset matching chartOption.js semantics)
// These operate on column maps rather than Arrow tables.
// ---------------------------------------------------------------------------

function emptyOption(message = 'No data') {
  return {
    color: PALETTE,
    backgroundColor: 'transparent',
    animation: false,
    graphic: [{
      type: 'text', left: 'center', top: 'middle',
      style: { text: message, fontSize: 14, fill: '#9ca3af' },
    }],
  }
}

function baseAxesOption(xData, isDark = false) {
  return {
    color: PALETTE,
    backgroundColor: 'transparent',
    animation: false,
    grid: { left: 48, right: 24, top: 36, bottom: 48, containLabel: false },
    xAxis: { type: 'category', data: xData, axisLabel: { fontSize: 11 } },
    yAxis: { type: 'value', axisLabel: { fontSize: 11 } },
    tooltip: { show: false },
  }
}

function buildBar(colMap, xCol, yCol) {
  const xData = toStrings(colMap[xCol] || [])
  const yData = toNumbers(colMap[yCol] || [])
  if (!xData.length) return emptyOption()
  return {
    ...baseAxesOption(xData),
    series: [{ type: 'bar', data: yData, itemStyle: { color: PALETTE[0] } }],
  }
}

function buildLine(colMap, xCol, yCol, area = false) {
  const xData = toStrings(colMap[xCol] || [])
  const yData = toNumbers(colMap[yCol] || [])
  if (!xData.length) return emptyOption()
  const series = {
    type: 'line', data: yData,
    itemStyle: { color: PALETTE[0] },
    lineStyle: { color: PALETTE[0] },
  }
  if (area) {
    series.areaStyle = { opacity: 0.25, color: PALETTE[0] }
  }
  return { ...baseAxesOption(xData), series: [series] }
}

function buildScatter(colMap, xCol, yCol) {
  const xData = toNumbers(colMap[xCol] || [])
  const yData = toNumbers(colMap[yCol] || [])
  if (!xData.length) return emptyOption()
  const data = xData.map((x, i) => [x, yData[i]])
  return {
    color: PALETTE,
    backgroundColor: 'transparent',
    animation: false,
    grid: { left: 48, right: 24, top: 36, bottom: 48, containLabel: false },
    xAxis: { type: 'value', axisLabel: { fontSize: 11 } },
    yAxis: { type: 'value', axisLabel: { fontSize: 11 } },
    tooltip: { show: false },
    series: [{ type: 'scatter', data, symbolSize: 6, itemStyle: { color: PALETTE[0] } }],
  }
}

function buildPie(colMap, xCol, yCol, donut = false) {
  const names = toStrings(colMap[xCol] || [])
  const vals = toNumbers(colMap[yCol] || [])
  if (!names.length) return emptyOption()
  const pieData = names.map((n, i) => ({ name: n, value: vals[i] }))
  const radius = donut ? ['40%', '65%'] : '65%'
  return {
    color: PALETTE,
    backgroundColor: 'transparent',
    animation: false,
    tooltip: { show: false },
    legend: { show: false },
    series: [{
      type: 'pie', radius, data: pieData,
      label: { fontSize: 10 }, emphasis: { disabled: true },
    }],
  }
}

function buildHBar(colMap, xCol, yCol) {
  const catData = toStrings(colMap[xCol] || [])
  const valData = toNumbers(colMap[yCol] || [])
  if (!catData.length) return emptyOption()
  return {
    color: PALETTE,
    backgroundColor: 'transparent',
    animation: false,
    grid: { left: 80, right: 24, top: 24, bottom: 36, containLabel: false },
    xAxis: { type: 'value', axisLabel: { fontSize: 11 } },
    yAxis: { type: 'category', data: catData, axisLabel: { fontSize: 11 } },
    tooltip: { show: false },
    series: [{ type: 'bar', data: valData, itemStyle: { color: PALETTE[0] } }],
  }
}

function buildGauge(colMap, valueCol, props = {}) {
  const vals = toNumbers(colMap[valueCol] || [])
  const value = vals[0] ?? 0
  const max = props.max ?? Math.max(value * 1.5, 100)
  const min = props.min ?? 0
  return {
    color: PALETTE,
    backgroundColor: 'transparent',
    animation: false,
    series: [{
      type: 'gauge', min, max,
      data: [{ value }],
      axisLabel: { fontSize: 10 },
      detail: { fontSize: 18 },
    }],
  }
}

/**
 * Build an ECharts option from a widget descriptor + its column data map.
 *
 * @param {object} widget   – Widget object (type, chart_type, encoding, props).
 * @param {object} colMap   – Column data as { colName: any[] }.
 * @returns {object}        – ECharts option object.
 */
function buildChartOption(widget, colMap) {
  const chartType = (widget.chart_type || 'bar').toLowerCase()
  const enc = widget.encoding || {}
  const props = widget.props || {}
  const xCol = enc.x || ''
  const yCol = enc.y || ''

  if (!Object.keys(colMap).length) return emptyOption()

  switch (chartType) {
    case 'bar':     return buildBar(colMap, xCol, yCol)
    case 'line':    return buildLine(colMap, xCol, yCol, false)
    case 'area':    return buildLine(colMap, xCol, yCol, true)
    case 'scatter': return buildScatter(colMap, xCol, yCol)
    case 'pie':     return buildPie(colMap, xCol, yCol, false)
    case 'donut':   return buildPie(colMap, xCol, yCol, true)
    case 'hbar':    return buildHBar(colMap, xCol, yCol)
    case 'gauge':   return buildGauge(colMap, enc.value || yCol, props)
    default:        return buildBar(colMap, xCol, yCol)
  }
}

// ---------------------------------------------------------------------------
// Widget-type SVG builders
// ---------------------------------------------------------------------------

/**
 * Render a chart widget to an SVG string via echarts SSR.
 *
 * @param {object} widget
 * @param {object} colMap
 * @param {number} width
 * @param {number} height
 * @returns {string}  SVG string
 */
function renderChart(widget, colMap, width, height, theme = 'light') {
  const option = buildChartOption(widget, colMap)
  // A chart draws to a canvas/SVG of its own, so it can't inherit CSS the way an
  // HTML widget would: axis + legend text must be told the theme explicitly, or
  // a dark tile gets near-black labels on it.
  const skin = widgetSkin(widget, theme, { bgFallback: 'transparent', fgFallback: null })
  const tokens = THEME_TOKENS[theme]
  const textColor = skin.fg || tokens['--text']
  option.backgroundColor = skin.bg || 'transparent'
  option.textStyle = { ...(option.textStyle || {}), color: textColor }
  for (const axis of ['xAxis', 'yAxis']) {
    if (option[axis]) {
      option[axis] = {
        ...option[axis],
        axisLabel: { ...(option[axis].axisLabel || {}), color: textColor },
        axisLine: { ...(option[axis].axisLine || {}), lineStyle: { color: tokens['--border'] } },
        splitLine: { ...(option[axis].splitLine || {}), lineStyle: { color: tokens['--border'] } },
      }
    }
  }
  if (option.legend) option.legend = { ...option.legend, textStyle: { color: textColor } }
  const chart = echartsInit(null, null, { renderer: 'svg', ssr: true, width, height })
  try {
    chart.setOption(option)
    return chart.renderToSVGString()
  } finally {
    chart.dispose()
  }
}

// ---------------------------------------------------------------------------
// Theme tokens
// ---------------------------------------------------------------------------

/**
 * Nubi's CSS custom properties, resolved to concrete colours per theme.
 *
 * Board widget styles are written against theme TOKENS on purpose — a board is
 * authored once and is meant to look right in light AND dark (see the
 * theme-adaptive dashboard work). That's correct in a browser, where the cascade
 * resolves them, and meaningless here: a standalone SVG has no DOM and no
 * cascade, so `fill="var(--surface)"` renders as nothing. A server render must
 * therefore CHOOSE a theme and resolve the tokens itself.
 *
 * Values mirror src/index.css (`:root` and the dark block). Keep them in sync;
 * a token missing here simply falls through to the caller's default, which is
 * the safe direction — a wrong-but-plausible colour is worse than the default.
 *
 * NOTE `--fg` is deliberately mapped even though src/index.css does NOT define
 * it: Tailwind's `text-fg` maps to `var(--text)`, and real board specs in the
 * wild say `color: "var(--fg)"` — which is dead CSS in the app (it resolves to
 * nothing and inherits). Treating it as `--text` renders what the author plainly
 * meant, and matches what the browser ends up showing via inheritance anyway.
 */
const THEME_TOKENS = {
  light: {
    '--bg': '#f6f8fb',
    '--surface': '#ffffff',
    '--surface-2': '#eef2f7',
    '--surface-3': '#e4eaf3',
    '--border': '#e2e8f0',
    '--text': '#0e1729',
    '--fg': '#0e1729',
    '--text-muted': '#566377',
    '--muted': '#566377',
    '--text-subtle': '#8496ae',
    '--primary': '#2456a6',
    '--primary-fg': '#ffffff',
    '--accent': '#0f9e90',
  },
  dark: {
    '--bg': '#0a1020',
    '--surface': '#111a2e',
    '--surface-2': '#16223b',
    '--surface-3': '#1c2d47',
    '--border': '#21304a',
    '--text': '#e7edf6',
    '--fg': '#e7edf6',
    '--text-muted': '#93a4bd',
    '--muted': '#93a4bd',
    '--text-subtle': '#5f7694',
    '--primary': '#4d8de0',
    '--primary-fg': '#06101f',
    '--accent': '#2dd4bf',
  },
}

/**
 * Resolve a CSS colour value for *theme*, or return `fallback`.
 *
 * Handles the shapes that actually appear in board styles: a plain colour, a
 * `var(--token)` (with optional fallback), and `color-mix(...)`/gradients, which
 * we deliberately DON'T attempt — returning the fallback for anything we can't
 * resolve honestly is better than emitting a broken paint value.
 */
function resolveColor(value, theme, fallback = null) {
  if (value == null) return fallback
  const s = String(value).trim()
  if (!s) return fallback

  const varMatch = s.match(/^var\(\s*(--[a-z0-9-]+)\s*(?:,\s*([^)]+))?\)$/i)
  if (varMatch) {
    const tok = THEME_TOKENS[theme]?.[varMatch[1]]
    if (tok) return tok
    // var(--x, <fallback>) — honour the author's own fallback before ours.
    return varMatch[2] ? resolveColor(varMatch[2].trim(), theme, fallback) : fallback
  }
  // Anything containing a var()/color-mix we can't statically resolve.
  if (/var\(|color-mix\(/i.test(s)) return fallback
  // A plain colour: hex, rgb(), hsl(), or a named colour.
  return s
}

/** The widget's own background + text colour, resolved for *theme*. */
function widgetSkin(widget, theme, { bgFallback = '#f9fafb', fgFallback = '#1f2937' } = {}) {
  const st = widget.style || {}
  const bg = st.background ?? st.backgroundColor
  return {
    bg: resolveColor(bg, theme, bgFallback),
    fg: resolveColor(st.color, theme, fgFallback),
    border: resolveColor(st.borderColor ?? (typeof st.border === 'string' ? st.border.split(' ').pop() : null), theme, null),
  }
}

/**
 * Escape text for SVG text elements.
 * @param {string} s
 * @returns {string}
 */
function svgEsc(s) {
  return String(s ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
}

/**
 * Render a KPI widget as a pure-SVG element.
 * Shows a large numeric value + label.
 *
 * @param {object} widget
 * @param {object} colMap
 * @param {number} width
 * @param {number} height
 * @returns {string}  SVG string
 */
function renderKpi(widget, colMap, width, height, theme = 'light') {
  const skin = widgetSkin(widget, theme)
  // The caption is the value's colour, softened — a second hard-coded grey
  // would fight a dark tile.
  const labelFg = skin.fg
  const enc = widget.encoding || {}
  const props = widget.props || {}
  const valueCol = enc.value || enc.y || enc.x || Object.keys(colMap)[0] || ''
  const label = props.label || valueCol.replace(/_/g, ' ')

  // Get first value
  const vals = colMap[valueCol] || []
  const rawVal = vals[0] ?? ''
  const fmt = props.format || ''

  // Format ONLY a real number. The previous version set '--' for a missing
  // value and then unconditionally overwrote it with `Number(rawVal)…`, so an
  // absent or non-numeric value under format:'percent' rendered the headline
  // "NaN%" — a KPI confidently displaying nonsense. Anything unparseable now
  // falls back to the raw text, or to '--' when there is nothing at all.
  const num = Number(rawVal)
  const isNum = rawVal !== null && rawVal !== undefined && rawVal !== '' && Number.isFinite(num)

  let displayVal
  if (rawVal === null || rawVal === undefined || rawVal === '') displayVal = '--'
  else if (!isNum) displayVal = String(rawVal)
  else if (fmt === 'currency') displayVal = '$' + num.toLocaleString()
  else if (fmt === 'percent') displayVal = (num * 100).toFixed(1) + '%'
  else if (fmt === 'percent100') displayVal = num.toFixed(1) + '%'
  else displayVal = num.toLocaleString()

  const cx = width / 2
  // Size the headline by BOTH axes. Height alone (the old rule) let a long
  // value like "9,100,313.6" render at 48px in a 234px-wide tile and spill out
  // past its card. SVG <text> does not wrap or shrink, so the fit has to be
  // computed here. 0.6em per char is a good approximation of bold sans digit
  // advance width; the 0.9 keeps a little breathing room inside the card.
  const fitByWidth = Math.floor((width * 0.9) / Math.max(1, displayVal.length * 0.6))
  const valueFontSize = Math.max(11, Math.min(48, Math.floor(height * 0.35), fitByWidth))
  const labelFontSize = Math.min(16, Math.floor(height * 0.12))

  return [
    `<svg width="${width}" height="${height}" xmlns="http://www.w3.org/2000/svg">`,
    `  <rect width="${width}" height="${height}" fill="${svgEsc(skin.bg)}" rx="8"${
        skin.border ? ` stroke="${svgEsc(skin.border)}" stroke-width="1"` : ''}/>`,
    `  <text x="${cx}" y="${Math.floor(height * 0.52)}" text-anchor="middle"`,
    `    font-family="sans-serif" font-size="${valueFontSize}" font-weight="700" fill="${svgEsc(skin.fg)}">`,
    `    ${svgEsc(displayVal)}`,
    `  </text>`,
    `  <text x="${cx}" y="${Math.floor(height * 0.75)}" text-anchor="middle"`,
    `    font-family="sans-serif" font-size="${labelFontSize}" fill="${svgEsc(labelFg)}" opacity="0.75">`,
    `    ${svgEsc(label)}`,
    `  </text>`,
    `</svg>`,
  ].join('\n')
}

/**
 * Render a table widget as a pure-SVG table.
 * Renders up to ~20 rows (limited by height).
 *
 * @param {object} widget
 * @param {object} colMap
 * @param {string[]} columns
 * @param {number} width
 * @param {number} height
 * @returns {string}  SVG string
 */
function renderTable(widget, colMap, columns, width, height, theme = 'light') {
  const skin = widgetSkin(widget, theme, { bgFallback: THEME_TOKENS[theme]['--surface'], fgFallback: THEME_TOKENS[theme]['--text'] })
  const tokens = THEME_TOKENS[theme]
  const props = widget.props || {}
  const limit = Math.min(props.limit || 50, 50)
  const colsToRender = (props.columns || columns).slice(0, 8)
  const numCols = colsToRender.length || 1
  const colW = Math.floor(width / numCols)
  const rowH = 28
  const headerH = 32
  const maxRows = Math.min(limit, Math.floor((height - headerH) / rowH))
  const numDataRows = colMap[colsToRender[0]]?.length ?? 0
  const numRows = Math.min(maxRows, numDataRows)

  const lines = [
    `<svg width="${width}" height="${height}" xmlns="http://www.w3.org/2000/svg">`,
    `  <rect width="${width}" height="${height}" fill="${svgEsc(skin.bg)}"/>`,
    // Header row background
    `  <rect x="0" y="0" width="${width}" height="${headerH}" fill="${svgEsc(tokens['--surface-2'])}"/>`,
  ]

  // Header labels
  for (let ci = 0; ci < colsToRender.length; ci++) {
    const col = colsToRender[ci]
    const x = ci * colW + 8
    lines.push(
      `  <text x="${x}" y="${Math.floor(headerH * 0.65)}" ` +
      `font-family="sans-serif" font-size="11" font-weight="600" fill="#374151">` +
      `${svgEsc(col)}</text>`
    )
  }

  // Data rows
  for (let ri = 0; ri < numRows; ri++) {
    const rowY = headerH + ri * rowH
    // Zebra stripe
    if (ri % 2 === 1) {
      lines.push(`  <rect x="0" y="${rowY}" width="${width}" height="${rowH}" fill="#f9fafb"/>`)
    }
    for (let ci = 0; ci < colsToRender.length; ci++) {
      const col = colsToRender[ci]
      const rawVal = (colMap[col] || [])[ri] ?? ''
      const x = ci * colW + 8
      const textY = rowY + Math.floor(rowH * 0.65)
      lines.push(
        `  <text x="${x}" y="${textY}" font-family="sans-serif" font-size="11" fill="#374151">` +
        `${svgEsc(String(rawVal).slice(0, 30))}</text>`
      )
    }
    // Row separator
    lines.push(`  <line x1="0" y1="${rowY + rowH}" x2="${width}" y2="${rowY + rowH}" stroke="#e5e7eb" stroke-width="0.5"/>`)
  }

  // Column separators
  for (let ci = 1; ci < colsToRender.length; ci++) {
    const x = ci * colW
    lines.push(`  <line x1="${x}" y1="0" x2="${x}" y2="${height}" stroke="#e5e7eb" stroke-width="0.5"/>`)
  }

  lines.push(`</svg>`)
  return lines.join('\n')
}

/**
 * Render a text widget as a minimal SVG (plain text, no markdown parsing).
 * Full markdown rendering requires a browser; this is a best-effort static
 * preview that strips markdown syntax and wraps at word boundaries.
 *
 * @param {object} widget
 * @param {number} width
 * @param {number} height
 * @returns {string}  SVG string
 */
function renderText(widget, width, height, theme = 'light') {
  const content = widget.content || ''
  // Strip markdown headers/bold/italic/links — keep plain text for the preview.
  const plain = content
    .replace(/^#{1,6}\s+/gm, '')
    .replace(/\*\*(.+?)\*\*/g, '$1')
    .replace(/\*(.+?)\*/g, '$1')
    .replace(/\[(.+?)\]\(.+?\)/g, '$1')
    .replace(/`(.+?)`/g, '$1')
    .trim()

  // Word-wrap at ~(width-16)/7.5 chars per line (approximate for 12px sans-serif).
  const charsPerLine = Math.max(20, Math.floor((width - 16) / 7.5))
  const lines = []
  for (const para of plain.split('\n')) {
    if (!para.trim()) { lines.push(''); continue }
    let line = ''
    for (const word of para.split(' ')) {
      if ((line + ' ' + word).trim().length > charsPerLine && line) {
        lines.push(line)
        line = word
      } else {
        line = line ? line + ' ' + word : word
      }
    }
    if (line) lines.push(line)
  }

  const fontSize = 13
  const lineH = fontSize * 1.5
  const maxLines = Math.floor((height - 16) / lineH)
  const rendered = lines.slice(0, maxLines)

  const svgLines = [
    `<svg width="${width}" height="${height}" xmlns="http://www.w3.org/2000/svg">`,
    `  <rect width="${width}" height="${height}" fill="#ffffff"/>`,
  ]
  for (let i = 0; i < rendered.length; i++) {
    const y = 16 + i * lineH
    svgLines.push(
      `  <text x="8" y="${y}" font-family="sans-serif" font-size="${fontSize}" fill="#374151">` +
      `${svgEsc(rendered[i])}</text>`
    )
  }
  svgLines.push(`</svg>`)
  return svgLines.join('\n')
}

/**
 * Render a placeholder SVG for filter or section widgets.
 * These widgets are interactive and have no meaningful static representation.
 *
 * @param {object} widget
 * @param {number} width
 * @param {number} height
 * @returns {string}  SVG string
 */
/**
 * Section header — a title (+ optional subtitle) and a divider rule.
 *
 * Sections used to fall through to renderPlaceholder, so every section on a
 * rendered board read as a literal "[section]" chip. They carry no data and are
 * pure chrome, but they are what makes a board scannable — dropping them makes
 * a thumbnail or PDF look broken rather than minimal.
 */
function renderSection(widget, width, height, theme = 'light') {
  const p = widget.props || {}
  const title = p.title || ''
  const subtitle = p.subtitle || ''
  const align = p.align || 'left'
  const divider = p.divider !== false
  // Resolve, don't trust: a raw `var(--fg)` in fill= paints nothing.
  const color = resolveColor(widget.style?.color, theme, THEME_TOKENS[theme]['--text'])

  const x = align === 'center' ? width / 2 : align === 'right' ? width - 4 : 4
  const anchor = align === 'center' ? 'middle' : align === 'right' ? 'end' : 'start'
  // Baseline: title sits above centre when a subtitle follows it.
  const titleY = subtitle ? height / 2 - 2 : height / 2 + 6
  const parts = [
    `<svg width="${width}" height="${height}" xmlns="http://www.w3.org/2000/svg">`,
  ]
  if (title) {
    parts.push(
      `  <text x="${x}" y="${titleY}" text-anchor="${anchor}"`,
      `    font-family="sans-serif" font-size="18" font-weight="700" fill="${svgEsc(color)}">`,
      `    ${svgEsc(title)}`,
      `  </text>`,
    )
  }
  if (subtitle) {
    parts.push(
      `  <text x="${x}" y="${height / 2 + 18}" text-anchor="${anchor}"`,
      `    font-family="sans-serif" font-size="12" fill="#6b7280">`,
      `    ${svgEsc(subtitle)}`,
      `  </text>`,
    )
  }
  if (divider) {
    parts.push(
      `  <line x1="0" y1="${height - 1}" x2="${width}" y2="${height - 1}"`,
      `    stroke="#e5e7eb" stroke-width="1"/>`,
    )
  }
  parts.push(`</svg>`)
  return parts.join('\n')
}

function renderPlaceholder(widget, width, height, theme = 'light') {
  const tokens = THEME_TOKENS[theme]
  const label = widget.props?.label || widget.target_var || widget.type || 'widget'
  return [
    `<svg width="${width}" height="${height}" xmlns="http://www.w3.org/2000/svg">`,
    `  <rect width="${width}" height="${height}" fill="${svgEsc(tokens['--surface-2'])}" rx="4"`,
    `    stroke="${svgEsc(THEME_TOKENS[theme]['--border'])}" stroke-width="1"/>`,
    `  <text x="${width / 2}" y="${height / 2 + 5}" text-anchor="middle"`,
    `    font-family="sans-serif" font-size="12" fill="${svgEsc(tokens['--text-muted'])}">`,
    `    [${svgEsc(label)}]`,
    `  </text>`,
    `</svg>`,
  ].join('\n')
}

// ---------------------------------------------------------------------------
// WebGL widget detection
// ---------------------------------------------------------------------------

/** Widget subtypes that require WebGL rendering (raster fallback needed). */
const WEBGL_SUBTYPES = new Set(['regl', 'webgl', 'scatter3d', 'globe'])

function isWebGLWidget(widget) {
  return WEBGL_SUBTYPES.has(widget.subtype) || WEBGL_SUBTYPES.has(widget.props?.renderer)
}

// ---------------------------------------------------------------------------
// Main renderer
// ---------------------------------------------------------------------------

/**
 * Render a single widget to an SVG string.
 *
 * @param {object} widget   – Widget descriptor with optional columns/rows data.
 * @returns {{ id: string, svg: string|null, webgl: boolean, error?: string }}
 */
function renderWidget(widget, theme = 'light') {
  const id = widget.id || '?'
  const width = widget.width || 800
  const height = widget.height || 400
  const columns = widget.columns || []
  const rows = widget.rows || []
  const colMap = toColumnMap(columns, rows)

  // WebGL flag — emit placeholder + flag for Python caller to rasterize.
  if (isWebGLWidget(widget)) {
    return { id, svg: null, webgl: true, error: 'webgl_widget_requires_raster_fallback' }
  }

  try {
    let svg
    switch (widget.type) {
      case 'chart':
      case 'metric':
        svg = renderChart(widget, colMap, width, height, theme)
        break
      case 'kpi':
        svg = renderKpi(widget, colMap, width, height, theme)
        break
      case 'table':
      case 'pivot':
        svg = renderTable(widget, colMap, columns, width, height, theme)
        break
      case 'text':
      case 'html':
        svg = renderText(widget, width, height, theme)
        break
      case 'section':
        svg = renderSection(widget, width, height, theme)
        break
      case 'stepper':
        // The Python side normally swaps a stepper for its first step's child
        // before we ever see it (svg_render._resolve_stepper_step), so reaching
        // here means the child could not be resolved — an empty or malformed
        // steps list. A titled placeholder is then the honest answer; drawing
        // the container would show nothing at all.
        svg = renderPlaceholder(widget, width, height, theme)
        break
      case 'filter':
      default:
        // A filter is an interactive control — a placeholder chip is the honest
        // representation in a static render.
        svg = renderPlaceholder(widget, width, height, theme)
        break
    }
    return { id, svg, webgl: false }
  } catch (err) {
    process.stderr.write(`[echarts-ssr] error rendering widget ${id}: ${err.message}\n`)
    return { id, svg: null, webgl: false, error: err.message }
  }
}

// ---------------------------------------------------------------------------
// Stdio protocol: read JSON from stdin, write JSON to stdout
// ---------------------------------------------------------------------------

async function readStdin() {
  const chunks = []
  for await (const chunk of process.stdin) chunks.push(chunk)
  return Buffer.concat(chunks).toString('utf8')
}

async function main() {
  const raw = await readStdin()
  let payload
  try {
    payload = JSON.parse(raw)
  } catch (err) {
    process.stderr.write(`[echarts-ssr] invalid JSON on stdin: ${err.message}\n`)
    process.exit(1)
  }

  const widgets = Array.isArray(payload.widgets) ? payload.widgets : []
  // Board styles are written against theme TOKENS, so a render has to pick a
  // theme — see THEME_TOKENS. Light is the safe default (it matches the
  // composer's white page).
  const theme = payload.theme === 'dark' ? 'dark' : 'light'
  const results = widgets.map(w => renderWidget(w, theme))

  process.stdout.write(JSON.stringify({ widgets: results }) + '\n')
}

main().catch(err => {
  process.stderr.write(`[echarts-ssr] fatal: ${err.message}\n${err.stack}\n`)
  process.exit(1)
})
