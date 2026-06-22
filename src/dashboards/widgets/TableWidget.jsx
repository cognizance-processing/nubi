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
 * Master-detail (expandable rows):
 *   widget.props.detailQueryId  {string}       — query to run for each expanded row.
 *   widget.props.detailParam    {string}       — column name whose value is passed as
 *                                                the :id param to the detail query.
 *   widget.props.detailColumns  {string[]}     — optional column whitelist for the
 *                                                child grid.
 *   widget.props.detailLimit    {number}       — row cap for child grid (default 20).
 *   widget.props.expandable     {boolean}      — alternative flag; uses detailQueryId.
 *   When detailQueryId is present a ▶/▼ toggle is prepended to each row. The child
 *   grid is rendered inline beneath the parent row in a collapsible panel. The
 *   plain table path (no detailQueryId) is 100% unchanged.
 *
 * Props
 * -----
 * widget  {object}  A spec Widget object with type === 'table'.
 *                   Shape: { id, type, query_id, encoding,
 *                            props:{limit,columns,detailQueryId,detailParam,
 *                                   detailColumns,detailLimit,expandable},
 *                            params?, columnFormats?,
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
import { useRefreshEpoch } from '../RefreshContext.jsx'
import DataGrid from '../../components/DataGrid.jsx'
import { arrowTypeToColumnType } from '../../components/dataTableUtils.js'

// ---------------------------------------------------------------------------
// DetailPanel — lazy child grid rendered when a row is expanded
// ---------------------------------------------------------------------------

/**
 * Fetches and renders a child DataGrid for a single expanded parent row.
 *
 * @param {{ parentRow: object, detailQueryId: string, detailParam: string,
 *           detailColumns: string[]|null, detailLimit: number }} props
 */
function DetailPanel({ parentRow, detailQueryId, detailParam, detailColumns, detailLimit }) {
  const [detailData, setDetailData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError]     = useState(null)

  const paramValue = detailParam ? parentRow[detailParam] : undefined

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)

    async function fetch() {
      try {
        const namedParams = paramValue != null ? { id: paramValue } : {}
        const { table } = await runArrowQueryById(
          detailQueryId,
          Object.keys(namedParams).length > 0 ? { namedParams } : undefined,
        )
        if (cancelled) return

        const fields    = table.schema.fields
        const colNames  = detailColumns && detailColumns.length > 0
          ? detailColumns
          : fields.map(f => f.name)
        const cols = colNames.map(name => ({
          key:   name,
          label: name,
          type:  arrowTypeToColumnType(fields.find(f => f.name === name)?.type) ?? 'string',
        }))
        const maxRows = Math.min(table.numRows, detailLimit)
        const rows    = []
        for (let i = 0; i < maxRows; i++) {
          const row = {}
          for (const c of cols) {
            const v = table.getChild(c.key)?.get(i) ?? null
            row[c.key] = typeof v === 'bigint' ? Number(v) : v
          }
          rows.push(row)
        }
        setDetailData({ cols, rows })
      } catch (err) {
        if (!cancelled) setError(err.message ?? 'Detail query failed.')
      } finally {
        if (!cancelled) setLoading(false)
      }
    }

    fetch()
    return () => { cancelled = true }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [detailQueryId, String(paramValue), String(detailColumns), detailLimit])

  if (loading) {
    return (
      <div className="px-6 py-3 text-xs text-muted animate-pulse">
        Loading detail…
      </div>
    )
  }
  if (error) {
    return (
      <div className="px-6 py-2 text-xs" style={{ color: '#d97706' }}>
        {error}
      </div>
    )
  }
  if (!detailData || detailData.rows.length === 0) {
    return (
      <div className="px-6 py-2 text-xs text-muted">No detail rows.</div>
    )
  }

  return (
    <div className="px-6 py-2 bg-surface/60 border-t border-border">
      <DataGrid
        columns={detailData.cols}
        rows={detailData.rows}
        pageSize={detailData.rows.length}
        density="compact"
        toolbar={false}
        className="rounded-lg border border-border/60"
        style={{ maxHeight: 240 }}
        emptyMessage="No detail rows."
      />
    </div>
  )
}

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

  // Master-detail / expandable row config.
  const detailQueryId  = wProps.detailQueryId  ?? null
  const detailParam    = wProps.detailParam    ?? null
  const detailColumnsRaw = wProps.detailColumns ?? null
  const detailColumns  = Array.isArray(detailColumnsRaw)
    ? detailColumnsRaw
    : detailColumnsRaw
    ? detailColumnsRaw.split(',').map(c => c.trim()).filter(Boolean)
    : null
  const detailLimit    = wProps.detailLimit    ?? 20
  const isExpandable   = !!(detailQueryId || wProps.expandable)

  // Track which row indices are expanded.
  const [expandedRows, setExpandedRows] = useState(new Set())

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

  // Board-level auto-refresh epoch.
  const refreshEpoch = useRefreshEpoch()

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
  }, [query_id, JSON.stringify(metric), limit, columnsRaw, JSON.stringify(resolvedParams), refreshEpoch])

  // ── Column descriptors with columnFormats applied via renderCell ──────────
  const gridColumns = useMemo(() => {
    if (!data) return []
    const baseCols = data.cols.map((col) => {
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

    // Prepend expand toggle column for master-detail mode.
    if (!isExpandable) return baseCols
    return [
      {
        key:    '__expand__',
        label:  '',
        type:   'string',
        width:  36,
        align:  'center',
        renderCell: (_val, row) => {
          const rowKey = JSON.stringify(row)
          const open   = expandedRows.has(rowKey)
          return (
            <button
              type="button"
              onClick={() => {
                setExpandedRows(prev => {
                  const next = new Set(prev)
                  if (next.has(rowKey)) next.delete(rowKey)
                  else next.add(rowKey)
                  return next
                })
              }}
              className="flex items-center justify-center w-5 h-5 rounded text-muted hover:text-fg transition-colors"
              aria-label={open ? 'Collapse row' : 'Expand row'}
              title={open ? 'Collapse' : 'Expand detail'}
            >
              {open ? '▼' : '▶'}
            </button>
          )
        },
      },
      ...baseCols,
    ]
  }, [data, fmtMap, isExpandable, expandedRows, setExpandedRows])

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

  // When expandable, we render each row + its optional detail panel in a
  // custom wrapper instead of the bare DataGrid, so detail panels slot in
  // between rows without disrupting DataGrid's virtualizer.
  // For the plain case (not expandable) we render DataGrid exactly as before.
  // NOTE: master-detail mode disables virtualization (rows are known small sets).

  if (isExpandable && data) {
    const rows = data.rows
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
        <div className="flex-1 min-h-0 overflow-auto">
          {/* Header row */}
          <table className="w-full text-xs border-collapse">
            <thead className="sticky top-0 z-10 bg-surface border-b border-border">
              <tr>
                {gridColumns.map(col => (
                  <th
                    key={col.key}
                    className="px-2 py-1.5 text-left font-semibold text-muted whitespace-nowrap"
                    style={{ width: col.width, textAlign: col.align ?? 'left' }}
                  >
                    {col.key === '__expand__' ? '' : col.label}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((row, rowIdx) => {
                const rowKey  = JSON.stringify(row)
                const open    = expandedRows.has(rowKey)
                const rowStyle = getRowStyle ? getRowStyle(row) : null

                return [
                  <tr
                    key={rowIdx}
                    className="border-b border-border/50 hover:bg-surface/50 transition-colors"
                    style={rowStyle ?? undefined}
                  >
                    {gridColumns.map(col => {
                      const cellStyle = getCellStyle ? getCellStyle(row, col.key) : null
                      const val       = row[col.key]
                      const rendered  = col.renderCell ? col.renderCell(val, row) : (val == null ? '' : String(val))
                      return (
                        <td
                          key={col.key}
                          className="px-2 py-1.5 whitespace-nowrap"
                          style={{ textAlign: col.align ?? 'left', ...(cellStyle ?? {}) }}
                        >
                          {rendered}
                        </td>
                      )
                    })}
                  </tr>,
                  open && detailQueryId && (
                    <tr key={`${rowIdx}__detail`}>
                      <td colSpan={gridColumns.length} className="p-0">
                        <DetailPanel
                          parentRow={row}
                          detailQueryId={detailQueryId}
                          detailParam={detailParam}
                          detailColumns={detailColumns}
                          detailLimit={detailLimit}
                        />
                      </td>
                    </tr>
                  ),
                ].filter(Boolean)
              })}
            </tbody>
          </table>
        </div>
      </div>
    )
  }

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
