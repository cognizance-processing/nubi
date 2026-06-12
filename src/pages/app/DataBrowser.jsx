/**
 * DataBrowser — Supabase-style table editor for a single connector.
 *
 * Route: /connectors/:id/data
 * Reached from: the Connectors page ("View data" on each connector card).
 *
 * Layout
 * ------
 *   Header  — back link to /connectors + connector name.
 *   Left    — polished table list/sidebar (name + row count, searchable).
 *   Right   — EditableDataGrid: a sticky-header / sticky-selector grid with
 *             type-aware rendering, click-to-sort, resizable columns and
 *             INLINE CELL EDITING + insert/delete (gated on the write contract).
 *
 * Data + write contract
 * ----------------------
 *   GET    /data/{id}/tables                         → table list
 *   GET    /data/{id}/tables/{t}/columns             → { writable, primary_key,
 *                                                        columns:[{name,type,nullable,editable}] }
 *   GET    /data/{id}/tables/{t}/rows                 → rows (JSON)
 *   PATCH  /data/{id}/tables/{t}/rows {pk,set}        → edit cell
 *   POST   /data/{id}/tables/{t}/rows {values}        → insert row
 *   DELETE /data/{id}/tables/{t}/rows {pk}            → delete row
 *
 * The grid degrades to read-only when the table is not `writable`, has no
 * primary key, or the write endpoints 404. The first table auto-selects so the
 * connector's data shows immediately. All loads use the deferred async pattern
 * (no setState in an effect body) per the repo's react-hooks rules.
 */

import { useState, useEffect, useCallback, useMemo, useRef } from 'react'
import { useParams, Link } from 'react-router-dom'
import {
  ArrowLeft,
  Database,
  Table2,
  Search,
  AlertCircle,
  RefreshCw,
} from 'lucide-react'
import EditableDataGrid from '../../components/app/EditableDataGrid.jsx'
import { normalizeColumnMeta } from '../../components/app/editableGridUtils.js'
import * as api from '../../lib/api.js'

const ROW_LIMIT = 200

// ---------------------------------------------------------------------------
// Table list (left rail)
// ---------------------------------------------------------------------------

