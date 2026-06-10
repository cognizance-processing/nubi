/**
 * FlowRunView.jsx — read-only DAG view colored live by task_run states.
 *
 * Polls getFlowRun(runId) every ~1.5 s until the run reaches a terminal state
 * (success | failed | cancelled).
 *
 * Clicking a node shows its result/error/logs in a side panel (desktop) or
 * bottom sheet (mobile).
 *
 * Props:
 *   runId      {string}   — flow run ID to poll
 *   spec       {object}   — FlowSpec (for DAG structure; nodes / edges don't change)
 *   onClose    {Function} — called to dismiss the run view
 */

import 'reactflow/dist/style.css'

import { useState, useEffect, useRef, useCallback, useMemo, useSyncExternalStore } from 'react'
import ReactFlow, {
  Background,
  BackgroundVariant,
  Controls,
  MiniMap,
} from 'reactflow'

import {
  X,
  CheckCircle2,
  XCircle,
  Loader2,
  Clock,
  AlertCircle,
  ChevronDown,
  ChevronRight,
  Terminal,
  RefreshCw,
  Timer,
  Layers,
} from 'lucide-react'

import { getFlowRun } from '../lib/flows.js'
import { specToGraph } from './specGraph.js'
import TaskNode from './nodes/TaskNode.jsx'

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const NODE_TYPES = { taskNode: TaskNode }
const TERMINAL_STATES = new Set(['success', 'failed', 'cancelled'])
const POLL_INTERVAL_MS = 1500

// ---------------------------------------------------------------------------
// State badge
// ---------------------------------------------------------------------------

