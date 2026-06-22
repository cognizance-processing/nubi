/**
 * useAutoRefresh.js — per-board auto-refresh / polling hook.
 *
 * Fires a callback on a configurable interval, pausing when the browser tab
 * is hidden (Page Visibility API) to avoid wasteful background fetches.
 *
 * Usage:
 *   useAutoRefresh({ intervalMs, onRefresh, enabled })
 *
 * @param {{
 *   intervalMs: number|null|undefined,  // ms between refreshes; falsy → disabled
 *   onRefresh:  () => void,             // called each tick
 *   enabled?:   boolean,                // extra flag to pause (default true)
 * }} options
 *
 * Behaviour:
 *   - When the tab becomes hidden the interval is paused (the timer is kept
 *     alive but the callback is skipped on the next tick if the page is hidden).
 *   - When the tab becomes visible again the callback fires immediately, then
 *     the interval resumes normally.
 *   - The timer is cleared on unmount.
 *   - Changing intervalMs replaces the existing timer.
 */

import { useEffect, useRef, useCallback } from 'react'

export function useAutoRefresh({ intervalMs, onRefresh, enabled = true }) {
  // Stable ref so the interval callback always calls the latest onRefresh
  // without needing to recreate the interval when the callback identity changes.
  const onRefreshRef = useRef(onRefresh)
  useEffect(() => { onRefreshRef.current = onRefresh }, [onRefresh])

  const fire = useCallback(() => {
    if (typeof document !== 'undefined' && document.visibilityState === 'hidden') return
    onRefreshRef.current?.()
  }, [])

  useEffect(() => {
    if (!intervalMs || !enabled) return

    // Fire immediately when the tab becomes visible after being hidden.
    function onVisibilityChange() {
      if (document.visibilityState === 'visible') {
        fire()
      }
    }

    const id = setInterval(fire, intervalMs)
    document.addEventListener('visibilitychange', onVisibilityChange)

    return () => {
      clearInterval(id)
      document.removeEventListener('visibilitychange', onVisibilityChange)
    }
  }, [intervalMs, enabled, fire])
}
