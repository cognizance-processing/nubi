/**
 * PythonCell — Python compute cell for the Nubi Playground (M4-B).
 *
 * Behaviour:
 *   1. User enters a Python snippet (default: `result = inputs['input']`) and
 *      an editable map of named inputs { name → query_id } (default one row:
 *      name 'input' → query 'demo_all').
 *   2. Clicks Run -> calls runPythonCell(code, inputsMap), where inputsMap is
 *      { name: query_id }. Each query's rows are bound as inputs[<name>].
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
 *   Body: { code, inputs?: { name: query_id }, timeout_s? }
 *   Response: Arrow IPC stream + X-Nubi-Tier header
 */

import { useState, useCallback } from 'react'
import { runPythonCell, SAMPLE_TABLE } from '../lib/wasmRuntime.js'
import CodeEditor from './CodeEditor.jsx'
import Button from './ui/Button.jsx'
import Tag from './ui/Tag.jsx'
import Spinner from './ui/Spinner.jsx'

/**
 * Default scaffold for a new Python cell.
 * Mirrors the flows python node convention: inputs, params → result.
 */
export const PYTHON_SCAFFOLD = `# Python transform — runs against the data above.
# Available:
#   inputs  — dict of upstream results keyed by cell ref (e.g. inputs['cell_1'])
#   params  — query params dict (e.g. params['region'])
# Bind your output to 'result' (a pyarrow Table, list, or dict).
result = inputs.get('input')`

const DEFAULT_CODE = PYTHON_SCAFFOLD
// Default named-inputs map: one row binding query 'demo_all' as inputs['input']
// (back-compat with the previous single-input behaviour).
const DEFAULT_INPUTS = [{ name: 'input', queryId: 'demo_all' }]
const MAX_ROWS = 100

// ---------------------------------------------------------------------------
// TierBadge helper
// ---------------------------------------------------------------------------

/**
 * Execution tier as a Tag: square, mono, a status dot (no tinted pill).
 *   local_kernel / remote_kernel  → accent
 *   sample                        → neutral (fallback, no backend hit)
 */
function TierBadge({ tier }) {
  if (!tier) return null

  const variant = tier === 'sample' ? 'neutral' : 'accent'
  return <Tag variant={variant} dot caps={false}>{tier}</Tag>
}

// ---------------------------------------------------------------------------
// PythonCell component
// ---------------------------------------------------------------------------

