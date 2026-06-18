/**
 * surfaceGenerators.test.mjs — T11 unit tests for surface conversion generators.
 *
 * Run: node --test src/dashboards/surfaceGenerators.test.mjs
 * Or: npm run test:dash  (picks up all .test.mjs in the tree)
 */

import { test, describe } from 'node:test'
import assert from 'node:assert/strict'

import {
  gridSpecToReport,
  gridSpecToSlides,
  canGenerateReport,
  canGenerateSlides,
  reportHasContent,
  slidesHasContent,
  REPORT_PAGE_SIZE,
  SLIDE_MAX_ITEMS,
} from './surfaceGenerators.js'

import {
  getReportSurface,
  getSlidesSurface,
} from './responsiveLayout.js'

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

/** A spec with 3 widgets arranged in two rows on a 12-col grid. */
function makeGridSpec() {
  return {
    layout: { cols: 12 },
    widgets: [
      { id: 'chart1', type: 'chart', pos: { x: 1, y: 1, w: 8, h: 6 } },  // wide
      { id: 'kpi1',   type: 'kpi',   pos: { x: 9, y: 1, w: 4, h: 3 } },  // narrow right
      { id: 'table1', type: 'table', pos: { x: 1, y: 7, w: 6, h: 5 } },  // half row 2
    ],
  }
}

/** Spec with many widgets to test paging. */
function makeLargeSpec(n = 10) {
  const widgets = []
  for (let i = 0; i < n; i++) {
    widgets.push({
      id: `w${i}`,
      type: 'kpi',
      pos: { x: (i % 4) * 3 + 1, y: Math.floor(i / 4) * 4 + 1, w: 3, h: 4 },
    })
  }
  return { layout: { cols: 12 }, widgets }
}

/** Spec with no positioned widgets (filter-only). */
function makeEmptySpec() {
  return {
    layout: { cols: 12 },
    widgets: [
      { id: 'f1', type: 'filter' },  // no pos
    ],
  }
}

// ---------------------------------------------------------------------------
// canGenerate* / hasContent helpers
// ---------------------------------------------------------------------------

describe('canGenerateReport / canGenerateSlides', () => {
  test('returns true when there are positioned widgets', () => {
    const spec = makeGridSpec()
    assert.equal(canGenerateReport(spec), true)
    assert.equal(canGenerateSlides(spec), true)
  })

  test('returns false when no widgets have positions', () => {
    const spec = makeEmptySpec()
    assert.equal(canGenerateReport(spec), false)
    assert.equal(canGenerateSlides(spec), false)
  })

  test('returns false for empty widget list', () => {
    const spec = { layout: { cols: 12 }, widgets: [] }
    assert.equal(canGenerateReport(spec), false)
    assert.equal(canGenerateSlides(spec), false)
  })
})

describe('reportHasContent / slidesHasContent', () => {
  test('false on fresh spec', () => {
    const spec = makeGridSpec()
    assert.equal(reportHasContent(spec), false)
    assert.equal(slidesHasContent(spec), false)
  })

  test('true after generating report', () => {
    const spec = gridSpecToReport(makeGridSpec())
    assert.equal(reportHasContent(spec), true)
  })

  test('true after generating slides', () => {
    const spec = gridSpecToSlides(makeGridSpec())
    assert.equal(slidesHasContent(spec), true)
  })

  test('false when pages exist but have no items', () => {
    const spec = {
      ...makeGridSpec(),
      surfaces: { report: { pages: [{ id: 'p1', items: [], page_break: false }] } },
    }
    assert.equal(reportHasContent(spec), false)
  })

  test('false when slides exist but have no items', () => {
    const spec = {
      ...makeGridSpec(),
      surfaces: { slides: { aspect: '16:9', slides: [{ id: 's1', notes: null, items: [] }] } },
    }
    assert.equal(slidesHasContent(spec), false)
  })
})

// ---------------------------------------------------------------------------
// gridSpecToReport
// ---------------------------------------------------------------------------

