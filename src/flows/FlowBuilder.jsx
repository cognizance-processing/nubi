/**
 * FlowBuilder.jsx — React Flow DAG builder for Nubi flows.
 *
 * Features:
 *   - ReactFlow canvas with MiniMap + Controls + dotted grid background
 *   - Floating node palette (add query / python / agent / noop tasks)
 *   - Drag-to-connect edges (creates needs relationships)
 *   - Click to select → opens NodeInspector drawer
 *   - Toolbar: Validate, Save (create/update), Run
 *   - Calls validateFlow, createFlow/updateFlow, runFlow from flows.js
 *   - Fully responsive: mobile bottom-sheet for palette + inspector
 *
 * Props:
 *   flow        {object|null}   — existing flow row (null for new)
 *   spec        {object}        — current FlowSpec (controlled)
 *   onSpecChange {Function}     — called with updated spec on every edit
 *   onSaved     {Function}      — called with saved flow row after create/update
 *   onRun       {Function}      — called with { flowRun, runId } after triggering
 */

import 'reactflow/dist/style.css'

import { useState, useCallback, useMemo, useRef, useEffect } from 'react'
import ReactFlow, {
  Background,
  BackgroundVariant,
  Controls,
  MiniMap,
  addEdge,
  useNodesState,
  useEdgesState,
  Panel,
} from 'reactflow'

import {
  Play,
  Save,
  ShieldCheck,
  Plus,
  AlertCircle,
  CheckCircle2,
  Loader2,
  Code2,
  Database,
  Bot,
  Zap,
  X,
  Layers,
  SlidersHorizontal,
  ChevronDown,
} from 'lucide-react'

import { validateFlow, createFlow, updateFlow, runFlow } from '../lib/flows.js'
import { specToGraph, graphToSpec } from './specGraph.js'
import TaskNode from './nodes/TaskNode.jsx'
import NodeInspector from './NodeInspector.jsx'

// ---------------------------------------------------------------------------
// Node types registration (must be stable — defined outside component)
// ---------------------------------------------------------------------------

const NODE_TYPES = { taskNode: TaskNode }

// ---------------------------------------------------------------------------
// Palette item definitions
// ---------------------------------------------------------------------------

const PALETTE_ITEMS = [
  { kind: 'query',  label: 'Query',  Icon: Database, color: 'text-blue-500',    defaultConfig: { query_id: '' } },
  { kind: 'python', label: 'Python', Icon: Code2,    color: 'text-violet-500',  defaultConfig: { code: '# Write your task code here\nresult = {}' } },
  { kind: 'agent',  label: 'Agent',  Icon: Bot,      color: 'text-emerald-500', defaultConfig: { prompt: '', max_steps: 4 } },
  { kind: 'noop',   label: 'Noop',   Icon: Zap,      color: 'text-slate-400',   defaultConfig: {} },
]

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

let _nodeCounter = 0
function genKey(kind) {
  _nodeCounter += 1
  return `${kind}_${_nodeCounter}`
}

function makeTaskNode(kind, position, defaultConfig) {
  const key = genKey(kind)
  return {
    id: key,
    type: 'taskNode',
    position,
    data: {
      task: {
        key,
        kind,
        needs: [],
        config: defaultConfig,
        retries: 0,
        retry_backoff_s: 30,
        timeout_s: 60,
        cache_ttl_s: 0,
        ui: { x: Math.round(position.x), y: Math.round(position.y) },
      },
      taskRun: null,
    },
  }
}

// ---------------------------------------------------------------------------
// ValidationBanner
// ---------------------------------------------------------------------------

function ValidationBanner({ issues, onClose }) {
  if (!issues) return null
  const valid = issues.length === 0
  return (
    <div
      className={[
        'flex items-start gap-2 px-4 py-2.5 text-xs border-b',
        valid
          ? 'bg-green-500/5 border-green-500/20 text-green-700 dark:text-green-400'
          : 'bg-rose-500/5 border-rose-500/20 text-rose-700 dark:text-rose-400',
      ].join(' ')}
    >
      {valid
        ? <CheckCircle2 size={14} className="shrink-0 mt-0.5" />
        : <AlertCircle size={14} className="shrink-0 mt-0.5" />
      }
      <div className="flex-1">
        {valid
          ? 'Flow spec is valid.'
          : <><strong>Validation issues:</strong><ul className="mt-1 space-y-0.5 list-disc list-inside">{issues.map((i, idx) => <li key={idx}>{i}</li>)}</ul></>
        }
      </div>
      <button onClick={onClose} className="shrink-0 opacity-60 hover:opacity-100 transition-opacity">
        <X size={13} />
      </button>
    </div>
  )
}