const STATE_BADGE = {
  pending:         { cls: 'bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-300',    label: 'Pending'   },
  ready:           { cls: 'bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400',     label: 'Ready'     },
  running:         { cls: 'bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400', label: 'Running…'  },
  retrying:        { cls: 'bg-orange-100 text-orange-700 dark:bg-orange-900/30 dark:text-orange-400', label: 'Retrying' },
  success:         { cls: 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400', label: 'Success'   },
  failed:          { cls: 'bg-rose-100  text-rose-700  dark:bg-rose-900/30  dark:text-rose-400',  label: 'Failed'    },
  timed_out:       { cls: 'bg-rose-100  text-rose-700  dark:bg-rose-900/30  dark:text-rose-400',  label: 'Timed out' },
  upstream_failed: { cls: 'bg-orange-100 text-orange-700 dark:bg-orange-900/30 dark:text-orange-400', label: 'Upstream failed' },
  cancelled:       { cls: 'bg-slate-100 text-slate-500 dark:bg-slate-800 dark:text-slate-400',    label: 'Cancelled' },
}

function StateBadge({ state }) {
  const { cls, label } = STATE_BADGE[state] ?? STATE_BADGE.pending
  const isSpinning = state === 'running' || state === 'retrying'
  return (
    <span className={['inline-flex items-center gap-1 px-2.5 py-1 rounded-full text-xs font-semibold', cls].join(' ')}>
      {isSpinning        && <Loader2   size={11} className="animate-spin" />}
      {state === 'success'        && <CheckCircle2 size={11} />}
      {(state === 'failed' || state === 'timed_out') && <XCircle  size={11} />}
      {state === 'upstream_failed' && <AlertCircle size={11} />}
      {label}
    </span>
  )
}

// ---------------------------------------------------------------------------
// LogsDrawer — collapsible logs section
// ---------------------------------------------------------------------------

function LogsDrawer({ logs }) {
  const [open, setOpen] = useState(false)
  if (!logs || logs.length === 0) return null
  return (
    <div className="border border-border rounded-lg overflow-hidden">
      <button
        onClick={() => setOpen(o => !o)}
        className="w-full flex items-center gap-2 px-3 py-2 text-xs font-medium text-muted hover:text-fg hover:bg-surface-2 transition-colors"
      >
        <Terminal size={11} className="shrink-0" />
        <span className="flex-1 text-left">Logs ({logs.length} lines)</span>
        {open ? <ChevronDown size={11} /> : <ChevronRight size={11} />}
      </button>
      {open && (
        <div className="border-t border-border bg-surface-2/30 px-3 py-2 overflow-x-auto max-h-48 overflow-y-auto">
          <pre className="text-[11px] text-fg font-mono whitespace-pre-wrap break-all leading-relaxed">
            {logs.join('\n')}
          </pre>
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// TaskResultContent — shared content for result panel / sheet
// ---------------------------------------------------------------------------

function TaskResultContent({ taskRun }) {
  if (!taskRun) return null

  const durationLabel = taskRun.duration_s != null
    ? taskRun.duration_s < 1
      ? `${Math.round(taskRun.duration_s * 1000)} ms`
      : `${taskRun.duration_s.toFixed(1)} s`
    : null

  return (
    <>
      {/* Timing + meta */}
      <div className="px-4 py-3 border-b border-border space-y-1.5">
        {taskRun.started_at && (
          <p className="text-[11px] text-muted flex items-center gap-1.5">
            <Clock size={11} />
            Started: {new Date(taskRun.started_at).toLocaleTimeString()}
          </p>
        )}
        {taskRun.finished_at && (
          <p className="text-[11px] text-muted flex items-center gap-1.5">
            <CheckCircle2 size={11} />
            Finished: {new Date(taskRun.finished_at).toLocaleTimeString()}
          </p>
        )}
        {durationLabel && (
          <p className="text-[11px] text-muted flex items-center gap-1.5">
            <Timer size={11} />
            Duration: {durationLabel}
          </p>
        )}
        {(taskRun.attempt ?? 0) > 0 && (
          <p className="text-[11px] text-muted flex items-center gap-1.5">
            <RefreshCw size={11} />
            Attempt #{(taskRun.attempt ?? 0) + 1}
          </p>
        )}
      </div>

      {/* Body */}
      <div className="px-4 py-4 space-y-4">
        {/* Error */}
        {taskRun.error && (
          <div>
            <p className="text-[10px] font-semibold text-muted/70 uppercase tracking-widest mb-1.5">Error</p>
            <div className="rounded-lg border border-rose-200 dark:border-rose-800 bg-rose-50 dark:bg-rose-900/20 px-3 py-2">
              <p className="text-xs text-rose-700 dark:text-rose-400 font-mono break-words whitespace-pre-wrap">
                {taskRun.error}
              </p>
            </div>
          </div>
        )}

        {/* Logs */}
        {taskRun.logs && taskRun.logs.length > 0 && (
          <div>
            <p className="text-[10px] font-semibold text-muted/70 uppercase tracking-widest mb-1.5">Logs</p>
            <LogsDrawer logs={taskRun.logs} />
          </div>
        )}

        {/* Result */}
        {taskRun.result != null && (
          <div>
            <p className="text-[10px] font-semibold text-muted/70 uppercase tracking-widest mb-1.5">Result</p>
            <div className="rounded-lg border border-border bg-surface-2/30 px-3 py-2 overflow-x-auto">
              <pre className="text-xs text-fg font-mono whitespace-pre-wrap break-all">
                {JSON.stringify(taskRun.result, null, 2)}
              </pre>
            </div>
          </div>
        )}

        {/* Pending / running placeholder */}
        {!taskRun.error && taskRun.result == null && (!taskRun.logs || taskRun.logs.length === 0) && (
          <div className="text-xs text-muted text-center py-6 rounded-lg border border-dashed border-border">
            {taskRun.state === 'running' || taskRun.state === 'ready' || taskRun.state === 'retrying'
              ? 'Running — result will appear when complete.'
              : 'No result available.'}
          </div>
        )}
      </div>
    </>
  )
}

// ---------------------------------------------------------------------------
// TaskResultPanel — desktop side panel
// ---------------------------------------------------------------------------

function TaskResultPanel({ taskRun, onClose }) {
  if (!taskRun) return null

  return (
    <aside className="shrink-0 w-72 xl:w-80 flex flex-col border-l border-border bg-surface overflow-hidden">
      {/* Header */}
      <div className="shrink-0 flex items-center justify-between px-4 py-3 border-b border-border">
        <div className="min-w-0 flex-1 mr-2">
          <h3 className="text-sm font-semibold text-fg font-mono truncate">{taskRun.task_key}</h3>
          <div className="mt-1">
            <StateBadge state={taskRun.state} />
          </div>
        </div>
        <button
          onClick={onClose}
          className="w-7 h-7 flex items-center justify-center rounded-lg text-muted hover:text-fg hover:bg-surface-2 transition-colors shrink-0"
        >
          <X size={14} />
        </button>
      </div>

      <div className="flex-1 overflow-y-auto">
        <TaskResultContent taskRun={taskRun} />
      </div>
    </aside>
  )
}

// ---------------------------------------------------------------------------
// TaskResultSheet — mobile bottom sheet
// ---------------------------------------------------------------------------

function TaskResultSheet({ taskRun, open, onClose }) {
  return (
    <>
      {open && (
        <div
          className="fixed inset-0 z-40 bg-black/40"
          onClick={onClose}
          aria-hidden="true"
        />
      )}
      <div
        className={[
          'fixed bottom-0 left-0 right-0 z-50 flex flex-col',
          'bg-surface border-t border-border rounded-t-2xl',
          'transition-transform duration-300 ease-out',
          'max-h-[75dvh]',
          open ? 'translate-y-0' : 'translate-y-full',
        ].join(' ')}
        role="dialog"
        aria-modal="true"
        aria-label="Task result"
      >
        {/* Drag handle */}
        <div className="shrink-0 flex justify-center pt-3 pb-1">
          <div className="w-10 h-1 rounded-full bg-border" />
        </div>
        {/* Header */}
        <div className="shrink-0 flex items-center justify-between px-4 py-2 border-b border-border">
          <div className="min-w-0 flex-1 mr-2">
            <h3 className="text-sm font-semibold text-fg font-mono truncate">{taskRun?.task_key}</h3>
            {taskRun && <div className="mt-1"><StateBadge state={taskRun.state} /></div>}
          </div>
          <button
            onClick={onClose}
            className="w-9 h-9 flex items-center justify-center rounded-lg text-muted hover:text-fg hover:bg-surface-2 transition-colors shrink-0"
            aria-label="Close"
          >
            <X size={16} />
          </button>
        </div>
        <div className="flex-1 overflow-y-auto pb-8">
          <TaskResultContent taskRun={taskRun} />
        </div>
      </div>
    </>
  )
}

// ---------------------------------------------------------------------------
// TaskRunsTable — compact run summary table (visible in banner area on desktop)
// ---------------------------------------------------------------------------

function TaskRunsSummary({ taskRuns }) {
  if (!taskRuns || taskRuns.length === 0) return null

  const counts = taskRuns.reduce((acc, tr) => {
    acc[tr.state] = (acc[tr.state] || 0) + 1
    return acc
  }, {})

  const badges = [
    { state: 'success',         color: 'text-green-600 dark:text-green-400' },
    { state: 'failed',          color: 'text-rose-600 dark:text-rose-400' },
    { state: 'timed_out',       color: 'text-rose-600 dark:text-rose-400' },
    { state: 'upstream_failed', color: 'text-orange-600 dark:text-orange-400' },
    { state: 'retrying',        color: 'text-orange-600 dark:text-orange-400' },
    { state: 'running',         color: 'text-amber-600 dark:text-amber-400' },
    { state: 'ready',           color: 'text-blue-600 dark:text-blue-400' },
    { state: 'pending',         color: 'text-slate-500 dark:text-slate-400' },
  ].filter(b => counts[b.state])

  return (
    <div className="hidden md:flex items-center gap-2 text-[11px] shrink-0">
      {badges.map(b => (
        <span key={b.state} className={['font-medium', b.color].join(' ')}>
          {counts[b.state]} {b.state.replace('_', ' ')}
        </span>
      ))}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Mobile media query (subscribed via useSyncExternalStore — no setState-in-effect)
// ---------------------------------------------------------------------------

const MOBILE_QUERY = '(max-width: 767px)'

function subscribeMobile(callback) {
  if (typeof window === 'undefined') return () => {}
  const mq = window.matchMedia(MOBILE_QUERY)
  mq.addEventListener('change', callback)
  return () => mq.removeEventListener('change', callback)
}

function getMobileSnapshot() {
  return typeof window !== 'undefined' && window.matchMedia(MOBILE_QUERY).matches
}

function getMobileServerSnapshot() {
  return false
}

// ---------------------------------------------------------------------------
// FlowRunView
// ---------------------------------------------------------------------------

export default function FlowRunView({ runId, spec, onClose }) {
  const [flowRun, setFlowRun] = useState(null)
  const [taskRuns, setTaskRuns] = useState([])
  const [error, setError] = useState(null)
  const [selectedTaskKey, setSelectedTaskKey] = useState(null)
  const pollingRef = useRef(null)

  // ── Detect mobile ─────────────────────────────────────────────────────────
  const isMobile = useSyncExternalStore(subscribeMobile, getMobileSnapshot, getMobileServerSnapshot)

  // ── Build base graph from spec (structure never changes mid-run) ──────────
  const { nodes: baseNodes, edges } = useMemo(() => specToGraph(spec ?? {}), [spec])

  // ── Merge task_run states onto nodes ──────────────────────────────────────
  const nodes = useMemo(() => {
    const taskRunByKey = new Map(taskRuns.map(tr => [tr.task_key, tr]))
    return baseNodes.map(n => ({
      ...n,
      data: {
        ...n.data,
        taskRun: taskRunByKey.get(n.id) ?? null,
      },
    }))
  }, [baseNodes, taskRuns])

  // ── Poll ──────────────────────────────────────────────────────────────────
  const poll = useCallback(async () => {
    if (!runId) return
    const data = await getFlowRun(runId)
    if (!data) {
      setError('Failed to load run data.')
      return
    }
    setFlowRun(data)
    setTaskRuns(data.task_runs ?? [])

    if (TERMINAL_STATES.has(data.state)) {
      clearInterval(pollingRef.current)
      pollingRef.current = null
    }
  }, [runId])

  useEffect(() => {
    if (!runId) return
    const t = setTimeout(poll, 0)
    pollingRef.current = setInterval(poll, POLL_INTERVAL_MS)
    return () => {
      clearTimeout(t)
      clearInterval(pollingRef.current)
      pollingRef.current = null
    }
  }, [runId, poll])

  // ── Clicked task ──────────────────────────────────────────────────────────
  const selectedTaskRun = taskRuns.find(tr => tr.task_key === selectedTaskKey) ?? null

  const onNodeClick = useCallback((_evt, node) => {
    setSelectedTaskKey(node.id)
  }, [])

  const onPaneClick = useCallback(() => {
    setSelectedTaskKey(null)
  }, [])

  // ── Render ────────────────────────────────────────────────────────────────
  return (
    <div className="flex flex-col h-full overflow-hidden">

      {/* Banner */}
      <div className="shrink-0 flex items-center gap-2 sm:gap-3 px-3 sm:px-4 py-2.5 border-b border-border bg-surface-2/40 overflow-x-auto">
        <div className="shrink-0">
          <span className="text-xs font-semibold text-fg">Run</span>
          <span className="ml-1.5 sm:ml-2 text-xs text-muted font-mono">{runId?.slice(0, 8)}…</span>
        </div>
        {flowRun && <StateBadge state={flowRun.state} />}
        {flowRun?.env && (
          <span
            className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] font-medium bg-violet-100 text-violet-700 dark:bg-violet-900/30 dark:text-violet-400 shrink-0"
            title="Environment this run targeted"
          >
            <Layers size={10} />
            {flowRun.env}
          </span>
        )}
        {!flowRun && !error && <Loader2 size={13} className="animate-spin text-muted shrink-0" />}
        {error && (
          <div className="flex items-center gap-1.5 text-xs text-rose-600 dark:text-rose-400 shrink-0">
            <AlertCircle size={13} />
            <span className="hidden sm:inline">{error}</span>
          </div>
        )}
        {/* Task state summary counts */}
        <TaskRunsSummary taskRuns={taskRuns} />
        {/* Timing — hidden on mobile to save space */}
        {flowRun?.started_at && (
          <span className="hidden sm:inline text-[11px] text-muted ml-1 shrink-0">
            Started {new Date(flowRun.started_at).toLocaleTimeString()}
          </span>
        )}
        <div className="flex-1 min-w-0" />
        {!TERMINAL_STATES.has(flowRun?.state) && (
          <span className="text-[11px] text-muted flex items-center gap-1 shrink-0">
            <Loader2 size={11} className="animate-spin" />
            <span className="hidden sm:inline">Polling…</span>
          </span>
        )}
        {onClose && (
          <button
            onClick={onClose}
            className="w-8 h-8 flex items-center justify-center rounded-lg text-muted hover:text-fg hover:bg-surface-2 transition-colors shrink-0 min-h-[44px] min-w-[44px]"
            title="Close run view"
          >
            <X size={14} />
          </button>
        )}
      </div>

      {/* Canvas + side panel */}
      <div className="flex flex-1 min-h-0 overflow-hidden">

        {/* Read-only DAG */}
        <div className="flex-1 relative">
          <ReactFlow
            nodes={nodes}
            edges={edges}
            nodeTypes={NODE_TYPES}
            nodesDraggable={false}
            nodesConnectable={false}
            elementsSelectable={true}
            onNodeClick={onNodeClick}
            onPaneClick={onPaneClick}
            fitView
            fitViewOptions={{ padding: 0.25 }}
            className="bg-bg"
            panOnDrag
            panOnScroll={false}
            zoomOnScroll
            zoomOnPinch
            preventScrolling
            defaultEdgeOptions={{
              type: 'smoothstep',
              style: { strokeWidth: 1.5, stroke: '#64748b' },
            }}
          >
            <Background
              variant={BackgroundVariant.Dots}
              gap={16}
              size={1}
              color="var(--color-border, #e2e8f0)"
            />
            <Controls className="!shadow-md !border !border-border !rounded-xl overflow-hidden" />
            {/* MiniMap: hidden on mobile */}
            <div className="hidden md:block">
              <MiniMap
                className="!border !border-border !rounded-xl overflow-hidden !shadow-md"
                nodeColor={(node) => {
                  const state = node.data?.taskRun?.state
                  return {
                    pending:         '#94a3b8',
                    ready:           '#3b82f6',
                    running:         '#f59e0b',
                    retrying:        '#f97316',
                    success:         '#22c55e',
                    failed:          '#ef4444',
                    timed_out:       '#ef4444',
                    upstream_failed: '#f97316',
                    cancelled:       '#6b7280',
                  }[state] ?? '#94a3b8'
                }}
                maskColor="rgba(0,0,0,0.05)"
              />
            </div>
          </ReactFlow>
        </div>

        {/* Task result panel — desktop inline */}
        {selectedTaskRun && !isMobile && (
          <TaskResultPanel
            taskRun={selectedTaskRun}
            onClose={() => setSelectedTaskKey(null)}
          />
        )}
      </div>

      {/* Task result sheet — mobile bottom sheet */}
      <TaskResultSheet
        taskRun={selectedTaskRun}
        open={!!selectedTaskRun && isMobile}
        onClose={() => setSelectedTaskKey(null)}
      />
    </div>
  )
}
