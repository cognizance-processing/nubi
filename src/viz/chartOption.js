/**
 * chartOption.js — builds ECharts `option` objects from Apache Arrow Tables.
 *
 * API:
 *   buildChartOption({ chartType, table, x, y, color }) -> echartsOption
 *
 * Supported chartType values: 'line' | 'bar' | 'scatter' | 'area' | 'pie'
 *
 * - Reads Arrow columns via table.getChild(name).toArray() (no row materialisation).
 * - Mobile-friendly defaults: responsive grid, touch-enabled tooltips, dataZoom
 *   for large series, sampling for very large datasets.
 * - Categorical color column → per-series grouping (scatter/line) or visualMap (pie).
 * - Numeric color column → continuous visualMap.
 * - Empty / degenerate tables return a safe empty option.
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
// Shared chart defaults (mobile-friendly)
// ---------------------------------------------------------------------------

/**
 * Shared grid / tooltip / legend / toolbox defaults for all chart types.
 *
 * @param {{ showLegend?: boolean, showDataZoom?: boolean }} opts
 * @returns {object} partial ECharts option
 */
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
 * @returns {object} ECharts option
 */
function buildLine(table, xCol, yCol, colorCol, area = false) {
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
    }))
  } else {
    opt.series = [{
      type: 'line',
      name: yCol,
      data: xIsNumeric
        ? xNums.map((x, i) => [x, yVals[i]])
        : yVals,
      smooth: true,
      areaStyle,
      sampling: n > 5000 ? 'lttb' : undefined,
      itemStyle: { color: PALETTE[0] },
      lineStyle: { color: PALETTE[0] },
      areaStyle: area ? { color: PALETTE[0], opacity: 0.2 } : undefined,
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
 * @returns {object} ECharts option
 */
function buildBar(table, xCol, yCol, colorCol) {
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

  if (colorRaw && !isNumericArray(colorRaw)) {
    // Stack bars by category
    const groups = groupByCategory(Array.from({ length: n }, (_, i) => i), yVals, colorRaw)
    opt.series = groups.map((g) => ({
      type: 'bar',
      name: g.category,
      data: g.yVals,
      stack: 'total',
    }))
  } else {
    opt.series = [{
      type: 'bar',
      name: yCol,
      data: yVals,
      itemStyle: { color: PALETTE[0], borderRadius: [2, 2, 0, 0] },
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

// ---------------------------------------------------------------------------
// Main export
// ---------------------------------------------------------------------------

/**
 * Build an ECharts `option` object from an Apache Arrow Table.
 *
 * @param {{
 *   chartType: 'line'|'bar'|'scatter'|'area'|'pie',
 *   table: import('apache-arrow').Table,
 *   x: string,
 *   y: string,
 *   color?: string,
 * }} params
 * @returns {object} ECharts option (ready for chart.setOption())
 */
export function buildChartOption({ chartType, table, x, y, color }) {
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

  switch (type) {
    case 'scatter': return buildScatter(table, x, y, color)
    case 'line':    return buildLine(table, x, y, color, false)
    case 'area':    return buildLine(table, x, y, color, true)
    case 'bar':     return buildBar(table, x, y, color)
    case 'pie':     return buildPie(table, x, y)
    default:
      return buildScatter(table, x, y, color)
  }
}
