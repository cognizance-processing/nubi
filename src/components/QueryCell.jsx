/**
 * QueryCell — SQL input + result table card for the Nubi Playground (M2-D).
 *
 * Behaviour:
 *   1. User types SQL (default "SELECT 1 AS n") and clicks Run.
 *   2. Calls runArrowQuery(sql, onBatch) against the backend (Arrow IPC STREAM).
 *      - onBatch is called after each record batch arrives, updating the live
 *        "rows: N" counter while data is still streaming.
 *   3. On backend failure falls back to SAMPLE_TABLE + shows a non-blocking notice.
 *   4. Renders the resulting Arrow Table as an HTML table (first 100 rows).
 *   5. Shows:
 *        - Cache badge: HIT (green) / MISS (amber) / SAMPLE (gray)
 *        - "elapsed: N ms" timing from runArrowQuery
 *        - "rows: N" counter that live-updates via onBatch during streaming
 */

import { useState, useCallback, useRef } from 'react'
import { runArrowQuery, SAMPLE_TABLE } from '../lib/wasmRuntime.js'

const DEFAULT_SQL = 'SELECT 1 AS n'
const MAX_ROWS = 100

// ---------------------------------------------------------------------------
// Cache badge helper
// ---------------------------------------------------------------------------

/**
 * Renders a small coloured pill for the cache status.
 *   HIT    → green
 *   MISS   → amber
 *   SAMPLE → gray (fallback data, no backend hit)
 */
function CacheBadge({ status }) {
  if (!status) return null

  const styles = {
    HIT:    'bg-green-100 text-green-700',
    MISS:   'bg-amber-100 text-amber-700',
    SAMPLE: 'bg-gray-100 text-gray-500',
  }

  const className = `px-2 py-0.5 rounded-full text-xs font-semibold ${
    styles[status] ?? 'bg-gray-100 text-gray-500'
  }`

  return <span className={className}>{status}</span>
}

// ---------------------------------------------------------------------------
// QueryCell component
// ---------------------------------------------------------------------------

