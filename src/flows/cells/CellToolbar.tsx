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

import { Play, ChevronUp, ChevronDown, Trash2 } from 'lucide-react'
import Button from '../../components/ui/Button.jsx'
import Tag from '../../components/ui/Tag.jsx'

const TYPE_LABEL = { sql: 'SQL', python: 'Python' }

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
  const label = TYPE_LABEL[cellType] ?? TYPE_LABEL.sql

  return (
    <div className="flex items-center gap-1.5 border-b border-border bg-surface-2 px-3 py-1.5">
      {/* Ordinal + type tags */}
      <span className="ap-meta shrink-0" aria-label={`Cell ${index + 1}`}>{index + 1}</span>
      <Tag className="shrink-0">{label}</Tag>

      {/* Cell key */}
      <span className="min-w-0 flex-1 truncate font-mono text-[11px] text-subtle">{cell?.key}</span>

      <Button variant="ghost" size="icon-xs" onClick={onMoveUp} disabled={!onMoveUp} title="Move cell up" aria-label="Move cell up">
        <ChevronUp size={14} aria-hidden="true" />
      </Button>
      <Button variant="ghost" size="icon-xs" onClick={onMoveDown} disabled={!onMoveDown} title="Move cell down" aria-label="Move cell down">
        <ChevronDown size={14} aria-hidden="true" />
      </Button>
      <Button variant="ghost" size="icon-xs" onClick={onDelete} title="Delete cell" aria-label="Delete cell">
        <Trash2 size={13} aria-hidden="true" />
      </Button>

      {/* Run / Preview */}
      <Button size="xs" onClick={onRun} loading={running} title="Run cell (preview)">
        {!running && <Play size={12} aria-hidden="true" />}
        {running ? 'Running' : 'Run'}
      </Button>
    </div>
  )
}
