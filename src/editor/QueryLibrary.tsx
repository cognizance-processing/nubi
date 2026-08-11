/**
 * QueryLibrary — page listing registered server-side queries with their SQL
 * (read-only Monaco editor) and declared params. Users can supply param values
 * and run any registered query via runArrowQueryById.
 *
 * Route: /queries  (behind ProtectedRoute — see App.jsx)
 *
 * Data flow:
 *   1. On mount, call listRegisteredQueries() → GET /api/v1/query/registry.
 *   2. Each query card shows: id, name, SQL in a read-only <SqlEditor>, and
 *      its declared params with simple text inputs.
 *   3. "Run" calls runArrowQueryById(id, { namedParams }) and shows the first
 *      100 rows of the Arrow result in an inline table.
 */

import { useState, useEffect, useCallback } from 'react'
import SqlEditor from '../components/SqlEditor.jsx'
import { listRegisteredQueries } from '../lib/api.js'
import { runArrowQueryById, SAMPLE_TABLE } from '../lib/wasmRuntime.js'

const MAX_ROWS = 100

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/**
 * Render an Arrow table's first MAX_ROWS rows as an HTML table.
 */
function ArrowTable({ table }) {
  if (!table) return null

  const columns = table.schema.fields.map(f => f.name)
  const numRows = Math.min(table.numRows, MAX_ROWS)
  const rows = Array.from({ length: numRows }, (_, i) =>
    columns.map(col => {
      const val = table.getChild(col)?.get(i)
      return val === null || val === undefined ? 'NULL' : String(val)
    })
  )

  return (
    <div className="overflow-x-auto rounded-lg border border-border">
      <table className="w-full text-sm border-collapse">
        <thead>
          <tr className="bg-surface-2">
            {columns.map(col => (
              <th
                key={col}
                className="px-3 py-2 text-left text-xs font-semibold text-muted border-b border-border whitespace-nowrap"
              >
                {col}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.length === 0 ? (
            <tr>
              <td
                colSpan={columns.length || 1}
                className="px-3 py-4 text-center text-xs text-muted"
              >
                No rows returned.
              </td>
            </tr>
          ) : (
            rows.map((row, ri) => (
              <tr key={ri} className={ri % 2 === 0 ? 'bg-surface' : 'bg-surface-2'}>
                {row.map((cell, ci) => (
                  <td
                    key={ci}
                    className="px-3 py-1.5 text-xs font-mono text-fg border-b border-border whitespace-nowrap max-w-xs truncate"
                    title={cell}
                  >
                    {cell === 'NULL' ? (
                      <span className="text-muted italic">NULL</span>
                    ) : (
                      cell
                    )}
                  </td>
                ))}
              </tr>
            ))
          )}
        </tbody>
      </table>
      {table.numRows > MAX_ROWS && (
        <p className="px-3 py-1.5 text-xs text-muted border-t border-border bg-surface-2">
          Showing first {MAX_ROWS} of {table.numRows.toLocaleString()} rows.
        </p>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// ParamInput — a single named param input
// ---------------------------------------------------------------------------

function ParamInput({ param, value, onChange }) {
  const id = `param-${param.name}`
  return (
    <div className="flex flex-col gap-1">
      <label htmlFor={id} className="text-xs font-medium text-fg">
        <span className="font-mono">{param.name}</span>
        <span className="ml-1 text-muted">({param.type})</span>
        {param.required && <span className="ml-1 text-red-500">*</span>}
      </label>
      <input
        id={id}
        type={param.type === 'number' ? 'number' : 'text'}
        value={value ?? ''}
        onChange={e => onChange(param.name, e.target.value)}
        placeholder={
          param.default !== undefined && param.default !== null
            ? `default: ${param.default}`
            : param.required
              ? 'required'
              : 'optional'
        }
        className="px-3 py-1.5 text-sm border border-border rounded-lg bg-surface text-fg placeholder:text-muted focus:outline-none focus:ring-2 focus:ring-ring transition-colors"
      />
    </div>
  )
}

// ---------------------------------------------------------------------------
// QueryCard — a single registered query
// ---------------------------------------------------------------------------

function QueryCard({ query }) {
  const [paramValues, setParamValues] = useState(() => {
    // Seed defaults from declared params
    const init = {}
    if (Array.isArray(query.params)) {
      query.params.forEach(p => {
        if (p.default !== undefined && p.default !== null) {
          init[p.name] = String(p.default)
        }
      })
    }
    return init
  })

  const [result, setResult] = useState(null)         // arrow.Table | null
  const [running, setRunning] = useState(false)
  const [elapsedMs, setElapsedMs] = useState(null)
  const [cacheStatus, setCacheStatus] = useState(null)
  const [notice, setNotice] = useState(null)
  const [error, setError] = useState(null)

  const handleParamChange = useCallback((name, value) => {
    setParamValues(prev => ({ ...prev, [name]: value }))
  }, [])

  const handleRun = useCallback(async () => {
    setRunning(true)
    setResult(null)
    setError(null)
    setNotice(null)
    setElapsedMs(null)
    setCacheStatus(null)

    // Build namedParams — only include non-empty values
    const namedParams = {}
    Object.entries(paramValues).forEach(([k, v]) => {
      if (v !== '' && v !== undefined) namedParams[k] = v
    })

    try {
      const { table, cacheStatus: cs, elapsedMs: ms } = await runArrowQueryById(
        query.id,
        { namedParams: Object.keys(namedParams).length > 0 ? namedParams : undefined },
      )
      setResult(table)
      setCacheStatus(cs)
      setElapsedMs(ms)
      if (cs === 'SAMPLE') {
        setNotice('Backend unavailable — showing sample data.')
      }
    } catch (err) {
      setError(err.message)
    } finally {
      setRunning(false)
    }
  }, [query.id, paramValues])

  const hasParams = Array.isArray(query.params) && query.params.length > 0
  const sqlHeight = Math.max(80, Math.min(300, (query.sql?.split('\n').length ?? 1) * 20 + 40))

  return (
    <div className="bg-surface border border-border rounded-xl overflow-hidden" data-testid="query-card">
      {/* Card header */}
      <div className="px-4 py-3 border-b border-border bg-surface-2 flex items-start justify-between gap-4">
        <div className="min-w-0">
          <h2 className="text-sm font-semibold text-fg truncate" data-testid="query-card-name">
            {query.name ?? query.id}
          </h2>
          <p className="text-xs font-mono text-muted mt-0.5">{query.id}</p>
        </div>
        <button
          onClick={handleRun}
          disabled={running}
          data-testid="query-run-btn"
          className="shrink-0 px-4 py-1.5 text-sm font-medium bg-primary text-primary-fg rounded-lg hover:opacity-90 disabled:opacity-50 disabled:cursor-not-allowed transition-opacity focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-1"
        >
          {running ? 'Running…' : 'Run'}
        </button>
      </div>

      <div className="p-4 space-y-4">
        {/* SQL viewer */}
        <div>
          <p className="text-xs font-semibold text-muted uppercase tracking-wider mb-2">SQL</p>
          <SqlEditor
            value={query.sql ?? '-- no SQL available'}
            readOnly
            height={`${sqlHeight}px`}
          />
        </div>

        {/* Declared params */}
        {hasParams && (
          <div>
            <p className="text-xs font-semibold text-muted uppercase tracking-wider mb-2">
              Parameters
            </p>
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
              {query.params.map(param => (
                <ParamInput
                  key={param.name}
                  param={param}
                  value={paramValues[param.name] ?? ''}
                  onChange={handleParamChange}
                />
              ))}
            </div>
          </div>
        )}

        {/* Errors / notices */}
        {!running && notice && (
          <div
            className="text-xs px-3 py-2 rounded-lg border"
            style={{
              background: 'color-mix(in srgb, #f59e0b 8%, transparent)',
              color: '#d97706',
              borderColor: 'color-mix(in srgb, #f59e0b 20%, transparent)',
            }}
          >
            {notice}
          </div>
        )}
        {!running && error && (
          <div
            className="text-xs px-3 py-2 rounded-lg border"
            style={{
              background: 'color-mix(in srgb, #ef4444 8%, transparent)',
              color: '#dc2626',
              borderColor: 'color-mix(in srgb, #ef4444 20%, transparent)',
            }}
          >
            {error}
          </div>
        )}

        {/* Result */}
        {!running && result && (
          <div data-testid="query-result">
            <div className="flex items-center justify-between mb-2">
              <p className="text-xs font-semibold text-muted uppercase tracking-wider">
                Result
              </p>
              <div className="flex items-center gap-2 text-xs text-muted">
                {elapsedMs !== null && (
                  <span className="font-mono">{elapsedMs.toLocaleString()} ms</span>
                )}
                {cacheStatus && (
                  <span
                    className={`px-2 py-0.5 rounded-full font-semibold ${
                      cacheStatus === 'HIT'
                        ? 'bg-green-100 text-green-700'
                        : cacheStatus === 'MISS'
                          ? 'bg-amber-100 text-amber-700'
                          : 'bg-gray-100 text-gray-500'
                    }`}
                  >
                    {cacheStatus}
                  </span>
                )}
                <span className="font-mono">
                  {result.numRows.toLocaleString()} rows
                </span>
              </div>
            </div>
            <ArrowTable table={result} />
          </div>
        )}

        {/* Loading */}
        {running && (
          <div className="flex items-center gap-2 text-xs text-muted py-2">
            <div className="h-3 w-3 rounded-full border-2 border-primary border-t-transparent animate-spin" />
            Running query…
          </div>
        )}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// QueryLibrary page
// ---------------------------------------------------------------------------

export default function QueryLibrary() {
  const [queries, setQueries] = useState(null)   // null = not yet loaded
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState(null)

  const load = useCallback(async () => {
    setLoading(true)
    setLoadError(null)
    try {
      const data = await listRegisteredQueries()
      setQueries(data)
    } catch (err) {
      setLoadError(err.message)
      setQueries([])
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load() }, [load])

  return (
    <div className="max-w-5xl mx-auto px-4 sm:px-6 lg:px-8 py-10" data-testid="query-library-page">
      {/* Page header */}
      <div className="mb-8 flex items-start justify-between gap-4">
        <div>
          <h1 className="text-3xl font-bold font-display text-fg">Query Library</h1>
          <p className="mt-2 text-sm text-muted leading-relaxed">
            Browse and run registered server-side queries. Supply any declared parameter
            values and click Run to stream results as Arrow batches.
          </p>
        </div>
        <button
          onClick={load}
          disabled={loading}
          className="shrink-0 flex items-center gap-1.5 px-3 py-1.5 text-sm font-medium text-fg border border-border bg-surface rounded-lg hover:bg-surface-2 disabled:opacity-50 disabled:cursor-not-allowed transition-colors focus:outline-none focus:ring-2 focus:ring-ring"
        >
          <span className={loading ? 'animate-spin inline-block' : 'inline-block'} aria-hidden>&#8635;</span>
          Refresh
        </button>
      </div>

      {/* Loading state */}
      {loading && queries === null && (
        <div className="flex items-center gap-3 text-sm text-muted py-12 justify-center">
          <div className="h-4 w-4 rounded-full border-2 border-primary border-t-transparent animate-spin" />
          Loading registered queries…
        </div>
      )}

      {/* Load error */}
      {!loading && loadError && (
        <div
          className="text-sm px-4 py-3 rounded-xl border mb-6"
          style={{
            background: 'color-mix(in srgb, #ef4444 8%, transparent)',
            color: '#dc2626',
            borderColor: 'color-mix(in srgb, #ef4444 20%, transparent)',
          }}
        >
          Failed to load query registry: {loadError}
        </div>
      )}

      {/* Empty state */}
      {!loading && queries !== null && queries.length === 0 && !loadError && (
        <div className="text-center py-16 text-sm text-muted">
          <p className="text-lg font-medium text-fg mb-2">No registered queries</p>
          <p>Register queries on the backend via the query registry to see them here.</p>
        </div>
      )}

      {/* Query list */}
      {queries !== null && queries.length > 0 && (
        <div className="space-y-6" data-testid="query-list">
          {queries.map(query => (
            <QueryCard key={query.id} query={query} />
          ))}
        </div>
      )}

      {/* Footer note */}
      {queries !== null && queries.length > 0 && (
        <p className="mt-6 text-xs text-muted">
          Tip: named params override defaults but cannot override token-claim values (server-enforced).
        </p>
      )}
    </div>
  )
}
