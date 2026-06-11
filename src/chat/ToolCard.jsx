/**
 * ToolCard — shared, presentation-only rendering for an AI tool invocation.
 *
 * Both chat surfaces (the global rail panel `src/chat/ChatPanel.jsx` and the
 * dashboard-editor panel `src/editor/ChatPanel.jsx`) stream tool calls with
 * slightly different field names, so this component takes a normalised shape:
 *
 *   {
 *     id:        string,
 *     tool:      string,                 // tool name, e.g. "run_query"
 *     args:      object | undefined,     // tool input/arguments
 *     result:    any | undefined,        // tool output (undefined = still running)
 *     status:    'running' | 'done' | 'error',
 *   }
 *
 * It renders a clean, collapsible card: a friendly label + icon, a live
 * running → done/error status, a compact one-line summary, and (when expanded)
 * the arguments and a *richly* rendered result (SQL, a mini result table, a
 * dashboard widget summary, an error) — never a raw JSON dump unless there's
 * no better view for it.
 *
 * Tool metadata + summary helpers live in `./toolMeta.js` (a plain module, so
 * this file can stay a clean component-only module for fast-refresh).
 */

import { useState } from 'react'
import {
  ChevronDown, ChevronRight, AlertCircle, Check, Loader2, FileJson,
} from 'lucide-react'
import { getToolMeta, toolLabel, truncate, coerce, toolSummary } from './toolMeta.js'

// ---------------------------------------------------------------------------
// Result renderers
// ---------------------------------------------------------------------------

