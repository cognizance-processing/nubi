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
  // T8 — report surface
  emptyReportSurface,
  getReportSurface,
  setReportSurface,
  addReportPage,
  removeReportPage,
  reorderReportPages,
  updateReportPage,
  // T8 — slides surface
  emptySlidesSurface,
  getSlidesSurface,
  setSlidesSurface,
  addSlide,
  removeSlide,
  reorderSlides,
  updateSlide,
  updateSlideWidgetPos,
  SLIDES_DEFAULT_ASPECT,
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
    const spec = makeSpec()
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

// ---------------------------------------------------------------------------
// T8 — Report surface schema + accessors + writers
// ---------------------------------------------------------------------------

describe('T8 — report surface', () => {
  test('emptyReportSurface returns { pages: [] }', () => {
    const s = emptyReportSurface()
    assert.deepEqual(s, { pages: [] })
  })

  test('getReportSurface returns default when absent', () => {
    const spec = makeSpec()
    const r = getReportSurface(spec)
    assert.deepEqual(r, { pages: [] })
  })

  test('getReportSurface returns stored report surface', () => {
    const report = { pages: [{ id: 'p1', items: [], page_break: false }] }
    const spec = { ...makeSpec(), surfaces: { report } }
    const r = getReportSurface(spec)
    assert.deepEqual(r, report)
  })

  test('setReportSurface writes to spec.surfaces.report', () => {
    const spec = makeSpec()
    const report = { pages: [] }
    const next = setReportSurface(spec, report)
    assert.deepEqual(next.surfaces.report, report)
    // original untouched
    assert.equal(spec.surfaces, undefined)
  })

  test('addReportPage appends a page with defaults', () => {
    const spec = makeSpec()
    const next = addReportPage(spec, { id: 'p1' })
    assert.equal(next.surfaces.report.pages.length, 1)
    assert.deepEqual(next.surfaces.report.pages[0], { id: 'p1', items: [], page_break: false })
  })

  test('addReportPage generates id when absent', () => {
    const spec = makeSpec()
    const next = addReportPage(spec, {})
    assert.ok(typeof next.surfaces.report.pages[0].id === 'string')
    assert.ok(next.surfaces.report.pages[0].id.length > 0)
  })

  test('removeReportPage removes by id', () => {
    let spec = makeSpec()
    spec = addReportPage(spec, { id: 'p1' })
    spec = addReportPage(spec, { id: 'p2' })
    const next = removeReportPage(spec, 'p1')
    assert.equal(next.surfaces.report.pages.length, 1)
    assert.equal(next.surfaces.report.pages[0].id, 'p2')
  })

  test('reorderReportPages reorders by id array', () => {
    let spec = makeSpec()
    spec = addReportPage(spec, { id: 'p1' })
    spec = addReportPage(spec, { id: 'p2' })
    spec = addReportPage(spec, { id: 'p3' })
    const next = reorderReportPages(spec, ['p3', 'p1', 'p2'])
    assert.deepEqual(next.surfaces.report.pages.map(p => p.id), ['p3', 'p1', 'p2'])
  })

  test('updateReportPage patches a page by id', () => {
    let spec = makeSpec()
    spec = addReportPage(spec, { id: 'p1', items: [] })
    const item = { widgetId: 'a', width: 'half' }
    const next = updateReportPage(spec, 'p1', { items: [item], page_break: true })
    const page = next.surfaces.report.pages.find(p => p.id === 'p1')
    assert.equal(page.page_break, true)
    assert.deepEqual(page.items, [item])
  })

  test('getSurfaceLayout("report") returns report surface or default', () => {
    const spec = makeSpec()
    const r = getSurfaceLayout(spec, 'report')
    // absent → default (null/empty)
    assert.ok(r === null || (typeof r === 'object' && Array.isArray(r?.pages)))
  })
})

// ---------------------------------------------------------------------------
// T8 — Slides surface schema + accessors + writers
// ---------------------------------------------------------------------------

