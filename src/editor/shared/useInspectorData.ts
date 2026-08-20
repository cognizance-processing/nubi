/**
 * shared/useInspectorData.js — Data-fetching hooks for inspector panels.
 * Shared editor primitives (used by DashboardEditor).
 *
 * Exports:
 *   useColumnIntrospection(queryId) → { columns: string[], introspecting: bool }
 *   useQuerySample(queryId, limit)  → { columns, rows, rowCount, loading, error }
 *   useMetricsList()                → MetricRow[]
 *   useWidgetLibrary()              → { rows, loading, reload }
 *   useWidgetUsage(id)              → { count, boards } | null | undefined
 */

import { useState, useEffect, useCallback } from 'react'
import { runArrowQueryById } from '../../lib/wasmRuntime.js'
import { listMetrics } from '../../lib/metrics.js'
import { listLibraryWidgets, getWidgetUsage } from '../../lib/widgetLibrary.js'

/**
 * Run a query by ID and return its schema column names.
 * Resets whenever queryId changes. Degrades to [] on any failure.
 */
export function useColumnIntrospection(queryId) {
  const [columns, setColumns] = useState([])
  const [introspecting, setIntrospecting] = useState(false)
  useEffect(() => {
    if (!queryId) { setColumns([]); return }  
    let cancelled = false
    setIntrospecting(true)
    runArrowQueryById(queryId)
      .then(({ table }) => {
        if (!cancelled) {
          // table is null when the query failed — degrade to no columns.
          setColumns(table ? table.schema.fields.map(f => f.name) : [])
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
 * Run a query by ID and return its columns (name + arrow type) alongside the
 * first `limit` rows. Powers the focus-mode data rail, where seeing actual
 * values — not just column names — is the point.
 *
 * Unlike useColumnIntrospection this surfaces the failure, because the data
 * rail has room to explain why a query came back empty.
 */
export function useQuerySample(queryId, limit = 8) {
  const [state, setState] = useState({ columns: [], rows: [], rowCount: 0, loading: false, error: null })

  useEffect(() => {
    if (!queryId) { setState({ columns: [], rows: [], rowCount: 0, loading: false, error: null }); return }
    let cancelled = false
    setState(s => ({ ...s, loading: true, error: null }))

    runArrowQueryById(queryId)
      .then(({ table, error }) => {
        if (cancelled) return
        // A failed query reports its real reason instead of an empty sample.
        if (error || !table) {
          setState({
            columns: [], rows: [], rowCount: 0, loading: false,
            error: error?.message ?? 'Query failed',
          })
          return
        }
        const columns = table.schema.fields.map(f => ({ name: f.name, type: String(f.type) }))
        const vectors = Object.fromEntries(columns.map(c => [c.name, table.getChild(c.name)]))
        const rows = []
        for (let i = 0; i < Math.min(table.numRows, limit); i++) {
          const row = {}
          for (const c of columns) {
            const val = vectors[c.name] ? vectors[c.name].get(i) : null
            row[c.name] = typeof val === 'bigint' ? Number(val) : val
          }
          rows.push(row)
        }
        setState({ columns, rows, rowCount: table.numRows, loading: false, error: null })
      })
      .catch(err => {
        if (!cancelled) setState({ columns: [], rows: [], rowCount: 0, loading: false, error: err?.message ?? 'Query failed' })
      })

    return () => { cancelled = true }
  }, [queryId, limit])

  return state
}

/**
 * Load the org's reusable library widgets. `reload` is exposed so the palette
 * refreshes right after a save/delete without a full remount.
 */
export function useWidgetLibrary() {
  const [rows, setRows] = useState([])
  const [loading, setLoading] = useState(true)

  const reload = useCallback(async () => {
    setLoading(true)
    const next = await listLibraryWidgets()
    setRows(next)
    setLoading(false)
  }, [])

  useEffect(() => { reload() }, [reload])

  return { rows, loading, reload }
}

// ---------------------------------------------------------------------------
// "Used by N" — module-level cache so the palette (one row per entry) and the
// inspector banner (the selected entry) never issue duplicate requests for
// the same library widget id within a session. A `null` result IS a real
// answer ("route unavailable / errored, hide the count" — see
// `getWidgetUsage`'s contract) and is cached too, so a 404'ing endpoint
// doesn't get hammered every render; call `invalidateWidgetUsage` after an
// action that changes usage (add/detach/delete a reference) to force a
// fresh count next render.
const _usageCache = new Map()

/** Drop a cached usage count so the next render re-fetches it. */
export function invalidateWidgetUsage(id) {
  if (id) _usageCache.delete(id)
}

/**
 * "Used by N boards" for a library widget id. Returns `undefined` while
 * loading, `null` when unknown (endpoint unavailable / errored — callers
 * must hide the count, never treat this as zero), or `{ count, boards }`
 * once resolved.
 * @param {string|null|undefined} id — a library row id, or nullish to skip
 */
export function useWidgetUsage(id) {
  const [usage, setUsage] = useState(() => (id && _usageCache.has(id)) ? _usageCache.get(id) : undefined)

  useEffect(() => {
    if (!id) { setUsage(undefined); return }
    if (_usageCache.has(id)) { setUsage(_usageCache.get(id)); return }
    let cancelled = false
    getWidgetUsage(id).then(result => {
      if (cancelled) return
      _usageCache.set(id, result)
      setUsage(result)
    })
    return () => { cancelled = true }
  }, [id])

  return usage
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
