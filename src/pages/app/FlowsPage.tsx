/**
 * FlowsPage — Flows orchestrator page.
 *
 * Route:  /flows          → FlowsPage (no selected flow)
 *         /flows/:id      → FlowsPage with a pre-selected flow
 *
 * Layout:
 *   Desktop  ≥ md:
 *     ┌──────────────────┬────────────────────────────────────────────┐
 *     │  Left rail       │  Right pane                                │
 *     │  (flow list)     │  [Builder tab] | [Runs tab]                │
 *     └──────────────────┴────────────────────────────────────────────┘
 *
 *   Mobile < md:
 *     ┌────────────────────────────────────────────┐
 *     │  [Builder tab] | [Runs tab]                │
 *     │  (full width)                              │
 *     └────────────────────────────────────────────┘
 *     + bottom sheet / slide-up drawer for the flow list
 *
 * "New flow" seeds an empty spec: { version:1, name:'new', params:[], tasks:[] }
 * and starts the user in the Builder tab.
 *
 * After a run is triggered the page automatically switches to the Runs tab
 * and shows the live FlowRunView.
 *
 * Saving & dirty tracking (single owner — this page):
 *   - The last-saved spec is snapshotted as JSON (on flow select / new draft /
 *     successful save); `dirty` is a cheap JSON.stringify comparison.
 *   - All saves (top-bar Save and autosave) go through one `performSave`,
 *     serialised so writes can't land out of order; stale responses are
 *     dropped via a request-sequence counter.
 *   - Autosave: existing flows (with an id) are saved ~2s after the last edit.
 *     Unsaved drafts are NEVER auto-created — they keep manual Save only.
 *   - While dirty: a `beforeunload` handler warns on tab close/navigation, and
 *     in-app swaps away from the editor (selecting another flow / New flow)
 *     ask for confirmation first.
 */

import { useState, useEffect, useCallback, useRef, useMemo } from 'react'
import { createPortal } from 'react-dom'
import { useParams, useNavigate } from 'react-router-dom'
import {
  Plus,
  Check,
  RefreshCw,
  GitBranch,
  Trash2,
  Loader2,
  Play,
  List,
  KeyRound,
  X,
  SlidersHorizontal,
  Variable,
  PanelRightClose,
  ShieldCheck,
  Save,
  Code2,
  AlertCircle,
  CheckCircle2,
  Share2,
  LayoutList,
  Database,
  FileText,
  CalendarClock,
  History,
  GitCommitHorizontal,
  FileCode2,
  Clock,
  ChevronRight,
  MoreHorizontal,
} from 'lucide-react'

import { useUi } from '../../contexts/UiContext.jsx'
import { useCanWrite } from '../../contexts/OrgContext.jsx'
import { useEnv } from '../../contexts/EnvContext.jsx'
import { checkpoint, restoreVersion } from '../../lib/versions.js'
import VersionHistoryDialog from '../../components/app/VersionHistoryDialog.jsx'
import { toast } from '../../components/ui/Toast.jsx'
import Skeleton from '../../components/ui/Skeleton.jsx'
import Badge from '../../components/ui/Badge.jsx'
import EmptyState from '../../components/ui/EmptyState.jsx'
import { SearchBar } from '../../components/app/PageShell.jsx'
import {
  listFlows,
  getFlow,
  deleteFlow,
  listFlowRuns,
  validateFlow,
  createFlow,
  updateFlow,
  runFlow,
} from '../../lib/flows.js'
import FlowBuilder from '../../flows/FlowBuilder.jsx'
import { SaveStatusBadge } from '../../flows/NotebookView.jsx'
import FlowRunView from '../../flows/FlowRunView.jsx'
import { AddTaskPanel } from '../../flows/AddTaskPanel.jsx'
import NodeInspector from '../../flows/NodeInspector.jsx'
import VariablesPanel from '../../flows/VariablesPanel.jsx'
import SecretsPanel from '../../flows/SecretsPanel.jsx'

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const EMPTY_SPEC = { version: 1, name: 'new', params: [], tasks: [] }

// Debounce window for autosaving existing flows after the last edit.
const AUTOSAVE_DELAY_MS = 2000

const UNSAVED_CONFIRM_MSG = 'You have unsaved changes that will be lost. Discard them?'

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function newFlowDraft() {
  return {
    _isNew: true,
    id: null,
    name: 'new',
    spec: { ...EMPTY_SPEC },
  }
}

/** Compact "Xh ago" / "just now" relative-time label for rail/list metadata. */
function fmtRelative(iso) {
  if (!iso) return null
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return null
  const diffMs = Date.now() - d.getTime()
  const mins = Math.round(diffMs / 60000)
  if (mins < 1) return 'just now'
  if (mins < 60) return `${mins}m ago`
  const hours = Math.round(diffMs / 3600000)
  if (hours < 48) return `${hours}h ago`
  const days = Math.round(diffMs / 86400000)
  return `${days}d ago`
}

/** Signed relative time — "in 2h" for future, "3h ago" for past. */
function fmtRelativeSigned(iso) {
  if (!iso) return null
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return null
  const diffMs = d.getTime() - Date.now()
  const future = diffMs >= 0
  const abs = Math.abs(diffMs)
  const mins = Math.round(abs / 60000)
  if (mins < 1) return 'just now'
  const hours = Math.round(abs / 3600000)
  const days = Math.round(abs / 86400000)
  let unit
  if (mins < 60) unit = `${mins}m`
  else if (hours < 48) unit = `${hours}h`
  else unit = `${days}d`
  return future ? `in ${unit}` : `${unit} ago`
}

/** Absolute "Jul 2, 09:00" label — used for schedule/run tooltips. */
function fmtAbs(iso) {
  if (!iso) return null
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return null
  return d.toLocaleString(undefined, {
    month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
  })
}

/**
 * Full human-readable schedule label for the overview.
 * "Manual" when unset; otherwise interval / preset / parsed-cron / raw.
 */
function humanSchedule(schedule) {
  if (!schedule) return 'Manual'
  const trimmed = String(schedule).trim()
  const m = /^interval:(\d+)([smhd])$/.exec(trimmed)
  if (m) {
    const unit = { s: 'second', m: 'minute', h: 'hour', d: 'day' }[m[2]]
    return `Every ${m[1]} ${unit}${m[1] === '1' ? '' : 's'}`
  }
  const preset = SCHEDULE_PRESETS.find(p => p.value === trimmed)
  if (preset) return preset.label
  const parts = trimmed.split(/\s+/)
  if (parts.length === 5) {
    const [min, hour, dom, mon, dow] = parts
    const numeric = (v) => /^\d+$/.test(v)
    if (dom === '*' && mon === '*' && dow === '*' && numeric(min) && numeric(hour)) {
      return `Daily at ${hour.padStart(2, '0')}:${min.padStart(2, '0')}`
    }
    if (hour === '*' && dom === '*' && mon === '*' && dow === '*' && numeric(min)) {
      return min === '0' ? 'Hourly' : `Hourly at :${min.padStart(2, '0')}`
    }
    const DOW = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']
    if (dom === '*' && mon === '*' && numeric(dow) && numeric(min) && numeric(hour)) {
      return `${DOW[Number(dow) % 7]} at ${hour.padStart(2, '0')}:${min.padStart(2, '0')}`
    }
    // Monthly — a specific day-of-month (e.g. "0 8 1 * *" → "Monthly on the 1st at 08:00")
    if (mon === '*' && dow === '*' && numeric(dom) && numeric(min) && numeric(hour)) {
      const n = Number(dom)
      const v = n % 100
      const suffix = (['th', 'st', 'nd', 'rd'][(v - 20) % 10] || ['th', 'st', 'nd', 'rd'][v] || 'th')
      return `Monthly on the ${n}${suffix} at ${hour.padStart(2, '0')}:${min.padStart(2, '0')}`
    }
  }
  return trimmed
}

// ---------------------------------------------------------------------------
// Run state badge
// ---------------------------------------------------------------------------

const RUN_STATE_VARIANT = {
  pending:   'default',
  running:   'warning',
  success:   'success',
  failed:    'danger',
  cancelled: 'default',
}

function RunStateBadge({ state, size = 'sm' }) {
  return (
    <Badge variant={RUN_STATE_VARIANT[state] ?? 'default'} size={size} dot
      className={state === 'running' ? 'animate-pulse' : ''}>
      {state ?? 'pending'}
    </Badge>
  )
}

// ---------------------------------------------------------------------------
// Flow status — the single dot/label shown on rail rows + overview cards.
// Priority: draft → last-run failed → scheduled → paused → manual.
// ---------------------------------------------------------------------------

function flowStatusMeta(flow) {
  if (flow?._isNew) return { dot: 'bg-amber-400', ring: 'ring-amber-400/30', label: 'Unsaved draft' }
  if (flow?.last_run_state === 'failed') return { dot: 'bg-red-500', ring: 'ring-red-500/30', label: 'Last run failed' }
  if (flow?.schedule && flow?.enabled) return { dot: 'bg-emerald-500', ring: 'ring-emerald-500/30', label: 'Scheduled' }
  if (flow?.schedule && !flow?.enabled) return { dot: 'bg-amber-400', ring: 'ring-amber-400/30', label: 'Schedule paused' }
  return { dot: 'bg-muted/40', ring: 'ring-border', label: 'Runs manually' }
}

/** Small status dot for rail rows — colour keyed by flowStatusMeta. */
function FlowStatusDot({ flow, className = '' }) {
  const s = flowStatusMeta(flow)
  return (
    <span
      title={s.label}
      aria-label={s.label}
      className={['inline-block h-2 w-2 rounded-full shrink-0 ring-2', s.dot, s.ring, className].join(' ')}
    />
  )
}

// ---------------------------------------------------------------------------
// FlowListItem — entry in the left rail
// ---------------------------------------------------------------------------