describe('gridSpecToReport — basic generation', () => {
  test('returns a new spec (does not mutate original)', () => {
    const spec = makeGridSpec()
    const next = gridSpecToReport(spec)
    assert.notStrictEqual(next, spec)
    assert.equal(spec.surfaces, undefined)
  })

  test('populates surfaces.report with pages', () => {
    const next = gridSpecToReport(makeGridSpec())
    const report = getReportSurface(next)
    assert.ok(Array.isArray(report.pages))
    assert.ok(report.pages.length > 0)
  })

  test('all widget ids from the spec appear exactly once across all pages', () => {
    const spec = makeGridSpec()
    const next = gridSpecToReport(spec)
    const report = getReportSurface(next)
    const allWidgetIds = report.pages.flatMap(p => p.items.map(i => i.widgetId))
    const positionedIds = spec.widgets.filter(w => w.pos).map(w => w.id)
    assert.deepEqual([...allWidgetIds].sort(), [...positionedIds].sort())
  })

  test('items reference valid widget ids (all in spec.widgets)', () => {
    const spec = makeGridSpec()
    const next = gridSpecToReport(spec)
    const report = getReportSurface(next)
    const specIds = new Set(spec.widgets.map(w => w.id))
    for (const page of report.pages) {
      for (const item of page.items) {
        assert.ok(specIds.has(item.widgetId), `Unknown widgetId: ${item.widgetId}`)
      }
    }
  })

  test('width hints are one of full/half/third', () => {
    const next = gridSpecToReport(makeGridSpec())
    const report = getReportSurface(next)
    const valid = new Set(['full', 'half', 'third'])
    for (const page of report.pages) {
      for (const item of page.items) {
        assert.ok(valid.has(item.width), `Bad width: ${item.width}`)
      }
    }
  })

  test('wide widget (w=8) gets full width hint', () => {
    const next = gridSpecToReport(makeGridSpec())
    const report = getReportSurface(next)
    const allItems = report.pages.flatMap(p => p.items)
    const chart1Item = allItems.find(i => i.widgetId === 'chart1')
    assert.equal(chart1Item.width, 'full')
  })

  test('narrow widget (w=4) gets third width hint', () => {
    const next = gridSpecToReport(makeGridSpec())
    const report = getReportSurface(next)
    const allItems = report.pages.flatMap(p => p.items)
    const kpi1Item = allItems.find(i => i.widgetId === 'kpi1')
    assert.equal(kpi1Item.width, 'third')
  })

  test('half-width widget (w=6) gets half width hint', () => {
    const next = gridSpecToReport(makeGridSpec())
    const report = getReportSurface(next)
    const allItems = report.pages.flatMap(p => p.items)
    const tableItem = allItems.find(i => i.widgetId === 'table1')
    assert.equal(tableItem.width, 'half')
  })

  test('only first page has page_break=false; all subsequent pages have page_break=true', () => {
    const spec = makeLargeSpec(REPORT_PAGE_SIZE + 1)
    const next = gridSpecToReport(spec)
    const report = getReportSurface(next)
    assert.ok(report.pages.length >= 2)
    assert.equal(report.pages[0].page_break, false)
    for (let i = 1; i < report.pages.length; i++) {
      assert.equal(report.pages[i].page_break, true)
    }
  })

  test('pages have stable ids (strings, non-empty)', () => {
    const next = gridSpecToReport(makeGridSpec())
    const report = getReportSurface(next)
    for (const page of report.pages) {
      assert.ok(typeof page.id === 'string' && page.id.length > 0)
    }
  })

  test('page ids are unique within the report', () => {
    const spec = makeLargeSpec(REPORT_PAGE_SIZE * 2)
    const next = gridSpecToReport(spec)
    const report = getReportSurface(next)
    const ids = report.pages.map(p => p.id)
    assert.equal(new Set(ids).size, ids.length)
  })
})

describe('gridSpecToReport — pagination', () => {
  test(`${REPORT_PAGE_SIZE + 1} widgets → 2 pages`, () => {
    const spec = makeLargeSpec(REPORT_PAGE_SIZE + 1)
    const next = gridSpecToReport(spec)
    const report = getReportSurface(next)
    assert.equal(report.pages.length, 2)
  })

  test(`${REPORT_PAGE_SIZE} widgets → 1 page`, () => {
    const spec = makeLargeSpec(REPORT_PAGE_SIZE)
    const next = gridSpecToReport(spec)
    const report = getReportSurface(next)
    assert.equal(report.pages.length, 1)
  })

  test('custom pageSize is honoured', () => {
    const spec = makeLargeSpec(9)
    const next = gridSpecToReport(spec, { pageSize: 3 })
    const report = getReportSurface(next)
    assert.equal(report.pages.length, 3)
  })
})

