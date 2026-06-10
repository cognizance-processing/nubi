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
  ChevronDown,
  SlidersHorizontal,
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
} from 'lucide-react'

import { useUi } from '../../contexts/UiContext.jsx'
import { useCanWrite } from '../../contexts/OrgContext.jsx'
import { useEnv, envDotClass } from '../../contexts/EnvContext.jsx'
import { checkpoint, restoreVersion } from '../../lib/versions.js'
import VersionHistoryDialog from '../../components/app/VersionHistoryDialog.jsx'
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

// ---------------------------------------------------------------------------
// Run state badge
// ---------------------------------------------------------------------------

function RunStateDot({ state }) {
  const map = {
    pending:   'bg-slate-400',
    running:   'bg-amber-400 animate-pulse',
    success:   'bg-green-500',
    failed:    'bg-red-500',
    cancelled: 'bg-slate-400',
  }
  return <span className={['w-2 h-2 rounded-full shrink-0', map[state] ?? 'bg-slate-400'].join(' ')} />
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
    await deleteFlow(flow.id)
    onDelete(flow.id)
  }, [flow, onDelete])

  return (
    <button
      onClick={() => onClick(flow)}
      className={[
        'w-full text-left px-3 py-3 rounded-lg transition-all group relative min-h-[44px]',
        isActive
          ? 'bg-primary/10 border border-primary/20 text-fg'
          : 'hover:bg-surface-2 border border-transparent text-fg/80 hover:text-fg',
      ].join(' ')}
    >
      <div className="flex items-start gap-2 min-w-0 pr-6">
        <GitBranch
          size={13}
          className={['shrink-0 mt-0.5', isActive ? 'text-primary' : 'text-muted group-hover:text-fg/60'].join(' ')}
        />
        <div className="min-w-0 flex-1">
          <p className="text-xs font-semibold truncate leading-tight">{flow.name}</p>
          {flow.id && (
            <p className="text-[10px] font-mono text-muted truncate mt-0.5">{flow.id.slice(0, 8)}…</p>
          )}
          {flow._isNew && (
            <span className="inline-flex items-center px-1 py-0.5 text-[9px] font-medium rounded bg-amber-500/10 text-amber-600 dark:text-amber-400 mt-1">
              draft
            </span>
          )}
          {/* Strict-env visibility: the active env is protected and this flow
              has no pinned version there (pinned_envs from the list API). */}
          {!flow._isNew && strictEnv && Array.isArray(flow.pinned_envs)
            && !flow.pinned_envs.includes(strictEnv) && (
            <span
              title={`No version is pinned to ${strictEnv} — promote one to make it visible there.`}
              className="inline-flex items-center px-1 py-0.5 text-[9px] font-medium rounded bg-rose-500/10 text-rose-600 dark:text-rose-400 border border-rose-500/20 mt-1"
            >
              not in {strictEnv}
            </span>
          )}
        </div>
      </div>

      {/* Delete button (visible on hover, not for drafts; writers only) */}
      {!flow._isNew && canWrite && (
        <button
          onClick={handleDelete}
          disabled={deleting}
          className="absolute right-2 top-1/2 -translate-y-1/2 w-8 h-8 flex items-center justify-center rounded-md text-muted hover:text-red-500 hover:bg-red-50 dark:hover:bg-red-900/20 opacity-0 group-hover:opacity-100 transition-all disabled:opacity-40"
          title="Delete flow"
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

function FlowList({ flows, activeId, loading, onSelect, onNew, onRefresh, onDelete, onItemClick, showHeader = true, canWrite = true, strictEnv = null }) {
  const handleSelect = useCallback((flow) => {
    onSelect(flow)
    onItemClick?.()
  }, [onSelect, onItemClick])

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
          >
            <RefreshCw size={12} className={loading ? 'animate-spin' : ''} />
          </button>
        </div>
      )}

      {/* New button — writers only */}
      {canWrite && (
        <div className="shrink-0 px-2 py-2">
          <button
            onClick={() => { onNew(); onItemClick?.() }}
            className="w-full h-11 flex items-center justify-center gap-1.5 text-sm font-medium rounded-lg border border-dashed border-border text-muted hover:text-fg hover:border-border hover:bg-surface-2 transition-colors"
          >
            <Plus size={14} />
            New flow
          </button>
        </div>
      )}

      {/* List */}
      <div className="flex-1 overflow-y-auto px-2 pb-2 space-y-0.5">
        {loading && flows.length === 0 && (
          <div className="flex items-center gap-2 text-[11px] text-muted py-4 justify-center">
            <Loader2 size={12} className="animate-spin" />
            Loading…
          </div>
        )}
        {!loading && flows.length === 0 && (
          <div className="text-[11px] text-muted text-center py-6">
            <GitBranch size={20} className="mx-auto mb-2 opacity-30" />
            No flows yet
          </div>
        )}
        {flows.map(f => (
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
        <p className="text-xs font-semibold text-muted">Run history</p>
        <button
          onClick={load}
          disabled={loading}
          className="h-7 w-7 flex items-center justify-center rounded text-muted hover:text-fg hover:bg-surface-2 disabled:opacity-40 transition-colors"
        >
          <RefreshCw size={11} className={loading ? 'animate-spin' : ''} />
        </button>
      </div>

      <div className="flex-1 overflow-y-auto px-3 sm:px-4 py-3 space-y-2">
        {loading && (
          <div className="flex items-center gap-2 text-xs text-muted py-4 justify-center">
            <Loader2 size={12} className="animate-spin" />
            Loading runs…
          </div>
        )}
        {!loading && runs.length === 0 && (
          <div className="text-xs text-muted text-center py-8 rounded-lg border border-dashed border-border">
            <Play size={20} className="mx-auto mb-2 opacity-30" />
            No runs yet. Use the Builder tab to run this flow.
          </div>
        )}
        {runs.map(run => {
          const isCurrent = run.id === currentRunId
          return (
            <button
              key={run.id}
              onClick={() => onSelectRun(run.id)}
              className={[
                'w-full text-left px-3 py-3 rounded-lg border transition-colors min-h-[44px]',
                isCurrent
                  ? 'bg-primary/10 border-primary/20 text-fg'
                  : 'bg-surface border-border hover:bg-surface-2 text-fg/80 hover:text-fg',
              ].join(' ')}
            >
              <div className="flex items-center gap-2">
                <RunStateDot state={run.state} />
                <div className="flex-1 min-w-0">
                  <p className="text-xs font-mono truncate">{run.id.slice(0, 12)}…</p>
                  <p className="text-[10px] text-muted mt-0.5">
                    {run.trigger} · {run.state}
                    {run.created_at && ` · ${new Date(run.created_at).toLocaleString()}`}
                  </p>
                </div>
                {isCurrent && (
                  <span className="text-[10px] text-primary font-medium">live</span>
                )}
              </div>
            </button>
          )
        })}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// EnvSelector — active run environment (dev / prod / custom)
// ---------------------------------------------------------------------------

// prod leads (the default + production target); dev second. Custom envs the
// user adds are appended and persisted in localStorage.
// (Per-env accent dot styling is shared with the sidebar selector — see
// envDotClass in contexts/EnvContext.jsx.)
const DEFAULT_ENVS = ['prod', 'dev']
const ENVS_STORAGE_KEY = 'nubi.flow.customEnvs'

function loadCustomEnvs() {
  try {
    const raw = localStorage.getItem(ENVS_STORAGE_KEY)
    const arr = raw ? JSON.parse(raw) : []
    return Array.isArray(arr) ? arr.filter(e => typeof e === 'string' && e) : []
  } catch {
    return []
  }
}

/**
 * Top-bar environment selector. Sets the env a run is triggered against; the
 * backend namespaces materialized/incremental targets under <env>/ so dev and
 * prod never clobber each other. Defaults to prod.
 *
 * Env list + selection source: the global EnvContext (FlowsPage passes
 * `environments`/`value` from useEnv()), so this selector stays in sync with
 * the sidebar environment selector. When the API is unavailable
 * (`environments` is null) we fall back to the legacy localStorage
 * custom-env list so the selector keeps working offline. "Add environment"
 * calls `onAddEnv` (EnvContext addEnv) in API mode.
 *
 * @param {{
 *   value: string,
 *   onChange: (env: string) => void,
 *   disabled?: boolean,
 *   environments?: Array<{id:string,key:string,is_default?:boolean,protected?:boolean}>|null,
 *   onAddEnv?: (key: string) => Promise<any>,
 *   onRemoveEnv?: (env: {id:string,key:string}) => Promise<any>,
 * }} props
 */
function EnvSelector({ value, onChange, disabled = false, environments = null, onAddEnv, onRemoveEnv }) {
  const [open, setOpen] = useState(false)
  const [adding, setAdding] = useState(false)
  const [draft, setDraft] = useState('')
  const [customEnvs, setCustomEnvs] = useState(loadCustomEnvs)
  const ref = useRef(null)
  const inputRef = useRef(null)

  // API mode when the project's environments were loaded; otherwise fall back
  // to defaults + persisted localStorage customs.
  const apiMode = Array.isArray(environments)
  // All selectable envs (+ the active value if it is itself a one-off custom),
  // de-duplicated.
  const envs = apiMode
    ? Array.from(new Set([...environments.map(e => e.key), ...(value ? [value] : [])]))
    : Array.from(new Set([...DEFAULT_ENVS, ...customEnvs, ...(value ? [value] : [])]))
  const active = value || 'prod'

  useEffect(() => {
    if (!open) return
    const onDown = (e) => { if (ref.current && !ref.current.contains(e.target)) { setOpen(false); setAdding(false) } }
    const onKey = (e) => { if (e.key === 'Escape') { setOpen(false); setAdding(false) } }
    window.addEventListener('mousedown', onDown)
    window.addEventListener('keydown', onKey)
    return () => { window.removeEventListener('mousedown', onDown); window.removeEventListener('keydown', onKey) }
  }, [open])

  useEffect(() => { if (adding) inputRef.current?.focus() }, [adding])

  const select = (env) => { onChange(env); setOpen(false); setAdding(false) }

  const commitNew = async () => {
    const name = draft.trim().toLowerCase().replace(/[^a-z0-9_-]/g, '')
    if (!name) return
    if (apiMode && onAddEnv) {
      // Persist in the project's environments via the API.
      if (!envs.includes(name)) {
        try {
          await onAddEnv(name)
        } catch (cause) {
          window.alert(cause?.message || 'Could not create environment.')
          return
        }
      }
    } else if (!customEnvs.includes(name) && !DEFAULT_ENVS.includes(name)) {
      // Offline fallback: persist in localStorage.
      const next = [...customEnvs, name]
      setCustomEnvs(next)
      try { localStorage.setItem(ENVS_STORAGE_KEY, JSON.stringify(next)) } catch { /* ignore */ }
    }
    setDraft('')
    setAdding(false)
    select(name)
  }

  const removeEnv = async (env, e) => {
    e.stopPropagation()
    if (apiMode) {
      const row = environments.find(x => x.key === env)
      if (!row || !onRemoveEnv) return
      if (!window.confirm(`Delete environment "${env}" from this project?`)) return
      try {
        await onRemoveEnv(row)
      } catch (cause) {
        window.alert(cause?.message || 'Could not delete environment.')
        return
      }
    } else {
      const next = customEnvs.filter(x => x !== env)
      setCustomEnvs(next)
      try { localStorage.setItem(ENVS_STORAGE_KEY, JSON.stringify(next)) } catch { /* ignore */ }
    }
    if (active === env) onChange('prod')
  }

  return (
    <div className="relative shrink-0" ref={ref}>
      <button
        type="button"
        disabled={disabled}
        onClick={() => setOpen(o => !o)}
        title="Run environment — targets are namespaced under this env"
        aria-label="Run environment"
        aria-haspopup="listbox"
        aria-expanded={open}
        className={[
          'h-8 flex items-center gap-1.5 pl-2 pr-2 rounded-lg border text-xs font-medium transition-colors',
          'disabled:opacity-50 disabled:cursor-not-allowed',
          open ? 'border-primary bg-surface-2 text-fg' : 'border-border bg-surface text-fg hover:border-border/80 hover:bg-surface-2',
        ].join(' ')}
      >
        <span className={['w-2 h-2 rounded-full shrink-0', envDotClass(active)].join(' ')} />
        <span className="font-mono">{active}</span>
        <ChevronDown size={13} className={['text-muted shrink-0 transition-transform', open ? 'rotate-180' : ''].join(' ')} />
      </button>

      {open && (
        <div className="absolute right-0 top-full mt-1.5 z-50 w-52 rounded-xl border border-border bg-surface shadow-xl shadow-black/10 overflow-hidden">
          <p className="px-3 pt-2.5 pb-1.5 text-[10px] font-semibold uppercase tracking-widest text-muted/70">
            Run environment
          </p>
          <ul role="listbox" className="px-1 pb-1 max-h-60 overflow-y-auto">
            {envs.map(env => {
              // Removable: custom localStorage envs, or API envs that are
              // neither the default nor protected.
              const row = apiMode ? environments.find(x => x.key === env) : null
              const isCustom = apiMode
                ? Boolean(row && !row.is_default && !row.protected)
                : !DEFAULT_ENVS.includes(env)
              return (
                <li key={env}>
                  <button
                    role="option"
                    aria-selected={env === active}
                    onClick={() => select(env)}
                    className="group w-full flex items-center gap-2 px-2 py-1.5 rounded-lg text-sm text-fg hover:bg-surface-2 transition-colors"
                  >
                    <span className={['w-2 h-2 rounded-full shrink-0', envDotClass(env)].join(' ')} />
                    <span className="flex-1 text-left font-mono text-xs">{env}</span>
                    {isCustom && (
                      <span
                        role="button"
                        tabIndex={0}
                        onClick={(e) => removeEnv(env, e)}
                        title="Remove environment"
                        className="opacity-0 group-hover:opacity-100 w-5 h-5 flex items-center justify-center rounded text-muted/60 hover:text-red-500 transition-colors"
                      >
                        <X size={12} />
                      </span>
                    )}
                    {env === active && <Check size={14} className="text-primary shrink-0" />}
                  </button>
                </li>
              )
            })}
          </ul>

          <div className="border-t border-border p-1">
            {adding ? (
              <div className="flex items-center gap-1 px-1 py-0.5">
                <input
                  ref={inputRef}
                  type="text"
                  value={draft}
                  placeholder="staging"
                  className="h-7 flex-1 min-w-0 text-xs font-mono border border-border rounded-md px-2 bg-surface text-fg placeholder:text-muted/50 focus:outline-none focus:ring-2 focus:ring-ring/60"
                  onChange={e => setDraft(e.target.value)}
                  onKeyDown={e => { if (e.key === 'Enter') commitNew(); if (e.key === 'Escape') { setAdding(false); setDraft('') } }}
                />
                <button
                  onClick={commitNew}
                  className="h-7 px-2 rounded-md text-xs font-medium bg-primary text-primary-fg hover:opacity-90 transition-opacity"
                >
                  Add
                </button>
              </div>
            ) : (
              <button
                onClick={() => setAdding(true)}
                className="w-full flex items-center gap-2 px-2 py-1.5 rounded-lg text-xs font-medium text-muted hover:text-fg hover:bg-surface-2 transition-colors"
              >
                <Plus size={13} className="shrink-0" />
                Add environment
              </button>
            )}
          </div>
        </div>
      )}
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
            {error && <p className="text-[11px] text-red-500 mt-2">{error}</p>}
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

  // ── Tab: 'builder' | 'runs' ───────────────────────────────────────────────
  const [activeTab, setActiveTab] = useState('builder')

  // ── Active run (for live view) ────────────────────────────────────────────
  const [activeRunId, setActiveRunId] = useState(null)

  // ── Mobile sheet open state ───────────────────────────────────────────────
  const [mobileSheetOpen, setMobileSheetOpen] = useState(false)

  // ── Shared RHS sidebar (mirrors the dashboard editor) ─────────────────────
  // One collapsible right-hand panel switched between Flows / Add task /
  // Inspector via the top toggle buttons. The Add + Inspector panels drive the
  // FlowBuilder via an imperative ref; selection is reported back up.
  const flowBuilderRef = useRef(null)
  const [rightPanel, setRightPanel] = useState('flows')      // 'flows' | 'add' | 'inspector'
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
  // Notebook lineage panel visibility — toggled from the top bar, rendered by
  // NotebookView (notebook view only).
  const [lineageOpen, setLineageOpen] = useState(false)
  // Current builder view ('canvas' | 'notebook'), reported up from FlowBuilder
  // so the top-bar switcher reflects + drives it.
  const [flowView, setFlowView] = useState('canvas')
  // Active run environment (dev/prod/custom) — global app state shared with
  // the sidebar environment selector (EnvContext, persisted per project).
  // Drives the env passed to runFlow; backend resolution order is:
  // explicit override → spec.env → 'prod'. projectEnvs is the project's env
  // list from the API (null until loaded / when the API is unavailable —
  // EnvSelector then falls back to localStorage).
  const {
    environments: projectEnvs,
    activeEnv: runEnv,
    setActiveEnv: setRunEnv,
    addEnv: handleAddEnv,
    removeEnv: handleRemoveEnv,
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
    setLineageOpen(false)
    setActiveTab('builder')
    setActiveRunId(null)
    if (flow.id && !flow._isNew) {
      navigate(`/flows/${flow.id}`, { replace: true })
    }
  }, [navigate, setSaveError, setLineageOpen, setViewingVersion])

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
    setLineageOpen(false)
    setActiveTab('builder')
    setActiveRunId(null)
    navigate('/flows', { replace: true })
  }, [navigate, dirty, setSaveError, setLineageOpen, setViewingVersion])

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
          else setSaveError('Save failed — check the console for details.')
        }
        return null
      }
      if (!stale) {
        setSavedSnapshotJson(specJson)
        setAutosaveStatus(auto ? 'saved' : null)
        handleSaved({ ...saved, _localId: target?._localId })
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
    // Notebook view: route through the plan-gated "Run all" (PlanGateDialog
    // inside NotebookView) so the run plan is reviewed before triggering.
    if (activeTab === 'builder' && flowView === 'notebook') {
      flowBuilderRef.current?.runAll()
      return
    }
    setRunning(true)
    setRunError(null)
    const result = await runFlow(activeFlow.id, {}, runEnv || undefined)
    setRunning(false)
    if (!result) setRunError('Run failed — check the console for details.')
    else handleRun({ runId: result.id })
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
        window.alert('Save failed — checkpoint aborted.')
        return
      }
    }
    try {
      const v = await checkpoint('flow', activeFlow.id, { message: message.trim() || undefined })
      window.alert(v?.deduped
        ? `No changes since v${v.version} — the existing version was reused.`
        : `Created version v${v?.version}.`)
    } catch (cause) {
      window.alert(cause?.message || 'Checkpoint failed.')
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
    } catch (cause) {
      window.alert(cause?.message || 'Restore failed.')
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

  // ── All flows (drafts first, then saved) ──────────────────────────────────
  const allFlows = [...localDrafts, ...savedFlows]
  const activeId = activeFlow?.id ?? activeFlow?._localId

  // Strict-env badges: when the ACTIVE env is protected, rail items whose
  // pinned_envs (from the list API) lack it get a 'not in <env>' chip.
  const strictEnv = useMemo(() => {
    const row = Array.isArray(projectEnvs) ? projectEnvs.find(e => e.key === runEnv) : null
    return row?.protected ? runEnv : null
  }, [projectEnvs, runEnv])

  // ── Empty state ───────────────────────────────────────────────────────────
  const showEmpty = !activeFlow

  const canRun = !!activeFlow?.id && !activeFlow?._isNew

  // ── Toolbar portaled into the single app top bar (mirrors the dashboard
  //    editor): Builder/Runs switcher · view switcher · flow name · notebook
  //    add-cell buttons · Validate/Save/Run/Lineage/Code · RHS panel toggles.
  //    One bar, not a stacked second one — the notebook's old in-page toolbar
  //    was merged in here (its controls drive NotebookView via the ref). ─────
  const flowsToolbar = (
    <div className="flex items-center gap-1.5 w-full min-w-0 overflow-x-auto">
      {/* Mobile: flows list */}
      <button
        onClick={() => setMobileSheetOpen(true)}
        className="md:hidden flex items-center justify-center w-8 h-8 rounded-lg border border-border bg-surface text-muted hover:text-fg hover:bg-surface-2 transition-colors shrink-0"
        aria-label="Open flows list" title="Flows list"
      >
        <List size={16} />
      </button>

      {/* Builder / Runs switcher */}
      <div className="flex h-8 rounded-lg border border-border overflow-hidden shrink-0">
        {[{ id: 'builder', label: 'Builder' }, { id: 'runs', label: 'Runs' }].map((tab, i) => (
          <button
            key={tab.id}
            onClick={() => setActiveTab(tab.id)}
            className={[
              'flex items-center gap-1.5 px-3 text-xs font-medium transition-colors',
              i > 0 ? 'border-l border-border' : '',
              activeTab === tab.id ? 'bg-primary text-primary-fg' : 'bg-surface text-muted hover:text-fg hover:bg-surface-2',
            ].join(' ')}
          >
            {tab.label}
            {tab.id === 'runs' && activeRunId && (
              <span className="inline-flex w-1.5 h-1.5 rounded-full bg-amber-400 animate-pulse" />
            )}
          </button>
        ))}
      </div>

      {/* Canvas / Notebook view switcher (icons) — builder tab only */}
      {activeTab === 'builder' && (
        <div className="flex h-8 rounded-lg border border-border overflow-hidden shrink-0">
          {[
            { id: 'canvas', Icon: Share2, title: 'Canvas / DAG view' },
            { id: 'notebook', Icon: LayoutList, title: 'Notebook / cell view' },
          ].map((v, i) => (
            <button
              key={v.id}
              onClick={() => flowBuilderRef.current?.setView(v.id)}
              title={v.title}
              aria-label={v.title}
              aria-pressed={flowView === v.id}
              className={[
                'flex items-center justify-center w-8 transition-colors',
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
      {activeTab === 'builder' && (
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
      {activeTab === 'builder' && flowView === 'notebook' && (
        <div className="flex items-center gap-1 shrink-0">
          <button
            onClick={() => flowBuilderRef.current?.addCell('sql')}
            title="Add SQL cell"
            className="flex items-center gap-1.5 px-2 sm:px-2.5 h-8 text-xs font-medium rounded-lg border border-border bg-surface text-fg hover:bg-surface-2 transition-colors shrink-0"
          >
            <Database size={12} className="text-blue-500" />
            <span className="hidden sm:inline">+ SQL</span>
          </button>
          <button
            onClick={() => flowBuilderRef.current?.addCell('python')}
            title="Add Python cell"
            className="flex items-center gap-1.5 px-2 sm:px-2.5 h-8 text-xs font-medium rounded-lg border border-border bg-surface text-fg hover:bg-surface-2 transition-colors shrink-0"
          >
            <Code2 size={12} className="text-violet-500" />
            <span className="hidden sm:inline">+ Python</span>
          </button>
          <button
            onClick={() => flowBuilderRef.current?.addCell('markdown')}
            title="Add Note (markdown) cell"
            className="flex items-center gap-1.5 px-2 sm:px-2.5 h-8 text-xs font-medium rounded-lg border border-border bg-surface text-fg hover:bg-surface-2 transition-colors shrink-0"
          >
            <FileText size={12} className="text-slate-400" />
            <span className="hidden sm:inline">+ Note</span>
          </button>
        </div>
      )}

      <div className="flex items-center gap-1 ml-auto shrink-0">
        {/* Builder-only actions */}
        {activeTab === 'builder' && (
          <>
            <EnvSelector
              value={runEnv}
              onChange={setRunEnv}
              disabled={!canWrite}
              environments={projectEnvs}
              onAddEnv={handleAddEnv}
              onRemoveEnv={handleRemoveEnv}
            />
            {/* Unsaved / autosave status — sits next to the Save button */}
            <SaveStatusBadge dirty={dirty} saving={saving} autosaveStatus={autosaveStatus} className="hidden sm:flex px-1" />
            <button onClick={triggerValidate} disabled={validating} title="Validate flow"
              className="flex items-center gap-1.5 px-2 sm:px-2.5 h-8 text-xs font-medium rounded-lg border border-border bg-surface text-fg hover:bg-surface-2 disabled:opacity-50 transition-colors">
              {validating ? <Loader2 size={13} className="animate-spin" /> : <ShieldCheck size={13} />}
              <span className="hidden lg:inline">Validate</span>
            </button>
            {canWrite && (
              <button onClick={triggerSave} disabled={saving} title="Save flow"
                className="flex items-center gap-1.5 px-2 sm:px-2.5 h-8 text-xs font-medium rounded-lg border border-border bg-surface text-fg hover:bg-surface-2 disabled:opacity-50 transition-colors">
                {saving ? <Loader2 size={13} className="animate-spin" /> : <Save size={13} />}
                <span className="hidden lg:inline">Save</span>
              </button>
            )}
            {canWrite && canRun && (
              <button onClick={triggerCheckpoint} title="Checkpoint — snapshot the current draft as a new version"
                className="flex items-center gap-1.5 px-2 sm:px-2.5 h-8 text-xs font-medium rounded-lg border border-border bg-surface text-fg hover:bg-surface-2 transition-colors">
                <GitCommitHorizontal size={13} />
                <span className="hidden lg:inline">Checkpoint</span>
              </button>
            )}
            {canRun && (
              <button onClick={() => setHistoryOpen(true)} title="Version history"
                className="flex items-center gap-1.5 px-2 sm:px-2.5 h-8 text-xs font-medium rounded-lg border border-border bg-surface text-fg hover:bg-surface-2 transition-colors">
                <History size={13} />
                <span className="hidden lg:inline">History</span>
              </button>
            )}
            {canWrite && activeFlow?.id && !activeFlow?._isNew && (
              <ScheduleControl flow={activeFlow} onSaved={handleSaved} />
            )}
            {canWrite && (
              <button onClick={triggerRun} disabled={running || !canRun} title={!canRun ? 'Save the flow first' : 'Run flow'}
                className="flex items-center gap-1.5 px-2.5 h-8 text-xs font-medium rounded-lg bg-primary text-primary-fg hover:opacity-90 disabled:opacity-50 disabled:cursor-not-allowed transition-all">
                {running ? <Loader2 size={13} className="animate-spin" /> : <Play size={13} />}
                <span className="hidden lg:inline">Run</span>
              </button>
            )}
            {flowView === 'notebook' && (
              <button onClick={() => setLineageOpen(v => !v)} title={lineageOpen ? 'Hide lineage panel' : 'Show flow lineage'}
                className={[
                  'flex items-center gap-1.5 px-2 sm:px-2.5 h-8 text-xs font-medium rounded-lg border transition-colors',
                  lineageOpen ? 'border-blue-500/40 bg-blue-500/10 text-blue-600 dark:text-blue-400' : 'border-border bg-surface text-fg hover:bg-surface-2',
                ].join(' ')}>
                <GitBranch size={13} className={lineageOpen ? 'text-blue-500' : ''} />
                <span className="hidden lg:inline">Lineage</span>
              </button>
            )}
            {/* Code panel only exists in the canvas view (FlowBuilder renders it there) */}
            {flowView === 'canvas' && (
              <button onClick={() => setCodeOpen(v => !v)} title={codeOpen ? 'Hide code editor' : 'Edit flow as Python code'}
                className={[
                  'flex items-center gap-1.5 px-2 sm:px-2.5 h-8 text-xs font-medium rounded-lg border transition-colors',
                  codeOpen ? 'border-violet-400/60 bg-violet-500/10 text-violet-600 dark:text-violet-400' : 'border-border bg-surface text-fg hover:bg-surface-2',
                ].join(' ')}>
                <Code2 size={13} />
                <span className="hidden lg:inline">Code</span>
              </button>
            )}
          </>
        )}

        {/* RHS panel toggles (desktop). Click the active panel to fully collapse.
            Add task / Inspector are canvas tools — in the notebook view only the
            Flows panel toggle is shown (cells are added via the +SQL/+Python/+Note
            buttons and configured inline). */}
        <div className="hidden md:flex items-center gap-0.5 pl-1.5 ml-0.5 border-l border-border">
          {[
            { id: 'flows',     Icon: List,              title: 'Flows' },
            { id: 'add',       Icon: Plus,              title: 'Add task' },
            { id: 'inspector', Icon: SlidersHorizontal, title: 'Inspector' },
          ].filter(p => !(activeTab === 'builder' && flowView === 'notebook' && p.id !== 'flows')).map(p => {
            const active = rightPanel === p.id && !rightCollapsed
            return (
              <button key={p.id} onClick={() => togglePanel(p.id)} title={p.title} aria-label={p.title} aria-pressed={active}
                className={[
                  'w-8 h-8 flex items-center justify-center rounded-lg border transition-colors',
                  active ? 'bg-primary text-primary-fg border-primary' : 'bg-surface text-muted border-border hover:text-fg hover:bg-surface-2',
                ].join(' ')}>
                <p.Icon size={15} />
              </button>
            )
          })}
        </div>
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

          {showEmpty ? (
            /* ── Empty state ─────────────────────────────────────────────── */
            <div className="flex-1 flex flex-col items-center justify-center gap-4 text-center px-6">
              <div
                className="flex items-center justify-center w-14 h-14 rounded-2xl"
                style={{ background: 'linear-gradient(135deg, #1b2363, #2456a6, #17b3a3)' }}
              >
                <GitBranch size={26} className="text-white" />
              </div>
              <div>
                <h2 className="text-lg font-semibold font-display text-fg mb-1">Workflow orchestrator</h2>
                <p className="text-sm text-muted max-w-xs">
                  Build and run DAG-based workflows. Select a flow from the list or create one to get started.
                </p>
              </div>
              <div className="flex flex-col sm:flex-row items-center gap-3">
                {canWrite && (
                  <button
                    onClick={handleNew}
                    className="flex items-center gap-2 px-4 py-2.5 text-sm font-medium bg-primary text-primary-fg rounded-lg hover:opacity-90 transition-opacity min-h-[44px]"
                  >
                    <Plus size={15} />
                    New flow
                  </button>
                )}
                {/* Mobile: open the sheet to see existing flows */}
                <button
                  onClick={() => setMobileSheetOpen(true)}
                  className="flex md:hidden items-center gap-2 px-4 py-2.5 text-sm font-medium border border-border rounded-lg text-fg hover:bg-surface-2 transition-colors min-h-[44px]"
                >
                  <List size={15} />
                  All flows
                </button>
                {/* Secrets are flow-scoped — managed here, not in the global nav */}
                <button
                  onClick={() => navigate('/flows/secrets')}
                  className="flex items-center gap-2 px-4 py-2.5 text-sm font-medium border border-border rounded-lg text-fg hover:bg-surface-2 transition-colors min-h-[44px]"
                >
                  <KeyRound size={15} />
                  Secrets
                </button>
              </div>
            </div>
          ) : (
            /* ── Flow workspace ──────────────────────────────────────────── */
            <>
              {/* Toolbar lives in the single app top bar (portaled into AppTopbar). */}
              {topbarSlot && createPortal(flowsToolbar, topbarSlot)}

              {/* Action banners (validate / save / run) */}
              {validationIssues && (
                <div className={[
                  'shrink-0 flex items-start gap-2 px-4 py-2.5 text-xs border-b',
                  validationIssues.length === 0
                    ? 'bg-green-500/5 border-green-500/20 text-green-700 dark:text-green-400'
                    : 'bg-rose-500/5 border-rose-500/20 text-rose-700 dark:text-rose-400',
                ].join(' ')}>
                  {validationIssues.length === 0
                    ? <CheckCircle2 size={14} className="shrink-0 mt-0.5" />
                    : <AlertCircle size={14} className="shrink-0 mt-0.5" />}
                  <div className="flex-1">
                    {validationIssues.length === 0
                      ? 'Flow spec is valid.'
                      : <><strong>Validation issues:</strong><ul className="mt-1 space-y-0.5 list-disc list-inside">{validationIssues.map((i, idx) => <li key={idx}>{i}</li>)}</ul></>}
                  </div>
                  <button onClick={() => setValidationIssues(null)} className="shrink-0 opacity-60 hover:opacity-100 transition-opacity"><X size={13} /></button>
                </div>
              )}
              {saveError && (
                <div className="shrink-0 flex items-center gap-2 px-4 py-2 bg-rose-500/5 border-b border-rose-500/20 text-xs text-rose-600 dark:text-rose-400">
                  <AlertCircle size={13} />
                  <span className="flex-1 min-w-0">{saveError}</span>
                  <button onClick={() => setSaveError(null)} className="ml-auto opacity-60 hover:opacity-100 shrink-0"><X size={12} /></button>
                </div>
              )}
              {runError && (
                <div className="shrink-0 flex items-center gap-2 px-4 py-2 bg-rose-500/5 border-b border-rose-500/20 text-xs text-rose-600 dark:text-rose-400">
                  <AlertCircle size={13} />
                  <span className="flex-1 min-w-0">{runError}</span>
                  <button onClick={() => setRunError(null)} className="ml-auto opacity-60 hover:opacity-100 shrink-0"><X size={12} /></button>
                </div>
              )}

              {/* Read-only version-view banner — builder shows the version's
                  spec; the draft is untouched until Restore. */}
              {viewingVersion && activeTab === 'builder' && (
                <div className="shrink-0 flex items-center gap-2 px-4 py-2 bg-sky-500/5 border-b border-sky-500/20 text-xs text-sky-700 dark:text-sky-400">
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
                    lineageOpen={lineageOpen}
                    onLineageClose={() => setLineageOpen(false)}
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

        {/* ── Shared RHS sidebar (desktop) — Flows · Add · Inspector ──────
            One collapsible right-hand panel, switched by the top toggle buttons,
            mirroring the dashboard editor's shared sidebar. Fully collapses —
            no leftover rail; re-open via the top-bar panel toggles. */}
        {!rightCollapsed && (
          <aside className="hidden md:flex shrink-0 w-64 lg:w-72 flex-col border-l border-border bg-surface">
            <div className="shrink-0 flex items-center justify-between px-3 h-9 border-b border-border">
              <span className="text-[11px] font-semibold uppercase tracking-wider text-muted truncate">
                {rightPanel === 'flows' ? 'Flows' : rightPanel === 'add' ? 'Add task' : (
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
                {rightPanel === 'flows' && (
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
              {rightPanel === 'flows' && (
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
              {rightPanel === 'add' && (
                <AddTaskPanel
                  disabled={!canWrite || !activeFlow || activeTab !== 'builder'}
                  onAdd={(kind, config, cellType) => flowBuilderRef.current?.addNode(kind, config, cellType)}
                />
              )}
              {rightPanel === 'inspector' && (
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
