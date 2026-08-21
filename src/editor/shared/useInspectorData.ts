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
 *   useQueryParamsIndex()           → { paramsById, nameById, loaded }
 */

import { useState, useEffect, useCallback, useSyncExternalStore } from 'react'
import { runArrowQueryById } from '../../lib/wasmRuntime.js'
import { listMetrics } from '../../lib/metrics.js'
import { listLibraryWidgets, getWidgetUsage } from '../../lib/widgetLibrary.js'
import { listRegisteredQueries } from '../../lib/api.js'

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

// ---------------------------------------------------------------------------
// Query params index — "which params does each registered query declare?"
//
// The inspector needs this on every widget it shows (to offer real param names
// instead of asking the author to type them) and the filter panel needs it for
// every widget on the board at once. That is one shared answer, so it lives in
// a module-level store rather than a fetch per component: the registry list is
// already loaded for the query picker, and re-reading it per selection would
// mean a request every time someone clicks a different widget.
//
// The store is filled once per session and refreshed on demand — call
// `refreshQueryParamsIndex()` after saving a query, since its params may have
// changed.
// ---------------------------------------------------------------------------

/** @type {{ paramsById: Map<string, any[]>, nameById: Map<string, string>, loaded: boolean }} */
let _paramsIndex: { paramsById: Map<string, any[]>, nameById: Map<string, string>, loaded: boolean } = { paramsById: new Map(), nameById: new Map(), loaded: false }
let _paramsPromise: Promise<void> | null = null
const _paramsListeners = new Set<() => void>()

function _emitParamsIndex(next) {
  _paramsIndex = next
  for (const listener of _paramsListeners) listener()
}

function _loadParamsIndex() {
  if (_paramsPromise) return _paramsPromise
  _paramsPromise = listRegisteredQueries()
    .then(rows => {
      const paramsById = new Map()
      const nameById = new Map()
      for (const row of Array.isArray(rows) ? rows : []) {
        if (!row?.id) continue
        paramsById.set(row.id, Array.isArray(row.params) ? row.params : [])
        if (row.name) nameById.set(row.id, row.name)
      }
      _emitParamsIndex({ paramsById, nameById, loaded: true })
    })
    .catch(() => {
      // A registry read that fails must not break the inspector: an empty index
      // reads as "params unknown", which every consumer already handles by
      // falling back to manual entry.
      _emitParamsIndex({ ..._paramsIndex, loaded: true })
    })
  return _paramsPromise
}

/** Re-read the registry (after a query's params were edited elsewhere). */
export function refreshQueryParamsIndex() {
  _paramsPromise = null
  return _loadParamsIndex()
}

function _subscribeParamsIndex(listener) {
  _paramsListeners.add(listener)
  return () => { _paramsListeners.delete(listener) }
}

/**
 * Declared params for every registered query, keyed by query id.
 *
 * `paramsById.get(id)` returns `undefined` while the registry is still loading
 * or for a query the caller cannot see — which callers must treat as "unknown",
 * NOT as "no params", or a slow network would look like a query with nothing to
 * bind. `loaded` distinguishes the two.
 */
export function useQueryParamsIndex() {
  useEffect(() => { _loadParamsIndex() }, [])
  return useSyncExternalStore(_subscribeParamsIndex, () => _paramsIndex, () => _paramsIndex)
}

/** Declared params for one query id (`undefined` until the index has loaded). */
export function useQueryParams(queryId) {
  const { paramsById, loaded } = useQueryParamsIndex()
  return { params: queryId ? paramsById.get(queryId) : undefined, loaded }
}
