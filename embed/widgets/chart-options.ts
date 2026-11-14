/**
 * chart-options.js — Pure ECharts option builder for <nubi-chart>.
 *
 * This module is intentionally framework- and DOM-free so it can be unit
 * tested in isolation (no echarts, no jsdom, no Arrow required). The web
 * component (`nubi-chart.js`) wraps an Apache Arrow Table in the lightweight
 * `tableView` adapter below and hands it to `buildChartOption()`.
 *
 * The single public entry point is:
 *
 *   buildChartOption({ type, table, encoding, config, theme }) -> EChartsOption
 *
 * where:
 *   type      — chart type string (see SUPPORTED_TYPES)
 *   table     — a TableView (or rows[] / Arrow Table / columnar object)
 *   encoding  — { x, y, y2, color, size, value, label, source, target,
 *                 low, high, open, close, lower, upper, bars, lines }
 *               (column-name mapping; meaning varies per chart type.
 *               `bars`/`lines` are arrays of column names, used only by
 *               chart_type 'combo' — see buildCombo.)
 *   config    — declarative customization object (deep-merged LAST, so it wins)
 *   theme     — { palette: string[], fg, fgMuted, primary, border, grid, axis,
 *                 tooltipBg, tooltipFg }
 *               resolved theme tokens used as ECharts defaults. tooltipBg/Fg
 *               are optional — when omitted they're inferred from `fg` so
 *               older callers still get a readable (not-invisible) tooltip.
 *
 * The returned object is a complete ECharts option. Type-specific logic lives
 * entirely here; the web component only handles data loading, lifecycle and
 * DOM wiring.
 *
 * ────────────────────────────────────────────────────────────────────────────
 * CONFIG SCHEMA (all optional)
 * ────────────────────────────────────────────────────────────────────────────
 * {
 *   title:     string | { text, subtext, left, ... },
 *   subtitle:  string,
 *   legend:    boolean | { show, position: 'top'|'bottom'|'left'|'right', ... },
 *   tooltip:   boolean | { ...echarts tooltip, valueFormat: FormatSpec },
 *   palette:   string[] | string,        // custom colors; string -> theme token key
 *   stack:     boolean | 'normal' | 'percent',   // bar/area/combo-bar stacking (percent = 100%)
 *   orientation: 'vertical' | 'horizontal',
 *   smooth:    boolean,                   // line/area/fan
 *   areaOpacity: number,                  // 0..1
 *   dataLabels: boolean | { show, format: FormatSpec, position },
 *   animation: boolean,
 *   grid:      object,                    // echarts grid override (margins)
 *   xAxis:     AxisSpec,
 *   yAxis:     AxisSpec,
 *   y2Axis:    AxisSpec,                  // secondary / dual y-axis
 *   referenceLines: ReferenceLine[],      // markLine entries (e.g. targets)
 *   annotations:    Annotation[],         // markPoint entries
 *   centerLabel:    boolean | { text?, format?, fontSize?, color?, index? },  // pie/donut
 *                   only: a big centered number/label (e.g. "97.4%"). text
 *                   defaults to the `index`-th segment's share (default 0 —
 *                   the first/meaningful slice of a value+remainder ring),
 *                   formatted via `format` (FormatSpec) or a plain "N.N%".
 *   barMaxWidth: number | null,           // bar/combo: cap bar width; null = no cap
 *   lineAxis:  'left' | 'right',          // combo: axis the lines read from (default right)
 *   lineColors: string[],                 // combo: line colours, independent of the bars
 *   symbolSize: number,                   // combo: size of the filled points on its lines
 *   fontFamily: string,                   // canvas text family; default the page's body font
 *   gaugeStyle: 'needle' | 'ring',        // gauge: banded needle dial (default) or progress ring
 *   gaugeArcGradient: boolean,            // gauge: fill the arc with a low->high gradient
 *   gaugeArcStart: string,                // gauge: gradient start colour (low end)
 *   gaugeArcEnd:   string,                // gauge: gradient end colour (high end)
 *   gaugeArcColor: string,                // gauge: solid arc colour when not a gradient.
 *                   Any arc key set -> the needle drops its 3 theme bands for a
 *                   neutral track + the filled-to-value arc in this colour.
 *   gaugeArcCaps: 'flat' | 'round',       // gauge: arc end shape. Unset (Auto) -> gradient
 *                   flat; ring round; needle square (see resolveGaugeArcCaps).
 *   locale:    string,                    // BCP-47 locale for number formatting
 *   echarts:   object,                    // raw echarts option, deep-merged last
 *   ...any other top-level echarts option keys (deep-merged, config wins)
 * }
 *
 * AxisSpec = {
 *   label: string, type: 'value'|'category'|'log'|'time', log: boolean,
 *   min: number, max: number, format: FormatSpec, rotate: number
 * }
 * FormatSpec = 'number'|'currency'|'percent'|'si'|'date'
 *            | { kind, currency, locale, decimals, dateStyle }
 * ReferenceLine = { value, axis:'x'|'y', label, color, type:'dashed'|'solid' }
 *               | { from:[x,y], to:[x,y], label, color }
 * Annotation = { x, y, label, color }
 */

import { humaniseColumn } from './column-labels.js'

// ---------------------------------------------------------------------------
// Supported chart types
// ---------------------------------------------------------------------------

export const SUPPORTED_TYPES = [
  'bar', 'line', 'area', 'scatter', 'bubble', 'pie', 'donut',
  'sankey', 'funnel', 'waterfall', 'heatmap', 'radar', 'treemap',
  'boxplot', 'gauge', 'candlestick', 'fan', 'combo', 'map',
  'polar', 'radialbar',
]

// Default palette (used when no theme palette supplied).
export const DEFAULT_PALETTE = [
  '#6366f1', '#f59e0b', '#10b981', '#ef4444', '#3b82f6',
  '#8b5cf6', '#ec4899', '#14b8a6', '#f97316', '#84cc16',
]

const DEFAULT_THEME = {
  palette: DEFAULT_PALETTE,
  fg: '#e2e8f0',
  fgMuted: '#94a3b8',
  primary: '#6366f1',
  border: '#2d3748',
  grid: 'rgba(148,163,184,0.12)',
  axis: 'rgba(148,163,184,0.35)',
  tooltipBg: 'rgba(15,17,23,0.96)',
  tooltipFg: '#f8fafc',
}

// ---------------------------------------------------------------------------
// tableView — minimal column adapter (decouples builder from Apache Arrow)
// ---------------------------------------------------------------------------

/**
 * Wrap a plain row array OR an Apache Arrow Table behind a uniform interface:
 *   { numRows, columns: string[], col(name) -> Array, isNumeric(name) -> bool }
 *
 * Accepts:
 *  - Array<Record<string, unknown>>   (rows)
 *  - Apache Arrow Table-like          (has .numRows, .schema.fields, .getChild)
 *  - { columns: {name: values[]} }    (already-columnar plain object)
 *
 * @param {any} input
 */
// Arrow hands dates back as epoch numbers, which would read as raw numbers on a
// category axis and arrive as such in a click-to-filter value.
function isArrowTemporal(child) {
  return /^(Date|Timestamp)/.test(String(child.type ?? ''))
}

function arrowDatesToIso(child) {
  const out = new Array(child.length)
  for (let i = 0; i < child.length; i++) {
    const v = child.get(i)
    if (v == null) { out[i] = null; continue }
    const iso = new Date(v).toISOString()
    out[i] = iso.endsWith('T00:00:00.000Z') ? iso.slice(0, 10) : iso
  }
  return out
}

export function tableView(input) {
  if (input && typeof input.col === 'function' && Array.isArray(input.columns)) {
    return input // already a TableView
  }

  // Apache Arrow Table-like
  if (input && typeof input.getChild === 'function' && input.schema) {
    const names = input.schema.fields.map((f) => f.name)
    const cache = new Map()
    return {
      numRows: input.numRows,
      columns: names,
      col(name) {
        if (!name) return null
        if (cache.has(name)) return cache.get(name)
        const child = input.getChild(name)
        const arr = !child ? null : isArrowTemporal(child) ? arrowDatesToIso(child) : child.toArray()
        cache.set(name, arr)
        return arr
      },
      isNumeric(name) { const a = this.col(name); return a ? isNumericArray(a) : false },
    }
  }

  // Columnar plain object: { columns: { a: [...], b: [...] } }
  if (input && input.columns && !Array.isArray(input.columns) && typeof input.columns === 'object') {
    const cols = input.columns
    const names = Object.keys(cols)
    const n = names.length ? cols[names[0]].length : 0
    return {
      numRows: n,
      columns: names,
      col: (name) => ((name && cols[name]) ? cols[name] : null),
      isNumeric(name) { const a = this.col(name); return a ? isNumericArray(a) : false },
    }
  }

  // Array of row objects
  const rows = Array.isArray(input) ? input : []
  const names = rows.length ? Object.keys(rows[0]) : []
  const cache = new Map()
  return {
    numRows: rows.length,
    columns: names,
    col(name) {
      if (!name) return null
      if (cache.has(name)) return cache.get(name)
      const arr = rows.map((r) => r[name])
      cache.set(name, arr)
      return arr
    },
    isNumeric(name) { const a = this.col(name); return a ? isNumericArray(a) : false },
  }
}

// ---------------------------------------------------------------------------
// Small data utilities
// ---------------------------------------------------------------------------

function isNumericArray(arr) {
  for (let i = 0; i < arr.length; i++) {
    if (arr[i] != null) return typeof arr[i] === 'number' || typeof arr[i] === 'bigint'
  }
  return false
}

function toNumbers(arr) {
  const out = new Array(arr.length)
  for (let i = 0; i < arr.length; i++) {
    const v = arr[i]
    out[i] = v == null ? 0 : Number(v)
  }
  return out
}

function toStrings(arr) {
  const out = new Array(arr.length)
  for (let i = 0; i < arr.length; i++) {
    const v = arr[i]
    out[i] = v == null ? '' : String(v)
  }
  return out
}

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

function isDeepObject(v) {
  return v && typeof v === 'object' && !Array.isArray(v)
}

/** Deep-merge `source` onto `target` (source wins). Arrays replaced wholesale. */
export function deepMerge(target, source) {
  if (!isDeepObject(source)) return source === undefined ? target : source
  const out = isDeepObject(target) ? { ...target } : {}
  for (const key of Object.keys(source)) {
    const sv = source[key]
    if (isDeepObject(sv) && isDeepObject(out[key])) out[key] = deepMerge(out[key], sv)
    else out[key] = sv
  }
  return out
}

// ---------------------------------------------------------------------------
// Value formatting (number / currency / percent / SI / date)
// ---------------------------------------------------------------------------

/**
 * Build a (value) -> string formatter from a FormatSpec.
 * @param {string|object|undefined} spec
 * @param {string} [locale]
 * @returns {(v:any)=>string}
 */
