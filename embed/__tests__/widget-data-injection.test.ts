/**
 * widget-data-injection.test.js — Tests for rowsToArrowTable and the inline
 * `data` attribute injection path across the vanilla embed widgets.
 *
 * Run with:
 *   npx vitest run embed/__tests__/widget-data-injection.test.js
 */

import { describe, it, expect } from 'vitest'
import { JSDOM } from 'jsdom'

// ---------------------------------------------------------------------------
// Bootstrap a jsdom environment (same pattern as nubi-kpi-targets.test.js)
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
// Import rowsToArrowTable from shared.js
// ---------------------------------------------------------------------------
import { rowsToArrowTable } from '../widgets/shared.js'

// ---------------------------------------------------------------------------
// Tests for rowsToArrowTable
// ---------------------------------------------------------------------------

describe('rowsToArrowTable', () => {

  it('returns a table with the correct columns and values for a single row', () => {
    const rows = [{ revenue: 124500, revenue_rag: 'green', revenue_pct_to_goal: 1.25 }]
    const table = rowsToArrowTable(rows)

    expect(table).not.toBeNull()
    expect(table.numRows).toBe(1)

    const revenueVec = table.getChild('revenue')
    expect(revenueVec).not.toBeNull()
    expect(Number(revenueVec.get(0))).toBeCloseTo(124500)

    const ragVec = table.getChild('revenue_rag')
    expect(ragVec).not.toBeNull()
    expect(String(ragVec.get(0))).toBe('green')

    const pctVec = table.getChild('revenue_pct_to_goal')
    expect(pctVec).not.toBeNull()
    expect(Number(pctVec.get(0))).toBeCloseTo(1.25)
  })

  it('returns a table without throwing for an empty rows array', () => {
    expect(() => rowsToArrowTable([])).not.toThrow()
    const table = rowsToArrowTable([])
    expect(table).not.toBeNull()
    // Empty table has a dummy _empty column
    expect(table.getChild('_empty')).not.toBeNull()
  })

  it('handles a row with all four target columns including string rag', () => {
    const rows = [{
      revenue:              42000,
      revenue_target:       100000,
      revenue_pct_to_goal:  0.42,
      revenue_rag:          'red',
    }]
    const table = rowsToArrowTable(rows)

    expect(table.numRows).toBe(1)

    // All 4 columns present
    expect(table.getChild('revenue')).not.toBeNull()
    expect(table.getChild('revenue_target')).not.toBeNull()
    expect(table.getChild('revenue_pct_to_goal')).not.toBeNull()
    expect(table.getChild('revenue_rag')).not.toBeNull()

    // Numeric columns have correct values
    expect(Number(table.getChild('revenue').get(0))).toBeCloseTo(42000)
    expect(Number(table.getChild('revenue_target').get(0))).toBeCloseTo(100000)
    expect(Number(table.getChild('revenue_pct_to_goal').get(0))).toBeCloseTo(0.42)

    // String rag column
    expect(String(table.getChild('revenue_rag').get(0))).toBe('red')
  })

  it('numRows equals the number of input rows', () => {
    const rows = [
      { x: 1, label: 'a' },
      { x: 2, label: 'b' },
      { x: 3, label: 'c' },
    ]
    const table = rowsToArrowTable(rows)
    expect(table.numRows).toBe(rows.length)
  })

  it('handles null values in numeric columns', () => {
    const rows = [
      { revenue: null },
      { revenue: 5000 },
    ]
    const table = rowsToArrowTable(rows)
    expect(table.numRows).toBe(2)
    // Second row value
    expect(Number(table.getChild('revenue').get(1))).toBeCloseTo(5000)
  })

  it('handles mixed string columns with null', () => {
    const rows = [
      { category: 'A' },
      { category: null },
    ]
    const table = rowsToArrowTable(rows)
    expect(String(table.getChild('category').get(0))).toBe('A')
    // null becomes empty string
    expect(String(table.getChild('category').get(1))).toBe('')
  })

  it('numeric columns are detected when all values are numbers', () => {
    const rows = [{ value: 1 }, { value: 2 }, { value: 3 }]
    const table = rowsToArrowTable(rows)
    // All numeric — should use Float64 path (no toString coercion)
    const vec = table.getChild('value')
    expect(vec).not.toBeNull()
    expect(Number(vec.get(0))).toBe(1)
    expect(Number(vec.get(2))).toBe(3)
  })

  it('string columns are detected when any value is a string', () => {
    const rows = [{ label: 'foo' }, { label: 'bar' }]
    const table = rowsToArrowTable(rows)
    expect(String(table.getChild('label').get(0))).toBe('foo')
    expect(String(table.getChild('label').get(1))).toBe('bar')
  })

  it('non-array input returns a table without throwing', () => {
    expect(() => rowsToArrowTable(null)).not.toThrow()
    expect(() => rowsToArrowTable(undefined)).not.toThrow()
    expect(() => rowsToArrowTable('not an array')).not.toThrow()
  })

  it('green/amber/red KPI row produces table with correct rag and pct values', () => {
    const rows = [{ revenue: 124500, revenue_target: 100000, revenue_pct_to_goal: 1.25, revenue_rag: 'green' }]
    const table = rowsToArrowTable(rows)

    const rag = String(table.getChild('revenue_rag').get(0))
    const pct = Number(table.getChild('revenue_pct_to_goal').get(0))

    expect(rag).toBe('green')
    expect(pct).toBeCloseTo(1.25)
  })
})

