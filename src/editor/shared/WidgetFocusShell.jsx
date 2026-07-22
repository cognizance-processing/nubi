/**
 * shared/WidgetFocusShell.jsx — full-screen single-widget authoring surface.
 *
 * Focus mode is the "widget editor" half of Nubi's canvas-first model: the
 * widget still belongs to the board (nothing is saved as a separate asset),
 * but it can be opened into a roomy three-zone surface —
 *
 *   Data (query + live columns/sample) | Preview (the real widget) | Config
 *
 * — instead of being configured through the cramped right sidebar.
 *
 * The shell is deliberately presentational: it owns the chrome, the data rail
 * and the preview frame, and takes the per-type config as a `config` node so
 * DashboardEditor keeps ownership of its widget-config components.
 *
 * Props:
 *   widget     object      — the spec widget being edited
 *   onChange   (w)=>void   — full widget update callback
 *   onClose    ()=>void
 *   onRemove   ()=>void    — optional; renders a Remove action in the header
 *   extraQueryIds (string | {id,name})[] — passed through to QueryPicker
 *   icon       Component   — lucide icon for the widget type
 *   rowHeight  number      — the board's grid row height, used to size the frame
 *   frameStyle object      — the widget's own style as CSS, applied to the
 *                            preview frame so a dark-tiled widget previews on
 *                            its real background rather than the app surface
 *   preview    node        — the live widget preview (rendered inside the frame)
 *   config     node        — the per-type config sections (right rail)
 */

import { useEffect, useState } from 'react'
import { createPortal } from 'react-dom'
import { Database, X, Trash2, Table2, AlertCircle } from 'lucide-react'
import { QueryPicker } from './QueryPicker.jsx'
import QueryStatusLink from './QueryStatusLink.jsx'
import { useQuerySample } from './useInspectorData.js'
import { FieldLabel } from './inspectorPrimitives.jsx'

// Widget types that read from a query — they get the data rail.
const DATA_WIDGETS = ['kpi', 'metric', 'table', 'pivot', 'chart']

// Preview frame widths. The widget is rendered at the real component level, so
// these let you sanity-check how it reflows before returning to the canvas.
const PREVIEW_WIDTHS = [
  { key: 'sm',   label: 'S',    width: 360 },
  { key: 'md',   label: 'M',    width: 640 },
  { key: 'lg',   label: 'L',    width: 960 },
  { key: 'full', label: 'Fill', width: null },
]

/** Compact type badge, e.g. Utf8 → text, Int64 → num. */
function shortType(t = '') {
  const s = t.toLowerCase()
  if (s.includes('utf8') || s.includes('string')) return 'text'
  if (s.includes('date') || s.includes('time')) return 'date'
  if (s.includes('bool')) return 'bool'
  if (/int|float|decimal|double/.test(s)) return 'num'
  return 'other'
}

const TYPE_CLS = {
  text:  'text-sky-600 bg-sky-500/10 dark:text-sky-400',
  num:   'text-emerald-600 bg-emerald-500/10 dark:text-emerald-400',
  date:  'text-amber-600 bg-amber-500/10 dark:text-amber-400',
  bool:  'text-violet-600 bg-violet-500/10 dark:text-violet-400',
  other: 'text-muted bg-surface-2',
}

/** Render a sample cell value without letting a long string blow out the rail. */
function cellText(v) {
  if (v === null || v === undefined) return '—'
  if (v instanceof Date) return v.toISOString().slice(0, 10)
  return String(v)
}

/**
 * DataRail — query binding + a live look at what that query actually returns.
 * This is the half of the workflow the sidebar could never fit: you pick a
 * query and immediately see its columns and real values next to the preview.
 */
