/**
 * chartOption.test.mjs — unit tests for buildChartOption() advanced features.
 *
 * Run with:
 *   npm run test:dash
 *   # or directly:
 *   node --test src/dashboards/chartOption.test.mjs
 *
 * Coverage
 * --------
 *   REGRESSION: simple single-series line/scatter produces a valid single-series option
 *   STACKING:   props.stack:true → bar series share a stack id
 *   STACKING:   props.stack:true → area/line series share a stack id
 *   STACKING:   encoding.stack (string) → custom stack group id used
 *   COMBO:      encoding.y as array → multiple series with differing ECharts types
 *   COMBO:      props.series array → multiple series with differing ECharts types
 *   DUAL AXIS:  series with axis:'right' → 2 yAxis entries + yAxisIndex:1 on that series
 *   DUAL AXIS:  props.secondaryAxis column names → 2 yAxis entries
 *   EDGE:       empty table → returns a safe empty option (graphic placeholder)
 */

import { test } from 'node:test'
import assert from 'node:assert/strict'
import { makeTable, vectorFromArray, Float64, Utf8 } from 'apache-arrow'
import { buildChartOption } from '../../src/viz/chartOption.js'

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/**
 * Build a small Arrow table with month (Utf8), revenue (Float64), profit (Float64).
 */
function makeMonthTable() {
  return makeTable({
    month:   vectorFromArray(['Jan', 'Feb', 'Mar', 'Apr'], new Utf8()),
    revenue: vectorFromArray([100, 200, 150, 300], new Float64()),
    profit:  vectorFromArray([10, 30, 20, 60], new Float64()),
  })
}

/**
 * Build a small numeric x/y scatter table.
 */
function makeScatterTable() {
  return makeTable({
    x: vectorFromArray([1, 2, 3, 4], new Float64()),
    y: vectorFromArray([10, 20, 15, 25], new Float64()),
  })
}

// ---------------------------------------------------------------------------
// REGRESSION — simple paths must still work
// ---------------------------------------------------------------------------

test('simple bar: single series, single yAxis', () => {
  const table = makeMonthTable()
  const opt   = buildChartOption({ chartType: 'bar', table, x: 'month', y: 'revenue' })

  assert.ok(Array.isArray(opt.series), 'series must be an array')
  assert.equal(opt.series.length, 1, 'single-series for simple bar')
  assert.equal(opt.series[0].type, 'bar', 'series type must be bar')
  // Single y-axis (plain object, not an array)
  assert.ok(!Array.isArray(opt.yAxis) || opt.yAxis.length === 1,
    'single yAxis expected for simple bar')
  // No stack
  assert.equal(opt.series[0].stack, undefined, 'no stack on simple bar without props.stack')
})

test('simple line: single series, valid option', () => {
  const table = makeMonthTable()
  const opt   = buildChartOption({ chartType: 'line', table, x: 'month', y: 'revenue' })

  assert.ok(Array.isArray(opt.series), 'series must be an array')
  assert.equal(opt.series.length, 1, 'single-series for simple line')
  assert.equal(opt.series[0].type, 'line', 'series type must be line')
})

test('simple scatter: single series, valid option', () => {
  const table = makeScatterTable()
  const opt   = buildChartOption({ chartType: 'scatter', table, x: 'x', y: 'y' })

  assert.ok(Array.isArray(opt.series), 'series must be an array')
  assert.equal(opt.series.length, 1, 'single-series for simple scatter')
  assert.equal(opt.series[0].type, 'scatter', 'series type must be scatter')
})

test('empty table → safe empty option (no series, has graphic)', () => {
  const emptyTable = { numRows: 0 } // minimal empty sentinel
  const opt = buildChartOption({ chartType: 'bar', table: emptyTable, x: 'month', y: 'revenue' })

  assert.ok(!opt.series || opt.series.length === 0,
    'empty table must produce no real series')
  assert.ok(Array.isArray(opt.graphic) && opt.graphic.length > 0,
    'empty table must produce a graphic placeholder')
})

// ---------------------------------------------------------------------------
// STACKING
// ---------------------------------------------------------------------------

test('stacking: props.stack=true → bar series get a shared stack id', () => {
  const table = makeMonthTable()
  // Multi-series bar via props.series (to have 2 stackable bars)
  const opt = buildChartOption({
    chartType: 'bar',
    table,
    x: 'month',
    encoding: {},
    props: {
      stack: true,
      series: [
        { col: 'revenue', type: 'bar' },
        { col: 'profit',  type: 'bar' },
      ],
    },
  })

  assert.ok(Array.isArray(opt.series), 'series must be an array')
  assert.equal(opt.series.length, 2, 'two bar series for combo stack')

  const stackIds = opt.series.map((s) => s.stack)
  // Both must have a stack id and it must be the same value
  assert.ok(stackIds[0], 'first series must have a stack id')
  assert.ok(stackIds[1], 'second series must have a stack id')
  assert.equal(stackIds[0], stackIds[1], 'stacked series must share the same stack id')
})

