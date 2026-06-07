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
 * - props.format      {string}  Headline number format (number|integer|percent|currency).
 * - props.deltaFormat {string}  Delta display: 'percent' (default) or 'absolute'.
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
import { useResolvedParams } from '../VariableStore.jsx'
import EChart from '../../viz/EChart.jsx'

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

export default function KpiWidget({ widget }) {
  const { query_id, encoding = {}, props: wProps = {}, params: widgetParams } = widget
  const valueCol = encoding.value || encoding.y || encoding.x || ''
  const compareCol = encoding.compare || ''
  const sparkCol = encoding.spark || ''
  const label = wProps.label || (valueCol ? valueCol.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase()) : 'KPI')
  const format = wProps.format || 'number'
  const deltaFormat = wProps.deltaFormat || 'percent'

  // Resolve widget params against the variable store. Re-renders (and re-queries)
  // whenever any referenced variable changes. Widgets with no params get {}.
  const resolvedParams = useResolvedParams(widgetParams)

  const [value, setValue] = useState(null)
  const [compareValue, setCompareValue] = useState(null)
  const [sparkValues, setSparkValues] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)

  useEffect(() => {
    if (!query_id) return
    let cancelled = false

    async function fetchData() {
      setLoading(true)
      setError(null)
      try {
        // Pass resolved params as { namedParams } — M13-B wires this signature
        // into wasmRuntime. When resolvedParams is empty ({}) the behaviour is
        // identical to the pre-M14-C call (regression-safe).
        const hasParams = Object.keys(resolvedParams).length > 0
        const { table, cacheStatus } = await runArrowQueryById(
          query_id,
          hasParams ? { namedParams: resolvedParams } : undefined,
        )
        if (!cancelled) {
          if (table && table.numRows > 0 && valueCol) {
            const col = table.getChild(valueCol)
            setValue(col ? col.get(0) : null)
          } else if (table && table.numRows > 0) {
            // No value col specified — just show row count
            setValue(table.numRows)
          }

          // Optional comparison baseline (first row of the compare column).
          if (table && table.numRows > 0 && compareCol) {
            const cmp = table.getChild(compareCol)
            setCompareValue(cmp ? cmp.get(0) : null)
          } else {
            setCompareValue(null)
          }

          // Optional sparkline series (full numeric column).
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

          if (cacheStatus === 'SAMPLE') {
            setError('Sample data')
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
  // resolvedParams is a new object each render when vars change; JSON.stringify gives
  // a stable dependency so the effect only re-fires when the actual values differ.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [query_id, valueCol, compareCol, sparkCol, JSON.stringify(resolvedParams)])

  const delta = useMemo(
    () => (compareCol ? computeDelta(value, compareValue, deltaFormat) : null),
    [value, compareValue, compareCol, deltaFormat],
  )

  const spark = useMemo(
    () => (sparkValues && sparkValues.length > 1 ? sparkOption(sparkValues) : null),
    [sparkValues],
  )

  return (
    <div className="flex flex-col justify-center h-full px-5 py-4">
      {loading ? (
        <div className="space-y-2 animate-pulse">
          <div className="h-8 w-24 bg-surface-2 rounded-lg" />
          <div className="h-4 w-16 bg-surface-2 rounded" />
        </div>
      ) : (
        <>
          <div className="flex items-baseline gap-2 flex-wrap">
            <p className="text-3xl font-bold font-display text-fg tabular-nums leading-none">
              {formatValue(value, format)}
            </p>
            {delta && (
              <span className="text-sm font-semibold tabular-nums" style={{ color: DELTA_COLORS[delta.direction] }}>
                {delta.text}
              </span>
            )}
          </div>
          <p className="mt-2 text-sm font-medium text-muted">{label}</p>
          {spark && (
            <div className="mt-2 -mx-1">
              <EChart option={spark} height={36} />
            </div>
          )}
          {error && (
            <p className="mt-1 text-xs" style={{ color: '#d97706' }}>{error}</p>
          )}
        </>
      )}
    </div>
  )
}
