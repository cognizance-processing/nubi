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

import { useState, useEffect, useRef, useCallback, useMemo, useContext, useSyncExternalStore } from 'react'
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
import { StateDot } from './nodes/nodeChrome.jsx'
import Tag from '../components/ui/Tag.jsx'
import Button from '../components/ui/Button.jsx'
import { ThemeContext } from '../contexts/ThemeContext.jsx'
import '../components/app/appPatterns.css'

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const NODE_TYPES = { taskNode: TaskNode }
const TERMINAL_STATES = new Set(['success', 'failed', 'cancelled'])
const POLL_INTERVAL_MS = 1500

// ---------------------------------------------------------------------------
// State badge
// ---------------------------------------------------------------------------

const STATE_BADGE: Record<string, { variant: 'neutral' | 'warning' | 'success' | 'danger'; label: string }> = {
  pending:         { variant: 'neutral', label: 'Pending' },
  ready:           { variant: 'neutral', label: 'Ready' },
  running:         { variant: 'warning', label: 'Running' },
  retrying:        { variant: 'warning', label: 'Retrying' },
  success:         { variant: 'success', label: 'Success' },
  failed:          { variant: 'danger',  label: 'Failed' },
  timed_out:       { variant: 'danger',  label: 'Timed out' },
  upstream_failed: { variant: 'warning', label: 'Upstream failed' },
  cancelled:       { variant: 'neutral', label: 'Cancelled' },
}

function StateBadge({ state }) {
  const { variant, label } = STATE_BADGE[state] ?? STATE_BADGE.pending
  return (
    <Tag variant={variant} dot size="md">
      {label}
    </Tag>
  )
}

// ---------------------------------------------------------------------------
// LogsDrawer — collapsible logs section
// ---------------------------------------------------------------------------

