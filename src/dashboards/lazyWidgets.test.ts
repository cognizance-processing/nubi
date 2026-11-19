/**
 * lazyWidgets.test.ts — which widgets wait until they are near the screen.
 *
 * Run with:
 *   node --import tsx --test src/dashboards/lazyWidgets.test.ts
 */

import { test } from 'node:test'
import assert from 'node:assert/strict'
import { shouldLazyMount } from './lazyWidgets.js'

test('data widgets wait on the board view', () => {
  for (const type of ['table', 'chart', 'pivot', 'kpi', 'metric']) {
    assert.equal(shouldLazyMount({ type }, false), true, type)
  }
})

test('nothing waits on the editor canvas', () => {
  for (const type of ['table', 'chart', 'pivot', 'kpi', 'metric']) {
    assert.equal(shouldLazyMount({ type }, true), false, type)
  }
})

test('filters and static tiles always mount at once', () => {
  // A filter can set its default value on mount, which tables above it need.
  for (const type of ['filter', 'text', 'image', 'section', 'divider', 'datedisplay', 'breadcrumb', 'stepper']) {
    assert.equal(shouldLazyMount({ type }, false), false, type)
  }
})

test('custom-HTML tiles always mount at once', () => {
  assert.equal(shouldLazyMount({ type: 'table', html: '<b>x</b>' }, false), false)
})

test('bad input never defers', () => {
  assert.equal(shouldLazyMount(null, false), false)
  assert.equal(shouldLazyMount(undefined, false), false)
  assert.equal(shouldLazyMount('table', false), false)
})
