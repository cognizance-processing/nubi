/**
 * validateSpec.test.mjs — unit tests for the client-side DashboardSpec
 * validator (mirrors backend/app/dashboards/spec.py::validate_spec rules).
 *
 * Run: npm run test:dash  (node --test 'src/**\/*.test.mjs')
 */

import test from 'node:test'
import assert from 'node:assert/strict'
import { validateDashboardSpec } from './validateSpec.js'

const VALID = {
  version: 1,
  title: 'Sales overview',
  layout: { cols: 12, row_height: 60 },
  variables: [{ name: 'region', type: 'select', default: 'ZA' }],
  widgets: [
    {
      id: 'w1', type: 'kpi', query_id: 'demo_all',
      encoding: { value: 'n' }, props: { label: 'Total' },
      pos: { x: 1, y: 1, w: 4, h: 3 },
    },
    {
      id: 'w2', type: 'chart', query_id: 'demo_all', chart_type: 'bar',
      encoding: { x: 'x', y: 'y' },
      pos: { x: 5, y: 1, w: 8, h: 6 },
      params: { region: { ref: 'region' } },
    },
    {
      id: 'w3', type: 'filter', subtype: 'select', target_var: 'region',
      pos: { x: 1, y: 4, w: 4, h: 2 },
    },
    {
      id: 'w4', type: 'text', content: '# Notes',
      pos: { x: 1, y: 6, w: 4, h: 2 },
    },
  ],
}

test('valid spec produces no issues', () => {
  assert.deepEqual(validateDashboardSpec(VALID), [])
})

test('non-object specs are rejected', () => {
  assert.equal(validateDashboardSpec(null).length, 1)
  assert.equal(validateDashboardSpec('nope').length, 1)
  assert.equal(validateDashboardSpec([1, 2]).length, 1)
})

test('missing title is an issue', () => {
  const issues = validateDashboardSpec({ ...VALID, title: '' })
  assert.ok(issues.some(i => i.includes("'title'")))
})

test('duplicate widget ids are flagged', () => {
  const spec = { ...VALID, widgets: [VALID.widgets[0], { ...VALID.widgets[0] }] }
  const issues = validateDashboardSpec(spec)
  assert.ok(issues.some(i => i.includes('Duplicate widget id')))
})

test('unknown widget type is flagged', () => {
  const spec = { ...VALID, widgets: [{ id: 'x', type: 'sparkline', pos: { x: 1, y: 1, w: 1, h: 1 } }] }
  const issues = validateDashboardSpec(spec)
  assert.ok(issues.some(i => i.includes("unknown type 'sparkline'")))
})

test('invalid pos values are flagged', () => {
  const spec = { ...VALID, widgets: [{ id: 'x', type: 'kpi', pos: { x: 0, y: 1, w: 4, h: 1.5 } }] }
  const issues = validateDashboardSpec(spec)
  assert.ok(issues.some(i => i.includes('pos.x')))
  assert.ok(issues.some(i => i.includes('pos.h')))
})

test('missing pos is flagged', () => {
  const spec = { ...VALID, widgets: [{ id: 'x', type: 'kpi' }] }
  const issues = validateDashboardSpec(spec)
  assert.ok(issues.some(i => i.includes("'pos'")))
})

test('chart without chart_type / encoding is flagged', () => {
  const spec = { ...VALID, widgets: [{ id: 'c', type: 'chart', pos: { x: 1, y: 1, w: 4, h: 4 } }] }
  const issues = validateDashboardSpec(spec)
  assert.ok(issues.some(i => i.includes("'chart_type'")))
  assert.ok(issues.some(i => i.includes("'x' column")))
  assert.ok(issues.some(i => i.includes("'y' column")))
})

test('filter without subtype / target_var is flagged', () => {
  const spec = { ...VALID, widgets: [{ id: 'f', type: 'filter', pos: { x: 1, y: 1, w: 4, h: 2 } }] }
  const issues = validateDashboardSpec(spec)
  assert.ok(issues.some(i => i.includes("'subtype'")))
  assert.ok(issues.some(i => i.includes("'target_var'")))
})

test('text widget without content is flagged', () => {
  const spec = { ...VALID, widgets: [{ id: 't', type: 'text', pos: { x: 1, y: 1, w: 4, h: 2 } }] }
  const issues = validateDashboardSpec(spec)
  assert.ok(issues.some(i => i.includes("'content'")))
})

test('undeclared param ref is flagged', () => {
  const spec = {
    ...VALID,
    variables: [],
    widgets: [{
      id: 'w', type: 'kpi', pos: { x: 1, y: 1, w: 4, h: 2 },
      params: { region: { ref: 'region' } },
    }],
  }
  const issues = validateDashboardSpec(spec)
  assert.ok(issues.some(i => i.includes("ref 'region' is not declared")))
})

test('literal params are allowed without declarations', () => {
  const spec = {
    ...VALID,
    variables: [],
    widgets: [{
      id: 'w', type: 'kpi', pos: { x: 1, y: 1, w: 4, h: 2 },
      params: { limit: 10 },
    }],
  }
  assert.deepEqual(validateDashboardSpec(spec), [])
})

test('bad variable entries are flagged', () => {
  const spec = { ...VALID, variables: [{ type: 'select' }, { name: 'a', type: 'magic' }], widgets: [] }
  const issues = validateDashboardSpec(spec)
  assert.ok(issues.some(i => i.includes("'name' is required")))
  assert.ok(issues.some(i => i.includes('type must be one of')))
})
