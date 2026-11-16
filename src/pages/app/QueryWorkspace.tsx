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

import { useState, useCallback, useRef, useEffect, useMemo } from 'react'
import { createPortal } from 'react-dom'
import { Link } from 'react-router-dom'
import {
  Play,
  Save,
  Sparkles,
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
  ArrowLeftRight,
  MoreHorizontal,
  Wand2,
  Eye,
  EyeOff,
  Table2,
  Grid2x2,
  Search,
} from 'lucide-react'

import SqlEditor from '../../components/SqlEditor.jsx'
import Button from '../../components/ui/Button.jsx'
import Tag from '../../components/ui/Tag.jsx'
import Modal from '../../components/ui/Modal.jsx'
import Input, { Select } from '../../components/ui/Input.jsx'
import SegmentedControl from '../../components/ui/SegmentedControl.jsx'
import Tooltip from '../../components/ui/Tooltip.jsx'
import { Tabs, TabsList, TabsTrigger } from '../../components/ui/Tabs.jsx'
import '../../components/app/appPatterns.css'
import ChatPanel from '../../editor/ChatPanel.jsx'
import MetricExposePanel from '../../components/app/MetricExposePanel.jsx'
import ConnectorCombobox from '../../components/app/ConnectorCombobox.jsx'
import { metricToDraft, draftToMetricBlock } from './metricBlock.logic.js'
import {
  reconcileAutoParams,
  buildFilterParamSnippet,
  validateNewParamName,
  FILTER_PARAM_TYPES,
} from './queryParams.logic.js'
import QueryCodeView from './QueryCodeView.jsx'
import DataTable from '../../components/DataTable.jsx'
import PythonCell from '../../components/PythonCell.jsx'
import { runArrowQueryById, runArrowQuery, registerArrowTable, runLocalSqlForCell } from '../../lib/wasmRuntime.js'
import { get, post, registerQuery, renderQueryTemplate, listConnectors, listQueryWidgetUsages, fetchSchema } from '../../lib/api.js'
import VisualQueryBuilder from '../../components/VisualQueryBuilder.jsx'
import TableWidget from '../../dashboards/widgets/TableWidget.jsx'
import ChartWidget from '../../dashboards/widgets/ChartWidget.jsx'
import KpiWidget from '../../dashboards/widgets/KpiWidget.jsx'
import PivotWidget from '../../dashboards/widgets/PivotWidget.jsx'
import { VariableProvider } from '../../dashboards/VariableStore.jsx'
import { CHART_TYPES } from '../../editor/shared/constants.js'
import { CHART_ICONS, CHART_LABELS } from '../../editor/shared/chartMeta.jsx'
import { profileTable, suggestVisuals, encodingFor } from '../../editor/shared/chartSuggest.js'
import { checkpoint, restoreVersion } from '../../lib/versions.js'
import { loadSqlHistory, pushSqlHistory, clearSqlHistory } from '../../lib/sqlHistory.js'
import VersionHistoryDialog from '../../components/app/VersionHistoryDialog.jsx'
import { useUi } from '../../contexts/UiContext.jsx'
import { useCanWrite, useOrg } from '../../contexts/OrgContext.jsx'
import { useProject } from '../../contexts/ProjectContext.jsx'
import { dialectForConnectorType } from '../../lib/sqlDialect.js'
import TranspileDialog from './TranspileDialog.jsx'

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
// Param panel logic (SQL placeholder extraction, auto-param reconciliation,
// "Add filter parameter" snippet builder) — pure, unit-tested separately.
// See queryParams.logic.js.
// ---------------------------------------------------------------------------

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

/**
 * Seed the run-form values from the params' declared defaults.
 *
 * Not every default is a scalar: a `daterange` default is `{from, to}` and a
 * `multiselect` default is an array. Blanket-stringifying them (the old
 * `String(p.default)`) turned a date range into the literal `"[object Object]"`
 * and a list into `"a,b"`, and the editor then POSTed that as the parameter
 * value — so a query that a dashboard renders fine 500'd the moment you opened
 * it in the editor and pressed Run. Keep structured defaults structured.
 */
function initialParamValues(params) {
  const init = {}
  ;(params ?? []).forEach(p => {
    if (p.default == null) return
    init[p.name] = (typeof p.default === 'object') ? p.default : String(p.default)
  })
  return init
}

/**
 * True when a param's form value carries nothing the server should be told
 * about, so the query's own declared default applies instead.
 */
function isBlankParamValue(v) {
  if (v == null || v === '') return true
  if (Array.isArray(v)) return v.length === 0
  if (typeof v === 'object') return Object.values(v).every(x => x == null || x === '')
  return false
}

// ---------------------------------------------------------------------------
// ParamInputRow
// ---------------------------------------------------------------------------

