/**
 * chartOption.js — builds ECharts `option` objects from Apache Arrow Tables.
 *
 * API:
 *   buildChartOption({ chartType, table, x, y, color, encoding, props }) -> echartsOption
 *
 * Supported chartType values:
 *   'line' | 'bar' | 'scatter' | 'area' | 'pie' | 'donut' | 'hbar' | 'heatmap' | 'gauge'
 *
 * Extra chart types (encoding → ECharts mapping):
 *   donut   — pie variant; x→name, y→value, with an inner-radius hole.
 *             props.innerRadius / props.outerRadius override the hole size.
 *   hbar    — horizontal bar; x→y-axis category, y→x-axis value. Categorical
 *             encoding.color groups become stacked horizontal series.
 *   heatmap — encoding.x → x-axis category, encoding.y → y-axis category, and a
 *             heat value from encoding.value (falls back to encoding.color).
 *             A continuous visualMap colours the cells.
 *   gauge   — single value from the first row of encoding.value; range is
 *             [props.min ?? 0, props.max ?? value*1.5].
 *
 * - Reads Arrow columns via table.getChild(name).toArray() (no row materialisation).
 * - Mobile-friendly defaults: responsive grid, touch-enabled tooltips, dataZoom
 *   for large series, sampling for very large datasets.
 * - Categorical color column → per-series grouping (scatter/line) or visualMap (pie).
 * - Numeric color column → continuous visualMap.
 * - Empty / degenerate tables return a safe empty option.
 *
 * ---------------------------------------------------------------------------
 * Advanced chart depth — driven by `encoding` and `props`
 * ---------------------------------------------------------------------------
 *
 * STACKING
 *   props.stack {boolean|string}
 *     - true  → all bar/area/line series share a single stack group ('total').
 *     - string → stack group id (allows multiple independent stacks one day).
 *   encoding.stack {string}  — alternative: name the stack group directly.
 *   Only bar, line, and area series are stacked; scatter/pie ignore this.
 *
 * COMBO (per-series chart type)
 *   Two equivalent forms — pick whichever suits your spec:
 *
 *   Form A — encoding.y as an array:
 *     encoding: {
 *       x: 'month',
 *       y: [
 *         { col: 'revenue', type: 'bar' },
 *         { col: 'profit',  type: 'line', axis: 'right' },
 *       ]
 *     }
 *
 *   Form B — props.series array (takes precedence over encoding.y string):
 *     props: {
 *       series: [
 *         { col: 'revenue', type: 'bar' },
 *         { col: 'profit',  type: 'line', axis: 'right' },
 *       ]
 *     }
 *
 *   Each series entry: { col: string, type?: 'bar'|'line'|'area'|'scatter', axis?: 'left'|'right' }
 *   `type` defaults to the widget's top-level chartType.
 *   When encoding.y is a plain string (existing behaviour), single-series path is used.
 *
 * DUAL Y-AXIS
 *   Mark individual series with axis:'right' (see COMBO above), OR:
 *   props.secondaryAxis {string[]}  — list of col names to assign to the right y-axis.
 *   When any series targets the right axis, a second yAxis entry is added and
 *   those series receive yAxisIndex:1.
 *   The grid right margin is widened automatically to accommodate the right axis label.
 */

// ---------------------------------------------------------------------------
// Palette
// ---------------------------------------------------------------------------

const PALETTE = [
  '#6366f1', // indigo-500
  '#f59e0b', // amber-500
  '#10b981', // emerald-500
  '#ef4444', // red-500
  '#3b82f6', // blue-500
  '#8b5cf6', // violet-500
  '#ec4899', // pink-500
  '#14b8a6', // teal-500
  '#f97316', // orange-500
  '#84cc16', // lime-500
]

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/**
 * Read an Arrow column as a plain JS Array (for string/mixed types) or
 * as a Float64Array (for numeric types). Returns null if the column doesn't exist.
 *
 * @param {import('apache-arrow').Table} table
 * @param {string} colName
 * @returns {ArrayLike|null}
 */
function getColumn(table, colName) {
  if (!colName) return null
  const child = table.getChild(colName)
  if (!child) return null
  return child.toArray()
}

/**
 * Coerce an ArrayLike to a plain number array, treating null/undefined as 0.
 *
 * @param {ArrayLike} arr
 * @returns {number[]}
 */
function toNumbers(arr) {
  const out = new Array(arr.length)
  for (let i = 0; i < arr.length; i++) {
    const v = arr[i]
    out[i] = v === null || v === undefined ? 0 : Number(v)
  }
  return out
}

