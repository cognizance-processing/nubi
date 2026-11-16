/**
 * Unit tests for the stepper widget's step-transition logic.
 *
 * These cover the legacy in-tile drill-down contract: stepping BACK releases the
 * filter variables the abandoned steps had set, while stepping FORWARD keeps the
 * value the click just wrote (that value is exactly what the next step reads).
 */

import { test } from 'node:test'
import assert from 'node:assert/strict'
import { clampStep, variablesToClear } from './stepper.js'

const steps = [
  { label: 'Region', widget: { type: 'chart', onClick: { setVar: 'region', stepNext: true } } },
  { label: 'Branch', widget: { type: 'chart', onClick: { setVar: 'branch', stepNext: true } } },
  { label: 'Employee', widget: { type: 'table' } },
]

test('clampStep keeps the index inside the step list', () => {
  assert.equal(clampStep(-3, 3), 0)
  assert.equal(clampStep(0, 3), 0)
  assert.equal(clampStep(2, 3), 2)
  assert.equal(clampStep(9, 3), 2)
})

test('clampStep is safe for an empty or malformed step list', () => {
  assert.equal(clampStep(1, 0), 0)
  assert.equal(clampStep(Number.NaN, 3), 0)
})

test('stepping forward clears nothing', () => {
  // The value the click just set is what the next step filters on — clearing it
  // would defeat the drill-down.
  assert.deepEqual(variablesToClear(steps, 0, 1), [])
  assert.deepEqual(variablesToClear(steps, 1, 2), [])
})

test('staying on the same step clears nothing', () => {
  assert.deepEqual(variablesToClear(steps, 1, 1), [])
})

test('stepping back releases the abandoned steps variables', () => {
  // Back to Region: both Region's and Branch's filters must be released, or the
  // widgets behind the tile stay pinned to a drill-down the user left.
  assert.deepEqual(variablesToClear(steps, 2, 0), ['region', 'branch'])
  assert.deepEqual(variablesToClear(steps, 2, 1), ['branch'])
})

test('steps without an onClick setVar contribute nothing', () => {
  const noVars = [{ label: 'A', widget: { type: 'table' } }, { label: 'B', widget: {} }]
  assert.deepEqual(variablesToClear(noVars, 1, 0), [])
})

test('a variable reused across steps is only cleared once', () => {
  const dupes = [
    { label: 'A', widget: { onClick: { setVar: 'region' } } },
    { label: 'B', widget: { onClick: { setVar: 'region' } } },
  ]
  assert.deepEqual(variablesToClear(dupes, 1, 0), ['region'])
})

test('malformed step lists do not throw', () => {
  assert.deepEqual(variablesToClear(undefined, 2, 0), [])
  assert.deepEqual(variablesToClear([null, undefined], 1, 0), [])
})