function ParamInputRow({ param, value, onChange }) {
  const hasOptions = Boolean(param.options_query_id)

  const label = (
    <label className="t-label flex items-center gap-1">
      <span className="font-mono normal-case text-fg">{param.name}</span>
      <span className="normal-case text-subtle">({param.type})</span>
      {param.required && (
        <span className="text-danger" title="Required" aria-label="required">*</span>
      )}
    </label>
  )

  const inputCls = 'nubi-input nubi-input-sm'

  // A daterange is a {from, to} OBJECT, not a string. Rendering it through the
  // generic text input stringified it to "[object Object]", which then went to
  // the server as the parameter value and 500'd the query — a board using the
  // same query worked fine because it sends the object. Give it two real date
  // fields and keep the value structured.
  if (param.type === 'daterange') {
    const v = value && typeof value === 'object' ? value : {}
    const setSide = (side, next) => onChange(param.name, { ...v, [side]: next })
    return (
      <div className="flex flex-col gap-1 min-w-0">
        {label}
        <div className="flex items-center gap-1">
          <input
            type="date"
            value={v.from ?? ''}
            onChange={e => setSide('from', e.target.value)}
            aria-label={`${param.name} from`}
            className={inputCls}
          />
          <span className="text-xs text-subtle">to</span>
          <input
            type="date"
            value={v.to ?? ''}
            onChange={e => setSide('to', e.target.value)}
            aria-label={`${param.name} to`}
            className={inputCls}
          />
        </div>
      </div>
    )
  }

  // A multiselect is a LIST. Show it comma-separated but keep it an array, so
  // `{{ p | inclause }}` receives real elements rather than one joined string.
  if (param.type === 'multiselect') {
    const list = Array.isArray(value) ? value : []
    return (
      <div className="flex flex-col gap-1 min-w-0">
        {label}
        <input
          type="text"
          value={list.join(', ')}
          onChange={e => {
            const parts = e.target.value.split(',').map(s => s.trim()).filter(Boolean)
            onChange(param.name, parts)
          }}
          placeholder="Comma-separated. Empty uses the default."
          className={inputCls}
        />
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-1 min-w-0">
      {label}
      {hasOptions ? (
        <input
          type="text"
          value={value ?? ''}
          onChange={e => onChange(param.name, e.target.value)}
          placeholder={param.default != null ? `default: ${param.default}` : 'value…'}
          className="nubi-input nubi-input-sm"
        />
      ) : (
        <input
          type={paramInputType(param.type)}
          value={value ?? ''}
          onChange={e => onChange(param.name, e.target.value)}
          placeholder={param.default != null ? `default: ${param.default}` : param.required ? 'required' : 'optional'}
          className="nubi-input nubi-input-sm"
        />
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// AddFilterParamPopover — drop a guarded {% if %} filter snippet at the
// cursor without hand-typing Jinja. See queryParams.logic.js for the
// snippet grammar; this component is just the form + open/close chrome.
// ---------------------------------------------------------------------------

function AddFilterParamPopover({ existingNames, onAdd }) {
  const [open, setOpen] = useState(false)
  const [name, setName] = useState('')
  const [uiType, setUiType] = useState('multiselect')
  const ref = useRef(null)
  const nameInputRef = useRef(null)

  useEffect(() => {
    if (!open) return
    nameInputRef.current?.focus()
    function onDocClick(e) {
      if (ref.current && !ref.current.contains(e.target)) setOpen(false)
    }
    function onKey(e) { if (e.key === 'Escape') setOpen(false) }
    document.addEventListener('mousedown', onDocClick)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDocClick)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])

  const error = name.trim() ? validateNewParamName(name, existingNames) : null

  const handleSubmit = (e) => {
    e.preventDefault()
    const trimmed = name.trim()
    if (validateNewParamName(trimmed, existingNames)) return
    onAdd({ name: trimmed, uiType })
    setName('')
    setUiType('multiselect')
    setOpen(false)
  }

  return (
    <div className="relative" ref={ref}>
      <Button
        type="button"
        variant={open ? 'outline' : 'secondary'}
        size="xs"
        onClick={() => setOpen(o => !o)}
        aria-haspopup="dialog"
        aria-expanded={open}
        title="Add a filter parameter. Drops a guarded snippet at your cursor."
      >
        <ArrowLeftRight size={13} aria-hidden="true" />
        Add filter parameter
      </Button>

      {open && (
        <form
          onSubmit={handleSubmit}
          role="dialog"
          aria-label="Add filter parameter"
          className="nubi-dropdown absolute left-0 top-full mt-1.5 z-30 w-72 p-3 flex flex-col gap-3"
        >
          <div>
            <p className="m-0 text-[13px] font-semibold text-fg">Add filter parameter</p>
            <p className="mt-0.5 mb-0 text-xs text-muted leading-snug">
              Inserts a guarded snippet at your cursor and selects{' '}
              <code className="px-1 bg-surface-2 font-mono text-[11px]" style={{ borderRadius: 2 }}>{'<column>'}</code>{' '}
              so you just type the column name.
            </p>
          </div>

          <div className="flex flex-col gap-1">
            <label htmlFor="new-param-name" className="t-label">Name</label>
            <input
              id="new-param-name"
              ref={nameInputRef}
              type="text"
              value={name}
              onChange={e => setName(e.target.value)}
              placeholder="region"
              aria-invalid={error ? 'true' : undefined}
              className={`nubi-input nubi-input-sm font-mono ${error ? 'nubi-input-error' : ''}`}
            />
            {error && <p className="nubi-field-error" role="alert">{error}</p>}
          </div>

          <div className="flex flex-col gap-1">
            <label htmlFor="new-param-type" className="t-label">Type</label>
            <select
              id="new-param-type"
              value={uiType}
              onChange={e => setUiType(e.target.value)}
              className="nubi-input nubi-input-sm nubi-select"
            >
              {FILTER_PARAM_TYPES.map(t => (
                <option key={t.value} value={t.value}>{t.label}</option>
              ))}
            </select>
          </div>

          <div className="flex items-center gap-2 justify-end">
            <Button type="button" variant="ghost" size="sm" onClick={() => setOpen(false)}>Cancel</Button>
            <Button type="submit" size="sm" disabled={!name.trim() || Boolean(error)}>Insert snippet</Button>
          </div>
        </form>
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
    } catch { /* clipboard copy is best-effort */ }
  }, [name])

  return (
    <Tag
      as="button"
      type="button"
      caps={false}
      onClick={handleCopy}
      title={`Reference this cell as ${name} in later cells. Click to copy.`}
      className="group shrink-0 font-semibold"
    >
      <Hash size={10} aria-hidden="true" />
      {name}
      {copied
        ? <Check size={10} className="text-success" aria-hidden="true" />
        : <Copy size={10} className="opacity-0 transition-opacity group-hover:opacity-100 group-focus-visible:opacity-100" aria-hidden="true" />
      }
    </Tag>
  )
}

// ---------------------------------------------------------------------------
// SaveDialog — inline modal for naming a query
// ---------------------------------------------------------------------------

function SaveDialog({ query, onConfirm, onCancel, saving }) {
  const [name, setName] = useState(query?.name ?? '')

  const handleSubmit = (e) => {
    e.preventDefault()
    if (!name.trim()) return
    onConfirm(name.trim())
  }

  return (
    <Modal
      open
      onClose={saving ? undefined : onCancel}
      title="Save query"
      description="Give this query a name to save it to the registry. Boards and flows reference it by id."
      size="sm"
    >
      <form onSubmit={handleSubmit} aria-label="Save query">
        <Modal.Body>
          <Input
            id="save-query-name"
            label="Name"
            autoFocus
            onFocus={(e) => e.currentTarget.select()}
            value={name}
            onChange={e => setName(e.target.value)}
            placeholder="Revenue by region"
          />
        </Modal.Body>
        <Modal.Footer>
          <Button type="button" variant="secondary" onClick={onCancel} disabled={saving}>Cancel</Button>
          <Button type="submit" disabled={!name.trim()} loading={saving}>
            {saving ? 'Saving' : 'Save query'}
          </Button>
        </Modal.Footer>
      </form>
    </Modal>
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

function ScheduleDialog({ query, params, onConfirm, onCancel, scheduling, status, createdFlow }) {
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

  // Close on Escape.
  useEffect(() => {
    const handler = (e) => { if (e.key === 'Escape') onCancel?.() }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [onCancel])

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
    <Modal
      open
      onClose={scheduling ? undefined : onCancel}
      title="Schedule query"
      description={<>Run <span className="font-mono text-fg">{query?.name}</span> on a schedule as a one-task flow.</>}
      size="md"
    >
      <form onSubmit={handleSubmit} aria-label="Schedule query">
        {status === 'ok' ? (
          <>
            <Modal.Body className="flex flex-col gap-2">
              <p className="m-0 flex items-center gap-2 text-sm font-semibold text-fg">
                <span className="nubi-dot nubi-dot-success" aria-hidden="true" />
                Scheduled. Manage it in Flows.
              </p>
              {createdFlow?.name && (
                <p className="m-0 text-xs text-muted">
                  Created flow <span className="font-mono text-fg">{createdFlow.name}</span>.
                </p>
              )}
            </Modal.Body>
            <Modal.Footer>
              <Button type="button" variant="secondary" onClick={onCancel}>Close</Button>
              <Button asChild>
                <Link to="/flows"><ExternalLink size={14} aria-hidden="true" /> Open Flows</Link>
              </Button>
            </Modal.Footer>
          </>
        ) : (
          <>
            <Modal.Body className="flex flex-col gap-4">
              <Input
                id="schedule-query-name"
                label="Name"
                autoFocus
                value={name}
                onChange={e => setName(e.target.value)}
                placeholder="Scheduled query name"
              />

              <div className="flex flex-col gap-2">
                <span className="nubi-label">Schedule</span>
                <SegmentedControl
                  size="sm"
                  value={mode}
                  onChange={setMode}
                  options={[{ value: 'interval', label: 'Interval' }, { value: 'cron', label: 'Cron' }]}
                  className="w-fit"
                />

                {mode === 'interval' ? (
                  <div className="flex items-center gap-2">
                    <span className="text-sm text-muted">Every</span>
                    <input
                      type="number"
                      min="1"
                      value={intervalN}
                      onChange={e => setIntervalN(e.target.value)}
                      aria-label="Interval count"
                      className="nubi-input nubi-input-sm w-20 font-mono"
                    />
                    <Select
                      size="sm"
                      value={intervalUnit}
                      onChange={e => setIntervalUnit(e.target.value)}
                      aria-label="Interval unit"
                      options={INTERVAL_UNITS}
                      className="w-32"
                    />
                    <span className="ap-meta">{schedule}</span>
                  </div>
                ) : (
                  <div className="flex flex-col gap-1">
                    <input
                      type="text"
                      value={cron}
                      onChange={e => setCron(e.target.value)}
                      placeholder="0 9 * * *"
                      aria-label="Cron expression"
                      aria-invalid={cronInvalid || undefined}
                      className={`nubi-input nubi-input-sm font-mono ${cronInvalid ? 'nubi-input-error' : ''}`}
                    />
                    <p className="nubi-hint">
                      Standard 5-field cron: <span className="font-mono">min hour day month weekday</span>
                    </p>
                  </div>
                )}
              </div>

              {preview && !cronInvalid && (
                <p className="m-0 flex items-center gap-1.5 text-xs text-muted">
                  <Clock size={12} aria-hidden="true" />
                  {preview}
                </p>
              )}
              {cronInvalid && <p className="nubi-field-error" role="alert">Cron expression must have 5 fields.</p>}

              {params.length > 0 && (
                <p className="m-0 text-xs text-subtle">
                  Current parameter values ({params.map(p => p.name).join(', ')}) will be captured for each run.
                </p>
              )}

              {status === 'err' && (
                <div className="nubi-notice nubi-notice-error text-xs" role="alert">
                  <span>Couldn&rsquo;t schedule this query. Check the schedule and try again.</span>
                </div>
              )}
            </Modal.Body>
            <Modal.Footer>
              <Button type="button" variant="secondary" onClick={onCancel} disabled={scheduling}>Cancel</Button>
              <Button type="submit" disabled={!canSubmit} loading={scheduling}>
                {!scheduling && <CalendarClock size={14} aria-hidden="true" />}
                {scheduling ? 'Scheduling' : 'Schedule query'}
              </Button>
            </Modal.Footer>
          </>
        )}
      </form>
    </Modal>
  )
}

// ---------------------------------------------------------------------------
// CacheBadge
// ---------------------------------------------------------------------------

function CacheBadge({ status }) {
  if (!status || status === 'MISS') return null
  if (status === 'SAMPLE') {
    return <Tag variant="warning" dot title="Showing a sample of the result"><Database size={10} aria-hidden="true" /> Sample</Tag>
  }
  if (status === 'LOCAL') {
    return <Tag title="Computed in this browser tab"><Database size={10} aria-hidden="true" /> Local</Tag>
  }
  return <Tag variant="success" dot title="Served from cache"><Zap size={10} aria-hidden="true" /> {status}</Tag>
}

// ---------------------------------------------------------------------------
// Preview tab — render the query result as ANY board widget.
//
// The result grid itself lives in the sibling "Results" tab, so this panel is
// purely "what would this look like on a dashboard?": one widget, one stage,
// every type the board editor offers (table, pivot, KPI, and all 21 chart
// types from CHART_TYPES) rather than the three it used to hard-code.
// ---------------------------------------------------------------------------

// Non-chart widget kinds that can render straight from a query result.
const BASE_PREVIEW_TYPES = [
  { value: 'table', label: 'Table', icon: Table2 },
  { value: 'pivot', label: 'Pivot', icon: Grid2x2 },
  { value: 'kpi',   label: 'KPI',   icon: Hash },
]

/**
 * Build the synthetic widget spec fed to the real board widgets.
 *
 * `table` (an Arrow table, or undefined before the query has been run) is
 * passed separately as `providerTable` — these widgets skip their own fetch
 * and render whatever table they're handed (the BET-3 DataProvider path), so
 * this reuses the exact same query result the Results tab already shows.
 */
function buildDemoWidget(previewType, profile) {
  const nums = profile.measures.map(c => c.name)
  const dims = [...profile.categories, ...profile.temporals, ...profile.booleans, ...profile.ids].map(c => c.name)
  const all = profile.columns.map(c => c.name)

  if (previewType === 'table') return { widget: { id: 'demo-table', type: 'table' }, missing: [] }

  if (previewType === 'kpi') {
    const value = nums[0] ?? all[0]
    // Spark the second numeric column when there is one, otherwise the value
    // column itself — a KPI reads only its first row, so the sparkline is what
    // shows the shape of the result.
    const spark = nums[1] ?? nums[0]
    return {
      widget: {
        id: 'demo-kpi',
        type: 'kpi',
        encoding: { value, spark },
        props: { label: value ?? 'Value' },
      },
      missing: value ? [] : ['a value column'],
    }
  }

  if (previewType === 'pivot') {
    // rows and cols must be DIFFERENT columns — pivoting a column against
    // itself just draws a diagonal.
    const missing = []
    if (dims.length < 2) missing.push('two text columns (one for rows, one for columns)')
    if (nums.length < 1) missing.push('a numeric column to aggregate')
    return {
      widget: {
        id: 'demo-pivot',
        type: 'pivot',
        encoding: { rows: dims[0], cols: dims[1], value: nums[0] },
        props: { agg: 'sum' },
      },
      missing,
    }
  }

  // Everything else is a chart type from CHART_TYPES.
  const { encoding, missing } = encodingFor(previewType, profile)
  return {
    widget: { id: `demo-${previewType}`, type: 'chart', chart_type: previewType, encoding },
    missing,
  }
}

/**
 * One suggested visual — icon, name, and the one-line reason it fits. The
 * first entry is flagged as the best match so the eye lands on it.
 */
function SuggestionCard({ suggestion, icon: Icon, label, active, best, onClick }) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className="flex w-full items-start gap-2 border p-2 text-left transition-colors nubi-focus-inset"
      style={{
        borderRadius: 2,
        borderColor: active ? 'var(--accent)' : 'var(--border)',
        background: active ? 'var(--accent-soft)' : 'var(--surface)',
      }}
    >
      <span
        className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center"
        style={{ borderRadius: 2, background: active ? 'var(--ink)' : 'var(--surface-2)', color: active ? 'var(--ink-fg)' : 'var(--text-muted)' }}
      >
        <Icon size={14} aria-hidden="true" />
      </span>
      <span className="min-w-0 flex-1">
        <span className="flex items-center gap-1.5">
          <span className="truncate text-xs font-semibold leading-tight">{label}</span>
          {best && <Tag variant="ink">Best</Tag>}
        </span>
        <span className="mt-0.5 block text-[11px] leading-snug text-muted">{suggestion.reason}</span>
      </span>
    </button>
  )
}

/** One button in the widget-type rail. */
function PreviewTypeButton({ icon: Icon, label, active, onClick }) {
  return (
    <button
      type="button"
      onClick={onClick}
      title={label}
      aria-pressed={active}
      className="flex h-[52px] flex-col items-center justify-center gap-1 border px-1 text-[11px] font-medium transition-colors nubi-focus-inset"
      style={{
        borderRadius: 2,
        borderColor: active ? 'var(--ink)' : 'var(--border)',
        background: active ? 'var(--ink)' : 'var(--surface)',
        color: active ? 'var(--ink-fg)' : 'var(--text-muted)',
      }}
    >
      <Icon size={15} aria-hidden="true" />
      <span className="max-w-full truncate leading-none">{label}</span>
    </button>
  )
}

function QueryPreviewTab({ table }) {
  const [search, setSearch] = useState('')

  // Profile once per result; every picker below reads from it.
  const profile = useMemo(() => profileTable(table), [table])
  const suggestions = useMemo(() => suggestVisuals(profile), [profile])

  // Open on the best suggestion for THIS result; a new result re-suggests
  // unless the user has already chosen something for it.
  const [choice, setChoice] = useState({ table: null, type: null })
  const previewType = choice.table === table && choice.type
    ? choice.type
    : (suggestions[0]?.type ?? 'bar')
  const setPreviewType = (type) => setChoice({ table, type })

  const chartEntries = CHART_TYPES.map(t => ({
    value: t,
    label: CHART_LABELS[t] ?? t,
    icon: CHART_ICONS[t] ?? Layers,
  }))
  const needle = search.trim().toLowerCase()
  const matches = (e) => !needle || e.label.toLowerCase().includes(needle) || e.value.includes(needle)
  const baseShown = BASE_PREVIEW_TYPES.filter(matches)
  const chartsShown = chartEntries.filter(matches)

  const built: { widget: Record<string, any>; missing: string[] } = buildDemoWidget(previewType, profile)
  // A suggestion may carry presentation props (e.g. horizontal bars for long
  // labels); apply them so the preview matches what the card promised.
  const chosen = suggestions.find(sg => sg.type === previewType)
  const demoWidget = (chosen?.props || chosen?.config)
    ? {
        ...built.widget,
        props: { ...(built.widget.props ?? {}), ...(chosen.props ?? {}) },
        config: { ...(built.widget.config ?? {}), ...(chosen.config ?? {}) },
      }
    : built.widget
  const missing = built.missing
  const activeLabel =
    BASE_PREVIEW_TYPES.find(t => t.value === previewType)?.label ??
    CHART_LABELS[previewType] ?? previewType

  if (!table) {
    return (
      <div className="flex flex-col items-center justify-center gap-1.5 h-full py-16 text-center">
        <Layers size={18} className="text-muted/50" />
        <p className="text-xs text-muted">Run the query to preview it as a widget.</p>
      </div>
    )
  }

  return (
    <div className="flex h-full min-h-0">

      {/* ── Widget-type rail — every type the board editor offers ── */}
      <div className="w-[188px] shrink-0 border-r border-border flex flex-col min-h-0">
        <div className="p-2 shrink-0">
          <div className="relative">
            <Search size={11} className="absolute left-2 top-1/2 -translate-y-1/2 text-muted pointer-events-none" />
            <input
              value={search}
              onChange={e => setSearch(e.target.value)}
              placeholder="Find a widget…"
              className="w-full h-7 pl-6 pr-2 text-[11px] rounded-md border border-border bg-surface text-fg placeholder:text-muted focus:outline-none focus:ring-2 focus:ring-ring/50"
            />
          </div>
        </div>
        <div className="flex-1 min-h-0 overflow-y-auto px-2 pb-2 space-y-2">
          {!needle && suggestions.length > 0 && (
            <div>
              <p className="text-[11px] font-semibold text-muted uppercase tracking-wider mb-1 px-0.5 flex items-center gap-1">
                <Sparkles size={9} /> Suggested for this data
              </p>
              <div className="space-y-1">
                {suggestions.map((sg, i) => (
                  <SuggestionCard
                    key={sg.type}
                    suggestion={sg}
                    icon={BASE_PREVIEW_TYPES.find(t => t.value === sg.type)?.icon ?? CHART_ICONS[sg.type] ?? Layers}
                    label={BASE_PREVIEW_TYPES.find(t => t.value === sg.type)?.label ?? CHART_LABELS[sg.type] ?? sg.type}
                    active={previewType === sg.type}
                    best={i === 0}
                    onClick={() => setPreviewType(sg.type)}
                  />
                ))}
              </div>
            </div>
          )}
          {baseShown.length > 0 && (
            <div>
              <p className="t-label mb-1">Data</p>
              <div className="grid grid-cols-3 gap-1.5">
                {baseShown.map(t => (
                  <PreviewTypeButton
                    key={t.value}
                    icon={t.icon}
                    label={t.label}
                    active={previewType === t.value}
                    onClick={() => setPreviewType(t.value)}
                  />
                ))}
              </div>
            </div>
          )}
          {chartsShown.length > 0 && (
            <div>
              <p className="t-label mb-1">Charts</p>
              <div className="grid grid-cols-3 gap-1.5">
                {chartsShown.map(t => (
                  <PreviewTypeButton
                    key={t.value}
                    icon={t.icon}
                    label={t.label}
                    active={previewType === t.value}
                    onClick={() => setPreviewType(t.value)}
                  />
                ))}
              </div>
            </div>
          )}
          {baseShown.length === 0 && chartsShown.length === 0 && (
            <p className="text-xs text-muted text-center py-6">No widget matches “{search}”.</p>
          )}
        </div>
      </div>

      {/* ── Stage — the widget rendered exactly as a board tile would ── */}
      <div className="flex-1 min-w-0 flex flex-col p-3 gap-2">
        <div className="flex items-center gap-2 shrink-0">
          <span className="text-xs font-semibold text-fg">{activeLabel}</span>
          {missing.length === 0 && (
            <span className="ap-meta truncate">
              {Object.entries(demoWidget.encoding ?? {})
                .filter(([, v]) => v != null && v !== '' && (!Array.isArray(v) || v.length))
                .map(([k, v]) => `${k}: ${Array.isArray(v) ? v.join(', ') : v}`)
                .join('  ·  ')}
            </span>
          )}
        </div>

        {missing.length > 0 ? (
          <div className="flex-1 min-h-0 border border-dashed border-frame-line flex flex-col items-center justify-center gap-1.5 text-center px-6" style={{ borderRadius: 2 }}>
            <AlertCircle size={16} className="text-muted" aria-hidden="true" />
            <p className="text-xs text-fg">
              This result doesn't have the columns a {activeLabel} widget needs.
            </p>
            <p className="text-[11px] text-muted">
              Missing: {missing.join(', ')}
            </p>
            <p className="m-0 text-xs text-subtle">
              Adjust the SQL above, or pick another widget type.
            </p>
          </div>
        ) : (
          // VariableProvider is otherwise unused here — providerTable already
          // supplies the data — but the widgets call useResolvedParams()
          // unconditionally (hooks can't be conditional), and that hook throws
          // outside a VariableProvider ancestor.
          <VariableProvider>
            <div className="flex-1 min-h-0 border border-border bg-surface overflow-hidden" style={{ borderRadius: 2 }}>
              {demoWidget.type === 'table' && <TableWidget widget={demoWidget} providerTable={table} />}
              {demoWidget.type === 'pivot' && <PivotWidget widget={demoWidget} providerTable={table} />}
              {demoWidget.type === 'chart' && <ChartWidget widget={demoWidget} providerTable={table} />}
              {demoWidget.type === 'kpi'   && <KpiWidget   widget={demoWidget} providerTable={table} />}
            </div>
          </VariableProvider>
        )}
      </div>
    </div>
  )
}

function ConnectedWidgetsTab({ queryId }) {
  const [usages, setUsages] = useState(null) // null = loading
  const [loadError, setLoadError] = useState(null)

  useEffect(() => {
    if (!queryId) return
    let cancelled = false
    setUsages(null)
    setLoadError(null)
    listQueryWidgetUsages(queryId)
      .then(data => { if (!cancelled) setUsages(data) })
      .catch(err => { if (!cancelled) setLoadError(err.message) })
    return () => { cancelled = true }
  }, [queryId])

  if (!queryId) {
    return <p className="m-0 text-sm text-muted text-center py-10 px-3">Save this query to see which widgets use it.</p>
  }
  if (usages === null) {
    return (
      <div className="flex items-center gap-2 text-xs text-muted py-10 justify-center" role="status">
        <Loader2 size={12} className="animate-spin" aria-hidden="true" /> Loading connected widgets
      </div>
    )
  }
  if (loadError) {
    return <p className="m-0 text-sm text-muted text-center py-10" role="alert">Couldn&rsquo;t load connected widgets: {loadError}</p>
  }
  if (usages.length === 0) {
    return <p className="m-0 text-sm text-muted text-center py-10">Not used by any widgets yet. Add it to a board and it appears here.</p>
  }
  return (
    <ul className="divide-y divide-border px-3 pb-3">
      {usages.map(u => (
        <li key={`${u.board_id}-${u.widget_id}`}>
          {/* Links back to the board — the return leg of the seam, so impact
              analysis ("what breaks if I change this SQL?") is one click, not
              a manual hunt through the dashboard list. */}
          <Link
            to={`/d/${u.board_id}/edit`}
            className="py-2 flex items-center justify-between gap-3 group nubi-focus-inset"
          >
            <div className="min-w-0">
              <p className="m-0 text-[13px] text-fg truncate group-hover:underline underline-offset-4">
                {u.board_name || u.board_id}
              </p>
              <p className="m-0 ap-meta truncate">{u.widget_id}</p>
            </div>
            <Tag caps={false} className="shrink-0">{u.widget_type}</Tag>
          </Link>
        </li>
      ))}
    </ul>
  )
}

// ---------------------------------------------------------------------------
// QueryHistoryTab — SQL-Lab-style scratch run history (browser-local, see
// lib/sqlHistory.js). Distinct from the formal, server-persisted version
// history (the History toolbar button / VersionHistoryDialog) — this logs
// every Run, not just checkpoints, and never leaves the browser.
// ---------------------------------------------------------------------------

function relativeTime(ts) {
  const s = Math.max(0, Math.round((Date.now() - ts) / 1000))
  if (s < 5) return 'just now'
  if (s < 60) return `${s}s ago`
  const m = Math.round(s / 60)
  if (m < 60) return `${m}m ago`
  const h = Math.round(m / 60)
  if (h < 24) return `${h}h ago`
  return `${Math.round(h / 24)}d ago`
}

function QueryHistoryTab({ entries, onRestore, onClear }) {
  const [copiedId, setCopiedId] = useState(null)

  const copy = useCallback(async (entry) => {
    try {
      await navigator.clipboard.writeText(entry.sql)
      setCopiedId(entry.id)
      setTimeout(() => setCopiedId(id => (id === entry.id ? null : id)), 1200)
    } catch { /* clipboard unavailable — silently no-op */ }
  }, [])

  if (entries.length === 0) {
    return (
      <p className="m-0 text-sm text-muted text-center py-10 px-4 leading-relaxed">
        Nothing run yet in this browser. Every query you run here is kept locally
        (up to 50), so you can get back to an earlier attempt without saving a version.
      </p>
    )
  }

  return (
    <div className="px-3 pb-3">
      <div className="flex items-center justify-between py-2">
        <span className="ap-count">{entries.length} run{entries.length !== 1 ? 's' : ''} · this browser only</span>
        <button type="button" onClick={onClear} className="ap-clear">Clear history</button>
      </div>
      <ul className="space-y-1.5">
        {entries.map(entry => (
          <li key={entry.id} className="border border-border bg-surface-2 p-2.5" style={{ borderRadius: 2 }}>
            <div className="flex items-center gap-1.5 mb-1">
              <span className={`nubi-dot ${entry.ok ? 'nubi-dot-success' : 'nubi-dot-danger'}`} aria-label={entry.ok ? 'Succeeded' : 'Failed'} />
              <span className="ap-meta">{relativeTime(entry.ranAt)}</span>
              {entry.ok && entry.rowCount != null && (
                <span className="ap-meta">· {entry.rowCount.toLocaleString()} row{entry.rowCount !== 1 ? 's' : ''}</span>
              )}
              {entry.ok && entry.elapsedMs != null && (
                <span className="ap-meta">· {entry.elapsedMs} ms</span>
              )}
              <div className="flex-1" />
              <Button variant="ghost" size="icon-xs" onClick={() => copy(entry)} title="Copy SQL" aria-label="Copy SQL">
                {copiedId === entry.id ? <Check size={13} aria-hidden="true" /> : <Copy size={13} aria-hidden="true" />}
              </Button>
              <Button variant="ghost" size="icon-xs" onClick={() => onRestore(entry.sql)} title="Restore into editor" aria-label="Restore into editor">
                <ArrowLeftRight size={13} aria-hidden="true" />
              </Button>
            </div>
            <pre className="text-[11px] font-mono text-fg whitespace-pre-wrap break-words leading-snug max-h-24 overflow-hidden">
              {entry.sql.length > 240 ? `${entry.sql.slice(0, 240)}…` : entry.sql}
            </pre>
            {!entry.ok && entry.error && (
              <p className="text-[11px] text-danger mt-1 line-clamp-2">{entry.error}</p>
            )}
          </li>
        ))}
      </ul>
    </div>
  )
}

// ---------------------------------------------------------------------------
// ConnectorPicker
// ---------------------------------------------------------------------------

function ConnectorPicker({ datastores, value, onChange }) {
  const hasDatastores = datastores.length > 0
  return (
    <div className="flex items-center gap-2 shrink-0">
      <label htmlFor="primary-connector" className="inline-flex items-center gap-1.5 t-label">
        <Database size={12} className="shrink-0" aria-hidden="true" />
        Connector
      </label>
      {hasDatastores ? (
        // Connectors come from GET /connectors. The built-in "Demo data"
        // connector (id __demo__) is included by the backend ONLY in the
        // org's demo/default project — it is not hardcoded here.
        <ConnectorCombobox
          id="primary-connector"
          datastores={datastores}
          value={value}
          onChange={onChange}
        />
      ) : (
        <Button asChild variant="secondary" size="xs">
          <Link to="/connectors" title="This project has no connectors yet. Add one.">
            <Plus size={13} aria-hidden="true" />
            Add a connector
          </Link>
        </Button>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Editor-height resize hook
// ---------------------------------------------------------------------------

const MIN_EDITOR_H = 120
const MAX_EDITOR_H = 600
const DEFAULT_EDITOR_H = 280

function useResizableHeight(initial = DEFAULT_EDITOR_H) {
  const [editorH, setEditorH] = useState(initial)
  const dragState = useRef(null)

  const startDrag = useCallback((e) => {
    e.preventDefault()
    dragState.current = { startY: e.clientY, startH: editorH }
    const onMove = (me) => {
      const delta = me.clientY - dragState.current.startY
      setEditorH(() => Math.max(MIN_EDITOR_H, Math.min(MAX_EDITOR_H, dragState.current.startH + delta)))
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
  // Revealed on hover and on keyboard focus (focus-within), never display:none.
  return (
    <div className="group relative flex items-center gap-2 py-2">
      <div className="h-px flex-1 bg-border transition-colors group-hover:bg-frame-line group-focus-within:bg-frame-line" />
      <div className="flex shrink-0 items-center gap-1.5 opacity-0 transition-opacity group-hover:opacity-100 group-focus-within:opacity-100 [@media(hover:none)]:opacity-100">
        <Button variant="secondary" size="xs" onClick={onAddSql} title="Add SQL cell here">
          <Plus size={12} aria-hidden="true" />
          SQL
        </Button>
        <Button variant="secondary" size="xs" onClick={onAddPython} title="Add Python cell here">
          <Plus size={12} aria-hidden="true" />
          Python
        </Button>
      </div>
      <div className="h-px flex-1 bg-border transition-colors group-hover:bg-frame-line group-focus-within:bg-frame-line" />
    </div>
  )
}

// ---------------------------------------------------------------------------
// CellRunStatus — compact run state chip shown in cell header
// ---------------------------------------------------------------------------

function CellRunStatus({ running, result, error }) {
  if (running) {
    return (
      <span className="inline-flex items-center gap-1.5 ap-meta" role="status">
        <Loader2 size={11} className="animate-spin" aria-hidden="true" />
        running
      </span>
    )
  }
  if (error) {
    return (
      <span className="inline-flex items-center gap-1.5 ap-meta" style={{ color: 'var(--danger-fg)' }} role="status">
        <span className="nubi-dot nubi-dot-danger" aria-hidden="true" />
        error
      </span>
    )
  }
  if (result) {
    const rows = result.table?.numRows ?? 0
    return (
      <span className="inline-flex items-center gap-1.5 ap-meta" role="status">
        <span className="nubi-dot nubi-dot-success" aria-hidden="true" />
        {rows.toLocaleString()} row{rows !== 1 ? 's' : ''}
        {result.elapsedMs != null && <span className="text-subtle">· {result.elapsedMs} ms</span>}
      </span>
    )
  }
  // Not run yet. The cell's reference name already has one home — the
  // CellNameBadge next to it — so there's nothing useful to say here.
  return null
}

// ---------------------------------------------------------------------------
// CellControls — collapse / reorder / remove, shared by every scratch cell
// ---------------------------------------------------------------------------

function CellControls({ collapsed, onToggle, onMoveUp, onMoveDown, onRemove, first, last }) {
  return (
    <div className="flex shrink-0 items-center gap-0.5">
      <Button variant="ghost" size="icon-sm" onClick={onToggle} aria-expanded={!collapsed}
        title={collapsed ? 'Expand cell' : 'Collapse cell'} aria-label={collapsed ? 'Expand cell' : 'Collapse cell'}>
        {collapsed ? <ChevronRight size={14} aria-hidden="true" /> : <ChevronDown size={14} aria-hidden="true" />}
      </Button>
      <Button variant="ghost" size="icon-sm" onClick={onMoveUp} disabled={first} title="Move up" aria-label="Move cell up">
        <ArrowUp size={14} aria-hidden="true" />
      </Button>
      <Button variant="ghost" size="icon-sm" onClick={onMoveDown} disabled={last} title="Move down" aria-label="Move cell down">
        <ArrowDown size={14} aria-hidden="true" />
      </Button>
      <Button variant="ghost" size="icon-sm" onClick={onRemove} title="Remove cell" aria-label="Remove cell">
        <Trash2 size={14} aria-hidden="true" />
      </Button>
    </div>
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
      // Surface a failed query as the cell's error rather than an empty grid.
      if (res?.error) {
        setRunError(res.error.message)
        setResult(null)
        return
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
      className="overflow-hidden border border-frame-line bg-surface transition-colors hover:border-ink"
      style={{ scrollMarginTop: '1rem', borderRadius: 2 }}
    >
      {/* Cell header */}
      <div className="flex items-center gap-2 px-3 py-2 border-b border-border bg-surface-2 flex-wrap gap-y-1.5 min-h-[44px]">

        {/* Cell number badge */}
        <CellNameBadge name={cellRef} />

        {/* Type badge */}
        <Tag className="shrink-0"><Code2 size={10} aria-hidden="true" /> SQL</Tag>

        {/* Run status */}
        <CellRunStatus running={running} result={result} error={runError} />

        {result?.cacheStatus && !running && (
          <CacheBadge status={result.cacheStatus} />
        )}

        <div className="flex-1" />

        <CellControls
          collapsed={collapsed}
          onToggle={() => setCollapsed(c => !c)}
          onMoveUp={onMoveUp}
          onMoveDown={onMoveDown}
          onRemove={onRemove}
          first={index === 0}
          last={index === total - 1}
        />
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
              <p className="mt-1.5 mb-0 text-xs text-muted flex items-center gap-1.5 flex-wrap">
                <FileCode2 size={12} className="text-subtle shrink-0" aria-hidden="true" />
                <span>Earlier results are queryable here, e.g.</span>
                <code className="ap-meta px-1 border border-border" style={{ borderRadius: 2 }}>SELECT * FROM cell_1</code>
              </p>
            )}
            <div className="mt-2 flex items-center gap-2 flex-wrap">
              <Button size="sm" onClick={handleRun} disabled={isEmpty} loading={running}>
                {!running && <Play size={13} aria-hidden="true" />}
                {running ? 'Running' : 'Run cell'}
              </Button>
              <span className="select-none text-xs text-subtle"><kbd className="nubi-kbd">{MOD_KEY}</kbd> <kbd className="nubi-kbd">Enter</kbd></span>
            </div>
          </div>

          {/* Drag handle */}
          <div
            className="flex items-center justify-center h-3 cursor-row-resize group select-none"
            onMouseDown={startDrag}
            title="Drag to resize editor"
          >
            <GripVertical size={12} className="rotate-90 text-subtle opacity-50 group-hover:opacity-100 transition-opacity" aria-hidden="true" />
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
              Run this cell to see rows here. Its result is then available downstream as{' '}
              <code className="ap-meta px-1 border border-border" style={{ borderRadius: 2 }}>{cellRef}</code>.
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

function ScratchPythonCell({ index, total, onRemove, onMoveUp, onMoveDown, cellRef }) {
  const [collapsed, setCollapsed] = useState(false)

  return (
    <div
      data-cell-ref={cellRef}
      className="overflow-hidden border border-frame-line bg-surface transition-colors hover:border-ink"
      style={{ scrollMarginTop: '1rem', borderRadius: 2 }}
    >
      <div className="flex items-center gap-2 px-3 py-2 border-b border-border bg-surface-2 flex-wrap gap-y-1.5 min-h-[44px]">

        {/* Cell reference badge */}
        <CellNameBadge name={cellRef} />

        <Tag className="shrink-0"><Terminal size={10} aria-hidden="true" /> Python</Tag>

        <span className="ap-meta shrink-0">on-demand kernel</span>

        <div className="flex-1" />

        <CellControls
          collapsed={collapsed}
          onToggle={() => setCollapsed(c => !c)}
          onMoveUp={onMoveUp}
          onMoveDown={onMoveDown}
          onRemove={onRemove}
          first={index === 0}
          last={index === total - 1}
        />
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

/** True below the md breakpoint, where the shell topbar has no room for a toolbar. */
function useNarrow() {
  const [narrow, setNarrow] = useState(() => {
    try { return window.matchMedia('(max-width: 767px)').matches } catch { return false }
  })
  useEffect(() => {
    let mq
    try { mq = window.matchMedia('(max-width: 767px)') } catch { return }
    const on = () => setNarrow(mq.matches)
    mq.addEventListener('change', on)
    return () => mq.removeEventListener('change', on)
  }, [])
  return narrow
}

/** The platform's run chord, spelled out (no glyph the mono font may lack). */
const IS_MAC = typeof navigator !== 'undefined' && /Mac|iPhone|iPad/.test(navigator.platform || '')
const MOD_KEY = IS_MAC ? '⌘' : 'Ctrl'

function WorkspaceToolbar({ slot, children }) {
  if (slot) {
    return createPortal(
      <div className="flex items-center gap-2 w-full min-w-0">{children}</div>,
      slot
    )
  }
  return (
    <div className="shrink-0 flex items-center gap-2 px-4 py-2 border-b border-border bg-surface flex-wrap gap-y-2 min-h-[48px]">
      {children}
    </div>
  )
}

// Vertical divider between toolbar clusters — hidden on the narrowest widths
// where every pixel of horizontal space is precious.
function ToolbarDivider() {
  return <div className="hidden sm:block w-px h-5 bg-frame-line shrink-0" aria-hidden="true" />
}

// ---------------------------------------------------------------------------
// ToolbarKebabMenu — collects secondary / rarely-used actions (Run all,
// Translate, Schedule, Checkpoint, History) behind a single "More" button so
// the primary bar stays scannable; Run + Save remain inline and prominent.
// ---------------------------------------------------------------------------

function ToolbarKebabMenu({ items }) {
  const [open, setOpen] = useState(false)
  const ref = useRef(null)

  useEffect(() => {
    if (!open) return
    const onDown = (e) => { if (ref.current && !ref.current.contains(e.target)) setOpen(false) }
    const onKey = (e) => { if (e.key === 'Escape') setOpen(false) }
    window.addEventListener('mousedown', onDown)
    window.addEventListener('keydown', onKey)
    return () => { window.removeEventListener('mousedown', onDown); window.removeEventListener('keydown', onKey) }
  }, [open])

  const visible = items.filter(i => !i.hidden)
  if (visible.length === 0) return null

  return (
    <div className="relative shrink-0" ref={ref}>
      <Button
        variant={open ? 'outline' : 'secondary'}
        size="icon-sm"
        onClick={() => setOpen(o => !o)}
        aria-label="More actions"
        aria-expanded={open}
        aria-haspopup="menu"
        title="More actions"
        data-testid="query-toolbar-more"
      >
        <MoreHorizontal size={15} aria-hidden="true" />
      </Button>

      {open && (
        <div role="menu" className="nubi-dropdown right-0 top-[calc(100%+6px)] w-56">
          {visible.map(item => (
            <button
              type="button"
              role="menuitem"
              key={item.key}
              onClick={() => { setOpen(false); item.onClick() }}
              disabled={item.disabled}
              title={item.title}
              className="nubi-dropdown-item"
            >
              <item.icon size={14} className="shrink-0 text-muted" aria-hidden="true" />
              <span className="flex-1 truncate">{item.label}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

export default function QueryWorkspace({ query, onQueryChange, onSaved, isNew, toolbarExtra = null }) {
  const canWrite = useCanWrite()
  const narrow = useNarrow()
  // Active workspace — the connector list is org/project-scoped, so switching
  // either must refetch it (see the listConnectors effect below).
  const { activeOrg } = useOrg()
  const { activeProject } = useProject()
  // AppShell topbar slot — the toolbar portals into the single top bar.
  const { topbarSlot } = useUi()
  // ── PRIMARY cell: SQL / params state (the query of record) ──────────────
  const [sql, setSql] = useState(query?.sql ?? '')
  const [params, setParams] = useState(() => query?.params ?? [])
  const [paramValues, setParamValues] = useState(() => initialParamValues(query?.params))
  // Imperative handle onto the primary SqlEditor — lets "Add filter
  // parameter" drop a snippet at the cursor instead of appending blindly.
  const sqlEditorRef = useRef(null)
  // "Show rendered SQL" preview panel (POST /query/render — never executes).
  const [showRendered, setShowRendered] = useState(false)
  const [renderedPreview, setRenderedPreview] = useState(null)
  const [renderedLoading, setRenderedLoading] = useState(false)
  const [renderedError, setRenderedError] = useState(null)

  // ── "Expose as metric" panel draft (config.metric block) ─────────────────
  const [metricDraft, setMetricDraft] = useState(() =>
    metricToDraft(query?.metric, query?.name),
  )
  const [metricCollapsed, setMetricCollapsed] = useState(true)

  // Sync the primary cell when the selected query changes.
  useEffect(() => {
    setSql(query?.sql ?? '')
    setParams(query?.params ?? [])
    setParamValues(initialParamValues(query?.params))
    // Re-parse the query's config.metric block into the panel draft; expand the
    // panel for queries that already expose a metric so it's discoverable.
    const draft = metricToDraft(query?.metric, query?.name)
    setMetricDraft(draft)
    setMetricCollapsed(!draft.enabled)
    setRightPanelTab('results')
  }, [query?.id]) // eslint-disable-line react-hooks/exhaustive-deps

  // Auto-sync params list from SQL {{placeholders}} / {% if %} guards.
  //
  // Reconciles both directions, not just additions:
  //   - a new name in the SQL that isn't declared yet → added, flagged `auto`
  //   - a declared param whose placeholder disappeared → removed, but ONLY
  //     if it still carries the `auto` flag from when this effect created it.
  //     A param the presenter added by hand (the "Add filter parameter"
  //     affordance, or by typing in the panel) is never auto-removed, even
  //     if they later delete its placeholder from the SQL — provenance is
  //     tracked on the param itself so this effect can tell the difference
  //     without guessing. See queryParams.logic.js for the pure rule.
  useEffect(() => {
    setParams(prev => reconcileAutoParams(prev, sql))
  }, [sql])

  // ── Connector (datastore) picker ─────────────────────────────────────────
  const [datastores, setDatastores] = useState([])
  const [datastoreId, setDatastoreId] = useState(query?.datastore_id ?? '')

  // ── SQL dialect ──────────────────────────────────────────────────────────
  const [dialect, setDialect] = useState(() =>
    dialectForDatastore([], query?.datastore_id ?? ''),
  )
  const lastDerivedKey = useRef(null)

  // Reload the connector list whenever the active workspace changes — it is
  // org-scoped, so switching orgs must refetch it. Keying only on mount left
  // the picker showing the previous org's connectors (or just the demo one),
  // so a query ran against the wrong datastore and failed.
  useEffect(() => {
    let alive = true
    listConnectors().then(ds => { if (alive) setDatastores(Array.isArray(ds) ? ds : []) })
    return () => { alive = false }
  }, [activeOrg?.id, activeProject?.id])

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
  // Bottom panel tab: 'results' | 'preview' | 'widgets' | 'ai' | 'history'.
  // One panel, one rendering of the result — see the panel's own note below.
  const [rightPanelTab, setRightPanelTab] = useState('results')
  const [sqlHistory, setSqlHistory] = useState(() => loadSqlHistory())
  const [builderOpen, setBuilderOpen] = useState(false)
  const [builderSchema, setBuilderSchema] = useState(null)
  useEffect(() => {
    if (!builderOpen || builderSchema) return
    let cancelled = false
    fetchSchema().then(s => { if (!cancelled) setBuilderSchema(s) })
    return () => { cancelled = true }
  }, [builderOpen, builderSchema])

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

  // ── Transpile dialog ─────────────────────────────────────────────────────
  const [transpileOpen, setTranspileOpen] = useState(false)

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
  }, [query?.id])  

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
          // Blank means "use the query's declared default" — omit it entirely
          // rather than sending '' / [] / {from:'',to:''}.
          if (isBlankParamValue(v)) return
          const descriptor = params.find(p => p.name === k)
          const ptype = descriptor?.type ?? 'text'
          if (ptype === 'number' || ptype === 'integer' || ptype === 'float') {
            namedParams[k] = Number(v)
          } else if (ptype === 'boolean') {
            namedParams[k] = v === 'true' || v === true
          } else {
            // daterange objects and multiselect arrays pass through as-is —
            // the template engine expects the structure, not a stringification.
            namedParams[k] = v
          }
        })
        res = await runArrowQueryById(query.id, {
          namedParams: Object.keys(namedParams).length > 0 ? namedParams : undefined,
          datastoreId: datastoreId || undefined,
        })
      } else {
        // Ad-hoc (unsaved) SQL cannot bind {{name}}-style named params — only
        // a REGISTERED query can (the backend's ad-hoc path only resolves
        // {{ vars.* }} org variables, nothing else). Running unfiltered here
        // would silently ignore a value the presenter just set, which reads
        // as "the filter doesn't work" — tell them plainly instead.
        const hasActiveParamValue = Object.values(paramValues).some(v => !isBlankParamValue(v))
        if (hasActiveParamValue && params.length > 0) {
          setRunError('Save this query to test its filter parameters — an unsaved query can’t bind named params yet. Save, then Run.')
          return
        }
        res = await runArrowQuery(sql, undefined, { datastoreId: datastoreId || undefined })
      }
      // Surface a failed query as the run error rather than an empty grid.
      if (res?.error) {
        setRunError(res.error.message)
        setResult(null)
        setSqlHistory(pushSqlHistory({ sql, queryName: query?.name, ok: false, error: res.error.message }))
        return
      }
      setResult(res)
      setSqlHistory(pushSqlHistory({
        sql, queryName: query?.name, ok: true,
        rowCount: res?.table?.numRows ?? null, elapsedMs: res?.elapsedMs ?? null,
      }))

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
      setSqlHistory(pushSqlHistory({ sql, queryName: query?.name, ok: false, error: err?.message ?? 'Query failed' }))
    } finally {
      setRunning(false)
    }
  }, [running, viewingVersion, query, isNew, sql, paramValues, params, datastoreId])

  // ── Run all (primary + scratch SQL cells, top to bottom) ────────────────
  const handleRunAll = useCallback(async () => {
    setRunAllLoading(true)
    try { await handleRun() } catch { /* keep running the remaining cells */ }
    for (const cell of scratchCells) {
      if (cell.type !== 'sql') continue
      const runner = scratchRunners.current.get(cell.id)
      if (runner) {
        try { await runner() } catch { /* one cell failing must not abort run-all */ }
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

  // ── Add filter parameter — drop a guarded {% if %} snippet at the cursor,
  //    multi-select every <column> placeholder, and declare the param. The
  //    SQL update itself round-trips through SqlEditor's own onChange (same
  //    as the Templates menu) — insertSnippetAtCursor edits the model, Monaco
  //    fires onChange, which calls handleSqlChange for us.
  const handleAddFilterParam = useCallback(({ name, uiType }) => {
    const { snippet, param } = buildFilterParamSnippet({ name, uiType })
    sqlEditorRef.current?.insertSnippetAtCursor(snippet)
    setParams(prev => (prev.some(p => p.name === name) ? prev : [...prev, param]))
  }, [])

  // ── Show rendered SQL — POST /query/render, never executes. Only
  //    meaningful for a SAVED query (query_id resolves declared params +
  //    RLS the same way execution would); an unsaved query can't render
  //    named params either, for the same reason it can't run them. Fetching
  //    itself is the effect below, keyed on showRendered so it also covers
  //    "keep the preview live while a param value changes".
  const handleToggleRendered = useCallback(() => {
    setShowRendered(v => !v)
  }, [])

  // Keep the preview live while the panel is open — a param value tweak
  // should update the rendered SQL without a manual re-toggle.
  useEffect(() => {
    if (!showRendered || !query?.id || isNew) return
    let cancelled = false
    const namedParams = {}
    Object.entries(paramValues).forEach(([k, v]) => {
      if (isBlankParamValue(v)) return
      namedParams[k] = v
    })
    setRenderedLoading(true)
    setRenderedError(null)
    renderQueryTemplate({
      query_id: query.id,
      named_params: Object.keys(namedParams).length > 0 ? namedParams : undefined,
      datastore_id: datastoreId || undefined,
    })
      .then(preview => { if (!cancelled) setRenderedPreview(preview) })
      .catch(err => { if (!cancelled) setRenderedError(err?.message ?? 'Could not render this query.') })
      .finally(() => { if (!cancelled) setRenderedLoading(false) })
    return () => { cancelled = true }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [showRendered, query?.id, JSON.stringify(paramValues), datastoreId])

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
      const payload: Record<string, any> = {
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
      setParamValues(initialParamValues(nextParams))
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
  // Has the primary query been run (or attempted) at least once? Drives
  // whether the results/preview row claims real vertical space or steps
  // aside so the SQL editor stays the dominant thing on screen — the same
  // "editor first, results once they exist" shape as Hex/Mode/Snowsight.
  const hasRun = Boolean(result || running || runError)

  return (
    <div className="flex flex-col h-full overflow-hidden relative">

      {/* ── Top toolbar — portaled into the AppShell top bar when available ── */}
      <WorkspaceToolbar slot={narrow ? null : topbarSlot}>
        <div className="flex items-center gap-2 min-w-0 flex-1">
          <span className="text-sm font-semibold text-fg truncate">
            {query?.name ?? (isNew ? 'New query' : 'Ad-hoc query')}
          </span>
          {isRegistered && <Tag variant="success" dot className="shrink-0">Saved</Tag>}
          {isNew && <Tag variant="warning" dot className="shrink-0 hidden sm:inline-flex">Unsaved</Tag>}
        </div>

        <ToolbarDivider />

        {/* View switcher — Notebook / Code (VS Code-style files view). Mirrors
            the flows view switcher; Code replaces the notebook body with the
            <slug>.sql + <slug>.meta.json file view. */}
        <div data-testid="query-view-switcher" className="shrink-0">
          <SegmentedControl
            size="sm"
            value={codeView ? 'code' : 'notebook'}
            onChange={(v) => setCodeView(v === 'code')}
            options={[
              { value: 'notebook', label: <Terminal size={14} aria-hidden="true" />, ariaLabel: 'Notebook / editor view' },
              { value: 'code', label: <FileCode2 size={14} aria-hidden="true" />, ariaLabel: 'Code / Files view (.sql + .meta.json)' },
            ]}
          />
        </div>

        {/* Add cells — always visible; labels collapse to icon-only below sm. */}
        <div className="flex items-center gap-1 shrink-0">
          <Button variant="ghost" size="xs" onClick={addSqlCell} title="Add a SQL scratch cell" aria-label="Add a SQL cell">
            <Plus size={13} aria-hidden="true" /> <span className="hidden sm:inline">SQL</span>
          </Button>
          <Button variant="ghost" size="xs" onClick={addPythonCell} title="Add a Python scratch cell (on-demand kernel)" aria-label="Add a Python cell">
            <Plus size={13} aria-hidden="true" /> <span className="hidden sm:inline">Python</span>
          </Button>
        </div>

        <ToolbarDivider />

        {/* Secondary actions — Run all / Translate / Schedule / Checkpoint /
            History live behind one "More" button so Run + Save stay the only
            prominent CTAs on the bar. */}
        <ToolbarKebabMenu
          items={[
            {
              key: 'run-all',
              icon: Layers,
              label: runAllLoading ? 'Running all…' : 'Run all',
              onClick: handleRunAll,
              disabled: runAllLoading,
              hidden: !scratchCells.some(c => c.type === 'sql'),
              title: 'Run the primary query then every scratch SQL cell, top to bottom',
            },
            {
              key: 'translate',
              icon: ArrowLeftRight,
              label: 'Translate SQL',
              onClick: () => setTranspileOpen(true),
              hidden: isEmptyPrimary,
              title: 'Translate SQL to a different dialect',
            },
            {
              key: 'schedule',
              icon: CalendarClock,
              label: 'Schedule',
              onClick: handleScheduleClick,
              disabled: !isRegistered || viewing,
              hidden: !canWrite,
              title: isRegistered ? 'Run this query on a schedule' : 'Save the query first',
            },
            {
              key: 'checkpoint',
              icon: GitCommitHorizontal,
              label: 'Checkpoint',
              onClick: handleCheckpoint,
              disabled: !isRegistered || saving || viewing,
              hidden: !canWrite,
              title: isRegistered ? 'Checkpoint — snapshot the current draft as a new version' : 'Save the query first',
            },
            {
              key: 'history',
              icon: History,
              label: 'Version history',
              onClick: () => setHistoryOpen(true),
              disabled: !isRegistered,
              title: isRegistered ? 'Version history' : 'Save the query first',
            },
          ]}
        />

        <ToolbarDivider />

        {/* Save — mutating (registerQuery); writers only */}
        {canWrite ? (
          <Button
            variant="secondary"
            size="sm"
            onClick={handleSaveClick}
            disabled={saving || viewing}
            data-testid="query-save-btn"
            title={isRegistered ? 'Update saved query (primary cell)' : 'Save query (primary cell)'}
            aria-label={saving ? 'Saving' : isRegistered ? 'Update saved query' : 'Save query'}
          >
            {saving ? (
              <Loader2 size={14} className="animate-spin" aria-hidden="true" />
            ) : saveStatus === 'ok' ? (
              <CheckCircle2 size={14} className="text-success" aria-hidden="true" />
            ) : saveStatus === 'err' ? (
              <AlertCircle size={14} className="text-danger" aria-hidden="true" />
            ) : (
              <Save size={14} aria-hidden="true" />
            )}
            <span className="hidden sm:inline">
              {saving ? 'Saving' : isRegistered ? 'Update' : 'Save'}
            </span>
          </Button>
        ) : (
          <Tag className="hidden sm:inline-flex" title="Read-only access">Read-only</Tag>
        )}

        {/* Run (primary, ink) */}
        <Button
          size="sm"
          onClick={handleRun}
          disabled={running || viewing}
          loading={running}
          data-testid="query-run-btn"
          title={`Run query (${MOD_KEY}+Enter)`}
        >
          {!running && <Play size={13} aria-hidden="true" />}
          <span>{running ? 'Running' : 'Run'}</span>
          <kbd className="nubi-kbd hidden sm:inline-flex" style={{ background: 'transparent', color: 'inherit', borderColor: 'currentColor', opacity: .7 }}>{MOD_KEY} Enter</kbd>
        </Button>

        {/* Page-level extras (view toggle + side-panel buttons from QueriesPage) */}
        {toolbarExtra && (
          <>
            <ToolbarDivider />
            {toolbarExtra}
          </>
        )}
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
          <div className="nubi-notice nubi-notice-info mx-3 mt-3 text-xs" role="status">
            <History size={14} className="shrink-0" aria-hidden="true" />
            <span className="flex-1 min-w-0 truncate">
              Viewing <span className="font-mono font-semibold">v{viewingVersion.version}</span> (read-only)
              {viewingVersion.message ? <span className="text-muted"> · {viewingVersion.message}</span> : null}
            </span>
            {canWrite && (
              <Button size="xs" variant="secondary" onClick={restoreViewedVersion}>Restore</Button>
            )}
            <Button size="xs" variant="secondary" onClick={() => setViewingVersion(null)}>Back to draft</Button>
          </div>
        )}

        {/* ════ PRIMARY CELL (cell_1 — the saved query of record) ══════════ */}
        <div className="px-3 pt-3">
          <div
            data-cell-ref="cell_1"
            className="overflow-hidden border border-frame-line bg-surface"
            style={{ scrollMarginTop: '1rem', borderTop: '2px solid var(--ink)', borderRadius: '0 0 2px 2px' }}
          >
            {/* Primary cell header */}
            <div className="flex items-center gap-2 px-3 py-2 border-b border-border flex-wrap gap-y-1.5 min-h-[44px]">
              {/* Cell_1 badge */}
              <CellNameBadge name="cell_1" />

              <Tag variant="ink" className="shrink-0" title="This is the query that Save persists to the registry.">
                <Star size={10} aria-hidden="true" /> Primary query
              </Tag>

              <CellRunStatus
                running={running}
                result={result}
                error={runError}
              />

              {result?.cacheStatus && !running && (
                <CacheBadge status={result.cacheStatus} />
              )}

              <div className="flex-1" />

              {!viewing && (
                <Button
                  variant={builderOpen ? 'outline' : 'secondary'}
                  size="xs"
                  onClick={() => setBuilderOpen(o => !o)}
                  aria-pressed={builderOpen}
                  title="Build a query without writing SQL"
                >
                  <Wand2 size={13} aria-hidden="true" />
                  Build
                </Button>
              )}

              {!viewing && (
                <AddFilterParamPopover
                  existingNames={params.map(p => p.name)}
                  onAdd={handleAddFilterParam}
                />
              )}

              {!viewing && query?.id && !isNew && (
                <Button
                  variant={showRendered ? 'outline' : 'secondary'}
                  size="xs"
                  onClick={handleToggleRendered}
                  aria-pressed={showRendered}
                  title="Preview the SQL that will actually run. Values stay bound placeholders, never text."
                >
                  {showRendered ? <EyeOff size={13} aria-hidden="true" /> : <Eye size={13} aria-hidden="true" />}
                  Rendered SQL
                </Button>
              )}

              <ConnectorPicker
                datastores={datastores}
                value={datastoreId}
                onChange={setDatastoreId}
              />
            </div>

            {/* ── SQL full width on top (the shape every SQL-first tool uses:
                BigQuery, Snowflake, Databricks SQL, Mode, Hex, Redash), with
                raw Results and the live widget Preview side by side beneath it.
                That keeps both visible while iterating — the legacy
                WidgetEditor.tsx behaviour worth keeping — without squeezing the
                editor to 60% or the chart to a 360px column. ── */}
            <div className="min-w-0">

            {/* Version-view params — static, read-only */}
            {viewing && Array.isArray(viewCfg.params) && viewCfg.params.length > 0 && (
              <div className="px-3 py-2.5 border-b border-border bg-surface-2">
                <p className="t-label mb-2">
                  Parameters (v{viewingVersion.version})
                </p>
                <div className="flex flex-wrap gap-1.5">
                  {viewCfg.params.map(p => (
                    <Tag key={p.name} caps={false}>
                      {p.name}
                      <span className="text-subtle">({p.type ?? 'text'})</span>
                      {p.default != null && <span className="text-subtle">= {String(p.default)}</span>}
                    </Tag>
                  ))}
                </div>
              </div>
            )}

            {/* Param inputs */}
            {!viewing && hasParams && (
              <div className="px-3 py-2.5 border-b border-border bg-surface-2">
                <p className="t-label mb-2">Parameters</p>
                <div className="flex flex-wrap gap-3">
                  {params.map(param => (
                    <div key={param.name} className="flex flex-col gap-1 min-w-0">
                      <div className="flex items-center gap-1">
                        <label className="text-[11px] font-semibold text-muted uppercase tracking-wide flex items-center gap-1">
                          <span className="font-mono normal-case text-fg/80">{param.name}</span>
                          {param.required && (
                            <span className="text-danger text-[11px]" title="Required">*</span>
                          )}
                        </label>
                        <select
                          value={param.type ?? 'text'}
                          onChange={e => handleParamDescChange(param.name, 'type', e.target.value)}
                          className="h-5 px-1 text-[11px] bg-surface border border-border rounded text-muted focus:outline-none focus:ring-1 focus:ring-ring"
                          title="Parameter type"
                        >
                          <option value="text">text</option>
                          <option value="number">number</option>
                          <option value="boolean">boolean</option>
                          <option value="date">date</option>
                          {/* daterange was missing, so a migrated filter param
                              rendered as the first option ("text") and silently
                              retyped itself the moment anything else changed. */}
                          <option value="daterange">daterange</option>
                          <option value="select">select</option>
                          <option value="multiselect">multiselect</option>
                        </select>
                        <label className="flex items-center gap-0.5 text-[11px] text-muted cursor-pointer select-none">
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
                          className="text-muted/40 hover:text-danger transition-colors"
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

            {!viewing && builderOpen && (
              <div className="px-3 pt-3">
                <VisualQueryBuilder
                  schema={builderSchema}
                  onGenerate={(generatedSql) => { handleSqlChange(generatedSql); setBuilderOpen(false) }}
                  onClose={() => setBuilderOpen(false)}
                />
              </div>
            )}

            {/* SQL editor — shows the viewed version's SQL read-only while a
                version view is active; the draft SQL state is untouched. */}
            <div className="px-3 pt-3">
              <SqlEditor
                ref={sqlEditorRef}
                value={viewing ? (typeof viewCfg.sql === 'string' ? viewCfg.sql : '') : sql}
                onChange={viewing ? () => {} : handleSqlChange}
                onRun={viewing ? undefined : handleRun}
                height={`${editorH}px`}
                dialect={dialect}
                onDialectChange={setDialect}
                readOnly={viewing}
              />
              {!viewing && isEmptyPrimary && (
                <p className="mt-1.5 mb-0 text-xs text-muted flex items-center gap-1.5 flex-wrap">
                  <FileCode2 size={12} className="text-subtle shrink-0" aria-hidden="true" />
                  New here? Use the <span className="font-medium text-fg">Templates</span> menu above for starters, or
                  type <span className="font-mono">{' {{param}} '}</span> to declare a bindable parameter.
                </p>
              )}
            </div>

            {/* Rendered SQL preview — POST /query/render, never executes.
                Shows the presenter exactly what will run: bound values stay
                placeholders in the SQL text, RLS predicates are visible too. */}
            {!viewing && showRendered && query?.id && !isNew && (
              <div className="px-3 pb-3">
                <div className="border border-border bg-surface-2 overflow-hidden" style={{ borderRadius: 2 }}>
                  <div className="flex items-center gap-1.5 px-2.5 py-1.5 border-b border-border">
                    <Eye size={12} aria-hidden="true" />
                    <span className="t-label">
                      Rendered SQL: what will actually run
                    </span>
                    {renderedLoading && <Loader2 size={10} className="animate-spin text-muted ml-1" />}
                  </div>
                  {renderedError ? (
                    <p className="px-2.5 py-2 text-[11px] text-danger">{renderedError}</p>
                  ) : renderedPreview ? (
                    <div className="px-2.5 py-2 flex flex-col gap-1.5">
                      <pre className="text-[11px] font-mono text-fg whitespace-pre-wrap break-words leading-relaxed">
                        {renderedPreview.sql}
                      </pre>
                      {Array.isArray(renderedPreview.params) && renderedPreview.params.length > 0 && (
                        <div className="flex flex-wrap items-center gap-1 pt-1 border-t border-border/60">
                          <span className="text-[11px] text-muted uppercase tracking-wide">Bound values:</span>
                          {renderedPreview.params.map((v, i) => (
                            <code
                              key={i}
                              className="px-1.5 py-0.5 bg-surface border border-border font-mono text-[11px] text-accent" style={{ borderRadius: 2 }}
                            >
                              {'$' + (i + 1)} = {JSON.stringify(v)}
                            </code>
                          ))}
                        </div>
                      )}
                    </div>
                  ) : (
                    <p className="px-2.5 py-2 text-[11px] text-muted">Loading…</p>
                  )}
                </div>
              </div>
            )}

            {/* Drag handle */}
            <div
              className="flex items-center justify-center h-4 cursor-row-resize group select-none mx-3"
              onMouseDown={startDrag}
              title="Drag to resize editor"
            >
              <GripVertical size={14} className="rotate-90 text-subtle opacity-50 group-hover:opacity-100 transition-opacity" aria-hidden="true" />
            </div>

            </div>

            {/* ── Bottom panel: ONE surface, tab-switched. Results (the raw
                grid) and Preview (the same rows as a board widget) used to sit
                side by side, which showed the same table twice whenever the
                preview was a table — and squeezed both. As tabs of one
                full-width panel each gets the whole width, and there is only
                ever one rendering of the result on screen. ── */}
            <div className="border-t border-border flex flex-col min-h-0">
              <div className="flex items-center gap-3 px-3 shrink-0 border-b border-border flex-wrap">
                <Tabs value={rightPanelTab} onValueChange={setRightPanelTab}>
                  <TabsList aria-label="Result views" className="border-b-0">
                    {[
                      { value: 'results', label: 'Results' },
                      { value: 'preview', label: 'Preview', icon: Layers },
                      { value: 'widgets', label: 'Connected' },
                      { value: 'ai', label: 'Ask AI', icon: Sparkles },
                      { value: 'history', label: 'History', icon: History },
                    ].map(t => (
                      <TabsTrigger key={t.value} value={t.value}>
                        {t.icon && <t.icon size={13} aria-hidden="true" />}
                        {t.label}
                      </TabsTrigger>
                    ))}
                  </TabsList>
                </Tabs>

                {/* Result meta — always visible, whichever tab is open, so the
                    row count and timing don't vanish when previewing. */}
                <div className="flex items-center gap-2 ml-auto py-1">
                  {running && (
                    <span className="inline-flex items-center gap-1.5 ap-meta" role="status">
                      <Loader2 size={12} className="animate-spin" aria-hidden="true" /> Streaming
                    </span>
                  )}
                  {result && !running && (
                    <>
                      <span className="ap-meta" style={{ color: 'var(--text)' }}>
                        {rowCount.toLocaleString()} row{rowCount !== 1 ? 's' : ''}
                      </span>
                      {result.elapsedMs != null && (
                        <span className="inline-flex items-center gap-1 ap-meta">
                          <Clock size={11} aria-hidden="true" /> {result.elapsedMs} ms
                        </span>
                      )}
                      <CacheBadge status={result.cacheStatus} />
                    </>
                  )}
                </div>
              </div>

              <div
                className={rightPanelTab === 'ai' ? 'min-h-0 overflow-hidden' : 'min-h-0 overflow-auto'}
                style={{ height: hasRun || rightPanelTab !== 'results' ? 400 : undefined }}
              >
                {rightPanelTab === 'results' && (
                  hasRun ? (
                    <div className="h-full px-3 py-3">
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
                    <div className="px-4 py-10 flex flex-col items-center justify-center gap-2 text-center">
                      <p className="m-0 text-sm text-muted">
                        Run the query to see rows here.
                      </p>
                      <p className="m-0 text-xs text-subtle">
                        <kbd className="nubi-kbd">{MOD_KEY}</kbd> <kbd className="nubi-kbd">Enter</kbd> runs it from the editor.
                      </p>
                    </div>
                  )
                )}
                {rightPanelTab === 'preview' && <QueryPreviewTab table={result?.table} />}
                {rightPanelTab === 'widgets' && <ConnectedWidgetsTab queryId={query?.id} />}
                {rightPanelTab === 'ai' && (
                  <ChatPanel
                    title="Ask AI"
                    tagline="Ask about this query, request changes, or generate SQL from a description — watch each tool run live."
                    placeholder="Ask about this query, or describe SQL to generate…"
                    suggestions={[
                      'Explain what this query does',
                      'Add a date-range filter',
                      'Optimise this query',
                      'Write a query for total sales by region',
                    ]}
                    system={`The user is editing a SQL query${query?.name ? ` named "${query.name}"` : ''} in the ${dialect} dialect. Current SQL:\n\`\`\`sql\n${sql || '(empty)'}\n\`\`\`\nWhen asked to write or modify SQL, prefer the generate_sql tool and keep results in the ${dialect} dialect. Do not propose a dashboard spec here — this is the query editor, not the dashboard editor.`}
                    onInsertSql={handleSqlChange}
                  />
                )}
                {rightPanelTab === 'history' && (
                  <QueryHistoryTab
                    entries={sqlHistory}
                    onRestore={handleSqlChange}
                    onClear={() => setSqlHistory(clearSqlHistory())}
                  />
                )}
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
            <Button variant="secondary" size="sm" onClick={addSqlCell}>
              <Plus size={14} aria-hidden="true" />
              SQL cell
            </Button>
            <Button variant="secondary" size="sm" onClick={addPythonCell}>
              <Plus size={14} aria-hidden="true" />
              Python cell
            </Button>
            {scratchCells.length === 0 ? (
              <span className="text-xs text-subtle">
                Add cells to build a notebook. Each result is referenceable as{' '}
                <code className="ap-meta px-1 border border-border" style={{ borderRadius: 2 }}>cell_N</code>
                {' '}in later SQL cells.
              </span>
            ) : (
              <span className="text-xs text-subtle">
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

      {/* ── Transpile dialog ─────────────────────────────────────────────── */}
      <TranspileDialog
        open={transpileOpen}
        onClose={() => setTranspileOpen(false)}
        initialSql={sql}
        onApply={(translatedSql) => {
          handleSqlChange(translatedSql)
        }}
      />
    </div>
  )
}