/**
 * Convert an ArrayLike to a plain string array.
 *
 * @param {ArrayLike} arr
 * @returns {string[]}
 */
function toStrings(arr) {
  const out = new Array(arr.length)
  for (let i = 0; i < arr.length; i++) {
    const v = arr[i]
    out[i] = v === null || v === undefined ? '' : String(v)
  }
  return out
}

/**
 * Determine if the first non-null value is numeric (number or bigint).
 *
 * @param {ArrayLike} arr
 * @returns {boolean}
 */
function isNumericArray(arr) {
  for (let i = 0; i < arr.length; i++) {
    if (arr[i] != null) {
      return typeof arr[i] === 'number' || typeof arr[i] === 'bigint'
    }
  }
  return false
}

/**
 * Group scatter/line data points by a categorical color column.
 * Returns an array of { category, xVals, yVals } objects.
 *
 * @param {number[]} xVals
 * @param {number[]} yVals
 * @param {ArrayLike} colorArr
 * @returns {{ category: string, xVals: number[], yVals: number[] }[]}
 */
function groupByCategory(xVals, yVals, colorArr) {
  const groups = new Map()
  const n = Math.min(xVals.length, yVals.length)

  for (let i = 0; i < n; i++) {
    const cat = colorArr[i] === null || colorArr[i] === undefined
      ? '(null)'
      : String(colorArr[i])
    if (!groups.has(cat)) {
      groups.set(cat, { category: cat, xVals: [], yVals: [] })
    }
    const g = groups.get(cat)
    g.xVals.push(xVals[i])
    g.yVals.push(yVals[i])
  }

  return Array.from(groups.values())
}

// ---------------------------------------------------------------------------
// Advanced encoding helpers
// ---------------------------------------------------------------------------

/**
 * Normalise the `encoding` + `props` combo/stack/dual-axis spec into a flat
 * list of series descriptors that `buildMultiSeries` can consume.
 *
 * @param {{
 *   chartType: string,
 *   encoding?: {
 *     x?: string,
 *     y?: string | Array<{col:string, type?:string, axis?:string}>,
 *     color?: string,
 *     stack?: string,
 *   },
 *   props?: {
 *     series?:        Array<{col:string, type?:string, axis?:string}>,
 *     stack?:         boolean | string,
 *     secondaryAxis?: string[],
 *   },
 * }} params
 * @returns {{
 *   seriesDefs: Array<{col:string, type:string, axis:'left'|'right'}>,
 *   stackId:    string|null,
 * }}
 */
function resolveMultiSpec({ chartType, encoding = {}, props = {} }) {
  const baseType = (chartType || 'bar').toLowerCase()

  // --- series definitions ---
  let seriesDefs
  if (Array.isArray(props.series) && props.series.length > 0) {
    // Form B: props.series wins
    seriesDefs = props.series.map((s) => ({
      col:  s.col,
      type: (s.type || baseType).toLowerCase(),
      axis: s.axis === 'right' ? 'right' : 'left',
    }))
  } else if (Array.isArray(encoding.y) && encoding.y.length > 0) {
    // Form A: encoding.y array
    seriesDefs = encoding.y.map((s) => ({
      col:  s.col,
      type: (s.type || baseType).toLowerCase(),
      axis: s.axis === 'right' ? 'right' : 'left',
    }))
  } else {
    return null // caller falls back to the simple single-series path
  }

  // secondaryAxis override — any col listed there moves to right
  const secondary = new Set(Array.isArray(props.secondaryAxis) ? props.secondaryAxis : [])
  if (secondary.size > 0) {
    seriesDefs = seriesDefs.map((s) =>
      secondary.has(s.col) ? { ...s, axis: 'right' } : s,
    )
  }

  // --- stack id ---
  let stackId = null
  if (props.stack === true) {
    stackId = 'total'
  } else if (typeof props.stack === 'string' && props.stack) {
    stackId = props.stack
  } else if (typeof encoding.stack === 'string' && encoding.stack) {
    stackId = encoding.stack
  }

  return { seriesDefs, stackId }
}

// ---------------------------------------------------------------------------
// Shared chart defaults (mobile-friendly)
// ---------------------------------------------------------------------------

/**
 * Shared grid / tooltip / legend / toolbox defaults for all chart types.
 *
 * @param {{ showLegend?: boolean, showDataZoom?: boolean, dualAxis?: boolean }} opts
 * @returns {object} partial ECharts option
 */