describe('gridSpecToReport — sort order', () => {
  test('widgets appear in top-to-bottom, left-to-right reading order', () => {
    const spec = makeGridSpec()
    const next = gridSpecToReport(spec)
    const report = getReportSurface(next)
    const orderedIds = report.pages.flatMap(p => p.items.map(i => i.widgetId))
    // chart1 (y=1,x=1) → kpi1 (y=1,x=9) → table1 (y=7,x=1)
    assert.deepEqual(orderedIds, ['chart1', 'kpi1', 'table1'])
  })
})

describe('gridSpecToReport — idempotency / force', () => {
  test('returns same spec if report already has content and force=false', () => {
    const first = gridSpecToReport(makeGridSpec())
    const second = gridSpecToReport(first)  // default force=false
    assert.strictEqual(first, second)
  })

  test('force=true overwrites existing content', () => {
    const first = gridSpecToReport(makeGridSpec())
    const second = gridSpecToReport(first, { force: true })
    // should be a new object with the same shape
    assert.notStrictEqual(first, second)
    const report = getReportSurface(second)
    assert.ok(report.pages.length > 0)
  })

  test('empty positioned widget list → surfaces.report with empty pages', () => {
    const spec = makeEmptySpec()
    const next = gridSpecToReport(spec)
    const report = getReportSurface(next)
    assert.deepEqual(report.pages, [])
  })

  test('widgets without pos are excluded from report', () => {
    const spec = makeEmptySpec()
    const next = gridSpecToReport(spec, { force: true })
    const report = getReportSurface(next)
    assert.equal(report.pages.flatMap(p => p.items).length, 0)
  })
})

describe('gridSpecToReport — grid surface unaffected', () => {
  test('grid layout unchanged after generating report', () => {
    const spec = makeGridSpec()
    const next = gridSpecToReport(spec)
    // widgets array untouched
    assert.deepEqual(next.widgets, spec.widgets)
    // no responsive side-effects
    assert.equal(next.responsive, spec.responsive)
  })
})

// ---------------------------------------------------------------------------
// gridSpecToSlides
// ---------------------------------------------------------------------------

describe('gridSpecToSlides — basic generation', () => {
  test('returns a new spec (does not mutate original)', () => {
    const spec = makeGridSpec()
    const next = gridSpecToSlides(spec)
    assert.notStrictEqual(next, spec)
    assert.equal(spec.surfaces, undefined)
  })

  test('populates surfaces.slides with slides', () => {
    const next = gridSpecToSlides(makeGridSpec())
    const slides = getSlidesSurface(next)
    assert.ok(Array.isArray(slides.slides))
    assert.ok(slides.slides.length > 0)
  })

  test('default mode: one widget per slide', () => {
    const spec = makeGridSpec()
    const positionedCount = spec.widgets.filter(w => w.pos).length
    const next = gridSpecToSlides(spec)
    const slides = getSlidesSurface(next)
    assert.equal(slides.slides.length, positionedCount)
  })

  test('all widget ids from the spec appear exactly once across all slides', () => {
    const spec = makeGridSpec()
    const next = gridSpecToSlides(spec)
    const surface = getSlidesSurface(next)
    const allWidgetIds = surface.slides.flatMap(s => s.items.map(i => i.widgetId))
    const positionedIds = spec.widgets.filter(w => w.pos).map(w => w.id)
    assert.deepEqual([...allWidgetIds].sort(), [...positionedIds].sort())
  })

  test('items reference valid widget ids', () => {
    const spec = makeGridSpec()
    const next = gridSpecToSlides(spec)
    const surface = getSlidesSurface(next)
    const specIds = new Set(spec.widgets.map(w => w.id))
    for (const slide of surface.slides) {
      for (const item of slide.items) {
        assert.ok(specIds.has(item.widgetId), `Unknown widgetId: ${item.widgetId}`)
      }
    }
  })

  test('slide items have x/y/w/h in 0–100 range', () => {
    const next = gridSpecToSlides(makeGridSpec())
    const surface = getSlidesSurface(next)
    for (const slide of surface.slides) {
      for (const item of slide.items) {
        assert.ok(item.x >= 0 && item.x <= 100, `x out of range: ${item.x}`)
        assert.ok(item.y >= 0 && item.y <= 100, `y out of range: ${item.y}`)
        assert.ok(item.w > 0 && item.w <= 100, `w out of range: ${item.w}`)
        assert.ok(item.h > 0 && item.h <= 100, `h out of range: ${item.h}`)
      }
    }
  })

  test('default aspect is 16:9', () => {
    const next = gridSpecToSlides(makeGridSpec())
    const surface = getSlidesSurface(next)
    assert.equal(surface.aspect, '16:9')
  })

  test('options.aspect overrides default', () => {
    const next = gridSpecToSlides(makeGridSpec(), { aspect: '4:3' })
    const surface = getSlidesSurface(next)
    assert.equal(surface.aspect, '4:3')
  })

  test('single-widget slides are centered (x=5, y=10, w=90, h=80)', () => {
    const next = gridSpecToSlides(makeGridSpec())
    const surface = getSlidesSurface(next)
    for (const slide of surface.slides) {
      assert.equal(slide.items.length, 1)
      const item = slide.items[0]
      assert.equal(item.x, 5)
      assert.equal(item.y, 10)
      assert.equal(item.w, 90)
      assert.equal(item.h, 80)
    }
  })

  test('slides have stable ids (strings, non-empty)', () => {
    const next = gridSpecToSlides(makeGridSpec())
    const surface = getSlidesSurface(next)
    for (const slide of surface.slides) {
      assert.ok(typeof slide.id === 'string' && slide.id.length > 0)
    }
  })

  test('slide ids are unique', () => {
    const spec = makeLargeSpec(8)
    const next = gridSpecToSlides(spec)
    const surface = getSlidesSurface(next)
    const ids = surface.slides.map(s => s.id)
    assert.equal(new Set(ids).size, ids.length)
  })

  test('slides have notes: null by default', () => {
    const next = gridSpecToSlides(makeGridSpec())
    const surface = getSlidesSurface(next)
    for (const slide of surface.slides) {
      assert.equal(slide.notes, null)
    }
  })
})

