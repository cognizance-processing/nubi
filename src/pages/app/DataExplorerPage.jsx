/**
 * DataExplorerPage — Supabase-style connector data browser.
 *
 * Route: /data
 *
 * Layout
 * ------
 * Desktop: left rail (220px) + main panel with tabs.
 * Mobile/tablet: left rail collapses to a dropdown; main stays full-width.
 *
 * Left rail
 * ---------
 *   - Connector picker: lists org datastores + a built-in "Demo" entry.
 *   - Table list (searchable) for the selected connector.
 *
 * Main panel — two tabs
 * ---------------------
 *   "Data"   — DataGrid showing rows fetched via GET /data/.../rows.
 *   "Schema" — clean column list (name, type, nullable, PK badge).
 *
 * Features: row count, refresh button, loading/error/empty states.
 */

import { useState, useEffect, useCallback, useRef } from 'react'
import * as arrow from 'apache-arrow'
import {
  Database,
  Table2,
  Search,
  RefreshCw,
  ChevronDown,
  ChevronRight,
  Loader2,
  AlertCircle,
  Hash,
  Type,
  Calendar,
  ToggleLeft,
  Key,
  Rows3,
  SlidersHorizontal,
} from 'lucide-react'
import DataGrid from '../../components/DataGrid.jsx'
import { get, getAccessToken } from '../../lib/api.js'

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const BACKEND_URL = import.meta.env.VITE_BACKEND_URL ?? 'http://localhost:8000'
const DEFAULT_LIMIT = 500
const DEMO_ENTRY = { id: null, name: 'Demo (built-in)', config: { connector_type: 'duckdb' } }

// ---------------------------------------------------------------------------
// API helpers
// ---------------------------------------------------------------------------

/** GET /data/tables or /data/{id}/tables */
async function fetchTables(datastoreId) {
  const path = datastoreId ? `/data/${datastoreId}/tables` : '/data/tables'
  return get(path)
}

/** GET /data/tables/{table}/columns or /data/{id}/tables/{table}/columns */
async function fetchColumns(datastoreId, table) {
  const path = datastoreId
    ? `/data/${datastoreId}/tables/${encodeURIComponent(table)}/columns`
    : `/data/tables/${encodeURIComponent(table)}/columns`
  return get(path)
}

/**
 * GET rows as Arrow IPC — returns { table: arrow.Table, rowCount: number }.
 * Uses a direct fetch (not api.js request) to handle binary Arrow IPC.
 */
async function fetchRows(datastoreId, table, limit = DEFAULT_LIMIT) {
  const pathBase = datastoreId
    ? `${BACKEND_URL}/api/v1/data/${datastoreId}/tables/${encodeURIComponent(table)}/rows`
    : `${BACKEND_URL}/api/v1/data/tables/${encodeURIComponent(table)}/rows`
  const url = `${pathBase}?limit=${limit}`

  const headers = { Accept: 'application/vnd.apache.arrow.stream' }
  const token = getAccessToken()
  if (token) headers['Authorization'] = `Bearer ${token}`

  const resp = await fetch(url, { headers, credentials: 'include' })
  if (!resp.ok) {
    const txt = await resp.text().catch(() => String(resp.status))
    throw new Error(`Row fetch failed (${resp.status}): ${txt}`)
  }
  const buf = await resp.arrayBuffer()
  // Buffer is already complete → synchronous IPC decode. (apache-arrow has no
  // `fromByteStream`; `RecordBatchReader.from()` is the supported entry point.)
  const arrowReader = arrow.RecordBatchReader.from(new Uint8Array(buf))
  const tbl = new arrow.Table([...arrowReader])
  return { table: tbl, rowCount: tbl.numRows }
}

// ---------------------------------------------------------------------------
// Arrow table → DataGrid props conversion
// ---------------------------------------------------------------------------

