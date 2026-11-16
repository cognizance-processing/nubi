/**
 * FlowBuilder.jsx — React Flow DAG builder for Nubi flows.
 *
 * Features:
 *   - ReactFlow canvas with MiniMap + Controls + dotted grid background
 *   - Floating node palette (add query / python / agent / noop tasks)
 *   - Drag-to-connect edges (creates needs relationships)
 *   - Click to select → opens NodeInspector drawer
 *   - Canvas / Notebook view toggle (the only in-component toolbar)
 *   - Fully responsive: mobile bottom-sheet for palette + inspector
 *
 * The flow name + Validate/Save/Run/Code actions, the Builder/Runs switcher
 * and the notebook controls (add-cell / Run all) live in the app top bar —
 * FlowsPage portals them there (mirrors the dashboard editor). This
 * component only owns the canvas, the notebook body, and the code panel
 * (whose visibility is controlled by the `codeOpen` prop). Saving (manual +
 * autosave) is owned by FlowsPage.
 *
 * Props:
 *   flow           {object|null}  — existing flow row (null for new)
 *   spec           {object}       — current FlowSpec (controlled)
 *   onSpecChange   {Function}     — called with updated spec on every edit
 *   onRun          {Function}     — passed through to NotebookView
 *   env            {string}       — run environment; passed to NotebookView
 *   codeOpen       {boolean}      — whether the Python code panel is shown
 *   onCodeClose    {Function}     — called to dismiss the code panel
 */

import 'reactflow/dist/style.css'

import { useState, useCallback, useMemo, useRef, useEffect, useContext, forwardRef, useImperativeHandle } from 'react'
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
  Plus,
  X,
  SlidersHorizontal,
} from 'lucide-react'

import Button from '../components/ui/Button.jsx'
import EmptyState from '../components/ui/EmptyState.jsx'
import { EmptyFlows } from '../components/illustrations'
import '../components/app/appPatterns.css'

import { specToGraph, graphToSpec } from './specGraph.js'
import NotebookView from './NotebookView.jsx'
import FlowCodeView from './FlowCodeView.jsx'
import TaskNode from './nodes/TaskNode.jsx'
import MapGroupNode from './nodes/MapGroupNode.jsx'
import BranchNode from './nodes/BranchNode.jsx'
import NodeInspector from './NodeInspector.jsx'
import CodePanel from './CodePanel.jsx'
import { AddTaskPanel } from './AddTaskPanel.jsx'
import { ThemeContext } from '../contexts/ThemeContext.jsx'

// ---------------------------------------------------------------------------
// Node types registration (must be stable — defined outside component)
// ---------------------------------------------------------------------------

const NODE_TYPES = {
  taskNode:   TaskNode,
  mapNode:    MapGroupNode,
  branchNode: BranchNode,
}


// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

let _nodeCounter = 0
function genKey(kind) {
  _nodeCounter += 1
  return `${kind}_${_nodeCounter}`
}

// Map kind → React Flow node type string.
const KIND_TO_NODE_TYPE = {
  map:    'mapNode',
  branch: 'branchNode',
}

function makeTaskNode(kind: string, position: { x: number; y: number }, defaultConfig: Record<string, any>, cellType?: string) {
  const key = genKey(kind)
  const nodeType = KIND_TO_NODE_TYPE[kind] ?? 'taskNode'
  const baseData: Record<string, any> = {
    task: {
      key,
      kind,
      // Stamp the user-facing cell type ('sql' | 'python' | 'markdown') so the
      // notebook/canvas render the cell correctly. Falls back to inferring from
      // kind for legacy/programmatic callers that don't pass one.
      cell_type: cellType ?? (kind === 'python' ? 'python' : kind === 'noop' ? 'markdown' : 'sql'),
      needs: [],
      config: defaultConfig,
      retries: 0,
      retry_backoff_s: 30,
      timeout_s: 60,
      cache_ttl_s: 0,
      ui: { x: Math.round(position.x), y: Math.round(position.y) },
    },
    taskRun: null,
  }
  // map nodes need expanded/bodySpec fields for MapGroupNode
  if (kind === 'map') {
    baseData.expanded = false
    baseData.bodySpec = defaultConfig.body ?? []
  }
  return {
    id: key,
    type: nodeType,
    position,
    data: baseData,
  }
}

