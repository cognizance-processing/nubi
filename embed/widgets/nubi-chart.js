/**
 * nubi-chart.js — <nubi-chart> ECharts-powered chart widget (EDITOR-2B).
 *
 * ATTRIBUTES
 * ----------
 * query-id   (required) Registered query id.
 * type       Chart type: "scatter" | "line" | "bar" | "area" | "pie". Default: "scatter".
 * x          Column name for X axis (required for scatter/line/bar/area).
 * y          Column name for Y axis (required).
 * color      Optional column name for per-series categorical colour grouping.
 * token      Static JWT string.
 * get-token  Name of a window function returning Promise<string>|string.
 * backend    Base URL of Nubi API. Defaults to http://localhost:8000.
 *
 * RENDERER
 * --------
 * All chart types use Apache ECharts (canvas renderer).
 * For n > 100k points, 'lttb' sampling is enabled automatically.
 * The widget renders inside shadow DOM so host styles don't bleed in.
 *
 * EVENTS
 * ------
 * nubi:widget-ready  { rows, renderer: 'echarts' }
 * nubi:widget-error  { message }
 *
 * CSS CUSTOM PROPERTIES
 * ---------------------
 * --nubi-bg, --nubi-fg, --nubi-accent, --nubi-border
 */

import * as echarts from 'echarts'
import { resolveToken, fetchArrow, makeSampleTableData, BASE_STYLES } from './shared.js'

// ---------------------------------------------------------------------------
// Styles
// ---------------------------------------------------------------------------

const CHART_STYLES = /* css */ `
  ${BASE_STYLES}

  :host {
    min-height: 220px;
  }

  .chart-wrap {
    display: flex;
    flex-direction: column;
    width: 100%;
    height: 100%;
    box-sizing: border-box;
  }

  .chart-toolbar {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 7px 12px;
    background: var(--nubi-accent, #1e2433);
    border-bottom: 1px solid var(--nubi-border, #2d3748);
    font-size: 11px;
    gap: 8px;
    flex-shrink: 0;
  }

  .chart-title {
    font-weight: 600;
    letter-spacing: 0.02em;
    opacity: 0.75;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    flex: 1;
  }

  .chart-body {
    flex: 1;
    position: relative;
    overflow: hidden;
    min-height: 160px;
  }

  .echarts-container {
    position: absolute;
    inset: 0;
    width: 100%;
    height: 100%;
  }

  .chart-footer {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 4px 10px;
    font-size: 10px;
    opacity: 0.4;
    border-top: 1px solid var(--nubi-border, #2d3748);
    gap: 8px;
    flex-shrink: 0;
  }

  .nubi-sample-note {
    font-size: 10px;
    padding: 2px 7px;
    border: none;
  }
`

// ---------------------------------------------------------------------------
// Palette
// ---------------------------------------------------------------------------

const PALETTE = [
  '#6366f1', '#f59e0b', '#10b981', '#ef4444', '#3b82f6',
  '#8b5cf6', '#ec4899', '#14b8a6', '#f97316', '#84cc16',
]

// ---------------------------------------------------------------------------
// Compact option builder (mirrors src/viz/chartOption.js logic)
// Can't import from src/ in the widget bundle, so logic is inlined here.
// ---------------------------------------------------------------------------

function getColumn(table, colName) {
  if (!colName) return null
  const child = table.getChild(colName)
  if (!child) return null
  return child.toArray()
}

function toNumbers(arr) {
  const out = new Array(arr.length)
  for (let i = 0; i < arr.length; i++) {
    const v = arr[i]
    out[i] = v === null || v === undefined ? 0 : Number(v)
  }
  return out
}

function toStrings(arr) {
  const out = new Array(arr.length)
  for (let i = 0; i < arr.length; i++) {
    const v = arr[i]
    out[i] = v === null || v === undefined ? '' : String(v)
  }
  return out
}

function isNumericArray(arr) {
  for (let i = 0; i < arr.length; i++) {
    if (arr[i] != null) {
      return typeof arr[i] === 'number' || typeof arr[i] === 'bigint'
    }
  }
  return false
}

function groupByCategory(xVals, yVals, colorArr) {
  const groups = new Map()
  const n = Math.min(xVals.length, yVals.length)
  for (let i = 0; i < n; i++) {
    const cat = colorArr[i] === null || colorArr[i] === undefined
      ? '(null)' : String(colorArr[i])
    if (!groups.has(cat)) groups.set(cat, { category: cat, xVals: [], yVals: [] })
    const g = groups.get(cat)
    g.xVals.push(xVals[i])
    g.yVals.push(yVals[i])
  }
  return Array.from(groups.values())
}