// ---------------------------------------------------------------------------
// _showError DOM logic (tested via helper simulation, matching kpi-targets
// test pattern of extracting + testing logic inline without a live CE)
// ---------------------------------------------------------------------------

describe('_showError DOM logic', () => {
  /**
   * Minimal shadow DOM simulation using real jsdom elements.
   * Mirrors what _ensureScaffold creates.
   */
  function createMockKpiShadow() {
    const root = document.createElement('div')
    root.innerHTML = `
      <div class="kpi-wrap">
        <div class="kpi-top">
          <span class="kpi-label">Revenue</span>
          <span class="nubi-badge sample" style="display:inline-block">SAMPLE</span>
        </div>
        <div class="kpi-value">124.5K</div>
        <div class="kpi-target-row" style="display:flex">
          <div class="kpi-goal-bar-wrap"><div class="kpi-goal-bar"></div></div>
          <span class="kpi-pct-label">125%</span>
          <span class="kpi-rag-chip green">GREEN</span>
        </div>
        <div class="kpi-footer">
          <span class="kpi-sublabel">query: test</span>
          <span class="nubi-sample-note" style="display:inline-block">preview · sample data</span>
        </div>
      </div>
    `
    return root
  }

  /**
   * Simulate the _showError method from NubiKpi (polished error-state, no giant font).
   * Raw message is NOT shown in visible UI — only kept for the emitted event.
   */
  function simulateShowError(shadow, _rawMessage, label = 'Revenue') {
    // Clear the big metric value — don't render giant text in error state
    const v = shadow.querySelector('.kpi-value')
    if (v) {
      v.className = 'kpi-value'
      v.textContent = ''
    }

    const badge = shadow.querySelector('.nubi-badge')
    const note  = shadow.querySelector('.nubi-sample-note')
    if (badge) badge.style.display = 'none'
    if (note)  note.style.display  = 'none'

    // Inject friendly error UI
    let errState = shadow.querySelector('.kpi-error-state')
    if (!errState) {
      errState = document.createElement('div')
      errState.className = 'kpi-error-state'
      errState.innerHTML = `
        <span class="kpi-error-primary">Couldn't load data</span>
        <span class="kpi-error-secondary">Check your connection or try again</span>
      `
      // Keep a hidden .kpi-error-note for e2e selector compatibility
      let errNote = shadow.querySelector('.kpi-error-note')
      if (!errNote) {
        errNote = document.createElement('div')
        errNote.className = 'kpi-error-note'
        errNote.style.display = 'none'
      }
      const footer = shadow.querySelector('.kpi-footer')
      if (footer) {
        footer.before(errState)
        footer.before(errNote)
      }
    }

    const targetRow = shadow.querySelector('.kpi-target-row')
    if (targetRow) targetRow.style.display = 'none'
    const labelEl = shadow.querySelector('.kpi-label')
    if (labelEl) labelEl.textContent = label
  }

  it('hides SAMPLE badge when _showError is called', () => {
    const shadow = createMockKpiShadow()
    simulateShowError(shadow, 'Network error')

    const badge = shadow.querySelector('.nubi-badge') as HTMLElement
    expect(badge.style.display).toBe('none')
  })

  it('hides sample note when _showError is called', () => {
    const shadow = createMockKpiShadow()
    simulateShowError(shadow, 'Network error')

    const note = shadow.querySelector('.nubi-sample-note') as HTMLElement
    expect(note.style.display).toBe('none')
  })

  it('clears kpi-value text and does NOT use the big-number style for error', () => {
    const shadow = createMockKpiShadow()
    simulateShowError(shadow, 'Network error')

    const v = shadow.querySelector('.kpi-value')
    // Value text must be cleared — error goes in kpi-error-state, not big font
    expect(v.textContent).toBe('')
    expect(v.className).toBe('kpi-value')  // no nubi-loading class
  })

  it('inserts kpi-error-state with "Couldn\'t load data" primary text', () => {
    const shadow = createMockKpiShadow()
    simulateShowError(shadow, 'Failed to fetch')

    const errState = shadow.querySelector('.kpi-error-state')
    expect(errState).not.toBeNull()
    const primary = errState.querySelector('.kpi-error-primary')
    expect(primary).not.toBeNull()
    expect(primary.textContent).toContain("Couldn't load data")
  })

  it('error state shows secondary hint, not raw exception text', () => {
    const shadow = createMockKpiShadow()
    simulateShowError(shadow, 'Failed to fetch')

    const errState = shadow.querySelector('.kpi-error-state')
    // Raw exception text must NOT appear anywhere in visible DOM
    expect(errState.textContent).not.toContain('Failed to fetch')
    // Secondary hint should be present
    const secondary = errState.querySelector('.kpi-error-secondary')
    expect(secondary).not.toBeNull()
    expect(secondary.textContent).toContain('Check your connection')
  })

  it('inserts kpi-error-note (hidden, for e2e selector compat) before the footer', () => {
    const shadow = createMockKpiShadow()
    simulateShowError(shadow, 'failed')

    const footer = shadow.querySelector('.kpi-footer')
    const errNote = shadow.querySelector('.kpi-error-note') as HTMLElement
    expect(errNote).not.toBeNull()
    // errNote is present in the DOM (used by e2e selector)
    expect(errNote.style.display).toBe('none')
    // kpi-error-state should also be inserted before footer
    const errState = shadow.querySelector('.kpi-error-state')
    expect(errState).not.toBeNull()
    expect(footer.previousElementSibling).toBe(errNote)
  })

  it('hides target row when _showError is called', () => {
    const shadow = createMockKpiShadow()
    simulateShowError(shadow, 'Error')

    const targetRow = shadow.querySelector('.kpi-target-row') as HTMLElement
    expect(targetRow.style.display).toBe('none')
  })

  it('keeps the label text on error', () => {
    const shadow = createMockKpiShadow()
    simulateShowError(shadow, 'Error', 'My Revenue')

    const labelEl = shadow.querySelector('.kpi-label')
    expect(labelEl.textContent).toBe('My Revenue')
  })

  it('shows "Couldn\'t load data" regardless of raw error message', () => {
    const shadow = createMockKpiShadow()
    simulateShowError(shadow, '')

    const errState = shadow.querySelector('.kpi-error-state')
    expect(errState).not.toBeNull()
    expect(errState.textContent).toContain("Couldn't load data")
  })
})