function baseOption({ showLegend = false, showDataZoom = false, dualAxis = false } = {}) {
  const opt = {
    color: PALETTE,
    backgroundColor: 'transparent',
    animation: false,

    grid: {
      top: showLegend ? 40 : 12,
      right: dualAxis ? 60 : 20, // widen right margin to fit right-axis label
      bottom: showDataZoom ? 60 : 32,
      left: 52,
      containLabel: true,
    },

    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'cross', crossStyle: { color: '#999' } },
      confine: true, // keep tooltip inside chart area (mobile-friendly)
    },
  }

  if (showLegend) {
    opt.legend = {
      top: 4,
      type: 'scroll',
      textStyle: { fontSize: 11 },
    }
  }

  if (showDataZoom) {
    opt.dataZoom = [
      { type: 'inside', xAxisIndex: 0 },
      { type: 'slider', xAxisIndex: 0, height: 20, bottom: 8 },
    ]
  }

  return opt
}

// ---------------------------------------------------------------------------
// Chart builders
// ---------------------------------------------------------------------------

/**
 * Build a scatter option.
 *
 * @param {import('apache-arrow').Table} table
 * @param {string} xCol
 * @param {string} yCol
 * @param {string|undefined} colorCol
 * @returns {object} ECharts option
 */
function buildScatter(table, xCol, yCol, colorCol) {
  const xRaw = getColumn(table, xCol)
  const yRaw = getColumn(table, yCol)
  if (!xRaw || !yRaw) return {}

  const n = Math.min(xRaw.length, yRaw.length)
  if (n === 0) return {}

  const xVals = toNumbers(xRaw)
  const yVals = toNumbers(yRaw)

  const large = n > 5000
  const showDataZoom = n > 200

  const opt = baseOption({ showLegend: !!colorCol, showDataZoom })

  opt.tooltip = {
    trigger: 'item',
    formatter: (params) => {
      const [px, py] = params.value
      return `${xCol}: ${px}<br/>${yCol}: ${py}`
    },
    confine: true,
  }

  opt.xAxis = { type: 'value', name: xCol, nameLocation: 'middle', nameGap: 28,
    nameTextStyle: { fontSize: 11 } }
  opt.yAxis = { type: 'value', name: yCol, nameLocation: 'middle', nameGap: 36,
    nameTextStyle: { fontSize: 11 } }

  const colorRaw = getColumn(table, colorCol)

  if (colorRaw && !isNumericArray(colorRaw)) {
    // Categorical color → multiple series (one per category)
    const groups = groupByCategory(xVals, yVals, colorRaw)
    opt.series = groups.map((g) => ({
      type: 'scatter',
      name: g.category,
      data: g.xVals.map((x, i) => [x, g.yVals[i]]),
      symbolSize: large ? 4 : 6,
      large,
      largeThreshold: 2000,
      ...(large ? { sampling: 'lttb' } : {}),
    }))
  } else if (colorRaw && isNumericArray(colorRaw)) {
    // Numeric color → single series with visualMap
    const colorVals = toNumbers(colorRaw)
    const cMin = Math.min(...colorVals)
    const cMax = Math.max(...colorVals)
    opt.visualMap = {
      min: cMin, max: cMax,
      dimension: 2,
      orient: 'vertical',
      right: 4,
      top: 'center',
      calculable: true,
      inRange: { color: ['#3b82f6', '#ef4444'] },
      textStyle: { fontSize: 10 },
    }
    opt.series = [{
      type: 'scatter',
      data: xVals.map((x, i) => [x, yVals[i], colorVals[i]]),
      symbolSize: large ? 4 : 6,
      large,
      largeThreshold: 2000,
      ...(large ? { sampling: 'lttb' } : {}),
    }]
  } else {
    // No color — single series, uniform colour from palette
    opt.series = [{
      type: 'scatter',
      data: xVals.map((x, i) => [x, yVals[i]]),
      symbolSize: large ? 4 : 6,
      itemStyle: { color: PALETTE[0] },
      large,
      largeThreshold: 2000,
      ...(large ? { sampling: 'lttb' } : {}),
    }]
  }

  return opt
}

/**
 * Build a line (or area) option.
 *
 * @param {import('apache-arrow').Table} table
 * @param {string} xCol
 * @param {string} yCol
 * @param {string|undefined} colorCol
 * @param {boolean} area — true for area chart
 * @param {string|null} stackId — if set, series are stacked under this group id
 * @returns {object} ECharts option
 */
