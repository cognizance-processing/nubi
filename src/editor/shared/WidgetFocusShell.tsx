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
import { titleText, withTitleText } from './titleValue.js'
import Button from '../../components/ui/Button.jsx'
import Tooltip from '../../components/ui/Tooltip.jsx'
import '../../dashboards/widgets/chrome.css'

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

// Column types are data, so they read as mono labels, not coloured pills.

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
    if (widget.type === 'chart' && !titleText(widget.config?.title)) {
      const match = extraQueryIds.find(q => q?.id === qid)
      if (match?.name) {
        next.config = { ...(widget.config ?? {}), title: withTitleText(widget.config?.title, match.name) }
      }
    }
    onChange(next)
  }

  return (
    <div className="flex flex-col h-full min-h-0">
      <div className="p-3 space-y-1.5 border-b border-border shrink-0">
        <FieldLabel className="flex items-center gap-1.5"><Database size={14} strokeWidth={1.5} /> Query</FieldLabel>
        <QueryPicker value={widget.query_id} onChange={setQueryId} extraIds={extraQueryIds} />
        <QueryStatusLink queryId={widget.query_id} />
      </div>

      <div className="flex-1 min-h-0 overflow-y-auto p-3 space-y-3">
        {!widget.query_id && (
          <p className="m-0 text-[13px] leading-snug text-muted">
            Pick a query above to see its columns and sample rows here.
          </p>
        )}

        {loading && <p className="m-0 text-[13px] text-muted" role="status">Running query…</p>}

        {error && (
          <div className="nubi-notice nubi-notice-error items-start" role="alert">
            <AlertCircle size={14} strokeWidth={1.5} className="mt-0.5 shrink-0" />
            <div className="min-w-0">
              <p className="m-0 text-[13px] font-semibold">Query failed</p>
              <p className="m-0 mt-0.5 break-words font-mono text-[11px]">{error}</p>
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
                    <li key={c.name} className="flex items-center justify-between gap-2 border-b border-border px-1 py-1.5 hover:bg-surface-2">
                      <span className="font-mono text-[12px] text-fg truncate" title={c.name}>{c.name}</span>
                      <span className="nbc-meta shrink-0 uppercase">{t}</span>
                    </li>
                  )
                })}
              </ul>
            </div>

            <div className="space-y-1.5">
              <FieldLabel className="flex items-center gap-1.5">
                <Table2 size={14} strokeWidth={1.5} /> Sample · {rows.length} of {rowCount.toLocaleString()}
              </FieldLabel>
              <div className="max-h-[280px] overflow-auto border border-border">
                <table className="w-full border-collapse text-[11px] font-mono tabular-nums">
                  <thead className="sticky top-0 bg-surface-2">
                    <tr>
                      {columns.map(c => (
                        <th key={c.name} className="border-b-2 border-[var(--ink)] px-1.5 py-1 text-left font-semibold text-muted whitespace-nowrap">{c.name}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((r, i) => (
                      <tr key={i} className="border-t border-border/60">
                        {columns.map(c => (
                          <td key={c.name} className="px-1.5 py-1 text-fg whitespace-nowrap max-w-[120px] truncate" title={cellText(r[c.name])}>
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
    <div className="fixed inset-0 z-[90] flex flex-col bg-bg" role="dialog" aria-modal="true" aria-label={`Edit ${widget.type} widget`} data-testid="widget-focus-shell">
      {/* Header */}
      <header className="flex items-center justify-between gap-3 px-4 h-12 border-b-2 border-[var(--ink)] bg-surface shrink-0">
        <div className="flex items-center gap-2 min-w-0">
          {Icon && <Icon size={16} strokeWidth={1.5} className="shrink-0 text-muted" />}
          <h2 className="m-0 truncate text-sm font-semibold capitalize text-fg">{widget.type} widget</h2>
          <span className="nbc-meta hidden truncate sm:inline">{widget.id}</span>
        </div>

        <div className="flex items-center gap-2 shrink-0">
          {onRemove && (
            <Button type="button" variant="ghost" size="sm" onClick={onRemove} className="hover:!text-danger">
              <Trash2 size={14} strokeWidth={1.5} aria-hidden="true" /> Remove
            </Button>
          )}
          <Tooltip content="Back to canvas" kbd="Esc" side="bottom">
            <Button type="button" size="sm" onClick={onClose} data-testid="widget-focus-done">
              Done
            </Button>
          </Tooltip>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="nbc-tool !px-0"
          >
            <X size={16} strokeWidth={1.5} />
          </button>
        </div>
      </header>

      {/* Three zones */}
      <div className="flex-1 flex min-h-0">
        {isDataWidget && (
          <aside className="w-[280px] shrink-0 border-r border-border bg-surface flex flex-col" aria-label="Data">
            <DataRail widget={widget} onChange={onChange} extraQueryIds={extraQueryIds} />
          </aside>
        )}

        <main className="flex-1 min-w-0 flex flex-col bg-bg">
          <div className="flex items-center justify-between gap-1 px-3 h-10 border-b border-border bg-surface shrink-0">
            <span className="nbc-label">Preview</span>
            <div className="nbc-seg !h-7" role="group" aria-label="Preview width">
              {PREVIEW_WIDTHS.map(p => (
                <button
                  key={p.key}
                  type="button"
                  onClick={() => setPreviewWidth(p.key)}
                  title={p.width ? `Preview at ${p.width}px` : 'Fill available width'}
                  aria-pressed={previewWidth === p.key}
                  className="!px-2.5 !text-[12px]"
                >
                  {p.label}
                </button>
              ))}
            </div>
          </div>

          <div className="flex-1 min-h-0 overflow-auto p-6 flex justify-center items-start">
            <div
              className="w-full overflow-hidden border border-border bg-surface shrink-0"
              style={{ maxWidth: frameWidth ?? undefined, height: frameHeight, borderRadius: 2, ...(frameStyle ?? {}) }}
            >
              {preview}
            </div>
          </div>
        </main>

        <aside className="w-[340px] shrink-0 border-l border-border bg-surface overflow-y-auto" aria-label="Settings">
          {config}
        </aside>
      </div>
    </div>,
    document.body,
  )
}
