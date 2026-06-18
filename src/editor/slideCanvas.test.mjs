/**
 * slideCanvas.test.mjs — T10 unit tests for slide surface logic.
 *
 * Tests:
 *   1. addSlide / removeSlide / reorderSlides via responsiveLayout helpers
 *   2. updateSlideWidgetPos normalizes positions correctly
 *   3. Coordinate helpers (normToPixel / pixelToNorm round-trips)
 *   4. Multiple slides with items
 *
 * Run: node --test src/editor/slideCanvas.test.mjs
 * (or npm run test:dash which runs 'node --test src/**\/\*.test.mjs')
 */

import { test, describe } from 'node:test'
import assert from 'node:assert/strict'

import {
  emptySlidesSurface,
  getSlidesSurface,
  setSlidesSurface,
  addSlide,
  removeSlide,
  reorderSlides,
  updateSlide,
  updateSlideWidgetPos,
  SLIDES_DEFAULT_ASPECT,
} from '../dashboards/responsiveLayout.js'

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function makeSpec(overrides = {}) {
  return {
    version: 1,
    title: 'Test',
    layout: { cols: 12 },
    variables: [],
    widgets: [
      { id: 'w1', type: 'kpi', query_id: 'q1', pos: { x: 1, y: 1, w: 3, h: 3 } },
      { id: 'w2', type: 'chart', query_id: 'q1', pos: { x: 4, y: 1, w: 6, h: 5 } },
    ],
    ...overrides,
  }
}

function normToPixel(normValue, canvasSize) {
  return (normValue / 100) * canvasSize
}

function pixelToNorm(pixelDelta, canvasSize) {
  return (pixelDelta / canvasSize) * 100
}

// ---------------------------------------------------------------------------
// T10 — Slide surface state (via responsiveLayout accessors)
// ---------------------------------------------------------------------------

