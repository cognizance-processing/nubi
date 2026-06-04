/**
 * ChartWidget.jsx — Spec-driven chart widget for the SpecRenderer.
 *
 * Props
 * -----
 * widget  {object}  A spec Widget object with type === 'chart'.
 *                   Shape: { id, type, query_id, chart_type, encoding:{x,y,color}, props, pos }
 *
 * Behaviour
 * ---------
 * - On mount (and when query_id changes) calls runArrowQuery(query_id) to fetch data.
 * - Renders <Chart> with the loaded Arrow Table and encoding columns.
 * - Shows a loading spinner, error state, or empty state as appropriate.
 * - Falls back gracefully to SAMPLE_TABLE when the query fails.
 */

import { useState, useEffect } from 'react'
import { runArrowQueryById } from '../../lib/wasmRuntime.js'
import Chart from '../../components/Chart.jsx'

export default function ChartWidget({ widget }) {
  const { query_id, chart_type = 'scatter', encoding = {}, props: wProps = {} } = widget
  const xCol = encoding.x || ''
  const yCol = encoding.y || ''
  const colorCol = encoding.color || undefined

  const [table, setTable] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)

  useEffect(() => {
    if (!query_id) return
    let cancelled = false

    async function fetchData() {
      setLoading(true)
      setError(null)
      try {
        const { table: t, cacheStatus } = await runArrowQueryById(query_id)
        if (!cancelled) {
          setTable(t)
          if (cacheStatus === 'SAMPLE') {
            setError('Using sample data — query unavailable.')
          }
        }
      } catch (err) {
        if (!cancelled) setError(err.message ?? 'Query failed.')
      } finally {
        if (!cancelled) setLoading(false)
      }
    }

    fetchData()
    return () => { cancelled = true }
  }, [query_id])

  const height = wProps.height ?? 260

  if (loading) {
    return (
      <div className="flex items-center justify-center h-full text-sm text-muted">
        <span className="animate-pulse">Loading chart…</span>
      </div>
    )
  }

  return (
    <div className="flex flex-col h-full overflow-hidden bg-surface">
      {error && (
        <div className="px-3 py-1.5 text-xs border-b shrink-0"
          style={{ background: 'color-mix(in srgb, #f59e0b 8%, transparent)', color: '#d97706', borderColor: 'color-mix(in srgb, #f59e0b 20%, transparent)' }}>
          {error}
        </div>
      )}
      <div className="flex-1 min-h-0">
        <Chart
          table={table}
          xCol={xCol || undefined}
          yCol={yCol || undefined}
          colorCol={colorCol}
          chartType={chart_type}
          height={height}
        />
      </div>
    </div>
  )
}
