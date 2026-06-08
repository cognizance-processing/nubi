/**
 * DataBrowser — see a single connector's data (tables + preview rows).
 *
 * Route: /connectors/:id/data
 * Reached from: the Connectors page ("View data" on each connector card).
 *
 * Layout
 * ------
 *   Header  — back link to /connectors + connector name + refresh.
 *   Left    — table list/sidebar (name + row count, searchable).
 *   Right   — preview grid (reuses src/components/DataGrid.jsx) showing the
 *             selected table's columns + first N rows.
 *
 * Data
 * ----
 *   GET /datastores/{id}/tables                      → table list
 *   GET /datastores/{id}/tables/{table}/preview      → columns + rows
 *
 * The first table is auto-selected so opening the Sample connector immediately
 * shows its sales/customers/products data. Loading / empty / error states are
 * handled at both the table-list and preview levels.
 */

import { useState, useEffect, useCallback, useMemo } from 'react'
import { useParams, Link } from 'react-router-dom'
import {
  ArrowLeft,
  Database,
  Table2,
  Search,
  RefreshCw,
  Loader2,
  AlertCircle,
} from 'lucide-react'
import DataGrid from '../../components/DataGrid.jsx'
import * as api from '../../lib/api.js'

const PREVIEW_LIMIT = 50

// ---------------------------------------------------------------------------
// Map a backend (DuckDB / SQL) column type to a DataGrid descriptor type.
// ---------------------------------------------------------------------------