test('stacking: props.stack string → uses that string as stack id', () => {
  const table = makeMonthTable()
  const opt = buildChartOption({
    chartType: 'bar',
    table,
    x: 'month',
    encoding: {},
    props: {
      stack: 'myStack',
      series: [
        { col: 'revenue', type: 'bar' },
        { col: 'profit',  type: 'bar' },
      ],
    },
  })

  for (const s of opt.series) {
    assert.equal(s.stack, 'myStack', `series "${s.name}" must use 'myStack' as stack id`)
  }
})

test('stacking: encoding.stack string → custom stack id used', () => {
  const table = makeMonthTable()
  const opt = buildChartOption({
    chartType: 'bar',
    table,
    x: 'month',
    encoding: { stack: 'encStack' },
    props: {
      series: [
        { col: 'revenue', type: 'bar' },
        { col: 'profit',  type: 'bar' },
      ],
    },
  })

  for (const s of opt.series) {
    assert.equal(s.stack, 'encStack', `series "${s.name}" must use 'encStack' as stack id`)
  }
})

test('stacking: props.stack=true with area series → area series are stacked', () => {
  const table = makeMonthTable()
  const opt = buildChartOption({
    chartType: 'line',
    table,
    x: 'month',
    encoding: {},
    props: {
      stack: true,
      series: [
        { col: 'revenue', type: 'area' },
        { col: 'profit',  type: 'area' },
      ],
    },
  })

  assert.equal(opt.series.length, 2, 'two series')
  for (const s of opt.series) {
    // ECharts renders 'area' as type:'line' + areaStyle
    assert.equal(s.type, 'line', `area series must have ECharts type 'line', got '${s.type}'`)
    assert.ok(s.stack, `area series "${s.name}" must have a stack id`)
  }
  // Both share same stack
  assert.equal(opt.series[0].stack, opt.series[1].stack, 'area series must share a stack id')
})

test('stacking: scatter series are NOT stacked even when props.stack=true', () => {
  const table = makeScatterTable()
  // scatter with props.stack — should be ignored (scatter not stackable)
  const opt = buildChartOption({
    chartType: 'scatter',
    table,
    x: 'x',
    y: 'y',
    props: { stack: true },
  })

  assert.ok(Array.isArray(opt.series), 'series must be an array')
  // Simple scatter falls through to the single-series path; no stack expected
  for (const s of opt.series) {
    assert.equal(s.stack, undefined, 'scatter series must not receive a stack id')
  }
})

// ---------------------------------------------------------------------------
// COMBO (per-series chart type)
// ---------------------------------------------------------------------------

test('combo via encoding.y array → multiple series with differing types', () => {
  const table = makeMonthTable()
  const opt = buildChartOption({
    chartType: 'bar',
    table,
    x: 'month',
    encoding: {
      y: [
        { col: 'revenue', type: 'bar' },
        { col: 'profit',  type: 'line' },
      ],
    },
  })

  assert.equal(opt.series.length, 2, 'two series for combo')

  const types = opt.series.map((s) => s.type)
  assert.ok(types.includes('bar'),  'combo must include a bar series')
  assert.ok(types.includes('line'), 'combo must include a line series')

  // Verify they differ
  assert.notEqual(types[0], types[1], 'combo series must have different ECharts types')
})

test('combo via props.series array → multiple series with differing types', () => {
  const table = makeMonthTable()
  const opt = buildChartOption({
    chartType: 'bar',
    table,
    x: 'month',
    props: {
      series: [
        { col: 'revenue', type: 'bar' },
        { col: 'profit',  type: 'line' },
      ],
    },
  })

  assert.equal(opt.series.length, 2, 'two series for combo')

  const types = opt.series.map((s) => s.type)
  assert.ok(types.includes('bar'),  'combo must include a bar series')
  assert.ok(types.includes('line'), 'combo must include a line series')
  assert.notEqual(types[0], types[1], 'combo series must have different ECharts types')
})

test('combo: props.series wins over encoding.y array when both present', () => {
  const table = makeMonthTable()
  const opt = buildChartOption({
    chartType: 'bar',
    table,
    x: 'month',
    encoding: {
      y: [
        { col: 'revenue', type: 'line' }, // would produce line first
        { col: 'profit',  type: 'line' },
      ],
    },
    props: {
      series: [
        { col: 'revenue', type: 'bar' }, // props.series wins
        { col: 'profit',  type: 'bar' },
      ],
    },
  })

  for (const s of opt.series) {
    assert.equal(s.type, 'bar', `props.series should override encoding.y; got type '${s.type}'`)
  }
})

