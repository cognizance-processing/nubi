/**
 * widgetLibraryShape.test.mjs — the pure strip/restore logic behind library widgets.
 *
 * The contract that matters: a library entry keeps everything that makes a
 * widget what it is, and drops everything about where it happened to live.
 */

import test from 'node:test'
import assert from 'node:assert/strict'
import { toLibraryConfig, fromLibraryRow } from './widgetLibraryShape.js'

const WIDGET = {
  id: 'c_abc123',
  type: 'chart',
  chart_type: 'donut',
  tab_id: 't_overview',
  pos: { x: 5, y: 3, w: 6, h: 4 },
  query_id: 'q_revenue',
  encoding: { x: 'seg', y: 'val' },
  config: { legend: false, palette: ['#22d3ee'] },
  style: { color: '#f5f5f5' },
}

test('toLibraryConfig drops board-local identity', () => {
  const cfg = toLibraryConfig(WIDGET)
  assert.equal(cfg.id, undefined, 'id is board-local')
  assert.equal(cfg.tab_id, undefined, 'tab_id is board-local')
  assert.equal(cfg.pos, undefined, 'pos is replaced by size')
})

test('toLibraryConfig keeps what the widget IS', () => {
  const cfg = toLibraryConfig(WIDGET)
  assert.equal(cfg.type, 'chart')
  assert.equal(cfg.chart_type, 'donut')
  assert.equal(cfg.query_id, 'q_revenue')
  assert.deepEqual(cfg.encoding, { x: 'seg', y: 'val' })
  assert.deepEqual(cfg.config, { legend: false, palette: ['#22d3ee'] })
  assert.deepEqual(cfg.style, { color: '#f5f5f5' })
})

test('toLibraryConfig preserves size as authoring intent, not position', () => {
  const cfg = toLibraryConfig(WIDGET)
  assert.deepEqual(cfg.size, { w: 6, h: 4 }, 'w/h survive')
  assert.equal(JSON.stringify(cfg).includes('"x":5'), false, 'x/y do not survive')
})

test('toLibraryConfig omits size when pos is absent or malformed', () => {
  assert.equal(toLibraryConfig({ type: 'kpi' }).size, undefined)
  assert.equal(toLibraryConfig({ type: 'kpi', pos: { x: 1, y: 1 } }).size, undefined)
})

test('toLibraryConfig does not mutate the source widget', () => {
  const before = structuredClone(WIDGET)
  toLibraryConfig(WIDGET)
  assert.deepEqual(WIDGET, before)
})

test('fromLibraryRow round-trips a saved widget, sans id/pos', () => {
  const row = { id: 'lib1', name: 'Revenue ring', config: toLibraryConfig(WIDGET) }
  const { widget, size } = fromLibraryRow(row)

  assert.deepEqual(size, { w: 6, h: 4 })
  assert.equal(widget.id, undefined, 'caller assigns a fresh id')
  assert.equal(widget.pos, undefined, 'caller assigns placement')
  assert.equal(widget.size, undefined, 'size is handed back separately, not leaked into the spec')
  assert.equal(widget.type, 'chart')
  assert.equal(widget.chart_type, 'donut')
  assert.deepEqual(widget.encoding, { x: 'seg', y: 'val' })
})

test('fromLibraryRow tolerates a junk row rather than throwing', () => {
  for (const row of [null, {}, { config: null }, { config: 'nope' }]) {
    const { widget, size } = fromLibraryRow(row)
    assert.deepEqual(widget, {})
    assert.equal(size, null)
  }
})