function buildLineWithStack(table, xCol, yCol, colorCol, area = false, stackId = null) {
  const xRaw = getColumn(table, xCol)
  const yRaw = getColumn(table, yCol)
  if (!xRaw || !yRaw) return {}

  const n = Math.min(xRaw.length, yRaw.length)
  if (n === 0) return {}

  const showDataZoom = n > 100

  const opt = baseOption({ showLegend: !!colorCol, showDataZoom })

  opt.tooltip = { trigger: 'axis', confine: true }

  // Determine if x is categorical
  const xIsNumeric = isNumericArray(xRaw)
  const xStrings = toStrings(xRaw)
  const xNums = xIsNumeric ? toNumbers(xRaw) : null

  opt.xAxis = xIsNumeric
    ? { type: 'value', name: xCol, nameLocation: 'middle', nameGap: 28,
        nameTextStyle: { fontSize: 11 } }
    : { type: 'category', data: xStrings, name: xCol, nameLocation: 'middle', nameGap: 28,
        nameTextStyle: { fontSize: 11 }, axisLabel: { rotate: n > 20 ? 30 : 0, fontSize: 10 } }
  opt.yAxis = { type: 'value', name: yCol, nameLocation: 'middle', nameGap: 36,
    nameTextStyle: { fontSize: 11 } }

  const areaStyle = area ? { opacity: 0.25 } : undefined

  const colorRaw = getColumn(table, colorCol)
  const yVals = toNumbers(yRaw)

  if (colorRaw && !isNumericArray(colorRaw)) {
    // Categorical → group into multiple line series
    const xVals = xIsNumeric ? xNums : Array.from({ length: n }, (_, i) => i)
    const groups = groupByCategory(xVals, yVals, colorRaw)
    opt.series = groups.map((g) => ({
      type: 'line',
      name: g.category,
      data: xIsNumeric
        ? g.xVals.map((x, i) => [x, g.yVals[i]])
        : g.yVals,
      smooth: true,
      areaStyle,
      sampling: n > 5000 ? 'lttb' : undefined,
      ...(stackId ? { stack: stackId } : {}),
    }))
  } else {
    opt.series = [{
      type: 'line',
      name: yCol,
      data: xIsNumeric
        ? xNums.map((x, i) => [x, yVals[i]])
        : yVals,
      smooth: true,
      sampling: n > 5000 ? 'lttb' : undefined,
      itemStyle: { color: PALETTE[0] },
      lineStyle: { color: PALETTE[0] },
      areaStyle: area ? { color: PALETTE[0], opacity: 0.2 } : undefined,
      ...(stackId ? { stack: stackId } : {}),
    }]
  }

  return opt
}

/**
 * Build a bar option.
 *
 * @param {import('apache-arrow').Table} table
 * @param {string} xCol
 * @param {string} yCol
 * @param {string|undefined} colorCol
 * @param {string|null} stackId — if set, bars are stacked under this group id
 * @returns {object} ECharts option
 */
function buildBarWithStack(table, xCol, yCol, colorCol, stackId = null) {
  const xRaw = getColumn(table, xCol)
  const yRaw = getColumn(table, yCol)
  if (!xRaw || !yRaw) return {}

  const n = Math.min(xRaw.length, yRaw.length)
  if (n === 0) return {}

  const showDataZoom = n > 30

  const opt = baseOption({ showLegend: !!colorCol, showDataZoom })

  opt.tooltip = { trigger: 'axis', confine: true }

  const xStrings = toStrings(xRaw)
  const yVals = toNumbers(yRaw)

  opt.xAxis = {
    type: 'category',
    data: xStrings,
    name: xCol,
    nameLocation: 'middle',
    nameGap: 28,
    nameTextStyle: { fontSize: 11 },
    axisLabel: { rotate: n > 12 ? 30 : 0, fontSize: 10 },
  }
  opt.yAxis = { type: 'value', name: yCol, nameLocation: 'middle', nameGap: 36,
    nameTextStyle: { fontSize: 11 } }

  const colorRaw = getColumn(table, colorCol)

  // Effective stack id: explicit stackId wins; categorical color fallback uses 'total'
  const effectiveStack = stackId || (colorRaw && !isNumericArray(colorRaw) ? 'total' : null)

  if (colorRaw && !isNumericArray(colorRaw)) {
    // Group bars by category
    const groups = groupByCategory(Array.from({ length: n }, (_, i) => i), yVals, colorRaw)
    opt.series = groups.map((g) => ({
      type: 'bar',
      name: g.category,
      data: g.yVals,
      ...(effectiveStack ? { stack: effectiveStack } : {}),
    }))
  } else {
    opt.series = [{
      type: 'bar',
      name: yCol,
      data: yVals,
      itemStyle: { color: PALETTE[0], borderRadius: [2, 2, 0, 0] },
      ...(stackId ? { stack: stackId } : {}),
    }]
  }

  return opt
}

