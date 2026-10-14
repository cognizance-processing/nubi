/**
 * EChart.jsx — lightweight React wrapper around Apache ECharts.
 *
 * Props
 * -----
 * option   {object}            ECharts option object (required).
 * height   {number|string}     CSS height for the chart container (default 320).
 * theme    {string|object}     ECharts theme name or theme object (optional).
 * onEvents {Record<string,fn>} Map of ECharts event name → handler (optional).
 *                              Handler is called with (params, chartInstance).
 *
 * Behaviour
 * ---------
 * - Calls echarts.init() on a ref div once on mount; disposes on unmount.
 * - Calls chart.setOption(option) whenever `option` changes.
 * - ResizeObserver watches the container div and calls chart.resize() so the
 *   chart is always responsive to its parent (works on mobile without JS window resize).
 * - Registers event listeners from `onEvents` after init, removes them on unmount.
 * - Width is always 100%; height is controlled via the `height` prop.
 * - Touch-friendly: ECharts enables touch by default on canvas renderers.
 */

import { useEffect, useRef, useState } from 'react'
import * as echarts from 'echarts'
import { attachECharts } from '../lib/maps.js'

// Choropleth (`chart_type: 'map'`) geometry must be registered on the ECharts
// module before a map chart renders. This is the one place that holds the
// module, so hand it to the registry at import time; any GeoJSON registered
// before or after is flushed through.
attachECharts(echarts)

export default function EChart({ option, height = 320, theme, onEvents }: {
  option: Record<string, any>
  height?: number | string
  theme?: string | object
  onEvents?: Record<string, (params: any, chart: echarts.ECharts) => void>
}) {
  const containerRef = useRef<HTMLDivElement>(null)
  // ECharts throws for options its own validators reject (a sankey whose
  // source/target columns form a cycle, for example). That happens inside an
  // effect, where an uncaught throw unmounts the whole React tree — one
  // awkwardly-shaped result set would take the entire page down. Catch it and
  // render the reason in place of the chart instead.
  const [renderError, setRenderError] = useState<string | null>(null)
  const chartRef = useRef<echarts.ECharts | null>(null)    // echarts instance
  const eventsRef = useRef<Record<string, (params: any, chart: echarts.ECharts) => void> | null>(null)   // track bound events for cleanup

  // ----- Init / dispose lifecycle -----
  useEffect(() => {
    const container = containerRef.current
    if (!container) return

    const chart = echarts.init(container, theme ?? null, {
      renderer: 'canvas',
      useDirtyRect: true,   // performance hint
    })
    chartRef.current = chart

    // Bind events from onEvents prop
    if (onEvents) {
      eventsRef.current = onEvents
      for (const [eventName, handler] of Object.entries(onEvents)) {
        chart.on(eventName, (params) => handler(params, chart))
      }
    }

    // ResizeObserver for responsive resize
    const ro = new ResizeObserver(() => {
      if (!chart.isDisposed()) chart.resize()
    })
    ro.observe(container)

    return () => {
      ro.disconnect()

      // Remove event listeners if any
      if (eventsRef.current) {
        for (const eventName of Object.keys(eventsRef.current)) {
          chart.off(eventName)
        }
        eventsRef.current = null
      }

      chart.dispose()
      chartRef.current = null
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [theme]) // re-init only when theme changes

  // ----- Update option when it changes -----
  useEffect(() => {
    const chart = chartRef.current
    if (!chart || chart.isDisposed()) return
    // notMerge: false  → ECharts merges intelligently (faster for live updates)
    // lazyUpdate: false → render immediately
    try {
      chart.setOption(option, { notMerge: false, lazyUpdate: false })
      setRenderError(null)
    } catch (err) {
      // Clear the half-drawn canvas so the message isn't overlaid on a stale chart.
      try { chart.clear() } catch { /* disposed mid-update */ }
      setRenderError(err instanceof Error ? err.message : String(err))
    }
  }, [option])

  return (
    <div
      style={{
        width: '100%',
        height: typeof height === 'number' ? `${height}px` : height,
        display: 'block',
        position: 'relative',
      }}
    >
      <div ref={containerRef} style={{ width: '100%', height: '100%' }} />
      {renderError && (
        <div className="absolute inset-0 flex flex-col items-center justify-center gap-1 px-6 text-center bg-bg/90">
          <p className="text-xs font-medium text-fg">This data can't be drawn as this chart.</p>
          <p className="text-[11px] text-muted">{renderError}</p>
        </div>
      )}
    </div>
  )
}
