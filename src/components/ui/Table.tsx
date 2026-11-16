/**
 * Table — Nubi data table primitive (Swiss).
 *
 * 36px rows (28px compact), 1px row rules, no zebra by default, a 2px ink rule
 * under a sticky header, Geist Mono tabular numerals for numeric columns, hover
 * `--surface-2`, selected rows `--accent-soft`. Sort indicators are 8px ink
 * triangles; sortable headers are real buttons (keyboard + aria-sort).
 *
 * Usage:
 *   <Table
 *     columns={[
 *       { key: 'name', label: 'Name', sticky: true },
 *       { key: 'status', label: 'Status', sortable: true },
 *       { key: 'amount', label: 'Amount', numeric: true, sortable: true },
 *     ]}
 *     rows={data}
 *   />
 *
 *   // custom cell render, selection, row click, compact density
 *   <Table
 *     density="compact"
 *     selectable selected={ids} onSelectedChange={setIds}
 *     onRowClick={(row) => open(row.id)}
 *     columns={[{ key: 'name', label: 'Name', render: (v, row) => <b>{v}</b> }]}
 *     rows={data}
 *   />
 *
 * Props:
 *   columns        Array<{ key, label, sortable?, numeric?, align?, width?, sticky?,
 *                  render?(value,row), className?, headerClassName? }>
 *   rows           Array<object>
 *   rowKey         field used as the React key / selection id (default 'id')
 *   density        'comfortable' (36px, default) | 'compact' (28px)
 *   zebra          alternate row wash (default false; Swiss uses rules)
 *   sticky         sticky header (default true). The header sticks to the nearest
 *                  scroll container: pass maxHeight to make the table its own.
 *   maxHeight      CSS length: the table scrolls vertically inside this height
 *   stickyFirstColumn  pin column 0 while scrolling horizontally (mobile)
 *   loading        layout-matching skeleton rows
 *   empty          ReactNode shown when rows is empty (defaults to EmptyState)
 *   onSort(key, dir) + sortKey + sortDir   controlled sorting; omit for internal
 *   selectable     checkbox column; selected = array of rowKey values
 *   onSelectedChange(ids)
 *   onRowClick(row)  rows become clickable (hover pointer); put real links in
 *                    cells for keyboard users
 *   caption        accessible table caption (visually hidden)
 *   className      applied to the outer wrapper
 */

import { useMemo, useState } from 'react'
import EmptyState from './EmptyState'
import Skeleton from './Skeleton'
import { Checkbox } from './Input'

function cx(...parts: unknown[]) {
  return parts.filter(Boolean).join(' ')
}

function SortTriangle({ state }: { state: 'asc' | 'desc' | 'none' }) {
  // 8px triangles in ink; idle shows both faintly
  return (
    <svg className={cx('nubi-sort-tri', state === 'none' && 'nubi-sort-tri-idle')} viewBox="0 0 8 8" aria-hidden="true">
      {(state === 'asc' || state === 'none') && <path d="M4 1 7 4H1z" fill="currentColor" opacity={state === 'none' ? 0.6 : 1} />}
      {(state === 'desc' || state === 'none') && <path d="M4 7 1 4h6z" fill="currentColor" opacity={state === 'none' ? 0.6 : 1} />}
    </svg>
  )
}

