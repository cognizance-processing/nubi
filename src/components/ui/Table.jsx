/**
 * Table — Nubi data table primitive with sort, zebra, and sticky header.
 *
 * Usage (simple):
 *   <Table
 *     columns={[
 *       { key: 'name', label: 'Name' },
 *       { key: 'status', label: 'Status', sortable: true },
 *       { key: 'amount', label: 'Amount', align: 'right', sortable: true },
 *     ]}
 *     rows={data}
 *   />
 *
 * Usage (custom cell render):
 *   <Table
 *     columns={[
 *       { key: 'name', label: 'Name', render: (v, row) => <strong>{v}</strong> },
 *       { key: 'action', label: '', render: (_, row) => <Button size="sm">Edit</Button> },
 *     ]}
 *     rows={data}
 *   />
 *
 * Props:
 *   columns   Array<{ key, label, sortable?, align?, render?, className? }>
 *   rows      Array<object>
 *   zebra     boolean — alternate row background (default true)
 *   sticky    boolean — sticky header (default true)
 *   loading   boolean — shows skeleton rows
 *   empty     ReactNode — shown when rows is empty (defaults to EmptyState)
 *   onSort    (key: string, dir: 'asc' | 'desc') => void
 *   sortKey   string — current sort column key
 *   sortDir   'asc' | 'desc'
 *   className — applied to the outer wrapper
 *
 * Key rules: rows must have a stable `id` field or pass `rowKey` prop.
 */

import { useState } from 'react'
import { ChevronUp, ChevronDown, ChevronsUpDown } from 'lucide-react'
import EmptyState from './EmptyState.jsx'
import Skeleton from './Skeleton.jsx'

function cx(...parts) {
  return parts.filter(Boolean).join(' ')
}

function SortIcon({ col, sortKey, sortDir }) {
  if (!col.sortable) return null
  if (col.key !== sortKey) return <ChevronsUpDown size={13} className="ml-1 opacity-40" aria-hidden="true" />
  return sortDir === 'asc'
    ? <ChevronUp   size={13} className="ml-1 text-primary" aria-hidden="true" />
    : <ChevronDown size={13} className="ml-1 text-primary" aria-hidden="true" />
}

export default function Table({
  columns = [],
  rows = [],
  zebra = true,
  sticky = true,
  loading = false,
  empty,
  onSort,
  sortKey: sortKeyProp,
  sortDir: sortDirProp,
  rowKey = 'id',
  className,
}) {
  // Internal sort state (uncontrolled) — used when onSort is not provided
  const [intSortKey, setIntSortKey] = useState(null)
  const [intSortDir, setIntSortDir] = useState('asc')

  const controlled = typeof onSort === 'function'
  const sortKey = controlled ? sortKeyProp : intSortKey
  const sortDir = controlled ? sortDirProp : intSortDir

  const handleSort = (col) => {
    if (!col.sortable) return
    if (controlled) {
      const dir = sortKey === col.key && sortDir === 'asc' ? 'desc' : 'asc'
      onSort(col.key, dir)
    } else {
      setIntSortDir((prev) => (intSortKey === col.key && prev === 'asc' ? 'desc' : 'asc'))
      setIntSortKey(col.key)
    }
  }

  // Internal sort when uncontrolled
  let displayRows = rows
  if (!controlled && intSortKey) {
    displayRows = [...rows].sort((a, b) => {
      const av = a[intSortKey]
      const bv = b[intSortKey]
      const cmp = av < bv ? -1 : av > bv ? 1 : 0
      return intSortDir === 'asc' ? cmp : -cmp
    })
  }

  const isEmpty = !loading && displayRows.length === 0

  return (
    <div className={cx('nubi-table-wrap', className)}>
      <table className={cx('nubi-table', zebra && 'nubi-table-zebra', sticky && 'nubi-table-sticky')}>
        <thead>
          <tr>
            {columns.map((col) => (
              <th
                key={col.key}
                className={cx(
                  col.sortable && 'sortable',
                  col.align === 'right' && 'text-right',
                  col.align === 'center' && 'text-center',
                  col.headerClassName,
                )}
                onClick={col.sortable ? () => handleSort(col) : undefined}
                aria-sort={
                  col.key === sortKey
                    ? sortDir === 'asc'
                      ? 'ascending'
                      : 'descending'
                    : col.sortable
                    ? 'none'
                    : undefined
                }
              >
                <span className="inline-flex items-center">
                  {col.label}
                  <SortIcon col={col} sortKey={sortKey} sortDir={sortDir} />
                </span>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {loading
            ? Array.from({ length: 5 }).map((_, i) => (
                <tr key={i}>
                  {columns.map((col) => (
                    <td key={col.key}>
                      <Skeleton className="h-4 w-full" />
                    </td>
                  ))}
                </tr>
              ))
            : isEmpty
            ? (
              <tr>
                <td colSpan={columns.length} className="p-0 border-0">
                  {empty ?? (
                    <EmptyState
                      title="No data"
                      description="Nothing to show here yet."
                    />
                  )}
                </td>
              </tr>
            )
            : displayRows.map((row, idx) => (
              <tr key={row[rowKey] ?? idx}>
                {columns.map((col) => (
                  <td
                    key={col.key}
                    className={cx(
                      col.align === 'right'  && 'text-right',
                      col.align === 'center' && 'text-center',
                      col.className,
                    )}
                  >
                    {col.render
                      ? col.render(row[col.key], row)
                      : row[col.key] ?? '—'}
                  </td>
                ))}
              </tr>
            ))}
        </tbody>
      </table>
    </div>
  )
}