function FlowListItem({ flow, isActive, onClick, onDelete, canWrite, strictEnv }) {
  const [deleting, setDeleting] = useState(false)

  const handleDelete = useCallback(async (e) => {
    e.stopPropagation()
    if (!window.confirm(`Delete flow "${flow.name}"?`)) return
    setDeleting(true)
    const ok = await deleteFlow(flow.id)
    setDeleting(false)
    if (ok) {
      toast.success(`Deleted "${flow.name}".`)
      onDelete(flow.id)
    } else {
      toast.error('Delete failed — check the console.')
    }
  }, [flow, onDelete])

  const scheduleSummary = !flow._isNew ? describeSchedule(flow.schedule) : null
  const lastRunRel = !flow._isNew ? fmtRelative(flow.last_run_at) : null
  const notInEnv = !flow._isNew && strictEnv && Array.isArray(flow.pinned_envs)
    && !flow.pinned_envs.includes(strictEnv)

  return (
    <button
      onClick={() => onClick(flow)}
      className={[
        'w-full text-left px-2.5 py-2.5 rounded-lg transition-all group relative min-h-[44px]',
        'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
        isActive
          ? 'bg-primary/10 border border-primary/20 text-fg'
          : 'hover:bg-surface-2 border border-transparent text-fg/80 hover:text-fg',
      ].join(' ')}
    >
      <div className="flex items-start gap-2.5 min-w-0 pr-6">
        <FlowStatusDot flow={flow} className="mt-[5px]" />
        <div className="min-w-0 flex-1">
          <p className="text-[13px] font-semibold truncate leading-tight">{flow.name}</p>

          {/* Metadata row: schedule + last run, aligned as small labelled chips */}
          {(scheduleSummary || lastRunRel) && (
            <div className="flex flex-wrap items-center gap-x-2.5 gap-y-0.5 mt-1 text-[10px] text-muted">
              {scheduleSummary && (
                <span className="inline-flex items-center gap-1" title={`Schedule: ${scheduleSummary}`}>
                  <CalendarClock size={9} className="shrink-0" />
                  <span className="truncate">{scheduleSummary}</span>
                </span>
              )}
              {lastRunRel && (
                <span className="inline-flex items-center gap-1" title="Last run">
                  <History size={9} className="shrink-0" />
                  {lastRunRel}
                </span>
              )}
            </div>
          )}

          {(flow._isNew || notInEnv) && (
            <div className="flex flex-wrap items-center gap-1 mt-1.5">
              {flow._isNew && <Badge variant="warning" size="sm">Unsaved</Badge>}
              {/* Strict-env visibility: the active env is protected and this flow
                  has no pinned version there (pinned_envs from the list API). */}
              {notInEnv && (
                <Badge
                  variant="danger"
                  size="sm"
                  title={`No version is pinned to ${strictEnv} — promote one to make it visible there.`}
                >
                  not in {strictEnv}
                </Badge>
              )}
            </div>
          )}
        </div>
      </div>

      {/* Delete button (visible on hover, not for drafts; writers only) */}
      {!flow._isNew && canWrite && (
        <button
          onClick={handleDelete}
          disabled={deleting}
          className="absolute right-2 top-1/2 -translate-y-1/2 w-8 h-8 flex items-center justify-center rounded-md text-muted hover:text-danger hover:bg-danger-bg opacity-0 group-hover:opacity-100 transition-all disabled:opacity-40"
          title="Delete flow"
          aria-label={`Delete flow "${flow.name}"`}
        >
          {deleting ? <Loader2 size={11} className="animate-spin" /> : <Trash2 size={11} />}
        </button>
      )}
    </button>
  )
}

// ---------------------------------------------------------------------------
// FlowList — shared content between rail and bottom sheet
// ---------------------------------------------------------------------------

function FlowList({ flows, activeId, loading, onSelect, onNew, onRefresh, onDelete, onItemClick = undefined, showHeader = true, canWrite = true, strictEnv = null }) {
  const [query, setQuery] = useState('')

  const handleSelect = useCallback((flow) => {
    onSelect(flow)
    onItemClick?.()
  }, [onSelect, onItemClick])

  const handleNew = useCallback(() => { onNew(); onItemClick?.() }, [onNew, onItemClick])

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase()
    if (!q) return flows
    return flows.filter(f => (f.name ?? '').toLowerCase().includes(q))
  }, [flows, query])

  return (
    <>
      {/* Header — hidden when the host (desktop aside) already shows a title. */}
      {showHeader && (
        <div className="shrink-0 flex items-center justify-between px-3 py-2.5 border-b border-border">
          <span className="text-xs font-semibold text-muted uppercase tracking-wider">Flows</span>
          <button
            onClick={onRefresh}
            disabled={loading}
            className="h-7 w-7 flex items-center justify-center rounded text-muted hover:text-fg hover:bg-surface-2 disabled:opacity-40 transition-colors"
            title="Refresh flows"
            aria-label="Refresh flows"
          >
            <RefreshCw size={12} className={loading ? 'animate-spin' : ''} />
          </button>
        </div>
      )}

      {/* New button — writers only */}
      {canWrite && (
        <div className="shrink-0 px-2 py-2">
          <button
            onClick={handleNew}
            className="w-full h-11 flex items-center justify-center gap-1.5 text-sm font-medium rounded-lg border border-dashed border-border text-muted hover:text-fg hover:border-border hover:bg-surface-2 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            <Plus size={14} />
            New flow
          </button>
        </div>
      )}

      {/* Search — only worth showing once there's something to filter */}
      {flows.length > 0 && (
        <div className="shrink-0 px-2 pb-2">
          <SearchBar value={query} onChange={setQuery} placeholder="Search flows…" />
        </div>
      )}

      {/* List */}
      <div className="flex-1 overflow-y-auto px-2 pb-2 space-y-0.5">
        {loading && flows.length === 0 && (
          <div className="space-y-1.5 px-1 py-1" aria-label="Loading flows">
            {[0, 1, 2, 3].map(i => (
              <div key={i} className="flex items-center gap-2 px-2 py-2.5 rounded-lg">
                <Skeleton className="w-3.5 h-3.5 rounded" />
                <div className="flex-1 space-y-1.5">
                  <Skeleton className="h-2.5 w-3/4" />
                  <Skeleton className="h-2 w-1/3" />
                </div>
              </div>
            ))}
          </div>
        )}
        {!loading && flows.length === 0 && (
          <EmptyState
            compact
            icon={<GitBranch size={20} />}
            title="No flows yet"
            description="Build a pipeline once, then run it on demand or on a schedule."
            action={canWrite ? (
              <button
                onClick={handleNew}
                className="inline-flex items-center gap-1.5 h-8 px-3 text-xs font-medium rounded-lg bg-primary text-primary-fg hover:opacity-90 transition-opacity focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                <Plus size={13} />
                Create your first flow
              </button>
            ) : undefined}
          />
        )}
        {!loading && flows.length > 0 && filtered.length === 0 && (
          <p className="text-[11px] text-muted text-center py-6">No flows match “{query}”.</p>
        )}
        {filtered.map(f => (
          <FlowListItem
            key={f.id ?? f._localId ?? f.name}
            flow={f}
            isActive={activeId === (f.id ?? f._localId)}
            onClick={handleSelect}
            onDelete={onDelete}
            canWrite={canWrite}
            strictEnv={strictEnv}
          />
        ))}
      </div>
    </>
  )
}

// ---------------------------------------------------------------------------
// MobileFlowsSheet — bottom sheet for flow list on mobile
// ---------------------------------------------------------------------------

