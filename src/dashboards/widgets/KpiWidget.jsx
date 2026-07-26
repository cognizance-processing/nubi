/**
 * KpiWidget.jsx — Spec-driven KPI card widget for the SpecRenderer.
 *
 * Props
 * -----
 * widget  {object}  A spec Widget object with type === 'kpi'.
 *                   Shape: { id, type, query_id,
 *                            encoding:{ value, compare?, spark? },
 *                            props:{ label, format, deltaFormat? },
 *                            params?, pos }
 *
 * Encoding
 * --------
 * - encoding.value   {string}  Column whose first row is the headline number.
 * - encoding.compare {string}  Optional. Column whose first row is the comparison
 *                              baseline; the delta (headline − baseline) is shown
 *                              with an up/down arrow and colour.
 * - encoding.spark   {string}  Optional. Numeric column rendered as a tiny
 *                              axis-less ECharts sparkline beneath the number.
 *
 * Props
 * -----
 * - props.label       {string}  Card label (defaults to a prettified value col).
 * - props.format      {string}  Headline number format
 *                              (number|integer|percent|percent100|currency);
 *                              percent expects a 0-1 fraction, percent100 a
 *                              value already on a 0-100 scale.
 * - props.deltaFormat {string}  Delta display: 'percent' (default) or 'absolute'.
 * - props.labelPosition {string} 'bottom' (default — value first) or 'top'
 *                              (label above the value, stat-tile style).
 * - props.progress    {object}  Optional progress bar: { segments, target, style }.
 *                              Fraction filled = value / target (share the same scale,
 *                              e.g. target: 100 for a 0-100 percent value).
 *                              style: 'segments' (default pips) or 'bar' (continuous meter).
 *
 * Behaviour
 * ---------
 * - Fetches query_id via runArrowQueryById, threading resolved params as { namedParams }.
 * - Re-queries whenever resolved params change (via useResolvedParams dependency).
 * - Reads the first row of the encoding.value column as the primary number.
 * - Optionally shows a delta vs encoding.compare and a sparkline from encoding.spark.
 * - Shows loading skeleton and error notice gracefully.
 * - With compare/spark unset the card renders exactly as before (regression-safe).
 */

import { useState, useEffect, useMemo } from 'react'
import { runArrowQueryById } from '../../lib/wasmRuntime.js'
import { runMetricQuery } from '../../lib/metricRuntime.js'
import { useResolvedParams } from '../VariableStore.jsx'
import EChart from '../../viz/EChart.jsx'
import { applySignal } from '../../viz/signals.js'
import { useRefreshEpoch } from '../RefreshContext.jsx'
import Skeleton from '../../components/ui/Skeleton.jsx'
import Badge from '../../components/ui/Badge.jsx'
import WidgetError from '../../components/app/WidgetError.jsx'
import { findKpiIcon } from '../kpiIcons.jsx'

