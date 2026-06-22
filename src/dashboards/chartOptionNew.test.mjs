/**
 * chartOptionNew.test.mjs — Tests for the new chart types added in BET4:
 *   waterfall, quadrant (zoned scatter), sparkline.
 *
 * Also tests buildSparklineOption (plain-array variant for KPI tiles).
 *
 * Run with:
 *   npm run test:dash
 */

import { test } from 'node:test'
import assert from 'node:assert/strict'
import { makeTable, vectorFromArray, Float64, Utf8 } from 'apache-arrow'
import { buildChartOption, buildSparklineOption } from '../../src/viz/chartOption.js'

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function makeDeltaTable() {
  return makeTable({
    category: vectorFromArray(['Start', 'Revenue', 'Cost', 'SubTotal', 'Tax', 'Net'], new Utf8()),
    delta:    vectorFromArray([0, 500, -200, 0, -50, 0], new Float64()),
  })
}

function makeXYTable() {
  return makeTable({
    x: vectorFromArray([1, 4, 2, 8, 3, 7], new Float64()),
    y: vectorFromArray([10, 40, 20, 80, 30, 70], new Float64()),
  })
}

function makeSparkTable() {
  return makeTable({
    value: vectorFromArray([10, 20, 15, 30, 25], new Float64()),
  })
}

// ---------------------------------------------------------------------------
// WATERFALL
// ---------------------------------------------------------------------------

test('waterfall: produces two stacked bar series (spacer + delta)', () => {
  const table = makeDeltaTable()
  const opt = buildChartOption({ chartType: 'waterfall', table, x: 'category', y: 'delta' })

  assert.ok(Array.isArray(opt.series), 'must have series')
  assert.equal(opt.series.length, 2, 'waterfall must have exactly 2 series')
  assert.equal(opt.series[0].type, 'bar', 'first series type must be bar')
  assert.equal(opt.series[1].type, 'bar', 'second series type must be bar')
})

test('waterfall: both series share the same stack group', () => {
  const table = makeDeltaTable()
  const opt = buildChartOption({ chartType: 'waterfall', table, x: 'category', y: 'delta' })

  assert.ok(opt.series[0].stack, 'spacer must have a stack id')
  assert.equal(opt.series[0].stack, opt.series[1].stack, 'both series must share the same stack id')
})

test('waterfall: spacer series has transparent item style', () => {
  const table = makeDeltaTable()
  const opt = buildChartOption({ chartType: 'waterfall', table, x: 'category', y: 'delta' })

  const spacer = opt.series[0]
  assert.ok(spacer.itemStyle, 'spacer must have itemStyle')
  assert.equal(spacer.itemStyle.color, 'transparent', 'spacer bar must be transparent')
})

test('waterfall: x-axis categories match input', () => {
  const table = makeDeltaTable()
  const opt = buildChartOption({ chartType: 'waterfall', table, x: 'category', y: 'delta' })

  assert.deepEqual(opt.xAxis.data, ['Start', 'Revenue', 'Cost', 'SubTotal', 'Tax', 'Net'])
})

test('waterfall: delta series data length matches row count', () => {
  const table = makeDeltaTable()
  const opt = buildChartOption({ chartType: 'waterfall', table, x: 'category', y: 'delta' })

  assert.equal(opt.series[1].data.length, 6, 'one data point per row')
})

test('waterfall: empty table → safe empty option', () => {
  const opt = buildChartOption({ chartType: 'waterfall', table: null, x: 'category', y: 'delta' })
  assert.ok(!opt.series || opt.series.length === 0, 'null table must produce no series')
})

// ---------------------------------------------------------------------------
// QUADRANT
// ---------------------------------------------------------------------------

test('quadrant: produces a scatter series', () => {
  const table = makeXYTable()
  const opt = buildChartOption({ chartType: 'quadrant', table, x: 'x', y: 'y' })

  assert.ok(Array.isArray(opt.series), 'must have series')
  assert.ok(opt.series.length >= 1, 'must have at least one series')
  assert.equal(opt.series[0].type, 'scatter', 'series type must be scatter')
})

test('quadrant: first series has markArea (quadrant backgrounds)', () => {
  const table = makeXYTable()
  const opt = buildChartOption({ chartType: 'quadrant', table, x: 'x', y: 'y' })

  const s = opt.series[0]
  assert.ok(s.markArea, 'first series must have a markArea')
  assert.ok(Array.isArray(s.markArea.data), 'markArea.data must be an array')
  assert.equal(s.markArea.data.length, 4, 'must have 4 quadrant areas')
})

test('quadrant: first series has markLine (reference lines)', () => {
  const table = makeXYTable()
  const opt = buildChartOption({ chartType: 'quadrant', table, x: 'x', y: 'y' })

  const s = opt.series[0]
  assert.ok(s.markLine, 'first series must have markLine')
  assert.equal(s.markLine.data.length, 2, 'must have 2 reference lines (x and y midpoints)')
})

