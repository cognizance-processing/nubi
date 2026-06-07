/**
 * WidgetToolbar.jsx — Per-widget export action bar.
 *
 * Renders a small, icon-only toolbar (positioned top-right inside a widget
 * wrapper) that exposes CSV, PNG, and PDF export actions.  Uses icons from
 * `lucide-react` which is already a project dependency.
 *
 * Props
 * -----
 * onExportCsv  {() => void}  Called when the user clicks "Download CSV".
 *                             Pass `undefined` or omit to hide the CSV button.
 * onExportPng  {() => void}  Called when the user clicks "Download PNG".
 *                             Pass `undefined` or omit to hide the PNG button.
 * onExportPdf  {() => void}  Called when the user clicks "Download PDF".
 *                             Pass `undefined` or omit to hide the PDF button.
 * className    {string}      Additional Tailwind classes for the toolbar wrapper.
 *
 * Usage — mounting in a widget wrapper
 * -------------------------------------
 * Integration into a widget card happens in the WRAPPER layer (SpecRenderer
 * or a future WidgetCard component), NOT inside the widget's own JSX.  This
 * keeps widget internals (ChartWidget, TableWidget, …) untouched.
 *
 * Example wrapper pattern:
 *
 *   import { useRef, useState } from 'react'
 *   import * as echarts from 'echarts'
 *   import WidgetToolbar from '../dashboards/WidgetToolbar.jsx'
 *   import { arrowTableToCSV, downloadCSV, chartToPNG } from '../lib/exports.js'
 *
 *   function WidgetCard({ widget, table, echartsRef }) {
 *     return (
 *       <div className="relative group border rounded-lg p-2">
 *         <div className="absolute top-1 right-1 z-10 opacity-0 group-hover:opacity-100 transition-opacity">
 *           <WidgetToolbar
 *             onExportCsv={table ? () => downloadCSV(`${widget.id}.csv`, arrowTableToCSV(table)) : undefined}
 *             onExportPng={echartsRef.current ? () => chartToPNG(echartsRef.current, `${widget.id}.png`) : undefined}
 *           />
 *         </div>
 *         {/* the actual widget renders here */}
 *       </div>
 *     )
 *   }
 *
 * The toolbar is hidden by default and fades in on hover via Tailwind's
 * `group` / `group-hover` utilities, keeping the chart canvas uncluttered.
 */

import { Download, Image, FileText } from 'lucide-react'

/**
 * @param {{
 *   onExportCsv?: () => void,
 *   onExportPng?: () => void,
 *   onExportPdf?: () => void,
 *   className?: string,
 * }} props
 */
export default function WidgetToolbar({
  onExportCsv,
  onExportPng,
  onExportPdf,
  className = '',
}) {
  const hasAny = onExportCsv || onExportPng || onExportPdf
  if (!hasAny) return null

  return (
    <div
      className={`flex items-center gap-0.5 rounded-md border bg-surface shadow-sm ${className}`}
      role="toolbar"
      aria-label="Widget export actions"
    >
      {onExportCsv && (
        <ToolbarButton
          onClick={onExportCsv}
          title="Download CSV"
          aria-label="Download CSV"
        >
          <Download size={13} />
          <span className="sr-only">CSV</span>
        </ToolbarButton>
      )}

      {onExportPng && (
        <ToolbarButton
          onClick={onExportPng}
          title="Download PNG"
          aria-label="Download PNG"
        >
          <Image size={13} />
          <span className="sr-only">PNG</span>
        </ToolbarButton>
      )}

      {onExportPdf && (
        <ToolbarButton
          onClick={onExportPdf}
          title="Download PDF"
          aria-label="Download PDF"
        >
          <FileText size={13} />
          <span className="sr-only">PDF</span>
        </ToolbarButton>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Internal button primitive
// ---------------------------------------------------------------------------

function ToolbarButton({ onClick, title, children, ...rest }) {
  return (
    <button
      type="button"
      onClick={onClick}
      title={title}
      className={[
        'inline-flex items-center justify-center',
        'h-6 w-6 rounded',
        'text-muted hover:text-foreground',
        'hover:bg-accent',
        'transition-colors duration-100',
        'focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring',
      ].join(' ')}
      {...rest}
    >
      {children}
    </button>
  )
}