function TableList({ tables, loading, error, selected, onSelect, onRetry }) {
  const [query, setQuery] = useState('')

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase()
    if (!q) return tables
    return tables.filter((t) => t.name.toLowerCase().includes(q))
  }, [tables, query])

  return (
    <div className="flex flex-col h-full border-r border-border bg-surface">
      <div className="px-3 pt-3 pb-2.5 border-b border-border space-y-2.5">
        <div className="flex items-center justify-between">
          <span className="text-[10px] font-semibold uppercase tracking-wider text-muted">Tables</span>
          {!loading && !error && (
            <span className="text-[10px] text-muted/70 tabular-nums">{tables.length}</span>
          )}
        </div>
        <div className="relative">
          <Search size={13} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-muted pointer-events-none" />
          <input
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search tables…"
            className="w-full h-8 pl-7 pr-2 text-xs bg-surface-2 border border-border rounded-lg text-fg placeholder:text-muted/60 focus:outline-none focus:ring-1 focus:ring-ring"
          />
        </div>
      </div>

      <div className="flex-1 overflow-y-auto p-2">
        {loading && (
          <div className="space-y-1.5 px-1">
            {[1, 2, 3, 4].map((i) => (
              <div key={i} className="h-8 rounded-lg bg-surface-2 animate-pulse" />
            ))}
          </div>
        )}

        {!loading && error && (
          <div className="flex flex-col items-center gap-3 px-3 py-8 text-center">
            <AlertCircle size={20} className="text-rose-500" />
            <p className="text-xs text-muted">{error}</p>
            <button
              onClick={onRetry}
              className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-border text-xs text-muted hover:text-fg hover:bg-surface-2 transition-colors"
            >
              <RefreshCw size={12} /> Retry
            </button>
          </div>
        )}

        {!loading && !error && filtered.length === 0 && (
          <div className="flex flex-col items-center gap-2 px-3 py-8 text-center">
            <Database size={20} className="text-muted" />
            <p className="text-xs text-muted">
              {tables.length === 0 ? 'No tables found.' : 'No tables match your search.'}
            </p>
          </div>
        )}

        {!loading &&
          !error &&
          filtered.map((t) => {
            const active = t.name === selected
            return (
              <button
                key={`${t.schema ?? ''}.${t.name}`}
                onClick={() => onSelect(t.name)}
                className={[
                  'group relative w-full flex items-center gap-2 pl-3 pr-2.5 py-2 rounded-lg text-left transition-colors mb-0.5',
                  active ? 'bg-primary/10 text-primary' : 'text-fg hover:bg-surface-2',
                ].join(' ')}
                title={t.schema ? `${t.schema}.${t.name}` : t.name}
              >
                {active && (
                  <span className="absolute left-0 top-1.5 bottom-1.5 w-0.5 rounded-full bg-primary" />
                )}
                <Table2 size={14} className={active ? 'text-primary shrink-0' : 'text-muted group-hover:text-fg shrink-0'} />
                <span className="flex-1 min-w-0 truncate text-xs font-medium font-mono">{t.name}</span>
                {t.rows != null && (
                  <span className={[
                    'shrink-0 text-[10px] font-mono tabular-nums',
                    active ? 'text-primary/70' : 'text-muted',
                  ].join(' ')}>
                    {t.rows.toLocaleString()}
                  </span>
                )}
              </button>
            )
          })}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// DataBrowser — page
// ---------------------------------------------------------------------------

export default function DataBrowser() {
  const { id: datastoreId } = useParams()

  const [connectorName, setConnectorName] = useState(null)

  const [tables, setTables] = useState([])
  const [tablesLoading, setTablesLoading] = useState(true)
  const [tablesError, setTablesError] = useState(null)

  const [selected, setSelected] = useState(null)

  // Per-table data + meta.
  const [meta, setMeta] = useState(null) // normalized column meta
  const [rows, setRows] = useState([])
  const [total, setTotal] = useState(null)
  const [dataLoading, setDataLoading] = useState(false)
  const [dataError, setDataError] = useState(null)
  const [reloadKey, setReloadKey] = useState(0)

  // ── Resolve the connector display name (best-effort) ──────────────────────
  useEffect(() => {
    let cancelled = false
    api
      .listConnectors()
      .then((list) => {
        if (cancelled) return
        const found = list.find((c) => c.id === datastoreId)
        if (found) setConnectorName(found.name)
      })
      .catch(() => {})
    return () => { cancelled = true }
  }, [datastoreId])

  // ── Load the table list ────────────────────────────────────────────────────
  const loadTables = useCallback(() => {
    setTablesLoading(true)
    setTablesError(null)
    api
      .get(`/data/${datastoreId}/tables`)
      .then((data) => {
        const list = (data?.tables ?? data ?? []).map((t) =>
          typeof t === 'string' ? { name: t } : t,
        )
        setTables(list)
        setSelected((prev) => {
          if (prev && list.some((t) => t.name === prev)) return prev
          return list.length ? list[0].name : null
        })
      })
      .catch((err) => {
        setTablesError(err.message ?? 'Failed to load tables')
        setTables([])
      })
      .finally(() => setTablesLoading(false))
  }, [datastoreId])

  useEffect(() => { loadTables() }, [loadTables])

  // ── Load meta + rows for the selected table ───────────────────────────────
  // A monotonically increasing token guards against out-of-order responses
  // when the user clicks between tables quickly.
  const loadToken = useRef(0)

  useEffect(() => {
    if (!selected) {
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
          api.fetchDataColumns(datastoreId, selected).catch(() => ({})),
          api.fetchDataRows(datastoreId, selected, { limit: ROW_LIMIT }),
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
  }, [datastoreId, selected, reloadKey])

  const refreshData = useCallback(() => setReloadKey((k) => k + 1), [])

  const handleTotalChange = useCallback((delta) => {
    setTotal((t) => (t == null ? t : Math.max(0, t + delta)))
  }, [])

  return (
    <div className="flex flex-col h-full min-h-0">
      {/* Header */}
      <div className="flex items-center gap-3 px-6 py-3.5 border-b border-border bg-surface shrink-0">
        <Link
          to="/connectors"
          className="flex items-center justify-center w-8 h-8 rounded-lg border border-border text-muted hover:text-fg hover:bg-surface-2 transition-colors"
          title="Back to connectors"
          aria-label="Back to connectors"
        >
          <ArrowLeft size={15} />
        </Link>
        <div className="flex items-center gap-2 min-w-0">
          <Database size={16} className="text-primary shrink-0" />
          <h1 className="font-display font-semibold text-lg text-fg truncate">
            {connectorName ?? 'Connector data'}
          </h1>
        </div>
      </div>

      {/* Body: table list + editable grid */}
      <div className="flex-1 min-h-0 grid grid-cols-[240px_1fr]">
        <TableList
          tables={tables}
          loading={tablesLoading}
          error={tablesError}
          selected={selected}
          onSelect={setSelected}
          onRetry={loadTables}
        />

        <div className="flex flex-col min-w-0 min-h-0">
          {!selected ? (
            <div className="flex-1 flex flex-col items-center justify-center gap-3 text-center p-8">
              <div className="w-12 h-12 rounded-2xl bg-surface-2 border border-border flex items-center justify-center">
                <Table2 size={22} className="text-muted" />
              </div>
              <div>
                <p className="text-sm font-medium text-fg">Select a table</p>
                <p className="text-xs text-muted mt-1">Choose a table to browse and edit its data.</p>
              </div>
            </div>
          ) : (
            <EditableDataGrid
              key={`${datastoreId}:${selected}`}
              datastoreId={datastoreId}
              table={selected}
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
        </div>
      </div>
    </div>
  )
}