function MiniTable({ columns = [], rows = [] }) {
  const cols = columns.length ? columns : (rows[0] ? Object.keys(rows[0]) : [])
  if (!cols.length) return <p className="text-[11px] text-muted">No columns.</p>
  return (
    <div className="overflow-x-auto rounded-lg border border-border">
      <table className="w-full text-[11px] font-mono border-collapse">
        <thead>
          <tr className="bg-surface-2">
            {cols.map(c => (
              <th key={c} className="text-left font-semibold text-muted px-2 py-1 border-b border-border whitespace-nowrap">
                {c}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.slice(0, 8).map((r, i) => (
            <tr key={i} className="even:bg-surface-2/40">
              {cols.map(c => (
                <td key={c} className="px-2 py-1 text-fg whitespace-nowrap max-w-[160px] truncate border-b border-border/50">
                  {String(r?.[c] ?? '')}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      {rows.length > 8 && (
        <p className="text-[10px] text-muted px-2 py-1 bg-surface-2/40">+{rows.length - 8} more rows</p>
      )}
    </div>
  )
}

/** A labelled section with an uppercase caption. */
function Field({ label, children }) {
  return (
    <div>
      <p className="text-muted uppercase tracking-wider mb-1 text-[10px] font-semibold">{label}</p>
      {children}
    </div>
  )
}

/** Rich result body, keyed on the tool. Falls back to pretty JSON. */
function ToolResultBody({ tool, result }) {
  const value = coerce(result)

  if (value == null) return null
  if (typeof value !== 'object') {
    return <pre className="text-[11px] text-fg whitespace-pre-wrap break-words leading-relaxed">{String(value)}</pre>
  }
  if (value.error) {
    return (
      <p className="text-[11px] text-red-500 flex items-start gap-1.5">
        <AlertCircle size={12} className="shrink-0 mt-0.5" />{String(value.error)}
      </p>
    )
  }

  if (tool === 'generate_sql' && (value.sql || value.valid != null)) {
    return (
      <div className="space-y-1.5">
        <pre className="text-[11px] leading-relaxed font-mono text-fg bg-surface-2 rounded-lg px-2.5 py-2 overflow-x-auto whitespace-pre-wrap break-words">
          {value.sql || '—'}
        </pre>
        <div className="flex items-center gap-1.5 flex-wrap">
          <span className={`text-[10px] px-1.5 py-0.5 rounded-md font-medium ${value.valid ? 'bg-brand-teal/15 text-brand-teal' : 'bg-amber-500/15 text-amber-600'}`}>
            {value.valid ? 'valid' : 'needs review'}
          </span>
          {(value.tables || []).slice(0, 4).map(t => (
            <span key={t} className="text-[10px] px-1.5 py-0.5 rounded-md bg-surface-2 text-muted font-mono">{t}</span>
          ))}
        </div>
        {(value.issues || []).length > 0 && (
          <ul className="text-[10px] text-amber-600 list-disc ml-4">
            {value.issues.slice(0, 3).map((iss, i) => <li key={i}>{iss}</li>)}
          </ul>
        )}
      </div>
    )
  }

  if (tool === 'run_query' && (value.rows || value.row_count != null)) {
    const colCount = (value.columns || (value.rows?.[0] ? Object.keys(value.rows[0]) : [])).length
    return (
      <div className="space-y-1.5">
        <p className="text-[11px] text-muted">
          <span className="font-semibold text-fg">{value.row_count ?? (value.rows || []).length}</span> rows ·{' '}
          <span className="font-semibold text-fg">{colCount}</span> cols
        </p>
        {(value.rows || []).length > 0 && <MiniTable columns={value.columns} rows={value.rows} />}
      </div>
    )
  }

  if ((tool === 'create_dashboard' || tool === 'edit_dashboard' || tool === 'propose_dashboard_spec') &&
      (value.widgets || value.title || value.widget_count != null)) {
    return (
      <div className="space-y-1.5">
        {value.title && <p className="text-[11px] text-fg font-medium">{value.title}</p>}
        <div className="flex flex-wrap gap-1">
          {(value.widgets || []).slice(0, 12).map((w, i) => (
            <span key={i} className="text-[10px] px-1.5 py-0.5 rounded-md bg-brand-blue/10 text-brand-blue font-mono">
              {w.type}{w.title ? ` · ${truncate(w.title, 18)}` : ''}
            </span>
          ))}
          {!value.widgets?.length && (
            <span className="text-[10px] text-muted">{value.widget_count ?? 0} widget(s)</span>
          )}
        </div>
      </div>
    )
  }

  // Fallback: pretty JSON, capped so a huge payload doesn't blow out the panel.
  return (
    <pre className="text-[11px] text-fg whitespace-pre-wrap break-words leading-relaxed max-h-56 overflow-auto bg-surface-2 rounded-lg px-2.5 py-2">
      {JSON.stringify(value, null, 2)}
    </pre>
  )
}

// ---------------------------------------------------------------------------
// ToolCard — collapsible tool invocation
// ---------------------------------------------------------------------------

/**
 * @param {{
 *   action: { id, tool, args?, result?, status: 'running'|'done'|'error' },
 *   footer?: React.ReactNode,   // optional extra UI under the result (e.g. a Pin button)
 *   defaultOpen?: boolean,
 * }} props
 */
export default function ToolCard({ action, footer = null, defaultOpen = false }) {
  const [open, setOpen] = useState(defaultOpen)
  const { icon: Icon, color, bg } = getToolMeta(action.tool)
  const running = action.status === 'running'
  const errored = action.status === 'error'
  const summary = !running ? toolSummary(action.tool, action.result) : ''
  const hasArgs = action.args && Object.keys(action.args).length > 0
  const hasResult = action.result !== undefined && action.result !== null

  return (
    <div className={`rounded-xl border overflow-hidden transition-shadow ${errored ? 'border-red-500/30' : 'border-border'} ${open ? 'shadow-sm' : ''}`}>
      <button
        type="button"
        onClick={() => setOpen(v => !v)}
        className="w-full flex items-center gap-2.5 px-2.5 py-2 bg-surface-2 hover:bg-surface-2/70 text-left transition-colors focus:outline-none focus:ring-2 focus:ring-ring focus:ring-inset"
        aria-expanded={open}
      >
        <span className={`flex items-center justify-center w-6 h-6 rounded-lg ${bg} shrink-0`}>
          <Icon size={13} className={color} />
        </span>
        <span className="flex-1 min-w-0">
          <span className="block font-mono text-[11px] font-semibold text-fg leading-tight truncate">
            {toolLabel(action.tool)}
          </span>
          {running ? (
            <span className="block text-[10px] text-brand-teal font-mono">running…</span>
          ) : summary ? (
            <span className="block text-[10px] text-muted font-mono truncate">{summary}</span>
          ) : null}
        </span>
        <span className="shrink-0 flex items-center">
          {running && <Loader2 size={14} className="text-brand-teal animate-spin" />}
          {action.status === 'done' && <Check size={14} className="text-brand-teal" />}
          {errored && <AlertCircle size={14} className="text-red-500" />}
        </span>
        <span className="text-muted shrink-0">
          {open ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
        </span>
      </button>

      {open && (
        <div className="px-2.5 py-2.5 bg-surface border-t border-border space-y-2">
          {hasArgs && (
            <Field label="Arguments">
              <pre className="text-[11px] text-fg font-mono whitespace-pre-wrap break-words bg-surface-2 rounded-lg px-2 py-1.5 max-h-40 overflow-auto">
                {JSON.stringify(action.args, null, 2)}
              </pre>
            </Field>
          )}
          {hasResult && (
            <Field label="Result">
              <ToolResultBody tool={action.tool} result={action.result} />
              {footer}
            </Field>
          )}
          {running && (
            <p className="text-[11px] text-muted italic flex items-center gap-1.5">
              <FileJson size={11} className="shrink-0" /> Executing…
            </p>
          )}
        </div>
      )}
    </div>
  )
}
