/**
 * chartOptionExtra.test.mjs — Additional unit tests for buildChartOption() covering
 * paths not exercised by chartOption.test.mjs.
 *
 * New coverage:
 *   PIE:    chartType:'pie' → produces a single pie series with the right data shape
 *   PIE:    pie ignores props.stack (no stack key on pie series)
 *   AREA:   chartType:'area' single series → type:'line' + areaStyle defined
 *   AREA:   area single series with no stack → areaStyle exists but no stack
 *   COLOR:  color column (categorical) → multiple series, one per category (bar path)
 *   COLOR:  color column (categorical) → multiple series, one per category (line path)
 *   SAFE:   null table sentinel → empty option with graphic placeholder
 *   SAFE:   unknown chartType falls through to scatter (no crash)
 *
 * Run with:
 *   node --test src/dashboards/chartOptionExtra.test.mjs
 *   # or via npm run test:dash (when glob covers src/**\/*.test.mjs)
 */

import { test } from 'node:test'
import assert from 'node:assert/strict'
import { makeTable, vectorFromArray, Float64, Utf8 } from 'apache-arrow'
import { buildChartOption } from '../../src/viz/chartOption.js'

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function makeMonthTable() {
  return makeTable({
    month:   vectorFromArray(['Jan', 'Feb', 'Mar', 'Apr'], new Utf8()),
    revenue: vectorFromArray([100, 200, 150, 300], new Float64()),
    profit:  vectorFromArray([10, 30, 20, 60], new Float64()),
  })
}

function makeRegionTable() {
  // Has a categorical 'region' column for color grouping tests.
  return makeTable({
    month:   vectorFromArray(['Jan', 'Jan', 'Feb', 'Feb'], new Utf8()),
    revenue: vectorFromArray([100, 200, 150, 300], new Float64()),
    region:  vectorFromArray(['EMEA', 'APAC', 'EMEA', 'APAC'], new Utf8()),
  })
}

// ---------------------------------------------------------------------------
// PIE chart
// ---------------------------------------------------------------------------

test('pie: produces a single series of type "pie"', () => {
  const table = makeMonthTable()
  const opt = buildChartOption({ chartType: 'pie', table, x: 'month', y: 'revenue' })

  assert.ok(Array.isArray(opt.series), 'series must be an array')
  assert.equal(opt.series.length, 1, 'pie must produce exactly one series')
  assert.equal(opt.series[0].type, 'pie', 'series type must be "pie"')
})

test('pie: data items have name and value fields', () => {
  const table = makeMonthTable()
  const opt = buildChartOption({ chartType: 'pie', table, x: 'month', y: 'revenue' })

  const data = opt.series[0].data
  assert.ok(Array.isArray(data), 'pie series.data must be an array')
  assert.ok(data.length > 0, 'pie data must be non-empty for a populated table')

  for (const item of data) {
    assert.ok('name' in item, `pie data item must have "name": ${JSON.stringify(item)}`)
    assert.ok('value' in item, `pie data item must have "value": ${JSON.stringify(item)}`)
  }
})

test('pie: each data item name matches the x column values', () => {
  const table = makeMonthTable()
  const opt = buildChartOption({ chartType: 'pie', table, x: 'month', y: 'revenue' })

  const names = opt.series[0].data.map((d) => d.name)
  assert.deepEqual(names, ['Jan', 'Feb', 'Mar', 'Apr'],
    'pie data names must equal the x column values in order')
})

test('pie: data values are numeric and match the y column', () => {
  const table = makeMonthTable()
  const opt = buildChartOption({ chartType: 'pie', table, x: 'month', y: 'revenue' })

  const values = opt.series[0].data.map((d) => d.value)
  assert.deepEqual(values, [100, 200, 150, 300],
    'pie data values must match the y column in order')
})

test('pie: props.stack is ignored (pie series has no stack key)', () => {
  const table = makeMonthTable()
  const opt = buildChartOption({
    chartType: 'pie',
    table,
    x: 'month',
    y: 'revenue',
    props: { stack: true },
  })

  for (const s of opt.series) {
    assert.equal(s.stack, undefined,
      'pie series must not receive a stack id (stacking does not apply to pie)')
  }
})

test('pie: has a tooltip with trigger:item', () => {
  const table = makeMonthTable()
  const opt = buildChartOption({ chartType: 'pie', table, x: 'month', y: 'revenue' })

  assert.ok(opt.tooltip, 'pie option must have a tooltip')
  assert.equal(opt.tooltip.trigger, 'item', 'pie tooltip trigger must be "item"')
})

