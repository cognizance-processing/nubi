/**
 * AutomationsPage — the single home for scheduled / automated runs.
 *
 * Route:  /automations
 *
 * Sections
 * --------
 *  1. Flows (primary, via /flows endpoints)
 *  2. Jobs  (create / edit / delete via /jobs endpoints)
 *
 * Endpoints (via api.js get/post/put/del, paths under /api/v1):
 *   GET  /flows                  list flows
 *   POST /flows/{id}/run         run a flow now
 *   PUT  /flows/{id} {enabled}   enable / disable
 *   GET  /flows/{id}/runs        flow run history
 *   GET  /flows/runs/{run_id}    single run detail
 *   GET  /jobs                   list jobs
 *   POST /jobs                   create job
 *   GET  /jobs/{id}              get job
 *   DELETE /jobs/{id}            delete job
 *   POST /jobs/{id}/run          run job now
 *   GET  /jobs/{id}/runs         job run history
 */

import { useEffect, useState, useCallback, useRef } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  CalendarClock,
  Plus,
  Play,
  Loader2,
  RefreshCw,
  ChevronRight,
  ChevronDown,
  AlertTriangle,
  FileCode2,
  Workflow,
  Clock,
  History,
  Archive,
  Trash2,
  X,
  Check,
  Terminal,
  BarChart2,
  Edit3,
  Zap,
} from 'lucide-react'
import { get, post, put, del } from '../../lib/api.js'
import { useProject } from '../../contexts/ProjectContext.jsx'
import { useCanWrite } from '../../contexts/OrgContext.jsx'

// ---------------------------------------------------------------------------
// API helpers
// ---------------------------------------------------------------------------

const listFlows      = ()         => get('/flows')
const runFlowNow     = (id)       => post(`/flows/${id}/run`, { params: {} })
const setFlowEnabled = (id, on)   => put(`/flows/${id}`, { enabled: on })
const listFlowRuns   = (id)       => get(`/flows/${id}/runs`)
const getFlowRun     = (runId)    => get(`/flows/runs/${runId}`)

const listJobs       = ()         => get('/jobs')
const createJob      = (body)     => post('/jobs', body)
const deleteJob      = (id)       => del(`/jobs/${id}`)
const runJobNow      = (id)       => post(`/jobs/${id}/run`)
const listJobRuns    = (id)       => get(`/jobs/${id}/runs`)

// ---------------------------------------------------------------------------
// Formatting helpers
// ---------------------------------------------------------------------------

function fmtTime(iso) {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return '—'
  return d.toLocaleString(undefined, {
    month: 'short', day: 'numeric',
    hour: '2-digit', minute: '2-digit',
  })
}

function fmtRelative(iso) {
  if (!iso) return null
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return null
  const diffMs = d.getTime() - Date.now()
  const future = diffMs >= 0
  const abs = Math.abs(diffMs)
  const mins = Math.round(abs / 60000)
  const hours = Math.round(abs / 3600000)
  const days = Math.round(abs / 86400000)
  let unit
  if (mins < 2) unit = 'just now'
  else if (mins < 60) unit = `${mins}m`
  else if (hours < 48) unit = `${hours}h`
  else unit = `${days}d`
  if (unit === 'just now') return unit
  return future ? `in ${unit}` : `${unit} ago`
}

function fmtDuration(startIso, endIso) {
  if (!startIso || !endIso) return null
  const ms = new Date(endIso) - new Date(startIso)
  if (ms < 0) return null
  if (ms < 1000) return `${ms}ms`
  if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`
  return `${Math.round(ms / 60000)}m`
}

const CRON_PRESETS = [
  { label: 'Every minute',   value: '* * * * *' },
  { label: 'Hourly',         value: '0 * * * *' },
  { label: 'Daily at 6am',   value: '0 6 * * *' },
  { label: 'Daily at 9am',   value: '0 9 * * *' },
  { label: 'Daily at midnight', value: '0 0 * * *' },
  { label: 'Weekly (Monday)', value: '0 9 * * 1' },
  { label: 'Monthly',        value: '0 0 1 * *' },
  { label: 'Custom…',        value: '__custom__' },
]

const CRON_MAP = Object.fromEntries(CRON_PRESETS.filter(p => p.value !== '__custom__').map(p => [p.value, p.label]))

function humanSchedule(schedule) {
  if (!schedule) return 'Manual'
  const trimmed = String(schedule).trim()
  if (CRON_MAP[trimmed]) return CRON_MAP[trimmed]
  const everyMatch = trimmed.match(/^@?every\s+(.+)$/i)
  if (everyMatch) return `Every ${everyMatch[1]}`
  const parts = trimmed.split(/\s+/)
  if (parts.length === 5) {
    const [min, hour, dom, mon, dow] = parts
    if (dom === '*' && mon === '*' && dow === '*' && /^\d+$/.test(min) && /^\d+$/.test(hour)) {
      const h = hour.padStart(2, '0'), m = min.padStart(2, '0')
      return `Daily at ${h}:${m}`
    }
    if (hour === '*' && dom === '*' && mon === '*' && dow === '*' && /^\d+$/.test(min)) {
      return min === '0' ? 'Hourly' : `Hourly at :${min.padStart(2, '0')}`
    }
  }
  return trimmed
}

function flowType(flow) {
  const tasks = flow?.spec?.tasks ?? []
  if (tasks.length === 1 && tasks[0]?.kind === 'query') return 'scheduled_query'
  return 'workflow'
}

// ---------------------------------------------------------------------------
// Small reusable UI atoms
// ---------------------------------------------------------------------------

const STATE_STYLES = {
  pending:   'bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-300',
  running:   'bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-300',
  success:   'bg-emerald-100 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-300',
  failed:    'bg-red-100 text-red-700 dark:bg-red-900/40 dark:text-red-300',
  error:     'bg-red-100 text-red-700 dark:bg-red-900/40 dark:text-red-300',
  cancelled: 'bg-slate-100 text-slate-500 dark:bg-slate-800 dark:text-slate-400',
}

function StatePill({ state }) {
  const s = (state ?? 'pending').toLowerCase()
  const style = STATE_STYLES[s] ?? 'bg-surface-2 text-muted'
  const isRunning = s === 'running'
  const isFail = s === 'failed' || s === 'error'
  return (
    <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-md text-[10px] font-semibold capitalize ${style}`}>
      {isRunning && <Loader2 size={9} className="animate-spin" />}
      {isFail && <AlertTriangle size={9} />}
      {!isRunning && !isFail && s === 'success' && <Check size={9} strokeWidth={3} />}
      {state ?? 'pending'}
    </span>
  )
}

