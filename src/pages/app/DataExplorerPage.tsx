/**
 * DataExplorerPage — Supabase-style connector data browser.
 *
 * Route: /data   (optionally /data?connector=<datastore_id> to pre-select one)
 *
 * Layout
 * ------
 * Desktop: left rail (220px) + main panel.
 * Mobile/tablet: left rail collapses to a dropdown; main stays full-width.
 *
 * Left rail
 * ---------
 *   - Connector picker: lists org datastores + a built-in "Demo" entry.
 *   - Table list (searchable) for the selected connector.
 *
 * Main panel
 * ----------
 *   EditableDataGrid — a sticky-header / sticky-selector grid with type-aware
 *   rendering, click-to-sort, resizable columns, a row-detail panel, and INLINE
 *   CELL EDITING + insert/delete (gated on the backend write contract). It
 *   degrades to read-only when the table is not writable / has no primary key.
 *
 * The connector is reflected in the URL (?connector=<id>, shallow) so the page
 * is deep-linkable from the Connectors page. Selecting the demo connector
 * clears the param. All loads use the deferred async pattern (no setState in an
 * effect body) per the repo's react-hooks rules.
 */

import { useState, useEffect, useCallback, useRef } from 'react'
import { useSearchParams } from 'react-router-dom'
import {
  Database,
  Table2,
  Search,
  RefreshCw,
  ChevronDown,
  ChevronRight,
  AlertCircle,
  SlidersHorizontal,
  BarChart2,
} from 'lucide-react'
import EditableDataGrid from '../../components/app/EditableDataGrid.jsx'
import { normalizeColumnMeta } from '../../components/app/editableGridUtils.js'
import * as api from '../../lib/api.js'
import DatasetProfileView from './DatasetProfileView.jsx'
import EmptyState from '../../components/ui/EmptyState.jsx'
import Badge from '../../components/ui/Badge.jsx'

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const ROW_LIMIT = 200
const DEMO_ENTRY = { id: null, name: 'Demo (built-in)', config: { connector_type: 'duckdb' } }

// ---------------------------------------------------------------------------
// API helpers
// ---------------------------------------------------------------------------

/** GET /data/tables or /data/{id}/tables */
async function fetchTables(datastoreId) {
  const path = datastoreId ? `/data/${datastoreId}/tables` : '/data/tables'
  return api.get(path)
}

// ---------------------------------------------------------------------------
// Table list item
// ---------------------------------------------------------------------------

function TableItem({ name, active, onClick }) {
  return (
    <button
      onClick={onClick}
      className={[
        'w-full flex items-center gap-2 px-2.5 py-1.5 rounded-lg text-[13px] font-mono text-left transition-colors duration-100',
        'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
        active
          ? 'bg-primary/10 text-primary'
          : 'text-muted hover:bg-surface-2 hover:text-fg',
      ].join(' ')}
    >
      <Table2 size={13} className={active ? 'text-primary shrink-0' : 'text-muted/70 shrink-0'} />
      <span className="truncate">{name}</span>
    </button>
  )
}

// ---------------------------------------------------------------------------
// Connector picker (mobile dropdown or desktop label)
// ---------------------------------------------------------------------------