// ---------------------------------------------------------------------------
// NodePaletteDesktop — floating Panel inside ReactFlow (desktop only)
// ---------------------------------------------------------------------------

// (The desktop "Add task" palette now lives in the FlowsPage shared RHS sidebar;
// see src/flows/AddTaskPanel.jsx and FlowBuilder's imperative `addNode` handle.)

// ---------------------------------------------------------------------------
// MobileBottomSheet — shared bottom sheet wrapper
// ---------------------------------------------------------------------------

function MobileBottomSheet({ open, onClose, title, children }) {
  return (
    <>
      {open && <div className="ap-backdrop" onClick={onClose} aria-hidden="true" />}
      <div
        className="fixed bottom-0 left-0 right-0 flex max-h-[80dvh] flex-col border-t-2 border-ink bg-surface"
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
        aria-label={title}
      >
        {/* Header */}
        <div className="flex h-12 shrink-0 items-center justify-between border-b border-border pl-4 pr-2">
          <span className="t-label">{title}</span>
          <Button variant="ghost" size="icon" onClick={onClose} aria-label="Close">
            <X size={16} aria-hidden="true" />
          </Button>
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
      <div className="px-2 py-1">
        <AddTaskPanel onAdd={(kind, config, cellType) => { onAdd(kind, config, cellType); onClose() }} />
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
            showHeader={false}
          />
        </div>
      )}
    </MobileBottomSheet>
  )
}

// ---------------------------------------------------------------------------
// FlowBuilder
// ---------------------------------------------------------------------------

export interface FlowBuilderHandle {
  addNode: (kind: string, defaultConfig?: Record<string, any>, cellType?: string) => void
  updateSelectedTask: (updated: Record<string, any>) => void
  clearSelection: () => void
  setView: (mode: 'canvas' | 'notebook' | 'code') => void
  runAll: () => void
  addCell: (cellType?: string) => void
}

interface FlowBuilderProps {
  flow: Record<string, any> | null
  spec: Record<string, any>
  onSpecChange: (spec: Record<string, any>) => void
  onRun?: (...args: any[]) => void
  env?: string
  onSelectedTaskChange?: (task: Record<string, any> | null) => void
  codeOpen?: boolean
  onCodeClose?: () => void
  onViewModeChange?: (mode: 'canvas' | 'notebook' | 'code') => void
}