function baseOption({ showLegend = false, showDataZoom = false } = {}) {
  const opt = {
    color: PALETTE,
    backgroundColor: 'transparent',
    animation: false,
    grid: {
      top: showLegend ? 40 : 12,
      right: 20,
      bottom: showDataZoom ? 60 : 32,
      left: 52,
      containLabel: true,
    },
    tooltip: { trigger: 'axis', axisPointer: { type: 'cross' }, confine: true },
  }
  if (showLegend) {
    opt.legend = { top: 4, type: 'scroll', textStyle: { fontSize: 11 } }
  }
  if (showDataZoom) {
    opt.dataZoom = [
      { type: 'inside', xAxisIndex: 0 },
      { type: 'slider', xAxisIndex: 0, height: 20, bottom: 8 },
    ]
  }
  return opt
}

function buildScatterOption(table, xCol, yCol, colorCol) {
  const xRaw = getColumn(table, xCol)
  const yRaw = getColumn(table, yCol)
  if (!xRaw || !yRaw) return {}
  const n = Math.min(xRaw.length, yRaw.length)
  if (n === 0) return {}
  const xVals = toNumbers(xRaw)
  const yVals = toNumbers(yRaw)
  const large = n > 5000
  const sampling = n > 100_000 ? 'lttb' : undefined
  const opt = baseOption({ showLegend: !!colorCol, showDataZoom: n > 200 })
  opt.tooltip = { trigger: 'item', formatter: (p) => `${xCol}: ${p.value[0]}<br/>${yCol}: ${p.value[1]}`, confine: true }
  opt.xAxis = { type: 'value', name: xCol, nameLocation: 'middle', nameGap: 28, nameTextStyle: { fontSize: 11 } }
  opt.yAxis = { type: 'value', name: yCol, nameLocation: 'middle', nameGap: 36, nameTextStyle: { fontSize: 11 } }
  const colorRaw = getColumn(table, colorCol)
  if (colorRaw && !isNumericArray(colorRaw)) {
    const groups = groupByCategory(xVals, yVals, colorRaw)
    opt.series = groups.map((g) => ({
      type: 'scatter', name: g.category,
      data: g.xVals.map((x, i) => [x, g.yVals[i]]),
      symbolSize: large ? 4 : 6, large, largeThreshold: 2000, sampling,
    }))
  } else {
    opt.series = [{
      type: 'scatter',
      data: xVals.map((x, i) => [x, yVals[i]]),
      symbolSize: large ? 4 : 6,
      itemStyle: { color: PALETTE[0] },
      large, largeThreshold: 2000, sampling,
    }]
  }
  return opt
}

function buildLineOption(table, xCol, yCol, colorCol, area = false) {
  const xRaw = getColumn(table, xCol)
  const yRaw = getColumn(table, yCol)
  if (!xRaw || !yRaw) return {}
  const n = Math.min(xRaw.length, yRaw.length)
  if (n === 0) return {}
  const yVals = toNumbers(yRaw)
  const xIsNumeric = isNumericArray(xRaw)
  const opt = baseOption({ showLegend: !!colorCol, showDataZoom: n > 100 })
  opt.tooltip = { trigger: 'axis', confine: true }
  opt.xAxis = xIsNumeric
    ? { type: 'value', name: xCol, nameLocation: 'middle', nameGap: 28, nameTextStyle: { fontSize: 11 } }
    : { type: 'category', data: toStrings(xRaw), name: xCol, nameLocation: 'middle', nameGap: 28,
        nameTextStyle: { fontSize: 11 }, axisLabel: { rotate: n > 20 ? 30 : 0, fontSize: 10 } }
  opt.yAxis = { type: 'value', name: yCol, nameLocation: 'middle', nameGap: 36, nameTextStyle: { fontSize: 11 } }
  const sampling = n > 5000 ? 'lttb' : undefined
  const colorRaw = getColumn(table, colorCol)
  if (colorRaw && !isNumericArray(colorRaw)) {
    const xVals = xIsNumeric ? toNumbers(xRaw) : Array.from({ length: n }, (_, i) => i)
    const groups = groupByCategory(xVals, yVals, colorRaw)
    opt.series = groups.map((g) => ({
      type: 'line', name: g.category,
      data: xIsNumeric ? g.xVals.map((x, i) => [x, g.yVals[i]]) : g.yVals,
      smooth: true, areaStyle: area ? { opacity: 0.25 } : undefined, sampling,
    }))
  } else {
    opt.series = [{
      type: 'line', name: yCol,
      data: xIsNumeric ? toNumbers(xRaw).map((x, i) => [x, yVals[i]]) : yVals,
      smooth: true,
      itemStyle: { color: PALETTE[0] }, lineStyle: { color: PALETTE[0] },
      areaStyle: area ? { color: PALETTE[0], opacity: 0.2 } : undefined,
      sampling,
    }]
  }
  return opt
}