/**
 * Build a pie option.
 *
 * @param {import('apache-arrow').Table} table
 * @param {string} xCol — category/name column
 * @param {string} yCol — value column
 * @returns {object} ECharts option
 */
function buildPie(table, xCol, yCol) {
  const xRaw = getColumn(table, xCol)
  const yRaw = getColumn(table, yCol)
  if (!xRaw || !yRaw) return {}

  const n = Math.min(xRaw.length, yRaw.length)
  if (n === 0) return {}

  const xStrings = toStrings(xRaw)
  const yVals = toNumbers(yRaw)

  const data = xStrings.map((name, i) => ({ name, value: yVals[i] }))

  return {
    color: PALETTE,
    backgroundColor: 'transparent',
    animation: false,
    tooltip: {
      trigger: 'item',
      formatter: '{b}: {c} ({d}%)',
      confine: true,
    },
    legend: {
      type: 'scroll',
      orient: 'horizontal',
      bottom: 4,
      textStyle: { fontSize: 10 },
    },
    series: [{
      type: 'pie',
      name: yCol,
      data,
      radius: ['30%', '65%'], // donut
      center: ['50%', '45%'],
      label: { show: n <= 12, fontSize: 10 },
      labelLine: { show: n <= 12 },
      emphasis: { itemStyle: { shadowBlur: 8, shadowOffsetX: 0, shadowColor: 'rgba(0,0,0,0.3)' } },
    }],
  }
}

/**
 * Build a donut option — a pie with an enlarged inner radius (hole).
 *
 * Maps the same encoding as pie (x → name, y → value); only the series radius
 * differs. props.innerRadius / props.outerRadius (percent strings or numbers)
 * override the defaults.
 *
 * @param {import('apache-arrow').Table} table
 * @param {string} xCol — category/name column
 * @param {string} yCol — value column
 * @param {object} [props]
 * @returns {object} ECharts option
 */
function buildDonut(table, xCol, yCol, props = {}) {
  const opt = buildPie(table, xCol, yCol)
  if (!opt.series) return opt // safe empty (degenerate table)

  const inner = props.innerRadius ?? '50%'
  const outer = props.outerRadius ?? '72%'
  opt.series[0].radius = [inner, outer]
  return opt
}

/**
 * Build a horizontal-bar option.
 *
 * Like buildBarWithStack but with the axes swapped: the category column is on
 * the yAxis and the value on the xAxis. Categorical color groups become
 * stacked horizontal series (matching the vertical-bar fallback behaviour).
 *
 * @param {import('apache-arrow').Table} table
 * @param {string} xCol — category column (rendered on the y-axis)
 * @param {string} yCol — value column (rendered on the x-axis)
 * @param {string|undefined} colorCol
 * @param {string|null} stackId
 * @returns {object} ECharts option
 */
function buildHBar(table, xCol, yCol, colorCol, stackId = null) {
  const xRaw = getColumn(table, xCol)
  const yRaw = getColumn(table, yCol)
  if (!xRaw || !yRaw) return {}

  const n = Math.min(xRaw.length, yRaw.length)
  if (n === 0) return {}

  const opt = baseOption({ showLegend: !!colorCol, showDataZoom: false })
  opt.tooltip = { trigger: 'axis', axisPointer: { type: 'shadow' }, confine: true }

  const catStrings = toStrings(xRaw)
  const yVals = toNumbers(yRaw)

  // Axes swapped relative to vertical bar.
  opt.xAxis = {
    type: 'value',
    name: yCol,
    nameLocation: 'middle',
    nameGap: 28,
    nameTextStyle: { fontSize: 11 },
  }
  opt.yAxis = {
    type: 'category',
    data: catStrings,
    name: xCol,
    nameLocation: 'end',
    nameGap: 12,
    nameTextStyle: { fontSize: 11 },
    axisLabel: { fontSize: 10 },
  }
  // Category labels need more left room than the numeric default.
  opt.grid = { ...opt.grid, left: 8, right: 24 }

  const colorRaw = getColumn(table, colorCol)
  const effectiveStack = stackId || (colorRaw && !isNumericArray(colorRaw) ? 'total' : null)

  if (colorRaw && !isNumericArray(colorRaw)) {
    const groups = groupByCategory(Array.from({ length: n }, (_, i) => i), yVals, colorRaw)
    opt.series = groups.map((g) => ({
      type: 'bar',
      name: g.category,
      data: g.yVals,
      ...(effectiveStack ? { stack: effectiveStack } : {}),
    }))
  } else {
    opt.series = [{
      type: 'bar',
      name: yCol,
      data: yVals,
      itemStyle: { color: PALETTE[0], borderRadius: [0, 2, 2, 0] },
      ...(stackId ? { stack: stackId } : {}),
    }]
  }

  return opt
}

