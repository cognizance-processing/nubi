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

/**
 * Whether board widgets may reuse a recently loaded query result held in this
 * browser (lib/queryResultCache). SpecRenderer turns it on for the read-only
 * board view and off on the editor canvas, where an author expects every
 * render to hit the server. Default false, so any widget rendered outside a
 * SpecRenderer keeps the old always-fetch behaviour.
 */
export const QueryCacheContext = createContext(false)

/**
 * Options to spread into runArrowQueryById(...) from a board widget:
 * `{ cache, cacheEpoch }`. The board's refresh epoch is part of the memory
 * key, so an auto-refresh tick always fetches fresh data.
 */
export function useQueryCacheOpts(): { cache: boolean; cacheEpoch: number } {
  const enabled = useContext(QueryCacheContext)
  const epoch = useContext(RefreshContext)
  return { cache: enabled === true, cacheEpoch: epoch }
}
