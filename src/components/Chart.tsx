/**
 * Chart.jsx — ECharts-based chart component (EDITOR-2B).
 *
 * Props
 * -----
 * table      {import('apache-arrow').Table}  Arrow Table to visualise.
 * xCol       {string}                        Column name for X axis.
 * yCol       {string}                        Column name for Y axis.
 * colorCol   {string|undefined}              Optional column for colour encoding.
 * chartType  {'scatter'|'line'|'bar'|'area'|'pie'}  Default: 'scatter'.
 * height     {number}                        Chart height in px.  Default: 320.
 *
 * Behaviour
 * ---------
 * - Builds an ECharts option via buildChartOption() and renders via <EChart>.
 * - Re-builds the option whenever table or column selections change.
 * - Shows a graceful empty state when table is absent or xCol/yCol are missing.
 * - The old regl/WebGL path (scatterRenderer.js) is kept on disk for the explicit
 *   1M-point GPU demo; Chart.jsx now defaults to ECharts for all chart types.
 *
 * Footer
 * ------
 * Shows column assignments and row count once a table is loaded.
 */

import { useMemo } from 'react'
import { buildChartOption } from '../viz/chartOption.js'
import EChart from '../viz/EChart.jsx'

// Default height in pixels
const DEFAULT_HEIGHT = 320

/**
 * @param {{
 *   table?: import('apache-arrow').Table,
 *   xCol?: string,
 *   yCol?: string,
 *   colorCol?: string,
 *   chartType?: 'scatter'|'line'|'bar'|'area'|'pie',
 *   height?: number,
 * }} props
 */
export default function Chart({ table, xCol, yCol, colorCol, chartType = 'scatter', height = DEFAULT_HEIGHT }) {
  // Build ECharts option (memoized — only re-computes when inputs change)
  const option = useMemo(() => {
    if (!table || !xCol || !yCol) return null
    return buildChartOption({ chartType, table, x: xCol, y: yCol, color: colorCol })
  }, [table, xCol, yCol, colorCol, chartType])

  const isEmpty = !table || !xCol || !yCol

  return (
    <div className="rounded-xl border border-gray-200 bg-white shadow-sm overflow-hidden">
      {/* Chart area */}
      <div className="relative bg-white" style={{ minHeight: height }}>
        {isEmpty ? (
          /* Empty state */
          <div
            className="flex items-center justify-center"
            style={{ height }}
          >
            <p className="text-sm text-gray-400">No data — select columns and click Render.</p>
          </div>
        ) : (
          <EChart option={option} height={height} />
        )}
      </div>

      {/* Footer — column assignments + row count */}
      {!isEmpty && table && (
        <div className="px-4 py-2 border-t border-gray-100 flex items-center gap-4 text-xs text-gray-500 flex-wrap">
          <span>
            <span className="font-medium text-gray-600">X:</span> {xCol}
          </span>
          <span>
            <span className="font-medium text-gray-600">Y:</span> {yCol}
          </span>
          {colorCol && (
            <span>
              <span className="font-medium text-gray-600">Color:</span> {colorCol}
            </span>
          )}
          <span>
            <span className="font-medium text-gray-600">Type:</span> {chartType}
          </span>
          <span className="ml-auto text-gray-400">
            {table.numRows.toLocaleString()} rows
          </span>
        </div>
      )}
    </div>
  )
}
