/**
 * specMiniature.test.mjs — Unit tests for the dashboard-card miniature model.
 *
 * Run with:
 *   node --test src/dashboards/specMiniature.test.mjs
 *
 * The miniature is what makes a /dashboards card a real preview of the board,
 * so the contract that matters is TRUTHFULNESS: it must reflect the widgets a
 * viewer would actually see on opening the board (first tab, on-canvas), and
 * must refuse to draw rather than invent geometry it doesn't have.
 */

import { test, describe } from 'node:test'
import assert from 'node:assert/strict'

import {
  buildMiniature,
  widgetKind,
  miniatureWidgets,
  DEFAULT_COLS,
  DEFAULT_ROW_HEIGHT,
  REFERENCE_CANVAS_W,
} from './specMiniature.js'

// Spec positions are ONE-based (see responsiveLayout.posToGridItem, the SSR
// composer, and the backend validator's x,y >= 1 rule). buildMiniature emits
// ZERO-based drawing coords, so these helpers keep the distinction explicit.
const at = (x, y, w, h) => ({ pos: { x, y, w, h } })
const chart = (chart_type, pos) => ({ id: `c_${chart_type}`, type: 'chart', chart_type, ...pos })

describe('widgetKind', () => {
  test('maps non-chart widget types to their glyphs', () => {
    assert.equal(widgetKind({ type: 'kpi' }), 'kpi')
    assert.equal(widgetKind({ type: 'metric' }), 'kpi')
    assert.equal(widgetKind({ type: 'table' }), 'table')
    assert.equal(widgetKind({ type: 'pivot' }), 'table')
    assert.equal(widgetKind({ type: 'filter' }), 'filter')
    assert.equal(widgetKind({ type: 'text' }), 'text')
    assert.equal(widgetKind({ type: 'section' }), 'heading')
  })

  test('collapses chart types by silhouette', () => {
    assert.equal(widgetKind({ type: 'chart', chart_type: 'line' }), 'line')
    assert.equal(widgetKind({ type: 'chart', chart_type: 'fan' }), 'line')
    assert.equal(widgetKind({ type: 'chart', chart_type: 'area' }), 'area')
    assert.equal(widgetKind({ type: 'chart', chart_type: 'scatter' }), 'points')
    assert.equal(widgetKind({ type: 'chart', chart_type: 'bubble' }), 'points')
    assert.equal(widgetKind({ type: 'chart', chart_type: 'pie' }), 'circle')
    assert.equal(widgetKind({ type: 'chart', chart_type: 'donut' }), 'circle')
    assert.equal(widgetKind({ type: 'chart', chart_type: 'gauge' }), 'circle')
    assert.equal(widgetKind({ type: 'chart', chart_type: 'radar' }), 'circle')
    assert.equal(widgetKind({ type: 'chart', chart_type: 'bar' }), 'bars')
  })

  test('unknown and future chart types fall back to bars, never vanish', () => {
    assert.equal(widgetKind({ type: 'chart', chart_type: 'sankey' }), 'bars')
    assert.equal(widgetKind({ type: 'chart', chart_type: 'some_future_type' }), 'bars')
    assert.equal(widgetKind({ type: 'chart', chart_type: null }), 'bars')
    assert.equal(widgetKind({}), 'bars')
  })
})

describe('miniatureWidgets', () => {
  test('with no tabs, every grid widget is included', () => {
    const spec = { widgets: [{ id: 'a', type: 'kpi' }, { id: 'b', type: 'chart' }] }
    const { grid } = miniatureWidgets(spec)
    assert.deepEqual(grid.map(w => w.id), ['a', 'b'])
  })

  test('with tabs, only the first tab shows — including null-tab widgets', () => {
    const spec = {
      tabs: [{ id: 't1' }, { id: 't2' }],
      widgets: [
        { id: 'first', type: 'kpi', tab_id: 't1' },
        { id: 'legacy', type: 'kpi' },            // null tab_id → belongs to first tab
        { id: 'second', type: 'kpi', tab_id: 't2' },
      ],
    }
    const { grid } = miniatureWidgets(spec)
    assert.deepEqual(grid.map(w => w.id), ['first', 'legacy'])
  })

  test('drawer widgets are excluded; header widgets are reported separately', () => {
    const spec = {
      widgets: [
        { id: 'g', type: 'chart' },
        { id: 'd', type: 'filter', placement: 'drawer' },
        { id: 'legacyDrawer', type: 'filter', drawer: true },
        { id: 'h', type: 'filter', placement: 'header' },
      ],
    }
    const { grid, header } = miniatureWidgets(spec)
    assert.deepEqual(grid.map(w => w.id), ['g'])
    assert.deepEqual(header.map(w => w.id), ['h'])
  })

  test('tolerates a missing/!array widgets list', () => {
    assert.deepEqual(miniatureWidgets({}).grid, [])
    assert.deepEqual(miniatureWidgets({ widgets: null }).grid, [])
  })
})

