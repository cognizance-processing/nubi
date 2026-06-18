/**
 * surfaceGenerators.js — T11 surface conversion generators.
 *
 * Pure functions that derive a report or slides surface from a grid spec.
 * They never mutate; they return a new spec via the responsiveLayout writers.
 *
 * Grid → Report  (gridSpecToReport)
 * ----------------------------------
 * Widgets are sorted top-to-bottom, then left-to-right (reading order).
 * They are distributed across pages: each page holds up to REPORT_PAGE_SIZE
 * widgets. Width hints follow widget dimensions:
 *   w >= 8 → 'full'
 *   w >= 5 → 'half'
 *   w <= 4 → 'third'   (charts / KPIs in a tight grid)
 * page_break is set to true on every page after the first.
 *
 * Grid → Slides  (gridSpecToSlides)
 * -----------------------------------
 * Widgets are sorted top-to-bottom, then left-to-right. Each widget (or
 * logical group of widgets sharing the same grid row band) becomes one slide.
 * In SINGLE mode (default): one widget per slide, centered.
 * In GROUP mode (opt-in via options.groupRows): widgets whose tops overlap
 * within SLIDE_ROW_BAND grid rows are co-placed on the same slide.
 *
 * Widget items use the 0-100 normalized coordinate space. At most
 * SLIDE_MAX_ITEMS widgets are placed per slide to keep things legible.
 *
 * Idempotency options
 * --------------------
 * Both generators accept `options.force = true` to unconditionally overwrite.
 * Without force, if the target surface already has content the function returns
 * the spec unchanged (the caller should confirm before overwriting).
 *
 * Exported API
 * -------------
 *   gridSpecToReport(spec, options?) → newSpec | spec (unchanged)
 *   gridSpecToSlides(spec, options?) → newSpec | spec (unchanged)
 *   canGenerateReport(spec)  → boolean
 *   canGenerateSlides(spec)  → boolean
 *   reportHasContent(spec)   → boolean
 *   slidesHasContent(spec)   → boolean
 */

import {
  effectiveWidgetPos,
  setReportSurface,
  setSlidesSurface,
  SLIDES_DEFAULT_ASPECT,
} from './responsiveLayout.js'

// ---------------------------------------------------------------------------
// Tuneable constants
// ---------------------------------------------------------------------------

/** Max widgets per generated report page. */
export const REPORT_PAGE_SIZE = 6

/** Max widgets per generated slide. */
export const SLIDE_MAX_ITEMS = 4

/**
 * Grid-row band for grouping widgets on the same slide in GROUP mode.
 * Widgets whose y positions differ by less than this value are considered
 * to be in the same "row band".
 */
export const SLIDE_ROW_BAND = 3

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/**
 * True if there are widgets in the spec that have grid positions and can be
 * meaningfully converted to a different surface.
 * @param {object} spec
 * @returns {boolean}
 */
export function canGenerateReport(spec) {
  return _positionedWidgets(spec).length > 0
}

/**
 * @param {object} spec
 * @returns {boolean}
 */
export function canGenerateSlides(spec) {
  return _positionedWidgets(spec).length > 0
}

/**
 * True if spec.surfaces.report already has at least one page with items.
 * @param {object} spec
 * @returns {boolean}
 */
export function reportHasContent(spec) {
  const pages = spec?.surfaces?.report?.pages ?? []
  return pages.length > 0 && pages.some(p => p.items?.length > 0)
}

/**
 * True if spec.surfaces.slides already has at least one slide with items.
 * @param {object} spec
 * @returns {boolean}
 */
export function slidesHasContent(spec) {
  const slides = spec?.surfaces?.slides?.slides ?? []
  return slides.length > 0 && slides.some(s => s.items?.length > 0)
}

/**
 * Widgets that have a grid position (via effectiveWidgetPos), sorted in
 * natural reading order: primary sort y (top-to-bottom), secondary sort x
 * (left-to-right).
 * @param {object} spec
 * @returns {Array<{widget, pos}>}
 */
function _positionedWidgets(spec) {
  return (spec?.widgets ?? [])
    .map(w => ({ widget: w, pos: effectiveWidgetPos(w, spec) }))
    .filter(({ pos }) => pos != null)
    .sort((a, b) => {
      const dy = (a.pos.y ?? 1) - (b.pos.y ?? 1)
      if (dy !== 0) return dy
      return (a.pos.x ?? 1) - (b.pos.x ?? 1)
    })
}

/**
 * Derive a width hint from a widget's grid width (in column units, 1-based).
 * @param {number} w  – widget column span
 * @returns {'full'|'half'|'third'}
 */
function _widthHint(w) {
  if (w >= 8) return 'full'
  if (w >= 5) return 'half'
  return 'third'
}

/**
 * Stable, deterministic page id (no Date.now so tests are reproducible).
 * Uses the ids of the widgets on that page.
 * @param {number} idx  – 0-based page index
 * @param {string[]} widgetIds
 * @returns {string}
 */
function _pageId(idx, widgetIds) {
  return `gen_page_${idx}_${widgetIds.join('_')}`
}

/**
 * Stable slide id derived from index + widget ids.
 * @param {number} idx
 * @param {string[]} widgetIds
 * @returns {string}
 */
function _slideId(idx, widgetIds) {
  return `gen_slide_${idx}_${widgetIds.join('_')}`
}

// ---------------------------------------------------------------------------
// Grid → Report
// ---------------------------------------------------------------------------

