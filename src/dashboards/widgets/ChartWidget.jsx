/**
 * ChartWidget.jsx — Spec-driven chart widget for the SpecRenderer.
 *
 * Props
 * -----
 * widget  {object}  A spec Widget object with type === 'chart'.
 *                   Shape: { id, type, query_id, chart_type, encoding, props, params?, pos }
 *
 * encoding shape:
 *   {
 *     x:     string,                           // x-axis / category column
 *     y:     string | SeriesDef[],             // single col OR array for combo charts
 *     color: string,                           // optional categorical-color grouping
 *     stack: string,                           // optional stack-group id
 *   }
 *
 *   SeriesDef: { col: string, type?: 'bar'|'line'|'area'|'scatter', axis?: 'left'|'right' }
 *
 * props shape (all optional):
 *   {
 *     height:        number,          // chart height in px (default 260)
 *     stack:         boolean|string,  // true → 'total'; string → custom stack id
 *     series:        SeriesDef[],     // explicit per-series combo spec (overrides encoding.y array)
 *     secondaryAxis: string[],        // column names to bind to the right y-axis
 *   }
 *
 * Behaviour
 * ---------
 * - On mount (and when query_id or resolved params change) fetches data via
 *   runArrowQueryById(query_id, { namedParams }).
 * - Re-queries whenever any referenced variable changes via useResolvedParams.
 * - Builds an ECharts option via buildChartOption() — supports stacking, combo, dual y-axis.
 * - Shows a loading spinner, error state, or empty state as appropriate.
 * - Falls back gracefully to SAMPLE_TABLE when the query fails.
 * - Widgets without params behave identically to before (regression-safe).
 */

import { useState, useEffect, useMemo, useCallback } from 'react'
import { runArrowQueryById } from '../../lib/wasmRuntime.js'
import { runMetricQuery } from '../../lib/metricRuntime.js'
import { buildChartOption } from '../../viz/chartOption.js'
import EChart from '../../viz/EChart.jsx'
import { useResolvedParams, useSetVariable } from '../VariableStore.jsx'

export default function ChartWidget({ widget }) {
  const {
    query_id,
    chart_type = 'scatter',
    encoding = {},
    props: wProps = {},
    params: widgetParams,
    drilldown,
    metric,
  } = widget

  // Resolve x / y / color from encoding (backward-compatible)
  const xCol    = encoding.x || ''
  // encoding.y can be a string (simple) or an array (combo) — chartOption handles both
  const yCol    = typeof encoding.y === 'string' ? encoding.y : ''
  const colorCol = encoding.color || undefined

  // Resolve widget params against the variable store — re-renders when vars change.
  // Widgets with no params get {} and behave identically to pre-M14-C (regression-safe).
  const resolvedParams = useResolvedParams(widgetParams)

  const [table, setTable] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)

  useEffect(() => {
    // A widget binds to either a governed `metric` or a registered `query_id`.
    if (!metric && !query_id) return
    let cancelled = false

    async function fetchData() {
      setLoading(true)
      setError(null)
      try {
        // Governed metric path: server-side compile + RLS injection.
        // Otherwise the existing query_id path is used EXACTLY as before.
        const hasParams = Object.keys(resolvedParams).length > 0
        const { table: t, cacheStatus } = metric
          ? await runMetricQuery(metric)
          : await runArrowQueryById(
              query_id,
              hasParams ? { namedParams: resolvedParams } : undefined,
            )
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
  // resolvedParams + metric are new objects each render; JSON.stringify gives a
  // stable dep so the effect only re-fires when actual values change.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [query_id, JSON.stringify(metric), JSON.stringify(resolvedParams)])

  const height = wProps.height ?? 260

  // ── Drilldown (cross-widget filtering) ──────────────────────────────────
  // When widget.drilldown = { target_var, value_field? } is set, clicking a
  // data point writes the clicked value into the named dashboard variable so
  // other widgets bound to that variable re-query.
  const setVariable = useSetVariable()
  const onEvents = useMemo(() => {
    const targetVar = drilldown?.target_var
    if (!targetVar) return undefined
    return {
      click: (params) => {
        // Prefer an explicit value_field from the clicked row's data object,
        // else fall back to the category name (params.name = x value).
        const field = drilldown.value_field
        let val
        if (field && params?.data && typeof params.data === 'object' && field in params.data) {
          val = params.data[field]
        } else {
          val = params?.name ?? params?.value
        }
        if (val !== undefined && val !== null) setVariable(targetVar, val)
      },
    }
  }, [drilldown?.target_var, drilldown?.value_field, setVariable])

  // Build ECharts option — threading encoding + props through for advanced features
  const option = useMemo(() => {
    if (!table || !xCol) return null
    return buildChartOption({
      chartType: chart_type,
      table,
      x: xCol,
      y: yCol || undefined,
      color: colorCol,
      encoding,
      props: wProps,
    })
  }, [table, xCol, yCol, colorCol, chart_type, encoding, wProps])

  if (loading) {
    return (
      <div className="flex items-center justify-center h-full text-sm text-muted">
        <span className="animate-pulse">Loading chart…</span>
      </div>
    )
  }

  return (
    <div className="flex flex-col h-full overflow-hidden">
      {error && (
        <div className="px-3 py-1.5 text-xs border-b shrink-0"
          style={{ background: 'color-mix(in srgb, #f59e0b 8%, transparent)', color: '#d97706', borderColor: 'color-mix(in srgb, #f59e0b 20%, transparent)' }}>
          {error}
        </div>
      )}
      <div className="flex-1 min-h-0">
        {option
          ? <EChart option={option} height={height} onEvents={onEvents} />
          : (
            <div className="flex items-center justify-center h-full text-sm text-gray-400" style={{ height }}>
              No data — select columns to render the chart.
            </div>
          )
        }
      </div>
    </div>
  )
}
