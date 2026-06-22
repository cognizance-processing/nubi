/**
 * CrossFilterContext.jsx — board-level cross-filter / navigation event bus.
 *
 * Provides a context that widgets can use to:
 *   1. Emit a cross-filter event when a data point is clicked
 *      (sets one or more board variables OR switches the active tab).
 *   2. Navigate to a named tab within the same board.
 *
 * The context is entirely opt-in:
 *   - Boards / embeds that do not pass an `onCrossFilter` prop behave exactly
 *     as before — no event is fired, no tab switch occurs.
 *   - Widget specs that do not declare `onClick` are unaffected.
 *
 * ── Widget onClick spec shape (widget.onClick) ────────────────────────────
 * {
 *   // Option A — set one variable in the board's VariableStore:
 *   setVar:      string,      // variable name to set
 *   valueField?: string,      // data field whose clicked value is used
 *                             // (falls back to the primary click value: params.name)
 *
 *   // Option B — navigate to a tab by id:
 *   navigateTab: string,      // target tab id
 *
 *   // Both may be combined (set var AND navigate):
 *   setVar:      string,
 *   valueField?: string,
 *   navigateTab: string,
 * }
 *
 * ── API ──────────────────────────────────────────────────────────────────────
 * useCrossFilter()  → { emit }   — call emit(clickSpec, echartsParams) from widget
 * CrossFilterProvider             — wraps a board; receives onTabChange + setVariable
 */

import { createContext, useContext, useCallback } from 'react'

const CrossFilterContext = createContext(null)

/**
 * Provider for the board-level cross-filter bus.
 *
 * @param {{
 *   setVariable?: (name: string, value: unknown) => void,
 *   onTabChange?:  (tabId: string) => void,
 *   onCrossFilter?: (event: CrossFilterEvent) => void,
 *   children: React.ReactNode,
 * }} props
 *
 * onCrossFilter — optional external callback for parent frames / embeds.
 *   Called with { type, var, value, tabId } after internal state is updated.
 */
export function CrossFilterProvider({ setVariable, onTabChange, onCrossFilter, children }) {
  /**
   * Emit a cross-filter / navigate event from a widget data-point click.
   *
   * @param {object} clickSpec   — widget.onClick spec
   * @param {object} eParams     — ECharts event params (params.name / params.data / params.value)
   */
  const emit = useCallback((clickSpec, eParams) => {
    if (!clickSpec) return

    // Resolve the clicked value.
    const field = clickSpec.valueField
    let clickedValue
    if (field && eParams?.data && typeof eParams.data === 'object' && field in eParams.data) {
      clickedValue = eParams.data[field]
    } else {
      clickedValue = eParams?.name ?? eParams?.value
    }

    // Option A: set a variable.
    if (clickSpec.setVar && setVariable) {
      setVariable(clickSpec.setVar, clickedValue)
    }

    // Option B: navigate to a tab.
    if (clickSpec.navigateTab && onTabChange) {
      onTabChange(clickSpec.navigateTab)
    }

    // Notify external listeners.
    if (onCrossFilter) {
      onCrossFilter({
        type:  clickSpec.navigateTab ? 'navigate' : 'filter',
        var:   clickSpec.setVar ?? null,
        value: clickedValue,
        tabId: clickSpec.navigateTab ?? null,
      })
    }
  }, [setVariable, onTabChange, onCrossFilter])

  return (
    <CrossFilterContext.Provider value={{ emit }}>
      {children}
    </CrossFilterContext.Provider>
  )
}

/**
 * Consume the cross-filter bus from any widget.
 * Returns { emit } — safe to call even outside a provider (no-op).
 */
// eslint-disable-next-line react-refresh/only-export-components
export function useCrossFilter() {
  const ctx = useContext(CrossFilterContext)
  const noop = useCallback(() => {}, [])
  if (!ctx) return { emit: noop }
  return ctx
}
