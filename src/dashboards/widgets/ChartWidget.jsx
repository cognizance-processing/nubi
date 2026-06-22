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

import { useState, useEffect, useMemo } from 'react'
import { runArrowQueryById } from '../../lib/wasmRuntime.js'
import { runMetricQuery } from '../../lib/metricRuntime.js'
import { buildChartOption } from '../../viz/chartOption.js'
import EChart from '../../viz/EChart.jsx'
import { useResolvedParams, useSetVariable } from '../VariableStore.jsx'
import { useCrossFilter } from '../CrossFilterContext.jsx'
import { useRefreshEpoch } from '../RefreshContext.jsx'
import Skeleton from '../../components/ui/Skeleton.jsx'
import EmptyState from '../../components/ui/EmptyState.jsx'

/**
 * @param {{ widget: object, providerTable?: import('apache-arrow').Table | null }} props
 *
 * providerTable — when supplied by SpecRenderer (BET-3 DataProvider path) the
 * widget skips its own query_id / metric fetch and renders from this table.
 * When absent the legacy query_id / metric path is used unchanged.
 */
export default function ChartWidget({ widget, providerTable = null }) {
  const {
    query_id,
    chart_type = 'scatter',
    encoding = {},
    props: wProps = {},
    params: widgetParams,
    drilldown,
    onClick: onClickSpec,   // widget.onClick → cross-filter / navigate spec
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

  // Board-level refresh epoch — when the board's auto-refresh timer fires this
  // increments and causes the widget to re-fetch its data.
  const refreshEpoch = useRefreshEpoch()

  const [table, setTable] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)

  // BET-3: when a providerTable is supplied, use it directly — skip the
  // query_id / metric fetch entirely. The provider fetch is shared across all
  // bound widgets (one call per provider, not one per widget).
  useEffect(() => {
    if (providerTable !== null) {
      setTable(providerTable)
      setLoading(false)
      setError(null)
    }
  }, [providerTable])

  useEffect(() => {
    // Skip own fetch when this widget is bound to a DataProvider (providerTable
    // is supplied by SpecRenderer). Also skip when there is no binding at all.
    if (providerTable !== null) return
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
  // refreshEpoch increments on board auto-refresh ticks.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [query_id, JSON.stringify(metric), JSON.stringify(resolvedParams), refreshEpoch, providerTable])

  const height = wProps.height ?? 260

  // ── Drilldown / cross-filter (cross-widget filtering + tab navigation) ─────
  // Supports two opt-in mechanisms — both are inactive unless configured:
  //
  //   1. Legacy `drilldown` — writes a single variable (backward-compatible).
  //      widget.drilldown = { target_var, value_field? }
  //
  //   2. New `onClick` spec — uses the CrossFilterContext bus (set var AND/OR
  //      navigate to a tab, and fires an optional external onCrossFilter callback).
  //      widget.onClick = { setVar?, valueField?, navigateTab? }
  //
  // Both can coexist; the legacy drilldown fires first.
  const setVariable   = useSetVariable()
  const { emit: emitCrossFilter } = useCrossFilter()

  const onEvents = useMemo(() => {
    const targetVar    = drilldown?.target_var
    const hasCrossFilter = !!(onClickSpec?.setVar || onClickSpec?.navigateTab)

    if (!targetVar && !hasCrossFilter) return undefined

    return {
      click: (params) => {
        // Legacy drilldown path (backward-compatible).
        if (targetVar) {
          const field = drilldown.value_field
          let val
          if (field && params?.data && typeof params.data === 'object' && field in params.data) {
            val = params.data[field]
          } else {
            val = params?.name ?? params?.value
          }
          if (val !== undefined && val !== null) setVariable(targetVar, val)
        }

        // New cross-filter / navigate path.
        if (hasCrossFilter) {
          emitCrossFilter(onClickSpec, params)
        }
      },
    }
  }, [
    drilldown?.target_var, drilldown?.value_field, setVariable,
    onClickSpec, emitCrossFilter,
  ])

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
      <div className="flex flex-col h-full p-3 gap-2" aria-busy="true" aria-label="Loading chart">
        {/* Mimic a bar-chart skeleton: axis area + bars */}
        <div className="flex-1 min-h-0 flex items-end gap-1.5 px-2 pb-2">
          {[55, 80, 40, 90, 65, 75, 50, 85].map((h, i) => (
            <Skeleton
              key={i}
              className="flex-1 rounded-sm"
              style={{ height: `${h}%` }}
            />
          ))}
        </div>
        <Skeleton className="h-2.5 w-32 mx-auto rounded" />
      </div>
    )
  }

  return (
    <div className="flex flex-col h-full overflow-hidden">
      {error && (
        <div
          className="px-3 py-1.5 text-xs border-b shrink-0 flex items-center gap-1.5"
          style={{
            background: 'color-mix(in srgb, #f59e0b 8%, transparent)',
            color: '#d97706',
            borderColor: 'color-mix(in srgb, #f59e0b 20%, transparent)',
          }}
          role="status"
        >
          <span aria-hidden="true">&#9888;</span>
          {error}
        </div>
      )}
      <div className="flex-1 min-h-0">
        {option
          ? <EChart option={option} height={height} onEvents={onEvents} />
          : (
            <EmptyState
              title="No data"
              description="Select columns to render the chart."
              compact
            />
          )
        }
      </div>
    </div>
  )
}
