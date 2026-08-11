/**
 * chart-options.test.js — Unit tests for the pure ECharts option builder.
 *
 * These tests exercise buildChartOption() directly (no DOM, no echarts, no
 * Arrow) using plain row arrays via the tableView adapter.
 *
 * Run with:  npm run test:embed
 */

import { describe, test, expect } from 'vitest'
import {
  buildChartOption, tableView, makeFormatter, deepMerge,
  SUPPORTED_TYPES, DEFAULT_PALETTE,
} from '../widgets/chart-options.js'

// Generic categorical sample (x = category, y = value, color = group).
const CAT_ROWS = [
  { month: 'Jan', sales: 120, region: 'North' },
  { month: 'Feb', sales: 200, region: 'North' },
  { month: 'Mar', sales: 150, region: 'South' },
  { month: 'Apr', sales: 80, region: 'South' },
]

const XY_ROWS = [
  { x: 1, y: 2, size: 5, grp: 'a' },
  { x: 2, y: 4, size: 12, grp: 'b' },
  { x: 3, y: 1, size: 8, grp: 'a' },
  { x: 4, y: 5, size: 20, grp: 'b' },
]

function firstSeriesType(opt) {
  return Array.isArray(opt.series) ? opt.series[0].type : undefined
}

// ---------------------------------------------------------------------------
// Every chart type renders a valid option
// ---------------------------------------------------------------------------