function ConnectorAvatar({ name, active }) {
  const letter = (name ?? '?').trim().charAt(0).toUpperCase() || '?'
  return (
    <span
      className={[
        'flex items-center justify-center w-6 h-6 rounded-lg text-[11px] font-display font-semibold shrink-0',
        active ? 'bg-primary text-primary-fg' : 'bg-surface-2 text-muted border border-border',
      ].join(' ')}
      aria-hidden="true"
    >
      {letter}
    </span>
  )
}

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
        className="w-full flex items-center gap-2 px-2.5 py-2 rounded-xl border border-border bg-surface-2 hover:bg-surface text-sm font-medium text-fg transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        aria-haspopup="listbox"
        aria-expanded={open}
      >
        <ConnectorAvatar name={selected?.name} active />
        <span className="flex-1 truncate text-left">{selected?.name ?? 'Select connector'}</span>
        <ChevronDown size={13} className={`text-muted shrink-0 transition-transform duration-150 ${open ? 'rotate-180' : ''}`} />
      </button>
      {open && (
        <div
          className="absolute left-0 right-0 top-full mt-1.5 z-50 rounded-xl border border-border bg-surface shadow-nubi-xl p-1 max-h-72 overflow-y-auto nubi-animate-scale-in"
          role="listbox"
        >
          {connectors.map((c) => (
            <button
              key={c.id ?? 'demo'}
              role="option"
              aria-selected={c.id === selectedId}
              onClick={() => { onSelect(c.id); setOpen(false) }}
              className={[
                'w-full flex items-center gap-2 px-2 py-1.5 rounded-lg text-sm text-left transition-colors duration-100',
                c.id === selectedId ? 'text-primary bg-primary/5' : 'text-fg hover:bg-surface-2',
              ].join(' ')}
            >
              <ConnectorAvatar name={c.name} active={c.id === selectedId} />
              <span className="flex-1 min-w-0">
                <span className="block truncate">{c.name}</span>
                {c.config?.connector_type && (
                  <span className="block truncate text-[10px] font-mono text-muted/70 leading-tight">
                    {c.config.connector_type}
                  </span>
                )}
              </span>
              {c.id === selectedId && <ChevronRight size={12} className="text-primary shrink-0" />}
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
  // ── URL state — ?connector=<datastore_id> pre-selects a connector ─────────
  const [searchParams, setSearchParams] = useSearchParams()

  // Seed the selected connector from the URL on first render (no setState in an
  // effect). `null` = the built-in demo datastore.
  const [selectedConnectorId, setSelectedConnectorId] = useState(
    () => searchParams.get('connector') || null,
  )

  // ── State ─────────────────────────────────────────────────────────────────
  const [connectors, setConnectors] = useState([DEMO_ENTRY])

  const [tables, setTables] = useState([])
  const [tablesLoading, setTablesLoading] = useState(false)
  const [tablesError, setTablesError] = useState(null)
  const [tablesReloadKey, setTablesReloadKey] = useState(0)
  const [tableSearch, setTableSearch] = useState('')
  const [selectedTable, setSelectedTable] = useState(null)
  // Schema owning `selectedTable`. A connector can expose the same table name in
  // several schemas; without this the server falls back to the connection's
  // default schema and opens the wrong table (or none).
  const [selectedSchema, setSelectedSchema] = useState(null)

  // Per-table data + meta (loaded here, passed to EditableDataGrid).
  const [meta, setMeta] = useState(null)
  const [rows, setRows] = useState([])
  const [total, setTotal] = useState(null)
  const [dataLoading, setDataLoading] = useState(false)
  const [dataError, setDataError] = useState(null)
  const [reloadKey, setReloadKey] = useState(0)

  // Mobile rail open/close
  const [railOpen, setRailOpen] = useState(false)

  // Profile tab
  const [mainTab, setMainTab] = useState('data') // 'data' | 'profile'
  const [datasets, setDatasets] = useState([])

  // Load org datasets list (for Profile tab dataset_id lookup)
  useEffect(() => {
    let cancelled = false
    api.get('/datasets').then(data => {
      if (cancelled) return
      const list = Array.isArray(data) ? data : (data?.datasets ?? [])
      setDatasets(list)
    }).catch(() => {})
    return () => { cancelled = true }
  }, [])

  // ── Load connectors ───────────────────────────────────────────────────────
  useEffect(() => {
    let cancelled = false
    ;(async () => {
      try {
        const data = await api.get('/connectors')
        if (cancelled) return
        const list = Array.isArray(data) ? data : (data?.connectors ?? [])
        // The backend already injects the virtual "Demo data" connector into
        // this list, so don't prepend our own — that produced a duplicate demo
        // entry. Fall back to the local demo entry only if the list is empty.
        setConnectors(list.length ? list : [DEMO_ENTRY])
      } catch {
        // Keep the local demo entry as a fallback.
      }
    })()
    return () => { cancelled = true }
  }, [])

  // ── Load tables when connector changes ────────────────────────────────────
  useEffect(() => {
    let cancelled = false
    setSelectedTable(null)
    setSelectedSchema(null)
    setMeta(null)
    setRows([])
    setTotal(null)
    setTablesError(null)
    setTablesLoading(true)

    ;(async () => {
      try {
        const data = await fetchTables(selectedConnectorId)
        if (cancelled) return
        const list = (data?.tables ?? data ?? []).map((t) =>
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
  }, [selectedConnectorId, tablesReloadKey])

  // ── Load meta + rows for the selected table ───────────────────────────────
  // A monotonically increasing token guards against out-of-order responses
  // when the user clicks between tables quickly.
  const loadToken = useRef(0)

  useEffect(() => {
    if (!selectedTable) {
      setMeta(null)
      setRows([])
      setTotal(null)
      return
    }
    const token = ++loadToken.current
    setDataLoading(true)
    setDataError(null)

    ;(async () => {
      try {
        const [rawMeta, rowData] = await Promise.all([
          api
            .fetchDataColumns(selectedConnectorId, selectedTable, { schema: selectedSchema })
            .catch(() => ({})),
          api.fetchDataRows(selectedConnectorId, selectedTable, {
            limit: ROW_LIMIT,
            schema: selectedSchema,
          }),
        ])
        if (token !== loadToken.current) return
        setMeta(normalizeColumnMeta(rawMeta))
        setRows(rowData.rows)
        setTotal(rowData.total)
      } catch (err) {
        if (token !== loadToken.current) return
        setDataError(err.message ?? 'Failed to load table data')
        setMeta(null)
        setRows([])
        setTotal(null)
      } finally {
        if (token === loadToken.current) setDataLoading(false)
      }
    })()
  }, [selectedConnectorId, selectedTable, selectedSchema, reloadKey])

  // ── Handlers ──────────────────────────────────────────────────────────────
  const handleSelectTable = useCallback((name, schema = null) => {
    setSelectedTable(name)
    setSelectedSchema(schema ?? null)
    setMainTab('data')
    setRailOpen(false)
  }, [])

  const refreshData = useCallback(() => setReloadKey((k) => k + 1), [])

  const handleTotalChange = useCallback((delta) => {
    setTotal((t) => (t == null ? t : Math.max(0, t + delta)))
  }, [])

  // Switching connectors reflects shallowly into the URL (?connector=<id>);
  // the demo connector (id === null) clears the param.
  const handleSelectConnector = useCallback((id) => {
    setSelectedConnectorId(id)
    setTableSearch('')
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev)
        if (id) next.set('connector', id)
        else next.delete('connector')
        return next
      },
      { replace: true },
    )
  }, [setSearchParams])

  // ── Filtered tables ───────────────────────────────────────────────────────
  const filteredTables = tables.filter((t) =>
    t.name.toLowerCase().includes(tableSearch.toLowerCase())
  )

  // ── Selected connector label ──────────────────────────────────────────────
  const selectedConnector = connectors.find((c) => c.id === selectedConnectorId) ?? DEMO_ENTRY
  const connectorType = selectedConnector?.config?.connector_type ?? 'duckdb'

  // ── Dataset id lookup for Profile tab ─────────────────────────────────────
  // Match a dataset whose name matches the selected table (best-effort). If
  // multiple datasets match, prefer an exact name match.
  const profileDatasetId = selectedTable
    ? (datasets.find(d => d.name === selectedTable) ?? datasets.find(d => (d.name ?? '').toLowerCase() === selectedTable.toLowerCase()))?.id ?? null
    : null

  // ── Render ────────────────────────────────────────────────────────────────
  return (
    <div className="flex h-full overflow-hidden bg-bg">

      {/* ── Mobile rail toggle ──────────────────────────────────────────── */}
      <div className="md:hidden shrink-0 flex items-center border-b border-border bg-surface px-3 py-2 gap-2 absolute top-0 left-0 right-0 z-30">
        <button
          onClick={() => setRailOpen((v) => !v)}
          className="flex items-center gap-2 text-sm font-medium text-fg border border-border rounded-lg px-2.5 py-1.5 bg-surface-2 hover:bg-surface transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          aria-expanded={railOpen}
          aria-label="Toggle connector and table list"
        >
          <SlidersHorizontal size={14} className="text-muted shrink-0" />
          {selectedTable ? (
            <span className="font-mono truncate max-w-[50vw]">{selectedTable}</span>
          ) : (
            <span className="text-muted">Select table</span>
          )}
          <ChevronDown size={12} className={`text-muted shrink-0 transition-transform duration-150 ${railOpen ? 'rotate-180' : ''}`} />
        </button>
      </div>

      {/* ── Mobile rail overlay ──────────────────────────────────────────── */}
      {railOpen && (
        <div
          className="md:hidden fixed inset-0 z-40 bg-black/40 backdrop-blur-sm nubi-animate-fade-in"
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
              className="w-full h-7 pl-6 pr-2 text-xs bg-surface-2 border border-border rounded-lg text-fg placeholder:text-muted/50 transition-colors focus:outline-none focus:ring-2 focus:ring-ring focus:border-transparent"
              placeholder="Search tables…"
              value={tableSearch}
              onChange={(e) => setTableSearch(e.target.value)}
              aria-label="Search tables"
            />
          </div>
        </div>

        {/* Table list */}
        <div className="flex-1 overflow-y-auto py-2 px-2">
          {tablesLoading ? (
            <div className="space-y-1 px-1" aria-hidden="true">
              {Array.from({ length: 4 }).map((_, i) => (
                <div key={i} className="h-7 rounded-lg nubi-shimmer" />
              ))}
            </div>
          ) : tablesError ? (
            <div className="flex flex-col items-center gap-2.5 p-4 text-center">
              <div className="flex items-center justify-center w-8 h-8 rounded-lg bg-danger-bg">
                <AlertCircle size={15} className="text-danger" />
              </div>
              <p className="text-xs text-danger leading-relaxed">{tablesError}</p>
              <button
                onClick={() => setTablesReloadKey((k) => k + 1)}
                className="inline-flex items-center gap-1.5 text-xs font-medium text-primary hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring rounded"
              >
                <RefreshCw size={11} />
                Retry
              </button>
            </div>
          ) : filteredTables.length === 0 ? (
            <p className="text-xs text-muted px-3 py-4 text-center leading-relaxed">
              {tableSearch ? 'No tables match your search.' : 'No tables found.'}
            </p>
          ) : (
            <div className="space-y-0.5">
              {filteredTables.map((t) => (
                <TableItem
                  key={`${t.schema ?? ''}.${t.name}`}
                  name={t.name}
                  active={selectedTable === t.name}
                  onClick={() => handleSelectTable(t.name, t.schema)}
                />
              ))}
            </div>
          )}
        </div>

        {/* Rail footer: connector type badge */}
        <div className="px-3 py-2.5 border-t border-border">
          <Badge variant="default" size="sm" className="font-mono">
            <Database size={9} aria-hidden="true" />
            {connectorType}
          </Badge>
        </div>
      </aside>

      {/* ── Main panel ──────────────────────────────────────────────────── */}
      <main className="flex flex-col flex-1 min-w-0 overflow-hidden md:pt-0 pt-[44px]">
        {!selectedTable ? (
          /* Empty state — no table selected */
          <div className="flex flex-col items-center justify-center h-full p-8">
            <EmptyState
              icon={<Database size={24} />}
              title="Browse your connector data"
              description="Pick a connector and select a table from the left rail to view and edit its data."
              action={tables.length > 0 && (
                <div className="flex flex-wrap gap-2 justify-center max-w-sm">
                  {tables.slice(0, 6).map((t) => (
                    <button
                      key={`${t.schema ?? ''}.${t.name}`}
                      onClick={() => handleSelectTable(t.name, t.schema)}
                      className="px-3 py-1.5 text-xs font-mono rounded-lg border border-border bg-surface-2 hover:border-primary/40 hover:text-primary transition-colors text-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                    >
                      {t.name}
                    </button>
                  ))}
                </div>
              )}
            />
          </div>
        ) : (
          <div className="flex flex-col flex-1 min-h-0 overflow-hidden">
            {/* Tab bar */}
            <div className="shrink-0 flex items-center gap-1 px-4 border-b border-border bg-surface">
              <button
                onClick={() => setMainTab('data')}
                className={[
                  'flex items-center gap-1.5 px-3 py-2.5 text-xs font-medium border-b-2 transition-colors duration-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-inset',
                  mainTab === 'data'
                    ? 'border-primary text-primary'
                    : 'border-transparent text-muted hover:text-fg',
                ].join(' ')}
              >
                <Table2 size={13} />
                Data
              </button>
              <button
                onClick={() => setMainTab('profile')}
                className={[
                  'flex items-center gap-1.5 px-3 py-2.5 text-xs font-medium border-b-2 transition-colors duration-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-inset',
                  mainTab === 'profile'
                    ? 'border-primary text-primary'
                    : 'border-transparent text-muted hover:text-fg',
                ].join(' ')}
                title={!profileDatasetId ? 'No matching dataset found for profiling' : undefined}
              >
                <BarChart2 size={13} />
                Profile
                {!profileDatasetId && (
                  <span className="text-[9px] text-muted/50 font-normal ml-0.5">(no dataset)</span>
                )}
              </button>
            </div>

            {/* Tab content */}
            <div className="flex-1 min-h-0 overflow-hidden">
              {mainTab === 'data' ? (
                <EditableDataGrid
                  key={`${selectedConnectorId ?? 'demo'}:${selectedSchema ?? ''}.${selectedTable}`}
                  datastoreId={selectedConnectorId}
                  table={selectedTable}
                  meta={meta}
                  rows={rows}
                  total={total}
                  loading={dataLoading}
                  error={dataError}
                  onRetry={refreshData}
                  onRefresh={refreshData}
                  onRowsChange={setRows}
                  onTotalChange={handleTotalChange}
                />
              ) : (
                <DatasetProfileView datasetId={profileDatasetId} />
              )}
            </div>
          </div>
        )}
      </main>
    </div>
  )
}
