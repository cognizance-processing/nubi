/**
 * nubi-chart.js — <nubi-chart> comprehensive ECharts-powered chart widget.
 *
 * A single, fully customizable chart element covering 17 chart families. All
 * type-specific option building lives in the pure, unit-testable
 * `chart-options.js` module; this file is responsible only for data loading,
 * lifecycle, theming, the WebGL fast path, and DOM/event wiring.
 *
 * ════════════════════════════════════════════════════════════════════════════
 * ATTRIBUTES
 * ════════════════════════════════════════════════════════════════════════════
 * Data source:
 *   query-id    Registered query id (POSTed to {backend}/api/v1/query).
 *   data        Inline JSON array of row objects — bypasses fetch entirely.
 *   token       Static JWT string.
 *   get-token   Name of a window function returning Promise<string>|string.
 *   backend     Base URL of Nubi API. Default http://localhost:8000.
 *
 * Chart type:
 *   type        bar | line | area | scatter | bubble | pie | donut | sankey |
 *               funnel | waterfall | heatmap | radar | treemap | boxplot |
 *               gauge | candlestick | fan        (default: scatter)
 *
 * Encoding (column-name mapping — meaning varies by type):
 *   x           Category / X-axis column (or source for sankey, name for pie).
 *   y           Value / Y-axis column.
 *   y2          Secondary Y column → renders on a dual right-hand axis (line).
 *   color       Categorical series-split column (or 2nd dim for heatmap/sankey).
 *   size        Bubble size column (bubble charts).
 *   value       Explicit value column (sankey/heatmap/funnel/treemap/gauge).
 *   source / target            Sankey link endpoints.
 *   open / close / low / high  Candlestick OHLC columns.
 *   lower / upper              Fan-chart confidence-band columns.
 *
 * Customization — individual attributes (sugar over `config`):
 *   title, subtitle           Chart heading.
 *   legend                    "true" | "false" | "top|bottom|left|right".
 *   stacked                   "true" | "normal" | "percent" (100%-stacked).
 *   orientation               "vertical" | "horizontal".
 *   smooth                    "true" | "false" — smooth line/area.
 *   area-opacity              0..1.
 *   data-labels               "true" | "false".
 *   palette                   Comma-separated colors, or a theme token name.
 *   x-label, y-label, y2-label   Axis titles.
 *   x-format, y-format, y2-format  number|currency|percent|si|date.
 *   y-min, y-max              Numeric axis bounds.
 *   log-y                     "true" → log scale on Y.
 *   animation                 "false" → disable animations.
 *   locale                    BCP-47 locale for number formatting.
 *
 * Customization — `config` JSON attribute:
 *   A full config object (see chart-options.js CONFIG SCHEMA). Deep-merged on
 *   top of the per-attribute config, so `config` always WINS. Raw ECharts
 *   option keys under `config.echarts` are merged last of all.
 *
 * Theming:
 *   theme       "dark" | "light"  (applies the Nubi token preset).
 *   The chart reads resolved CSS custom properties (--nubi-*) so host overrides
 *   propagate into ECharts colors automatically.
 *   no-sample-fallback   Show an error state instead of sample data on failure.
 *
 * ════════════════════════════════════════════════════════════════════════════
 * EVENTS
 * ════════════════════════════════════════════════════════════════════════════
 *   nubi:widget-ready  { rows, type, renderer: 'echarts' | 'webgl' }
 *   nubi:widget-error  { message }
 *   nubi:select        { name, value, dataIndex, seriesName, seriesIndex }
 *                      — fired when a data point / segment is clicked.
 *
 * CSS CUSTOM PROPERTIES
 *   --nubi-bg, --nubi-fg, --nubi-fg-muted, --nubi-primary, --nubi-border, …
 */

import * as echarts from 'echarts'
import {
  resolveToken, fetchArrow, makeSampleTableData, BASE_STYLES, rowsToArrowTable,
} from './shared.js'
import { applyTheme } from '../theme.js'
import { buildChartOption, tableView, SUPPORTED_TYPES, DEFAULT_PALETTE } from './chart-options.js'
import { createGlScatter } from './glScatter.js'

// Number of scatter/bubble rows above which the regl WebGL fast path is used.
export const WEBGL_THRESHOLD = 20_000

// ---------------------------------------------------------------------------
// Styles
// ---------------------------------------------------------------------------

