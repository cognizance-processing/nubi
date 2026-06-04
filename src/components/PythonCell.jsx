/**
 * PythonCell — Python compute cell for the Nubi Playground (M4-B).
 *
 * Behaviour:
 *   1. User enters a Python snippet (default: `result = inputs['input']`) and
 *      an optional input query ID (default: 'demo_all').
 *   2. Clicks Run -> calls runPythonCell(code, inputQueryId).
 *   3. Displays the resulting Arrow Table (first 100 rows) with:
 *        - Tier badge: local_kernel (blue) / sample (gray)
 *        - "elapsed: N ms" timing
 *        - Row count
 *   4. On backend failure / embed token (403), falls back to SAMPLE_TABLE
 *      and shows a non-blocking notice with the error detail.
 *
 * The server-side contract (M4 spec):
 *   POST /api/v1/compute/run
 *   Authorization: Bearer <first-party token>   (embed tokens get 403)
 *   Body: { code, input_query_id?, timeout_s? }
 *   Response: Arrow IPC stream + X-Nubi-Tier header
 */

import { useState, useCallback } from 'react'
import { runPythonCell, SAMPLE_TABLE } from '../lib/wasmRuntime.js'

const DEFAULT_CODE = "result = inputs['input']"
const DEFAULT_INPUT_QUERY_ID = 'demo_all'
const MAX_ROWS = 100

// ---------------------------------------------------------------------------
// TierBadge helper
// ---------------------------------------------------------------------------

/**
 * Renders a small coloured pill for the execution tier.
 *   local_kernel   → blue
 *   remote_kernel  → indigo
 *   sample         → gray  (fallback — no backend hit)
 */
function TierBadge({ tier }) {
  if (!tier) return null

  const styles = {
    local_kernel:  'bg-blue-100 text-blue-700',
    remote_kernel: 'bg-indigo-100 text-indigo-700',
    sample:        'bg-gray-100 text-gray-500',
  }

  const label = {
    local_kernel:  'local_kernel',
    remote_kernel: 'remote_kernel',
    sample:        'sample',
  }

  const cls = `px-2 py-0.5 rounded-full text-xs font-semibold ${
    styles[tier] ?? 'bg-gray-100 text-gray-500'
  }`

  return <span className={cls}>{label[tier] ?? tier}</span>
}

// ---------------------------------------------------------------------------
// PythonCell component
// ---------------------------------------------------------------------------