function LogsDrawer({ logs }) {
  const [open, setOpen] = useState(false)
  if (!logs || logs.length === 0) return null
  return (
    <div className="overflow-hidden border border-border" style={{ borderRadius: 2 }}>
      <button
        type="button"
        onClick={() => setOpen(o => !o)}
        aria-expanded={open}
        className="nubi-focus-inset flex w-full items-center gap-2 px-3 py-2 text-xs font-medium text-muted transition-colors hover:bg-frame-hover hover:text-fg"
      >
        <Terminal size={11} className="shrink-0" />
        <span className="flex-1 text-left">Logs ({logs.length} lines)</span>
        {open ? <ChevronDown size={11} /> : <ChevronRight size={11} />}
      </button>
      {open && (
        <div className="max-h-48 overflow-x-auto overflow-y-auto border-t border-border bg-surface-2 px-3 py-2">
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
            <p className="t-label mb-1.5">Error</p>
            <div className="nubi-notice nubi-notice-error" role="alert">
              <p className="m-0 break-words whitespace-pre-wrap font-mono text-xs">
                {taskRun.error}
              </p>
            </div>
          </div>
        )}

        {/* Logs */}
        {taskRun.logs && taskRun.logs.length > 0 && (
          <div>
            <p className="t-label mb-1.5">Logs</p>
            <LogsDrawer logs={taskRun.logs} />
          </div>
        )}

        {/* Result */}
        {taskRun.result != null && (
          <div>
            <p className="t-label mb-1.5">Result</p>
            <div className="overflow-x-auto border border-border bg-surface-2 px-3 py-2" style={{ borderRadius: 2 }}>
              <pre className="text-xs text-fg font-mono whitespace-pre-wrap break-all">
                {JSON.stringify(taskRun.result, null, 2)}
              </pre>
            </div>
          </div>
        )}

        {/* Pending / running placeholder */}
        {!taskRun.error && taskRun.result == null && (!taskRun.logs || taskRun.logs.length === 0) && (
          <div className="border border-dashed border-frame-line py-6 text-center text-xs text-muted" style={{ borderRadius: 2 }}>
            {taskRun.state === 'running' || taskRun.state === 'ready' || taskRun.state === 'retrying'
              ? 'Running. The result appears here when the task completes.'
              : 'This task produced no result.'}
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
    <aside className="flex w-72 shrink-0 flex-col overflow-hidden border-l border-frame-line bg-surface xl:w-80">
      {/* Header */}
      <div className="flex shrink-0 items-center justify-between border-b border-border py-3 pl-4 pr-2" style={{ borderTop: '2px solid var(--ink)' }}>
        <div className="mr-2 min-w-0 flex-1">
          <h3 className="m-0 truncate font-mono text-sm font-semibold text-fg">{taskRun.task_key}</h3>
          <div className="mt-1">
            <StateBadge state={taskRun.state} />
          </div>
        </div>
        <Button variant="ghost" size="icon-sm" onClick={onClose} aria-label="Close task result">
          <X size={14} aria-hidden="true" />
        </Button>
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
      {open && <div className="ap-backdrop" onClick={onClose} aria-hidden="true" />}
      <div
        className="fixed bottom-0 left-0 right-0 flex max-h-[75dvh] flex-col border-t-2 border-ink bg-surface"
        style={{
          zIndex: 'var(--z-modal)' as any,
          transform: open ? 'translateY(0)' : 'translateY(100%)',
          visibility: open ? 'visible' : 'hidden',
          transition: 'transform var(--dur-3) var(--ease-out), visibility 0s linear ' + (open ? '0s' : 'var(--dur-3)'),
          boxShadow: 'var(--shadow-pop)',
        }}
        role="dialog"
        aria-modal="true"
        aria-hidden={!open}
        aria-label="Task result"
      >
        {/* Header */}
        <div className="flex shrink-0 items-center justify-between border-b border-border py-2 pl-4 pr-2">
          <div className="mr-2 min-w-0 flex-1">
            <h3 className="m-0 truncate font-mono text-sm font-semibold text-fg">{taskRun?.task_key}</h3>
            {taskRun && <div className="mt-1"><StateBadge state={taskRun.state} /></div>}
          </div>
          <Button variant="ghost" size="icon" onClick={onClose} aria-label="Close">
            <X size={16} aria-hidden="true" />
          </Button>
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

  const order = ['success', 'failed', 'timed_out', 'upstream_failed', 'retrying', 'running', 'ready', 'pending']
  const badges = order.filter(state => counts[state])

  return (
    <div className="hidden shrink-0 items-center gap-3 md:flex">
      {badges.map(state => (
        <span key={state} className="inline-flex items-center gap-1.5 ap-meta">
          <StateDot state={state} />
          <span style={{ color: 'var(--text)' }}>{counts[state]}</span> {state.replace('_', ' ')}
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

  // React Flow takes plain colour strings (SVG attributes): read the tokens off
  // :root and re-read when the theme flips.
  const theme = useContext(ThemeContext)?.theme ?? 'light'
  const { canvasDotColor, miniColors } = useMemo(() => {
    const cs = getComputedStyle(document.documentElement)
    const v = (name: string) => cs.getPropertyValue(name).trim() || 'currentColor'
    const subtle = v('--text-subtle')
    return {
      canvasDotColor: v('--frame-line'),
      miniColors: {
        pending: subtle, ready: v('--accent'), running: v('--warning'), retrying: v('--warning'),
        success: v('--success'), failed: v('--danger'), timed_out: v('--danger'),
        upstream_failed: v('--warning'), cancelled: subtle,
      } as Record<string, string>,
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [theme])

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
      <div className="flex shrink-0 items-center gap-2 overflow-x-auto border-b border-border bg-surface px-3 py-2 sm:gap-3 sm:px-4">
        <div className="shrink-0">
          <span className="t-label">Run</span>
          <span className="ml-2 font-mono text-xs text-muted">{runId?.slice(0, 8)}</span>
        </div>
        {flowRun && <StateBadge state={flowRun.state} />}
        {flowRun?.env && (
          <Tag caps={false} className="shrink-0" title="Environment this run targeted">
            <Layers size={10} aria-hidden="true" />
            {flowRun.env}
          </Tag>
        )}
        {!flowRun && !error && <Loader2 size={14} className="shrink-0 animate-spin text-muted" aria-label="Loading run" />}
        {error && (
          <div className="flex shrink-0 items-center gap-1.5 text-xs" role="alert" style={{ color: 'var(--danger-fg)' }}>
            <AlertCircle size={14} aria-hidden="true" />
            <span className="hidden sm:inline">{error}</span>
          </div>
        )}
        {/* Task state summary counts */}
        <TaskRunsSummary taskRuns={taskRuns} />
        {/* Timing — hidden on mobile to save space */}
        {flowRun?.started_at && (
          <span className="ap-meta ml-1 hidden shrink-0 sm:inline">
            Started {new Date(flowRun.started_at).toLocaleTimeString()}
          </span>
        )}
        <div className="flex-1 min-w-0" />
        {!TERMINAL_STATES.has(flowRun?.state) && (
          <span className="ap-meta flex shrink-0 items-center gap-1" role="status">
            <Loader2 size={12} className="animate-spin" aria-hidden="true" />
            <span className="hidden sm:inline">Live</span>
          </span>
        )}
        {onClose && (
          <Button variant="ghost" size="icon-sm" onClick={onClose} title="Close run view" aria-label="Close run view">
            <X size={14} aria-hidden="true" />
          </Button>
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
            className="fn-canvas"
            panOnDrag
            panOnScroll={false}
            zoomOnScroll
            zoomOnPinch
            preventScrolling
            defaultEdgeOptions={{
              type: 'smoothstep',
              style: { strokeWidth: 1.5 },
            }}
          >
            <Background
              variant={BackgroundVariant.Dots}
              gap={16}
              size={1}
              color={canvasDotColor}
            />
            <Controls showInteractive={false} />
            {/* MiniMap: hidden on mobile. Fill follows the task's run state. */}
            <div className="hidden md:block">
              <MiniMap
                nodeColor={(node) => {
                  const state = node.data?.taskRun?.state
                  return miniColors[state] ?? miniColors.pending
                }}
                nodeStrokeWidth={0}
                maskColor="rgba(11,12,14,0.06)"
                pannable
                zoomable
                ariaLabel="Run minimap"
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