describe('T10 — slide canvas unit tests', () => {

  // ── Slide CRUD ──────────────────────────────────────────────────────────────

  test('addSlide appends a slide and spec is immutable', () => {
    const spec = makeSpec()
    const next = addSlide(spec, { id: 'slide1' })
    assert.notEqual(next, spec)                     // new object
    assert.equal(next.surfaces.slides.slides.length, 1)
    assert.equal(next.surfaces.slides.slides[0].id, 'slide1')
    assert.deepEqual(next.surfaces.slides.slides[0].items, [])
    assert.equal(next.surfaces.slides.slides[0].notes, null)
  })

  test('addSlide preserves existing slides', () => {
    let spec = makeSpec()
    spec = addSlide(spec, { id: 's1' })
    spec = addSlide(spec, { id: 's2' })
    spec = addSlide(spec, { id: 's3' })
    assert.equal(spec.surfaces.slides.slides.length, 3)
    assert.deepEqual(
      spec.surfaces.slides.slides.map(s => s.id),
      ['s1', 's2', 's3'],
    )
  })

  test('removeSlide removes by id and leaves others intact', () => {
    let spec = makeSpec()
    spec = addSlide(spec, { id: 's1' })
    spec = addSlide(spec, { id: 's2' })
    spec = addSlide(spec, { id: 's3' })

    const next = removeSlide(spec, 's2')
    assert.equal(next.surfaces.slides.slides.length, 2)
    assert.deepEqual(next.surfaces.slides.slides.map(s => s.id), ['s1', 's3'])
    // original spec unchanged
    assert.equal(spec.surfaces.slides.slides.length, 3)
  })

  test('removeSlide on missing id is a no-op', () => {
    let spec = makeSpec()
    spec = addSlide(spec, { id: 's1' })
    const next = removeSlide(spec, 'nonexistent')
    assert.equal(next.surfaces.slides.slides.length, 1)
  })

  // ── Reorder ─────────────────────────────────────────────────────────────────

  test('reorderSlides reorders by orderedIds', () => {
    let spec = makeSpec()
    spec = addSlide(spec, { id: 'A' })
    spec = addSlide(spec, { id: 'B' })
    spec = addSlide(spec, { id: 'C' })

    const reordered = reorderSlides(spec, ['C', 'A', 'B'])
    assert.deepEqual(
      reordered.surfaces.slides.slides.map(s => s.id),
      ['C', 'A', 'B'],
    )
    // original order unchanged
    assert.deepEqual(spec.surfaces.slides.slides.map(s => s.id), ['A', 'B', 'C'])
  })

  test('reorderSlides with partial ids only keeps matched slides', () => {
    let spec = makeSpec()
    spec = addSlide(spec, { id: 'A' })
    spec = addSlide(spec, { id: 'B' })
    spec = addSlide(spec, { id: 'C' })

    // Drop 'C' from the ordered list (simulate deletion race)
    const reordered = reorderSlides(spec, ['B', 'A'])
    assert.deepEqual(
      reordered.surfaces.slides.slides.map(s => s.id),
      ['B', 'A'],
    )
  })

  // ── Widget positioning ───────────────────────────────────────────────────────

  test('updateSlide sets items on a slide', () => {
    let spec = makeSpec()
    spec = addSlide(spec, { id: 's1' })

    const items = [
      { widgetId: 'w1', x: 10, y: 10, w: 40, h: 30 },
      { widgetId: 'w2', x: 55, y: 10, w: 40, h: 50 },
    ]
    const next = updateSlide(spec, 's1', { items })
    const slide = next.surfaces.slides.slides.find(s => s.id === 's1')
    assert.equal(slide.items.length, 2)
    assert.equal(slide.items[0].widgetId, 'w1')
    assert.equal(slide.items[0].x, 10)
    assert.equal(slide.items[1].x, 55)
  })

  test('updateSlideWidgetPos updates item position in normalized space', () => {
    let spec = makeSpec()
    spec = addSlide(spec, {
      id: 's1',
      items: [
        { widgetId: 'w1', x: 0, y: 0, w: 50, h: 50 },
        { widgetId: 'w2', x: 55, y: 0, w: 40, h: 40 },
      ],
    })

    // Move w1 to new position
    const next = updateSlideWidgetPos(spec, 's1', 'w1', { x: 20, y: 30, w: 45, h: 35 })
    const slide = next.surfaces.slides.slides.find(s => s.id === 's1')
    const item = slide.items.find(i => i.widgetId === 'w1')
    assert.equal(item.x, 20)
    assert.equal(item.y, 30)
    assert.equal(item.w, 45)
    assert.equal(item.h, 35)

    // w2 should be unchanged
    const w2 = slide.items.find(i => i.widgetId === 'w2')
    assert.equal(w2.x, 55)
  })

  test('updateSlideWidgetPos on missing widgetId is a no-op for that item', () => {
    let spec = makeSpec()
    spec = addSlide(spec, {
      id: 's1',
      items: [{ widgetId: 'w1', x: 10, y: 10, w: 30, h: 30 }],
    })
    const next = updateSlideWidgetPos(spec, 's1', 'nonexistent', { x: 99, y: 99, w: 10, h: 10 })
    const slide = next.surfaces.slides.slides.find(s => s.id === 's1')
    assert.equal(slide.items.length, 1)
    assert.equal(slide.items[0].x, 10) // unchanged
  })

  test('updateSlideWidgetPos on missing slideId leaves spec unchanged', () => {
    let spec = makeSpec()
    spec = addSlide(spec, { id: 's1', items: [] })
    const next = updateSlideWidgetPos(spec, 'nonexistent', 'w1', { x: 5, y: 5, w: 30, h: 30 })
    // slides unchanged
    assert.deepEqual(next.surfaces.slides, spec.surfaces.slides)
  })

  // ── Notes ───────────────────────────────────────────────────────────────────

  test('updateSlide patches speaker notes', () => {
    let spec = makeSpec()
    spec = addSlide(spec, { id: 's1', notes: null })
    const next = updateSlide(spec, 's1', { notes: 'Remember to **pause** here.' })
    const slide = next.surfaces.slides.slides.find(s => s.id === 's1')
    assert.equal(slide.notes, 'Remember to **pause** here.')
  })

  // ── Coordinate helpers ───────────────────────────────────────────────────────

  test('normToPixel correctly converts normalized to pixel coords', () => {
    // 50% of 800px canvas = 400px
    assert.equal(normToPixel(50, 800), 400)
    assert.equal(normToPixel(0, 800), 0)
    assert.equal(normToPixel(100, 800), 800)
    assert.equal(normToPixel(25, 1024), 256)
  })

  test('pixelToNorm correctly converts pixel delta to normalized', () => {
    // 400px / 800px = 50 (normalized)
    assert.equal(pixelToNorm(400, 800), 50)
    assert.equal(pixelToNorm(0, 1000), 0)
    assert.equal(pixelToNorm(1024, 1024), 100)
  })

  test('normToPixel and pixelToNorm are inverses', () => {
    const canvasW = 960
    const canvasH = 540

    const origX = 33
    const origY = 17
    const dragPxX = normToPixel(20, canvasW)   // 20 normalized units in pixels
    const dragPxY = normToPixel(15, canvasH)

    const dxNorm = pixelToNorm(dragPxX, canvasW)
    const dyNorm = pixelToNorm(dragPxY, canvasH)

    assert.ok(Math.abs(dxNorm - 20) < 0.001, `dxNorm=${dxNorm} expected ~20`)
    assert.ok(Math.abs(dyNorm - 15) < 0.001, `dyNorm=${dyNorm} expected ~15`)
  })

  // ── Aspect ratio ─────────────────────────────────────────────────────────────

  test('default slides surface has aspect 16:9', () => {
    const spec = makeSpec()
    const surface = getSlidesSurface(spec)
    assert.equal(surface.aspect, SLIDES_DEFAULT_ASPECT)
    assert.equal(surface.aspect, '16:9')
  })

  test('can set 4:3 aspect ratio', () => {
    const spec = makeSpec()
    const surface = { aspect: '4:3', slides: [] }
    const next = setSlidesSurface(spec, surface)
    assert.equal(next.surfaces.slides.aspect, '4:3')
  })

  // ── Multi-slide deck scenario ─────────────────────────────────────────────────

  test('a 3-slide deck with widgets can be built incrementally', () => {
    let spec = makeSpec()

    spec = addSlide(spec, {
      id: 'title_slide',
      items: [{ widgetId: 'w1', x: 10, y: 20, w: 80, h: 30 }],
    })
    spec = addSlide(spec, {
      id: 'data_slide',
      items: [
        { widgetId: 'w1', x: 5, y: 5, w: 40, h: 40 },
        { widgetId: 'w2', x: 50, y: 5, w: 45, h: 80 },
      ],
    })
    spec = addSlide(spec, {
      id: 'summary_slide',
      notes: 'Wrap up',
      items: [{ widgetId: 'w2', x: 20, y: 30, w: 60, h: 50 }],
    })

    const surface = getSlidesSurface(spec)
    assert.equal(surface.slides.length, 3)

    const title = surface.slides.find(s => s.id === 'title_slide')
    assert.equal(title.items[0].widgetId, 'w1')

    const data = surface.slides.find(s => s.id === 'data_slide')
    assert.equal(data.items.length, 2)

    const summary = surface.slides.find(s => s.id === 'summary_slide')
    assert.equal(summary.notes, 'Wrap up')

    // Reorder: put data first
    spec = reorderSlides(spec, ['data_slide', 'title_slide', 'summary_slide'])
    const reordered = getSlidesSurface(spec)
    assert.equal(reordered.slides[0].id, 'data_slide')
    assert.equal(reordered.slides[1].id, 'title_slide')

    // Move widget in data slide
    spec = updateSlideWidgetPos(spec, 'data_slide', 'w2', { x: 55, y: 10, w: 40, h: 85 })
    const moved = getSlidesSurface(spec)
    const movedItem = moved.slides.find(s => s.id === 'data_slide').items.find(i => i.widgetId === 'w2')
    assert.equal(movedItem.y, 10)
    assert.equal(movedItem.h, 85)
  })
})
