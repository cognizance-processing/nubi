/**
 * TableWidget.jsx — Spec-driven table widget for the SpecRenderer.
 *
 * Renders the dashboard `table` widget on top of the shared, headless
 * <DataGrid> (TanStack Table + react-virtual) so it gains MUI-DataGrid-Premium
 * class features (multi-sort, per-column + global filter, pagination AND
 * virtualization, column resize / reorder / pin / show-hide, row grouping +
 * aggregation subtotals, CSV + Excel export, density toggle, sticky header)
 * while PRESERVING every existing dashboard behaviour:
 *
 *   - param binding        → runArrowQueryById + useResolvedParams
 *   - props.columns        → column selection / ordering
 *   - props.limit          → row cap
 *   - widget.columnFormats → value formatting via formatValue()
 *   - widget.formattingRules → conditional cell/row styling via evalRules()
 *
 * Props
 * -----
 * widget  {object}  A spec Widget object with type === 'table'.
 *                   Shape: { id, type, query_id, encoding,
 *                            props:{limit,columns}, params?, columnFormats?,
 *                            formattingRules?, pos }
 *
 * The widget card background is intentionally transparent so widget.style
 * (set by the SpecRenderer) shows through.
 */

import { useState, useEffect, useMemo, useCallback } from 'react'
import { runArrowQueryById } from '../../lib/wasmRuntime.js'
import { runMetricQuery } from '../../lib/metricRuntime.js'
import { evalRules, formatValue } from './conditionalFormat.js'
import { useResolvedParams } from '../VariableStore.jsx'
import DataGrid from '../../components/DataGrid.jsx'
import { arrowTypeToColumnType } from '../../components/dataTableUtils.js'

/**
 * Convert an Arrow Table to { cols:[{key,label,type}], rows:[{...}] } limited
 * to `limit` rows and (optionally) restricted/ordered by `columns`.
 */
function tableToGrid(arrowTable, columns, limit) {
  const fields = arrowTable.schema.fields
  const typeByName = {}
  for (const f of fields) typeByName[f.name] = arrowTypeToColumnType(f.type)

  const colNames =
    columns && columns.length > 0 ? columns : fields.map((f) => f.name)

  const cols = colNames.map((name) => ({
    key: name,
    label: name,
    type: typeByName[name] ?? 'string',
  }))

  const maxRows = Math.min(arrowTable.numRows, limit)
  const vectors = {}
  for (const c of cols) vectors[c.key] = arrowTable.getChild(c.key)

  const rows = []
  for (let i = 0; i < maxRows; i++) {
    const row = {}
    for (const c of cols) {
      const v = vectors[c.key]
      const val = v ? v.get(i) : null
      row[c.key] = typeof val === 'bigint' ? Number(val) : val
    }
    rows.push(row)
  }
  return { cols, rows }
}

export default function TableWidget({ widget }) {
  const {
    query_id,
    props: wProps = {},
    formattingRules,
    columnFormats,
    params: widgetParams,
    metric,
  } = widget

  const limit = wProps.limit ?? 50
  const columnsRaw = wProps.columns ?? ''
  const columns = Array.isArray(columnsRaw)
    ? columnsRaw
    : columnsRaw
    ? columnsRaw.split(',').map((c) => c.trim()).filter(Boolean)
    : []

  // Normalise so downstream code is always safe (stable refs for hook deps).
  const rules = useMemo(
    () => (Array.isArray(formattingRules) ? formattingRules : []),
    [formattingRules],
  )
  const fmtMap = useMemo(
    () => (columnFormats && typeof columnFormats === 'object' ? columnFormats : {}),
    [columnFormats],
  )

  // Resolve widget params against the variable store — re-renders on var change.
  const resolvedParams = useResolvedParams(widgetParams)

  const [data, setData] = useState(null) // { cols, rows }
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
        const { table, cacheStatus } = metric
          ? await runMetricQuery(metric)
          : await runArrowQueryById(
              query_id,
              hasParams ? { namedParams: resolvedParams } : undefined,
            )
        if (!cancelled) {
          setData(tableToGrid(table, columns, limit))
          if (cacheStatus === 'SAMPLE') setError('Using sample data.')
        }
      } catch (err) {
        if (!cancelled) setError(err.message ?? 'Query failed.')
      } finally {
        if (!cancelled) setLoading(false)
      }
    }

    fetchData()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [query_id, JSON.stringify(metric), limit, columnsRaw, JSON.stringify(resolvedParams)])

  // ── Column descriptors with columnFormats applied via renderCell ──────────
  const gridColumns = useMemo(() => {
    if (!data) return []
    return data.cols.map((col) => {
      const fmt = fmtMap[col.key]
      const descriptor = { ...col }
      if (fmt) {
        // Apply column format both on-screen and on export.
        descriptor.renderCell = (val) => {
          if (val == null) return <span className="text-muted/50">—</span>
          return formatValue(val, fmt)
        }
        descriptor.exportValue = (val) => (val == null ? '' : formatValue(val, fmt))
      }
      // Sensible aggregation default for numeric columns when grouping is used.
      if (col.type === 'number') descriptor.aggregation = 'sum'
      return descriptor
    })
  }, [data, fmtMap])

  // ── Conditional formatting: evalRules → per-row + per-cell styles ─────────
  const colKeys = useMemo(() => (data ? data.cols.map((c) => c.key) : []), [data])

  // Cache evalRules() per row object so getRowStyle/getCellStyle share one pass.
  // Intentionally rebuild the cache when `data` or `rules` change (cache keyed
  // by row identity; stale entries would otherwise survive a rules edit).
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const evalCache = useMemo(() => new WeakMap(), [data, rules])
  const evalRow = useCallback(
    (row) => {
      let r = evalCache.get(row)
      if (!r) {
        r = evalRules(rules, row, colKeys)
        evalCache.set(row, r)
      }
      return r
    },
    [evalCache, rules, colKeys],
  )

  const getRowStyle = useMemo(() => {
    if (rules.length === 0) return undefined
    return (row) => evalRow(row).rowStyle ?? null
  }, [evalRow, rules.length])

  const getCellStyle = useMemo(() => {
    if (rules.length === 0) return undefined
    return (row, colKey) => evalRow(row).cellStyles[colKey] ?? null
  }, [evalRow, rules.length])

  return (
    <div className="flex flex-col h-full overflow-hidden">
      {error && (
        <div
          className="px-3 py-1.5 text-xs border border-b-0 rounded-t-xl shrink-0"
          style={{
            background: 'color-mix(in srgb, #f59e0b 8%, transparent)',
            color: '#d97706',
            borderColor: 'color-mix(in srgb, #f59e0b 20%, transparent)',
          }}
        >
          {error}
        </div>
      )}
      <div className="flex-1 min-h-0">
        <DataGrid
          columns={gridColumns}
          rows={data ? data.rows : []}
          loading={loading}
          // Transparent card so widget.style (set by SpecRenderer) shows through.
          className={error ? 'rounded-t-none' : ''}
          pageSize={50}
          density="compact"
          exportFileName={`table-${query_id ?? 'widget'}`}
          getRowStyle={getRowStyle}
          getCellStyle={getCellStyle}
          emptyMessage="No rows returned."
        />
      </div>
    </div>
  )
}
