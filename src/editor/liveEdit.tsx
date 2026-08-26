/**
 * liveEdit.jsx — a "this edit is still in progress" signal for the editor.
 *
 * Some inspector controls emit a continuous stream of values rather than one
 * value per decision: dragging inside the colour picker's spectrum fires an
 * `input` event on every pointer move, and each one used to rewrite the whole
 * board spec AND append a full copy of it to the undo stack. A one-second drag
 * therefore re-rendered the canvas ~60 times and left 60 near-identical undo
 * steps behind it — the drag felt laggy, and Ctrl+Z afterwards stepped through
 * the drag one shade at a time instead of undoing "I changed the colour".
 *
 * A control wraps such a gesture in `begin()` … `end()`. While a gesture is
 * open, DashboardEditor's spec commits REPLACE the newest history entry instead
 * of appending, so the whole gesture collapses to a single undo step. The
 * re-render cost is handled separately, by the control throttling how often it
 * commits at all (see ColorSwatch).
 *
 * The context is optional: with no provider the hook returns null and controls
 * behave exactly as they did before. That keeps the primitives usable outside
 * DashboardEditor (the focus-mode shell, standalone panels, tests).
 */

import { createContext, useContext, useEffect, useMemo, useRef, type ReactNode } from 'react'

const LiveEditContext = createContext<{ begin: () => void; end: () => void } | null>(null)

/**
 * Provide a live-edit gesture signal.
 *
 * @param onChange  called with `true` when a gesture opens and `false` when the
 *                  last open gesture closes. Nested/among-controls overlapping
 *                  gestures are reference-counted, so an inner control ending
 *                  its gesture cannot close an outer one.
 */
export function LiveEditProvider({ onChange, children }: {
  onChange: (live: boolean) => void
  children: ReactNode
}) {
  const depth = useRef(0)
  // The callback identity must stay stable: these end up in effect deps and in
  // event handlers on controls that re-render on every value change.
  const onChangeRef = useRef(onChange)
  useEffect(() => { onChangeRef.current = onChange }, [onChange])

  const value = useMemo(() => ({
    begin() {
      depth.current += 1
      if (depth.current === 1) onChangeRef.current?.(true)
    },
    end() {
      if (depth.current === 0) return
      depth.current -= 1
      if (depth.current === 0) onChangeRef.current?.(false)
    },
  }), [])

  return <LiveEditContext.Provider value={value}>{children}</LiveEditContext.Provider>
}

/**
 * Returns `{ begin, end }`, or null when there is no provider — callers must
 * treat the null case as "no gesture tracking", not as an error.
 */
export function useLiveEdit() {
  return useContext(LiveEditContext)
}
