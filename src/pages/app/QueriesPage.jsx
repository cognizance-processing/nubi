/**
 * QueriesPage — full SQL IDE for Nubi.
 *
 * Layout (desktop):
 *   ┌─────────────────────────────────────────────────────────┐
 *   │  Left rail (240px)  │  QueryWorkspace (flex-1)          │
 *   │  - search           │  - toolbar (run / save / AI)      │
 *   │  - new query btn    │  - param inputs (if registered)   │
 *   │  - query list       │  - Monaco SQL editor (resizable)  │
 *   │    (registered +    │  - results DataTable              │
 *   │     local drafts)   │                                   │
 *   └─────────────────────────────────────────────────────────┘
 *
 * Layout (mobile):
 *   - Left rail collapses into a dropdown selector at the top.
 *   - Editor + results stack vertically.
 *
 * Registered queries come from listRegisteredQueries() (GET /query/registry).
 * "New query" creates an in-memory draft with id=null (ad-hoc).
 */

import { useState, useEffect, useCallback, useRef } from 'react'
import { Link } from 'react-router-dom'
import {
  FileCode2,
  Plus,
  Search,
  RefreshCw,
  ChevronDown,
  ChevronRight,
  Tag,
  Loader2,
  AlertCircle,
  Database,
  List,
  Combine,
} from 'lucide-react'

import { listRegisteredQueries } from '../../lib/api.js'
import { useProject } from '../../contexts/ProjectContext.jsx'
import QueryWorkspace from './QueryWorkspace.jsx'

// ---------------------------------------------------------------------------
// New ad-hoc query template
// ---------------------------------------------------------------------------

function newAdHocQuery() {
  return {
    id: null,
    name: 'New query',
    sql: '-- Write your SQL here\nSELECT * FROM demo LIMIT 100',
    params: [],
    isNew: true,
    _localId: `adhoc-${Date.now()}`,
  }
}

// ---------------------------------------------------------------------------
// QueryListItem — single entry in the left rail
// ---------------------------------------------------------------------------

function QueryListItem({ query, isActive, onClick }) {
  const hasParams = Array.isArray(query.params) && query.params.length > 0

  return (
    <button
      onClick={() => onClick(query)}
      className={[
        'w-full text-left px-3 py-2.5 rounded-lg transition-all group',
        isActive
          ? 'bg-primary/10 border border-primary/20 text-fg'
          : 'hover:bg-surface-2 border border-transparent text-fg/80 hover:text-fg',
      ].join(' ')}
    >
      <div className="flex items-start gap-2 min-w-0">
        <FileCode2
          size={13}
          className={[
            'shrink-0 mt-0.5',
            isActive ? 'text-primary' : 'text-muted group-hover:text-fg/60',
          ].join(' ')}
        />
        <div className="min-w-0 flex-1">
          <p className="text-xs font-medium truncate leading-tight">
            {query.name ?? query.id}
          </p>
          {query.id && (
            <p className="text-[10px] font-mono text-muted truncate mt-0.5">
              {query.id}
            </p>
          )}
          {hasParams && (
            <div className="flex flex-wrap gap-1 mt-1">
              {query.params.slice(0, 3).map(p => (
                <span
                  key={p.name}
                  className="inline-flex items-center gap-0.5 px-1 py-0 rounded text-[9px] font-mono bg-surface-2 text-muted border border-border/60"
                >
                  <Tag size={7} />
                  {p.name}
                </span>
              ))}
              {query.params.length > 3 && (
                <span className="text-[9px] text-muted">+{query.params.length - 3}</span>
              )}
            </div>
          )}
          {query.isNew && (
            <span className="inline-flex items-center px-1 py-0.5 text-[9px] font-medium rounded bg-amber-500/10 text-amber-600 dark:text-amber-400 mt-1">
              draft
            </span>
          )}
        </div>
      </div>
    </button>
  )
}

// ---------------------------------------------------------------------------
// LeftRail
// ---------------------------------------------------------------------------

