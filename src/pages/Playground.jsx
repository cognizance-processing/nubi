/**
 * Playground — comprehensive SQL + Python notebook scratchpad.
 *
 * Features
 * --------
 * - Multi-cell notebook: SQL cells (Monaco SqlEditor + DataTable) and Python cells.
 * - Each cell runs independently; "Run all" runs all cells top-to-bottom.
 * - Add / remove / reorder cells with drag handles (lightweight — no heavy lib).
 * - Query history: session-level list of executed SQL with quick re-run.
 * - Sample query chips for instant quick-start.
 * - Full DataTable grid per SQL cell: sort / filter / paginate / export / row count /
 *   elapsed / cache badge — reusing the shared <DataTable> component directly.
 * - Python cells reuse the <PythonCell> component drop-in.
 * - Polished toolbar, keyboard hints, empty / loading / error states.
 * - Responsive: stacks on mobile, comfortable spacing, light + dark semantic tokens.
 *
 * Route: /playground  (inside AppShell <Outlet/>)
 * Owns: this file only (may add playgroundCells.jsx helpers).
 * Does NOT edit: SqlEditor, DataTable, QueryCell, PythonCell, wasmRuntime, api.
 */

import {
  useState,
  useCallback,
  useRef,
  useEffect,
} from 'react'
import {
  Play,
  Plus,
  Trash2,
  ChevronUp,
  ChevronDown,
  Clock,
  Database,
  History,
  RotateCcw,
  Sparkles,
  Terminal,
  Code2,
  Layers,
  X,
  MessageSquare,
  Zap,
  ChevronRight,
  GripVertical,
  Save,
  Check,
  Copy,
  LayoutDashboard,
} from 'lucide-react'

import SqlEditor from '../components/SqlEditor.jsx'
import DataTable from '../components/DataTable.jsx'
import PythonCell from '../components/PythonCell.jsx'
import { runArrowQuery } from '../lib/wasmRuntime.js'
import { registerQuery } from '../lib/api.js'

// Import useUi — only safe to call inside AppShell (UiProvider must be mounted).
import { useUi as _useUi } from '../contexts/UiContext.jsx'

// ---------------------------------------------------------------------------
// Constants / sample queries
// ---------------------------------------------------------------------------

const SAMPLE_QUERIES = [
  {
    label: 'Row count',
    sql: 'SELECT COUNT(*) AS total_rows FROM information_schema.tables',
    description: 'Count tables in the schema',
  },
  {
    label: 'Schema info',
    sql: "SELECT table_name, table_type\nFROM information_schema.tables\nORDER BY table_name",
    description: 'List all tables',
  },
  {
    label: 'Current time',
    sql: "SELECT\n  NOW()                       AS ts,\n  CURRENT_DATE                AS today,\n  EXTRACT(epoch FROM NOW())   AS unix_epoch",
    description: 'Server timestamp',
  },
  {
    label: 'Generate series',
    sql: "SELECT\n  n,\n  n * n        AS square,\n  n * n * n    AS cube\nFROM generate_series(1, 20) AS t(n)",
    description: 'Math series via generate_series',
  },
  {
    label: 'JSON demo',
    sql: `SELECT\n  '{"name":"nubi","version":1}'::jsonb ->> 'name' AS name,\n  '{"name":"nubi","version":1}'::jsonb ->> 'version' AS version`,
    description: 'JSONB operators',
  },
]



// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Generate a stable unique cell id. */
function makeCellId() {
  return `cell-${Math.random().toString(36).slice(2, 9)}`
}

/** Truncate SQL for history display. */
function shortSql(sql, max = 72) {
  const trimmed = sql.trim().replace(/\s+/g, ' ')
  return trimmed.length > max ? trimmed.slice(0, max) + '…' : trimmed
}

// ---------------------------------------------------------------------------
// CacheBadge — inline, matches DataTable visual style
// ---------------------------------------------------------------------------

function CacheBadge({ cacheStatus }) {
  if (!cacheStatus || cacheStatus === 'MISS') return null
  if (cacheStatus === 'SAMPLE') {
    return (
      <span className="inline-flex items-center gap-1 text-[10px] font-medium px-2 py-0.5 rounded-full bg-amber-500/10 text-amber-600 dark:text-amber-400 border border-amber-500/20">
        <Database size={9} />
        SAMPLE
      </span>
    )
  }
  return (
    <span className="inline-flex items-center gap-1 text-[10px] font-medium px-2 py-0.5 rounded-full bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border border-emerald-500/20">
      <Zap size={9} />
      {cacheStatus}
    </span>
  )
}

