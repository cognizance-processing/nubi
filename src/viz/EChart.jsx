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

import { useEffect, useRef } from 'react'
import * as echarts from 'echarts'

/**
 * @param {{
 *   option: object,
 *   height?: number|string,
 *   theme?: string|object,
 *   onEvents?: Record<string, (params: any, chart: any) => void>
 * }} props
 */
export default function EChart({ option, height = 320, theme, onEvents }) {
  const containerRef = useRef(null)
  const chartRef = useRef(null)    // echarts instance
  const eventsRef = useRef(null)   // track bound events for cleanup

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
    chart.setOption(option, { notMerge: false, lazyUpdate: false })
  }, [option])

  return (
    <div
      ref={containerRef}
      style={{
        width: '100%',
        height: typeof height === 'number' ? `${height}px` : height,
        display: 'block',
      }}
    />
  )
}
