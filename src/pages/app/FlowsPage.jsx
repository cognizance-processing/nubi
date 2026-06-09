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
 */

import { useState, useEffect, useCallback, useRef } from 'react'
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
} from 'lucide-react'

import { useUi } from '../../contexts/UiContext.jsx'
import { useCanWrite } from '../../contexts/OrgContext.jsx'
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
import FlowRunView from '../../flows/FlowRunView.jsx'
import { AddTaskPanel } from '../../flows/AddTaskPanel.jsx'
import NodeInspector from '../../flows/NodeInspector.jsx'

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const EMPTY_SPEC = { version: 1, name: 'new', params: [], tasks: [] }

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

function FlowListItem({ flow, isActive, onClick, onDelete, canWrite }) {
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

function FlowList({ flows, activeId, loading, onSelect, onNew, onRefresh, onDelete, onItemClick, showHeader = true, canWrite = true }) {
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
          />
        ))}
      </div>
    </>
  )
}

// ---------------------------------------------------------------------------
// MobileFlowsSheet — bottom sheet for flow list on mobile
// ---------------------------------------------------------------------------

function MobileFlowsSheet({ open, onClose, flows, activeId, loading, onSelect, onNew, onRefresh, onDelete, canWrite }) {
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
const DEFAULT_ENVS = ['prod', 'dev']
const ENVS_STORAGE_KEY = 'nubi.flow.customEnvs'

// Per-env accent dot. prod = emerald (live), dev = sky, anything else = violet.
function envDotClass(env) {
  if (env === 'prod') return 'bg-emerald-500'
  if (env === 'dev') return 'bg-sky-500'
  return 'bg-violet-500'
}

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
 * prod never clobber each other. Defaults to prod. Users can add their own
 * named environments via the inline "Add environment" action (persisted).
 *
 * @param {{ value: string, onChange: (env: string) => void, disabled?: boolean }} props
 */
function EnvSelector({ value, onChange, disabled = false }) {
  const [open, setOpen] = useState(false)
  const [adding, setAdding] = useState(false)
  const [draft, setDraft] = useState('')
  const [customEnvs, setCustomEnvs] = useState(loadCustomEnvs)
  const ref = useRef(null)
  const inputRef = useRef(null)

  // All selectable envs: defaults + persisted customs + (the active value if it
  // is itself a one-off custom not yet saved), de-duplicated, prod-first.
  const envs = Array.from(new Set([...DEFAULT_ENVS, ...customEnvs, ...(value ? [value] : [])]))
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

  const commitNew = () => {
    const name = draft.trim().toLowerCase().replace(/[^a-z0-9_-]/g, '')
    if (!name) return
    if (!customEnvs.includes(name) && !DEFAULT_ENVS.includes(name)) {
      const next = [...customEnvs, name]
      setCustomEnvs(next)
      try { localStorage.setItem(ENVS_STORAGE_KEY, JSON.stringify(next)) } catch { /* ignore */ }
    }
    setDraft('')
    setAdding(false)
    select(name)
  }

  const removeEnv = (env, e) => {
    e.stopPropagation()
    const next = customEnvs.filter(x => x !== env)
    setCustomEnvs(next)
    try { localStorage.setItem(ENVS_STORAGE_KEY, JSON.stringify(next)) } catch { /* ignore */ }
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
              const isCustom = !DEFAULT_ENVS.includes(env)
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
  // Current builder view ('canvas' | 'notebook'), reported up from FlowBuilder
  // so the top-bar switcher reflects + drives it.
  const [flowView, setFlowView] = useState('canvas')
  // Active run environment (dev/prod/custom). Drives the env passed to runFlow;
  // backend resolution order is: explicit override → spec.env → 'prod'.
  const [runEnv, setRunEnv] = useState('prod')

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
    setActiveFlow(flow)
    setActiveSpec(flow.spec ?? EMPTY_SPEC)
    setActiveTab('builder')
    setActiveRunId(null)
    if (flow.id && !flow._isNew) {
      navigate(`/flows/${flow.id}`, { replace: true })
    }
  }, [navigate])

  // Keep the active run env in sync with the active flow's spec.env (default
  // 'prod'). Done in an effect so the selection callbacks stay setter-free for
  // the React Compiler's manual-memoization check.
  useEffect(() => {
    const t = setTimeout(() => setRunEnv(activeSpec?.env || 'prod'), 0)
    return () => clearTimeout(t)
  }, [activeFlow, activeSpec?.env])

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
    const draft = { ...newFlowDraft(), _localId: `draft-${Date.now()}` }
    setLocalDrafts(prev => [draft, ...prev])
    setActiveFlow(draft)
    setActiveSpec({ ...EMPTY_SPEC })
    setActiveTab('builder')
    setActiveRunId(null)
    navigate('/flows', { replace: true })
  }, [navigate])

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

  const triggerSave = async () => {
    setSaving(true)
    setSaveError(null)
    let saved
    if (activeFlow && !activeFlow._isNew && activeFlow.id) {
      saved = await updateFlow(activeFlow.id, { name: activeSpec.name, spec: activeSpec })
    } else {
      saved = await createFlow(activeSpec.name, activeSpec)
    }
    setSaving(false)
    if (!saved) setSaveError('Save failed — check the console for details.')
    else handleSaved({ ...saved, _localId: activeFlow?._localId })
  }

  const triggerRun = async () => {
    if (!activeFlow?.id || activeFlow._isNew) {
      setRunError('Save the flow first before running.')
      return
    }
    setRunning(true)
    setRunError(null)
    const result = await runFlow(activeFlow.id, {}, runEnv || undefined)
    setRunning(false)
    if (!result) setRunError('Run failed — check the console for details.')
    else handleRun({ runId: result.id })
  }

  // ── Delete callback ───────────────────────────────────────────────────────
  const handleDelete = useCallback((flowId) => {
    setSavedFlows(prev => prev.filter(f => f.id !== flowId))
    setActiveFlow(prev => {
      if (prev?.id === flowId) {
        setActiveSpec(EMPTY_SPEC)
        navigate('/flows', { replace: true })
        return null
      }
      return prev
    })
  }, [navigate])

  // ── All flows (drafts first, then saved) ──────────────────────────────────
  const allFlows = [...localDrafts, ...savedFlows]
  const activeId = activeFlow?.id ?? activeFlow?._localId

  // ── Empty state ───────────────────────────────────────────────────────────
  const showEmpty = !activeFlow

  const canRun = !!activeFlow?.id && !activeFlow?._isNew

  // ── Toolbar portaled into the single app top bar (mirrors the dashboard
  //    editor): flow name · Builder/Runs switcher · Validate/Save/Run/Code ·
  //    RHS panel toggles. One bar, not a stacked second one. ─────────────────
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

      <div className="flex items-center gap-1 ml-auto shrink-0">
        {/* Builder-only actions */}
        {activeTab === 'builder' && (
          <>
            <EnvSelector value={runEnv} onChange={setRunEnv} disabled={!canWrite} />
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
            {canWrite && (
              <button onClick={triggerRun} disabled={running || !canRun} title={!canRun ? 'Save the flow first' : 'Run flow'}
                className="flex items-center gap-1.5 px-2.5 h-8 text-xs font-medium rounded-lg bg-primary text-primary-fg hover:opacity-90 disabled:opacity-50 disabled:cursor-not-allowed transition-all">
                {running ? <Loader2 size={13} className="animate-spin" /> : <Play size={13} />}
                <span className="hidden lg:inline">Run</span>
              </button>
            )}
            <button onClick={() => setCodeOpen(v => !v)} title={codeOpen ? 'Hide code editor' : 'Edit flow as Python code'}
              className={[
                'flex items-center gap-1.5 px-2 sm:px-2.5 h-8 text-xs font-medium rounded-lg border transition-colors',
                codeOpen ? 'border-violet-400/60 bg-violet-500/10 text-violet-600 dark:text-violet-400' : 'border-border bg-surface text-fg hover:bg-surface-2',
              ].join(' ')}>
              <Code2 size={13} />
              <span className="hidden lg:inline">Code</span>
            </button>
          </>
        )}

        {/* RHS panel toggles (desktop). Click the active panel to fully collapse. */}
        <div className="hidden md:flex items-center gap-0.5 pl-1.5 ml-0.5 border-l border-border">
          {[
            { id: 'flows',     Icon: List,              title: 'Flows' },
            { id: 'add',       Icon: Plus,              title: 'Add task' },
            { id: 'inspector', Icon: SlidersHorizontal, title: 'Inspector' },
          ].map(p => {
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

              {/* Tab content */}
              <div className="flex-1 min-h-0 overflow-hidden">
                {activeTab === 'builder' && (
                  <FlowBuilder
                    key={activeFlow?.id ?? activeFlow?._localId}
                    ref={flowBuilderRef}
                    flow={activeFlow?._isNew ? null : activeFlow}
                    spec={activeSpec}
                    onSpecChange={setActiveSpec}
                    onSaved={handleSaved}
                    onRun={handleRun}
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
                  onSelect={selectFlow}
                  onNew={handleNew}
                  onRefresh={loadFlows}
                  onDelete={handleDelete}
                  showHeader={false}
                  canWrite={canWrite}
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
        onSelect={selectFlow}
        onNew={handleNew}
        onRefresh={loadFlows}
        onDelete={handleDelete}
        canWrite={canWrite}
      />
    </div>
  )
}