// ---------------------------------------------------------------------------
// PythonNotebookCell — thin wrapper around PythonCell with notebook chrome
// ---------------------------------------------------------------------------

function PythonNotebookCell({ cell, index, total, onRemove, onMoveUp, onMoveDown }) {
  const [collapsed, setCollapsed] = useState(false)

  return (
    <div className="group/cell relative rounded-xl border border-border bg-surface overflow-hidden shadow-sm transition-shadow hover:shadow-md">
      {/* ── Cell header ─────────────────────────────────────────────── */}
      <div className="flex items-center gap-2 px-3 py-2 border-b border-border bg-surface-2/60">
        <div className="text-muted/30 cursor-grab shrink-0">
          <GripVertical size={14} />
        </div>

        <span className="inline-flex items-center gap-1 text-[10px] font-semibold uppercase tracking-wider text-muted bg-surface border border-border rounded px-1.5 py-0.5 shrink-0">
          <Terminal size={9} />
          Python
        </span>

        <span className="text-xs text-muted flex-1 min-w-0 truncate">
          on-demand kernel
        </span>

        <div className="flex items-center gap-1 shrink-0">
          <button
            onClick={() => setCollapsed(c => !c)}
            className="h-6 w-6 flex items-center justify-center rounded text-muted hover:text-fg hover:bg-surface transition-colors"
            title={collapsed ? 'Expand cell' : 'Collapse cell'}
          >
            {collapsed ? <ChevronRight size={13} /> : <ChevronDown size={13} />}
          </button>
          <button
            onClick={onMoveUp}
            disabled={index === 0}
            className="h-6 w-6 flex items-center justify-center rounded text-muted hover:text-fg hover:bg-surface disabled:opacity-20 disabled:cursor-not-allowed transition-colors"
            title="Move up"
          >
            <ChevronUp size={13} />
          </button>
          <button
            onClick={onMoveDown}
            disabled={index === total - 1}
            className="h-6 w-6 flex items-center justify-center rounded text-muted hover:text-fg hover:bg-surface disabled:opacity-20 disabled:cursor-not-allowed transition-colors"
            title="Move down"
          >
            <ChevronDown size={13} />
          </button>
          <button
            onClick={onRemove}
            className="h-6 w-6 flex items-center justify-center rounded text-muted hover:text-rose-500 hover:bg-rose-500/10 transition-colors"
            title="Remove cell"
          >
            <Trash2 size={12} />
          </button>
        </div>
      </div>

      {!collapsed && (
        <div className="p-3">
          <PythonCell />
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// HistoryPanel — session query history sidebar
// ---------------------------------------------------------------------------

function HistoryPanel({ history, onRerun, onClose }) {
  return (
    <div className="flex flex-col bg-surface border border-border rounded-xl overflow-hidden shadow-lg">
      <div className="flex items-center justify-between px-3 py-2.5 border-b border-border bg-surface-2/60">
        <div className="flex items-center gap-1.5">
          <History size={13} className="text-muted" />
          <span className="text-xs font-semibold text-fg">Query History</span>
          <span className="text-[10px] text-muted">({history.length})</span>
        </div>
        <button
          onClick={onClose}
          className="h-6 w-6 flex items-center justify-center rounded text-muted hover:text-fg hover:bg-surface transition-colors"
        >
          <X size={12} />
        </button>
      </div>

      <div className="flex-1 overflow-y-auto max-h-80">
        {history.length === 0 ? (
          <div className="px-3 py-6 text-center text-xs text-muted">
            No queries yet. Run a SQL cell to build history.
          </div>
        ) : (
          <ul className="divide-y divide-border/60">
            {[...history].reverse().map((entry, i) => (
              <li key={i} className="group flex items-start gap-2 px-3 py-2.5 hover:bg-surface-2/60 transition-colors">
                <div className="flex-1 min-w-0">
                  <code className="block text-[11px] font-mono text-fg truncate" title={entry.sql}>
                    {shortSql(entry.sql, 60)}
                  </code>
                  <div className="flex items-center gap-2 mt-0.5">
                    <span className="text-[10px] text-muted font-mono">
                      {entry.rows?.toLocaleString()} rows · {entry.elapsedMs}ms
                    </span>
                    {entry.cacheStatus && entry.cacheStatus !== 'MISS' && (
                      <CacheBadge cacheStatus={entry.cacheStatus} />
                    )}
                  </div>
                </div>
                <button
                  onClick={() => onRerun(entry.sql)}
                  className="shrink-0 h-6 w-6 flex items-center justify-center rounded text-muted hover:text-primary hover:bg-primary/10 transition-colors opacity-0 group-hover:opacity-100"
                  title="Re-run in new cell"
                >
                  <RotateCcw size={11} />
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// SampleChips — quick-start query chips
// ---------------------------------------------------------------------------

function SampleChips({ onSelect }) {
  return (
    <div className="flex flex-wrap gap-2">
      {SAMPLE_QUERIES.map((q, i) => (
        <button
          key={i}
          onClick={() => onSelect(q.sql)}
          className="inline-flex items-center gap-1.5 px-2.5 py-1 text-xs font-medium bg-surface border border-border text-muted hover:text-fg hover:border-primary/40 hover:bg-primary/5 rounded-lg transition-colors"
          title={q.description}
        >
          <Sparkles size={10} className="text-primary/60" />
          {q.label}
        </button>
      ))}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Toolbar — global actions
// ---------------------------------------------------------------------------

function PlaygroundToolbar({
  onAddSql,
  onAddPython,
  onRunAll,
  onToggleHistory,
  historyCount,
  historyOpen,
  onAskAI,
  runAllLoading,
}) {
  return (
    <div className="flex items-center gap-2 flex-wrap">
      {/* Add cell buttons */}
      <div className="flex items-center gap-1.5">
        <button
          onClick={onAddSql}
          className="inline-flex items-center gap-1.5 h-8 px-3 text-xs font-medium bg-surface border border-border text-fg hover:bg-surface-2 hover:border-border rounded-lg transition-colors focus:outline-none focus:ring-2 focus:ring-ring"
        >
          <Plus size={12} />
          <Code2 size={12} />
          SQL cell
        </button>
        <button
          onClick={onAddPython}
          className="inline-flex items-center gap-1.5 h-8 px-3 text-xs font-medium bg-surface border border-border text-fg hover:bg-surface-2 hover:border-border rounded-lg transition-colors focus:outline-none focus:ring-2 focus:ring-ring"
        >
          <Plus size={12} />
          <Terminal size={12} />
          Python cell
        </button>
      </div>

      {/* Divider */}
      <div className="h-5 w-px bg-border/60" />

      {/* Run all */}
      <button
        onClick={onRunAll}
        disabled={runAllLoading}
        className="inline-flex items-center gap-1.5 h-8 px-3 text-xs font-semibold bg-primary text-primary-fg rounded-lg hover:opacity-90 disabled:opacity-50 disabled:cursor-not-allowed transition-opacity focus:outline-none focus:ring-2 focus:ring-ring"
      >
        <Layers size={12} />
        {runAllLoading ? 'Running all…' : 'Run all'}
      </button>

      <div className="flex-1" />

      {/* History */}
      <button
        onClick={onToggleHistory}
        className={[
          'inline-flex items-center gap-1.5 h-8 px-3 text-xs font-medium rounded-lg border transition-colors focus:outline-none focus:ring-2 focus:ring-ring',
          historyOpen
            ? 'bg-primary/10 border-primary/30 text-primary'
            : 'bg-surface border-border text-muted hover:text-fg',
        ].join(' ')}
      >
        <History size={12} />
        History
        {historyCount > 0 && (
          <span className="inline-flex items-center justify-center min-w-[16px] h-4 px-1 rounded-full text-[9px] font-bold bg-primary text-primary-fg">
            {historyCount}
          </span>
        )}
      </button>

      {/* Ask AI */}
      {onAskAI && (
        <button
          onClick={onAskAI}
          className="inline-flex items-center gap-1.5 h-8 px-3 text-xs font-medium bg-surface border border-border text-muted hover:text-fg hover:bg-surface-2 rounded-lg transition-colors focus:outline-none focus:ring-2 focus:ring-ring"
        >
          <MessageSquare size={12} />
          Ask AI
        </button>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Playground — main page
// ---------------------------------------------------------------------------

export default function Playground() {
  // Cells: array of { id, type: 'sql'|'python', sql? }
  const [cells, setCells] = useState(() => [
    { id: makeCellId(), type: 'sql', sql: SAMPLE_QUERIES[3].sql },
  ])

  // Query history: array of { sql, cacheStatus, elapsedMs, rows }
  const [history, setHistory] = useState([])

  // History panel open/close
  const [historyOpen, setHistoryOpen] = useState(false)

  // Run-all state
  const [runAllLoading, setRunAllLoading] = useState(false)

  // Per-cell external SQL injection (for history re-run)
  // Map: cellId → sql string to inject
  const [externalSqlMap, setExternalSqlMap] = useState({})

  // openChat from UiContext (soft-fail if UiProvider is not mounted)
  let openChat = null
  try {
    openChat = _useUi().openChat
  } catch (_) {}

  // ---------------------------------------------------------------------------
  // Cell management
  // ---------------------------------------------------------------------------

  const addSqlCell = useCallback(() => {
    setCells(prev => [...prev, { id: makeCellId(), type: 'sql', sql: '' }])
  }, [])

  const addPythonCell = useCallback(() => {
    setCells(prev => [...prev, { id: makeCellId(), type: 'python' }])
  }, [])

  const removeCell = useCallback((id) => {
    setCells(prev => prev.filter(c => c.id !== id))
  }, [])

  const moveCell = useCallback((id, dir) => {
    setCells(prev => {
      const idx = prev.findIndex(c => c.id === id)
      if (idx === -1) return prev
      const next = [...prev]
      const swapIdx = idx + dir
      if (swapIdx < 0 || swapIdx >= next.length) return prev
      ;[next[idx], next[swapIdx]] = [next[swapIdx], next[idx]]
      return next
    })
  }, [])

  const updateCellSql = useCallback((id, sql) => {
    setCells(prev => prev.map(c => c.id === id ? { ...c, sql } : c))
  }, [])

  // ---------------------------------------------------------------------------
  // History
  // ---------------------------------------------------------------------------

  const pushHistory = useCallback((entry) => {
    setHistory(prev => {
      // deduplicate: if same SQL already at top, update it
      if (prev.length > 0 && prev[prev.length - 1].sql === entry.sql) {
        return [...prev.slice(0, -1), entry]
      }
      return [...prev.slice(-49), entry] // keep last 50
    })
  }, [])

  // Re-run a history query by injecting it into the first SQL cell (or adding one)
  const handleHistoryRerun = useCallback((sql) => {
    const firstSqlCell = cells.find(c => c.type === 'sql')
    if (firstSqlCell) {
      setExternalSqlMap(prev => ({ ...prev, [firstSqlCell.id]: sql }))
    } else {
      const id = makeCellId()
      setCells(prev => [{ id, type: 'sql', sql }, ...prev])
    }
    setHistoryOpen(false)
  }, [cells])

  // ---------------------------------------------------------------------------
  // Sample query chips
  // ---------------------------------------------------------------------------

  const handleSampleSelect = useCallback((sql) => {
    const firstSqlCell = cells.find(c => c.type === 'sql')
    if (firstSqlCell) {
      setExternalSqlMap(prev => ({ ...prev, [firstSqlCell.id]: sql }))
    } else {
      const id = makeCellId()
      setCells(prev => [...prev, { id, type: 'sql', sql }])
    }
  }, [cells])

  // ---------------------------------------------------------------------------
  // Run all (SQL cells only — Python cells each manage their own run)
  // ---------------------------------------------------------------------------

  // We coordinate via a ref-based event bus: each SqlNotebookCell exposes a
  // runCell function. To keep cells self-contained we use a dispatch mechanism
  // via a Map stored in a ref.
  const cellRunners = useRef(new Map()) // cellId → () => Promise<void>

  const handleRunAll = useCallback(async () => {
    setRunAllLoading(true)
    const sqlCells = cells.filter(c => c.type === 'sql')
    for (const cell of sqlCells) {
      const runner = cellRunners.current.get(cell.id)
      if (runner) {
        try { await runner() } catch (_) {}
      }
    }
    setRunAllLoading(false)
  }, [cells])

  // ---------------------------------------------------------------------------
  // Empty state (no cells)
  // ---------------------------------------------------------------------------

  if (cells.length === 0) {
    return (
      <div className="max-w-4xl mx-auto px-4 sm:px-6 py-10">
        <PageHeader openChat={openChat} />
        <PlaygroundToolbar
          onAddSql={addSqlCell}
          onAddPython={addPythonCell}
          onRunAll={handleRunAll}
          onToggleHistory={() => setHistoryOpen(o => !o)}
          historyCount={history.length}
          historyOpen={historyOpen}
          onAskAI={openChat}
          runAllLoading={runAllLoading}
        />
        <div className="mt-12 flex flex-col items-center gap-4 text-center">
          <div className="w-14 h-14 rounded-2xl bg-surface border border-border flex items-center justify-center">
            <Database size={24} className="text-muted" />
          </div>
          <div>
            <p className="text-base font-semibold text-fg">Empty notebook</p>
            <p className="text-sm text-muted mt-1">Add a SQL or Python cell to get started.</p>
          </div>
          <div className="flex gap-3">
            <button
              onClick={addSqlCell}
              className="inline-flex items-center gap-2 px-4 py-2 text-sm font-medium bg-primary text-primary-fg rounded-lg hover:opacity-90 transition-opacity focus:outline-none focus:ring-2 focus:ring-ring"
            >
              <Code2 size={14} />
              Add SQL cell
            </button>
            <button
              onClick={addPythonCell}
              className="inline-flex items-center gap-2 px-4 py-2 text-sm font-medium bg-surface border border-border text-fg rounded-lg hover:bg-surface-2 transition-colors focus:outline-none focus:ring-2 focus:ring-ring"
            >
              <Terminal size={14} />
              Add Python cell
            </button>
          </div>
        </div>
      </div>
    )
  }

  // ---------------------------------------------------------------------------
  // Main render
  // ---------------------------------------------------------------------------

  return (
    <div className="max-w-4xl mx-auto px-4 sm:px-6 py-8 space-y-6">
      {/* ── Page header ─────────────────────────────────────────────────── */}
      <PageHeader openChat={openChat} />

      {/* ── Quick-start chips ───────────────────────────────────────────── */}
      <section>
        <p className="text-xs font-medium text-muted mb-2 uppercase tracking-wider">
          Quick-start
        </p>
        <SampleChips onSelect={handleSampleSelect} />
      </section>

      {/* ── Toolbar ─────────────────────────────────────────────────────── */}
      <PlaygroundToolbar
        onAddSql={addSqlCell}
        onAddPython={addPythonCell}
        onRunAll={handleRunAll}
        onToggleHistory={() => setHistoryOpen(o => !o)}
        historyCount={history.length}
        historyOpen={historyOpen}
        onAskAI={openChat}
        runAllLoading={runAllLoading}
      />

      {/* ── History panel (inline, toggleable) ──────────────────────────── */}
      {historyOpen && (
        <HistoryPanel
          history={history}
          onRerun={handleHistoryRerun}
          onClose={() => setHistoryOpen(false)}
        />
      )}

      {/* ── Cells ───────────────────────────────────────────────────────── */}
      <div className="space-y-4">
        {cells.map((cell, index) =>
          cell.type === 'sql' ? (
            <SqlNotebookCellWithRunner
              key={cell.id}
              cell={cell}
              index={index}
              total={cells.length}
              onSqlChange={(val) => updateCellSql(cell.id, val)}
              onRemove={() => removeCell(cell.id)}
              onMoveUp={() => moveCell(cell.id, -1)}
              onMoveDown={() => moveCell(cell.id, 1)}
              onHistoryPush={pushHistory}
              externalSql={externalSqlMap[cell.id] ?? null}
              onExternalSqlConsumed={() =>
                setExternalSqlMap(prev => {
                  const next = { ...prev }
                  delete next[cell.id]
                  return next
                })
              }
              registerRunner={(runner) => {
                cellRunners.current.set(cell.id, runner)
              }}
            />
          ) : (
            <PythonNotebookCell
              key={cell.id}
              cell={cell}
              index={index}
              total={cells.length}
              onRemove={() => removeCell(cell.id)}
              onMoveUp={() => moveCell(cell.id, -1)}
              onMoveDown={() => moveCell(cell.id, 1)}
            />
          )
        )}
      </div>

      {/* ── Add cell footer ─────────────────────────────────────────────── */}
      <div className="flex items-center gap-3 pt-2">
        <button
          onClick={addSqlCell}
          className="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium bg-surface border border-border border-dashed text-muted hover:text-fg hover:border-solid hover:border-primary/40 rounded-lg transition-all"
        >
          <Plus size={11} />
          SQL cell
        </button>
        <button
          onClick={addPythonCell}
          className="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium bg-surface border border-border border-dashed text-muted hover:text-fg hover:border-solid hover:border-primary/40 rounded-lg transition-all"
        >
          <Plus size={11} />
          Python cell
        </button>
        <span className="text-xs text-muted">
          Tip: first run initialises DuckDB-WASM — subsequent queries are faster.
        </span>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// SqlNotebookCellWithRunner — exposes a run() function to parent via ref
// ---------------------------------------------------------------------------

/**
 * Thin wrapper around SqlNotebookCell that additionally exposes a `run()`
 * function to the parent Playground via the `registerRunner` prop.
 * This avoids threading imperative refs through the cell's state.
 */
function SqlNotebookCellWithRunner({ registerRunner, ...props }) {
  // Internal run trigger: we increment a counter to trigger runEffect
  const [runTrigger, setRunTrigger] = useState(0)
  const runResolveRef = useRef(null)

  // Register a runner with the parent (stable identity via useCallback)
  useEffect(() => {
    registerRunner?.(() => {
      return new Promise((resolve) => {
        runResolveRef.current = resolve
        setRunTrigger(n => n + 1)
      })
    })
    return () => registerRunner?.(null)
  }, [registerRunner])

  // When runTrigger changes, we want the cell's handleRun to fire.
  // We inject runTrigger as externalSql would be too blunt; instead we
  // render a hidden button and imperatively click it — or, more cleanly,
  // we pass a `autoRunTrigger` that the cell watches.
  return (
    <SqlNotebookCellAutoRun
      {...props}
      autoRunTrigger={runTrigger}
      onAutoRunComplete={() => {
        runResolveRef.current?.()
        runResolveRef.current = null
      }}
    />
  )
}

// ---------------------------------------------------------------------------
// SaveAsQuery — register the cell's SQL as a dashboard-usable query
// ---------------------------------------------------------------------------

/** Detect {{name}} placeholders so we can declare them as named params. */
function extractNamedParams(sql) {
  const names = new Set()
  const re = /\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}/g
  let m
  while ((m = re.exec(sql)) !== null) names.add(m[1])
  return [...names].map(name => ({ name, type: 'text' }))
}

function SaveAsQuery({ sql }) {
  const [open, setOpen] = useState(false)
  const [name, setName] = useState('')
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(null) // { id }
  const [error, setError] = useState(null)
  const [copied, setCopied] = useState(false)

  const handleSave = useCallback(async () => {
    if (!sql.trim() || saving) return
    setSaving(true)
    setError(null)
    try {
      const params = extractNamedParams(sql)
      const result = await registerQuery({
        name: name.trim() || 'Untitled query',
        sql,
        params,
      })
      setSaved({ id: result.id, params: result.params })
    } catch (err) {
      setError(err.message || 'Failed to save query')
    } finally {
      setSaving(false)
    }
  }, [sql, name, saving])

  const copyId = useCallback(() => {
    if (!saved?.id) return
    navigator.clipboard?.writeText(saved.id).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    })
  }, [saved])

  if (!open) {
    return (
      <button
        onClick={() => setOpen(true)}
        disabled={!sql.trim()}
        className="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium bg-surface border border-border text-muted hover:text-fg hover:bg-surface-2 rounded-lg transition-colors disabled:opacity-50 disabled:cursor-not-allowed focus:outline-none focus:ring-2 focus:ring-ring"
        title="Register this query so it appears in the dashboard editor"
      >
        <Save size={11} />
        Save as query
      </button>
    )
  }

  if (saved) {
    return (
      <div className="flex flex-col gap-1.5 px-3 py-2 rounded-lg border border-emerald-500/30 bg-emerald-500/5 text-xs">
        <div className="flex items-center gap-1.5 text-emerald-600 dark:text-emerald-400 font-medium">
          <Check size={12} />
          Saved as a registered query
        </div>
        <div className="flex items-center gap-1.5 text-fg">
          <span className="text-muted">id:</span>
          <code className="font-mono px-1.5 py-0.5 rounded bg-surface border border-border">{saved.id}</code>
          <button
            onClick={copyId}
            className="inline-flex items-center gap-1 text-muted hover:text-fg transition-colors"
            title="Copy query id"
          >
            {copied ? <Check size={11} /> : <Copy size={11} />}
          </button>
        </div>
        <p className="flex items-start gap-1.5 text-muted leading-relaxed">
          <LayoutDashboard size={11} className="mt-0.5 shrink-0" />
          <span>
            It now appears in the dashboard editor's query picker
            {saved.params?.length ? ` (with ${saved.params.length} param${saved.params.length > 1 ? 's' : ''})` : ''}.
          </span>
        </p>
      </div>
    )
  }

  return (
    <div className="flex items-center gap-1.5 flex-wrap">
      <input
        type="text"
        value={name}
        onChange={e => setName(e.target.value)}
        onKeyDown={e => { if (e.key === 'Enter') handleSave() }}
        placeholder="Query name"
        autoFocus
        className="h-7 w-40 rounded-md border border-border bg-surface text-fg text-xs px-2 focus:outline-none focus:ring-2 focus:ring-ring"
      />
      <button
        onClick={handleSave}
        disabled={saving}
        className="inline-flex items-center gap-1.5 h-7 px-2.5 text-xs font-semibold bg-primary text-primary-fg rounded-md hover:opacity-90 disabled:opacity-50 transition-opacity"
      >
        <Save size={11} />
        {saving ? 'Saving…' : 'Save'}
      </button>
      <button
        onClick={() => { setOpen(false); setError(null) }}
        className="h-7 px-2 text-xs text-muted hover:text-fg transition-colors"
      >
        Cancel
      </button>
      {error && <span className="text-[11px] text-rose-500">{error}</span>}
    </div>
  )
}

/**
 * SqlNotebookCell variant that supports an `autoRunTrigger` prop.
 * When `autoRunTrigger` increments, the cell automatically fires its run.
 */
function SqlNotebookCellAutoRun({ autoRunTrigger, onAutoRunComplete, ...props }) {
  const [sql, setSql] = useState(props.cell.sql || 'SELECT 1 AS n')
  const [loading, setLoading] = useState(false)
  const [result, setResult] = useState(null)
  const [meta, setMeta] = useState(null)
  const [error, setError] = useState(null)
  const [notice, setNotice] = useState(null)
  const [streamedRows, setStreamedRows] = useState(null)
  const [collapsed, setCollapsed] = useState(false)
  const streamedRef = useRef(0)

  // Handle external SQL injection (history re-run, sample chips)
  useEffect(() => {
    if (props.externalSql != null) {
      setSql(props.externalSql)
      props.onExternalSqlConsumed?.()
    }
  }, [props.externalSql]) // eslint-disable-line react-hooks/exhaustive-deps

  const handleChange = useCallback((val) => {
    setSql(val)
    props.onSqlChange?.(val)
  }, [props.onSqlChange]) // eslint-disable-line react-hooks/exhaustive-deps

  const handleRun = useCallback(async () => {
    if (!sql.trim() || loading) return
    setLoading(true)
    setError(null)
    setNotice(null)
    setResult(null)
    setMeta(null)
    setStreamedRows(null)
    streamedRef.current = 0

    const onBatch = (n) => {
      streamedRef.current = n
      setStreamedRows(n)
    }

    try {
      const { table, cacheStatus, elapsedMs } = await runArrowQuery(sql, onBatch)
      setResult(table)
      setMeta({ cacheStatus, elapsedMs })
      if (cacheStatus === 'SAMPLE') {
        setNotice('Backend unavailable — showing sample data.')
      }
      props.onHistoryPush?.({ sql, cacheStatus, elapsedMs, rows: table.numRows })
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }, [sql, loading, props.onHistoryPush]) // eslint-disable-line react-hooks/exhaustive-deps

  // Auto-run when trigger increments
  const prevTrigger = useRef(0)
  useEffect(() => {
    if (autoRunTrigger > 0 && autoRunTrigger !== prevTrigger.current) {
      prevTrigger.current = autoRunTrigger
      handleRun().finally(() => onAutoRunComplete?.())
    }
  }, [autoRunTrigger]) // eslint-disable-line react-hooks/exhaustive-deps

  const { index, total, onRemove, onMoveUp, onMoveDown } = props

  return (
    <div className="group/cell relative rounded-xl border border-border bg-surface overflow-hidden shadow-sm transition-shadow hover:shadow-md">
      {/* ── Cell header ─────────────────────────────────────────────── */}
      <div className="flex items-center gap-2 px-3 py-2 border-b border-border bg-surface-2/60">
        <div className="text-muted/30 cursor-grab active:cursor-grabbing shrink-0">
          <GripVertical size={14} />
        </div>

        <span className="inline-flex items-center gap-1 text-[10px] font-semibold uppercase tracking-wider text-muted bg-surface border border-border rounded px-1.5 py-0.5 shrink-0">
          <Code2 size={9} />
          SQL
        </span>

        <span className="text-xs text-muted font-mono truncate flex-1 min-w-0">
          {result
            ? `${result.numRows.toLocaleString()} rows`
            : loading
            ? `streaming${streamedRows != null ? ` · ${streamedRows.toLocaleString()} rows` : '…'}`
            : 'ready'}
        </span>

        {meta?.cacheStatus && !loading && <CacheBadge cacheStatus={meta.cacheStatus} />}
        {meta?.elapsedMs != null && !loading && (
          <span className="inline-flex items-center gap-1 text-[10px] text-muted shrink-0">
            <Clock size={9} />
            {meta.elapsedMs}ms
          </span>
        )}

        <div className="flex items-center gap-1 shrink-0">
          <button
            onClick={() => setCollapsed(c => !c)}
            className="h-6 w-6 flex items-center justify-center rounded text-muted hover:text-fg hover:bg-surface transition-colors"
            title={collapsed ? 'Expand' : 'Collapse'}
          >
            {collapsed ? <ChevronRight size={13} /> : <ChevronDown size={13} />}
          </button>
          <button
            onClick={onMoveUp}
            disabled={index === 0}
            className="h-6 w-6 flex items-center justify-center rounded text-muted hover:text-fg hover:bg-surface disabled:opacity-20 disabled:cursor-not-allowed transition-colors"
            title="Move up"
          >
            <ChevronUp size={13} />
          </button>
          <button
            onClick={onMoveDown}
            disabled={index === total - 1}
            className="h-6 w-6 flex items-center justify-center rounded text-muted hover:text-fg hover:bg-surface disabled:opacity-20 disabled:cursor-not-allowed transition-colors"
            title="Move down"
          >
            <ChevronDown size={13} />
          </button>
          <button
            onClick={onRemove}
            className="h-6 w-6 flex items-center justify-center rounded text-muted hover:text-rose-500 hover:bg-rose-500/10 transition-colors"
            title="Remove cell"
          >
            <Trash2 size={12} />
          </button>
        </div>
      </div>

      {!collapsed && (
        <>
          {/* ── Editor ────────────────────────────────────────────────── */}
          <div className="p-3">
            <SqlEditor
              value={sql}
              onChange={handleChange}
              height="160px"
              onRun={loading ? undefined : handleRun}
            />
            <div className="mt-2 flex items-center gap-2 flex-wrap">
              <button
                onClick={handleRun}
                disabled={loading || !sql.trim()}
                className="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-semibold bg-primary text-primary-fg rounded-lg hover:opacity-90 disabled:opacity-50 disabled:cursor-not-allowed transition-opacity focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-1"
              >
                <Play size={11} />
                {loading ? 'Running…' : 'Run'}
              </button>
              <span className="text-[11px] text-muted select-none">
                {typeof navigator !== 'undefined' && navigator.platform?.includes('Mac') ? '⌘' : 'Ctrl'}+Enter
              </span>
              <div className="flex-1" />
              <SaveAsQuery sql={sql} />
            </div>
          </div>

          {/* ── Notice ────────────────────────────────────────────────── */}
          {notice && !loading && (
            <div className="mx-3 mb-2 px-3 py-2 rounded-lg flex items-start gap-2 text-xs"
              style={{ background: 'color-mix(in srgb, #f59e0b 8%, transparent)', color: '#d97706', border: '1px solid color-mix(in srgb, #f59e0b 20%, transparent)' }}>
              <span className="shrink-0 mt-0.5">&#9888;</span>
              <span>{notice}</span>
            </div>
          )}

          {/* ── Empty state ───────────────────────────────────────────── */}
          {!loading && !result && !error && (
            <div className="px-4 py-8 text-center text-sm text-muted border-t border-border">
              Run a query to see results here.
            </div>
          )}

          {/* ── Loading ───────────────────────────────────────────────── */}
          {loading && (
            <div className="flex items-center gap-3 px-4 py-6 border-t border-border text-sm text-muted">
              <div className="h-4 w-4 rounded-full border-2 border-primary border-t-transparent animate-spin shrink-0" />
              <span>
                Streaming…
                {streamedRows != null && (
                  <span className="ml-2 font-mono text-xs text-primary">
                    {streamedRows.toLocaleString()} rows
                  </span>
                )}
              </span>
            </div>
          )}

          {/* ── DataTable ─────────────────────────────────────────────── */}
          {!loading && result && (
            <div
              className="border-t border-border"
              style={{ height: Math.min(460, Math.max(220, result.numRows * 38 + 100)) }}
            >
              <DataTable
                arrow={result}
                meta={meta}
                loading={false}
                error={null}
                toolbar
              />
            </div>
          )}

          {/* ── Error ─────────────────────────────────────────────────── */}
          {!loading && error && (
            <div className="mx-3 mb-3 px-3 py-2 rounded-lg text-xs font-mono"
              style={{ background: 'color-mix(in srgb, #ef4444 8%, transparent)', color: '#dc2626', border: '1px solid color-mix(in srgb, #ef4444 20%, transparent)' }}>
              {error}
            </div>
          )}
        </>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// PageHeader — shared between normal + empty state
// ---------------------------------------------------------------------------

function PageHeader({ openChat }) {
  return (
    <div className="flex items-start justify-between gap-4 flex-wrap">
      <div>
        <h1 className="text-2xl font-bold font-display text-fg">Playground</h1>
        <p className="mt-1 text-sm text-muted leading-relaxed max-w-lg">
          A SQL + Python notebook. Run queries against your datastores; results
          stream as Apache Arrow and render in a full data grid.
        </p>
      </div>
      {openChat && (
        <button
          onClick={openChat}
          className="shrink-0 inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium bg-surface border border-border text-muted hover:text-fg hover:bg-surface-2 rounded-lg transition-colors"
        >
          <MessageSquare size={12} />
          Ask AI
        </button>
      )}
    </div>
  )
}
