/**
 * NotebookView.jsx — Fabric-style notebook UI for a FlowSpec.
 *
 * Renders the spec's tasks as an ordered list of cells (not a canvas).
 * Each cell is a SqlCell or PythonCell.
 *
 * Features:
 *   - Add SQL / Python cell buttons at the top and after any cell
 *   - Move up / Move down reorders tasks in the spec
 *   - Delete cell
 *   - "Run cell" calls previewCell (POST /flows/preview) and shows rows inline;
 *     the backend re-executes upstream cells in the dependency chain so
 *     cross-cell references resolve server-side
 *   - "Run All" (durable) calls runFlow (POST /flows/{id}/run) directly
 *
 * This view has NO toolbar of its own — the single app top bar (portaled by
 * FlowsPage) owns the flow name input, add-cell buttons, save status, Save
 * and Run. Those top-bar controls drive this component via the imperative
 * ref handle ({ runAll, addCell }) forwarded through FlowBuilder.
 *
 * Saving is owned by FlowsPage (shared with the canvas view + autosave);
 * SaveStatusBadge is exported for the FlowsPage top bar.
 *
 * Props:
 *   flow           {object|null}  — saved flow row (null for unsaved draft)
 *   spec           {object}       — current FlowSpec (controlled)
 *   onSpecChange   {Function}     — called with updated spec on every edit
 *   onRun          {Function}     — called with { flowRun, runId } after triggering
 *   env            {string}       — run environment passed to runFlow (top-bar EnvSelector)
 */

import { useState, useCallback, useMemo, forwardRef, useImperativeHandle } from 'react'
import {
  Loader2,
  AlertCircle,
  Check,
  Code2,
  Database,
  FileText,
  X,
} from 'lucide-react'

import { runFlow } from '../lib/flows.js'
import { previewCell, makeBlankCell } from '../lib/notebooks.js'
import SqlCell from './cells/SqlCell.jsx'
import PythonCell from './cells/PythonCell.jsx'
import NoteCell from './cells/NoteCell.jsx'
import Button from '../components/ui/Button.jsx'
import EmptyState from '../components/ui/EmptyState.jsx'
import { EmptyFlows } from '../components/illustrations'
import '../components/app/appPatterns.css'

// ---------------------------------------------------------------------------
// AddCellBar — small inline add-cell row
// ---------------------------------------------------------------------------

function AddCellBar({ onAddSql, onAddPython, onAddNote }) {
  // Quiet until hovered or focused (never display:none, so it stays tabbable).
  return (
    <div className="group flex items-center justify-center gap-2 py-2">
      <div className="flex-1 border-t border-border transition-colors group-hover:border-frame-line group-focus-within:border-frame-line" />
      <div className="flex items-center gap-1 opacity-60 transition-opacity group-hover:opacity-100 group-focus-within:opacity-100">
        <Button variant="secondary" size="xs" onClick={onAddSql} title="Add SQL cell" aria-label="Add SQL cell">
          <Database size={12} aria-hidden="true" /> SQL
        </Button>
        <Button variant="secondary" size="xs" onClick={onAddPython} title="Add Python cell" aria-label="Add Python cell">
          <Code2 size={12} aria-hidden="true" /> Python
        </Button>
        <Button variant="secondary" size="xs" onClick={onAddNote} title="Add Note (markdown) cell" aria-label="Add note cell">
          <FileText size={12} aria-hidden="true" /> Note
        </Button>
      </div>
      <div className="flex-1 border-t border-border transition-colors group-hover:border-frame-line group-focus-within:border-frame-line" />
    </div>
  )
}

// ---------------------------------------------------------------------------
// SaveStatusBadge — compact unsaved/saving/saved/autosave-failed indicator.
// Rendered next to the Save buttons in both the notebook toolbar (here) and
// the FlowsPage top bar, so the two views share one consistent treatment.
// ---------------------------------------------------------------------------

