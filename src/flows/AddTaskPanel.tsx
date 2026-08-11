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

// The ONLY palette items — the three user-facing cell types.
const PRIMARY_ITEMS = [
  {
    kind: 'query',
    cellType: 'sql',
    label: 'SQL query',
    hint: 'A SELECT — the everyday data block',
    Icon: Database,
    color: 'text-blue-500',
    defaultConfig: { sql: '' },
  },
  {
    kind: 'python',
    cellType: 'python',
    label: 'Python',
    hint: 'Transform rows, call an API or an agent',
    Icon: Code2,
    color: 'text-violet-500',
    defaultConfig: { code: '# Write your task code here\nresult = {}' },
  },
  {
    kind: 'noop',
    cellType: 'markdown',
    label: 'Note',
    hint: 'Markdown prose — never executes',
    Icon: FileText,
    color: 'text-slate-400',
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
    <div className="p-2 space-y-1.5">
      {disabled && (
        <p className="text-[11px] text-muted/70 rounded-lg border border-dashed border-border bg-surface-2/30 px-3 py-3 text-center mb-1">
          Open a flow to add tasks.
        </p>
      )}

      {PRIMARY_ITEMS.map((item) => {
        const ItemIcon = item.Icon
        return (
          <button
            key={item.cellType}
            disabled={disabled}
            onClick={() => onAdd(item.kind, item.defaultConfig, item.cellType)}
            className="w-full flex items-start gap-2.5 px-2.5 py-2.5 rounded-lg text-sm font-semibold text-fg border border-border bg-surface-2/40 hover:bg-surface-2 transition-colors disabled:opacity-40 disabled:cursor-not-allowed text-left"
          >
            <ItemIcon size={18} className={[item.color, 'shrink-0 mt-0.5'].join(' ')} />
            <span className="flex-1 min-w-0">
              <span className="block">{item.label}</span>
              <span className="block text-[10px] font-normal text-muted/70 mt-0.5">{item.hint}</span>
            </span>
          </button>
        )
      })}

      <p className="text-[10px] text-muted/60 leading-relaxed px-1 pt-1.5">
        Advanced behaviour — materialize a table, fan out (for-each), or gate a
        cell (run-when) — is a setting on a SQL or Python cell, in the inspector.
      </p>
    </div>
  )
}