test('quadrant: props.xMid / props.yMid override the default median mid-points', () => {
  const table = makeXYTable()
  const opt = buildChartOption({
    chartType: 'quadrant', table, x: 'x', y: 'y',
    props: { xMid: 5, yMid: 50 },
  })

  // The markLine data contains the x and y reference lines.
  const lines = opt.series[0].markLine.data
  const xLine = lines.find(l => 'xAxis' in l)
  const yLine = lines.find(l => 'yAxis' in l)
  assert.equal(xLine.xAxis, 5, 'xMid prop must be used as x reference line')
  assert.equal(yLine.yAxis, 50, 'yMid prop must be used as y reference line')
})

test('quadrant: empty table → safe empty option', () => {
  const opt = buildChartOption({ chartType: 'quadrant', table: null, x: 'x', y: 'y' })
  assert.ok(!opt.series || opt.series.length === 0, 'null table must produce no series')
})

// ---------------------------------------------------------------------------
// SPARKLINE (chart type — Arrow table variant)
// ---------------------------------------------------------------------------

test('sparkline: produces a single line series with areaStyle', () => {
  const table = makeSparkTable()
  const opt = buildChartOption({ chartType: 'sparkline', table, y: 'value' })

  assert.ok(Array.isArray(opt.series), 'must have series')
  assert.equal(opt.series.length, 1, 'sparkline must have one series')
  assert.equal(opt.series[0].type, 'line', 'default sparkline type must be line')
  assert.ok(opt.series[0].areaStyle, 'line sparkline must have areaStyle')
})

test('sparkline: axes are hidden', () => {
  const table = makeSparkTable()
  const opt = buildChartOption({ chartType: 'sparkline', table, y: 'value' })

  assert.equal(opt.xAxis.show, false, 'x-axis must be hidden')
  assert.equal(opt.yAxis.show, false, 'y-axis must be hidden')
})

test('sparkline: tooltip is hidden', () => {
  const table = makeSparkTable()
  const opt = buildChartOption({ chartType: 'sparkline', table, y: 'value' })

  assert.equal(opt.tooltip.show, false, 'tooltip must be hidden for sparklines')
})

test('sparkline: props.sparkType bar → bar series', () => {
  const table = makeSparkTable()
  const opt = buildChartOption({ chartType: 'sparkline', table, y: 'value', props: { sparkType: 'bar' } })

  assert.equal(opt.series[0].type, 'bar', 'sparkType:bar must produce a bar series')
})

test('sparkline: empty table → safe empty option', () => {
  const opt = buildChartOption({ chartType: 'sparkline', table: null, y: 'value' })
  assert.ok(!opt.series || opt.series.length === 0, 'null table must produce no series')
})

// ---------------------------------------------------------------------------
// buildSparklineOption — plain number array variant (KPI embed)
// ---------------------------------------------------------------------------

test('buildSparklineOption: produces a line series from a number array', () => {
  const opt = buildSparklineOption([10, 20, 15, 30])
  assert.ok(Array.isArray(opt.series), 'must have series')
  assert.equal(opt.series[0].type, 'line', 'must be a line series')
})

test('buildSparklineOption: axes hidden, tooltip hidden', () => {
  const opt = buildSparklineOption([10, 20, 15])
  assert.equal(opt.xAxis.show, false, 'x-axis hidden')
  assert.equal(opt.yAxis.show, false, 'y-axis hidden')
  assert.equal(opt.tooltip.show, false, 'tooltip hidden')
})

test('buildSparklineOption: empty array → empty option', () => {
  const opt = buildSparklineOption([])
  // Returns {} for empty input.
  assert.deepEqual(opt, {}, 'empty array must return empty option object')
})

test('buildSparklineOption: null → empty option', () => {
  const opt = buildSparklineOption(null)
  assert.deepEqual(opt, {}, 'null must return empty option object')
})

test('buildSparklineOption: props.sparkType bar → bar series', () => {
  const opt = buildSparklineOption([5, 10, 8], { sparkType: 'bar' })
  assert.equal(opt.series[0].type, 'bar', 'sparkType:bar produces a bar series')
})

test('buildSparklineOption: props.sparkColor applied to line', () => {
  const opt = buildSparklineOption([1, 2, 3], { sparkColor: '#ff0000' })
  assert.equal(opt.series[0].lineStyle.color, '#ff0000', 'custom sparkColor must be applied')
})

test('buildSparklineOption: last data point has a dot symbol by default', () => {
  const values = [1, 2, 3, 4]
  const opt = buildSparklineOption(values)
  const last = opt.series[0].data[values.length - 1]
  assert.ok(last && typeof last === 'object', 'last point must be a data object (not a scalar)')
  assert.equal(last.symbol, 'circle', 'last point must have symbol:circle')
})

test('buildSparklineOption: props.showDot=false → no dot on last point', () => {
  const values = [1, 2, 3]
  const opt = buildSparklineOption(values, { showDot: false })
  const last = opt.series[0].data[values.length - 1]
  // With showDot false all points are objects with symbol:'none'
  if (typeof last === 'object' && last !== null) {
    assert.notEqual(last.symbol, 'circle', 'showDot:false must suppress the circle symbol')
  }
})