export default function PythonCell() {
  const [code, setCode] = useState(DEFAULT_CODE)
  // Editable list of { name, queryId } rows → sent as { name: query_id } map.
  const [inputRows, setInputRows] = useState(DEFAULT_INPUTS)

  const [result, setResult]       = useState(null)   // arrow.Table
  const [tier, setTier]           = useState(null)   // 'local_kernel' | 'sample' | ...
  const [elapsedMs, setElapsedMs] = useState(null)
  const [notice, setNotice]       = useState(null)   // non-blocking notice
  const [loading, setLoading]     = useState(false)

  // -- input-row editing helpers ------------------------------------------
  const updateRow = useCallback((idx, patch) => {
    setInputRows(rows => rows.map((r, i) => (i === idx ? { ...r, ...patch } : r)))
  }, [])
  const addRow = useCallback(() => {
    setInputRows(rows => [...rows, { name: '', queryId: '' }])
  }, [])
  const removeRow = useCallback((idx) => {
    setInputRows(rows => rows.filter((_, i) => i !== idx))
  }, [])

  const handleRun = useCallback(async () => {
    setLoading(true)
    setNotice(null)
    setResult(null)
    setTier(null)
    setElapsedMs(null)

    // Build the named-inputs map { name: query_id }, skipping blank rows.
    const inputs = {}
    for (const { name, queryId } of inputRows) {
      const n = name.trim()
      const q = queryId.trim()
      if (n && q) inputs[n] = q
    }

    const { table, tier: t, elapsedMs: ms, error } = await runPythonCell(code, inputs)

    setResult(table)
    setTier(t)
    setElapsedMs(ms)

    if (error) {
      setNotice(`Kernel unavailable — showing sample data. (${error})`)
    }

    setLoading(false)
  }, [code, inputRows])

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
    <div className="rounded-[var(--radius-sheet)] border border-border bg-surface overflow-hidden">
      {/* Code input area */}
      <div className="p-3 border-b border-border">
        <label className="block font-mono text-[11px] font-medium uppercase tracking-[0.06em] text-subtle mb-2">
          Python
        </label>
        {/* Monaco editor with Python syntax highlighting */}
        <CodeEditor
          value={code}
          onChange={setCode}
          language="python"
          height="160px"
          fontSize={13}
          onRun={loading ? undefined : handleRun}
          padding={{ top: 6, bottom: 6 }}
        />

        {/* Named inputs map: { name → query_id } */}
        <div className="mt-3">
          <label className="block font-mono text-[11px] font-medium uppercase tracking-[0.06em] text-subtle mb-1.5">
            Inputs
            <span className="ml-1 font-normal normal-case text-muted/70">
              (each row binds a query's rows as{' '}
              <code className="font-mono bg-surface-2 px-1 rounded-[2px]">inputs['&lt;name&gt;']</code>)
            </span>
          </label>

          <div className="space-y-1.5">
            {/* Column headers */}
            <div className="flex items-center gap-2 font-mono text-[11px] font-medium uppercase tracking-[0.06em] text-subtle">
              <span className="w-36">Name</span>
              <span className="flex-1">Query ID</span>
              <span className="w-7" aria-hidden="true" />
            </div>

            {inputRows.map((row, idx) => (
              <div key={idx} className="flex items-center gap-2">
                <input
                  type="text"
                  className="w-36 font-mono text-xs text-fg bg-surface-2 border border-border rounded-[var(--radius-control)] px-2.5 py-1.5 focus-visible:outline-none focus-visible:border-ink focus-visible:ring-2 focus-visible:ring-ring"
                  value={row.name}
                  onChange={e => updateRow(idx, { name: e.target.value })}
                  placeholder="input"
                  aria-label={`Input name ${idx + 1}`}
                />
                <input
                  type="text"
                  className="flex-1 font-mono text-xs text-fg bg-surface-2 border border-border rounded-[var(--radius-control)] px-2.5 py-1.5 focus-visible:outline-none focus-visible:border-ink focus-visible:ring-2 focus-visible:ring-ring"
                  value={row.queryId}
                  onChange={e => updateRow(idx, { queryId: e.target.value })}
                  placeholder="demo_all"
                  aria-label={`Input query ID ${idx + 1}`}
                />
                <button
                  type="button"
                  onClick={() => removeRow(idx)}
                  className="w-7 h-7 shrink-0 flex items-center justify-center text-muted hover:text-danger hover:bg-frame-hover rounded-[var(--radius-control)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                  aria-label={`Remove input ${idx + 1}`}
                  title="Remove input"
                >
                  &times;
                </button>
              </div>
            ))}
          </div>

          <button
            type="button"
            onClick={addRow}
            className="mt-1.5 text-[12px] font-medium text-primary underline-offset-2 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            + Add input
          </button>
        </div>

        {/* Run button row */}
        <div className="mt-3 flex items-center gap-2.5">
          <Button onClick={handleRun} disabled={loading || !code.trim()} loading={loading}>
            {loading ? 'Running…' : 'Run'}
          </Button>
          <kbd className="nubi-kbd select-none">Ctrl Enter</kbd>
        </div>
      </div>

      {/* Helper note */}
      <div className="px-3 py-2 bg-surface-2 border-b border-border text-[12px] text-muted">
        Code runs server-side in an on-demand kernel. Bind{' '}
        <code className="font-mono bg-surface-2 px-1 rounded-[2px] text-fg/80">result</code>{' '}
        to a pyarrow Table;{' '}
        each input row binds that query's rows as{' '}
        <code className="font-mono bg-surface-2 px-1 rounded-[2px] text-fg/80">inputs['&lt;name&gt;']</code>.
      </div>

      {/* Loading spinner */}
      {loading && (
        <div className="p-6 flex items-center gap-3 text-xs text-muted">
          <Spinner size="sm" label="Running kernel" />
          <span>Running kernel…</span>
        </div>
      )}

      {/* Non-blocking fallback notice */}
      {!loading && notice && (
        <div className="px-3 py-2 border-b border-border border-l-2 border-l-warning text-[12px] text-muted flex items-start gap-2">
          <span>{notice}</span>
        </div>
      )}

      {/* Results */}
      {!loading && result && (
        <div>
          {/* Metadata bar */}
          <div className="px-3 py-2 bg-surface-2 border-b border-border flex items-center justify-between text-[12px] text-muted">
            <span>
              rows:{' '}
              <span className="font-mono font-semibold text-fg">
                {result.numRows.toLocaleString()}
              </span>
              {result.numRows > MAX_ROWS && (
                <span className="ml-1 text-muted/70">(showing first {MAX_ROWS})</span>
              )}
            </span>
            <div className="flex items-center gap-2">
              {elapsedMs !== null && (
                <span className="font-mono text-muted">
                  {elapsedMs.toLocaleString()} ms
                </span>
              )}
              <TierBadge tier={tier} />
            </div>
          </div>

          {/* Table */}
          <div className="overflow-x-auto">
            <table className="w-full text-xs border-collapse">
              <thead>
                <tr className="bg-surface">
                  {columns.map(col => (
                    <th
                      key={col}
                      className="h-9 px-3 text-left font-mono text-[11px] font-medium uppercase tracking-[0.06em] text-subtle border-b-2 border-ink whitespace-nowrap"
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
                    className="hover:bg-frame-hover"
                  >
                    {row.map((cell, ci) => (
                      <td
                        key={ci}
                        className="h-9 px-3 text-fg border-b border-border font-mono tabular-nums whitespace-nowrap max-w-xs truncate"
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
                      className="px-3 py-6 text-center text-xs text-muted"
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
        <div className="px-4 py-8 text-center text-xs text-muted">
          Enter a Python snippet above and click{' '}
          <span className="font-medium text-fg/80">Run</span>.
        </div>
      )}
    </div>
  )
}
