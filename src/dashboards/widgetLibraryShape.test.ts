/**
 * widgetLibraryShape.test.mjs — the pure strip/restore logic behind library widgets.
 *
 * The contract that matters: a library entry keeps everything that makes a
 * widget what it is, and drops everything about where it happened to live.
 */

import test from 'node:test'
import assert from 'node:assert/strict'
import {
  toLibraryConfig, fromLibraryRow, resolveWidgetRef, isRefWidget,
  applyWidgetEdit, deepMergeOverrides, diffOverrides,
  flattenOverridePaths, removeOverridePath, humanizeOverridePath,
} from './widgetLibraryShape.js'

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

test('toLibraryConfig also strips placement/order/drawer/drawer_group/ref/overrides', () => {
  const w = {
    id: 'w1', type: 'filter', pos: { x: 1, y: 1, w: 2, h: 2 },
    placement: 'header', order: 3, drawer: true, drawer_group: 'filters',
    ref: 'lib9', overrides: { title: 'x' },
    subtype: 'select',
  }
  const cfg = toLibraryConfig(w)
  for (const k of ['placement', 'order', 'drawer', 'drawer_group', 'ref', 'overrides']) {
    assert.equal(cfg[k], undefined, `${k} must not survive into a library config`)
  }
  assert.equal(cfg.subtype, 'select', 'non-board-local fields still survive')
})

// ---------------------------------------------------------------------------
// deepMergeOverrides / diffOverrides — the merge rule and its inverse
// ---------------------------------------------------------------------------

test('deepMergeOverrides: sparse overrides only change named keys', () => {
  const base = { query_id: 'lib_query', props: { label: 'Revenue', format: 'currency' } }
  const merged = deepMergeOverrides(base, { props: { label: 'Custom Revenue' } })
  assert.deepEqual(merged, { query_id: 'lib_query', props: { label: 'Custom Revenue', format: 'currency' } })
})

test('deepMergeOverrides: nested dicts merge key-by-key recursively', () => {
  const base = { encoding: { x: 'month', y: 'revenue', color: 'region' } }
  const merged = deepMergeOverrides(base, { encoding: { y: 'profit' } })
  assert.deepEqual(merged.encoding, { x: 'month', y: 'profit', color: 'region' })
})

test('deepMergeOverrides: arrays replace wholesale, never concatenate', () => {
  const base = { props: { columns: ['a', 'b', 'c'] } }
  const merged = deepMergeOverrides(base, { props: { columns: ['x'] } })
  assert.deepEqual(merged.props.columns, ['x'])
})

test('deepMergeOverrides does not mutate its inputs', () => {
  const base = { props: { label: 'Revenue' } }
  const overrides = { props: { label: 'Custom' } }
  const baseBefore = structuredClone(base)
  const overridesBefore = structuredClone(overrides)
  deepMergeOverrides(base, overrides)
  assert.deepEqual(base, baseBefore)
  assert.deepEqual(overrides, overridesBefore)
})

test('diffOverrides is the inverse of deepMergeOverrides', () => {
  const base = { query_id: 'lib_query', props: { label: 'Revenue', format: 'currency' } }
  const edited = { query_id: 'lib_query', props: { label: 'Custom Revenue', format: 'currency' } }
  assert.deepEqual(diffOverrides(base, edited), { props: { label: 'Custom Revenue' } })
})

test('diffOverrides: editing a field back to the base value clears its override', () => {
  const base = { props: { label: 'Revenue' } }
  const edited = { props: { label: 'Revenue' } } // user typed it back to the original
  assert.deepEqual(diffOverrides(base, edited), {})
})

test('diffOverrides: array field only overrides when different', () => {
  const base = { props: { columns: ['a', 'b'] } }
  assert.deepEqual(diffOverrides(base, { props: { columns: ['a', 'b'] } }), {})
  assert.deepEqual(diffOverrides(base, { props: { columns: ['a'] } }), { props: { columns: ['a'] } })
})

// ---------------------------------------------------------------------------
// resolveWidgetRef — mirrors backend resolve_widget_refs
// ---------------------------------------------------------------------------

function pos(x = 1, y = 1, w = 4, h = 3) { return { x, y, w, h } }

test('isRefWidget', () => {
  assert.equal(isRefWidget({ ref: 'lib1' }), true)
  assert.equal(isRefWidget({ ref: null }), false)
  assert.equal(isRefWidget({ ref: '' }), false)
  assert.equal(isRefWidget({ type: 'kpi' }), false)
  assert.equal(isRefWidget(null), false)
})

