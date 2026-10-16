import { test } from 'node:test'
import assert from 'node:assert/strict'
import {
  summarizeSpecChanges, describeChanges, describeBreakdown, relativeTime, stableStringify,
} from './boardMeta.js'

const kpi = (id: string, label: string, pos = { x: 1, y: 1, w: 3, h: 2 }) => ({
  id, type: 'kpi', props: { label }, pos, query_id: 'q1',
})

test('identical specs have no changes, whatever the key order', () => {
  const a = { title: 'Sales', widgets: [kpi('a', 'Revenue')] }
  const b = { widgets: [{ pos: { h: 2, w: 3, y: 1, x: 1 }, query_id: 'q1', props: { label: 'Revenue' }, type: 'kpi', id: 'a' }], title: 'Sales' }
  const s = summarizeSpecChanges(a, b)
  assert.equal(s.total, 0)
  assert.equal(stableStringify(a.widgets[0]), stableStringify(b.widgets[0]))
})

test('classifies added, removed, edited and moved widgets', () => {
  const live = { widgets: [kpi('a', 'Revenue'), kpi('b', 'Orders'), kpi('c', 'Margin')] }
  const draft = {
    widgets: [
      kpi('a', 'Revenue', { x: 4, y: 1, w: 3, h: 2 }), // moved
      { ...kpi('b', 'Orders'), query_id: 'q2' },        // edited
      kpi('d', 'Units'),                                 // added (c removed)
    ],
  }
  const s = summarizeSpecChanges(live, draft)
  assert.deepEqual(
    { added: s.added, removed: s.removed, edited: s.edited, moved: s.moved, total: s.total },
    { added: 1, removed: 1, edited: 1, moved: 1, total: 4 },
  )
  assert.equal(s.changes.find(c => c.kind === 'added')?.label, 'Units')
  assert.equal(s.changes.find(c => c.kind === 'removed')?.label, 'Margin')
})

test('board-level differences count once each, widgets and version are ignored there', () => {
  const live = { title: 'Sales', version: 1, background: { color: '#fff' }, widgets: [] }
  const draft = { title: 'Sales review', version: 2, background: { color: '#000' }, widgets: [] }
  const s = summarizeSpecChanges(live, draft)
  assert.equal(s.board, 2)
  assert.deepEqual(s.changes.map(c => c.id).sort(), ['background', 'title'])
})

test('a missing side behaves like an empty board', () => {
  assert.equal(summarizeSpecChanges(null, { widgets: [kpi('a', 'Revenue')] }).added, 1)
  assert.equal(summarizeSpecChanges({ widgets: [kpi('a', 'Revenue')] }, undefined).removed, 1)
  assert.equal(summarizeSpecChanges(undefined, undefined).total, 0)
})

test('copy helpers', () => {
  assert.equal(describeChanges(0), 'No unpushed changes')
  assert.equal(describeChanges(1), '1 unpushed change')
  assert.equal(describeChanges(3), '3 unpushed changes')
  assert.equal(describeChanges(2, 'pending'), '2 pending changes')
  const s = summarizeSpecChanges(
    { title: 'a', widgets: [kpi('a', 'A')] },
    { title: 'b', widgets: [kpi('a', 'A'), kpi('b', 'B'), kpi('c', 'C')] },
  )
  assert.equal(describeBreakdown(s), '2 added · 1 board setting')
})

test('relativeTime', () => {
  const now = Date.parse('2026-10-16T12:00:00Z')
  assert.equal(relativeTime('2026-10-16T11:59:50Z', now), 'just now')
  assert.equal(relativeTime('2026-10-16T11:55:00Z', now), '5m ago')
  assert.equal(relativeTime('2026-10-16T10:00:00Z', now), '2h ago')
  assert.equal(relativeTime('2026-10-13T12:00:00Z', now), '3d ago')
  assert.equal(relativeTime('2026-06-01T12:00:00Z', now), '2026-06-01')
  assert.equal(relativeTime(null, now), '')
  assert.equal(relativeTime('nonsense', now), '')
})
