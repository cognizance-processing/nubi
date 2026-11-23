// Bar / line / funnel styling in the static-export builder (echarts-ssr.mjs)
// must stay in step with the browser builder (embed/widgets/chart-options.ts —
// see the "bar & line styling" block in embed/__tests__/chart-options.test.ts).
//
// Run: node --test scripts/render/chart-styling.test.mjs
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { buildChartOption } from './echarts-ssr.mjs'

const colMap = { m: ['Jan', 'Feb'], v: [5, 7], g: ['A', 'B'] }
const opt = (chart_type, config, encoding = { x: 'm', y: 'v' }, theme) =>
  buildChartOption({ chart_type, encoding, config }, colMap, theme)

test('unset keys leave bar output unchanged', () => {
  const [s] = opt('bar', undefined).series
  assert.equal('barMaxWidth' in s, false)
  assert.deepEqual(Object.keys(s.itemStyle), ['color'])
})

test('barMaxWidth applies only as an explicit number', () => {
  assert.equal(opt('bar', { barMaxWidth: 80 }).series[0].barMaxWidth, 80)
  assert.equal('barMaxWidth' in opt('bar', { barMaxWidth: null }).series[0], false)
  const grouped = opt('bar', { barMaxWidth: 60 }, { x: 'm', y: 'v', color: 'g' }).series
  assert.ok(grouped.every((s) => s.barMaxWidth === 60))
  assert.ok(opt('waterfall', { barMaxWidth: 30 }).series.every((s) => s.barMaxWidth === 30))
})

test('bar outline defaults to the bar colour; explicit colour wins; 0 = none', () => {
  const [s] = opt('bar', { barBorderWidth: 2 }).series
  assert.equal(s.itemStyle.borderWidth, 2)
  assert.equal(s.itemStyle.borderColor, s.itemStyle.color)
  assert.equal(opt('bar', { barBorderWidth: 2, barBorderColor: '#000' }).series[0].itemStyle.borderColor, '#000')
  assert.equal('borderWidth' in opt('bar', { barBorderWidth: 0 }).series[0].itemStyle, false)
  const grouped = opt('bar', { barBorderWidth: 1 }, { x: 'm', y: 'v', color: 'g' }).series
  assert.deepEqual(grouped.map((g) => g.itemStyle.borderColor), grouped.map((g) => g.itemStyle.color))
})

test('a non-colour stroke value never reaches the SVG', () => {
  const [s] = opt('bar', { barBorderWidth: 1, barBorderColor: '"/><script>x</script>' }).series
  assert.equal(s.itemStyle.borderColor, s.itemStyle.color)
  const f = opt('funnel', { barBorderColor: 'red" onload="x' }).series[0].itemStyle
  assert.equal(f.borderColor, '#e3e5e9')
})

test('bar fill opacity', () => {
  assert.equal(opt('bar', { barFillOpacity: 0.5 }).series[0].itemStyle.opacity, 0.5)
})

test('funnel: unset keeps a 1px theme border; colour-only = 1px in that colour; 0 = none', () => {
  const f = (config, theme) => opt('funnel', config, undefined, theme).series[0].itemStyle
  assert.deepEqual(f(undefined), { borderWidth: 1, borderColor: '#e3e5e9' })
  assert.deepEqual(f(undefined, 'dark'), { borderWidth: 1, borderColor: '#26282d' })
  assert.deepEqual(f({ barBorderColor: '#ff0000' }), { borderWidth: 1, borderColor: '#ff0000' })
  assert.deepEqual(f({ barBorderWidth: 3, barBorderColor: '#fff' }), { borderWidth: 3, borderColor: '#fff' })
  assert.equal(f({ barBorderWidth: 0 }).borderWidth, 0)
})

test('line/area honour lineWidth + lineOpacity; unset unchanged', () => {
  for (const t of ['line', 'area']) {
    assert.deepEqual(Object.keys(opt(t, undefined).series[0].lineStyle), ['color'])
    assert.deepEqual(opt(t, { lineWidth: 4, lineOpacity: 0.6 }).series[0].lineStyle, {
      color: opt(t, undefined).series[0].lineStyle.color, width: 4, opacity: 0.6,
    })
  }
})

test('combo: bar outline + width on bars, line width/opacity on lines', () => {
  const o = buildChartOption({
    chart_type: 'combo', encoding: { x: 'm', bars: ['a'], lines: ['b'] },
    config: { barMaxWidth: 50, barBorderWidth: 2, lineWidth: 4, lineOpacity: 0.6 },
  }, { m: ['Jan'], a: [1], b: [2] })
  const bar = o.series.find((s) => s.type === 'bar')
  const line = o.series.find((s) => s.type === 'line')
  assert.equal(bar.barMaxWidth, 50)
  assert.equal(bar.itemStyle.borderWidth, 2)
  assert.equal(line.lineStyle.width, 4)
  assert.equal(line.lineStyle.opacity, 0.6)
})