export function SaveStatusBadge({ dirty, saving, autosaveStatus, className = '' }) {
  const base = ['flex items-center gap-1.5 ap-meta whitespace-nowrap shrink-0', className].join(' ')
  if (saving || autosaveStatus === 'saving') {
    return (
      <span role="status" className={base}>
        <Loader2 size={12} className="animate-spin" aria-hidden="true" />
        Saving
      </span>
    )
  }
  if (autosaveStatus === 'error') {
    return (
      <span role="status" className={base} style={{ color: 'var(--danger-fg)' }}>
        <span className="nubi-dot nubi-dot-danger" aria-hidden="true" />
        Autosave failed. Save manually.
      </span>
    )
  }
  if (dirty) {
    return (
      <span role="status" className={base} style={{ color: 'var(--warning-fg)' }}>
        <span className="nubi-dot nubi-dot-warning" aria-hidden="true" />
        Unsaved
      </span>
    )
  }
  if (autosaveStatus === 'saved') {
    return (
      <span role="status" className={base}>
        <Check size={12} aria-hidden="true" />
        Saved
      </span>
    )
  }
  return null
}

// ---------------------------------------------------------------------------
// NotebookView
// ---------------------------------------------------------------------------

export interface NotebookViewHandle {
  runAll: () => void
  addCell: (cellType?: string) => void
}

interface NotebookViewProps {
  flow: Record<string, any> | null
  spec: Record<string, any>
  onSpecChange: (spec: Record<string, any>) => void
  onRun?: (result: { flowRun: any; runId: any }) => void
  env?: string
}

