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

// Resolve echarts from the repo root node_modules so this script works whether
// called from scripts/render/ or from the repo root.
import { fileURLToPath } from 'url'
import { dirname, resolve } from 'path'
import { createReadStream } from 'fs'
import { createInterface } from 'readline'

const __dirname = dirname(fileURLToPath(import.meta.url))
const REPO_ROOT = resolve(__dirname, '../..')

// Dynamic import of echarts — lets us emit a clear install error if missing.
let echartsInit
try {
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
function renderChart(widget, colMap, width, height) {
  const option = buildChartOption(widget, colMap)
  const chart = echartsInit(null, null, { renderer: 'svg', ssr: true, width, height })
  try {
    chart.setOption(option)
    return chart.renderToSVGString()
  } finally {
    chart.dispose()
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
function renderKpi(widget, colMap, width, height) {
  const enc = widget.encoding || {}
  const props = widget.props || {}
  const valueCol = enc.value || enc.y || enc.x || Object.keys(colMap)[0] || ''
  const label = props.label || valueCol.replace(/_/g, ' ')

  // Get first value
  const vals = colMap[valueCol] || []
  const rawVal = vals[0] ?? ''
  const fmt = props.format || ''
  let displayVal = rawVal === null || rawVal === undefined ? '--' : String(rawVal)
  if (fmt === 'currency') displayVal = '$' + Number(rawVal).toLocaleString()
  else if (fmt === 'percent') displayVal = Number(rawVal).toFixed(1) + '%'
  else if (typeof rawVal === 'number') displayVal = rawVal.toLocaleString()

  const cx = width / 2
  const valueFontSize = Math.min(48, Math.floor(height * 0.35))
  const labelFontSize = Math.min(16, Math.floor(height * 0.12))

  return [
    `<svg width="${width}" height="${height}" xmlns="http://www.w3.org/2000/svg">`,
    `  <rect width="${width}" height="${height}" fill="#f9fafb" rx="8"/>`,
    `  <text x="${cx}" y="${Math.floor(height * 0.52)}" text-anchor="middle"`,
    `    font-family="sans-serif" font-size="${valueFontSize}" font-weight="700" fill="#1f2937">`,
    `    ${svgEsc(displayVal)}`,
    `  </text>`,
    `  <text x="${cx}" y="${Math.floor(height * 0.75)}" text-anchor="middle"`,
    `    font-family="sans-serif" font-size="${labelFontSize}" fill="#6b7280">`,
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
function renderTable(widget, colMap, columns, width, height) {
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
    `  <rect width="${width}" height="${height}" fill="#ffffff"/>`,
    // Header row background
    `  <rect x="0" y="0" width="${width}" height="${headerH}" fill="#f3f4f6"/>`,
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
function renderText(widget, width, height) {
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
function renderPlaceholder(widget, width, height) {
  const label = widget.props?.label || widget.target_var || widget.type || 'widget'
  return [
    `<svg width="${width}" height="${height}" xmlns="http://www.w3.org/2000/svg">`,
    `  <rect width="${width}" height="${height}" fill="#f3f4f6" rx="4"`,
    `    stroke="#e5e7eb" stroke-width="1"/>`,
    `  <text x="${width / 2}" y="${height / 2 + 5}" text-anchor="middle"`,
    `    font-family="sans-serif" font-size="12" fill="#9ca3af">`,
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
function renderWidget(widget) {
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
        svg = renderChart(widget, colMap, width, height)
        break
      case 'kpi':
        svg = renderKpi(widget, colMap, width, height)
        break
      case 'table':
      case 'pivot':
        svg = renderTable(widget, colMap, columns, width, height)
        break
      case 'text':
      case 'html':
        svg = renderText(widget, width, height)
        break
      case 'filter':
      case 'section':
      default:
        svg = renderPlaceholder(widget, width, height)
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
  const results = widgets.map(renderWidget)

  process.stdout.write(JSON.stringify({ widgets: results }) + '\n')
}

main().catch(err => {
  process.stderr.write(`[echarts-ssr] fatal: ${err.message}\n${err.stack}\n`)
  process.exit(1)
})
