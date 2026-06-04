/**
 * SpecRenderer.jsx — Read-only React Grid Layout renderer for a DashboardSpec.
 *
 * Props
 * -----
 * spec  {DashboardSpec}  The spec object to render (matches backend spec.py shape exactly).
 *
 * Behaviour
 * ---------
 * - Uses react-grid-layout ResponsiveGridLayout with useContainerWidth for responsive layout.
 * - isDraggable and isResizable are both false — this is a read-only viewer.
 * - Each widget is dispatched to ChartWidget, KpiWidget, or TableWidget based on widget.type.
 * - On small screens (sm breakpoint) all widgets stack in a single column.
 * - Converts the backend 1-based pos (x,y,w,h) to RGL's 0-based x,y.
 *
 * NOTE: react-grid-layout v2 no longer exports WidthProvider.
 * Instead we use the useContainerWidth hook and pass width as a prop.
 */

import 'react-grid-layout/css/styles.css'
import 'react-resizable/css/styles.css'

import { useMemo } from 'react'
import { ResponsiveGridLayout, useContainerWidth } from 'react-grid-layout'
import ChartWidget from './widgets/ChartWidget.jsx'
import KpiWidget from './widgets/KpiWidget.jsx'
import TableWidget from './widgets/TableWidget.jsx'

/** Map widget type to the right component. */
function WidgetComponent({ widget }) {
  switch (widget.type) {
    case 'chart': return <ChartWidget widget={widget} />
    case 'kpi':   return <KpiWidget widget={widget} />
    case 'table': return <TableWidget widget={widget} />
    default:
      return (
        <div className="flex items-center justify-center h-full text-sm text-muted">
          Unknown widget type: {widget.type}
        </div>
      )
  }
}

/**
 * Convert a spec widget list into RGL layout arrays per breakpoint.
 * Backend pos uses 1-based x and y (column / row start).
 * RGL uses 0-based x and y.
 */
function buildLayouts(widgets, cols) {
  const lg = widgets.map(w => ({
    i: w.id,
    x: Math.max(0, (w.pos?.x ?? 1) - 1),
    y: Math.max(0, (w.pos?.y ?? 1) - 1),
    w: Math.min(w.pos?.w ?? 4, cols),
    h: w.pos?.h ?? 4,
    isDraggable: false,
    isResizable: false,
  }))

  // Single-column layout for small screens
  const sm = widgets.map((w, idx) => ({
    i: w.id,
    x: 0,
    y: idx * (w.pos?.h ?? 4),
    w: 1,
    h: w.pos?.h ?? 4,
    isDraggable: false,
    isResizable: false,
  }))

  return { lg, md: lg, sm }
}

/**
 * @param {{ spec: object }} props
 */
export default function SpecRenderer({ spec }) {
  if (!spec) {
    return (
      <div className="flex items-center justify-center py-16 text-sm text-muted">
        No spec provided.
      </div>
    )
  }

  const cols = spec.layout?.cols ?? 12
  const rowHeight = spec.layout?.row_height ?? 60
  const widgets = spec.widgets ?? []

  // eslint-disable-next-line react-hooks/rules-of-hooks
  const layouts = useMemo(() => buildLayouts(widgets, cols), [widgets, cols])

  // eslint-disable-next-line react-hooks/rules-of-hooks
  const { width, containerRef } = useContainerWidth({ initialWidth: 1200 })

  const breakpoints = { lg: 1200, md: 768, sm: 480 }
  const colsCfg = { lg: cols, md: cols, sm: 1 }

  return (
    <div className="w-full" ref={containerRef}>
      {spec.title && (
        <h2 className="text-xl font-bold font-display text-fg px-1 mb-4">{spec.title}</h2>
      )}
      {widgets.length === 0 ? (
        <div className="flex items-center justify-center py-16 text-sm text-muted border-2 border-dashed border-border rounded-xl bg-surface">
          No widgets in this dashboard.
        </div>
      ) : (
        <ResponsiveGridLayout
          width={width}
          className="layout"
          layouts={layouts}
          breakpoints={breakpoints}
          cols={colsCfg}
          rowHeight={rowHeight}
          isDraggable={false}
          isResizable={false}
          margin={[12, 12]}
          containerPadding={[0, 0]}
        >
          {widgets.map(widget => (
            <div key={widget.id} className="overflow-hidden rounded-xl bg-surface border border-border shadow-sm">
              <WidgetComponent widget={widget} />
            </div>
          ))}
        </ResponsiveGridLayout>
      )}
    </div>
  )
}
