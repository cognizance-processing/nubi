/**
 * useProviderData.js — React hook for fetching a board DataProvider result.
 *
 * Implements the BET-3 "one provider fetch, many widgets" contract:
 *   - POSTs to POST /api/v1/boards/{boardId}/providers/{providerId}/data
 *   - Decodes the multi-table Arrow IPC frame (custom framing from board_data.py)
 *   - Returns { tables: Record<string, arrow.Table>, loading, error }
 *
 * Multi-table frame format (produced by tables_to_multi_ipc_stream):
 *   4 bytes big-endian: count N
 *   N frames, each:
 *     4 bytes big-endian: name_len
 *     name bytes (UTF-8)
 *     4 bytes big-endian: ipc_len
 *     ipc bytes (Arrow IPC stream for one table)
 *
 * VIEWERS-NEVER-METERED invariant: the provider fetch is one server call shared
 * across all widgets bound to the same provider on the board. SpecRenderer is
 * responsible for calling this hook once per provider and passing the result
 * slices down to bound widgets.
 */

import { useState, useEffect } from 'react'
import { fetchProviderData } from '../lib/api.js'
import { decodeMultiTableIPC } from './providerDataUtils.js'

export { decodeMultiTableIPC }

/**
 * Hook that fetches a single DataProvider for a board and returns the decoded
 * named result tables.
 *
 * @param {string | null} boardId
 * @param {string | null} providerId
 * @param {Record<string, unknown>} [params]  resolved variable params
 * @param {number} [refreshEpoch]  increment to force a re-fetch
 * @returns {{
 *   tables: Record<string, arrow.Table>,
 *   loading: boolean,
 *   error: string | null
 * }}
 */
export function useProviderData(boardId, providerId, params = {}, refreshEpoch = 0) {
  const [tables, setTables] = useState({})
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)

  // Stable dep key: stringify params so the effect only re-fires on actual changes
  const paramsKey = JSON.stringify(params)

  useEffect(() => {
    if (!boardId || !providerId) return
    let cancelled = false

    async function fetchData() {
      setLoading(true)
      setError(null)
      try {
        const buf = await fetchProviderData(boardId, providerId, params)
        if (cancelled) return
        const decoded = decodeMultiTableIPC(buf)
        setTables(decoded)
      } catch (err) {
        if (!cancelled) {
          setError(err.message ?? 'Provider fetch failed.')
          setTables({})
        }
      } finally {
        if (!cancelled) setLoading(false)
      }
    }

    fetchData()
    return () => { cancelled = true }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [boardId, providerId, paramsKey, refreshEpoch])

  return { tables, loading, error }
}
