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

import { useState, useEffect, useCallback } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import {
  Plus,
  RefreshCw,
  GitBranch,
  Trash2,
  Loader2,
  Play,
  List,
  X,
  ChevronDown,
} from 'lucide-react'

import {
  listFlows,
  getFlow,
  deleteFlow,
  listFlowRuns,
} from '../../lib/flows.js'
import FlowBuilder from '../../flows/FlowBuilder.jsx'
import FlowRunView from '../../flows/FlowRunView.jsx'

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

function FlowListItem({ flow, isActive, onClick, onDelete }) {
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

      {/* Delete button (visible on hover, not for drafts) */}
      {!flow._isNew && (
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

function FlowList({ flows, activeId, loading, onSelect, onNew, onRefresh, onDelete, onItemClick }) {
  const handleSelect = useCallback((flow) => {
    onSelect(flow)
    onItemClick?.()
  }, [onSelect, onItemClick])

  return (
    <>
      {/* Header */}
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

      {/* New button */}
      <div className="shrink-0 px-2 py-2">
        <button
          onClick={() => { onNew(); onItemClick?.() }}
          className="w-full h-11 flex items-center justify-center gap-1.5 text-sm font-medium rounded-lg border border-dashed border-border text-muted hover:text-fg hover:border-border hover:bg-surface-2 transition-colors"
        >
          <Plus size={14} />
          New flow
        </button>
      </div>

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
          />
        ))}
      </div>
    </>
  )
}

// ---------------------------------------------------------------------------
// LeftRail — desktop sidebar (hidden on mobile)
// ---------------------------------------------------------------------------

function LeftRail({ flows, activeId, loading, onSelect, onNew, onRefresh, onDelete }) {
  return (
    <aside className="flex flex-col h-full border-r border-border bg-surface-2/40">
      <FlowList
        flows={flows}
        activeId={activeId}
        loading={loading}
        onSelect={onSelect}
        onNew={onNew}
        onRefresh={onRefresh}
        onDelete={onDelete}
      />
    </aside>
  )
}

// ---------------------------------------------------------------------------
// MobileFlowsSheet — bottom sheet for flow list on mobile
// ---------------------------------------------------------------------------

function MobileFlowsSheet({ open, onClose, flows, activeId, loading, onSelect, onNew, onRefresh, onDelete }) {
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
// FlowsPage
// ---------------------------------------------------------------------------

export default function FlowsPage() {
  const { id: routeId } = useParams()
  const navigate = useNavigate()

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

  // ── After run callback ────────────────────────────────────────────────────
  const handleRun = useCallback(({ runId }) => {
    setActiveRunId(runId)
    setActiveTab('runs')
  }, [])

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

  return (
    // AppShell's <main> is flex-1 overflow-y-auto inside a min-h-0 flex container,
    // so it has a definite height. We use h-full + overflow-hidden to fill it
    // without causing page scroll — critical for the ReactFlow canvas height.
    <div className="flex flex-col h-full overflow-hidden">
      {/* Outer wrapper that fills available space */}
      <div className="flex flex-1 min-h-0 overflow-hidden bg-bg">

        {/* ── Left rail (desktop only) ─────────────────────────────────── */}
        <div className="hidden md:flex shrink-0 w-56 lg:w-64 flex-col">
          <LeftRail
            flows={allFlows}
            activeId={activeId}
            loading={loadingFlows}
            onSelect={selectFlow}
            onNew={handleNew}
            onRefresh={loadFlows}
            onDelete={handleDelete}
          />
        </div>

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
                <button
                  onClick={handleNew}
                  className="flex items-center gap-2 px-4 py-2.5 text-sm font-medium bg-primary text-primary-fg rounded-lg hover:opacity-90 transition-opacity min-h-[44px]"
                >
                  <Plus size={15} />
                  New flow
                </button>
                {/* Mobile: open the sheet to see existing flows */}
                <button
                  onClick={() => setMobileSheetOpen(true)}
                  className="flex md:hidden items-center gap-2 px-4 py-2.5 text-sm font-medium border border-border rounded-lg text-fg hover:bg-surface-2 transition-colors min-h-[44px]"
                >
                  <List size={15} />
                  All flows
                </button>
              </div>
            </div>
          ) : (
            /* ── Flow workspace ──────────────────────────────────────────── */
            <>
              {/* Tab bar */}
              <div className="shrink-0 flex items-center gap-0 px-2 sm:px-4 border-b border-border bg-surface-2/20">

                {/* Mobile: flow list button */}
                <button
                  onClick={() => setMobileSheetOpen(true)}
                  className="md:hidden flex items-center justify-center w-9 h-9 rounded-lg text-muted hover:text-fg hover:bg-surface-2 transition-colors shrink-0 mr-1"
                  aria-label="Open flows list"
                  title="Flows list"
                >
                  <List size={16} />
                </button>

                {/* Flow name display */}
                <div className="flex-1 flex items-center gap-2 py-2 min-w-0">
                  <GitBranch size={14} className="text-muted shrink-0" />
                  <span className="text-sm font-semibold text-fg truncate max-w-[120px] sm:max-w-[200px]">
                    {activeSpec?.name || 'Untitled flow'}
                  </span>
                  {activeFlow?._isNew && (
                    <span className="hidden xs:inline px-1.5 py-0.5 rounded text-[10px] font-medium bg-amber-500/10 text-amber-600 dark:text-amber-400 shrink-0">
                      draft
                    </span>
                  )}
                </div>

                {/* Tabs */}
                <div className="flex shrink-0">
                  {[
                    { id: 'builder', label: 'Builder' },
                    { id: 'runs',    label: 'Runs' },
                  ].map(tab => (
                    <button
                      key={tab.id}
                      onClick={() => setActiveTab(tab.id)}
                      className={[
                        'px-3 sm:px-4 py-2.5 text-xs font-medium border-b-2 transition-colors min-h-[44px]',
                        activeTab === tab.id
                          ? 'border-primary text-primary'
                          : 'border-transparent text-muted hover:text-fg',
                      ].join(' ')}
                    >
                      {tab.label}
                      {tab.id === 'runs' && activeRunId && (
                        <span className="ml-1.5 inline-flex w-1.5 h-1.5 rounded-full bg-amber-400 animate-pulse" />
                      )}
                    </button>
                  ))}
                </div>
              </div>

              {/* Tab content */}
              <div className="flex-1 min-h-0 overflow-hidden">
                {activeTab === 'builder' && (
                  <FlowBuilder
                    key={activeFlow?.id ?? activeFlow?._localId}
                    flow={activeFlow?._isNew ? null : activeFlow}
                    spec={activeSpec}
                    onSpecChange={setActiveSpec}
                    onSaved={handleSaved}
                    onRun={handleRun}
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
      />
    </div>
  )
}