/**
 * Build a heatmap option.
 *
 * Encoding: x (category) → x-axis, y (category) → y-axis, value column → heat.
 * The value column is taken from encoding.value, then encoding.color, then the
 * `valueCol` arg. A continuous visualMap colours the cells. Degrades to a safe
 * empty option when any of the three columns are missing or the table is empty.
 *
 * @param {import('apache-arrow').Table} table
 * @param {string} xCol — x-axis category column
 * @param {string} yCol — y-axis category column
 * @param {string|undefined} valueCol — heat value column
 * @returns {object} ECharts option
 */
function buildHeatmap(table, xCol, yCol, valueCol) {
  const xRaw = getColumn(table, xCol)
  const yRaw = getColumn(table, yCol)
  const vRaw = getColumn(table, valueCol)
  if (!xRaw || !yRaw || !vRaw) return {}

  const n = Math.min(xRaw.length, yRaw.length, vRaw.length)
  if (n === 0) return {}

  const xStrings = toStrings(xRaw)
  const yStrings = toStrings(yRaw)
  const vVals = toNumbers(vRaw)

  // Distinct axis categories, preserving first-seen order.
  const xCats = []
  const xIndex = new Map()
  const yCats = []
  const yIndex = new Map()
  for (let i = 0; i < n; i++) {
    if (!xIndex.has(xStrings[i])) { xIndex.set(xStrings[i], xCats.length); xCats.push(xStrings[i]) }
    if (!yIndex.has(yStrings[i])) { yIndex.set(yStrings[i], yCats.length); yCats.push(yStrings[i]) }
  }

  let vMin = Infinity
  let vMax = -Infinity
  const data = new Array(n)
  for (let i = 0; i < n; i++) {
    const v = vVals[i]
    if (v < vMin) vMin = v
    if (v > vMax) vMax = v
    data[i] = [xIndex.get(xStrings[i]), yIndex.get(yStrings[i]), v]
  }
  if (!Number.isFinite(vMin)) { vMin = 0; vMax = 1 }
  if (vMin === vMax) vMax = vMin + 1

  return {
    color: PALETTE,
    backgroundColor: 'transparent',
    animation: false,
    tooltip: {
      position: 'top',
      confine: true,
      formatter: (p) => {
        const [xi, yi, val] = p.value
        return `${xCats[xi]} · ${yCats[yi]}<br/>${valueCol}: ${val}`
      },
    },
    grid: { top: 12, right: 16, bottom: 60, left: 8, containLabel: true },
    xAxis: {
      type: 'category',
      data: xCats,
      name: xCol,
      splitArea: { show: true },
      axisLabel: { rotate: xCats.length > 12 ? 30 : 0, fontSize: 10 },
    },
    yAxis: {
      type: 'category',
      data: yCats,
      name: yCol,
      splitArea: { show: true },
      axisLabel: { fontSize: 10 },
    },
    visualMap: {
      min: vMin,
      max: vMax,
      calculable: true,
      orient: 'horizontal',
      left: 'center',
      bottom: 4,
      itemHeight: 80,
      textStyle: { fontSize: 10 },
      inRange: { color: ['#eef2ff', '#6366f1', '#312e81'] },
    },
    series: [{
      type: 'heatmap',
      data,
      label: { show: false },
      emphasis: { itemStyle: { shadowBlur: 8, shadowColor: 'rgba(0,0,0,0.3)' } },
    }],
  }
}

/**
 * Build a single-value gauge option.
 *
 * Reads the first row of the value column as the gauge reading. Range is
 * [0, props.max] when props.max is given, otherwise [0, value * 1.5] (or a
 * floor of 1 for non-positive readings). Degrades to a safe empty option when
 * the value column is missing or the table is empty.
 *
 * @param {import('apache-arrow').Table} table
 * @param {string|undefined} valueCol — value column (first row is read)
 * @param {object} [props]
 * @returns {object} ECharts option
 */