describe('buildChartOption — every chart type returns a valid option', () => {
  const cases = {
    bar: { type: 'bar', rows: CAT_ROWS, enc: { x: 'month', y: 'sales' }, series: 'bar' },
    line: { type: 'line', rows: CAT_ROWS, enc: { x: 'month', y: 'sales' }, series: 'line' },
    area: { type: 'area', rows: CAT_ROWS, enc: { x: 'month', y: 'sales' }, series: 'line' },
    scatter: { type: 'scatter', rows: XY_ROWS, enc: { x: 'x', y: 'y' }, series: 'scatter' },
    bubble: { type: 'bubble', rows: XY_ROWS, enc: { x: 'x', y: 'y', size: 'size' }, series: 'scatter' },
    pie: { type: 'pie', rows: CAT_ROWS, enc: { x: 'month', y: 'sales' }, series: 'pie' },
    donut: { type: 'donut', rows: CAT_ROWS, enc: { x: 'month', y: 'sales' }, series: 'pie' },
    funnel: { type: 'funnel', rows: CAT_ROWS, enc: { x: 'month', y: 'sales' }, series: 'funnel' },
    radar: { type: 'radar', rows: CAT_ROWS, enc: { x: 'month', y: 'sales', color: 'region' }, series: 'radar' },
    treemap: { type: 'treemap', rows: CAT_ROWS, enc: { x: 'month', y: 'sales' }, series: 'treemap' },
    boxplot: { type: 'boxplot', rows: CAT_ROWS, enc: { x: 'region', y: 'sales' }, series: 'boxplot' },
    gauge: { type: 'gauge', rows: [{ v: 72 }], enc: { y: 'v' }, series: 'gauge' },
    waterfall: { type: 'waterfall', rows: [{ s: 'a', d: 5 }, { s: 'b', d: -2 }, { s: 'c', d: 3 }], enc: { x: 's', y: 'd' }, series: 'bar' },
    combo: { type: 'combo', rows: CAT_ROWS, enc: { x: 'month', bars: ['sales'] }, series: 'bar' },
  }

  for (const [name, c] of Object.entries(cases)) {
    test(`${name} → series[0].type is "${c.series}"`, () => {
      const opt = buildChartOption({ type: c.type, table: c.rows, encoding: c.enc })
      expect(opt).toBeTruthy()
      expect(firstSeriesType(opt)).toBe(c.series)
    })
  }

  test('sankey builds nodes + links', () => {
    const rows = [
      { src: 'A', tgt: 'B', val: 5 },
      { src: 'A', tgt: 'C', val: 3 },
      { src: 'B', tgt: 'C', val: 2 },
    ]
    const opt = buildChartOption({ type: 'sankey', table: rows, encoding: { source: 'src', target: 'tgt', value: 'val' } })
    expect(opt.series[0].type).toBe('sankey')
    expect(opt.series[0].data.map((d) => d.name).sort()).toEqual(['A', 'B', 'C'])
    expect(opt.series[0].links).toHaveLength(3)
  })

  test('heatmap builds visualMap + matrix data', () => {
    const rows = [
      { day: 'Mon', hour: '9', n: 3 },
      { day: 'Mon', hour: '10', n: 7 },
      { day: 'Tue', hour: '9', n: 1 },
    ]
    const opt = buildChartOption({ type: 'heatmap', table: rows, encoding: { x: 'day', color: 'hour', value: 'n' } })
    expect(opt.series[0].type).toBe('heatmap')
    expect(opt.visualMap).toBeTruthy()
    expect(opt.series[0].data).toHaveLength(3)
  })

  test('candlestick maps OHLC into [open, close, low, high]', () => {
    const rows = [{ d: 'd1', o: 10, c: 12, l: 9, h: 13 }]
    const opt = buildChartOption({
      type: 'candlestick', table: rows,
      encoding: { x: 'd', open: 'o', close: 'c', low: 'l', high: 'h' },
    })
    expect(opt.series[0].type).toBe('candlestick')
    expect(opt.series[0].data[0]).toEqual([10, 12, 9, 13])
  })

  test('fan chart adds a confidence band + forecast line when lower/upper given', () => {
    const rows = [
      { t: 1, mid: 10, lo: 8, hi: 12 },
      { t: 2, mid: 12, lo: 9, hi: 15 },
    ]
    const opt = buildChartOption({
      type: 'fan', table: rows,
      encoding: { x: 't', y: 'mid', lower: 'lo', upper: 'hi' },
    })
    // band-lower (transparent), confidence (filled), forecast line = 3 series
    expect(opt.series).toHaveLength(3)
    const names = opt.series.map((s) => s.name)
    expect(names).toContain('confidence')
    expect(names).toContain('mid')
  })

  test('SUPPORTED_TYPES lists all 19 families', () => {
    expect(SUPPORTED_TYPES).toHaveLength(19)
    expect(SUPPORTED_TYPES).toContain('fan')
    expect(SUPPORTED_TYPES).toContain('sankey')
    expect(SUPPORTED_TYPES).toContain('combo')
  })

  test('combo mixes bar + line series on dual y-axes', () => {
    const rows = [
      { branch: 'North', planned: 100, completed: 80, strike: 45.2 },
      { branch: 'South', planned: 120, completed: 90, strike: 52.1 },
    ]
    const opt = buildChartOption({
      type: 'combo', table: rows,
      encoding: { x: 'branch', bars: ['planned', 'completed'], lines: ['strike'] },
    })
    const types = opt.series.map((s) => s.type)
    expect(types).toEqual(['bar', 'bar', 'line'])
    expect(opt.series[2].yAxisIndex).toBe(1)
    expect(opt.yAxis).toHaveLength(2)
  })

  test('donut centerLabel renders a centered graphic text', () => {
    const rows = [{ seg: 'Planned', v: 60 }, { seg: 'OOC', v: 40 }]
    const opt = buildChartOption({
      type: 'donut', table: rows, encoding: { x: 'seg', y: 'v' },
      config: { centerLabel: { text: '97.4%' } },
    })
    expect(opt.graphic).toBeTruthy()
    expect(opt.graphic[0].style.text).toBe('97.4%')
  })

  test('donut centerLabel defaults to the FIRST segment share, not the largest', () => {
    // Regression: a value/remainder ring (e.g. "Strike Rate": 7.75 / "Remainder": 92.25)
    // must show the meaningful first slice, not whichever slice is numerically bigger.
    const rows = [{ seg: 'Strike Rate', v: 7.75 }, { seg: 'Remainder', v: 92.25 }]
    const opt = buildChartOption({
      type: 'donut', table: rows, encoding: { x: 'seg', y: 'v' },
      config: { centerLabel: true },
    })
    expect(opt.graphic[0].style.text).toBe('7.8%')
  })
})

// ---------------------------------------------------------------------------
// Empty data → graceful "No data" option
// ---------------------------------------------------------------------------

describe('buildChartOption — empty / missing data', () => {
  test('empty table yields a No-data graphic', () => {
    const opt = buildChartOption({ type: 'bar', table: [] })
    expect(opt.graphic[0].style.text).toBe('No data')
  })

  test('missing y column for sankey yields No-data', () => {
    const opt = buildChartOption({ type: 'sankey', table: [{ a: 1 }], encoding: { source: 'a' } })
    expect(opt.graphic).toBeTruthy()
  })
})

// ---------------------------------------------------------------------------
// Grouping / stacking
// ---------------------------------------------------------------------------

