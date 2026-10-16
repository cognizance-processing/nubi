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
import Tag from '../../components/ui/Tag.jsx'
import Button from '../../components/ui/Button.jsx'
import Modal from '../../components/ui/Modal.jsx'
import Input from '../../components/ui/Input.jsx'
import SegmentedControl from '../../components/ui/SegmentedControl.jsx'
import EmptyState from '../../components/ui/EmptyState.jsx'
import PageShell, { SearchBar } from '../../components/app/PageShell.jsx'
import { EmptyFlows, EmptySearch } from '../../components/illustrations'
import { ListSkeleton } from './appShared.jsx'
import '../../components/app/appPatterns.css'
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
// Run state tag
// ---------------------------------------------------------------------------

const RUN_STATE_VARIANT = {
  pending:   'neutral',
  running:   'warning',
  success:   'success',
  failed:    'danger',
  cancelled: 'neutral',
}

function RunStateBadge({ state, size = 'sm' as 'sm' | 'md' }) {
  return (
    <Tag variant={RUN_STATE_VARIANT[state] ?? 'neutral'} dot size={size}>
      {state ?? 'pending'}
    </Tag>
  )
}

// ---------------------------------------------------------------------------
// Flow status — the single dot/label shown on rail rows + overview rows.
// Priority: draft → last-run failed → scheduled → paused → manual.
// ---------------------------------------------------------------------------

function flowStatusMeta(flow) {
  if (flow?._isNew) return { tone: 'warning', label: 'Unsaved draft' }
  if (flow?.last_run_state === 'failed') return { tone: 'danger', label: 'Last run failed' }
  if (flow?.schedule && flow?.enabled) return { tone: 'success', label: 'Scheduled' }
  if (flow?.schedule && !flow?.enabled) return { tone: 'warning', label: 'Schedule paused' }
  return { tone: 'muted', label: 'Runs manually' }
}

/** Small square status dot for rows, keyed by flowStatusMeta. */
function FlowStatusDot({ flow, className = '' }) {
  const s = flowStatusMeta(flow)
  return (
    <span
      title={s.label}
      role="img"
      aria-label={s.label}
      className={`nubi-dot nubi-dot-${s.tone} ${className}`}
    />
  )
}

// ---------------------------------------------------------------------------
// FlowListItem — entry in the right rail
// ---------------------------------------------------------------------------

