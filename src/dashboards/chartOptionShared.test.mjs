/**
 * chartOptionShared.test.mjs — Tests for new chart types and config features
 * exposed through the shared embed/widgets/chart-options.js builder.
 *
 * Covers:
 *   - All 17 chart types produce a valid ECharts option (no throw)
 *   - Sankey: nodes + links structure
 *   - Boxplot: five-number summary output
 *   - Fan chart: confidence band series + forecast line
 *   - Reference lines (markLine) present on cartesian charts
 *   - Dual-axis (y2) on line chart
 *   - Config round-trip: options persist as expected (stack, orientation, dataLabels)
 *   - ChartWidget compat: resolveEncoding / resolveConfig helpers (pure logic)
 *
 * Run with:
 *   npm run test:dash
 */

import { test, describe } from 'node:test'
import assert from 'node:assert/strict'

// Import via the shared re-export (same path ChartWidget uses)
import { buildChartOption, SUPPORTED_TYPES, tableView } from '../../src/lib/chartOptionShared.js'

// ---------------------------------------------------------------------------
// Table helpers
// ---------------------------------------------------------------------------

function makeRows(rows) {
  return tableView(rows)
}

const CAT_NUM = [
  { cat: 'A', val: 10 },
  { cat: 'B', val: 20 },
  { cat: 'C', val: 15 },
]

const XY_NUM = [
  { x: 1, y: 10 },
  { x: 2, y: 20 },
  { x: 3, y: 15 },
]

const SANKEY_ROWS = [
  { src: 'A', tgt: 'C', flow: 5 },
  { src: 'B', tgt: 'C', flow: 8 },
  { src: 'C', tgt: 'D', flow: 13 },
]

const BOXPLOT_ROWS = [
  { cat: 'G1', val: 10 },
  { cat: 'G1', val: 20 },
  { cat: 'G1', val: 15 },
  { cat: 'G1', val: 30 },
  { cat: 'G1', val: 25 },
  { cat: 'G2', val: 5 },
  { cat: 'G2', val: 12 },
  { cat: 'G2', val: 18 },
]

const FAN_ROWS = [
  { t: '2024-Q1', mid: 100, lo: 90, hi: 110 },
  { t: '2024-Q2', mid: 120, lo: 105, hi: 135 },
  { t: '2024-Q3', mid: 115, lo: 98, hi: 132 },
]

const CANDLE_ROWS = [
  { date: '2024-01', open: 100, close: 110, low: 95, high: 115 },
  { date: '2024-02', open: 110, close: 105, low: 100, high: 118 },
]

const HEATMAP_ROWS = [
  { x: 'Mon', y: 'AM', val: 5 },
  { x: 'Mon', y: 'PM', val: 8 },
  { x: 'Tue', y: 'AM', val: 3 },
]

// ---------------------------------------------------------------------------
// 1. SUPPORTED_TYPES export
// ---------------------------------------------------------------------------

describe('SUPPORTED_TYPES', () => {
  test('exports an array of 18 types', () => {
    assert.ok(Array.isArray(SUPPORTED_TYPES))
    assert.equal(SUPPORTED_TYPES.length, 18)
  })

  test('includes all expected types', () => {
    const required = [
      'bar', 'line', 'area', 'scatter', 'bubble', 'pie', 'donut',
      'sankey', 'funnel', 'waterfall', 'heatmap', 'radar', 'treemap',
      'boxplot', 'gauge', 'candlestick', 'fan', 'combo',
    ]
    for (const t of required) {
      assert.ok(SUPPORTED_TYPES.includes(t), `SUPPORTED_TYPES must include '${t}'`)
    }
  })
})

// ---------------------------------------------------------------------------
// 2. All 17 types produce a valid option without throwing
// ---------------------------------------------------------------------------

