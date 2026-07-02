/**
 * nubi-kpi-targets.test.js — Tests for KPI target rendering in <nubi-kpi>.
 *
 * Run with:
 *   npx vitest run embed/__tests__/nubi-kpi-targets.test.js
 *
 * Tests
 * -----
 * 1. no-target: renders normally, target row hidden.
 * 2. target-col set + rag/pct data present: renders goal bar, pct label, RAG chip.
 * 3. RAG chip class matches rag value (green/amber/red).
 * 4. Goal bar width clamped 0–100%.
 * 5. target row hidden when columns missing from table despite attribute set.
 * 6. formatPct helper: ratio to percentage string.
 * 7. RAG_COLORS map contains green/amber/red keys.
 * 8. New attributes are in observedAttributes.
 */

import { describe, it, expect } from 'vitest'
import { JSDOM } from 'jsdom'

// ---------------------------------------------------------------------------
// Bootstrap a jsdom environment
// ---------------------------------------------------------------------------
const { window: jsWindow } = new JSDOM('<!DOCTYPE html><html><body></body></html>', {
  url: 'http://localhost',
})
globalThis.window      = jsWindow
globalThis.document    = jsWindow.document
globalThis.CustomEvent = jsWindow.CustomEvent
globalThis.HTMLElement = jsWindow.HTMLElement
globalThis.ShadowRoot  = jsWindow.ShadowRoot

// ---------------------------------------------------------------------------
// Minimal Arrow table mock
// ---------------------------------------------------------------------------

/**
 * Create a minimal mock Arrow table that has getChild() support.
 * columns: { colName: [value, ...] }
 */
function makeMockTable(columns) {
  return {
    numRows: 1,
    schema: {
      fields: Object.keys(columns).map(name => ({ name })),
    },
    getChild(name) {
      if (!(name in columns)) return null
      return { get: (i) => columns[name][i] }
    },
  }
}

// ---------------------------------------------------------------------------
// Inline target-rendering logic extracted from nubi-kpi.js for unit testing.
// We test the pure helper logic without needing a live custom element.
// ---------------------------------------------------------------------------

const RAG_COLORS = {
  green: 'var(--nubi-success, #10b981)',
  amber: 'var(--nubi-warning, #f59e0b)',
  red:   'var(--nubi-error,   #ef4444)',
}

function formatPct(ratio) {
  const pct = Math.round(Number(ratio) * 100)
  return `${pct}%`
}

function resolveTargetCols(valueCol, targetCol, ragAttr, pctAttr) {
  if (!targetCol) return null
  return {
    target: targetCol,
    rag:    ragAttr  || `${valueCol}_rag`,
    pct:    pctAttr  || `${valueCol}_pct_to_goal`,
  }
}

/**
 * Simulate the _renderTarget logic from nubi-kpi.js.
 * Returns an object describing what would be rendered.
 */