function MobileFlowsSheet({ open, onClose, flows, activeId, loading, onSelect, onNew, onRefresh, onDelete, canWrite, strictEnv = null }) {
  // Close on Escape.
  useEffect(() => {
    if (!open) return undefined
    const handler = (e) => { if (e.key === 'Escape') onClose?.() }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [open, onClose])

  return (
    <>
      {/* Backdrop */}
      {open && (
        <div
          className="fixed inset-0 z-40 bg-black/40"
          onClick={onClose}
          aria-hidden="true"
        />
      )}

      {/* Sheet */}
      <div
        className={[
          'fixed bottom-0 left-0 right-0 z-50 flex flex-col',
          'bg-surface border-t border-border rounded-t-2xl',
          'transition-transform duration-300 ease-out',
          'max-h-[75dvh]',
          open ? 'translate-y-0' : 'translate-y-full',
        ].join(' ')}
        aria-label="Flows list"
        aria-modal="true"
        role="dialog"
      >
        {/* Pull handle */}
        <div className="shrink-0 flex justify-center pt-3 pb-1">
          <div className="w-10 h-1 rounded-full bg-border" />
        </div>

        {/* Close */}
        <div className="absolute top-3 right-3">
          <button
            onClick={onClose}
            className="w-9 h-9 flex items-center justify-center rounded-lg text-muted hover:text-fg hover:bg-surface-2 transition-colors"
            aria-label="Close flows list"
          >
            <X size={16} />
          </button>
        </div>

        <FlowList
          flows={flows}
          activeId={activeId}
          loading={loading}
          onSelect={onSelect}
          onNew={onNew}
          onRefresh={onRefresh}
          onDelete={onDelete}
          onItemClick={onClose}
          canWrite={canWrite}
          strictEnv={strictEnv}
        />
      </div>
    </>
  )
}

// ---------------------------------------------------------------------------
// RunsList — tab panel listing past runs of the active flow
// ---------------------------------------------------------------------------

function RunsTab({ flow, currentRunId, onSelectRun }) {
  const [runs, setRuns] = useState([])
  const [loading, setLoading] = useState(false)
  const flowId = flow?.id ?? null

  const load = useCallback(async () => {
    if (!flowId) { setRuns([]); return }
    setLoading(true)
    const data = await listFlowRuns(flowId)
    setRuns(data)
    setLoading(false)
  }, [flowId])

  // Defer the initial load to avoid synchronous setState inside the effect body.
  useEffect(() => {
    const t = setTimeout(load, 0)
    return () => clearTimeout(t)
  }, [load])

  return (
    <div className="flex flex-col h-full">
      {/* Sub-toolbar */}
      <div className="shrink-0 flex items-center justify-between px-4 py-2 border-b border-border bg-surface-2/20">
        <p className="text-xs font-semibold text-muted uppercase tracking-wider">Run history</p>
        <button
          onClick={load}
          disabled={loading}
          title="Refresh runs"
          aria-label="Refresh runs"
          className="h-7 w-7 flex items-center justify-center rounded text-muted hover:text-fg hover:bg-surface-2 disabled:opacity-40 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          <RefreshCw size={11} className={loading ? 'animate-spin' : ''} />
        </button>
      </div>

      <div className="flex-1 overflow-y-auto px-3 sm:px-4 py-3 space-y-2">
        {loading && runs.length === 0 && (
          <div className="space-y-2" aria-label="Loading run history">
            {[0, 1, 2].map(i => (
              <div key={i} className="flex items-center gap-2 px-3 py-3 rounded-lg border border-border">
                <Skeleton className="w-2 h-2 rounded-full" />
                <div className="flex-1 space-y-1.5">
                  <Skeleton className="h-2.5 w-1/2" />
                  <Skeleton className="h-2 w-1/3" />
                </div>
              </div>
            ))}
          </div>
        )}
        {!loading && runs.length === 0 && (
          <EmptyState
            compact
            icon={<Play size={20} />}
            title="No runs yet"
            description="Run this flow from the Builder and its history shows up here."
          />
        )}
        {runs.map(run => {
          const isCurrent = run.id === currentRunId
          return (
            <button
              key={run.id}
              onClick={() => onSelectRun(run.id)}
              className={[
                'w-full text-left px-3 py-3 rounded-lg border transition-colors min-h-[44px]',
                'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
                isCurrent
                  ? 'bg-primary/10 border-primary/20 text-fg'
                  : 'bg-surface border-border hover:bg-surface-2 text-fg/80 hover:text-fg',
              ].join(' ')}
            >
              <div className="flex items-center gap-2.5">
                <RunStateBadge state={run.state} />
                <div className="flex-1 min-w-0">
                  <p className="text-xs font-mono truncate">{run.id.slice(0, 12)}…</p>
                  <p className="text-[10px] text-muted mt-0.5 truncate">
                    {run.trigger}
                    {run.created_at && ` · ${new Date(run.created_at).toLocaleString()}`}
                  </p>
                </div>
                {isCurrent && <Badge variant="primary" size="sm" dot className="shrink-0">live</Badge>}
              </div>
            </button>
          )
        })}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Schedule control — set a flow to run on a cron/interval (toolbar popover)
// ---------------------------------------------------------------------------

const SCHEDULE_PRESETS = [
  { label: 'Every hour', value: 'interval:1h' },
  { label: 'Every 6 hours', value: 'interval:6h' },
  { label: 'Daily · 9am', value: '0 9 * * *' },
  { label: 'Weekly · Mon 9am', value: '0 9 * * 1' },
]

/** Human-readable summary of a schedule string for the toolbar button. */
function describeSchedule(schedule) {
  if (!schedule) return null
  const m = /^interval:(\d+)([smhd])$/.exec(schedule)
  if (m) return `Every ${m[1]}${m[2]}`
  const preset = SCHEDULE_PRESETS.find(p => p.value === schedule)
  if (preset) return preset.label
  return 'Custom'
}

function ScheduleControl({ flow, onSaved }) {
  const [open, setOpen] = useState(false)
  const [enabled, setEnabled] = useState(Boolean(flow?.enabled))
  const [schedule, setSchedule] = useState(flow?.schedule || 'interval:1h')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState(null)

  // Re-sync when the active flow changes.
  useEffect(() => {
    setEnabled(Boolean(flow?.enabled))
    setSchedule(flow?.schedule || 'interval:1h')
    setError(null)
  }, [flow?.id, flow?.schedule, flow?.enabled])

  const isActive = Boolean(flow?.enabled && flow?.schedule)
  const summary = describeSchedule(flow?.schedule)

  async function save() {
    setSaving(true)
    setError(null)
    try {
      const trimmed = (schedule || '').trim()
      if (enabled && !trimmed) {
        setError('Enter an interval or cron expression.')
        setSaving(false)
        return
      }
      const updated = await updateFlow(flow.id, {
        enabled,
        schedule: enabled ? trimmed : null,
      })
      if (!updated) {
        setError('Save failed — check the console.')
        setSaving(false)
        return
      }
      onSaved?.(updated)
      setOpen(false)
    } catch (e) {
      setError(e?.message || 'Save failed.')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="relative shrink-0">
      <button
        onClick={() => setOpen(v => !v)}
        title={isActive ? `Scheduled: ${summary}` : 'Schedule this flow to run automatically'}
        className={[
          'flex items-center gap-1.5 px-2 sm:px-2.5 h-8 text-xs font-medium rounded-lg border transition-colors',
          isActive
            ? 'border-emerald-500/40 bg-emerald-500/10 text-emerald-600 dark:text-emerald-400'
            : 'border-border bg-surface text-fg hover:bg-surface-2',
        ].join(' ')}
      >
        <CalendarClock size={13} className={isActive ? 'text-emerald-500' : ''} />
        <span className="hidden lg:inline">{isActive ? summary : 'Schedule'}</span>
      </button>

      {open && (
        <>
          <div className="fixed inset-0 z-40" onClick={() => setOpen(false)} />
          <div className="absolute right-0 top-9 z-50 w-72 rounded-xl border border-border bg-surface shadow-lg p-3 text-fg">
            <div className="flex items-center justify-between mb-2">
              <p className="text-xs font-semibold">Schedule flow</p>
              <label className="flex items-center gap-1.5 text-xs cursor-pointer">
                <input
                  type="checkbox"
                  checked={enabled}
                  onChange={e => setEnabled(e.target.checked)}
                  className="accent-emerald-500"
                />
                Enabled
              </label>
            </div>
            <div className={enabled ? '' : 'opacity-50 pointer-events-none'}>
              <div className="grid grid-cols-2 gap-1.5 mb-2">
                {SCHEDULE_PRESETS.map(p => (
                  <button
                    key={p.value}
                    onClick={() => setSchedule(p.value)}
                    className={[
                      'px-2 py-1 text-[11px] rounded-lg border transition-colors text-left',
                      schedule === p.value
                        ? 'border-emerald-500/40 bg-emerald-500/10 text-emerald-600 dark:text-emerald-400'
                        : 'border-border bg-surface-2 hover:bg-surface',
                    ].join(' ')}
                  >
                    {p.label}
                  </button>
                ))}
              </div>
              <input
                type="text"
                value={schedule}
                onChange={e => setSchedule(e.target.value)}
                placeholder="interval:30m or cron: 0 9 * * *"
                className="w-full px-2 py-1.5 text-xs font-mono rounded-lg border border-border bg-surface-2 focus:outline-none focus:ring-1 focus:ring-emerald-500/40"
              />
              <p className="text-[10px] text-muted mt-1">
                Interval (e.g. <code>interval:30m</code>, <code>interval:6h</code>) or a 5-field cron expression.
              </p>
            </div>
            {error && <p className="text-[11px] text-danger mt-2">{error}</p>}
            <div className="flex items-center justify-end gap-2 mt-3">
              <button onClick={() => setOpen(false)} className="px-2.5 h-7 text-xs rounded-lg border border-border bg-surface hover:bg-surface-2">
                Cancel
              </button>
              <button onClick={save} disabled={saving}
                className="flex items-center gap-1.5 px-2.5 h-7 text-xs font-medium rounded-lg bg-primary text-primary-fg hover:opacity-90 disabled:opacity-50">
                {saving ? <Loader2 size={12} className="animate-spin" /> : null}
                Save
              </button>
            </div>
          </div>
        </>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// AutoRebuildToggle — set runtime_config.auto_rebuild_downstream on a flow
// ---------------------------------------------------------------------------

/**
 * Inline toggle that enables/disables ``runtime_config.auto_rebuild_downstream``
 * on a saved (non-draft) materialized flow. Saves immediately on toggle.
 *
 * The flag is stored inside the flow's spec.runtime_config dict.  The UI reads
 * the current spec to determine the toggle's initial state and PATCH-saves on
 * change by calling updateFlow with the merged spec.
 *
 * @param {{ flow: object, spec: object, onSaved: (updated: object) => void }} props
 */
function AutoRebuildToggle({ flow, spec, onSaved }) {
  const [saving, setSaving] = useState(false)

  // Read current state from the spec's runtime_config
  const isOn = Boolean(spec?.runtime_config?.auto_rebuild_downstream)

  async function toggle() {
    if (saving || !flow?.id) return
    setSaving(true)
    try {
      const rc = { ...(spec?.runtime_config ?? {}), auto_rebuild_downstream: !isOn }
      const nextSpec = { ...(spec ?? {}), runtime_config: rc }
      const updated = await updateFlow(flow.id, { spec: nextSpec })
      if (updated) onSaved?.(updated)
    } catch {
      // ignore; can surface a toast if needed
    } finally {
      setSaving(false)
    }
  }

  return (
    <button
      onClick={toggle}
      disabled={saving}
      title={isOn ? 'Auto-rebuild downstream enabled — click to disable' : 'Enable auto-rebuild downstream flows when this flow completes'}
      className={[
        'flex items-center gap-1.5 px-2 sm:px-2.5 h-8 text-xs font-medium rounded-lg border transition-colors disabled:opacity-50',
        isOn
          ? 'border-blue-500/40 bg-blue-500/10 text-blue-600 dark:text-blue-400'
          : 'border-border bg-surface text-fg hover:bg-surface-2',
      ].join(' ')}
    >
      {saving ? <Loader2 size={13} className="animate-spin" /> : <RefreshCw size={13} className={isOn ? 'text-blue-500' : ''} />}
      <span className="hidden lg:inline">Auto-rebuild</span>
    </button>
  )
}

// ---------------------------------------------------------------------------
// MoreActionsMenu — overflow "⋯" popover holding secondary builder actions
// ---------------------------------------------------------------------------

/**
 * Overflow menu that collects the secondary builder toolbar actions (Validate,
 * Checkpoint, Version history, Auto-rebuild) so the top bar fits at narrow
 * widths. Mirrors ``ScheduleControl``'s popover pattern: a small icon button
 * opening an ``absolute right-0`` dropdown behind a ``fixed inset-0``
 * click-catcher, closing on Escape / outside-click / selection.
 *
 * Every guard is applied by the caller-passed booleans so this stays identical
 * to the standalone buttons it replaces.
 */
function MoreActionsMenu({
  canWrite,
  canRun,
  activeFlow,
  activeSpec,
  validating,
  triggerValidate,
  triggerCheckpoint,
  setHistoryOpen,
  onSaved,
}) {
  const [open, setOpen] = useState(false)

  // Close on Escape while open.
  useEffect(() => {
    if (!open) return
    const onKey = e => { if (e.key === 'Escape') setOpen(false) }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open])

  const showCheckpoint = canWrite && canRun
  const showHistory = canRun
  const showAutoRebuild = canWrite && activeFlow?.id && !activeFlow?._isNew

  const itemClass =
    'flex w-full items-center gap-2 px-2.5 h-8 text-xs font-medium rounded-lg text-fg hover:bg-surface-2 disabled:opacity-50 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring text-left'

  return (
    <div className="relative shrink-0">
      <button
        onClick={() => setOpen(v => !v)}
        title="More actions"
        aria-label="More actions"
        aria-haspopup="menu"
        aria-expanded={open}
        className="flex items-center justify-center w-8 h-8 rounded-lg border border-border bg-surface text-fg hover:bg-surface-2 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        <MoreHorizontal size={15} />
      </button>

      {open && (
        <>
          <div className="fixed inset-0 z-40" onClick={() => setOpen(false)} />
          <div
            role="menu"
            aria-label="More actions"
            className="absolute right-0 top-9 z-50 w-56 rounded-xl border border-border bg-surface shadow-lg p-1.5 text-fg"
          >
            <button
              role="menuitem"
              onClick={() => { setOpen(false); triggerValidate() }}
              disabled={validating}
              className={itemClass}
            >
              {validating ? <Loader2 size={13} className="animate-spin" /> : <ShieldCheck size={13} />}
              Validate
            </button>
            {showCheckpoint && (
              <button
                role="menuitem"
                onClick={() => { setOpen(false); triggerCheckpoint() }}
                className={itemClass}
              >
                <GitCommitHorizontal size={13} />
                Checkpoint
              </button>
            )}
            {showHistory && (
              <button
                role="menuitem"
                onClick={() => { setOpen(false); setHistoryOpen(true) }}
                className={itemClass}
              >
                <History size={13} />
                Version history
              </button>
            )}
            {showAutoRebuild && (
              <div className="mt-1 pt-1 border-t border-border">
                <AutoRebuildToggle flow={activeFlow} spec={activeSpec} onSaved={onSaved} />
              </div>
            )}
          </div>
        </>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// MiniToggle — enable / disable switch used in the schedules overview
// ---------------------------------------------------------------------------

function MiniToggle({ on, busy, onToggle, label }) {
  return (
    <button
      type="button"
      onClick={onToggle}
      disabled={busy}
      role="switch"
      aria-checked={on}
      aria-label={label}
      title={label}
      className={[
        'relative inline-flex h-5 w-9 shrink-0 items-center rounded-full transition-colors',
        'disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-1',
        on ? 'bg-emerald-500 dark:bg-emerald-500' : 'bg-surface-2 border border-border',
      ].join(' ')}
    >
      <span
        className={[
          'inline-block h-3.5 w-3.5 transform rounded-full bg-white shadow transition-transform',
          on ? 'translate-x-[18px]' : 'translate-x-0.5',
        ].join(' ')}
      />
    </button>
  )
}

// ---------------------------------------------------------------------------
// FlowScheduleRow — one row in the "Schedules & runs" overview
// ---------------------------------------------------------------------------

function FlowScheduleRow({ flow, canWrite, onOpen, onPatched }) {
  const [lastRun, setLastRun] = useState(null)     // newest run {state, ...} | null
  const [runsLoaded, setRunsLoaded] = useState(false)
  const [runRefresh, setRunRefresh] = useState(0)
  const [running, setRunning] = useState(false)
  const [toggling, setToggling] = useState(false)

  const enabled = Boolean(flow.enabled)
  const scheduled = Boolean(flow.schedule)
  const description = flow.spec?.description ?? null

  // Fetch the newest run to show a status chip — only when the flow has ever run.
  useEffect(() => {
    if (!flow.id || !flow.last_run_at) { setRunsLoaded(true); return undefined }
    let alive = true
    setRunsLoaded(false)
    listFlowRuns(flow.id).then(runs => {
      if (!alive) return
      setLastRun(Array.isArray(runs) && runs.length > 0 ? runs[0] : null)
      setRunsLoaded(true)
    })
    return () => { alive = false }
  }, [flow.id, flow.last_run_at, runRefresh])

  const handleRun = useCallback(async (e) => {
    e.stopPropagation()
    setRunning(true)
    const result = await runFlow(flow.id)
    setRunning(false)
    if (result) {
      toast.success(`Started “${flow.name}”.`)
      setRunRefresh(v => v + 1)
    } else {
      toast.error('Run failed to start.')
    }
  }, [flow.id, flow.name])

  const handleToggle = useCallback(async (e) => {
    e.stopPropagation()
    if (!canWrite) return
    setToggling(true)
    const updated = await updateFlow(flow.id, { enabled: !enabled })
    setToggling(false)
    if (updated) {
      onPatched?.(updated)
    } else {
      toast.error('Update failed — check the console.')
    }
  }, [flow.id, enabled, canWrite, onPatched])

  const nextRel = fmtRelativeSigned(flow.next_run_at)
  const lastRel = fmtRelative(flow.last_run_at)

  return (
    <div className="bg-surface rounded-xl border border-border transition-all duration-150 hover:border-primary/30 hover:shadow-nubi-sm">
      <div className="flex flex-col sm:flex-row sm:items-center gap-3 p-4">
        {/* Clickable name + metadata — opens the builder */}
        <button
          type="button"
          onClick={() => onOpen(flow)}
          className="group flex-1 min-w-0 text-left rounded-lg -m-1 p-1 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          <div className="flex flex-wrap items-center gap-2 mb-0.5">
            <FlowStatusDot flow={flow} className="mt-px" />
            <h3 className="font-semibold text-sm text-fg truncate group-hover:text-primary transition-colors">
              {flow.name}
            </h3>
            {scheduled && (
              <Badge variant={enabled ? 'success' : 'default'} size="sm" dot>
                {enabled ? 'Scheduled' : 'Paused'}
              </Badge>
            )}
          </div>

          {description && (
            <p className="text-xs text-muted truncate mb-1.5 ml-[18px]">{description}</p>
          )}

          <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px] text-muted ml-[18px]">
            <span className="inline-flex items-center gap-1" title={flow.schedule || 'No schedule — runs manually'}>
              <Clock size={11} className="shrink-0" />
              {humanSchedule(flow.schedule)}
            </span>

            {scheduled && enabled && flow.next_run_at && (
              <span className="inline-flex items-center gap-1" title={fmtAbs(flow.next_run_at) ?? undefined}>
                <CalendarClock size={11} className="shrink-0" />
                Next {nextRel}
              </span>
            )}

            <span className="inline-flex items-center gap-1.5" title={fmtAbs(flow.last_run_at) ?? undefined}>
              <History size={11} className="shrink-0" />
              {!flow.last_run_at ? (
                <span className="text-muted/70">Never run</span>
              ) : !runsLoaded ? (
                <Skeleton className="h-3.5 w-16 rounded-full" />
              ) : lastRun ? (
                <>
                  <RunStateBadge state={lastRun.state} />
                  {lastRel && <span className="text-muted/70">{lastRel}</span>}
                </>
              ) : (
                <span className="text-muted/70">Ran {lastRel}</span>
              )}
            </span>
          </div>
        </button>

        {/* Actions — not nested in the row button */}
        <div className="flex items-center gap-2 shrink-0 self-start sm:self-center ml-[18px] sm:ml-0">
          {scheduled && (
            <MiniToggle
              on={enabled}
              busy={toggling || !canWrite}
              onToggle={handleToggle}
              label={enabled ? `Disable schedule for “${flow.name}”` : `Enable schedule for “${flow.name}”`}
            />
          )}

          {canWrite && (
            <button
              type="button"
              onClick={handleRun}
              disabled={running}
              title="Run now"
              className="inline-flex items-center gap-1.5 h-8 px-2.5 rounded-lg text-xs font-medium border border-border text-muted hover:text-fg hover:bg-surface-2 disabled:opacity-50 disabled:cursor-not-allowed transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              {running ? <Loader2 size={12} className="animate-spin" /> : <Play size={12} strokeWidth={2.2} />}
              <span className="hidden sm:inline">{running ? 'Running…' : 'Run now'}</span>
            </button>
          )}

          <button
            type="button"
            onClick={() => onOpen(flow)}
            title="Open in builder"
            aria-label={`Open “${flow.name}” in builder`}
            className="inline-flex items-center justify-center h-8 w-8 rounded-lg border border-border text-muted hover:text-fg hover:bg-surface-2 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            <ChevronRight size={15} />
          </button>
        </div>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// FlowsLanding — the /flows landing: flows-first. A welcoming header, a
// prominent "Create a flow" action, and every flow as a card the user can
// open to build. Scheduling is folded INTO each card (schedule + last-run
// status + enable toggle + Run now via FlowScheduleRow) rather than being a
// separate front page.
// ---------------------------------------------------------------------------

function FlowsLanding({ flows, loading, canWrite, onOpen, onNew, onPatched }) {
  const scheduledCount = flows.filter(f => f.schedule && f.enabled).length
  const pausedCount = flows.filter(f => f.schedule && !f.enabled).length

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="px-4 sm:px-6 lg:px-8 py-6 sm:py-8 max-w-3xl w-full mx-auto">
        {/* Header — flows-first, not schedules-first */}
        <header className="mb-6">
          <p className="text-[11px] font-mono uppercase tracking-[0.14em] text-muted mb-1.5">Automation</p>
          <h1 className="font-display font-semibold text-xl sm:text-2xl text-fg">Flows</h1>
          <p className="text-sm text-muted mt-1.5 max-w-lg leading-relaxed">
            Build a pipeline once — chain SQL, Python and exports — then run it on demand or put it on a schedule. Open a flow to build it, or start a new one.
          </p>
        </header>

        {/* Loading */}
        {loading && flows.length === 0 && (
          <div className="space-y-3" aria-label="Loading flows">
            {[0, 1, 2].map(i => (
              <div key={i} className="bg-surface rounded-xl border border-border p-4 flex items-center gap-3">
                <div className="flex-1 space-y-2">
                  <Skeleton className="h-4 w-40" />
                  <Skeleton className="h-3 w-56" />
                </div>
                <Skeleton className="h-8 w-20 rounded-lg" />
              </div>
            ))}
          </div>
        )}

        {/* Empty — strong "create your first flow" centrepiece */}
        {!loading && flows.length === 0 && (
          <div className="flex flex-col items-center justify-center text-center py-16 px-6 rounded-2xl border border-dashed border-border">
            <div
              className="flex items-center justify-center w-14 h-14 rounded-2xl mb-4 shadow-sm"
              style={{ background: 'linear-gradient(135deg, #1b2363, #2456a6, #17b3a3)' }}
            >
              <GitBranch size={26} className="text-white" />
            </div>
            <h2 className="font-display font-semibold text-lg text-fg mb-1">Create your first flow</h2>
            <p className="text-sm text-muted max-w-sm leading-relaxed mb-5">
              A flow chains SQL, Python and exports into one pipeline — reports, syncs and multi-step jobs. Run it whenever you like, or hand it a schedule and let it run itself.
            </p>
            {canWrite && (
              <button
                type="button"
                onClick={onNew}
                className="inline-flex items-center gap-2 px-5 py-2.5 rounded-xl bg-primary text-primary-fg text-sm font-semibold hover:opacity-90 transition-opacity shadow-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                <Plus size={16} strokeWidth={2.4} />
                Create your first flow
              </button>
            )}
          </div>
        )}

        {/* Populated — prominent create action, then the flows themselves */}
        {!loading && flows.length > 0 && (
          <>
            {canWrite && (
              <button
                type="button"
                onClick={onNew}
                className="group w-full flex items-center gap-4 text-left rounded-2xl border border-primary/30 bg-primary/[0.04] p-4 sm:p-5 hover:border-primary/50 hover:bg-primary/[0.07] hover:shadow-nubi-sm transition-all focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                <div
                  className="flex items-center justify-center w-11 h-11 rounded-xl shrink-0 shadow-sm"
                  style={{ background: 'linear-gradient(135deg, #1b2363, #2456a6, #17b3a3)' }}
                >
                  <Plus size={22} className="text-white" strokeWidth={2.4} />
                </div>
                <div className="flex-1 min-w-0">
                  <p className="font-display font-semibold text-sm text-fg">Create a flow</p>
                  <p className="text-xs text-muted mt-0.5">Start from a blank canvas and wire queries, transforms and exports into a pipeline.</p>
                </div>
                <ChevronRight size={18} className="text-muted shrink-0 group-hover:text-primary group-hover:translate-x-0.5 transition-all" />
              </button>
            )}

            {/* Section label + at-a-glance schedule summary (integrated, not the headline) */}
            <div className="flex flex-wrap items-center justify-between gap-2 mt-6 mb-3">
              <p className="text-[11px] font-mono uppercase tracking-[0.14em] text-muted">Your flows</p>
              <div className="flex flex-wrap items-center gap-2">
                <Badge variant="default" size="sm">{flows.length} flow{flows.length === 1 ? '' : 's'}</Badge>
                {scheduledCount > 0 && (
                  <Badge variant="success" size="sm" dot>{scheduledCount} scheduled</Badge>
                )}
                {pausedCount > 0 && (
                  <Badge variant="warning" size="sm" dot>{pausedCount} paused</Badge>
                )}
              </div>
            </div>

            <div className="space-y-2.5">
              {flows.map(flow => (
                <FlowScheduleRow
                  key={flow.id}
                  flow={flow}
                  canWrite={canWrite}
                  onOpen={onOpen}
                  onPatched={onPatched}
                />
              ))}
            </div>
          </>
        )}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// FlowsPage
// ---------------------------------------------------------------------------

export default function FlowsPage() {
  const { id: routeId } = useParams()
  const navigate = useNavigate()
  const { topbarSlot } = useUi()
  const canWrite = useCanWrite()

  // ── Flow list ─────────────────────────────────────────────────────────────
  const [savedFlows, setSavedFlows] = useState([])
  const [localDrafts, setLocalDrafts] = useState([])
  const [loadingFlows, setLoadingFlows] = useState(true)

  // ── Active flow ───────────────────────────────────────────────────────────
  const [activeFlow, setActiveFlow] = useState(null)
  const [activeSpec, setActiveSpec] = useState(EMPTY_SPEC)

  // ── Main-pane view: 'flows' (flows-first landing) | 'builder' | 'runs' ─────
  // 'flows' is the landing — the list of flows + a prominent create action,
  // with scheduling folded into each flow card. It's the default when nothing
  // is open and is reachable any time from the top-bar switcher. 'builder' and
  // 'runs' target the active flow. Deep-linking to /flows/:id opens straight
  // into the builder.
  const [activeTab, setActiveTab] = useState(routeId ? 'builder' : 'flows')

  // ── Active run (for live view) ────────────────────────────────────────────
  const [activeRunId, setActiveRunId] = useState(null)

  // ── Mobile sheet open state ───────────────────────────────────────────────
  const [mobileSheetOpen, setMobileSheetOpen] = useState(false)

  // ── Shared RHS sidebar (mirrors the dashboard editor) ─────────────────────
  // One collapsible right-hand panel switched between Flows / Add task /
  // Inspector via the top toggle buttons. The Add + Inspector panels drive the
  // FlowBuilder via an imperative ref; selection is reported back up.
  const flowBuilderRef = useRef(null)
  const [rightPanel, setRightPanel] = useState('flows')      // 'flows' | 'variables' | 'secrets' | 'add' | 'inspector'
  const [rightCollapsed, setRightCollapsed] = useState(() => {
    try { return localStorage.getItem('nubi:flows:railCollapsed') === '1' } catch { return false }
  })
  const [selectedTask, setSelectedTask] = useState(null)

  // ── Builder actions (live in the app top bar; operate on activeSpec) ──────
  const [validating, setValidating] = useState(false)
  const [saving, setSaving] = useState(false)
  const [running, setRunning] = useState(false)
  const [validationIssues, setValidationIssues] = useState(null)
  const [saveError, setSaveError] = useState(null)
  const [runError, setRunError] = useState(null)
  const [codeOpen, setCodeOpen] = useState(false)
  // Current builder view ('canvas' | 'notebook'), reported up from FlowBuilder
  // so the top-bar switcher reflects + drives it.
  const [flowView, setFlowView] = useState('canvas')
  // Active run environment (dev/prod/custom) — global app state owned by the
  // sidebar environment selector (EnvContext, persisted per project). Runs read
  // the selected env (runEnv) from here; there is no flows-local selector.
  // Drives the env passed to runFlow; backend resolution order is:
  // explicit override → spec.env → 'prod'. projectEnvs is the project's env
  // list from the API (null until loaded / when the API is unavailable).
  const {
    environments: projectEnvs,
    activeEnv: runEnv,
  } = useEnv()
  // Version-history dialog visibility (kind='flow', the active flow).
  const [historyOpen, setHistoryOpen] = useState(false)
  // Read-only version view — full version row (incl. config = the flow spec)
  // loaded via the history dialog's View action. While set, the builder shows
  // the version's spec read-only under a banner; the draft stays untouched.
  const [viewingVersion, setViewingVersion] = useState(null)

  // ── Dirty tracking + autosave ─────────────────────────────────────────────
  // JSON snapshot of the last-saved spec; refreshed on flow select, new draft
  // and successful save. dirty = current spec no longer matches the snapshot.
  const [savedSnapshotJson, setSavedSnapshotJson] = useState(() => JSON.stringify(EMPTY_SPEC))
  // Autosave status: null | 'saving' | 'saved' | 'error' (surfaced subtly).
  const [autosaveStatus, setAutosaveStatus] = useState(null)
  // Monotonic save-request counter: any newer request (manual or auto, or a
  // flow switch) marks older in-flight saves stale so their snapshot/status
  // updates are dropped.
  const saveSeqRef = useRef(0)
  // Serialises all saves so concurrent PUT/POSTs can't land out of order.
  const saveChainRef = useRef(Promise.resolve())
  // Latest performSave closure — lets the debounced autosave timer call the
  // freshest version without putting a render-scoped function in effect deps.
  const performSaveRef = useRef(null)

  const dirty = useMemo(
    () => JSON.stringify(activeSpec ?? null) !== savedSnapshotJson,
    [activeSpec, savedSnapshotJson]
  )

  const setCollapsed = useCallback((v) => {
    setRightCollapsed(v)
    try { localStorage.setItem('nubi:flows:railCollapsed', v ? '1' : '0') } catch { /* ignore */ }
  }, [])

  // Node selected in the canvas → surface the Inspector panel (like the
  // dashboard editor's "select widget → Configure").
  const handleSelectedTaskChange = useCallback((task) => {
    setSelectedTask(task)
    if (task) { setRightPanel('inspector'); setCollapsed(false) }
  }, [setCollapsed])

  // Click a top toggle: collapse if it's already the active+open panel,
  // otherwise switch to it and expand.
  const togglePanel = useCallback((panel) => {
    setRightCollapsed(prevCollapsed => {
      const next = rightPanel === panel && !prevCollapsed
      try { localStorage.setItem('nubi:flows:railCollapsed', next ? '1' : '0') } catch { /* ignore */ }
      return next
    })
    setRightPanel(panel)
  }, [rightPanel])

  // ── Select a flow (declared early; used by the route-param effect below) ──
  const selectFlow = useCallback((flow) => {
    saveSeqRef.current += 1 // invalidate in-flight saves from the previous flow
    setViewingVersion(null)
    setActiveFlow(flow)
    setActiveSpec(flow.spec ?? EMPTY_SPEC)
    setSavedSnapshotJson(JSON.stringify(flow.spec ?? EMPTY_SPEC))
    setAutosaveStatus(null)
    setSaveError(null)
    setActiveTab('builder')
    setActiveRunId(null)
    if (flow.id && !flow._isNew) {
      navigate(`/flows/${flow.id}`, { replace: true })
    }
  }, [navigate, setSaveError, setViewingVersion])

  // List-click selection — guards in-app swaps away from a dirty editor.
  // (The route-param effect below still calls selectFlow directly.)
  const handleSelectFlow = (flow) => {
    if (dirty && !window.confirm(UNSAVED_CONFIRM_MSG)) return
    selectFlow(flow)
  }

  // NOTE: the run env is global (EnvContext) and deliberately NOT re-synced
  // from the active flow's spec.env — the user's selected environment
  // persists across flows/pages and is passed as an explicit override to
  // runFlow (backend resolution: explicit override → spec.env → 'prod').

  // ── Load flows ────────────────────────────────────────────────────────────
  const loadFlows = useCallback(async () => {
    setLoadingFlows(true)
    const data = await listFlows()
    setSavedFlows(data)
    setLoadingFlows(false)
  }, [])

  // Defer to avoid synchronous setState inside the effect body.
  useEffect(() => {
    const t = setTimeout(loadFlows, 0)
    return () => clearTimeout(t)
  }, [loadFlows])

  // ── Route param → auto-select flow ───────────────────────────────────────
  useEffect(() => {
    if (!routeId) return
    // Already active?
    if (activeFlow?.id === routeId) return
    // Try in loaded list first
    const found = savedFlows.find(f => f.id === routeId)
    if (found) {
      // Defer setState call so it isn't synchronous inside the effect body.
      const t = setTimeout(() => selectFlow(found), 0)
      return () => clearTimeout(t)
    } else if (savedFlows.length > 0) {
      // Flow not in list — fetch directly (already async via promise)
      getFlow(routeId).then(f => { if (f) selectFlow(f) })
    }
  }, [routeId, savedFlows, activeFlow, selectFlow])

  // ── New draft ─────────────────────────────────────────────────────────────
  const handleNew = useCallback(() => {
    if (dirty && !window.confirm(UNSAVED_CONFIRM_MSG)) return
    saveSeqRef.current += 1 // invalidate in-flight saves from the previous flow
    setViewingVersion(null)
    const draft = { ...newFlowDraft(), _localId: `draft-${Date.now()}` }
    setLocalDrafts(prev => [draft, ...prev])
    setActiveFlow(draft)
    setActiveSpec({ ...EMPTY_SPEC })
    setSavedSnapshotJson(JSON.stringify(EMPTY_SPEC))
    setAutosaveStatus(null)
    setSaveError(null)
    setActiveTab('builder')
    setActiveRunId(null)
    navigate('/flows', { replace: true })
  }, [navigate, dirty, setSaveError, setViewingVersion])

  // ── After save callback ───────────────────────────────────────────────────
  const handleSaved = useCallback((savedFlow) => {
    // Remove draft if applicable
    setLocalDrafts(prev => prev.filter(d => d._localId !== savedFlow._localId))
    // Upsert in saved list
    setSavedFlows(prev => {
      const idx = prev.findIndex(f => f.id === savedFlow.id)
      if (idx >= 0) {
        const next = [...prev]
        next[idx] = savedFlow
        return next
      }
      return [savedFlow, ...prev]
    })
    setActiveFlow(savedFlow)
    navigate(`/flows/${savedFlow.id}`, { replace: true })
  }, [navigate])

  // ── After run callback (NotebookView triggers its own run via FlowBuilder) ─
  const handleRun = useCallback(({ runId }) => {
    setActiveRunId(runId)
    setActiveTab('runs')
  }, [])

  // ── Top-bar builder actions — operate on the live activeSpec.
  //    Plain functions (rebuilt with the inline toolbar each render); the React
  //    Compiler handles memoization. ──────────────────────────────────────────
  const triggerValidate = async () => {
    setValidating(true)
    setValidationIssues(null)
    const result = await validateFlow(activeSpec)
    setValidationIssues(result?.issues ?? [])
    setValidating(false)
  }

  // ── Unified save — the single implementation behind the top-bar Save, the
  //    notebook toolbar Save (passed down via FlowBuilder `onSave`), and the
  //    debounced autosave. Saves are chained through saveChainRef so writes
  //    can't race/land out of order; saveSeqRef drops stale status updates
  //    (e.g. a queued autosave superseded by a manual save is skipped). ──────
  const performSave = async ({ auto = false } = {}) => {
    const spec = activeSpec
    const specJson = JSON.stringify(spec ?? null)
    const target = activeFlow
    const isUpdate = !!(target && !target._isNew && target.id)
    // Never auto-create: unsaved drafts keep manual Save only.
    if (auto && !isUpdate) return null
    if (auto && specJson === savedSnapshotJson) return null // nothing to save
    const seq = ++saveSeqRef.current

    if (auto) {
      setAutosaveStatus('saving')
    } else {
      setSaving(true)
      setSaveError(null)
    }

    const run = async () => {
      // A newer save request superseded this queued autosave — skip the write.
      if (auto && seq !== saveSeqRef.current) return null
      const saved = isUpdate
        ? await updateFlow(target.id, { name: spec.name, spec })
        : await createFlow(spec.name ?? 'untitled', spec)
      const stale = seq !== saveSeqRef.current
      if (!auto) setSaving(false)
      if (!saved) {
        if (!stale) {
          if (auto) setAutosaveStatus('error')
          else { setSaveError('Save failed — check the console for details.'); toast.error('Save failed.') }
        }
        return null
      }
      if (!stale) {
        setSavedSnapshotJson(specJson)
        setAutosaveStatus(auto ? 'saved' : null)
        handleSaved({ ...saved, _localId: target?._localId })
        if (!auto) toast.success('Flow saved.')
      }
      return saved
    }
    const chained = saveChainRef.current.then(run, run)
    saveChainRef.current = chained
    return chained
  }

  const triggerSave = () => performSave()

  // Keep the autosave timer pointing at the freshest performSave closure.
  useEffect(() => { performSaveRef.current = performSave })

  // ── Autosave: existing flows only, AUTOSAVE_DELAY_MS after the last edit.
  //    Each spec edit re-arms the timer (classic debounce via effect cleanup).
  useEffect(() => {
    if (!dirty || !canWrite) return undefined
    if (!activeFlow?.id || activeFlow._isNew) return undefined
    const t = setTimeout(() => performSaveRef.current?.({ auto: true }), AUTOSAVE_DELAY_MS)
    return () => clearTimeout(t)
  }, [activeSpec, dirty, canWrite, activeFlow])

  // ── beforeunload guard — registered only while dirty, so closing/refreshing
  //    the tab warns about unsaved changes. ──────────────────────────────────
  useEffect(() => {
    if (!dirty) return undefined
    const handler = (e) => {
      e.preventDefault()
      e.returnValue = '' // required by some browsers to show the prompt
    }
    window.addEventListener('beforeunload', handler)
    return () => window.removeEventListener('beforeunload', handler)
  }, [dirty])

  const triggerRun = async () => {
    if (!activeFlow?.id || activeFlow._isNew) {
      setRunError('Save the flow first before running.')
      return
    }
    // Notebook view: delegate to NotebookView's own "Run all" handler.
    if (activeTab === 'builder' && flowView === 'notebook') {
      flowBuilderRef.current?.runAll()
      return
    }
    setRunning(true)
    setRunError(null)
    const result = await runFlow(activeFlow.id, {}, runEnv || undefined)
    setRunning(false)
    if (!result) {
      setRunError('Run failed — check the console for details.')
      toast.error('Run failed to start.')
    } else {
      toast.success('Run started.')
      handleRun({ runId: result.id })
    }
  }

  // ── Checkpoint — snapshot the saved draft spec as a new version ──────────
  const triggerCheckpoint = async () => {
    if (!activeFlow?.id || activeFlow._isNew) {
      setSaveError('Save the flow first before creating a checkpoint.')
      return
    }
    const message = window.prompt('Checkpoint message (optional):', '')
    if (message === null) return // cancelled
    // The backend snapshots the *saved* draft — flush unsaved edits first.
    if (dirty) {
      const saved = await performSave()
      if (!saved) {
        toast.error('Save failed — checkpoint aborted.')
        return
      }
    }
    try {
      const v = await checkpoint('flow', activeFlow.id, { message: message.trim() || undefined })
      toast.success(v?.deduped
        ? `No changes since v${v.version} — the existing version was reused.`
        : `Created version v${v?.version}.`)
    } catch (cause) {
      toast.error(cause?.message || 'Checkpoint failed.')
    }
  }

  // ── After a version restore — reload the flow so the editor shows the
  //    restored draft (selectFlow re-snapshots, clearing dirty state). ───────
  const handleRestored = async () => {
    if (!activeFlow?.id) return
    const fresh = await getFlow(activeFlow.id)
    if (fresh) {
      selectFlow(fresh) // also clears any read-only version view
      setSavedFlows(prev => prev.map(f => (f.id === fresh.id ? fresh : f)))
    }
  }

  // ── Restore the version currently being VIEWED (banner action) ────────────
  const restoreViewedVersion = async () => {
    if (!activeFlow?.id || !viewingVersion) return
    if (!window.confirm(`Restore version v${viewingVersion.version} into the current draft? Unsaved draft changes are overwritten.`)) return
    try {
      await restoreVersion('flow', activeFlow.id, viewingVersion.version)
      await handleRestored()
      toast.success(`Restored v${viewingVersion.version}.`)
    } catch (cause) {
      toast.error(cause?.message || 'Restore failed.')
    }
  }

  // ── Delete callback ───────────────────────────────────────────────────────
  const handleDelete = useCallback((flowId) => {
    setSavedFlows(prev => prev.filter(f => f.id !== flowId))
    setActiveFlow(prev => {
      if (prev?.id === flowId) {
        saveSeqRef.current += 1 // invalidate in-flight saves for the deleted flow
        setActiveSpec(EMPTY_SPEC)
        setSavedSnapshotJson(JSON.stringify(EMPTY_SPEC))
        setAutosaveStatus(null)
        navigate('/flows', { replace: true })
        return null
      }
      return prev
    })
  }, [navigate])

  // ── Patch a flow in place (overview enable/disable toggle) ────────────────
  const handleFlowPatched = useCallback((updated) => {
    if (!updated?.id) return
    setSavedFlows(prev => prev.map(f => (f.id === updated.id ? { ...f, ...updated } : f)))
    setActiveFlow(prev => (prev?.id === updated.id ? { ...prev, ...updated } : prev))
  }, [])

  // ── All flows (drafts first, then saved) ──────────────────────────────────
  const allFlows = [...localDrafts, ...savedFlows]
  const activeId = activeFlow?.id ?? activeFlow?._localId

  // Strict-env badges: when the ACTIVE env is protected, rail items whose
  // pinned_envs (from the list API) lack it get a 'not in <env>' chip.
  const strictEnv = useMemo(() => {
    const row = Array.isArray(projectEnvs) ? projectEnvs.find(e => e.key === runEnv) : null
    return row?.protected ? runEnv : null
  }, [projectEnvs, runEnv])

  // ── Which main-pane view is showing ───────────────────────────────────────
  // The flows-first landing renders whenever it's selected OR there's no flow
  // open yet (e.g. a deep-link that's still loading). The Builder / Runs
  // top-bar segments only apply once a flow is open.
  const hasFlow = !!activeFlow
  const showLanding = activeTab === 'flows' || !hasFlow
  const builderBar = activeTab === 'builder' && hasFlow
  // The persistent right rail is always the flow list on the landing; only the
  // builder/runs workspace exposes the Add / Inspector / Variables panels.
  // On the landing the rail defaults to the flow list, but 'secrets' is allowed
  // to override so the top-bar Secrets button can open it there too.
  const effectivePanel = showLanding && rightPanel !== 'secrets' ? 'flows' : rightPanel

  const canRun = !!activeFlow?.id && !activeFlow?._isNew

  // ── Toolbar portaled into the single app top bar (mirrors the dashboard
  //    editor): Builder/Runs switcher · view switcher · flow name · notebook
  //    add-cell buttons · Validate/Save/Run/Code · RHS panel toggles.
  //    One bar, not a stacked second one — the notebook's old in-page toolbar
  //    was merged in here (its controls drive NotebookView via the ref). ─────
  const flowsToolbar = (
    <div className="flex items-center gap-1.5 w-full min-w-0 overflow-x-auto">
      {/* Mobile: open the flow list */}
      <button
        onClick={() => setMobileSheetOpen(true)}
        className="md:hidden flex items-center justify-center w-8 h-8 rounded-lg border border-border bg-surface text-muted hover:text-fg hover:bg-surface-2 transition-colors shrink-0 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        aria-label="Open flow list" title="Flow list"
      >
        <List size={16} />
      </button>

      {/* Main-pane switcher: Flows | Builder | Runs.
          'Flows' is the landing (list + create, scheduling folded into each
          flow) and is always available; Builder/Runs light up once a flow is
          open. Scheduling is reachable per-flow — never a headline mode here. */}
      <div role="tablist" aria-label="Flows view" className="flex h-8 rounded-lg border border-border overflow-hidden shrink-0">
        {[
          { id: 'flows',   label: 'Flows',   Icon: GitBranch, enabled: true },
          { id: 'builder', label: 'Builder', Icon: Share2,    enabled: hasFlow },
          { id: 'runs',    label: 'Runs',    Icon: History,   enabled: hasFlow },
        ].map((tab, i) => {
          const selected = tab.id === 'flows' ? showLanding : (!showLanding && activeTab === tab.id)
          return (
            <button
              key={tab.id}
              role="tab"
              aria-selected={selected}
              disabled={!tab.enabled}
              onClick={() => tab.enabled && setActiveTab(tab.id)}
              title={tab.enabled ? tab.label : 'Open a flow first'}
              className={[
                'flex items-center gap-1.5 px-2.5 sm:px-3 text-xs font-medium transition-colors whitespace-nowrap',
                'focus-visible:outline-none focus-visible:relative focus-visible:z-10 focus-visible:ring-2 focus-visible:ring-ring',
                'disabled:opacity-40 disabled:cursor-not-allowed',
                i > 0 ? 'border-l border-border' : '',
                selected ? 'bg-primary text-primary-fg' : 'bg-surface text-muted hover:text-fg hover:bg-surface-2',
              ].join(' ')}
            >
              <tab.Icon size={13} className="shrink-0" />
              <span>{tab.label}</span>
              {tab.id === 'runs' && activeRunId && (
                <span className="inline-flex w-1.5 h-1.5 rounded-full bg-warning animate-pulse" />
              )}
            </button>
          )
        })}
      </div>

      {/* Canvas / Notebook view switcher (icons) — builder tab only */}
      {builderBar && (
        <div className="flex h-8 rounded-lg border border-border overflow-hidden shrink-0">
          {[
            { id: 'canvas', Icon: Share2, title: 'Canvas / DAG view' },
            { id: 'notebook', Icon: LayoutList, title: 'Notebook / cell view' },
            { id: 'code', Icon: FileCode2, title: 'Code / Files view' },
          ].map((v, i) => (
            <button
              key={v.id}
              onClick={() => flowBuilderRef.current?.setView(v.id)}
              title={v.title}
              aria-label={v.title}
              aria-pressed={flowView === v.id}
              className={[
                'flex items-center justify-center w-8 transition-colors',
                'focus-visible:outline-none focus-visible:relative focus-visible:z-10 focus-visible:ring-2 focus-visible:ring-ring',
                i > 0 ? 'border-l border-border' : '',
                flowView === v.id ? 'bg-primary text-primary-fg' : 'bg-surface text-muted hover:text-fg hover:bg-surface-2',
              ].join(' ')}
            >
              <v.Icon size={14} />
            </button>
          ))}
        </div>
      )}

      {/* Flow / notebook name (was the notebook toolbar's name input) */}
      {builderBar && (
        <input
          type="text"
          value={activeSpec?.name ?? ''}
          onChange={e => setActiveSpec({ ...activeSpec, name: e.target.value })}
          placeholder={flowView === 'notebook' ? 'Notebook name…' : 'Flow name…'}
          disabled={!canWrite}
          aria-label="Flow name"
          className={[
            'h-8 px-2.5 text-xs font-medium border border-border rounded-lg bg-surface text-fg placeholder:text-muted/50 focus:outline-none focus:ring-2 focus:ring-ring/60 shrink-0 disabled:opacity-50',
            // Notebook view packs more controls into the bar — keep the input compact.
            flowView === 'notebook' ? 'w-24 sm:w-28 2xl:w-40' : 'w-24 sm:w-40',
          ].join(' ')}
        />
      )}

      {/* Notebook-only: add-cell buttons (was the notebook toolbar) */}
      {builderBar && flowView === 'notebook' && (
        <div className="flex items-center gap-1 shrink-0">
          <button
            onClick={() => flowBuilderRef.current?.addCell('sql')}
            title="Add SQL cell"
            className="flex items-center gap-1.5 px-2 sm:px-2.5 h-8 text-xs font-medium rounded-lg border border-border bg-surface text-fg hover:bg-surface-2 transition-colors shrink-0 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            <Database size={12} className="text-blue-500" />
            <span className="hidden sm:inline">+ SQL</span>
          </button>
          <button
            onClick={() => flowBuilderRef.current?.addCell('python')}
            title="Add Python cell"
            className="flex items-center gap-1.5 px-2 sm:px-2.5 h-8 text-xs font-medium rounded-lg border border-border bg-surface text-fg hover:bg-surface-2 transition-colors shrink-0 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            <Code2 size={12} className="text-violet-500" />
            <span className="hidden sm:inline">+ Python</span>
          </button>
          <button
            onClick={() => flowBuilderRef.current?.addCell('markdown')}
            title="Add Note (markdown) cell"
            className="flex items-center gap-1.5 px-2 sm:px-2.5 h-8 text-xs font-medium rounded-lg border border-border bg-surface text-fg hover:bg-surface-2 transition-colors shrink-0 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            <FileText size={12} className="text-slate-400" />
            <span className="hidden sm:inline">+ Note</span>
          </button>
        </div>
      )}

      <div className="flex items-center gap-1 ml-auto shrink-0">
        {/* Flows landing: refresh · secrets · new flow · rail toggle */}
        {showLanding && (
          <>
            <button
              onClick={loadFlows}
              disabled={loadingFlows}
              title="Refresh flows"
              aria-label="Refresh flows"
              className="flex items-center justify-center w-8 h-8 rounded-lg border border-border bg-surface text-muted hover:text-fg hover:bg-surface-2 disabled:opacity-40 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              <RefreshCw size={14} className={loadingFlows ? 'animate-spin' : ''} />
            </button>
            <button
              onClick={() => togglePanel('secrets')}
              title="Secrets"
              aria-pressed={rightPanel === 'secrets' && !rightCollapsed}
              className={[
                'flex items-center gap-1.5 px-2 sm:px-2.5 h-8 text-xs font-medium rounded-lg border transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
                rightPanel === 'secrets' && !rightCollapsed
                  ? 'bg-primary text-primary-fg border-primary'
                  : 'border-border bg-surface text-fg hover:bg-surface-2',
              ].join(' ')}
            >
              <KeyRound size={13} />
              <span className="hidden sm:inline">Secrets</span>
            </button>
            {canWrite && (
              <button
                onClick={handleNew}
                title="New flow"
                className="inline-flex items-center gap-1.5 h-8 px-2.5 rounded-lg bg-primary text-primary-fg text-xs font-medium hover:opacity-90 transition-opacity focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                <Plus size={13} strokeWidth={2.5} />
                <span className="hidden sm:inline">New flow</span>
              </button>
            )}
            <button
              onClick={() => setCollapsed(!rightCollapsed)}
              title={rightCollapsed ? 'Show flow list' : 'Hide flow list'}
              aria-label={rightCollapsed ? 'Show flow list' : 'Hide flow list'}
              aria-pressed={!rightCollapsed}
              className={[
                'hidden md:flex w-8 h-8 items-center justify-center rounded-lg border transition-colors ml-0.5',
                'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
                !rightCollapsed ? 'bg-primary text-primary-fg border-primary' : 'bg-surface text-muted border-border hover:text-fg hover:bg-surface-2',
              ].join(' ')}
            >
              <List size={15} />
            </button>
          </>
        )}
        {/* Builder-only actions */}
        {builderBar && (
          <>
            {/* Environment is selected globally in the sidebar (SidebarEnvSelector,
                shared via EnvContext). Runs target the EnvContext-selected env
                (runEnv) — no second selector is rendered here. */}
            {/* Unsaved / autosave status — sits next to the Save button */}
            <SaveStatusBadge dirty={dirty} saving={saving} autosaveStatus={autosaveStatus} className="hidden sm:flex px-1" />
            {canWrite && (
              <button onClick={triggerSave} disabled={saving} title="Save flow"
                className="flex items-center gap-1.5 px-2 sm:px-2.5 h-8 text-xs font-medium rounded-lg border border-border bg-surface text-fg hover:bg-surface-2 disabled:opacity-50 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
                {saving ? <Loader2 size={13} className="animate-spin" /> : <Save size={13} />}
                <span className="hidden lg:inline">Save</span>
              </button>
            )}
            {/* Secondary actions (Validate · Checkpoint · Version history ·
                Auto-rebuild) live in an overflow menu so the bar fits at
                narrow widths — each keeps its original guard. */}
            <MoreActionsMenu
              canWrite={canWrite}
              canRun={canRun}
              activeFlow={activeFlow}
              activeSpec={activeSpec}
              validating={validating}
              triggerValidate={triggerValidate}
              triggerCheckpoint={triggerCheckpoint}
              setHistoryOpen={setHistoryOpen}
              onSaved={handleSaved}
            />
            {canWrite && activeFlow?.id && !activeFlow?._isNew && (
              <ScheduleControl flow={activeFlow} onSaved={handleSaved} />
            )}
            {canWrite && (
              <button onClick={triggerRun} disabled={running || !canRun} title={!canRun ? 'Save the flow first' : 'Run flow'}
                className="flex items-center gap-1.5 px-2.5 h-8 text-xs font-medium rounded-lg bg-primary text-primary-fg hover:opacity-90 disabled:opacity-50 disabled:cursor-not-allowed transition-all focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
                {running ? <Loader2 size={13} className="animate-spin" /> : <Play size={13} />}
                <span className="hidden lg:inline">Run</span>
              </button>
            )}
            {/* Editing the flow as code now lives in the dedicated "Code" view
                (the third view-switcher option) — the old canvas-side code
                panel is subsumed by it, so no separate toggle here. */}
          </>
        )}

        {/* RHS panel toggles (desktop) — shown once a flow is open. Click the
            active panel to fully collapse. Add task / Inspector are canvas tools;
            in the notebook/code view only Flows + Variables are offered. */}
        {!showLanding && (
        <div className="hidden md:flex items-center gap-0.5 pl-1.5 ml-0.5 border-l border-border">
          {[
            { id: 'flows',     Icon: List,              title: 'Flows' },
            { id: 'variables', Icon: Variable,          title: 'Variables' },
            { id: 'secrets',   Icon: KeyRound,          title: 'Secrets' },
            { id: 'add',       Icon: Plus,              title: 'Add task' },
            { id: 'inspector', Icon: SlidersHorizontal, title: 'Inspector' },
          ].filter(p => !(builderBar && (flowView === 'notebook' || flowView === 'code') && p.id !== 'flows' && p.id !== 'variables' && p.id !== 'secrets')).map(p => {
            const active = rightPanel === p.id && !rightCollapsed
            return (
              <button key={p.id} onClick={() => togglePanel(p.id)} title={p.title} aria-label={p.title} aria-pressed={active}
                className={[
                  'w-8 h-8 flex items-center justify-center rounded-lg border transition-colors',
                  'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
                  active ? 'bg-primary text-primary-fg border-primary' : 'bg-surface text-muted border-border hover:text-fg hover:bg-surface-2',
                ].join(' ')}>
                <p.Icon size={15} />
              </button>
            )
          })}
        </div>
        )}
      </div>
    </div>
  )

  return (
    // AppShell's <main> is flex-1 overflow-y-auto inside a min-h-0 flex container,
    // so it has a definite height. We use h-full + overflow-hidden to fill it
    // without causing page scroll — critical for the ReactFlow canvas height.
    <div className="flex flex-col h-full overflow-hidden">
      {/* Outer wrapper that fills available space */}
      <div className="flex flex-1 min-h-0 overflow-hidden bg-bg">

        {/* ── Main area ──────────────────────────────────────────────────── */}
        <div className="flex-1 flex flex-col min-w-0 overflow-hidden">

          {/* One unified toolbar in the app top bar, whichever view is showing. */}
          {topbarSlot && createPortal(flowsToolbar, topbarSlot)}

          {showLanding ? (
            /* ── Flows-first landing (list + create; scheduling per-flow) ─── */
            <FlowsLanding
              flows={savedFlows}
              loading={loadingFlows}
              canWrite={canWrite}
              onOpen={handleSelectFlow}
              onNew={handleNew}
              onPatched={handleFlowPatched}
            />
          ) : (
            /* ── Flow workspace (Builder / Runs) ─────────────────────────── */
            <>
              {/* Action banners (validate / save / run) */}
              {validationIssues && (
                <div className={[
                  'shrink-0 flex items-start gap-2 px-4 py-2.5 text-xs border-b',
                  validationIssues.length === 0
                    ? 'bg-success-bg border-success/20 text-success'
                    : 'bg-danger-bg border-danger/20 text-danger',
                ].join(' ')}>
                  {validationIssues.length === 0
                    ? <CheckCircle2 size={14} className="shrink-0 mt-0.5" />
                    : <AlertCircle size={14} className="shrink-0 mt-0.5" />}
                  <div className="flex-1">
                    {validationIssues.length === 0
                      ? 'Flow spec is valid.'
                      : <><strong>Validation issues:</strong><ul className="mt-1 space-y-0.5 list-disc list-inside">{validationIssues.map((i, idx) => <li key={idx}>{i}</li>)}</ul></>}
                  </div>
                  <button onClick={() => setValidationIssues(null)} aria-label="Dismiss validation result" className="shrink-0 opacity-60 hover:opacity-100 transition-opacity"><X size={13} /></button>
                </div>
              )}
              {saveError && (
                <div className="shrink-0 flex items-center gap-2 px-4 py-2 bg-danger-bg border-b border-danger/20 text-xs text-danger">
                  <AlertCircle size={13} />
                  <span className="flex-1 min-w-0">{saveError}</span>
                  <button onClick={() => setSaveError(null)} aria-label="Dismiss save error" className="ml-auto opacity-60 hover:opacity-100 shrink-0"><X size={12} /></button>
                </div>
              )}
              {runError && (
                <div className="shrink-0 flex items-center gap-2 px-4 py-2 bg-danger-bg border-b border-danger/20 text-xs text-danger">
                  <AlertCircle size={13} />
                  <span className="flex-1 min-w-0">{runError}</span>
                  <button onClick={() => setRunError(null)} aria-label="Dismiss run error" className="ml-auto opacity-60 hover:opacity-100 shrink-0"><X size={12} /></button>
                </div>
              )}

              {/* Read-only version-view banner — builder shows the version's
                  spec; the draft is untouched until Restore. */}
              {viewingVersion && activeTab === 'builder' && (
                <div className="shrink-0 flex items-center gap-2 px-4 py-2 bg-info-bg border-b border-info/20 text-xs text-info">
                  <History size={13} className="shrink-0" />
                  <span className="flex-1 min-w-0 truncate">
                    Viewing <span className="font-mono font-semibold">v{viewingVersion.version}</span> (read-only)
                    {viewingVersion.message ? <span className="text-muted"> — {viewingVersion.message}</span> : null}
                  </span>
                  {canWrite && (
                    <button
                      onClick={restoreViewedVersion}
                      className="shrink-0 px-2 h-6 rounded-md border border-info/30 font-medium hover:bg-info/10 transition-colors"
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

              {/* Tab content */}
              <div className="flex-1 min-h-0 overflow-hidden">
                {activeTab === 'builder' && (
                  <FlowBuilder
                    key={`${activeFlow?.id ?? activeFlow?._localId}${viewingVersion ? `:view-v${viewingVersion.version}` : ''}`}
                    ref={flowBuilderRef}
                    flow={activeFlow?._isNew ? null : activeFlow}
                    spec={viewingVersion ? (viewingVersion.config ?? {}) : activeSpec}
                    onSpecChange={viewingVersion ? () => {} : setActiveSpec}
                    onRun={handleRun}
                    env={runEnv}
                    onSelectedTaskChange={handleSelectedTaskChange}
                    onViewModeChange={setFlowView}
                    codeOpen={codeOpen}
                    onCodeClose={() => setCodeOpen(false)}
                  />
                )}

                {activeTab === 'runs' && (
                  activeRunId ? (
                    <FlowRunView
                      key={activeRunId}
                      runId={activeRunId}
                      spec={activeSpec}
                      onClose={() => setActiveRunId(null)}
                    />
                  ) : (
                    <RunsTab
                      flow={activeFlow}
                      currentRunId={activeRunId}
                      onSelectRun={(rid) => setActiveRunId(rid)}
                    />
                  )
                )}
              </div>
            </>
          )}
        </div>

        {/* ── Persistent RHS rail (desktop) — the flow list is the primary
            navigation, always here for quick switching (like the Queries /
            Dashboards rails). In the builder/runs workspace the same rail also
            hosts the Add task / Inspector / Variables panels via the top-bar
            toggles. Collapsible; re-open from the top bar. */}
        {!rightCollapsed && (
          <aside className="hidden md:flex shrink-0 w-64 lg:w-72 flex-col border-l border-border bg-surface">
            <div className="shrink-0 flex items-center justify-between px-3 h-9 border-b border-border">
              <span className="text-[11px] font-semibold uppercase tracking-wider text-muted truncate">
                {effectivePanel === 'flows' ? 'Flows' : effectivePanel === 'variables' ? 'Variables' : effectivePanel === 'secrets' ? 'Secrets' : effectivePanel === 'add' ? 'Add task' : (
                  <>
                    Inspector
                    {selectedTask?.key && (
                      <span className="ml-1.5 font-mono normal-case tracking-normal text-fg/80">
                        · {selectedTask.key}
                      </span>
                    )}
                  </>
                )}
              </span>
              <div className="flex items-center gap-0.5">
                {effectivePanel === 'flows' && (
                  <button
                    onClick={loadFlows}
                    disabled={loadingFlows}
                    title="Refresh flows"
                    className="flex items-center justify-center w-7 h-7 rounded-lg text-muted hover:text-fg hover:bg-surface-2 disabled:opacity-40 transition-colors"
                  >
                    <RefreshCw size={13} className={loadingFlows ? 'animate-spin' : ''} />
                  </button>
                )}
                <button
                  onClick={() => setCollapsed(true)}
                  title="Collapse panel"
                  aria-label="Collapse side panel"
                  className="flex items-center justify-center w-7 h-7 rounded-lg text-muted hover:text-fg hover:bg-surface-2 transition-colors"
                >
                  <PanelRightClose size={16} />
                </button>
              </div>
            </div>
            <div className="flex-1 min-h-0 overflow-y-auto flex flex-col">
              {effectivePanel === 'flows' && (
                <FlowList
                  flows={allFlows}
                  activeId={activeId}
                  loading={loadingFlows}
                  onSelect={handleSelectFlow}
                  onNew={handleNew}
                  onRefresh={loadFlows}
                  onDelete={handleDelete}
                  showHeader={false}
                  canWrite={canWrite}
                  strictEnv={strictEnv}
                />
              )}
              {effectivePanel === 'variables' && (
                <div className="p-3">
                  <VariablesPanel readOnly={!canWrite} />
                </div>
              )}
              {effectivePanel === 'secrets' && (
                <div className="p-3">
                  <SecretsPanel readOnly={!canWrite} />
                </div>
              )}
              {effectivePanel === 'add' && (
                <AddTaskPanel
                  disabled={!canWrite || !activeFlow || activeTab !== 'builder'}
                  onAdd={(kind, config, cellType) => flowBuilderRef.current?.addNode(kind, config, cellType)}
                />
              )}
              {effectivePanel === 'inspector' && (
                selectedTask ? (
                  <NodeInspector
                    task={selectedTask}
                    onChange={(t) => flowBuilderRef.current?.updateSelectedTask(t)}
                    onClose={() => flowBuilderRef.current?.clearSelection()}
                    readOnly={!canWrite}
                    showHeader={false}
                  />
                ) : (
                  <p className="text-xs text-muted/70 m-3 rounded-lg border border-dashed border-border bg-surface-2/30 px-3 py-4 text-center">
                    Select a task on the canvas to configure it.
                  </p>
                )
              )}
            </div>
          </aside>
        )}
      </div>

      {/* ── Mobile bottom sheet: flow list ─────────────────────────────────── */}
      <MobileFlowsSheet
        open={mobileSheetOpen}
        onClose={() => setMobileSheetOpen(false)}
        flows={allFlows}
        activeId={activeId}
        loading={loadingFlows}
        onSelect={handleSelectFlow}
        onNew={handleNew}
        onRefresh={loadFlows}
        onDelete={handleDelete}
        canWrite={canWrite}
        strictEnv={strictEnv}
      />

      {/* ── Version history (kind='flow', the active saved flow) ───────────── */}
      {canRun && (
        <VersionHistoryDialog
          kind="flow"
          resourceId={activeFlow.id}
          resourceName={activeFlow.name ?? activeSpec?.name ?? ''}
          open={historyOpen}
          onClose={() => setHistoryOpen(false)}
          onRestored={handleRestored}
          onView={(v) => { setViewingVersion(v); setActiveTab('builder') }}
          environments={projectEnvs ?? undefined}
        />
      )}
    </div>
  )
}
