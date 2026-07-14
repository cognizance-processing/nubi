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
 *   theme     — { palette: string[], fg, fgMuted, primary, border, grid, axis }
 *               resolved theme tokens used as ECharts defaults
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
 *   stack:     boolean | 'normal' | 'percent',   // bar/area stacking (percent = 100%)
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

// ---------------------------------------------------------------------------
// Supported chart types
// ---------------------------------------------------------------------------

export const SUPPORTED_TYPES = [
  'bar', 'line', 'area', 'scatter', 'bubble', 'pie', 'donut',
  'sankey', 'funnel', 'waterfall', 'heatmap', 'radar', 'treemap',
  'boxplot', 'gauge', 'candlestick', 'fan', 'combo',
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
        const arr = child ? child.toArray() : null
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
  const base = { type: 'scroll', textStyle: { color: theme.fgMuted, fontSize: 11 } }
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
    return {
      text: title || '',
      subtext: subtext || '',
      left: 'center',
      textStyle: { color: theme.fg, fontSize: 14, fontWeight: 600 },
      subtextStyle: { color: theme.fgMuted, fontSize: 11 },
    }
  }
  return title // object form passed through
}

function axisCommon(theme) {
  return {
    axisLine: { lineStyle: { color: theme.axis } },
    axisLabel: { color: theme.fgMuted, fontSize: 10 },
    nameTextStyle: { color: theme.fgMuted, fontSize: 11 },
    splitLine: { lineStyle: { color: theme.grid } },
  }
}

function buildAxis(kind, spec, theme, locale, defaults = {}) {
  const s = spec || {}
  const axis = { ...axisCommon(theme), ...defaults }
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
  if (s.format && axis.type !== 'category') {
    const fmt = makeFormatter(s.format, locale)
    axis.axisLabel = { ...axis.axisLabel, formatter: (v) => fmt(v) }
  }
  return axis
}

/**
 * nameGap for a value axis whose name is auto-derived from the column name:
 * wide tick labels (e.g. "6,000,000") need more clearance than the flat 44px
 * default, or the rotated axis name overlaps the outermost tick label.
 */