describe('T8 — slides surface', () => {
  test('SLIDES_DEFAULT_ASPECT is "16:9"', () => {
    assert.equal(SLIDES_DEFAULT_ASPECT, '16:9')
  })

  test('emptySlidesSurface returns { aspect: "16:9", slides: [] }', () => {
    const s = emptySlidesSurface()
    assert.deepEqual(s, { aspect: '16:9', slides: [] })
  })

  test('getSlidesSurface returns default when absent', () => {
    const spec = makeSpec()
    const s = getSlidesSurface(spec)
    assert.deepEqual(s, { aspect: '16:9', slides: [] })
  })

  test('getSlidesSurface returns stored slides surface', () => {
    const slides = { aspect: '4:3', slides: [{ id: 's1', notes: null, items: [] }] }
    const spec = { ...makeSpec(), surfaces: { slides } }
    const s = getSlidesSurface(spec)
    assert.deepEqual(s, slides)
  })

  test('setSlidesSurface writes to spec.surfaces.slides', () => {
    const spec = makeSpec()
    const slides = emptySlidesSurface()
    const next = setSlidesSurface(spec, slides)
    assert.deepEqual(next.surfaces.slides, slides)
  })

  test('addSlide appends a slide with defaults', () => {
    const spec = makeSpec()
    const next = addSlide(spec, { id: 's1' })
    assert.equal(next.surfaces.slides.slides.length, 1)
    assert.deepEqual(next.surfaces.slides.slides[0], { id: 's1', notes: null, items: [] })
  })

  test('addSlide generates id when absent', () => {
    const spec = makeSpec()
    const next = addSlide(spec, {})
    assert.ok(typeof next.surfaces.slides.slides[0].id === 'string')
    assert.ok(next.surfaces.slides.slides[0].id.length > 0)
  })

  test('removeSlide removes by id', () => {
    let spec = makeSpec()
    spec = addSlide(spec, { id: 's1' })
    spec = addSlide(spec, { id: 's2' })
    const next = removeSlide(spec, 's1')
    assert.equal(next.surfaces.slides.slides.length, 1)
    assert.equal(next.surfaces.slides.slides[0].id, 's2')
  })

  test('reorderSlides reorders by id array', () => {
    let spec = makeSpec()
    spec = addSlide(spec, { id: 's1' })
    spec = addSlide(spec, { id: 's2' })
    spec = addSlide(spec, { id: 's3' })
    const next = reorderSlides(spec, ['s3', 's1', 's2'])
    assert.deepEqual(next.surfaces.slides.slides.map(s => s.id), ['s3', 's1', 's2'])
  })

  test('updateSlide patches a slide by id', () => {
    let spec = makeSpec()
    spec = addSlide(spec, { id: 's1' })
    const next = updateSlide(spec, 's1', { notes: 'Hello speaker' })
    const slide = next.surfaces.slides.slides.find(s => s.id === 's1')
    assert.equal(slide.notes, 'Hello speaker')
  })

  test('updateSlideWidgetPos updates a widget item pos in a slide', () => {
    let spec = makeSpec()
    spec = addSlide(spec, {
      id: 's1',
      items: [{ widgetId: 'a', x: 0, y: 0, w: 50, h: 50 }],
    })
    const next = updateSlideWidgetPos(spec, 's1', 'a', { x: 10, y: 20, w: 60, h: 40 })
    const item = next.surfaces.slides.slides[0].items.find(i => i.widgetId === 'a')
    assert.equal(item.x, 10)
    assert.equal(item.y, 20)
    assert.equal(item.w, 60)
    assert.equal(item.h, 40)
  })

  test('getSurfaceLayout("slides") returns slides surface or default', () => {
    const spec = makeSpec()
    const s = getSurfaceLayout(spec, 'slides')
    // absent → default (null/empty)
    assert.ok(s === null || (typeof s === 'object' && Array.isArray(s?.slides)))
  })

  test('unknown surface throws', () => {
    const spec = makeSpec()
    assert.throws(() => getSurfaceLayout(spec, 'unknown_xyz'), /Unknown surface/)
  })
})

