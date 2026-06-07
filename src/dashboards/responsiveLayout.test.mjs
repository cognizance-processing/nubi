/**
 * responsiveLayout.test.mjs — Node:test unit tests for per-breakpoint layout.
 *
 * Run: node --test src/dashboards/responsiveLayout.test.mjs
 */

import { test, describe } from 'node:test'
import assert from 'node:assert/strict'

import {
  DEVICE_TO_BREAKPOINT,
  overridesFor,
  hasOverrides,
  posToRglItem,
  rglItemToPos,
  effectivePos,
  buildResponsiveLayouts,
  applyLayoutCommit,
  clearBreakpointOverrides,
} from './responsiveLayout.js'

const makeSpec = (responsive) => ({
  layout: { cols: 12 },
  widgets: [
    { id: 'a', type: 'chart', pos: { x: 1, y: 1, w: 4, h: 4 } },
    { id: 'b', type: 'kpi', pos: { x: 5, y: 1, w: 3, h: 3 } },
  ],
  ...(responsive ? { responsive } : {}),
})

describe('device → breakpoint mapping', () => {
  test('desktop=lg, tablet=md, mobile=sm', () => {
    assert.equal(DEVICE_TO_BREAKPOINT.desktop, 'lg')
    assert.equal(DEVICE_TO_BREAKPOINT.tablet, 'md')
    assert.equal(DEVICE_TO_BREAKPOINT.mobile, 'sm')
  })
})

describe('pos ↔ rgl conversion', () => {
  test('posToRglItem converts 1-based to 0-based and clamps to cols', () => {
    const item = posToRglItem('a', { x: 3, y: 2, w: 20, h: 5 }, { cols: 12 })
    assert.deepEqual(item, { i: 'a', x: 2, y: 1, w: 12, h: 5 })
  })
  test('posToRglItem carries static/min/max', () => {
    const item = posToRglItem('a', { x: 1, y: 1, w: 4, h: 4, static: true, minW: 2, maxH: 9 })
    assert.equal(item.static, true)
    assert.equal(item.minW, 2)
    assert.equal(item.maxH, 9)
  })
  test('posToRglItem applies minDefaults when no per-widget min', () => {
    const item = posToRglItem('a', { x: 1, y: 1, w: 4, h: 4 }, { minDefaults: { minW: 3, minH: 3 } })
    assert.equal(item.minW, 3)
    assert.equal(item.minH, 3)
  })
  test('rglItemToPos round-trips and preserves prev constraints', () => {
    const pos = rglItemToPos({ i: 'a', x: 2, y: 1, w: 4, h: 5 }, { static: true, minW: 2 })
    assert.deepEqual(pos, { static: true, minW: 2, x: 3, y: 2, w: 4, h: 5 })
  })
})

describe('overridesFor / hasOverrides', () => {
  test('absent → empty / false', () => {
    const spec = makeSpec()
    assert.deepEqual(overridesFor(spec, 'md'), {})
    assert.equal(hasOverrides(spec, 'md'), false)
  })
  test('present → returns map / true', () => {
    const spec = makeSpec({ md: { a: { x: 1, y: 1, w: 6, h: 4 } } })
    assert.deepEqual(overridesFor(spec, 'md'), { a: { x: 1, y: 1, w: 6, h: 4 } })
    assert.equal(hasOverrides(spec, 'md'), true)
    assert.equal(hasOverrides(spec, 'sm'), false)
  })
})

describe('effectivePos — override else fallback', () => {
  test('lg always returns canonical pos', () => {
    const spec = makeSpec({ md: { a: { x: 1, y: 1, w: 6, h: 6 } } })
    assert.deepEqual(effectivePos(spec.widgets[0], spec, 'lg'), { x: 1, y: 1, w: 4, h: 4 })
  })
  test('md with override returns override merged over pos', () => {
    const spec = makeSpec({ md: { a: { x: 1, y: 1, w: 6, h: 6 } } })
    assert.deepEqual(effectivePos(spec.widgets[0], spec, 'md'), { x: 1, y: 1, w: 6, h: 6 })
  })
  test('md without override falls back to pos', () => {
    const spec = makeSpec({ md: { a: { x: 1, y: 1, w: 6, h: 6 } } })
    assert.deepEqual(effectivePos(spec.widgets[1], spec, 'md'), { x: 5, y: 1, w: 3, h: 3 })
  })
})

