/**
 * QueryWorkspace — Playground-style NOTEBOOK with query-of-record management.
 *
 * Structure
 * ---------
 *   ┌─ Toolbar (query name, registered/unsaved badge, AI assist, Save, Run) ─┐
 *   ├─ PRIMARY SQL cell (cell_1) ───────────────────────────────────────────┤
 *   │   • the "query of record": its SQL + params + datastore are what Save  │
 *   │     persists via registerQuery({ ..., datastore_id }).                  │
 *   │   • prominent Connector picker on its toolbar.                          │
 *   │   • params panel + AI assist live here.                                 │
 *   │   • SqlEditor (with its built-in Templates + dialect toolbar). The      │
 *   │     dialect is auto-detected from the selected connector, overridable.  │
 *   │     + DataTable results.                                                │
 *   ├─ SCRATCH cells (session-only) ────────────────────────────────────────┤
 *   │   • extra SQL cells (SqlEditor + DataTable, runnable)                   │
 *   │   • Python cells (reuse <PythonCell/> via the on-demand kernel)         │
 *   │   • add / remove / reorder; not persisted.                             │
 *   │   • CROSS-CELL DATA FLOW: each cell result is registered as cell_N     │
 *   │     in DuckDB-WASM, so SELECT * FROM cell_2 in cell_3 works.           │
 *   └─ Add-cell footer (SQL cell / Python cell) ────────────────────────────┘
 *
 * Props:
 *   query         {object|null}  — { id?, name, sql, params: [], datastore_id? }
 *   onQueryChange {fn}           — called with updated query object (primary cell)
 *   onSaved       {fn}           — called after a successful save
 *   isNew         {boolean}      — true when editing an unsaved ad-hoc query
 *   toolbarExtra  {ReactNode}    — optional cluster appended to the toolbar
 *                                  (QueriesPage passes the Editor/Rollups view
 *                                  toggle + Queries panel button)
 */

import { useState, useCallback, useRef, useEffect } from 'react'
import { createPortal } from 'react-dom'
import { Link } from 'react-router-dom'
import {
  Play,
  Save,
  Sparkles,
  MessageSquare,
  Plus,
  Trash2,
  ChevronDown,
  ChevronRight,
  Loader2,
  AlertCircle,
  CheckCircle2,
  Clock,
  Zap,
  Database,
  GripVertical,
  Code2,
  Terminal,
  Layers,
  Star,
  FileCode2,
  CalendarClock,
  GitCommitHorizontal,
  History,
  ExternalLink,
  Copy,
  Check,
  ArrowUp,
  ArrowDown,
  Hash,
} from 'lucide-react'

import SqlEditor from '../../components/SqlEditor.jsx'
import SpecIO from '../../components/SpecIO.jsx'
import MetricExposePanel from '../../components/app/MetricExposePanel.jsx'
import { metricToDraft, draftToMetricBlock } from './metricBlock.logic.js'
import QueryCodeView from './QueryCodeView.jsx'
import DataTable from '../../components/DataTable.jsx'
import PythonCell from '../../components/PythonCell.jsx'
import { runArrowQueryById, runArrowQuery, registerArrowTable, runLocalSqlForCell } from '../../lib/wasmRuntime.js'
import { get, post, registerQuery, listConnectors } from '../../lib/api.js'
import { checkpoint, restoreVersion } from '../../lib/versions.js'
import VersionHistoryDialog from '../../components/app/VersionHistoryDialog.jsx'
import { useUi } from '../../contexts/UiContext.jsx'
import { useCanWrite } from '../../contexts/OrgContext.jsx'
import { dialectForConnectorType } from '../../lib/sqlDialect.js'

// ---------------------------------------------------------------------------
// Connector type → SQL dialect
// ---------------------------------------------------------------------------

const DEFAULT_DIALECT = 'duckdb'

// The connector_type → dialect mapping lives in src/lib/sqlDialect.js (shared
// with SqlEditor). We read the backend's canonical `config.connector_type`
// field, falling back to legacy `config.type` / `type` for older shapes.
function datastoreType(ds) {
  return (
    ds?.config?.connector_type ??
    ds?.config?.type ??
    ds?.type ??
    ''
  )
    .toString()
    .toLowerCase()
}

/**
 * Derive the SQL dialect from the selected connector's type. The built-in
 * "Demo data" connector (id __demo__, type "demo") runs on DuckDB-WASM, as
 * does the empty/no-connector case. Unknown types fall back to DuckDB.
 */
function dialectForDatastore(datastores, datastoreId) {
  if (!datastoreId || datastoreId === DEMO_DATASTORE_ID) return DEFAULT_DIALECT
  const ds = datastores.find(d => d.id === datastoreId)
  if (!ds) return DEFAULT_DIALECT
  return dialectForConnectorType(datastoreType(ds))
}

// Sentinel id of the built-in virtual demo connector (matches the backend).
const DEMO_DATASTORE_ID = '__demo__'

// ---------------------------------------------------------------------------
// Extract {{name}} placeholders from SQL text
// ---------------------------------------------------------------------------

function extractPlaceholders(sql) {
  const re = /\{\{([A-Za-z_][A-Za-z0-9_]*)\}\}/g
  const found = new Set()
  let m
  while ((m = re.exec(sql)) !== null) {
    found.add(m[1])
  }
  return Array.from(found)
}

/** Generate a stable unique scratch-cell id. */
function makeCellId() {
  return `cell-${Math.random().toString(36).slice(2, 9)}`
}

// ---------------------------------------------------------------------------
// Param type → HTML input type
// ---------------------------------------------------------------------------

function paramInputType(type) {
  if (type === 'number' || type === 'integer' || type === 'float') return 'number'
  if (type === 'date') return 'date'
  if (type === 'datetime') return 'datetime-local'
  return 'text'
}

// ---------------------------------------------------------------------------
// ParamInputRow
// ---------------------------------------------------------------------------