export default function Table({
  columns = [],
  rows = [],
  rowKey = 'id',
  density = 'comfortable',
  zebra = false,
  sticky = true,
  maxHeight = undefined,
  stickyFirstColumn = false,
  loading = false,
  empty = undefined,
  onSort = undefined,
  sortKey: sortKeyProp = undefined,
  sortDir: sortDirProp = undefined,
  selectable = false,
  selected = [],
  onSelectedChange = undefined,
  onRowClick = undefined,
  caption = undefined,
  className = undefined,
}: {
  columns?: any[]
  rows?: any[]
  rowKey?: string
  density?: 'comfortable' | 'compact'
  zebra?: boolean
  sticky?: boolean
  maxHeight?: string | number
  stickyFirstColumn?: boolean
  loading?: boolean
  empty?: React.ReactNode
  onSort?: (key: string, dir: 'asc' | 'desc') => void
  sortKey?: string
  sortDir?: 'asc' | 'desc'
  selectable?: boolean
  selected?: any[]
  onSelectedChange?: (ids: any[]) => void
  onRowClick?: (row: any) => void
  caption?: string
  className?: string
}) {
  // Internal sort state (uncontrolled), used when onSort is not provided
  const [intSortKey, setIntSortKey] = useState<string | null>(null)
  const [intSortDir, setIntSortDir] = useState<'asc' | 'desc'>('asc')

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

  const displayRows = useMemo(() => {
    if (controlled || !intSortKey) return rows
    return [...rows].sort((a, b) => {
      const av = a[intSortKey]
      const bv = b[intSortKey]
      const cmp = av < bv ? -1 : av > bv ? 1 : 0
      return intSortDir === 'asc' ? cmp : -cmp
    })
  }, [rows, controlled, intSortKey, intSortDir])

  const isEmpty = !loading && displayRows.length === 0
  const ids = displayRows.map((r, i) => r[rowKey] ?? i)
  const selectedSet = new Set(selected)
  const allSelected = ids.length > 0 && ids.every((id) => selectedSet.has(id))
  const someSelected = !allSelected && ids.some((id) => selectedSet.has(id))

  const toggleAll = () => onSelectedChange?.(allSelected ? [] : ids)
  const toggleOne = (id) => {
    const next = new Set(selectedSet)
    if (next.has(id)) next.delete(id)
    else next.add(id)
    onSelectedChange?.([...next])
  }

  const colCount = columns.length + (selectable ? 1 : 0)
  const isNum = (col) => col.numeric
  const alignCls = (col) =>
    isNum(col) ? 'nubi-num' : col.align === 'right' ? 'text-right' : col.align === 'center' ? 'text-center' : undefined
  const stickyCls = (col, i) => (col.sticky || (stickyFirstColumn && i === 0 && !selectable) ? 'nubi-sticky-col' : undefined)

  return (
    <div
      className={cx('nubi-table-wrap', className)}
      style={maxHeight ? { maxHeight, overflowY: 'auto' } : undefined}
    >
      <table
        className={cx(
          'nubi-table',
          density === 'compact' && 'nubi-table-compact',
          zebra && 'nubi-table-zebra',
          sticky && 'nubi-table-sticky',
        )}
      >
        {caption && <caption className="sr-only">{caption}</caption>}
        <thead>
          <tr>
            {selectable && (
              <th className="nubi-col-check" scope="col">
                <Checkbox
                  aria-label="Select all rows"
                  checked={allSelected}
                  indeterminate={someSelected}
                  onChange={toggleAll}
                  disabled={ids.length === 0}
                />
              </th>
            )}
            {columns.map((col, i) => {
              const active = col.key === sortKey
              const state: 'asc' | 'desc' | 'none' = active ? (sortDir === 'desc' ? 'desc' : 'asc') : 'none'
              return (
                <th
                  key={col.key}
                  scope="col"
                  style={col.width ? { width: col.width } : undefined}
                  className={cx(col.sortable && 'sortable', alignCls(col), stickyCls(col, i), col.headerClassName)}
                  aria-sort={active ? (sortDir === 'asc' ? 'ascending' : 'descending') : col.sortable ? 'none' : undefined}
                >
                  {col.sortable ? (
                    <button type="button" className="nubi-th-btn nubi-focus-inset" onClick={() => handleSort(col)}>
                      {col.label}
                      <SortTriangle state={state} />
                    </button>
                  ) : (
                    col.label
                  )}
                </th>
              )
            })}
          </tr>
        </thead>
        <tbody>
          {loading
            ? Array.from({ length: 5 }).map((_, r) => (
                <tr key={r}>
                  {selectable && <td className="nubi-col-check"><Skeleton className="h-4 w-4" /></td>}
                  {columns.map((col, i) => (
                    <td key={col.key} className={cx(alignCls(col), stickyCls(col, i))}>
                      <Skeleton className={cx('h-3', isNum(col) ? 'w-10 ml-auto' : 'w-full')} style={isNum(col) ? undefined : { width: `${88 - ((r * 13 + i * 17) % 40)}%` }} />
                    </td>
                  ))}
                </tr>
              ))
            : isEmpty
            ? (
              <tr>
                <td colSpan={colCount} className="p-0 border-0" style={{ height: 'auto' }}>
                  {empty ?? (
                    <EmptyState
                      compact
                      title="No data"
                      description="Nothing to show here yet."
                    />
                  )}
                </td>
              </tr>
            )
            : displayRows.map((row, idx) => {
                const id = row[rowKey] ?? idx
                const isSel = selectedSet.has(id)
                return (
                  <tr
                    key={id}
                    aria-selected={selectable ? isSel : undefined}
                    data-clickable={onRowClick ? 'true' : undefined}
                    onClick={onRowClick ? () => onRowClick(row) : undefined}
                  >
                    {selectable && (
                      <td className="nubi-col-check" onClick={(e) => e.stopPropagation()}>
                        <Checkbox
                          aria-label={`Select row ${idx + 1}`}
                          checked={isSel}
                          onChange={() => toggleOne(id)}
                        />
                      </td>
                    )}
                    {columns.map((col, i) => (
                      <td key={col.key} className={cx(alignCls(col), stickyCls(col, i), col.className)}>
                        {col.render ? col.render(row[col.key], row) : row[col.key] ?? '—'}
                      </td>
                    ))}
                  </tr>
                )
              })}
        </tbody>
      </table>
    </div>
  )
}
