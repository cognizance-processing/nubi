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
 *   - "Run All" (durable) opens PlanGateDialog, then calls runFlow
 *     (POST /flows/{id}/run); the dialog stays open until the run actually
 *     starts and shows the error inline if triggering fails
 *
 * Saving is owned by FlowsPage (shared with the canvas view + autosave): the
 * toolbar Save button just calls the `onSave` prop, and the dirty/autosave
 * status passed down is rendered via SaveStatusBadge (also exported for the
 * FlowsPage top bar so both views show one consistent indicator).
 *
 * Props:
 *   flow           {object|null}  — saved flow row (null for unsaved draft)
 *   spec           {object}       — current FlowSpec (controlled)
 *   onSpecChange   {Function}     — called with updated spec on every edit
 *   onSave         {Function}     — triggers the shared (page-level) save
 *   saving         {boolean}      — a manual save is in flight
 *   dirty          {boolean}      — spec differs from the last-saved snapshot
 *   autosaveStatus {string|null}  — null | 'saving' | 'saved' | 'error'
 *   onRun          {Function}     — called with { flowRun, runId } after triggering
 */

import { useState, useCallback, useRef, useMemo } from 'react'
import {
  Plus,
  Save,
  Play,
  Loader2,
  AlertCircle,
  Check,
  Code2,
  Database,
  FileText,
  X,
  GitBranch,
} from 'lucide-react'

import { runFlow } from '../lib/flows.js'
import { previewCell, makeBlankCell } from '../lib/notebooks.js'
import SqlCell from './cells/SqlCell.jsx'
import PythonCell from './cells/PythonCell.jsx'
import NoteCell from './cells/NoteCell.jsx'
import LineagePanel from './LineagePanel.jsx'
import PlanGateDialog from './PlanGateDialog.jsx'

// ---------------------------------------------------------------------------
// AddCellBar — small inline add-cell row
// ---------------------------------------------------------------------------

function AddCellBar({ onAddSql, onAddPython, onAddNote }) {
  return (
    <div className="flex items-center justify-center gap-2 py-2">
      <div className="flex-1 border-t border-dashed border-border/40" />
      <button
        onClick={onAddSql}
        className="flex items-center gap-1 px-2.5 py-1 rounded-lg text-[11px] font-medium border border-dashed border-border text-muted hover:text-fg hover:border-border hover:bg-surface-2 transition-colors"
        title="Add SQL cell"
      >
        <Database size={11} />
        SQL
      </button>
      <button
        onClick={onAddPython}
        className="flex items-center gap-1 px-2.5 py-1 rounded-lg text-[11px] font-medium border border-dashed border-border text-muted hover:text-fg hover:border-border hover:bg-surface-2 transition-colors"
        title="Add Python cell"
      >
        <Code2 size={11} />
        Python
      </button>
      <button
        onClick={onAddNote}
        className="flex items-center gap-1 px-2.5 py-1 rounded-lg text-[11px] font-medium border border-dashed border-border text-muted hover:text-fg hover:border-border hover:bg-surface-2 transition-colors"
        title="Add Note (markdown) cell"
      >
        <FileText size={11} />
        Note
      </button>
      <div className="flex-1 border-t border-dashed border-border/40" />
    </div>
  )
}

// ---------------------------------------------------------------------------
// SaveStatusBadge — compact unsaved/saving/saved/autosave-failed indicator.
// Rendered next to the Save buttons in both the notebook toolbar (here) and
// the FlowsPage top bar, so the two views share one consistent treatment.
// ---------------------------------------------------------------------------

export function SaveStatusBadge({ dirty, saving, autosaveStatus, className = '' }) {
  const base = ['flex items-center gap-1 text-[11px] whitespace-nowrap shrink-0', className].join(' ')
  if (saving || autosaveStatus === 'saving') {
    return (
      <span role="status" className={[base, 'text-muted'].join(' ')}>
        <Loader2 size={11} className="animate-spin" />
        Saving…
      </span>
    )
  }
  if (autosaveStatus === 'error') {
    return (
      <span role="status" className={[base, 'text-rose-600 dark:text-rose-400'].join(' ')}>
        <AlertCircle size={11} />
        Autosave failed — Save manually
      </span>
    )
  }
  if (dirty) {
    return (
      <span role="status" className={[base, 'text-amber-600 dark:text-amber-400'].join(' ')}>
        <span className="w-1.5 h-1.5 rounded-full bg-amber-500" />
        Unsaved
      </span>
    )
  }
  if (autosaveStatus === 'saved') {
    return (
      <span role="status" className={[base, 'text-muted'].join(' ')}>
        <Check size={11} className="text-green-500" />
        Saved
      </span>
    )
  }
  return null
}

// ---------------------------------------------------------------------------
// NotebookView
// ---------------------------------------------------------------------------

