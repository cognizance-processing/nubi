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
import { hasMap, ensureMapRegistered } from '../../lib/maps.js'
import { resolveSpaTheme } from '../../lib/spaTheme.js'
import EChart from '../../viz/EChart.jsx'
import { useResolvedParams, useSetVariable } from '../VariableStore.jsx'
import { useCrossFilter } from '../CrossFilterContext.jsx'
import { useStepper } from '../StepperContext.jsx'
import { useRefreshEpoch } from '../RefreshContext.jsx'
import Skeleton from '../../components/ui/Skeleton.jsx'
import EmptyState from '../../components/ui/EmptyState.jsx'
import WidgetError from '../../components/app/WidgetError.jsx'
import { useTheme } from '../../contexts/ThemeContext.jsx'
import { adaptBackgroundColor, ensureReadable } from '../../lib/themeColor.js'

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
  // Bumped by the error state's Retry button to re-run the fetch effect.
  const [retryEpoch, setRetryEpoch] = useState(0)

  // Choropleth (chart_type: 'map') geometry may be registered lazily (see
  // lib/maps.js) — bump this once loading finishes so the option memo below
  // recomputes and picks up the now-registered map.
  // Falls back to 'world' to mirror buildMap()'s own default (chart-options.js)
  // — a map widget with no geography picked yet still needs something registered.
  const mapName = chart_type === 'map' ? (widget.config?.map || 'world') : undefined
  const [mapReadyEpoch, setMapReadyEpoch] = useState(0)
  useEffect(() => {
    if (!mapName || hasMap(mapName)) return
    let cancelled = false
    ensureMapRegistered(mapName).then(() => {
      if (!cancelled) setMapReadyEpoch(e => e + 1)
    })
    return () => { cancelled = true }
  }, [mapName])

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
        const { table: t, error: queryError } = metric
          ? await runMetricQuery(metric)
          : await runArrowQueryById(
              query_id,
              hasParams ? { namedParams: resolvedParams } : undefined,
            )
        if (cancelled) return
        // A failed query surfaces its real reason and renders NO chart — never
        // a chart of substitute rows (see wasmRuntime's SAMPLE_TABLE note).
        if (queryError) {
          setTable(null)
          setError(queryError.message)
        } else {
          setTable(t)
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
  }, [query_id, JSON.stringify(metric), JSON.stringify(resolvedParams), refreshEpoch, providerTable, retryEpoch])

  // Fill the card by default: grid cells have a fixed height, so '100%' sizes
  // the canvas to the widget instead of leaving dead space under a fixed 260px
  // chart. An explicit props.height still wins (and contexts without a sized
  // parent are caught by the min-height on the flex wrapper below).
  const height = (widget.props?.height) ?? '100%'

  // Drilldown / cross-filter
  const setVariable = useSetVariable()
  const { emit: emitCrossFilter } = useCrossFilter()
  const { advance: stepAdvance } = useStepper()

  const onEvents = useMemo(() => {
    const targetVar = drilldown?.target_var
    const hasCrossFilter = !!(onClickSpec?.setVar || onClickSpec?.navigateTab)
    // `stepNext` advances an enclosing stepper widget (no-op outside one), so a
    // click can both set the drill-down value and move to the next step.
    const stepsNext = !!onClickSpec?.stepNext
    if (!targetVar && !hasCrossFilter && !stepsNext) return undefined
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
        // Advance last, so the variable the next step reads is already set.
        if (stepsNext) stepAdvance()
      },
    }
  }, [
    drilldown?.target_var, drilldown?.value_field, setVariable,
    onClickSpec, emitCrossFilter, stepAdvance,
  ])

  // Build ECharts option via the shared builder
  const option = useMemo(() => {
    if (!table) return null
    // Wait for lazily-loaded map geometry before building a choropleth option
    // — ECharts silently no-ops a series referencing an unregistered map name.
    if (mapName && !hasMap(mapName)) return null
    const encoding = resolveEncoding(widget)
    const config = resolveConfig(widget)
    let theme = resolveSpaTheme(isDark, Array.isArray(config.palette) ? config.palette : null)
    // A widget-level text color (Appearance → Text color) is a plain CSS
    // `color` on the card wrapper — that reaches ordinary DOM text (KPI
    // numbers, table cells) via inheritance, but ECharts renders axis labels,
    // legends, titles, and centerLabel by painting theme.fg/fgMuted onto a
    // canvas, entirely outside the CSS box. Feed the same override in here so
    // "Text color" means the same thing for every widget type.
    // Contrast-check the author's text color against the card's ACTUAL
    // rendered background (SpecRenderer adapts widget.style.background the
    // same way — see lib/themeColor.js), not the raw authored value, so a
    // fixed dark color doesn't go illegible once a light-neutral card
    // becomes the dark surface token.
    const rawBg = typeof widget.style?.background === 'string' ? widget.style.background : undefined
    const effectiveBg = widget.style?.themeAdapt === false
      ? rawBg
      : (adaptBackgroundColor(rawBg, isDark ? 'dark' : 'light').hex ?? rawBg)
    const textColorOverride = typeof widget.style?.color === 'string' && widget.style.color
      ? ensureReadable(widget.style.color, effectiveBg)
      : null
    if (textColorOverride) {
      // Deliberately don't touch tooltipBg/tooltipFg here: the tooltip is a
      // floating DOM surface with its own background, not on-canvas text, so
      // it must keep the base theme's readable bg/fg pair regardless of the
      // author's per-widget text color override.
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
  }, [table, chart_type, JSON.stringify(widget.encoding), JSON.stringify(widget.config), JSON.stringify(widget.props), widget.style?.color, widget.style?.background, widget.style?.themeAdapt, isDark, mapName, mapReadyEpoch])

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

  // A failed query replaces the body outright — rendering a chart alongside an
  // error would be showing numbers we know are wrong.
  if (error) {
    return (
      <WidgetError
        message={error}
        queryId={query_id}
        onRetry={() => setRetryEpoch(e => e + 1)}
      />
    )
  }

  return (
    <div className="flex flex-col h-full overflow-hidden">
      {/* The 180px floor keeps a chart from collapsing when the parent has no
          definite height, but as a hard value it also forced a chart TALLER
          than a short grid cell, so the cell clipped it — a small gauge tile
          rendered a 180px ring inside 111px and lost its bottom third.
          `min()` keeps the floor only while it fits the space available. */}
      <div className="flex-1 min-h-0" style={{ minHeight: 'min(180px, 100%)' }}>
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
