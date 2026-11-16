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
import Tag from './ui/Tag.jsx'
import Button from './ui/Button.jsx'
import Spinner from './ui/Spinner.jsx'

const DEFAULT_SQL = 'SELECT 1 AS n'
const MAX_ROWS = 100

// ---------------------------------------------------------------------------
// Cache badge helper
// ---------------------------------------------------------------------------

/**
 * Cache status as a Tag: square, mono, a status dot (no tinted pill).
 *   HIT → success, MISS → warning, SAMPLE → neutral (fallback, no backend hit)
 */
function CacheBadge({ status }) {
  if (!status) return null

  const variant = status === 'HIT' ? 'success' : status === 'MISS' ? 'warning' : 'neutral'
  return <Tag variant={variant} dot>{status}</Tag>
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
    <div className="border border-border bg-surface rounded-[var(--radius-sheet)] overflow-hidden">
      {/* SQL input area */}
      <div className="p-4 border-b border-border">
        <label htmlFor="query-cell-sql" className="block font-mono text-[11px] font-medium uppercase tracking-[0.06em] text-subtle mb-2">
          SQL
        </label>
        <textarea
          id="query-cell-sql"
          className="w-full font-mono text-[13px] text-fg bg-surface-2 border border-border rounded-[var(--radius-control)] p-3 resize-y min-h-[80px] focus-visible:outline-none focus-visible:border-ink focus-visible:ring-2 focus-visible:ring-ring"
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
          <Button onClick={handleRun} disabled={loading || !sql.trim()} loading={loading}>
            {loading ? 'Running…' : 'Run'}
          </Button>
          <kbd className="nubi-kbd select-none">Ctrl Enter</kbd>
        </div>
      </div>

      {/* Loading state — shows live streamed-rows counter */}
      {loading && (
        <div className="p-6 flex items-center gap-3 text-sm text-muted" role="status" aria-live="polite">
          <Spinner size="sm" label="Running query" />
          <span>
            Streaming…
            {streamedRows !== null && (
              <span className="ml-2 font-mono tabular-nums text-xs text-fg">
                rows: {streamedRows.toLocaleString()}
              </span>
            )}
          </span>
        </div>
      )}

      {/* Non-blocking fallback notice */}
      {!loading && notice && (
        <div className="px-4 py-2 border-b border-border border-l-2 border-l-warning text-xs text-muted flex items-start gap-2">
          <span>{notice}</span>
        </div>
      )}

      {/* Hard error (shouldn't happen after fallback, kept for safety) */}
      {!loading && error && (
        <div role="alert" className="px-4 py-3 border-b border-border border-l-2 border-l-danger text-sm text-fg">
          {error}
        </div>
      )}

      {/* Results */}
      {!loading && result && (
        <div>
          {/* Metadata bar: row count, elapsed, cache status */}
          <div className="px-4 py-2 bg-surface-2 border-b border-border flex items-center justify-between text-xs text-muted">
            <span>
              {rowCount !== null && (
                <>
                  rows{' '}
                  <span className="font-mono tabular-nums font-semibold text-fg">
                    {rowCount.toLocaleString()}
                  </span>
                  {result.numRows > MAX_ROWS && (
                    <span className="ml-1 text-subtle">(showing first {MAX_ROWS})</span>
                  )}
                </>
              )}
            </span>
            <div className="flex items-center gap-2">
              {elapsedMs !== null && (
                <span className="font-mono tabular-nums text-muted">
                  {elapsedMs.toLocaleString()} ms
                </span>
              )}
              <CacheBadge status={cacheStatus} />
            </div>
          </div>

          {/* Table */}
          <div className="overflow-x-auto">
            <table className="w-full text-sm border-collapse">
              <thead>
                <tr className="bg-surface">
                  {columns.map(col => (
                    <th
                      key={col}
                      scope="col"
                      className="h-9 px-4 text-left font-mono text-[11px] font-medium uppercase tracking-[0.06em] text-subtle border-b-2 border-ink whitespace-nowrap"
                    >
                      {col}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {rows.map((row, ri) => (
                  <tr key={ri} className="hover:bg-frame-hover">
                    {row.map((cell, ci) => (
                      <td
                        key={ci}
                        className="h-9 px-4 text-fg border-b border-border font-mono tabular-nums text-xs whitespace-nowrap max-w-xs truncate"
                        title={cell}
                      >
                        {cell === 'NULL' ? (
                          <span className="text-subtle">NULL</span>
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
                      className="px-4 py-6 text-center text-sm text-muted"
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
        <div className="px-4 py-8 text-center text-sm text-muted">
          Enter a SQL query above and press <span className="font-medium text-fg">Run</span>.
        </div>
      )}
    </div>
  )
}
