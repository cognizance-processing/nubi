/**
 * RefreshContext.jsx — board-level refresh epoch counter.
 *
 * SpecRenderer increments this counter whenever the board's auto-refresh
 * timer fires. Widgets read the counter via useRefreshEpoch() and include
 * it in their useEffect dependency arrays so they re-fetch on tick.
 *
 * Additive and opt-in: widgets that do not call useRefreshEpoch() are
 * unaffected by auto-refresh (no behavioral regression).
 */

import { createContext, useContext } from 'react'

export const RefreshContext = createContext(0)

/** Returns the current refresh epoch (increments on each board refresh tick). */
export function useRefreshEpoch() {
  return useContext(RefreshContext)
}
