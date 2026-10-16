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

import { useState, type ReactNode } from 'react'
import { ChevronDown, ChevronRight, Loader2 } from 'lucide-react'
import Tag from '../components/ui/Tag.jsx'
import { getToolMeta, toolLabel, truncate, coerce, toolSummary } from './toolMeta.js'
import '../components/app/appPatterns.css'

export interface ToolAction {
  id: string
  tool: string
  args?: Record<string, any>
  result?: any
  status: 'running' | 'done' | 'error'
}

// ---------------------------------------------------------------------------
// Result renderers
// ---------------------------------------------------------------------------

function MiniTable({ columns = [], rows = [] }: { columns?: string[]; rows?: Record<string, any>[] }) {
  const cols = columns.length ? columns : (rows[0] ? Object.keys(rows[0]) : [])
  if (!cols.length) return <p className="m-0 text-xs text-muted">No columns.</p>
  return (
    <div className="overflow-x-auto border border-border" style={{ borderRadius: 2 }}>
      <table className="w-full border-collapse font-mono text-[11px]">
        <thead>
          <tr>
            {cols.map(c => (
              <th key={c} className="whitespace-nowrap px-2 py-1 text-left font-medium text-muted" style={{ borderBottom: '2px solid var(--ink)' }}>
                {c}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.slice(0, 8).map((r, i) => (
            <tr key={i}>
              {cols.map(c => (
                <td key={c} className="max-w-[160px] truncate whitespace-nowrap border-b border-border px-2 py-1 text-fg">
                  {String(r?.[c] ?? '')}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      {rows.length > 8 && <p className="m-0 bg-surface-2 px-2 py-1 font-mono text-[11px] text-muted">+{rows.length - 8} more rows</p>}
    </div>
  )
}

/** A labelled section with a mono caption. */
function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div>
      <p className="t-label m-0 mb-1">{label}</p>
      {children}
    </div>
  )
}

/** Rich result body, keyed on the tool. Falls back to pretty JSON. */
function ToolResultBody({ tool, result }: { tool: string; result: any }) {
  const value = coerce(result) as Record<string, any>

  if (value == null) return null
  if (typeof value !== 'object') {
    return <pre className="m-0 whitespace-pre-wrap break-words font-mono text-[11px] leading-relaxed text-fg">{String(value)}</pre>
  }
  if (value.error) {
    return (
      <p className="m-0 flex items-start gap-1.5 text-xs" role="alert" style={{ color: 'var(--danger-fg)' }}>
        <span className="nubi-dot nubi-dot-danger mt-1.5" aria-hidden="true" />{String(value.error)}
      </p>
    )
  }

  if (tool === 'generate_sql' && (value.sql || value.valid != null)) {
    return (
      <div className="space-y-1.5">
        <pre className="m-0 overflow-x-auto whitespace-pre-wrap break-words bg-surface-2 px-2.5 py-2 font-mono text-[11px] leading-relaxed text-fg" style={{ borderRadius: 2 }}>
          {value.sql || '—'}
        </pre>
        <div className="flex flex-wrap items-center gap-1.5">
          <Tag variant={value.valid ? 'success' : 'warning'} dot>{value.valid ? 'valid' : 'needs review'}</Tag>
          {(value.tables || []).slice(0, 4).map(t => <Tag key={t} caps={false}>{t}</Tag>)}
        </div>
        {(value.issues || []).length > 0 && (
          <ul className="m-0 ml-4 list-disc text-[11px]" style={{ color: 'var(--warning-fg)' }}>
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
        <p className="ap-meta m-0">
          <span style={{ color: 'var(--text)' }}>{(value.row_count ?? (value.rows || []).length).toLocaleString()}</span> rows ·{' '}
          <span style={{ color: 'var(--text)' }}>{colCount}</span> cols
        </p>
        {(value.rows || []).length > 0 && <MiniTable columns={value.columns} rows={value.rows} />}
      </div>
    )
  }

  if ((tool === 'create_dashboard' || tool === 'edit_dashboard' || tool === 'propose_dashboard_spec') &&
      (value.widgets || value.title || value.widget_count != null)) {
    return (
      <div className="space-y-1.5">
        {value.title && <p className="m-0 text-xs font-semibold text-fg">{value.title}</p>}
        <div className="flex flex-wrap gap-1">
          {(value.widgets || []).slice(0, 12).map((w, i) => (
            <Tag key={i} caps={false}>
              {w.type}{w.title ? ` · ${truncate(w.title, 18)}` : ''}
            </Tag>
          ))}
          {!value.widgets?.length && (
            <span className="ap-meta">{value.widget_count ?? 0} widget(s)</span>
          )}
        </div>
      </div>
    )
  }

  // Fallback: pretty JSON, capped so a huge payload doesn't blow out the panel.
  return (
    <pre className="m-0 max-h-56 overflow-auto whitespace-pre-wrap break-words bg-surface-2 px-2.5 py-2 font-mono text-[11px] leading-relaxed text-fg" style={{ borderRadius: 2 }}>
      {JSON.stringify(value, null, 2)}
    </pre>
  )
}

// ---------------------------------------------------------------------------
// ToolCard — one ruled, collapsible tool call (mono, like a terminal log line)
// ---------------------------------------------------------------------------

export default function ToolCard({ action, footer = null, defaultOpen = false }: {
  action: ToolAction
  footer?: ReactNode
  defaultOpen?: boolean
}) {
  const [open, setOpen] = useState(defaultOpen)
  const { icon: Icon } = getToolMeta(action.tool)
  const running = action.status === 'running'
  const errored = action.status === 'error'
  const summary = !running ? toolSummary(action.tool, action.result) : ''
  const hasArgs = action.args && Object.keys(action.args).length > 0
  const hasResult = action.result !== undefined && action.result !== null

  return (
    <div className="border-b border-border" data-state={action.status}>
      <button
        type="button"
        onClick={() => setOpen(v => !v)}
        className="nubi-focus-inset flex w-full items-center gap-2 py-1.5 text-left transition-colors hover:bg-frame-hover"
        aria-expanded={open}
      >
        <Icon size={14} className="shrink-0 text-subtle" aria-hidden="true" />
        <span className="min-w-0 flex-1 truncate font-mono text-xs leading-tight">
          <span className="font-semibold text-fg">{toolLabel(action.tool)}</span>
          {running ? (
            <span className="ml-2 text-muted">running</span>
          ) : summary ? (
            <span className="ml-2 text-muted">{summary}</span>
          ) : null}
        </span>
        <span className="flex shrink-0 items-center" role="img" aria-label={running ? 'Running' : errored ? 'Failed' : 'Done'}>
          {running
            ? <Loader2 size={13} className="animate-spin text-muted" aria-hidden="true" />
            : <span className={`nubi-dot ${errored ? 'nubi-dot-danger' : 'nubi-dot-success'}`} />}
        </span>
        <span className="shrink-0 text-subtle">
          {open ? <ChevronDown size={13} aria-hidden="true" /> : <ChevronRight size={13} aria-hidden="true" />}
        </span>
      </button>

      {open && (
        <div className="mb-2 ml-1.5 space-y-2 border-l-2 border-frame-line py-1 pl-3">
          {hasArgs && (
            <Field label="Arguments">
              <pre className="m-0 max-h-40 overflow-auto whitespace-pre-wrap break-words bg-surface-2 px-2 py-1.5 font-mono text-[11px] text-fg" style={{ borderRadius: 2 }}>
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
          {running && <p className="ap-meta m-0">Executing</p>}
        </div>
      )}
    </div>
  )
}
