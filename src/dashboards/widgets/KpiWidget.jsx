/**
 * KpiWidget.jsx — Spec-driven KPI card widget for the SpecRenderer.
 *
 * Props
 * -----
 * widget  {object}  A spec Widget object with type === 'kpi'.
 *                   Shape: { id, type, query_id, encoding:{value}, props:{label,format}, pos }
 *
 * Behaviour
 * ---------
 * - Fetches query_id via runArrowQuery.
 * - Reads the first row of the encoding.value column as the primary number.
 * - Renders a Tailwind card with: big number, label, and optional secondary stat.
 * - Shows loading skeleton and error notice gracefully.
 */

import { useState, useEffect } from 'react'
import { runArrowQueryById } from '../../lib/wasmRuntime.js'

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

export default function KpiWidget({ widget }) {
  const { query_id, encoding = {}, props: wProps = {} } = widget
  const valueCol = encoding.value || encoding.y || encoding.x || ''
  const label = wProps.label || (valueCol ? valueCol.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase()) : 'KPI')
  const format = wProps.format || 'number'

  const [value, setValue] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)

  useEffect(() => {
    if (!query_id) return
    let cancelled = false

    async function fetchData() {
      setLoading(true)
      setError(null)
      try {
        const { table, cacheStatus } = await runArrowQueryById(query_id)
        if (!cancelled) {
          if (table && table.numRows > 0 && valueCol) {
            const col = table.getChild(valueCol)
            const raw = col ? col.get(0) : null
            setValue(raw)
          } else if (table && table.numRows > 0) {
            // No value col specified — just show row count
            setValue(table.numRows)
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
  }, [query_id, valueCol])

  return (
    <div className="flex flex-col justify-center h-full px-5 py-4 bg-surface rounded-xl border border-border">
      {loading ? (
        <div className="space-y-2 animate-pulse">
          <div className="h-8 w-24 bg-surface-2 rounded-lg" />
          <div className="h-4 w-16 bg-surface-2 rounded" />
        </div>
      ) : (
        <>
          <p className="text-3xl font-bold font-display text-fg tabular-nums leading-none">
            {formatValue(value, format)}
          </p>
          <p className="mt-2 text-sm font-medium text-muted">{label}</p>
          {error && (
            <p className="mt-1 text-xs" style={{ color: '#d97706' }}>{error}</p>
          )}
        </>
      )}
    </div>
  )
}
