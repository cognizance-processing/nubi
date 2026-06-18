/**
 * DocCanvas.jsx — T10 Report / Document surface canvas.
 *
 * Renders spec.surfaces.report as a paginated A4/Letter document preview with:
 *   - Left PAGES RAIL: page thumbnails, add/reorder/delete pages, page-break toggle.
 *   - Center CANVAS: A4/Letter-proportioned pages with top-to-bottom widget flow.
 *     Each page's items flow in order; `width` hints control inline width per item.
 *   - Export layout hints (spec.export.header / spec.export.footer / spec.export.title_slide)
 *     are shown visually so the on-screen report matches the exported PDF.
 *
 * Props
 * -----
 * spec          {object}   — Live dashboard spec (read-only; mutations go via surfaceAPI).
 * surfaceAPI    {object}   — From useSurfaceState(spec, setSpec):
 *                            { reportSurface, addReportPage, removeReportPage,
 *                              reorderReportPages, updateReportPage }
 * selectedWidgetId {string|null}  — Currently selected widget from the shared inspector.
 * onSelectWidget   {fn}           — (widgetId | null) => void — propagated back to shell.
 *
 * PAGE BREAKS
 * -----------
 * Authors insert a page break by toggling page.page_break = true on any page in the
 * pages rail. In the canvas this renders a visible "page break" band with a
 * `break-before: page` / `page-break-before: always` CSS rule so printed/exported
 * versions honour it. In the editor the band is a visual divider labeled "Page break".
 */

import { useState, useCallback, useMemo } from 'react'
import {
  Plus, Trash2, FileText, ChevronUp, ChevronDown, LayoutList,
} from 'lucide-react'
import { memo } from 'react'
import { VariableProvider } from '../dashboards/VariableStore.jsx'
import ChartWidget from '../dashboards/widgets/ChartWidget.jsx'
import KpiWidget from '../dashboards/widgets/KpiWidget.jsx'
import MetricWidget from '../dashboards/widgets/MetricWidget.jsx'
import TableWidget from '../dashboards/widgets/TableWidget.jsx'
import PivotWidget from '../dashboards/widgets/PivotWidget.jsx'
import FilterWidget from '../dashboards/widgets/FilterWidget.jsx'
import TextWidget from '../dashboards/widgets/TextWidget.jsx'
import SectionWidget from '../dashboards/widgets/SectionWidget.jsx'
import HtmlWidget from '../dashboards/widgets/HtmlWidget.jsx'
import { Wand2 } from 'lucide-react'
import {
  gridSpecToReport,
  canGenerateReport,
  reportHasContent,
} from '../dashboards/surfaceGenerators.js'

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

/** Width hint → CSS percent.  `number` values are used directly as percent. */
const WIDTH_MAP = { full: '100%', half: '50%', third: '33.33%' }

function widthToCss(width) {
  if (!width || width === 'full') return '100%'
  if (typeof width === 'number') return `${width}%`
  return WIDTH_MAP[width] ?? '100%'
}


// ---------------------------------------------------------------------------
// WidgetRenderer — same widget set as DashboardEditor.WidgetPreview (no fork)
// ---------------------------------------------------------------------------

function normalizeWidget(w) {
  return {
    query_id: w.query_id ?? null,
    chart_type: w.chart_type ?? null,
    encoding: w.encoding ?? {},
    props: w.props ?? {},
    ...w,
  }
}

const WidgetRenderer = memo(function WidgetRenderer({ widget, caption }) {
  const w = normalizeWidget(widget)
  return (
    <VariableProvider initialValues={{}}>
      <div className="w-full">
        <div className="w-full overflow-hidden" style={{ minHeight: 48 }}>
          {w.html ? (
            <HtmlWidget widget={w} />
          ) : (
            <>
              {w.type === 'chart'   && <ChartWidget   widget={w} />}
              {w.type === 'kpi'     && <KpiWidget     widget={w} />}
              {w.type === 'metric'  && <MetricWidget  widget={w} />}
              {w.type === 'table'   && <TableWidget   widget={w} />}
              {w.type === 'pivot'   && <PivotWidget   widget={w} />}
              {w.type === 'filter'  && <FilterWidget  widget={w} options={[]} />}
              {w.type === 'text'    && <TextWidget    widget={w} />}
              {w.type === 'section' && <SectionWidget widget={w} />}
              {!['chart','kpi','metric','table','pivot','filter','text','section'].includes(w.type) && (
                <div className="flex items-center justify-center h-12 text-xs text-muted bg-surface-2/30 rounded-lg border border-dashed border-border">
                  {w.type}
                </div>
              )}
            </>
          )}
        </div>
        {caption && (
          <p className="mt-1.5 text-[11px] text-muted/70 italic text-center leading-snug">
            {caption}
          </p>
        )}
      </div>
    </VariableProvider>
  )
})

