/**
 * CellToolbar.jsx — per-cell action bar for NotebookView cells.
 *
 * Rendered at the top of every SqlCell / PythonCell.
 *
 * Shows:
 *   - Ordinal badge (Cell 1, Cell 2, …)
 *   - Cell type badge (SQL | Python)
 *   - Cell key (monospace, dimmed)
 *   - Run / Preview button (loading state)
 *   - Move up / Move down arrows
 *   - Delete cell button
 *
 * Props:
 *   index       {number}           — 0-based position in the notebook
 *   cell        {object}           — CellSpec
 *   running     {boolean}
 *   onRun       {Function}
 *   onMoveUp    {Function}         — undefined when cell is first
 *   onMoveDown  {Function}         — undefined when cell is last
 *   onDelete    {Function}
 */

import { Play, ChevronUp, ChevronDown, Trash2, Loader2 } from 'lucide-react'

const TYPE_BADGE = {
  sql:    { label: 'SQL',    cls: 'bg-blue-500/10 text-blue-600 dark:text-blue-400' },
  python: { label: 'Python', cls: 'bg-violet-500/10 text-violet-600 dark:text-violet-400' },
}

export default function CellToolbar({
  index,
  cell,
  running = false,
  onRun,
  onMoveUp,
  onMoveDown,
  onDelete,
}) {
  const cellType = cell?.cell_type ?? 'sql'
  const badge = TYPE_BADGE[cellType] ?? TYPE_BADGE.sql

  return (
    <div className="flex items-center gap-1.5 px-3 py-1.5 bg-surface-2/40 border-b border-border rounded-t-xl">
      {/* Ordinal + type badges */}
      <span className="text-[10px] font-semibold text-muted/60 shrink-0">
        {index + 1}
      </span>
      <span className={[
        'inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-semibold tracking-wide shrink-0',
        badge.cls,
      ].join(' ')}>
        {badge.label}
      </span>

      {/* Cell key */}
      <span className="flex-1 text-[10px] font-mono text-muted/50 truncate min-w-0">
        {cell?.key}
      </span>

      {/* Move up */}
      <button
        onClick={onMoveUp}
        disabled={!onMoveUp}
        title="Move cell up"
        className="w-6 h-6 flex items-center justify-center rounded text-muted hover:text-fg hover:bg-surface-2 disabled:opacity-20 disabled:cursor-not-allowed transition-colors"
      >
        <ChevronUp size={12} />
      </button>

      {/* Move down */}
      <button
        onClick={onMoveDown}
        disabled={!onMoveDown}
        title="Move cell down"
        className="w-6 h-6 flex items-center justify-center rounded text-muted hover:text-fg hover:bg-surface-2 disabled:opacity-20 disabled:cursor-not-allowed transition-colors"
      >
        <ChevronDown size={12} />
      </button>

      {/* Delete */}
      <button
        onClick={onDelete}
        title="Delete cell"
        className="w-6 h-6 flex items-center justify-center rounded text-muted hover:text-red-500 hover:bg-red-50 dark:hover:bg-red-900/20 transition-colors"
      >
        <Trash2 size={11} />
      </button>

      {/* Run / Preview */}
      <button
        onClick={onRun}
        disabled={running}
        title="Run cell (preview)"
        className="flex items-center gap-1 px-2.5 py-1 rounded-md text-[11px] font-medium bg-primary text-primary-fg hover:opacity-90 disabled:opacity-60 disabled:cursor-not-allowed transition-all shrink-0 min-h-[24px]"
      >
        {running
          ? <Loader2 size={11} className="animate-spin" />
          : <Play size={11} />
        }
        {running ? 'Running…' : 'Run'}
      </button>
    </div>
  )
}