describe('gridSpecToSlides — groupRows mode', () => {
  test('groupRows=true: widgets in the same row band share a slide', () => {
    // chart1 (y=1) and kpi1 (y=1) share row 1 → same slide
    // table1 (y=7) is in a separate row band → different slide
    const spec = makeGridSpec()
    const next = gridSpecToSlides(spec, { groupRows: true })
    const surface = getSlidesSurface(next)
    // We expect 2 slides: one for row-1 widgets, one for row-7 widget
    assert.equal(surface.slides.length, 2)
    const firstSlide = surface.slides[0]
    const firstWidgetIds = firstSlide.items.map(i => i.widgetId).sort()
    assert.deepEqual(firstWidgetIds, ['chart1', 'kpi1'])
    const secondSlide = surface.slides[1]
    assert.equal(secondSlide.items[0].widgetId, 'table1')
  })

  test('groupRows: items have x/y/w/h in 0-100 range', () => {
    const next = gridSpecToSlides(makeGridSpec(), { groupRows: true })
    const surface = getSlidesSurface(next)
    for (const slide of surface.slides) {
      for (const item of slide.items) {
        assert.ok(item.x >= 0 && item.x <= 100)
        assert.ok(item.y >= 0 && item.y <= 100)
        assert.ok(item.w > 0 && item.w <= 100)
        assert.ok(item.h > 0 && item.h <= 100)
      }
    }
  })

  test('groupRows: SLIDE_MAX_ITEMS cap per slide', () => {
    const spec = makeLargeSpec(SLIDE_MAX_ITEMS + 1)
    const next = gridSpecToSlides(spec, { groupRows: true })
    const surface = getSlidesSurface(next)
    for (const slide of surface.slides) {
      assert.ok(slide.items.length <= SLIDE_MAX_ITEMS)
    }
  })

  test('groupRows: all widgets still appear', () => {
    const spec = makeLargeSpec(8)
    const next = gridSpecToSlides(spec, { groupRows: true })
    const surface = getSlidesSurface(next)
    const allWidgetIds = surface.slides.flatMap(s => s.items.map(i => i.widgetId))
    const positionedIds = spec.widgets.filter(w => w.pos).map(w => w.id)
    assert.deepEqual([...allWidgetIds].sort(), [...positionedIds].sort())
  })
})

describe('gridSpecToSlides — sort order', () => {
  test('slides appear in top-to-bottom, left-to-right order', () => {
    const spec = makeGridSpec()
    const next = gridSpecToSlides(spec)
    const surface = getSlidesSurface(next)
    const orderedIds = surface.slides.flatMap(s => s.items.map(i => i.widgetId))
    // chart1 (y=1,x=1) → kpi1 (y=1,x=9) → table1 (y=7,x=1)
    assert.deepEqual(orderedIds, ['chart1', 'kpi1', 'table1'])
  })
})