const NotebookView = forwardRef<NotebookViewHandle, NotebookViewProps>(function NotebookView({ flow, spec, onSpecChange, onRun, env = 'prod' }, ref) {
  const [runError, setRunError] = useState(null)

  const specTasks = spec?.tasks
  const tasks = useMemo(() => specTasks ?? [], [specTasks])

  // ── Spec mutation helpers ─────────────────────────────────────────────────

  const setTasks = useCallback((newTasks) => {
    onSpecChange?.({ ...spec, tasks: newTasks })
  }, [spec, onSpecChange])

  const handleCellChange = useCallback((idx, updatedCell) => {
    setTasks(tasks.map((t, i) => i === idx ? updatedCell : t))
  }, [tasks, setTasks])

  const handleMoveUp = useCallback((idx) => {
    if (idx === 0) return
    const next = [...tasks]
    ;[next[idx - 1], next[idx]] = [next[idx], next[idx - 1]]
    setTasks(next)
  }, [tasks, setTasks])

  const handleMoveDown = useCallback((idx) => {
    if (idx >= tasks.length - 1) return
    const next = [...tasks]
    ;[next[idx], next[idx + 1]] = [next[idx + 1], next[idx]]
    setTasks(next)
  }, [tasks, setTasks])

  const handleDelete = useCallback((idx) => {
    setTasks(tasks.filter((_, i) => i !== idx))
  }, [tasks, setTasks])

  const handleAddCell = useCallback((cellType, insertAfterIdx) => {
    const blank = makeBlankCell(cellType)
    const next = [...tasks]
    const insertAt = insertAfterIdx == null ? next.length : insertAfterIdx + 1
    next.splice(insertAt, 0, blank)
    setTasks(next)
  }, [tasks, setTasks])

  // ── Cell run (preview) ────────────────────────────────────────────────────

  const handleRunCell = useCallback(async (cell) => {
    // The backend executes all upstream cells in the dependency chain itself,
    // so we send the full (possibly unsaved) spec plus the target cell key.
    return previewCell(spec, cell.key)
  }, [spec])

  // ── Run all (durable) — runs directly, no plan-gate dialog ────────────────

  const handleRunAll = useCallback(async () => {
    if (!flow?.id) {
      setRunError('Save the notebook first before running.')
      return
    }
    setRunError(null)
    const result = await runFlow(flow.id, {}, env || undefined)
    if (!result) {
      setRunError('Run failed — check the console for details.')
      return
    }
    onRun?.({ flowRun: result, runId: result.id })
  }, [flow, onRun, env])

  // ── Imperative handle — drives this view from the app top bar (FlowsPage
  //    portals the toolbar there; calls arrive via FlowBuilder's ref). ───────
  useImperativeHandle(ref, () => ({
    /** Durable "Run all" — triggers runFlow directly. */
    runAll: handleRunAll,
    /** Append a blank cell of the given type ('sql' | 'python' | 'markdown'). */
    addCell: (cellType) => handleAddCell(cellType, null),
  }), [handleRunAll, handleAddCell])

  return (
    <div className="flex flex-col h-full overflow-hidden">

      {/* The notebook toolbar lives in the single app top bar (FlowsPage). */}

      {/* Error banner (save errors surface via the page-level banner) */}
      {runError && (
        <div className="nubi-notice nubi-notice-error m-3 shrink-0 text-xs" role="alert">
          <span className="min-w-0 flex-1"><b>Run failed.</b> {runError}</span>
          <button type="button" onClick={() => setRunError(null)} aria-label="Dismiss run error" className="shrink-0 opacity-70 hover:opacity-100"><X size={14} aria-hidden="true" /></button>
        </div>
      )}

      {/* ── Cell list ──────────────────────────────────────────────────────── */}
      <div className="flex-1 overflow-y-auto px-3 sm:px-6 py-4 space-y-0">

        {/* Empty state */}
        {tasks.length === 0 && (
          <EmptyState
            art={<EmptyFlows />}
            title="This notebook is empty"
            description="Cells share data: a later SQL cell can read an earlier result by its key, for example cell_key. Start with a query, a Python step or a note."
            action={<Button onClick={() => handleAddCell('sql', null)}><Database size={14} aria-hidden="true" /> Add SQL cell</Button>}
            secondaryAction={
              <>
                <Button variant="secondary" onClick={() => handleAddCell('python', null)}><Code2 size={14} aria-hidden="true" /> Add Python cell</Button>
                <Button variant="ghost" onClick={() => handleAddCell('markdown', null)}><FileText size={14} aria-hidden="true" /> Add note</Button>
              </>
            }
          />
        )}

        {/* Add bar before first cell */}
        {tasks.length > 0 && (
          <AddCellBar
            onAddSql={() => handleAddCell('sql', -1)}
            onAddPython={() => handleAddCell('python', -1)}
            onAddNote={() => handleAddCell('markdown', -1)}
          />
        )}

        {/* Cells */}
        {tasks.map((cell, idx) => {
          const cellType = cell.cell_type ?? (
            cell.kind === 'python' ? 'python' : cell.kind === 'noop' ? 'markdown' : 'sql'
          )
          const isNote = cellType === 'markdown'
          const CellComponent = isNote ? NoteCell : cellType === 'python' ? PythonCell : SqlCell

          // Note cells never execute — don't hand them a run callback.
          const cellProps: Record<string, any> = {
            index: idx,
            cell,
            onCellChange: (updated: any) => handleCellChange(idx, updated),
            onMoveUp: idx > 0 ? () => handleMoveUp(idx) : null,
            onMoveDown: idx < tasks.length - 1 ? () => handleMoveDown(idx) : null,
            onDelete: () => handleDelete(idx),
          }
          if (!isNote) cellProps.onRun = handleRunCell

          return (
            <div key={cell.key} className="space-y-0">
              <CellComponent {...(cellProps as any)} />

              {/* Add-cell bar after each cell */}
              <AddCellBar
                onAddSql={() => handleAddCell('sql', idx)}
                onAddPython={() => handleAddCell('python', idx)}
                onAddNote={() => handleAddCell('markdown', idx)}
              />
            </div>
          )
        })}
      </div>
    </div>
  )
})

export default NotebookView