function ParamInputRow({ param, value, onChange }) {
  const hasOptions = Boolean(param.options_query_id)

  return (
    <div className="flex flex-col gap-1 min-w-0">
      <label className="text-[11px] font-semibold text-muted uppercase tracking-wide flex items-center gap-1">
        <span className="font-mono normal-case text-fg/80">{param.name}</span>
        <span className="text-muted/60 normal-case">({param.type})</span>
        {param.required && (
          <span className="text-rose-500 text-[10px]" title="Required">*</span>
        )}
      </label>
      {hasOptions ? (
        <input
          type="text"
          value={value ?? ''}
          onChange={e => onChange(param.name, e.target.value)}
          placeholder={param.default != null ? `default: ${param.default}` : 'value…'}
          className="h-8 px-2.5 text-xs bg-surface border border-border rounded-lg text-fg placeholder:text-muted/40 focus:outline-none focus:ring-1 focus:ring-ring transition-colors"
        />
      ) : (
        <input
          type={paramInputType(param.type)}
          value={value ?? ''}
          onChange={e => onChange(param.name, e.target.value)}
          placeholder={param.default != null ? `default: ${param.default}` : param.required ? 'required' : 'optional'}
          className="h-8 px-2.5 text-xs bg-surface border border-border rounded-lg text-fg placeholder:text-muted/40 focus:outline-none focus:ring-1 focus:ring-ring transition-colors"
        />
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// CellNameBadge — shows "cell_N" with a copy affordance
// ---------------------------------------------------------------------------

function CellNameBadge({ name }) {
  const [copied, setCopied] = useState(false)

  const handleCopy = useCallback(async (e) => {
    e.stopPropagation()
    try {
      await navigator.clipboard.writeText(name)
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    } catch {}
  }, [name])

  return (
    <button
      onClick={handleCopy}
      title={`Reference this cell as ${name} in later cells — click to copy`}
      className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded font-mono text-[10px] font-semibold bg-indigo-500/10 text-indigo-600 dark:text-indigo-400 border border-indigo-500/20 hover:bg-indigo-500/20 transition-colors group shrink-0"
    >
      <Hash size={8} />
      {name}
      {copied
        ? <Check size={8} className="text-emerald-500" />
        : <Copy size={8} className="opacity-0 group-hover:opacity-100 transition-opacity" />
      }
    </button>
  )
}

// ---------------------------------------------------------------------------
// SaveDialog — inline modal for naming a query
// ---------------------------------------------------------------------------

function SaveDialog({ query, onConfirm, onCancel, saving }) {
  const [name, setName] = useState(query?.name ?? '')
  const inputRef = useRef(null)

  useEffect(() => {
    inputRef.current?.focus()
    inputRef.current?.select()
  }, [])

  const handleSubmit = (e) => {
    e.preventDefault()
    if (!name.trim()) return
    onConfirm(name.trim())
  }

  return (
    <div className="absolute inset-0 z-50 flex items-center justify-center bg-bg/60 backdrop-blur-sm rounded-xl">
      <form
        onSubmit={handleSubmit}
        className="bg-surface border border-border rounded-xl shadow-2xl p-5 w-80 flex flex-col gap-4"
      >
        <div>
          <h3 className="text-sm font-semibold text-fg font-display">Save query</h3>
          <p className="text-[11px] text-muted mt-0.5">Give this query a name to save it to the registry.</p>
        </div>
        <div className="flex flex-col gap-1">
          <label className="text-[11px] font-semibold text-muted uppercase tracking-wide">Name</label>
          <input
            ref={inputRef}
            type="text"
            value={name}
            onChange={e => setName(e.target.value)}
            placeholder="My query…"
            className="h-9 px-3 text-sm bg-surface border border-border rounded-lg text-fg placeholder:text-muted/40 focus:outline-none focus:ring-1 focus:ring-ring"
          />
        </div>
        <div className="flex items-center gap-2 justify-end">
          <button
            type="button"
            onClick={onCancel}
            className="h-8 px-3 text-xs text-muted hover:text-fg border border-border rounded-lg bg-surface hover:bg-surface-2 transition-colors"
          >
            Cancel
          </button>
          <button
            type="submit"
            disabled={saving || !name.trim()}
            className="h-8 px-4 text-xs font-medium bg-primary text-primary-fg rounded-lg hover:opacity-90 disabled:opacity-50 disabled:cursor-not-allowed transition-opacity flex items-center gap-1.5"
          >
            {saving ? <Loader2 size={11} className="animate-spin" /> : <Save size={11} />}
            {saving ? 'Saving…' : 'Save'}
          </button>
        </div>
      </form>
    </div>
  )
}

// ---------------------------------------------------------------------------
// ScheduleDialog — schedule a registered query as a 1-task flow
// ---------------------------------------------------------------------------

const INTERVAL_UNITS = [
  { value: 'm', label: 'minutes' },
  { value: 'h', label: 'hours' },
  { value: 'd', label: 'days' },
]

function buildIntervalString(n, unit) {
  const count = Math.max(1, Math.floor(Number(n) || 1))
  if (unit === 'd') return `${count * 24}h`
  return `${count}${unit}`
}

function describeSchedule(mode, schedule) {
  if (mode === 'interval') {
    const m = /^(\d+)([mh])$/.exec(schedule)
    if (!m) return null
    const n = Number(m[1])
    if (m[2] === 'm') return `Runs every ${n} minute${n !== 1 ? 's' : ''}.`
    if (n % 24 === 0) {
      const days = n / 24
      return `Runs every ${days} day${days !== 1 ? 's' : ''}.`
    }
    return `Runs every ${n} hour${n !== 1 ? 's' : ''}.`
  }
  const parts = schedule.trim().split(/\s+/)
  if (parts.length === 5) {
    const [min, hour, dom, mon, dow] = parts
    if (/^\d+$/.test(min) && /^\d+$/.test(hour) && dom === '*' && mon === '*' && dow === '*') {
      const hh = String(hour).padStart(2, '0')
      const mm = String(min).padStart(2, '0')
      return `Runs every day at ${hh}:${mm}.`
    }
    return 'Valid 5-field cron expression.'
  }
  return null
}

function ScheduleDialog({ query, params, paramValues, onConfirm, onCancel, scheduling, status, createdFlow }) {
  const [name, setName] = useState(`${query?.name ?? 'Query'} (scheduled)`)
  const [mode, setMode] = useState('interval')
  const [intervalN, setIntervalN] = useState('1')
  const [intervalUnit, setIntervalUnit] = useState('h')
  const [cron, setCron] = useState('0 9 * * *')
  const inputRef = useRef(null)

  useEffect(() => {
    inputRef.current?.focus()
    inputRef.current?.select()
  }, [])

  const schedule = mode === 'interval'
    ? buildIntervalString(intervalN, intervalUnit)
    : cron.trim()

  const preview = describeSchedule(mode, schedule)
  const cronInvalid = mode === 'cron' && cron.trim().split(/\s+/).length !== 5
  const canSubmit = name.trim() && schedule && !cronInvalid && !scheduling

  const handleSubmit = (e) => {
    e.preventDefault()
    if (!canSubmit) return
    onConfirm({ name: name.trim(), schedule })
  }

  return (
    <div className="absolute inset-0 z-50 flex items-center justify-center bg-bg/60 backdrop-blur-sm rounded-xl p-4">
      <form
        onSubmit={handleSubmit}
        className="bg-surface border border-border rounded-xl shadow-2xl p-5 w-[26rem] max-w-full flex flex-col gap-4"
      >
        <div className="flex items-start gap-2">
          <CalendarClock size={16} className="text-primary shrink-0 mt-0.5" />
          <div>
            <h3 className="text-sm font-semibold text-fg font-display">Schedule query</h3>
            <p className="text-[11px] text-muted mt-0.5">
              Run <span className="font-mono text-fg/80">{query?.name}</span> on a schedule as a 1-task flow.
            </p>
          </div>
        </div>

        {status === 'ok' ? (
          <div className="flex flex-col gap-3">
            <div className="flex items-center gap-2 text-sm text-emerald-600 dark:text-emerald-400">
              <CheckCircle2 size={16} />
              Scheduled — manage it in Automations.
            </div>
            {createdFlow?.name && (
              <p className="text-[11px] text-muted">
                Created flow <span className="font-mono text-fg/80">{createdFlow.name}</span>.
              </p>
            )}
            <div className="flex items-center gap-2 justify-end">
              <Link
                to="/automations"
                className="h-8 px-3 text-xs font-medium bg-primary text-primary-fg rounded-lg hover:opacity-90 transition-opacity flex items-center gap-1.5"
              >
                <ExternalLink size={11} />
                Open Automations
              </Link>
              <button
                type="button"
                onClick={onCancel}
                className="h-8 px-3 text-xs text-muted hover:text-fg border border-border rounded-lg bg-surface hover:bg-surface-2 transition-colors"
              >
                Close
              </button>
            </div>
          </div>
        ) : (
          <>
            <div className="flex flex-col gap-1">
              <label className="text-[11px] font-semibold text-muted uppercase tracking-wide">Name</label>
              <input
                ref={inputRef}
                type="text"
                value={name}
                onChange={e => setName(e.target.value)}
                placeholder="Scheduled query name…"
                className="h-9 px-3 text-sm bg-surface border border-border rounded-lg text-fg placeholder:text-muted/40 focus:outline-none focus:ring-1 focus:ring-ring"
              />
            </div>

            <div className="flex flex-col gap-2">
              <label className="text-[11px] font-semibold text-muted uppercase tracking-wide">Schedule</label>
              <div className="flex items-center rounded-lg border border-border overflow-hidden w-fit">
                {[
                  { v: 'interval', l: 'Interval' },
                  { v: 'cron', l: 'Cron' },
                ].map(opt => (
                  <button
                    key={opt.v}
                    type="button"
                    onClick={() => setMode(opt.v)}
                    className={[
                      'h-7 px-3 text-[11px] font-medium transition-colors',
                      mode === opt.v
                        ? 'bg-primary/10 text-primary'
                        : 'bg-surface text-muted hover:text-fg hover:bg-surface-2',
                      opt.v === 'cron' ? 'border-l border-border' : '',
                    ].join(' ')}
                  >
                    {opt.l}
                  </button>
                ))}
              </div>

              {mode === 'interval' ? (
                <div className="flex items-center gap-2">
                  <span className="text-xs text-muted">Every</span>
                  <input
                    type="number"
                    min="1"
                    value={intervalN}
                    onChange={e => setIntervalN(e.target.value)}
                    className="h-8 w-16 px-2 text-xs bg-surface border border-border rounded-lg text-fg focus:outline-none focus:ring-1 focus:ring-ring"
                  />
                  <select
                    value={intervalUnit}
                    onChange={e => setIntervalUnit(e.target.value)}
                    className="h-8 px-2 text-xs bg-surface border border-border rounded-lg text-fg focus:outline-none focus:ring-1 focus:ring-ring cursor-pointer"
                  >
                    {INTERVAL_UNITS.map(u => (
                      <option key={u.value} value={u.value}>{u.label}</option>
                    ))}
                  </select>
                  <span className="text-[10px] font-mono text-muted/70">→ {schedule}</span>
                </div>
              ) : (
                <div className="flex flex-col gap-1">
                  <input
                    type="text"
                    value={cron}
                    onChange={e => setCron(e.target.value)}
                    placeholder="0 9 * * *"
                    className={[
                      'h-8 px-2.5 text-xs font-mono bg-surface border rounded-lg text-fg placeholder:text-muted/40 focus:outline-none focus:ring-1 focus:ring-ring',
                      cronInvalid ? 'border-rose-500/50' : 'border-border',
                    ].join(' ')}
                  />
                  <p className="text-[10px] text-muted/70">
                    Standard 5-field cron: <span className="font-mono">min hour day month weekday</span>
                  </p>
                </div>
              )}
            </div>

            {preview && !cronInvalid && (
              <p className="text-[11px] text-muted flex items-center gap-1.5">
                <Clock size={11} className="text-primary/70" />
                {preview}
              </p>
            )}
            {cronInvalid && (
              <p className="text-[11px] text-rose-500 flex items-center gap-1">
                <AlertCircle size={10} /> Cron expression must have 5 fields.
              </p>
            )}

            {params.length > 0 && (
              <p className="text-[10px] text-muted/70">
                Current parameter values ({params.map(p => p.name).join(', ')}) will be captured for each run.
              </p>
            )}

            {status === 'err' && (
              <p className="text-[11px] text-rose-500 flex items-center gap-1">
                <AlertCircle size={10} /> Failed to schedule — please try again.
              </p>
            )}

            <div className="flex items-center gap-2 justify-end">
              <button
                type="button"
                onClick={onCancel}
                className="h-8 px-3 text-xs text-muted hover:text-fg border border-border rounded-lg bg-surface hover:bg-surface-2 transition-colors"
              >
                Cancel
              </button>
              <button
                type="submit"
                disabled={!canSubmit}
                className="h-8 px-4 text-xs font-medium bg-primary text-primary-fg rounded-lg hover:opacity-90 disabled:opacity-50 disabled:cursor-not-allowed transition-opacity flex items-center gap-1.5"
              >
                {scheduling ? <Loader2 size={11} className="animate-spin" /> : <CalendarClock size={11} />}
                {scheduling ? 'Scheduling…' : 'Schedule'}
              </button>
            </div>
          </>
        )}
      </form>
    </div>
  )
}

// ---------------------------------------------------------------------------
// AiAssistBar
// ---------------------------------------------------------------------------

function AiAssistBar({ onResult, onClose }) {
  const { openChat } = useUi()
  const [question, setQuestion] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const inputRef = useRef(null)

  useEffect(() => { inputRef.current?.focus() }, [])

  const handleGenerate = useCallback(async () => {
    if (!question.trim()) return
    setLoading(true)
    setError(null)
    try {
      const data = await post('/ai/ask', { question: question.trim() })
      const sql = data?.suggestion ?? data?.sql ?? ''
      if (sql) {
        onResult(sql)
        onClose()
      } else {
        setError('No SQL was generated — try rephrasing your question.')
      }
    } catch (err) {
      setError(err.message ?? 'AI generation failed.')
    } finally {
      setLoading(false)
    }
  }, [question, onResult, onClose])

  return (
    <div className="flex flex-col gap-3 px-4 py-3 bg-surface-2/80 border-b border-border">
      <div className="flex items-center gap-2">
        <Sparkles size={14} className="text-primary shrink-0" />
        <span className="text-xs font-semibold text-fg">Generate SQL with AI</span>
        <div className="flex-1" />
        <button
          onClick={() => { openChat(); onClose() }}
          className="text-[11px] text-muted hover:text-fg flex items-center gap-1 transition-colors"
          title="Open full AI chat"
        >
          <MessageSquare size={11} />
          Open chat
        </button>
        <button
          onClick={onClose}
          className="text-[11px] text-muted hover:text-fg transition-colors ml-1"
        >
          ✕
        </button>
      </div>
      <div className="flex items-center gap-2">
        <input
          ref={inputRef}
          type="text"
          value={question}
          onChange={e => setQuestion(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); handleGenerate() } }}
          placeholder="e.g. Show total sales by region for last 30 days…"
          className="flex-1 h-8 px-3 text-xs bg-surface border border-border rounded-lg text-fg placeholder:text-muted/40 focus:outline-none focus:ring-1 focus:ring-ring"
          disabled={loading}
        />
        <button
          onClick={handleGenerate}
          disabled={loading || !question.trim()}
          className="h-8 px-3 flex items-center gap-1.5 text-xs font-medium bg-primary text-primary-fg rounded-lg hover:opacity-90 disabled:opacity-50 disabled:cursor-not-allowed transition-opacity shrink-0"
        >
          {loading ? <Loader2 size={11} className="animate-spin" /> : <Sparkles size={11} />}
          Generate
        </button>
      </div>
      {error && (
        <p className="text-[11px] text-rose-500 flex items-center gap-1">
          <AlertCircle size={10} /> {error}
        </p>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// CacheBadge
// ---------------------------------------------------------------------------

function CacheBadge({ status }) {
  if (!status || status === 'MISS') return null
  if (status === 'SAMPLE') {
    return (
      <span className="inline-flex items-center gap-1 px-2 py-0.5 text-[10px] font-semibold rounded-full bg-amber-500/10 text-amber-600 dark:text-amber-400 border border-amber-500/20">
        <Database size={8} /> SAMPLE
      </span>
    )
  }
  if (status === 'LOCAL') {
    return (
      <span className="inline-flex items-center gap-1 px-2 py-0.5 text-[10px] font-semibold rounded-full bg-indigo-500/10 text-indigo-600 dark:text-indigo-400 border border-indigo-500/20">
        <Database size={8} /> LOCAL
      </span>
    )
  }
  return (
    <span className="inline-flex items-center gap-1 px-2 py-0.5 text-[10px] font-semibold rounded-full bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border border-emerald-500/20">
      <Zap size={8} /> {status}
    </span>
  )
}

// ---------------------------------------------------------------------------
// ConnectorPicker
// ---------------------------------------------------------------------------

function ConnectorPicker({ datastores, value, onChange }) {
  const hasDatastores = datastores.length > 0
  return (
    <div className="flex items-center gap-1.5 shrink-0">
      <label
        htmlFor="primary-connector"
        className="inline-flex items-center gap-1 text-[11px] font-semibold text-fg"
      >
        <Database size={12} className="text-primary shrink-0" />
        Connector
      </label>
      {hasDatastores ? (
        <select
          id="primary-connector"
          value={value}
          onChange={e => onChange(e.target.value)}
          className="h-7 max-w-[180px] rounded-md border border-border bg-surface text-fg text-xs px-2 focus:outline-none focus:ring-1 focus:ring-ring cursor-pointer transition-colors hover:bg-surface-2"
          title="Run / bind this query against a connector."
        >
          {/* Connectors come from GET /connectors. The built-in "Demo data"
              connector (id __demo__) is included by the backend ONLY in the
              org's demo/default project — it is not hardcoded here. */}
          {datastores.map(ds => (
            <option key={ds.id} value={ds.id}>{ds.name ?? ds.id}</option>
          ))}
        </select>
      ) : (
        <Link
          to="/connectors"
          className="inline-flex items-center gap-1 h-7 px-2 rounded-md border border-dashed border-border bg-surface text-[11px] text-muted hover:text-fg hover:bg-surface-2 transition-colors"
          title="This project has no connectors yet — add one"
        >
          <Plus size={11} />
          No connectors yet — add one
        </Link>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Editor-height resize hook
// ---------------------------------------------------------------------------

const MIN_EDITOR_H = 120
const MAX_EDITOR_H = 600
const DEFAULT_EDITOR_H = 220

function useResizableHeight(initial = DEFAULT_EDITOR_H) {
  const [editorH, setEditorH] = useState(initial)
  const dragState = useRef(null)

  const startDrag = useCallback((e) => {
    e.preventDefault()
    dragState.current = { startY: e.clientY, startH: editorH }
    const onMove = (me) => {
      const delta = me.clientY - dragState.current.startY
      setEditorH(h => Math.max(MIN_EDITOR_H, Math.min(MAX_EDITOR_H, dragState.current.startH + delta)))
    }
    const onUp = () => {
      dragState.current = null
      window.removeEventListener('mousemove', onMove)
      window.removeEventListener('mouseup', onUp)
    }
    window.addEventListener('mousemove', onMove)
    window.addEventListener('mouseup', onUp)
  }, [editorH])

  return { editorH, startDrag }
}

// ---------------------------------------------------------------------------
// AddCellDivider — the "+ SQL cell / + Python cell" affordance between cells
// ---------------------------------------------------------------------------

function AddCellDivider({ onAddSql, onAddPython }) {
  const [hovered, setHovered] = useState(false)

  return (
    <div
      className="relative flex items-center gap-2 py-2 group"
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
    >
      <div className={[
        'flex-1 h-px transition-colors duration-150',
        hovered ? 'bg-primary/30' : 'bg-border/40',
      ].join(' ')} />

      <div className={[
        'flex items-center gap-1.5 shrink-0 transition-all duration-150',
        hovered ? 'opacity-100 scale-100' : 'opacity-0 scale-95',
      ].join(' ')}>
        <button
          onClick={onAddSql}
          className="inline-flex items-center gap-1 h-6 px-2.5 text-[10px] font-semibold text-primary bg-primary/10 border border-primary/20 rounded-full hover:bg-primary/20 transition-colors"
          title="Add SQL cell here"
        >
          <Plus size={9} />
          <Code2 size={9} />
          SQL
        </button>
        <button
          onClick={onAddPython}
          className="inline-flex items-center gap-1 h-6 px-2.5 text-[10px] font-semibold text-violet-600 dark:text-violet-400 bg-violet-500/10 border border-violet-500/20 rounded-full hover:bg-violet-500/20 transition-colors"
          title="Add Python cell here"
        >
          <Plus size={9} />
          <Terminal size={9} />
          Python
        </button>
      </div>

      <div className={[
        'flex-1 h-px transition-colors duration-150',
        hovered ? 'bg-primary/30' : 'bg-border/40',
      ].join(' ')} />
    </div>
  )
}

// ---------------------------------------------------------------------------
// CellRunStatus — compact run state chip shown in cell header
// ---------------------------------------------------------------------------

function CellRunStatus({ running, result, error, cellName }) {
  if (running) {
    return (
      <span className="inline-flex items-center gap-1 text-[10px] text-muted font-mono">
        <Loader2 size={9} className="animate-spin" />
        running…
      </span>
    )
  }
  if (error) {
    return (
      <span className="inline-flex items-center gap-1 text-[10px] text-rose-500 font-mono">
        <AlertCircle size={9} />
        error
      </span>
    )
  }
  if (result) {
    const rows = result.table?.numRows ?? 0
    return (
      <span className="inline-flex items-center gap-1.5 text-[10px] text-emerald-600 dark:text-emerald-400 font-mono">
        <CheckCircle2 size={9} />
        {rows.toLocaleString()} row{rows !== 1 ? 's' : ''}
        {result.elapsedMs != null && (
          <span className="text-muted/80">· {result.elapsedMs}ms</span>
        )}
      </span>
    )
  }
  return (
    <span className="text-[10px] text-muted/50 font-mono shrink-0">
      {cellName ? `→ ref as ${cellName}` : 'ready'}
    </span>
  )
}

// ---------------------------------------------------------------------------
// ScratchSqlCell — a session-only SQL exploration cell (ad-hoc run)
//
// Cross-cell flow: after successful run, registerArrowTable(cellRef, table) is
// called so subsequent cells can SELECT * FROM cell_2 etc.
// The cell also attempts runLocalSqlForCell first if the SQL references any
// cell_N table (heuristic: if no datastore and sql contains "cell_N").
// ---------------------------------------------------------------------------

function ScratchSqlCell({
  cell,
  cellNumber,
  index,
  total,
  onSqlChange,
  onRemove,
  onMoveUp,
  onMoveDown,
  datastoreId,
  dialect,
  registerRunner,
  cellRef,
  onResult,
}) {
  const [sql, setSql] = useState(cell.sql ?? '')
  const [running, setRunning] = useState(false)
  const [result, setResult] = useState(null)
  const [runError, setRunError] = useState(null)
  const [collapsed, setCollapsed] = useState(false)
  const { editorH, startDrag } = useResizableHeight(180)

  const handleChange = useCallback((val) => {
    setSql(val)
    onSqlChange?.(val)
  }, [onSqlChange])

  const handleRun = useCallback(async () => {
    if (!sql.trim() || running) return
    setRunning(true)
    setRunError(null)
    setResult(null)
    try {
      let res
      // Use local DuckDB when SQL references cell_ tables (cross-cell flow)
      const refersToCell = /\bcell_\d+\b/i.test(sql)
      if (refersToCell && !datastoreId) {
        res = await runLocalSqlForCell(sql)
      } else {
        res = await runArrowQuery(sql, undefined, { datastoreId: datastoreId || undefined })
      }
      setResult(res)
      // Register result in DuckDB-WASM for downstream cells
      if (res?.table) {
        try {
          await registerArrowTable(cellRef, res.table)
          onResult?.({ cellRef, table: res.table })
        } catch (regErr) {
          console.warn('[ScratchSqlCell] registerArrowTable failed:', regErr)
        }
      }
    } catch (err) {
      setRunError(err?.message ?? 'Query failed')
    } finally {
      setRunning(false)
    }
  }, [sql, running, datastoreId, cellRef, onResult])

  // Expose run() to the parent for "Run all"
  useEffect(() => {
    registerRunner?.(handleRun)
    return () => registerRunner?.(null)
  }, [registerRunner, handleRun])

  const isEmpty = !sql.trim()
  const cellElRef = useRef(null)

  // Test hook: listen for a custom 'nubi:set-sql' event so Playwright can set SQL
  // without needing to interact with Monaco. Dispatched as:
  //   el.dispatchEvent(new CustomEvent('nubi:set-sql', { detail: 'SELECT ...' }))
  useEffect(() => {
    const el = cellElRef.current
    if (!el) return
    const handler = (e) => {
      const newSql = e.detail ?? ''
      handleChange(newSql)
    }
    el.addEventListener('nubi:set-sql', handler)
    return () => el.removeEventListener('nubi:set-sql', handler)
  }, [handleChange])

  return (
    <div
      ref={cellElRef}
      data-cell-ref={cellRef}
      className="rounded-xl border border-border bg-surface overflow-hidden shadow-sm transition-shadow hover:shadow-md"
      style={{ scrollMarginTop: '1rem' }}
    >
      {/* Cell header */}
      <div className="flex items-center gap-1.5 px-3 py-2 border-b border-border bg-surface-2/60 flex-wrap gap-y-1.5 min-h-[44px]">
        <div className="text-muted/30 cursor-grab shrink-0 hidden sm:block">
          <GripVertical size={14} />
        </div>

        {/* Cell number badge */}
        <CellNameBadge name={cellRef} />

        {/* Type badge */}
        <span className="inline-flex items-center gap-1 text-[10px] font-semibold uppercase tracking-wider text-muted bg-surface border border-border rounded px-1.5 py-0.5 shrink-0">
          <Code2 size={9} /> SQL
        </span>

        {/* Run status */}
        <CellRunStatus running={running} result={result} error={runError} cellName={cellRef} />

        {result?.cacheStatus && !running && (
          <CacheBadge status={result.cacheStatus} />
        )}

        <div className="flex-1" />

        {/* Controls — ≥44px tap targets */}
        <div className="flex items-center gap-0.5 shrink-0">
          <button
            onClick={() => setCollapsed(c => !c)}
            className="h-9 w-9 flex items-center justify-center rounded text-muted hover:text-fg hover:bg-surface transition-colors"
            title={collapsed ? 'Expand' : 'Collapse'}
          >
            {collapsed ? <ChevronRight size={13} /> : <ChevronDown size={13} />}
          </button>
          <button
            onClick={onMoveUp}
            disabled={index === 0}
            className="h-9 w-9 flex items-center justify-center rounded text-muted hover:text-fg hover:bg-surface disabled:opacity-20 disabled:cursor-not-allowed transition-colors"
            title="Move up"
          >
            <ArrowUp size={12} />
          </button>
          <button
            onClick={onMoveDown}
            disabled={index === total - 1}
            className="h-9 w-9 flex items-center justify-center rounded text-muted hover:text-fg hover:bg-surface disabled:opacity-20 disabled:cursor-not-allowed transition-colors"
            title="Move down"
          >
            <ArrowDown size={12} />
          </button>
          <button
            onClick={onRemove}
            className="h-9 w-9 flex items-center justify-center rounded text-muted hover:text-rose-500 hover:bg-rose-500/10 transition-colors"
            title="Remove cell"
          >
            <Trash2 size={12} />
          </button>
        </div>
      </div>

      {!collapsed && (
        <>
          <div className="p-3">
            <SqlEditor
              value={sql}
              onChange={handleChange}
              onRun={running ? undefined : handleRun}
              height={`${editorH}px`}
              dialect={dialect}
            />
            {isEmpty && (
              <p className="mt-1.5 text-[11px] text-muted flex items-center gap-1 flex-wrap">
                <FileCode2 size={11} className="text-primary/60 shrink-0" />
                <span>Reference earlier results as</span>
                <code className="font-mono text-indigo-600 dark:text-indigo-400 bg-indigo-500/10 px-1 rounded">cell_1</code>
                <span>— e.g.</span>
                <code className="font-mono text-indigo-600 dark:text-indigo-400 bg-indigo-500/10 px-1 rounded">SELECT * FROM cell_1</code>
                <span>or</span>
                <code className="font-mono text-indigo-600 dark:text-indigo-400 bg-indigo-500/10 px-1 rounded">SELECT count(*) AS n FROM cell_1</code>
              </p>
            )}
            <div className="mt-2 flex items-center gap-2 flex-wrap">
              <button
                onClick={handleRun}
                disabled={running || isEmpty}
                className="inline-flex items-center gap-1.5 h-9 px-4 text-xs font-semibold bg-primary text-primary-fg rounded-lg hover:opacity-90 disabled:opacity-50 disabled:cursor-not-allowed transition-opacity"
              >
                {running ? <Loader2 size={11} className="animate-spin" /> : <Play size={11} />}
                {running ? 'Running…' : 'Run'}
              </button>
              <span className="text-[11px] text-muted select-none">⌘/Ctrl+Enter</span>
            </div>
          </div>

          {/* Drag handle */}
          <div
            className="flex items-center justify-center h-3 cursor-row-resize group select-none"
            onMouseDown={startDrag}
            title="Drag to resize editor"
          >
            <GripVertical size={12} className="rotate-90 text-muted/30 group-hover:text-muted/60 transition-colors" />
          </div>

          {/* Results area */}
          {(result || running || runError) ? (
            <div className="border-t border-border" style={{ height: 320 }}>
              <DataTable
                arrow={result?.table ?? undefined}
                loading={running}
                error={runError}
                meta={result ? { cacheStatus: result.cacheStatus, elapsedMs: result.elapsedMs } : undefined}
                toolbar={Boolean(result?.table)}
                pageSize={50}
              />
            </div>
          ) : (
            <div className="px-4 py-6 text-center text-xs text-muted border-t border-border">
              Run a query to see results here. Results will be available downstream as{' '}
              <code className="font-mono text-indigo-600 dark:text-indigo-400">{cellRef}</code>.
            </div>
          )}
        </>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// ScratchPythonCell — thin notebook wrapper around <PythonCell/>
// ---------------------------------------------------------------------------

function ScratchPythonCell({ cell, cellNumber, index, total, onRemove, onMoveUp, onMoveDown, cellRef }) {
  const [collapsed, setCollapsed] = useState(false)

  return (
    <div
      data-cell-ref={cellRef}
      className="rounded-xl border border-border bg-surface overflow-hidden shadow-sm transition-shadow hover:shadow-md"
      style={{ scrollMarginTop: '1rem' }}
    >
      <div className="flex items-center gap-1.5 px-3 py-2 border-b border-border bg-surface-2/60 flex-wrap gap-y-1.5 min-h-[44px]">
        <div className="text-muted/30 cursor-grab shrink-0 hidden sm:block">
          <GripVertical size={14} />
        </div>

        {/* Cell reference badge */}
        <CellNameBadge name={cellRef} />

        <span className="inline-flex items-center gap-1 text-[10px] font-semibold uppercase tracking-wider text-muted bg-surface border border-border rounded px-1.5 py-0.5 shrink-0">
          <Terminal size={9} /> Python
        </span>

        <span className="text-[10px] text-muted/70 italic shrink-0">on-demand kernel</span>

        <div className="flex-1" />

        <div className="flex items-center gap-0.5 shrink-0">
          <button
            onClick={() => setCollapsed(c => !c)}
            className="h-9 w-9 flex items-center justify-center rounded text-muted hover:text-fg hover:bg-surface transition-colors"
            title={collapsed ? 'Expand' : 'Collapse'}
          >
            {collapsed ? <ChevronRight size={13} /> : <ChevronDown size={13} />}
          </button>
          <button
            onClick={onMoveUp}
            disabled={index === 0}
            className="h-9 w-9 flex items-center justify-center rounded text-muted hover:text-fg hover:bg-surface disabled:opacity-20 disabled:cursor-not-allowed transition-colors"
            title="Move up"
          >
            <ArrowUp size={12} />
          </button>
          <button
            onClick={onMoveDown}
            disabled={index === total - 1}
            className="h-9 w-9 flex items-center justify-center rounded text-muted hover:text-fg hover:bg-surface disabled:opacity-20 disabled:cursor-not-allowed transition-colors"
            title="Move down"
          >
            <ArrowDown size={12} />
          </button>
          <button
            onClick={onRemove}
            className="h-9 w-9 flex items-center justify-center rounded text-muted hover:text-rose-500 hover:bg-rose-500/10 transition-colors"
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
// QueryWorkspace
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// WorkspaceToolbar — renders the toolbar into the AppShell topbar slot when
// available (single-top-bar pattern, like the dashboard editor's portaled
// toolbar); falls back to an inline bar when no slot exists.
// ---------------------------------------------------------------------------

function WorkspaceToolbar({ slot, children }) {
  if (slot) {
    return createPortal(
      <div className="flex items-center gap-1.5 w-full min-w-0">{children}</div>,
      slot
    )
  }
  return (
    <div className="shrink-0 flex items-center gap-1.5 px-3 py-2 border-b border-border bg-surface-2/60 flex-wrap gap-y-2 min-h-[48px]">
      {children}
    </div>
  )
}

export default function QueryWorkspace({ query, onQueryChange, onSaved, isNew, toolbarExtra = null }) {
  const canWrite = useCanWrite()
  // AppShell topbar slot — the toolbar portals into the single top bar.
  const { topbarSlot } = useUi()
  // ── PRIMARY cell: SQL / params state (the query of record) ──────────────
  const [sql, setSql] = useState(query?.sql ?? '')
  const [params, setParams] = useState(() => query?.params ?? [])
  const [paramValues, setParamValues] = useState(() => {
    const init = {}
    ;(query?.params ?? []).forEach(p => {
      if (p.default != null) init[p.name] = String(p.default)
    })
    return init
  })

  // ── "Expose as metric" panel draft (config.metric block) ─────────────────
  const [metricDraft, setMetricDraft] = useState(() =>
    metricToDraft(query?.metric, query?.name),
  )
  const [metricCollapsed, setMetricCollapsed] = useState(true)

  // Sync the primary cell when the selected query changes.
  useEffect(() => {
    setSql(query?.sql ?? '')
    setParams(query?.params ?? [])
    const init = {}
    ;(query?.params ?? []).forEach(p => {
      if (p.default != null) init[p.name] = String(p.default)
    })
    setParamValues(init)
    // Re-parse the query's config.metric block into the panel draft; expand the
    // panel for queries that already expose a metric so it's discoverable.
    const draft = metricToDraft(query?.metric, query?.name)
    setMetricDraft(draft)
    setMetricCollapsed(!draft.enabled)
  }, [query?.id]) // eslint-disable-line react-hooks/exhaustive-deps

  // Auto-sync params list from SQL {{placeholders}}.
  useEffect(() => {
    const found = extractPlaceholders(sql)
    if (found.length === 0) return
    setParams(prev => {
      const existing = new Set(prev.map(p => p.name))
      const newOnes = found.filter(n => !existing.has(n))
      if (newOnes.length === 0) return prev
      return [
        ...prev,
        ...newOnes.map(n => ({ name: n, type: 'text', default: null, required: false })),
      ]
    })
  }, [sql])

  // ── Connector (datastore) picker ─────────────────────────────────────────
  const [datastores, setDatastores] = useState([])
  const [datastoreId, setDatastoreId] = useState(query?.datastore_id ?? '')

  // ── SQL dialect ──────────────────────────────────────────────────────────
  const [dialect, setDialect] = useState(() =>
    dialectForDatastore([], query?.datastore_id ?? ''),
  )
  const lastDerivedKey = useRef(null)

  useEffect(() => {
    let alive = true
    listConnectors().then(ds => { if (alive) setDatastores(Array.isArray(ds) ? ds : []) })
    return () => { alive = false }
  }, [])

  useEffect(() => {
    setDatastoreId(query?.datastore_id ?? '')
  }, [query?.id]) // eslint-disable-line react-hooks/exhaustive-deps

  // Once connectors load, ensure the selected id maps to a real option so the
  // picker value and the auto-derived dialect stay in sync. If the query has no
  // saved connector (or the saved one no longer exists), default to the demo
  // connector when present, else the first available connector.
  useEffect(() => {
    if (datastores.length === 0) return
    const exists = datastoreId && datastores.some(d => d.id === datastoreId)
    if (exists) return
    const demo = datastores.find(d => d.id === DEMO_DATASTORE_ID)
    setDatastoreId((demo ?? datastores[0]).id)
  }, [datastores]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    const detected = dialectForDatastore(datastores, datastoreId)
    const key = `${datastoreId}:${detected}`
    if (lastDerivedKey.current === key) return
    lastDerivedKey.current = key
    setDialect(detected)
  }, [datastoreId, datastores])

  // ── Primary run state ───────────────────────────────────────────────────
  const [running, setRunning] = useState(false)
  const [result, setResult] = useState(null)
  const [runError, setRunError] = useState(null)

  // ── Save state ──────────────────────────────────────────────────────────
  const [showSaveDialog, setShowSaveDialog] = useState(false)
  const [saving, setSaving] = useState(false)
  const [saveStatus, setSaveStatus] = useState(null)

  // ── Schedule state ────────────────────────────────────────────────────────
  const [showScheduleDialog, setShowScheduleDialog] = useState(false)
  const [scheduling, setScheduling] = useState(false)
  const [scheduleStatus, setScheduleStatus] = useState(null)
  const [scheduledFlow, setScheduledFlow] = useState(null)

  // ── Version history (kind='query', saved queries only) ──────────────────
  const [historyOpen, setHistoryOpen] = useState(false)
  // Read-only version view — full version row (incl. config = {sql, params,
  // datastore_id, ...}) loaded via the history dialog's View action. While
  // set, the primary editor shows the version's SQL/params read-only under a
  // banner; the draft (and the editor state) stays untouched.
  const [viewingVersion, setViewingVersion] = useState(null)
  const viewing = Boolean(viewingVersion)
  const viewCfg = viewingVersion?.config ?? {}

  // ── AI assist ───────────────────────────────────────────────────────────
  const [showAi, setShowAi] = useState(false)

  // ── VS Code-style "Code" view (files: <slug>.sql + <slug>.meta.json) ──────
  // Additional full-pane mode alongside the notebook editor; replaces the
  // notebook body when on. SQL edits round-trip through handleSqlChange.
  const [codeView, setCodeView] = useState(false)

  // ── Primary editor height ───────────────────────────────────────────────
  const { editorH, startDrag } = useResizableHeight(DEFAULT_EDITOR_H)

  // ── Scratch cells (session-only) ────────────────────────────────────────
  const [scratchCells, setScratchCells] = useState([])
  const scratchRunners = useRef(new Map()) // cellId → run()
  const [runAllLoading, setRunAllLoading] = useState(false)
  const notebookBodyRef = useRef(null)

  // Reset scratch cells (and any read-only version view) when switching the
  // active query of record.
  useEffect(() => {
    setScratchCells([])
    scratchRunners.current = new Map()
    setViewingVersion(null)
  }, [query?.id]) // eslint-disable-line react-hooks/exhaustive-deps

  // ── Add cells ────────────────────────────────────────────────────────────

  const addSqlCellAndScroll = useCallback((newCell) => {
    setScratchCells(prev => [...prev, newCell])
    // Wait for the new cell to be rendered, then scroll to it + focus editor
    requestAnimationFrame(() => {
      setTimeout(() => {
        const body = notebookBodyRef.current
        if (!body) return
        const el = body.querySelector(`[data-cell-ref="${newCell._pendingRef}"]`)
        if (el) {
          el.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
          // Try to focus the Monaco editor textarea
          setTimeout(() => {
            const textarea = el.querySelector('.monaco-editor textarea')
            textarea?.focus()
          }, 350)
        } else {
          body.scrollTo({ top: body.scrollHeight, behavior: 'smooth' })
        }
      }, 50)
    })
  }, [])

  const addSqlCell = useCallback(() => {
    // We pre-compute what the cellRef will be based on current length
    const nextIndex = scratchCells.length + 2 // cell_1 is primary
    const newCell = { id: makeCellId(), type: 'sql', sql: '', _pendingRef: `cell_${nextIndex}` }
    addSqlCellAndScroll(newCell)
  }, [scratchCells.length, addSqlCellAndScroll])

  const addPythonCell = useCallback(() => {
    const nextIndex = scratchCells.length + 2
    const newCell = { id: makeCellId(), type: 'python', _pendingRef: `cell_${nextIndex}` }
    setScratchCells(prev => [...prev, newCell])
    requestAnimationFrame(() => {
      setTimeout(() => {
        notebookBodyRef.current?.scrollTo({ top: notebookBodyRef.current.scrollHeight, behavior: 'smooth' })
      }, 50)
    })
  }, [scratchCells.length])

  const removeCell = useCallback((id) => {
    scratchRunners.current.delete(id)
    setScratchCells(prev => prev.filter(c => c.id !== id))
  }, [])

  const moveCell = useCallback((id, dir) => {
    setScratchCells(prev => {
      const idx = prev.findIndex(c => c.id === id)
      if (idx === -1) return prev
      const swapIdx = idx + dir
      if (swapIdx < 0 || swapIdx >= prev.length) return prev
      const next = [...prev]
      ;[next[idx], next[swapIdx]] = [next[swapIdx], next[idx]]
      return next
    })
  }, [])

  const updateCellSql = useCallback((id, val) => {
    setScratchCells(prev => prev.map(c => c.id === id ? { ...c, sql: val } : c))
  }, [])

  // ── Run (primary cell / cell_1) ──────────────────────────────────────────
  const handleRun = useCallback(async () => {
    if (running || viewingVersion) return
    setRunning(true)
    setRunError(null)
    setResult(null)

    try {
      let res
      if (query?.id && !isNew) {
        const namedParams = {}
        Object.entries(paramValues).forEach(([k, v]) => {
          if (v !== '' && v != null) {
            const descriptor = params.find(p => p.name === k)
            const ptype = descriptor?.type ?? 'text'
            if (ptype === 'number' || ptype === 'integer' || ptype === 'float') {
              namedParams[k] = Number(v)
            } else if (ptype === 'boolean') {
              namedParams[k] = v === 'true' || v === true
            } else {
              namedParams[k] = v
            }
          }
        })
        res = await runArrowQueryById(query.id, {
          namedParams: Object.keys(namedParams).length > 0 ? namedParams : undefined,
          datastoreId: datastoreId || undefined,
        })
      } else {
        res = await runArrowQuery(sql, undefined, { datastoreId: datastoreId || undefined })
      }
      setResult(res)

      // Register primary cell result as "cell_1" in DuckDB-WASM for cross-cell flow
      if (res?.table) {
        try {
          await registerArrowTable('cell_1', res.table)
          // Also register under query id/name if available
          if (query?.id) await registerArrowTable(query.id, res.table).catch(() => {})
          if (query?.name) {
            const safeName = query.name.replace(/[^a-z0-9_]/gi, '_').toLowerCase()
            if (safeName) await registerArrowTable(safeName, res.table).catch(() => {})
          }
        } catch (regErr) {
          console.warn('[QueryWorkspace] registerArrowTable cell_1 failed:', regErr)
        }
      }
    } catch (err) {
      setRunError(err?.message ?? 'Query failed')
    } finally {
      setRunning(false)
    }
  }, [running, viewingVersion, query, isNew, sql, paramValues, params, datastoreId])

  // ── Run all (primary + scratch SQL cells, top to bottom) ────────────────
  const handleRunAll = useCallback(async () => {
    setRunAllLoading(true)
    try { await handleRun() } catch (_) {}
    for (const cell of scratchCells) {
      if (cell.type !== 'sql') continue
      const runner = scratchRunners.current.get(cell.id)
      if (runner) {
        try { await runner() } catch (_) {}
      }
    }
    setRunAllLoading(false)
  }, [handleRun, scratchCells])

  // ── Param change ────────────────────────────────────────────────────────
  const handleParamChange = useCallback((name, value) => {
    setParamValues(prev => ({ ...prev, [name]: value }))
  }, [])

  // ── SQL change (propagate up — primary cell is the saved query) ─────────
  const handleSqlChange = useCallback((val) => {
    setSql(val)
    onQueryChange?.({ ...query, sql: val, params })
  }, [query, params, onQueryChange])

  // ── Save ─────────────────────────────────────────────────────────────────
  const handleSaveClick = useCallback(() => {
    if (isNew || !query?.id) {
      setShowSaveDialog(true)
    } else {
      doSave(query.name)
    }
  }, [isNew, query])

  const doSave = useCallback(async (name) => {
    setSaving(true)
    setSaveStatus(null)
    try {
      const payload = {
        name,
        sql,
        params: params.map(p => ({
          name: p.name,
          type: p.type ?? 'text',
          default: p.default ?? null,
          required: p.required ?? false,
          options_query_id: p.options_query_id ?? null,
        })),
      }
      if (query?.id && !isNew) {
        payload.id = query.id
      }
      if (datastoreId) {
        payload.datastore_id = datastoreId
      }
      // "Expose as metric" — write (or clear) the config.metric block. Sending
      // `metric: null` when disabled clears any previously-exposed metric.
      const metricBlock = draftToMetricBlock(metricDraft)
      payload.metric = metricBlock

      const saved = await registerQuery(payload)

      setSaveStatus('ok')
      onSaved?.({
        ...query,
        id: saved.id,
        name: saved.name,
        sql: saved.sql ?? sql,
        params: saved.params ?? params,
        datastore_id: saved.datastore_id ?? (datastoreId || null),
        metric: saved.metric ?? metricBlock ?? null,
        isNew: false,
      })
      setShowSaveDialog(false)
      setTimeout(() => setSaveStatus(null), 2500)
      return saved
    } catch (err) {
      console.error('[QueryWorkspace] save failed:', err)
      setSaveStatus('err')
      setTimeout(() => setSaveStatus(null), 3000)
      return null
    } finally {
      setSaving(false)
    }
  }, [sql, params, query, isNew, onSaved, datastoreId, metricDraft])

  // ── Checkpoint — snapshot the saved draft as a new version ───────────────
  const handleCheckpoint = useCallback(async () => {
    if (!query?.id || isNew) return
    const message = window.prompt('Checkpoint message (optional):', '')
    if (message === null) return // cancelled
    // The backend snapshots the *persisted* draft — flush the editor state
    // through the normal save path first so the checkpoint matches the screen.
    const saved = await doSave(query.name)
    if (!saved) {
      window.alert('Save failed — checkpoint aborted.')
      return
    }
    try {
      const v = await checkpoint('query', query.id, { message: message.trim() || undefined })
      window.alert(v?.deduped
        ? `No changes since v${v.version} — the existing version was reused.`
        : `Created version v${v?.version}.`)
    } catch (cause) {
      window.alert(cause?.message || 'Checkpoint failed.')
    }
  }, [query, isNew, doSave])

  // ── After a version restore — the backend wrote the pinned config back into
  //    the persisted queries row; re-read it, load it into the editor, and
  //    re-register it so runs (by id) execute the restored SQL. ─────────────
  const handleRestored = useCallback(async () => {
    if (!query?.id) return
    try {
      const row = await get(`/queries/${query.id}`)
      const cfg = row?.config ?? {}
      const nextSql = typeof cfg.sql === 'string' ? cfg.sql : ''
      const nextParams = Array.isArray(cfg.params) ? cfg.params : []
      const nextDs = cfg.datastore_id ?? ''
      setSql(nextSql)
      setParams(nextParams)
      setDatastoreId(nextDs)
      const init = {}
      nextParams.forEach(p => { if (p.default != null) init[p.name] = String(p.default) })
      setParamValues(init)
      // Re-parse the restored config.metric block into the panel draft.
      const restoredMetric = metricToDraft(cfg.metric, cfg.name ?? row?.name ?? query.name)
      setMetricDraft(restoredMetric)
      setMetricCollapsed(!restoredMetric.enabled)
      // Sync the runtime registry so POST /query by id runs the restored SQL.
      if (nextSql.trim()) {
        const restoredBlock = draftToMetricBlock(restoredMetric)
        const saved = await registerQuery({
          id: query.id,
          name: cfg.name ?? row?.name ?? query.name,
          sql: nextSql,
          params: nextParams,
          ...(nextDs ? { datastore_id: nextDs } : {}),
          metric: restoredBlock,
        })
        onSaved?.({
          ...query,
          id: saved.id,
          name: saved.name,
          sql: saved.sql ?? nextSql,
          params: saved.params ?? nextParams,
          datastore_id: saved.datastore_id ?? (nextDs || null),
          metric: saved.metric ?? restoredBlock ?? null,
          isNew: false,
        })
      }
    } catch (err) {
      console.warn('[QueryWorkspace] reload after restore failed:', err)
      window.alert(err?.message || 'Restored, but reloading the query failed — refresh the page.')
    }
  }, [query, onSaved])

  // ── Restore the version currently being VIEWED (banner action) ──────────
  const restoreViewedVersion = useCallback(async () => {
    if (!query?.id || !viewingVersion) return
    if (!window.confirm(`Restore version v${viewingVersion.version} into the current draft? Unsaved draft changes are overwritten.`)) return
    try {
      await restoreVersion('query', query.id, viewingVersion.version)
      setViewingVersion(null)
      await handleRestored()
    } catch (cause) {
      window.alert(cause?.message || 'Restore failed.')
    }
  }, [query, viewingVersion, handleRestored])

  // ── Schedule ─────────────────────────────────────────────────────────────
  const handleScheduleClick = useCallback(() => {
    if (isNew || !query?.id) {
      setShowSaveDialog(true)
      return
    }
    setScheduleStatus(null)
    setScheduledFlow(null)
    setShowScheduleDialog(true)
  }, [isNew, query])

  const handleScheduleConfirm = useCallback(async ({ name, schedule }) => {
    if (!query?.id) return
    setScheduling(true)
    setScheduleStatus(null)
    try {
      const scheduledParams = params.map(p => ({
        name: p.name,
        type: p.type ?? 'text',
        value: paramValues[p.name] ?? p.default ?? null,
      }))

      const flow = await post('/flows/scheduled-query', {
        name,
        query_id: query.id,
        schedule,
        ...(scheduledParams.length > 0 ? { params: scheduledParams } : {}),
      })

      setScheduledFlow(flow ?? null)
      setScheduleStatus('ok')
    } catch (err) {
      console.error('[QueryWorkspace] schedule failed:', err)
      setScheduleStatus('err')
    } finally {
      setScheduling(false)
    }
  }, [query, params, paramValues])

  // ── AI result → primary cell ────────────────────────────────────────────
  const handleAiResult = useCallback((generatedSql) => {
    setSql(generatedSql)
    onQueryChange?.({ ...query, sql: generatedSql })
  }, [query, onQueryChange])

  // ── Apply a spec from SpecIO (view-as-code / import) → primary cell ───────
  const handleApplySpec = useCallback((nextSpec) => {
    if (!nextSpec || typeof nextSpec !== 'object') return
    const nextSql = typeof nextSpec.sql === 'string' ? nextSpec.sql : sql
    const nextParams = Array.isArray(nextSpec.params) ? nextSpec.params : params
    setSql(nextSql)
    setParams(nextParams)
    if (nextSpec.datastore_id !== undefined) {
      setDatastoreId(nextSpec.datastore_id ?? '')
    }
    // Seed param values from any new defaults.
    setParamValues(prev => {
      const next = { ...prev }
      nextParams.forEach(p => {
        if (next[p.name] === undefined && p.default != null) next[p.name] = String(p.default)
      })
      return next
    })
    onQueryChange?.({ ...query, sql: nextSql, params: nextParams })
  }, [sql, params, query, onQueryChange])

  // ── Param descriptor edits ──────────────────────────────────────────────
  const handleParamDescChange = useCallback((name, field, value) => {
    setParams(prev => prev.map(p => p.name === name ? { ...p, [field]: value } : p))
  }, [])

  const handleRemoveParam = useCallback((name) => {
    setParams(prev => prev.filter(p => p.name !== name))
  }, [])

  // ── Derived ─────────────────────────────────────────────────────────────
  const hasParams = params.length > 0
  const isRegistered = Boolean(query?.id) && !isNew
  const rowCount = result?.table?.numRows ?? 0
  const isEmptyPrimary = !sql.trim()
  const selectedDatastore = datastores.find(d => d.id === datastoreId)
  const dialectHint = datastoreId
    ? (selectedDatastore?.name ?? 'connector')
    : 'Demo data'

  return (
    <div className="flex flex-col h-full overflow-hidden relative">

      {/* ── Top toolbar — portaled into the AppShell top bar when available ── */}
      <WorkspaceToolbar slot={topbarSlot}>
        <div className="flex items-center gap-2 min-w-0 flex-1">
          <span className="text-sm font-semibold font-display text-fg truncate">
            {query?.name ?? (isNew ? 'New query' : 'Ad-hoc query')}
          </span>
          {isRegistered && (
            <span className="shrink-0 inline-flex items-center px-1.5 py-0.5 text-[10px] font-medium rounded bg-primary/10 text-primary border border-primary/20">
              registered
            </span>
          )}
          {isNew && (
            <span className="shrink-0 inline-flex items-center px-1.5 py-0.5 text-[10px] font-medium rounded bg-amber-500/10 text-amber-600 dark:text-amber-400 border border-amber-500/20">
              unsaved
            </span>
          )}
        </div>

        {/* View switcher — Notebook / Code (VS Code-style files view). Mirrors
            the flows view switcher; Code replaces the notebook body with the
            <slug>.sql + <slug>.meta.json file view. */}
        <div className="flex h-8 rounded-lg border border-border overflow-hidden shrink-0" data-testid="query-view-switcher">
          {[
            { id: 'notebook', Icon: Terminal, title: 'Notebook / editor view' },
            { id: 'code', Icon: FileCode2, title: 'Code / Files view (.sql + .meta.json)' },
          ].map((v, i) => {
            const active = v.id === 'code' ? codeView : !codeView
            return (
              <button
                key={v.id}
                onClick={() => setCodeView(v.id === 'code')}
                title={v.title}
                aria-label={v.title}
                aria-pressed={active}
                className={[
                  'flex items-center justify-center w-8 transition-colors',
                  i > 0 ? 'border-l border-border' : '',
                  active ? 'bg-primary text-primary-fg' : 'bg-surface text-muted hover:text-fg hover:bg-surface-2',
                ].join(' ')}
              >
                <v.Icon size={14} />
              </button>
            )
          })}
        </div>

        {/* Add cells — always visible */}
        <div className="flex items-center rounded-lg border border-border overflow-hidden shrink-0">
          <button
            onClick={addSqlCell}
            className="h-8 px-2.5 flex items-center gap-1.5 text-[11px] font-medium bg-surface text-muted hover:text-fg hover:bg-surface-2 transition-colors"
            title="Add a SQL scratch cell"
          >
            <Plus size={12} /> SQL
          </button>
          <button
            onClick={addPythonCell}
            className="h-8 px-2.5 flex items-center gap-1.5 text-[11px] font-medium border-l border-border bg-surface text-muted hover:text-fg hover:bg-surface-2 transition-colors"
            title="Add a Python scratch cell (on-demand kernel)"
          >
            <Plus size={12} /> Python
          </button>
        </div>

        {/* Run all */}
        {scratchCells.some(c => c.type === 'sql') && (
          <button
            onClick={handleRunAll}
            disabled={runAllLoading}
            className="h-8 px-2.5 flex items-center gap-1.5 text-[11px] font-medium rounded-lg border border-border bg-surface text-muted hover:text-fg hover:bg-surface-2 disabled:opacity-50 transition-colors"
            title="Run the primary query then every scratch SQL cell, top to bottom"
          >
            <Layers size={12} />
            <span className="hidden sm:inline">{runAllLoading ? 'Running all…' : 'Run all'}</span>
          </button>
        )}

        {/* Schedule — mutating (creates a scheduled flow); writers only */}
        {canWrite && (
          <button
            onClick={handleScheduleClick}
            disabled={!isRegistered || viewing}
            className="h-8 px-2.5 flex items-center gap-1.5 text-[11px] font-medium rounded-lg border border-border bg-surface text-muted hover:text-fg hover:bg-surface-2 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
            title={isRegistered ? 'Run this query on a schedule' : 'Save the query first'}
          >
            <CalendarClock size={12} />
            <span className="hidden sm:inline">Schedule</span>
          </button>
        )}

        {/* Checkpoint — snapshot the saved draft as a new version; writers only */}
        {canWrite && (
          <button
            onClick={handleCheckpoint}
            disabled={!isRegistered || saving || viewing}
            className="h-8 px-2.5 flex items-center gap-1.5 text-[11px] font-medium rounded-lg border border-border bg-surface text-muted hover:text-fg hover:bg-surface-2 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
            title={isRegistered ? 'Checkpoint — snapshot the current draft as a new version' : 'Save the query first'}
          >
            <GitCommitHorizontal size={12} />
            <span className="hidden sm:inline">Checkpoint</span>
          </button>
        )}

        {/* Version history — restore / promote checkpointed versions */}
        <button
          onClick={() => setHistoryOpen(true)}
          disabled={!isRegistered}
          className="h-8 px-2.5 flex items-center gap-1.5 text-[11px] font-medium rounded-lg border border-border bg-surface text-muted hover:text-fg hover:bg-surface-2 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
          title={isRegistered ? 'Version history' : 'Save the query first'}
        >
          <History size={12} />
          <span className="hidden sm:inline">History</span>
        </button>

        {/* View as code / Import */}
        <SpecIO
          kind="query"
          spec={{ sql, params, datastore_id: datastoreId || null }}
          onApply={handleApplySpec}
          query={query}
        />

        {/* Save — mutating (registerQuery); writers only */}
        {canWrite ? (
          <button
            onClick={handleSaveClick}
            disabled={saving || viewing}
            className="h-8 px-2.5 flex items-center gap-1.5 text-[11px] font-medium rounded-lg border border-border bg-surface text-muted hover:text-fg hover:bg-surface-2 disabled:opacity-50 transition-colors"
            title={isRegistered ? 'Update saved query (primary cell)' : 'Save query (primary cell)'}
          >
            {saving ? (
              <Loader2 size={12} className="animate-spin" />
            ) : saveStatus === 'ok' ? (
              <CheckCircle2 size={12} className="text-emerald-500" />
            ) : saveStatus === 'err' ? (
              <AlertCircle size={12} className="text-rose-500" />
            ) : (
              <Save size={12} />
            )}
            <span className="hidden sm:inline">
              {saving ? 'Saving…' : isRegistered ? 'Update' : 'Save'}
            </span>
          </button>
        ) : (
          <span className="h-8 px-2.5 flex items-center text-[11px] font-medium text-muted/70 select-none" title="Read-only access">
            Read-only
          </span>
        )}

        {/* Run (primary) */}
        <button
          onClick={handleRun}
          disabled={running || viewing}
          className="h-8 px-3 flex items-center gap-1.5 text-[11px] font-semibold rounded-lg bg-primary text-primary-fg hover:opacity-90 disabled:opacity-60 disabled:cursor-not-allowed transition-opacity"
          title="Run query (⌘/Ctrl+Enter)"
        >
          {running ? <Loader2 size={12} className="animate-spin" /> : <Play size={12} />}
          <span>{running ? 'Running…' : 'Run'}</span>
          <kbd className="hidden sm:inline text-[9px] opacity-50 font-mono ml-0.5">⌘↵</kbd>
        </button>

        {/* Page-level extras (view toggle + side-panel buttons from QueriesPage) */}
        {toolbarExtra}
      </WorkspaceToolbar>

      {/* ── Code view — full-pane VS Code-style .sql + .meta.json files ── */}
      {codeView && (
        <div className="flex-1 min-h-0 overflow-hidden">
          <QueryCodeView
            sql={viewing ? (typeof viewCfg.sql === 'string' ? viewCfg.sql : sql) : sql}
            params={viewing ? (Array.isArray(viewCfg.params) ? viewCfg.params : params) : params}
            datastoreId={viewing ? (viewCfg.datastore_id ?? '') : datastoreId}
            query={query}
            onSqlChange={viewing ? undefined : handleSqlChange}
          />
        </div>
      )}

      {/* ── Scrollable notebook body ─────────────────────────────────────── */}
      <div ref={notebookBodyRef} className={`flex-1 min-h-0 overflow-y-auto overflow-x-hidden${codeView ? ' hidden' : ''}`}>

        {/* Read-only version-view banner — the editor below shows the
            version's SQL/params; the draft is untouched until Restore. */}
        {viewing && (
          <div className="flex items-center gap-2 px-4 py-2 bg-sky-500/5 border-b border-sky-500/20 text-xs text-sky-700 dark:text-sky-400">
            <History size={13} className="shrink-0" />
            <span className="flex-1 min-w-0 truncate">
              Viewing <span className="font-mono font-semibold">v{viewingVersion.version}</span> (read-only)
              {viewingVersion.message ? <span className="text-muted"> — {viewingVersion.message}</span> : null}
            </span>
            {canWrite && (
              <button
                onClick={restoreViewedVersion}
                className="shrink-0 px-2 h-6 rounded-md border border-sky-500/30 font-medium hover:bg-sky-500/10 transition-colors"
              >
                Restore
              </button>
            )}
            <button
              onClick={() => setViewingVersion(null)}
              className="shrink-0 px-2 h-6 rounded-md border border-border text-fg font-medium hover:bg-surface-2 transition-colors"
            >
              Back to draft
            </button>
          </div>
        )}

        {/* ════ PRIMARY CELL (cell_1 — the saved query of record) ══════════ */}
        <div className="px-3 pt-3">
          <div
            data-cell-ref="cell_1"
            className="rounded-xl border-2 border-primary/30 bg-surface overflow-hidden shadow-sm"
            style={{ scrollMarginTop: '1rem' }}
          >
            {/* Primary cell header */}
            <div className="flex items-center gap-1.5 px-3 py-2 border-b border-border bg-primary/5 flex-wrap gap-y-1.5 min-h-[44px]">
              {/* Cell_1 badge */}
              <CellNameBadge name="cell_1" />

              <span className="inline-flex items-center gap-1 text-[10px] font-semibold uppercase tracking-wider text-primary bg-primary/10 border border-primary/20 rounded px-1.5 py-0.5 shrink-0">
                <Star size={9} /> Primary query
              </span>

              <CellRunStatus
                running={running}
                result={result}
                error={runError}
                cellName="cell_1"
              />

              {result?.cacheStatus && !running && (
                <CacheBadge status={result.cacheStatus} />
              )}

              <span className="hidden lg:inline text-[10px] text-muted/70 shrink-0">
                saved · downstream cells reference it as{' '}
                <code className="font-mono text-indigo-600 dark:text-indigo-400">cell_1</code>
              </span>

              <div className="flex-1" />

              <ConnectorPicker
                datastores={datastores}
                value={datastoreId}
                onChange={setDatastoreId}
              />
            </div>

            {/* Version-view params — static, read-only */}
            {viewing && Array.isArray(viewCfg.params) && viewCfg.params.length > 0 && (
              <div className="px-3 py-2.5 border-b border-border bg-surface-2/40">
                <p className="text-[10px] font-semibold text-muted uppercase tracking-wider mb-2">
                  Parameters (v{viewingVersion.version})
                </p>
                <div className="flex flex-wrap gap-1.5">
                  {viewCfg.params.map(p => (
                    <span
                      key={p.name}
                      className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-mono bg-surface border border-border text-muted"
                    >
                      {p.name}
                      <span className="text-muted/60">({p.type ?? 'text'})</span>
                      {p.default != null && <span className="text-muted/60">= {String(p.default)}</span>}
                    </span>
                  ))}
                </div>
              </div>
            )}

            {/* Param inputs */}
            {!viewing && hasParams && (
              <div className="px-3 py-2.5 border-b border-border bg-surface-2/40">
                <p className="text-[10px] font-semibold text-muted uppercase tracking-wider mb-2">Parameters</p>
                <div className="flex flex-wrap gap-3">
                  {params.map(param => (
                    <div key={param.name} className="flex flex-col gap-1 min-w-0">
                      <div className="flex items-center gap-1">
                        <label className="text-[11px] font-semibold text-muted uppercase tracking-wide flex items-center gap-1">
                          <span className="font-mono normal-case text-fg/80">{param.name}</span>
                          {param.required && (
                            <span className="text-rose-500 text-[10px]" title="Required">*</span>
                          )}
                        </label>
                        <select
                          value={param.type ?? 'text'}
                          onChange={e => handleParamDescChange(param.name, 'type', e.target.value)}
                          className="h-5 px-1 text-[9px] bg-surface border border-border rounded text-muted focus:outline-none focus:ring-1 focus:ring-ring"
                          title="Parameter type"
                        >
                          <option value="text">text</option>
                          <option value="number">number</option>
                          <option value="boolean">boolean</option>
                          <option value="date">date</option>
                          <option value="select">select</option>
                          <option value="multiselect">multiselect</option>
                        </select>
                        <label className="flex items-center gap-0.5 text-[9px] text-muted cursor-pointer select-none">
                          <input
                            type="checkbox"
                            checked={param.required ?? false}
                            onChange={e => handleParamDescChange(param.name, 'required', e.target.checked)}
                            className="w-2.5 h-2.5 rounded"
                          />
                          req
                        </label>
                        <button
                          onClick={() => handleRemoveParam(param.name)}
                          className="text-muted/40 hover:text-rose-500 transition-colors"
                          title={`Remove param ${param.name}`}
                        >
                          <Trash2 size={9} />
                        </button>
                      </div>
                      <ParamInputRow
                        param={param}
                        value={paramValues[param.name] ?? ''}
                        onChange={handleParamChange}
                      />
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* SQL editor — shows the viewed version's SQL read-only while a
                version view is active; the draft SQL state is untouched. */}
            <div className="px-3 pt-3">
              <SqlEditor
                value={viewing ? (typeof viewCfg.sql === 'string' ? viewCfg.sql : '') : sql}
                onChange={viewing ? () => {} : handleSqlChange}
                onRun={viewing ? undefined : handleRun}
                height={`${editorH}px`}
                dialect={dialect}
                onDialectChange={setDialect}
                dialectHint={dialectHint}
                readOnly={viewing}
              />
              {!viewing && isEmptyPrimary && (
                <p className="mt-1.5 text-[11px] text-muted flex items-center gap-1 flex-wrap">
                  <FileCode2 size={11} className="text-primary/60 shrink-0" />
                  New here? Use the <span className="font-medium text-fg">Templates</span> menu above for starters, or
                  type <span className="font-mono">{' {{param}} '}</span> to declare a bindable parameter.
                </p>
              )}
            </div>

            {/* Drag handle */}
            <div
              className="flex items-center justify-center h-4 cursor-row-resize group select-none mx-3"
              onMouseDown={startDrag}
              title="Drag to resize editor"
            >
              <GripVertical size={14} className="rotate-90 text-muted/30 group-hover:text-muted/60 transition-colors" />
            </div>

            {/* Results */}
            <div className="border-t border-border">
              <div className="flex items-center gap-2 px-3 py-2 flex-wrap">
                <span className="text-[11px] font-semibold text-muted uppercase tracking-wider">Results</span>
                {result && !running && (
                  <>
                    <span className="text-[11px] font-mono text-fg">
                      {rowCount.toLocaleString()} row{rowCount !== 1 ? 's' : ''}
                    </span>
                    {result.elapsedMs != null && (
                      <span className="inline-flex items-center gap-1 text-[10px] text-muted">
                        <Clock size={9} /> {result.elapsedMs}ms
                      </span>
                    )}
                    <CacheBadge status={result.cacheStatus} />
                    <span className="text-[10px] text-muted/60 ml-auto shrink-0">
                      registered as{' '}
                      <code className="font-mono text-indigo-600 dark:text-indigo-400">cell_1</code>
                    </span>
                  </>
                )}
                {running && (
                  <span className="inline-flex items-center gap-1.5 text-[11px] text-muted">
                    <Loader2 size={11} className="animate-spin" /> Streaming…
                  </span>
                )}
              </div>
              <div style={{ height: 360 }} className="px-3 pb-3">
                <DataTable
                  arrow={result?.table ?? undefined}
                  loading={running}
                  error={runError}
                  meta={result ? { cacheStatus: result.cacheStatus, elapsedMs: result.elapsedMs } : undefined}
                  toolbar={Boolean(result?.table)}
                  pageSize={50}
                />
              </div>
            </div>
          </div>
        </div>

        {/* ════ EXPOSE AS METRIC — optional config.metric block ════════════ */}
        {!viewing && (
          <div className="px-3 pt-3">
            <MetricExposePanel
              draft={metricDraft}
              onChange={setMetricDraft}
              collapsed={metricCollapsed}
              onToggleCollapsed={() => setMetricCollapsed(c => !c)}
              canWrite={canWrite}
            />
          </div>
        )}

        {/* ════ SCRATCH CELLS with add-cell dividers ════════════════════════ */}
        <div className="px-3">
          {scratchCells.map((cell, index) => (
            <div key={cell.id}>
              <AddCellDivider
                onAddSql={() => {
                  const nextIndex = scratchCells.length + 2
                  const newCell = { id: makeCellId(), type: 'sql', sql: '', _pendingRef: `cell_${nextIndex}` }
                  addSqlCellAndScroll(newCell)
                }}
                onAddPython={() => addPythonCell()}
              />

              {cell.type === 'sql' ? (
                <ScratchSqlCell
                  cell={cell}
                  cellNumber={index + 2}
                  cellRef={`cell_${index + 2}`}
                  index={index}
                  total={scratchCells.length}
                  datastoreId={datastoreId}
                  dialect={dialect}
                  onSqlChange={(val) => updateCellSql(cell.id, val)}
                  onRemove={() => removeCell(cell.id)}
                  onMoveUp={() => moveCell(cell.id, -1)}
                  onMoveDown={() => moveCell(cell.id, 1)}
                  registerRunner={(runner) => {
                    if (runner) scratchRunners.current.set(cell.id, runner)
                    else scratchRunners.current.delete(cell.id)
                  }}
                  onResult={() => {}}
                />
              ) : (
                <ScratchPythonCell
                  cell={cell}
                  cellNumber={index + 2}
                  cellRef={`cell_${index + 2}`}
                  index={index}
                  total={scratchCells.length}
                  onRemove={() => removeCell(cell.id)}
                  onMoveUp={() => moveCell(cell.id, -1)}
                  onMoveDown={() => moveCell(cell.id, 1)}
                />
              )}
            </div>
          ))}
        </div>

        {/* ════ Add-cell footer ═════════════════════════════════════════════ */}
        <div className="px-3 pb-6">
          <AddCellDivider
            onAddSql={addSqlCell}
            onAddPython={addPythonCell}
          />

          {/* Explicit footer buttons */}
          <div className="flex items-center gap-3 pt-2 flex-wrap">
            <button
              onClick={addSqlCell}
              className="inline-flex items-center gap-1.5 h-9 px-4 text-xs font-medium bg-surface border border-dashed border-border text-muted hover:text-fg hover:border-solid hover:border-primary/40 rounded-lg transition-all"
            >
              <Plus size={11} />
              <Code2 size={11} />
              SQL cell
            </button>
            <button
              onClick={addPythonCell}
              className="inline-flex items-center gap-1.5 h-9 px-4 text-xs font-medium bg-surface border border-dashed border-border text-muted hover:text-fg hover:border-solid hover:border-violet-400/50 rounded-lg transition-all"
            >
              <Plus size={11} />
              <Terminal size={11} />
              Python cell
            </button>
            {scratchCells.length === 0 ? (
              <span className="text-[11px] text-muted/70">
                Add cells to build a notebook. Results from each cell are referenceable as{' '}
                <code className="font-mono text-indigo-600 dark:text-indigo-400 bg-indigo-500/10 px-1 rounded">cell_N</code>
                {' '}in later SQL cells.
              </span>
            ) : (
              <span className="text-[11px] text-muted/70">
                Scratch cells are session-only. Only the primary query is saved.
              </span>
            )}
          </div>
        </div>
      </div>

      {/* ── Save dialog ──────────────────────────────────────────────────── */}
      {showSaveDialog && (
        <SaveDialog
          query={query}
          onConfirm={doSave}
          onCancel={() => setShowSaveDialog(false)}
          saving={saving}
        />
      )}

      {/* ── Schedule dialog ──────────────────────────────────────────────── */}
      {showScheduleDialog && (
        <ScheduleDialog
          query={query}
          params={params}
          paramValues={paramValues}
          onConfirm={handleScheduleConfirm}
          onCancel={() => setShowScheduleDialog(false)}
          scheduling={scheduling}
          status={scheduleStatus}
          createdFlow={scheduledFlow}
        />
      )}

      {/* ── Version history (kind='query', the saved query of record) ───── */}
      {isRegistered && (
        <VersionHistoryDialog
          kind="query"
          resourceId={query.id}
          resourceName={query?.name ?? query.id}
          open={historyOpen}
          onClose={() => setHistoryOpen(false)}
          onRestored={handleRestored}
          onView={setViewingVersion}
        />
      )}
    </div>
  )
}