describe('gridSpecToSlides — idempotency / force', () => {
  test('returns same spec if slides already have content and force=false', () => {
    const first = gridSpecToSlides(makeGridSpec())
    const second = gridSpecToSlides(first)  // default force=false
    assert.strictEqual(first, second)
  })

  test('force=true overwrites existing slides', () => {
    const first = gridSpecToSlides(makeGridSpec())
    const second = gridSpecToSlides(first, { force: true })
    assert.notStrictEqual(first, second)
    const surface = getSlidesSurface(second)
    assert.ok(surface.slides.length > 0)
  })

  test('empty positioned widget list → surfaces.slides with empty slides', () => {
    const spec = makeEmptySpec()
    const next = gridSpecToSlides(spec)
    const surface = getSlidesSurface(next)
    assert.deepEqual(surface.slides, [])
  })
})

describe('gridSpecToSlides — grid surface unaffected', () => {
  test('grid layout unchanged after generating slides', () => {
    const spec = makeGridSpec()
    const next = gridSpecToSlides(spec)
    assert.deepEqual(next.widgets, spec.widgets)
    assert.equal(next.responsive, spec.responsive)
  })
})

// ---------------------------------------------------------------------------
// Cross-surface: grid unaffected by both conversions
// ---------------------------------------------------------------------------

describe('T11 cross-surface: grid surface unaffected by conversions', () => {
  test('generate both report and slides; grid layout still correct', () => {
    const spec = makeGridSpec()
    let next = gridSpecToReport(spec)
    next = gridSpecToSlides(next)

    // Grid layout from widget.pos still works identically.
    const posA = next.widgets.find(w => w.id === 'chart1').pos
    assert.deepEqual(posA, spec.widgets.find(w => w.id === 'chart1').pos)

    // Report still has pages.
    assert.ok(getReportSurface(next).pages.length > 0)
    // Slides still have slides.
    assert.ok(getSlidesSurface(next).slides.length > 0)
  })

  test('widgets referenced in report/slides exist in spec.widgets', () => {
    const spec = makeGridSpec()
    let next = gridSpecToReport(spec)
    next = gridSpecToSlides(next)

    const allIds = new Set(next.widgets.map(w => w.id))

    for (const page of getReportSurface(next).pages) {
      for (const item of page.items) {
        assert.ok(allIds.has(item.widgetId))
      }
    }
    for (const slide of getSlidesSurface(next).slides) {
      for (const item of slide.items) {
        assert.ok(allIds.has(item.widgetId))
      }
    }
  })
})

// ---------------------------------------------------------------------------
// T11 — surfaces.grid (migrated boards) as the source
// ---------------------------------------------------------------------------

describe('T11 — generates from surfaces.grid when present', () => {
  test('gridSpecToReport uses surfaces.grid positions when available', () => {
    const spec = {
      layout: { cols: 12 },
      widgets: [
        { id: 'w1', type: 'kpi', pos: { x: 1, y: 1, w: 3, h: 3 } },
        { id: 'w2', type: 'chart', pos: { x: 4, y: 1, w: 9, h: 6 } },
      ],
      surfaces: {
        grid: {
          w1: { x: 1, y: 1, w: 3, h: 3 },
          w2: { x: 4, y: 1, w: 9, h: 6 },
        },
      },
    }
    const next = gridSpecToReport(spec)
    const report = getReportSurface(next)
    const allIds = report.pages.flatMap(p => p.items.map(i => i.widgetId))
    assert.deepEqual([...allIds].sort(), ['w1', 'w2'])
    // w2 is wide (w=9) → full
    const w2Item = report.pages.flatMap(p => p.items).find(i => i.widgetId === 'w2')
    assert.equal(w2Item.width, 'full')
  })

  test('gridSpecToSlides uses surfaces.grid positions when available', () => {
    const spec = {
      layout: { cols: 12 },
      widgets: [
        { id: 'w1', type: 'kpi', pos: { x: 1, y: 1, w: 3, h: 3 } },
        { id: 'w2', type: 'chart', pos: { x: 4, y: 1, w: 9, h: 6 } },
      ],
      surfaces: {
        grid: {
          w1: { x: 1, y: 1, w: 3, h: 3 },
          w2: { x: 4, y: 1, w: 9, h: 6 },
        },
      },
    }
    const next = gridSpecToSlides(spec)
    const surface = getSlidesSurface(next)
    const allIds = surface.slides.flatMap(s => s.items.map(i => i.widgetId))
    assert.deepEqual([...allIds].sort(), ['w1', 'w2'])
  })
})
