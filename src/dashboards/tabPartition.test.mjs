/**
 * tabPartition.test.mjs — Unit tests for the widget-by-tab partition rule.
 *
 * Run with:
 *   node --test src/dashboards/tabPartition.test.mjs
 *
 * Tests the pure partitioning logic extracted from SpecRenderer.jsx's
 * `tabbedWidgets` memo (Track T — DASHBOARD_TABS_AND_FILTERS_IMPLEMENTATION.md T3).
 *
 * Contract (verbatim from SpecRenderer):
 *   "Filter grid widgets down to the active tab. A widget belongs to the active
 *    tab when its tab_id matches the effective tab, OR its tab_id is null/absent
 *    and the effective tab is the first tab (null === first tab). With no tabs
 *    every widget passes through unchanged."
 *
 * Additional drawer-widget contract (from spec.py and SpecRenderer):
 *   Drawer widgets (drawer: true) are already removed from the `widgets` list
 *   before partitioning runs (they live in drawerGroups instead). The partition
 *   function therefore only ever sees non-drawer grid widgets.
 *
 * This file is self-contained — it duplicates the pure function inline rather
 * than importing from JSX (which requires a full transpile step). If the logic
 * in SpecRenderer.jsx changes, update the inline copy here too.
 */

import { test } from 'node:test'
import assert from 'node:assert/strict'

// ---------------------------------------------------------------------------
// Inline copy of the tabbedWidgets filter from SpecRenderer.jsx.
// The function is stateless and has no React dependencies, so it can be
// copied verbatim.
// ---------------------------------------------------------------------------

/**
 * Partition a flat widget array down to those that belong to the active tab.
 *
 * @param {Array<{id: string, tab_id?: string|null}>} widgets  Non-drawer grid widgets.
 * @param {Array<{id: string}>}                       tabs      spec.tabs list.
 * @param {string|null}                               activeTabId  The currently active tab id.
 * @returns {Array}  Subset of widgets visible in the active tab.
 */
