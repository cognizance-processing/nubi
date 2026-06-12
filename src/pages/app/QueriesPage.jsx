/**
 * QueriesPage — full SQL IDE for Nubi.
 *
 * Layout (desktop) — mirrors the DashboardEditor right-sidebar pattern:
 *   ┌─────────────────────────────────────────────────────────┐
 *   │  QueryWorkspace (flex-1)          │  Right sidebar      │
 *   │  - toolbar (run / save / view     │  - "Queries" panel: │
 *   │    toggle / panel buttons)        │    search, new      │
 *   │  - Monaco SQL editor (resizable)  │    query, drafts +  │
 *   │  - results DataTable              │    registry list    │
 *   └─────────────────────────────────────────────────────────┘
 *
 * Topbar buttons (dashboard-style icon toggles):
 *   - Queries — this page's query-list sidebar (lg+: static 288px panel;
 *     md–lg: slide-over drawer; <md: hidden, mobile dropdown instead)
 * plus an Editor ↔ Rollups segmented view toggle. Chat is opened with the
 * shell's single global chat button (far right of the topbar) — this page
 * intentionally has NO chat button of its own to avoid duplicates; it only
 * reacts to chatOpen so the Queries panel and chat share the right edge.
 *
 * Layout (mobile):
 *   - Query list collapses into a dropdown selector at the top.
 *   - Editor + results stack vertically.
 *
 * Registered queries come from listRegisteredQueries() (GET /query/registry).
 * "New query" creates an in-memory draft with id=null (ad-hoc).
 */

import { useState, useEffect, useCallback, useRef } from 'react'
import { createPortal } from 'react-dom'
import { Link } from 'react-router-dom'
import {
  FileCode2,
  Plus,
  Search,
  RefreshCw,
  CheckCircle2,
  CheckSquare,
  ChevronDown,
  ChevronRight,
  Tag,
  ListChecks,
  Loader2,
  AlertCircle,
  Database,
  List,
  Combine,
  Boxes,
  PanelRightClose,
  History,
  Square,
  Trash2,
} from 'lucide-react'

import { del, get, listRegisteredQueries, registerQuery } from '../../lib/api.js'
import VersionHistoryDialog from '../../components/app/VersionHistoryDialog.jsx'
import DangerConfirmDialog from '../../components/app/DangerConfirmDialog.jsx'
import { useEnv } from '../../contexts/EnvContext.jsx'
import { useProject } from '../../contexts/ProjectContext.jsx'
import { useCanWrite } from '../../contexts/OrgContext.jsx'
import { useUi } from '../../contexts/UiContext.jsx'
import QueryWorkspace from './QueryWorkspace.jsx'
import PreaggregationsPanel from './PreaggregationsPanel.jsx'

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

