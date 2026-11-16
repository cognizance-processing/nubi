import { test } from 'node:test'
import assert from 'node:assert/strict'
import { previewPropsEqual } from './previewPropsEqual.js'

const chart = (over: Record<string, any> = {}) => ({
  id: 'c1',
  type: 'chart',
  chart_type: 'bar',
  query_id: 'q_revenue',
  pos: { x: 1, y: 1, w: 6, h: 6 },
  config: { xAxis: { axisLabel: { fontSize: 12 } } },
  ...over,
})

test('moving or resizing a widget does not redraw its preview', () => {
  assert.equal(
    previewPropsEqual({ widget: chart() }, { widget: chart({ pos: { x: 9, y: 4, w: 3, h: 2 } }) }),
    true,
  )
})

test('a chart config edit redraws the preview', () => {
  const next = chart({ config: { xAxis: { axisLabel: { fontSize: 20 } } } })
  assert.equal(previewPropsEqual({ widget: chart() }, { widget: next }), false)
})

test('a field the comparator has never heard of still redraws the preview', () => {
  assert.equal(previewPropsEqual({ widget: chart() }, { widget: chart({ params: { region: 'EU' } }) }), false)
})

test('new variable defaults redraw the preview', () => {
  const widget = chart()
  assert.equal(
    previewPropsEqual({ widget, variableDefaults: { a: 1 } }, { widget, variableDefaults: { a: 2 } }),
    false,
  )
})