test('resolveWidgetRef: inline widgets pass through unchanged', () => {
  const w = { id: 'w1', type: 'kpi', query_id: 'q1', pos: pos() }
  const { widget, broken } = resolveWidgetRef(w, null)
  assert.equal(broken, false)
  assert.equal(widget, w)
})

test('resolveWidgetRef: ref resolves to merged library config', () => {
  const row = { id: 'lib1', config: {
    type: 'kpi', query_id: 'lib_query',
    encoding: { value: 'revenue' }, props: { label: 'Revenue', format: 'currency' },
  } }
  const w = { id: 'w1', ref: 'lib1', overrides: { props: { label: 'Custom Revenue' } }, pos: pos() }
  const { widget, broken } = resolveWidgetRef(w, row)
  assert.equal(broken, false)
  assert.equal(widget.type, 'kpi')
  assert.equal(widget.query_id, 'lib_query')
  assert.deepEqual(widget.props, { label: 'Custom Revenue', format: 'currency' })
  assert.deepEqual(widget.encoding, { value: 'revenue' })
})

test('resolveWidgetRef: board-local fields always forced from the spec entry', () => {
  const row = { id: 'lib1', config: {
    type: 'kpi', query_id: 'q1',
    id: 'STALE_ID', pos: { x: 99, y: 99, w: 1, h: 1 }, tab_id: 'STALE_TAB',
    placement: 'header', order: 999, drawer: true, drawer_group: 'STALE_GROUP',
  } }
  const w = { id: 'w1', ref: 'lib1', pos: pos(2, 3, 5, 6), tab_id: 't1', order: 7 }
  const { widget } = resolveWidgetRef(w, row)
  assert.equal(widget.id, 'w1')
  assert.deepEqual(widget.pos, pos(2, 3, 5, 6))
  assert.equal(widget.tab_id, 't1')
  assert.equal(widget.placement, 'grid')
  assert.equal(widget.order, 7)
  assert.equal(widget.drawer, false)
  assert.equal(widget.drawer_group, null)
})

test('resolveWidgetRef: missing library row degrades to a visible broken placeholder', () => {
  const w = { id: 'w1', ref: 'does_not_exist', pos: pos() }
  const { widget, broken } = resolveWidgetRef(w, null)
  assert.equal(broken, true)
  assert.equal(widget.id, 'w1')
  assert.equal(widget.type, 'text')
  assert.deepEqual(widget.pos, pos())
  assert.match(widget.content, /does_not_exist/)
})

test('resolveWidgetRef: library config missing a type degrades cleanly', () => {
  const row = { id: 'lib1', config: { query_id: 'q1' } }
  const w = { id: 'w1', ref: 'lib1', pos: pos() }
  const { widget, broken } = resolveWidgetRef(w, row)
  assert.equal(broken, true)
  assert.equal(widget.type, 'text')
})

test('resolveWidgetRef: non-dict config degrades cleanly', () => {
  const row = { id: 'lib1', config: 'not-a-dict' }
  const w = { id: 'w1', ref: 'lib1', pos: pos() }
  const { widget, broken } = resolveWidgetRef(w, row)
  assert.equal(broken, true)
  assert.equal(widget.type, 'text')
})

test('resolveWidgetRef does not mutate the library row or the spec widget', () => {
  const row = { id: 'lib1', config: { type: 'kpi', props: { label: 'Revenue' } } }
  const rowBefore = structuredClone(row)
  const w = { id: 'w1', ref: 'lib1', overrides: { props: { label: 'Custom' } }, pos: pos() }
  const wBefore = structuredClone(w)
  resolveWidgetRef(w, row)
  assert.deepEqual(row, rowBefore)
  assert.deepEqual(w, wBefore)
})

// ---------------------------------------------------------------------------
// applyWidgetEdit — the inverse: fold an inspector edit back into overrides
// ---------------------------------------------------------------------------

test('applyWidgetEdit: identity for inline widgets (backward compatible)', () => {
  const spec = { id: 'w1', type: 'kpi', query_id: 'q1', pos: pos() }
  const edited = { ...spec, query_id: 'q2' }
  assert.equal(applyWidgetEdit(spec, edited, null), edited)
})