function QueryListItem({ query, isActive, onClick, onHistory, strictEnv, manageMode = false, checked = false, onToggleCheck }) {
  const hasParams = Array.isArray(query.params) && query.params.length > 0
  const isSaved = Boolean(query.id) && !query.isNew

  // In manage (multi-select) mode the whole row toggles its checkbox instead
  // of opening the query, and the leading icon becomes the checkbox.
  const CheckIcon = checked ? CheckSquare : Square

  return (
    <div className="relative group">
      <button
        onClick={() => (manageMode ? onToggleCheck?.(query) : onClick(query))}
        data-testid={manageMode ? 'query-manage-row' : undefined}
        data-query-id={query.id ?? undefined}
        aria-pressed={manageMode ? checked : undefined}
        className={[
          'w-full text-left px-3 py-2.5 rounded-lg transition-all',
          isSaved && onHistory && !manageMode ? 'pr-9' : '',
          manageMode && checked
            ? 'bg-red-500/5 border border-red-500/30 text-fg'
            : isActive && !manageMode
            ? 'bg-primary/10 border border-primary/20 text-fg'
            : 'hover:bg-surface-2 border border-transparent text-fg/80 hover:text-fg',
        ].join(' ')}
      >
        <div className="flex items-start gap-2 min-w-0">
          {manageMode ? (
            <CheckIcon
              size={13}
              className={[
                'shrink-0 mt-0.5',
                checked ? 'text-red-500' : 'text-muted group-hover:text-fg/60',
              ].join(' ')}
            />
          ) : (
          <FileCode2
            size={13}
            className={[
              'shrink-0 mt-0.5',
              isActive ? 'text-primary' : 'text-muted group-hover:text-fg/60',
            ].join(' ')}
          />
          )}
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
            {/* Strict-env visibility: the active env is protected and this
                query has no pinned version there (pinned_envs joined from
                the persisted GET /queries rows). */}
            {!query.isNew && strictEnv && Array.isArray(query.pinned_envs)
              && !query.pinned_envs.includes(strictEnv) && (
              <span
                title={`No version is pinned to ${strictEnv} — promote one to make it visible there.`}
                className="inline-flex items-center px-1 py-0.5 text-[9px] font-medium rounded bg-rose-500/10 text-rose-600 dark:text-rose-400 border border-rose-500/20 mt-1"
              >
                not in {strictEnv}
              </span>
            )}
          </div>
        </div>
      </button>

      {/* Version history — saved (registered) queries only */}
      {isSaved && onHistory && !manageMode && (
        <button
          onClick={(e) => { e.stopPropagation(); onHistory(query) }}
          title="Version history"
          aria-label={`Version history for ${query.name ?? query.id}`}
          className="absolute right-1.5 top-2 w-6 h-6 flex items-center justify-center rounded-md text-muted/60 hover:text-fg hover:bg-surface-2 opacity-0 group-hover:opacity-100 focus:opacity-100 transition-opacity"
        >
          <History size={12} />
        </button>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// QueriesPanel — query-list body of the right sidebar (search, new query,
// blend, drafts + registry list). Header/collapse chrome lives in the page.
// ---------------------------------------------------------------------------

function QueriesPanel({
  queries, localQueries, activeId, loading, onSelect, onNewQuery, onRefresh,
  searchQuery, onSearchChange, canWrite, onHistory, strictEnv,
  // Manage (multi-select bulk delete) mode
  manageMode = false, onToggleManage,
  selectedIds, onToggleSelect, onSelectAll, onClearSelection,
  onDeleteSelected, onDeleteAll, bulkNotice,
}) {
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
    <div className="flex flex-col h-full bg-surface-2/40">
      {/* New query / Blend buttons */}
      {canWrite ? (
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
      ) : (
        <div className="shrink-0 px-2 py-2">
          <p className="text-[10px] text-muted/70 text-center py-1.5 rounded-lg border border-dashed border-border">
            Read-only access
          </p>
        </div>
      )}

      {/* Search + registry refresh */}
      <div className="shrink-0 px-2 pb-2 flex items-center gap-1.5">
        <div className="relative flex-1">
          <Search size={11} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-muted pointer-events-none" />
          <input
            type="text"
            value={searchQuery}
            onChange={e => onSearchChange(e.target.value)}
            placeholder="Search queries…"
            className="w-full h-7 pl-7 pr-2.5 text-[11px] bg-surface border border-border rounded-lg text-fg placeholder:text-muted/50 focus:outline-none focus:ring-1 focus:ring-ring"
          />
        </div>
        <button
          onClick={onRefresh}
          disabled={loading}
          className="h-7 w-7 shrink-0 flex items-center justify-center rounded-lg border border-border bg-surface text-muted hover:text-fg hover:bg-surface-2 disabled:opacity-40 transition-colors"
          title="Refresh query registry"
        >
          <RefreshCw size={12} className={loading ? 'animate-spin' : ''} />
        </button>
        {/* Manage (multi-select) toggle — writers only */}
        {canWrite && onToggleManage && (
          <button
            onClick={onToggleManage}
            data-testid="queries-manage-toggle"
            aria-pressed={manageMode}
            title={manageMode ? 'Exit manage mode' : 'Manage queries (multi-select)'}
            className={[
              'h-7 w-7 shrink-0 flex items-center justify-center rounded-lg border transition-colors',
              manageMode
                ? 'bg-primary text-primary-fg border-primary'
                : 'border-border bg-surface text-muted hover:text-fg hover:bg-surface-2',
            ].join(' ')}
          >
            <ListChecks size={12} />
          </button>
        )}
      </div>

      {/* Bulk-delete success notice */}
      {bulkNotice && (
        <div
          data-testid="queries-bulk-notice"
          role="status"
          className="shrink-0 mx-2 mb-2 flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg border border-emerald-500/30 bg-emerald-500/10 text-[11px] text-emerald-700 dark:text-emerald-400"
        >
          <CheckCircle2 size={12} className="shrink-0" />
          <span className="min-w-0 truncate">{bulkNotice}</span>
        </div>
      )}

      {/* Selection action bar — manage mode only */}
      {manageMode && canWrite && (
        <div
          data-testid="queries-selection-bar"
          className="shrink-0 mx-2 mb-2 px-2.5 py-2 rounded-lg border border-primary/30 bg-primary/5 space-y-1.5"
        >
          <div className="flex items-center gap-2 text-[11px]">
            <span className="font-medium text-fg">{selectedIds?.size ?? 0} selected</span>
            <button
              onClick={() => onSelectAll?.(registeredFiltered.map(q => q.id))}
              disabled={registeredFiltered.length === 0}
              className="text-primary hover:underline disabled:opacity-40 disabled:no-underline"
            >
              Select all ({registeredFiltered.length})
            </button>
            {(selectedIds?.size ?? 0) > 0 && (
              <button
                onClick={onClearSelection}
                className="text-muted hover:text-fg transition-colors"
              >
                Clear
              </button>
            )}
          </div>
          <div className="flex items-center gap-1.5">
            <button
              data-testid="queries-delete-selected"
              onClick={onDeleteSelected}
              disabled={(selectedIds?.size ?? 0) === 0}
              className="flex-1 h-7 flex items-center justify-center gap-1 rounded-lg bg-red-600 text-white text-[11px] font-semibold hover:bg-red-700 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
            >
              <Trash2 size={11} />
              Delete {(selectedIds?.size ?? 0) > 0 ? selectedIds.size : ''}
            </button>
            <button
              data-testid="queries-delete-all"
              onClick={() => onDeleteAll?.(registeredFiltered)}
              disabled={registeredFiltered.length === 0}
              title={searchQuery ? 'Delete all queries matching the search' : 'Delete all registered queries'}
              className="flex-1 h-7 flex items-center justify-center gap-1 rounded-lg border border-red-500/30 text-red-600 dark:text-red-400 text-[11px] font-medium hover:bg-red-500/10 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
            >
              <Trash2 size={11} />
              Delete all{searchQuery ? ' matching' : ''}
            </button>
          </div>
        </div>
      )}

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
                onHistory={onHistory}
                strictEnv={strictEnv}
                manageMode={manageMode}
                checked={Boolean(selectedIds?.has(q.id))}
                onToggleCheck={() => onToggleSelect?.(q.id)}
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
    </div>
  )
}

// ---------------------------------------------------------------------------
// MobileQueryDropdown — compact selector for small screens
// ---------------------------------------------------------------------------

function MobileQueryDropdown({ queries, localQueries, activeQuery, onSelect, onNewQuery, loading, canWrite }) {
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
          {canWrite && (
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
          )}
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
  const canWrite = useCanWrite()

  // Strict-env badges: when the ACTIVE env is protected, registry rows whose
  // pinned_envs lack it get a 'not in <env>' chip.
  const { environments, activeEnv } = useEnv()
  const strictEnv = (Array.isArray(environments)
    && environments.find(e => e.key === activeEnv)?.protected)
    ? activeEnv
    : null

  // AppShell topbar slot — page toolbars portal into the single top bar
  // (dashboard-editor pattern). The shell's own Chat button handles chat.
  const { topbarSlot, chatOpen, closeChat } = useUi()

  // Right-hand side: the Queries panel and the global Chat panel share the
  // right edge. To avoid crushing the editor (especially md–lg where both are
  // ~300–340px) they are MUTUALLY EXCLUSIVE — opening one closes the other, so
  // the user can flip between them like tabs without either destroying the
  // other's state (the query list lives in this page; toggling never resets it).
  const [queriesPanelOpen, setQueriesPanelOpen] = useState(true)

  // Queries panel only actually occupies the RHS when chat isn't open.
  const queriesPanelVisible = queriesPanelOpen && !chatOpen

  const toggleQueriesPanel = useCallback(() => {
    if (chatOpen) {
      // Chat owns the RHS — bring the Queries panel forward instead of hiding.
      closeChat()
      setQueriesPanelOpen(true)
      return
    }
    setQueriesPanelOpen(o => !o)
  }, [chatOpen, closeChat])

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

  // ── Active view: SQL editor or Pre-aggregations panel ────────────────────
  const [view, setView] = useState('editor')

  // ── Load registry ──────────────────────────────────────────────────────
  const loadRegistry = useCallback(async () => {
    setLoadingRegistry(true)
    setRegistryError(null)
    try {
      // pinned_envs lives on the persisted rows (GET /queries — strict-env
      // list contract), not the runtime registry — fetch both and join by id.
      const [data, rows] = await Promise.all([
        listRegisteredQueries(),
        get('/queries').catch(() => null),
      ])
      const pinnedById = new Map()
      for (const r of Array.isArray(rows) ? rows : []) {
        if (Array.isArray(r.pinned_envs)) pinnedById.set(r.id, r.pinned_envs)
      }
      const merged = data.map(q =>
        pinnedById.has(q.id) ? { ...q, pinned_envs: pinnedById.get(q.id) } : q
      )
      setRegisteredQueries(merged)

      // Auto-select first local draft (or first registered if no drafts yet)
      setActiveQuery(prev => {
        if (prev) return prev // keep selection
        if (localQueries.length > 0) return localQueries[0]
        if (merged.length > 0) return merged[0]
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
    setView('editor') // picking a query from the sidebar always lands in the editor
  }, [])

  const handleNewQuery = useCallback(() => {
    const draft = newAdHocQuery()
    setLocalQueries(prev => [draft, ...prev])
    setActiveQuery(draft)
    setView('editor')
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

  // ── Manage mode (multi-select bulk delete of registered queries) ────────
  const [manageMode, setManageMode] = useState(false)
  const [selectedIds, setSelectedIds] = useState(() => new Set())
  const [bulkDialog, setBulkDialog] = useState(null) // { ids, names, all } | null
  const [bulkBusy, setBulkBusy] = useState(false)
  const [bulkError, setBulkError] = useState(null)
  const [bulkNotice, setBulkNotice] = useState(null)
  const noticeTimer = useRef(null)
  useEffect(() => () => clearTimeout(noticeTimer.current), [])

  const handleToggleManage = useCallback(() => {
    setManageMode(prev => {
      if (prev) setSelectedIds(new Set()) // leaving manage mode clears selection
      return !prev
    })
  }, [])

  const handleToggleSelect = useCallback((id) => {
    setSelectedIds(prev => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }, [])

  const handleSelectAll = useCallback((ids) => {
    setSelectedIds(prev =>
      prev.size === ids.length ? new Set() : new Set(ids)
    )
  }, [])

  const handleClearSelection = useCallback(() => setSelectedIds(new Set()), [])

  /** Open the confirm dialog for the current selection. */
  const handleDeleteSelected = useCallback(() => {
    const rows = registeredQueries.filter(q => selectedIds.has(q.id))
    if (rows.length === 0) return
    setBulkError(null)
    setBulkDialog({
      ids: rows.map(q => q.id),
      names: rows.map(q => q.name ?? q.id),
      all: false,
    })
  }, [registeredQueries, selectedIds])

  /** Open the confirm dialog for ALL registered queries matching the search. */
  const handleDeleteAll = useCallback((rows) => {
    if (!Array.isArray(rows) || rows.length === 0) return
    setBulkError(null)
    setBulkDialog({
      ids: rows.map(q => q.id),
      names: rows.map(q => q.name ?? q.id),
      all: true,
    })
  }, [])

  /** Run the bulk delete — loops the per-query DELETE /queries/{id} endpoint. */
  const handleBulkConfirm = useCallback(async () => {
    if (!bulkDialog || bulkBusy) return
    setBulkBusy(true)
    setBulkError(null)
    let failed = 0
    for (const id of bulkDialog.ids) {
      try {
        await del(`/queries/${id}`)
      } catch (err) {
        console.error(`Delete failed for query ${id}:`, err)
        failed++
      }
    }
    const deleted = bulkDialog.ids.length - failed
    setBulkBusy(false)
    setSelectedIds(new Set())
    // If the active query was just deleted, drop it so loadRegistry can
    // auto-select the first remaining draft instead.
    if (activeQuery?.id && bulkDialog.ids.includes(activeQuery.id)) {
      setActiveQuery(null)
    }
    await loadRegistry()
    if (failed > 0) {
      setBulkError(
        `Deleted ${deleted} of ${bulkDialog.ids.length} queries — ${failed} failed. The list has been refreshed.`
      )
    } else {
      setBulkDialog(null)
      clearTimeout(noticeTimer.current)
      setBulkNotice(`Deleted ${deleted} quer${deleted === 1 ? 'y' : 'ies'}.`)
      noticeTimer.current = setTimeout(() => setBulkNotice(null), 5000)
    }
  }, [bulkDialog, bulkBusy, activeQuery, loadRegistry])

  // ── Version history (kind='query') — opened from a list-row action ──────
  const [historyQuery, setHistoryQuery] = useState(null)
  // Bumped after a restore of the ACTIVE query so the workspace remounts and
  // the editor reloads the restored draft (its sync effect keys on query.id).
  const [restoreNonce, setRestoreNonce] = useState(0)

  const handleHistoryRestored = useCallback(async () => {
    const target = historyQuery
    if (!target?.id) return
    try {
      // The restore endpoint wrote the pinned config back into the persisted
      // queries row — re-read it (the in-memory registry is still stale).
      const row = await get(`/queries/${target.id}`)
      const cfg = row?.config ?? {}
      const fresh = {
        ...target,
        name: cfg.name ?? row?.name ?? target.name,
        sql: typeof cfg.sql === 'string' ? cfg.sql : target.sql,
        params: Array.isArray(cfg.params) ? cfg.params : (target.params ?? []),
        datastore_id: cfg.datastore_id ?? null,
        isNew: false,
      }
      // Sync the runtime registry so POST /query by id runs the restored SQL.
      if (fresh.sql) {
        await registerQuery({
          id: fresh.id,
          name: fresh.name,
          sql: fresh.sql,
          params: fresh.params,
          ...(fresh.datastore_id ? { datastore_id: fresh.datastore_id } : {}),
        }).catch(() => {})
      }
      setRegisteredQueries(prev => prev.map(q => (q.id === fresh.id ? fresh : q)))
      if (activeQuery?.id === fresh.id) {
        setActiveQuery(fresh)
        setRestoreNonce(n => n + 1)
      }
    } catch (err) {
      console.warn('[QueriesPage] reload after restore failed:', err)
      loadRegistry()
    }
  }, [historyQuery, activeQuery, loadRegistry])

  // ── Derived ────────────────────────────────────────────────────────────
  const activeId = activeQuery?._localId ?? activeQuery?.id

  // ── Topbar cluster (md+) — Editor/Rollups view toggle + RHS panel buttons,
  //    dashboard-editor style. Rendered inside the QueryWorkspace toolbar, or
  //    in the slim page bar when the workspace isn't mounted (rollups/empty).
  const toolbarCluster = (
    <div className="hidden md:flex items-center gap-1.5 shrink-0">
      {/* View toggle: SQL editor ↔ Pre-aggregations */}
      <div className="flex items-center rounded-lg border border-border overflow-hidden">
        <button
          onClick={() => setView('editor')}
          aria-pressed={view === 'editor'}
          className={[
            'h-8 px-2.5 flex items-center gap-1.5 text-[11px] font-medium transition-colors',
            view === 'editor' ? 'bg-primary/10 text-primary' : 'bg-surface text-muted hover:text-fg hover:bg-surface-2',
          ].join(' ')}
        >
          <FileCode2 size={12} /> Editor
        </button>
        <button
          onClick={() => setView('preagg')}
          aria-pressed={view === 'preagg'}
          title="Auto rollups mined from the query log"
          className={[
            'h-8 px-2.5 flex items-center gap-1.5 text-[11px] font-medium border-l border-border transition-colors',
            view === 'preagg' ? 'bg-primary/10 text-primary' : 'bg-surface text-muted hover:text-fg hover:bg-surface-2',
          ].join(' ')}
        >
          <Boxes size={12} /> Rollups
        </button>
      </div>

      {/* Queries-panel toggle. Chat is opened via the shell's single global
          chat button (far right) — no second chat button here. The two panels
          still share the right edge: opening chat hides the Queries panel,
          and this toggle brings the Queries panel back (closing chat). */}
      <button
        data-testid="panel-toggle-queries"
        title="Queries panel"
        aria-label="Queries panel"
        aria-pressed={queriesPanelVisible}
        onClick={toggleQueriesPanel}
        className={[
          'w-9 h-8 flex items-center justify-center rounded-lg border border-border transition-colors duration-150 focus:outline-none focus:ring-2 focus:ring-ring/60',
          queriesPanelVisible
            ? 'bg-primary text-primary-fg border-primary'
            : 'bg-surface text-muted hover:text-fg hover:bg-surface-2',
        ].join(' ')}
      >
        <List size={15} strokeWidth={2} />
      </button>
    </div>
  )

  return (
    <div className="flex h-[calc(100vh-var(--shell-header-h,56px))] overflow-hidden bg-bg">

      {/* ── Main content ───────────────────────────────────────────────── */}
      <div className="flex-1 flex flex-col min-w-0 overflow-hidden">

        {/* Mobile top bar (<md): query dropdown + view toggle. md+ uses the
            toolbar cluster + right sidebar instead. */}
        <div data-testid="queries-mobile-bar" className="md:hidden shrink-0 flex items-center gap-2 px-3 py-2 border-b border-border bg-surface-2/40">
          {view === 'editor' && (
            <MobileQueryDropdown
              queries={registeredQueries}
              localQueries={localQueries}
              activeQuery={activeQuery}
              onSelect={handleSelectQuery}
              onNewQuery={handleNewQuery}
              loading={loadingRegistry}
              canWrite={canWrite}
            />
          )}
          <div className="flex-1" />
          {/* Mobile view toggle */}
          <div className="flex items-center rounded-lg border border-border overflow-hidden">
            <button
              onClick={() => setView('editor')}
              className={[
                'h-8 px-2.5 flex items-center gap-1 text-[11px] font-medium transition-colors',
                view === 'editor' ? 'bg-primary/10 text-primary' : 'bg-surface text-muted hover:text-fg',
              ].join(' ')}
            >
              <FileCode2 size={12} /> Editor
            </button>
            <button
              onClick={() => setView('preagg')}
              className={[
                'h-8 px-2.5 flex items-center gap-1 text-[11px] font-medium border-l border-border transition-colors',
                view === 'preagg' ? 'bg-primary/10 text-primary' : 'bg-surface text-muted hover:text-fg',
              ].join(' ')}
            >
              <Boxes size={12} /> Rollups
            </button>
          </div>
        </div>

        {/* Shell-topbar toolbar when the workspace toolbar isn't mounted
            (rollups view / empty state) — portaled into the single top bar.
            When the workspace IS mounted it portals its own toolbar (with
            this cluster appended) instead; the two are mutually exclusive. */}
        {(view === 'preagg' || !activeQuery) && topbarSlot && createPortal(
          <div className="flex items-center gap-1.5 w-full min-w-0">
            <span className="text-sm font-semibold font-display text-fg truncate">
              {view === 'preagg' ? 'Rollups' : 'Queries'}
            </span>
            <div className="flex-1" />
            {toolbarCluster}
          </div>,
          topbarSlot
        )}

        {/* Registry error banner (editor view only) */}
        {view === 'editor' && registryError && (
          <div className="shrink-0 flex items-center gap-2 px-4 py-2 bg-rose-500/5 border-b border-rose-500/20 text-xs text-rose-600 dark:text-rose-400">
            <AlertCircle size={12} />
            Registry unavailable: {registryError}. Ad-hoc queries still work.
          </div>
        )}

        {/* Pre-aggregations panel */}
        {view === 'preagg' && (
          <div className="flex-1 min-h-0 overflow-hidden">
            <PreaggregationsPanel />
          </div>
        )}

        {/* Workspace */}
        {view === 'editor' && (activeQuery ? (
          <div className="flex-1 min-h-0 overflow-hidden">
            <QueryWorkspace
              key={`${activeId}:${restoreNonce}`}
              query={activeQuery}
              onQueryChange={handleQueryChange}
              onSaved={handleSaved}
              isNew={Boolean(activeQuery.isNew)}
              toolbarExtra={toolbarCluster}
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
                Select a query from the Queries panel on the right (desktop) or the dropdown above (mobile), or create a new one to get started.
              </p>
            </div>
            {canWrite && (
              <button
                onClick={handleNewQuery}
                className="flex items-center gap-2 px-4 py-2 text-sm font-medium bg-primary text-primary-fg rounded-lg hover:opacity-90 transition-opacity"
              >
                <Plus size={15} />
                New query
              </button>
            )}
          </div>
        ))}
      </div>

      {/* ── Right sidebar — Queries panel ──
          Desktop (lg+): static 288px panel.
          Tablet (md–lg): slide-over drawer (fixed, z-30), toggled from the topbar.
          Mobile (<md): hidden — the mobile dropdown handles selection. */}
      {queriesPanelVisible && (
        <aside
          data-testid="queries-side-panel"
          className={`
            border-l border-border bg-surface flex-col overflow-hidden
            hidden md:flex
            lg:static lg:w-72 lg:shrink-0
            md:fixed md:top-[var(--shell-header-h,56px)] md:bottom-0 md:right-0 md:z-30 md:w-80 md:shadow-2xl
            lg:shadow-none
          `}
        >
          <div className="flex items-center justify-between px-3 h-9 border-b border-border shrink-0">
            <span className="text-[11px] font-semibold uppercase tracking-wider text-muted">
              Queries
            </span>
            <button
              onClick={() => setQueriesPanelOpen(false)}
              title="Collapse panel"
              aria-label="Collapse side panel"
              className="flex items-center justify-center w-7 h-7 rounded-lg text-muted hover:text-fg hover:bg-surface-2 transition-colors focus:outline-none focus:ring-2 focus:ring-ring"
            >
              <PanelRightClose size={16} />
            </button>
          </div>
          {/* Body scrolls WITHIN the fixed-width sidebar; the sidebar itself is static. */}
          <div className="flex-1 min-h-0 overflow-hidden flex flex-col">
            <QueriesPanel
              queries={registeredQueries}
              localQueries={localQueries}
              activeId={activeId}
              loading={loadingRegistry}
              onSelect={handleSelectQuery}
              onNewQuery={handleNewQuery}
              onRefresh={loadRegistry}
              searchQuery={railSearch}
              onSearchChange={setRailSearch}
              canWrite={canWrite}
              onHistory={setHistoryQuery}
              strictEnv={strictEnv}
              manageMode={manageMode}
              onToggleManage={handleToggleManage}
              selectedIds={selectedIds}
              onToggleSelect={handleToggleSelect}
              onSelectAll={handleSelectAll}
              onClearSelection={handleClearSelection}
              onDeleteSelected={handleDeleteSelected}
              onDeleteAll={handleDeleteAll}
              bulkNotice={bulkNotice}
            />
          </div>
        </aside>
      )}

      {/* ── Bulk-delete confirmation — random-code gate ──────────────────── */}
      {bulkDialog && (
        <DangerConfirmDialog
          title={bulkDialog.all
            ? `Delete ALL ${bulkDialog.ids.length} quer${bulkDialog.ids.length === 1 ? 'y' : 'ies'}`
            : `Delete ${bulkDialog.ids.length} quer${bulkDialog.ids.length === 1 ? 'y' : 'ies'}`}
          description={bulkDialog.all
            ? (railSearch
              ? `You are about to wipe every registered query matching “${railSearch}”. Dashboards, embeds and flows that reference these queries will break.`
              : 'You are about to wipe EVERY registered query in this project. Dashboards, embeds and flows that reference these queries will break.')
            : 'The selected queries will be permanently removed from the registry. Dashboards, embeds and flows that reference them will break.'}
          items={bulkDialog.names}
          count={bulkDialog.ids.length}
          itemNoun="query"
          itemNounPlural="queries"
          confirmLabel={`Delete ${bulkDialog.ids.length} quer${bulkDialog.ids.length === 1 ? 'y' : 'ies'}`}
          loading={bulkBusy}
          error={bulkError}
          onCancel={() => { if (!bulkBusy) { setBulkDialog(null); setBulkError(null) } }}
          onConfirm={handleBulkConfirm}
        />
      )}

      {/* ── Version history (kind='query', opened from a list row) ───────── */}
      {historyQuery && (
        <VersionHistoryDialog
          kind="query"
          resourceId={historyQuery.id}
          resourceName={historyQuery.name ?? historyQuery.id}
          open
          onClose={() => setHistoryQuery(null)}
          onRestored={handleHistoryRestored}
        />
      )}
    </div>
  )
}