test('combo: series names match the col names declared in spec', () => {
  const table = makeMonthTable()
  const opt = buildChartOption({
    chartType: 'bar',
    table,
    x: 'month',
    props: {
      series: [
        { col: 'revenue', type: 'bar' },
        { col: 'profit',  type: 'line' },
      ],
    },
  })

  const names = opt.series.map((s) => s.name)
  assert.ok(names.includes('revenue'), 'series name "revenue" must be present')
  assert.ok(names.includes('profit'),  'series name "profit" must be present')
})

// ---------------------------------------------------------------------------
// DUAL Y-AXIS
// ---------------------------------------------------------------------------

test('dual axis via axis:right → 2 yAxis entries', () => {
  const table = makeMonthTable()
  const opt = buildChartOption({
    chartType: 'bar',
    table,
    x: 'month',
    props: {
      series: [
        { col: 'revenue', type: 'bar',  axis: 'left' },
        { col: 'profit',  type: 'line', axis: 'right' },
      ],
    },
  })

  assert.ok(Array.isArray(opt.yAxis), 'yAxis must be an array when dual-axis is active')
  assert.equal(opt.yAxis.length, 2, 'must have exactly 2 yAxis entries for dual-axis')
})

test('dual axis → right-axis series has yAxisIndex:1', () => {
  const table = makeMonthTable()
  const opt = buildChartOption({
    chartType: 'bar',
    table,
    x: 'month',
    props: {
      series: [
        { col: 'revenue', type: 'bar',  axis: 'left' },
        { col: 'profit',  type: 'line', axis: 'right' },
      ],
    },
  })

  const leftSeries  = opt.series.find((s) => s.name === 'revenue')
  const rightSeries = opt.series.find((s) => s.name === 'profit')

  assert.ok(leftSeries,  '"revenue" series must exist')
  assert.ok(rightSeries, '"profit" series must exist')

  assert.equal(leftSeries.yAxisIndex,  0, 'left-axis series must have yAxisIndex:0')
  assert.equal(rightSeries.yAxisIndex, 1, 'right-axis series must have yAxisIndex:1')
})

test('dual axis via props.secondaryAxis → 2 yAxis entries + correct yAxisIndex', () => {
  const table = makeMonthTable()
  const opt = buildChartOption({
    chartType: 'bar',
    table,
    x: 'month',
    props: {
      secondaryAxis: ['profit'],
      series: [
        { col: 'revenue', type: 'bar'  },
        { col: 'profit',  type: 'line' },
      ],
    },
  })

  assert.ok(Array.isArray(opt.yAxis), 'yAxis must be an array when dual-axis is active')
  assert.equal(opt.yAxis.length, 2, 'must have exactly 2 yAxis entries')

  const rightSeries = opt.series.find((s) => s.name === 'profit')
  assert.equal(rightSeries.yAxisIndex, 1,
    '"profit" (in secondaryAxis) must have yAxisIndex:1')

  const leftSeries = opt.series.find((s) => s.name === 'revenue')
  assert.equal(leftSeries.yAxisIndex, 0,
    '"revenue" (not in secondaryAxis) must have yAxisIndex:0')
})

test('dual axis via encoding.y array with axis:right', () => {
  const table = makeMonthTable()
  const opt = buildChartOption({
    chartType: 'bar',
    table,
    x: 'month',
    encoding: {
      y: [
        { col: 'revenue', type: 'bar' },
        { col: 'profit',  type: 'line', axis: 'right' },
      ],
    },
  })

  assert.ok(Array.isArray(opt.yAxis), 'yAxis must be array for dual-axis')
  assert.equal(opt.yAxis.length, 2, 'must have 2 yAxis entries')

  const rightSeries = opt.series.find((s) => s.name === 'profit')
  assert.equal(rightSeries.yAxisIndex, 1, 'right-axis series yAxisIndex must be 1')
})

test('no dual axis when all series are on left → yAxis is object or single-entry array', () => {
  const table = makeMonthTable()
  const opt = buildChartOption({
    chartType: 'bar',
    table,
    x: 'month',
    props: {
      series: [
        { col: 'revenue', type: 'bar' },
        { col: 'profit',  type: 'bar' },
      ],
    },
  })

  // yAxis should be an array with 1 entry (multi-series always uses array form)
  if (Array.isArray(opt.yAxis)) {
    assert.equal(opt.yAxis.length, 1, 'single y-axis when no right-axis series')
  } else {
    assert.ok(opt.yAxis, 'yAxis must exist')
  }

  for (const s of opt.series) {
    assert.equal(s.yAxisIndex, 0, `series "${s.name}" must have yAxisIndex:0 on left-only chart`)
  }
})