// ---------------------------------------------------------------------------
// AREA chart (single series)
// ---------------------------------------------------------------------------

test('area: single series has ECharts type "line"', () => {
  const table = makeMonthTable()
  const opt = buildChartOption({ chartType: 'area', table, x: 'month', y: 'revenue' })

  assert.ok(Array.isArray(opt.series), 'series must be an array')
  assert.equal(opt.series.length, 1, 'single-series area chart must have one series')
  assert.equal(opt.series[0].type, 'line',
    'ECharts renders area charts as type "line" with areaStyle')
})

test('area: single series has areaStyle defined', () => {
  const table = makeMonthTable()
  const opt = buildChartOption({ chartType: 'area', table, x: 'month', y: 'revenue' })

  const series = opt.series[0]
  assert.ok(series.areaStyle !== undefined && series.areaStyle !== null,
    'area chart series must have an areaStyle property')
})

test('area: no stack on simple area without props.stack', () => {
  const table = makeMonthTable()
  const opt = buildChartOption({ chartType: 'area', table, x: 'month', y: 'revenue' })

  assert.equal(opt.series[0].stack, undefined,
    'simple area series must not have a stack id when props.stack is not set')
})

test('area: props.stack=true → area series gets a stack id', () => {
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

  // Each series should be type:'line' with areaStyle (area→line) and a stack id
  assert.equal(opt.series.length, 2, 'two area series expected')
  for (const s of opt.series) {
    assert.equal(s.type, 'line', 'area series must map to ECharts type "line"')
    assert.ok(s.stack, `area series must have a stack id when props.stack=true; got ${s.stack}`)
  }
})

// ---------------------------------------------------------------------------
// COLOR column — categorical grouping
// ---------------------------------------------------------------------------

test('bar with categorical color column → one series per category', () => {
  const table = makeRegionTable()
  const opt = buildChartOption({
    chartType: 'bar',
    table,
    x: 'month',
    y: 'revenue',
    color: 'region',
  })

  assert.ok(Array.isArray(opt.series), 'series must be an array')
  assert.ok(opt.series.length > 1,
    'categorical color column must produce multiple series (one per category)')

  const seriesNames = opt.series.map((s) => s.name)
  assert.ok(seriesNames.includes('EMEA'), 'must have a series named "EMEA"')
  assert.ok(seriesNames.includes('APAC'), 'must have a series named "APAC"')
})

test('bar with categorical color column → legend is shown', () => {
  const table = makeRegionTable()
  const opt = buildChartOption({
    chartType: 'bar',
    table,
    x: 'month',
    y: 'revenue',
    color: 'region',
  })

  // baseOption is called with showLegend:true when colorCol is set
  assert.ok(opt.legend, 'legend must be present when color column is set')
})

test('line with categorical color column → multiple line series', () => {
  const table = makeRegionTable()
  const opt = buildChartOption({
    chartType: 'line',
    table,
    x: 'month',
    y: 'revenue',
    color: 'region',
  })

  assert.ok(Array.isArray(opt.series), 'series must be an array')
  assert.ok(opt.series.length > 1, 'color column must produce multiple line series')
  for (const s of opt.series) {
    assert.equal(s.type, 'line', `all series must be type "line", got "${s.type}"`)
  }
})

// ---------------------------------------------------------------------------
// Safe / degenerate edge cases
// ---------------------------------------------------------------------------

test('null table → returns safe empty option with graphic placeholder', () => {
  const opt = buildChartOption({ chartType: 'bar', table: null, x: 'month', y: 'revenue' })

  assert.ok(!opt.series || opt.series.length === 0,
    'null table must produce no series')
  assert.ok(Array.isArray(opt.graphic) && opt.graphic.length > 0,
    'null table must produce a graphic placeholder')
})

test('unknown chartType falls back without throwing', () => {
  const table = makeMonthTable()
  // 'heatmap' is not a handled case → should fall through to scatter without crashing
  assert.doesNotThrow(() => {
    buildChartOption({ chartType: 'heatmap', table, x: 'month', y: 'revenue' })
  }, 'unknown chartType must not throw')
})

test('missing y column name → empty option (getColumn returns null)', () => {
  const table = makeMonthTable()
  // Pass a y column name that does not exist in the table
  const opt = buildChartOption({ chartType: 'bar', table, x: 'month', y: 'nonexistent_col' })

  // buildBarWithStack returns {} when yRaw is null — the chart is empty but safe
  assert.ok(!opt.series || opt.series.length === 0,
    'missing y column must produce no series (safe empty option)')
})

