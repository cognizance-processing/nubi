/**
 * useAsyncLoad — shared async-load hook for Nubi SPA.
 *
 * Usage:
 *   const { data, loading, error, reload } = useAsyncLoad(asyncFn, deps)
 *
 * - asyncFn is called on mount and whenever deps change.
 * - Stale results are ignored (ignore-flag guard for unmounted/changed deps).
 * - reload() re-runs asyncFn with the same deps.
 * - loading starts true; becomes false after first resolve/reject.
 * - error is the Error object (or message string) on failure; null otherwise.
 * - data is the resolved value; null until resolved.
 */
import { useState, useEffect, useCallback, useRef } from 'react'

export function useAsyncLoad<T>(asyncFn: () => Promise<T>, deps: unknown[] = []) {
  const [data, setData] = useState<T | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<unknown>(null)
  const [reloadCounter, setReloadCounter] = useState(0)
  // Keep stable ref to asyncFn so we don't re-run if caller doesn't memoize
  const fnRef = useRef(asyncFn)
  useEffect(() => { fnRef.current = asyncFn })

  useEffect(() => {
    let ignore = false
    setLoading(true)
    setError(null)
    ;(async () => {
      try {
        const result = await fnRef.current()
        if (!ignore) {
          setData(result)
          setLoading(false)
        }
      } catch (e) {
        if (!ignore) {
          setError(e)
          setLoading(false)
        }
      }
    })()
    return () => { ignore = true }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, reloadCounter])

  const reload = useCallback(() => setReloadCounter(c => c + 1), [])

  return { data, loading, error, reload }
}

export default useAsyncLoad