// ---------------------------------------------------------------------------
// data attribute JSON parsing (integration-style, testing parse path)
// ---------------------------------------------------------------------------

describe('data attribute parsing logic', () => {
  /**
   * Simulate the data injection block from _render().
   * Returns the table on success, null on parse failure.
   */
  function simulateDataInjection(dataAttr) {
    try {
      const rows = JSON.parse(dataAttr)
      return rowsToArrowTable(rows)
    } catch {
      return null
    }
  }

  it('parses a valid JSON array into a table', () => {
    const attr = JSON.stringify([{ revenue: 42000 }])
    const table = simulateDataInjection(attr)
    expect(table).not.toBeNull()
    expect(table.numRows).toBe(1)
  })

  it('returns null for malformed JSON', () => {
    const table = simulateDataInjection('{bad json}')
    expect(table).toBeNull()
  })

  it('returns a table for empty array JSON', () => {
    const table = simulateDataInjection('[]')
    expect(table).not.toBeNull()
    // Empty → has _empty col
    expect(table.getChild('_empty')).not.toBeNull()
  })

  it('parses green KPI data correctly', () => {
    const rows = [{ revenue: 124500, revenue_target: 100000, revenue_pct_to_goal: 1.25, revenue_rag: 'green' }]
    const table = simulateDataInjection(JSON.stringify(rows))

    expect(table.numRows).toBe(1)
    expect(String(table.getChild('revenue_rag').get(0))).toBe('green')
    expect(Number(table.getChild('revenue_pct_to_goal').get(0))).toBeCloseTo(1.25)
  })

  it('parses amber KPI data correctly', () => {
    const rows = [{ revenue: 85000, revenue_target: 100000, revenue_pct_to_goal: 0.85, revenue_rag: 'amber' }]
    const table = simulateDataInjection(JSON.stringify(rows))

    expect(String(table.getChild('revenue_rag').get(0))).toBe('amber')
    expect(Number(table.getChild('revenue_pct_to_goal').get(0))).toBeCloseTo(0.85)
  })

  it('parses red KPI data correctly', () => {
    const rows = [{ revenue: 50000, revenue_target: 100000, revenue_pct_to_goal: 0.5, revenue_rag: 'red' }]
    const table = simulateDataInjection(JSON.stringify(rows))

    expect(String(table.getChild('revenue_rag').get(0))).toBe('red')
    expect(Number(table.getChild('revenue_pct_to_goal').get(0))).toBeCloseTo(0.5)
  })
})