// ---------------------------------------------------------------------------
// New chart types: donut / hbar / heatmap / gauge
// ---------------------------------------------------------------------------

function makeHeatTable() {
  return makeTable({
    day:   vectorFromArray(['Mon', 'Mon', 'Tue', 'Tue'], new Utf8()),
    hour:  vectorFromArray(['AM', 'PM', 'AM', 'PM'], new Utf8()),
    count: vectorFromArray([5, 9, 2, 7], new Float64()),
  })
}

test('donut: single pie series with an inner-radius hole', () => {
  const table = makeMonthTable()
  const opt = buildChartOption({ chartType: 'donut', table, x: 'month', y: 'revenue' })

  assert.equal(opt.series.length, 1, 'donut must produce one series')
  assert.equal(opt.series[0].type, 'pie', 'donut maps to a pie series')
  const [inner] = opt.series[0].radius
  // Inner radius must be a non-zero percentage (the hole)
  assert.equal(inner, '50%', 'donut inner radius default must be 50%')
})

test('donut: props.innerRadius/outerRadius override the radii', () => {
  const table = makeMonthTable()
  const opt = buildChartOption({
    chartType: 'donut', table, x: 'month', y: 'revenue',
    props: { innerRadius: '60%', outerRadius: '80%' },
  })
  assert.deepEqual(opt.series[0].radius, ['60%', '80%'])
})

test('hbar: value on x-axis (value type), category on y-axis (category type)', () => {
  const table = makeMonthTable()
  const opt = buildChartOption({ chartType: 'hbar', table, x: 'month', y: 'revenue' })

  assert.equal(opt.xAxis.type, 'value', 'hbar x-axis must be value')
  assert.equal(opt.yAxis.type, 'category', 'hbar y-axis must be category')
  assert.deepEqual(opt.yAxis.data, ['Jan', 'Feb', 'Mar', 'Apr'],
    'hbar y-axis categories must be the x column values')
  assert.equal(opt.series[0].type, 'bar', 'hbar series type must be bar')
  assert.deepEqual(opt.series[0].data, [100, 200, 150, 300],
    'hbar series data must be the y column values')
})

test('heatmap: builds a heatmap series + visualMap from encoding.value', () => {
  const table = makeHeatTable()
  const opt = buildChartOption({
    chartType: 'heatmap', table,
    x: 'day', encoding: { x: 'day', y: 'hour', value: 'count' },
  })

  assert.equal(opt.series[0].type, 'heatmap', 'series type must be heatmap')
  assert.ok(opt.visualMap, 'heatmap must have a visualMap')
  assert.deepEqual(opt.xAxis.data, ['Mon', 'Tue'], 'distinct x categories in order')
  assert.deepEqual(opt.yAxis.data, ['AM', 'PM'], 'distinct y categories in order')
  // 4 cells, each [xIdx, yIdx, value]
  assert.equal(opt.series[0].data.length, 4, 'one data point per row')
  assert.deepEqual(opt.series[0].data[0], [0, 0, 5], 'first cell: Mon/AM = 5')
})

test('heatmap: missing value column → safe empty option (no series)', () => {
  const table = makeHeatTable()
  const opt = buildChartOption({
    chartType: 'heatmap', table,
    encoding: { x: 'day', y: 'hour' }, // no value/color
  })
  assert.ok(!opt.series || opt.series.length === 0,
    'heatmap without a value column must produce no series')
})

test('gauge: single-value series from first row of encoding.value', () => {
  const table = makeMonthTable()
  const opt = buildChartOption({
    chartType: 'gauge', table, x: 'month',
    encoding: { value: 'revenue' },
  })

  assert.equal(opt.series[0].type, 'gauge', 'series type must be gauge')
  assert.equal(opt.series[0].data[0].value, 100, 'gauge reads first row of value column')
  // Auto max = value * 1.5 = 150
  assert.equal(opt.series[0].max, 150, 'auto gauge max is value * 1.5')
})

test('gauge: props.max overrides the auto range', () => {
  const table = makeMonthTable()
  const opt = buildChartOption({
    chartType: 'gauge', table,
    encoding: { value: 'revenue' }, props: { max: 500 },
  })
  assert.equal(opt.series[0].max, 500, 'props.max must override auto range')
})