export function makeFormatter(spec, locale = undefined) {
  if (!spec) return (v) => (v == null ? '' : String(v))
  const s = typeof spec === 'string' ? { kind: spec } : spec
  const kind = s.kind || 'number'
  const loc = s.locale || locale

  switch (kind) {
    case 'currency': {
      const nf = new Intl.NumberFormat(loc, {
        style: 'currency', currency: s.currency || 'USD',
        maximumFractionDigits: s.decimals ?? 0,
      })
      return (v) => (v == null || isNaN(v) ? '' : nf.format(Number(v)))
    }
    case 'percent': {
      const nf = new Intl.NumberFormat(loc, {
        style: 'percent', maximumFractionDigits: s.decimals ?? 1,
      })
      return (v) => (v == null || isNaN(v) ? '' : nf.format(Number(v)))
    }
    case 'percent100':
      // value is already on a 0-100 scale (common for migrated legacy queries)
      return (v) => (v == null || isNaN(v) ? '' : `${Number(v).toFixed(s.decimals ?? 1)}%`)
    case 'si':
      return (v) => (v == null || isNaN(v) ? '' : siFormat(Number(v), s.decimals ?? 1))
    case 'date': {
      const opts = s.dateOptions || dateStylePreset(s.dateStyle)
      const df = new Intl.DateTimeFormat(loc, opts)
      return (v) => {
        if (v == null) return ''
        const d = v instanceof Date ? v : new Date(v)
        return isNaN(d.getTime()) ? String(v) : df.format(d)
      }
    }
    case 'number':
    default: {
      const nf = new Intl.NumberFormat(loc, { maximumFractionDigits: s.decimals ?? 2 })
      return (v) => (v == null || isNaN(v) ? (v == null ? '' : String(v)) : nf.format(Number(v)))
    }
  }
}

function dateStylePreset(style) {
  switch (style) {
    case 'full': return { dateStyle: 'full' }
    case 'long': return { dateStyle: 'long' }
    case 'medium': return { dateStyle: 'medium' }
    case 'short': return { dateStyle: 'short' }
    default: return { year: 'numeric', month: 'short', day: 'numeric' }
  }
}

function siFormat(n, decimals) {
  const abs = Math.abs(n)
  const units = [
    { v: 1e12, s: 'T' }, { v: 1e9, s: 'B' }, { v: 1e6, s: 'M' }, { v: 1e3, s: 'k' },
  ]
  for (const u of units) {
    if (abs >= u.v) return (n / u.v).toFixed(decimals).replace(/\.0+$/, '') + u.s
  }
  return String(Math.round(n * 100) / 100)
}

// ---------------------------------------------------------------------------
// Theme + base option helpers
// ---------------------------------------------------------------------------

function resolveTheme(theme) {
  return { ...DEFAULT_THEME, ...(theme || {}) }
}

function resolvePalette(config, theme) {
  if (Array.isArray(config.palette) && config.palette.length) return config.palette
  if (typeof config.palette === 'string' && theme[config.palette]) return [theme[config.palette]]
  return theme.palette
}

function legendConfig(config, theme, { singleSeries = false, hasTitle = false } = {}) {
  const l = config.legend
  if (l === false) return undefined
  // A legend distinguishing exactly one (ungrouped) series just repeats the
  // axis name — noise, not signal. Hide it unless the user explicitly asked
  // for one (config.legend set to a position/object rather than left auto).
  if (l === undefined && singleSeries) return undefined
  const pos = (isDeepObject(l) && l.position) || 'top'
  // Small round swatches + comfortable gaps read as considered chrome rather
  // than the default chunky rectangles.
  const base: Record<string, any> = {
    type: 'scroll',
    icon: 'circle',
    itemWidth: 8,
    itemHeight: 8,
    itemGap: 14,
    textStyle: { color: theme.fgMuted, fontSize: 11 },
  }
  switch (pos) {
    case 'bottom': base.bottom = 4; base.left = 'center'; break
    case 'left': base.left = 4; base.top = 'middle'; base.orient = 'vertical'; break
    case 'right': base.right = 4; base.top = 'middle'; base.orient = 'vertical'; break
    case 'top':
    default: base.top = hasTitle ? 34 : 4; base.left = 'center'
  }
  if (isDeepObject(l)) {
    const rest = { ...l }
    delete rest.position
    return deepMerge(base, rest)
  }
  return base
}

function titleConfig(config, theme) {
  const title = config.title
  const subtext = config.subtitle
  if (!title && !subtext) return undefined
  if (typeof title === 'string' || title == null) {
    // Left-aligned by default — matches how premium analytics products anchor a
    // card's title to its reading edge. Object-form titles pass through untouched.
    return {
      text: title || '',
      subtext: subtext || '',
      left: 4,
      top: 2,
      textStyle: { color: theme.fg, fontSize: 13, fontWeight: 600 },
      subtextStyle: { color: theme.fgMuted, fontSize: 11 },
    }
  }
  return title // object form passed through
}

/**
 * A concrete family for canvas text. A canvas rejects a font string naming a CSS
 * keyword such as 'inherit' and falls back to 10px sans-serif, whatever the size
 * and weight, so the family cannot be left to the cascade.
 */
function canvasFontFamily(config): string {
  if (typeof config.fontFamily === 'string' && config.fontFamily) return config.fontFamily
  const body = typeof document !== 'undefined' && document.body ? getComputedStyle(document.body).fontFamily : ''
  return body || 'sans-serif'
}

function axisCommon(theme): Record<string, any> {
  return {
    axisLine: { lineStyle: { color: theme.axis } },
    axisLabel: { color: theme.fgMuted, fontSize: 10 },
    nameTextStyle: { color: theme.fgMuted, fontSize: 11 },
    splitLine: { lineStyle: { color: theme.grid } },
  }
}

/**
 * Keys of an axis spec that this builder INTERPRETS. Everything else in the
 * spec is a raw ECharts axis option and is merged through verbatim — `show`,
 * `inverse`, `splitLine`, `axisLabel`, `nameGap`, `boundaryGap`, … — so an
 * author can reach any axis feature without the builder needing a case for it.
 * (`config.xAxis`/`yAxis` are stripped from the top-level passthrough merge, so
 * without this they would be silently dropped.)
 */
const AXIS_INTERPRETED_KEYS = new Set(['log', 'type', 'label', 'min', 'max', 'rotate', 'format'])

function buildAxis(kind, spec, theme, locale, defaults: Record<string, any> = {}) {
  const s = spec || {}
  const axis: Record<string, any> = { ...axisCommon(theme), ...defaults }
  if (kind === 'category') axis.type = 'category'
  else axis.type = s.log ? 'log' : (s.type || defaults.type || 'value')
  if (s.label != null) {
    axis.name = s.label
    axis.nameLocation = axis.nameLocation || 'middle'
    axis.nameGap = axis.nameGap || (kind === 'category' ? 28 : 44)
  }
  if (s.min != null) axis.min = s.min
  if (s.max != null) axis.max = s.max
  if (s.rotate != null) axis.axisLabel = { ...axis.axisLabel, rotate: s.rotate }
  // Formatting applies to category axes too — e.g. an ISO date string category
  // ("2026-06-15") with format:'date' renders as "Jun 15, 2026" tick labels.
  if (s.format) {
    const fmt = makeFormatter(s.format, locale)
    axis.axisLabel = { ...axis.axisLabel, formatter: (v) => fmt(v) }
  }
  // Uninterpreted keys last, so an explicit `axisLabel`/`splitLine` overrides
  // the themed default rather than being overwritten by it.
  const rest: Record<string, any> = {}
  for (const k of Object.keys(s)) {
    if (!AXIS_INTERPRETED_KEYS.has(k)) rest[k] = s[k]
  }
  return Object.keys(rest).length ? deepMerge(axis, rest) : axis
}

/**
 * nameGap for a NAMED value axis — whether the name was auto-derived from the
 * column or supplied by the author. Wide tick labels (e.g. "6,000,000") need
 * more clearance than the flat 44px default, or the rotated axis name overlaps
 * the outermost tick label. This used to run only for auto-derived names, which
 * had it backwards: an authored label is the one more likely to be on screen.
 */
function estimateValueAxisNameGap(values, axis) {
  if (!Array.isArray(values) || !values.length) return 44
  const nums = values.map(Number).filter(Number.isFinite)
  if (!nums.length) return 44
  const maxAbs = Math.max(...nums.map(Math.abs))
  const fmt = axis.axisLabel?.formatter
  const sample = fmt ? String(fmt(maxAbs)) : maxAbs.toLocaleString()
  const fontSize = axis.axisLabel?.fontSize ?? 10
  return Math.max(44, Math.round(sample.length * fontSize * 0.55) + 10)
}

/**
 * nameGap for a NAMED category axis. Tick labels take more room when they are
 * large or rotated, and the name has to clear them.
 */
function estimateCategoryAxisNameGap(cats, axis) {
  const fontSize = axis.axisLabel?.fontSize ?? 10
  const rotate = Number(axis.axisLabel?.rotate) || 0
  const longest = cats.reduce((m, c) => Math.max(m, String(c).length), 0)
  const labelHeight = rotate
    ? Math.abs(Math.sin((rotate * Math.PI) / 180)) * longest * fontSize * 0.55 + fontSize
    : fontSize
  return Math.max(28, Math.round(labelHeight) + 14)
}

function gridConfig(config, hasLegend, legendPos) {
  const hasTitle = !!(config.title || config.subtitle)
  const legendOnTop = hasLegend && (!legendPos || legendPos === 'top')
  const top = hasTitle
    ? (legendOnTop ? 76 : 56)
    : (legendOnTop ? 40 : 16)
  const bottom = hasLegend && legendPos === 'bottom' ? 44 : 36
  const left = hasLegend && legendPos === 'left' ? 90 : 52
  const right = hasLegend && legendPos === 'right' ? 110 : 24
  return deepMerge({ top, right, bottom, left, containLabel: true }, config.grid || {})
}

// Build reference lines (markLine) for a series.
function buildMarkLine(config, theme) {
  const refs = config.referenceLines
  if (!Array.isArray(refs) || !refs.length) return undefined
  const data = refs.map((r) => {
    const color = r.color || theme.primary
    const lineStyle = { color, type: r.type || 'dashed', width: r.width || 1.5 }
    const labelText = r.label != null ? r.label : (r.value != null ? String(r.value) : '')
    const label = { show: !!labelText, formatter: labelText, color, position: r.position || 'insideEndTop', fontSize: 10 }
    if (Array.isArray(r.from) && Array.isArray(r.to)) {
      return [{ coord: r.from, lineStyle, label }, { coord: r.to }]
    }
    if (r.axis === 'x') return { xAxis: r.value, lineStyle, label }
    return { yAxis: r.value, lineStyle, label }
  })
  return { silent: true, symbol: ['none', 'none'], data }
}

// Build annotations (markPoint) for a series.
function buildMarkPoint(config, theme) {
  const ann = config.annotations
  if (!Array.isArray(ann) || !ann.length) return undefined
  return {
    data: ann.map((a) => ({
      coord: [a.x, a.y],
      value: a.label,
      itemStyle: { color: a.color || theme.primary },
      label: { color: theme.fg, fontSize: 10 },
    })),
  }
}

// Attach markLine/markPoint/dataLabels to cartesian series.
function decorateCartesian(option, config, theme, locale) {
  if (!Array.isArray(option.series) || !option.series.length) return
  const ml = buildMarkLine(config, theme)
  const mp = buildMarkPoint(config, theme)
  const dl = config.dataLabels
  let labelObj
  if (dl) {
    const show = dl === true || dl.show !== false
    const fmt = isDeepObject(dl) && dl.format ? makeFormatter(dl.format, locale) : null
    labelObj = {
      show,
      fontSize: (isDeepObject(dl) && dl.fontSize) || 10,
      color: (isDeepObject(dl) && dl.color) || theme.fg,
      position: (isDeepObject(dl) && dl.position) || 'top',
    }
    if (fmt) labelObj.formatter = (p) => fmt(Array.isArray(p.value) ? p.value[1] : p.value)
  }
  option.series.forEach((s, i) => {
    if (ml && i === 0) s.markLine = ml
    if (mp && i === 0) s.markPoint = mp
    if (labelObj && (s.type === 'bar' || s.type === 'line' || s.type === 'scatter')) {
      s.label = deepMerge(labelObj, s.label || {})
    }
  })
}