function KindBadge({ kind }) {
  const cfg = {
    query:  { icon: FileCode2,  label: 'Query',  cls: 'bg-teal-100 text-teal-700 dark:bg-teal-900/40 dark:text-teal-300' },
    python: { icon: Terminal,   label: 'Python', cls: 'bg-violet-100 text-violet-700 dark:bg-violet-900/40 dark:text-violet-300' },
    report: { icon: BarChart2,  label: 'Report', cls: 'bg-blue-100 text-blue-700 dark:bg-blue-900/40 dark:text-blue-300' },
  }[kind] ?? { icon: Zap, label: kind, cls: 'bg-surface-2 text-muted' }
  const Icon = cfg.icon
  return (
    <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-md text-[10px] font-semibold capitalize ${cfg.cls}`}>
      <Icon size={10} strokeWidth={2.2} />
      {cfg.label}
    </span>
  )
}

function FlowTypeBadge({ type }) {
  if (type === 'scheduled_query') {
    return (
      <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md text-[10px] font-semibold bg-teal-100 text-teal-700 dark:bg-teal-900/40 dark:text-teal-300">
        <FileCode2 size={10} strokeWidth={2} />
        Scheduled query
      </span>
    )
  }
  return (
    <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md text-[10px] font-semibold bg-indigo-100 text-indigo-700 dark:bg-indigo-900/40 dark:text-indigo-300">
      <Workflow size={10} strokeWidth={2} />
      Workflow
    </span>
  )
}

function Toggle({ on, busy, onToggle }) {
  return (
    <button
      onClick={onToggle}
      disabled={busy}
      role="switch"
      aria-checked={on}
      title={on ? 'Enabled — click to disable' : 'Disabled — click to enable'}
      className={`
        relative inline-flex h-5 w-9 shrink-0 items-center rounded-full
        transition-colors disabled:opacity-50
        focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-1
        ${on ? 'bg-primary' : 'bg-surface-2 border border-border'}
      `}
    >
      <span className={`
        inline-block h-3.5 w-3.5 transform rounded-full bg-white shadow transition-transform
        ${on ? 'translate-x-[18px]' : 'translate-x-0.5'}
      `} />
    </button>
  )
}

function SectionLabel({ children, icon: Icon }) {
  return (
    <div className="flex items-center gap-2 mb-3">
      {Icon && <Icon size={13} className="text-muted" />}
      <h2 className="text-xs font-semibold text-muted uppercase tracking-wider">{children}</h2>
    </div>
  )
}

function SkeletonCard() {
  return <div className="bg-surface rounded-xl border border-border h-20 animate-pulse" />
}

// ---------------------------------------------------------------------------
// Toast
// ---------------------------------------------------------------------------

function Toast({ toast, onDismiss }) {
  useEffect(() => {
    if (!toast) return
    const t = setTimeout(onDismiss, 4000)
    return () => clearTimeout(t)
  }, [toast, onDismiss])

  if (!toast) return null
  const isError = toast.type === 'error'
  return (
    <div
      role="status"
      aria-live="polite"
      className={`
        fixed bottom-5 left-1/2 -translate-x-1/2 z-[80]
        flex items-center gap-2
        px-4 py-3 rounded-2xl shadow-xl text-sm font-medium
        border max-w-sm w-[calc(100vw-2rem)] animate-in fade-in slide-in-from-bottom-2 duration-200
        ${isError
          ? 'bg-red-600 text-white border-red-700'
          : 'bg-emerald-600 text-white border-emerald-700'}
      `}
    >
      {isError ? <AlertTriangle size={16} /> : <Check size={16} strokeWidth={3} />}
      {toast.message}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Flow run history (expandable per flow)
// ---------------------------------------------------------------------------

function FlowRunHistory({ flowId }) {
  const [runs, setRuns] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [openRunId, setOpenRunId] = useState(null)
  const [detail, setDetail] = useState(null)
  const [detailLoading, setDetailLoading] = useState(false)

  const load = useCallback(async () => {
    setLoading(true); setError(null)
    try {
      const data = await listFlowRuns(flowId)
      setRuns(Array.isArray(data) ? data : data?.runs ?? [])
    } catch (err) {
      setError(err.message ?? 'Failed to load runs')
    } finally { setLoading(false) }
  }, [flowId])

  useEffect(() => { load() }, [load])

  const selectRun = useCallback(async (runId) => {
    if (openRunId === runId) { setOpenRunId(null); setDetail(null); return }
    setOpenRunId(runId); setDetail(null); setDetailLoading(true)
    try { const data = await getFlowRun(runId); setDetail(data) }
    catch { setDetail(null) }
    finally { setDetailLoading(false) }
  }, [openRunId])

  return (
    <div className="border-t border-border bg-surface-2/30 px-4 py-3">
      <div className="flex items-center justify-between mb-2.5">
        <p className="text-[11px] font-semibold text-muted uppercase tracking-wider flex items-center gap-1.5">
          <History size={11} />
          Run history
        </p>
        <button onClick={load} disabled={loading}
          className="h-6 w-6 flex items-center justify-center rounded text-muted hover:text-fg hover:bg-surface-2 disabled:opacity-40 transition-colors"
          title="Refresh">
          <RefreshCw size={11} className={loading ? 'animate-spin' : ''} />
        </button>
      </div>

      {loading && (
        <div className="flex items-center gap-2 text-xs text-muted py-3 justify-center">
          <Loader2 size={12} className="animate-spin" /> Loading runs…
        </div>
      )}
      {!loading && error && <p className="text-xs text-red-600 dark:text-red-400 py-2">{error}</p>}
      {!loading && !error && runs.length === 0 && (
        <p className="text-xs text-muted py-3 text-center">No runs yet.</p>
      )}
      {!loading && !error && runs.length > 0 && (
        <ul className="space-y-1.5">
          {[...runs].reverse().slice(0, 20).map(run => {
            const isOpen = openRunId === run.id
            return (
              <li key={run.id} className="rounded-lg border border-border bg-surface overflow-hidden">
                <button
                  onClick={() => selectRun(run.id)}
                  className="w-full flex items-center gap-2 px-3 py-2 text-left hover:bg-surface-2 transition-colors min-h-[44px]"
                >
                  {isOpen ? <ChevronDown size={13} className="text-muted shrink-0" /> : <ChevronRight size={13} className="text-muted shrink-0" />}
                  <StatePill state={run.state} />
                  <span className="text-[11px] text-muted flex-1 min-w-0 truncate">
                    {run.trigger ? `${run.trigger} · ` : ''}
                    {fmtTime(run.started_at ?? run.created_at)}
                  </span>
                  <span className="text-[10px] font-mono text-muted/60 shrink-0">{String(run.id).slice(0, 8)}</span>
                </button>
                {isOpen && (
                  <div className="px-3 pb-2.5 pt-1 border-t border-border bg-surface-2/40">
                    {detailLoading && (
                      <div className="flex items-center gap-2 text-[11px] text-muted py-2">
                        <Loader2 size={11} className="animate-spin" /> Loading tasks…
                      </div>
                    )}
                    {!detailLoading && detail && (
                      <div className="space-y-1 pt-1.5">
                        {(detail.task_runs ?? []).length === 0 && <p className="text-[11px] text-muted py-1">No task runs recorded.</p>}
                        {(detail.task_runs ?? []).map(tr => (
                          <div key={tr.id} className="flex items-center gap-2 py-0.5">
                            <StatePill state={tr.state} />
                            <span className="text-[11px] font-medium text-fg truncate">{tr.task_key}</span>
                            {tr.attempt > 0 && <span className="text-[10px] text-muted">attempt {tr.attempt + 1}</span>}
                            {tr.error && <span className="text-[10px] text-red-600 dark:text-red-400 truncate" title={tr.error}>{tr.error}</span>}
                            <span className="text-[10px] text-muted ml-auto shrink-0">{fmtTime(tr.finished_at ?? tr.started_at)}</span>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                )}
              </li>
            )
          })}
        </ul>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Job run history (slide-in panel)
// ---------------------------------------------------------------------------

function JobRunHistory({ jobId, onClose }) {
  const [runs, setRuns] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  const load = useCallback(async () => {
    setLoading(true); setError(null)
    try {
      const data = await listJobRuns(jobId)
      setRuns(Array.isArray(data) ? data : [])
    } catch (err) {
      setError(err.message ?? 'Failed to load runs')
    } finally { setLoading(false) }
  }, [jobId])

  useEffect(() => { load() }, [load])

  return (
    <div className="flex flex-col h-full">
      <div className="flex items-center justify-between px-5 py-4 border-b border-border shrink-0">
        <div className="flex items-center gap-2">
          <History size={16} className="text-primary" />
          <h3 className="font-semibold text-sm text-fg">Run history</h3>
        </div>
        <div className="flex items-center gap-1">
          <button onClick={load} disabled={loading}
            className="h-8 w-8 flex items-center justify-center rounded-lg text-muted hover:text-fg hover:bg-surface-2 transition-colors disabled:opacity-40">
            <RefreshCw size={14} className={loading ? 'animate-spin' : ''} />
          </button>
          <button onClick={onClose}
            className="h-8 w-8 flex items-center justify-center rounded-lg text-muted hover:text-fg hover:bg-surface-2 transition-colors">
            <X size={16} />
          </button>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto px-5 py-4">
        {loading && (
          <div className="flex flex-col items-center justify-center py-16 gap-3">
            <Loader2 size={24} className="animate-spin text-primary" />
            <p className="text-sm text-muted">Loading runs…</p>
          </div>
        )}
        {!loading && error && (
          <div className="flex flex-col items-center justify-center py-12 gap-3 rounded-xl border border-dashed border-red-200 dark:border-red-900/40">
            <AlertTriangle size={20} className="text-red-500" />
            <p className="text-sm text-red-600 dark:text-red-400">{error}</p>
            <button onClick={load} className="text-xs text-muted hover:text-fg underline">Retry</button>
          </div>
        )}
        {!loading && !error && runs.length === 0 && (
          <div className="flex flex-col items-center justify-center py-16 gap-3">
            <div className="w-12 h-12 rounded-2xl bg-surface-2 flex items-center justify-center">
              <History size={22} className="text-muted" />
            </div>
            <p className="text-sm font-medium text-fg">No runs yet</p>
            <p className="text-xs text-muted">Trigger a run to see history here.</p>
          </div>
        )}
        {!loading && !error && runs.length > 0 && (
          <div className="space-y-2">
            {[...runs].reverse().map((run, idx) => {
              const isFail = run.status === 'error' || run.status === 'failed'
              const duration = fmtDuration(run.started_at, run.finished_at)
              return (
                <div key={run.id} className={`rounded-xl border p-3.5 ${isFail ? 'border-red-200 dark:border-red-900/40 bg-red-50/50 dark:bg-red-900/10' : 'border-border bg-surface'}`}>
                  <div className="flex items-start gap-2.5">
                    {/* timeline dot */}
                    <div className={`mt-1 h-2 w-2 rounded-full shrink-0 ${isFail ? 'bg-red-500' : run.status === 'success' ? 'bg-emerald-500' : 'bg-amber-400'}`} />
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2 mb-1">
                        <StatePill state={run.status} />
                        {run.row_count != null && run.row_count > 0 && (
                          <span className="text-[10px] text-muted">{run.row_count.toLocaleString()} rows</span>
                        )}
                        <span className="text-[10px] text-muted ml-auto">{idx === 0 ? 'Latest' : `#${runs.length - idx}`}</span>
                      </div>
                      <div className="flex flex-wrap items-center gap-x-3 gap-y-0.5 text-[11px] text-muted">
                        <span>Started {fmtTime(run.started_at)}</span>
                        {duration && <span>Duration: {duration}</span>}
                      </div>
                      {run.message && (
                        <p className={`mt-1.5 text-[11px] font-mono break-all leading-relaxed ${isFail ? 'text-red-700 dark:text-red-400' : 'text-muted'}`}>
                          {run.message}
                        </p>
                      )}
                    </div>
                  </div>
                </div>
              )
            })}
          </div>
        )}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Job create / edit modal