describe('buildChartOption — grouping & stacking', () => {
  test('color column splits bar into one series per category', () => {
    const opt = buildChartOption({ type: 'bar', table: CAT_ROWS, encoding: { x: 'month', y: 'sales', color: 'region' } })
    expect(opt.series.length).toBe(2) // North + South
  })

  test('stack:normal sets a shared stack key', () => {
    const opt = buildChartOption({ type: 'bar', table: CAT_ROWS, encoding: { x: 'month', y: 'sales', color: 'region' }, config: { stack: 'normal' } })
    expect(opt.series.every((s) => s.stack === 'total')).toBe(true)
  })

  test('stack:percent normalizes each category to 100', () => {
    const rows = [
      { m: 'Jan', v: 30, g: 'A' }, { m: 'Jan', v: 70, g: 'B' },
    ]
    const opt = buildChartOption({ type: 'bar', table: rows, encoding: { x: 'm', y: 'v', color: 'g' }, config: { stack: 'percent' } })
    const total = opt.series.reduce((acc, s) => acc + s.data[0], 0)
    expect(Math.round(total)).toBe(100)
  })

  test('horizontal orientation swaps category to the y-axis', () => {
    const opt = buildChartOption({ type: 'bar', table: CAT_ROWS, encoding: { x: 'month', y: 'sales' }, config: { orientation: 'horizontal' } })
    expect(opt.yAxis.type).toBe('category')
    expect(opt.xAxis.type).toBe('value')
  })
})

// ---------------------------------------------------------------------------
// Theme tokens
// ---------------------------------------------------------------------------

describe('buildChartOption — theme tokens applied', () => {
  test('uses theme palette for color array', () => {
    const palette = ['#111111', '#222222', '#333333']
    const opt = buildChartOption({ type: 'bar', table: CAT_ROWS, encoding: { x: 'month', y: 'sales' }, theme: { palette } })
    expect(opt.color).toEqual(palette)
    expect(opt.series[0].itemStyle.color).toBe('#111111')
  })

  test('default palette used when no theme supplied', () => {
    const opt = buildChartOption({ type: 'bar', table: CAT_ROWS, encoding: { x: 'month', y: 'sales' } })
    expect(opt.color).toEqual(DEFAULT_PALETTE)
  })

  test('axis label colors come from theme.fgMuted', () => {
    const opt = buildChartOption({ type: 'line', table: CAT_ROWS, encoding: { x: 'month', y: 'sales' }, theme: { fgMuted: '#abcdef' } })
    expect(opt.xAxis.axisLabel.color).toBe('#abcdef')
  })
})

// ---------------------------------------------------------------------------
// Tooltip contrast (light/dark) — regression coverage for the invisible
// tooltip bug: tooltip bg/fg must always contrast, in both app modes.
// ---------------------------------------------------------------------------

describe('buildChartOption — tooltip contrast', () => {
  test('explicit light theme gets a light tooltip bg + dark tooltip text', () => {
    const opt = buildChartOption({
      type: 'line',
      table: CAT_ROWS,
      encoding: { x: 'month', y: 'sales' },
      theme: { fg: '#1e293b', fgMuted: '#64748b', tooltipBg: 'rgba(255,255,255,0.97)', tooltipFg: '#0f172a' },
    })
    expect(opt.tooltip.backgroundColor).toBe('rgba(255,255,255,0.97)')
    expect(opt.tooltip.textStyle.color).toBe('#0f172a')
  })

  test('explicit dark theme gets a dark tooltip bg + light tooltip text', () => {
    const opt = buildChartOption({
      type: 'line',
      table: CAT_ROWS,
      encoding: { x: 'month', y: 'sales' },
      theme: { fg: '#e2e8f0', fgMuted: '#94a3b8', tooltipBg: 'rgba(15,17,23,0.96)', tooltipFg: '#f8fafc' },
    })
    expect(opt.tooltip.backgroundColor).toBe('rgba(15,17,23,0.96)')
    expect(opt.tooltip.textStyle.color).toBe('#f8fafc')
  })

  test('legacy theme with only a dark `fg` (light mode) still infers a readable tooltip', () => {
    // No tooltipBg/tooltipFg supplied — this is the exact shape that used to
    // produce dark-on-dark (hardcoded dark bg + theme.fg used as text color).
    const opt = buildChartOption({
      type: 'line',
      table: CAT_ROWS,
      encoding: { x: 'month', y: 'sales' },
      theme: { fg: '#1e293b', fgMuted: '#64748b' },
    })
    expect(opt.tooltip.backgroundColor).not.toBe('rgba(15,17,23,0.95)')
    expect(opt.tooltip.textStyle.color).not.toBe('#1e293b')
  })

  test('legacy theme with only a light `fg` (dark mode) still infers a readable tooltip', () => {
    const opt = buildChartOption({
      type: 'line',
      table: CAT_ROWS,
      encoding: { x: 'month', y: 'sales' },
      theme: { fg: '#e2e8f0', fgMuted: '#94a3b8' },
    })
    expect(opt.tooltip.backgroundColor).not.toBe(opt.tooltip.textStyle.color)
  })

  test('config.tooltip object still deep-merges over the resolved bg/fg', () => {
    const opt = buildChartOption({
      type: 'line',
      table: CAT_ROWS,
      encoding: { x: 'month', y: 'sales' },
      theme: { tooltipBg: 'rgba(255,255,255,0.97)', tooltipFg: '#0f172a' },
      config: { tooltip: { backgroundColor: '#ff0000' } },
    })
    expect(opt.tooltip.backgroundColor).toBe('#ff0000')
  })
})