function estimateValueAxisNameGap(values, axis) {
  if (!Array.isArray(values) || !values.length) return 44
  const nums = values.map(Number).filter(Number.isFinite)
  if (!nums.length) return 44
  const maxAbs = Math.max(...nums.map(Math.abs))
  const fmt = axis.axisLabel?.formatter
  const sample = fmt ? String(fmt(maxAbs)) : maxAbs.toLocaleString()
  return Math.max(44, Math.round(sample.length * 5.5) + 8)
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
      fontSize: 10,
      color: theme.fg,
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

function tooltipConfig(config, theme, { trigger = 'axis', locale } = {}) {
  if (config.tooltip === false) return { show: false }
  const base = {
    trigger,
    confine: true,
    backgroundColor: 'rgba(15,17,23,0.95)',
    borderColor: theme.border,
    textStyle: { color: theme.fg, fontSize: 11 },
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

  const catAxis = buildAxis('category', config.xAxis, theme, locale, { data: cats, boundaryGap: true })
  if (!config.xAxis?.label && e.x) { catAxis.name = e.x; catAxis.nameLocation = 'middle'; catAxis.nameGap = 28 }
  if (n > 12 && !horizontal) catAxis.axisLabel = { ...catAxis.axisLabel, rotate: config.xAxis?.rotate ?? 30 }
  const valAxis = buildAxis('value', config.yAxis, theme, locale)
  if (!config.yAxis?.label && e.y) {
    valAxis.name = e.y; valAxis.nameLocation = 'middle'
    valAxis.nameGap = estimateValueAxisNameGap(vals, valAxis)
  }

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
        barMaxWidth: 40,
        itemStyle: { borderRadius: stack ? 0 : [2, 2, 0, 0] },
      }
    })
    if (config.stack === 'percent') applyPercentStack(series)
  } else {
    series = [{
      type: 'bar', name: e.y, data: vals,
      barMaxWidth: 48,
      itemStyle: { color: theme.palette[0], borderRadius: horizontal ? [0, 2, 2, 0] : [2, 2, 0, 0] },
    }]
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
  if (!config.xAxis?.label && e.x) { xAxis.name = e.x; xAxis.nameLocation = 'middle'; xAxis.nameGap = 28 }
  if (!xIsNumeric && n > 20) xAxis.axisLabel = { ...xAxis.axisLabel, rotate: config.xAxis?.rotate ?? 30 }

  const hasY2 = !!e.y2
  const yVals = toNumbers(yRaw)
  const yAxis = buildAxis('value', config.yAxis, theme, locale)
  if (!config.yAxis?.label && e.y) {
    yAxis.name = e.y; yAxis.nameLocation = 'middle'
    yAxis.nameGap = estimateValueAxisNameGap(yVals, yAxis)
  }
  const yAxes = [yAxis]
  if (hasY2) {
    const y2 = buildAxis('value', config.y2Axis, theme, locale)
    if (!config.y2Axis?.label) {
      y2.name = e.y2; y2.nameLocation = 'middle'
      y2.nameGap = estimateValueAxisNameGap(toNumbers(table.col(e.y2) || []), y2)
    }
    yAxes.push(y2)
  }
  const sampling = n > 5000 ? 'lttb' : undefined
  const colorRaw = e.color ? table.col(e.color) : null
  let series
  if (colorRaw && !isNumericArray(colorRaw)) {
    const xv = xIsNumeric ? toNumbers(xRaw) : Array.from({ length: n }, (_, i) => i)
    const groups = groupByCategory(xv, yVals, colorRaw)
    series = groups.map((g) => ({
      type: 'line', name: g.category,
      data: xIsNumeric ? g.a.map((x, i) => [x, g.b[i]]) : g.b,
      smooth, sampling, showSymbol: n <= 60,
      stack: area && stack ? stack : undefined,
      areaStyle: area ? { opacity } : undefined,
    }))
    if (config.stack === 'percent' && area) applyPercentStack(series)
  } else {
    series = [{
      type: 'line', name: e.y,
      data: xIsNumeric ? toNumbers(xRaw).map((x, i) => [x, yVals[i]]) : yVals,
      smooth, sampling, showSymbol: n <= 60,
      itemStyle: { color: theme.palette[0] }, lineStyle: { color: theme.palette[0] },
      areaStyle: area ? { color: theme.palette[0], opacity } : undefined,
    }]
    if (hasY2) {
      const y2Vals = toNumbers(table.col(e.y2) || [])
      series.push({
        type: 'line', name: e.y2, yAxisIndex: 1,
        data: xIsNumeric ? toNumbers(xRaw).map((x, i) => [x, y2Vals[i]]) : y2Vals,
        smooth, showSymbol: n <= 60,
        itemStyle: { color: theme.palette[1] }, lineStyle: { color: theme.palette[1] },
      })
    }
  }

  const singleSeries = !(colorRaw && !isNumericArray(colorRaw)) && !hasY2
  return {
    ...baseFrame(config, theme, locale, { trigger: 'axis', dataZoom: n > 100, singleSeries }),
    xAxis,
    yAxis: hasY2 ? yAxes : yAxis,
    series,
  }
}

