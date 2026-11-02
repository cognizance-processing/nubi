import test from 'node:test'
import assert from 'node:assert/strict'

import {
  suggestVarName, boardParamUsage, renameVariableReads, readsVariable,
  writesVariable, readerCounts, planVariableRename,
} from './paramWiring.js'

const P = (...names) => names.map(name => ({ name, type: 'text' }))

// ── suggestVarName ───────────────────────────────────────────────────────────

test('suggestVarName strips edge filler words from the label', () => {
  assert.equal(suggestVarName('The region filter'), 'region')
  assert.equal(suggestVarName('Filter by store group'), 'store_group')
  assert.equal(suggestVarName('Select a country'), 'country')
  assert.equal(suggestVarName('Region dropdown'), 'region')
})

test('suggestVarName keeps inner words and lone filler words', () => {
  assert.equal(suggestVarName('Date of birth'), 'date_of_birth')
  assert.equal(suggestVarName('Region manager'), 'region_manager')
  assert.equal(suggestVarName('Filter'), 'filter')
  assert.equal(suggestVarName(''), '')
})

test('suggestVarName adopts an existing board name verbatim', () => {
  assert.equal(suggestVarName('The region filter', ['Region', 'country']), 'Region')
  assert.equal(suggestVarName('Store Group', ['storeGroup']), 'storeGroup')
  // the raw slug is tried too, for names that ARE "filter-ish"
  assert.equal(suggestVarName('Filter', ['filter']), 'filter')
})

test('suggestVarName never guesses between two folded matches', () => {
  assert.equal(suggestVarName('Store group', ['store_group', 'storegroup']), 'store_group')
  assert.equal(suggestVarName('Store group', ['storeGroup', 'store-group']), 'store_group')
})

test('suggestVarName does not match on a partial word', () => {
  assert.equal(suggestVarName('Region manager', ['region']), 'region_manager')
})

test('suggestVarName prefixes a leading digit', () => {
  assert.equal(suggestVarName('2024 targets'), 'v_2024_targets')
})

// ── boardParamUsage ──────────────────────────────────────────────────────────

test('boardParamUsage counts widgets per param, most used first', () => {
  const widgets = [
    { id: 'a', type: 'chart', query_id: 'q1' },
    { id: 'b', type: 'table', query_id: 'q2' },
    { id: 'c', type: 'kpi', query_id: 'q1' },
    { id: 'f', type: 'filter', query_id: 'q1' },      // not wirable
    { id: 't', type: 'text' },
  ]
  const paramsByQueryId = { q1: P('region', 'country', 'region'), q2: P('country') }
  assert.deepEqual(boardParamUsage({ widgets, paramsByQueryId }), [
    { name: 'country', widgets: 3 },
    { name: 'region', widgets: 2 },
  ])
})

// ── renameVariableReads ──────────────────────────────────────────────────────

test('renameVariableReads re-points params, filters and props.variable', () => {
  const w = {
    id: 'a', type: 'chart', query_id: 'q',
    params: { region: { ref: 'old' }, year: { ref: 'yr' }, lit: 'old' },
    filters: { old: 'region_col', yr: 'year' },
    props: { variable: 'old' },
  }
  const out = renameVariableReads(w, 'old', 'new')
  assert.deepEqual(out.params, { region: { ref: 'new' }, year: { ref: 'yr' }, lit: 'old' })
  assert.deepEqual(out.filters, { new: 'region_col', yr: 'year' })
  assert.equal(out.props.variable, 'new')
  assert.equal(w.params.region.ref, 'old', 'input not mutated')
})

test('renameVariableReads walks nested widgets and leaves writers alone', () => {
  const w = {
    id: 's', type: 'stepper',
    props: { steps: [{ widget: { type: 'table', params: { r: { ref: 'old' } }, onClick: { setVar: 'old' } } }] },
    target_var: 'old',
  }
  const out = renameVariableReads(w, 'old', 'new')
  assert.equal(out.props.steps[0].widget.params.r.ref, 'new')
  assert.equal(out.props.steps[0].widget.onClick.setVar, 'old')
  assert.equal(out.target_var, 'old')
})

test('renameVariableReads returns the same object when nothing reads the variable', () => {
  const w = { id: 'a', type: 'chart', params: { r: { ref: 'other' } } }
  assert.equal(renameVariableReads(w, 'old', 'new'), w)
  assert.equal(renameVariableReads(w, 'other', 'other'), w)
})

test('renameVariableReads does not mistake a library-ref widget for a binding', () => {
  const w = { id: 'a', ref: 'old', overrides: {} }
  assert.equal(renameVariableReads(w, 'old', 'new'), w)
})

// ── readers / writers / plan ─────────────────────────────────────────────────

test('readsVariable and writesVariable tell consumers from producers', () => {
  const chart = { type: 'chart', params: { r: { ref: 'region' } } }
  const filter = { type: 'filter', target_var: 'region' }
  const clicker = { type: 'table', onClick: { setVars: [{ var: 'region', field: 'r' }] } }
  assert.equal(readsVariable(chart, 'region'), true)
  assert.equal(readsVariable(filter, 'region'), false)
  assert.equal(writesVariable(filter, 'region'), true)
  assert.equal(writesVariable(clicker, 'region'), true)
  assert.equal(writesVariable(chart, 'region'), false)
})

test('readerCounts counts readers per variable', () => {
  const widgets = [
    { id: 'a', type: 'chart', params: { r: { ref: 'region' } } },
    { id: 'b', type: 'table', filters: { region: 'r', country: 'c' } },
    { id: 'f', type: 'filter', target_var: 'region' },
  ]
  const counts = readerCounts(widgets, ['region', 'country', 'nobody'])
  assert.equal(counts.get('region'), 2)
  assert.equal(counts.get('country'), 1)
  assert.equal(counts.get('nobody'), 0)
})

test('planVariableRename reports moves and other producers of the old name', () => {
  const widgets = [
    { id: 'self', type: 'filter', target_var: 'old' },
    { id: 'a', type: 'chart', params: { r: { ref: 'old' } } },
    { id: 'b', type: 'kpi', filters: { old: 'r' } },
  ]
  assert.deepEqual(planVariableRename(widgets, 'old', 'new', 'self'), { moves: 2, sharedWith: [] })
  const shared = [...widgets, { id: 'f2', type: 'filter', target_var: 'old' }]
  assert.deepEqual(planVariableRename(shared, 'old', 'new', 'self'), { moves: 2, sharedWith: ['f2'] })
  assert.deepEqual(planVariableRename(widgets, 'old', 'old', 'self'), { moves: 0, sharedWith: [] })
})