const CHART_STYLES = /* css */ `
  ${BASE_STYLES}

  :host { min-height: 220px; }

  .chart-wrap {
    display: flex; flex-direction: column;
    width: 100%; height: 100%; box-sizing: border-box;
  }

  .chart-toolbar {
    display: flex; align-items: center; justify-content: space-between;
    padding: 7px 12px;
    background: var(--nubi-accent, #1e2433);
    border-bottom: 1px solid var(--nubi-border, #2d3748);
    font-size: 11px; gap: 8px; flex-shrink: 0;
  }

  .chart-title {
    font-weight: 600; letter-spacing: 0.02em; opacity: 0.75;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis; flex: 1;
  }

  .chart-body {
    flex: 1; position: relative; overflow: hidden; min-height: 160px;
  }

  .echarts-container, .gl-canvas {
    position: absolute; inset: 0; width: 100%; height: 100%;
  }

  .chart-footer {
    display: flex; align-items: center; justify-content: space-between;
    padding: 4px 10px; font-size: 10px; opacity: 0.4;
    border-top: 1px solid var(--nubi-border, #2d3748); gap: 8px; flex-shrink: 0;
  }

  .nubi-sample-note { font-size: 10px; padding: 2px 7px; border: none; }

  .nubi-error-state {
    display: flex; flex-direction: column; align-items: center;
    justify-content: center; gap: 6px; height: 100%; padding: 24px; text-align: center;
  }
  .nubi-error-primary {
    font-size: 13px; font-weight: 500; color: var(--nubi-fg, #e2e8f0); opacity: 0.6;
  }
  .nubi-error-primary::before { content: '⚠ '; font-size: 12px; }
  .nubi-error-secondary { font-size: 11px; color: var(--nubi-fg, #e2e8f0); opacity: 0.35; }
`

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function _escHtml(str) {
  return String(str)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;')
    .replace(/>/g, '&gt;').replace(/"/g, '&quot;')
}

function _bool(v, dflt = undefined) {
  if (v == null) return dflt
  if (v === '' || v === 'true') return true
  if (v === 'false') return false
  return dflt
}

// Local deep-merge for the per-attribute + JSON-config combine (source wins).
function _deepMergeLocal(target, source) {
  if (!source || typeof source !== 'object' || Array.isArray(source)) return source
  const out = { ...target }
  for (const key of Object.keys(source)) {
    const sv = source[key]
    if (sv && typeof sv === 'object' && !Array.isArray(sv) &&
        out[key] && typeof out[key] === 'object' && !Array.isArray(out[key])) {
      out[key] = _deepMergeLocal(out[key], sv)
    } else {
      out[key] = sv
    }
  }
  return out
}

// ---------------------------------------------------------------------------
// NubiChart — custom element
// ---------------------------------------------------------------------------

class NubiChart extends HTMLElement {
  static get observedAttributes() {
    return [
      'query-id', 'type', 'x', 'y', 'y2', 'color', 'size', 'value',
      'source', 'target', 'open', 'close', 'low', 'high', 'lower', 'upper',
      'token', 'get-token', 'backend', 'theme', 'data', 'no-sample-fallback',
      'title', 'subtitle', 'legend', 'stacked', 'orientation', 'smooth',
      'area-opacity', 'data-labels', 'palette', 'x-label', 'y-label', 'y2-label',
      'x-format', 'y-format', 'y2-format', 'y-min', 'y-max', 'log-y',
      'animation', 'locale', 'config',
    ]
  }

  constructor() {
    super()
    this._shadow = this.attachShadow({ mode: 'open' })
    this._ac = null      // AbortController for in-flight fetch
    this._chart = null   // echarts instance
    this._gl = null      // regl scatter renderer (large-data fast path)
    this._ro = null      // ResizeObserver
  }

  connectedCallback() {
    applyTheme(this, this.getAttribute('theme') || 'dark')
    this._render()
  }

  disconnectedCallback() {
    this._abort()
    this._destroyChart()
  }

  attributeChangedCallback(name, old, val) {
    if (old === val) return
    if (name === 'theme') applyTheme(this, val || 'dark')
    if (this.isConnected) this._render()
  }

  _abort() { if (this._ac) { this._ac.abort(); this._ac = null } }

  _destroyChart() {
    if (this._ro) { this._ro.disconnect(); this._ro = null }
    if (this._gl) { try { this._gl.destroy() } catch { /* ignore */ } this._gl = null }
    if (this._chart) {
      if (!this._chart.isDisposed()) this._chart.dispose()
      this._chart = null
    }
  }

  _backend() {
    return (this.getAttribute('backend') || 'http://localhost:8000').replace(/\/$/, '')
  }

  // --- Encoding / config / theme resolution -------------------------------

  _encoding(view) {
    const cols = view.columns
    const attr = (n) => this.getAttribute(n) || undefined
    return {
      x: attr('x') || cols[0],
      y: attr('y') || cols[1],
      y2: attr('y2'),
      color: attr('color'),
      size: attr('size'),
      value: attr('value'),
      source: attr('source'),
      target: attr('target'),
      open: attr('open'),
      close: attr('close'),
      low: attr('low'),
      high: attr('high'),
      lower: attr('lower'),
      upper: attr('upper'),
    }
  }

  /** Build config from individual attributes, then deep-merge JSON `config` (config wins). */
  _config() {
    const cfg = {}
    const a = (n) => this.getAttribute(n)

    if (a('title') != null) cfg.title = a('title')
    if (a('subtitle') != null) cfg.subtitle = a('subtitle')

    const legend = a('legend')
    if (legend != null) {
      if (legend === 'false') cfg.legend = false
      else if (legend === 'true' || legend === '') cfg.legend = true
      else cfg.legend = { position: legend }
    }

    const stacked = a('stacked')
    if (stacked != null) {
      cfg.stack = stacked === 'percent' ? 'percent'
        : stacked === 'false' ? false
          : (stacked === 'normal' || stacked === 'true' || stacked === '' ? true : stacked)
    }

    if (a('orientation')) cfg.orientation = a('orientation')
    const smooth = _bool(a('smooth'))
    if (smooth !== undefined) cfg.smooth = smooth
    if (a('area-opacity') != null) cfg.areaOpacity = Number(a('area-opacity'))
    const dl = _bool(a('data-labels'))
    if (dl !== undefined) cfg.dataLabels = dl
    const anim = _bool(a('animation'))
    if (anim !== undefined) cfg.animation = anim
    if (a('locale')) cfg.locale = a('locale')

    const palette = a('palette')
    if (palette) cfg.palette = palette.includes(',') ? palette.split(',').map((s) => s.trim()) : palette

    const xAxis = {}
    if (a('x-label') != null) xAxis.label = a('x-label')
    if (a('x-format')) xAxis.format = a('x-format')
    if (Object.keys(xAxis).length) cfg.xAxis = xAxis

    const yAxis = {}
    if (a('y-label') != null) yAxis.label = a('y-label')
    if (a('y-format')) yAxis.format = a('y-format')
    if (a('y-min') != null) yAxis.min = Number(a('y-min'))
    if (a('y-max') != null) yAxis.max = Number(a('y-max'))
    if (_bool(a('log-y'))) yAxis.log = true
    if (Object.keys(yAxis).length) cfg.yAxis = yAxis

    const y2Axis = {}
    if (a('y2-label') != null) y2Axis.label = a('y2-label')
    if (a('y2-format')) y2Axis.format = a('y2-format')
    if (Object.keys(y2Axis).length) cfg.y2Axis = y2Axis

    const raw = a('config')
    if (raw) {
      try {
        return _deepMergeLocal(cfg, JSON.parse(raw))
      } catch (err) {
        console.warn('[nubi-chart] invalid config attribute:', err.message)
      }
    }
    return cfg
  }

  /** Resolve theme tokens from computed CSS custom properties on the host. */
  _theme() {
    let cs = null
    try { cs = getComputedStyle(this) } catch { /* jsdom */ }
    const get = (name, fallback) => {
      const v = cs && cs.getPropertyValue(name).trim()
      return v || fallback
    }
    const cfgPalette = this.getAttribute('palette')
    const palette = (cfgPalette && cfgPalette.includes(','))
      ? cfgPalette.split(',').map((s) => s.trim())
      : DEFAULT_PALETTE
    return {
      palette,
      fg: get('--nubi-fg', '#e2e8f0'),
      fgMuted: get('--nubi-fg-muted', '#94a3b8'),
      primary: get('--nubi-primary', '#6366f1'),
      border: get('--nubi-border', '#2d3748'),
      grid: 'rgba(148,163,184,0.12)',
      axis: 'rgba(148,163,184,0.35)',
      // No fallback: an embed host that doesn't theme these vars falls
      // through to chart-options.js's own light/dark inference from `fg`.
      tooltipBg: get('--nubi-tooltip-bg', undefined),
      tooltipFg: get('--nubi-tooltip-fg', undefined),
    }
  }

  // --- Scaffold / chrome ---------------------------------------------------

  _ensureScaffold() {
    if (this._shadow.querySelector('.chart-wrap')) return
    const styleEl = document.createElement('style')
    styleEl.textContent = CHART_STYLES
    this._shadow.innerHTML = ''
    this._shadow.appendChild(styleEl)

    const heading = this.getAttribute('title') || this.getAttribute('query-id') || 'chart'
    const type = this.getAttribute('type') || 'scatter'

    const wrap = document.createElement('div')
    wrap.className = 'chart-wrap'
    wrap.innerHTML = /* html */ `
      <div class="chart-toolbar">
        <span class="chart-title">${_escHtml(heading)} · ${_escHtml(type)}</span>
        <span class="nubi-badge" style="visibility:hidden">…</span>
      </div>
      <div class="nubi-sample-note" style="display:none">preview · sample data</div>
      <div class="chart-body"><div class="nubi-loading">Loading</div></div>
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

  // --- Rendering -----------------------------------------------------------

  _renderChart(table, isSample) {
    const type = (this.getAttribute('type') || 'scatter').toLowerCase()
    const view = tableView(table)
    const encoding = this._encoding(view)
    const config = this._config()
    const theme = this._theme()
    const n = view.numRows

    this._destroyChart()

    const body = this._shadow.querySelector('.chart-body')
    if (!body) return 'echarts'
    body.innerHTML = ''

    const note = this._shadow.querySelector('.nubi-sample-note')
    if (note) note.style.display = isSample ? 'block' : 'none'

    // Large-data WebGL fast path for plain (single-series) scatter/bubble.
    const wantsWebGL = (type === 'scatter' || type === 'bubble') &&
      n >= WEBGL_THRESHOLD && !encoding.color
    let renderer = 'echarts'
    if (wantsWebGL && this._tryWebGL(body, view, encoding)) {
      renderer = 'webgl'
    } else {
      this._renderECharts(body, type, view, encoding, config, theme)
    }

    const label = `${renderer} · ${n.toLocaleString()} pts`
    this._setBadge(label, isSample ? 'sample' : (renderer === 'webgl' ? 'webgl' : 'live'))
    this._setFooter(this._footerLabel(type, encoding), isSample ? 'sample data' : '')
    return renderer
  }

  _renderECharts(body, type, view, encoding, config, theme) {
    const container = document.createElement('div')
    container.className = 'echarts-container'
    body.appendChild(container)

    const option = buildChartOption({ type, table: view, encoding, config, theme })
    const chart = echarts.init(container, null, { renderer: 'canvas', useDirtyRect: true })
    chart.setOption(option)
    this._chart = chart

    chart.on('click', (params) => {
      this.dispatchEvent(new CustomEvent('nubi:select', {
        bubbles: true, composed: true,
        detail: {
          name: params.name,
          value: params.value,
          dataIndex: params.dataIndex,
          seriesName: params.seriesName,
          seriesIndex: params.seriesIndex,
        },
      }))
    })

    const ro = new ResizeObserver(() => {
      if (this._chart && !this._chart.isDisposed()) this._chart.resize()
    })
    ro.observe(body)
    this._ro = ro
  }

  /** Attempt the regl WebGL scatter fast path. Returns true on success. */
  _tryWebGL(body, view, encoding) {
    let canvas
    try {
      canvas = document.createElement('canvas')
      canvas.className = 'gl-canvas'
      const rect = body.getBoundingClientRect()
      canvas.width = Math.max(1, Math.floor(rect.width || 600))
      canvas.height = Math.max(1, Math.floor(rect.height || 320))
      body.appendChild(canvas)

      const xRaw = view.col(encoding.x) || []
      const yRaw = view.col(encoding.y) || []
      const n = Math.min(xRaw.length, yRaw.length)
      const x = new Float32Array(n)
      const y = new Float32Array(n)
      let xmin = Infinity, xmax = -Infinity, ymin = Infinity, ymax = -Infinity
      for (let i = 0; i < n; i++) {
        const xv = Number(xRaw[i]) || 0, yv = Number(yRaw[i]) || 0
        x[i] = xv; y[i] = yv
        if (xv < xmin) xmin = xv
        if (xv > xmax) xmax = xv
        if (yv < ymin) ymin = yv
        if (yv > ymax) ymax = yv
      }
      const xr = (xmax - xmin) || 1, yr = (ymax - ymin) || 1
      for (let i = 0; i < n; i++) {
        x[i] = ((x[i] - xmin) / xr) * 1.9 - 0.95
        y[i] = ((y[i] - ymin) / yr) * 1.9 - 0.95
      }
      const gl = createGlScatter(canvas)
      gl.draw({ x, y, pointSize: 3 })
      this._gl = gl

      const ro = new ResizeObserver(() => {
        const r = body.getBoundingClientRect()
        canvas.width = Math.max(1, Math.floor(r.width))
        canvas.height = Math.max(1, Math.floor(r.height))
        if (this._gl) this._gl.draw({ x, y, pointSize: 3 })
      })
      ro.observe(body)
      this._ro = ro
      return true
    } catch (err) {
      console.warn('[nubi-chart] WebGL path failed, falling back to ECharts:', err.message)
      if (canvas) canvas.remove()
      return false
    }
  }

  _footerLabel(type, e) {
    if (type === 'sankey') return `${e.source || e.x} → ${e.target || e.color}`
    if (type === 'pie' || type === 'donut' || type === 'funnel' || type === 'treemap') return `${e.x} · ${e.y}`
    if (type === 'candlestick') return `${e.x} · OHLC`
    return `${e.x || ''} × ${e.y || ''}`
  }

  _showChartError() {
    this._destroyChart()
    const body = this._shadow.querySelector('.chart-body')
    if (body) {
      body.innerHTML = ''
      const d = document.createElement('div')
      d.className = 'nubi-error-state'
      d.innerHTML = `
        <span class="nubi-error-primary">Couldn't load data</span>
        <span class="nubi-error-secondary">Check your connection or try again</span>
      `
      body.appendChild(d)
    }
    this._setBadge('error', 'error')
    const note = this._shadow.querySelector('.nubi-sample-note')
    if (note) note.style.display = 'none'
  }

  _emitReady(rows, renderer) {
    this.dispatchEvent(new CustomEvent('nubi:widget-ready', {
      bubbles: true, composed: true,
      detail: { rows, type: (this.getAttribute('type') || 'scatter').toLowerCase(), renderer },
    }))
  }

  async _render() {
    this._abort()
    const ac = new AbortController()
    this._ac = ac

    this._ensureScaffold()
    const body = this._shadow.querySelector('.chart-body')
    if (body) body.innerHTML = '<div class="nubi-loading">Loading</div>'

    // Inline data injection — bypasses fetch entirely.
    const dataAttr = this.getAttribute('data')
    if (dataAttr) {
      try {
        const rows = JSON.parse(dataAttr)
        const table = rowsToArrowTable(rows)
        const renderer = this._renderChart(table, false)
        this._emitReady(table.numRows, renderer)
      } catch (err) {
        console.warn('[nubi-chart] invalid data attribute:', err.message)
        this._showChartError()
      }
      return
    }

    const queryId = this.getAttribute('query-id')
    const backend = this._backend()

    let token = null
    try { token = await resolveToken(this) } catch { /* ignore */ }
    if (ac.signal.aborted) return

    if (queryId && backend) {
      try {
        const table = await fetchArrow(backend, queryId, token, ac.signal)
        if (ac.signal.aborted) return
        const renderer = this._renderChart(table, false)
        this._emitReady(table.numRows, renderer)
        return
      } catch (err) {
        if (err.name === 'AbortError') return
        console.warn('[nubi-chart] fetch failed — showing sample:', err.message)
        this.dispatchEvent(new CustomEvent('nubi:widget-error', {
          bubbles: true, composed: true, detail: { message: err.message },
        }))
        if (this.hasAttribute('no-sample-fallback')) { this._showChartError(); return }
      }
    }

    if (ac.signal.aborted) return

    if (this.hasAttribute('no-sample-fallback')) { this._showChartError(); return }

    const sample = makeSampleTableData()
    const renderer = this._renderChart(sample, true)
    this._emitReady(sample.numRows, renderer)
  }
}

export { NubiChart, SUPPORTED_TYPES }
