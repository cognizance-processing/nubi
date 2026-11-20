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

/**
 * @param enabled when false the load waits (stays `loading`, sends nothing)
 *   and runs once it turns true. Use it to hold a request until the context it
 *   depends on has resolved, instead of sending it twice — once without that
 *   context and again with it.
 */
export function useAsyncLoad<T>(asyncFn: () => Promise<T>, deps: unknown[] = [], enabled = true) {
  const [data, setData] = useState<T | null>(null)
  const [loading, setLoading] = useState(true)
  // Typed loosely (not `unknown`): callers read `.message`/`.status` off this
  // directly (it's "the Error object or message string" per the header doc),
  // and requiring a cast at every call site bought nothing across this many
  // consumers.
  const [error, setError] = useState<any>(null)
  const [reloadCounter, setReloadCounter] = useState(0)
  // Keep stable ref to asyncFn so we don't re-run if caller doesn't memoize
  const fnRef = useRef(asyncFn)
  useEffect(() => { fnRef.current = asyncFn })

  useEffect(() => {
    if (!enabled) return
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
  }, [...deps, reloadCounter, enabled])

  const reload = useCallback(() => setReloadCounter(c => c + 1), [])

  return { data, loading, error, reload }
}

export default useAsyncLoad