function arrowTypeLabel(field) {
  const t = field.type
  if (arrow.DataType.isInt(t) || arrow.DataType.isFloat(t)) return 'number'
  if (arrow.DataType.isBool(t)) return 'bool'
  if (arrow.DataType.isDate(t) || arrow.DataType.isTimestamp(t)) return 'date'
  return 'string'
}

function arrowTableToGrid(tbl) {
  if (!tbl || tbl.numCols === 0) return { columns: [], rows: [] }
  const columns = tbl.schema.fields.map((f) => ({
    key: f.name,
    label: f.name,
    type: arrowTypeLabel(f),
  }))
  const rows = []
  const colNames = columns.map((c) => c.key)
  for (let i = 0; i < tbl.numRows; i++) {
    const row = {}
    for (const name of colNames) {
      const v = tbl.getChild(name)?.get(i)
      row[name] = v == null ? null : (typeof v === 'bigint' ? Number(v) : v)
    }
    rows.push(row)
  }
  return { columns, rows }
}

// ---------------------------------------------------------------------------
// Column type icon
// ---------------------------------------------------------------------------

function TypeIcon({ type }) {
  const cls = 'shrink-0 text-muted'
  switch (type?.toLowerCase()) {
    case 'integer':
    case 'bigint':
    case 'int':
    case 'int32':
    case 'int64':
    case 'float':
    case 'double':
    case 'numeric':
    case 'decimal':
      return <Hash size={12} className={cls} />
    case 'boolean':
    case 'bool':
      return <ToggleLeft size={12} className={cls} />
    case 'date':
    case 'timestamp':
    case 'timestamptz':
      return <Calendar size={12} className={cls} />
    default:
      return <Type size={12} className={cls} />
  }
}

// ---------------------------------------------------------------------------
// Table list item
// ---------------------------------------------------------------------------

function TableItem({ name, active, onClick }) {
  return (
    <button
      onClick={onClick}
      className={[
        'w-full flex items-center gap-2 px-3 py-1.5 rounded-lg text-sm font-mono text-left transition-colors',
        active
          ? 'bg-primary/10 text-primary'
          : 'text-fg hover:bg-surface-2 text-muted hover:text-fg',
      ].join(' ')}
    >
      <Table2 size={13} className={active ? 'text-primary' : 'text-muted'} />
      <span className="truncate">{name}</span>
    </button>
  )
}

// ---------------------------------------------------------------------------
// Schema tab
// ---------------------------------------------------------------------------