function FlowListItem({ flow, isActive, onClick, onDelete, canWrite, strictEnv }) {
  const [deleting, setDeleting] = useState(false)
  const [confirming, setConfirming] = useState(false)

  const handleDelete = useCallback(async () => {
    setDeleting(true)
    const ok = await deleteFlow(flow.id)
    setDeleting(false)
    if (ok) {
      setConfirming(false)
      toast.success(`Deleted "${flow.name}".`)
      onDelete(flow.id)
    } else {
      toast.error('Couldn’t delete this flow. Try again.')
    }
  }, [flow, onDelete])

  const scheduleSummary = !flow._isNew ? describeSchedule(flow.schedule) : null
  const lastRunRel = !flow._isNew ? fmtRelative(flow.last_run_at) : null
  const notInEnv = !flow._isNew && strictEnv && Array.isArray(flow.pinned_envs)
    && !flow.pinned_envs.includes(strictEnv)

  return (
    <div className="relative group">
      <button
        type="button"
        onClick={() => onClick(flow)}
        aria-current={isActive ? 'true' : undefined}
        className={`qr-row nubi-focus-inset ${isActive ? 'is-active' : ''} ${!flow._isNew && canWrite ? 'pr-10' : ''}`}
      >
        <FlowStatusDot flow={flow} className="mt-[5px] shrink-0" />
        <div className="min-w-0 flex-1">
          <p className="qr-name">{flow.name}</p>

          {(scheduleSummary || lastRunRel) && (
            <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-0.5 ap-meta">
              {scheduleSummary && (
                <span className="inline-flex items-center gap-1" title={`Schedule: ${scheduleSummary}`}>
                  <CalendarClock size={11} className="shrink-0" aria-hidden="true" />
                  <span className="truncate">{scheduleSummary}</span>
                </span>
              )}
              {lastRunRel && (
                <span className="inline-flex items-center gap-1" title="Last run">
                  <History size={11} className="shrink-0" aria-hidden="true" />
                  {lastRunRel}
                </span>
              )}
            </div>
          )}

          {(flow._isNew || notInEnv) && (
            <div className="ap-tags mt-1.5">
              {flow._isNew && <Tag variant="warning" dot>Unsaved</Tag>}
              {/* Strict-env visibility: the active env is protected and this flow
                  has no pinned version there (pinned_envs from the list API). */}
              {notInEnv && (
                <Tag
                  variant="warning"
                  dot
                  caps={false}
                  title={`No version is pinned to ${strictEnv}. Promote one to make it visible there.`}
                >
                  not in {strictEnv}
                </Tag>
              )}
            </div>
          )}
        </div>
      </button>

      {/* Delete (revealed on hover / focus; not for drafts; writers only) */}
      {!flow._isNew && canWrite && (
        <Button
          variant="ghost"
          size="icon-sm"
          onClick={() => setConfirming(true)}
          className="absolute right-1.5 top-2 opacity-0 group-hover:opacity-100 focus-visible:opacity-100 [@media(hover:none)]:opacity-100"
          title="Delete flow"
          aria-label={`Delete flow "${flow.name}"`}
        >
          <Trash2 size={14} aria-hidden="true" />
        </Button>
      )}

      {confirming && (
        <Modal open onClose={deleting ? undefined : () => setConfirming(false)} title="Delete this flow?" size="sm">
          <Modal.Body>
            <p className="m-0 text-sm text-muted leading-relaxed">
              <b className="font-semibold text-fg">{flow.name}</b> and its run history will be deleted.
              Anything that triggers it on a schedule stops. This cannot be undone.
            </p>
          </Modal.Body>
          <Modal.Footer>
            <Button variant="secondary" onClick={() => setConfirming(false)} disabled={deleting}>Cancel</Button>
            <Button variant="danger" onClick={handleDelete} loading={deleting}>Delete flow</Button>
          </Modal.Footer>
        </Modal>
      )}
    </div>
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
        <div className="shrink-0 flex items-center justify-between pl-3 pr-1.5 h-10 border-b border-border">
          <span className="t-label">Flows</span>
          <Button variant="ghost" size="icon-sm" onClick={onRefresh} disabled={loading} title="Refresh flows" aria-label="Refresh flows">
            <RefreshCw size={14} className={loading ? 'animate-spin' : ''} aria-hidden="true" />
          </Button>
        </div>
      )}

      {/* New button — writers only */}
      {canWrite && (
        <div className="shrink-0 px-3 pt-3 pb-2">
          <Button onClick={handleNew} size="sm" className="w-full">
            <Plus size={14} aria-hidden="true" />
            New flow
          </Button>
        </div>
      )}

      {/* Search — only worth showing once there's something to filter */}
      {flows.length > 0 && (
        <div className="shrink-0 px-3 pb-2">
          <SearchBar value={query} onChange={setQuery} placeholder="Search flows" />
        </div>
      )}

      {/* List */}
      <div className="flex-1 overflow-y-auto pb-2">
        {loading && flows.length === 0 && <ListSkeleton rows={4} tile={false} />}
        {!loading && flows.length === 0 && (
          <EmptyState
            compact
            art={<EmptyFlows />}
            title="No flows yet"
            description="Chain SQL, Python and exports once, then run it on demand or on a schedule."
            action={canWrite ? (
              <Button size="sm" onClick={handleNew}>
                <Plus size={14} aria-hidden="true" />
                New flow
              </Button>
            ) : undefined}
          />
        )}
        {!loading && flows.length > 0 && filtered.length === 0 && (
          <EmptyState
            compact
            kind="none-match"
            art={<EmptySearch />}
            title={`No flow matches “${query}”`}
            action={<Button size="sm" variant="secondary" onClick={() => setQuery('')}>Clear search</Button>}
          />
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
        <div className="ap-backdrop" onClick={onClose} aria-hidden="true" />
      )}

      {/* Sheet */}
      <div
        className="fixed bottom-0 left-0 right-0 flex flex-col bg-surface max-h-[75dvh] border-t-2 border-ink"
        style={{
          zIndex: 'var(--z-modal)' as any,
          transform: open ? 'translateY(0)' : 'translateY(100%)',
          visibility: open ? 'visible' : 'hidden',
          transition: 'transform var(--dur-3) var(--ease-out), visibility 0s linear ' + (open ? '0s' : 'var(--dur-3)'),
          boxShadow: 'var(--shadow-pop)',
        }}
        aria-label="Flows list"
        aria-modal="true"
        aria-hidden={!open}
        role="dialog"
      >
        <div className="shrink-0 flex items-center justify-between pl-4 pr-2 h-12 border-b border-border">
          <span className="t-label">Flows</span>
          <Button variant="ghost" size="icon" onClick={onClose} aria-label="Close flows list">
            <X size={16} aria-hidden="true" />
          </Button>
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
          showHeader={false}
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
      <div className="shrink-0 flex items-center justify-between pl-4 pr-2 h-10 border-b border-border">
        <p className="t-label m-0">Run history</p>
        <Button variant="ghost" size="icon-sm" onClick={load} disabled={loading} title="Refresh runs" aria-label="Refresh runs">
          <RefreshCw size={14} className={loading ? 'animate-spin' : ''} aria-hidden="true" />
        </Button>
      </div>

      <div className="flex-1 overflow-y-auto">
        {loading && runs.length === 0 && <ListSkeleton rows={3} tile={false} />}
        {!loading && runs.length === 0 && (
          <EmptyState
            compact
            art={<EmptyFlows />}
            title="No runs yet"
            description="Run this flow from the Builder and each run is listed here with its state and trigger."
          />
        )}
        {runs.map(run => {
          const isCurrent = run.id === currentRunId
          return (
            <button
              type="button"
              key={run.id}
              onClick={() => onSelectRun(run.id)}
              aria-current={isCurrent ? 'true' : undefined}
              className={`qr-row nubi-focus-inset items-center ${isCurrent ? 'is-active' : ''}`}
            >
              <RunStateBadge state={run.state} />
              <div className="min-w-0 flex-1">
                <p className="m-0 ap-mono truncate text-[13px]">{run.id.slice(0, 12)}</p>
                <p className="m-0 mt-0.5 ap-meta truncate">
                  {run.trigger}
                  {run.created_at && ` · ${new Date(run.created_at).toLocaleString()}`}
                </p>
              </div>
              {isCurrent && <Tag variant="accent" dot className="shrink-0">live</Tag>}
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
  const [error, setError] = useState<string | null>(null)

  // Re-sync when the active flow changes.
  useEffect(() => {
    setEnabled(Boolean(flow?.enabled))
    setSchedule(flow?.schedule || 'interval:1h')
    setError(null)
  }, [flow?.id, flow?.schedule, flow?.enabled])

  useEffect(() => {
    if (!open) return
    const onKey = (e) => { if (e.key === 'Escape') setOpen(false) }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open])

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
        setError('Couldn’t save the schedule. Try again.')
        setSaving(false)
        return
      }
      onSaved?.(updated)
      setOpen(false)
    } catch (e) {
      setError(e?.message || 'Couldn’t save the schedule.')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="relative shrink-0">
      <Button
        variant={isActive ? 'outline' : 'secondary'}
        size="sm"
        onClick={() => setOpen(v => !v)}
        aria-expanded={open}
        aria-haspopup="dialog"
        title={isActive ? `Scheduled: ${summary}` : 'Schedule this flow to run automatically'}
        aria-label={isActive ? `Schedule: ${summary}` : 'Schedule'}
      >
        {isActive ? <span className="nubi-dot nubi-dot-success" aria-hidden="true" /> : <CalendarClock size={14} aria-hidden="true" />}
        <span className="hidden lg:inline">{isActive ? summary : 'Schedule'}</span>
      </Button>

      {open && (
        <>
          <div className="fixed inset-0 z-40" onClick={() => setOpen(false)} aria-hidden="true" />
          <div role="dialog" aria-label="Schedule flow" className="nubi-dropdown right-0 top-[calc(100%+6px)] z-50 w-80 p-3 text-fg">
            <div className="flex items-center justify-between mb-3">
              <p className="m-0 text-[13px] font-semibold">Schedule flow</p>
              <label className="flex cursor-pointer items-center gap-2 text-xs">
                <Switch on={enabled} onToggle={() => setEnabled(v => !v)} label={enabled ? 'Disable schedule' : 'Enable schedule'} />
                Enabled
              </label>
            </div>
            <div className={enabled ? '' : 'opacity-50 pointer-events-none'}>
              <div className="grid grid-cols-2 gap-1.5 mb-2">
                {SCHEDULE_PRESETS.map(p => (
                  <button
                    type="button"
                    key={p.value}
                    onClick={() => setSchedule(p.value)}
                    aria-pressed={schedule === p.value}
                    className="px-2 py-1.5 text-left text-xs transition-colors"
                    style={{
                      borderRadius: 2,
                      border: `1px solid ${schedule === p.value ? 'var(--accent)' : 'var(--border)'}`,
                      background: schedule === p.value ? 'var(--accent-soft)' : 'var(--surface)',
                      color: 'var(--text)',
                    }}
                  >
                    {p.label}
                  </button>
                ))}
              </div>
              <input
                type="text"
                value={schedule}
                onChange={e => setSchedule(e.target.value)}
                placeholder="interval:30m or 0 9 * * *"
                aria-label="Schedule expression"
                className="nubi-input nubi-input-sm font-mono"
              />
              <p className="nubi-hint mt-1.5">
                Interval (<code className="font-mono">interval:30m</code>, <code className="font-mono">interval:6h</code>) or a 5-field cron expression.
              </p>
            </div>
            {error && <p className="nubi-field-error mt-2" role="alert">{error}</p>}
            <div className="mt-3 flex items-center justify-end gap-2">
              <Button variant="ghost" size="sm" onClick={() => setOpen(false)}>Cancel</Button>
              <Button size="sm" onClick={save} loading={saving}>Save schedule</Button>
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
      type="button"
      role="menuitemcheckbox"
      aria-checked={isOn}
      onClick={toggle}
      disabled={saving}
      title={isOn ? 'Auto-rebuild downstream is on. Click to turn it off.' : 'Rebuild downstream flows automatically when this flow completes'}
      className="nubi-dropdown-item"
    >
      {saving ? <Loader2 size={14} className="animate-spin" aria-hidden="true" /> : <RefreshCw size={14} className="text-muted" aria-hidden="true" />}
      <span className="flex-1 text-left">Auto-rebuild downstream</span>
      <span className="nubi-dot" style={{ background: isOn ? 'var(--success)' : 'var(--border-control)' }} aria-hidden="true" />
    </button>
  )
}

// ---------------------------------------------------------------------------
// MoreActionsMenu — overflow "⋯" popover holding secondary builder actions
// ---------------------------------------------------------------------------

/**
 * Overflow menu that collects the secondary builder toolbar actions (Validate,
 * Checkpoint, Version history, Auto-rebuild) so the top bar fits at narrow
 * widths. A small icon button opening a dropdown behind a click-catcher,
 * closing on Escape / outside-click / selection.
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

  return (
    <div className="relative shrink-0">
      <Button
        variant="secondary"
        size="icon-sm"
        onClick={() => setOpen(v => !v)}
        title="More actions"
        aria-label="More actions"
        aria-haspopup="menu"
        aria-expanded={open}
      >
        <MoreHorizontal size={15} aria-hidden="true" />
      </Button>

      {open && (
        <>
          <div className="fixed inset-0 z-40" onClick={() => setOpen(false)} aria-hidden="true" />
          <div role="menu" aria-label="More actions" className="nubi-dropdown right-0 top-[calc(100%+6px)] z-50 w-60">
            <button
              type="button"
              role="menuitem"
              onClick={() => { setOpen(false); triggerValidate() }}
              disabled={validating}
              className="nubi-dropdown-item"
            >
              {validating ? <Loader2 size={14} className="animate-spin" aria-hidden="true" /> : <ShieldCheck size={14} className="text-muted" aria-hidden="true" />}
              Validate
            </button>
            {showCheckpoint && (
              <button type="button" role="menuitem" onClick={() => { setOpen(false); triggerCheckpoint() }} className="nubi-dropdown-item">
                <GitCommitHorizontal size={14} className="text-muted" aria-hidden="true" />
                Checkpoint
              </button>
            )}
            {showHistory && (
              <button type="button" role="menuitem" onClick={() => { setOpen(false); setHistoryOpen(true) }} className="nubi-dropdown-item">
                <History size={14} className="text-muted" aria-hidden="true" />
                Version history
              </button>
            )}
            {showAutoRebuild && (
              <>
                <div className="nubi-dropdown-divider" role="separator" />
                <AutoRebuildToggle flow={activeFlow} spec={activeSpec} onSaved={onSaved} />
              </>
            )}
          </div>
        </>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Switch — enable / disable switch (square, ink when on)
// ---------------------------------------------------------------------------

function Switch({ on, busy = false, onToggle, label }) {
  return (
    <button
      type="button"
      onClick={onToggle}
      disabled={busy}
      role="switch"
      aria-checked={on}
      aria-label={label}
      title={label}
      className="ap-switch disabled:opacity-50"
    >
      <span className="ap-switch-knob" />
    </button>
  )
}

// ---------------------------------------------------------------------------
// FlowScheduleRow — one row in the Flows overview
// ---------------------------------------------------------------------------

function FlowScheduleRow({ flow, canWrite, onOpen, onPatched }) {
  const [lastRun, setLastRun] = useState<any>(null)     // newest run {state, ...} | null
  const [runsLoaded, setRunsLoaded] = useState(false)
  const [runRefresh, setRunRefresh] = useState(0)
  const [running, setRunning] = useState(false)
  const [toggling, setToggling] = useState(false)

  const enabled = Boolean(flow.enabled)
  const scheduled = Boolean(flow.schedule)
  const description = flow.spec?.description ?? null

  // Fetch the newest run to show a status tag — only when the flow has ever run.
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
      toast.error('The run didn’t start. Try again.')
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
      toast.error('Couldn’t update the schedule. Try again.')
    }
  }, [flow.id, enabled, canWrite, onPatched])

  const nextRel = fmtRelativeSigned(flow.next_run_at)
  const lastRel = fmtRelative(flow.last_run_at)

  return (
    <li className="list-none">
      <div className="ap-row">
        {/* Clickable name + metadata — opens the builder */}
        <button
          type="button"
          onClick={() => onOpen(flow)}
          className="ap-row-main group text-left nubi-focus-inset"
          style={{ background: 'none', border: 0, padding: 0 }}
        >
          <div className="ap-row-title">
            <FlowStatusDot flow={flow} />
            <span className="ap-trunc group-hover:underline underline-offset-4">{flow.name}</span>
            {scheduled && (
              <Tag variant={enabled ? 'success' : 'neutral'} dot>
                {enabled ? 'Scheduled' : 'Paused'}
              </Tag>
            )}
          </div>

          {description && <div className="ap-row-sub">{description}</div>}

          <div className="mt-1 flex flex-wrap items-center gap-x-4 gap-y-1 ap-meta">
            <span className="inline-flex items-center gap-1" title={flow.schedule || 'No schedule. Runs manually.'}>
              <Clock size={12} className="shrink-0" aria-hidden="true" />
              {humanSchedule(flow.schedule)}
            </span>

            {scheduled && enabled && flow.next_run_at && (
              <span className="inline-flex items-center gap-1" title={fmtAbs(flow.next_run_at) ?? undefined}>
                <CalendarClock size={12} className="shrink-0" aria-hidden="true" />
                Next {nextRel}
              </span>
            )}

            <span className="inline-flex items-center gap-1.5" title={fmtAbs(flow.last_run_at) ?? undefined}>
              <History size={12} className="shrink-0" aria-hidden="true" />
              {!flow.last_run_at ? (
                <span>Never run</span>
              ) : !runsLoaded ? (
                <Skeleton className="h-4 w-16" />
              ) : lastRun ? (
                <>
                  <RunStateBadge state={lastRun.state} />
                  {lastRel && <span>{lastRel}</span>}
                </>
              ) : (
                <span>Ran {lastRel}</span>
              )}
            </span>
          </div>
        </button>

        {/* Actions — not nested in the row button */}
        <div className="ap-actions">
          {scheduled && (
            <Switch
              on={enabled}
              busy={toggling || !canWrite}
              onToggle={handleToggle}
              label={enabled ? `Disable schedule for “${flow.name}”` : `Enable schedule for “${flow.name}”`}
            />
          )}

          {canWrite && (
            <Button variant="ghost" size="sm" onClick={handleRun} loading={running} title="Run now">
              {!running && <Play size={14} aria-hidden="true" />}
              <span className="hidden sm:inline">{running ? 'Running' : 'Run now'}</span>
            </Button>
          )}

          <Button variant="ghost" size="icon-sm" onClick={() => onOpen(flow)} title="Open in builder" aria-label={`Open “${flow.name}” in builder`}>
            <ChevronRight size={16} aria-hidden="true" />
          </Button>
        </div>
      </div>
    </li>
  )
}

