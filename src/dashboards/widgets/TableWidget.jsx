/**
 * TableWidget.jsx — Spec-driven table widget for the SpecRenderer.
 *
 * Props
 * -----
 * widget  {object}  A spec Widget object with type === 'table'.
 *                   Shape: { id, type, query_id, encoding, props:{limit,columns}, pos }
 *
 * Behaviour
 * ---------
 * - Fetches query_id via runArrowQuery.
 * - Renders an HTML table limited to props.limit rows (default 50).
 * - If props.columns (array or comma-string) is provided, only those columns show.
 * - Scrollable container — fills widget height.
 * - Loading skeleton and error notice.
 */

import { useState, useEffect } from 'react'
import { runArrowQueryById } from '../../lib/wasmRuntime.js'

/** Convert an Arrow Table to a plain array of row objects (for the given columns). */
function tableToRows(arrowTable, columns, limit) {
  const cols = columns && columns.length > 0 ? columns : arrowTable.schema.fields.map(f => f.name)
  const rows = []
  const maxRows = Math.min(arrowTable.numRows, limit)
  for (let i = 0; i < maxRows; i++) {
    const row = {}
    for (const col of cols) {
      const child = arrowTable.getChild(col)
      row[col] = child ? child.get(i) : null
    }
    rows.push(row)
  }
  return { cols, rows }
}

function renderCell(val) {
  if (val == null) return <span className="text-muted/50">—</span>
  if (typeof val === 'boolean') return val ? 'true' : 'false'
  if (typeof val === 'number') return val.toLocaleString()
  return String(val)
}

export default function TableWidget({ widget }) {
  const { query_id, props: wProps = {} } = widget
  const limit = wProps.limit ?? 50
  const columnsRaw = wProps.columns ?? ''
  const columns = Array.isArray(columnsRaw)
    ? columnsRaw
    : columnsRaw ? columnsRaw.split(',').map(c => c.trim()).filter(Boolean) : []

  const [data, setData] = useState(null) // { cols, rows }
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
          setData(tableToRows(table, columns, limit))
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
  }, [query_id, limit, columnsRaw])

  if (loading) {
    return (
      <div className="flex items-center justify-center h-full text-sm text-muted animate-pulse">
        Loading table…
      </div>
    )
  }

  if (!data) {
    return (
      <div className="flex items-center justify-center h-full text-sm text-muted/60">
        No data
      </div>
    )
  }

  const { cols, rows } = data

  return (
    <div className="flex flex-col h-full overflow-hidden rounded-xl border border-border bg-surface">
      {error && (
        <div className="px-3 py-1.5 text-xs border-b shrink-0"
          style={{ background: 'color-mix(in srgb, #f59e0b 8%, transparent)', color: '#d97706', borderColor: 'color-mix(in srgb, #f59e0b 20%, transparent)' }}>
          {error}
        </div>
      )}
      <div className="flex-1 overflow-auto">
        <table className="w-full text-xs">
          <thead className="sticky top-0 bg-surface-2 z-10">
            <tr>
              {cols.map(col => (
                <th
                  key={col}
                  className="px-3 py-2 text-left font-semibold text-muted whitespace-nowrap border-b border-border"
                >
                  {col}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, i) => (
              <tr
                key={i}
                className={i % 2 === 0 ? 'bg-surface' : 'bg-surface-2'}
              >
                {cols.map(col => (
                  <td key={col} className="px-3 py-1.5 text-fg whitespace-nowrap border-b border-border/50">
                    {renderCell(row[col])}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
        {rows.length === 0 && (
          <div className="flex items-center justify-center py-8 text-xs text-muted">
            No rows returned.
          </div>
        )}
      </div>
      <div className="shrink-0 px-3 py-1.5 border-t border-border text-xs text-muted bg-surface-2">
        {rows.length} rows shown (limit: {limit})
      </div>
    </div>
  )
}