describe('buildResponsiveLayouts — back-compat fallback', () => {
  test('no responsive: md mirrors lg, sm is single-column stacked', () => {
    const spec = makeSpec()
    const { lg, md, sm } = buildResponsiveLayouts(spec, 12)
    assert.deepEqual(lg.map(i => [i.i, i.x, i.y, i.w, i.h]), [['a',0,0,4,4],['b',4,0,3,3]])
    assert.deepEqual(md, lg)
    // sm stacks: a at y0 h4, b at y4 h3, width 1
    assert.deepEqual(sm.map(i => [i.i, i.x, i.y, i.w, i.h]), [['a',0,0,1,4],['b',0,4,1,3]])
  })

  test('md override applied only to overridden widget; others fall back', () => {
    const spec = makeSpec({ md: { a: { x: 1, y: 1, w: 12, h: 6 } } })
    const { md } = buildResponsiveLayouts(spec, 12)
    assert.deepEqual(md.find(i => i.i === 'a'), { i: 'a', x: 0, y: 0, w: 12, h: 6 })
    // b inherited from pos
    assert.deepEqual(md.find(i => i.i === 'b'), { i: 'b', x: 4, y: 0, w: 3, h: 3 })
  })

  test('sm override honoured over derived stacking', () => {
    const spec = makeSpec({ sm: { b: { x: 1, y: 1, w: 1, h: 2 } } })
    const { sm } = buildResponsiveLayouts(spec, 12)
    // a uses derived stack (y0), b uses override (y0 h2)
    assert.deepEqual(sm.find(i => i.i === 'b'), { i: 'b', x: 0, y: 0, w: 1, h: 2 })
  })
})

describe('applyLayoutCommit — routes to active breakpoint only', () => {
  test('lg commit writes widget.pos, no responsive added', () => {
    const spec = makeSpec()
    const next = applyLayoutCommit(spec, 'lg', [{ i: 'a', x: 5, y: 5, w: 6, h: 6 }])
    assert.deepEqual(next.widgets.find(w => w.id === 'a').pos, { x: 6, y: 6, w: 6, h: 6 })
    // b untouched
    assert.deepEqual(next.widgets.find(w => w.id === 'b').pos, { x: 5, y: 1, w: 3, h: 3 })
    assert.equal(next.responsive, undefined)
  })

  test('md commit writes ONLY moved widget into responsive.md, pos untouched', () => {
    const spec = makeSpec()
    // Report both items; only "a" actually moved off its fallback.
    const next = applyLayoutCommit(spec, 'md', [
      { i: 'a', x: 0, y: 0, w: 12, h: 6 },  // changed
      { i: 'b', x: 4, y: 0, w: 3, h: 3 },   // same as fallback
    ])
    assert.deepEqual(next.responsive.md, { a: { x: 1, y: 1, w: 12, h: 6 } })
    // canonical pos for both widgets is unchanged
    assert.deepEqual(next.widgets.find(w => w.id === 'a').pos, { x: 1, y: 1, w: 4, h: 4 })
    assert.deepEqual(next.widgets.find(w => w.id === 'b').pos, { x: 5, y: 1, w: 3, h: 3 })
  })

  test('editing md does NOT affect sm and vice-versa', () => {
    let spec = makeSpec()
    spec = applyLayoutCommit(spec, 'md', [{ i: 'a', x: 0, y: 0, w: 12, h: 6 }])
    spec = applyLayoutCommit(spec, 'sm', [{ i: 'a', x: 0, y: 0, w: 1, h: 8 }])
    assert.deepEqual(spec.responsive.md, { a: { x: 1, y: 1, w: 12, h: 6 } })
    assert.deepEqual(spec.responsive.sm, { a: { x: 1, y: 1, w: 1, h: 8 } })
    // desktop layout untouched
    assert.deepEqual(spec.widgets.find(w => w.id === 'a').pos, { x: 1, y: 1, w: 4, h: 4 })
  })

  test('no-op md commit (everything at fallback) returns same spec', () => {
    const spec = makeSpec()
    const next = applyLayoutCommit(spec, 'md', [
      { i: 'a', x: 0, y: 0, w: 4, h: 4 },
      { i: 'b', x: 4, y: 0, w: 3, h: 3 },
    ])
    assert.equal(next, spec)
  })
})

describe('clearBreakpointOverrides', () => {
  test('removes one breakpoint, keeps the other', () => {
    const spec = makeSpec({ md: { a: { x: 1, y: 1, w: 6, h: 4 } }, sm: { a: { x: 1, y: 1, w: 1, h: 5 } } })
    const next = clearBreakpointOverrides(spec, 'md')
    assert.equal(next.responsive.md, undefined)
    assert.deepEqual(next.responsive.sm, { a: { x: 1, y: 1, w: 1, h: 5 } })
  })
  test('removing the last breakpoint drops responsive entirely', () => {
    const spec = makeSpec({ sm: { a: { x: 1, y: 1, w: 1, h: 5 } } })
    const next = clearBreakpointOverrides(spec, 'sm')
    assert.equal(next.responsive, undefined)
  })
  test('lg is a no-op', () => {
    const spec = makeSpec({ md: { a: { x: 1, y: 1, w: 6, h: 4 } } })
    assert.equal(clearBreakpointOverrides(spec, 'lg'), spec)
  })
})