function buildGauge(table, valueCol, props = {}) {
  const vRaw = getColumn(table, valueCol)
  if (!vRaw || vRaw.length === 0) return {}

  const first = vRaw[0]
  const value = first === null || first === undefined ? 0 : Number(first)
  const safeValue = Number.isFinite(value) ? value : 0

  let max
  if (props.max != null && Number.isFinite(Number(props.max))) {
    max = Number(props.max)
  } else {
    max = safeValue > 0 ? safeValue * 1.5 : 1
  }
  const min = props.min != null && Number.isFinite(Number(props.min)) ? Number(props.min) : 0

  return {
    color: PALETTE,
    backgroundColor: 'transparent',
    animation: false,
    series: [{
      type: 'gauge',
      min,
      max,
      progress: { show: true, width: 12, itemStyle: { color: PALETTE[0] } },
      axisLine: { lineStyle: { width: 12 } },
      axisTick: { show: false },
      splitLine: { length: 10, lineStyle: { width: 2 } },
      axisLabel: { fontSize: 9, distance: 14 },
      pointer: { width: 4, itemStyle: { color: PALETTE[0] } },
      anchor: { show: true, size: 8, itemStyle: { color: PALETTE[0] } },
      title: { show: !!(props.label || valueCol), fontSize: 12, offsetCenter: [0, '72%'] },
      detail: {
        valueAnimation: true,
        fontSize: 22,
        offsetCenter: [0, '40%'],
        formatter: (v) => `${Math.round(v * 100) / 100}`,
      },
      data: [{ value: safeValue, name: props.label || valueCol || '' }],
    }],
  }
}

// ---------------------------------------------------------------------------
// Multi-series builder (stacking + combo + dual y-axis)
// ---------------------------------------------------------------------------

/**
 * Build a combo/stacked/dual-axis option for bar/line/area series.
 *
 * @param {import('apache-arrow').Table} table
 * @param {string} xCol   — category column name
 * @param {Array<{col:string, type:string, axis:'left'|'right'}>} seriesDefs
 * @param {string|null} stackId  — if set, stackable series share this stack group
 * @returns {object} ECharts option
 */
function buildMultiSeries(table, xCol, seriesDefs, stackId) {
  const xRaw = getColumn(table, xCol)
  if (!xRaw) return {}

  const n = xRaw.length
  if (n === 0) return {}

  const xStrings  = toStrings(xRaw)
  const hasDual   = seriesDefs.some((s) => s.axis === 'right')
  const showDataZoom = n > 30

  const opt = baseOption({ showLegend: true, showDataZoom, dualAxis: hasDual })
  opt.tooltip = { trigger: 'axis', confine: true }

  // X axis — always category for combo charts
  opt.xAxis = {
    type: 'category',
    data: xStrings,
    name: xCol,
    nameLocation: 'middle',
    nameGap: 28,
    nameTextStyle: { fontSize: 11 },
    axisLabel: { rotate: n > 12 ? 30 : 0, fontSize: 10 },
  }

  // Y axes
  const leftCols  = seriesDefs.filter((s) => s.axis !== 'right').map((s) => s.col)
  const rightCols = seriesDefs.filter((s) => s.axis === 'right').map((s) => s.col)

  opt.yAxis = [
    {
      type: 'value',
      name: leftCols.length === 1 ? leftCols[0] : '',
      nameLocation: 'middle',
      nameGap: 36,
      nameTextStyle: { fontSize: 11 },
    },
  ]
  if (hasDual) {
    opt.yAxis.push({
      type: 'value',
      name: rightCols.length === 1 ? rightCols[0] : '',
      nameLocation: 'middle',
      nameGap: 36,
      nameTextStyle: { fontSize: 11 },
      splitLine: { show: false }, // avoid double grid-line clutter
    })
  }

  // Series
  opt.series = seriesDefs.map((s, idx) => {
    const raw  = getColumn(table, s.col)
    const vals = raw ? toNumbers(raw) : new Array(n).fill(0)
    const yAxisIndex = hasDual && s.axis === 'right' ? 1 : 0

    // Only bar/line/area are stackable
    const stackable = s.type === 'bar' || s.type === 'line' || s.type === 'area'
    const stack     = stackId && stackable ? stackId : undefined

    const areaStyle = s.type === 'area' ? { color: PALETTE[idx % PALETTE.length], opacity: 0.2 } : undefined
    const seriesType = s.type === 'area' ? 'line' : s.type // 'area' is line+areaStyle in ECharts

    return {
      type: seriesType,
      name: s.col,
      data: vals,
      yAxisIndex,
      ...(stack !== undefined ? { stack } : {}),
      ...(areaStyle ? { areaStyle } : {}),
      ...(seriesType === 'line' ? { smooth: true } : {}),
      ...(seriesType === 'bar'  ? { itemStyle: { borderRadius: [2, 2, 0, 0] } } : {}),
      itemStyle: { color: PALETTE[idx % PALETTE.length] },
      lineStyle: seriesType === 'line' ? { color: PALETTE[idx % PALETTE.length] } : undefined,
    }
  })

  return opt
}