describe('all 17 types - no throw', () => {
  const cases = [
    { type: 'bar',         table: CAT_NUM, encoding: { x: 'cat', y: 'val' } },
    { type: 'line',        table: XY_NUM,  encoding: { x: 'x', y: 'y' } },
    { type: 'area',        table: XY_NUM,  encoding: { x: 'x', y: 'y' } },
    { type: 'scatter',     table: XY_NUM,  encoding: { x: 'x', y: 'y' } },
    { type: 'bubble',      table: XY_NUM,  encoding: { x: 'x', y: 'y' } },
    { type: 'pie',         table: CAT_NUM, encoding: { x: 'cat', y: 'val' } },
    { type: 'donut',       table: CAT_NUM, encoding: { x: 'cat', y: 'val' } },
    { type: 'sankey',      table: SANKEY_ROWS,  encoding: { source: 'src', target: 'tgt', value: 'flow' } },
    { type: 'funnel',      table: CAT_NUM, encoding: { x: 'cat', y: 'val' } },
    { type: 'waterfall',   table: CAT_NUM, encoding: { x: 'cat', y: 'val' } },
    { type: 'heatmap',     table: HEATMAP_ROWS, encoding: { x: 'x', color: 'y', value: 'val' } },
    { type: 'radar',       table: CAT_NUM, encoding: { x: 'cat', y: 'val' } },
    { type: 'treemap',     table: CAT_NUM, encoding: { x: 'cat', y: 'val' } },
    { type: 'boxplot',     table: BOXPLOT_ROWS, encoding: { x: 'cat', y: 'val' } },
    { type: 'gauge',       table: XY_NUM,  encoding: { y: 'y' } },
    { type: 'candlestick', table: CANDLE_ROWS,  encoding: { x: 'date', open: 'open', close: 'close', low: 'low', high: 'high' } },
    { type: 'fan',         table: FAN_ROWS, encoding: { x: 't', y: 'mid', lower: 'lo', upper: 'hi' } },
  ]

  for (const { type, table, encoding } of cases) {
    test(`${type}: builds without throwing`, () => {
      assert.doesNotThrow(() => {
        const opt = buildChartOption({ type, table, encoding })
        assert.ok(opt && typeof opt === 'object', `${type}: option must be an object`)
      })
    })
  }
})

// ---------------------------------------------------------------------------
// 3. Sankey — specific structure
// ---------------------------------------------------------------------------

describe('sankey', () => {
  test('produces series type sankey', () => {
    const opt = buildChartOption({
      type: 'sankey',
      table: SANKEY_ROWS,
      encoding: { source: 'src', target: 'tgt', value: 'flow' },
    })
    assert.ok(Array.isArray(opt.series))
    assert.equal(opt.series[0].type, 'sankey')
  })

  test('nodes derived from source + target columns', () => {
    const opt = buildChartOption({
      type: 'sankey',
      table: SANKEY_ROWS,
      encoding: { source: 'src', target: 'tgt', value: 'flow' },
    })
    const nodeNames = opt.series[0].data.map(n => n.name)
    assert.ok(nodeNames.includes('A'))
    assert.ok(nodeNames.includes('B'))
    assert.ok(nodeNames.includes('C'))
    assert.ok(nodeNames.includes('D'))
  })

  test('links match rows', () => {
    const opt = buildChartOption({
      type: 'sankey',
      table: SANKEY_ROWS,
      encoding: { source: 'src', target: 'tgt', value: 'flow' },
    })
    assert.equal(opt.series[0].links.length, 3)
    assert.equal(opt.series[0].links[0].source, 'A')
    assert.equal(opt.series[0].links[0].target, 'C')
    assert.equal(opt.series[0].links[0].value, 5)
  })
})

// ---------------------------------------------------------------------------
// 4. Boxplot — five-number summary
// ---------------------------------------------------------------------------

describe('boxplot', () => {
  test('produces series type boxplot', () => {
    const opt = buildChartOption({
      type: 'boxplot',
      table: BOXPLOT_ROWS,
      encoding: { x: 'cat', y: 'val' },
    })
    assert.ok(Array.isArray(opt.series))
    assert.equal(opt.series[0].type, 'boxplot')
  })

  test('each boxplot data point is a 5-element array [min, q1, med, q3, max]', () => {
    const opt = buildChartOption({
      type: 'boxplot',
      table: BOXPLOT_ROWS,
      encoding: { x: 'cat', y: 'val' },
    })
    const data = opt.series[0].data
    assert.ok(Array.isArray(data))
    assert.equal(data.length, 2, 'two groups (G1, G2)')
    for (const d of data) {
      assert.ok(Array.isArray(d) && d.length === 5, 'each entry must be [min, q1, med, q3, max]')
      // verify min <= q1 <= med <= q3 <= max
      assert.ok(d[0] <= d[1] && d[1] <= d[2] && d[2] <= d[3] && d[3] <= d[4])
    }
  })
})

// ---------------------------------------------------------------------------
// 5. Fan chart — confidence band + forecast line
// ---------------------------------------------------------------------------

describe('fan', () => {
  test('produces at least 2 series (band layers + forecast)', () => {
    const opt = buildChartOption({
      type: 'fan',
      table: FAN_ROWS,
      encoding: { x: 't', y: 'mid', lower: 'lo', upper: 'hi' },
    })
    assert.ok(Array.isArray(opt.series))
    // lower-band baseline + confidence area + forecast line = 3 series
    assert.ok(opt.series.length >= 2, 'fan with bounds must produce >= 2 series')
  })

  test('last series is the forecast line', () => {
    const opt = buildChartOption({
      type: 'fan',
      table: FAN_ROWS,
      encoding: { x: 't', y: 'mid', lower: 'lo', upper: 'hi' },
    })
    const last = opt.series[opt.series.length - 1]
    assert.equal(last.type, 'line')
    assert.ok(last.z && last.z >= 3, 'forecast must be drawn on top (z >= 3)')
  })

  test('without bounds produces a single line series', () => {
    const opt = buildChartOption({
      type: 'fan',
      table: XY_NUM,
      encoding: { x: 'x', y: 'y' },
    })
    assert.equal(opt.series.length, 1)
    assert.equal(opt.series[0].type, 'line')
  })
})

