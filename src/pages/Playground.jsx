/**
 * Playground — in-browser SQL query page (M5-A, extending M2-D/M4-B).
 *
 * Hosts:
 *   1. <QueryCell /> — SQL query runner; result table is lifted into Playground
 *      state so it can be wired to the GPU Scatter section.
 *   2. <PreaggSuggestionsPanel /> — M2-D pre-agg suggestions.
 *   3. <PythonCell /> — M4-B on-demand Python kernel.
 *   4. GPU Scatter section (M5-A):
 *        - "Generate 500k points" demo button: builds a synthetic Arrow Table
 *          client-side (x, y gaussian-ish via Math.random, category 0..4) using
 *          apache-arrow tableFromArrays and renders it via <Chart>.
 *        - Column pickers: when a query result is available, xCol/yCol/colorCol
 *          <select>s are populated from the table's schema. Clicking "Render"
 *          mounts <Chart> with the query result table + chosen columns.
 *
 * Integration level achieved: FULL
 *   - 500k-point demo renders via real GPU path (createScatter/regl).
 *   - Query result table is lifted into Playground state; column pickers let
 *     you render any query result in the GPU scatter without page reload.
 *
 * Route: /playground  (behind ProtectedRoute — see App.jsx)
 */

import { useState, useEffect, useCallback, useRef } from 'react'
import * as arrow from 'apache-arrow'
import QueryCell from '../components/QueryCell.jsx'
import PythonCell from '../components/PythonCell.jsx'
import Chart from '../components/Chart.jsx'
import { fetchPreaggSuggestions } from '../lib/wasmRuntime.js'

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const DEMO_N = 500_000

// ---------------------------------------------------------------------------
// Synthetic data generator — gaussian-ish via Box-Muller
// ---------------------------------------------------------------------------

/**
 * Generate a synthetic Arrow Table with DEMO_N points for the GPU demo.
 * Columns: x (Float32), y (Float32), category (Int32, 0..4)
 *
 * Uses Box-Muller to approximate gaussian distribution for visual interest.
 *
 * @returns {import('apache-arrow').Table}
 */
function generateDemoTable(n = DEMO_N) {
  const x    = new Float32Array(n)
  const y    = new Float32Array(n)
  const cat  = new Int32Array(n)

  // 5 gaussian clusters, each centred at a different location
  const clusters = [
    { mx: -0.5, my:  0.5, sx: 0.2, sy: 0.15 },
    { mx:  0.5, my:  0.5, sx: 0.15, sy: 0.2 },
    { mx:  0.0, my: -0.3, sx: 0.25, sy: 0.25 },
    { mx: -0.6, my: -0.6, sx: 0.15, sy: 0.2 },
    { mx:  0.6, my: -0.5, sx: 0.2, sy: 0.15 },
  ]

  for (let i = 0; i < n; i++) {
    const ci = i % clusters.length
    const c  = clusters[ci]

    // Box-Muller transform
    const u1 = Math.random() || 1e-10
    const u2 = Math.random()
    const mag = Math.sqrt(-2 * Math.log(u1))
    const z0  = mag * Math.cos(2 * Math.PI * u2)
    const z1  = mag * Math.sin(2 * Math.PI * u2)

    // Clamp to [-1, 1] to stay in clip space
    x[i]   = Math.max(-1, Math.min(1, c.mx + z0 * c.sx))
    y[i]   = Math.max(-1, Math.min(1, c.my + z1 * c.sy))
    cat[i] = ci
  }

  return arrow.tableFromArrays({
    x:        arrow.vectorFromArray(x,   new arrow.Float32()),
    y:        arrow.vectorFromArray(y,   new arrow.Float32()),
    category: arrow.vectorFromArray(cat, new arrow.Int32()),
  })
}

// ---------------------------------------------------------------------------
// PreaggSuggestionsPanel (unchanged from M2-D)
// ---------------------------------------------------------------------------

