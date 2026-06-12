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
} from 'lucide-react'
import EditableDataGrid from '../../components/app/EditableDataGrid.jsx'
import { normalizeColumnMeta } from '../../components/app/editableGridUtils.js'
import * as api from '../../lib/api.js'

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
  const [tableSearch, setTableSearch] = useState('')
  const [selectedTable, setSelectedTable] = useState(null)

  // Per-table data + meta (loaded here, passed to EditableDataGrid).
  const [meta, setMeta] = useState(null)
  const [rows, setRows] = useState([])
  const [total, setTotal] = useState(null)
  const [dataLoading, setDataLoading] = useState(false)
  const [dataError, setDataError] = useState(null)
  const [reloadKey, setReloadKey] = useState(0)

  // Mobile rail open/close
  const [railOpen, setRailOpen] = useState(false)

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
  }, [selectedConnectorId])

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
          api.fetchDataColumns(selectedConnectorId, selectedTable).catch(() => ({})),
          api.fetchDataRows(selectedConnectorId, selectedTable, { limit: ROW_LIMIT }),
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
  }, [selectedConnectorId, selectedTable, reloadKey])

  // ── Handlers ──────────────────────────────────────────────────────────────
  const handleSelectTable = useCallback((name) => {
    setSelectedTable(name)
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
                Pick a connector and select a table from the left rail to view and edit its data.
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
        ) : (
          <EditableDataGrid
            key={`${selectedConnectorId ?? 'demo'}:${selectedTable}`}
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
        )}
      </main>
    </div>
  )
}