/**
 * Derive a report surface from the current grid layout.
 *
 * @param {object} spec    – Dashboard spec (not mutated).
 * @param {{
 *   force?: boolean,
 *   pageSize?: number,
 * }} [options]
 * @returns {object}  New spec with surfaces.report populated, OR the same
 *                    spec reference when the surface already has content and
 *                    force is not set.
 */
export function gridSpecToReport(spec, options = {}) {
  const { force = false, pageSize = REPORT_PAGE_SIZE } = options

  // Guard: don't overwrite manual work unless force=true.
  if (!force && reportHasContent(spec)) {
    return spec
  }

  const positioned = _positionedWidgets(spec)
  if (positioned.length === 0) {
    // Nothing to convert; emit an empty report surface.
    return setReportSurface(spec, { pages: [] })
  }

  // Chunk widgets into pages.
  const pages = []
  for (let i = 0; i < positioned.length; i += pageSize) {
    const chunk = positioned.slice(i, i + pageSize)
    const widgetIds = chunk.map(({ widget }) => widget.id)
    const items = chunk.map(({ widget, pos }) => ({
      widgetId: widget.id,
      width: _widthHint(pos.w ?? 4),
    }))
    pages.push({
      id: _pageId(pages.length, widgetIds),
      items,
      page_break: pages.length > 0, // first page: no leading break
    })
  }

  return setReportSurface(spec, { pages })
}

// ---------------------------------------------------------------------------
// Grid → Slides
// ---------------------------------------------------------------------------

/**
 * Map a 1-based {x,y,w,h} grid position to the 0–100 normalized slide
 * coordinate space, given the total grid column count (cols).
 *
 * We assume the grid canvas aspect ratio is 16:9 for height mapping.
 * The grid's row height is assumed uniform at `rowH` grid units.
 * We cap the widget height at 80 units to keep it within the slide.
 *
 * For single-widget slides the widget is centered with comfortable padding.
 *
 * @param {{x,y,w,h}} pos        – 1-based grid pos
 * @param {number}    cols        – total grid columns (default 12)
 * @param {number}    totalRows   – approximate total rows in the grid
 * @returns {{x,y,w,h}}          – 0–100 normalized
 */
function _gridPosToSlideItem(pos, cols = 12, totalRows = 1) {
  const xPct = ((pos.x - 1) / cols) * 100
  const wPct = (pos.w / cols) * 100
  const yPct = ((pos.y - 1) / Math.max(totalRows, 1)) * 100
  const hPct = (pos.h / Math.max(totalRows, 1)) * 100

  // Clamp to slide bounds.
  return {
    x: Math.max(0, Math.min(xPct, 95)),
    y: Math.max(0, Math.min(yPct, 90)),
    w: Math.max(5, Math.min(wPct, 100)),
    h: Math.max(5, Math.min(hPct, 90)),
  }
}

/**
 * Layout for a single-widget slide: centered with padding.
 * @returns {{x,y,w,h}}
 */
function _centeredItem() {
  return { x: 5, y: 10, w: 90, h: 80 }
}

/**
 * Derive a slides surface from the current grid layout.
 *
 * Default: one widget per slide, centered.
 * With `options.groupRows = true`: widgets sharing the same row band
 * are co-placed on one slide.
 *
 * @param {object} spec
 * @param {{
 *   force?: boolean,
 *   aspect?: '16:9'|'4:3',
 *   groupRows?: boolean,
 * }} [options]
 * @returns {object}  New spec, or unchanged spec if content exists and !force.
 */
export function gridSpecToSlides(spec, options = {}) {
  const { force = false, aspect = SLIDES_DEFAULT_ASPECT, groupRows = false } = options

  if (!force && slidesHasContent(spec)) {
    return spec
  }

  const positioned = _positionedWidgets(spec)
  if (positioned.length === 0) {
    return setSlidesSurface(spec, { aspect, slides: [] })
  }

  const cols = spec?.layout?.cols ?? 12

  // Compute approximate total grid rows for y-normalization.
  const maxY = positioned.reduce((m, { pos }) => Math.max(m, (pos.y ?? 1) + (pos.h ?? 4) - 1), 1)

  let groups
  if (groupRows) {
    // Group widgets whose y-start is within SLIDE_ROW_BAND of the group's first y.
    groups = []
    let current = null
    for (const entry of positioned) {
      const y = entry.pos.y ?? 1
      if (!current || y - current.startY >= SLIDE_ROW_BAND) {
        current = { startY: y, entries: [] }
        groups.push(current)
      }
      current.entries.push(entry)
      // Split if the group exceeds SLIDE_MAX_ITEMS.
      if (current.entries.length >= SLIDE_MAX_ITEMS) {
        current = null
      }
    }
  } else {
    // One widget per slide.
    groups = positioned.map(entry => ({ startY: entry.pos.y ?? 1, entries: [entry] }))
  }

  const slides = groups.map((group, idx) => {
    const widgetIds = group.entries.map(({ widget }) => widget.id)
    const isSingle = group.entries.length === 1

    const items = group.entries.map(({ widget, pos }) => {
      const normalized = isSingle
        ? _centeredItem()
        : _gridPosToSlideItem(pos, cols, maxY)
      return { widgetId: widget.id, ...normalized }
    })

    return {
      id: _slideId(idx, widgetIds),
      notes: null,
      items,
    }
  })

  return setSlidesSurface(spec, { aspect, slides })
}