export default function QueryCell() {
  const [sql, setSql] = useState(DEFAULT_SQL)
  const [result, setResult] = useState(null)         // arrow.Table
  const [cacheStatus, setCacheStatus] = useState(null) // 'HIT' | 'MISS' | 'SAMPLE'
  const [notice, setNotice] = useState(null)         // fallback notice string
  const [error, setError] = useState(null)           // hard error (shouldn't occur after fallback)
  const [loading, setLoading] = useState(false)
  const [elapsedMs, setElapsedMs] = useState(null)   // ms, from runArrowQuery
  const [streamedRows, setStreamedRows] = useState(null) // live counter during streaming

  // Ref to avoid stale-closure issues in the onBatch callback
  const streamedRowsRef = useRef(0)

  const handleRun = useCallback(async () => {
    setLoading(true)
    setError(null)
    setNotice(null)
    setResult(null)
    setCacheStatus(null)
    setElapsedMs(null)
    setStreamedRows(null)
    streamedRowsRef.current = 0

    // onBatch callback — invoked by runArrowQuery after each Arrow RecordBatch
    // arrives over the stream, giving us a live running total of rows received.
    const onBatch = (rowsSoFar) => {
      streamedRowsRef.current = rowsSoFar
      setStreamedRows(rowsSoFar)
    }

    try {
      const { table, cacheStatus: cs, elapsedMs: ms } = await runArrowQuery(sql, onBatch)

      setResult(table)
      setCacheStatus(cs)
      setElapsedMs(ms)

      if (cs === 'SAMPLE') {
        setNotice('Backend unavailable — showing sample data.')
      }
    } catch (err) {
      // Should not happen because runArrowQuery always falls back to SAMPLE,
      // but kept as a safety net.
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }, [sql])

  // Derived table rendering data
  const columns = result ? result.schema.fields.map(f => f.name) : []
  const rows = result ? Array.from({ length: Math.min(result.numRows, MAX_ROWS) }, (_, i) =>
    columns.map(col => {
      const val = result.getChild(col)?.get(i)
      return val === null || val === undefined ? 'NULL' : String(val)
    })
  ) : []

  // Final row count: use result.numRows once done, or the streamed counter while loading
  const rowCount = result !== null
    ? result.numRows
    : (streamedRows !== null ? streamedRows : null)

  return (
    <div className="rounded-xl border border-gray-200 bg-white shadow-sm overflow-hidden">
      {/* SQL input area */}
      <div className="p-4 border-b border-gray-100">
        <label className="block text-xs font-medium text-gray-500 mb-2 uppercase tracking-wide">
          SQL
        </label>
        <textarea
          className="w-full font-mono text-sm text-gray-800 bg-gray-50 border border-gray-200 rounded-lg p-3 resize-y min-h-[80px] focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-transparent transition"
          value={sql}
          onChange={e => setSql(e.target.value)}
          onKeyDown={e => {
            // Ctrl+Enter or Cmd+Enter triggers run
            if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
              e.preventDefault()
              if (!loading) handleRun()
            }
          }}
          spellCheck={false}
          placeholder="SELECT 1 AS n"
          aria-label="SQL input"
        />
        <div className="mt-2 flex items-center gap-3">
          <button
            onClick={handleRun}
            disabled={loading || !sql.trim()}
            className="px-4 py-2 text-sm font-medium text-white bg-indigo-600 rounded-lg hover:bg-indigo-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:ring-offset-1"
          >
            {loading ? 'Running…' : 'Run'}
          </button>
          <span className="text-xs text-gray-400">Ctrl+Enter</span>
        </div>
      </div>

      {/* Loading state — shows live streamed-rows counter */}
      {loading && (
        <div className="p-6 flex items-center gap-3 text-sm text-gray-500">
          <div
            className="h-4 w-4 rounded-full border-2 border-indigo-600 border-t-transparent animate-spin"
            role="status"
            aria-label="Running query"
          />
          <span>
            Streaming…
            {streamedRows !== null && (
              <span className="ml-2 font-mono text-xs text-indigo-600">
                rows: {streamedRows.toLocaleString()}
              </span>
            )}
          </span>
        </div>
      )}

      {/* Non-blocking fallback notice */}
      {!loading && notice && (
        <div className="px-4 py-2 bg-amber-50 border-b border-amber-100 text-xs text-amber-700 flex items-start gap-2">
          <span className="shrink-0 mt-0.5">&#9888;</span>
          <span>{notice}</span>
        </div>
      )}

      {/* Hard error (shouldn't happen after fallback, kept for safety) */}
      {!loading && error && (
        <div className="px-4 py-3 bg-red-50 border-b border-red-100 text-sm text-red-700">
          {error}
        </div>
      )}

      {/* Results */}
      {!loading && result && (
        <div>
          {/* Metadata bar — cache badge + elapsed + row count */}
          <div className="px-4 py-2 bg-gray-50 border-b border-gray-100 flex items-center justify-between text-xs text-gray-500">
            <span>
              {rowCount !== null && (
                <>
                  rows:{' '}
                  <span className="font-mono font-semibold text-gray-700">
                    {rowCount.toLocaleString()}
                  </span>
                  {result.numRows > MAX_ROWS && (
                    <span className="ml-1 text-gray-400">(showing first {MAX_ROWS})</span>
                  )}
                </>
              )}
            </span>
            <div className="flex items-center gap-2">
              {elapsedMs !== null && (
                <span className="font-mono text-gray-400">
                  elapsed: {elapsedMs.toLocaleString()} ms
                </span>
              )}
              <CacheBadge status={cacheStatus} />
            </div>
          </div>

          {/* Table */}
          <div className="overflow-x-auto">
            <table className="w-full text-sm border-collapse">
              <thead>
                <tr className="bg-gray-50">
                  {columns.map(col => (
                    <th
                      key={col}
                      className="px-4 py-2 text-left text-xs font-semibold text-gray-600 border-b border-gray-200 whitespace-nowrap"
                    >
                      {col}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {rows.map((row, ri) => (
                  <tr
                    key={ri}
                    className={ri % 2 === 0 ? 'bg-white' : 'bg-gray-50/60'}
                  >
                    {row.map((cell, ci) => (
                      <td
                        key={ci}
                        className="px-4 py-2 text-gray-700 border-b border-gray-100 font-mono text-xs whitespace-nowrap max-w-xs truncate"
                        title={cell}
                      >
                        {cell === 'NULL' ? (
                          <span className="text-gray-300 italic">NULL</span>
                        ) : (
                          cell
                        )}
                      </td>
                    ))}
                  </tr>
                ))}
                {rows.length === 0 && (
                  <tr>
                    <td
                      colSpan={columns.length || 1}
                      className="px-4 py-6 text-center text-sm text-gray-400"
                    >
                      No rows returned.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Empty initial state */}
      {!loading && !result && !error && (
        <div className="px-4 py-8 text-center text-sm text-gray-400">
          Enter a SQL query above and click{' '}
          <span className="font-medium text-gray-500">Run</span>.
        </div>
      )}
    </div>
  )
}