function gridType(rawType) {
  const t = String(rawType || '').toLowerCase()
  if (/(int|decimal|numeric|double|float|real|hugeint)/.test(t)) return 'number'
  if (/(bool)/.test(t)) return 'bool'
  if (/(date|time)/.test(t)) return 'date'
  return 'string'
}

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
      <div className="px-3 py-3 border-b border-border">
        <div className="relative">
          <Search
            size={13}
            className="absolute left-2.5 top-1/2 -translate-y-1/2 text-muted pointer-events-none"
          />
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
                  'w-full flex items-center gap-2 px-2.5 py-2 rounded-lg text-left transition-colors mb-0.5',
                  active
                    ? 'bg-primary/10 text-primary'
                    : 'text-fg hover:bg-surface-2',
                ].join(' ')}
                title={t.schema ? `${t.schema}.${t.name}` : t.name}
              >
                <Table2 size={14} className={active ? 'text-primary shrink-0' : 'text-muted shrink-0'} />
                <span className="flex-1 min-w-0 truncate text-xs font-medium">{t.name}</span>
                {t.rows != null && (
                  <span className="shrink-0 text-[10px] font-mono text-muted tabular-nums">
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
// Preview panel (right) — DataGrid of the selected table
// ---------------------------------------------------------------------------

function PreviewPanel({ table, preview, loading, error }) {
  // Convert the {columns, rows: [[...]]} wire shape into DataGrid's
  // {columns:[{key,label,type}], rows:[{key:value}]} shape.
  const { columns, rows } = useMemo(() => {
    if (!preview) return { columns: [], rows: [] }
    const cols = (preview.columns || []).map((c) => ({
      key: c.name,
      label: c.name,
      type: gridType(c.type),
    }))
    const objRows = (preview.rows || []).map((r) => {
      const obj = {}
      cols.forEach((c, i) => {
        obj[c.key] = r[i]
      })
      return obj
    })
    return { columns: cols, rows: objRows }
  }, [preview])

  if (!table) {
    return (
      <div className="flex-1 flex flex-col items-center justify-center gap-3 text-center p-8">
        <div className="w-12 h-12 rounded-2xl bg-surface-2 border border-border flex items-center justify-center">
          <Table2 size={22} className="text-muted" />
        </div>
        <div>
          <p className="text-sm font-medium text-fg">Select a table</p>
          <p className="text-xs text-muted mt-1">Choose a table to preview its data.</p>
        </div>
      </div>
    )
  }

  return (
    <div className="flex-1 flex flex-col min-w-0 p-4">
      <div className="flex items-center gap-2 mb-3">
        <Table2 size={15} className="text-muted" />
        <h2 className="text-sm font-semibold text-fg font-display">{table}</h2>
        {preview && (
          <span className="text-[11px] text-muted">
            {preview.truncated
              ? `showing first ${preview.rows.length} of ${preview.row_count.toLocaleString()} rows`
              : `${preview.row_count.toLocaleString()} ${preview.row_count === 1 ? 'row' : 'rows'}`}
          </span>
        )}
      </div>

      <div className="flex-1 min-h-0">
        <DataGrid
          columns={columns}
          rows={rows}
          loading={loading}
          error={error}
          pageSize={PREVIEW_LIMIT}
          density="compact"
          exportFileName={table}
          emptyMessage="This table has no rows."
          className="h-full bg-surface"
        />
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
  const [preview, setPreview] = useState(null)
  const [previewLoading, setPreviewLoading] = useState(false)
  const [previewError, setPreviewError] = useState(null)

  // Resolve the connector's display name (best-effort).
  useEffect(() => {
    let cancelled = false
    api
      .listDatastores()
      .then((list) => {
        if (cancelled) return
        const found = list.find((c) => c.id === datastoreId)
        if (found) setConnectorName(found.name)
      })
      .catch(() => {})
    return () => {
      cancelled = true
    }
  }, [datastoreId])

  // Load the table list.
  const loadTables = useCallback(async () => {
    setTablesLoading(true)
    setTablesError(null)
    try {
      const list = await api.listDatastoreTables(datastoreId)
      setTables(list)
      // Auto-select the first table so data shows immediately.
      setSelected((prev) => {
        if (prev && list.some((t) => t.name === prev)) return prev
        return list.length ? list[0].name : null
      })
    } catch (err) {
      setTablesError(err.message ?? 'Failed to load tables')
      setTables([])
    } finally {
      setTablesLoading(false)
    }
  }, [datastoreId])

  useEffect(() => {
    loadTables()
  }, [loadTables])

  // Load the preview for the selected table.
  useEffect(() => {
    if (!selected) {
      setPreview(null)
      return
    }
    let cancelled = false
    setPreviewLoading(true)
    setPreviewError(null)
    api
      .previewDatastoreTable(datastoreId, selected, PREVIEW_LIMIT)
      .then((data) => {
        if (cancelled) return
        setPreview(data)
      })
      .catch((err) => {
        if (cancelled) return
        setPreview(null)
        setPreviewError(err.message ?? 'Failed to load preview')
      })
      .finally(() => {
        if (!cancelled) setPreviewLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [datastoreId, selected])

  const refresh = useCallback(() => {
    loadTables()
    if (selected) {
      setPreviewLoading(true)
      setPreviewError(null)
      api
        .previewDatastoreTable(datastoreId, selected, PREVIEW_LIMIT)
        .then(setPreview)
        .catch((err) => {
          setPreview(null)
          setPreviewError(err.message ?? 'Failed to load preview')
        })
        .finally(() => setPreviewLoading(false))
    }
  }, [loadTables, datastoreId, selected])

  return (
    <div className="flex flex-col h-full min-h-0">
      {/* Header */}
      <div className="flex items-center gap-3 px-6 py-4 border-b border-border bg-surface shrink-0">
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
        <div className="flex-1" />
        <button
          onClick={refresh}
          disabled={tablesLoading}
          title="Refresh"
          className="flex items-center justify-center w-8 h-8 rounded-lg border border-border text-muted hover:text-fg hover:bg-surface-2 disabled:opacity-40 transition-colors"
        >
          {tablesLoading ? (
            <Loader2 size={14} className="animate-spin" />
          ) : (
            <RefreshCw size={14} />
          )}
        </button>
      </div>

      {/* Body: table list + preview */}
      <div className="flex-1 min-h-0 grid grid-cols-[240px_1fr]">
        <TableList
          tables={tables}
          loading={tablesLoading}
          error={tablesError}
          selected={selected}
          onSelect={setSelected}
          onRetry={loadTables}
        />
        <PreviewPanel
          table={selected}
          preview={preview}
          loading={previewLoading}
          error={previewError}
        />
      </div>
    </div>
  )
}