// ---------------------------------------------------------------------------
// 6. Reference lines (markLine) — cartesian charts
// ---------------------------------------------------------------------------

describe('referenceLines', () => {
  test('bar chart: single y reference line appears in markLine', () => {
    const opt = buildChartOption({
      type: 'bar',
      table: CAT_NUM,
      encoding: { x: 'cat', y: 'val' },
      config: {
        referenceLines: [{ value: 12, axis: 'y', label: 'Target', type: 'dashed' }],
      },
    })
    const ml = opt.series[0].markLine
    assert.ok(ml, 'markLine must be present')
    assert.ok(Array.isArray(ml.data) && ml.data.length === 1)
    assert.equal(ml.data[0].yAxis, 12)
    assert.equal(ml.data[0].lineStyle.type, 'dashed')
  })

  test('line chart: multiple reference lines', () => {
    const opt = buildChartOption({
      type: 'line',
      table: XY_NUM,
      encoding: { x: 'x', y: 'y' },
      config: {
        referenceLines: [
          { value: 10, axis: 'y', label: 'Low' },
          { value: 18, axis: 'y', label: 'High', type: 'solid', color: '#ef4444' },
        ],
      },
    })
    const ml = opt.series[0].markLine
    assert.ok(ml)
    assert.equal(ml.data.length, 2)
    assert.equal(ml.data[1].lineStyle.color, '#ef4444')
    assert.equal(ml.data[1].lineStyle.type, 'solid')
  })

  test('non-cartesian chart (pie): no markLine injected', () => {
    const opt = buildChartOption({
      type: 'pie',
      table: CAT_NUM,
      encoding: { x: 'cat', y: 'val' },
      config: {
        referenceLines: [{ value: 10, axis: 'y' }],
      },
    })
    // pie series should not have markLine (decorateCartesian skips non-cartesian)
    assert.ok(!opt.series[0].markLine, 'pie must not have markLine')
  })
})

// ---------------------------------------------------------------------------
// 7. Dual-axis (y2) on line chart
// ---------------------------------------------------------------------------

describe('dual y-axis', () => {
  const TABLE_Y2 = [
    { x: 1, y: 10, y2: 1000 },
    { x: 2, y: 20, y2: 2000 },
    { x: 3, y: 15, y2: 1500 },
  ]

  test('line chart with y2 encoding produces two yAxis entries', () => {
    const opt = buildChartOption({
      type: 'line',
      table: TABLE_Y2,
      encoding: { x: 'x', y: 'y', y2: 'y2' },
    })
    assert.ok(Array.isArray(opt.yAxis), 'yAxis must be an array when dual-axis is active')
    assert.equal(opt.yAxis.length, 2, 'must have exactly 2 yAxis entries')
  })

  test('second series has yAxisIndex: 1', () => {
    const opt = buildChartOption({
      type: 'line',
      table: TABLE_Y2,
      encoding: { x: 'x', y: 'y', y2: 'y2' },
    })
    const y2series = opt.series.find(s => s.yAxisIndex === 1)
    assert.ok(y2series, 'one series must target yAxisIndex 1')
    assert.equal(y2series.name, 'y2')
  })

  test('y2Axis config is applied to second yAxis', () => {
    const opt = buildChartOption({
      type: 'line',
      table: TABLE_Y2,
      encoding: { x: 'x', y: 'y', y2: 'y2' },
      config: { y2Axis: { label: 'Revenue' } },
    })
    assert.equal(opt.yAxis[1].name, 'Revenue')
  })
})

// ---------------------------------------------------------------------------
// 8. Config round-trip — stack, orientation, dataLabels
// ---------------------------------------------------------------------------

