/**
 * shared/useInspectorData.js — Data-fetching hooks for inspector panels.
 * Shared between DashboardEditor and CanvasEditor.
 *
 * Exports:
 *   useColumnIntrospection(queryId) → { columns: string[], introspecting: bool }
 *   useMetricsList()                → MetricRow[]
 */

import { useState, useEffect } from 'react'
import { runArrowQueryById } from '../../lib/wasmRuntime.js'
import { listMetrics } from '../../lib/metrics.js'

/**
 * Run a query by ID and return its schema column names.
 * Resets whenever queryId changes. Degrades to [] on any failure.
 */
export function useColumnIntrospection(queryId) {
  const [columns, setColumns] = useState([])
  const [introspecting, setIntrospecting] = useState(false)
  useEffect(() => {
    if (!queryId) { setColumns([]); return } // eslint-disable-line react-hooks/set-state-in-effect
    let cancelled = false
    setIntrospecting(true)
    runArrowQueryById(queryId)
      .then(({ table }) => {
        if (!cancelled) {
          setColumns(table.schema.fields.map(f => f.name))
          setIntrospecting(false)
        }
      })
      .catch(() => {
        if (!cancelled) { setColumns([]); setIntrospecting(false) }
      })
    return () => { cancelled = true }
  }, [queryId])
  return { columns, introspecting }
}

/**
 * Load the org's governed metrics once. Degrades to [] on any failure.
 */
export function useMetricsList() {
  const [metrics, setMetrics] = useState([])
  useEffect(() => {
    let cancelled = false
    listMetrics().then(rows => { if (!cancelled) setMetrics(rows) })
    return () => { cancelled = true }
  }, [])
  return metrics
}
