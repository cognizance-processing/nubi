/**
 * NoteCell.jsx — a Note (markdown) notebook cell (v4 "cells, not kinds").
 *
 * A Note is the third user-facing cell type. Backend kind is 'noop' (a
 * pass-through that never executes data); the prose lives in
 * config.markdown. It has NO run button — it never calls previewCell.
 *
 * Click the body to edit (textarea); blur or "Done" renders the markdown.
 *
 * Props:
 *   index         {number}           0-based position in the notebook
 *   cell          {object}           CellSpec (kind='noop', cell_type='markdown')
 *   onCellChange  {Function(cell)}   called when markdown changes
 *   onMoveUp      {Function|null}
 *   onMoveDown    {Function|null}
 *   onDelete      {Function}
 */

import { useState, useCallback } from 'react'
import { ChevronUp, ChevronDown, Trash2, FileText, Pencil, Check } from 'lucide-react'
import MarkdownRenderer from '../../components/MarkdownRenderer.jsx'

export default function NoteCell({
  index,
  cell,
  onCellChange,
  onMoveUp,
  onMoveDown,
  onDelete,
}) {
  const markdown = cell?.config?.markdown ?? ''
  // Start in edit mode when the note is empty (nothing to render yet).
  const [editing, setEditing] = useState(() => !markdown.trim())

  const handleChange = useCallback((val) => {
    onCellChange?.({ ...cell, config: { ...cell.config, markdown: val ?? '' } })
  }, [cell, onCellChange])

  return (
    <div className="rounded-xl border border-border bg-surface shadow-sm overflow-hidden">
      {/* Toolbar — no run button; Note never executes. */}
      <div className="flex items-center gap-1.5 px-3 py-1.5 bg-surface-2/40 border-b border-border rounded-t-xl">
        <span className="text-[10px] font-semibold text-muted/60 shrink-0">{index + 1}</span>
        <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-semibold tracking-wide shrink-0 bg-slate-500/10 text-slate-600 dark:text-slate-300">
          <FileText size={10} />
          Note
        </span>
        <span className="flex-1 text-[10px] font-mono text-muted/50 truncate min-w-0">{cell?.key}</span>

        {/* Edit / Done toggle */}
        <button
          onClick={() => setEditing(v => !v)}
          title={editing ? 'Done editing' : 'Edit note'}
          className="w-6 h-6 flex items-center justify-center rounded text-muted hover:text-fg hover:bg-surface-2 transition-colors"
        >
          {editing ? <Check size={12} /> : <Pencil size={11} />}
        </button>

        <button
          onClick={onMoveUp}
          disabled={!onMoveUp}
          title="Move cell up"
          className="w-6 h-6 flex items-center justify-center rounded text-muted hover:text-fg hover:bg-surface-2 disabled:opacity-20 disabled:cursor-not-allowed transition-colors"
        >
          <ChevronUp size={12} />
        </button>
        <button
          onClick={onMoveDown}
          disabled={!onMoveDown}
          title="Move cell down"
          className="w-6 h-6 flex items-center justify-center rounded text-muted hover:text-fg hover:bg-surface-2 disabled:opacity-20 disabled:cursor-not-allowed transition-colors"
        >
          <ChevronDown size={12} />
        </button>
        <button
          onClick={onDelete}
          title="Delete cell"
          className="w-6 h-6 flex items-center justify-center rounded text-muted hover:text-red-500 hover:bg-red-50 dark:hover:bg-red-900/20 transition-colors"
        >
          <Trash2 size={11} />
        </button>
      </div>

      {/* Body — editor or rendered markdown */}
      {editing ? (
        <textarea
          autoFocus
          value={markdown}
          onChange={e => handleChange(e.target.value)}
          onBlur={() => { if (markdown.trim()) setEditing(false) }}
          placeholder="# Heading&#10;&#10;Write notes in **markdown**…"
          className="w-full min-h-[120px] resize-y px-4 py-3 text-sm font-mono bg-surface text-fg placeholder:text-muted/50 focus:outline-none"
        />
      ) : (
        <button
          type="button"
          onClick={() => setEditing(true)}
          className="block w-full text-left px-4 py-3 hover:bg-surface-2/30 transition-colors"
          title="Click to edit"
        >
          {markdown.trim() ? (
            <div className="prose prose-sm max-w-none">
              <MarkdownRenderer content={markdown} />
            </div>
          ) : (
            <span className="text-sm text-muted/50 italic">Empty note — click to edit.</span>
          )}
        </button>
      )}
    </div>
  )
}