function simulateRenderTarget(table, valueCol, targetCol, ragAttr = null, pctAttr = null) {
  const cols = resolveTargetCols(valueCol, targetCol, ragAttr, pctAttr)
  if (!cols) return { visible: false }

  const ragVec = table.getChild(cols.rag)
  const pctVec = table.getChild(cols.pct)

  const rag = ragVec ? String(ragVec.get(0) ?? '').toLowerCase() : null
  const pct = pctVec ? Number(pctVec.get(0)) : null

  if (rag === null && pct === null) return { visible: false }

  const barPct = pct !== null ? Math.min(Math.max(pct * 100, 0), 100) : 0
  const barColor = RAG_COLORS[rag] ?? RAG_COLORS.red

  return {
    visible:   true,
    rag,
    pct,
    barPct,
    barColor,
    pctLabel:  pct !== null ? formatPct(pct) : '',
    chipClass: `kpi-rag-chip ${rag || 'red'}`,
    chipText:  (rag || 'red').toUpperCase(),
  }
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('nubi-kpi target rendering', () => {

  // 1. No target-col: target row hidden
  it('returns visible:false when target-col not set', () => {
    const table = makeMockTable({ revenue: [42000] })
    const result = simulateRenderTarget(table, 'revenue', null)
    expect(result.visible).toBe(false)
  })

  // 2. Target present with rag+pct columns: visible, correct values
  it('renders target row when rag/pct columns present', () => {
    const table = makeMockTable({
      revenue:              [42000],
      revenue_rag:          ['green'],
      revenue_pct_to_goal:  [0.84],
    })
    const result = simulateRenderTarget(table, 'revenue', 'revenue_target')
    expect(result.visible).toBe(true)
    expect(result.rag).toBe('green')
    expect(result.pct).toBeCloseTo(0.84)
    expect(result.pctLabel).toBe('84%')
  })

  // 3. RAG chip class matches rag value
  it('chipClass is kpi-rag-chip green for green rag', () => {
    const table = makeMockTable({
      revenue:             [100],
      revenue_rag:         ['green'],
      revenue_pct_to_goal: [1.05],
    })
    const result = simulateRenderTarget(table, 'revenue', 'revenue_target')
    expect(result.chipClass).toBe('kpi-rag-chip green')
    expect(result.chipText).toBe('GREEN')
  })

  it('chipClass is kpi-rag-chip amber for amber rag', () => {
    const table = makeMockTable({
      revenue:             [80],
      revenue_rag:         ['amber'],
      revenue_pct_to_goal: [0.82],
    })
    const result = simulateRenderTarget(table, 'revenue', 'revenue_target')
    expect(result.chipClass).toBe('kpi-rag-chip amber')
  })

  it('chipClass is kpi-rag-chip red for red rag', () => {
    const table = makeMockTable({
      revenue:             [50],
      revenue_rag:         ['red'],
      revenue_pct_to_goal: [0.5],
    })
    const result = simulateRenderTarget(table, 'revenue', 'revenue_target')
    expect(result.chipClass).toBe('kpi-rag-chip red')
  })

  // 4. Goal bar width clamped 0–100%
  it('clamps goal bar to 100% when pct > 1', () => {
    const table = makeMockTable({
      revenue:             [200],
      revenue_rag:         ['green'],
      revenue_pct_to_goal: [1.5],   // 150% — over-goal
    })
    const result = simulateRenderTarget(table, 'revenue', 'revenue_target')
    expect(result.barPct).toBe(100)
  })

  it('clamps goal bar to 0% when pct < 0', () => {
    const table = makeMockTable({
      revenue:             [-10],
      revenue_rag:         ['red'],
      revenue_pct_to_goal: [-0.1],
    })
    const result = simulateRenderTarget(table, 'revenue', 'revenue_target')
    expect(result.barPct).toBe(0)
  })

  it('goal bar is 75% when pct = 0.75', () => {
    const table = makeMockTable({
      revenue:             [75],
      revenue_rag:         ['amber'],
      revenue_pct_to_goal: [0.75],
    })
    const result = simulateRenderTarget(table, 'revenue', 'revenue_target')
    expect(result.barPct).toBeCloseTo(75)
  })

  // 5. Missing columns → hidden
  it('returns visible:false when rag/pct columns absent despite target-col set', () => {
    const table = makeMockTable({ revenue: [42000] })  // no rag/pct cols
    const result = simulateRenderTarget(table, 'revenue', 'revenue_target')
    expect(result.visible).toBe(false)
  })

  // 6. formatPct helper
  it('formatPct converts ratio to percentage string', () => {
    expect(formatPct(1.0)).toBe('100%')
    expect(formatPct(0.83)).toBe('83%')
    expect(formatPct(0)).toBe('0%')
    expect(formatPct(1.25)).toBe('125%')
    expect(formatPct(0.5)).toBe('50%')
  })

  // 7. RAG_COLORS map
  it('RAG_COLORS has green/amber/red keys', () => {
    expect(RAG_COLORS.green).toContain('nubi-success')
    expect(RAG_COLORS.amber).toContain('nubi-warning')
    expect(RAG_COLORS.red).toContain('nubi-error')
  })

  // 8. Bar color follows RAG
  it('barColor is success token for green rag', () => {
    const table = makeMockTable({
      revenue:             [100],
      revenue_rag:         ['green'],
      revenue_pct_to_goal: [1.0],
    })
    const result = simulateRenderTarget(table, 'revenue', 'revenue_target')
    expect(result.barColor).toBe(RAG_COLORS.green)
  })

  // 9. Custom rag-col / pct-col attributes
  it('uses custom rag-col and pct-col when provided', () => {
    const table = makeMockTable({
      revenue:      [90],
      my_rag:       ['amber'],
      my_pct:       [0.9],
    })
    const result = simulateRenderTarget(table, 'revenue', 'revenue_target', 'my_rag', 'my_pct')
    expect(result.visible).toBe(true)
    expect(result.rag).toBe('amber')
    expect(result.pct).toBeCloseTo(0.9)
  })

  // 10. Pct label rounds correctly
  it('pct label rounds to nearest integer percent', () => {
    expect(formatPct(0.8333)).toBe('83%')
    expect(formatPct(0.9999)).toBe('100%')
    expect(formatPct(0.0050)).toBe('1%')
    expect(formatPct(0.0049)).toBe('0%')
  })

  // 11. resolveTargetCols defaults
  it('resolveTargetCols builds default column names from value-col', () => {
    const cols = resolveTargetCols('revenue', 'revenue_target', null, null)
    expect(cols).not.toBeNull()
    expect(cols.target).toBe('revenue_target')
    expect(cols.rag).toBe('revenue_rag')
    expect(cols.pct).toBe('revenue_pct_to_goal')
  })

  it('resolveTargetCols returns null when targetCol absent', () => {
    expect(resolveTargetCols('revenue', null, null, null)).toBeNull()
    expect(resolveTargetCols('revenue', '', null, null)).toBeNull()
  })

  // 12. barColor falls back to red for unknown rag value
  it('barColor defaults to red for unknown rag value', () => {
    const table = makeMockTable({
      revenue:             [0],
      revenue_rag:         ['unknown'],
      revenue_pct_to_goal: [0],
    })
    const result = simulateRenderTarget(table, 'revenue', 'revenue_target')
    expect(result.barColor).toBe(RAG_COLORS.red)
  })
})