/** Parse '#rgb' / '#rrggbb' / 'rgb(...)' / 'rgba(...)' into 0-255 channels, or null. */
function parseColorChannels(color) {
  if (typeof color !== 'string') return null
  const hex = color.match(/^#([0-9a-f]{3}|[0-9a-f]{6})$/i)
  if (hex) {
    let h = hex[1]
    if (h.length === 3) h = h.split('').map((c) => c + c).join('')
    const n = parseInt(h, 16)
    return { r: (n >> 16) & 255, g: (n >> 8) & 255, b: n & 255 }
  }
  const fn = color.match(/^rgba?\(\s*([\d.]+)[,\s]+([\d.]+)[,\s]+([\d.]+)/i)
  if (fn) return { r: Number(fn[1]), g: Number(fn[2]), b: Number(fn[3]) }
  return null
}

/** WCAG relative luminance (0 = black, 1 = white); defaults to mid-gray if unparseable. */
function relativeLuminance(color) {
  const ch = parseColorChannels(color)
  if (!ch) return 0.5
  const lin = (c) => {
    const s = c / 255
    return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4
  }
  return 0.2126 * lin(ch.r) + 0.7152 * lin(ch.g) + 0.0722 * lin(ch.b)
}

/** True when `color` reads as a dark surface (used to infer a tooltip bg/fg pair from legacy themes). */
function isDarkColor(color) {
  return relativeLuminance(color) < 0.5
}

function tooltipConfig(config, theme, { trigger = 'axis', locale }: { trigger?: string; locale?: any } = {}) {
  if (config.tooltip === false) return { show: false }
  // Legacy themes only carry `fg` (on-canvas text). Infer a readable tooltip
  // surface from it when the theme doesn't supply tooltipBg/tooltipFg directly —
  // a light theme.fg implies a dark app surface and vice versa.
  const appIsDark = theme.fg == null ? true : !isDarkColor(theme.fg)
  const bg = theme.tooltipBg ?? (appIsDark ? 'rgba(15,17,23,0.96)' : 'rgba(255,255,255,0.97)')
  const fg = theme.tooltipFg ?? (appIsDark ? '#f8fafc' : '#0f172a')
  const base: Record<string, any> = {
    trigger,
    confine: true,
    backgroundColor: bg,
    borderColor: theme.border,
    textStyle: { color: fg, fontSize: 11 },
    axisPointer: trigger === 'axis' ? { type: 'cross', crossStyle: { color: theme.fgMuted } } : undefined,
  }
  if (isDeepObject(config.tooltip)) {
    const t = { ...config.tooltip }
    if (t.valueFormat) {
      const fmt = makeFormatter(t.valueFormat, locale)
      base.valueFormatter = (v) => fmt(v)
      delete t.valueFormat
    }
    return deepMerge(base, t)
  }
  return base
}

// ---------------------------------------------------------------------------
// Encoding resolution — pick sensible default columns when not specified
// ---------------------------------------------------------------------------

function resolveEncoding(table, encoding) {
  const e = { ...(encoding || {}) }
  const cols = table.columns
  if (!e.x && cols[0]) e.x = cols[0]
  if (!e.y && cols[1]) e.y = cols[1]
  return e
}

// ===========================================================================
// Per-type builders
// ===========================================================================

function buildBar(table, e, config, theme, locale) {
  const horizontal = config.orientation === 'horizontal'
  const catRaw = table.col(e.x)
  const valRaw = table.col(e.y)
  if (!catRaw || !valRaw) return emptyOption(theme)
  const n = Math.min(catRaw.length, valRaw.length)
  const cats = toStrings(catRaw)
  const vals = toNumbers(valRaw)
  const stack = config.stack === true ? 'normal' : config.stack

  // No auto-title on the category axis: the category values (names, dates) are
  // self-describing, and an auto name collides with rotated tick labels. An
  // explicit config.xAxis.label still renders.
  const catAxis = buildAxis('category', config.xAxis, theme, locale, { data: cats, boundaryGap: true })
  if (n > 12 && !horizontal) catAxis.axisLabel = { ...catAxis.axisLabel, rotate: config.xAxis?.rotate ?? 30 }
  const valAxis = buildAxis('value', config.yAxis, theme, locale)
  if (!config.yAxis?.label && e.y) {
    valAxis.name = humaniseColumn(e.y); valAxis.nameLocation = 'middle'
  }
  if (valAxis.name) valAxis.nameGap = estimateValueAxisNameGap(vals, valAxis)
  if (catAxis.name && !config.xAxis?.nameGap) catAxis.nameGap = estimateCategoryAxisNameGap(cats, catAxis)

  const colorRaw = e.color ? table.col(e.color) : null
  let series
  if (colorRaw && !isNumericArray(colorRaw)) {
    const groups = groupByCategory(cats, vals, colorRaw)
    const allCats = Array.from(new Set(cats))
    catAxis.data = allCats
    series = groups.map((g) => {
      const m = new Map(g.a.map((c, i) => [String(c), g.b[i]]))
      const data = allCats.map((c) => (m.has(c) ? m.get(c) : 0))
      return {
        type: 'bar', name: g.category, data,
        stack: stack ? 'total' : undefined,
        barMaxWidth: 28,
        itemStyle: { borderRadius: stack ? 0 : [2, 2, 0, 0] },
      }
    })
    if (config.stack === 'percent') applyPercentStack(series)
  } else {
    // colorByPoint: paint each bar its own palette colour (a common BI look for
    // a single categorical breakdown, e.g. "revenue by product"). Otherwise all
    // bars share palette[0]. Generic — any single-series bar/hbar can opt in.
    const radius = horizontal ? [0, 2, 2, 0] : [2, 2, 0, 0]
    if (config.colorByPoint) {
      series = [{
        type: 'bar', name: humaniseColumn(e.y), barMaxWidth: config.barMaxWidth === undefined ? 40 : config.barMaxWidth ?? undefined,
        data: vals.map((v, i) => ({ value: v, itemStyle: { color: theme.palette[i % theme.palette.length], borderRadius: radius } })),
      }]
    } else {
      series = [{
        type: 'bar', name: humaniseColumn(e.y), data: vals,
        barMaxWidth: config.barMaxWidth === undefined ? 40 : config.barMaxWidth ?? undefined,
        itemStyle: { color: theme.palette[0], borderRadius: radius },
      }]
    }
  }

  const singleSeries = !(colorRaw && !isNumericArray(colorRaw))
  return {
    ...baseFrame(config, theme, locale, { trigger: 'axis', singleSeries }),
    xAxis: horizontal ? valAxis : catAxis,
    yAxis: horizontal ? catAxis : valAxis,
    series,
  }
}

function applyPercentStack(series) {
  const len = series[0]?.data?.length || 0
  for (let i = 0; i < len; i++) {
    let total = 0
    for (const s of series) total += Number(s.data[i]) || 0
    if (total === 0) continue
    for (const s of series) s.data[i] = +((Number(s.data[i]) || 0) / total * 100).toFixed(2)
  }
  for (const s of series) s.stack = 'total'
}

function buildLine(table, e, config, theme, locale, { area = false } = {}) {
  const xRaw = table.col(e.x)
  const yRaw = table.col(e.y)
  if (!xRaw || !yRaw) return emptyOption(theme)
  const n = Math.min(xRaw.length, yRaw.length)
  const xIsNumeric = isNumericArray(xRaw)
  const smooth = config.smooth !== false ? (config.smooth ?? true) : false
  const opacity = config.areaOpacity ?? 0.22
  const stack = config.stack === true ? 'total' : (config.stack || undefined)

  const xAxis = xIsNumeric
    ? buildAxis('value', config.xAxis, theme, locale)
    : buildAxis('category', config.xAxis, theme, locale, { data: toStrings(xRaw), boundaryGap: false })
  // Auto-title only a NUMERIC x-axis — categories (names, dates) are self-describing.
  if (xIsNumeric && !config.xAxis?.label && e.x) { xAxis.name = e.x; xAxis.nameLocation = 'middle'; xAxis.nameGap = 28 }
  if (!xIsNumeric && n > 20) xAxis.axisLabel = { ...xAxis.axisLabel, rotate: config.xAxis?.rotate ?? 30 }

  const hasY2 = !!e.y2
  const yVals = toNumbers(yRaw)
  const yAxis = buildAxis('value', config.yAxis, theme, locale)
  if (!config.yAxis?.label && e.y) {
    yAxis.name = humaniseColumn(e.y); yAxis.nameLocation = 'middle'
  }
  if (yAxis.name) yAxis.nameGap = estimateValueAxisNameGap(yVals, yAxis)
  const yAxes = [yAxis]
  if (hasY2) {
    const y2 = buildAxis('value', config.y2Axis, theme, locale)
    if (!config.y2Axis?.label) {
      y2.name = humaniseColumn(e.y2); y2.nameLocation = 'middle'
      y2.nameGap = estimateValueAxisNameGap(toNumbers(table.col(e.y2) || []), y2)
    }
    yAxes.push(y2)
  }
  const sampling = n > 5000 ? 'lttb' : undefined
  const colorRaw = e.color ? table.col(e.color) : null
  let series
  if (colorRaw && !isNumericArray(colorRaw)) {
    if (xIsNumeric) {
      const groups = groupByCategory(toNumbers(xRaw), yVals, colorRaw)
      series = groups.map((g) => ({
        type: 'line', name: g.category,
        data: g.a.map((x, i) => [x, g.b[i]]),
        smooth, sampling, showSymbol: n <= 60,
        stack: area && stack ? stack : undefined,
        areaStyle: area ? { opacity } : undefined,
      }))
    } else {
      // Long-format data ("day, metric, value") repeats each category once per
      // series, so the axis must be DEDUPED and every series re-indexed onto it
      // — same rule buildBar already applies. Grouping on row index instead
      // would leave the axis with one slot per ROW and squash each line into
      // the first 1/Nth of the plot. Missing points are null (a gap) rather
      // than bar's 0, since a line must not dive to zero for absent data.
      const cats = toStrings(xRaw)
      const allCats = Array.from(new Set(cats))
      xAxis.data = allCats
      if (allCats.length > 20) xAxis.axisLabel = { ...xAxis.axisLabel, rotate: config.xAxis?.rotate ?? 30 }
      const groups = groupByCategory(cats, yVals, colorRaw)
      series = groups.map((g) => {
        const m = new Map(g.a.map((c, i) => [String(c), g.b[i]]))
        return {
          type: 'line', name: g.category,
          data: allCats.map((c) => (m.has(c) ? m.get(c) : null)),
          smooth, sampling, showSymbol: allCats.length <= 60,
          connectNulls: false,
          stack: area && stack ? stack : undefined,
          areaStyle: area ? { opacity } : undefined,
        }
      })
    }
    if (config.stack === 'percent' && area) applyPercentStack(series)
  } else {
    series = [{
      type: 'line', name: humaniseColumn(e.y),
      data: xIsNumeric ? toNumbers(xRaw).map((x, i) => [x, yVals[i]]) : yVals,
      smooth, sampling, showSymbol: n <= 60,
      itemStyle: { color: theme.palette[0] }, lineStyle: { color: theme.palette[0] },
      areaStyle: area ? { color: theme.palette[0], opacity } : undefined,
    }]
    if (hasY2) {
      const y2Vals = toNumbers(table.col(e.y2) || [])
      series.push({
        type: 'line', name: humaniseColumn(e.y2), yAxisIndex: 1,
        data: xIsNumeric ? toNumbers(xRaw).map((x, i) => [x, y2Vals[i]]) : y2Vals,
        smooth, showSymbol: n <= 60,
        itemStyle: { color: theme.palette[1] }, lineStyle: { color: theme.palette[1] },
      })
    }
  }

  const singleSeries = !(colorRaw && !isNumericArray(colorRaw)) && !hasY2
  const option: Record<string, any> = {
    ...baseFrame(config, theme, locale, { trigger: 'axis', dataZoom: n > 100, singleSeries }),
    xAxis,
    yAxis: hasY2 ? yAxes : yAxis,
    series,
  }
  // Secondary-axis tick labels + title need a wider right gutter (see buildCombo).
  if (hasY2 && option.grid && config.grid?.right == null) {
    option.grid.right = 48
  }
  return option
}

// Bar+line combo: one or more bar series on the primary y-axis, one or more
// line series on a secondary y-axis, sharing one category x-axis. Unlike
// buildBar/buildLine (which take a single `y` column, optionally grouped by
// `color`), combo takes explicit column-name ARRAYS so heterogeneous series
// (e.g. 3 volume bars + 2 rate% lines) can share one chart.
//   encoding: { x: 'region', bars: ['units_shipped', 'units_delivered'],
//               lines: ['fill_rate', 'on_time_pct'] }
//   config:   { yAxis: {...}, y2Axis: {...} }  // bars use yAxis, lines use y2Axis
function buildCombo(table, e, config, theme, locale) {
  const catRaw = table.col(e.x)
  if (!catRaw) return emptyOption(theme)
  const cats = toStrings(catRaw)
  const n = cats.length
  const barCols = Array.isArray(e.bars) ? e.bars : (e.bars ? [e.bars] : [])
  const lineCols = Array.isArray(e.lines) ? e.lines : (e.lines ? [e.lines] : [])
  if (!barCols.length && !lineCols.length) return emptyOption(theme)

  // No auto-title on the category axis (self-describing values; collides with
  // rotated tick labels). An explicit config.xAxis.label still renders.
  const catAxis = buildAxis('category', config.xAxis, theme, locale, { data: cats, boundaryGap: true })
  // Combo categories are often named entities (branch/region) rather than short
  // codes, so rotate on label length too, not just count — n>12 alone (buildBar's
  // rule) still overlaps a handful of long names.
  const maxLabelLen = cats.reduce((m, c) => Math.max(m, c.length), 0)
  if (n > 12 || maxLabelLen > 10) catAxis.axisLabel = { ...catAxis.axisLabel, rotate: config.xAxis?.rotate ?? 30 }

  // yAxis is always the left axis and y2Axis the right. By default the bars read
  // the left one; config.lineAxis:'left' swaps them.
  const linesLeft = config.lineAxis === 'left' && barCols.length > 0 && lineCols.length > 0
  const leftCol = linesLeft ? lineCols[0] : barCols[0]
  const rightCol = linesLeft ? barCols[0] : lineCols[0]
  // An axis's ticks span every series plotted on it, so the name has to clear the
  // widest of them, not just the first column's.
  const valuesOf = (cols) => cols.flatMap((col) => toNumbers(table.col(col) || []))
  const leftValues = valuesOf(linesLeft ? lineCols : barCols.length ? barCols : lineCols)
  const rightValues = valuesOf(linesLeft ? barCols : lineCols)
  const yAxis = buildAxis('value', config.yAxis, theme, locale)
  if (!config.yAxis?.label && leftCol) { yAxis.name = leftCol; yAxis.nameLocation = 'middle' }
  if (yAxis.name) yAxis.nameGap = estimateValueAxisNameGap(leftValues, yAxis)
  const y2Axis = buildAxis('value', config.y2Axis, theme, locale)
  if (!config.y2Axis?.label && rightCol) { y2Axis.name = rightCol; y2Axis.nameLocation = 'middle' }
  if (y2Axis.name) y2Axis.nameGap = estimateValueAxisNameGap(rightValues, y2Axis)
  if (catAxis.name && !config.xAxis?.nameGap) catAxis.nameGap = estimateCategoryAxisNameGap(cats, catAxis)

  const palette = resolvePalette(config, theme)
  // config.stack stacks the BAR series (lines are never stacked in a combo).
  const stacked = config.stack === true || config.stack === 'normal' || config.stack === 'percent'
  const lineColor = (i) => (Array.isArray(config.lineColors) && config.lineColors.length
    ? config.lineColors[i % config.lineColors.length]
    : palette[(barCols.length + i) % palette.length])
  // colorByPoint paints one bar series a palette colour per category.
  const byPoint = config.colorByPoint && barCols.length === 1
  const barSeries = barCols.map((col, i) => ({
    type: 'bar', name: humaniseColumn(col), yAxisIndex: linesLeft ? 1 : 0,
    data: byPoint
      ? toNumbers(table.col(col) || []).map((v, k) => ({ value: v, itemStyle: { color: palette[k % palette.length] } }))
      : toNumbers(table.col(col) || []),
    barMaxWidth: config.barMaxWidth === undefined ? (stacked ? 28 : 24) : config.barMaxWidth ?? undefined,
    stack: stacked ? 'total' : undefined,
    itemStyle: { color: palette[i % palette.length], borderRadius: stacked ? 0 : [2, 2, 0, 0] },
  }))
  if (config.stack === 'percent') applyPercentStack(barSeries)
  const lineSeries = lineCols.map((col, i) => ({
    type: 'line', name: humaniseColumn(col), yAxisIndex: linesLeft ? 0 : 1,
    data: toNumbers(table.col(col) || []),
    smooth: config.smooth !== false,
    showSymbol: n <= 60,
    ...(config.symbolSize ? { symbol: 'circle', symbolSize: config.symbolSize } : {}),
    itemStyle: { color: lineColor(i) },
    lineStyle: { color: lineColor(i), width: 2 },
  }))

  const option: Record<string, any> = {
    ...baseFrame(config, theme, locale, { trigger: 'axis', singleSeries: false }),
    xAxis: catAxis,
    yAxis: lineCols.length ? [yAxis, y2Axis] : yAxis,
    series: [...barSeries, ...lineSeries],
  }
  // A secondary axis carries its own tick labels + title on the right — the
  // default 24px gutter clips them. Only widen when the author didn't say.
  if (lineCols.length && option.grid && config.grid?.right == null) {
    option.grid.right = 48
  }
  return option
}

function buildScatter(table, e, config, theme, locale, { bubble = false } = {}) {
  const xRaw = table.col(e.x)
  const yRaw = table.col(e.y)
  if (!xRaw || !yRaw) return emptyOption(theme)
  const n = Math.min(xRaw.length, yRaw.length)
  const xVals = toNumbers(xRaw)
  const yVals = toNumbers(yRaw)
  const large = n > 5000
  const sampling = n > 100_000 ? 'lttb' : undefined

  const xAxis = buildAxis('value', config.xAxis, theme, locale, { scale: true })
  if (!config.xAxis?.label && e.x) { xAxis.name = e.x; xAxis.nameLocation = 'middle'; xAxis.nameGap = 28 }
  const yAxis = buildAxis('value', config.yAxis, theme, locale, { scale: true })
  if (!config.yAxis?.label && e.y) {
    yAxis.name = humaniseColumn(e.y); yAxis.nameLocation = 'middle'
  }
  if (yAxis.name) yAxis.nameGap = estimateValueAxisNameGap(yVals, yAxis)

  const sizeRaw = bubble && e.size ? toNumbers(table.col(e.size) || []) : null
  let sizeScale = null
  if (sizeRaw) {
    const max = Math.max(...sizeRaw, 1)
    sizeScale = (v) => 6 + (Math.sqrt(Math.max(v, 0) / max) * 34)
  }

  const colorRaw = e.color ? table.col(e.color) : null
  let series
  if (colorRaw && !isNumericArray(colorRaw)) {
    const groups = groupByCategory(xVals, yVals, colorRaw)
    series = groups.map((g) => ({
      type: 'scatter', name: g.category,
      data: g.a.map((x, i) => [x, g.b[i]]),
      symbolSize: sizeScale ? ((val) => sizeScale(val[2] ?? 1)) : (large ? 4 : 8),
      large, largeThreshold: 2000, sampling,
    }))
  } else {
    const data = bubble && sizeRaw
      ? xVals.map((x, i) => [x, yVals[i], sizeRaw[i]])
      : xVals.map((x, i) => [x, yVals[i]])
    series = [{
      type: 'scatter',
      data,
      symbolSize: sizeScale ? ((val) => sizeScale(val[2] ?? 1)) : (large ? 4 : 8),
      itemStyle: { color: theme.palette[0], opacity: 0.8 },
      large, largeThreshold: 2000, sampling,
    }]
  }

  return {
    ...baseFrame(config, theme, locale, { trigger: 'item', dataZoom: n > 200 }),
    xAxis, yAxis, series,
  }
}

function buildPie(table, e, config, theme, locale, { donut = false } = {}) {
  const nameRaw = table.col(e.x || e.label)
  const valRaw = table.col(e.y || e.value)
  if (!nameRaw || !valRaw) return emptyOption(theme)
  const names = toStrings(nameRaw)
  const vals = toNumbers(valRaw)
  const n = names.length
  const dl = config.dataLabels
  const showLabel = dl === false ? false : (n <= 14)
  const fmt = isDeepObject(dl) && dl.format ? makeFormatter(dl.format, locale) : null
  const labelInside = isDeepObject(dl) && dl.position === 'inside'
  const labelFormatter = isDeepObject(dl) && typeof dl.formatter === 'string' ? dl.formatter : null

  // Pie legend defaults to bottom unless the user said otherwise.
  const legendCfg = config.legend === false ? undefined
    : legendConfig(isDeepObject(config.legend) ? config : { legend: { position: 'bottom' } }, theme)

  const frame = baseFrame(config, theme, locale, { trigger: 'item', noGrid: true })
  const option: Record<string, any> = {
    ...frame,
    legend: legendCfg,
    series: [{
      type: 'pie', name: e.y || e.value,
      data: names.map((name, i) => ({ name, value: vals[i] })),
      radius: donut ? ['48%', '72%'] : ['0%', '68%'],
      center: ['50%', '46%'],
      // `dataLabels.position: 'inside'` draws the label on the slice itself and
      // drops the leader line — outside labels need horizontal room the tile
      // often doesn't have, and get clipped. `dataLabels.formatter` overrides
      // the label template outright (e.g. '{d}%' for percentage-only rings).
      label: {
        show: showLabel,
        color: labelInside ? '#fff' : theme.fg,
        fontSize: 10,
        position: labelInside ? 'inside' : 'outside',
        formatter: labelFormatter ?? (fmt ? (p) => `${p.name}: ${fmt(p.value)}` : '{b}: {d}%'),
      },
      labelLine: { show: showLabel && !labelInside },
      itemStyle: { borderColor: theme.border, borderWidth: 1 },
    }],
  }

  // A big centered number (e.g. "97.4%") — typically used on a donut standing
  // in for a KPI ring. Defaults to the FIRST segment's share of the total
  // (the "value" slice of a value/remainder ring — callers order rows with
  // the meaningful slice first, same convention as buildGauge's single value).
  if (config.centerLabel) {
    const cl = config.centerLabel === true ? {} : config.centerLabel
    let text = cl.text
    if (text == null) {
      const total = vals.reduce((sum, v) => sum + (Number(v) || 0), 0)
      const idx = Number.isInteger(cl.index) ? cl.index : 0
      const share = total > 0 ? (vals[idx] / total) * 100 : 0
      const clFmt = cl.format ? makeFormatter(cl.format, locale) : null
      text = clFmt ? clFmt(share) : `${share.toFixed(1)}%`
    }
    option.graphic = [{
      type: 'text', left: 'center', top: '42%', z: 10,
      style: {
        text, fontSize: cl.fontSize || 22, fontWeight: 600,
        fill: cl.color || theme.fg, align: 'center',
      },
    }]
  }

  return option
}

/**
 * Polar area chart (legacy "Polar Chart" / Chart.js `polarArea`).
 *
 * Every sector gets the SAME central angle and encodes its value as the
 * RADIUS — which is what ECharts spells `roseType: 'area'` (the other rose
 * mode, `'radius'`, varies the angle instead and is the Nightingale variant).
 * Slice colours come from the palette, one per category, so it reads as a
 * ring of comparable wedges rather than a single-hue pie.
 */
function buildPolar(table, e, config, theme, locale) {
  const nameRaw = table.col(e.x || e.label)
  const valRaw = table.col(e.y || e.value)
  if (!nameRaw || !valRaw) return emptyOption(theme)
  const names = toStrings(nameRaw)
  const vals = toNumbers(valRaw)
  const dl = config.dataLabels
  const showLabel = dl === false ? false : names.length <= 14
  const fmt = isDeepObject(dl) && dl.format ? makeFormatter(dl.format, locale) : null

  // Same convention as buildPie: a categorical legend belongs at the bottom
  // unless the author said otherwise.
  const legendCfg = config.legend === false ? undefined
    : legendConfig(isDeepObject(config.legend) ? config : { legend: { position: 'bottom' } }, theme)

  return {
    ...baseFrame(config, theme, locale, { trigger: 'item', noGrid: true }),
    legend: legendCfg,
    series: [{
      type: 'pie',
      roseType: 'area',
      name: e.y || e.value,
      data: names.map((name, i) => ({ name, value: vals[i] })),
      radius: ['12%', '72%'],
      center: ['50%', '46%'],
      label: {
        show: showLabel,
        color: theme.fg,
        fontSize: 10,
        formatter: fmt ? (p) => `${p.name}: ${fmt(p.value)}` : '{b}',
      },
      labelLine: { show: showLabel },
      itemStyle: { borderColor: theme.border, borderWidth: 1 },
    }],
  }
}

/**
 * Radial bar chart (legacy "Radial Bar Chart" / ApexCharts `radialBar`).
 *
 * One concentric ring per category, each filled clockwise to `value / max` of
 * the full circle. Built as bars on a polar coordinate system: a category
 * radius axis (one band per ring) crossed with a 0..max angle axis, plus a
 * translucent full-circle background bar per ring so an unfilled ring still
 * reads as a track.
 *
 * `config.max` (or `config.yAxis.max`) sets the scale; it defaults to 100
 * because these are overwhelmingly percentage tiles in the legacy estate.
 */
function buildRadialBar(table, e, config, theme, locale) {
  const nameRaw = table.col(e.x || e.label)
  const valRaw = table.col(e.y || e.value)
  if (!nameRaw || !valRaw) return emptyOption(theme)
  // Outermost ring first reads top-to-bottom in the legend, so reverse the
  // category axis (ECharts stacks radius-axis categories inside-out).
  const names = toStrings(nameRaw).slice().reverse()
  const vals = toNumbers(valRaw).slice().reverse()
  const max = config.yAxis?.max ?? config.max ?? 100
  const min = config.yAxis?.min ?? config.min ?? 0
  const palette = resolvePalette(config, theme)
  const dl = config.dataLabels
  const fmt = isDeepObject(dl) && dl.format ? makeFormatter(dl.format, locale) : null
  const showLabel = dl !== false

  return {
    ...baseFrame(config, theme, locale, { trigger: 'item', noGrid: true }),
    legend: config.legend === false ? undefined
      : legendConfig(isDeepObject(config.legend) ? config : { legend: { position: 'bottom' } }, theme),
    polar: { center: ['50%', '50%'], radius: ['24%', '82%'] },
    angleAxis: {
      type: 'value', min, max, startAngle: 90, clockwise: true,
      axisLine: { show: false }, axisTick: { show: false },
      axisLabel: { show: false }, splitLine: { show: false },
    },
    radiusAxis: {
      type: 'category', data: names,
      axisLine: { show: false }, axisTick: { show: false },
      splitLine: { show: false },
      axisLabel: { color: theme.fgMuted, fontSize: 10 },
      z: 10,
    },
    series: [
      // Track ring — a full-circle bar behind each value bar.
      {
        type: 'bar', coordinateSystem: 'polar', silent: true, z: 1,
        roundCap: true, barGap: '-100%',
        itemStyle: { color: 'rgba(148,163,184,0.18)' },
        data: names.map(() => max),
      },
      // Value ring.
      {
        type: 'bar', coordinateSystem: 'polar', z: 2,
        roundCap: true, barGap: '-100%',
        name: e.y || e.value,
        data: vals.map((v, i) => ({
          value: v,
          itemStyle: { color: palette[(names.length - 1 - i) % palette.length] },
        })),
        label: {
          show: showLabel, position: 'middle', color: theme.fg, fontSize: 10,
          formatter: fmt ? (p) => fmt(p.value) : (p) => String(p.value),
        },
      },
    ],
  }
}

function buildSankey(table, e, config, theme, locale) {
  const srcRaw = table.col(e.source || e.x)
  const tgtRaw = table.col(e.target || e.color)
  const valRaw = table.col(e.value || e.y)
  if (!srcRaw || !tgtRaw || !valRaw) return emptyOption(theme)
  const src = toStrings(srcRaw)
  const tgt = toStrings(tgtRaw)
  const vals = toNumbers(valRaw)
  const nodeSet = new Set()
  src.forEach((s) => nodeSet.add(s))
  tgt.forEach((t) => nodeSet.add(t))
  const links = src.map((s, i) => ({ source: s, target: tgt[i], value: vals[i] }))

  return {
    ...baseFrame(config, theme, locale, { noGrid: true }),
    tooltip: tooltipConfig(config, theme, { trigger: 'item', locale }),
    series: [{
      type: 'sankey',
      data: Array.from(nodeSet).map((name) => ({ name })),
      links,
      emphasis: { focus: 'adjacency' },
      label: { color: theme.fg, fontSize: 11 },
      lineStyle: { color: 'gradient', opacity: 0.4 },
      nodeAlign: 'justify',
    }],
  }
}

function buildFunnel(table, e, config, theme, locale) {
  const nameRaw = table.col(e.x || e.label)
  const valRaw = table.col(e.y || e.value)
  if (!nameRaw || !valRaw) return emptyOption(theme)
  const names = toStrings(nameRaw)
  const vals = toNumbers(valRaw)
  const fmt = isDeepObject(config.dataLabels) && config.dataLabels.format
    ? makeFormatter(config.dataLabels.format, locale) : null
  return {
    ...baseFrame(config, theme, locale, { trigger: 'item', noGrid: true }),
    legend: config.legend === false ? undefined : legendConfig(config, theme),
    series: [{
      type: 'funnel',
      data: names.map((name, i) => ({ name, value: vals[i] })),
      sort: 'descending', gap: 2, left: '10%', right: '10%', top: 50, bottom: 20,
      label: {
        show: config.dataLabels !== false, color: theme.fg, position: 'inside',
        formatter: fmt ? (p) => `${p.name}: ${fmt(p.value)}` : '{b}: {c}',
      },
      itemStyle: { borderColor: theme.border, borderWidth: 1 },
    }],
  }
}

function buildWaterfall(table, e, config, theme, locale) {
  const nameRaw = table.col(e.x)
  const valRaw = table.col(e.y)
  if (!nameRaw || !valRaw) return emptyOption(theme)
  const names = toStrings(nameRaw)
  const deltas = toNumbers(valRaw)
  const base = []
  const up = []
  const down = []
  let running = 0
  for (let i = 0; i < deltas.length; i++) {
    const d = deltas[i]
    if (d >= 0) { base.push(running); up.push(d); down.push('-') }
    else { base.push(running + d); up.push('-'); down.push(-d) }
    running += d
  }
  const xAxis = buildAxis('category', config.xAxis, theme, locale, { data: names })
  const yAxis = buildAxis('value', config.yAxis, theme, locale)
  if (!config.yAxis?.label && e.y) {
    yAxis.name = humaniseColumn(e.y); yAxis.nameLocation = 'middle'
    const rendered = base.map((b, i) => b + (typeof up[i] === 'number' ? up[i] : 0))
    yAxis.nameGap = estimateValueAxisNameGap([...base, ...rendered], yAxis)
  }

  return {
    ...baseFrame(config, theme, locale, { trigger: 'axis' }),
    legend: config.legend === false ? undefined : legendConfig(config, theme),
    xAxis, yAxis,
    series: [
      { type: 'bar', name: 'base', stack: 'wf', itemStyle: { color: 'transparent' }, emphasis: { itemStyle: { color: 'transparent' } }, data: base, silent: true, tooltip: { show: false } },
      { type: 'bar', name: 'increase', stack: 'wf', itemStyle: { color: theme.palette[2] || '#10b981' }, data: up },
      { type: 'bar', name: 'decrease', stack: 'wf', itemStyle: { color: theme.palette[3] || '#ef4444' }, data: down },
    ],
  }
}

function buildHeatmap(table, e, config, theme, locale) {
  const xRaw = table.col(e.x)
  const yRaw = table.col(e.color || e.y)
  const valRaw = table.col(e.value || e.y)
  if (!xRaw || !yRaw || !valRaw) return emptyOption(theme)
  const xs = toStrings(xRaw)
  const ys = toStrings(yRaw)
  const vals = toNumbers(valRaw)
  const xCats = Array.from(new Set(xs))
  const yCats = Array.from(new Set(ys))
  const xi = new Map(xCats.map((c, i) => [c, i]))
  const yi = new Map(yCats.map((c, i) => [c, i]))
  const data = xs.map((x, i) => [xi.get(x), yi.get(ys[i]), vals[i]])
  const max = Math.max(...vals, 0)
  const min = Math.min(...vals, 0)

  // The colour ramp used to be hardcoded off `theme.palette`, so a chart with
  // its own `config.palette` was the one chart on a themed board that stayed in
  // the stock blues. An explicit `config.heatColors` (low -> high) wins;
  // otherwise the two upper stops come from the chart's resolved palette,
  // keeping the previous convention that the strongest value takes palette[0].
  const heatPalette = resolvePalette(config, theme)
  const heatRamp = Array.isArray(config.heatColors) && config.heatColors.length >= 2
    ? config.heatColors
    : ['#0f1117', heatPalette[4] || heatPalette[1] || '#3b82f6', heatPalette[0] || '#6366f1']

  // `config.heatColorBy: 'x' | 'y'` paints each cell by its column (or row)
  // category from the palette instead of by value — a coloured matrix, where
  // the numbers are read from the labels rather than from a colour ramp.
  const colorBy = config.heatColorBy === 'x' || config.heatColorBy === 'y' ? config.heatColorBy : null
  // ECharts refuses a heatmap without a visualMap, so the categorical mode is
  // still a visualMap — a hidden piecewise one over the x (or y) index.
  const colorCats = colorBy === 'x' ? xCats : yCats
  const categoricalMap = colorBy ? {
    type: 'piecewise', show: false, dimension: colorBy === 'x' ? 0 : 1,
    pieces: colorCats.map((_, i) => ({ value: i, color: heatPalette[i % heatPalette.length] })),
  } : null
  const cellBorder = colorBy && config.heatGap !== false
    ? { borderColor: config.heatGapColor || '#ffffff', borderWidth: 2 }
    : null
  const rotate = Number.isFinite(config.xLabelRotate) ? config.xLabelRotate : (xCats.length > 10 ? 30 : 0)

  return {
    ...baseFrame(config, theme, locale, { trigger: 'item', noGrid: true }),
    grid: deepMerge({ top: config.title ? 56 : 16, left: 80, right: 24, bottom: colorBy ? 16 : 64, containLabel: true }, config.grid || {}),
    tooltip: tooltipConfig(config, theme, { trigger: 'item', locale }),
    xAxis: { type: 'category', data: xCats, splitArea: { show: !colorBy }, axisLine: { show: !colorBy }, axisTick: { show: !colorBy }, axisLabel: { color: theme.fgMuted, fontSize: 10, rotate } },
    yAxis: { type: 'category', data: yCats, inverse: config.yInverse === true, splitArea: { show: !colorBy }, axisLine: { show: !colorBy }, axisTick: { show: !colorBy }, axisLabel: { color: theme.fgMuted, fontSize: 10 } },
    visualMap: categoricalMap ?? {
      min, max, calculable: true, orient: 'horizontal', left: 'center', bottom: 8,
      textStyle: { color: theme.fgMuted, fontSize: 10 },
      inRange: { color: heatRamp },
    },
    series: [{
      type: 'heatmap', data,
      ...(cellBorder ? { itemStyle: cellBorder } : {}),
      label: { show: config.dataLabels === true, color: theme.fg, fontSize: 9 },
      emphasis: { itemStyle: { shadowBlur: 8, shadowColor: 'rgba(0,0,0,0.4)' } },
    }],
  }
}

function buildRadar(table, e, config, theme, locale) {
  const indicatorRaw = table.col(e.x)
  const valRaw = table.col(e.y)
  if (!indicatorRaw || !valRaw) return emptyOption(theme)
  const indicators = toStrings(indicatorRaw)
  const vals = toNumbers(valRaw)
  const colorRaw = e.color ? table.col(e.color) : null

  let groups
  if (colorRaw) {
    const map = groupByCategory(indicators, vals, colorRaw)
    groups = map.map((g) => ({ name: g.category, indicators: g.a, values: g.b }))
  } else {
    groups = [{ name: e.y || 'series', indicators, values: vals }]
  }
  const indicatorNames = groups[0].indicators
  const globalMax = Math.max(...vals, 1) * 1.1

  return {
    ...baseFrame(config, theme, locale, { noGrid: true }),
    tooltip: tooltipConfig(config, theme, { trigger: 'item', locale }),
    legend: config.legend === false ? undefined : legendConfig(config, theme),
    radar: {
      indicator: indicatorNames.map((name) => ({ name, max: globalMax })),
      axisName: { color: theme.fgMuted, fontSize: 11 },
      splitLine: { lineStyle: { color: theme.grid } },
      splitArea: { areaStyle: { color: ['transparent'] } },
      axisLine: { lineStyle: { color: theme.grid } },
      center: ['50%', '54%'], radius: '62%',
    },
    series: [{
      type: 'radar',
      data: groups.map((g) => ({ name: g.name, value: g.values, areaStyle: { opacity: 0.12 } })),
    }],
  }
}

function buildTreemap(table, e, config, theme, locale) {
  const nameRaw = table.col(e.x || e.label)
  const valRaw = table.col(e.y || e.value)
  if (!nameRaw || !valRaw) return emptyOption(theme)
  const names = toStrings(nameRaw)
  const vals = toNumbers(valRaw)
  const groupRaw = e.color ? toStrings(table.col(e.color)) : null

  let data
  if (groupRaw) {
    const parents = new Map()
    for (let i = 0; i < names.length; i++) {
      const p = groupRaw[i]
      if (!parents.has(p)) parents.set(p, { name: p, children: [] })
      parents.get(p).children.push({ name: names[i], value: vals[i] })
    }
    data = Array.from(parents.values())
  } else {
    data = names.map((name, i) => ({ name, value: vals[i] }))
  }

  return {
    ...baseFrame(config, theme, locale, { trigger: 'item', noGrid: true }),
    tooltip: tooltipConfig(config, theme, { trigger: 'item', locale }),
    series: [{
      type: 'treemap', data, roam: false,
      label: { show: true, color: '#fff', fontSize: 11 },
      upperLabel: { show: !!groupRaw, height: 22, color: theme.fg },
      breadcrumb: { show: false },
      levels: [
        { itemStyle: { borderColor: theme.border, borderWidth: 2, gapWidth: 2 } },
        { itemStyle: { gapWidth: 1 } },
      ],
    }],
  }
}

/**
 * Choropleth map. Colours named regions of a registered GeoJSON map by a metric.
 *
 *   encoding: { name: '<region column>', value: '<metric column>' }
 *             (x/y and label/value are accepted as aliases)
 *   config.map: name of a map registered via echarts.registerMap() — see
 *               registerMapGeoJson() in src/lib/maps.js. Defaults to 'world'.
 *
 * Region values in the data must match the GeoJSON feature names. Rows whose
 * name doesn't match simply don't paint — ECharts ignores them — so a partial
 * match degrades gracefully instead of erroring.
 *
 * `config.roam` enables pan/zoom (off by default: a dashboard tile is usually
 * a fixed view, and roam swallows scroll events on a scrolling board).
 */
function buildMap(table, e, config, theme, locale) {
  const nameRaw = table.col(e.name || e.x || e.label)
  const valRaw = table.col(e.value || e.y)
  if (!nameRaw || !valRaw) return emptyOption(theme)
  const names = toStrings(nameRaw)
  const vals = toNumbers(valRaw)
  const data = names.map((name, i) => ({ name, value: vals[i] }))

  const finite = vals.filter((v) => Number.isFinite(v))
  const min = finite.length ? Math.min(...finite) : 0
  const max = finite.length ? Math.max(...finite) : 1

  const palette = resolvePalette(config, theme)
  // Low end is a neutral fill so unmatched/low regions read as "background".
  const lowColor = config.lowColor ?? theme.grid ?? '#e5e7eb'
  const highColor = config.highColor ?? palette[0]

  return {
    // `singleSeries` suppresses the auto legend. A choropleth has one series
    // and the visualMap below already IS its scale, so a legend adds nothing —
    // and worse, ECharts derives legend entries from the series data (one per
    // region) and paints each entry's symbol at that region's centroid, so the
    // map came out covered in stray dots.
    ...baseFrame(config, theme, locale, { trigger: 'item', noGrid: true, singleSeries: true }),
    tooltip: tooltipConfig(config, theme, { trigger: 'item', locale }),
    // visualMap does double duty: it IS the colour mapping as well as the
    // on-screen scale. `legend:false` must therefore only hide the control
    // (`show:false`) — dropping the block entirely would leave every region
    // unpainted. A single-stop range would make it emit NaN colours, so widen.
    visualMap: {
      type: 'continuous',
      show: config.legend !== false,
      min,
      max: max > min ? max : min + 1,
      left: 8,
      bottom: 8,
      calculable: true,
      inRange: { color: [lowColor, highColor] },
      textStyle: { color: theme.fgMuted ?? theme.fg },
    },
    series: [{
      type: 'map',
      map: config.map || 'world',
      data,
      roam: !!config.roam,
      // Keep the shape inside the tile regardless of the tile's aspect ratio.
      layoutCenter: ['50%', '50%'],
      layoutSize: '92%',
      label: { show: !!config.dataLabels, color: theme.fg, fontSize: 10 },
      itemStyle: { borderColor: theme.border, borderWidth: 0.5 },
      emphasis: {
        label: { show: true, color: theme.fg },
        itemStyle: { areaColor: palette[1] ?? highColor },
      },
    }],
  }
}

function buildBoxplot(table, e, config, theme, locale) {
  const catRaw = table.col(e.x)
  const valRaw = table.col(e.y)
  if (!catRaw || !valRaw) return emptyOption(theme)
  const cats = toStrings(catRaw)
  const vals = toNumbers(valRaw)
  const buckets = new Map()
  for (let i = 0; i < cats.length; i++) {
    if (!buckets.has(cats[i])) buckets.set(cats[i], [])
    buckets.get(cats[i]).push(vals[i])
  }
  const names = Array.from(buckets.keys())
  const boxData = names.map((name) => fiveNumberSummary(buckets.get(name)))

  const xAxis = buildAxis('category', config.xAxis, theme, locale, { data: names, boundaryGap: true })
  const yAxis = buildAxis('value', config.yAxis, theme, locale)
  if (!config.yAxis?.label && e.y) {
    yAxis.name = humaniseColumn(e.y); yAxis.nameLocation = 'middle'
    yAxis.nameGap = estimateValueAxisNameGap(vals, yAxis)
  }

  return {
    ...baseFrame(config, theme, locale, { trigger: 'item' }),
    xAxis, yAxis,
    series: [{
      type: 'boxplot', data: boxData,
      itemStyle: { color: 'rgba(99,102,241,0.25)', borderColor: theme.palette[0] },
    }],
  }
}

function fiveNumberSummary(arr) {
  const s = [...arr].sort((a, b) => a - b)
  const q = (p) => {
    const idx = (s.length - 1) * p
    const lo = Math.floor(idx), hi = Math.ceil(idx)
    return lo === hi ? s[lo] : s[lo] + (s[hi] - s[lo]) * (idx - lo)
  }
  return [s[0], q(0.25), q(0.5), q(0.75), s[s.length - 1]]
}

// Gauge arc colours from config.gaugeArc*. None set -> the stock look
// (needle: 3 theme bands; ring: palette[0]). A solid gaugeArcColor paints the
// progress arc. A gradient can't go on `progress`: ECharts fits a gradient to
// the element's bounding box, so it runs left->right instead of along the
// curve and a short fill squeezes the whole start->end range into a sliver.
// Instead the arc track is split into GAUGE_ARC_SEGMENTS colour stops, each
// sampled at its position on the SCALE (min..max), filled up to the value —
// so 5.5 of 100 is start-colour only and the colour follows the arc.
export const GAUGE_ARC_START = '#E24B4A'
export const GAUGE_ARC_END = '#1D9E75'
const GAUGE_TRACK = 'rgba(148,163,184,0.18)'
const GAUGE_CLEAR = 'rgba(0,0,0,0)'
const GAUGE_ARC_SEGMENTS = 96

function parseRgbColor(c) {
  if (typeof c !== 'string') return null
  const s = c.trim()
  let m = /^#([0-9a-f]{3}|[0-9a-f]{6})$/i.exec(s)
  if (m) {
    const h = m[1].length === 3 ? [...m[1]].map((x) => x + x).join('') : m[1]
    return [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16))
  }
  m = /^rgba?\(\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)/i.exec(s)
  return m ? [m[1], m[2], m[3]].map((x) => Math.max(0, Math.min(255, Number(x)))) : null
}

/**
 * Straight start->end blend (per-channel sRGB, the same blend as the
 * inspector's CSS `linear-gradient(to right, start, end)` preview) at t in
 * [0,1]. Hex or rgb() inputs; anything unparseable falls back to the default
 * start/end colour. Returns '#rrggbb'.
 */
export function lerpGaugeArcColor(start, end, t) {
  const a = parseRgbColor(start) || parseRgbColor(GAUGE_ARC_START)
  const b = parseRgbColor(end) || parseRgbColor(GAUGE_ARC_END)
  const k = Math.max(0, Math.min(1, Number(t) || 0))
  return '#' + a.map((x, i) => Math.round(x + (b[i] - x) * k).toString(16).padStart(2, '0')).join('')
}

// axisLine colour stops for a gradient fill up to `frac` of the scale.
// `shift` (in segments) offsets the boundaries — the underlay uses 0.5 so its
// segments straddle the main layer's anti-aliasing seams. `rest` fills
// frac..1 (transparent on the main layer, the grey track on the underlay).
function gaugeArcStops(grad, frac, shift, rest) {
  const n = GAUGE_ARC_SEGMENTS
  const out = []
  let prev = 0
  for (let i = 1; prev < frac; i++) {
    const t = Math.min(frac, (i - shift) / n)
    if (t <= prev) continue
    out.push([t, lerpGaugeArcColor(grad.start, grad.end, (prev + t) / 2)])
    prev = t
  }
  if (frac < 1) out.push([1, rest])
  return out
}

// Second gauge series drawn under the main one. Always present (inert when
// there's no gradient) so the series count never changes on a merge — ECharts
// keeps a dropped series around when merging rather than removing it.
// `round` follows Arc ends -> Round: on a gradient the underlay draws the grey
// track past the value, so its max end must be capped like the main arc.
function gaugeUnderlay(grad, { min, max, startAngle, endAngle, width, frac, round = false }) {
  return {
    type: 'gauge', silent: true, z: 1, min, max, startAngle, endAngle,
    tooltip: { show: false },
    axisLine: {
      show: !!grad, roundCap: !!round,
      lineStyle: { width, color: grad ? gaugeArcStops(grad, frac, 0.5, GAUGE_TRACK) : [[1, GAUGE_TRACK]] },
    },
    progress: { show: false }, pointer: { show: false }, anchor: { show: false },
    axisTick: { show: false }, splitLine: { show: false }, axisLabel: { show: false },
    title: { show: false }, detail: { show: false },
    data: [],
  }
}

function gaugeArc(config) {
  const grad = config.gaugeArcGradient
    ? { start: config.gaugeArcStart || GAUGE_ARC_START, end: config.gaugeArcEnd || GAUGE_ARC_END }
    : null
  return { grad, solid: grad ? null : (config.gaugeArcColor || null) }
}

/**
 * Arc end shape from config.gaugeArcCaps. Explicit 'flat' | 'round' wins.
 * Auto (unset/invalid): gradient -> 'flat'; solid ring -> 'round'; anything
 * else -> null = the stock look (ring progress round, needle square), so
 * existing solid-colour needles keep their square ends.
 */
export function resolveGaugeArcCaps(config, { grad, solid }) {
  const c = config?.gaugeArcCaps
  if (c === 'flat' || c === 'round') return c
  if (grad) return 'flat'
  if (solid && config?.gaugeStyle === 'ring') return 'round'
  return null
}

// Gauge reuse of the Display panel's axis controls: "Axis text size" (written
// to x/y/y2Axis.axisLabel.fontSize) and "Y axis ticks" (yAxis.splitNumber).
// Only positive finite numbers count; anything else -> the builder default.
function positiveOr(v, fallback) {
  const n = Number(v)
  return v != null && Number.isFinite(n) && n > 0 ? n : fallback
}
function gaugeTextSize(config, fallback) {
  return positiveOr(config.yAxis?.axisLabel?.fontSize ?? config.xAxis?.axisLabel?.fontSize, fallback)
}

/**
 * Tick-label formatter for the needle gauge's arc scale. ECharts places ticks
 * at min + i * (max - min) / splitNumber, so 6 ticks on 0..100 land on
 * 16.666…; left unformatted they print as raw floats.
 *
 * - No `format` (or 'date', meaningless on a scale): auto — the fewest
 *   decimals (0..2) that show the tick step exactly; when none does (100/6),
 *   by step size (step >= 1 -> 0, >= 0.1 -> 1, else 2).
 * - 'percent': gauge values are usually already 0..100, so when the range
 *   goes past 1 the number is shown as-is plus '%' (percent100-style); a 0..1
 *   range keeps true percent (x100). Gauge-only — cartesian axes unchanged.
 * - Anything else: makeFormatter(format, locale), as cartesian y axes use.
 * Null / non-finite ticks -> ''.
 */
export function gaugeScaleLabelFormatter({ min = 0, max = 100, splitNumber = 10, format = undefined, locale = undefined } = {}) {
  const lo = Number(min), hi = Number(max)
  const step = Math.abs(hi - lo) / Math.max(Number(splitNumber) || 1, 1)
  // Fewest decimals (0..2) that show the step exactly (0.25 -> 2, 2.5 -> 1);
  // a step that never comes out exact (100/6) rounds by size instead.
  const exactDp = [0, 1, 2].find((d) => {
    const x = step * 10 ** d
    return Math.abs(x - Math.round(x)) < 1e-9 * Math.max(1, x)
  })
  const autoDp = exactDp ?? (!(step < 1) ? 0 : step >= 0.1 ? 1 : 2)
  const spec = typeof format === 'string' && format ? { kind: format } : (isDeepObject(format) ? format : null)
  const kind = spec ? (spec.kind || 'number') : null
  let fmt
  if (!kind || kind === 'date') {
    const nf = new Intl.NumberFormat(locale, { maximumFractionDigits: autoDp })
    fmt = (n) => nf.format(n)
  } else if (kind === 'percent' && (hi > 1 || hi - lo > 1)) {
    const nf = new Intl.NumberFormat(spec.locale || locale, { maximumFractionDigits: spec.decimals ?? autoDp })
    fmt = (n) => `${nf.format(n)}%`
  } else {
    fmt = makeFormatter(spec, locale)
  }
  return (v) => {
    if (v == null || v === '') return ''
    const n = Number(v)
    return Number.isFinite(n) ? fmt(n) : ''
  }
}

// The gauge's value: the last row of the value column (0 when missing).
function gaugeLastValue(table, e) {
  const valRaw = table.col(e.y || e.value)
  return valRaw && valRaw.length ? Number(valRaw[valRaw.length - 1]) : 0
}

/**
 * The value buildGauge would read from `table` (same adapter + encoding
 * resolution), or null when there are no rows. Lets a live host tween the
 * gradient gauge from the value it reads to the next one.
 */
export function gaugeTargetValue(table, encoding) {
  const view = tableView(table)
  if (!view || !view.numRows) return null
  return gaugeLastValue(view, resolveEncoding(view, encoding))
}

// `displayValue` (runtime-only, never config) replaces the table value for
// the needle, readout and gradient stops — a host tweening the gradient arc
// passes the in-between value each frame. `tweening` turns off ECharts' own
// value/update animation so it doesn't chase those per-frame updates; when
// idle the fields are set back explicitly (undefined -> inherit) so a merge
// doesn't keep them off.
function buildGauge(table, e, config, theme, locale, { displayValue = undefined, tweening = false } = {}) {
  const value = Number.isFinite(displayValue) ? Number(displayValue) : gaugeLastValue(table, e)
  const animationDurationUpdate = tweening ? 0 : undefined
  const max = config.yAxis?.max ?? (config.max ?? 100)
  const min = config.yAxis?.min ?? (config.min ?? 0)
  const fmt = isDeepObject(config.dataLabels) && config.dataLabels.format
    ? makeFormatter(config.dataLabels.format, locale) : null
  const { grad, solid } = gaugeArc(config)
  const span = max - min
  const frac = span > 0 && Number.isFinite(value) ? Math.max(0, Math.min(1, (value - min) / span)) : 0
  // Arc ends — both set on every build so a needle<->ring merge or an
  // Auto<->Flat/Round change can't keep a stale cap. The transparent gradient
  // progress shares progressRound so its hover area matches the drawn arc.
  const caps = resolveGaugeArcCaps(config, { grad, solid })
  const progressRound = caps ? caps === 'round' : config.gaugeStyle === 'ring'
  // Round also caps the track (bands / solid track / gradient stops) so the
  // rim reads rounded at min and max. ECharts caps every axisLine colour
  // segment, so band joins get rounded too.
  const axisRound = caps === 'round'

  // 'ring' — a minimal radial progress ring (Apex radialBar look): full-circle
  // track, round-capped progress in the palette color, centered % readout, no
  // pointer/ticks/bands. The default remains the classic banded needle gauge.
  //
  // Each variant sets EVERY field the other one overrides (angles, pointer,
  // ticks/labels/title visibility, progress cap/colour, detail placement):
  // EChart merges same-key options, so a field one variant leaves to ECharts'
  // default would keep the other variant's value. The widget's replaceKey
  // also splits gauge:needle / gauge:ring — this is the second layer.
  if (config.gaugeStyle === 'ring') {
    const ringColor = solid || (config.palette && config.palette[0]) || theme.palette[0]
    return {
      ...baseFrame(config, theme, locale, { trigger: 'item', noGrid: true }),
      series: [{
        type: 'gauge', min, max, animationDurationUpdate,
        // No visible ticks on the ring; set only so a merge can't keep the
        // needle's tick count (see chartReplaceKey for the primary guard).
        splitNumber: 10,
        startAngle: 90, endAngle: -270,
        // Gradient: the coloured fill is the segmented axisLine; progress
        // stays, transparent, as the item-tooltip hover target.
        progress: { show: true, width: 12, roundCap: progressRound, itemStyle: { color: grad ? GAUGE_CLEAR : ringColor } },
        axisLine: { show: true, roundCap: axisRound, lineStyle: { width: 12, color: grad ? gaugeArcStops(grad, frac, 0, GAUGE_CLEAR) : [[1, GAUGE_TRACK]] } },
        pointer: { show: false },
        axisTick: { show: false },
        splitLine: { show: false },
        axisLabel: { show: false },
        detail: {
          valueAnimation: !tweening, color: theme.fg, fontSize: gaugeTextSize(config, 20), fontWeight: 700,
          offsetCenter: [0, 0],
          formatter: fmt ? (v) => fmt(v) : (v) => `${Number(v).toFixed(2)}%`,
        },
        title: { show: false },
        data: [{ value: Math.max(min, Math.min(max, value)), name: e.y || e.value || '' }],
      }, gaugeUnderlay(grad, { min, max, startAngle: 90, endAngle: -270, width: 12, frac, round: axisRound })],
    }
  }

  const splitNumber = positiveOr(config.yAxis?.splitNumber, 10)
  // Needle: the values below that match ECharts' gauge defaults (225/-45,
  // pointer/ticks/labels/title shown, detail at [0,'40%'], '{value}') are
  // spelled out so a merge from 'ring' can't leave them hidden.
  return {
    ...baseFrame(config, theme, locale, { trigger: 'item', noGrid: true }),
    series: [{
      type: 'gauge', min, max, animationDurationUpdate,
      // Always set (ECharts' default is 10) so clearing "Y axis ticks" undoes it.
      splitNumber,
      startAngle: 225, endAngle: -45,
      progress: {
        show: true, width: 14, roundCap: progressRound,
        itemStyle: { color: grad ? GAUGE_CLEAR : (solid || resolvePalette(config, theme)[0]) },
      },
      axisLine: { show: true, roundCap: axisRound, lineStyle: { width: 14, color: grad
        ? gaugeArcStops(grad, frac, 0, GAUGE_CLEAR)
        : solid
          ? [[1, GAUGE_TRACK]]
          : [[0.5, theme.palette[3] || '#ef4444'], [0.8, theme.palette[1] || '#f59e0b'], [1, theme.palette[2] || '#10b981']] } },
      pointer: { show: true, itemStyle: { color: theme.fg } },
      axisLabel: {
        show: true, color: theme.fgMuted, fontSize: gaugeTextSize(config, 9), distance: 18,
        formatter: gaugeScaleLabelFormatter({ min, max, splitNumber, format: config.yAxis?.format, locale }),
      },
      axisTick: { show: true, lineStyle: { color: theme.fgMuted } },
      splitLine: { show: true, lineStyle: { color: theme.fgMuted } },
      detail: {
        valueAnimation: !tweening, color: theme.fg, fontSize: 24, fontWeight: 'bold',
        offsetCenter: [0, '40%'],
        formatter: fmt ? (v) => fmt(v) : '{value}',
      },
      data: [{ value, name: e.y || e.value || '' }],
      title: { show: true, color: theme.fgMuted, fontSize: 12 },
    }, gaugeUnderlay(grad, { min, max, startAngle: 225, endAngle: -45, width: 14, frac, round: axisRound })],
  }
}

function buildCandlestick(table, e, config, theme, locale) {
  const tRaw = table.col(e.x)
  const oRaw = table.col(e.open)
  const cRaw = table.col(e.close)
  const lRaw = table.col(e.low)
  const hRaw = table.col(e.high)
  if (!tRaw || !oRaw || !cRaw || !lRaw || !hRaw) return emptyOption(theme)
  const cats = toStrings(tRaw)
  const o = toNumbers(oRaw), c = toNumbers(cRaw), l = toNumbers(lRaw), h = toNumbers(hRaw)
  // ECharts candlestick data: [open, close, low, high]
  const data = cats.map((_, i) => [o[i], c[i], l[i], h[i]])
  const xAxis = buildAxis('category', config.xAxis, theme, locale, { data: cats, boundaryGap: true })
  const yAxis = buildAxis('value', config.yAxis, theme, locale, { scale: true })
  return {
    ...baseFrame(config, theme, locale, { trigger: 'axis', dataZoom: cats.length > 60 }),
    xAxis, yAxis,
    series: [{
      type: 'candlestick', data,
      itemStyle: {
        color: theme.palette[2] || '#10b981', color0: theme.palette[3] || '#ef4444',
        borderColor: theme.palette[2] || '#10b981', borderColor0: theme.palette[3] || '#ef4444',
      },
    }],
  }
}

function buildFan(table, e, config, theme, locale) {
  // Forecast line (y) + shaded uncertainty band (lower..upper).
  const xRaw = table.col(e.x)
  const yRaw = table.col(e.y)
  if (!xRaw || !yRaw) return emptyOption(theme)
  const xIsNumeric = isNumericArray(xRaw)
  const cats = xIsNumeric ? toNumbers(xRaw) : toStrings(xRaw)
  const mid = toNumbers(yRaw)
  const lower = e.lower ? toNumbers(table.col(e.lower) || []) : null
  const upper = e.upper ? toNumbers(table.col(e.upper) || []) : null
  const smooth = config.smooth ?? true

  const xAxis = xIsNumeric
    ? buildAxis('value', config.xAxis, theme, locale)
    : buildAxis('category', config.xAxis, theme, locale, { data: cats, boundaryGap: false })
  // Auto-title only a NUMERIC x-axis — categories (names, dates) are self-describing.
  if (xIsNumeric && !config.xAxis?.label && e.x) { xAxis.name = e.x; xAxis.nameLocation = 'middle'; xAxis.nameGap = 28 }
  const yAxis = buildAxis('value', config.yAxis, theme, locale, { scale: true })
  if (!config.yAxis?.label && e.y) {
    yAxis.name = humaniseColumn(e.y); yAxis.nameLocation = 'middle'
    yAxis.nameGap = estimateValueAxisNameGap([...mid, ...(lower || []), ...(upper || [])], yAxis)
  }

  const accent = theme.palette[0]
  const series = []
  if (lower && upper) {
    // Band drawn as a transparent baseline (lower) + stacked area (upper-lower).
    const bandWidth = upper.map((u, i) => u - lower[i])
    series.push({
      name: 'band-lower', type: 'line', stack: 'fan', symbol: 'none',
      lineStyle: { opacity: 0 }, areaStyle: { opacity: 0 }, silent: true,
      data: xIsNumeric ? lower.map((v, i) => [cats[i], v]) : lower,
      tooltip: { show: false },
    })
    series.push({
      name: 'confidence', type: 'line', stack: 'fan', symbol: 'none',
      lineStyle: { opacity: 0 },
      areaStyle: { color: accent, opacity: 0.18 },
      data: xIsNumeric ? bandWidth.map((v, i) => [cats[i], v]) : bandWidth,
      tooltip: { show: false },
    })
  }
  series.push({
    name: e.y || 'forecast', type: 'line', smooth, symbol: 'none',
    lineStyle: { color: accent, width: 2 }, itemStyle: { color: accent },
    data: xIsNumeric ? mid.map((v, i) => [cats[i], v]) : mid,
    z: 5,
  })

  return {
    ...baseFrame(config, theme, locale, { trigger: 'axis', dataZoom: cats.length > 100 }),
    xAxis, yAxis, series,
  }
}

// ---------------------------------------------------------------------------
// Shared frame (color/background/title/legend/grid/tooltip/animation)
// ---------------------------------------------------------------------------

function baseFrame(config, theme, locale, { trigger = 'axis', dataZoom = false, noGrid = false, singleSeries = false } = {}) {
  const palette = resolvePalette(config, theme)
  const hasTitle = !!(config.title || config.subtitle)
  const legend = legendConfig(config, theme, { singleSeries, hasTitle })
  const hasLegend = legend !== undefined
  const legendPos = (isDeepObject(config.legend) && config.legend.position) || 'top'
  const frame: Record<string, any> = {
    color: palette,
    backgroundColor: 'transparent',
    animation: config.animation !== false,
    textStyle: { color: theme.fg, fontFamily: canvasFontFamily(config) },
    tooltip: tooltipConfig(config, theme, { trigger, locale }),
  }
  const title = titleConfig(config, theme)
  if (title) frame.title = title
  if (hasLegend) frame.legend = legend
  if (!noGrid) frame.grid = gridConfig(config, hasLegend, legendPos)
  // The zoom slider is normally auto-enabled past a point-count threshold, but
  // a small trend/sparkline tile has no room for it and migrated boards often
  // reproduce charts that never had one. `config.dataZoom` overrides the
  // heuristic in both directions; omitting it keeps the existing behaviour.
  const showZoom = config.dataZoom === undefined ? dataZoom : !!config.dataZoom
  if (showZoom) {
    frame.dataZoom = [
      { type: 'inside', xAxisIndex: 0 },
      {
        type: 'slider', xAxisIndex: 0, height: 18, bottom: 6, borderColor: theme.border,
        fillerColor: 'rgba(99,102,241,0.15)', textStyle: { color: theme.fgMuted, fontSize: 9 },
      },
    ]
    if (frame.grid) frame.grid.bottom = Math.max(frame.grid.bottom || 36, 56)
  }
  return frame
}

function emptyOption(theme) {
  return {
    backgroundColor: 'transparent', animation: false,
    graphic: [{
      type: 'text', left: 'center', top: 'middle',
      style: { text: 'No data', fontSize: 14, fill: theme.fgMuted },
    }],
  }
}

// ===========================================================================
// PUBLIC: buildChartOption
// ===========================================================================

// Keys we interpret ourselves — everything else in config is treated as a raw
// ECharts passthrough that deep-merges (and wins) over our computed option.
const INTERPRETED_KEYS = new Set([
  'title', 'subtitle', 'legend', 'tooltip', 'palette', 'heatColors', 'heatColorBy', 'heatGap', 'heatGapColor', 'yInverse', 'xLabelRotate', 'stack', 'orientation',
  // map-only: the geometry name and the choropleth end-stops. Without these
  // here they fall through to the raw-ECharts passthrough and land as unknown
  // top-level option keys.
  'map', 'lowColor', 'highColor', 'roam',
  'smooth', 'areaOpacity', 'dataLabels', 'animation', 'grid', 'xAxis', 'yAxis',
  'y2Axis', 'referenceLines', 'annotations', 'locale', 'echarts', 'max', 'min',
  'centerLabel',
  // gauge: needle vs ring (read by buildGauge, not a raw ECharts key).
  'gaugeStyle',
  // gauge arc colours — read by buildGauge, not raw ECharts keys.
  'gaugeArcGradient', 'gaugeArcStart', 'gaugeArcEnd', 'gaugeArcColor', 'gaugeArcCaps',
])

function stripInterpretedKeys(config) {
  const out = {}
  for (const k of Object.keys(config)) {
    if (!INTERPRETED_KEYS.has(k)) out[k] = config[k]
  }
  return out
}

/**
 * Build a complete ECharts option object for the given chart type + data.
 *
 * @param {object} args
 * @param {string} args.type      — chart type (see SUPPORTED_TYPES)
 * @param {object} args.table     — TableView | Arrow Table | rows[] | columnar
 * @param {object} [args.encoding]— column-name mapping (x, y, color, ...)
 * @param {object} [args.config]  — declarative customization (deep-merged last)
 * @param {object} [args.theme]   — resolved theme tokens
 * @param {number} [args.gaugeDisplayValue] — gauge only, runtime-only: value to
 *                                  draw instead of the table's (gradient tween)
 * @param {boolean} [args.gaugeTweening]    — gauge only: a tween frame (ECharts'
 *                                  own value animation off)
 * @returns {object} ECharts option
 */
export function buildChartOption({ type, table, encoding, config, theme, gaugeDisplayValue, gaugeTweening }: { type?: string; table?: any; encoding?: any; config?: any; theme?: any; gaugeDisplayValue?: number; gaugeTweening?: boolean } = {}) {
  const view = tableView(table)
  const th = resolveTheme(theme)
  const cfg = config || {}
  const locale = cfg.locale

  if (!view || !view.numRows) return emptyOption(th)

  const e = resolveEncoding(view, encoding)
  const t = (type || 'scatter').toLowerCase()

  let option
  switch (t) {
    case 'bar': option = buildBar(view, e, cfg, th, locale); break
    case 'line': option = buildLine(view, e, cfg, th, locale, { area: false }); break
    case 'area': option = buildLine(view, e, cfg, th, locale, { area: true }); break
    case 'scatter': option = buildScatter(view, e, cfg, th, locale, { bubble: false }); break
    case 'bubble': option = buildScatter(view, e, cfg, th, locale, { bubble: true }); break
    case 'pie': option = buildPie(view, e, cfg, th, locale, { donut: false }); break
    case 'donut': option = buildPie(view, e, cfg, th, locale, { donut: true }); break
    case 'polar': option = buildPolar(view, e, cfg, th, locale); break
    case 'radialbar': option = buildRadialBar(view, e, cfg, th, locale); break
    case 'sankey': option = buildSankey(view, e, cfg, th, locale); break
    case 'funnel': option = buildFunnel(view, e, cfg, th, locale); break
    case 'waterfall': option = buildWaterfall(view, e, cfg, th, locale); break
    case 'heatmap': option = buildHeatmap(view, e, cfg, th, locale); break
    case 'radar': option = buildRadar(view, e, cfg, th, locale); break
    case 'treemap': option = buildTreemap(view, e, cfg, th, locale); break
    case 'map': option = buildMap(view, e, cfg, th, locale); break
    case 'boxplot': option = buildBoxplot(view, e, cfg, th, locale); break
    case 'gauge': option = buildGauge(view, e, cfg, th, locale, { displayValue: gaugeDisplayValue, tweening: !!gaugeTweening }); break
    case 'candlestick': option = buildCandlestick(view, e, cfg, th, locale); break
    case 'fan': option = buildFan(view, e, cfg, th, locale); break
    case 'combo': option = buildCombo(view, e, cfg, th, locale); break
    default: option = buildScatter(view, e, cfg, th, locale, { bubble: false })
  }

  // Reference lines / annotations / data labels for cartesian charts.
  const cartesian = ['bar', 'line', 'area', 'scatter', 'bubble', 'fan', 'candlestick', 'boxplot', 'waterfall', 'combo'].includes(t)
  if (cartesian) decorateCartesian(option, cfg, th, locale)

  // Final deep-merge: any uninterpreted config keys + an explicit `echarts`
  // block win over everything we computed.
  const passthrough = stripInterpretedKeys(cfg)
  if (Object.keys(passthrough).length) option = deepMerge(option, passthrough)
  if (isDeepObject(cfg.echarts)) option = deepMerge(option, cfg.echarts)

  return option
}
