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
  posToGridItem,
  gridItemToPos,
  effectivePos,
  buildResponsiveLayouts,
  applyLayoutCommit,
  clearBreakpointOverrides,
  getSurfaceLayout,
  effectiveWidgetPos,
  migrateSpecToSurfaces,
} from './responsiveLayout.js'

const makeSpec = (responsive?: Record<string, any>) => ({
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

describe('pos ↔ grid conversion', () => {
  test('posToGridItem converts 1-based to 0-based and clamps to cols', () => {
    const item = posToGridItem('a', { x: 3, y: 2, w: 20, h: 5 }, { cols: 12 })
    assert.deepEqual(item, { i: 'a', x: 2, y: 1, w: 12, h: 5 })
  })
  test('posToGridItem carries static/min/max', () => {
    const item = posToGridItem('a', { x: 1, y: 1, w: 4, h: 4, static: true, minW: 2, maxH: 9 })
    assert.equal(item.static, true)
    assert.equal(item.minW, 2)
    assert.equal(item.maxH, 9)
  })
  test('posToGridItem applies minDefaults when no per-widget min', () => {
    const item = posToGridItem('a', { x: 1, y: 1, w: 4, h: 4 }, { minDefaults: { minW: 3, minH: 3 } })
    assert.equal(item.minW, 3)
    assert.equal(item.minH, 3)
  })
  test('gridItemToPos round-trips and preserves prev constraints', () => {
    const pos = gridItemToPos({ i: 'a', x: 2, y: 1, w: 4, h: 5 }, { static: true, minW: 2 })
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

describe('buildResponsiveLayouts — configurable per-breakpoint columns', () => {
  test('omitting colsByBp keeps historical defaults (lg/md=cols, sm=1)', () => {
    const spec = makeSpec()
    const { lg, md, sm } = buildResponsiveLayouts(spec, 12)
    // identical to the back-compat case above
    assert.deepEqual(lg.map(i => [i.i, i.x, i.y, i.w, i.h]), [['a',0,0,4,4],['b',4,0,3,3]])
    assert.deepEqual(md, lg)
    assert.deepEqual(sm.map(i => [i.i, i.x, i.y, i.w, i.h]), [['a',0,0,1,4],['b',0,4,1,3]])
  })

  test('colsByBp.sm widens the sm clamp so overrides are not clamped to 1', () => {
    // A 4-column mobile grid: a wide (w=3) sm override must survive clamping.
    const spec = makeSpec({ sm: { a: { x: 1, y: 1, w: 3, h: 4 } } })
    const { sm } = buildResponsiveLayouts(spec, 12, undefined, { sm: 4 })
    assert.deepEqual(sm.find(i => i.i === 'a'), { i: 'a', x: 0, y: 0, w: 3, h: 4 })
    // With the historical sm=1 clamp the same override would collapse to w=1.
    const { sm: smNarrow } = buildResponsiveLayouts(spec, 12)
    assert.equal(smNarrow.find(i => i.i === 'a').w, 1)
  })

  test('colsByBp.md narrows the md clamp', () => {
    // md derives from lg (w=4 for "a"); clamp md to 2 columns.
    const spec = makeSpec()
    const { md } = buildResponsiveLayouts(spec, 12, undefined, { md: 2 })
    assert.equal(md.find(i => i.i === 'a').w, 2) // clamped 4 -> 2
    assert.equal(md.find(i => i.i === 'b').w, 2) // clamped 3 -> 2
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
    let spec: any = makeSpec()
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

// ---------------------------------------------------------------------------
// T1 — getSurfaceLayout / effectiveWidgetPos / migrateSpecToSurfaces
// ---------------------------------------------------------------------------

describe('T1 getSurfaceLayout — backward-compatible accessor', () => {
  test('legacy board (no surfaces.grid) derives layout from widget.pos', () => {
    const spec = makeSpec()
    const layout = getSurfaceLayout(spec, 'grid')
    assert.deepEqual(layout, {
      a: { x: 1, y: 1, w: 4, h: 4 },
      b: { x: 5, y: 1, w: 3, h: 3 },
    })
  })

  test('migrated board returns surfaces.grid as-is', () => {
    const spec = {
      ...makeSpec(),
      surfaces: { grid: { a: { x: 2, y: 3, w: 6, h: 5 }, b: { x: 5, y: 1, w: 3, h: 3 } } },
    }
    const layout = getSurfaceLayout(spec, 'grid')
    assert.deepEqual(layout.a, { x: 2, y: 3, w: 6, h: 5 })
  })

  test('surfaces.grid overrides inline widget.pos', () => {
    const spec = {
      ...makeSpec(),
      surfaces: { grid: { a: { x: 9, y: 9, w: 1, h: 1 } } },
    }
    const layout = getSurfaceLayout(spec, 'grid')
    // surfaces.grid has only 'a' — that's the full map (non-empty → authoritative).
    assert.deepEqual(layout.a, { x: 9, y: 9, w: 1, h: 1 })
  })

  test('empty surfaces.grid falls back to widget.pos', () => {
    const spec = { ...makeSpec(), surfaces: { grid: {} } }
    const layout = getSurfaceLayout(spec, 'grid')
    // empty grid → legacy fallback
    assert.deepEqual(layout.a, { x: 1, y: 1, w: 4, h: 4 })
  })

  test('unknown surface throws', () => {
    const spec = makeSpec()
    assert.throws(() => getSurfaceLayout(spec, 'foobar'), /Unknown surface/)
  })

  test('empty board returns empty layout', () => {
    const spec = { layout: { cols: 12 }, widgets: [] }
    assert.deepEqual(getSurfaceLayout(spec, 'grid'), {})
  })

  test('widget without pos is skipped in legacy fallback', () => {
    const spec = {
      layout: { cols: 12 },
      widgets: [
        { id: 'a', type: 'chart', pos: { x: 1, y: 1, w: 4, h: 4 } },
        { id: 'b', type: 'filter', drawer: true },  // no pos
      ],
    }
    const layout = getSurfaceLayout(spec, 'grid')
    assert.ok('a' in layout)
    assert.ok(!('b' in layout))
  })
})

describe('T1 effectiveWidgetPos — respects surfaces.grid', () => {
  test('no surfaces.grid → returns widget.pos', () => {
    const spec = makeSpec()
    const w = spec.widgets[0]
    assert.deepEqual(effectiveWidgetPos(w, spec), { x: 1, y: 1, w: 4, h: 4 })
  })

  test('surfaces.grid entry → overrides widget.pos', () => {
    const spec = {
      ...makeSpec(),
      surfaces: { grid: { a: { x: 7, y: 8, w: 2, h: 3 } } },
    }
    const w = spec.widgets[0]
    const pos = effectiveWidgetPos(w, spec)
    assert.equal(pos.x, 7)
    assert.equal(pos.y, 8)
    assert.equal(pos.w, 2)
    assert.equal(pos.h, 3)
  })

  test('surfaces.grid present but no entry for widget → falls back to widget.pos', () => {
    const spec = {
      ...makeSpec(),
      surfaces: { grid: { a: { x: 7, y: 8, w: 2, h: 3 } } },
    }
    const w = spec.widgets[1] // id='b', not in surfaces.grid
    const pos = effectiveWidgetPos(w, spec)
    assert.deepEqual(pos, { x: 5, y: 1, w: 3, h: 3 })
  })

  test('null/undefined widget returns undefined', () => {
    assert.equal(effectiveWidgetPos(null, makeSpec()), undefined)
    assert.equal(effectiveWidgetPos(undefined, makeSpec()), undefined)
  })
})

describe('T1 migrateSpecToSurfaces', () => {
  test('populates surfaces.grid from widget.pos', () => {
    const spec = makeSpec()
    const migrated = migrateSpecToSurfaces(spec)
    assert.deepEqual(migrated.surfaces.grid, {
      a: { x: 1, y: 1, w: 4, h: 4 },
      b: { x: 5, y: 1, w: 3, h: 3 },
    })
  })

  test('widget.pos is preserved after migration (back-compat)', () => {
    const spec = makeSpec()
    const migrated = migrateSpecToSurfaces(spec)
    assert.deepEqual(migrated.widgets[0].pos, { x: 1, y: 1, w: 4, h: 4 })
    assert.deepEqual(migrated.widgets[1].pos, { x: 5, y: 1, w: 3, h: 3 })
  })

  test('idempotent — already-migrated spec returned unchanged', () => {
    const spec = makeSpec()
    const m1 = migrateSpecToSurfaces(spec)
    const m2 = migrateSpecToSurfaces(m1)
    assert.strictEqual(m1, m2)
  })

  test('does not mutate the original spec', () => {
    const spec: any = makeSpec()
    const origGrid = spec?.surfaces?.grid
    migrateSpecToSurfaces(spec)
    assert.equal(spec?.surfaces?.grid, origGrid)
  })

  test('widgets without pos are skipped', () => {
    const spec = {
      layout: { cols: 12 },
      widgets: [
        { id: 'a', type: 'chart', pos: { x: 1, y: 1, w: 4, h: 4 } },
        { id: 'b', type: 'filter', drawer: true },
      ],
    }
    const migrated = migrateSpecToSurfaces(spec)
    assert.ok('a' in migrated.surfaces.grid)
    assert.ok(!('b' in migrated.surfaces.grid))
  })
})

describe('T1 parity — legacy-inline board → accessor yields identical layout', () => {
  test('getSurfaceLayout parity: legacy vs migrated', () => {
    const spec = makeSpec()
    const legacyLayout = getSurfaceLayout(spec, 'grid')
    const migrated = migrateSpecToSurfaces(spec)
    const migratedLayout = getSurfaceLayout(migrated, 'grid')
    assert.deepEqual(legacyLayout, migratedLayout)
  })

  test('buildResponsiveLayouts lg parity: legacy vs migrated', () => {
    const spec = makeSpec()
    const { lg: lgLegacy } = buildResponsiveLayouts(spec, 12)
    const migrated = migrateSpecToSurfaces(spec)
    const { lg: lgMigrated } = buildResponsiveLayouts(migrated, 12)
    assert.deepEqual(lgLegacy, lgMigrated)
  })

  test('buildResponsiveLayouts md/sm parity: legacy vs migrated', () => {
    const spec = makeSpec({ md: { a: { x: 1, y: 1, w: 12, h: 6 } } })
    const { md: mdLegacy, sm: smLegacy } = buildResponsiveLayouts(spec, 12)
    const migrated = migrateSpecToSurfaces(spec)
    const { md: mdMigrated, sm: smMigrated } = buildResponsiveLayouts(migrated, 12)
    assert.deepEqual(mdLegacy, mdMigrated)
    assert.deepEqual(smLegacy, smMigrated)
  })

  test('applyLayoutCommit lg parity: legacy vs migrated', () => {
    const spec = makeSpec()
    const migrated = migrateSpecToSurfaces(spec)

    const legacyNext = applyLayoutCommit(spec, 'lg', [{ i: 'a', x: 5, y: 2, w: 6, h: 5 }])
    const migratedNext = applyLayoutCommit(migrated, 'lg', [{ i: 'a', x: 5, y: 2, w: 6, h: 5 }])

    // widget.pos must be updated identically in both.
    const legacyPos = legacyNext.widgets.find(w => w.id === 'a').pos
    const migratedPos = migratedNext.widgets.find(w => w.id === 'a').pos
    assert.deepEqual(legacyPos, migratedPos)

    // surfaces.grid on the migrated result should also be updated.
    assert.deepEqual(migratedNext.surfaces.grid.a, { x: 6, y: 3, w: 6, h: 5 })
  })
})
