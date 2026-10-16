/**
 * AddTaskPanel.jsx — the "Add task" palette, shared between:
 *   - the Flows workspace RHS sidebar (FlowsPage)
 *   - the mobile add-task bottom sheet (FlowBuilder)
 *
 * v4 "cells, not kinds": the palette surfaces ONLY the three user-facing cell
 * types — SQL query, Python, and Note (markdown). Everything advanced (map,
 * branch, materialize, agent, bucket_load, …) is now authored as a config block
 * on a SQL/Python cell (for_each / run_when / materialized) or pulled in from a
 * template, NOT as a separate palette item. The legacy kinds stay registered in
 * the backend so old specs keep running; they are just off the palette.
 *
 * `onAdd(kind, defaultConfig, cellType)` is called on click — `cellType` stamps
 * `cell_type` ('sql' | 'python' | 'markdown') onto the created task so the
 * notebook/canvas know how to render it.
 */

import { Database, Code2, FileText } from 'lucide-react'
import '../components/app/appPatterns.css'

// The ONLY palette items — the three user-facing cell types.
const PRIMARY_ITEMS = [
  {
    kind: 'query',
    cellType: 'sql',
    label: 'SQL query',
    hint: 'A SELECT. The everyday data block.',
    Icon: Database,
    defaultConfig: { sql: '' },
  },
  {
    kind: 'python',
    cellType: 'python',
    label: 'Python',
    hint: 'Transform rows, call an API or an agent.',
    Icon: Code2,
    defaultConfig: { code: '# Write your task code here\nresult = {}' },
  },
  {
    kind: 'noop',
    cellType: 'markdown',
    label: 'Note',
    hint: 'Markdown prose. Never executes.',
    Icon: FileText,
    defaultConfig: { markdown: '' },
  },
]

/**
 * The list of palette buttons. `onAdd(kind, defaultConfig, cellType)` is called
 * on click. `disabled` greys the whole list (e.g. when no flow is open in the
 * builder).
 *
 * @param {{ onAdd: (kind: string, defaultConfig: object, cellType: string) => void, disabled?: boolean }} props
 */
export function AddTaskPanel({ onAdd, disabled = false }) {
  return (
    <div className="flex flex-col gap-2 p-3">
      {disabled && (
        <p className="m-0 border border-dashed border-frame-line px-3 py-3 text-center text-xs text-muted" style={{ borderRadius: 2 }}>
          Open a flow to add tasks.
        </p>
      )}

      {PRIMARY_ITEMS.map((item) => {
        const ItemIcon = item.Icon
        return (
          <button
            type="button"
            key={item.cellType}
            disabled={disabled}
            onClick={() => onAdd(item.kind, item.defaultConfig, item.cellType)}
            className="ap-tile disabled:cursor-not-allowed disabled:opacity-45"
          >
            <span
              className="flex h-8 w-8 shrink-0 items-center justify-center border border-frame-line bg-surface text-fg"
              style={{ borderRadius: 2 }}
              aria-hidden="true"
            >
              <ItemIcon size={16} strokeWidth={1.75} />
            </span>
            <span className="min-w-0 flex-1">
              <span className="ap-tile-name block">{item.label}</span>
              <span className="ap-tile-desc block">{item.hint}</span>
            </span>
          </button>
        )
      })}

      <p className="m-0 px-0.5 pt-1 text-xs leading-relaxed text-subtle">
        Advanced behaviour (materialize a table, fan out with for-each, or gate a cell with
        run-when) is a setting on a SQL or Python cell, in the inspector.
      </p>
    </div>
  )
}
