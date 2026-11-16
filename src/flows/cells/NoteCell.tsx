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
import Button from '../../components/ui/Button.jsx'
import Tag from '../../components/ui/Tag.jsx'

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
    <div className="overflow-hidden border border-frame-line bg-surface" style={{ borderRadius: 2 }}>
      {/* Toolbar — no run button; Note never executes. */}
      <div className="flex items-center gap-1.5 border-b border-border bg-surface-2 px-3 py-1.5">
        <span className="ap-meta shrink-0" aria-label={`Cell ${index + 1}`}>{index + 1}</span>
        <Tag className="shrink-0"><FileText size={10} aria-hidden="true" /> Note</Tag>
        <span className="min-w-0 flex-1 truncate font-mono text-[11px] text-subtle">{cell?.key}</span>

        {/* Edit / Done toggle */}
        <Button
          variant="ghost" size="icon-xs"
          onClick={() => setEditing(v => !v)}
          title={editing ? 'Done editing' : 'Edit note'}
          aria-label={editing ? 'Done editing' : 'Edit note'}
        >
          {editing ? <Check size={14} aria-hidden="true" /> : <Pencil size={13} aria-hidden="true" />}
        </Button>
        <Button variant="ghost" size="icon-xs" onClick={onMoveUp} disabled={!onMoveUp} title="Move cell up" aria-label="Move cell up">
          <ChevronUp size={14} aria-hidden="true" />
        </Button>
        <Button variant="ghost" size="icon-xs" onClick={onMoveDown} disabled={!onMoveDown} title="Move cell down" aria-label="Move cell down">
          <ChevronDown size={14} aria-hidden="true" />
        </Button>
        <Button variant="ghost" size="icon-xs" onClick={onDelete} title="Delete cell" aria-label="Delete cell">
          <Trash2 size={13} aria-hidden="true" />
        </Button>
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
          className="block w-full text-left px-4 py-3 hover:bg-frame-hover transition-colors"
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