function LeftRail({ queries, localQueries, activeId, loading, onSelect, onNewQuery, onRefresh, searchQuery, onSearchChange }) {
  const allItems = [
    ...localQueries,
    ...queries,
  ]

  const filtered = allItems.filter(q =>
    !searchQuery ||
    (q.name ?? q.id ?? '').toLowerCase().includes(searchQuery.toLowerCase()) ||
    (q.id ?? '').toLowerCase().includes(searchQuery.toLowerCase())
  )

  const registeredFiltered = filtered.filter(q => !q.isNew && !q._localId?.startsWith('adhoc'))
  const draftFiltered = filtered.filter(q => q.isNew || q._localId?.startsWith('adhoc'))

  return (
    <aside className="flex flex-col h-full border-r border-border bg-surface-2/40">
      {/* Rail header */}
      <div className="shrink-0 flex items-center justify-between px-3 py-2.5 border-b border-border">
        <span className="text-xs font-semibold text-muted uppercase tracking-wider">Queries</span>
        <div className="flex items-center gap-1">
          <button
            onClick={onRefresh}
            disabled={loading}
            className="h-6 w-6 flex items-center justify-center rounded text-muted hover:text-fg hover:bg-surface-2 disabled:opacity-40 transition-colors"
            title="Refresh query registry"
          >
            <RefreshCw size={12} className={loading ? 'animate-spin' : ''} />
          </button>
        </div>
      </div>

      {/* New query / Blend buttons */}
      <div className="shrink-0 px-2 py-2 space-y-1.5">
        <button
          onClick={onNewQuery}
          className="w-full h-8 flex items-center justify-center gap-1.5 text-xs font-medium rounded-lg border border-dashed border-border text-muted hover:text-fg hover:border-border hover:bg-surface-2 transition-colors"
        >
          <Plus size={13} />
          New query
        </button>
        <Link
          to="/queries/blend"
          className="w-full h-8 flex items-center justify-center gap-1.5 text-xs font-medium rounded-lg border border-dashed border-border text-muted hover:text-fg hover:border-border hover:bg-surface-2 transition-colors"
        >
          <Combine size={13} className="text-primary/70" />
          Blend sources
        </Link>
      </div>

      {/* Search */}
      <div className="shrink-0 px-2 pb-2">
        <div className="relative">
          <Search size={11} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-muted pointer-events-none" />
          <input
            type="text"
            value={searchQuery}
            onChange={e => onSearchChange(e.target.value)}
            placeholder="Search queries…"
            className="w-full h-7 pl-7 pr-2.5 text-[11px] bg-surface border border-border rounded-lg text-fg placeholder:text-muted/50 focus:outline-none focus:ring-1 focus:ring-ring"
          />
        </div>
      </div>

      {/* Query list */}
      <div className="flex-1 overflow-y-auto px-2 pb-2 space-y-0.5">
        {loading && allItems.length === 0 && (
          <div className="flex items-center gap-2 text-[11px] text-muted py-4 justify-center">
            <Loader2 size={12} className="animate-spin" />
            Loading…
          </div>
        )}

        {!loading && allItems.length === 0 && (
          <div className="text-[11px] text-muted text-center py-6">
            <Database size={20} className="mx-auto mb-2 opacity-30" />
            No registered queries
          </div>
        )}

        {/* Draft queries */}
        {draftFiltered.length > 0 && (
          <div>
            <p className="text-[9px] font-semibold text-muted/60 uppercase tracking-wider px-1 py-1.5">Drafts</p>
            {draftFiltered.map(q => (
              <QueryListItem
                key={q._localId ?? q.id}
                query={q}
                isActive={activeId === (q._localId ?? q.id)}
                onClick={() => onSelect(q)}
              />
            ))}
          </div>
        )}

        {/* Registered queries */}
        {registeredFiltered.length > 0 && (
          <div>
            <p className="text-[9px] font-semibold text-muted/60 uppercase tracking-wider px-1 py-1.5 mt-1">
              Registry
            </p>
            {registeredFiltered.map(q => (
              <QueryListItem
                key={q.id}
                query={q}
                isActive={activeId === q.id}
                onClick={() => onSelect(q)}
              />
            ))}
          </div>
        )}

        {/* No results for search */}
        {searchQuery && filtered.length === 0 && (
          <p className="text-[11px] text-muted text-center py-4">
            No queries match "{searchQuery}"
          </p>
        )}
      </div>
    </aside>
  )
}

// ---------------------------------------------------------------------------
// MobileQueryDropdown — compact selector for small screens
// ---------------------------------------------------------------------------