// ---------------------------------------------------------------------------
// FlowsLanding — the /flows landing: PageShell header, every flow as a ruled
// row. Scheduling is folded INTO each row (schedule + last-run status + enable
// switch + Run now via FlowScheduleRow) rather than a separate front page.
// ---------------------------------------------------------------------------

function FlowsLanding({ flows, loading, canWrite, onOpen, onNew, onPatched, onRefresh, secretsOpen, onToggleSecrets }) {
  const scheduledCount = flows.filter(f => f.schedule && f.enabled).length
  const pausedCount = flows.filter(f => f.schedule && !f.enabled).length
  const eyebrow = loading || flows.length === 0
    ? 'Build'
    : `Build · ${flows.length} ${flows.length === 1 ? 'flow' : 'flows'}`

  return (
    <div className="flex-1 overflow-y-auto">
      <PageShell
        eyebrow={eyebrow}
        title="Flows"
        description="Chain SQL, Python and exports into one pipeline, then run it on demand or on a schedule."
        actions={
          <>
            <Button variant="ghost" size="icon-sm" onClick={onRefresh} disabled={loading} aria-label="Refresh flows" title="Refresh">
              <RefreshCw size={14} className={loading ? 'animate-spin' : ''} aria-hidden="true" />
            </Button>
            <Button variant="secondary" onClick={onToggleSecrets} aria-pressed={secretsOpen}>
              <KeyRound size={14} aria-hidden="true" /> Secrets
            </Button>
            {canWrite && (
              <Button onClick={onNew}>
                <Plus size={14} aria-hidden="true" /> New flow
              </Button>
            )}
          </>
        }
      >
        {/* Loading: rows that match the real ones */}
        {loading && flows.length === 0 && <ListSkeleton rows={3} tile={false} />}

        {/* Empty */}
        {!loading && flows.length === 0 && (
          <EmptyState
            art={<EmptyFlows />}
            title="No flows yet"
            description={canWrite
              ? 'A flow chains SQL, Python and exports into one job: a report, a sync or a multi-step refresh. Run it by hand or give it a schedule.'
              : 'Flows are built by editors. Ask one to set up a pipeline for you.'}
            action={canWrite ? <Button onClick={onNew}><Plus size={14} aria-hidden="true" /> New flow</Button> : undefined}
          />
        )}

        {/* Populated */}
        {!loading && flows.length > 0 && (
          <>
            <div className="ap-filterbar">
              <span className="ap-count">{flows.length} flow{flows.length === 1 ? '' : 's'}</span>
              {scheduledCount > 0 && <Tag variant="success" dot>{scheduledCount} scheduled</Tag>}
              {pausedCount > 0 && <Tag variant="warning" dot>{pausedCount} paused</Tag>}
            </div>
            <ul className="ap-list m-0 p-0" aria-label="Flows">
              {flows.map(flow => (
                <FlowScheduleRow
                  key={flow.id}
                  flow={flow}
                  canWrite={canWrite}
                  onOpen={onOpen}
                  onPatched={onPatched}
                />
              ))}
            </ul>
          </>
        )}
      </PageShell>
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
    <div className="flex items-center gap-2 w-full min-w-0 overflow-x-auto">
      {/* Mobile: open the flow list */}
      <Button
        variant="secondary"
        size="icon-sm"
        onClick={() => setMobileSheetOpen(true)}
        className="md:hidden shrink-0"
        aria-label="Open flow list" title="Flow list"
      >
        <List size={16} aria-hidden="true" />
      </Button>

      {/* Main-pane switcher: Flows | Builder | Runs.
          'Flows' is the landing (list + create, scheduling folded into each
          flow) and is always available; Builder/Runs light up once a flow is
          open. */}
      <div className="shrink-0" role="presentation">
        <SegmentedControl
          size="sm"
          value={showLanding ? 'flows' : activeTab}
          onChange={(id) => { if (id === 'flows' || hasFlow) setActiveTab(id) }}
          options={[
            { value: 'flows',   label: <span className="inline-flex items-center gap-1.5" title="All flows"><GitBranch size={13} aria-hidden="true" /> Flows</span> },
            { value: 'builder', label: <span className="inline-flex items-center gap-1.5" title={hasFlow ? 'Builder' : 'Open a flow first'}><Share2 size={13} aria-hidden="true" /> Builder</span>, disabled: !hasFlow },
            { value: 'runs',    label: (
              <span className="inline-flex items-center gap-1.5" title={hasFlow ? 'Runs' : 'Open a flow first'}>
                <History size={13} aria-hidden="true" /> Runs
                {activeRunId && <span className="nubi-dot nubi-dot-warning" aria-label="A run is in progress" />}
              </span>
            ), disabled: !hasFlow },
          ]}
        />
      </div>

      {/* Canvas / Notebook view switcher (icons) — builder tab only */}
      {builderBar && (
        <div className="shrink-0">
          <SegmentedControl
            size="sm"
            value={flowView}
            onChange={(id) => flowBuilderRef.current?.setView(id)}
            options={[
              { value: 'canvas', label: <Share2 size={14} aria-hidden="true" />, ariaLabel: 'Canvas / DAG view' },
              { value: 'notebook', label: <LayoutList size={14} aria-hidden="true" />, ariaLabel: 'Notebook / cell view' },
              { value: 'code', label: <FileCode2 size={14} aria-hidden="true" />, ariaLabel: 'Code / Files view' },
            ]}
          />
        </div>
      )}

      {/* Flow / notebook name (was the notebook toolbar's name input) */}
      {builderBar && (
        <input
          type="text"
          value={activeSpec?.name ?? ''}
          onChange={e => setActiveSpec({ ...activeSpec, name: e.target.value })}
          placeholder={flowView === 'notebook' ? 'Notebook name' : 'Flow name'}
          disabled={!canWrite}
          aria-label="Flow name"
          className={[
            'nubi-input nubi-input-sm shrink-0 font-semibold',
            flowView === 'notebook' ? 'w-28 2xl:w-40' : 'w-32 sm:w-44',
          ].join(' ')}
        />
      )}

      {/* Notebook-only: add-cell buttons (was the notebook toolbar) */}
      {builderBar && flowView === 'notebook' && (
        <div className="flex shrink-0 items-center gap-1">
          <Button variant="ghost" size="xs" onClick={() => flowBuilderRef.current?.addCell('sql')} title="Add SQL cell" aria-label="Add SQL cell">
            <Database size={13} aria-hidden="true" />
            <span className="hidden sm:inline">+ SQL</span>
          </Button>
          <Button variant="ghost" size="xs" onClick={() => flowBuilderRef.current?.addCell('python')} title="Add Python cell" aria-label="Add Python cell">
            <Code2 size={13} aria-hidden="true" />
            <span className="hidden sm:inline">+ Python</span>
          </Button>
          <Button variant="ghost" size="xs" onClick={() => flowBuilderRef.current?.addCell('markdown')} title="Add Note (markdown) cell" aria-label="Add note cell">
            <FileText size={13} aria-hidden="true" />
            <span className="hidden sm:inline">+ Note</span>
          </Button>
        </div>
      )}

      <div className="flex items-center gap-1.5 ml-auto shrink-0">
        {/* Builder-only actions */}
        {builderBar && (
          <>
            {/* Environment is selected globally in the sidebar (SidebarEnvSelector,
                shared via EnvContext). Runs target the EnvContext-selected env
                (runEnv) — no second selector is rendered here. */}
            {/* Unsaved / autosave status — sits next to the Save button */}
            <SaveStatusBadge dirty={dirty} saving={saving} autosaveStatus={autosaveStatus} className="hidden sm:flex px-1" />
            {canWrite && (
              <Button variant="secondary" size="sm" onClick={triggerSave} loading={saving} title="Save flow" aria-label="Save flow">
                {!saving && <Save size={14} aria-hidden="true" />}
                <span className="hidden lg:inline">Save</span>
              </Button>
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
              <Button size="sm" onClick={triggerRun} disabled={!canRun} loading={running} title={!canRun ? 'Save the flow first' : 'Run flow'} aria-label="Run flow">
                {!running && <Play size={14} aria-hidden="true" />}
                <span className="hidden lg:inline">Run</span>
              </Button>
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
        <div className="hidden md:flex items-center gap-1 pl-2 ml-1 border-l border-frame-line">
          {[
            { id: 'flows',     Icon: List,              title: 'Flows' },
            { id: 'variables', Icon: Variable,          title: 'Variables' },
            { id: 'secrets',   Icon: KeyRound,          title: 'Secrets' },
            { id: 'add',       Icon: Plus,              title: 'Add task' },
            { id: 'inspector', Icon: SlidersHorizontal, title: 'Inspector' },
          ].filter(p => !(builderBar && (flowView === 'notebook' || flowView === 'code') && p.id !== 'flows' && p.id !== 'variables' && p.id !== 'secrets')).map(p => {
            const active = rightPanel === p.id && !rightCollapsed
            return (
              <Button key={p.id} variant={active ? 'primary' : 'secondary'} size="icon-sm" onClick={() => togglePanel(p.id)} title={p.title} aria-label={p.title} aria-pressed={active}>
                <p.Icon size={15} aria-hidden="true" />
              </Button>
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
      <div className="flex flex-1 min-h-0 overflow-hidden bg-surface">

        {/* ── Main area ──────────────────────────────────────────────────── */}
        <div className="flex-1 flex flex-col min-w-0 overflow-hidden">

          {/* One unified toolbar in the app top bar, whichever view is showing. */}
          {topbarSlot && hasFlow && createPortal(flowsToolbar, topbarSlot)}

          {showLanding ? (
            /* ── Flows-first landing (list + create; scheduling per-flow) ─── */
            <FlowsLanding
              flows={savedFlows}
              loading={loadingFlows}
              canWrite={canWrite}
              onOpen={handleSelectFlow}
              onNew={handleNew}
              onPatched={handleFlowPatched}
              onRefresh={loadFlows}
              secretsOpen={rightPanel === 'secrets' && !rightCollapsed}
              onToggleSecrets={() => togglePanel('secrets')}
            />
          ) : (
            /* ── Flow workspace (Builder / Runs) ─────────────────────────── */
            <>
              {/* Action banners (validate / save / run): a 2px rule says ok / failed */}
              {validationIssues && (
                <div
                  role={validationIssues.length === 0 ? 'status' : 'alert'}
                  className={`nubi-notice shrink-0 mx-4 mt-3 text-xs ${validationIssues.length === 0 ? 'nubi-notice-success' : 'nubi-notice-error'}`}
                >
                  <div className="flex-1">
                    {validationIssues.length === 0
                      ? 'Flow spec is valid.'
                      : <><strong>Validation issues</strong><ul className="mt-1 space-y-0.5 list-disc list-inside">{validationIssues.map((i, idx) => <li key={idx}>{i}</li>)}</ul></>}
                  </div>
                  <button type="button" onClick={() => setValidationIssues(null)} aria-label="Dismiss validation result" className="shrink-0 opacity-70 hover:opacity-100"><X size={14} aria-hidden="true" /></button>
                </div>
              )}
              {saveError && (
                <div role="alert" className="nubi-notice nubi-notice-error shrink-0 mx-4 mt-3 text-xs">
                  <span className="flex-1 min-w-0"><b>Couldn&rsquo;t save.</b> {saveError}</span>
                  <Button size="xs" variant="secondary" onClick={triggerSave}>Retry</Button>
                  <button type="button" onClick={() => setSaveError(null)} aria-label="Dismiss save error" className="shrink-0 opacity-70 hover:opacity-100"><X size={14} aria-hidden="true" /></button>
                </div>
              )}
              {runError && (
                <div role="alert" className="nubi-notice nubi-notice-error shrink-0 mx-4 mt-3 text-xs">
                  <span className="flex-1 min-w-0"><b>Run failed.</b> {runError}</span>
                  <button type="button" onClick={() => setRunError(null)} aria-label="Dismiss run error" className="shrink-0 opacity-70 hover:opacity-100"><X size={14} aria-hidden="true" /></button>
                </div>
              )}

              {/* Read-only version-view banner — builder shows the version's
                  spec; the draft is untouched until Restore. */}
              {viewingVersion && activeTab === 'builder' && (
                <div className="nubi-notice nubi-notice-info shrink-0 mx-4 mt-3 text-xs" role="status">
                  <History size={14} className="shrink-0" aria-hidden="true" />
                  <span className="flex-1 min-w-0 truncate">
                    Viewing <span className="font-mono font-semibold">v{viewingVersion.version}</span> (read-only)
                    {viewingVersion.message ? <span className="text-muted"> · {viewingVersion.message}</span> : null}
                  </span>
                  {canWrite && <Button size="xs" variant="secondary" onClick={restoreViewedVersion}>Restore</Button>}
                  <Button size="xs" variant="secondary" onClick={() => setViewingVersion(null)}>Back to draft</Button>
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
        {!rightCollapsed && (!showLanding || rightPanel === 'secrets') && (
          <aside className="hidden md:flex shrink-0 w-64 lg:w-80 flex-col border-l border-frame-line bg-surface">
            <div className="shrink-0 flex items-center justify-between pl-3 pr-1.5 h-10 border-b border-border" style={{ borderTop: '2px solid var(--ink)' }}>
              <span className="t-label truncate">
                {effectivePanel === 'flows' ? 'Flows' : effectivePanel === 'variables' ? 'Variables' : effectivePanel === 'secrets' ? 'Secrets' : effectivePanel === 'add' ? 'Add task' : (
                  <>
                    Inspector
                    {selectedTask?.key && (
                      <span className="ml-1.5 font-mono normal-case tracking-normal text-fg">
                        · {selectedTask.key}
                      </span>
                    )}
                  </>
                )}
              </span>
              <div className="flex items-center gap-0.5">
                {effectivePanel === 'flows' && (
                  <Button variant="ghost" size="icon-sm" onClick={loadFlows} disabled={loadingFlows} title="Refresh flows" aria-label="Refresh flows">
                    <RefreshCw size={14} className={loadingFlows ? 'animate-spin' : ''} aria-hidden="true" />
                  </Button>
                )}
                <Button variant="ghost" size="icon-sm" onClick={() => setCollapsed(true)} title="Collapse panel" aria-label="Collapse side panel">
                  <PanelRightClose size={16} aria-hidden="true" />
                </Button>
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
                  <p className="m-3 border border-dashed border-frame-line px-3 py-4 text-center text-xs text-muted" style={{ borderRadius: 2 }}>
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