describe('config round-trip', () => {
  test('stack:normal → series share a stack group', () => {
    const TABLE = [
      { cat: 'A', v1: 10, grp: 'X' },
      { cat: 'A', v1: 20, grp: 'Y' },
      { cat: 'B', v1: 15, grp: 'X' },
      { cat: 'B', v1: 25, grp: 'Y' },
    ]
    const opt = buildChartOption({
      type: 'bar',
      table: TABLE,
      encoding: { x: 'cat', y: 'v1', color: 'grp' },
      config: { stack: true },
    })
    const stacks = opt.series.map(s => s.stack).filter(Boolean)
    assert.ok(stacks.length >= 2, 'multiple stacked series expected')
    // All stacks share same group id
    assert.ok(stacks.every(s => s === stacks[0]), 'all stacked series must share the same stack id')
  })

  test('stack:percent → series stacked with percent values (0-100 range)', () => {
    const TABLE = [
      { cat: 'A', val: 60, grp: 'X' },
      { cat: 'A', val: 40, grp: 'Y' },
    ]
    const opt = buildChartOption({
      type: 'bar',
      table: TABLE,
      encoding: { x: 'cat', y: 'val', color: 'grp' },
      config: { stack: 'percent' },
    })
    // Values should be near 60 and 40 (percentage of 100)
    const seriesValues = opt.series.map(s => s.data[0])
    const total = seriesValues.reduce((a, b) => a + b, 0)
    assert.ok(Math.abs(total - 100) < 0.1, `percent-stack total must be ~100, got ${total}`)
  })

  test('orientation:horizontal → x and y axes are swapped (category on yAxis)', () => {
    const opt = buildChartOption({
      type: 'bar',
      table: CAT_NUM,
      encoding: { x: 'cat', y: 'val' },
      config: { orientation: 'horizontal' },
    })
    // horizontal bar: category axis is yAxis, value axis is xAxis
    assert.equal(opt.yAxis.type, 'category', 'horizontal bar must put category on yAxis')
    assert.ok(Array.isArray(opt.yAxis.data), 'yAxis must have category data')
  })

  test('dataLabels:true → first cartesian series has label.show === true', () => {
    const opt = buildChartOption({
      type: 'bar',
      table: CAT_NUM,
      encoding: { x: 'cat', y: 'val' },
      config: { dataLabels: true },
    })
    assert.equal(opt.series[0].label?.show, true)
  })

  test('legend:false → no legend in option', () => {
    const opt = buildChartOption({
      type: 'bar',
      table: CAT_NUM,
      encoding: { x: 'cat', y: 'val' },
      config: { legend: false },
    })
    assert.ok(!opt.legend, 'legend must not be present when disabled')
  })

  test('palette override applied to color array', () => {
    const custom = ['#ff0000', '#00ff00', '#0000ff']
    const opt = buildChartOption({
      type: 'bar',
      table: CAT_NUM,
      encoding: { x: 'cat', y: 'val' },
      config: { palette: custom },
    })
    assert.deepEqual(opt.color, custom)
  })
})

// ---------------------------------------------------------------------------
// 9. Candlestick — OHLC structure
// ---------------------------------------------------------------------------

describe('candlestick', () => {
  test('produces series type candlestick', () => {
    const opt = buildChartOption({
      type: 'candlestick',
      table: CANDLE_ROWS,
      encoding: { x: 'date', open: 'open', close: 'close', low: 'low', high: 'high' },
    })
    assert.equal(opt.series[0].type, 'candlestick')
  })

  test('each data point is [open, close, low, high]', () => {
    const opt = buildChartOption({
      type: 'candlestick',
      table: CANDLE_ROWS,
      encoding: { x: 'date', open: 'open', close: 'close', low: 'low', high: 'high' },
    })
    const d = opt.series[0].data[0]
    assert.ok(Array.isArray(d) && d.length === 4, 'each point must be a 4-element array')
    assert.equal(d[0], 100) // open
    assert.equal(d[1], 110) // close
    assert.equal(d[2], 95)  // low
    assert.equal(d[3], 115) // high
  })
})

// ---------------------------------------------------------------------------
// 10. tableView adapter
// ---------------------------------------------------------------------------

describe('tableView', () => {
  test('wraps row array into a TableView', () => {
    const tv = tableView([{ a: 1, b: 2 }, { a: 3, b: 4 }])
    assert.equal(tv.numRows, 2)
    assert.deepEqual(tv.columns, ['a', 'b'])
    assert.deepEqual(Array.from(tv.col('a')), [1, 3])
  })

  test('returns null or empty for unknown column', () => {
    const tv = tableView([{ a: 1 }])
    // row-array path: col('z') returns an array of undefineds (row.z === undefined),
    // which is falsy-equivalent for chart rendering purposes
    const result = tv.col('z')
    // Either null (Arrow path) or an array of undefineds (row-array path) is acceptable
    const isFalsy = result === null || (Array.isArray(result) && result.every(v => v === undefined))
    assert.ok(isFalsy, 'unknown column must return null or array of undefineds')
  })

  test('returns passed-through TableView as-is', () => {
    const tv = tableView([{ x: 1 }])
    const tv2 = tableView(tv)
    assert.equal(tv, tv2, 'already-wrapped view must be returned as-is')
  })
})
