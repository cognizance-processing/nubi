// Tooltip formatters return HTML. Column names and cell values come from the
// data source, so every interpolated value must be escaped.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { makeTable, vectorFromArray, Float64, Utf8 } from 'apache-arrow'
import { buildChartOption } from '../../src/viz/chartOption.js'

const EVIL = '<img src=x onerror=alert(1)>'

function assertEscaped(html: string) {
  assert.ok(!html.includes('<img'), `unescaped markup in tooltip: ${html}`)
  assert.ok(html.includes('&lt;img'), `expected the value to be shown escaped: ${html}`)
}

test('scatter tooltip escapes column names', () => {
  const table = makeTable({
    [EVIL]: vectorFromArray([1, 2], new Float64()),
    y: vectorFromArray([3, 4], new Float64()),
  } as any)
  const opt = buildChartOption({ chartType: 'scatter', table, x: EVIL, y: 'y' })
  assertEscaped(opt.tooltip.formatter({ value: [1, 3], dataIndex: 0 }))
})

test('heatmap tooltip escapes category values', () => {
  const table = makeTable({
    a: vectorFromArray([EVIL, 'ok'], new Utf8()),
    b: vectorFromArray(['p', 'q'], new Utf8()),
    v: vectorFromArray([1, 2], new Float64()),
  } as any)
  const opt = buildChartOption({ chartType: 'heatmap', table, x: 'a', y: 'b', encoding: { value: 'v' } })
  const html = opt.tooltip.formatter({ value: [0, 0, 1] })
  assertEscaped(html)
})

test('waterfall tooltip escapes category labels', () => {
  const table = makeTable({
    category: vectorFromArray([EVIL, 'b'], new Utf8()),
    delta: vectorFromArray([5, -2], new Float64()),
  } as any)
  const opt = buildChartOption({ chartType: 'waterfall', table, x: 'category', y: 'delta' })
  assertEscaped(opt.tooltip.formatter([{ seriesName: 'delta', dataIndex: 0 }]))
})