function SchemaTab({ columns, loading, error }) {
  if (loading) {
    return (
      <div className="p-6 space-y-2">
        {Array.from({ length: 5 }).map((_, i) => (
          <div key={i} className="h-8 rounded-lg bg-border/40 animate-pulse" />
        ))}
      </div>
    )
  }
  if (error) {
    return (
      <div className="flex flex-col items-center justify-center p-10 gap-3 text-center">
        <AlertCircle size={20} className="text-rose-500" />
        <p className="text-sm text-rose-500">{error}</p>
      </div>
    )
  }
  if (!columns || columns.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center p-10 gap-3 text-center">
        <Database size={24} className="text-muted/40" />
        <p className="text-sm text-muted">Select a table to view its schema.</p>
      </div>
    )
  }
  return (
    <div className="overflow-auto">
      <table className="w-full text-sm">
        <thead className="sticky top-0 z-10">
          <tr className="bg-surface-2 border-b border-border text-xs text-muted font-semibold">
            <th className="text-left px-4 py-2.5">Column</th>
            <th className="text-left px-4 py-2.5">Type</th>
            <th className="text-left px-4 py-2.5">Nullable</th>
          </tr>
        </thead>
        <tbody>
          {columns.map((col, i) => (
            <tr
              key={col.name}
              className={[
                'border-b border-border/30 hover:bg-primary/5 transition-colors',
                i % 2 === 0 ? 'bg-surface' : 'bg-surface-2/30',
              ].join(' ')}
            >
              <td className="px-4 py-2 font-mono text-xs text-fg flex items-center gap-2">
                <TypeIcon type={col.type} />
                {col.pk && (
                  <Key size={11} className="text-amber-500 shrink-0" title="Primary key" />
                )}
                {col.name}
              </td>
              <td className="px-4 py-2 font-mono text-xs text-muted">{col.type}</td>
              <td className="px-4 py-2 text-xs">
                {col.nullable ? (
                  <span className="text-muted/60">YES</span>
                ) : (
                  <span className="inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-semibold bg-surface-2 border border-border text-muted">
                    NOT NULL
                  </span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Connector picker (mobile dropdown or desktop label)
// ---------------------------------------------------------------------------

function ConnectorDropdown({ connectors, selectedId, onSelect }) {
  const [open, setOpen] = useState(false)
  const ref = useRef(null)

  useEffect(() => {
    if (!open) return
    const onDown = (e) => {
      if (ref.current && !ref.current.contains(e.target)) setOpen(false)
    }
    document.addEventListener('mousedown', onDown)
    return () => document.removeEventListener('mousedown', onDown)
  }, [open])

  const selected = connectors.find((c) => c.id === selectedId) ?? connectors[0]

  return (
    <div ref={ref} className="relative">
      <button
        onClick={() => setOpen((v) => !v)}
        className="w-full flex items-center gap-2 px-3 py-2 rounded-xl border border-border bg-surface-2 hover:bg-surface text-sm font-medium text-fg transition-colors focus:outline-none focus:ring-2 focus:ring-ring"
      >
        <Database size={14} className="text-muted shrink-0" />
        <span className="flex-1 truncate text-left">{selected?.name ?? 'Select connector'}</span>
        <ChevronDown size={13} className={`text-muted shrink-0 transition-transform ${open ? 'rotate-180' : ''}`} />
      </button>
      {open && (
        <div className="absolute left-0 right-0 top-full mt-1 z-50 rounded-xl border border-border bg-surface shadow-xl shadow-black/10 py-1">
          {connectors.map((c) => (
            <button
              key={c.id ?? 'demo'}
              onClick={() => { onSelect(c.id); setOpen(false) }}
              className={[
                'w-full flex items-center gap-2 px-3 py-2 text-sm text-left transition-colors',
                c.id === selectedId ? 'text-primary bg-primary/5' : 'text-fg hover:bg-surface-2',
              ].join(' ')}
            >
              <Database size={13} className={c.id === selectedId ? 'text-primary' : 'text-muted'} />
              <span className="flex-1 truncate">{c.name}</span>
              {c.id === selectedId && <ChevronRight size={12} className="text-primary" />}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export default function DataExplorerPage() {
  // ── State ─────────────────────────────────────────────────────────────────
  const [connectors, setConnectors] = useState([DEMO_ENTRY])
  const [selectedConnectorId, setSelectedConnectorId] = useState(null) // null = demo

  const [tables, setTables] = useState([])
  const [tablesLoading, setTablesLoading] = useState(false)
  const [tablesError, setTablesError] = useState(null)
  const [tableSearch, setTableSearch] = useState('')
  const [selectedTable, setSelectedTable] = useState(null)

  const [activeTab, setActiveTab] = useState('data') // 'data' | 'schema'

  // Schema tab state
  const [columns, setColumns] = useState([])
  const [columnsLoading, setColumnsLoading] = useState(false)
  const [columnsError, setColumnsError] = useState(null)

  // Data tab state
  const [gridData, setGridData] = useState({ columns: [], rows: [] })
  const [rowsLoading, setRowsLoading] = useState(false)
  const [rowsError, setRowsError] = useState(null)
  const [totalRows, setTotalRows] = useState(null)
  const [refreshKey, setRefreshKey] = useState(0)

  // Mobile rail open/close
  const [railOpen, setRailOpen] = useState(false)

  // ── Load connectors ───────────────────────────────────────────────────────
  useEffect(() => {
    let cancelled = false
    ;(async () => {
      try {
        const data = await get('/connectors')
        if (cancelled) return
        const list = Array.isArray(data) ? data : (data?.connectors ?? [])
        setConnectors([DEMO_ENTRY, ...list])
      } catch {
        // Keep demo entry
      }
    })()
    return () => { cancelled = true }
  }, [])

  // ── Load tables when connector changes ────────────────────────────────────
  useEffect(() => {
    let cancelled = false
    setSelectedTable(null)
    setColumns([])
    setGridData({ columns: [], rows: [] })
    setTotalRows(null)
    setTablesError(null)
    setTablesLoading(true)

    ;(async () => {
      try {
        const data = await fetchTables(selectedConnectorId)
        if (cancelled) return
        const list = (data?.tables ?? []).map((t) =>
          typeof t === 'string' ? { name: t, schema: 'main' } : t
        )
        setTables(list)
      } catch (e) {
        if (!cancelled) setTablesError(e.message)
      } finally {
        if (!cancelled) setTablesLoading(false)
      }
    })()

    return () => { cancelled = true }
  }, [selectedConnectorId])

  // ── Load columns + rows when table or refreshKey changes ─────────────────
  useEffect(() => {
    if (!selectedTable) return

    let cancelled = false

    // Columns
    setColumnsLoading(true)
    setColumnsError(null)
    ;(async () => {
      try {
        const data = await fetchColumns(selectedConnectorId, selectedTable)
        if (cancelled) return
        setColumns(data?.columns ?? [])
      } catch (e) {
        if (!cancelled) setColumnsError(e.message)
      } finally {
        if (!cancelled) setColumnsLoading(false)
      }
    })()

    // Rows
    setRowsLoading(true)
    setRowsError(null)
    setGridData({ columns: [], rows: [] })
    setTotalRows(null)
    ;(async () => {
      try {
        const { table, rowCount } = await fetchRows(selectedConnectorId, selectedTable, DEFAULT_LIMIT)
        if (cancelled) return
        setGridData(arrowTableToGrid(table))
        setTotalRows(rowCount)
      } catch (e) {
        if (!cancelled) setRowsError(e.message)
      } finally {
        if (!cancelled) setRowsLoading(false)
      }
    })()

    return () => { cancelled = true }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedTable, selectedConnectorId, refreshKey])

  // ── Handlers ──────────────────────────────────────────────────────────────
  const handleSelectTable = useCallback((name) => {
    setSelectedTable(name)
    setActiveTab('data')
    setRailOpen(false)
  }, [])

  const handleRefresh = useCallback(() => {
    setRefreshKey((k) => k + 1)
  }, [])

  const handleSelectConnector = useCallback((id) => {
    setSelectedConnectorId(id)
    setTableSearch('')
  }, [])

  // ── Filtered tables ───────────────────────────────────────────────────────
  const filteredTables = tables.filter((t) =>
    t.name.toLowerCase().includes(tableSearch.toLowerCase())
  )

  // ── Selected connector label ──────────────────────────────────────────────
  const selectedConnector = connectors.find((c) => c.id === selectedConnectorId) ?? DEMO_ENTRY
  const connectorType = selectedConnector?.config?.connector_type ?? 'duckdb'

  // ── Render ────────────────────────────────────────────────────────────────
  return (
    <div className="flex h-full overflow-hidden bg-background">

      {/* ── Mobile rail toggle ──────────────────────────────────────────── */}
      <div className="md:hidden shrink-0 flex items-center border-b border-border bg-surface px-3 py-2 gap-2 absolute top-0 left-0 right-0 z-30">
        <button
          onClick={() => setRailOpen((v) => !v)}
          className="flex items-center gap-2 text-sm font-medium text-fg border border-border rounded-lg px-2.5 py-1.5 bg-surface-2 hover:bg-surface transition-colors"
        >
          <SlidersHorizontal size={14} className="text-muted" />
          {selectedTable ? (
            <span className="font-mono">{selectedTable}</span>
          ) : (
            <span className="text-muted">Select table</span>
          )}
          <ChevronDown size={12} className={`text-muted transition-transform ${railOpen ? 'rotate-180' : ''}`} />
        </button>
      </div>

      {/* ── Mobile rail overlay ──────────────────────────────────────────── */}
      {railOpen && (
        <div
          className="md:hidden fixed inset-0 z-40 bg-black/40 backdrop-blur-sm"
          onClick={() => setRailOpen(false)}
        />
      )}

      {/* ── Left rail ───────────────────────────────────────────────────── */}
      <aside
        className={[
          'flex flex-col shrink-0 border-r border-border bg-surface',
          // Desktop: always visible, fixed width
          'md:relative md:flex md:w-[220px]',
          // Mobile: absolute overlay drawer
          'fixed inset-y-0 left-0 z-50 w-[260px] transition-transform duration-200',
          railOpen ? 'translate-x-0' : '-translate-x-full md:translate-x-0',
        ].join(' ')}
      >
        {/* Connector picker */}
        <div className="p-3 border-b border-border">
          <p className="text-[10px] font-semibold text-muted uppercase tracking-wider mb-2 px-1">
            Connector
          </p>
          <ConnectorDropdown
            connectors={connectors}
            selectedId={selectedConnectorId}
            onSelect={handleSelectConnector}
          />
        </div>

        {/* Table search */}
        <div className="px-3 py-2 border-b border-border">
          <div className="relative">
            <Search size={12} className="absolute left-2 top-1/2 -translate-y-1/2 text-muted pointer-events-none" />
            <input
              type="text"
              className="w-full h-7 pl-6 pr-2 text-xs bg-surface-2 border border-border rounded-lg text-fg placeholder:text-muted/50 focus:outline-none focus:ring-1 focus:ring-ring"
              placeholder="Search tables…"
              value={tableSearch}
              onChange={(e) => setTableSearch(e.target.value)}
            />
          </div>
        </div>

        {/* Table list */}
        <div className="flex-1 overflow-y-auto py-2 px-2">
          {tablesLoading ? (
            <div className="space-y-1 px-1">
              {Array.from({ length: 4 }).map((_, i) => (
                <div key={i} className="h-7 rounded-lg bg-border/40 animate-pulse" />
              ))}
            </div>
          ) : tablesError ? (
            <div className="flex flex-col items-center gap-2 p-4 text-center">
              <AlertCircle size={16} className="text-rose-500" />
              <p className="text-xs text-rose-500">{tablesError}</p>
            </div>
          ) : filteredTables.length === 0 ? (
            <p className="text-xs text-muted px-3 py-4 text-center">
              {tableSearch ? 'No tables match your search.' : 'No tables found.'}
            </p>
          ) : (
            <div className="space-y-0.5">
              {filteredTables.map((t) => (
                <TableItem
                  key={t.name}
                  name={t.name}
                  active={selectedTable === t.name}
                  onClick={() => handleSelectTable(t.name)}
                />
              ))}
            </div>
          )}
        </div>

        {/* Rail footer: connector type badge */}
        <div className="px-4 py-2.5 border-t border-border">
          <span className="inline-flex items-center gap-1 text-[10px] font-mono text-muted/60">
            <Database size={9} />
            {connectorType}
          </span>
        </div>
      </aside>

      {/* ── Main panel ──────────────────────────────────────────────────── */}
      <main className="flex flex-col flex-1 min-w-0 overflow-hidden md:pt-0 pt-[44px]">

        {/* Header */}
        <div className="shrink-0 flex items-center gap-3 px-4 py-3 border-b border-border bg-surface flex-wrap">
          {selectedTable ? (
            <>
              <div className="flex items-center gap-2 min-w-0">
                <Table2 size={16} className="text-primary shrink-0" />
                <h1 className="text-base font-semibold font-mono text-fg truncate">
                  {selectedTable}
                </h1>
              </div>
              {totalRows != null && !rowsLoading && (
                <span className="inline-flex items-center gap-1 text-xs text-muted border border-border rounded-full px-2 py-0.5">
                  <Rows3 size={11} />
                  {totalRows.toLocaleString()} row{totalRows !== 1 ? 's' : ''}
                  {totalRows >= DEFAULT_LIMIT && ` (first ${DEFAULT_LIMIT})`}
                </span>
              )}
              {rowsLoading && (
                <span className="inline-flex items-center gap-1 text-xs text-muted">
                  <Loader2 size={11} className="animate-spin" /> Loading…
                </span>
              )}
            </>
          ) : (
            <div className="flex items-center gap-2 text-muted">
              <Database size={16} />
              <span className="text-sm">Select a table to browse data</span>
            </div>
          )}

          <div className="flex-1" />

          {selectedTable && (
            <button
              onClick={handleRefresh}
              disabled={rowsLoading || columnsLoading}
              className="flex items-center gap-1.5 text-xs text-muted hover:text-fg border border-border rounded-lg px-2.5 py-1.5 bg-surface hover:bg-surface-2 transition-colors disabled:opacity-40"
              title="Refresh"
            >
              <RefreshCw size={12} className={rowsLoading ? 'animate-spin' : ''} />
              Refresh
            </button>
          )}
        </div>

        {/* Tabs */}
        {selectedTable && (
          <div className="shrink-0 flex gap-0 px-4 border-b border-border bg-surface">
            {['data', 'schema'].map((tab) => (
              <button
                key={tab}
                onClick={() => setActiveTab(tab)}
                className={[
                  'px-4 py-2.5 text-sm font-medium border-b-2 transition-colors capitalize',
                  activeTab === tab
                    ? 'border-primary text-primary'
                    : 'border-transparent text-muted hover:text-fg',
                ].join(' ')}
              >
                {tab}
              </button>
            ))}
          </div>
        )}

        {/* Tab content */}
        <div className="flex-1 overflow-hidden">
          {!selectedTable ? (
            /* Empty state — no table selected */
            <div className="flex flex-col items-center justify-center h-full gap-4 p-8 text-center">
              <div className="w-14 h-14 rounded-2xl bg-surface-2 border border-border flex items-center justify-center">
                <Database size={24} className="text-muted/60" />
              </div>
              <div>
                <p className="text-base font-semibold text-fg mb-1">
                  Browse your connector data
                </p>
                <p className="text-sm text-muted max-w-xs">
                  Pick a connector and select a table from the left rail to view its data and schema.
                </p>
              </div>
              {tables.length > 0 && (
                <div className="flex flex-wrap gap-2 justify-center max-w-sm">
                  {tables.slice(0, 6).map((t) => (
                    <button
                      key={t.name}
                      onClick={() => handleSelectTable(t.name)}
                      className="px-3 py-1.5 text-xs font-mono rounded-lg border border-border bg-surface-2 hover:border-primary/40 hover:text-primary transition-colors text-muted"
                    >
                      {t.name}
                    </button>
                  ))}
                </div>
              )}
            </div>
          ) : activeTab === 'schema' ? (
            <SchemaTab
              columns={columns}
              loading={columnsLoading}
              error={columnsError}
            />
          ) : (
            /* Data tab — DataGrid */
            <div className="flex flex-col h-full">
              <DataGrid
                columns={gridData.columns}
                rows={gridData.rows}
                loading={rowsLoading}
                error={rowsError}
                title={null}
                toolbar={true}
                pageSize={50}
                paginate={true}
                density="comfortable"
                exportFileName={selectedTable}
                emptyMessage="No rows returned."
                className="border-0 rounded-none"
              />
            </div>
          )}
        </div>
      </main>
    </div>
  )
}