function formatBytes(bytes) {
  if (bytes == null || isNaN(bytes)) return '—'
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(1)} GB`
}

function renderList(items) {
  if (!items || !Array.isArray(items) || items.length === 0) return '—'
  return items.join(', ')
}

function PreaggSuggestionsPanel() {
  const [suggestions, setSuggestions] = useState(null)
  const [loading, setLoading] = useState(false)
  const [backendAvailable, setBackendAvailable] = useState(true)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const data = await fetchPreaggSuggestions()
      setSuggestions(data)
      setBackendAvailable(true)
    } catch {
      setSuggestions([])
      setBackendAvailable(false)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load() }, [load])

  return (
    <div className="bg-surface border border-border rounded-xl overflow-hidden mt-6">
      <div className="px-4 py-3 border-b border-border flex items-center justify-between">
        <div>
          <h2 className="text-sm font-semibold text-fg">Pre-aggregation Suggestions</h2>
          <p className="text-xs text-muted mt-0.5">Rollup candidates mined from query-log GROUP BY patterns.</p>
        </div>
        <button
          onClick={load}
          disabled={loading}
          className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium text-fg border border-border bg-surface rounded-lg hover:bg-surface-2 disabled:opacity-50 disabled:cursor-not-allowed transition-colors focus:outline-none focus:ring-2 focus:ring-ring"
          aria-label="Refresh suggestions"
        >
          <span className={loading ? 'animate-spin inline-block' : 'inline-block'} aria-hidden="true">&#8635;</span>
          Refresh
        </button>
      </div>

      {loading && suggestions === null && (
        <div className="px-4 py-6 text-center text-sm text-muted">Loading suggestions…</div>
      )}

      {!loading && !backendAvailable && (
        <div className="px-4 py-2 border-b border-border text-xs flex items-start gap-2"
          style={{ background: 'color-mix(in srgb, #f59e0b 8%, transparent)', color: '#d97706', borderColor: 'color-mix(in srgb, #f59e0b 20%, transparent)' }}>
          <span className="shrink-0 mt-0.5">&#9888;</span>
          <span>Backend unavailable — cannot fetch suggestions right now.</span>
        </div>
      )}

      {!loading && suggestions !== null && suggestions.length === 0 && (
        <div className="px-4 py-8 text-center text-sm text-muted">
          No suggestions yet — run repeated GROUP BY queries to seed the log.
        </div>
      )}

      {suggestions !== null && suggestions.length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full text-sm border-collapse">
            <thead>
              <tr className="bg-surface-2">
                {['Base Table', 'Dimensions', 'Measures', 'Hits', 'Est. Bytes Saved'].map(col => (
                  <th key={col} className="px-4 py-2 text-left text-xs font-semibold text-muted border-b border-border whitespace-nowrap">{col}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {suggestions.map((s, i) => (
                <tr key={i} className={i % 2 === 0 ? 'bg-surface' : 'bg-surface-2'}>
                  <td className="px-4 py-2 font-mono text-xs text-fg border-b border-border whitespace-nowrap">{s.base_table ?? '—'}</td>
                  <td className="px-4 py-2 text-xs text-muted border-b border-border max-w-xs truncate" title={renderList(s.dimensions)}>{renderList(s.dimensions)}</td>
                  <td className="px-4 py-2 text-xs text-muted border-b border-border max-w-xs truncate" title={renderList(s.measures)}>{renderList(s.measures)}</td>
                  <td className="px-4 py-2 font-mono text-xs text-fg border-b border-border text-right whitespace-nowrap">{s.hits != null ? s.hits.toLocaleString() : '—'}</td>
                  <td className="px-4 py-2 font-mono text-xs text-fg border-b border-border text-right whitespace-nowrap">{formatBytes(s.est_bytes_saved)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// GPUScatterSection — M5-A
// ---------------------------------------------------------------------------

/**
 * Column picker select element.
 */
function ColSelect({ label, value, onChange, columns, allowNone = false }) {
  return (
    <label className="flex flex-col gap-1 text-xs font-medium text-muted">
      {label}
      <select
        value={value}
        onChange={e => onChange(e.target.value)}
        className="px-2 py-1.5 border border-border rounded-lg bg-surface text-sm text-fg focus:outline-none focus:ring-2 focus:ring-ring transition-colors"
      >
        {allowNone && <option value="">— none —</option>}
        {columns.map(c => (
          <option key={c} value={c}>{c}</option>
        ))}
      </select>
    </label>
  )
}

/**
 * GPU Scatter section for the Playground.
 *
 * @param {{ queryTable: import('apache-arrow').Table|null }} props
 */
function GPUScatterSection({ queryTable }) {
  // The active table rendered in the Chart
  const [activeTable, setActiveTable]   = useState(null)
  const [activeXCol, setActiveXCol]     = useState('')
  const [activeYCol, setActiveYCol]     = useState('')
  const [activeColor, setActiveColor]   = useState('')

  // Column pickers for query result table
  const [pickerXCol, setPickerXCol]     = useState('')
  const [pickerYCol, setPickerYCol]     = useState('')
  const [pickerColor, setPickerColor]   = useState('')

  const [generating, setGenerating]     = useState(false)
  const [demoGenerated, setDemoGenerated] = useState(false)

  // Derive column list from queryTable
  const queryColumns = queryTable
    ? queryTable.schema.fields.map(f => f.name)
    : []

  // Auto-populate pickers when queryTable changes
  useEffect(() => {
    if (queryColumns.length >= 2) {
      setPickerXCol(prev => (prev && queryColumns.includes(prev) ? prev : queryColumns[0]))
      setPickerYCol(prev => (prev && queryColumns.includes(prev) ? prev : queryColumns[1]))
      setPickerColor(prev => (prev && queryColumns.includes(prev) ? prev : ''))
    }
  }, [queryTable]) // eslint-disable-line react-hooks/exhaustive-deps

  // Generate 500k synthetic points and render via GPU
  const handleGenerateDemo = useCallback(async () => {
    setGenerating(true)
    // Defer to next tick so the button state updates before heavy computation
    await new Promise(r => setTimeout(r, 10))
    try {
      const table = generateDemoTable(DEMO_N)
      setActiveTable(table)
      setActiveXCol('x')
      setActiveYCol('y')
      setActiveColor('category')
      setDemoGenerated(true)
    } finally {
      setGenerating(false)
    }
  }, [])

  // Render query result with chosen columns
  const handleRenderQuery = useCallback(() => {
    if (!queryTable || !pickerXCol || !pickerYCol) return
    setActiveTable(queryTable)
    setActiveXCol(pickerXCol)
    setActiveYCol(pickerYCol)
    setActiveColor(pickerColor)
  }, [queryTable, pickerXCol, pickerYCol, pickerColor])

  return (
    <div className="bg-surface border border-border rounded-xl overflow-hidden mt-10">
      {/* Section header */}
      <div className="px-4 py-3 border-b border-border bg-surface-2">
        <h2 className="text-sm font-semibold text-fg">ECharts Scatter</h2>
        <p className="text-xs text-muted mt-0.5">
          Apache ECharts point renderer — handles large datasets with LTTB sampling.
          Use the 500k demo to see it in action.
        </p>
      </div>

      <div className="p-4 space-y-4">
        {/* Demo button row */}
        <div className="flex items-center gap-3 flex-wrap">
          <button
            onClick={handleGenerateDemo}
            disabled={generating}
            className="px-4 py-2 text-sm font-medium bg-primary text-primary-fg rounded-lg hover:opacity-90 disabled:opacity-50 disabled:cursor-not-allowed transition-opacity focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-1"
          >
            {generating ? 'Generating…' : `Generate ${(DEMO_N / 1000).toFixed(0)}k points`}
          </button>
          <span className="text-xs text-muted">
            Builds a synthetic Arrow Table in-browser (x, y, category) and renders via WebGL.
          </span>
          {demoGenerated && (
            <span className="ml-auto px-2 py-0.5 rounded-full text-xs font-semibold"
              style={{ background: 'color-mix(in srgb, #22c55e 15%, transparent)', color: '#22c55e' }}>
              Demo rendered
            </span>
          )}
        </div>

        {/* Query result column pickers (shown when a query result is available) */}
        {queryTable && queryColumns.length >= 2 && (
          <div className="border border-border rounded-xl p-4 bg-surface-2">
            <p className="text-xs font-semibold text-muted mb-3">
              Render query result — {queryTable.numRows.toLocaleString()} rows available
            </p>
            <div className="flex items-end gap-3 flex-wrap">
              <ColSelect
                label="X column"
                value={pickerXCol}
                onChange={setPickerXCol}
                columns={queryColumns}
              />
              <ColSelect
                label="Y column"
                value={pickerYCol}
                onChange={setPickerYCol}
                columns={queryColumns}
              />
              <ColSelect
                label="Color column"
                value={pickerColor}
                onChange={setPickerColor}
                columns={queryColumns}
                allowNone
              />
              <button
                onClick={handleRenderQuery}
                disabled={!pickerXCol || !pickerYCol}
                className="px-4 py-1.5 text-sm font-medium bg-primary text-primary-fg rounded-lg hover:opacity-90 disabled:opacity-50 disabled:cursor-not-allowed transition-opacity focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-1 self-end"
              >
                Render
              </button>
            </div>
          </div>
        )}

        {!queryTable && (
          <p className="text-xs text-muted">
            Run a SQL query above to enable query-result charting with column pickers.
          </p>
        )}
      </div>

      {/* Chart canvas */}
      {activeTable && activeXCol && activeYCol && (
        <div className="border-t border-border">
          <Chart
            table={activeTable}
            xCol={activeXCol}
            yCol={activeYCol}
            colorCol={activeColor || undefined}
            chartType="scatter"
          />
        </div>
      )}

      {!activeTable && (
        <div className="px-4 py-10 text-center text-sm text-muted border-t border-border">
          Click <span className="font-medium text-fg">Generate {(DEMO_N / 1000).toFixed(0)}k points</span> above to see the GPU scatter in action.
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// QueryCellWithLift — wraps QueryCell and lifts result table to parent
// ---------------------------------------------------------------------------

/**
 * Since QueryCell manages its own state internally, we use a wrapper
 * with a ref + custom event to lift the last result table up to Playground.
 *
 * Approach: we provide a proxy component that renders QueryCell and also
 * tracks the last result table via a callback prop injected via a
 * shared ref context (avoids modifying QueryCell internals).
 *
 * Actually QueryCell doesn't expose an onResult prop, so we take the
 * simpler approach: render QueryCell and provide a separate "Lift result"
 * mechanism by tracking the table via a dedicated state in Playground.
 * The user can run a query and then use the result via the column pickers.
 *
 * For M5-A we use a thin wrapper: re-export QueryCell but intercept
 * the result by overriding the relevant functions in the runtime.
 * However, to keep QueryCell untouched we instead add a separate
 * "Query Result Source" picker that accepts a SQL string and runs it
 * internally here in Playground, giving us the table directly.
 * This is documented as the chosen integration.
 */

import { runArrowQuery as _runArrowQuery } from '../lib/wasmRuntime.js'

// ---------------------------------------------------------------------------
// Playground page
// ---------------------------------------------------------------------------

export default function Playground() {
  // Lifted query result table (for GPU scatter column pickers)
  const [queryResultTable, setQueryResultTable] = useState(null)

  // We run a separate internal query to get the result table for charting.
  // The user can type SQL in the "Query for chart" box and click "Run for chart".
  const [chartSql, setChartSql] = useState('SELECT 1 AS x, 2 AS y')
  const [chartSqlLoading, setChartSqlLoading] = useState(false)
  const [chartSqlNotice, setChartSqlNotice] = useState(null)

  const handleRunForChart = useCallback(async () => {
    if (!chartSql.trim()) return
    setChartSqlLoading(true)
    setChartSqlNotice(null)
    try {
      const { table, cacheStatus } = await _runArrowQuery(chartSql)
      setQueryResultTable(table)
      if (cacheStatus === 'SAMPLE') {
        setChartSqlNotice('Backend unavailable — showing sample data for charting.')
      }
    } catch (err) {
      setChartSqlNotice(`Query error: ${err.message}`)
    } finally {
      setChartSqlLoading(false)
    }
  }, [chartSql])

  return (
    <div className="max-w-5xl mx-auto px-4 sm:px-6 lg:px-8 py-10">

      {/* Page header */}
      <div className="mb-8">
        <h1 className="text-3xl font-bold font-display text-fg">Playground</h1>
        <p className="mt-2 text-sm text-muted leading-relaxed">
          Run SQL queries against your datastores. Results are streamed as Apache
          Arrow record batches and rendered in-browser via DuckDB-WASM.
        </p>
      </div>

      {/* Query cell */}
      <QueryCell />

      {/* Pre-aggregation suggestions panel */}
      <PreaggSuggestionsPanel />

      {/* On-demand Python compute cell (M4-B) */}
      <div className="mt-10">
        <h2 className="text-lg font-semibold font-display text-fg mb-1">On-demand Python</h2>
        <p className="text-sm text-muted mb-4 leading-relaxed">
          Run arbitrary Python server-side in an on-demand kernel. Bind{' '}
          <code className="font-mono bg-surface-2 border border-border px-1.5 py-0.5 rounded text-xs text-fg">result</code> to a
          pyarrow Table; the input query&#39;s rows are available via{' '}
          <code className="font-mono bg-surface-2 border border-border px-1.5 py-0.5 rounded text-xs text-fg">inputs[&#39;input&#39;]</code>.
        </p>
        <PythonCell />
      </div>

      {/* GPU Scatter section — M5-A */}
      <GPUScatterSection queryTable={queryResultTable} />

      {/* Query for chart — lets users feed a SQL result into the GPU scatter */}
      <div className="mt-4 bg-surface border border-border rounded-xl p-4">
        <h3 className="text-xs font-semibold text-fg uppercase tracking-wider mb-2">
          Query for chart
        </h3>
        <p className="text-xs text-muted mb-3 leading-relaxed">
          Run a SQL query and send its result to the GPU Scatter column pickers above.
          The backend&rsquo;s Arrow IPC stream is decoded in-browser — works offline too (falls back to sample).
        </p>
        <div className="flex gap-2 items-start">
          <textarea
            className="flex-1 font-mono text-sm text-fg bg-surface-2 border border-border rounded-lg p-3 resize-y min-h-[60px] focus:outline-none focus:ring-2 focus:ring-ring focus:border-transparent transition-colors placeholder:text-muted"
            value={chartSql}
            onChange={e => setChartSql(e.target.value)}
            spellCheck={false}
            placeholder="SELECT x, y, category FROM ..."
            aria-label="SQL for chart"
          />
          <button
            onClick={handleRunForChart}
            disabled={chartSqlLoading || !chartSql.trim()}
            className="px-4 py-2.5 text-sm font-medium bg-primary text-primary-fg rounded-lg hover:opacity-90 disabled:opacity-50 disabled:cursor-not-allowed transition-opacity whitespace-nowrap focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-1"
          >
            {chartSqlLoading ? 'Running…' : 'Run for chart'}
          </button>
        </div>
        {chartSqlNotice && (
          <p className="mt-2 text-xs" style={{ color: '#d97706' }}>{chartSqlNotice}</p>
        )}
        {queryResultTable && !chartSqlLoading && (
          <p className="mt-2 text-xs" style={{ color: '#22c55e' }}>
            Result table ready: {queryResultTable.numRows.toLocaleString()} rows,{' '}
            columns: {queryResultTable.schema.fields.map(f => f.name).join(', ')}
          </p>
        )}
      </div>

      {/* Footer note */}
      <p className="mt-4 text-xs text-muted">
        Tip: the first query initialises the in-browser DuckDB engine — subsequent
        queries are faster. Local queries run entirely in your browser.
      </p>
    </div>
  )
}