/** Format a raw value for display. */
function formatValue(raw, format) {
  if (raw == null) return '—'
  const num = Number(raw)
  if (Number.isNaN(num)) return String(raw)

  switch (format) {
    case 'integer':
      return num.toLocaleString(undefined, { maximumFractionDigits: 0 })
    case 'percent':
      return `${(num * 100).toFixed(1)}%`
    case 'percent100':
      // value is already on a 0-100 scale (common for migrated legacy queries)
      return `${num.toFixed(1)}%`
    case 'currency':
      return `$${num.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
    default:
      // 'number' or unset — smart compact formatting
      if (Math.abs(num) >= 1_000_000) return `${(num / 1_000_000).toFixed(1)}M`
      if (Math.abs(num) >= 1_000) return `${(num / 1_000).toFixed(1)}K`
      return num.toLocaleString(undefined, { maximumFractionDigits: 2 })
  }
}

/**
 * Compute a delta descriptor from a headline and a baseline value.
 * Returns null when either value is missing/non-numeric.
 *
 * @param {*} value
 * @param {*} baseline
 * @param {'percent'|'absolute'} deltaFormat
 * @returns {{ text: string, direction: 'up'|'down'|'flat' } | null}
 */
function computeDelta(value, baseline, deltaFormat) {
  if (value == null || baseline == null) return null
  const v = Number(value)
  const b = Number(baseline)
  if (Number.isNaN(v) || Number.isNaN(b)) return null

  const diff = v - b
  const direction = diff > 0 ? 'up' : diff < 0 ? 'down' : 'flat'
  const arrow = direction === 'up' ? '▲' : direction === 'down' ? '▼' : '→'

  let text
  if (deltaFormat === 'absolute') {
    const abs = Math.abs(diff)
    const compact = abs >= 1_000_000 ? `${(abs / 1_000_000).toFixed(1)}M`
      : abs >= 1_000 ? `${(abs / 1_000).toFixed(1)}K`
      : abs.toLocaleString(undefined, { maximumFractionDigits: 2 })
    text = `${arrow} ${compact}`
  } else {
    // percent (default)
    const pct = b === 0 ? (diff === 0 ? 0 : Infinity) : (diff / Math.abs(b)) * 100
    const pctText = Number.isFinite(pct) ? `${Math.abs(pct).toFixed(1)}%` : '—'
    text = `${arrow} ${pctText}`
  }
  return { text, direction }
}

const DELTA_COLORS = { up: '#10b981', down: '#ef4444', flat: '#9ca3af' }

/** Build a minimal axis-less sparkline ECharts option from a numeric array. */
function sparkOption(values) {
  return {
    backgroundColor: 'transparent',
    animation: false,
    grid: { top: 2, right: 2, bottom: 2, left: 2 },
    xAxis: { type: 'category', show: false, boundaryGap: false,
      data: values.map((_, i) => i) },
    yAxis: { type: 'value', show: false, scale: true },
    tooltip: { show: false },
    series: [{
      type: 'line',
      data: values,
      smooth: true,
      symbol: 'none',
      lineStyle: { width: 1.5, color: '#6366f1' },
      areaStyle: { color: 'rgba(99,102,241,0.15)' },
    }],
  }
}

/**
 * @param {{ widget: object, providerTable?: import('apache-arrow').Table | null }} props
 *
 * providerTable — when supplied by SpecRenderer (BET-3 DataProvider path) the
 * widget skips its own query_id / metric fetch and renders from this table.
 * When absent the legacy query_id / metric path is used unchanged.
 */
export default function KpiWidget({ widget, providerTable = null }) {
  const { query_id, encoding = {}, props: wProps = {}, params: widgetParams, metric } = widget
  const valueCol = encoding.value || encoding.y || encoding.x || ''
  const compareCol = encoding.compare || ''
  const sparkCol = encoding.spark || ''
  const label = wProps.label || (valueCol ? valueCol.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase()) : 'KPI')
  const format = wProps.format || 'number'
  const deltaFormat = wProps.deltaFormat || 'percent'
  // 'top' renders the label as a small uppercase eyebrow above the value
  // (stat-tile style); default 'bottom' keeps the historical value-first layout.
  const labelOnTop = wProps.labelPosition === 'top'
  // Signal rules for threshold coloring: widget.signalRules or widget.props.signalRules.
  const signalRules = widget.signalRules ?? wProps.signalRules ?? null

  // Resolve widget params against the variable store. Re-renders (and re-queries)
  // whenever any referenced variable changes. Widgets with no params get {}.
  const resolvedParams = useResolvedParams(widgetParams)

  // Board-level refresh epoch for auto-refresh polling.
  const refreshEpoch = useRefreshEpoch()

  const [value, setValue] = useState(null)
  const [compareValue, setCompareValue] = useState(null)
  const [sparkValues, setSparkValues] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  // Bumped by the error state's Retry button to re-run the fetch effect.
  const [retryEpoch, setRetryEpoch] = useState(0)

  /**
   * Extract KPI values from an Arrow table (shared by both provider and legacy paths).
   */
  function extractFromTable(table) {
    if (table && table.numRows > 0 && valueCol) {
      const col = table.getChild(valueCol)
      setValue(col ? col.get(0) : null)
    } else if (table && table.numRows > 0) {
      setValue(table.numRows)
    }
    if (table && table.numRows > 0 && compareCol) {
      const cmp = table.getChild(compareCol)
      setCompareValue(cmp ? cmp.get(0) : null)
    } else {
      setCompareValue(null)
    }
    if (table && table.numRows > 0 && sparkCol) {
      const sp = table.getChild(sparkCol)
      if (sp) {
        const arr = sp.toArray()
        const nums = new Array(arr.length)
        for (let i = 0; i < arr.length; i++) {
          const v = arr[i]
          nums[i] = v == null ? 0 : Number(v)
        }
        setSparkValues(nums)
      } else {
        setSparkValues(null)
      }
    } else {
      setSparkValues(null)
    }
  }

  // BET-3: when a providerTable is supplied, use it directly — skip the
  // query_id / metric fetch entirely.
  useEffect(() => {
    if (providerTable !== null) {
      extractFromTable(providerTable)
      setLoading(false)
      setError(null)
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [providerTable, valueCol, compareCol, sparkCol])

  useEffect(() => {
    // Skip own fetch when this widget is bound to a DataProvider.
    if (providerTable !== null) return
    // A widget binds to either a governed `metric` or a registered `query_id`.
    if (!metric && !query_id) return
    let cancelled = false

    async function fetchData() {
      setLoading(true)
      setError(null)
      try {
        // Governed metric path: server-side compile + RLS injection.
        // Otherwise the existing query_id path is used EXACTLY as before
        // (resolved params threaded as { namedParams }; empty {} is a no-op).
        const hasParams = Object.keys(resolvedParams).length > 0
        const { table, error: queryError } = metric
          ? await runMetricQuery(metric)
          : await runArrowQueryById(
              query_id,
              hasParams ? { namedParams: resolvedParams } : undefined,
            )
        if (cancelled) return
        // Failed query → the real reason, not a headline number we invented.
        if (queryError) {
          setError(queryError.message)
        } else {
          extractFromTable(table)
        }
      } catch (err) {
        if (!cancelled) setError(err.message ?? 'Query failed.')
      } finally {
        if (!cancelled) setLoading(false)
      }
    }

    fetchData()
    return () => { cancelled = true }
  // resolvedParams is a new object each render when vars change; JSON.stringify gives
  // a stable dependency so the effect only re-fires when the actual values differ.
  // refreshEpoch increments on board auto-refresh ticks.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [query_id, JSON.stringify(metric), valueCol, compareCol, sparkCol, JSON.stringify(resolvedParams), refreshEpoch, providerTable, retryEpoch])

  const delta = useMemo(
    () => (compareCol ? computeDelta(value, compareValue, deltaFormat) : null),
    [value, compareValue, compareCol, deltaFormat],
  )

  const spark = useMemo(
    () => (sparkValues && sparkValues.length > 1 ? sparkOption(sparkValues) : null),
    [sparkValues],
  )

  // Evaluate signal rules against the headline value for threshold coloring.
  const signal = useMemo(
    () => applySignal(value, signalRules),
    [value, signalRules],
  )

  // Number vs progress colouring (decoupled so a headline can stay neutral/ink
  // while the progress bar carries a separate accent):
  //   - the headline NUMBER follows the card's own text colour (widget.style.color),
  //     falling back to props.accentColor when no text colour is set;
  //   - the PROGRESS pips prefer an explicit props.accentColor, falling back to
  //     the card text colour, then the default indigo.
  // A matched signal (threshold colouring) overrides both. When style.color +
  // accentColor are the same (the common case) both stay in sync — backward
  // compatible with tiles that set one colour for the whole card.
  const valueColor = signal.matched ? signal.color : (widget.style?.color ?? wProps.accentColor)
  const progressColor = signal.matched ? signal.color : (wProps.accentColor ?? widget.style?.color ?? '#6366f1')

  // Optional Phosphor icon badge (props.icon = a key from kpiIcons.jsx). The
  // badge tint follows the same accent chain as the progress bar, and a matched
  // signal recolors it too so it stays consistent with the headline. Rendered
  // in a translucent chip (color-mix keeps it readable on any card style,
  // including the frosted Glass preset) above the value/eyebrow.
  const IconCmp = findKpiIcon(wProps.icon)
  const iconTint = signal.matched ? signal.color
    : (wProps.accentColor ?? (typeof widget.style?.color === 'string' ? widget.style.color : null) ?? '#6366f1')

  // A tile with an EXPLICIT background color (common on boards migrated from
  // other tools) can't rely on the theme's muted label color: dark-theme muted
  // (light grey) disappears on a pastel tile, light-theme muted on a dark one.
  // Derive the label color from the tile's own luminance instead; tiles
  // without a baked background keep the theme default.
  const labelColor = useMemo(() => {
    const bg = widget.style?.background
    if (typeof bg !== 'string' || !/^#[0-9a-fA-F]{6}$/.test(bg)) return undefined
    const r = parseInt(bg.slice(1, 3), 16), g = parseInt(bg.slice(3, 5), 16), b = parseInt(bg.slice(5, 7), 16)
    const lum = (0.299 * r + 0.587 * g + 0.114 * b) / 255
    return lum < 0.45 ? 'rgba(255,255,255,0.75)' : 'rgba(30,41,59,0.72)'
  }, [widget.style?.background])

  // Optional segmented "pip" progress bar: props.progress = { segments, target }.
  // Fraction filled = value / target, clamped to [0, segments]. `target`
  // should share the headline value's scale (e.g. target: 100 for a 0-100
  // percent value, target: 1 for a 0-1 fraction).
  const progress = useMemo(() => {
    const p = wProps.progress
    if (!p || value == null) return null
    const num = Number(value)
    if (Number.isNaN(num)) return null
    const segments = p.segments || 10
    const target = p.target ?? 100
    const ratio = target > 0 ? Math.max(0, Math.min(1, num / target)) : 0
    return { segments, filled: Math.round(ratio * segments), ratio, style: p.style === 'bar' ? 'bar' : 'segments' }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value, JSON.stringify(wProps.progress)])

  // A failed query replaces the headline number — a KPI showing a fabricated
  // figure is the single most misleading thing this app could render.
  if (error) {
    return (
      <WidgetError
        message={error}
        queryId={query_id}
        onRetry={() => setRetryEpoch(e => e + 1)}
        compact
      />
    )
  }

  return (
    <div className="flex flex-col justify-center h-full px-5 py-4 gap-1">
      {loading ? (
        <div className="space-y-3" aria-busy="true" aria-label="Loading KPI">
          <Skeleton className="h-9 w-28 rounded-lg" />
          <Skeleton className="h-3.5 w-20 rounded" />
        </div>
      ) : (
        <>
          {IconCmp && (
            <div
              className="mb-2.5 inline-flex items-center justify-center rounded-xl shrink-0"
              style={{
                width: 38,
                height: 38,
                color: iconTint,
                background: `color-mix(in srgb, ${iconTint} 15%, transparent)`,
                boxShadow: `inset 0 0 0 1px color-mix(in srgb, ${iconTint} 22%, transparent)`,
              }}
              aria-hidden="true"
            >
              <IconCmp size={21} weight="duotone" />
            </div>
          )}
          {labelOnTop && (
            <p
              className="mb-1.5 text-[11px] font-semibold uppercase tracking-[0.08em] text-muted leading-snug"
              style={labelColor ? { color: labelColor } : undefined}
            >
              {label}
            </p>
          )}
          <div className="flex items-baseline gap-2 flex-wrap">
            <p
              className="text-3xl font-bold font-display leading-none tracking-tight"
              style={{ color: valueColor ?? undefined }}
            >
              {formatValue(value, format)}
            </p>
            {signal.matched && signal.label && (
              <Badge
                variant={
                  signal.color === DELTA_COLORS.up ? 'success'
                  : signal.color === DELTA_COLORS.down ? 'danger'
                  : 'warning'
                }
                size="sm"
              >
                {signal.label}
              </Badge>
            )}
            {delta && (
              <span
                className="text-sm font-semibold tabular-nums"
                style={{ color: DELTA_COLORS[delta.direction] }}
                aria-label={`Change: ${delta.text}`}
              >
                {delta.text}
              </span>
            )}
          </div>
          {!labelOnTop && (
            <p
              className="mt-1 text-sm font-medium text-muted leading-snug"
              style={labelColor ? { color: labelColor } : undefined}
            >
              {label}
            </p>
          )}
          {progress && progress.style === 'bar' && (
            <div
              className="mt-2.5 h-1 w-full rounded-full overflow-hidden"
              style={{ backgroundColor: 'rgba(148,163,184,0.25)' }}
              aria-hidden="true"
            >
              <div
                className="h-full rounded-full"
                style={{ width: `${progress.ratio * 100}%`, backgroundColor: progressColor }}
              />
            </div>
          )}
          {progress && progress.style === 'segments' && (
            <div className="flex gap-1 mt-2" aria-hidden="true">
              {Array.from({ length: progress.segments }).map((_, i) => (
                <span
                  key={i}
                  className="h-1.5 flex-1 rounded-sm"
                  style={{
                    backgroundColor: i < progress.filled
                      ? progressColor
                      : 'rgba(148,163,184,0.25)',
                  }}
                />
              ))}
            </div>
          )}
          {spark && (
            <div className="mt-2 -mx-1">
              <EChart option={spark} height={36} />
            </div>
          )}
        </>
      )}
    </div>
  )
}