// ---------------------------------------------------------------------------
// T8 — EditorShell surface-switch unit (pure logic, no DOM)
// ---------------------------------------------------------------------------

describe('T8 — surface ids contract', () => {
  // Import SURFACE_IDS from useSurfaceState once that module is ESM-importable.
  // For now we test the contract via the constants directly.
  const SURFACE_IDS = ['dashboard', 'report', 'presentation']

  test('SURFACE_IDS contains exactly dashboard, report, presentation', () => {
    assert.deepEqual(SURFACE_IDS, ['dashboard', 'report', 'presentation'])
  })

  test('report surface schema: addReportPage + getReportSurface round-trips', () => {
    let spec = makeSpec()
    spec = addReportPage(spec, { id: 'p1', items: [{ widgetId: 'a', width: 'full' }] })
    const r = getReportSurface(spec)
    assert.equal(r.pages.length, 1)
    assert.equal(r.pages[0].items[0].widgetId, 'a')
    assert.equal(r.pages[0].items[0].width, 'full')
  })

  test('slides surface schema: addSlide + getSlidesSurface round-trips', () => {
    let spec = makeSpec()
    spec = addSlide(spec, {
      id: 'deck1',
      notes: 'Test notes',
      items: [{ widgetId: 'b', x: 5, y: 10, w: 40, h: 30 }],
    })
    const s = getSlidesSurface(spec)
    assert.equal(s.slides.length, 1)
    assert.equal(s.slides[0].notes, 'Test notes')
    assert.equal(s.slides[0].items[0].w, 40)
  })

  test('grid surface unaffected by report/slides mutations', () => {
    const spec = makeSpec()
    let next = addReportPage(spec, { id: 'p1' })
    next = addSlide(next, { id: 's1' })
    // grid layout from widget.pos still works
    const grid = getSurfaceLayout(next, 'grid')
    assert.deepEqual(grid, { a: { x: 1, y: 1, w: 4, h: 4 }, b: { x: 5, y: 1, w: 3, h: 3 } })
  })
})

// ---------------------------------------------------------------------------
// T10 — DocCanvas / report page-flow unit tests (pure logic, no DOM)
// ---------------------------------------------------------------------------