function tabbedWidgets(widgets, tabs, activeTabId) {
  if (!Array.isArray(tabs) || tabs.length === 0) return widgets

  const firstTabId = tabs[0]?.id ?? null
  // If no active tab is given, fall back to the first tab (mirrors SpecRenderer's
  // `effectiveTabId = activeTabId ?? internalState ?? firstTabId` and the initial
  // state being null → firstTabId).
  const effectiveTabId = activeTabId ?? firstTabId

  return widgets.filter((w) => {
    const t = w.tab_id ?? null
    if (t === effectiveTabId) return true
    return t == null && effectiveTabId === firstTabId
  })
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function w(id, tab_id = undefined) {
  return { id, tab_id: tab_id ?? null }
}

const TAB1 = { id: 't1', label: 'Tab 1' }
const TAB2 = { id: 't2', label: 'Tab 2' }
const TAB3 = { id: 't3', label: 'Tab 3' }

// ---------------------------------------------------------------------------
// No tabs → all widgets pass through
// ---------------------------------------------------------------------------

test('no tabs: all widgets returned unchanged', () => {
  const widgets = [w('a'), w('b'), w('c')]
  const result = tabbedWidgets(widgets, [], 't1')
  assert.deepEqual(result, widgets)
})

test('no tabs (null): all widgets returned unchanged', () => {
  const widgets = [w('a'), w('b')]
  const result = tabbedWidgets(widgets, null, 't1')
  assert.deepEqual(result, widgets)
})

test('no tabs: empty widget list returns empty array', () => {
  const result = tabbedWidgets([], [], 't1')
  assert.deepEqual(result, [])
})

// ---------------------------------------------------------------------------
// Single tab
// ---------------------------------------------------------------------------

test('single tab: widget with matching tab_id is returned', () => {
  const widgets = [w('w1', 't1')]
  const result = tabbedWidgets(widgets, [TAB1], 't1')
  assert.equal(result.length, 1)
  assert.equal(result[0].id, 'w1')
})

test('single tab: widget with null tab_id is returned (implicit first tab)', () => {
  const widgets = [w('implicit', null)]
  const result = tabbedWidgets(widgets, [TAB1], 't1')
  assert.equal(result.length, 1)
  assert.equal(result[0].id, 'implicit')
})

test('single tab: widget with undefined tab_id treated as null (first tab)', () => {
  const widgets = [{ id: 'x' }]  // tab_id absent, not even set to null
  const result = tabbedWidgets(widgets, [TAB1], 't1')
  assert.equal(result.length, 1)
})

// ---------------------------------------------------------------------------
// Two tabs — activeTabId = first tab
// ---------------------------------------------------------------------------

test('two tabs, active=t1: returns only t1 widgets', () => {
  const widgets = [w('a', 't1'), w('b', 't2'), w('c', 't1')]
  const result = tabbedWidgets(widgets, [TAB1, TAB2], 't1')
  const ids = result.map((x) => x.id)
  assert.deepEqual(ids.sort(), ['a', 'c'])
})

test('two tabs, active=t2: returns only t2 widgets', () => {
  const widgets = [w('a', 't1'), w('b', 't2'), w('c', 't1')]
  const result = tabbedWidgets(widgets, [TAB1, TAB2], 't2')
  const ids = result.map((x) => x.id)
  assert.deepEqual(ids, ['b'])
})

// ---------------------------------------------------------------------------
// null tab_id = first tab rule
// ---------------------------------------------------------------------------

test('null tab_id widget appears when first tab is active', () => {
  const widgets = [w('implicit', null), w('explicit', 't2')]
  const result = tabbedWidgets(widgets, [TAB1, TAB2], 't1')
  const ids = result.map((x) => x.id)
  assert.ok(ids.includes('implicit'), 'null tab_id should be in first tab')
  assert.ok(!ids.includes('explicit'), 'explicit t2 widget should NOT be in t1')
})

test('null tab_id widget does NOT appear when second tab is active', () => {
  const widgets = [w('implicit', null), w('explicit', 't2')]
  const result = tabbedWidgets(widgets, [TAB1, TAB2], 't2')
  const ids = result.map((x) => x.id)
  assert.ok(!ids.includes('implicit'), 'null tab_id widget should not appear on t2')
  assert.ok(ids.includes('explicit'), 'explicit t2 widget should appear on t2')
})

test('null tab_id + explicit first-tab tab_id both appear on first tab', () => {
  const widgets = [w('implicit', null), w('explicit', 't1'), w('other', 't2')]
  const result = tabbedWidgets(widgets, [TAB1, TAB2], 't1')
  const ids = result.map((x) => x.id)
  assert.ok(ids.includes('implicit'), 'implicit should be in first tab')
  assert.ok(ids.includes('explicit'), 'explicit t1 should be in first tab')
  assert.ok(!ids.includes('other'), 't2 widget should not appear on t1')
})

// ---------------------------------------------------------------------------
// No active tab provided → defaults to first tab
// ---------------------------------------------------------------------------

test('activeTabId null → defaults to first tab, returns first-tab widgets', () => {
  const widgets = [w('a', 't1'), w('b', 't2')]
  const result = tabbedWidgets(widgets, [TAB1, TAB2], null)
  const ids = result.map((x) => x.id)
  assert.deepEqual(ids, ['a'])
})

test('activeTabId null + null tab_id widget → both appear on first tab', () => {
  const widgets = [w('implicit', null), w('explicit', 't1'), w('other', 't2')]
  const result = tabbedWidgets(widgets, [TAB1, TAB2], null)
  const ids = result.map((x) => x.id)
  assert.ok(ids.includes('implicit'))
  assert.ok(ids.includes('explicit'))
  assert.ok(!ids.includes('other'))
})

// ---------------------------------------------------------------------------
// Three tabs
// ---------------------------------------------------------------------------

test('three tabs: active=t2 returns only t2 widgets', () => {
  const widgets = [w('a', 't1'), w('b', 't2'), w('c', 't3'), w('d', 't2')]
  const result = tabbedWidgets(widgets, [TAB1, TAB2, TAB3], 't2')
  const ids = result.map((x) => x.id)
  assert.deepEqual(ids.sort(), ['b', 'd'])
})

test('three tabs: null tab_id widget does not appear on t2 or t3', () => {
  const widgets = [w('implicit', null), w('b', 't2')]
  const result2 = tabbedWidgets(widgets, [TAB1, TAB2, TAB3], 't2')
  const result3 = tabbedWidgets(widgets, [TAB1, TAB2, TAB3], 't3')
  assert.ok(!result2.find((x) => x.id === 'implicit'), 'implicit not on t2')
  assert.ok(!result3.find((x) => x.id === 'implicit'), 'implicit not on t3')
})

test('three tabs: null tab_id widget appears only on t1', () => {
  const widgets = [w('implicit', null)]
  const result = tabbedWidgets(widgets, [TAB1, TAB2, TAB3], 't1')
  assert.equal(result.length, 1)
  assert.equal(result[0].id, 'implicit')
})

// ---------------------------------------------------------------------------
// All widgets null tab_id with tabs
// ---------------------------------------------------------------------------

test('all widgets null tab_id, active=first: all appear', () => {
  const widgets = [w('a', null), w('b', null), w('c', null)]
  const result = tabbedWidgets(widgets, [TAB1, TAB2], 't1')
  assert.equal(result.length, 3)
})

test('all widgets null tab_id, active=second: none appear', () => {
  const widgets = [w('a', null), w('b', null)]
  const result = tabbedWidgets(widgets, [TAB1, TAB2], 't2')
  assert.equal(result.length, 0)
})

// ---------------------------------------------------------------------------
// Empty widget list
// ---------------------------------------------------------------------------

test('empty widget list with tabs returns empty array', () => {
  const result = tabbedWidgets([], [TAB1, TAB2], 't1')
  assert.deepEqual(result, [])
})

// ---------------------------------------------------------------------------
// Widget with unknown tab_id (not a declared tab) is excluded
// ---------------------------------------------------------------------------

test('widget with undeclared tab_id is excluded from all tabs', () => {
  // This case is a validate_spec hard error but the filter still handles it
  // gracefully — the widget simply does not appear in any tab.
  const widgets = [w('ghost', 'not_a_tab'), w('real', 't1')]
  const result = tabbedWidgets(widgets, [TAB1, TAB2], 't1')
  const ids = result.map((x) => x.id)
  assert.ok(!ids.includes('ghost'), 'undeclared tab_id widget should not appear')
  assert.ok(ids.includes('real'), 'declared tab_id widget should appear')
})

// ---------------------------------------------------------------------------
// Partition does NOT alter widget objects
// ---------------------------------------------------------------------------

test('returned widgets are the same object references (no cloning)', () => {
  const wObj = w('w1', 't1')
  const result = tabbedWidgets([wObj], [TAB1], 't1')
  assert.equal(result[0], wObj)
})

// ---------------------------------------------------------------------------
// Partition preserves original order
// ---------------------------------------------------------------------------

test('widgets within a tab are returned in their original order', () => {
  const widgets = [
    w('first', 't1'),
    w('second', 't1'),
    w('third', 't1'),
  ]
  const result = tabbedWidgets(widgets, [TAB1, TAB2], 't1')
  assert.deepEqual(
    result.map((x) => x.id),
    ['first', 'second', 'third'],
  )
})