const FlowBuilder = forwardRef<FlowBuilderHandle, FlowBuilderProps>(function FlowBuilder({ flow, spec, onSpecChange, onRun, env = 'prod', onSelectedTaskChange, codeOpen = false, onCodeClose, onViewModeChange }, ref) {
  // ── View mode: 'canvas' | 'notebook' ─────────────────────────────────────
  // Initialise from spec.view if present; fall back to 'canvas'.
  const [viewMode, setViewMode] = useState<'canvas' | 'notebook' | 'code'>(() => spec?.view === 'notebook' ? 'notebook' : 'canvas')

  // Report the current view up so the app top bar (FlowsPage) can render the
  // Canvas/Notebook switcher. Fires on mount + whenever the view changes.
  useEffect(() => {
    onViewModeChange?.(viewMode)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [viewMode])

  // ── React Flow state ─────────────────────────────────────────────────────
  // useNodesState/useEdgesState forward straight into React's useState, which
  // honours a function argument as a lazy initializer — reactflow's types just
  // don't advertise that, so the lazy initializer is cast past them here.
  const [nodes, setNodes, onNodesChange] = useNodesState(
    (() => specToGraph(spec).nodes) as unknown as any[]
  )
  const [edges, setEdges, onEdgesChange] = useEdgesState(
    (() => specToGraph(spec).edges) as unknown as any[]
  )
  const reactFlowWrapper = useRef(null)
  const [rfInstance, setRfInstance] = useState(null)

  // Ref onto NotebookView so top-bar actions (Run all / add cell) can be
  // forwarded through this component's own imperative handle.
  const notebookRef = useRef<import('./NotebookView.jsx').NotebookViewHandle>(null)

  // ── Inspector ─────────────────────────────────────────────────────────────
  const [selectedNodeId, setSelectedNodeId] = useState(null)

  // ── Mobile sheet states ───────────────────────────────────────────────────
  const [mobilePaletteOpen, setMobilePaletteOpen] = useState(false)
  const [mobileInspectorOpen, setMobileInspectorOpen] = useState(false)

  // ── Detect mobile ─────────────────────────────────────────────────────────
  // Initialise from matchMedia so the useState lazy initialiser already has the
  // correct value; the effect only subscribes to future changes.
  // React Flow takes plain colour strings (SVG attributes), so read the design
  // tokens off :root and re-read when the theme flips.
  const theme = useContext(ThemeContext)?.theme ?? 'light'
  const { canvasDotColor, minimapInk, minimapAccent } = useMemo(() => {
    const cs = getComputedStyle(document.documentElement)
    const v = (name: string) => cs.getPropertyValue(name).trim() || 'currentColor'
    return { canvasDotColor: v('--frame-line'), minimapInk: v('--text-subtle'), minimapAccent: v('--accent') }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [theme])

  const [isMobile, setIsMobile] = useState(
    () => typeof window !== 'undefined' && window.matchMedia('(max-width: 767px)').matches
  )
  useEffect(() => {
    const mq = window.matchMedia('(max-width: 767px)')
    const handler = (e) => setIsMobile(e.matches)
    mq.addEventListener('change', handler)
    return () => mq.removeEventListener('change', handler)
  }, [])

  // When a node is selected on mobile, open the inspector sheet.
  // Derive this in the click handler rather than an effect to avoid the
  // synchronous setState-in-effect lint rule.
  // (The actual open call is inside onNodeClick below.)

  // ── Derive meta from spec ─────────────────────────────────────────────────
  // Everything except the tasks (which the canvas rebuilds from nodes/edges),
  // so top-level keys like runtime_config and view survive a canvas save.
  const meta = useMemo(() => {
    const { tasks: _tasks, ...rest } = spec ?? {}
    return {
      ...rest,
      version: spec?.version ?? 1,
      name: spec?.name ?? 'untitled',
      params: spec?.params ?? [],
    }
  }, [spec])

  // ── Build current spec from graph ─────────────────────────────────────────
  const buildSpec = useCallback(() => {
    return graphToSpec(nodes, edges, meta)
  }, [nodes, edges, meta])

  // ── Propagate spec change upward ──────────────────────────────────────────
  const notifySpecChange = useCallback((ns, es) => {
    const s = graphToSpec(ns ?? nodes, es ?? edges, meta)
    onSpecChange?.(s)
  }, [nodes, edges, meta, onSpecChange])

  // ── View switch (canvas ↔ notebook) ───────────────────────────────────────
  // Both views edit the SAME FlowSpec but hold their working state differently
  // (canvas → nodes/edges; notebook → spec.tasks). Switching must hand the live
  // state across or tasks are lost:
  //   • leaving canvas → flush the graph into the spec (buildSpec)
  //   • entering canvas → rebuild nodes/edges from the (notebook-edited) spec
  const handleViewChange = useCallback((mode) => {
    if (mode === viewMode) return
    if (viewMode === 'canvas') {
      const s = buildSpec()
      onSpecChange?.({ ...s, view: mode })
    } else {
      const g = specToGraph(spec)
      setNodes(g.nodes)
      setEdges(g.edges)
      onSpecChange?.({ ...spec, view: mode })
    }
    setViewMode(mode)
  }, [viewMode, buildSpec, spec, onSpecChange, setNodes, setEdges])

  // ── Node change handler (update task data when inspector edits) ───────────
  const handleTaskChange = useCallback((updatedTask) => {
    setNodes(nds => {
      const next = nds.map(n => {
        if (n.id !== selectedNodeId) return n
        const newId = updatedTask.key
        // Determine the correct node type in case kind was changed in inspector.
        const newType = KIND_TO_NODE_TYPE[updatedTask.kind] ?? 'taskNode'
        const updatedData = { ...n.data, task: updatedTask }
        // Keep bodySpec in sync for map nodes (used by MapGroupNode).
        if (updatedTask.kind === 'map') {
          updatedData.bodySpec = updatedTask.config?.body ?? []
        }
        return {
          ...n,
          id: newId,
          type: newType,
          data: updatedData,
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
          // Skip visual-only edges (branch routing + inferred SQL deps) so they
          // are never written into needs — mirrors graphToSpec / specToGraph.
          .filter(e => !(e.data != null && ('branchCondIndex' in e.data || e.data.inferred)))
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
            // Skip visual-only edges — they must never become needs:
            //  • branch-labeled routing edges (data.branchCondIndex); authoritative
            //    routing lives in config.conditions[i].next.
            //  • inferred SQL dependency edges (data.inferred); re-derived from
            //    config.sql on every specToGraph, never persisted.
            if (e.data != null && ('branchCondIndex' in e.data || e.data.inferred)) continue
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
  const handleAddNode = useCallback((kind, defaultConfig, cellType) => {
    const centerX = rfInstance
      ? rfInstance.getViewport().x / -rfInstance.getViewport().zoom + 300
      : 300
    const centerY = rfInstance
      ? rfInstance.getViewport().y / -rfInstance.getViewport().zoom + 200
      : 200

    const newNode = makeTaskNode(kind, { x: centerX, y: centerY }, defaultConfig, cellType)
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
    if (isMobile) setMobileInspectorOpen(true)
  }, [isMobile])

  const onPaneClick = useCallback(() => {
    setSelectedNodeId(null)
    if (isMobile) setMobileInspectorOpen(false)
  }, [isMobile])

  // Selected node's task
  const selectedNode = nodes.find(n => n.id === selectedNodeId)
  const selectedTask = selectedNode?.data?.task ?? null

  // Expose an imperative API + report the current selection upward, so the
  // page-level shared RHS sidebar (FlowsPage) can host the "Add task" palette
  // and the task inspector while this component keeps the React Flow state.
  useImperativeHandle(ref, () => ({
    addNode: handleAddNode,
    updateSelectedTask: handleTaskChange,
    clearSelection: () => setSelectedNodeId(null),
    setView: handleViewChange,
    // Notebook-view passthroughs (no-ops while the canvas view is active):
    runAll: () => notebookRef.current?.runAll(),
    addCell: (cellType) => notebookRef.current?.addCell(cellType),
  }), [handleAddNode, handleTaskChange, handleViewChange])

  // selectedTask's reference is stable across renders unless the selected node
  // changes or its data is edited, so this only fires on real changes.
  useEffect(() => {
    onSelectedTaskChange?.(selectedTask)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedTask])

  // ── Code panel spec apply ─────────────────────────────────────────────────
  // Called when the user clicks "Apply code" in CodePanel.  The panel has
  // round-tripped the Python source → FlowSpec via the backend sandbox.
  // We apply the incoming spec to the canvas (nodes + edges) and propagate
  // upward.  Canvas positions (ui.x/y) from the compile result are {0,0}
  // (scaffold-grade); we auto-layout by resetting to specToGraph which uses
  // the layered auto-layout fallback when ui coords are zero.
  const handleCodeApply = useCallback((incomingSpec) => {
    if (!incomingSpec) return
    const { nodes: newNodes, edges: newEdges } = specToGraph(incomingSpec)
    setNodes(newNodes)
    setEdges(newEdges)
    onSpecChange?.(incomingSpec)
  }, [setNodes, setEdges, onSpecChange])

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

  // ── If notebook view is active, delegate entirely to NotebookView ─────────
  if (viewMode === 'notebook') {
    return (
      <div className="flex flex-col h-full overflow-hidden">
        {/* The toolbar lives in the app top bar (FlowsPage portals it). */}
        <div className="flex-1 min-h-0 overflow-hidden">
          <NotebookView
            ref={notebookRef}
            flow={flow}
            spec={spec}
            onSpecChange={onSpecChange}
            onRun={onRun}
            env={env}
          />
        </div>
      </div>
    )
  }

  // ── Code / Files view — delegate to the VS Code-style projection ──────────
  if (viewMode === 'code') {
    return (
      <div className="flex flex-col h-full overflow-hidden">
        <div className="flex-1 min-h-0 overflow-hidden">
          <FlowCodeView flow={flow} spec={spec} onSpecChange={onSpecChange} />
        </div>
      </div>
    )
  }

  return (
    <div className="flex flex-col h-full overflow-hidden">

      {/* ── Mobile-only add bar. The view switcher + actions live in the app
          top bar (FlowsPage); desktop adds via the top-bar "Add task" toggle. ─ */}
      <div className="md:hidden shrink-0 flex items-center justify-end px-3 py-1.5 border-b border-border bg-surface">
        <Button variant="secondary" size="sm" onClick={() => setMobilePaletteOpen(true)} title="Add task" aria-label="Add task">
          <Plus size={14} aria-hidden="true" />
          Add task
        </Button>
      </div>

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
            className="fn-canvas"
            // Touch / pan / zoom support
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
            {/* MiniMap: hidden on small screens to save space */}
            <div className="hidden md:block">
              <MiniMap
                nodeColor={(node) => (node.type === 'branchNode' || node.data?.task?.kind === 'branch' ? minimapAccent : minimapInk)}
                nodeStrokeWidth={0}
                maskColor="rgba(11,12,14,0.06)"
                pannable
                zoomable
                ariaLabel="Flow minimap"
              />
            </div>
          </ReactFlow>

          {/* Empty canvas: say what to do next (pointer-events pass through) */}
          {nodes.length === 0 && (
            <div className="pointer-events-none absolute inset-0 flex items-center justify-center p-6">
              <EmptyState
                compact
                art={<EmptyFlows />}
                title="This canvas is empty"
                description={isMobile
                  ? 'Choose Add task above to place a SQL query, Python step or note.'
                  : 'Choose the plus in the top bar to place a SQL query, Python step or note, then drag between handles to connect them.'}
              />
            </div>
          )}

          {/* Mobile: inspector trigger button (shown when node selected) */}
          {selectedTask && isMobile && !mobileInspectorOpen && (
            <button
              onClick={() => setMobileInspectorOpen(true)}
              className="nubi-btn nubi-btn-secondary nubi-btn-md absolute bottom-20 right-3 z-10"
              aria-label="Open task inspector"
            >
              <SlidersHorizontal size={14} aria-hidden="true" />
              Inspect: {selectedTask.key}
            </button>
          )}
        </div>

        {/* Code panel — editable Python editor, desktop inline */}
        {codeOpen && !isMobile && (
          <div className="shrink-0 w-80 xl:w-96 overflow-hidden border-l border-border">
            <CodePanel
              flowId={flow?.id ?? null}
              spec={!flow?.id ? buildSpec() : null}
              onSpecChange={handleCodeApply}
              onClose={onCodeClose}
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

      {/* Code panel — editable Python editor, mobile bottom sheet */}
      <MobileBottomSheet
        open={codeOpen && isMobile}
        onClose={onCodeClose}
        title="Flow code (Python)"
      >
        <div className="h-[70vh]">
          <CodePanel
            flowId={flow?.id ?? null}
            spec={!flow?.id ? buildSpec() : null}
            onSpecChange={handleCodeApply}
            onClose={onCodeClose}
          />
        </div>
      </MobileBottomSheet>
    </div>
  )
})

export default FlowBuilder
