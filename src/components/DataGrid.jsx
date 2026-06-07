/**
 * DataGrid.jsx — MUI-DataGrid-Premium-class grid for Nubi.
 *
 * Headless core: @tanstack/react-table (v8) + @tanstack/react-virtual.
 * Styled with the app's Tailwind design tokens (bg-surface, border-border,
 * text-fg/muted, primary) so it drops into both the Playground and the
 * dashboard TableWidget unchanged.
 *
 * ── Public API ──────────────────────────────────────────────────────────────
 *   <DataGrid
 *     columns={[{                       // column descriptors
 *       key, label, type,               //   type: 'number'|'string'|'date'|'bool'
 *       width?, align?,                 //   align: 'left'|'right'|'center'
 *       aggregation?,                   //   'sum'|'mean'|'min'|'max'|'count' (grouping subtotals)
 *       renderCell?,                    //   (value, row) => ReactNode  (custom cell)
 *       exportValue?,                   //   (value, row) => string|number (export override)
 *     }]}
 *     rows={[{...}]}                    // plain row objects
 *     loading={bool}
 *     error={string|null}
 *     title={string}
 *     toolbar={true}                    // show the toolbar
 *     meta={{ cacheStatus, elapsedMs }} // optional badges
 *     pageSize={50}                     // initial page size; 0 / 'all' → virtualized, no pagination
 *     paginate={true}                   // false → pure virtualization for huge datasets
 *     density={'comfortable'}           // initial: 'compact'|'comfortable'|'spacious'
 *     stickyFirstCol={false}            // pin first column left
 *     enableGrouping={true}
 *     getRowStyle={(row) => style|null} // per-row inline style (conditional formatting)
 *     getCellStyle={(row, col) => style|null}  // per-cell inline style
 *     exportFileName={'data'}
 *     onCellClick={(value, row, col) => void}
 *     className, style                  // applied to the outer card (transparent-friendly)
 *   />
 *
 * Features: multi-column sort, per-column filter + global search, pagination
 * AND row virtualization, column resize / reorder (drag header) / pin
 * (left|right) / show-hide, row grouping with aggregation subtotals, CSV +
 * Excel export, density toggle, sticky header.
 */

import {
  useState,
  useMemo,
  useRef,
  useCallback,
  useEffect,
} from 'react'
import {
  useReactTable,
  getCoreRowModel,
  getSortedRowModel,
  getFilteredRowModel,
  getPaginationRowModel,
  getExpandedRowModel,
  getGroupedRowModel,
  flexRender,
} from '@tanstack/react-table'
import { useVirtualizer } from '@tanstack/react-virtual'
import {
  ChevronUp,
  ChevronDown,
  ChevronsUpDown,
  ChevronLeft,
  ChevronRight,
  ChevronRight as ChevronExpand,
  Search,
  Download,
  Filter,
  X,
  AlignJustify,
  Rows3,
  Rows4,
  Zap,
  Clock,
  Database,
  AlertCircle,
  Columns3,
  Pin,
  PinOff,
  Group,
  FileSpreadsheet,
  FileText,
} from 'lucide-react'

import { downloadCSV, downloadExcel } from './gridExport.js'

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const PAGE_SIZE_OPTIONS = [25, 50, 100, 250]
const DEFAULT_COL_WIDTH = 160
const MIN_COL_WIDTH = 64
const ROW_HEIGHT = { compact: 28, comfortable: 36, spacious: 46 }

const NUMERIC_OPS = ['eq', 'ne', 'gt', 'gte', 'lt', 'lte']
const STRING_OPS = ['contains', 'eq', 'ne']
const OP_LABELS = { eq: '=', ne: '≠', gt: '>', gte: '≥', lt: '<', lte: '≤', contains: '~' }

// ---------------------------------------------------------------------------
// Pure helpers
// ---------------------------------------------------------------------------

function formatNumber(n) {
  if (n == null) return ''
  if (typeof n === 'bigint') return n.toLocaleString('en-US')
  if (!Number.isFinite(n)) return String(n)
  if (Number.isInteger(n)) return n.toLocaleString('en-US')
  return n.toLocaleString('en-US', { maximumFractionDigits: 6 })
}

