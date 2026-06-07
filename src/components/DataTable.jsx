/**
 * DataTable.jsx — Shared data grid for Nubi (Playground / Query workspace).
 *
 * This is now a thin compatibility adapter over the headless <DataGrid>
 * (TanStack Table + react-virtual). The public API is unchanged so existing
 * consumers (Playground, QueryWorkspace) keep working without edits.
 *
 * Usage
 * -----
 *   <DataTable
 *     arrow={ArrowTable}              // preferred — derives columns + rows automatically
 *     columns={[{key,label,type}]}    // OR explicit columns array
 *     rows={[{...}]}                  // OR explicit row objects
 *     loading={bool}
 *     error={string}
 *     pageSize={50}
 *     title={string}
 *     toolbar={true}
 *     meta={{ cacheStatus, elapsedMs }}
 *     stickyFirstCol={false}
 *   />
 *
 * Column type: 'number' | 'string' | 'date' | 'bool'
 *
 * All the grid power (multi-sort, per-column + global filter, pagination AND
 * virtualization, resize / reorder / pin / show-hide, grouping + aggregation,
 * CSV + Excel export, density, sticky header) comes from <DataGrid>.
 */

import { useMemo } from 'react'

import DataGrid from './DataGrid.jsx'
import { deriveColumns, arrowToRows } from './dataTableUtils.js'

export default function DataTable({
  arrow: arrowTable,
  columns: columnsProp,
  rows: rowsProp,
  loading = false,
  error = null,
  pageSize = 50,
  title,
  toolbar = true,
  meta,
  stickyFirstCol = false,
}) {
  // Derive columns + rows from Arrow OR use explicit props.
  const [columns, rows] = useMemo(() => {
    if (arrowTable) {
      return [deriveColumns(arrowTable), arrowToRows(arrowTable)]
    }
    return [columnsProp ?? [], rowsProp ?? []]
  }, [arrowTable, columnsProp, rowsProp])

  return (
    <DataGrid
      columns={columns}
      rows={rows}
      loading={loading}
      error={error}
      pageSize={pageSize}
      title={title}
      toolbar={toolbar}
      meta={meta}
      stickyFirstCol={stickyFirstCol}
      exportFileName={title || 'data'}
      className="bg-surface"
    />
  )
}