export default function PythonCell() {
  const [code, setCode] = useState(DEFAULT_CODE)
  const [inputQueryId, setInputQueryId] = useState(DEFAULT_INPUT_QUERY_ID)

  const [result, setResult]       = useState(null)   // arrow.Table
  const [tier, setTier]           = useState(null)   // 'local_kernel' | 'sample' | ...
  const [elapsedMs, setElapsedMs] = useState(null)
  const [notice, setNotice]       = useState(null)   // non-blocking notice
  const [loading, setLoading]     = useState(false)

  const handleRun = useCallback(async () => {
    setLoading(true)
    setNotice(null)
    setResult(null)
    setTier(null)
    setElapsedMs(null)

    const queryId = inputQueryId.trim() || undefined

    const { table, tier: t, elapsedMs: ms, error } = await runPythonCell(code, queryId)

    setResult(table)
    setTier(t)
    setElapsedMs(ms)

    if (error) {
      setNotice(`Kernel unavailable — showing sample data. (${error})`)
    }

    setLoading(false)
  }, [code, inputQueryId])

  // Derived table rendering helpers
  const columns = result ? result.schema.fields.map(f => f.name) : []
  const rows = result
    ? Array.from({ length: Math.min(result.numRows, MAX_ROWS) }, (_, i) =>
        columns.map(col => {
          const val = result.getChild(col)?.get(i)
          return val === null || val === undefined ? 'NULL' : String(val)
        })
      )
    : []

  return (
    <div className="rounded-xl border border-gray-200 bg-white shadow-sm overflow-hidden">
      {/* Code input area */}
      <div className="p-4 border-b border-gray-100">
        <label className="block text-xs font-medium text-gray-500 mb-2 uppercase tracking-wide">
          Python
        </label>
        <textarea
          className="w-full font-mono text-sm text-gray-800 bg-gray-50 border border-gray-200 rounded-lg p-3 resize-y min-h-[100px] focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent transition"
          value={code}
          onChange={e => setCode(e.target.value)}
          onKeyDown={e => {
            if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
              e.preventDefault()
              if (!loading) handleRun()
            }
          }}
          spellCheck={false}
          placeholder="result = inputs['input']"
          aria-label="Python code input"
        />

        {/* Input query ID field */}
        <div className="mt-3">
          <label className="block text-xs font-medium text-gray-500 mb-1 uppercase tracking-wide">
            Input query ID
            <span className="ml-1 font-normal normal-case text-gray-400">(optional — bound as <code className="font-mono bg-gray-100 px-1 rounded">inputs[&#39;input&#39;]</code>)</span>
          </label>
          <input
            type="text"
            className="w-full sm:w-64 font-mono text-sm text-gray-800 bg-gray-50 border border-gray-200 rounded-lg px-3 py-1.5 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent transition"
            value={inputQueryId}
            onChange={e => setInputQueryId(e.target.value)}
            placeholder="demo_all"
            aria-label="Input query ID"
          />
        </div>

        {/* Run button row */}
        <div className="mt-3 flex items-center gap-3">
          <button
            onClick={handleRun}
            disabled={loading || !code.trim()}
            className="px-4 py-2 text-sm font-medium text-white bg-blue-600 rounded-lg hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-1"
          >
            {loading ? 'Running…' : 'Run'}
          </button>
          <span className="text-xs text-gray-400">Ctrl+Enter</span>
        </div>
      </div>

      {/* Helper note */}
      <div className="px-4 py-2 bg-blue-50 border-b border-blue-100 text-xs text-blue-700">
        Code runs server-side in an on-demand kernel. Bind <code className="font-mono bg-blue-100 px-1 rounded">result</code> to a pyarrow Table;{' '}
        <code className="font-mono bg-blue-100 px-1 rounded">inputs[&#39;input&#39;]</code> holds the input query&#39;s rows.
      </div>

      {/* Loading spinner */}
      {loading && (
        <div className="p-6 flex items-center gap-3 text-sm text-gray-500">
          <div
            className="h-4 w-4 rounded-full border-2 border-blue-600 border-t-transparent animate-spin"
            role="status"
            aria-label="Running kernel"
          />
          <span>Running kernel…</span>
        </div>
      )}

      {/* Non-blocking fallback notice */}
      {!loading && notice && (
        <div className="px-4 py-2 bg-amber-50 border-b border-amber-100 text-xs text-amber-700 flex items-start gap-2">
          <span className="shrink-0 mt-0.5">&#9888;</span>
          <span>{notice}</span>
        </div>
      )}

      {/* Results */}
      {!loading && result && (
        <div>
          {/* Metadata bar */}
          <div className="px-4 py-2 bg-gray-50 border-b border-gray-100 flex items-center justify-between text-xs text-gray-500">
            <span>
              rows:{' '}
              <span className="font-mono font-semibold text-gray-700">
                {result.numRows.toLocaleString()}
              </span>
              {result.numRows > MAX_ROWS && (
                <span className="ml-1 text-gray-400">(showing first {MAX_ROWS})</span>
              )}
            </span>
            <div className="flex items-center gap-2">
              {elapsedMs !== null && (
                <span className="font-mono text-gray-400">
                  elapsed: {elapsedMs.toLocaleString()} ms
                </span>
              )}
              <TierBadge tier={tier} />
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
      {!loading && !result && !notice && (
        <div className="px-4 py-8 text-center text-sm text-gray-400">
          Enter a Python snippet above and click{' '}
          <span className="font-medium text-gray-500">Run</span>.
        </div>
      )}
    </div>
  )
}