// ---------------------------------------------------------------------------
// Main export
// ---------------------------------------------------------------------------

/**
 * Build an ECharts `option` object from an Apache Arrow Table.
 *
 * Simple (existing) call signature:
 *   buildChartOption({ chartType, table, x, y, color })
 *
 * Advanced call signature (stacking / combo / dual y-axis):
 *   buildChartOption({ chartType, table, x, y, color, encoding, props })
 *
 *   encoding — mirrors the widget spec encoding field:
 *     { x, y (string|SeriesDef[]), color, stack }
 *   props — mirrors the widget spec props field:
 *     { stack, series, secondaryAxis, height }
 *
 *   See the module-level JSDoc comment for the full spec shape.
 *
 * @param {{
 *   chartType:  'line'|'bar'|'scatter'|'area'|'pie',
 *   table:      import('apache-arrow').Table,
 *   x:          string,
 *   y?:         string,
 *   color?:     string,
 *   encoding?:  object,
 *   props?:     object,
 * }} params
 * @returns {object} ECharts option (ready for chart.setOption())
 */
export function buildChartOption({ chartType, table, x, y, color, encoding = {}, props = {} }) {
  if (!table || table.numRows === 0) {
    // Return a safe empty option with a placeholder message
    return {
      color: PALETTE,
      backgroundColor: 'transparent',
      animation: false,
      graphic: [{
        type: 'text',
        left: 'center',
        top: 'middle',
        style: { text: 'No data', fontSize: 14, fill: '#9ca3af' },
      }],
    }
  }

  const type = (chartType || 'scatter').toLowerCase()

  // Types that own a bespoke (non-cartesian or single-value) builder and must
  // bypass the combo/stack/dual-axis multi-series path entirely.
  const SINGLE_BUILDER_TYPES = new Set(['donut', 'heatmap', 'gauge', 'pie'])

  // --- Advanced multi-series path (combo / stack / dual-axis) ---
  // Resolve from both encoding and props; returns null if neither declares multi-series.
  if (!SINGLE_BUILDER_TYPES.has(type)) {
    const multi = resolveMultiSpec({ chartType: type, encoding, props })
    if (multi) {
      return buildMultiSeries(table, x || encoding.x, multi.seriesDefs, multi.stackId)
    }
  }

  // --- Simple single-series path (existing behaviour) ---
  // Stacking on single-series bar/area/line is also supported via props.stack.
  const effectiveY = y || (typeof encoding.y === 'string' ? encoding.y : null)
  const effectiveColor = color || encoding.color

  // Compute a stackId for simple series if requested
  let simpleStackId = null
  if (props.stack === true) simpleStackId = 'total'
  else if (typeof props.stack === 'string' && props.stack) simpleStackId = props.stack
  else if (typeof encoding.stack === 'string' && encoding.stack) simpleStackId = encoding.stack

  switch (type) {
    case 'scatter': return buildScatter(table, x, effectiveY, effectiveColor)
    case 'line':    return buildLineWithStack(table, x, effectiveY, effectiveColor, false, simpleStackId)
    case 'area':    return buildLineWithStack(table, x, effectiveY, effectiveColor, true,  simpleStackId)
    case 'bar':     return buildBarWithStack(table, x, effectiveY, effectiveColor, simpleStackId)
    case 'hbar':    return buildHBar(table, x, effectiveY, effectiveColor, simpleStackId)
    case 'pie':     return buildPie(table, x, effectiveY)
    case 'donut':   return buildDonut(table, x, effectiveY, props)
    case 'heatmap': {
      // Heat value column: encoding.value → encoding.color → color arg.
      const heatVal = encoding.value || effectiveColor
      return buildHeatmap(table, x || encoding.x, effectiveY || encoding.y, heatVal)
    }
    case 'gauge':   return buildGauge(table, encoding.value || effectiveY || x, props)
    default:
      return buildScatter(table, x, effectiveY, effectiveColor)
  }
}
