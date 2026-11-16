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

import { useState, useEffect, useCallback, useRef, useMemo } from 'react'
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
import Tag from '../../components/ui/Tag.jsx'
import Button from '../../components/ui/Button.jsx'
import Input from '../../components/ui/Input.jsx'
import Skeleton from '../../components/ui/Skeleton.jsx'
import { Tabs, TabsList, TabsTrigger } from '../../components/ui/Tabs.jsx'
import ConnectorLogo from '../../components/app/ConnectorLogo.jsx'
import { getTypeInfo } from '../../data/connectors.js'
import { EmptyData, EmptySearch, ErrorFigure } from '../../components/illustrations'
import '../../components/app/appPatterns.css'

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

function TableItem({ name, active, onClick, indented = false }) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-current={active ? 'true' : undefined}
      className="ap-rail-item"
      style={indented ? { paddingLeft: 28 } : undefined}
    >
      <Table2 size={13} className="shrink-0 text-subtle" aria-hidden="true" />
      <span className="ap-trunc">{name}</span>
    </button>
  )
}

// ---------------------------------------------------------------------------
// Dataset (schema) group header in the table rail
// ---------------------------------------------------------------------------
//
// A connector can expose many datasets — "dataset" here is whatever the engine
// calls the container a table lives in: a MySQL/Postgres schema, a BigQuery
// dataset. Without these headers the rail is a flat list in which a table's
// dataset is invisible and same-named tables in different datasets (three
// `orders` tables, say) are indistinguishable.

function DatasetGroupHeader({ schema, count, collapsed, onToggle }) {
  return (
    <button
      type="button"
      onClick={onToggle}
      aria-expanded={!collapsed}
      className="nubi-focus-inset flex w-full items-center gap-1.5 border-b border-border px-3 py-1.5 text-left text-subtle transition-colors hover:bg-frame-hover hover:text-fg"
    >
      {collapsed
        ? <ChevronRight size={12} className="shrink-0" aria-hidden="true" />
        : <ChevronDown size={12} className="shrink-0" aria-hidden="true" />}
      <span className="t-label truncate" title={schema}>{schema}</span>
      <span className="ap-meta ml-auto shrink-0">{count}</span>
    </button>
  )
}

// ---------------------------------------------------------------------------
// Connector picker (mobile dropdown or desktop label)
// ---------------------------------------------------------------------------

function ConnectorAvatar({ connector }) {
  return <ConnectorLogo info={getTypeInfo(connector?.config?.connector_type)} size={16} />
}

