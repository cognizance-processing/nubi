/**
 * ChartWidget.jsx — Spec-driven chart widget for the SpecRenderer.
 *
 * Renders via the shared buildChartOption() builder from
 * embed/widgets/chart-options.js (re-exported through src/lib/chartOptionShared.js).
 * This is the single source of truth for ECharts option generation — identical
 * to the <nubi-chart> embed.
 *
 * Props
 * -----
 * widget  {object}  A spec Widget object with type === 'chart'.
 *                   Shape: { id, type, query_id, chart_type, encoding, props, config, params?, pos }
 *
 * encoding shape:
 *   {
 *     x:      string,   // x-axis / category column
 *     y:      string,   // primary y column (single-series path)
 *     y2:     string,   // secondary y column (dual-axis)
 *     color:  string,   // categorical grouping
 *     value:  string,   // for pie/donut/gauge/funnel/treemap
 *     size:   string,   // for bubble
 *     source: string,   // for sankey
 *     target: string,   // for sankey
 *     low:    string,   // for candlestick
 *     high:   string,   // for candlestick
 *     open:   string,   // for candlestick
 *     close:  string,   // for candlestick
 *     lower:  string,   // for fan (confidence lower bound)
 *     upper:  string,   // for fan (confidence upper bound)
 *   }
 *
 * config shape (all optional — persisted in widget.config):
 *   {
 *     title, subtitle, legend, tooltip, palette, stack, orientation,
 *     smooth, areaOpacity, dataLabels, animation, grid,
 *     xAxis, yAxis, y2Axis,         // AxisSpec { label, type, log, min, max, format, rotate }
 *     referenceLines, annotations,  // markLine / markPoint entries
 *     locale, echarts,
 *   }
 *
 * props shape (legacy, for backward-compat height/max):
 *   { height, max, min }
 *
 * Behaviour
 * ---------
 * - On mount fetches data via runArrowQueryById(query_id, { namedParams }).
 * - Re-queries whenever any referenced variable changes via useResolvedParams.
 * - Builds an ECharts option via the shared buildChartOption().
 * - Shows loading skeleton, error banner, or empty state as appropriate.
 * - Light/dark via ThemeContext → resolveSpaTheme().
 */

import { useState, useEffect, useMemo } from 'react'
import { runArrowQueryById } from '../../lib/wasmRuntime.js'
import { runMetricQuery } from '../../lib/metricRuntime.js'
import { buildChartOption } from '../../lib/chartOptionShared.js'
import { resolveSpaTheme } from '../../lib/spaTheme.js'
import EChart from '../../viz/EChart.jsx'
import { useResolvedParams, useSetVariable } from '../VariableStore.jsx'
import { useCrossFilter } from '../CrossFilterContext.jsx'
import { useRefreshEpoch } from '../RefreshContext.jsx'
import Skeleton from '../../components/ui/Skeleton.jsx'
import EmptyState from '../../components/ui/EmptyState.jsx'
import { useTheme } from '../../contexts/ThemeContext.jsx'

/**
 * Translate a widget spec into the encoding object expected by buildChartOption.
 * Merges widget.encoding directly (it already matches the shared API shape).
 * Backward-compat: widget.props.series / secondaryAxis → y2 in encoding.
 */
function resolveEncoding(widget) {
  const enc = { ...(widget.encoding ?? {}) }
  const wProps = widget.props ?? {}

  // Legacy: props.secondaryAxis array → take first column as y2 if not already set
  if (!enc.y2 && Array.isArray(wProps.secondaryAxis) && wProps.secondaryAxis.length) {
    enc.y2 = wProps.secondaryAxis[0]
  }

  // Legacy: props.series / encoding.y as array → flatten to y (take first col)
  // The shared builder handles single-column y; multi-series comes via encoding.y2
  // for dual-axis or via the color grouping mechanism.
  if (Array.isArray(enc.y)) {
    const first = enc.y[0]
    enc.y = first?.col ?? (typeof first === 'string' ? first : undefined)
  }
  if (Array.isArray(wProps.series) && wProps.series.length && !enc.y) {
    enc.y = wProps.series[0]?.col ?? ''
  }

  return enc
}

/**
 * Merge widget.config and widget.props into the config object for buildChartOption.
 * widget.config is the canonical store; widget.props holds legacy keys (height, max, min).
 */