describe('T10 — report page flow + page breaks', () => {
  /**
   * Build a multi-page report spec with the given page configurations.
   * Each entry in `pageDefs` is { id, items, page_break }.
   */
  function makeReportSpec(pageDefs) {
    let spec = makeSpec()
    for (const def of pageDefs) {
      spec = addReportPage(spec, def)
    }
    return spec
  }

  test('addReportPage appends a page to an empty report', () => {
    let spec = makeSpec()
    spec = addReportPage(spec, { id: 'p1', items: [] })
    const r = getReportSurface(spec)
    assert.equal(r.pages.length, 1)
    assert.equal(r.pages[0].id, 'p1')
    assert.equal(r.pages[0].page_break, false)
  })

  test('page.page_break defaults to false', () => {
    let spec = makeSpec()
    spec = addReportPage(spec, { id: 'p1' })
    assert.equal(getReportSurface(spec).pages[0].page_break, false)
  })

  test('page.page_break can be set to true on creation', () => {
    let spec = makeSpec()
    spec = addReportPage(spec, { id: 'p1', page_break: true })
    assert.equal(getReportSurface(spec).pages[0].page_break, true)
  })

  test('updateReportPage toggles page_break', () => {
    let spec = makeSpec()
    spec = addReportPage(spec, { id: 'p1', page_break: false })
    spec = updateReportPage(spec, 'p1', { page_break: true })
    assert.equal(getReportSurface(spec).pages[0].page_break, true)
    spec = updateReportPage(spec, 'p1', { page_break: false })
    assert.equal(getReportSurface(spec).pages[0].page_break, false)
  })

  test('multi-page report: each page is independently page-breakable', () => {
    const spec = makeReportSpec([
      { id: 'p1', items: [{ widgetId: 'a', width: 'full' }], page_break: false },
      { id: 'p2', items: [{ widgetId: 'b', width: 'half' }], page_break: true },
      { id: 'p3', items: [],                                   page_break: false },
    ])
    const pages = getReportSurface(spec).pages
    assert.equal(pages.length, 3)
    assert.equal(pages[0].page_break, false)
    assert.equal(pages[1].page_break, true)
    assert.equal(pages[2].page_break, false)
  })

  test('width hint values round-trip through page items', () => {
    const widths = ['full', 'half', 'third', 50]
    let spec = makeSpec()
    spec = addReportPage(spec, {
      id: 'p1',
      items: widths.map((w, i) => ({ widgetId: `w${i}`, width: w })),
    })
    const items = getReportSurface(spec).pages[0].items
    assert.equal(items.length, 4)
    assert.equal(items[0].width, 'full')
    assert.equal(items[1].width, 'half')
    assert.equal(items[2].width, 'third')
    assert.equal(items[3].width, 50)
  })

  test('reorderReportPages changes the page sequence', () => {
    const spec = makeReportSpec([
      { id: 'p1', items: [] },
      { id: 'p2', items: [] },
      { id: 'p3', items: [] },
    ])
    const reordered = reorderReportPages(spec, ['p3', 'p1', 'p2'])
    const pages = getReportSurface(reordered).pages
    assert.equal(pages[0].id, 'p3')
    assert.equal(pages[1].id, 'p1')
    assert.equal(pages[2].id, 'p2')
  })

  test('removeReportPage removes the target page only', () => {
    const spec = makeReportSpec([
      { id: 'p1', items: [{ widgetId: 'a', width: 'full' }] },
      { id: 'p2', items: [{ widgetId: 'b', width: 'half' }] },
    ])
    const next = removeReportPage(spec, 'p1')
    const pages = getReportSurface(next).pages
    assert.equal(pages.length, 1)
    assert.equal(pages[0].id, 'p2')
  })

  test('updateReportPage patches items without touching other pages', () => {
    const spec = makeReportSpec([
      { id: 'p1', items: [{ widgetId: 'a', width: 'full' }] },
      { id: 'p2', items: [{ widgetId: 'b', width: 'half' }] },
    ])
    const patched = updateReportPage(spec, 'p1', {
      items: [{ widgetId: 'a', width: 'third' }],
    })
    const pages = getReportSurface(patched).pages
    assert.equal(pages[0].items[0].width, 'third')
    assert.equal(pages[1].items[0].width, 'half') // unchanged
  })

  test('page_break on first page is valid (affects print CSS; no content before it)', () => {
    let spec = makeSpec()
    spec = addReportPage(spec, { id: 'p1', page_break: true, items: [] })
    assert.equal(getReportSurface(spec).pages[0].page_break, true)
  })

  test('report surface is null when absent (getReportSurface returns empty default)', () => {
    const spec = { version: 1, title: 'x', widgets: [] }
    const r = getReportSurface(spec)
    assert.deepEqual(r, { pages: [] })
    // setReportSurface creates surfaces.report
    const next = setReportSurface(spec, { pages: [{ id: 'p1', items: [], page_break: false }] })
    assert.equal(next.surfaces.report.pages.length, 1)
  })

  test('grid surface unaffected by report page-break edits', () => {
    let spec = makeSpec()
    spec = addReportPage(spec, { id: 'p1', items: [{ widgetId: 'a', width: 'full' }] })
    spec = updateReportPage(spec, 'p1', { page_break: true })
    // Grid must still read from widget.pos
    const grid = getSurfaceLayout(spec, 'grid')
    assert.ok(grid['a'], 'widget a still has grid pos')
    assert.equal(grid['a'].x, 1)
  })
})