function ConnectorDropdown({ connectors, selectedId, onSelect }) {
  const [open, setOpen] = useState(false)
  const ref = useRef(null)

  useEffect(() => {
    if (!open) return
    const onDown = (e) => {
      if (ref.current && !ref.current.contains(e.target)) setOpen(false)
    }
    const onKey = (e) => { if (e.key === 'Escape') setOpen(false) }
    document.addEventListener('mousedown', onDown)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDown)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])

  const selected = connectors.find((c) => c.id === selectedId) ?? connectors[0]

  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-2 border border-control bg-surface px-2 py-1.5 text-left text-sm font-medium text-fg transition-colors hover:border-ink"
        style={{ borderRadius: 2, borderColor: 'var(--border-control)' }}
        aria-haspopup="listbox"
        aria-expanded={open}
      >
        <ConnectorAvatar connector={selected} />
        <span className="flex-1 truncate">{selected?.name ?? 'Select connector'}</span>
        <ChevronDown size={14} className="shrink-0 text-muted" aria-hidden="true" style={{ transform: open ? 'rotate(180deg)' : 'none' }} />
      </button>
      {open && (
        <div className="nubi-dropdown left-0 right-0 top-[calc(100%+6px)] max-h-72 overflow-y-auto" role="listbox">
          {connectors.map((c) => (
            <button
              type="button"
              key={c.id ?? 'demo'}
              role="option"
              aria-selected={c.id === selectedId}
              onClick={() => { onSelect(c.id); setOpen(false) }}
              className="nubi-dropdown-item"
              style={c.id === selectedId ? { background: 'var(--accent-soft)' } : undefined}
            >
              <ConnectorAvatar connector={c} />
              <span className="min-w-0 flex-1 text-left">
                <span className="block truncate">{c.name}</span>
                {c.config?.connector_type && (
                  <span className="ap-meta block truncate">{c.config.connector_type}</span>
                )}
              </span>
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
  // Datasets the user has collapsed in the rail, keyed by schema name.
  const [collapsedDatasets, setCollapsedDatasets] = useState(() => new Set())

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
  const [datasetsLoaded, setDatasetsLoaded] = useState(false)

  // Load the org datasets list only when the Profile tab is opened (it maps a
  // table to a dataset id). A 404 means this deployment has no dataset-profile
  // service; say so in the tab instead of failing silently on every page load.
  const [profileAvailable, setProfileAvailable] = useState(true)
  useEffect(() => {
    if (mainTab !== 'profile' || datasetsLoaded) return
    let cancelled = false
    api.get('/datasets').then(data => {
      if (cancelled) return
      const list = Array.isArray(data) ? data : (data?.datasets ?? [])
      setDatasets(list)
      setDatasetsLoaded(true)
    }).catch(err => {
      if (cancelled) return
      if (err?.status === 404) setProfileAvailable(false)
      setDatasetsLoaded(true)
    })
    return () => { cancelled = true }
  }, [mainTab, datasetsLoaded])

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
    setCollapsedDatasets(new Set())
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
  // Search matches the dataset name too, so typing "sales" narrows the rail to
  // that dataset — filtering by dataset without a separate control.
  const search = tableSearch.trim().toLowerCase()
  const filteredTables = tables.filter((t) =>
    t.name.toLowerCase().includes(search) ||
    String(t.schema ?? '').toLowerCase().includes(search)
  )

  // ── Group tables by dataset (schema) ──────────────────────────────────────
  // Ordered by table count descending: a connector's primary dataset is the one
  // the user almost always wants, and alphabetical order buries it (an 84-table
  // schema sorting below a 1-table one).
  const tableGroups = useMemo(() => {
    const by = new Map()
    for (const t of filteredTables) {
      const key = String(t.schema ?? '')
      if (!by.has(key)) by.set(key, [])
      by.get(key).push(t)
    }
    return [...by.entries()]
      .map(([schema, items]) => ({ schema, tables: items }))
      .sort((a, b) => b.tables.length - a.tables.length || a.schema.localeCompare(b.schema))
  }, [filteredTables])

  // Only worth showing dataset headers when the CONNECTOR exposes more than one
  // — deliberately computed from the unfiltered list, so headers don't vanish
  // when a search happens to match a single dataset (the result would other-
  // wise not say which dataset it came from). Single-schema connectors (the
  // demo set, a local DuckDB file) stay a plain flat list.
  const showDatasetGroups = useMemo(
    () => new Set(tables.map((t) => String(t.schema ?? ''))).size > 1,
    [tables],
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
    <div className="relative flex h-full overflow-hidden bg-surface">
      <h1 className="sr-only">Data</h1>

      {/* ── Mobile rail toggle ──────────────────────────────────────────── */}
      <div className="absolute left-0 right-0 top-0 z-30 flex shrink-0 items-center gap-2 border-b border-border bg-surface px-3 py-2 md:hidden">
        <Button
          variant="secondary"
          size="sm"
          onClick={() => setRailOpen((v) => !v)}
          aria-expanded={railOpen}
          aria-label="Toggle connector and table list"
        >
          <SlidersHorizontal size={14} className="shrink-0" aria-hidden="true" />
          {selectedTable ? (
            <span className="max-w-[50vw] truncate font-mono">
              {selectedSchema && <span className="text-muted">{selectedSchema}.</span>}
              {selectedTable}
            </span>
          ) : (
            <span className="text-muted">Select table</span>
          )}
          <ChevronDown size={13} className="shrink-0 text-muted" aria-hidden="true" style={{ transform: railOpen ? 'rotate(180deg)' : 'none' }} />
        </Button>
      </div>

      {/* ── Mobile rail overlay ──────────────────────────────────────────── */}
      {railOpen && <div className="ap-backdrop md:hidden" onClick={() => setRailOpen(false)} aria-hidden="true" />}

      {/* ── Left rail ───────────────────────────────────────────────────── */}
      <aside
        className={[
          'flex shrink-0 flex-col border-r border-frame-line bg-surface',
          // Desktop: always visible, fixed width
          'md:relative md:flex md:w-[240px]',
          // Mobile: absolute overlay drawer
          'fixed inset-y-0 left-0 w-[280px]',
          railOpen ? 'translate-x-0' : '-translate-x-full md:translate-x-0',
        ].join(' ')}
        style={{ zIndex: railOpen ? 'var(--z-modal)' as any : undefined, transition: 'transform var(--dur-3) var(--ease-out)' }}
      >
        {/* Connector picker */}
        <div className="border-b border-border p-3" style={{ borderTop: '2px solid var(--ink)' }}>
          <p className="t-label mb-2">Connector</p>
          <ConnectorDropdown
            connectors={connectors}
            selectedId={selectedConnectorId}
            onSelect={handleSelectConnector}
          />
        </div>

        {/* Table search */}
        <div className="border-b border-border px-3 py-2">
          <Input
            type="search"
            size="sm"
            icon={<Search size={14} />}
            placeholder="Search tables"
            value={tableSearch}
            onChange={(e) => setTableSearch(e.target.value)}
            aria-label="Search tables or datasets"
          />
        </div>

        {/* Table list */}
        <div className="flex-1 overflow-y-auto py-1">
          {tablesLoading ? (
            <div className="space-y-1 px-3 py-2" aria-hidden="true">
              {Array.from({ length: 5 }).map((_, i) => <Skeleton key={i} className="h-6 w-full" />)}
            </div>
          ) : tablesError ? (
            <EmptyState
              compact
              kind="error"
              art={<ErrorFigure />}
              title="Tables didn’t load"
              description={tablesError}
              action={
                <Button variant="secondary" size="sm" onClick={() => setTablesReloadKey((k) => k + 1)}>
                  <RefreshCw size={14} aria-hidden="true" />
                  Retry
                </Button>
              }
            />
          ) : filteredTables.length === 0 ? (
            tableSearch ? (
              <EmptyState
                compact
                kind="none-match"
                art={<EmptySearch />}
                title={`No table matches “${tableSearch}”`}
                description="Search covers table and dataset names."
                action={<Button variant="secondary" size="sm" onClick={() => setTableSearch('')}>Clear search</Button>}
              />
            ) : (
              <EmptyState
                compact
                art={<EmptyData />}
                title="No tables here"
                description="This connector has no tables the current role can read."
              />
            )
          ) : (
            <div>
              {tableGroups.map(({ schema, tables: groupTables }) => {
                // While searching, always reveal matches — a collapsed dataset
                // must not hide a table the user just searched for.
                const collapsed = showDatasetGroups && !search && collapsedDatasets.has(schema)
                return (
                  <div key={schema || '__default__'}>
                    {showDatasetGroups && (
                      <DatasetGroupHeader
                        schema={schema || 'default'}
                        count={groupTables.length}
                        collapsed={collapsed}
                        onToggle={() => setCollapsedDatasets((prev) => {
                          const next = new Set(prev)
                          if (next.has(schema)) next.delete(schema)
                          else next.add(schema)
                          return next
                        })}
                      />
                    )}
                    {!collapsed && groupTables.map((t) => (
                      <TableItem
                        key={`${t.schema ?? ''}.${t.name}`}
                        name={t.name}
                        indented={showDatasetGroups}
                        active={selectedTable === t.name && (selectedSchema ?? null) === (t.schema ?? null)}
                        onClick={() => handleSelectTable(t.name, t.schema)}
                      />
                    ))}
                  </div>
                )
              })}
            </div>
          )}
        </div>

        {/* Rail footer: engine tag + table count */}
        <div className="flex items-center justify-between border-t border-border px-3 py-2.5">
          <Tag caps={false}>
            <Database size={10} aria-hidden="true" />
            {connectorType}
          </Tag>
          {!tablesLoading && !tablesError && <span className="ap-count">{tables.length} tables</span>}
        </div>
      </aside>

      {/* ── Main panel ──────────────────────────────────────────────────── */}
      <main className="flex min-w-0 flex-1 flex-col overflow-hidden pt-[52px] md:pt-0">
        {!selectedTable ? (
          /* Empty state — no table selected */
          <div className="flex h-full flex-col items-center justify-center p-8">
            <EmptyState
              art={<EmptyData />}
              title="Pick a table"
              description="Choose a connector and a table in the left rail to browse its rows. Where the table has a primary key, cells are editable."
              action={tables.length > 0 ? (
                <div className="flex max-w-sm flex-wrap justify-center gap-2">
                  {tables.slice(0, 6).map((t) => (
                    <Tag
                      as="button"
                      type="button"
                      size="md"
                      caps={false}
                      key={`${t.schema ?? ''}.${t.name}`}
                      onClick={() => handleSelectTable(t.name, t.schema)}
                      className="font-mono"
                    >
                      {t.name}
                    </Tag>
                  ))}
                </div>
              ) : undefined}
            />
          </div>
        ) : (
          <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
            {/* Tab bar */}
            <div className="shrink-0 border-b border-border bg-surface px-4">
              <Tabs value={mainTab} onValueChange={setMainTab}>
                <TabsList aria-label="Table views" className="border-b-0">
                  <TabsTrigger value="data"><Table2 size={14} aria-hidden="true" /> Data</TabsTrigger>
                  <TabsTrigger value="profile" title={!profileDatasetId ? 'No matching dataset found for profiling' : undefined}>
                    <BarChart2 size={14} aria-hidden="true" /> Profile
                    {!profileDatasetId && <span className="ap-meta ml-1">no dataset</span>}
                  </TabsTrigger>
                </TabsList>
              </Tabs>
            </div>

            {/* Tab content */}
            <div className="min-h-0 flex-1 overflow-hidden">
              {mainTab === 'data' ? (
                <EditableDataGrid
                  key={`${selectedConnectorId ?? 'demo'}:${selectedSchema ?? ''}.${selectedTable}`}
                  datastoreId={selectedConnectorId}
                  table={selectedTable}
                  schema={selectedSchema}
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
                <DatasetProfileView datasetId={profileDatasetId} unavailable={!profileAvailable} />
              )}
            </div>
          </div>
        )}
      </main>
    </div>
  )
}
