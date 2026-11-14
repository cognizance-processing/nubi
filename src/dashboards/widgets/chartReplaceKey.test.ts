import { test } from 'node:test'
import assert from 'node:assert/strict'
import { chartReplaceKey } from './chartReplaceKey.js'

test('gauge styles get distinct keys, so ring ↔ needle replaces instead of merging', () => {
  assert.equal(chartReplaceKey('gauge', {}), 'gauge:needle')
  assert.equal(chartReplaceKey('gauge', undefined), 'gauge:needle')
  assert.equal(chartReplaceKey('gauge', { gaugeStyle: 'ring' }), 'gauge:ring')
})

test('gauge arc colours do not change the key (merge stays cheap)', () => {
  assert.equal(
    chartReplaceKey('gauge', { gaugeArcGradient: true, gaugeArcColor: '#ff00aa' }),
    chartReplaceKey('gauge', {}),
  )
})

test('maps key on the geography, defaulting to world', () => {
  assert.equal(chartReplaceKey('map', { map: 'usa' }), 'map:usa')
  assert.equal(chartReplaceKey('map', {}), 'map:world')
})

test('other chart types key on chart_type alone', () => {
  assert.equal(chartReplaceKey('bar', { stack: true }), 'bar')
  assert.notEqual(chartReplaceKey('bar', {}), chartReplaceKey('line', {}))
})
