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
  SUPPORTED_TYPES, DEFAULT_PALETTE, gaugeScaleLabelFormatter,
  lerpGaugeArcColor, GAUGE_ARC_START, GAUGE_ARC_END, gaugeTargetValue,
  resolveGaugeArcCaps,
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
    // A polar area is a rose (roseType 'area'), so its series is still a pie;
    // a radial bar is bars on a polar grid, and its FIRST series is the track.
    polar: { type: 'polar', rows: CAT_ROWS, enc: { x: 'month', y: 'sales' }, series: 'pie' },
    radialbar: { type: 'radialbar', rows: CAT_ROWS, enc: { x: 'month', y: 'sales' }, series: 'bar' },
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

  test('heatmap takes its ramp from config.palette, and heatColors wins', () => {
    // A themed board sets config.palette on every chart. The heatmap used to
    // read theme.palette directly, so it was the one chart that stayed in the
    // stock blues while everything around it changed colour.
    const rows = [
      { day: 'Mon', hour: '9', n: 3 },
      { day: 'Mon', hour: '10', n: 7 },
    ]
    const enc = { x: 'day', color: 'hour', value: 'n' }

    const themed = buildChartOption({
      type: 'heatmap', table: rows, encoding: enc,
      config: { palette: ['#C8811A', '#1D5F5A', '#A8351D'] },
    })
    // Strongest value keeps taking palette[0], as every other builder does.
    expect(themed.visualMap.inRange.color.at(-1)).toBe('#C8811A')
    expect(themed.visualMap.inRange.color).not.toContain('#6366f1')

    const explicit = buildChartOption({
      type: 'heatmap', table: rows, encoding: enc,
      config: { palette: ['#C8811A'], heatColors: ['#241C15', '#A8351D', '#E0A044'] },
    })
    expect(explicit.visualMap.inRange.color).toEqual(['#241C15', '#A8351D', '#E0A044'])

    // heatColors is interpreted, not leaked into the raw ECharts passthrough.
    expect(explicit.heatColors).toBeUndefined()
  })

  test('heatmap heatColorBy colours cells by column and still has a visualMap', () => {
    // ECharts refuses a heatmap series with no visualMap ("Heatmap must use
    // with visualMap"), so the categorical mode must keep one — hidden and
    // piecewise over the x index — rather than dropping it.
    const rows = [
      { week: 'Week 1', day: 'Mon', n: 40 }, { week: 'Week 1', day: 'Tue', n: 44 },
      { week: 'Week 2', day: 'Mon', n: 371 }, { week: 'Week 2', day: 'Tue', n: 38 },
    ]
    const opt = buildChartOption({
      type: 'heatmap', table: rows, encoding: { x: 'day', y: 'week', value: 'n' },
      config: { heatColorBy: 'x', palette: ['#7EB3E3', '#4FBFCB'], yInverse: true, xLabelRotate: 40 },
    })
    expect(opt.visualMap.type).toBe('piecewise')
    expect(opt.visualMap.show).toBe(false)
    expect(opt.visualMap.dimension).toBe(0)
    expect(opt.visualMap.pieces).toEqual([{ value: 0, color: '#7EB3E3' }, { value: 1, color: '#4FBFCB' }])
    expect(opt.yAxis.inverse).toBe(true)
    expect(opt.xAxis.axisLabel.rotate).toBe(40)
    // Interpreted keys stay out of the raw ECharts passthrough.
    expect(opt.heatColorBy).toBeUndefined()
    expect(opt.yInverse).toBeUndefined()
  })

  test('an authored y-axis label gets the same clearance as a derived one', () => {
    // The widened nameGap used to apply only when the axis name came from the
    // column, so an authored label sat on top of tick labels like "3,500,000".
    const rows = Array.from({ length: 12 }, (_, i) => ({ m: `2025-${i + 1}`, v: 3_500_000 + i }))
    const authored = buildChartOption({
      type: 'line', table: rows, encoding: { x: 'm', y: 'v' },
      config: { yAxis: { label: 'Net sales (R)' } },
    })
    const derived = buildChartOption({ type: 'line', table: rows, encoding: { x: 'm', y: 'v' } })
    const yOf = (o: any) => Array.isArray(o.yAxis) ? o.yAxis[0] : o.yAxis
    expect(yOf(authored).name).toBe('Net sales (R)')
    expect(yOf(authored).nameGap).toBe(yOf(derived).nameGap)
    expect(yOf(authored).nameGap).toBeGreaterThan(44)
  })

  test('a choropleth emits no legend, so no stray dots land on the map', () => {
    // ECharts derives legend entries from a map series' own data — one per
    // region — and paints each entry's symbol at that region's centroid. With
    // the auto legend left on, every province wore a dot. The visualMap is the
    // scale a choropleth actually needs.
    const rows = [
      { province: 'Gauteng', net_sales: 18_605_395 },
      { province: 'Western Cape', net_sales: 9_868_123 },
      { province: 'Northern Cape', net_sales: 468_804 },
    ]
    const opt = buildChartOption({
      type: 'map', table: rows,
      encoding: { name: 'province', value: 'net_sales' },
      config: { map: 'south-africa', lowColor: '#EAF1F4', highColor: '#0E2A38' },
    })
    expect(opt.legend).toBeUndefined()
    expect(opt.visualMap).toBeTruthy()
    expect(opt.series[0].type).toBe('map')

    // The map-only config keys are interpreted, not leaked into the raw
    // ECharts passthrough as unknown top-level options.
    for (const k of ['map', 'lowColor', 'highColor', 'roam']) {
      expect(opt[k]).toBeUndefined()
    }
    expect(opt.series[0].map).toBe('south-africa')
    expect(opt.visualMap.inRange.color).toEqual(['#EAF1F4', '#0E2A38'])
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

  test('SUPPORTED_TYPES lists all 21 families', () => {
    expect(SUPPORTED_TYPES).toHaveLength(21)
    expect(SUPPORTED_TYPES).toContain('fan')
    expect(SUPPORTED_TYPES).toContain('sankey')
    expect(SUPPORTED_TYPES).toContain('combo')
    expect(SUPPORTED_TYPES).toContain('polar')
    expect(SUPPORTED_TYPES).toContain('radialbar')
  })

  test('radialbar puts a full-circle track ring behind the value ring', () => {
    const opt = buildChartOption({
      type: 'radialbar', table: CAT_ROWS, encoding: { x: 'month', y: 'sales' },
    })
    expect(opt.polar).toBeTruthy()
    expect(opt.series).toHaveLength(2)
    expect(opt.series[0].coordinateSystem).toBe('polar')
    expect(opt.angleAxis.max).toBe(100)
  })

  test("polar renders as an equal-angle rose, not a Nightingale", () => {
    const opt = buildChartOption({
      type: 'polar', table: CAT_ROWS, encoding: { x: 'month', y: 'sales' },
    })
    expect(opt.series[0].roseType).toBe('area')
  })

  test('combo lineAxis left puts the lines on the left axis and the bars on the right', () => {
    const rows = [{ m: 'Jan', count: 5, value: 900 }, { m: 'Feb', count: 7, value: 700 }]
    const opt = buildChartOption({
      type: 'combo', table: rows, encoding: { x: 'm', bars: ['count'], lines: ['value'] },
      config: { lineAxis: 'left', palette: ['#111', '#222'], lineColors: ['#333'] },
    })
    const [bar, line] = opt.series as any[]
    expect(bar.yAxisIndex).toBe(1)
    expect(line.yAxisIndex).toBe(0)
    expect(line.lineStyle.color).toBe('#333')
    expect((opt.yAxis as any[])[0].name).toBe('value')
    expect((opt.yAxis as any[])[1].name).toBe('count')
  })

  test('combo colorByPoint gives a single bar series one palette colour per category', () => {
    const rows = [{ m: 'Jan', count: 5 }, { m: 'Feb', count: 7 }, { m: 'Mar', count: 2 }]
    const opt = buildChartOption({
      type: 'combo', table: rows, encoding: { x: 'm', bars: ['count'] },
      config: { colorByPoint: true, palette: ['#111', '#222'] },
    })
    const data = (opt.series as any[])[0].data
    expect(data.map((d: any) => d.value)).toEqual([5, 7, 2])
    expect(data.map((d: any) => d.itemStyle.color)).toEqual(['#111', '#222', '#111'])
  })

  test('barMaxWidth null removes the default cap', () => {
    const rows = [{ m: 'Jan', count: 5 }]
    const capped = buildChartOption({ type: 'combo', table: rows, encoding: { x: 'm', bars: ['count'] } })
    const free = buildChartOption({ type: 'combo', table: rows, encoding: { x: 'm', bars: ['count'] }, config: { barMaxWidth: null } })
    expect((capped.series as any[])[0].barMaxWidth).toBe(24)
    expect((free.series as any[])[0].barMaxWidth).toBeUndefined()
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
    // A series name is read by a person, so it is humanised from the column the
    // same way a table header is — a legend saying `kitchen_seconds` is the
    // same defect as a column header saying it.
    expect(second.name).toBe('Margin')
  })

  test('series and axis names are humanised from their columns', () => {
    const rows = [
      { m: 'Jan', kitchen_seconds: 300, handover_seconds: 150 },
      { m: 'Feb', kitchen_seconds: 310, handover_seconds: 140 },
    ]
    const combo = buildChartOption({
      type: 'combo', table: rows,
      encoding: { x: 'm', bars: ['kitchen_seconds', 'handover_seconds'] },
    })
    expect(combo.series.map((s: any) => s.name)).toEqual(['Kitchen seconds', 'Handover seconds'])

    const line = buildChartOption({
      type: 'line', table: rows, encoding: { x: 'm', y: 'kitchen_seconds' },
    })
    const y = Array.isArray(line.yAxis) ? line.yAxis[0] : line.yAxis
    expect(y.name).toBe('Kitchen seconds')
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

describe('buildChartOption — canvas text', () => {
  const build = (config = {}) => buildChartOption({
    type: 'bar', table: CAT_ROWS, encoding: { x: 'month', y: 'sales' }, config,
  })

  test('never hands the canvas a CSS keyword as the font family', () => {
    const { fontFamily } = build().textStyle
    expect(fontFamily).toBeTruthy()
    expect(fontFamily).not.toBe('inherit')
  })

  test('uses the configured family when one is given', () => {
    expect(build({ fontFamily: 'Georgia' }).textStyle.fontFamily).toBe('Georgia')
  })
})

describe('buildChartOption — axis names clear their tick labels', () => {
  const rows = Array.from({ length: 20 }, (_, i) => ({ who: `customer-${i}`, big: 40000000 + i }))

  test('a value axis name moves out with larger tick labels', () => {
    const gap = (fontSize) => buildChartOption({
      type: 'bar', table: rows, encoding: { x: 'who', y: 'big' },
      config: { yAxis: { label: 'Amount', axisLabel: { fontSize } } },
    }).yAxis.nameGap
    expect(gap(14)).toBeGreaterThan(gap(10))
    expect(gap(14)).toBeGreaterThan(60)
  })

  test('a category axis name clears rotated labels', () => {
    const opt = buildChartOption({
      type: 'bar', table: rows, encoding: { x: 'who', y: 'big' },
      config: { xAxis: { label: 'Customer', axisLabel: { fontSize: 14 } } },
    })
    expect(opt.xAxis.axisLabel.rotate).toBe(30)
    expect(opt.xAxis.nameGap).toBeGreaterThan(50)
  })

  test('a combo axis name clears the widest of the series plotted on it', () => {
    const wide = [{ who: 'A', orders: 150, revenue: 52000 }, { who: 'B', orders: 90, revenue: 47000 }]
    const gap = (bars: string[]) => {
      const opt = buildChartOption({
        type: 'combo', table: wide, encoding: { x: 'who', bars },
        config: { yAxis: { label: 'Amount', axisLabel: { fontSize: 14 } } },
      }) as any
      return (Array.isArray(opt.yAxis) ? opt.yAxis[0] : opt.yAxis).nameGap
    }
    expect(gap(['orders', 'revenue'])).toBe(gap(['revenue', 'orders']))
    expect(gap(['orders', 'revenue'])).toBeGreaterThan(gap(['orders']))
  })

  test('combo axes size their names from their own column', () => {
    const opt = buildChartOption({
      type: 'combo', table: rows, encoding: { x: 'who', bars: ['big'] },
      config: { yAxis: { label: 'Amount', axisLabel: { fontSize: 14 } } },
    })
    const left = Array.isArray(opt.yAxis) ? opt.yAxis[0] : opt.yAxis
    expect(left.nameGap).toBeGreaterThan(60)
  })

  test('an authored category gap is kept', () => {
    const opt = buildChartOption({
      type: 'bar', table: rows, encoding: { x: 'who', y: 'big' },
      config: { xAxis: { label: 'Customer', nameGap: 12 } },
    })
    expect(opt.xAxis.nameGap).toBe(12)
  })
})

// ---------------------------------------------------------------------------
// Gauge arc colours (config.gaugeArc*)
// ---------------------------------------------------------------------------

describe('buildChartOption — gauge arc colours', () => {
  const gauge = (config = {}) =>
    buildChartOption({ type: 'gauge', table: [{ v: 72 }], encoding: { y: 'v' }, config }).series[0]

  test('unset → needle keeps the 3 theme bands, ring keeps palette[0]', () => {
    const needle = gauge()
    expect(needle.axisLine.lineStyle.color).toHaveLength(3)
    // Explicit palette[0] (what ECharts drew by default), so a merge after
    // "Reset to theme" can't keep a stale arc colour.
    expect(needle.progress.itemStyle.color).toBe(DEFAULT_PALETTE[0])
    expect(gauge({ palette: ['#123456'] }).progress.itemStyle.color).toBe('#123456')
    const ring = gauge({ gaugeStyle: 'ring', palette: ['#123456'] })
    expect(ring.progress.itemStyle.color).toBe('#123456')
  })

  test('solid arc colour → needle uses a neutral track + coloured fill', () => {
    const s = gauge({ gaugeArcColor: '#ff00aa' })
    expect(s.progress.itemStyle.color).toBe('#ff00aa')
    expect(s.axisLine.lineStyle.color).toEqual([[1, 'rgba(148,163,184,0.18)']])
  })

  test('solid arc colour overrides palette[0] on the ring', () => {
    const s = gauge({ gaugeStyle: 'ring', palette: ['#123456'], gaugeArcColor: '#ff00aa' })
    expect(s.progress.itemStyle.color).toBe('#ff00aa')
  })

  test('gradient → segmented track stops, transparent progress (needle and ring)', () => {
    for (const gaugeStyle of [undefined, 'ring']) {
      const s = gauge({ gaugeStyle, gaugeArcGradient: true, gaugeArcStart: '#111111', gaugeArcEnd: '#eeeeee', gaugeArcColor: '#ff00aa' })
      // No bounding-box gradient anywhere — it can't follow the arc.
      expect(s.progress.itemStyle.color).toBe('rgba(0,0,0,0)')
      const stops = s.axisLine.lineStyle.color
      expect(stops[0][1]).toMatch(/^#1[0-9a-f]1[0-9a-f]1[0-9a-f]$/)
      // gaugeArcColor is ignored while the gradient is on.
      expect(JSON.stringify(stops)).not.toContain('ff00aa')
    }
  })

  test('gradient without colours falls back to the default red→green', () => {
    const stops = buildChartOption({
      type: 'gauge', table: [{ v: 100 }], encoding: { y: 'v' }, config: { gaugeArcGradient: true },
    }).series[0].axisLine.lineStyle.color
    expect(stops[0][1]).toBe(lerpGaugeArcColor(GAUGE_ARC_START, GAUGE_ARC_END, 0.5 / 96))
    expect(stops.at(-1)[1]).toBe(lerpGaugeArcColor(GAUGE_ARC_START, GAUGE_ARC_END, 1 - 0.5 / 96))
  })

  test('arc keys are interpreted, not leaked into the raw ECharts passthrough', () => {
    const opt = buildChartOption({
      type: 'gauge', table: [{ v: 72 }], encoding: { y: 'v' },
      config: { gaugeArcGradient: true, gaugeArcStart: '#111111', gaugeArcEnd: '#eeeeee', gaugeArcColor: '#ff00aa' },
    })
    for (const k of ['gaugeArcGradient', 'gaugeArcStart', 'gaugeArcEnd', 'gaugeArcColor']) {
      expect(opt[k]).toBeUndefined()
    }
  })
})

// ---------------------------------------------------------------------------
// Gauge arc ends (config.gaugeArcCaps)
// ---------------------------------------------------------------------------

describe('gauge arc caps', () => {
  const series = (config = {}, extra = {}) =>
    buildChartOption({ type: 'gauge', table: [{ v: 72 }], encoding: { y: 'v' }, config, ...extra }).series
  const grad = { start: '#111111', end: '#eeeeee' }

  test('resolveGaugeArcCaps: explicit wins, Auto defaults', () => {
    expect(resolveGaugeArcCaps({ gaugeArcCaps: 'round' }, { grad, solid: null })).toBe('round')
    expect(resolveGaugeArcCaps({ gaugeArcCaps: 'flat', gaugeStyle: 'ring' }, { grad: null, solid: '#f0a' })).toBe('flat')
    expect(resolveGaugeArcCaps({ gaugeArcCaps: 'bogus' }, { grad, solid: null })).toBe('flat')
    expect(resolveGaugeArcCaps({}, { grad, solid: null })).toBe('flat')
    expect(resolveGaugeArcCaps({ gaugeStyle: 'ring' }, { grad: null, solid: '#f0a' })).toBe('round')
    expect(resolveGaugeArcCaps({}, { grad: null, solid: '#f0a' })).toBeNull()
    expect(resolveGaugeArcCaps({}, { grad: null, solid: null })).toBeNull()
    expect(resolveGaugeArcCaps({ gaugeStyle: 'ring' }, { grad: null, solid: null })).toBeNull()
  })

  test('Auto: stock needle square, stock ring round, no axis caps', () => {
    const [needle] = series()
    const [ring] = series({ gaugeStyle: 'ring' })
    expect(needle.progress.roundCap).toBe(false)
    expect(ring.progress.roundCap).toBe(true)
    expect(needle.axisLine.roundCap).toBe(false)
    expect(ring.axisLine.roundCap).toBe(false)
  })

  test('Auto + solid: needle stays square (no drift), ring round', () => {
    expect(series({ gaugeArcColor: '#ff00aa' })[0].progress.roundCap).toBe(false)
    expect(series({ gaugeArcColor: '#ff00aa', gaugeStyle: 'ring' })[0].progress.roundCap).toBe(true)
  })

  test('Auto + gradient: flat axis and progress on both styles; underlay flat', () => {
    for (const gaugeStyle of [undefined, 'ring']) {
      const [main, under] = series({ gaugeStyle, gaugeArcGradient: true })
      expect(main.axisLine.roundCap).toBe(false)
      expect(main.progress.roundCap).toBe(false)
      expect(under.axisLine.roundCap).toBe(false)
    }
  })

  test('explicit Round + gradient: round axis, hover progress and underlay (grey tail end)', () => {
    for (const gaugeStyle of [undefined, 'ring']) {
      const [main, under] = series({ gaugeStyle, gaugeArcGradient: true, gaugeArcCaps: 'round' })
      expect(main.axisLine.roundCap).toBe(true)
      expect(main.progress.roundCap).toBe(true)
      expect(under.axisLine.roundCap).toBe(true)
      const [, flatUnder] = series({ gaugeStyle, gaugeArcGradient: true, gaugeArcCaps: 'flat' })
      expect(flatUnder.axisLine.roundCap).toBe(false)
    }
  })

  test('explicit Flat / Round override solid and stock on both styles (fill and track)', () => {
    for (const gaugeStyle of [undefined, 'ring']) {
      for (const arc of [{}, { gaugeArcColor: '#ff00aa' }]) {
        const [flat, flatUnder] = series({ gaugeStyle, ...arc, gaugeArcCaps: 'flat' })
        const [round, roundUnder] = series({ gaugeStyle, ...arc, gaugeArcCaps: 'round' })
        expect(flat.progress.roundCap).toBe(false)
        expect(flat.axisLine.roundCap).toBe(false)
        // Round caps the track too, so a banded needle's rim is rounded at min and max.
        expect(round.progress.roundCap).toBe(true)
        expect(round.axisLine.roundCap).toBe(true)
        expect(flatUnder.axisLine.roundCap).toBe(false)
        // Inert without a gradient, but follows Round so the field is always set.
        expect(roundUnder.axisLine.roundCap).toBe(true)
      }
    }
  })

  test('Auto on stock and solid needle: fill and track both square (no drift)', () => {
    for (const arc of [{}, { gaugeArcColor: '#ff00aa' }]) {
      const [s] = series(arc)
      expect(s.progress.roundCap).toBe(false)
      expect(s.axisLine.roundCap).toBe(false)
    }
  })

  test('tween frames keep the same caps as the idle build', () => {
    for (const gaugeStyle of [undefined, 'ring']) {
      for (const gaugeArcCaps of [undefined, 'flat', 'round']) {
        if (gaugeArcCaps === 'round') {
          const [t] = series({ gaugeStyle, gaugeArcGradient: true, gaugeArcCaps }, { gaugeDisplayValue: 30, gaugeTweening: true })
          expect(t.axisLine.roundCap).toBe(true)
        }
        const config = { gaugeStyle, gaugeArcGradient: true, gaugeArcCaps }
        const [idle] = series(config)
        const [tween] = series(config, { gaugeDisplayValue: 30, gaugeTweening: true })
        expect(tween.progress.roundCap).toBe(idle.progress.roundCap)
        expect(tween.axisLine.roundCap).toBe(idle.axisLine.roundCap)
      }
    }
  })

  test('gaugeArcCaps is interpreted, not leaked into the raw ECharts passthrough', () => {
    const opt = buildChartOption({ type: 'gauge', table: [{ v: 72 }], encoding: { y: 'v' }, config: { gaugeArcCaps: 'round' } })
    expect(opt.gaugeArcCaps).toBeUndefined()
  })
})

// ---------------------------------------------------------------------------
// Gauge style switch (needle ↔ ring) — merge-safe options
// ---------------------------------------------------------------------------

describe('buildChartOption — gauge needle/ring are merge-safe', () => {
  const gauge = (config = {}) =>
    buildChartOption({ type: 'gauge', table: [{ v: 72 }], encoding: { y: 'v' }, config }).series[0]

  test('needle sets its geometry explicitly (semicircle, pointer, ticks, labels)', () => {
    const s = gauge()
    expect(s.startAngle).toBe(225)
    expect(s.endAngle).toBe(-45)
    expect(s.pointer.show).toBe(true)
    expect(s.axisTick.show).toBe(true)
    expect(s.splitLine.show).toBe(true)
    expect(s.axisLabel.show).toBe(true)
    expect(s.title.show).toBe(true)
    expect(s.progress.roundCap).toBe(false)
    expect(s.detail.offsetCenter).toEqual([0, '40%'])
    expect(s.detail.formatter).toBe('{value}')
  })

  test('ring keeps its full circle with no pointer/ticks/labels', () => {
    const s = gauge({ gaugeStyle: 'ring' })
    expect([s.startAngle, s.endAngle]).toEqual([90, -270])
    expect(s.pointer.show).toBe(false)
    expect(s.axisTick.show).toBe(false)
    expect(s.axisLabel.show).toBe(false)
    expect(s.detail.offsetCenter).toEqual([0, 0])
  })

  test('both variants define every field either one overrides', () => {
    const paths = [
      'startAngle', 'endAngle', 'progress.show', 'progress.width', 'progress.roundCap',
      'progress.itemStyle.color', 'axisLine.lineStyle.width', 'axisLine.lineStyle.color',
      'splitNumber', 'pointer.show', 'axisTick.show', 'splitLine.show', 'axisLabel.show', 'title.show',
      'detail.offsetCenter', 'detail.fontSize', 'detail.fontWeight', 'detail.formatter',
    ]
    const get = (o, p) => p.split('.').reduce((acc, k) => (acc == null ? acc : acc[k]), o)
    for (const s of [gauge(), gauge({ gaugeStyle: 'ring' })]) {
      for (const p of paths) expect(get(s, p), p).not.toBeUndefined()
    }
  })

  test('gaugeStyle is interpreted, not leaked into the raw ECharts passthrough', () => {
    const opt = buildChartOption({ type: 'gauge', table: [{ v: 72 }], encoding: { y: 'v' }, config: { gaugeStyle: 'ring' } })
    expect(opt.gaugeStyle).toBeUndefined()
  })
})

// ---------------------------------------------------------------------------
// Gauge reuse of Display "Axis text size" / "Y axis ticks"
// ---------------------------------------------------------------------------

describe('buildChartOption — gauge axis text size / ticks', () => {
  const gauge = (config = {}) =>
    buildChartOption({ type: 'gauge', table: [{ v: 72 }], encoding: { y: 'v' }, config }).series[0]

  test('needle: yAxis fontSize sizes the scale labels, splitNumber sets tick count', () => {
    const s = gauge({ yAxis: { axisLabel: { fontSize: 14 }, splitNumber: 5 } })
    expect(s.axisLabel.fontSize).toBe(14)
    expect(s.splitNumber).toBe(5)
    // The rest of axisLabel survives.
    expect(s.axisLabel.show).toBe(true)
    expect(s.axisLabel.distance).toBe(18)
    // Needle centre readout is not tied to axis text size.
    expect(s.detail.fontSize).toBe(24)
  })

  test('needle: unset (or cleared / non-positive) keeps defaults 9 / 10', () => {
    for (const yAxis of [undefined, {}, { axisLabel: { fontSize: undefined }, splitNumber: undefined }, { splitNumber: 0 }]) {
      const s = gauge({ yAxis })
      expect(s.axisLabel.fontSize).toBe(9)
      expect(s.splitNumber).toBe(10)
    }
  })

  test('falls back to xAxis fontSize when yAxis has none', () => {
    expect(gauge({ xAxis: { axisLabel: { fontSize: 16 } } }).axisLabel.fontSize).toBe(16)
  })

  test('ring: axis text size drives the centre readout; ticks have no effect', () => {
    const set = gauge({ gaugeStyle: 'ring', yAxis: { axisLabel: { fontSize: 30 }, splitNumber: 4 } })
    expect(set.detail.fontSize).toBe(30)
    expect(set.axisLabel.show).toBe(false)
    expect(set.axisTick.show).toBe(false)
    expect(set.splitNumber).toBe(10)
    expect(gauge({ gaugeStyle: 'ring' }).detail.fontSize).toBe(20)
  })
})

// ---------------------------------------------------------------------------
// Gauge arc scale labels — smart default + yAxis.format
// ---------------------------------------------------------------------------

describe('gaugeScaleLabelFormatter', () => {
  const ticks = (min, max, n) => Array.from({ length: n + 1 }, (_, i) => min + (i * (max - min)) / n)

  test('auto: 6 ticks on 0..100 give short labels, no repeating decimals', () => {
    const f = gaugeScaleLabelFormatter({ min: 0, max: 100, splitNumber: 6, locale: 'en-US' })
    const labels = ticks(0, 100, 6).map(f)
    expect(labels).toEqual(['0', '17', '33', '50', '67', '83', '100'])
    for (const l of labels) expect(l).not.toMatch(/666|333/)
  })

  test('auto: 5 ticks on 0..100 stay whole numbers', () => {
    const f = gaugeScaleLabelFormatter({ min: 0, max: 100, splitNumber: 5, locale: 'en-US' })
    expect(ticks(0, 100, 5).map(f)).toEqual(['0', '20', '40', '60', '80', '100'])
  })

  test('auto: decimals follow the tick step', () => {
    expect(gaugeScaleLabelFormatter({ min: 0, max: 1, splitNumber: 4, locale: 'en-US' })(0.25)).toBe('0.25')
    expect(gaugeScaleLabelFormatter({ min: 0, max: 5, splitNumber: 10, locale: 'en-US' })(2.5)).toBe('2.5')
    expect(gaugeScaleLabelFormatter({ min: 0, max: 25, splitNumber: 10, locale: 'en-US' })(7.5)).toBe('7.5')
    expect(gaugeScaleLabelFormatter({ min: 0, max: 1, splitNumber: 3, locale: 'en-US' })(1 / 3)).toBe('0.3')
    expect(gaugeScaleLabelFormatter({ min: 0, max: 10000, splitNumber: 5, locale: 'en-US' })(4000)).toBe('4,000')
  })

  test('author format: si / number with decimals / currency', () => {
    expect(gaugeScaleLabelFormatter({ min: 0, max: 5000, splitNumber: 5, format: 'si' })(2000)).toBe('2k')
    expect(gaugeScaleLabelFormatter({ min: 0, max: 100, splitNumber: 6, format: { kind: 'number', decimals: 1 }, locale: 'en-US' })(100 / 6)).toBe('16.7')
    expect(gaugeScaleLabelFormatter({ min: 0, max: 100, splitNumber: 6, format: 'currency', locale: 'en-US' })(100 / 6)).toBe('$17')
  })

  test('percent: 0..100 range is shown as-is + %, 0..1 range is true percent', () => {
    const p100 = gaugeScaleLabelFormatter({ min: 0, max: 100, splitNumber: 6, format: 'percent', locale: 'en-US' })
    expect(p100(100)).toBe('100%')
    expect(p100(100 / 6)).toBe('17%')
    const p1 = gaugeScaleLabelFormatter({ min: 0, max: 1, splitNumber: 4, format: 'percent', locale: 'en-US' })
    expect(p1(0.25)).toBe('25%')
    expect(p1(1)).toBe('100%')
  })

  test('date (hand-edited) falls back to auto; null / non-finite -> empty', () => {
    const f = gaugeScaleLabelFormatter({ min: 0, max: 100, splitNumber: 6, format: 'date', locale: 'en-US' })
    expect(f(50)).toBe('50')
    expect(f(null)).toBe('')
    expect(f(undefined)).toBe('')
    expect(f(NaN)).toBe('')
    expect(f(Infinity)).toBe('')
  })
})

describe('buildChartOption — gauge scale label wiring', () => {
  const gauge = (config = {}) =>
    buildChartOption({ type: 'gauge', table: [{ v: 72 }], encoding: { y: 'v' }, config }).series[0]

  test('needle formats arc labels with the tick count and author format', () => {
    const auto = gauge({ yAxis: { splitNumber: 6 } })
    expect(auto.axisLabel.formatter(100 / 6)).toBe('17')
    const cur = gauge({ locale: 'en-US', yAxis: { splitNumber: 6, format: 'currency' } })
    expect(cur.axisLabel.formatter(50)).toBe('$50')
    // Centre value is untouched by the scale format.
    expect(cur.detail.formatter).toBe('{value}')
    // Clearing the format falls back to auto.
    expect(gauge({ yAxis: { splitNumber: 6, format: undefined } }).axisLabel.formatter(100 / 6)).toBe('17')
  })

  test('ring keeps its hidden scale and % centre', () => {
    const s = gauge({ gaugeStyle: 'ring', yAxis: { format: 'currency' } })
    expect(s.axisLabel.show).toBe(false)
    expect(s.detail.formatter(50)).toBe('50.00%')
  })
})

// ---------------------------------------------------------------------------
// Gauge tooltip — item trigger (no cartesian axis to point at)
// ---------------------------------------------------------------------------

describe('buildChartOption — gauge tooltip', () => {
  const gaugeOpt = (config = {}) =>
    buildChartOption({ type: 'gauge', table: [{ v: 72 }], encoding: { y: 'v' }, config })

  test('needle and ring use an item-trigger tooltip with no axis pointer', () => {
    for (const gaugeStyle of [undefined, 'ring']) {
      const t = gaugeOpt({ gaugeStyle }).tooltip
      expect(t.trigger).toBe('item')
      expect(t.show).not.toBe(false)
      expect(t.axisPointer).toBeUndefined()
    }
  })

  test('tooltip: false still disables it; valueFormat still formats', () => {
    expect(gaugeOpt({ tooltip: false }).tooltip.show).toBe(false)
    const t = gaugeOpt({ locale: 'en-US', tooltip: { valueFormat: 'currency' } }).tooltip
    expect(t.trigger).toBe('item')
    expect(t.valueFormatter(72)).toBe('$72')
  })
})

// ---------------------------------------------------------------------------
// Gauge arc gradient — sampled along the scale, filled to the value
// ---------------------------------------------------------------------------

describe('lerpGaugeArcColor', () => {
  test('straight start→end blend, clamped to [0,1]', () => {
    expect(lerpGaugeArcColor('#000000', '#ffffff', 0)).toBe('#000000')
    expect(lerpGaugeArcColor('#000000', '#ffffff', 1)).toBe('#ffffff')
    expect(lerpGaugeArcColor('#000000', '#ffffff', 0.5)).toBe('#808080')
    expect(lerpGaugeArcColor('#000', '#fff', 2)).toBe('#ffffff')
    expect(lerpGaugeArcColor('#000', '#fff', -1)).toBe('#000000')
  })

  test('accepts rgb()/rgba(); unparseable falls back to the defaults', () => {
    expect(lerpGaugeArcColor('rgb(255, 0, 0)', 'rgba(0,0,255,0.5)', 0.5)).toBe('#800080')
    expect(lerpGaugeArcColor('var(--x)', 'nope', 0)).toBe(GAUGE_ARC_START.toLowerCase())
    expect(lerpGaugeArcColor('var(--x)', 'nope', 1)).toBe(GAUGE_ARC_END.toLowerCase())
  })
})

describe('buildChartOption — gauge arc gradient follows the scale', () => {
  const opt = (v, config = {}) =>
    buildChartOption({ type: 'gauge', table: [{ v }], encoding: { y: 'v' }, config: { gaugeArcGradient: true, ...config } })
  const filled = (stops) => stops.filter(([, c]) => c !== 'rgba(0,0,0,0)')
  const lerp = (t) => lerpGaugeArcColor(GAUGE_ARC_START, GAUGE_ARC_END, t)

  test('5.5 of 100: only start-colour segments, ending at the value', () => {
    for (const gaugeStyle of [undefined, 'ring']) {
      const stops = opt(5.5, { gaugeStyle }).series[0].axisLine.lineStyle.color
      const f = filled(stops)
      expect(f.at(-1)[0]).toBeCloseTo(0.055, 6)
      // Every filled colour sits in the first 5.5% of the blend — nowhere near the end colour.
      const rgb = (h) => [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16))
      const [lo, hi] = [rgb(lerp(0)), rgb(lerp(0.055))]
      for (const [, c] of f) {
        rgb(c).forEach((v, i) => {
          expect(v).toBeGreaterThanOrEqual(Math.min(lo[i], hi[i]))
          expect(v).toBeLessThanOrEqual(Math.max(lo[i], hi[i]))
        })
      }
      expect(JSON.stringify(f)).not.toContain(GAUGE_ARC_END.toLowerCase())
      // The rest of the main layer is see-through (the underlay draws the track).
      expect(stops.at(-1)).toEqual([1, 'rgba(0,0,0,0)'])
    }
  })

  test('72 and 100: the colour at the value is lerp(start, end, value position)', () => {
    const at72 = filled(opt(72).series[0].axisLine.lineStyle.color).at(-1)
    expect(at72[0]).toBeCloseTo(0.72, 6)
    const [r, g] = [parseInt(at72[1].slice(1, 3), 16), parseInt(lerp(0.72).slice(1, 3), 16)]
    expect(Math.abs(r - g)).toBeLessThanOrEqual(3)  // within half a segment of 0.72
    const at100 = opt(100).series[0].axisLine.lineStyle.color
    expect(at100.at(-1)[0]).toBe(1)
    expect(at100.at(-1)[1]).toBe(lerp(1 - 0.5 / 96))
  })

  test('the fill is tied to min/max, not to 0..100', () => {
    const f = filled(opt(150, { yAxis: { min: 100, max: 200 } }).series[0].axisLine.lineStyle.color)
    expect(f.at(-1)[0]).toBeCloseTo(0.5, 6)
  })

  test('underlay: half-segment offset under the main series, track after the value, silent', () => {
    const [main, under] = opt(72).series
    expect(under.type).toBe('gauge')
    expect(under.silent).toBe(true)
    expect(under.z).toBeLessThan(2)
    expect(under.tooltip.show).toBe(false)
    expect(under.axisLine.show).toBe(true)
    expect([under.startAngle, under.endAngle]).toEqual([main.startAngle, main.endAngle])
    const u = under.axisLine.lineStyle.color
    expect(u[0][0]).toBeCloseTo(0.5 / 96, 9)
    expect(u.at(-1)).toEqual([1, 'rgba(148,163,184,0.18)'])
  })

  test('always two series; the underlay is inert without a gradient (merge-safe reset)', () => {
    for (const config of [{}, { gaugeArcColor: '#ff00aa' }, { gaugeStyle: 'ring' }, { gaugeStyle: 'ring', gaugeArcGradient: true }]) {
      const series = buildChartOption({ type: 'gauge', table: [{ v: 72 }], encoding: { y: 'v' }, config }).series
      expect(series).toHaveLength(2)
    }
    const under = buildChartOption({ type: 'gauge', table: [{ v: 72 }], encoding: { y: 'v' }, config: {} }).series[1]
    expect(under.axisLine.show).toBe(false)
  })

  test('solid colour and default bands are unchanged by the gradient code', () => {
    const solid = buildChartOption({ type: 'gauge', table: [{ v: 72 }], encoding: { y: 'v' }, config: { gaugeArcColor: '#ff00aa' } }).series[0]
    expect(solid.progress.itemStyle.color).toBe('#ff00aa')
    expect(solid.axisLine.lineStyle.color).toEqual([[1, 'rgba(148,163,184,0.18)']])
    const bands = buildChartOption({ type: 'gauge', table: [{ v: 72 }], encoding: { y: 'v' } }).series[0]
    expect(bands.axisLine.lineStyle.color).toHaveLength(3)
  })
})

// ---------------------------------------------------------------------------
// Gauge display value (runtime-only gradient tween frames)
// ---------------------------------------------------------------------------

describe('buildChartOption — gauge display value override', () => {
  const build = (extra = {}, config = {}) =>
    buildChartOption({ type: 'gauge', table: [{ v: 10 }, { v: 72 }], encoding: { y: 'v' }, config: { gaugeArcGradient: true, ...config }, ...extra })

  test('gaugeDisplayValue replaces the table value for data, stops and underlay', () => {
    for (const gaugeStyle of [undefined, 'ring']) {
      const [main, under] = build({ gaugeDisplayValue: 50 }, { gaugeStyle }).series
      const [ref50main, ref50under] = buildChartOption({
        type: 'gauge', table: [{ v: 50 }], encoding: { y: 'v' }, config: { gaugeArcGradient: true, gaugeStyle },
      }).series
      expect(main.data[0].value).toBe(50)
      expect(main.axisLine.lineStyle.color).toEqual(ref50main.axisLine.lineStyle.color)
      expect(under.axisLine.lineStyle.color).toEqual(ref50under.axisLine.lineStyle.color)
      // Fill ends at frac 0.5, transparent after.
      expect(main.axisLine.lineStyle.color.at(-2)[0]).toBe(0.5)
    }
  })

  test('no override → last row of the value column', () => {
    const [main] = build().series
    expect(main.data[0].value).toBe(72)
    expect(main.axisLine.lineStyle.color.at(-2)[0]).toBeCloseTo(0.72)
  })

  test('non-finite override falls back to the table value', () => {
    expect(build({ gaugeDisplayValue: NaN }).series[0].data[0].value).toBe(72)
  })

  test('gaugeTweening turns ECharts value/update animation off; idle restores it', () => {
    for (const gaugeStyle of [undefined, 'ring']) {
      const tween = build({ gaugeDisplayValue: 50, gaugeTweening: true }, { gaugeStyle }).series[0]
      expect(tween.detail.valueAnimation).toBe(false)
      expect(tween.animationDurationUpdate).toBe(0)
      const idle = build({}, { gaugeStyle }).series[0]
      expect(idle.detail.valueAnimation).toBe(true)
      // Explicit own key (undefined → inherit) so a merge clears the 0.
      expect('animationDurationUpdate' in idle).toBe(true)
      expect(idle.animationDurationUpdate).toBeUndefined()
    }
  })

  test('override is ignored by non-gauge types and never leaks as a config key', () => {
    const barArgs = { type: 'bar', table: CAT_ROWS, encoding: { x: 'month', y: 'sales' } }
    expect(JSON.stringify(buildChartOption({ ...barArgs, gaugeDisplayValue: 1, gaugeTweening: true })))
      .toBe(JSON.stringify(buildChartOption(barArgs)))
    expect(build({ gaugeDisplayValue: 50 }).gaugeDisplayValue).toBeUndefined()
  })
})

describe('gaugeTargetValue', () => {
  test('last row of y (or value), null without rows', () => {
    expect(gaugeTargetValue([{ v: 5 }, { v: 72 }], { y: 'v' })).toBe(72)
    expect(gaugeTargetValue([{ v: 5 }], { value: 'v' })).toBe(5)
    expect(gaugeTargetValue([], { y: 'v' })).toBeNull()
  })
})