// ---------------------------------------------------------------------------
// DocWidgetItem — a widget on the report canvas (selectable, shows width ring)
// ---------------------------------------------------------------------------

function DocWidgetItem({ widget, item, selected, onSelect, exportHints, onWidthChange }) {
  const caption = exportHints?.caption ?? null
  const cssWidth = widthToCss(item.width)

  return (
    <div
      style={{ width: cssWidth, flexShrink: 0 }}
      className={[
        'relative group/item cursor-pointer rounded-xl transition-all',
        selected
          ? 'ring-2 ring-primary ring-offset-2 ring-offset-bg'
          : 'hover:ring-1 hover:ring-border',
      ].join(' ')}
      onClick={() => onSelect(widget.id)}
      data-testid={`report-widget-item-${widget.id}`}
    >
      {/* Width pill — only visible on hover/selected */}
      <div className={[
        'absolute -top-6 left-0 z-10 flex items-center gap-1 transition-opacity',
        selected ? 'opacity-100' : 'opacity-0 group-hover/item:opacity-100',
      ].join(' ')}>
        {['full', 'half', 'third'].map(w => (
          <button
            key={w}
            onClick={e => { e.stopPropagation(); onWidthChange(w) }}
            className={[
              'h-5 px-1.5 text-[10px] font-medium rounded-md border transition-colors',
              (item.width ?? 'full') === w
                ? 'bg-primary text-primary-fg border-primary'
                : 'bg-surface text-muted border-border hover:border-primary hover:text-primary',
            ].join(' ')}
          >
            {w}
          </button>
        ))}
      </div>

      <div className="bg-surface rounded-xl border border-border/60 overflow-hidden p-3">
        <WidgetRenderer widget={widget} caption={caption} />
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// PageBreakBand — visual divider for page.page_break = true
// ---------------------------------------------------------------------------

function PageBreakBand() {
  return (
    <div
      className="flex items-center gap-2 py-2 mb-4"
      style={{ breakBefore: 'page', pageBreakBefore: 'always' }}
      data-testid="page-break-band"
      aria-label="Page break"
    >
      <div className="flex-1 border-t border-dashed border-primary/40" />
      <span className="text-[10px] font-medium text-primary/60 uppercase tracking-widest px-2 bg-bg">
        Page break
      </span>
      <div className="flex-1 border-t border-dashed border-primary/40" />
    </div>
  )
}

// ---------------------------------------------------------------------------
// ExportHeaderBand / ExportFooterBand — mirrors spec.export.header/footer
// ---------------------------------------------------------------------------

function ExportHeaderBand({ text, titleSlide }) {
  if (!text && !titleSlide) return null
  return (
    <div
      className="flex items-center justify-between px-6 py-3 mb-6 border-b border-border/40 bg-surface-2/30 rounded-t-xl"
      data-testid="report-export-header"
    >
      {titleSlide?.heading && (
        <span className="text-sm font-semibold text-fg truncate">{titleSlide.heading}</span>
      )}
      {text && (
        <span className="text-xs text-muted ml-auto">{text}</span>
      )}
    </div>
  )
}

function ExportFooterBand({ text }) {
  if (!text) return null
  return (
    <div
      className="flex items-center justify-end px-6 py-2 mt-6 border-t border-border/40 bg-surface-2/30 rounded-b-xl"
      data-testid="report-export-footer"
    >
      <span className="text-xs text-muted">{text}</span>
    </div>
  )
}

// ---------------------------------------------------------------------------
// DocPage — renders one report page in the center canvas
// ---------------------------------------------------------------------------

function DocPage({
  page,
  pageIndex,
  allWidgets,
  selectedWidgetId,
  onSelectWidget,
  exportHints,
  onWidthChange,
  exportConfig,
}) {
  const widgetMap = Object.fromEntries(allWidgets.map(w => [w.id, w]))
  const showHeader = exportConfig?.header || exportConfig?.title_slide
  const showFooter = !!exportConfig?.footer

  return (
    <div
      className="doc-page relative bg-white shadow-lg rounded-xl overflow-hidden mb-4"
      data-testid={`report-page-${page.id}`}
      style={{
        // A4-style page width with natural height
        width: '100%',
        // Print media: enforce page break
        ...(page.page_break ? { breakBefore: 'page', pageBreakBefore: 'always' } : {}),
      }}
    >
      {/* Export header (visual analog of PDF page header) */}
      {showHeader && (
        <ExportHeaderBand
          text={exportConfig.header}
          titleSlide={exportConfig.title_slide}
        />
      )}

      {/* Page label */}
      <div className="px-6 pt-4 pb-2 flex items-center gap-2">
        <span className="text-[10px] font-semibold text-muted/50 uppercase tracking-widest">
          Page {pageIndex + 1}
        </span>
        {page.page_break && (
          <span className="text-[10px] text-primary/60 font-medium bg-primary/5 px-2 py-0.5 rounded-full border border-primary/20">
            break before
          </span>
        )}
      </div>

      {/* Widget items — flex-wrap to support half/third widths side-by-side */}
      <div className="px-6 pb-6 flex flex-wrap gap-4">
        {page.items.length === 0 && (
          <div className="w-full py-12 flex flex-col items-center justify-center rounded-xl border border-dashed border-border bg-surface-2/20">
            <LayoutList size={20} className="text-muted/40 mb-2" />
            <p className="text-xs text-muted/60">No widgets on this page.</p>
            <p className="text-[10px] text-muted/40 mt-1">
              Drag widgets from the palette to add them here.
            </p>
          </div>
        )}
        {page.items.map(item => {
          const widget = widgetMap[item.widgetId]
          if (!widget) return null
          const hint = exportHints?.[item.widgetId] ?? {}
          return (
            <DocWidgetItem
              key={item.widgetId}
              widget={widget}
              item={item}
              selected={selectedWidgetId === widget.id}
              onSelect={onSelectWidget}
              exportHints={hint}
              onWidthChange={(w) => onWidthChange(page.id, item.widgetId, w)}
            />
          )
        })}
      </div>

      {/* Export footer */}
      {showFooter && (
        <ExportFooterBand text={exportConfig.footer} />
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// PagesThumbnail — a small preview pill in the left pages rail
// ---------------------------------------------------------------------------

function PagesThumbnail({ page, pageIndex, active, onClick, onMoveUp, onMoveDown, onDelete, onToggleBreak, isFirst, isLast }) {
  return (
    <div
      className={[
        'group relative flex flex-col cursor-pointer rounded-xl border transition-all overflow-hidden',
        'hover:border-primary/40 hover:shadow-sm',
        active
          ? 'border-primary bg-primary/5 shadow-sm'
          : 'border-border bg-surface',
      ].join(' ')}
      onClick={onClick}
      data-testid={`page-thumb-${page.id}`}
    >
      {/* Page number badge */}
      <div className="flex items-center justify-between gap-1 px-2.5 pt-2 pb-1.5">
        <span className={`text-[10px] font-semibold uppercase tracking-wide ${active ? 'text-primary' : 'text-muted/60'}`}>
          p{pageIndex + 1}
        </span>
        {page.page_break && (
          <span className="text-[9px] font-medium text-primary/70 bg-primary/10 px-1.5 py-0.5 rounded-full">
            break
          </span>
        )}
      </div>

      {/* Mini widget count */}
      <div className="px-2.5 pb-2">
        <span className="text-[10px] text-muted/60">
          {page.items.length} widget{page.items.length !== 1 ? 's' : ''}
        </span>
      </div>

      {/* Actions — visible on hover */}
      <div className="hidden group-hover:flex items-center justify-between gap-0.5 px-1.5 py-1.5 border-t border-border/50 bg-surface-2/50">
        <button
          onClick={e => { e.stopPropagation(); onMoveUp() }}
          disabled={isFirst}
          title="Move page up"
          className="w-6 h-6 flex items-center justify-center rounded-md text-muted hover:text-fg hover:bg-surface transition-colors disabled:opacity-30 disabled:cursor-not-allowed"
        >
          <ChevronUp size={11} />
        </button>
        <button
          onClick={e => { e.stopPropagation(); onToggleBreak() }}
          title={page.page_break ? 'Remove page break before' : 'Insert page break before'}
          className={`w-6 h-6 flex items-center justify-center rounded-md transition-colors ${
            page.page_break ? 'text-primary hover:bg-primary/10' : 'text-muted hover:text-primary hover:bg-surface'
          }`}
        >
          <span className="text-[9px] font-bold">¶</span>
        </button>
        <button
          onClick={e => { e.stopPropagation(); onMoveDown() }}
          disabled={isLast}
          title="Move page down"
          className="w-6 h-6 flex items-center justify-center rounded-md text-muted hover:text-fg hover:bg-surface transition-colors disabled:opacity-30 disabled:cursor-not-allowed"
        >
          <ChevronDown size={11} />
        </button>
        <button
          onClick={e => { e.stopPropagation(); onDelete() }}
          title="Delete page"
          className="w-6 h-6 flex items-center justify-center rounded-md text-muted hover:text-red-500 hover:bg-red-50 transition-colors"
        >
          <Trash2 size={11} />
        </button>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// PagesRail — left sidebar showing page thumbnails + controls
// ---------------------------------------------------------------------------

function PagesRail({ pages, activePageId, onSelect, onAdd, onRemove, onMoveUp, onMoveDown, onToggleBreak }) {
  return (
    <aside
      className="w-[108px] shrink-0 flex flex-col bg-surface border-r border-border overflow-y-auto"
      data-testid="report-pages-rail"
      aria-label="Report pages"
    >
      {/* Rail header */}
      <div className="flex items-center justify-between px-2.5 h-9 border-b border-border shrink-0">
        <span className="text-[10px] font-semibold text-muted/70 uppercase tracking-widest">Pages</span>
        <button
          onClick={onAdd}
          title="Add page"
          className="w-6 h-6 flex items-center justify-center rounded-lg text-muted hover:text-primary hover:bg-primary/10 transition-colors"
          data-testid="report-add-page"
        >
          <Plus size={13} />
        </button>
      </div>

      {/* Thumbnails list */}
      <div className="flex flex-col gap-2 p-2 flex-1">
        {pages.length === 0 && (
          <div className="flex flex-col items-center justify-center py-6 gap-2">
            <FileText size={20} className="text-muted/30" />
            <p className="text-[10px] text-muted/50 text-center leading-snug">
              No pages yet.
            </p>
          </div>
        )}
        {pages.map((page, idx) => (
          <PagesThumbnail
            key={page.id}
            page={page}
            pageIndex={idx}
            active={page.id === activePageId}
            onClick={() => onSelect(page.id)}
            onMoveUp={() => onMoveUp(idx)}
            onMoveDown={() => onMoveDown(idx)}
            onDelete={() => onRemove(page.id)}
            onToggleBreak={() => onToggleBreak(page.id)}
            isFirst={idx === 0}
            isLast={idx === pages.length - 1}
          />
        ))}
      </div>

      {/* Add page footer shortcut */}
      {pages.length > 0 && (
        <div className="px-2 pb-2 shrink-0">
          <button
            onClick={onAdd}
            className="w-full flex items-center justify-center gap-1 h-8 rounded-xl border border-dashed border-border text-muted/60 hover:border-primary hover:text-primary text-[10px] font-medium transition-colors"
          >
            <Plus size={11} />
            Add page
          </button>
        </div>
      )}
    </aside>
  )
}

// ---------------------------------------------------------------------------
// DocCanvas — main export
// ---------------------------------------------------------------------------

/**
 * @param {{
 *   spec:              object,
 *   surfaceAPI:        object,
 *   selectedWidgetId?: string|null,
 *   onSelectWidget?:   (id: string|null) => void,
 * }} props
 */
export default function DocCanvas({ spec, surfaceAPI, setSpec, selectedWidgetId = null, onSelectWidget }) {
  const {
    reportSurface,
    addReportPage,
    removeReportPage,
    reorderReportPages,
    updateReportPage,
  } = surfaceAPI

  const pages = useMemo(() => reportSurface?.pages ?? [], [reportSurface])
  const [activePageId, setActivePageId] = useState(() => pages[0]?.id ?? null)

  // Keep activePageId valid when pages change
  const validActive = pages.find(p => p.id === activePageId)?.id ?? pages[0]?.id ?? null
  const effectiveActiveId = validActive

  // Export hints from spec.export (T5 — forward-compatible, may be absent)
  const exportConfig = spec?.export ?? {}
  const widgetHints = exportConfig.widget_hints ?? {}

  // ── Page management ────────────────────────────────────────────────────────

  const handleAddPage = useCallback(() => {
    const id = `page_${Date.now()}`
    addReportPage({ id, items: [], page_break: false })
    setActivePageId(id)
  }, [addReportPage])

  const handleRemovePage = useCallback((pageId) => {
    removeReportPage(pageId)
    // If we deleted the active page, select the previous one
    setActivePageId(prev => {
      if (prev !== pageId) return prev
      const idx = pages.findIndex(p => p.id === pageId)
      return pages[idx - 1]?.id ?? pages[idx + 1]?.id ?? null
    })
  }, [removeReportPage, pages])

  const handleMoveUp = useCallback((fromIdx) => {
    if (fromIdx === 0) return
    const ids = pages.map(p => p.id)
    const next = [...ids]
    ;[next[fromIdx - 1], next[fromIdx]] = [next[fromIdx], next[fromIdx - 1]]
    reorderReportPages(next)
  }, [pages, reorderReportPages])

  const handleMoveDown = useCallback((fromIdx) => {
    if (fromIdx >= pages.length - 1) return
    const ids = pages.map(p => p.id)
    const next = [...ids]
    ;[next[fromIdx], next[fromIdx + 1]] = [next[fromIdx + 1], next[fromIdx]]
    reorderReportPages(next)
  }, [pages, reorderReportPages])

  const handleToggleBreak = useCallback((pageId) => {
    const page = pages.find(p => p.id === pageId)
    if (!page) return
    updateReportPage(pageId, { page_break: !page.page_break })
  }, [pages, updateReportPage])

  const handleWidthChange = useCallback((pageId, widgetId, width) => {
    const page = pages.find(p => p.id === pageId)
    if (!page) return
    const items = page.items.map(it =>
      it.widgetId === widgetId ? { ...it, width } : it
    )
    updateReportPage(pageId, { items })
  }, [pages, updateReportPage])

  // ── Empty state ───────────────────────────────────────────────────────────

  if (pages.length === 0) {
    return (
      <div
        className="flex flex-1 min-h-0 overflow-hidden bg-bg"
        data-testid="report-canvas"
      >
        {/* Pages rail — empty */}
        <PagesRail
          pages={[]}
          activePageId={null}
          onSelect={setActivePageId}
          onAdd={handleAddPage}
          onRemove={handleRemovePage}
          onMoveUp={handleMoveUp}
          onMoveDown={handleMoveDown}
          onToggleBreak={handleToggleBreak}
        />

        {/* Empty state */}
        <div className="flex flex-1 flex-col items-center justify-center gap-4">
          <div className="w-14 h-14 rounded-2xl bg-surface border border-border flex items-center justify-center">
            <FileText size={24} className="text-muted/40" />
          </div>
          <div className="text-center space-y-1 max-w-xs">
            <h2 className="text-sm font-semibold text-fg">No pages yet</h2>
            <p className="text-xs text-muted/70 leading-relaxed">
              Add a page to start building your report. Widgets from the dashboard
              will flow here in document order.
            </p>
          </div>
          <button
            onClick={handleAddPage}
            className="flex items-center gap-2 h-9 px-4 rounded-xl bg-primary text-primary-fg text-sm font-medium hover:bg-primary/90 transition-colors focus:outline-none focus:ring-2 focus:ring-ring/60"
            data-testid="report-add-first-page"
          >
            <Plus size={15} />
            Add first page
          </button>
          {setSpec && canGenerateReport(spec) && (
            <button
              onClick={() => setSpec(prev => gridSpecToReport(prev, { force: !reportHasContent(prev) }))}
              className="flex items-center gap-2 h-8 px-3 rounded-xl border border-border text-muted hover:text-primary hover:border-primary text-xs font-medium transition-colors focus:outline-none focus:ring-2 focus:ring-ring/60"
              data-testid="generate-report-from-dashboard"
              title="Auto-generate report pages from dashboard widget layout"
            >
              <Wand2 size={13} />
              Generate from dashboard
            </button>
          )}
        </div>
      </div>
    )
  }

  return (
    <div
      className="flex flex-1 min-h-0 overflow-hidden bg-bg"
      data-testid="report-canvas"
    >
      {/* Left pages rail */}
      <PagesRail
        pages={pages}
        activePageId={effectiveActiveId}
        onSelect={setActivePageId}
        onAdd={handleAddPage}
        onRemove={handleRemovePage}
        onMoveUp={handleMoveUp}
        onMoveDown={handleMoveDown}
        onToggleBreak={handleToggleBreak}
      />

      {/* Center document canvas — scrollable A4-style column */}
      <div
        className="flex-1 overflow-y-auto px-8 py-6 bg-[#f0f2f5] dark:bg-[#1a1d23]"
        data-testid="report-doc-scroll"
      >
        {/* A4/Letter-proportioned page container */}
        <div
          className="mx-auto"
          style={{ maxWidth: 794 /* 210mm @ 96dpi ≈ 794px */ }}
        >
          {pages.map((page, idx) => (
            <div key={page.id}>
              {/* Page-break divider (visual only; CSS break is on the page itself) */}
              {idx > 0 && page.page_break && <PageBreakBand />}

              <DocPage
                page={page}
                pageIndex={idx}
                allWidgets={spec?.widgets ?? []}
                selectedWidgetId={selectedWidgetId}
                onSelectWidget={onSelectWidget ?? (() => {})}
                exportHints={widgetHints}
                onWidthChange={handleWidthChange}
                exportConfig={exportConfig}
              />
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}