export default function NotebookView({ flow, spec, onSpecChange, onSave, saving = false, dirty = false, autosaveStatus = null, onRun }) {
  const [runningAll, setRunningAll] = useState(false)
  const [runError, setRunError] = useState(null)

  // Lineage toggle — show the full-flow lineage panel below the toolbar
  const [showLineage, setShowLineage] = useState(false)

  // Plan gate dialog state — open before durable Run All
  const [planGateOpen, setPlanGateOpen] = useState(false)
  // Track the most recently changed cell key so the plan can highlight it.
  // The ref captures changes during render-free callbacks; we snapshot it into
  // state when the plan gate opens so render never reads the ref directly.
  const lastChangedCellRef = useRef(null)
  const [planChangedCellKey, setPlanChangedCellKey] = useState('')

  const specTasks = spec?.tasks
  const tasks = useMemo(() => specTasks ?? [], [specTasks])

  // ── Spec mutation helpers ─────────────────────────────────────────────────

  const setTasks = useCallback((newTasks) => {
    onSpecChange?.({ ...spec, tasks: newTasks })
  }, [spec, onSpecChange])

  const handleCellChange = useCallback((idx, updatedCell) => {
    // Track the last changed cell so the plan gate can highlight impact
    lastChangedCellRef.current = updatedCell?.key ?? null
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

  // ── Run all (durable) — open plan gate first ──────────────────────────────

  const handleRunAll = useCallback(() => {
    if (!flow?.id) {
      setRunError('Save the notebook first before running.')
      return
    }
    // Snapshot the most recently changed cell key so the dialog can highlight
    // impact without reading the ref during render.
    setPlanChangedCellKey(lastChangedCellRef.current ?? '')
    // Open the plan gate dialog; the actual run happens in handlePlanConfirm
    setPlanGateOpen(true)
  }, [flow])

  // Triggers the durable run. Returns true on success / false on failure so
  // PlanGateDialog can keep itself open (with an inline error + retry) when
  // the run fails; we only close the dialog once the run actually started.
  const handlePlanConfirm = useCallback(async () => {
    if (!flow?.id) return false
    setRunningAll(true)
    setRunError(null)
    const result = await runFlow(flow.id, {})
    setRunningAll(false)
    if (!result) {
      setRunError('Run failed — check the console for details.')
      return false
    }
    setPlanGateOpen(false)
    onRun?.({ flowRun: result, runId: result.id })
    return true
  }, [flow, onRun])

  return (
    <div className="flex flex-col h-full overflow-hidden">

      {/* ── Notebook toolbar ──────────────────────────────────────────────── */}
      <div className="shrink-0 flex items-center gap-1.5 sm:gap-2 px-2 sm:px-4 py-2 border-b border-border bg-surface-2/40 overflow-x-auto">

        {/* Notebook name */}
        <input
          type="text"
          value={spec?.name ?? ''}
          onChange={e => onSpecChange?.({ ...spec, name: e.target.value })}
          placeholder="Notebook name…"
          className="h-9 px-2.5 text-sm font-medium border border-border rounded-lg bg-surface text-fg placeholder:text-muted/50 focus:outline-none focus:ring-2 focus:ring-ring/60 w-28 sm:w-44 shrink-0"
        />

        <div className="flex-1 min-w-0" />

        {/* Add SQL cell */}
        <button
          onClick={() => handleAddCell('sql', null)}
          className="flex items-center gap-1.5 px-2 sm:px-3 h-9 text-xs font-medium rounded-lg border border-border bg-surface text-fg hover:bg-surface-2 transition-colors shrink-0"
          title="Add SQL cell"
        >
          <Database size={12} className="text-blue-500" />
          <span className="hidden sm:inline">+ SQL</span>
          <span className="sm:hidden"><Plus size={12} /></span>
        </button>

        {/* Add Python cell */}
        <button
          onClick={() => handleAddCell('python', null)}
          className="flex items-center gap-1.5 px-2 sm:px-3 h-9 text-xs font-medium rounded-lg border border-border bg-surface text-fg hover:bg-surface-2 transition-colors shrink-0"
          title="Add Python cell"
        >
          <Code2 size={12} className="text-violet-500" />
          <span className="hidden sm:inline">+ Python</span>
        </button>

        {/* Add Note cell */}
        <button
          onClick={() => handleAddCell('markdown', null)}
          className="flex items-center gap-1.5 px-2 sm:px-3 h-9 text-xs font-medium rounded-lg border border-border bg-surface text-fg hover:bg-surface-2 transition-colors shrink-0"
          title="Add Note (markdown) cell"
        >
          <FileText size={12} className="text-slate-400" />
          <span className="hidden sm:inline">+ Note</span>
        </button>

        {/* Unsaved / autosave status (shared treatment with the top bar) */}
        <SaveStatusBadge dirty={dirty} saving={saving} autosaveStatus={autosaveStatus} className="hidden sm:flex px-1" />

        {/* Save — delegates to the page-level shared save (also autosaved) */}
        <button
          onClick={() => onSave?.()}
          disabled={saving}
          className="flex items-center gap-1.5 px-2 sm:px-3 h-9 text-xs font-medium rounded-lg border border-border bg-surface text-fg hover:bg-surface-2 disabled:opacity-50 transition-colors shrink-0"
          title="Save notebook"
        >
          {saving
            ? <Loader2 size={13} className="animate-spin" />
            : <Save size={13} />
          }
          <span className="hidden sm:inline">Save</span>
        </button>

        {/* Lineage toggle */}
        <button
          onClick={() => setShowLineage(v => !v)}
          title={showLineage ? 'Hide lineage panel' : 'Show flow lineage'}
          className={[
            'flex items-center gap-1.5 px-2 sm:px-3 h-9 text-xs font-medium rounded-lg border transition-colors shrink-0',
            showLineage
              ? 'border-blue-500/40 bg-blue-500/10 text-blue-600 dark:text-blue-400'
              : 'border-border bg-surface text-fg hover:bg-surface-2',
          ].join(' ')}
        >
          <GitBranch size={12} className={showLineage ? 'text-blue-500' : ''} />
          <span className="hidden sm:inline">Lineage</span>
        </button>

        {/* Run All (durable) */}
        <button
          onClick={handleRunAll}
          disabled={runningAll || !flow?.id}
          title={!flow?.id ? 'Save the notebook first' : 'Run all cells (durable)'}
          className="flex items-center gap-1.5 px-2 sm:px-3 h-9 text-xs font-medium rounded-lg bg-primary text-primary-fg hover:opacity-90 disabled:opacity-50 disabled:cursor-not-allowed transition-all shrink-0"
        >
          {runningAll
            ? <Loader2 size={13} className="animate-spin" />
            : <Play size={13} />
          }
          <span className="hidden sm:inline">Run all</span>
        </button>
      </div>

      {/* Error banner (save errors surface via the page-level banner) */}
      {runError && (
        <div className="shrink-0 flex items-center gap-2 px-4 py-2 bg-rose-500/5 border-b border-rose-500/20 text-xs text-rose-600 dark:text-rose-400">
          <AlertCircle size={13} />
          <span className="flex-1 min-w-0">{runError}</span>
          <button onClick={() => setRunError(null)} className="ml-auto opacity-60 hover:opacity-100 shrink-0"><X size={12} /></button>
        </div>
      )}

      {/* ── Flow lineage panel ────────────────────────────────────────────── */}
      {showLineage && (
        <div className="shrink-0 px-3 sm:px-6 pt-3 pb-0">
          <LineagePanel
            mode="flow"
            flowId={flow?.id ?? null}
            spec={spec}
            onClose={() => setShowLineage(false)}
          />
        </div>
      )}

      {/* ── Plan gate dialog ──────────────────────────────────────────────── */}
      <PlanGateDialog
        open={planGateOpen}
        spec={spec}
        changedCellKey={planChangedCellKey}
        onConfirm={handlePlanConfirm}
        onCancel={() => setPlanGateOpen(false)}
      />

      {/* ── Cell list ──────────────────────────────────────────────────────── */}
      <div className="flex-1 overflow-y-auto px-3 sm:px-6 py-4 space-y-0">

        {/* Empty state */}
        {tasks.length === 0 && (
          <div className="flex flex-col items-center justify-center py-16 text-center">
            <div className="w-12 h-12 rounded-2xl bg-surface-2 flex items-center justify-center mb-3">
              <Database size={22} className="text-muted/50" />
            </div>
            <p className="text-sm font-medium text-fg/80 mb-1">Empty notebook</p>
            <p className="text-xs text-muted max-w-xs">
              Add a SQL or Python cell to get started. Cells share data — reference
              an earlier cell&apos;s result as{' '}
              <code className="font-mono bg-surface-2 px-1 rounded">cell_key</code>.
            </p>
            <div className="flex items-center gap-2 mt-4">
              <button
                onClick={() => handleAddCell('sql', null)}
                className="flex items-center gap-1.5 px-3 py-2 text-xs font-medium rounded-lg bg-blue-500/10 text-blue-600 dark:text-blue-400 border border-blue-500/20 hover:bg-blue-500/20 transition-colors"
              >
                <Database size={13} />
                Add SQL cell
              </button>
              <button
                onClick={() => handleAddCell('python', null)}
                className="flex items-center gap-1.5 px-3 py-2 text-xs font-medium rounded-lg bg-violet-500/10 text-violet-600 dark:text-violet-400 border border-violet-500/20 hover:bg-violet-500/20 transition-colors"
              >
                <Code2 size={13} />
                Add Python cell
              </button>
              <button
                onClick={() => handleAddCell('markdown', null)}
                className="flex items-center gap-1.5 px-3 py-2 text-xs font-medium rounded-lg bg-slate-500/10 text-slate-600 dark:text-slate-300 border border-slate-500/20 hover:bg-slate-500/20 transition-colors"
              >
                <FileText size={13} />
                Add Note
              </button>
            </div>
          </div>
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
          const cellProps = {
            index: idx,
            cell,
            onCellChange: (updated) => handleCellChange(idx, updated),
            onMoveUp: idx > 0 ? () => handleMoveUp(idx) : null,
            onMoveDown: idx < tasks.length - 1 ? () => handleMoveDown(idx) : null,
            onDelete: () => handleDelete(idx),
          }
          if (!isNote) cellProps.onRun = handleRunCell

          return (
            <div key={cell.key} className="space-y-0">
              <CellComponent {...cellProps} />

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
}