test('applyWidgetEdit: an edit on a ref widget becomes a sparse override, never touches the library', () => {
  const row = { id: 'lib1', config: { type: 'kpi', query_id: 'lib_query', props: { label: 'Revenue', format: 'currency' } } }
  const spec = { id: 'w1', ref: 'lib1', overrides: {}, pos: pos() }
  const { widget: effective } = resolveWidgetRef(spec, row)
  const edited = { ...effective, props: { ...effective.props, label: 'Scheduled Calls' }, query_id: 'calls_scheduled' }
  const nextSpec = applyWidgetEdit(spec, edited, row)
  assert.equal(nextSpec.ref, 'lib1')
  assert.deepEqual(nextSpec.overrides, { query_id: 'calls_scheduled', props: { label: 'Scheduled Calls' } })
  // The library row itself is untouched by this — only the caller choosing to
  // PUT the change to the library ("edit for all") would change row.config.
  assert.deepEqual(row.config.props, { label: 'Revenue', format: 'currency' })
})

test('applyWidgetEdit: editing a field back to the library value clears its override (reset)', () => {
  const row = { id: 'lib1', config: { type: 'kpi', props: { label: 'Revenue' } } }
  const spec = { id: 'w1', ref: 'lib1', overrides: { props: { label: 'Custom' } }, pos: pos() }
  const { widget: effective } = resolveWidgetRef(spec, row)
  assert.equal(effective.props.label, 'Custom')
  const resetEdit = { ...effective, props: { ...effective.props, label: 'Revenue' } }
  const nextSpec = applyWidgetEdit(spec, resetEdit, row)
  assert.deepEqual(nextSpec.overrides, {})
})

test('applyWidgetEdit: pos/tab_id edits are taken directly, never diffed against the library', () => {
  const row = { id: 'lib1', config: { type: 'kpi', props: { label: 'Revenue' } } }
  const spec = { id: 'w1', ref: 'lib1', overrides: {}, pos: pos(1, 1, 4, 3) }
  const { widget: effective } = resolveWidgetRef(spec, row)
  const moved = { ...effective, pos: pos(5, 5, 4, 3) }
  const nextSpec = applyWidgetEdit(spec, moved, row)
  assert.deepEqual(nextSpec.pos, pos(5, 5, 4, 3))
  assert.deepEqual(nextSpec.overrides, {}, 'pos never becomes part of overrides')
})

test('applyWidgetEdit round-trips through resolveWidgetRef', () => {
  const row = { id: 'lib1', config: { type: 'chart', chart_type: 'bar', query_id: 'q1', encoding: { x: 'month', y: 'revenue' } } }
  const spec = { id: 'w1', ref: 'lib1', overrides: {}, pos: pos() }
  const { widget: effective } = resolveWidgetRef(spec, row)
  const edited = { ...effective, encoding: { ...effective.encoding, y: 'profit' } }
  const nextSpec = applyWidgetEdit(spec, edited, row)
  const { widget: reresolved } = resolveWidgetRef(nextSpec, row)
  assert.equal(reresolved.encoding.y, 'profit')
  assert.equal(reresolved.encoding.x, 'month')
})

// ---------------------------------------------------------------------------
// Override introspection helpers
// ---------------------------------------------------------------------------

test('flattenOverridePaths flattens nested overrides to leaf paths', () => {
  const flat = flattenOverridePaths({ query_id: 'q2', props: { label: 'X', format: 'percent' } })
  const byPath = Object.fromEntries(flat.map(e => [e.path, e.value]))
  assert.deepEqual(byPath, { query_id: 'q2', 'props.label': 'X', 'props.format': 'percent' })
})

test('flattenOverridePaths returns [] for empty/nullish overrides', () => {
  assert.deepEqual(flattenOverridePaths({}), [])
  assert.deepEqual(flattenOverridePaths(null), [])
})

test('removeOverridePath removes one leaf and prunes an emptied parent', () => {
  const overrides = { props: { label: 'X', format: 'percent' } }
  const afterOne = removeOverridePath(overrides, 'props.label')
  assert.deepEqual(afterOne, { props: { format: 'percent' } })
  const afterBoth = removeOverridePath(afterOne, 'props.format')
  assert.deepEqual(afterBoth, {})
})

test('removeOverridePath does not mutate the input', () => {
  const overrides = { props: { label: 'X' } }
  const before = structuredClone(overrides)
  removeOverridePath(overrides, 'props.label')
  assert.deepEqual(overrides, before)
})

test('humanizeOverridePath: known paths get friendly labels, unknown paths humanize the last segment', () => {
  assert.equal(humanizeOverridePath('props.label'), 'Label')
  assert.equal(humanizeOverridePath('query_id'), 'Query')
  assert.equal(humanizeOverridePath('props.someCustomField'), 'Some Custom Field')
})