// ---------------------------------------------------------------------------
// Config overrides
// ---------------------------------------------------------------------------

describe('buildChartOption — config overrides', () => {
  test('title + subtitle render into the title block', () => {
    const opt = buildChartOption({ type: 'bar', table: CAT_ROWS, encoding: { x: 'month', y: 'sales' }, config: { title: 'Sales', subtitle: 'by month' } })
    expect(opt.title.text).toBe('Sales')
    expect(opt.title.subtext).toBe('by month')
  })

  test('legend:false removes the legend even with a color split', () => {
    const opt = buildChartOption({ type: 'bar', table: CAT_ROWS, encoding: { x: 'month', y: 'sales', color: 'region' }, config: { legend: false } })
    expect(opt.legend).toBeUndefined()
  })

  test('legend position bottom moves the legend', () => {
    const opt = buildChartOption({ type: 'line', table: CAT_ROWS, encoding: { x: 'month', y: 'sales', color: 'region' }, config: { legend: { position: 'bottom' } } })
    expect(opt.legend.bottom).toBeDefined()
  })

  test('animation:false disables animation', () => {
    const opt = buildChartOption({ type: 'bar', table: CAT_ROWS, encoding: { x: 'month', y: 'sales' }, config: { animation: false } })
    expect(opt.animation).toBe(false)
  })

  test('custom palette via config wins over theme', () => {
    const opt = buildChartOption({ type: 'bar', table: CAT_ROWS, encoding: { x: 'month', y: 'sales' }, theme: { palette: ['#000'] }, config: { palette: ['#abc', '#def'] } })
    expect(opt.color).toEqual(['#abc', '#def'])
  })

  test('raw echarts passthrough deep-merges and wins', () => {
    const opt = buildChartOption({ type: 'bar', table: CAT_ROWS, encoding: { x: 'month', y: 'sales' }, config: { echarts: { backgroundColor: '#ff0000' } } })
    expect(opt.backgroundColor).toBe('#ff0000')
  })

  test('uninterpreted top-level config keys deep-merge as echarts passthrough', () => {
    const opt = buildChartOption({ type: 'bar', table: CAT_ROWS, encoding: { x: 'month', y: 'sales' }, config: { toolbox: { show: true } } })
    expect(opt.toolbox).toEqual({ show: true })
  })

  test('axis label + log scale + min/max', () => {
    const opt = buildChartOption({
      type: 'line', table: CAT_ROWS, encoding: { x: 'month', y: 'sales' },
      config: { yAxis: { label: 'Revenue', log: true, min: 1, max: 1000 } },
    })
    expect(opt.yAxis.name).toBe('Revenue')
    expect(opt.yAxis.type).toBe('log')
    expect(opt.yAxis.min).toBe(1)
    expect(opt.yAxis.max).toBe(1000)
  })
})

// ---------------------------------------------------------------------------
// Dual axis
// ---------------------------------------------------------------------------

describe('buildChartOption — dual / secondary y-axis', () => {
  test('y2 encoding produces two y-axes and a 2nd series on axis index 1', () => {
    const rows = [
      { m: 'Jan', rev: 100, margin: 0.2 },
      { m: 'Feb', rev: 140, margin: 0.3 },
    ]
    const opt = buildChartOption({ type: 'line', table: rows, encoding: { x: 'm', y: 'rev', y2: 'margin' } })
    expect(Array.isArray(opt.yAxis)).toBe(true)
    expect(opt.yAxis).toHaveLength(2)
    const second = opt.series.find((s) => s.yAxisIndex === 1)
    expect(second).toBeTruthy()
    expect(second.name).toBe('margin')
  })
})

