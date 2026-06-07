/**
 * PivotWidget.jsx — Spec-driven pivot / matrix table for the SpecRenderer.
 *
 * Renders a simple rows × cols × measure matrix from an Arrow query result.
 *
 * widget shape (type === 'pivot'):
 *   {
 *     id, type: 'pivot', query_id,
 *     encoding: { rows: string, cols: string, value: string },
 *     props: { agg?: 'sum'|'avg'|'count'|'min'|'max', limit?: number },
 *     params?, pos
 *   }
 *
 * Behaviour
 * ---------
 * - encoding.rows  → the row-dimension column (one matrix row per distinct value)
 * - encoding.cols  → the column-dimension column (one matrix column per distinct value)
 * - encoding.value → the measure column, aggregated per (row, col) cell
 * - props.agg      → aggregation: sum (default), avg, count, min, max
 * - Re-queries whenever resolved params change (regression-safe with no params).
 */

import { useState, useEffect, useMemo } from 'react'
import { runArrowQueryById } from '../../lib/wasmRuntime.js'
import { useResolvedParams } from '../VariableStore.jsx'

const AGGS = {
  sum:   (acc) => acc.reduce((s, v) => s + v, 0),
  avg:   (acc) => (acc.length ? acc.reduce((s, v) => s + v, 0) / acc.length : 0),
  count: (acc) => acc.length,
  min:   (acc) => (acc.length ? Math.min(...acc) : 0),
  max:   (acc) => (acc.length ? Math.max(...acc) : 0),
}

/** Build the pivot matrix from an Arrow table. */
function buildPivot(table, rowCol, colCol, valCol, agg) {
  const aggFn = AGGS[agg] ?? AGGS.sum
  const rCol = table.getChild(rowCol)
  const cCol = table.getChild(colCol)
  const vCol = valCol ? table.getChild(valCol) : null
  if (!rCol || !cCol) return null

  const rowKeys = []
  const colKeys = []
  const rowSeen = new Set()
  const colSeen = new Set()
  // cells[rowKey][colKey] = number[]
  const cells = new Map()

  const n = table.numRows
  for (let i = 0; i < n; i++) {
    const rk = rCol.get(i)
    const ck = cCol.get(i)
    const rKey = rk == null ? '(null)' : String(rk)
    const cKey = ck == null ? '(null)' : String(ck)
    if (!rowSeen.has(rKey)) { rowSeen.add(rKey); rowKeys.push(rKey) }
    if (!colSeen.has(cKey)) { colSeen.add(cKey); colKeys.push(cKey) }
    if (!cells.has(rKey)) cells.set(rKey, new Map())
    const rowMap = cells.get(rKey)
    if (!rowMap.has(cKey)) rowMap.set(cKey, [])
    const raw = vCol ? vCol.get(i) : 1
    rowMap.get(cKey).push(raw == null ? 0 : Number(raw))
  }

  rowKeys.sort()
  colKeys.sort()

  const matrix = rowKeys.map(rKey => {
    const rowMap = cells.get(rKey) ?? new Map()
    return colKeys.map(cKey => {
      const bucket = rowMap.get(cKey)
      return bucket && bucket.length ? aggFn(bucket) : null
    })
  })

  return { rowKeys, colKeys, matrix }
}

function fmtNum(v) {
  if (v == null) return '—'
  if (!Number.isFinite(v)) return String(v)
  return v.toLocaleString(undefined, { maximumFractionDigits: 2 })
}

export default function PivotWidget({ widget }) {
  const { query_id, encoding = {}, props: wProps = {}, params: widgetParams } = widget
  const rowCol = encoding.rows || ''
  const colCol = encoding.cols || ''
  const valCol = encoding.value || ''
  const agg = wProps.agg || 'sum'

  const resolvedParams = useResolvedParams(widgetParams)

  const [table, setTable] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)

  useEffect(() => {
    if (!query_id) return
    let cancelled = false
    async function fetchData() {
      setLoading(true)
      setError(null)
      try {
        const hasParams = Object.keys(resolvedParams).length > 0
        const { table: t, cacheStatus } = await runArrowQueryById(
          query_id,
          hasParams ? { namedParams: resolvedParams } : undefined,
        )
        if (!cancelled) {
          setTable(t)
          if (cacheStatus === 'SAMPLE') setError('Using sample data — query unavailable.')
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
  }, [query_id, JSON.stringify(resolvedParams)])

  const pivot = useMemo(() => {
    if (!table || !rowCol || !colCol) return null
    return buildPivot(table, rowCol, colCol, valCol, agg)
  }, [table, rowCol, colCol, valCol, agg])

  if (loading) {
    return (
      <div className="flex items-center justify-center h-full text-sm text-muted">
        <span className="animate-pulse">Loading pivot…</span>
      </div>
    )
  }

  return (
    <div className="flex flex-col h-full overflow-hidden">
      {error && (
        <div className="px-3 py-1.5 text-xs border-b shrink-0"
          style={{ background: 'color-mix(in srgb, #f59e0b 8%, transparent)', color: '#d97706', borderColor: 'color-mix(in srgb, #f59e0b 20%, transparent)' }}>
          {error}
        </div>
      )}
      <div className="flex-1 min-h-0 overflow-auto">
        {pivot ? (
          <table className="w-full text-xs border-collapse">
            <thead>
              <tr>
                <th className="sticky top-0 left-0 z-10 bg-surface-2 text-left font-semibold text-muted px-2 py-1.5 border-b border-r border-border">
                  {rowCol} \ {colCol}
                </th>
                {pivot.colKeys.map(ck => (
                  <th key={ck} className="sticky top-0 z-[5] bg-surface-2 text-right font-semibold text-fg px-2 py-1.5 border-b border-border whitespace-nowrap">
                    {ck}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {pivot.rowKeys.map((rk, ri) => (
                <tr key={rk} className="hover:bg-surface-2/40">
                  <th className="sticky left-0 bg-surface text-left font-medium text-fg px-2 py-1 border-r border-b border-border whitespace-nowrap">
                    {rk}
                  </th>
                  {pivot.matrix[ri].map((v, ci) => (
                    <td key={ci} className="text-right tabular-nums px-2 py-1 border-b border-border/60 text-fg">
                      {v == null ? <span className="text-muted/40">·</span> : fmtNum(v)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <div className="flex items-center justify-center h-full text-sm text-muted">
            Select row, column &amp; value dimensions to build the pivot.
          </div>
        )}
      </div>
    </div>
  )
}