/** Custom column filter: typed comparison driven by {op, value}. */
function typedFilter(row, columnId, filterValue) {
  if (!filterValue || filterValue.value === '' || filterValue.value == null) return true
  const value = row.getValue(columnId)
  const { op = 'contains', value: fv, type = 'string' } = filterValue

  if (value == null) return false

  if (type === 'number') {
    const num = typeof value === 'number' ? value : Number(value)
    const fnum = Number(fv)
    if (Number.isNaN(fnum)) return true
    switch (op) {
      case 'eq': return num === fnum
      case 'ne': return num !== fnum
      case 'gt': return num > fnum
      case 'gte': return num >= fnum
      case 'lt': return num < fnum
      case 'lte': return num <= fnum
      default: return true
    }
  }

  if (type === 'bool') {
    const a = String(value).toLowerCase()
    const b = String(fv).toLowerCase()
    return op === 'ne' ? a !== b : a === b
  }

  const sv = String(value).toLowerCase()
  const sf = String(fv).toLowerCase()
  switch (op) {
    case 'eq': return sv === sf
    case 'ne': return sv !== sf
    default: return sv.includes(sf)
  }
}

/** Aggregation functions for grouping subtotals. */
function aggregate(kind, values) {
  const nums = values
    .map((v) => (typeof v === 'bigint' ? Number(v) : v))
    .filter((v) => typeof v === 'number' && Number.isFinite(v))
  switch (kind) {
    case 'sum': return nums.reduce((a, b) => a + b, 0)
    case 'mean': return nums.length ? nums.reduce((a, b) => a + b, 0) / nums.length : null
    case 'min': return nums.length ? Math.min(...nums) : null
    case 'max': return nums.length ? Math.max(...nums) : null
    case 'count': return values.length
    default: return null
  }
}

// ---------------------------------------------------------------------------
// Small presentational bits
// ---------------------------------------------------------------------------

function NullBadge() {
  return (
    <span className="inline-block px-1.5 rounded text-[10px] font-mono leading-4 bg-surface-2 text-muted/60 border border-border/60 select-none">
      NULL
    </span>
  )
}

function BoolPill({ value }) {
  return value ? (
    <span className="inline-flex items-center px-2 rounded-full text-[10px] font-semibold leading-5 bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border border-emerald-500/20 select-none">
      true
    </span>
  ) : (
    <span className="inline-flex items-center px-2 rounded-full text-[10px] font-semibold leading-5 bg-rose-500/10 text-rose-600 dark:text-rose-400 border border-rose-500/20 select-none">
      false
    </span>
  )
}

/** Default cell renderer (used when a column has no custom renderCell). */
function DefaultCell({ value, type }) {
  if (value == null) return <NullBadge />
  if (type === 'bool') return <BoolPill value={value} />
  if (type === 'number') {
    return <span className="font-mono tabular-nums">{formatNumber(value)}</span>
  }
  if (type === 'date') {
    const str = value instanceof Date ? value.toLocaleString() : String(value)
    return <span className="font-mono text-[11px]">{str}</span>
  }
  const str = String(value)
  return (
    <span className="truncate block" title={str.length > 60 ? str : undefined}>
      {str}
    </span>
  )
}

function CacheBadge({ cacheStatus }) {
  if (!cacheStatus || cacheStatus === 'MISS') return null
  if (cacheStatus === 'SAMPLE') {
    return (
      <span className="inline-flex items-center gap-1 text-[10px] font-medium px-2 py-0.5 rounded-full bg-amber-500/10 text-amber-600 dark:text-amber-400 border border-amber-500/20">
        <Database size={9} /> SAMPLE
      </span>
    )
  }
  return (
    <span className="inline-flex items-center gap-1 text-[10px] font-medium px-2 py-0.5 rounded-full bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border border-emerald-500/20">
      <Zap size={9} /> {cacheStatus}
    </span>
  )
}

