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
 *         type: 'chart'|'kpi'|'table'|'text'|'filter'|'metric'|'pivot'|'section'|'html'|'image',
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
  const {
    BarChart, LineChart, ScatterChart, PieChart, HeatmapChart, GaugeChart,
    // Server renders used to fall back to a BAR chart for every type without a
    // local builder, so a thumbnail or an exported PDF showed a treemap as
    // bars. These are the series types the remaining builders below need.
    SankeyChart, FunnelChart, RadarChart, TreemapChart, BoxplotChart,
    CandlestickChart,
  } = await import(resolve(REPO_ROOT, 'node_modules/echarts/charts.js'))
  const {
    GridComponent, TooltipComponent, LegendComponent,
    VisualMapComponent, DataZoomComponent, TitleComponent,
    // A radial bar is bars on a POLAR grid rather than the cartesian one, and
    // this is a tree-shaken echarts build: without PolarComponent registered,
    // setOption throws instead of drawing.
    PolarComponent,
    // A radar chart needs its own coordinate system component, like polar.
    RadarComponent,
  } = await import(resolve(REPO_ROOT, 'node_modules/echarts/components.js'))

  echarts.use([
    SVGRenderer,
    BarChart, LineChart, ScatterChart, PieChart, HeatmapChart, GaugeChart,
    SankeyChart, FunnelChart, RadarChart, TreemapChart, BoxplotChart, CandlestickChart,
    GridComponent, TooltipComponent, LegendComponent,
    VisualMapComponent, DataZoomComponent, TitleComponent,
    PolarComponent, RadarComponent,
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

/**
 * Resolve the colour list a chart should draw with: a widget's own
 * `config.palette` if the author set one, else the generic default.
 * Mirrors `resolvePalette` in embed/widgets/chart-options.js.
 */
function resolvePalette(config) {
  const p = config && config.palette
  return Array.isArray(p) && p.length ? p : PALETTE
}

/**
 * Pivot two parallel arrays into one group per distinct `colorArr` value —
 * the same shape `groupByCategory` in chart-options.js produces, used to turn
 * an `encoding.color`-keyed column into one series per category (grouped
 * bars / multi-line).
 */
function groupByCategory(primary, secondary, colorArr) {
  const groups = new Map()
  const n = Math.min(primary.length, secondary.length)
  for (let i = 0; i < n; i++) {
    const cat = colorArr[i] == null ? '(null)' : String(colorArr[i])
    if (!groups.has(cat)) groups.set(cat, { category: cat, a: [], b: [] })
    const g = groups.get(cat)
    g.a.push(primary[i])
    g.b.push(secondary[i])
  }
  return Array.from(groups.values())
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

function buildBar(colMap, xCol, yCol, colorCol, config = {}) {
  const xData = toStrings(colMap[xCol] || [])
  const yData = toNumbers(colMap[yCol] || [])
  if (!xData.length) return emptyOption()
  const palette = resolvePalette(config)
  const colorData = colorCol ? (colMap[colorCol] || []) : null
  // encoding.color pivots the y values into one bar series per distinct
  // colour value (grouped bars) — mirrors buildBar's colorRaw branch in
  // chart-options.js. Falls through to a single flat series otherwise.
  if (colorData && colorData.length && !isNumericCol(colorData)) {
    const allCats = Array.from(new Set(xData))
    const groups = groupByCategory(xData, yData, colorData)
    const series = groups.map((g, i) => {
      const m = new Map(g.a.map((c, idx) => [String(c), g.b[idx]]))
      return {
        type: 'bar', name: g.category,
        data: allCats.map((c) => (m.has(c) ? m.get(c) : 0)),
        itemStyle: { color: palette[i % palette.length] },
      }
    })
    return { ...baseAxesOption(allCats), color: palette, series }
  }
  return {
    ...baseAxesOption(xData),
    color: palette,
    series: [{ type: 'bar', data: yData, itemStyle: { color: palette[0] } }],
  }
}

function buildLine(colMap, xCol, yCol, area = false, colorCol, config = {}) {
  const xData = toStrings(colMap[xCol] || [])
  const yData = toNumbers(colMap[yCol] || [])
  if (!xData.length) return emptyOption()
  const palette = resolvePalette(config)
  const colorData = colorCol ? (colMap[colorCol] || []) : null
  if (colorData && colorData.length && !isNumericCol(colorData)) {
    const allCats = Array.from(new Set(xData))
    const groups = groupByCategory(xData, yData, colorData)
    const series = groups.map((g, i) => {
      const m = new Map(g.a.map((c, idx) => [String(c), g.b[idx]]))
      const col = palette[i % palette.length]
      return {
        type: 'line', name: g.category,
        data: allCats.map((c) => (m.has(c) ? m.get(c) : null)),
        itemStyle: { color: col }, lineStyle: { color: col },
        areaStyle: area ? { opacity: 0.25, color: col } : undefined,
      }
    })
    return { ...baseAxesOption(allCats), color: palette, series }
  }
  const series = {
    type: 'line', data: yData,
    itemStyle: { color: palette[0] },
    lineStyle: { color: palette[0] },
  }
  if (area) {
    series.areaStyle = { opacity: 0.25, color: palette[0] }
  }
  return { ...baseAxesOption(xData), color: palette, series: [series] }
}

function buildScatter(colMap, xCol, yCol, config = {}) {
  const xData = toNumbers(colMap[xCol] || [])
  const yData = toNumbers(colMap[yCol] || [])
  if (!xData.length) return emptyOption()
  const palette = resolvePalette(config)
  const data = xData.map((x, i) => [x, yData[i]])
  return {
    color: palette,
    backgroundColor: 'transparent',
    animation: false,
    grid: { left: 48, right: 24, top: 36, bottom: 48, containLabel: false },
    xAxis: { type: 'value', axisLabel: { fontSize: 11 } },
    yAxis: { type: 'value', axisLabel: { fontSize: 11 } },
    tooltip: { show: false },
    series: [{ type: 'scatter', data, symbolSize: 6, itemStyle: { color: palette[0] } }],
  }
}

function buildPie(colMap, xCol, yCol, donut = false, config = {}) {
  const names = toStrings(colMap[xCol] || [])
  const vals = toNumbers(colMap[yCol] || [])
  if (!names.length) return emptyOption()
  const palette = resolvePalette(config)
  const pieData = names.map((n, i) => ({ name: n, value: vals[i] }))
  const radius = donut ? ['40%', '65%'] : '65%'
  return {
    color: palette,
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

/**
 * Polar area chart — equal-angle sectors whose RADIUS encodes the value.
 * Mirrors `buildPolar` in embed/widgets/chart-options.js (roseType 'area').
 */
function buildPolar(colMap, xCol, yCol, config = {}) {
  const names = toStrings(colMap[xCol] || [])
  const vals = toNumbers(colMap[yCol] || [])
  if (!names.length) return emptyOption()
  return {
    color: resolvePalette(config),
    backgroundColor: 'transparent',
    animation: false,
    tooltip: { show: false },
    legend: { show: false },
    series: [{
      type: 'pie', roseType: 'area', radius: ['12%', '70%'],
      data: names.map((n, i) => ({ name: n, value: vals[i] })),
      label: { fontSize: 10 }, emphasis: { disabled: true },
    }],
  }
}

/**
 * Radial bar chart — one concentric ring per category, filled to value/max.
 * Mirrors `buildRadialBar` in embed/widgets/chart-options.js, including the
 * reversed category order that puts the first data row on the OUTSIDE.
 */
function buildRadialBar(colMap, xCol, yCol, config = {}) {
  const names = toStrings(colMap[xCol] || []).slice().reverse()
  const vals = toNumbers(colMap[yCol] || []).slice().reverse()
  if (!names.length) return emptyOption()
  const palette = resolvePalette(config)
  const max = (config.yAxis && config.yAxis.max != null) ? config.yAxis.max : (config.max != null ? config.max : 100)
  const min = (config.yAxis && config.yAxis.min != null) ? config.yAxis.min : (config.min != null ? config.min : 0)
  return {
    color: palette,
    backgroundColor: 'transparent',
    animation: false,
    tooltip: { show: false },
    legend: { show: false },
    polar: { center: ['50%', '50%'], radius: ['24%', '82%'] },
    angleAxis: {
      type: 'value', min, max, startAngle: 90, clockwise: true,
      axisLine: { show: false }, axisTick: { show: false },
      axisLabel: { show: false }, splitLine: { show: false },
    },
    radiusAxis: {
      type: 'category', data: names,
      axisLine: { show: false }, axisTick: { show: false }, splitLine: { show: false },
      axisLabel: { fontSize: 10 }, z: 10,
    },
    series: [
      {
        type: 'bar', coordinateSystem: 'polar', silent: true, z: 1,
        roundCap: true, barGap: '-100%',
        itemStyle: { color: 'rgba(148,163,184,0.18)' },
        data: names.map(() => max),
      },
      {
        type: 'bar', coordinateSystem: 'polar', z: 2, roundCap: true, barGap: '-100%',
        data: vals.map((v, i) => ({
          value: v,
          itemStyle: { color: palette[(names.length - 1 - i) % palette.length] },
        })),
        label: { show: true, position: 'middle', fontSize: 10 },
        emphasis: { disabled: true },
      },
    ],
  }
}

/**
 * Sankey — flow between named nodes. Mirrors `buildSankey` in
 * embed/widgets/chart-options.js: nodes are the union of source and target
 * values, links are one per row.
 */
function buildSankey(colMap, enc, config = {}) {
  const src = toStrings(colMap[enc.source || enc.x] || [])
  const tgt = toStrings(colMap[enc.target || enc.color] || [])
  const val = toNumbers(colMap[enc.value || enc.y] || [])
  if (!src.length || !tgt.length) return emptyOption()
  const names = Array.from(new Set([...src, ...tgt]))
  return {
    color: resolvePalette(config),
    backgroundColor: 'transparent',
    animation: false,
    tooltip: { show: false },
    series: [{
      type: 'sankey',
      data: names.map((name) => ({ name })),
      links: src.map((s, i) => ({ source: s, target: tgt[i], value: val[i] || 0 }))
        .filter((l) => l.source && l.target && l.source !== l.target),
      label: { fontSize: 10 },
      emphasis: { disabled: true },
    }],
  }
}

/** Funnel — stages ordered largest-first. */
function buildFunnel(colMap, xCol, yCol, config = {}) {
  const names = toStrings(colMap[xCol] || [])
  const vals = toNumbers(colMap[yCol] || [])
  if (!names.length) return emptyOption()
  return {
    color: resolvePalette(config),
    backgroundColor: 'transparent',
    animation: false,
    tooltip: { show: false },
    legend: { show: false },
    series: [{
      type: 'funnel',
      data: names.map((n, i) => ({ name: n, value: vals[i] })),
      orient: config.orientation === 'horizontal' ? 'horizontal' : 'vertical',
      label: { fontSize: 10 },
      emphasis: { disabled: true },
    }],
  }
}

/**
 * Waterfall — running total built from per-category deltas, drawn as a
 * transparent "support" bar under a visible delta bar.
 */
function buildWaterfall(colMap, xCol, yCol, config = {}) {
  const cats = toStrings(colMap[xCol] || [])
  const deltas = toNumbers(colMap[yCol] || [])
  if (!cats.length) return emptyOption()
  const palette = resolvePalette(config)
  const support = []
  const bars = []
  let running = 0
  for (const d of deltas) {
    support.push(d >= 0 ? running : running + d)
    bars.push(Math.abs(d))
    running += d
  }
  return {
    ...baseAxesOption(cats),
    color: palette,
    series: [
      { type: 'bar', stack: 'wf', silent: true, itemStyle: { color: 'transparent' }, data: support },
      {
        type: 'bar', stack: 'wf',
        data: deltas.map((d, i) => ({
          value: bars[i],
          itemStyle: { color: d >= 0 ? (palette[2] || '#10b981') : (palette[3] || '#ef4444') },
        })),
      },
    ],
  }
}

/** Heatmap — x × y matrix coloured by value. */
function buildHeatmap(colMap, enc, config = {}) {
  const xs = toStrings(colMap[enc.x] || [])
  const ys = toStrings(colMap[enc.color || enc.y] || [])
  const vals = toNumbers(colMap[enc.value || enc.y] || [])
  if (!xs.length || !ys.length) return emptyOption()
  const xCats = Array.from(new Set(xs))
  const yCats = Array.from(new Set(ys))
  const palette = resolvePalette(config)
  const data = xs.map((x, i) => [xCats.indexOf(x), yCats.indexOf(ys[i]), vals[i] ?? 0])
  const max = Math.max(1, ...vals)
  return {
    color: palette,
    backgroundColor: 'transparent',
    animation: false,
    tooltip: { show: false },
    grid: { left: 70, right: 24, top: 16, bottom: 48, containLabel: false },
    xAxis: { type: 'category', data: xCats, axisLabel: { fontSize: 10 } },
    yAxis: { type: 'category', data: yCats, axisLabel: { fontSize: 10 } },
    visualMap: {
      show: false, min: 0, max,
      inRange: { color: [config.lowColor || '#e5e7eb', config.highColor || palette[0]] },
    },
    series: [{ type: 'heatmap', data, label: { show: false }, emphasis: { disabled: true } }],
  }
}

/** Radar — one axis per indicator, one polygon per group (or one overall). */
function buildRadar(colMap, enc, config = {}) {
  const indicators = toStrings(colMap[enc.x] || [])
  const vals = toNumbers(colMap[enc.y] || [])
  if (!indicators.length) return emptyOption()
  const colorArr = enc.color ? colMap[enc.color] : null
  const groups = colorArr
    ? groupByCategory(indicators, vals, colorArr).map((g) => ({ name: g.category, indicators: g.a, values: g.b }))
    : [{ name: enc.y || 'series', indicators, values: vals }]
  const max = Math.max(...vals, 1) * 1.1
  return {
    color: resolvePalette(config),
    backgroundColor: 'transparent',
    animation: false,
    tooltip: { show: false },
    legend: { show: false },
    radar: {
      indicator: groups[0].indicators.map((name) => ({ name, max })),
      axisName: { fontSize: 10 },
      center: ['50%', '54%'], radius: '62%',
    },
    series: [{
      type: 'radar',
      data: groups.map((g) => ({ name: g.name, value: g.values, areaStyle: { opacity: 0.12 } })),
      emphasis: { disabled: true },
    }],
  }
}

/** Treemap — one rectangle per category, sized by value. */
function buildTreemap(colMap, xCol, yCol, config = {}) {
  const names = toStrings(colMap[xCol] || [])
  const vals = toNumbers(colMap[yCol] || [])
  if (!names.length) return emptyOption()
  return {
    color: resolvePalette(config),
    backgroundColor: 'transparent',
    animation: false,
    tooltip: { show: false },
    series: [{
      type: 'treemap',
      data: names.map((n, i) => ({ name: n, value: vals[i] })),
      roam: false, breadcrumb: { show: false },
      label: { fontSize: 10 },
      emphasis: { disabled: true },
    }],
  }
}

/** Box plot — five-number summary per category, computed from raw values. */
function buildBoxplot(colMap, xCol, yCol, config = {}) {
  const cats = toStrings(colMap[xCol] || [])
  const vals = toNumbers(colMap[yCol] || [])
  if (!cats.length) return emptyOption()
  const byCat = new Map()
  for (let i = 0; i < cats.length; i++) {
    if (!byCat.has(cats[i])) byCat.set(cats[i], [])
    byCat.get(cats[i]).push(vals[i])
  }
  const names = Array.from(byCat.keys())
  const boxes = names.map((n) => {
    const arr = byCat.get(n).slice().sort((a, b) => a - b)
    const q = (p) => arr[Math.min(arr.length - 1, Math.max(0, Math.round((arr.length - 1) * p)))]
    return [arr[0], q(0.25), q(0.5), q(0.75), arr[arr.length - 1]]
  })
  return {
    ...baseAxesOption(names),
    color: resolvePalette(config),
    series: [{ type: 'boxplot', data: boxes }],
  }
}

/** Candlestick — OHLC per period. */
function buildCandlestick(colMap, enc, config = {}) {
  const dates = toStrings(colMap[enc.x] || [])
  const open = toNumbers(colMap[enc.open] || [])
  const close = toNumbers(colMap[enc.close] || [])
  const low = toNumbers(colMap[enc.low] || [])
  const high = toNumbers(colMap[enc.high] || [])
  if (!dates.length) return emptyOption()
  return {
    ...baseAxesOption(dates),
    color: resolvePalette(config),
    series: [{
      type: 'candlestick',
      data: dates.map((_, i) => [open[i], close[i], low[i], high[i]]),
    }],
  }
}

/**
 * Fan chart — a forecast midline inside a confidence band, drawn as a
 * transparent lower bound stacked under a translucent band.
 */
function buildFan(colMap, enc, config = {}) {
  const xs = toStrings(colMap[enc.x] || [])
  const mid = toNumbers(colMap[enc.y] || [])
  if (!xs.length) return emptyOption()
  const palette = resolvePalette(config)
  const lower = enc.lower ? toNumbers(colMap[enc.lower] || []) : null
  const upper = enc.upper ? toNumbers(colMap[enc.upper] || []) : null
  const series = []
  if (lower && upper) {
    series.push(
      { type: 'line', stack: 'fan', silent: true, symbol: 'none', lineStyle: { opacity: 0 }, data: lower },
      {
        type: 'line', stack: 'fan', silent: true, symbol: 'none', lineStyle: { opacity: 0 },
        areaStyle: { color: palette[0], opacity: 0.18 },
        data: upper.map((u, i) => u - (lower[i] ?? 0)),
      },
    )
  }
  series.push({ type: 'line', symbol: 'none', smooth: true, lineStyle: { width: 2, color: palette[0] }, data: mid })
  return { ...baseAxesOption(xs), color: palette, series }
}

function buildHBar(colMap, xCol, yCol, config = {}) {
  const catData = toStrings(colMap[xCol] || [])
  const valData = toNumbers(colMap[yCol] || [])
  if (!catData.length) return emptyOption()
  const palette = resolvePalette(config)
  return {
    color: palette,
    backgroundColor: 'transparent',
    animation: false,
    grid: { left: 80, right: 24, top: 24, bottom: 36, containLabel: false },
    xAxis: { type: 'value', axisLabel: { fontSize: 11 } },
    yAxis: { type: 'category', data: catData, axisLabel: { fontSize: 11 } },
    tooltip: { show: false },
    series: [{ type: 'bar', data: valData, itemStyle: { color: palette[0] } }],
  }
}

/**
 * Bar+line combo: one or more bar series plus one or more line series
 * sharing a category x-axis. Mirrors buildCombo in chart-options.js, minus
 * the dual-axis title/gap fine-tuning — not worth it at thumbnail scale.
 *   encoding: { x, bars: ['colA', ...], lines: ['colB', ...] }
 */
function buildCombo(colMap, enc, config = {}) {
  const xCol = enc.x || ''
  const xData = toStrings(colMap[xCol] || [])
  const barCols = Array.isArray(enc.bars) ? enc.bars : (enc.bars ? [enc.bars] : [])
  const lineCols = Array.isArray(enc.lines) ? enc.lines : (enc.lines ? [enc.lines] : [])
  if (!xData.length || (!barCols.length && !lineCols.length)) return emptyOption()

  const palette = resolvePalette(config)
  const stacked = config.stack === true || config.stack === 'normal' || config.stack === 'percent'
  const barSeries = barCols.map((col, i) => ({
    type: 'bar', name: col, yAxisIndex: 0,
    data: toNumbers(colMap[col] || []),
    stack: stacked ? 'total' : undefined,
    itemStyle: { color: palette[i % palette.length] },
  }))
  const lineSeries = lineCols.map((col, i) => ({
    type: 'line', name: col, yAxisIndex: barCols.length && lineCols.length ? 1 : 0,
    data: toNumbers(colMap[col] || []),
    itemStyle: { color: palette[(barCols.length + i) % palette.length] },
    lineStyle: { color: palette[(barCols.length + i) % palette.length], width: 2 },
  }))

  const option = {
    ...baseAxesOption(xData),
    color: palette,
    series: [...barSeries, ...lineSeries],
  }
  // A second value axis for the lines when bars are also present, so a
  // rate-vs-volume combo doesn't squash one series flat against the other's
  // scale.
  if (barCols.length && lineCols.length) {
    option.yAxis = [option.yAxis, { type: 'value', axisLabel: { fontSize: 11 } }]
  }
  return option
}

function buildGauge(colMap, valueCol, props = {}, config = {}) {
  const vals = toNumbers(colMap[valueCol] || [])
  const value = vals[0] ?? 0
  const max = props.max ?? config.max ?? Math.max(value * 1.5, 100)
  const min = props.min ?? config.min ?? 0
  const palette = resolvePalette(config)
  // 'ring' — radial progress ring (Apex radialBar look), mirroring the
  // browser builder in embed/widgets/chart-options.js so thumbnails match.
  if (config.gaugeStyle === 'ring') {
    const ringColor = palette[0]
    return {
      backgroundColor: 'transparent',
      animation: false,
      series: [{
        type: 'gauge', min, max,
        startAngle: 90, endAngle: -270,
        progress: { show: true, width: 12, roundCap: true, itemStyle: { color: ringColor } },
        axisLine: { lineStyle: { width: 12, color: [[1, 'rgba(148,163,184,0.18)']] } },
        pointer: { show: false },
        axisTick: { show: false },
        splitLine: { show: false },
        axisLabel: { show: false },
        detail: { fontSize: 18, fontWeight: 700, offsetCenter: [0, 0],
                  formatter: (v) => `${Number(v).toFixed(2)}%` },
        title: { show: false },
        data: [{ value: Math.max(min, Math.min(max, value)) }],
      }],
    }
  }
  return {
    color: palette,
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
  const config = widget.config || {}
  const xCol = enc.x || ''
  const yCol = enc.y || ''
  const colorCol = enc.color || ''

  if (!Object.keys(colMap).length) return emptyOption()

  let option
  switch (chartType) {
    case 'bar':     option = buildBar(colMap, xCol, yCol, colorCol, config); break
    case 'line':    option = buildLine(colMap, xCol, yCol, false, colorCol, config); break
    case 'area':    option = buildLine(colMap, xCol, yCol, true, colorCol, config); break
    case 'scatter': option = buildScatter(colMap, xCol, yCol, config); break
    case 'pie':     option = buildPie(colMap, xCol, yCol, false, config); break
    case 'donut':   option = buildPie(colMap, xCol, yCol, true, config); break
    case 'polar':     option = buildPolar(colMap, xCol, yCol, config); break
    case 'radialbar': option = buildRadialBar(colMap, xCol, yCol, config); break
    case 'sankey':      option = buildSankey(colMap, enc, config); break
    case 'funnel':      option = buildFunnel(colMap, xCol, yCol, config); break
    case 'waterfall':   option = buildWaterfall(colMap, xCol, yCol, config); break
    case 'heatmap':     option = buildHeatmap(colMap, enc, config); break
    case 'radar':       option = buildRadar(colMap, enc, config); break
    case 'treemap':     option = buildTreemap(colMap, xCol, yCol, config); break
    case 'boxplot':     option = buildBoxplot(colMap, xCol, yCol, config); break
    case 'candlestick': option = buildCandlestick(colMap, enc, config); break
    case 'fan':         option = buildFan(colMap, enc, config); break
    // A bubble is a scatter whose points are sized by `encoding.size`; the
    // sizing is a browser-side nicety, the silhouette is what matters here.
    case 'bubble':      option = buildScatter(colMap, xCol, yCol, config); break
    // 'map' has no server-side geometry registry, so there is no honest
    // choropleth to draw. Fall through to the placeholder rather than emit a
    // bar chart, which is what every unlisted type used to become.
    case 'map':         option = null; break
    case 'hbar':    option = buildHBar(colMap, xCol, yCol, config); break
    case 'gauge':   option = buildGauge(colMap, enc.value || yCol, props, config); break
    case 'combo':   option = buildCombo(colMap, enc, config); break
    default:        option = buildBar(colMap, xCol, yCol, colorCol, config)
  }
  // A legend only earns its space when there's more than one named series to
  // tell apart (grouped bars/lines, a combo) — a single-series chart with
  // config.legend:true would otherwise show one lonely swatch.
  if (config.legend && Array.isArray(option.series) && option.series.length > 1) {
    option.legend = { top: 0, textStyle: { fontSize: 10 } }
  }
  return option
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
  // A chart type with no server-side equivalent (currently only 'map', which
  // needs a GeoJSON registry this process does not have) returns null. A
  // labelled placeholder is the honest answer; drawing some other chart in its
  // place would be a confidently wrong picture.
  if (!option) return renderPlaceholder(widget, width, height, theme)
  // A chart draws to a canvas/SVG of its own, so it can't inherit CSS the way an
  // HTML widget would: axis + legend text must be told the theme explicitly, or
  // a dark tile gets near-black labels on it.
  const skin = widgetSkin(widget, theme, { bgFallback: 'transparent', fgFallback: null })
  const tokens = THEME_TOKENS[theme]
  const textColor = skin.fg || tokens['--text']
  // Axis and legend text is the `muted` role; the chart title is `title`. This
  // mirrors ChartWidget, which feeds the same two roles into theme.fg /
  // theme.fgMuted — a server render that used one colour for both would not
  // match the board it is a picture of.
  const axisColor = skin.muted || textColor
  const titleColor = skin.title || textColor
  option.backgroundColor = skin.bg || 'transparent'
  option.textStyle = { ...(option.textStyle || {}), color: textColor }

  // A chart's `config.title` / `config.subtitle` were dropped entirely by the
  // server builders, so a titled chart came out of a thumbnail or a PDF
  // untitled. Emitted here, once, rather than in each per-type builder — the
  // title is chrome around the plot, not part of any series.
  const cfg = widget.config || {}
  const titleText = typeof cfg.title === 'string' ? cfg.title : cfg.title?.text
  const subtitleText = typeof cfg.subtitle === 'string' ? cfg.subtitle : undefined
  if (titleText || subtitleText) {
    option.title = {
      text: titleText || '',
      subtext: subtitleText || '',
      left: 4,
      top: 2,
      textStyle: { color: titleColor, fontSize: 13, fontWeight: 600 },
      subtextStyle: { color: axisColor, fontSize: 11 },
    }
    // Give the plot room for it, the way baseFrame does in the browser builder.
    if (option.grid) {
      option.grid = { ...option.grid, top: Math.max(option.grid.top ?? 0, subtitleText ? 56 : 36) }
    }
  }
  // `radiusAxis` carries the ring labels on a radial bar, which are the only
  // text on that chart — leaving it out would render them near-black on a dark
  // tile exactly the way the cartesian axes used to.
  for (const axis of ['xAxis', 'yAxis', 'radiusAxis', 'angleAxis']) {
    if (option[axis]) {
      option[axis] = {
        ...option[axis],
        axisLabel: { ...(option[axis].axisLabel || {}), color: axisColor },
        axisLine: { ...(option[axis].axisLine || {}), lineStyle: { color: tokens['--border'] } },
        splitLine: { ...(option[axis].splitLine || {}), lineStyle: { color: tokens['--border'] } },
      }
    }
  }
  if (option.legend) option.legend = { ...option.legend, textStyle: { color: axisColor } }
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

/**
 * `widget.style.background` can be a plain CSS colour string OR a structured
 * descriptor `{type:'solid'|'gradient'|'transparent'|'image'|'css', color, from, to}`
 * — see src/dashboards/widgetHtml.ts:backgroundToCss, which is what the live
 * app's card wrapper actually reads. `resolveColor` alone only understands the
 * string shape; handed a descriptor object it stringifies to "[object Object]",
 * an invalid SVG fill that renders as opaque black. Unwrap the descriptor to a
 * flat colour first (same class of bug as the missing `Widget.config`/
 * `DashboardSpec.background` fields — see nubi-serverside-board-render memory).
 */
function resolveBackgroundDescriptor(bg, theme, fallback) {
  if (bg == null) return fallback
  if (typeof bg !== 'object') return resolveColor(bg, theme, fallback)
  switch (bg.type) {
    case 'transparent':
    case 'none':
      return 'transparent'
    case 'solid':
      return bg.color ? resolveColor(bg.color, theme, fallback) : fallback
    case 'gradient':
      // A flat SVG rect fill can't render a CSS gradient without a
      // <linearGradient> def — approximate with its start colour (mirrors
      // svg_render.py's resolve_page_bg for DashboardSpec.background).
      return bg.from ? resolveColor(bg.from, theme, fallback) : fallback
    // 'image' / 'css': no honest flat-colour equivalent — fall back rather
    // than guess at a wrong paint.
    default:
      return fallback
  }
}

/** The widget's own background + text colour, resolved for *theme*. */
function widgetSkin(widget, theme, { bgFallback = '#f9fafb', fgFallback = '#1f2937' } = {}) {
  const st = widget.style || {}
  const bg = st.background ?? st.backgroundColor
  const fg = resolveColor(st.color, theme, fgFallback)
  return {
    bg: resolveBackgroundDescriptor(bg, theme, bgFallback),
    fg,
    // Per-role text colours, mirroring src/dashboards/textColors.js.
    //
    // Both roles fall back to the BODY colour before the theme, which is what
    // keeps the existing behaviour intact: a card that bakes a dark background
    // and one light text colour still gets light headings and light axis
    // labels, rather than the theme's muted grey disappearing into the tile.
    // Only a card that authors nothing at all reaches the theme token.
    title: resolveColor(st.titleColor, theme, null) ?? fg,
    muted: resolveColor(st.mutedColor, theme, null) ?? fg ?? THEME_TOKENS[theme]['--text-muted'],
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
  // The caption is the `muted` text role — authorable in Appearance, and
  // falling back to the theme's muted token rather than a hard-coded grey that
  // would fight a dark tile.
  const labelFg = skin.muted
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

  // `decimals` overrides each format's default precision, matching
  // KpiWidget.formatValue — a server render that rounds differently from the
  // browser makes an exported report disagree with the board it came from.
  const dp = Number.isInteger(props.decimals) && props.decimals >= 0 ? props.decimals : null

  let displayVal
  if (rawVal === null || rawVal === undefined || rawVal === '') displayVal = '--'
  else if (!isNum) displayVal = String(rawVal)
  else if (fmt === 'currency') displayVal = '$' + num.toLocaleString(undefined, dp == null ? undefined : { minimumFractionDigits: dp, maximumFractionDigits: dp })
  else if (fmt === 'percent') displayVal = (num * 100).toFixed(dp ?? 1) + '%'
  else if (fmt === 'percent100') displayVal = num.toFixed(dp ?? 1) + '%'
  else if (fmt === 'comma') displayVal = num.toLocaleString(undefined, { minimumFractionDigits: dp ?? 0, maximumFractionDigits: dp ?? 0 })
  else displayVal = num.toLocaleString(undefined, dp == null ? undefined : { maximumFractionDigits: dp })
  if (displayVal !== '--') displayVal = `${props.prefix ?? ''}${displayVal}${props.suffix ?? ''}`

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

  // Was hard-coded to white-bg/dark-text regardless of the widget's own
  // style — every custom-styled text/html tile (a common way to author a
  // dashboard's title card) rendered as a plain white box, clashing with a
  // dark or custom-background board around it. Same widgetSkin resolution
  // renderKpi/renderTable already use.
  const skin = widgetSkin(widget, theme, {
    bgFallback: THEME_TOKENS[theme]['--surface'], fgFallback: THEME_TOKENS[theme]['--text'],
  })

  const svgLines = [
    `<svg width="${width}" height="${height}" xmlns="http://www.w3.org/2000/svg">`,
    `  <rect width="${width}" height="${height}" fill="${svgEsc(skin.bg)}"/>`,
  ]
  for (let i = 0; i < rendered.length; i++) {
    const y = 16 + i * lineH
    svgLines.push(
      `  <text x="8" y="${y}" font-family="sans-serif" font-size="${fontSize}" fill="${svgEsc(skin.fg)}">` +
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
  const skin = widgetSkin(widget, theme, { bgFallback: null, fgFallback: THEME_TOKENS[theme]['--text'] })
  const color = skin.title
  const subColor = skin.muted

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
      `    font-family="sans-serif" font-size="12" fill="${svgEsc(subColor)}">`,
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

/**
 * Render a `divider` widget — a rule filling its tile's long axis.
 *
 * Kept as a hand-drawn <line> rather than a placeholder chip because a divider
 * is one of the most common widgets on a migrated board (the legacy estate
 * uses one under nearly every header strip); a grey "[divider]" box in its
 * place is far more visually wrong than the rule itself.
 */
function renderDivider(widget, width, height, theme = 'light') {
  const p = widget.props || {}
  const vertical = p.orientation === 'vertical'
  const thickness = Math.max(1, Number(p.thickness) || 1)
  const inset = Math.max(0, Number(p.inset) || 0)
  const opacity = p.opacity == null ? 1 : Math.min(1, Math.max(0, Number(p.opacity)))
  const color = resolveColor(p.color, theme, null)
    || resolveColor(widget.style?.color, theme, null)
    || THEME_TOKENS[theme]['--border']
  // SVG has no border-style, but stroke-dasharray covers the same three cases.
  const dash = p.lineStyle === 'dashed' ? ` stroke-dasharray="${thickness * 6},${thickness * 4}"`
    : p.lineStyle === 'dotted' ? ` stroke-dasharray="${thickness},${thickness * 2}" stroke-linecap="round"`
    : ''
  const coords = vertical
    ? { x1: width / 2, y1: inset, x2: width / 2, y2: Math.max(inset, height - inset) }
    : { x1: inset, y1: height / 2, x2: Math.max(inset, width - inset), y2: height / 2 }
  return [
    `<svg width="${width}" height="${height}" xmlns="http://www.w3.org/2000/svg">`,
    `  <line x1="${coords.x1}" y1="${coords.y1}" x2="${coords.x2}" y2="${coords.y2}"`,
    `    stroke="${svgEsc(color)}" stroke-width="${thickness}" opacity="${opacity}"${dash}/>`,
    `</svg>`,
  ].join('\n')
}

/**
 * Render a `datedisplay` widget — an optional eyebrow label above a caption.
 *
 * Only the `static` source has text a server-side render can know: `variable`
 * depends on live filter state that does not exist here, and `query` values
 * arrive through colMap. Both of those are handled by the caller; anything
 * still unresolved renders as an em dash, matching the browser widget's own
 * empty state rather than inventing a value.
 */
function renderDateDisplay(widget, colMap, width, height, theme = 'light') {
  const p = widget.props || {}
  const tokens = THEME_TOKENS[theme]
  const skin = widgetSkin(widget, theme, { bgFallback: null, fgFallback: tokens['--text'] })
  const color = skin.fg
  const align = p.align || 'left'
  const x = align === 'center' ? width / 2 : align === 'right' ? width - 8 : 8
  const anchor = align === 'center' ? 'middle' : align === 'right' ? 'end' : 'start'

  let value = ''
  if (p.source === 'static') value = String(p.text ?? '')
  else if (p.source === 'query') {
    const col = widget.encoding?.value
    const arr = col ? colMap[col] : null
    const row = Number.isInteger(p.row) ? p.row : 0
    if (arr && arr.length) value = String(arr[Math.min(row, arr.length - 1)] ?? '')
  }
  const text = `${p.prefix ?? ''}${value || '—'}${p.suffix ?? ''}`

  const parts = [`<svg width="${width}" height="${height}" xmlns="http://www.w3.org/2000/svg">`]
  const valueY = p.label ? height / 2 + 10 : height / 2 + 5
  if (p.label) {
    parts.push(
      `  <text x="${x}" y="${height / 2 - 6}" text-anchor="${anchor}"`,
      `    font-family="sans-serif" font-size="10" letter-spacing="0.5" fill="${svgEsc(skin.muted)}">`,
      `    ${svgEsc(String(p.label).toUpperCase())}`,
      `  </text>`,
    )
  }
  parts.push(
    `  <text x="${x}" y="${valueY}" text-anchor="${anchor}"`,
    `    font-family="sans-serif" font-size="14" font-weight="600" fill="${svgEsc(color)}">`,
    `    ${svgEsc(text)}`,
    `  </text>`,
    `</svg>`,
  )
  return parts.join('\n')
}

/**
 * Render a `breadcrumb` widget as its resting state: the Home chip alone.
 *
 * A drill path is session state — nothing has been drilled into at render
 * time — so Home is not an approximation, it IS what the widget shows when the
 * board is opened, which is exactly what a thumbnail or a PDF depicts.
 */
function renderBreadcrumb(widget, width, height, theme = 'light') {
  const p = widget.props || {}
  const tokens = THEME_TOKENS[theme]
  const label = p.homeLabel || 'All'
  const chipColor = resolveColor(p.chipColor, theme, null) || tokens['--border']
  const filled = p.chipVariant !== 'outlined'
  const cy = height / 2
  const chipH = Math.min(26, Math.max(18, height - 8))
  const chipW = Math.min(width - 16, 28 + String(label).length * 7)
  return [
    `<svg width="${width}" height="${height}" xmlns="http://www.w3.org/2000/svg">`,
    `  <rect x="8" y="${cy - chipH / 2}" width="${chipW}" height="${chipH}" rx="${chipH / 2}"`,
    `    fill="${filled ? svgEsc(chipColor) : 'none'}" stroke="${svgEsc(chipColor)}" stroke-width="1"/>`,
    `  <text x="${8 + chipW / 2}" y="${cy + 4}" text-anchor="middle"`,
    `    font-family="sans-serif" font-size="11" font-weight="600"`,
    `    fill="${filled ? '#ffffff' : svgEsc(chipColor)}">`,
    `    ${svgEsc(label)}`,
    `  </text>`,
    `</svg>`,
  ].join('\n')
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

/**
 * A grid-placed filter control, drawn as a chip: a funnel glyph + its label.
 *
 * `renderPlaceholder`'s bracketed text (e.g. "[COUNTRY]") is legible at the
 * widget's own render size, but a thumbnail card shrinks the whole 1000px-wide
 * page down to a few hundred CSS px — a further ~3x on top of that. 12px text
 * survives the first shrink and vanishes in the second; a viewer sees a blank
 * grey box and reasonably reports "the filters aren't showing". A shape holds
 * up under that scale-down the way fine text strokes don't, so a real filter
 * bar reads as "there are filter controls here" at a glance even illegible.
 */
function renderFilterChip(widget, width, height, theme = 'light') {
  const tokens = THEME_TOKENS[theme]
  const label = widget.props?.label || widget.target_var || 'Filter'
  const cy = height / 2
  const iconSize = Math.max(10, Math.min(18, height * 0.35))
  const iconX = Math.max(8, height * 0.2)
  const barWidths = [iconSize, iconSize * 0.65, iconSize * 0.3]
  const bars = barWidths
    .map((w, i) => {
      const y = cy - iconSize / 2 + i * (iconSize / 2.4)
      const x = iconX + (iconSize - w) / 2
      return `  <rect x="${x.toFixed(1)}" y="${y.toFixed(1)}" width="${w.toFixed(1)}" height="2" rx="1" fill="${svgEsc(tokens['--text-muted'])}"/>`
    })
    .join('\n')
  const textX = iconX + iconSize + 10
  return [
    `<svg width="${width}" height="${height}" xmlns="http://www.w3.org/2000/svg">`,
    `  <rect width="${width}" height="${height}" fill="${svgEsc(tokens['--surface-2'])}" rx="${Math.min(height / 2, 8)}"`,
    `    stroke="${svgEsc(tokens['--border'])}" stroke-width="1"/>`,
    bars,
    `  <text x="${textX}" y="${cy + 4}" text-anchor="start"`,
    `    font-family="sans-serif" font-size="12" font-weight="600" fill="${svgEsc(tokens['--text-muted'])}">`,
    `    ${svgEsc(label)}`,
    `  </text>`,
    `</svg>`,
  ].join('\n')
}

/**
 * Render an `image` widget (logos, header art) as an embedded <image>.
 *
 * The Python side inlines the actual bytes as a data: URI before this ever
 * runs (app.dashboards.svg_render._resolve_image_data_uri) — a standalone SVG
 * has no origin to resolve `/api/v1/images/...` against, and Node never
 * fetches it either. When that resolution failed or was skipped (an external
 * URL, a missing image), `props.url` is not a data: URI and there is nothing
 * safe to embed — fall back to the same placeholder chip every other
 * undrawable widget gets, rather than an empty or broken <image>.
 */
function renderImage(widget, width, height, theme = 'light') {
  const props = widget.props || {}
  const url = String(props.url || '')
  if (!url.startsWith('data:')) return renderPlaceholder(widget, width, height, theme)

  const skin = widgetSkin(widget, theme, { bgFallback: null, fgFallback: null })
  const preserveAspectRatio =
    props.fit === 'cover' ? 'xMidYMid slice' : props.fit === 'fill' ? 'none' : 'xMidYMid meet'
  const parts = [`<svg width="${width}" height="${height}" xmlns="http://www.w3.org/2000/svg">`]
  if (skin.bg) {
    parts.push(
      `  <rect width="${width}" height="${height}" fill="${svgEsc(skin.bg)}"${
        skin.border ? ` stroke="${svgEsc(skin.border)}" stroke-width="1"` : ''}/>`,
    )
  }
  parts.push(
    `  <image href="${svgEsc(url)}" x="0" y="0" width="${width}" height="${height}"`,
    `    preserveAspectRatio="${preserveAspectRatio}"/>`,
    `</svg>`,
  )
  return parts.join('\n')
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
      case 'image':
        svg = renderImage(widget, width, height, theme)
        break
      case 'divider':
        svg = renderDivider(widget, width, height, theme)
        break
      case 'datedisplay':
        svg = renderDateDisplay(widget, colMap, width, height, theme)
        break
      case 'breadcrumb':
        svg = renderBreadcrumb(widget, width, height, theme)
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
        svg = renderFilterChip(widget, width, height, theme)
        break
      default:
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