// ---------------------------------------------------------------------------
// NodePaletteDesktop — floating Panel inside ReactFlow (desktop only)
// ---------------------------------------------------------------------------

function NodePaletteDesktop({ onAdd }) {
  return (
    <Panel position="top-left">
      <div className="bg-surface border border-border rounded-xl shadow-lg p-2 space-y-1 min-w-[130px]">
        <p className="text-[10px] font-semibold text-muted/70 uppercase tracking-widest px-1 pb-1">Add task</p>
        {PALETTE_ITEMS.map((item) => {
          const ItemIcon = item.Icon
          return (
            <button
              key={item.kind}
              onClick={() => onAdd(item.kind, item.defaultConfig)}
              className="w-full flex items-center gap-2.5 px-2.5 py-2 rounded-lg text-xs font-medium text-fg hover:bg-surface-2 transition-colors group"
            >
              <ItemIcon size={14} className={[item.color, 'shrink-0'].join(' ')} />
              <span className="flex-1 text-left">{item.label}</span>
            </button>
          )
        })}
      </div>
    </Panel>
  )
}

// ---------------------------------------------------------------------------
// MobileBottomSheet — shared bottom sheet wrapper
// ---------------------------------------------------------------------------

function MobileBottomSheet({ open, onClose, title, children }) {
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
          'max-h-[80dvh]',
          open ? 'translate-y-0' : 'translate-y-full',
        ].join(' ')}
        role="dialog"
        aria-modal="true"
        aria-label={title}
      >
        {/* Drag handle */}
        <div className="shrink-0 flex justify-center pt-3 pb-2">
          <div className="w-10 h-1 rounded-full bg-border" />
        </div>
        {/* Header */}
        <div className="shrink-0 flex items-center justify-between px-4 pb-2 border-b border-border">
          <span className="text-sm font-semibold text-fg">{title}</span>
          <button
            onClick={onClose}
            className="w-9 h-9 flex items-center justify-center rounded-lg text-muted hover:text-fg hover:bg-surface-2 transition-colors"
            aria-label="Close"
          >
            <X size={16} />
          </button>
        </div>
        {/* Scrollable content */}
        <div className="flex-1 overflow-y-auto">
          {children}
        </div>
      </div>
    </>
  )
}

// ---------------------------------------------------------------------------
// MobilePaletteSheet — add-task sheet for mobile
// ---------------------------------------------------------------------------

function MobilePaletteSheet({ open, onClose, onAdd }) {
  return (
    <MobileBottomSheet open={open} onClose={onClose} title="Add task">
      <div className="px-4 py-3 space-y-1">
        {PALETTE_ITEMS.map((item) => {
          const ItemIcon = item.Icon
          return (
            <button
              key={item.kind}
              onClick={() => { onAdd(item.kind, item.defaultConfig); onClose() }}
              className="w-full flex items-center gap-3 px-3 py-3.5 rounded-xl text-sm font-medium text-fg hover:bg-surface-2 active:bg-surface-2 transition-colors min-h-[52px]"
            >
              <ItemIcon size={18} className={[item.color, 'shrink-0'].join(' ')} />
              <span className="flex-1 text-left">{item.label}</span>
            </button>
          )
        })}
      </div>
    </MobileBottomSheet>
  )
}

// ---------------------------------------------------------------------------
// MobileInspectorSheet — node inspector sheet for mobile
// ---------------------------------------------------------------------------

function MobileInspectorSheet({ open, onClose, task, onChange }) {
  return (
    <MobileBottomSheet open={open} onClose={onClose} title="Task inspector">
      {task && (
        <div className="pb-8">
          <NodeInspector
            task={task}
            onChange={onChange}
            onClose={onClose}
          />
        </div>
      )}
    </MobileBottomSheet>
  )
}

// ---------------------------------------------------------------------------
// FlowBuilder
// ---------------------------------------------------------------------------