// Bar+line combo: one or more bar series on the primary y-axis, one or more
// line series on a secondary y-axis, sharing one category x-axis. Unlike
// buildBar/buildLine (which take a single `y` column, optionally grouped by
// `color`), combo takes explicit column-name ARRAYS so heterogeneous series
// (e.g. 3 volume bars + 2 rate% lines) can share one chart.
//   encoding: { x: 'branch', bars: ['planned_calls', 'completed_calls'],
//               lines: ['strike_rate', 'adherence'] }
//   config:   { yAxis: {...}, y2Axis: {...} }  // bars use yAxis, lines use y2Axis
function buildCombo(table, e, config, theme, locale) {
  const catRaw = table.col(e.x)
  if (!catRaw) return emptyOption(theme)
  const cats = toStrings(catRaw)
  const n = cats.length
  const barCols = Array.isArray(e.bars) ? e.bars : (e.bars ? [e.bars] : [])
  const lineCols = Array.isArray(e.lines) ? e.lines : (e.lines ? [e.lines] : [])
  if (!barCols.length && !lineCols.length) return emptyOption(theme)

  const catAxis = buildAxis('category', config.xAxis, theme, locale, { data: cats, boundaryGap: true })
  if (!config.xAxis?.label && e.x) { catAxis.name = e.x; catAxis.nameLocation = 'middle'; catAxis.nameGap = 28 }
  // Combo categories are often named entities (branch/region) rather than short
  // codes, so rotate on label length too, not just count — n>12 alone (buildBar's
  // rule) still overlaps a handful of long names.
  const maxLabelLen = cats.reduce((m, c) => Math.max(m, c.length), 0)
  if (n > 12 || maxLabelLen > 10) catAxis.axisLabel = { ...catAxis.axisLabel, rotate: config.xAxis?.rotate ?? 30 }

  const yAxis = buildAxis('value', config.yAxis, theme, locale)
  if (!config.yAxis?.label && barCols[0]) { yAxis.name = barCols[0]; yAxis.nameLocation = 'middle'; yAxis.nameGap = 44 }
  const y2Axis = buildAxis('value', config.y2Axis, theme, locale)
  if (!config.y2Axis?.label && lineCols[0]) { y2Axis.name = lineCols[0]; y2Axis.nameLocation = 'middle'; y2Axis.nameGap = 44 }

  const palette = resolvePalette(config, theme)
  const barSeries = barCols.map((col, i) => ({
    type: 'bar', name: col, yAxisIndex: 0,
    data: toNumbers(table.col(col) || []),
    barMaxWidth: 32,
    itemStyle: { color: palette[i % palette.length], borderRadius: [2, 2, 0, 0] },
  }))
  const lineSeries = lineCols.map((col, i) => ({
    type: 'line', name: col, yAxisIndex: 1,
    data: toNumbers(table.col(col) || []),
    smooth: config.smooth !== false,
    showSymbol: n <= 60,
    itemStyle: { color: palette[(barCols.length + i) % palette.length] },
    lineStyle: { color: palette[(barCols.length + i) % palette.length], width: 2 },
  }))

  return {
    ...baseFrame(config, theme, locale, { trigger: 'axis', singleSeries: false }),
    xAxis: catAxis,
    yAxis: lineCols.length ? [yAxis, y2Axis] : yAxis,
    series: [...barSeries, ...lineSeries],
  }
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
    yAxis.name = e.y; yAxis.nameLocation = 'middle'
    yAxis.nameGap = estimateValueAxisNameGap(yVals, yAxis)
  }

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

  // Pie legend defaults to bottom unless the user said otherwise.
  const legendCfg = config.legend === false ? undefined
    : legendConfig(isDeepObject(config.legend) ? config : { legend: { position: 'bottom' } }, theme)

  const frame = baseFrame(config, theme, locale, { trigger: 'item', noGrid: true })
  const option = {
    ...frame,
    legend: legendCfg,
    series: [{
      type: 'pie', name: e.y || e.value,
      data: names.map((name, i) => ({ name, value: vals[i] })),
      radius: donut ? ['42%', '70%'] : ['0%', '68%'],
      center: ['50%', '46%'],
      label: {
        show: showLabel, color: theme.fg, fontSize: 10,
        formatter: fmt ? (p) => `${p.name}: ${fmt(p.value)}` : '{b}: {d}%',
      },
      labelLine: { show: showLabel },
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
    yAxis.name = e.y; yAxis.nameLocation = 'middle'
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

  return {
    ...baseFrame(config, theme, locale, { trigger: 'item', noGrid: true }),
    grid: deepMerge({ top: config.title ? 56 : 16, left: 80, right: 24, bottom: 64, containLabel: true }, config.grid || {}),
    tooltip: tooltipConfig(config, theme, { trigger: 'item', locale }),
    xAxis: { type: 'category', data: xCats, splitArea: { show: true }, axisLabel: { color: theme.fgMuted, fontSize: 10, rotate: xCats.length > 10 ? 30 : 0 } },
    yAxis: { type: 'category', data: yCats, splitArea: { show: true }, axisLabel: { color: theme.fgMuted, fontSize: 10 } },
    visualMap: {
      min, max, calculable: true, orient: 'horizontal', left: 'center', bottom: 8,
      textStyle: { color: theme.fgMuted, fontSize: 10 },
      inRange: { color: ['#0f1117', theme.palette[4] || '#3b82f6', theme.palette[0] || '#6366f1'] },
    },
    series: [{
      type: 'heatmap', data,
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
    yAxis.name = e.y; yAxis.nameLocation = 'middle'
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

function buildGauge(table, e, config, theme, locale) {
  const valRaw = table.col(e.y || e.value)
  const value = valRaw && valRaw.length ? Number(valRaw[valRaw.length - 1]) : 0
  const max = config.yAxis?.max ?? (config.max ?? 100)
  const min = config.yAxis?.min ?? (config.min ?? 0)
  const fmt = isDeepObject(config.dataLabels) && config.dataLabels.format
    ? makeFormatter(config.dataLabels.format, locale) : null
  return {
    ...baseFrame(config, theme, locale, { noGrid: true }),
    series: [{
      type: 'gauge', min, max,
      progress: { show: true, width: 14 },
      axisLine: { lineStyle: { width: 14, color: [[0.5, theme.palette[3] || '#ef4444'], [0.8, theme.palette[1] || '#f59e0b'], [1, theme.palette[2] || '#10b981']] } },
      pointer: { itemStyle: { color: theme.fg } },
      axisLabel: { color: theme.fgMuted, fontSize: 9, distance: 18 },
      axisTick: { lineStyle: { color: theme.fgMuted } },
      splitLine: { lineStyle: { color: theme.fgMuted } },
      detail: {
        valueAnimation: true, color: theme.fg, fontSize: 24,
        formatter: fmt ? (v) => fmt(v) : undefined,
      },
      data: [{ value, name: e.y || e.value || '' }],
      title: { color: theme.fgMuted, fontSize: 12 },
    }],
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
  if (!config.xAxis?.label && e.x) { xAxis.name = e.x; xAxis.nameLocation = 'middle'; xAxis.nameGap = 28 }
  const yAxis = buildAxis('value', config.yAxis, theme, locale, { scale: true })
  if (!config.yAxis?.label && e.y) {
    yAxis.name = e.y; yAxis.nameLocation = 'middle'
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
  const frame = {
    color: palette,
    backgroundColor: 'transparent',
    animation: config.animation !== false,
    textStyle: { color: theme.fg, fontFamily: 'inherit' },
    tooltip: tooltipConfig(config, theme, { trigger, locale }),
  }
  const title = titleConfig(config, theme)
  if (title) frame.title = title
  if (hasLegend) frame.legend = legend
  if (!noGrid) frame.grid = gridConfig(config, hasLegend, legendPos)
  if (dataZoom) {
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
  'title', 'subtitle', 'legend', 'tooltip', 'palette', 'stack', 'orientation',
  'smooth', 'areaOpacity', 'dataLabels', 'animation', 'grid', 'xAxis', 'yAxis',
  'y2Axis', 'referenceLines', 'annotations', 'locale', 'echarts', 'max', 'min',
  'centerLabel',
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
 * @returns {object} ECharts option
 */
export function buildChartOption({ type, table, encoding, config, theme } = {}) {
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
    case 'sankey': option = buildSankey(view, e, cfg, th, locale); break
    case 'funnel': option = buildFunnel(view, e, cfg, th, locale); break
    case 'waterfall': option = buildWaterfall(view, e, cfg, th, locale); break
    case 'heatmap': option = buildHeatmap(view, e, cfg, th, locale); break
    case 'radar': option = buildRadar(view, e, cfg, th, locale); break
    case 'treemap': option = buildTreemap(view, e, cfg, th, locale); break
    case 'boxplot': option = buildBoxplot(view, e, cfg, th, locale); break
    case 'gauge': option = buildGauge(view, e, cfg, th, locale); break
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