/** Toolbar dropdown panel (columns / pin / group config). */
function Popover({ open, onClose, children, align = 'right' }) {
  const ref = useRef(null)
  useEffect(() => {
    if (!open) return
    const onDoc = (e) => {
      if (ref.current && !ref.current.contains(e.target)) onClose()
    }
    document.addEventListener('mousedown', onDoc)
    return () => document.removeEventListener('mousedown', onDoc)
  }, [open, onClose])
  if (!open) return null
  return (
    <div
      ref={ref}
      className={[
        'absolute top-full mt-1 z-50 min-w-[200px] max-h-80 overflow-auto rounded-lg border border-border bg-surface shadow-xl p-1.5',
        align === 'right' ? 'right-0' : 'left-0',
      ].join(' ')}
    >
      {children}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export default function DataGrid({
  columns: columnDescriptors = [],
  rows = [],
  loading = false,
  error = null,
  title,
  toolbar = true,
  meta,
  pageSize: pageSizeProp = 50,
  paginate: paginateProp = true,
  density: densityProp = 'comfortable',
  stickyFirstCol = false,
  enableGrouping = true,
  getRowStyle,
  getCellStyle,
  exportFileName = 'data',
  onCellClick,
  className = '',
  style,
  emptyMessage = 'No rows returned.',
}) {
  // ── Local UI state ────────────────────────────────────────────────────────
  const [sorting, setSorting] = useState([])
  const [columnFilters, setColumnFilters] = useState([])
  const [globalFilter, setGlobalFilter] = useState('')
  const [columnVisibility, setColumnVisibility] = useState({})
  const [columnOrder, setColumnOrder] = useState([])
  const [columnSizing, setColumnSizing] = useState({})
  const [columnPinning, setColumnPinning] = useState({ left: [], right: [] })
  const [grouping, setGrouping] = useState([])
  const [expanded, setExpanded] = useState({})
  const [showFilters, setShowFilters] = useState(false)
  const [density, setDensity] = useState(densityProp)
  const [pagination, setPagination] = useState({
    pageIndex: 0,
    pageSize: paginateProp ? pageSizeProp : 100000,
  })

  // popovers
  const [openMenu, setOpenMenu] = useState(null) // 'columns' | 'pin' | 'group' | 'export' | null

  const descByKey = useMemo(() => {
    const m = {}
    for (const d of columnDescriptors) m[d.key] = d
    return m
  }, [columnDescriptors])

  // Apply initial first-col pin if requested
  useEffect(() => {
    if (stickyFirstCol && columnDescriptors.length > 0) {
      setColumnPinning((prev) =>
        prev.left.length ? prev : { ...prev, left: [columnDescriptors[0].key] },
      )
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [stickyFirstCol, columnDescriptors.length])

  // ── Build TanStack column defs ────────────────────────────────────────────
  const columns = useMemo(
    () =>
      columnDescriptors.map((d) => ({
        id: d.key,
        accessorKey: d.key,
        header: d.label ?? d.key,
        size: d.width ?? DEFAULT_COL_WIDTH,
        minSize: MIN_COL_WIDTH,
        enableResizing: true,
        enableGrouping: enableGrouping,
        filterFn: typedFilter,
        meta: { type: d.type ?? 'string', align: d.align, descriptor: d },
        aggregationFn: undefined,
        aggregatedCell: ({ row, column }) => {
          const kind = d.aggregation
          if (!kind) return null
          const leafValues = row.getLeafRows().map((r) => r.getValue(column.id))
          const result = aggregate(kind, leafValues)
          if (result == null) return null
          const display =
            kind === 'count'
              ? `${result}`
              : (d.type === 'number' ? formatNumber(result) : String(result))
          return (
            <span className="font-mono text-[11px] text-muted">
              {kind === 'count' ? '' : `${kind}: `}
              {display}
            </span>
          )
        },
        cell: ({ getValue, row }) => {
          const value = getValue()
          if (d.renderCell) return d.renderCell(value, row.original)
          return <DefaultCell value={value} type={d.type ?? 'string'} />
        },
      })),
    [columnDescriptors, enableGrouping],
  )

  // ── Table instance ────────────────────────────────────────────────────────
  const table = useReactTable({
    data: rows,
    columns,
    state: {
      sorting,
      columnFilters,
      globalFilter,
      columnVisibility,
      columnOrder,
      columnSizing,
      columnPinning,
      grouping,
      expanded,
      pagination: paginateProp ? pagination : undefined,
    },
    onSortingChange: setSorting,
    onColumnFiltersChange: setColumnFilters,
    onGlobalFilterChange: setGlobalFilter,
    onColumnVisibilityChange: setColumnVisibility,
    onColumnOrderChange: setColumnOrder,
    onColumnSizingChange: setColumnSizing,
    onColumnPinningChange: setColumnPinning,
    onGroupingChange: setGrouping,
    onExpandedChange: setExpanded,
    onPaginationChange: setPagination,
    columnResizeMode: 'onChange',
    enableMultiSort: true,
    isMultiSortEvent: () => true, // every header click stacks (shift not required)
    globalFilterFn: (row, _colId, filterValue) => {
      if (!filterValue) return true
      const q = String(filterValue).toLowerCase()
      return row.getAllCells().some((c) => {
        const v = c.getValue()
        return v != null && String(v).toLowerCase().includes(q)
      })
    },
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
    getFilteredRowModel: getFilteredRowModel(),
    getGroupedRowModel: getGroupedRowModel(),
    getExpandedRowModel: getExpandedRowModel(),
    getPaginationRowModel: paginateProp ? getPaginationRowModel() : undefined,
    autoResetPageIndex: true,
  })

  // ── Virtualization ────────────────────────────────────────────────────────
  const scrollRef = useRef(null)
  const visibleRows = table.getRowModel().rows
  const rowVirtualizer = useVirtualizer({
    count: visibleRows.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => ROW_HEIGHT[density],
    overscan: 12,
  })
  const virtualRows = rowVirtualizer.getVirtualItems()
  const totalSize = rowVirtualizer.getTotalSize()
  const paddingTop = virtualRows.length ? virtualRows[0].start : 0
  const paddingBottom = virtualRows.length
    ? totalSize - virtualRows[virtualRows.length - 1].end
    : 0

  // remeasure when density changes
  useEffect(() => {
    rowVirtualizer.measure()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [density])

  // ── Column drag-reorder ───────────────────────────────────────────────────
  const dragColRef = useRef(null)
  const handleColDrop = useCallback(
    (targetId) => {
      const src = dragColRef.current
      dragColRef.current = null
      if (!src || src === targetId) return
      const order = table.getState().columnOrder.length
        ? [...table.getState().columnOrder]
        : table.getAllLeafColumns().map((c) => c.id)
      const from = order.indexOf(src)
      const to = order.indexOf(targetId)
      if (from === -1 || to === -1) return
      order.splice(to, 0, order.splice(from, 1)[0])
      setColumnOrder(order)
    },
    [table],
  )

  // ── Export ────────────────────────────────────────────────────────────────
  const exportColumns = useMemo(
    () =>
      table
        .getVisibleLeafColumns()
        .filter((c) => c.id !== '__group')
        .map((c) => ({ key: c.id, label: String(c.columnDef.header ?? c.id) })),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [table, columnVisibility, columnOrder],
  )

  const exportRows = useMemo(
    () => table.getFilteredRowModel().rows.filter((r) => !r.getIsGrouped()).map((r) => r.original),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [table, columnFilters, globalFilter, sorting],
  )

  const getExportValue = useCallback(
    (row, col) => {
      const desc = descByKey[col.key]
      const raw = row[col.key]
      if (desc?.exportValue) return desc.exportValue(raw, row)
      return raw
    },
    [descByKey],
  )

  const doExportCSV = useCallback(() => {
    downloadCSV(`${exportFileName}.csv`, exportRows, exportColumns, getExportValue)
    setOpenMenu(null)
  }, [exportFileName, exportRows, exportColumns, getExportValue])

  const doExportExcel = useCallback(() => {
    downloadExcel(`${exportFileName}.xls`, exportRows, exportColumns, exportFileName, getExportValue)
    setOpenMenu(null)
  }, [exportFileName, exportRows, exportColumns, getExportValue])

  // ── Density classes ───────────────────────────────────────────────────────
  const rowH = ROW_HEIGHT[density]
  const fontSize = density === 'compact' ? 'text-[11px]' : 'text-xs'
  const cellPx = density === 'spacious' ? 'px-4' : 'px-3'

  const allColumns = table.getAllLeafColumns()
  const activeFilterCount = columnFilters.length
  const totalFilteredRows = table.getFilteredRowModel().rows.filter((r) => !r.getIsGrouped()).length

  // Helper: pinned offset style for a header/cell
  const pinStyle = (column) => {
    const pinned = column.getIsPinned()
    if (!pinned) return {}
    return {
      position: 'sticky',
      [pinned]: pinned === 'left' ? column.getStart('left') : column.getAfter('right'),
      zIndex: 11,
    }
  }

  // ── Error state ───────────────────────────────────────────────────────────
  if (error && !loading) {
    return (
      <div
        className={`flex flex-col h-full rounded-xl border border-border overflow-hidden ${className}`}
        style={style}
      >
        <div className="flex-1 flex flex-col items-center justify-center gap-3 p-8 text-center">
          <div className="w-10 h-10 rounded-full bg-rose-500/10 border border-rose-500/20 flex items-center justify-center">
            <AlertCircle size={18} className="text-rose-500" />
          </div>
          <div>
            <p className="text-sm font-medium text-fg mb-1">Query error</p>
            <p className="text-xs text-muted font-mono max-w-sm">{error}</p>
          </div>
        </div>
      </div>
    )
  }

  const toolbarBtn =
    'h-7 px-2 flex items-center gap-1.5 text-[11px] text-muted hover:text-fg border border-border rounded-md bg-surface hover:border-border transition-colors'

  return (
    <div
      className={`flex flex-col h-full rounded-xl border border-border overflow-hidden relative ${className}`}
      style={style}
    >
      {/* ── Toolbar ──────────────────────────────────────────────────────── */}
      {toolbar && (
        <div className="shrink-0 flex items-center gap-2 px-3 py-2 border-b border-border bg-surface-2/60 flex-wrap relative">
          {title && (
            <span className="text-xs font-semibold text-fg font-display mr-1 shrink-0">{title}</span>
          )}
          {meta?.cacheStatus && <CacheBadge cacheStatus={meta.cacheStatus} />}
          {meta?.elapsedMs != null && (
            <span className="inline-flex items-center gap-1 text-[10px] text-muted">
              <Clock size={9} /> {meta.elapsedMs}ms
            </span>
          )}

          <div className="flex-1" />

          {/* Global search */}
          <div className="relative">
            <Search size={12} className="absolute left-2 top-1/2 -translate-y-1/2 text-muted pointer-events-none" />
            <input
              type="text"
              className="h-7 pl-6 pr-2 text-[11px] bg-surface border border-border rounded-md text-fg placeholder:text-muted/60 focus:outline-none focus:ring-1 focus:ring-ring w-32 focus:w-48 transition-all"
              placeholder="Search…"
              value={globalFilter}
              onChange={(e) => setGlobalFilter(e.target.value)}
            />
            {globalFilter && (
              <button
                className="absolute right-1.5 top-1/2 -translate-y-1/2 text-muted hover:text-fg"
                onClick={() => setGlobalFilter('')}
              >
                <X size={10} />
              </button>
            )}
          </div>

          {/* Filter toggle */}
          <button
            onClick={() => setShowFilters((s) => !s)}
            className={[
              'h-7 px-2 flex items-center gap-1.5 text-[11px] font-medium rounded-md border transition-colors',
              showFilters
                ? 'bg-primary/10 border-primary/30 text-primary'
                : 'bg-surface border-border text-muted hover:text-fg',
            ].join(' ')}
            title="Toggle column filters"
          >
            <Filter size={12} />
            {activeFilterCount > 0 && (
              <span className="inline-flex items-center justify-center min-w-4 h-4 px-1 text-[9px] rounded-full bg-primary text-primary-fg font-bold">
                {activeFilterCount}
              </span>
            )}
          </button>

          {/* Grouping menu */}
          {enableGrouping && (
            <div className="relative">
              <button
                onClick={() => setOpenMenu((m) => (m === 'group' ? null : 'group'))}
                className={[
                  'h-7 px-2 flex items-center gap-1.5 text-[11px] font-medium rounded-md border transition-colors',
                  grouping.length
                    ? 'bg-primary/10 border-primary/30 text-primary'
                    : 'bg-surface border-border text-muted hover:text-fg',
                ].join(' ')}
                title="Group rows"
              >
                <Group size={12} />
                {grouping.length > 0 && (
                  <span className="inline-flex items-center justify-center min-w-4 h-4 px-1 text-[9px] rounded-full bg-primary text-primary-fg font-bold">
                    {grouping.length}
                  </span>
                )}
              </button>
              <Popover open={openMenu === 'group'} onClose={() => setOpenMenu(null)}>
                <p className="px-2 py-1 text-[10px] font-semibold text-muted uppercase tracking-wide">Group by</p>
                {allColumns.map((col) => (
                  <label
                    key={col.id}
                    className="flex items-center gap-2 px-2 py-1 rounded hover:bg-surface-2 cursor-pointer text-[11px] text-fg"
                  >
                    <input
                      type="checkbox"
                      checked={col.getIsGrouped()}
                      onChange={() => col.toggleGrouping()}
                      className="accent-[var(--primary)]"
                    />
                    {String(col.columnDef.header ?? col.id)}
                  </label>
                ))}
                {grouping.length > 0 && (
                  <button
                    className="w-full mt-1 px-2 py-1 text-[11px] text-muted hover:text-fg text-left rounded hover:bg-surface-2"
                    onClick={() => setGrouping([])}
                  >
                    Clear grouping
                  </button>
                )}
              </Popover>
            </div>
          )}

          {/* Columns (show/hide + pin) menu */}
          <div className="relative">
            <button
              onClick={() => setOpenMenu((m) => (m === 'columns' ? null : 'columns'))}
              className={toolbarBtn}
              title="Columns"
            >
              <Columns3 size={12} />
            </button>
            <Popover open={openMenu === 'columns'} onClose={() => setOpenMenu(null)}>
              <p className="px-2 py-1 text-[10px] font-semibold text-muted uppercase tracking-wide">Columns</p>
              {allColumns.map((col) => {
                const pinned = col.getIsPinned()
                return (
                  <div
                    key={col.id}
                    className="flex items-center gap-1.5 px-2 py-1 rounded hover:bg-surface-2 text-[11px] text-fg"
                  >
                    <label className="flex items-center gap-2 flex-1 cursor-pointer min-w-0">
                      <input
                        type="checkbox"
                        checked={col.getIsVisible()}
                        onChange={col.getToggleVisibilityHandler()}
                        className="accent-[var(--primary)]"
                      />
                      <span className="truncate">{String(col.columnDef.header ?? col.id)}</span>
                    </label>
                    <button
                      className={`p-0.5 rounded hover:bg-surface ${pinned === 'left' ? 'text-primary' : 'text-muted/50 hover:text-fg'}`}
                      title="Pin left"
                      onClick={() => col.pin(pinned === 'left' ? false : 'left')}
                    >
                      <Pin size={11} />
                    </button>
                    <button
                      className={`p-0.5 rounded hover:bg-surface ${pinned === 'right' ? 'text-primary' : 'text-muted/50 hover:text-fg'}`}
                      title="Pin right"
                      onClick={() => col.pin(pinned === 'right' ? false : 'right')}
                    >
                      <Pin size={11} className="rotate-90" />
                    </button>
                  </div>
                )
              })}
            </Popover>
          </div>

          {/* Density */}
          <button
            onClick={() =>
              setDensity((d) =>
                d === 'compact' ? 'comfortable' : d === 'comfortable' ? 'spacious' : 'compact',
              )
            }
            className={toolbarBtn}
            title={`Density: ${density}`}
          >
            {density === 'compact' ? <Rows4 size={12} /> : density === 'comfortable' ? <Rows3 size={12} /> : <AlignJustify size={12} />}
          </button>

          {/* Export menu */}
          <div className="relative">
            <button
              onClick={() => setOpenMenu((m) => (m === 'export' ? null : 'export'))}
              className={toolbarBtn}
              title="Export"
            >
              <Download size={12} />
            </button>
            <Popover open={openMenu === 'export'} onClose={() => setOpenMenu(null)}>
              <button
                onClick={doExportCSV}
                className="w-full flex items-center gap-2 px-2 py-1.5 rounded hover:bg-surface-2 text-[11px] text-fg text-left"
              >
                <FileText size={13} className="text-muted" /> Export CSV
              </button>
              <button
                onClick={doExportExcel}
                className="w-full flex items-center gap-2 px-2 py-1.5 rounded hover:bg-surface-2 text-[11px] text-fg text-left"
              >
                <FileSpreadsheet size={13} className="text-muted" /> Export Excel
              </button>
            </Popover>
          </div>
        </div>
      )}

      {/* ── Grid body ────────────────────────────────────────────────────── */}
      <div ref={scrollRef} className="flex-1 overflow-auto relative">
        {loading ? (
          <GridSkeleton cols={Math.min(columnDescriptors.length || 5, 7)} />
        ) : columnDescriptors.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-full gap-3 p-8 text-center">
            <div className="w-10 h-10 rounded-full bg-surface-2 border border-border flex items-center justify-center">
              <Database size={18} className="text-muted" />
            </div>
            <div>
              <p className="text-sm font-medium text-fg mb-1">No data</p>
              <p className="text-xs text-muted">Run a query to see results here.</p>
            </div>
          </div>
        ) : (
          <table className="border-collapse w-full" style={{ minWidth: table.getTotalSize() }}>
            {/* Header */}
            <thead className="sticky top-0 z-20">
              {table.getHeaderGroups().map((hg) => (
                <tr key={hg.id} className="bg-surface-2 border-b-2 border-border">
                  {hg.headers.map((header) => {
                    const col = header.column
                    const type = col.columnDef.meta?.type
                    const sortDir = col.getIsSorted()
                    const sortIndex = col.getSortIndex()
                    const isMultiSorted = sorting.length > 1 && sortIndex >= 0
                    return (
                      <th
                        key={header.id}
                        colSpan={header.colSpan}
                        className="relative select-none border-r border-border/40 last:border-r-0 group bg-surface-2"
                        style={{ width: header.getSize(), ...pinStyle(col) }}
                        draggable
                        onDragStart={() => (dragColRef.current = col.id)}
                        onDragOver={(e) => e.preventDefault()}
                        onDrop={() => handleColDrop(col.id)}
                        aria-sort={sortDir ? (sortDir === 'asc' ? 'ascending' : 'descending') : 'none'}
                      >
                        <div
                          className={[
                            'w-full flex items-center gap-1 py-2 font-semibold text-muted',
                            cellPx,
                            type === 'number' ? 'flex-row-reverse' : '',
                            fontSize,
                          ].join(' ')}
                        >
                          <button
                            className="flex items-center gap-1 min-w-0 hover:text-fg transition-colors"
                            onClick={col.getToggleSortingHandler()}
                            title={`Sort by ${String(col.columnDef.header)}`}
                          >
                            <span className="truncate">{flexRender(col.columnDef.header, header.getContext())}</span>
                            <span className={sortDir ? 'text-primary shrink-0' : 'text-muted/30 group-hover:text-muted/60 shrink-0'}>
                              {sortDir === 'asc' && <ChevronUp size={12} />}
                              {sortDir === 'desc' && <ChevronDown size={12} />}
                              {!sortDir && <ChevronsUpDown size={11} />}
                            </span>
                            {isMultiSorted && (
                              <span className="text-[9px] text-primary font-bold shrink-0">{sortIndex + 1}</span>
                            )}
                          </button>
                        </div>

                        {/* Resize handle */}
                        {col.getCanResize() && (
                          <div
                            onMouseDown={header.getResizeHandler()}
                            onTouchStart={header.getResizeHandler()}
                            className="absolute right-0 top-0 bottom-0 w-2 flex items-center justify-center cursor-col-resize opacity-0 group-hover:opacity-100 z-10"
                          >
                            <div className="w-0.5 h-4 rounded bg-primary/40 hover:bg-primary" />
                          </div>
                        )}
                      </th>
                    )
                  })}
                </tr>
              ))}

              {/* Filter row */}
              {showFilters && (
                <tr className="bg-surface-2/80 border-b border-border sticky top-[37px] z-10">
                  {table.getVisibleLeafColumns().map((col) => {
                    const type = col.columnDef.meta?.type ?? 'string'
                    const ops = type === 'number' ? NUMERIC_OPS : type === 'bool' ? ['eq', 'ne'] : STRING_OPS
                    const fv = col.getFilterValue() ?? { op: ops[0], value: '', type }
                    return (
                      <th
                        key={col.id}
                        className="px-1 py-1 border-r border-border/40 last:border-r-0 bg-surface-2/90"
                        style={{ width: col.getSize(), ...pinStyle(col) }}
                      >
                        <div className="flex items-center gap-1">
                          <select
                            className="text-[10px] bg-surface border border-border rounded px-0.5 py-0.5 text-muted focus:outline-none focus:ring-1 focus:ring-ring cursor-pointer h-6"
                            value={fv.op}
                            onChange={(e) => col.setFilterValue({ ...fv, op: e.target.value, type })}
                            style={{ width: 30 }}
                          >
                            {ops.map((op) => (
                              <option key={op} value={op}>{OP_LABELS[op]}</option>
                            ))}
                          </select>
                          <input
                            type="text"
                            className="flex-1 min-w-0 text-[11px] bg-surface border border-border rounded px-1.5 py-0.5 text-fg placeholder:text-muted/50 focus:outline-none focus:ring-1 focus:ring-ring h-6"
                            placeholder="Filter…"
                            value={fv.value}
                            onChange={(e) => {
                              const v = e.target.value
                              col.setFilterValue(v === '' ? undefined : { ...fv, value: v, type })
                            }}
                          />
                          {fv.value && (
                            <button
                              className="text-muted hover:text-fg shrink-0 p-0.5 rounded hover:bg-surface-2"
                              onClick={() => col.setFilterValue(undefined)}
                              title="Clear"
                            >
                              <X size={10} />
                            </button>
                          )}
                        </div>
                      </th>
                    )
                  })}
                </tr>
              )}
            </thead>

            {/* Body (virtualized) */}
            <tbody>
              {paddingTop > 0 && (
                <tr><td style={{ height: paddingTop }} colSpan={table.getVisibleLeafColumns().length} /></tr>
              )}
              {visibleRows.length === 0 ? (
                <tr>
                  <td colSpan={table.getVisibleLeafColumns().length} className="py-12 text-center text-xs text-muted">
                    {globalFilter || activeFilterCount > 0 ? 'No rows match the current filter.' : emptyMessage}
                  </td>
                </tr>
              ) : (
                virtualRows.map((vr) => {
                  const row = visibleRows[vr.index]
                  const isGrouped = row.getIsGrouped()
                  const rowStyle = !isGrouped && getRowStyle ? getRowStyle(row.original) : null
                  return (
                    <tr
                      key={row.id}
                      data-index={vr.index}
                      ref={rowVirtualizer.measureElement}
                      className={[
                        'group transition-colors',
                        isGrouped ? 'bg-surface-2 font-medium' : vr.index % 2 === 0 ? 'bg-surface' : 'bg-surface-2/40',
                        'hover:bg-primary/5',
                      ].join(' ')}
                      style={{ height: rowH, ...(rowStyle ?? {}) }}
                    >
                      {row.getVisibleCells().map((cell) => {
                        const col = cell.column
                        const type = col.columnDef.meta?.type
                        const align = col.columnDef.meta?.align
                        const cellStyle =
                          !isGrouped && getCellStyle ? getCellStyle(row.original, col.id) : null
                        const textAlign =
                          align === 'right' || (!align && type === 'number')
                            ? 'text-right'
                            : align === 'center'
                            ? 'text-center'
                            : ''
                        return (
                          <td
                            key={cell.id}
                            className={[
                              'border-b border-border/30 border-r border-r-border/20 last:border-r-0 text-fg transition-colors',
                              cellPx,
                              fontSize,
                              textAlign,
                              onCellClick && !isGrouped ? 'cursor-pointer' : '',
                              col.getIsPinned() ? 'bg-surface group-hover:bg-surface-2' : '',
                            ].join(' ')}
                            style={{ width: cell.column.getSize(), ...pinStyle(col), ...(cellStyle ?? {}) }}
                            onClick={
                              onCellClick && !isGrouped
                                ? () => onCellClick(cell.getValue(), row.original, col.id)
                                : undefined
                            }
                          >
                            {cell.getIsGrouped() ? (
                              <button
                                className="flex items-center gap-1 hover:text-fg"
                                onClick={row.getToggleExpandedHandler()}
                              >
                                <ChevronExpand
                                  size={12}
                                  className={`transition-transform ${row.getIsExpanded() ? 'rotate-90' : ''}`}
                                />
                                {flexRender(cell.column.columnDef.cell, cell.getContext())}
                                <span className="text-muted/60">({row.subRows.length})</span>
                              </button>
                            ) : cell.getIsAggregated() ? (
                              flexRender(
                                cell.column.columnDef.aggregatedCell ?? cell.column.columnDef.cell,
                                cell.getContext(),
                              )
                            ) : cell.getIsPlaceholder() ? null : (
                              flexRender(cell.column.columnDef.cell, cell.getContext())
                            )}
                          </td>
                        )
                      })}
                    </tr>
                  )
                })
              )}
              {paddingBottom > 0 && (
                <tr><td style={{ height: paddingBottom }} colSpan={table.getVisibleLeafColumns().length} /></tr>
              )}
            </tbody>
          </table>
        )}
      </div>

      {/* ── Footer ───────────────────────────────────────────────────────── */}
      <div className="shrink-0 flex items-center gap-3 px-3 py-2 border-t border-border bg-surface-2/60 flex-wrap">
        <span className="text-[11px] text-muted">
          {totalFilteredRows === 0 ? (
            '0 rows'
          ) : (
            <>
              <span className="font-mono font-semibold text-fg">{totalFilteredRows.toLocaleString()}</span>{' '}
              {totalFilteredRows === 1 ? 'row' : 'rows'}
              {totalFilteredRows !== rows.length && (
                <span className="text-muted/60"> (of {rows.length.toLocaleString()})</span>
              )}
            </>
          )}
        </span>

        <div className="flex-1" />

        {paginateProp && (
          <>
            <div className="flex items-center gap-1.5">
              <span className="text-[11px] text-muted">Rows:</span>
              <select
                className="text-[11px] bg-surface border border-border rounded px-1.5 py-0.5 text-fg focus:outline-none focus:ring-1 focus:ring-ring h-6 cursor-pointer"
                value={pagination.pageSize}
                onChange={(e) => table.setPageSize(Number(e.target.value))}
              >
                {PAGE_SIZE_OPTIONS.map((n) => (
                  <option key={n} value={n}>{n}</option>
                ))}
              </select>
            </div>
            <div className="flex items-center gap-1">
              <button
                className="h-6 w-6 flex items-center justify-center rounded border border-border text-muted hover:text-fg disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
                onClick={() => table.previousPage()}
                disabled={!table.getCanPreviousPage()}
                aria-label="Previous page"
              >
                <ChevronLeft size={13} />
              </button>
              <span className="text-[11px] text-muted px-1 font-mono">
                {table.getPageCount() > 0
                  ? `${pagination.pageIndex + 1} / ${table.getPageCount()}`
                  : '—'}
              </span>
              <button
                className="h-6 w-6 flex items-center justify-center rounded border border-border text-muted hover:text-fg disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
                onClick={() => table.nextPage()}
                disabled={!table.getCanNextPage()}
                aria-label="Next page"
              >
                <ChevronRight size={13} />
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Loading skeleton
// ---------------------------------------------------------------------------

function GridSkeleton({ rows = 8, cols = 5 }) {
  return (
    <table className="w-full text-xs">
      <thead className="sticky top-0 z-10 bg-surface-2">
        <tr>
          {Array.from({ length: cols }).map((_, i) => (
            <th key={i} className="px-3 py-2 border-b border-border">
              <div className="h-3 rounded bg-border animate-pulse" style={{ width: `${60 + i * 15}px` }} />
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {Array.from({ length: rows }).map((_, ri) => (
          <tr key={ri} className={ri % 2 === 0 ? 'bg-surface' : 'bg-surface-2'}>
            {Array.from({ length: cols }).map((_, ci) => (
              <td key={ci} className="px-3 py-2 border-b border-border/40">
                <div
                  className="h-3 rounded bg-border/70 animate-pulse"
                  style={{
                    width: `${40 + Math.sin(ri + ci) * 30 + 40}px`,
                    animationDelay: `${(ri * cols + ci) * 30}ms`,
                  }}
                />
              </td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  )
}