function DataRail({ widget, onChange, extraQueryIds }) {
  const { columns, rows, rowCount, loading, error } = useQuerySample(widget.query_id, 8)

  const setQueryId = (qid) => {
    const next = { ...widget, query_id: qid }
    // Mirror ConfigPanel: pre-fill an empty chart title from the query name once.
    if (widget.type === 'chart' && !widget.config?.title) {
      const match = extraQueryIds.find(q => q?.id === qid)
      if (match?.name) next.config = { ...(widget.config ?? {}), title: match.name }
    }
    onChange(next)
  }

  return (
    <div className="flex flex-col h-full min-h-0">
      <div className="p-3 space-y-1.5 border-b border-border shrink-0">
        <FieldLabel className="flex items-center gap-1.5"><Database size={12} /> Query</FieldLabel>
        <QueryPicker value={widget.query_id} onChange={setQueryId} extraIds={extraQueryIds} />
        <QueryStatusLink queryId={widget.query_id} />
      </div>

      <div className="flex-1 min-h-0 overflow-y-auto p-3 space-y-3">
        {!widget.query_id && (
          <p className="text-xs text-muted/70 leading-relaxed">
            Pick a query above to see its columns and sample rows here.
          </p>
        )}

        {loading && <p className="text-xs text-muted/70">Running query…</p>}

        {error && (
          <div className="flex items-start gap-2 rounded-lg border border-red-200 bg-red-50/70 p-2.5 dark:border-red-900/60 dark:bg-red-950/30">
            <AlertCircle size={13} className="text-red-500 mt-0.5 shrink-0" />
            <div className="min-w-0">
              <p className="text-[11px] font-medium text-red-600 dark:text-red-400">Query failed</p>
              <p className="text-[10px] text-red-500/80 break-words mt-0.5">{error}</p>
            </div>
          </div>
        )}

        {columns.length > 0 && (
          <>
            <div className="space-y-1.5">
              <FieldLabel>Columns · {columns.length}</FieldLabel>
              <ul className="space-y-0.5">
                {columns.map(c => {
                  const t = shortType(c.type)
                  return (
                    <li key={c.name} className="flex items-center justify-between gap-2 px-2 py-1 rounded-md hover:bg-surface-2/70">
                      <span className="text-[11px] font-mono text-fg/85 truncate" title={c.name}>{c.name}</span>
                      <span className={`shrink-0 px-1.5 rounded text-[9px] font-semibold uppercase tracking-wide ${TYPE_CLS[t]}`}>{t}</span>
                    </li>
                  )
                })}
              </ul>
            </div>

            <div className="space-y-1.5">
              <FieldLabel className="flex items-center gap-1.5">
                <Table2 size={12} /> Sample · {rows.length} of {rowCount.toLocaleString()}
              </FieldLabel>
              <div className="overflow-auto rounded-lg border border-border max-h-[280px]">
                <table className="w-full text-[10px] border-collapse">
                  <thead className="sticky top-0 bg-surface-2">
                    <tr>
                      {columns.map(c => (
                        <th key={c.name} className="px-1.5 py-1 text-left font-semibold text-muted whitespace-nowrap">{c.name}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((r, i) => (
                      <tr key={i} className="border-t border-border/60">
                        {columns.map(c => (
                          <td key={c.name} className="px-1.5 py-1 text-fg/75 whitespace-nowrap max-w-[120px] truncate" title={cellText(r[c.name])}>
                            {cellText(r[c.name])}
                          </td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          </>
        )}
      </div>
    </div>
  )
}

export default function WidgetFocusShell({
  widget, onChange, onClose, onRemove,
  extraQueryIds = [], icon: Icon, rowHeight = 60, frameStyle, preview, config,
}) {
  const [previewWidth, setPreviewWidth] = useState('full')

  // The frame needs an explicit height — widgets render h-full, so an
  // auto-height frame collapses charts to nothing. Track the widget's own grid
  // height so the preview keeps roughly the proportions it has on the canvas,
  // clamped so a 2-row KPI is still legible and a 20-row table still fits.
  const frameHeight = Math.min(Math.max((widget.pos?.h ?? 4) * rowHeight, 220), 640)

  useEffect(() => {
    const onKey = (e) => {
      // Let nested surfaces (the HTML editor's own expand, popovers) handle
      // Escape first — they stopPropagation; this only fires for the shell.
      if (e.key === 'Escape') { e.stopPropagation(); onClose() }
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [onClose])

  const isDataWidget = DATA_WIDGETS.includes(widget.type)
  const frameWidth = PREVIEW_WIDTHS.find(p => p.key === previewWidth)?.width

  return createPortal(
    <div className="fixed inset-0 z-[90] flex flex-col bg-bg" role="dialog" aria-modal="true" aria-label={`Edit ${widget.type} widget`}>
      {/* Header */}
      <header className="flex items-center justify-between gap-3 px-4 h-12 border-b border-border shrink-0">
        <div className="flex items-center gap-2 min-w-0">
          {Icon && <Icon size={15} className="text-primary shrink-0" />}
          <h2 className="text-sm font-semibold text-fg capitalize truncate">{widget.type} widget</h2>
          <span className="text-[10px] text-muted/50 font-mono truncate hidden sm:inline">{widget.id}</span>
        </div>

        <div className="flex items-center gap-2 shrink-0">
          {onRemove && (
            <button
              type="button"
              onClick={onRemove}
              className="flex items-center gap-1 text-xs px-2 h-7 rounded-lg border border-transparent text-muted hover:text-red-500 hover:border-red-200 hover:bg-red-50/80 transition-all dark:hover:bg-red-950/40"
            >
              <Trash2 size={12} /> Remove
            </button>
          )}
          <button
            type="button"
            onClick={onClose}
            data-testid="widget-focus-done"
            className="text-xs font-medium px-3 h-7 rounded-lg bg-primary text-white hover:opacity-90 transition-opacity"
          >
            Done
          </button>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="w-8 h-8 flex items-center justify-center rounded-lg text-muted hover:text-fg hover:bg-surface-2 transition-colors"
          >
            <X size={16} />
          </button>
        </div>
      </header>

      {/* Three zones */}
      <div className="flex-1 flex min-h-0">
        {isDataWidget && (
          <aside className="w-[280px] shrink-0 border-r border-border bg-surface/40 flex flex-col">
            <DataRail widget={widget} onChange={onChange} extraQueryIds={extraQueryIds} />
          </aside>
        )}

        <main className="flex-1 min-w-0 flex flex-col bg-surface-2/30">
          <div className="flex items-center justify-end gap-1 px-3 h-9 border-b border-border/60 shrink-0">
            {PREVIEW_WIDTHS.map(p => (
              <button
                key={p.key}
                type="button"
                onClick={() => setPreviewWidth(p.key)}
                title={p.width ? `Preview at ${p.width}px` : 'Fill available width'}
                className={`px-2 h-6 rounded-md text-[10px] font-semibold transition-colors ${
                  previewWidth === p.key ? 'bg-primary/10 text-primary ring-1 ring-primary/40' : 'text-muted hover:text-fg hover:bg-surface-2'
                }`}
              >
                {p.label}
              </button>
            ))}
          </div>

          <div className="flex-1 min-h-0 overflow-auto p-6 flex justify-center items-start">
            <div
              className="w-full rounded-xl border border-border bg-surface shadow-sm overflow-hidden shrink-0"
              style={{ maxWidth: frameWidth ?? undefined, height: frameHeight, ...(frameStyle ?? {}) }}
            >
              {preview}
            </div>
          </div>
        </main>

        <aside className="w-[340px] shrink-0 border-l border-border overflow-y-auto">
          {config}
        </aside>
      </div>
    </div>,
    document.body,
  )
}