// ---------------------------------------------------------------------------

const EMPTY_JOB = { name: '', kind: 'query', target: '', schedule: '0 9 * * *', enabled: true }

function JobModal({ initial, onSave, onClose, saving }) {
  const [form, setForm] = useState(initial ?? EMPTY_JOB)
  const [customCron, setCustomCron] = useState(false)

  // Determine if the current schedule matches a preset
  const matchedPreset = CRON_PRESETS.find(p => p.value === form.schedule && p.value !== '__custom__')

  useEffect(() => {
    if (!matchedPreset) setCustomCron(true)
  }, [])

  const set = (key, val) => setForm(f => ({ ...f, [key]: val }))

  const handleSchedulePreset = (val) => {
    if (val === '__custom__') { setCustomCron(true); return }
    setCustomCron(false)
    set('schedule', val)
  }

  const handleSubmit = (e) => {
    e.preventDefault()
    onSave(form)
  }

  const isEdit = !!initial?.id

  return (
    <div className="fixed inset-0 z-[70] flex items-end sm:items-center justify-center p-0 sm:p-4">
      {/* Backdrop */}
      <div className="absolute inset-0 bg-black/40 backdrop-blur-sm" onClick={onClose} />

      {/* Panel */}
      <div className="relative w-full sm:max-w-lg bg-surface rounded-t-2xl sm:rounded-2xl border border-border shadow-2xl flex flex-col max-h-[90vh]">
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-border shrink-0">
          <div className="flex items-center gap-2.5">
            <div className="w-8 h-8 rounded-xl bg-primary/10 flex items-center justify-center">
              <CalendarClock size={16} className="text-primary" />
            </div>
            <h2 className="font-semibold text-base text-fg">
              {isEdit ? 'Edit automation' : 'New automation'}
            </h2>
          </div>
          <button onClick={onClose}
            className="h-8 w-8 flex items-center justify-center rounded-lg text-muted hover:text-fg hover:bg-surface-2 transition-colors">
            <X size={18} />
          </button>
        </div>

        {/* Form */}
        <form onSubmit={handleSubmit} className="flex-1 overflow-y-auto px-5 py-5 space-y-4">
          {/* Name */}
          <div>
            <label className="block text-xs font-semibold text-muted mb-1.5">Name</label>
            <input
              required
              type="text"
              placeholder="Daily revenue report"
              value={form.name}
              onChange={e => set('name', e.target.value)}
              className="
                w-full h-10 px-3 rounded-xl border border-border bg-surface-2
                text-sm text-fg placeholder:text-muted/60
                focus:outline-none focus:ring-2 focus:ring-ring focus:border-transparent
                transition-shadow
              "
            />
          </div>

          {/* Kind */}
          <div>
            <label className="block text-xs font-semibold text-muted mb-1.5">Kind</label>
            <div className="grid grid-cols-3 gap-2">
              {[
                { value: 'query',  label: 'Query',  Icon: FileCode2 },
                { value: 'python', label: 'Python', Icon: Terminal  },
                { value: 'report', label: 'Report', Icon: BarChart2 },
              ].map(({ value, label, Icon }) => (
                <button
                  key={value}
                  type="button"
                  onClick={() => set('kind', value)}
                  className={`
                    flex flex-col items-center gap-1.5 py-3 px-2 rounded-xl border text-xs font-medium transition-all
                    ${form.kind === value
                      ? 'border-primary bg-primary/5 text-primary ring-1 ring-primary/20'
                      : 'border-border text-muted hover:border-border/80 hover:text-fg hover:bg-surface-2'}
                  `}
                >
                  <Icon size={16} strokeWidth={1.8} />
                  {label}
                </button>
              ))}
            </div>
          </div>

          {/* Target */}
          <div>
            <label className="block text-xs font-semibold text-muted mb-1.5">
              {form.kind === 'query' ? 'Query ID' : form.kind === 'python' ? 'Python code' : 'Report config (JSON)'}
            </label>
            {form.kind === 'python' ? (
              <textarea
                required
                rows={5}
                placeholder={'# Python script\nprint("hello nubi")'}
                value={form.target}
                onChange={e => set('target', e.target.value)}
                className="
                  w-full px-3 py-2.5 rounded-xl border border-border bg-surface-2
                  text-sm text-fg placeholder:text-muted/60 font-mono
                  focus:outline-none focus:ring-2 focus:ring-ring focus:border-transparent
                  resize-none transition-shadow
                "
              />
            ) : form.kind === 'report' ? (
              <textarea
                required
                rows={4}
                placeholder={'{\n  "board_id": "uuid",\n  "recipients": ["you@example.com"]\n}'}
                value={form.target}
                onChange={e => set('target', e.target.value)}
                className="
                  w-full px-3 py-2.5 rounded-xl border border-border bg-surface-2
                  text-sm text-fg placeholder:text-muted/60 font-mono
                  focus:outline-none focus:ring-2 focus:ring-ring focus:border-transparent
                  resize-none transition-shadow
                "
              />
            ) : (
              <input
                required
                type="text"
                placeholder="query-uuid or registered query name"
                value={form.target}
                onChange={e => set('target', e.target.value)}
                className="
                  w-full h-10 px-3 rounded-xl border border-border bg-surface-2
                  text-sm text-fg placeholder:text-muted/60
                  focus:outline-none focus:ring-2 focus:ring-ring focus:border-transparent
                  transition-shadow
                "
              />
            )}
          </div>

          {/* Schedule */}
          <div>
            <label className="block text-xs font-semibold text-muted mb-1.5">Schedule</label>
            <select
              value={customCron ? '__custom__' : (form.schedule || '__custom__')}
              onChange={e => handleSchedulePreset(e.target.value)}
              className="
                w-full h-10 px-3 rounded-xl border border-border bg-surface-2
                text-sm text-fg
                focus:outline-none focus:ring-2 focus:ring-ring focus:border-transparent
                transition-shadow mb-2
              "
            >
              {CRON_PRESETS.map(p => (
                <option key={p.value} value={p.value}>{p.label}</option>
              ))}
            </select>
            {customCron && (
              <div>
                <input
                  type="text"
                  placeholder="*/15 * * * * — cron expression"
                  value={form.schedule}
                  onChange={e => set('schedule', e.target.value)}
                  className="
                    w-full h-10 px-3 rounded-xl border border-border bg-surface-2
                    text-sm text-fg placeholder:text-muted/60 font-mono
                    focus:outline-none focus:ring-2 focus:ring-ring focus:border-transparent
                    transition-shadow
                  "
                />
                <p className="text-[10px] text-muted mt-1.5">
                  Standard 5-field cron: min hour dom month dow
                </p>
              </div>
            )}
          </div>

          {/* Enabled */}
          <div className="flex items-center justify-between py-1">
            <div>
              <p className="text-sm font-medium text-fg">Enabled</p>
              <p className="text-xs text-muted">Run on schedule automatically</p>
            </div>
            <Toggle on={form.enabled} onToggle={() => set('enabled', !form.enabled)} />
          </div>
        </form>

        {/* Footer */}
        <div className="flex items-center justify-end gap-2.5 px-5 py-4 border-t border-border shrink-0">
          <button type="button" onClick={onClose}
            className="h-9 px-4 rounded-xl border border-border text-sm font-medium text-muted hover:text-fg hover:bg-surface-2 transition-colors focus:outline-none focus:ring-2 focus:ring-ring">
            Cancel
          </button>
          <button
            onClick={handleSubmit}
            disabled={saving || !form.name || !form.target || !form.schedule}
            className="
              inline-flex items-center gap-2 h-9 px-5 rounded-xl
              bg-primary text-primary-fg text-sm font-semibold
              hover:opacity-90 disabled:opacity-50 disabled:cursor-not-allowed
              transition-opacity shadow-sm focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2
            "
          >
            {saving && <Loader2 size={14} className="animate-spin" />}
            {saving ? 'Saving…' : isEdit ? 'Save changes' : 'Create automation'}
          </button>
        </div>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Flow row
// ---------------------------------------------------------------------------

function FlowRow({ flow, onChanged, onToast, canWrite }) {
  const [expanded, setExpanded] = useState(false)
  const [running, setRunning] = useState(false)
  const [toggling, setToggling] = useState(false)
  const navigate = useNavigate()

  const type = flowType(flow)
  const enabled = flow.enabled !== false

  const handleRun = useCallback(async () => {
    setRunning(true)
    try {
      await runFlowNow(flow.id)
      onToast?.(`Started "${flow.name}"`, 'success')
      onChanged?.()
      setExpanded(true)
    } catch (err) {
      onToast?.(err.message ?? 'Run failed', 'error')
    } finally { setRunning(false) }
  }, [flow, onChanged, onToast])

  const handleToggle = useCallback(async () => {
    setToggling(true)
    try {
      await setFlowEnabled(flow.id, !enabled)
      onChanged?.()
    } catch (err) {
      onToast?.(err.message ?? 'Update failed', 'error')
    } finally { setToggling(false) }
  }, [flow, enabled, onChanged, onToast])

  const nextRel = fmtRelative(flow.next_run_at)
  const lastRel = fmtRelative(flow.last_run_at)

  return (
    <div className="bg-surface rounded-xl border border-border overflow-hidden hover:border-border/80 transition-colors">
      <div className="flex flex-col sm:flex-row sm:items-center gap-3 p-4">
        {/* Name + badges */}
        <div className="flex-1 min-w-0">
          <div className="flex flex-wrap items-center gap-2 mb-1">
            <h3 className="font-semibold text-sm text-fg truncate">{flow.name}</h3>
            <FlowTypeBadge type={type} />
            {!enabled && (
              <span className="inline-flex items-center px-2 py-0.5 rounded-md text-[10px] font-medium bg-surface-2 text-muted">
                Disabled
              </span>
            )}
          </div>
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px] text-muted">
            <span className="inline-flex items-center gap-1">
              <Clock size={10} />
              {humanSchedule(flow.schedule)}
            </span>
            <span>
              Next: <span className="text-fg/80">{fmtTime(flow.next_run_at)}</span>
              {nextRel && <span className="text-muted/60 ml-1">({nextRel})</span>}
            </span>
            <span>
              Last: <span className="text-fg/80">{fmtTime(flow.last_run_at)}</span>
              {lastRel && <span className="text-muted/60 ml-1">({lastRel})</span>}
            </span>
          </div>
        </div>

        {/* Actions */}
        <div className="flex items-center gap-2 shrink-0">
          <Toggle
            on={enabled}
            busy={toggling || !canWrite}
            onToggle={canWrite ? handleToggle : undefined}
          />

          <button
            onClick={handleRun}
            disabled={running || !canWrite}
            title={canWrite ? 'Run now' : 'Read-only — you don’t have permission to run flows'}
            className="
              inline-flex items-center gap-1.5 h-9 px-3 rounded-lg
              text-xs font-medium border border-border
              text-muted hover:text-fg hover:bg-surface-2
              disabled:opacity-50 disabled:cursor-not-allowed
              transition-colors focus:outline-none focus:ring-2 focus:ring-ring
            "
          >
            {running ? <Loader2 size={12} className="animate-spin" /> : <Play size={12} strokeWidth={2.2} />}
            {running ? 'Running…' : 'Run now'}
          </button>

          <button
            onClick={() => navigate(`/flows/${flow.id}`)}
            title={canWrite ? 'Edit flow' : 'View flow'}
            className="
              inline-flex items-center justify-center h-9 w-9 rounded-lg
              border border-border text-muted
              hover:text-fg hover:bg-surface-2
              transition-colors focus:outline-none focus:ring-2 focus:ring-ring
            "
          >
            <Edit3 size={13} />
          </button>

          <button
            onClick={() => setExpanded(v => !v)}
            title={expanded ? 'Collapse' : 'View run history'}
            className="
              inline-flex items-center justify-center h-9 w-9 rounded-lg
              border border-border text-muted
              hover:text-fg hover:bg-surface-2
              transition-colors focus:outline-none focus:ring-2 focus:ring-ring
            "
          >
            {expanded ? <ChevronDown size={14} /> : <History size={14} />}
          </button>
        </div>
      </div>

      {expanded && <FlowRunHistory flowId={flow.id} />}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Job card
// ---------------------------------------------------------------------------

function JobCard({ job, onToast, onDeleted, onViewRuns, canWrite }) {
  const [running, setRunning] = useState(false)
  const [deleting, setDeleting] = useState(false)
  const [confirmDelete, setConfirmDelete] = useState(false)

  const handleRun = useCallback(async () => {
    setRunning(true)
    try {
      await runJobNow(job.id)
      onToast?.(`Started "${job.name}"`, 'success')
    } catch (err) {
      onToast?.(err.message ?? 'Run failed', 'error')
    } finally { setRunning(false) }
  }, [job, onToast])

  const handleDelete = useCallback(async () => {
    setDeleting(true)
    try {
      await deleteJob(job.id)
      onToast?.(`Deleted "${job.name}"`, 'success')
      onDeleted?.()
    } catch (err) {
      onToast?.(err.message ?? 'Delete failed', 'error')
      setDeleting(false)
      setConfirmDelete(false)
    }
  }, [job, onToast, onDeleted])

  const lastRel = fmtRelative(job.last_run_at)
  const nextRel = fmtRelative(job.next_run_at)

  return (
    <div className="bg-surface rounded-xl border border-border hover:border-border/80 transition-colors overflow-hidden">
      <div className="flex flex-col sm:flex-row sm:items-center gap-3 p-4">
        <div className="flex-1 min-w-0">
          <div className="flex flex-wrap items-center gap-2 mb-1">
            <h3 className="font-semibold text-sm text-fg truncate">{job.name}</h3>
            {job.kind && <KindBadge kind={job.kind} />}
            {job.enabled === false && (
              <span className="inline-flex items-center px-2 py-0.5 rounded-md text-[10px] font-medium bg-surface-2 text-muted">
                Disabled
              </span>
            )}
          </div>
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px] text-muted">
            <span className="inline-flex items-center gap-1">
              <Clock size={10} />
              {humanSchedule(job.schedule)}
            </span>
            {job.next_run_at && (
              <span>
                Next: <span className="text-fg/80">{fmtTime(job.next_run_at)}</span>
                {nextRel && <span className="text-muted/60 ml-1">({nextRel})</span>}
              </span>
            )}
            <span>
              Last: <span className="text-fg/80">{fmtTime(job.last_run_at)}</span>
              {lastRel && <span className="text-muted/60 ml-1">({lastRel})</span>}
            </span>
          </div>
        </div>

        <div className="flex items-center gap-2 shrink-0">
          <button
            onClick={handleRun}
            disabled={running || !canWrite}
            title={canWrite ? 'Run now' : 'Read-only — you don’t have permission to run jobs'}
            className="
              inline-flex items-center gap-1.5 h-9 px-3 rounded-lg
              text-xs font-medium border border-border
              text-muted hover:text-fg hover:bg-surface-2
              disabled:opacity-50 disabled:cursor-not-allowed
              transition-colors focus:outline-none focus:ring-2 focus:ring-ring
            "
          >
            {running ? <Loader2 size={12} className="animate-spin" /> : <Play size={12} strokeWidth={2.2} />}
            {running ? 'Running…' : 'Run now'}
          </button>

          <button
            onClick={() => onViewRuns?.(job)}
            title="View run history"
            className="
              inline-flex items-center justify-center h-9 w-9 rounded-lg
              border border-border text-muted
              hover:text-fg hover:bg-surface-2
              transition-colors focus:outline-none focus:ring-2 focus:ring-ring
            "
          >
            <History size={14} />
          </button>

          {!canWrite ? null : confirmDelete ? (
            <div className="flex items-center gap-1.5">
              <button
                onClick={handleDelete}
                disabled={deleting}
                className="inline-flex items-center gap-1 h-9 px-3 rounded-lg bg-red-600 text-white text-xs font-semibold hover:bg-red-700 disabled:opacity-50 transition-colors"
              >
                {deleting ? <Loader2 size={12} className="animate-spin" /> : <Check size={12} />}
                Confirm
              </button>
              <button
                onClick={() => setConfirmDelete(false)}
                className="h-9 w-9 flex items-center justify-center rounded-lg border border-border text-muted hover:text-fg hover:bg-surface-2 transition-colors"
              >
                <X size={14} />
              </button>
            </div>
          ) : (
            <button
              onClick={() => setConfirmDelete(true)}
              title="Delete"
              className="
                inline-flex items-center justify-center h-9 w-9 rounded-lg
                border border-border text-muted
                hover:text-red-600 hover:border-red-200 hover:bg-red-50
                dark:hover:text-red-400 dark:hover:border-red-900/40 dark:hover:bg-red-900/10
                transition-colors focus:outline-none focus:ring-2 focus:ring-ring
              "
            >
              <Trash2 size={13} />
            </button>
          )}
        </div>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Slide-over (run history panel)
// ---------------------------------------------------------------------------

function SlideOver({ open, onClose, children }) {
  // Close on Escape
  useEffect(() => {
    if (!open) return
    const handler = (e) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [open, onClose])

  if (!open) return null

  return (
    <div className="fixed inset-0 z-[60] flex">
      <div className="absolute inset-0 bg-black/40 backdrop-blur-sm" onClick={onClose} />
      <div className="relative ml-auto w-full max-w-md bg-surface border-l border-border shadow-2xl flex flex-col h-full overflow-hidden animate-in slide-in-from-right-4 duration-200">
        {children}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// AutomationsPage
// ---------------------------------------------------------------------------

export default function AutomationsPage() {
  const navigate = useNavigate()
  const { activeProject } = useProject()
  const projectId = activeProject?.id
  const canWrite = useCanWrite()

  // Flows
  const [flows, setFlows] = useState([])
  const [flowsLoading, setFlowsLoading] = useState(true)
  const [flowsError, setFlowsError] = useState(null)

  // Jobs
  const [jobs, setJobs] = useState([])
  const [jobsLoading, setJobsLoading] = useState(true)
  const [jobsError, setJobsError] = useState(null)

  // Modals / slide-overs
  const [showJobModal, setShowJobModal] = useState(false)
  const [savingJob, setSavingJob] = useState(false)
  const [runsJob, setRunsJob] = useState(null) // job whose runs to show

  // Toast
  const [toast, setToast] = useState(null)
  const showToast = useCallback((message, type = 'success') => setToast({ message, type }), [])

  const fetchFlows = useCallback(async () => {
    setFlowsLoading(true); setFlowsError(null)
    try {
      const data = await listFlows()
      setFlows(Array.isArray(data) ? data : data?.flows ?? [])
    } catch (err) {
      setFlowsError(err.message ?? 'Failed to load flows')
    } finally { setFlowsLoading(false) }
  }, [projectId])

  const fetchJobs = useCallback(async () => {
    setJobsLoading(true); setJobsError(null)
    try {
      const data = await listJobs()
      setJobs(Array.isArray(data) ? data : data?.jobs ?? [])
    } catch (err) {
      setJobsError(err.message ?? 'Failed to load jobs')
    } finally { setJobsLoading(false) }
  }, [projectId])

  useEffect(() => { fetchFlows(); fetchJobs() }, [fetchFlows, fetchJobs])

  const refreshAll = useCallback(() => { fetchFlows(); fetchJobs() }, [fetchFlows, fetchJobs])

  const handleCreateJob = useCallback(async (form) => {
    setSavingJob(true)
    try {
      // For report kind, target should be JSON object
      let target = form.target
      if (form.kind === 'report') {
        try { target = JSON.parse(form.target) }
        catch { throw new Error('Report config must be valid JSON') }
      }
      await createJob({ name: form.name, kind: form.kind, target, schedule: form.schedule, enabled: form.enabled })
      showToast(`Created "${form.name}"`, 'success')
      setShowJobModal(false)
      fetchJobs()
    } catch (err) {
      showToast(err.message ?? 'Failed to create automation', 'error')
    } finally { setSavingJob(false) }
  }, [fetchJobs, showToast])

  const isLoading = flowsLoading || jobsLoading
  const totalCount = flows.length + jobs.length

  return (
    <div className="flex flex-col min-h-full bg-bg">
      {/* ── Header ─────────────────────────────────────────────────────────── */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4 px-6 pt-6 pb-4 border-b border-border bg-surface sticky top-0 z-10">
        <div>
          <h1 className="font-display font-semibold text-2xl text-fg flex items-center gap-2.5">
            <div className="w-8 h-8 rounded-xl bg-brand-gradient flex items-center justify-center shadow-sm">
              <CalendarClock size={17} className="text-white" />
            </div>
            Automations
          </h1>
          <p className="text-sm text-muted mt-0.5">
            Scheduled queries, workflows, and reports — everything that runs on its own.
          </p>
        </div>

        <div className="flex items-center gap-2.5">
          <button
            onClick={refreshAll}
            disabled={isLoading}
            title="Refresh"
            className="
              flex items-center justify-center w-9 h-9 rounded-xl
              border border-border text-muted hover:text-fg hover:bg-surface-2
              disabled:opacity-40 transition-colors focus:outline-none focus:ring-2 focus:ring-ring
            "
          >
            <RefreshCw size={15} className={isLoading ? 'animate-spin' : ''} strokeWidth={2} />
          </button>

          {canWrite && (
            <button
              onClick={() => setShowJobModal(true)}
              className="
                inline-flex items-center gap-2 px-4 py-2 rounded-xl
                bg-primary text-primary-fg text-sm font-semibold
                hover:opacity-90 transition-opacity shadow-sm
                focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2
                min-h-[44px]
              "
            >
              <Plus size={15} strokeWidth={2.5} />
              New automation
            </button>
          )}
        </div>
      </div>

      {/* ── Content ─────────────────────────────────────────────────────────── */}
      <div className="flex-1 px-6 py-6 max-w-4xl w-full mx-auto">

        {/* ── Flows section ───────────────────────────────────────────────── */}
        <section className="mb-10">
          <SectionLabel icon={Workflow}>Workflows &amp; Scheduled queries</SectionLabel>

          {flowsLoading && (
            <div className="space-y-3">
              {[1, 2].map(i => <SkeletonCard key={i} />)}
            </div>
          )}

          {!flowsLoading && flowsError && (
            <div className="flex flex-col items-center justify-center py-12 gap-3 rounded-xl border border-dashed border-red-200 dark:border-red-900/40">
              <div className="w-11 h-11 rounded-xl bg-red-100 dark:bg-red-900/30 flex items-center justify-center">
                <AlertTriangle size={20} className="text-red-600 dark:text-red-400" />
              </div>
              <p className="text-sm font-medium text-fg">Failed to load workflows</p>
              <p className="text-xs text-muted">{flowsError}</p>
              <button
                onClick={fetchFlows}
                className="inline-flex items-center gap-2 px-4 py-2 rounded-xl border border-border text-sm text-muted hover:text-fg hover:bg-surface-2 transition-colors"
              >
                <RefreshCw size={14} /> Retry
              </button>
            </div>
          )}

          {!flowsLoading && !flowsError && flows.length === 0 && (
            <div className="flex flex-col items-center justify-center py-12 px-6 text-center rounded-xl border border-dashed border-border">
              <div className="flex items-center justify-center w-12 h-12 rounded-2xl bg-indigo-100 dark:bg-indigo-900/30 mb-3">
                <Workflow size={22} className="text-indigo-600 dark:text-indigo-400" />
              </div>
              <p className="text-sm font-medium text-fg mb-1">No workflows yet</p>
              <p className="text-xs text-muted mb-4 max-w-xs leading-relaxed">
                Build multi-step pipelines in the Flows builder, or schedule a query from the Queries page.
              </p>
              <button
                onClick={() => navigate('/flows')}
                className="inline-flex items-center gap-2 px-4 py-2 rounded-xl bg-primary text-primary-fg text-sm font-semibold hover:opacity-90 transition-opacity shadow-sm"
              >
                <Plus size={15} /> Build a workflow
              </button>
            </div>
          )}

          {!flowsLoading && !flowsError && flows.length > 0 && (
            <div className="space-y-3">
              {flows.map(flow => (
                <FlowRow key={flow.id} flow={flow} onChanged={fetchFlows} onToast={showToast} canWrite={canWrite} />
              ))}
            </div>
          )}
        </section>

        {/* ── Jobs section ────────────────────────────────────────────────── */}
        <section>
          <div className="flex items-center justify-between mb-3">
            <SectionLabel icon={CalendarClock}>Scheduled jobs</SectionLabel>
            {jobs.length > 0 && (
              <span className="text-xs text-muted bg-surface-2 px-2 py-0.5 rounded-full">
                {jobs.length}
              </span>
            )}
          </div>

          {jobsLoading && (
            <div className="space-y-3">
              {[1, 2].map(i => <SkeletonCard key={i} />)}
            </div>
          )}

          {!jobsLoading && jobsError && (
            <div className="flex flex-col items-center justify-center py-10 gap-3 rounded-xl border border-dashed border-red-200 dark:border-red-900/40">
              <AlertTriangle size={18} className="text-red-500" />
              <p className="text-sm text-red-600 dark:text-red-400">{jobsError}</p>
              <button onClick={fetchJobs} className="text-xs text-muted hover:text-fg underline">Retry</button>
            </div>
          )}

          {!jobsLoading && !jobsError && jobs.length === 0 && totalCount === 0 && (
            // Big empty state when nothing at all exists
            <div className="flex flex-col items-center justify-center py-20 px-6 text-center rounded-xl border border-dashed border-border">
              <div className="flex items-center justify-center w-16 h-16 rounded-2xl bg-brand-gradient shadow-lg mb-5">
                <CalendarClock size={28} className="text-white" />
              </div>
              <h3 className="font-display font-semibold text-xl text-fg mb-2">No automations yet</h3>
              <p className="text-sm text-muted max-w-sm leading-relaxed mb-6">
                Schedule your first report, sync, or query to run automatically on a cron schedule.
              </p>
              <div className="flex flex-wrap items-center justify-center gap-3">
                {canWrite && (
                  <button
                    onClick={() => setShowJobModal(true)}
                    className="inline-flex items-center gap-2 px-5 py-2.5 rounded-xl bg-primary text-primary-fg text-sm font-semibold hover:opacity-90 transition-opacity shadow-sm"
                  >
                    <Plus size={15} strokeWidth={2.5} /> New automation
                  </button>
                )}
                <button
                  onClick={() => navigate('/flows')}
                  className="inline-flex items-center gap-2 px-5 py-2.5 rounded-xl border border-border text-sm font-medium text-muted hover:text-fg hover:bg-surface-2 transition-colors"
                >
                  <Workflow size={15} /> Build a workflow
                </button>
              </div>
            </div>
          )}

          {!jobsLoading && !jobsError && jobs.length === 0 && totalCount > 0 && (
            // Small empty state when flows exist but no jobs
            <div className="flex flex-col items-center justify-center py-10 px-6 text-center rounded-xl border border-dashed border-border">
              <div className="flex items-center justify-center w-10 h-10 rounded-xl bg-surface-2 mb-2.5">
                <CalendarClock size={18} className="text-muted" />
              </div>
              <p className="text-sm font-medium text-fg mb-1">No scheduled jobs</p>
              <p className="text-xs text-muted mb-3">Create one to schedule a query, Python script, or report.</p>
              {canWrite && (
                <button
                  onClick={() => setShowJobModal(true)}
                  className="inline-flex items-center gap-2 px-4 py-2 rounded-xl border border-border text-sm font-medium text-muted hover:text-fg hover:bg-surface-2 transition-colors"
                >
                  <Plus size={14} /> New automation
                </button>
              )}
            </div>
          )}

          {!jobsLoading && !jobsError && jobs.length > 0 && (
            <div className="space-y-3">
              {jobs.map(job => (
                <JobCard
                  key={job.id}
                  job={job}
                  onToast={showToast}
                  onDeleted={fetchJobs}
                  onViewRuns={(j) => setRunsJob(j)}
                  canWrite={canWrite}
                />
              ))}
            </div>
          )}
        </section>
      </div>

      {/* ── Create job modal ─────────────────────────────────────────────── */}
      {showJobModal && (
        <JobModal
          onSave={handleCreateJob}
          onClose={() => setShowJobModal(false)}
          saving={savingJob}
        />
      )}

      {/* ── Run history slide-over ───────────────────────────────────────── */}
      <SlideOver open={!!runsJob} onClose={() => setRunsJob(null)}>
        {runsJob && (
          <>
            {/* Job header in slide-over */}
            <div className="px-5 pt-3 pb-0 shrink-0">
              <div className="flex flex-wrap items-center gap-2 mb-0.5">
                <span className="text-[11px] font-semibold text-muted uppercase tracking-wider">{runsJob.name}</span>
                {runsJob.kind && <KindBadge kind={runsJob.kind} />}
              </div>
              <p className="text-[11px] text-muted mb-0.5">
                <Clock size={10} className="inline mr-1" />
                {humanSchedule(runsJob.schedule)}
              </p>
            </div>
            <JobRunHistory jobId={runsJob.id} onClose={() => setRunsJob(null)} />
          </>
        )}
      </SlideOver>

      {/* ── Toast ───────────────────────────────────────────────────────── */}
      <Toast toast={toast} onDismiss={() => setToast(null)} />
    </div>
  )
}