describe('buildMiniature', () => {
  test('returns null when there is nothing truthful to draw', () => {
    assert.equal(buildMiniature(null), null)
    assert.equal(buildMiniature(undefined), null)
    assert.equal(buildMiniature({}), null)
    assert.equal(buildMiniature({ widgets: [] }), null)
  })

  test('returns null when no widget carries usable geometry', () => {
    // A legacy/HTML-ish board whose widgets have no pos: better to fall back to
    // an icon than to invent a layout.
    const spec = { widgets: [{ id: 'a', type: 'kpi' }, { id: 'b', type: 'chart' }] }
    assert.equal(buildMiniature(spec), null)
  })

  test('projects real widget geometry and kinds, converting 1-based to 0-based', () => {
    const spec = {
      layout: { cols: 12 },
      widgets: [
        { id: 'k', type: 'kpi', ...at(1, 1, 3, 2) },
        chart('line', at(4, 1, 9, 4)),
        { id: 't', type: 'table', ...at(1, 5, 12, 5) },
      ],
    }
    const m = buildMiniature(spec)
    assert.equal(m.cols, 12)
    assert.equal(m.rows, 9)          // lowest bottom edge: (5-1) + 5
    assert.equal(m.truncated, false)
    assert.deepEqual(m.items, [
      { id: 'k', kind: 'kpi', x: 0, y: 0, w: 3, h: 2 },
      { id: 'c_line', kind: 'line', x: 3, y: 0, w: 9, h: 4 },
      { id: 't', kind: 'table', x: 0, y: 4, w: 12, h: 5 },
    ])
  })

  test('a right-edge widget stays inside the frame', () => {
    // Regression: treating the 1-based x as 0-based pushed the last column's
    // widget (x=10, w=3 on a 12-col grid) out past the right edge.
    const spec = { layout: { cols: 12 }, widgets: [{ id: 'k', type: 'kpi', ...at(10, 1, 3, 2) }] }
    const it = buildMiniature(spec).items[0]
    assert.equal(it.x, 9)
    assert.ok(it.x + it.w <= 12, 'widget must not extend past the last column')
  })

  test('defaults to 12 cols and honours a custom column count', () => {
    const w = [{ id: 'a', type: 'kpi', ...at(0, 0, 2, 2) }]
    assert.equal(buildMiniature({ widgets: w }).cols, DEFAULT_COLS)
    assert.equal(buildMiniature({ layout: { cols: 24 }, widgets: w }).cols, 24)
    // A nonsense col count falls back rather than producing a divide-by-zero frame.
    assert.equal(buildMiniature({ layout: { cols: 0 }, widgets: w }).cols, DEFAULT_COLS)
  })

  test('reads migrated surface positions in preference to inline pos', () => {
    // responsiveLayout.effectiveWidgetPos: spec.surfaces.grid overrides widget.pos.
    const spec = {
      widgets: [{ id: 'a', type: 'kpi', pos: { x: 1, y: 1, w: 2, h: 2 } }],
      surfaces: { grid: { a: { x: 7, y: 4, w: 4, h: 2 } } },
    }
    const m = buildMiniature(spec)
    // Surface entry (1-based x=7,y=4) wins over the inline pos → 0-based 6,3.
    assert.deepEqual(m.items[0], { id: 'a', kind: 'kpi', x: 6, y: 3, w: 4, h: 2 })
  })

  test('skips widgets with unusable geometry but keeps the rest', () => {
    const spec = {
      widgets: [
        { id: 'ok', type: 'kpi', ...at(0, 0, 3, 2) },
        { id: 'noPos', type: 'kpi' },
        { id: 'nanPos', type: 'kpi', pos: { x: 'x', y: 0, w: 3, h: 2 } },
        { id: 'zeroW', type: 'kpi', pos: { x: 0, y: 2, w: 0, h: 2 } },
      ],
    }
    const m = buildMiniature(spec)
    assert.deepEqual(m.items.map(i => i.id), ['ok'])
  })

  test('clamps a tall board and flags it truncated', () => {
    const spec = {
      widgets: [
        { id: 'top', type: 'kpi', ...at(0, 0, 3, 2) },
        { id: 'deep', type: 'chart', ...at(0, 30, 6, 4) },
      ],
    }
    const m = buildMiniature(spec, { maxRows: 14 })
    assert.equal(m.rows, 14)
    assert.equal(m.truncated, true)
    // 'deep' starts below the clamp → dropped, never drawn outside the frame.
    assert.deepEqual(m.items.map(i => i.id), ['top'])
  })

  test('trims a widget straddling the clamp instead of dropping it', () => {
    const spec = { widgets: [{ id: 'tall', type: 'table', ...at(1, 13, 12, 10) }] }
    const m = buildMiniature(spec, { maxRows: 14 })
    assert.equal(m.rows, 14)
    assert.equal(m.truncated, true)
    // 1-based y=13 → 0-based 12; rows 12..21 clipped to the 14-row frame.
    assert.deepEqual(m.items, [{ id: 'tall', kind: 'table', x: 0, y: 12, w: 12, h: 2 }])
  })

  test('a widget wider than the grid is clamped to the grid', () => {
    const spec = { layout: { cols: 12 }, widgets: [{ id: 'w', type: 'chart', ...at(0, 0, 99, 3) }] }
    assert.equal(buildMiniature(spec).items[0].w, 12)
  })

  test('a header-only board still renders (filters strip, no grid items)', () => {
    const spec = { widgets: [{ id: 'h', type: 'filter', placement: 'header' }] }
    const m = buildMiniature(spec)
    assert.notEqual(m, null)
    assert.deepEqual(m.items, [])
    assert.deepEqual(m.header, [{ id: 'h', kind: 'filter' }])
  })

  test('unit height reflects row_height, so a board is landscape not portrait', () => {
    // Regression: the first cut assumed square cells, which made a normal
    // 12-col × 14-row board compute as a PORTRAIT box (100 wide × 116 tall).
    // Rendered with slice, that cropped every card to its top ~3 rows and made
    // all boards look alike. A cell is (canvasW/cols) wide by row_height tall.
    const spec = {
      layout: { cols: 12, row_height: 60 },
      widgets: [{ id: 'a', type: 'kpi', ...at(0, 0, 12, 14) }],
    }
    const m = buildMiniature(spec)
    assert.equal(m.unitWidth, 100 / 12)
    assert.equal(m.unitHeight, (100 * 60) / REFERENCE_CANVAS_W)   // → 5
    // 14 rows × 5 = 70 tall against 100 wide: landscape, as a dashboard is.
    assert.ok(m.rows * m.unitHeight < 100, 'a 14-row board must not be portrait')
  })

  test('unit height is independent of the column count', () => {
    // More columns narrows cells in BOTH axes equally, so the frame's aspect is
    // driven by row_height alone.
    const w = [{ id: 'a', type: 'kpi', ...at(0, 0, 2, 2) }]
    const a = buildMiniature({ layout: { cols: 12, row_height: 60 }, widgets: w })
    const b = buildMiniature({ layout: { cols: 24, row_height: 60 }, widgets: w })
    assert.equal(a.unitHeight, b.unitHeight)
    assert.equal(b.unitWidth, a.unitWidth / 2)
  })

  test('a taller row_height makes the board taller', () => {
    const w = [{ id: 'a', type: 'kpi', ...at(0, 0, 2, 2) }]
    const short = buildMiniature({ layout: { cols: 12, row_height: 40 }, widgets: w })
    const tall = buildMiniature({ layout: { cols: 12, row_height: 120 }, widgets: w })
    assert.ok(tall.unitHeight > short.unitHeight)
    assert.equal(tall.unitHeight, short.unitHeight * 3)
  })

  test('falls back to the default row height when absent or nonsense', () => {
    const w = [{ id: 'a', type: 'kpi', ...at(0, 0, 2, 2) }]
    const expected = (100 * DEFAULT_ROW_HEIGHT) / REFERENCE_CANVAS_W
    assert.equal(buildMiniature({ widgets: w }).unitHeight, expected)
    assert.equal(buildMiniature({ layout: { row_height: 0 }, widgets: w }).unitHeight, expected)
  })

  test('does not mutate the input spec', () => {
    const spec = {
      layout: { cols: 12 },
      widgets: [{ id: 'a', type: 'kpi', pos: { x: 0, y: 0, w: 3, h: 2 } }],
    }
    const snapshot = JSON.stringify(spec)
    buildMiniature(spec)
    assert.equal(JSON.stringify(spec), snapshot)
  })
})