function buildBarOption(table, xCol, yCol, colorCol) {
  const xRaw = getColumn(table, xCol)
  const yRaw = getColumn(table, yCol)
  if (!xRaw || !yRaw) return {}
  const n = Math.min(xRaw.length, yRaw.length)
  if (n === 0) return {}
  const yVals = toNumbers(yRaw)
  const opt = baseOption({ showLegend: !!colorCol, showDataZoom: n > 30 })
  opt.tooltip = { trigger: 'axis', confine: true }
  opt.xAxis = {
    type: 'category', data: toStrings(xRaw), name: xCol,
    nameLocation: 'middle', nameGap: 28, nameTextStyle: { fontSize: 11 },
    axisLabel: { rotate: n > 12 ? 30 : 0, fontSize: 10 },
  }
  opt.yAxis = { type: 'value', name: yCol, nameLocation: 'middle', nameGap: 36, nameTextStyle: { fontSize: 11 } }
  const colorRaw = getColumn(table, colorCol)
  if (colorRaw && !isNumericArray(colorRaw)) {
    const groups = groupByCategory(Array.from({ length: n }, (_, i) => i), yVals, colorRaw)
    opt.series = groups.map((g) => ({ type: 'bar', name: g.category, data: g.yVals, stack: 'total' }))
  } else {
    opt.series = [{ type: 'bar', name: yCol, data: yVals, itemStyle: { color: PALETTE[0], borderRadius: [2, 2, 0, 0] } }]
  }
  return opt
}

function buildPieOption(table, xCol, yCol) {
  const xRaw = getColumn(table, xCol)
  const yRaw = getColumn(table, yCol)
  if (!xRaw || !yRaw) return {}
  const n = Math.min(xRaw.length, yRaw.length)
  if (n === 0) return {}
  const xStrings = toStrings(xRaw)
  const yVals = toNumbers(yRaw)
  return {
    color: PALETTE, backgroundColor: 'transparent', animation: false,
    tooltip: { trigger: 'item', formatter: '{b}: {c} ({d}%)', confine: true },
    legend: { type: 'scroll', orient: 'horizontal', bottom: 4, textStyle: { fontSize: 10 } },
    series: [{
      type: 'pie', name: yCol,
      data: xStrings.map((name, i) => ({ name, value: yVals[i] })),
      radius: ['30%', '65%'], center: ['50%', '45%'],
      label: { show: n <= 12, fontSize: 10 },
      labelLine: { show: n <= 12 },
    }],
  }
}

function buildChartOption({ chartType, table, x, y, color }) {
  if (!table || table.numRows === 0) {
    return {
      color: PALETTE, backgroundColor: 'transparent', animation: false,
      graphic: [{ type: 'text', left: 'center', top: 'middle',
        style: { text: 'No data', fontSize: 14, fill: '#9ca3af' } }],
    }
  }
  const type = (chartType || 'scatter').toLowerCase()
  switch (type) {
    case 'scatter': return buildScatterOption(table, x, y, color)
    case 'line':    return buildLineOption(table, x, y, color, false)
    case 'area':    return buildLineOption(table, x, y, color, true)
    case 'bar':     return buildBarOption(table, x, y, color)
    case 'pie':     return buildPieOption(table, x, y)
    default:        return buildScatterOption(table, x, y, color)
  }
}

// ---------------------------------------------------------------------------
// NubiChart — custom element
// ---------------------------------------------------------------------------

class NubiChart extends HTMLElement {
  static get observedAttributes() {
    return ['query-id', 'type', 'x', 'y', 'color', 'token', 'get-token', 'backend']
  }

  constructor() {
    super()
    this._shadow = this.attachShadow({ mode: 'open' })
    this._ac = null          // AbortController for in-flight fetch
    this._chart = null       // echarts instance
    this._ro = null          // ResizeObserver
  }

  connectedCallback() { this._render() }

  disconnectedCallback() {
    this._abort()
    this._destroyChart()
  }

  attributeChangedCallback(_n, old, val) {
    if (old !== val && this.isConnected) this._render()
  }

  _abort() {
    if (this._ac) { this._ac.abort(); this._ac = null }
  }

  _destroyChart() {
    if (this._ro) { this._ro.disconnect(); this._ro = null }
    if (this._chart) {
      if (!this._chart.isDisposed()) this._chart.dispose()
      this._chart = null
    }
  }

  _backend() {
    return (this.getAttribute('backend') || 'http://localhost:8000').replace(/\/$/, '')
  }