function resolveConfig(widget) {
  const cfg = { ...(widget.config ?? {}) }
  const wProps = widget.props ?? {}

  // Legacy stack in props
  if (cfg.stack === undefined && (wProps.stack === true || typeof wProps.stack === 'string')) {
    cfg.stack = wProps.stack
  }
  // Legacy gauge max/min
  if (cfg.yAxis === undefined) {
    if (wProps.max !== undefined || wProps.min !== undefined) {
      cfg.yAxis = {}
      if (wProps.max !== undefined) cfg.yAxis.max = wProps.max
      if (wProps.min !== undefined) cfg.yAxis.min = wProps.min
    }
  }

  return cfg
}

/**
 * @param {{ widget: object, providerTable?: import('apache-arrow').Table | null }} props
 *
 * providerTable — when supplied by SpecRenderer (BET-3 DataProvider path) the
 * widget skips its own query_id / metric fetch and renders from this table.
 */
export default function ChartWidget({ widget, providerTable = null }) {
  const {
    query_id,
    chart_type = 'scatter',
    params: widgetParams,
    drilldown,
    onClick: onClickSpec,
    metric,
  } = widget

  const { theme: appTheme } = useTheme()
  const isDark = appTheme === 'dark'

  const resolvedParams = useResolvedParams(widgetParams)
  const refreshEpoch = useRefreshEpoch()

  const [table, setTable] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)

  // BET-3: DataProvider path — use the shared table directly
  useEffect(() => {
    if (providerTable !== null) {
      setTable(providerTable)
      setLoading(false)
      setError(null)
    }
  }, [providerTable])

  useEffect(() => {
    if (providerTable !== null) return
    if (!metric && !query_id) return
    let cancelled = false

    async function fetchData() {
      setLoading(true)
      setError(null)
      try {
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
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [query_id, JSON.stringify(metric), JSON.stringify(resolvedParams), refreshEpoch, providerTable])

  // Fill the card by default: grid cells have a fixed height, so '100%' sizes
  // the canvas to the widget instead of leaving dead space under a fixed 260px
  // chart. An explicit props.height still wins (and contexts without a sized
  // parent are caught by the min-height on the flex wrapper below).
  const height = (widget.props?.height) ?? '100%'

  // Drilldown / cross-filter
  const setVariable = useSetVariable()
  const { emit: emitCrossFilter } = useCrossFilter()

  const onEvents = useMemo(() => {
    const targetVar = drilldown?.target_var
    const hasCrossFilter = !!(onClickSpec?.setVar || onClickSpec?.navigateTab)
    if (!targetVar && !hasCrossFilter) return undefined
    return {
      click: (params) => {
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
        if (hasCrossFilter) {
          emitCrossFilter(onClickSpec, params)
        }
      },
    }
  }, [
    drilldown?.target_var, drilldown?.value_field, setVariable,
    onClickSpec, emitCrossFilter,
  ])

  // Build ECharts option via the shared builder
  const option = useMemo(() => {
    if (!table) return null
    const encoding = resolveEncoding(widget)
    const config = resolveConfig(widget)
    let theme = resolveSpaTheme(isDark, Array.isArray(config.palette) ? config.palette : null)
    // A widget-level text color (Appearance → Text color) is a plain CSS
    // `color` on the card wrapper — that reaches ordinary DOM text (KPI
    // numbers, table cells) via inheritance, but ECharts renders axis labels,
    // legends, titles, and centerLabel by painting theme.fg/fgMuted onto a
    // canvas, entirely outside the CSS box. Feed the same override in here so
    // "Text color" means the same thing for every widget type.
    const textColorOverride = typeof widget.style?.color === 'string' && widget.style.color
      ? widget.style.color
      : null
    if (textColorOverride) {
      theme = { ...theme, fg: textColorOverride, fgMuted: textColorOverride }
    }

    const opt = buildChartOption({
      type: chart_type,
      table,
      encoding,
      config,
      theme,
    })
    return opt
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [table, chart_type, JSON.stringify(widget.encoding), JSON.stringify(widget.config), JSON.stringify(widget.props), widget.style?.color, isDark])

  if (loading) {
    return (
      <div className="flex flex-col h-full p-3 gap-2" aria-busy="true" aria-label="Loading chart">
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
      <div className="flex-1 min-h-0" style={{ minHeight: 180 }}>
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