// ---------------------------------------------------------------------------
// Reference lines / annotations / data labels
// ---------------------------------------------------------------------------

describe('buildChartOption — reference lines & annotations', () => {
  test('horizontal target reference line attaches a markLine on series[0]', () => {
    const opt = buildChartOption({
      type: 'bar', table: CAT_ROWS, encoding: { x: 'month', y: 'sales' },
      config: { referenceLines: [{ value: 150, label: 'Target' }] },
    })
    expect(opt.series[0].markLine).toBeTruthy()
    expect(opt.series[0].markLine.data[0].yAxis).toBe(150)
    expect(opt.series[0].markLine.data[0].label.formatter).toBe('Target')
  })

  test('x-axis reference line uses xAxis coordinate', () => {
    const opt = buildChartOption({
      type: 'scatter', table: XY_ROWS, encoding: { x: 'x', y: 'y' },
      config: { referenceLines: [{ value: 2, axis: 'x', label: 'cutoff' }] },
    })
    expect(opt.series[0].markLine.data[0].xAxis).toBe(2)
  })

  test('annotations attach a markPoint on series[0]', () => {
    const opt = buildChartOption({
      type: 'scatter', table: XY_ROWS, encoding: { x: 'x', y: 'y' },
      config: { annotations: [{ x: 2, y: 4, label: 'peak' }] },
    })
    expect(opt.series[0].markPoint).toBeTruthy()
    expect(opt.series[0].markPoint.data[0].value).toBe('peak')
  })

  test('dataLabels:true turns on series labels', () => {
    const opt = buildChartOption({
      type: 'bar', table: CAT_ROWS, encoding: { x: 'month', y: 'sales' },
      config: { dataLabels: true },
    })
    expect(opt.series[0].label.show).toBe(true)
  })
})

// ---------------------------------------------------------------------------
// Formatters
// ---------------------------------------------------------------------------

describe('makeFormatter — value formatting', () => {
  test('currency', () => {
    expect(makeFormatter({ kind: 'currency', currency: 'USD', locale: 'en-US' })(1234)).toBe('$1,234')
  })
  test('percent', () => {
    expect(makeFormatter({ kind: 'percent', locale: 'en-US', decimals: 0 })(0.5)).toBe('50%')
  })
  test('si', () => {
    expect(makeFormatter('si')(2_500_000)).toBe('2.5M')
    expect(makeFormatter('si')(1500)).toBe('1.5k')
  })
  test('number default', () => {
    expect(makeFormatter('number', 'en-US')(1234.5)).toBe('1,234.5')
  })
  test('date', () => {
    const out = makeFormatter({ kind: 'date', locale: 'en-US' })('2024-01-15T00:00:00Z')
    expect(out).toMatch(/Jan|2024/)
  })
})

// ---------------------------------------------------------------------------
// tableView adapter + deepMerge
// ---------------------------------------------------------------------------

describe('tableView adapter', () => {
  test('rows[] → columns + col()', () => {
    const v = tableView(CAT_ROWS)
    expect(v.numRows).toBe(4)
    expect(v.columns).toContain('month')
    expect(v.col('sales')).toEqual([120, 200, 150, 80])
    expect(v.isNumeric('sales')).toBe(true)
    expect(v.isNumeric('month')).toBe(false)
  })

  test('columnar object form', () => {
    const v = tableView({ columns: { a: [1, 2], b: ['x', 'y'] } })
    expect(v.numRows).toBe(2)
    expect(v.col('a')).toEqual([1, 2])
  })

  test('passthrough existing tableView', () => {
    const v = tableView(CAT_ROWS)
    expect(tableView(v)).toBe(v)
  })
})

describe('deepMerge', () => {
  test('nested objects merge, source wins on conflict', () => {
    const out = deepMerge({ a: { x: 1, y: 2 }, b: 3 }, { a: { y: 9, z: 4 } })
    expect(out).toEqual({ a: { x: 1, y: 9, z: 4 }, b: 3 })
  })
  test('arrays are replaced wholesale', () => {
    expect(deepMerge({ a: [1, 2] }, { a: [9] })).toEqual({ a: [9] })
  })
})