function MobileQueryDropdown({ queries, localQueries, activeQuery, onSelect, onNewQuery, loading }) {
  const allItems = [...localQueries, ...queries]
  const [open, setOpen] = useState(false)
  const ref = useRef(null)

  useEffect(() => {
    if (!open) return
    const handler = (e) => {
      if (ref.current && !ref.current.contains(e.target)) setOpen(false)
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [open])

  return (
    <div className="relative" ref={ref}>
      <button
        onClick={() => setOpen(o => !o)}
        className="flex items-center gap-2 px-3 h-9 text-sm font-medium text-fg bg-surface border border-border rounded-lg hover:bg-surface-2 transition-colors"
      >
        <List size={14} />
        <span className="truncate max-w-[160px]">
          {activeQuery?.name ?? activeQuery?.id ?? 'Select query'}
        </span>
        <ChevronDown size={13} className="text-muted shrink-0" />
      </button>

      {open && (
        <div className="absolute top-full mt-1 left-0 z-50 w-64 bg-surface border border-border rounded-xl shadow-xl overflow-hidden">
          <div className="p-1.5 border-b border-border">
            <button
              onClick={() => { onNewQuery(); setOpen(false) }}
              className="w-full flex items-center gap-2 px-3 py-2 text-xs font-medium text-fg hover:bg-surface-2 rounded-lg transition-colors"
            >
              <Plus size={12} />
              New query
            </button>
            <Link
              to="/queries/blend"
              onClick={() => setOpen(false)}
              className="w-full flex items-center gap-2 px-3 py-2 text-xs font-medium text-fg hover:bg-surface-2 rounded-lg transition-colors"
            >
              <Combine size={12} className="text-primary/70" />
              Blend sources
            </Link>
          </div>
          <div className="max-h-64 overflow-y-auto p-1.5 space-y-0.5">
            {loading && (
              <div className="text-[11px] text-muted text-center py-3">Loading…</div>
            )}
            {allItems.map(q => (
              <button
                key={q._localId ?? q.id}
                onClick={() => { onSelect(q); setOpen(false) }}
                className={[
                  'w-full text-left px-3 py-2 text-xs rounded-lg transition-colors',
                  (activeQuery?._localId ?? activeQuery?.id) === (q._localId ?? q.id)
                    ? 'bg-primary/10 text-fg'
                    : 'text-fg/80 hover:bg-surface-2 hover:text-fg',
                ].join(' ')}
              >
                <span className="font-medium">{q.name ?? q.id}</span>
                {q.isNew && (
                  <span className="ml-1.5 text-[10px] text-amber-600 dark:text-amber-400">draft</span>
                )}
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// QueriesPage
// ---------------------------------------------------------------------------

export default function QueriesPage() {
  // Re-scope the registry whenever the active project changes (api.js sends X-Project-Id).
  const { activeProject } = useProject()
  const projectId = activeProject?.id

  // ── Registry ───────────────────────────────────────────────────────────
  const [registeredQueries, setRegisteredQueries] = useState([])
  const [loadingRegistry, setLoadingRegistry] = useState(true)
  const [registryError, setRegistryError] = useState(null)

  // ── Local drafts (in-memory; not persisted) ───────────────────────────
  const [localQueries, setLocalQueries] = useState(() => [newAdHocQuery()])

  // ── Active query ───────────────────────────────────────────────────────
  const [activeQuery, setActiveQuery] = useState(null)

  // ── Rail search ────────────────────────────────────────────────────────
  const [railSearch, setRailSearch] = useState('')

  // ── Load registry ──────────────────────────────────────────────────────
  const loadRegistry = useCallback(async () => {
    setLoadingRegistry(true)
    setRegistryError(null)
    try {
      const data = await listRegisteredQueries()
      setRegisteredQueries(data)

      // Auto-select first local draft (or first registered if no drafts yet)
      setActiveQuery(prev => {
        if (prev) return prev // keep selection
        if (localQueries.length > 0) return localQueries[0]
        if (data.length > 0) return data[0]
        return null
      })
    } catch (err) {
      setRegistryError(err?.message ?? 'Failed to load registry')
      setRegisteredQueries([])
    } finally {
      setLoadingRegistry(false)
    }
  }, [localQueries])

  useEffect(() => {
    loadRegistry()
  }, [projectId]) // eslint-disable-line react-hooks/exhaustive-deps

  // Init active selection after first load
  useEffect(() => {
    if (activeQuery) return
    if (localQueries.length > 0) {
      setActiveQuery(localQueries[0])
    }
  }, [localQueries, activeQuery])

  // ── Actions ────────────────────────────────────────────────────────────

  const handleSelectQuery = useCallback((q) => {
    setActiveQuery(q)
  }, [])

  const handleNewQuery = useCallback(() => {
    const draft = newAdHocQuery()
    setLocalQueries(prev => [draft, ...prev])
    setActiveQuery(draft)
  }, [])

  const handleQueryChange = useCallback((updatedQuery) => {
    // Propagate SQL edits back into local drafts
    if (updatedQuery.isNew || updatedQuery._localId) {
      setLocalQueries(prev =>
        prev.map(q =>
          (q._localId ?? q.id) === (updatedQuery._localId ?? updatedQuery.id)
            ? updatedQuery
            : q
        )
      )
    }
    setActiveQuery(updatedQuery)
  }, [])

  const handleSaved = useCallback((savedQuery) => {
    // After saving, the query has been registered in the backend QueryRegistry.
    // Move it from localQueries (drafts) into registeredQueries and set it active.
    const upgraded = { ...savedQuery, isNew: false }

    // Remove from local drafts (matched by _localId or old id)
    setLocalQueries(prev =>
      prev.filter(q =>
        (q._localId ?? q.id) !== (savedQuery._localId ?? savedQuery.id)
      )
    )

    // Add/update in the registered list (upsert by id)
    setRegisteredQueries(prev => {
      const idx = prev.findIndex(q => q.id === upgraded.id)
      if (idx >= 0) {
        const next = [...prev]
        next[idx] = upgraded
        return next
      }
      return [upgraded, ...prev]
    })

    setActiveQuery(upgraded)
  }, [])

  // ── Derived ────────────────────────────────────────────────────────────
  const activeId = activeQuery?._localId ?? activeQuery?.id

  return (
    <div className="flex h-[calc(100vh-var(--shell-header-h,56px))] overflow-hidden bg-bg">

      {/* ── Desktop left rail (lg+ only) ──────────────────────────────── */}
      <div className="hidden lg:flex shrink-0 w-56 xl:w-64 flex-col">
        <LeftRail
          queries={registeredQueries}
          localQueries={localQueries}
          activeId={activeId}
          loading={loadingRegistry}
          onSelect={handleSelectQuery}
          onNewQuery={handleNewQuery}
          onRefresh={loadRegistry}
          searchQuery={railSearch}
          onSearchChange={setRailSearch}
        />
      </div>

      {/* ── Main content ───────────────────────────────────────────────── */}
      <div className="flex-1 flex flex-col min-w-0 overflow-hidden">

        {/* Mobile/tablet top bar (shown below lg) */}
        <div className="lg:hidden shrink-0 flex items-center gap-2 px-3 py-2 border-b border-border bg-surface-2/40">
          <MobileQueryDropdown
            queries={registeredQueries}
            localQueries={localQueries}
            activeQuery={activeQuery}
            onSelect={handleSelectQuery}
            onNewQuery={handleNewQuery}
            loading={loadingRegistry}
          />
          <div className="flex-1" />
        </div>

        {/* Registry error banner */}
        {registryError && (
          <div className="shrink-0 flex items-center gap-2 px-4 py-2 bg-rose-500/5 border-b border-rose-500/20 text-xs text-rose-600 dark:text-rose-400">
            <AlertCircle size={12} />
            Registry unavailable: {registryError}. Ad-hoc queries still work.
          </div>
        )}

        {/* Workspace */}
        {activeQuery ? (
          <div className="flex-1 min-h-0 overflow-hidden">
            <QueryWorkspace
              key={activeId}
              query={activeQuery}
              onQueryChange={handleQueryChange}
              onSaved={handleSaved}
              isNew={Boolean(activeQuery.isNew)}
            />
          </div>
        ) : (
          /* Empty state */
          <div className="flex-1 flex flex-col items-center justify-center gap-4 text-center px-6">
            <div
              className="flex items-center justify-center w-14 h-14 rounded-2xl"
              style={{ background: 'linear-gradient(135deg, #1b2363, #2456a6, #17b3a3)' }}
            >
              <FileCode2 size={24} className="text-white" />
            </div>
            <div>
              <h2 className="text-lg font-semibold font-display text-fg mb-1">SQL editor</h2>
              <p className="text-sm text-muted max-w-xs">
                Select a query from the sidebar (desktop) or the dropdown above (mobile/tablet), or create a new one to get started.
              </p>
            </div>
            <button
              onClick={handleNewQuery}
              className="flex items-center gap-2 px-4 py-2 text-sm font-medium bg-primary text-primary-fg rounded-lg hover:opacity-90 transition-opacity"
            >
              <Plus size={15} />
              New query
            </button>
          </div>
        )}
      </div>
    </div>
  )
}