  _ensureScaffold() {
    if (this._shadow.querySelector('.chart-wrap')) return

    const styleEl = document.createElement('style')
    styleEl.textContent = CHART_STYLES
    this._shadow.innerHTML = ''
    this._shadow.appendChild(styleEl)

    const queryId = this.getAttribute('query-id') || 'chart'
    const type = this.getAttribute('type') || 'scatter'

    const wrap = document.createElement('div')
    wrap.className = 'chart-wrap'
    wrap.innerHTML = /* html */ `
      <div class="chart-toolbar">
        <span class="chart-title">${_escHtml(queryId)} · ${_escHtml(type)}</span>
        <span class="nubi-badge" style="visibility:hidden">…</span>
      </div>
      <div class="nubi-sample-note" style="display:none">preview · sample data</div>
      <div class="chart-body">
        <div class="nubi-loading">Loading</div>
      </div>
      <div class="chart-footer">
        <span class="footer-left"></span>
        <span class="footer-right"></span>
      </div>
    `
    this._shadow.appendChild(wrap)
  }

  _setBadge(text, cls) {
    const badge = this._shadow.querySelector('.nubi-badge')
    if (!badge) return
    badge.textContent = text
    badge.className = `nubi-badge ${cls}`
    badge.style.visibility = 'visible'
  }

  _setFooter(left, right) {
    const fl = this._shadow.querySelector('.footer-left')
    const fr = this._shadow.querySelector('.footer-right')
    if (fl) fl.textContent = left
    if (fr) fr.textContent = right
  }

  _renderChart(table, isSample) {
    const type     = (this.getAttribute('type') || 'scatter').toLowerCase()
    const xCol     = this.getAttribute('x') || table.schema.fields[0]?.name || ''
    const yCol     = this.getAttribute('y') || table.schema.fields[1]?.name || ''
    const colorCol = this.getAttribute('color') || null
    const n        = table.numRows

    // Destroy previous ECharts instance if any
    this._destroyChart()

    const body = this._shadow.querySelector('.chart-body')
    if (!body) return
    body.innerHTML = '' // clear loading / previous chart

    const note = this._shadow.querySelector('.nubi-sample-note')
    if (note) note.style.display = isSample ? 'block' : 'none'

    // Create ECharts container div inside shadow root
    const container = document.createElement('div')
    container.className = 'echarts-container'
    body.appendChild(container)

    // Build option
    const option = buildChartOption({ chartType: type, table, x: xCol, y: yCol, color: colorCol })

    // Init ECharts inside shadow DOM
    const chart = echarts.init(container, null, { renderer: 'canvas', useDirtyRect: true })
    chart.setOption(option)
    this._chart = chart

    // Responsive: observe the body element for size changes
    const ro = new ResizeObserver(() => {
      if (this._chart && !this._chart.isDisposed()) this._chart.resize()
    })
    ro.observe(body)
    this._ro = ro

    const label = `echarts · ${n.toLocaleString()} pts`
    this._setBadge(label, isSample ? 'sample' : 'live')
    this._setFooter(`${xCol} × ${yCol}`, isSample ? 'sample data' : '')
  }

  async _render() {
    this._abort()
    const ac = new AbortController()
    this._ac = ac

    this._ensureScaffold()

    // Reset body to loading state
    const body = this._shadow.querySelector('.chart-body')
    if (body) body.innerHTML = '<div class="nubi-loading">Loading</div>'

    const queryId = this.getAttribute('query-id')
    const backend = this._backend()

    let token = null
    try { token = await resolveToken(this) } catch (_) { /* ignore */ }
    if (ac.signal.aborted) return

    if (queryId && backend) {
      try {
        const table = await fetchArrow(backend, queryId, token, ac.signal)
        if (ac.signal.aborted) return

        const n = table.numRows
        this._renderChart(table, false)
        this.dispatchEvent(new CustomEvent('nubi:widget-ready', {
          bubbles: true, composed: true,
          detail: { rows: n, renderer: 'echarts' },
        }))
        return
      } catch (err) {
        if (err.name === 'AbortError') return
        console.warn('[nubi-chart] fetch failed — showing sample:', err.message)
        this.dispatchEvent(new CustomEvent('nubi:widget-error', {
          bubbles: true, composed: true,
          detail: { message: err.message },
        }))
      }
    }

    if (ac.signal.aborted) return

    // Sample fallback
    const sample = makeSampleTableData()
    this._renderChart(sample, true)
    this.dispatchEvent(new CustomEvent('nubi:widget-ready', {
      bubbles: true, composed: true,
      detail: { rows: sample.numRows, renderer: 'echarts' },
    }))
  }
}

function _escHtml(str) {
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
}

export { NubiChart }
