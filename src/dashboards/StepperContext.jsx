/**
 * StepperContext.jsx — lets a widget nested inside a stepper advance it.
 *
 * A `stepper` widget shows one child at a time in a single grid tile. Legacy
 * boards used this for in-tile drill-downs: the user clicks a data point in the
 * step-1 chart and the tile advances to step 2, *filtered by the clicked value*.
 * The value half of that is already handled by the cross-filter bus
 * (`widget.onClick.setVar`); this context supplies the missing "and move to the
 * next step" half.
 *
 * A child opts in by adding `stepNext: true` to its `onClick` spec:
 *
 *   onClick: { setVar: 'region', stepNext: true }
 *
 * Outside a stepper the hook is a no-op, so the same widget spec renders fine
 * anywhere.
 */

import { createContext, useContext, useCallback } from 'react'

const StepperContext = createContext(null)

export function StepperProvider({ advance, children }) {
  return (
    <StepperContext.Provider value={{ advance }}>
      {children}
    </StepperContext.Provider>
  )
}

/**
 * Consume the enclosing stepper. Returns { advance } — safe to call outside a
 * stepper (no-op), so widgets never need to know whether they are nested.
 */
// eslint-disable-next-line react-refresh/only-export-components
export function useStepper() {
  const ctx = useContext(StepperContext)
  const noop = useCallback(() => {}, [])
  if (!ctx) return { advance: noop }
  return ctx
}