export default function FlowBuilder({ flow, spec, onSpecChange, onSaved, onRun }) {
  // ── React Flow state ─────────────────────────────────────────────────────
  const [nodes, setNodes, onNodesChange] = useNodesState(
    () => specToGraph(spec).nodes
  )
  const [edges, setEdges, onEdgesChange] = useEdgesState(
    () => specToGraph(spec).edges
  )
  const reactFlowWrapper = useRef(null)
  const [rfInstance, setRfInstance] = useState(null)

  // ── Inspector ─────────────────────────────────────────────────────────────
  const [selectedNodeId, setSelectedNodeId] = useState(null)

  // ── Mobile sheet states ───────────────────────────────────────────────────
  const [mobilePaletteOpen, setMobilePaletteOpen] = useState(false)
  const [mobileInspectorOpen, setMobileInspectorOpen] = useState(false)

  // ── Detect mobile ─────────────────────────────────────────────────────────
  // We use a simple state tracking via window width (updates on resize)
  const [isMobile, setIsMobile] = useState(() => typeof window !== 'undefined' && window.innerWidth < 768)
  useEffect(() => {
    const mq = window.matchMedia('(max-width: 767px)')
    const handler = (e) => setIsMobile(e.matches)
    mq.addEventListener('change', handler)
    setIsMobile(mq.matches)
    return () => mq.removeEventListener('change', handler)
  }, [])

  // When a node is selected on mobile, open the inspector sheet
  useEffect(() => {
    if (selectedNodeId && isMobile) {
      setMobileInspectorOpen(true)
    }
  }, [selectedNodeId, isMobile])

  // ── UI state ──────────────────────────────────────────────────────────────
  const [validationIssues, setValidationIssues] = useState(null)
  const [saving, setSaving] = useState(false)
  const [running, setRunning] = useState(false)
  const [validating, setValidating] = useState(false)
  const [saveError, setSaveError] = useState(null)
  const [runError, setRunError] = useState(null)

  // ── Derive meta from spec ─────────────────────────────────────────────────
  const meta = useMemo(() => ({
    version: spec?.version ?? 1,
    name: spec?.name ?? 'untitled',
    params: spec?.params ?? [],
  }), [spec])

  // ── Build current spec from graph ─────────────────────────────────────────
  const buildSpec = useCallback(() => {
    return graphToSpec(nodes, edges, meta)
  }, [nodes, edges, meta])

  // ── Propagate spec change upward ──────────────────────────────────────────
  const notifySpecChange = useCallback((ns, es) => {
    const s = graphToSpec(ns ?? nodes, es ?? edges, meta)
    onSpecChange?.(s)
  }, [nodes, edges, meta, onSpecChange])

  // ── Node change handler (update task data when inspector edits) ───────────
  const handleTaskChange = useCallback((updatedTask) => {
    setNodes(nds => {
      const next = nds.map(n => {
        if (n.id !== (selectedNodeId)) return n
        const newId = updatedTask.key
        return {
          ...n,
          id: newId,
          data: { ...n.data, task: updatedTask },
        }
      })
      notifySpecChange(next, edges)
      return next
    })
  }, [selectedNodeId, edges, notifySpecChange, setNodes])

  // ── Edge connection ────────────────────────────────────────────────────────
  const onConnect = useCallback((params) => {
    setEdges(eds => {
      const next = addEdge({
        ...params,
        type: 'smoothstep',
        animated: false,
        style: { strokeWidth: 1.5 },
      }, eds)
      setNodes(nds => {
        const targetNeeds = next
          .filter(e => e.target === params.target)
          .map(e => e.source)
        const updated = nds.map(n =>
          n.id === params.target
            ? { ...n, data: { ...n.data, task: { ...n.data.task, needs: targetNeeds } } }
            : n
        )
        notifySpecChange(updated, next)
        return updated
      })
      return next
    })
  }, [setEdges, setNodes, notifySpecChange])

  // ── After edges change, sync needs onto all nodes ─────────────────────────
  const onEdgesChangeWrapped = useCallback((changes) => {
    onEdgesChange(changes)
    setTimeout(() => {
      setEdges(currentEdges => {
        setNodes(currentNodes => {
          const needsMap = new Map(currentNodes.map(n => [n.id, []]))
          for (const e of currentEdges) {
            if (needsMap.has(e.target)) needsMap.get(e.target).push(e.source)
          }
          const updated = currentNodes.map(n => ({
            ...n,
            data: {
              ...n.data,
              task: { ...n.data.task, needs: needsMap.get(n.id) ?? [] },
            },
          }))
          notifySpecChange(updated, currentEdges)
          return updated
        })
        return currentEdges
      })
    }, 0)
  }, [onEdgesChange, setEdges, setNodes, notifySpecChange])

  // ── Add node from palette ─────────────────────────────────────────────────
  const handleAddNode = useCallback((kind, defaultConfig) => {
    const centerX = rfInstance
      ? rfInstance.getViewport().x / -rfInstance.getViewport().zoom + 300
      : 300
    const centerY = rfInstance
      ? rfInstance.getViewport().y / -rfInstance.getViewport().zoom + 200
      : 200

    const newNode = makeTaskNode(kind, { x: centerX, y: centerY }, defaultConfig)
    setNodes(nds => {
      const next = [...nds, newNode]
      notifySpecChange(next, edges)
      return next
    })
    setSelectedNodeId(newNode.id)
  }, [rfInstance, setNodes, edges, notifySpecChange])

  // ── Node click → inspector ─────────────────────────────────────────────────
  const onNodeClick = useCallback((_evt, node) => {
    setSelectedNodeId(node.id)
  }, [])

  const onPaneClick = useCallback(() => {
    setSelectedNodeId(null)
    if (isMobile) setMobileInspectorOpen(false)
  }, [isMobile])

  // Selected node's task
  const selectedNode = nodes.find(n => n.id === selectedNodeId)
  const selectedTask = selectedNode?.data?.task ?? null

  // ── Validate ───────────────────────────────────────────────────────────────
  const handleValidate = useCallback(async () => {
    setValidating(true)
    setValidationIssues(null)
    const s = buildSpec()
    const result = await validateFlow(s)
    setValidationIssues(result?.issues ?? [])
    setValidating(false)
  }, [buildSpec])

  // ── Save ───────────────────────────────────────────────────────────────────
  const handleSave = useCallback(async () => {
    setSaving(true)
    setSaveError(null)
    const s = buildSpec()
    let saved
    if (flow?.id) {
      saved = await updateFlow(flow.id, { name: s.name, spec: s })
    } else {
      saved = await createFlow(s.name, s)
    }
    setSaving(false)
    if (!saved) {
      setSaveError('Save failed — check the console for details.')
    } else {
      onSaved?.(saved)
    }
  }, [buildSpec, flow, onSaved])

  // ── Run ────────────────────────────────────────────────────────────────────
  const handleRun = useCallback(async () => {
    if (!flow?.id) {
      setRunError('Save the flow first before running.')
      return
    }
    setRunning(true)
    setRunError(null)
    const result = await runFlow(flow.id, {})
    setRunning(false)
    if (!result) {
      setRunError('Run failed — check the console for details.')
    } else {
      onRun?.({ flowRun: result, runId: result.id })
    }
  }, [flow, onRun])

  // ── Node position change → sync ui coords ─────────────────────────────────
  const onNodesChangeWrapped = useCallback((changes) => {
    onNodesChange(changes)
    const posChanges = changes.filter(c => c.type === 'position' && !c.dragging)
    if (posChanges.length > 0) {
      setNodes(nds => {
        const updated = nds.map(n => {
          const ch = posChanges.find(c => c.id === n.id)
          if (!ch) return n
          return {
            ...n,
            data: {
              ...n.data,
              task: { ...n.data.task, ui: { x: Math.round(n.position.x), y: Math.round(n.position.y) } },
            },
          }
        })
        notifySpecChange(updated, edges)
        return updated
      })
    }
  }, [onNodesChange, setNodes, edges, notifySpecChange])

  return (
    <div className="flex flex-col h-full overflow-hidden">

      {/* ── Toolbar ──────────────────────────────────────────────────────── */}
      <div className="shrink-0 flex items-center gap-1.5 sm:gap-2 px-2 sm:px-4 py-2 border-b border-border bg-surface-2/40 overflow-x-auto">
        {/* Flow name — wider on desktop */}
        <input
          type="text"
          value={spec?.name ?? ''}
          onChange={e => onSpecChange?.({ ...spec, name: e.target.value })}
          placeholder="Flow name…"
          className="h-9 px-2.5 text-sm font-medium border border-border rounded-lg bg-surface text-fg placeholder:text-muted/50 focus:outline-none focus:ring-2 focus:ring-ring/60 w-28 sm:w-44 shrink-0"
        />

        <div className="flex-1 min-w-0" />

        {/* Validate — hidden label on xs */}
        <button
          onClick={handleValidate}
          disabled={validating}
          className="flex items-center gap-1.5 px-2 sm:px-3 h-9 text-xs font-medium rounded-lg border border-border bg-surface text-fg hover:bg-surface-2 disabled:opacity-50 transition-colors shrink-0 min-h-[36px]"
          title="Validate flow"
        >
          {validating
            ? <Loader2 size={13} className="animate-spin" />
            : <ShieldCheck size={13} />
          }
          <span className="hidden sm:inline">Validate</span>
        </button>

        {/* Save */}
        <button
          onClick={handleSave}
          disabled={saving}
          className="flex items-center gap-1.5 px-2 sm:px-3 h-9 text-xs font-medium rounded-lg border border-border bg-surface text-fg hover:bg-surface-2 disabled:opacity-50 transition-colors shrink-0 min-h-[36px]"
          title="Save flow"
        >
          {saving
            ? <Loader2 size={13} className="animate-spin" />
            : <Save size={13} />
          }
          <span className="hidden sm:inline">Save</span>
        </button>

        {/* Run */}
        <button
          onClick={handleRun}
          disabled={running || !flow?.id}
          title={!flow?.id ? 'Save the flow first' : 'Run flow'}
          className="flex items-center gap-1.5 px-2 sm:px-3 h-9 text-xs font-medium rounded-lg bg-primary text-primary-fg hover:opacity-90 disabled:opacity-50 disabled:cursor-not-allowed transition-all shrink-0 min-h-[36px]"
        >
          {running
            ? <Loader2 size={13} className="animate-spin" />
            : <Play size={13} />
          }
          <span className="hidden sm:inline">Run</span>
        </button>

        {/* Mobile: Add node button */}
        <button
          onClick={() => setMobilePaletteOpen(true)}
          className="md:hidden flex items-center gap-1.5 px-2 h-9 text-xs font-medium rounded-lg border border-dashed border-border text-muted hover:text-fg hover:bg-surface-2 transition-colors shrink-0 min-h-[36px]"
          title="Add task"
          aria-label="Add task"
        >
          <Plus size={13} />
        </button>
      </div>

      {/* Error banners */}
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

      {/* Validation banner */}
      <ValidationBanner
        issues={validationIssues}
        onClose={() => setValidationIssues(null)}
      />

      {/* ── Canvas + inspector row ────────────────────────────────────────── */}
      <div className="flex flex-1 min-h-0 overflow-hidden">

        {/* React Flow canvas */}
        <div className="flex-1 relative" ref={reactFlowWrapper}>
          <ReactFlow
            nodes={nodes}
            edges={edges}
            nodeTypes={NODE_TYPES}
            onNodesChange={onNodesChangeWrapped}
            onEdgesChange={onEdgesChangeWrapped}
            onConnect={onConnect}
            onNodeClick={onNodeClick}
            onPaneClick={onPaneClick}
            onInit={setRfInstance}
            fitView
            fitViewOptions={{ padding: 0.25 }}
            deleteKeyCode="Delete"
            className="bg-bg"
            // Touch / pan / zoom support
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
            {/* MiniMap: hidden on small screens to save space */}
            <div className="hidden md:block">
              <MiniMap
                className="!border !border-border !rounded-xl overflow-hidden !shadow-md"
                nodeColor={(node) => {
                  const kind = node.data?.task?.kind ?? 'noop'
                  return {
                    query: '#3b82f6',
                    python: '#8b5cf6',
                    agent: '#10b981',
                    noop: '#94a3b8',
                  }[kind] ?? '#94a3b8'
                }}
                maskColor="rgba(0,0,0,0.05)"
              />
            </div>
            {/* Node palette: desktop floating panel */}
            <div className="hidden md:block">
              <NodePaletteDesktop onAdd={handleAddNode} />
            </div>
          </ReactFlow>

          {/* Mobile: inspector trigger button (shown when node selected) */}
          {selectedTask && isMobile && !mobileInspectorOpen && (
            <button
              onClick={() => setMobileInspectorOpen(true)}
              className="absolute bottom-20 right-3 z-10 flex items-center gap-2 px-3 py-2.5 rounded-xl bg-surface border border-border shadow-lg text-xs font-medium text-fg min-h-[44px]"
              aria-label="Open task inspector"
            >
              <SlidersHorizontal size={14} className="text-primary" />
              Inspect: {selectedTask.key}
            </button>
          )}
        </div>

        {/* Inspector drawer — desktop inline */}
        {selectedTask && !isMobile && (
          <div className="shrink-0 w-72 xl:w-80 overflow-hidden">
            <NodeInspector
              task={selectedTask}
              onChange={handleTaskChange}
              onClose={() => setSelectedNodeId(null)}
            />
          </div>
        )}
      </div>

      {/* ── Mobile sheets ────────────────────────────────────────────────── */}
      <MobilePaletteSheet
        open={mobilePaletteOpen}
        onClose={() => setMobilePaletteOpen(false)}
        onAdd={handleAddNode}
      />

      <MobileInspectorSheet
        open={mobileInspectorOpen && !!selectedTask}
        onClose={() => { setMobileInspectorOpen(false); setSelectedNodeId(null) }}
        task={selectedTask}
        onChange={handleTaskChange}
      />
    </div>
  )
}
