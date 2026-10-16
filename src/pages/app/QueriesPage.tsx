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
import { Link, useParams, useNavigate } from 'react-router-dom'
import {
  FileCode2,
  Plus,
  RefreshCw,
  ChevronDown,
  ListChecks,
  AlertCircle,
  List,
  Combine,
  Boxes,
  PanelRightClose,
  History,
  Trash2,
} from 'lucide-react'

import {
  del, get, listRegisteredQueries, registerQuery, getRegisteredQuery,
  validateRegisteredQueries,
} from '../../lib/api.js'
import Button from '../../components/ui/Button.jsx'
import TagChip from '../../components/ui/Tag.jsx'
import EmptyState from '../../components/ui/EmptyState.jsx'
import SegmentedControl from '../../components/ui/SegmentedControl.jsx'
import Tooltip from '../../components/ui/Tooltip.jsx'
import { Checkbox } from '../../components/ui/Input.jsx'
import { EmptyQueries, EmptySearch } from '../../components/illustrations'
import { SearchBar } from '../../components/app/PageShell.jsx'
import { ListSkeleton } from './appShared.jsx'
import '../../components/app/appPatterns.css'
import VersionHistoryDialog from '../../components/app/VersionHistoryDialog.jsx'
import DangerConfirmDialog from '../../components/app/DangerConfirmDialog.jsx'
import { useEnv } from '../../contexts/EnvContext.jsx'
import { useProject } from '../../contexts/ProjectContext.jsx'
import { useCanWrite } from '../../contexts/OrgContext.jsx'
import { useUi } from '../../contexts/UiContext.jsx'
import { usePageCrumb } from '../../components/app/PageShell.jsx'
import QueryWorkspace from './QueryWorkspace.jsx'
import PreaggregationsPanel from './PreaggregationsPanel.jsx'

// Remembers whether the right-hand Queries rail is open, across visits.
const QUERIES_PANEL_STORAGE_KEY = 'nubi:queries-panel-open'

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
// QueryListItem — single ruled row in the right rail
// ---------------------------------------------------------------------------

function QueryListItem({ query, isActive, onClick, onHistory = undefined, strictEnv = null, manageMode = false, checked = false, onToggleCheck = undefined, health = undefined }) {
  const hasParams = Array.isArray(query.params) && query.params.length > 0
  const isSaved = Boolean(query.id) && !query.isNew
  // `health` is undefined until POST /query/registry/validate has covered this
  // row, so only an explicit `valid: false` flags it — never a pending check.
  const broken = health?.valid === false
  const notInEnv = !query.isNew && strictEnv && Array.isArray(query.pinned_envs) && !query.pinned_envs.includes(strictEnv)

  return (
    <div className="relative group">
      <button
        type="button"
        onClick={() => (manageMode ? onToggleCheck?.(query) : onClick(query))}
        data-testid={manageMode ? 'query-manage-row' : undefined}
        data-query-id={query.id ?? undefined}
        aria-pressed={manageMode ? checked : undefined}
        aria-current={isActive && !manageMode ? 'true' : undefined}
        className={[
          'qr-row nubi-focus-inset',
          isSaved && onHistory && !manageMode ? 'pr-9' : '',
          manageMode && checked ? 'is-checked' : '',
          isActive && !manageMode ? 'is-active' : '',
        ].join(' ')}
      >
        {manageMode && (
          <span className="mt-0.5 shrink-0" aria-hidden="true">
            <Checkbox checked={checked} readOnly tabIndex={-1} aria-hidden="true" />
          </span>
        )}
        <div className="min-w-0 flex-1">
          <p className="qr-name">{query.name ?? query.id}</p>
          {/* Every row gets the same second line — the id for a saved query,
              or a plain-language status for a draft — so rows don't jump. */}
          <p className="qr-id">{query.id ?? 'not saved yet'}</p>
          {(hasParams || broken || query.isNew || notInEnv) && (
            <div className="ap-tags mt-1.5">
              {query.isNew && <TagChip variant="warning" dot>Draft</TagChip>}
              {/* Stored SQL does not parse — running it can only 400. Say so
                  here rather than letting the user find out on Run. */}
              {broken && (
                <TagChip variant="danger" dot title={health?.error ?? 'The stored SQL cannot be parsed.'}>
                  won&apos;t run
                </TagChip>
              )}
              {/* Strict-env visibility: the active env is protected and this
                  query has no pinned version there. */}
              {notInEnv && (
                <TagChip
                  variant="warning"
                  dot
                  caps={false}
                  title={`No version is pinned to ${strictEnv}. Promote one to make it visible there.`}
                >
                  not in {strictEnv}
                </TagChip>
              )}
              {hasParams && query.params.slice(0, 3).map(p => (
                <TagChip key={p.name} caps={false}>{p.name}</TagChip>
              ))}
              {hasParams && query.params.length > 3 && (
                <span className="ap-meta">+{query.params.length - 3}</span>
              )}
            </div>
          )}
        </div>
      </button>

      {/* Version history — saved (registered) queries only */}
      {isSaved && onHistory && !manageMode && (
        <button
          type="button"
          onClick={(e) => { e.stopPropagation(); onHistory(query) }}
          title="Version history"
          aria-label={`Version history for ${query.name ?? query.id}`}
          className="absolute right-1.5 top-2 flex h-7 w-7 items-center justify-center text-subtle opacity-0 transition-colors hover:bg-frame-hover hover:text-fg focus:opacity-100 group-hover:opacity-100 [@media(hover:none)]:opacity-100"
          style={{ borderRadius: 2 }}
        >
          <History size={13} aria-hidden="true" />
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
  searchQuery, onSearchChange, canWrite, onHistory, strictEnv, queryHealth,
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
    <div className="flex flex-col h-full bg-surface">
      {/* New query / Blend buttons */}
      {canWrite ? (
        <div className="shrink-0 px-3 pt-3 pb-2 flex gap-2">
          <Button onClick={onNewQuery} size="sm" className="flex-1">
            <Plus size={14} aria-hidden="true" />
            New query
          </Button>
          <Button asChild variant="secondary" size="sm" className="flex-1">
            <Link to="/queries/blend">
              <Combine size={14} aria-hidden="true" />
              Blend
            </Link>
          </Button>
        </div>
      ) : (
        <div className="shrink-0 px-3 pt-3 pb-2">
          <p className="ap-count m-0">Read-only access</p>
        </div>
      )}

      {/* Search + registry refresh */}
      <div className="shrink-0 px-3 pb-2 flex items-center gap-1.5">
        <SearchBar
          value={searchQuery}
          onChange={onSearchChange}
          placeholder="Search queries"
        />
        <Button
          variant="ghost"
          size="icon"
          onClick={onRefresh}
          disabled={loading}
          aria-label="Refresh query registry"
          title="Refresh query registry"
        >
          <RefreshCw size={14} className={loading ? 'animate-spin' : ''} aria-hidden="true" />
        </Button>
        {/* Manage (multi-select) toggle — writers only */}
        {canWrite && onToggleManage && (
          <Button
            variant={manageMode ? 'primary' : 'ghost'}
            size="icon"
            onClick={onToggleManage}
            data-testid="queries-manage-toggle"
            aria-pressed={manageMode}
            aria-label={manageMode ? 'Exit manage mode' : 'Manage queries (multi-select)'}
            title={manageMode ? 'Exit manage mode' : 'Manage queries (multi-select)'}
          >
            <ListChecks size={14} aria-hidden="true" />
          </Button>
        )}
      </div>

      {/* Bulk-delete success notice */}
      {bulkNotice && (
        <div data-testid="queries-bulk-notice" role="status" className="nubi-notice nubi-notice-success shrink-0 mx-3 mb-2 text-xs">
          <span className="min-w-0 truncate">{bulkNotice}</span>
        </div>
      )}

      {/* Selection action bar — manage mode only */}
      {manageMode && canWrite && (
        <div data-testid="queries-selection-bar" className="shrink-0 mx-3 mb-2 px-3 py-2 space-y-2 bg-accent-soft" style={{ borderLeft: '2px solid var(--accent)' }}>
          <div className="flex items-center gap-3 text-xs">
            <span className="font-semibold text-fg ap-mono">{selectedIds?.size ?? 0} selected</span>
            <button
              type="button"
              onClick={() => onSelectAll?.(registeredFiltered.map(q => q.id))}
              disabled={registeredFiltered.length === 0}
              className="ap-link-btn disabled:opacity-40"
              style={{ color: 'var(--accent)' }}
            >
              Select all {registeredFiltered.length}
            </button>
            {(selectedIds?.size ?? 0) > 0 && (
              <button type="button" onClick={onClearSelection} className="ap-link-btn">Clear</button>
            )}
          </div>
          <div className="flex items-center gap-2">
            <Button
              data-testid="queries-delete-selected"
              onClick={onDeleteSelected}
              disabled={(selectedIds?.size ?? 0) === 0}
              variant="danger"
              size="xs"
              className="flex-1"
            >
              <Trash2 size={12} aria-hidden="true" />
              Delete {(selectedIds?.size ?? 0) > 0 ? selectedIds.size : ''}
            </Button>
            <Button
              data-testid="queries-delete-all"
              onClick={() => onDeleteAll?.(registeredFiltered)}
              disabled={registeredFiltered.length === 0}
              title={searchQuery ? 'Delete all queries matching the search' : 'Delete all registered queries'}
              variant="outline"
              size="xs"
              className="flex-1"
            >
              <Trash2 size={12} aria-hidden="true" />
              Delete all{searchQuery ? ' matching' : ''}
            </Button>
          </div>
        </div>
      )}

      {/* Query list */}
      <div className="flex-1 overflow-y-auto pb-2">
        {loading && allItems.length === 0 && <ListSkeleton rows={4} tile={false} />}

        {!loading && allItems.length === 0 && (
          <EmptyState
            compact
            art={<EmptyQueries />}
            title="No saved queries"
            description={canWrite ? 'Write SQL in the editor, then save it to share it with boards and flows.' : 'Nothing has been saved to the registry yet.'}
            action={canWrite ? (
              <Button size="sm" onClick={onNewQuery}>
                <Plus size={14} aria-hidden="true" />
                New query
              </Button>
            ) : undefined}
          />
        )}

        {/* Draft queries */}
        {draftFiltered.length > 0 && (
          <div>
            <p className="qr-group">Drafts</p>
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
            <p className="qr-group">Registry <span className="ap-mono">{registeredFiltered.length}</span></p>
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
                health={queryHealth?.[q.id]}
              />
            ))}
          </div>
        )}

        {/* No results for search */}
        {searchQuery && filtered.length === 0 && (
          <EmptyState
            compact
            kind="none-match"
            art={<EmptySearch />}
            title={`No query matches “${searchQuery}”`}
            description="Search covers names and ids."
            action={<Button size="sm" variant="secondary" onClick={() => onSearchChange('')}>Clear search</Button>}
          />
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
    const key = (e) => { if (e.key === 'Escape') setOpen(false) }
    document.addEventListener('mousedown', handler)
    document.addEventListener('keydown', key)
    return () => {
      document.removeEventListener('mousedown', handler)
      document.removeEventListener('keydown', key)
    }
  }, [open])

  return (
    <div className="relative min-w-0" ref={ref}>
      <Button
        variant="secondary"
        size="sm"
        onClick={() => setOpen(o => !o)}
        aria-expanded={open}
        aria-haspopup="listbox"
        className="max-w-full"
      >
        <List size={14} className="shrink-0" aria-hidden="true" />
        <span className="truncate max-w-[160px]">
          {activeQuery?.name ?? activeQuery?.id ?? 'Select query'}
        </span>
        <ChevronDown size={13} className="text-muted shrink-0" aria-hidden="true" style={{ transform: open ? 'rotate(180deg)' : 'none' }} />
      </Button>

      {open && (
        <div role="listbox" className="nubi-dropdown left-0 top-[calc(100%+6px)] w-72 max-w-[calc(100vw-2rem)] p-0">
          {canWrite && (
            <div className="p-1.5 border-b border-border">
              <button
                type="button"
                onClick={() => { onNewQuery(); setOpen(false) }}
                className="nubi-dropdown-item"
              >
                <Plus size={14} aria-hidden="true" />
                New query
              </button>
              <Link to="/queries/blend" onClick={() => setOpen(false)} className="nubi-dropdown-item">
                <Combine size={14} aria-hidden="true" />
                Blend sources
              </Link>
            </div>
          )}
          <div className="max-h-64 overflow-y-auto p-1.5">
            {loading && (
              <div className="flex items-center justify-center gap-2 text-xs text-muted py-3">
                <RefreshCw size={12} className="animate-spin" aria-hidden="true" />
                Loading queries
              </div>
            )}
            {!loading && allItems.length === 0 && (
              <div className="text-xs text-muted text-center py-3">No queries yet</div>
            )}
            {allItems.map(q => {
              const on = (activeQuery?._localId ?? activeQuery?.id) === (q._localId ?? q.id)
              return (
                <button
                  type="button"
                  key={q._localId ?? q.id}
                  role="option"
                  aria-selected={on}
                  onClick={() => { onSelect(q); setOpen(false) }}
                  className={`nubi-dropdown-item ${on ? 'font-semibold' : ''}`}
                  style={on ? { background: 'var(--accent-soft)' } : undefined}
                >
                  <span className="truncate flex-1 text-left">{q.name ?? q.id}</span>
                  {q.isNew && <TagChip variant="warning" dot>Draft</TagChip>}
                </button>
              )
            })}
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
  //
  // Open/collapsed is remembered across visits (localStorage) so a user who
  // collapses the rail to give the editor more room doesn't have to redo
  // that every time they come back to /queries. The width fallback below only
  // applies the very first time (no stored preference yet): open at md+
  // (matches the "Tablet: open by default" / "Desktop: static panel"
  // behaviour docs above), closed below that where the CSS hides it anyway.
  const [queriesPanelOpen, setQueriesPanelOpen] = useState(() => {
    if (typeof window === 'undefined') return false
    try {
      const stored = window.localStorage.getItem(QUERIES_PANEL_STORAGE_KEY)
      if (stored === '1' || stored === '0') return stored === '1'
    } catch { /* localStorage unavailable (privacy mode etc.) — fall through */ }
    return window.innerWidth >= 768
  })

  useEffect(() => {
    try {
      window.localStorage.setItem(QUERIES_PANEL_STORAGE_KEY, queriesPanelOpen ? '1' : '0')
    } catch { /* best-effort persistence only */ }
  }, [queriesPanelOpen])

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
  // { [queryId]: { valid: boolean, error?: string } } — filled in progressively
  // by POST /query/registry/validate; an id absent from the map is "not checked
  // yet" and is rendered exactly as before.
  const [queryHealth, setQueryHealth] = useState({})

  // ── Local drafts (in-memory; not persisted) ───────────────────────────
  const [localQueries, setLocalQueries] = useState(() => [newAdHocQuery()])

  // ── Active query ───────────────────────────────────────────────────────
  const [activeQuery, setActiveQuery] = useState(null)

  // Deep-link support: /queries/:id selects that query (see effect below).
  const { id: routeQueryId } = useParams()
  const navigate = useNavigate()

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

  // ── Which registered queries can actually run? ──────────────────────────
  // A query whose stored SQL no longer parses is indistinguishable from a
  // healthy one in this list — you only find out after opening it and pressing
  // Run. Migrated estates can carry hundreds of those, so flag them in the rail
  // instead. This runs AFTER the list renders and fills in progressively, so a
  // slow validation never delays the library.
  useEffect(() => {
    if (registeredQueries.length === 0) return
    const controller = new AbortController()
    setQueryHealth({})
    validateRegisteredQueries(
      registeredQueries.map(q => q.id).filter(Boolean),
      {
        signal: controller.signal,
        onChunk: partial => {
          if (controller.signal.aborted) return
          setQueryHealth(prev => ({ ...prev, ...partial }))
        },
      },
    )
    return () => controller.abort()
  }, [registeredQueries])

  // ── Deep link: /queries/:id ─────────────────────────────────────────────
  // The route has always existed (App.jsx) but nothing read the param, so
  // every /queries/:id URL silently rendered a blank new draft. It is the
  // prerequisite for "Open in query editor →" on a dashboard widget.
  //
  // Resolution order: the already-loaded registry list first (no round-trip),
  // then GET /query/registry/{id} — which also resolves slug ids that the list
  // endpoint deliberately omits, so a migrated board's q_xxxxxxxx opens too.
  useEffect(() => {
    if (!routeQueryId) return
    if (activeQuery?.id === routeQueryId) return

    const fromList = registeredQueries.find(q => q.id === routeQueryId)
    if (fromList) {
      setActiveQuery(fromList)
      setView('editor')
      return
    }
    let cancelled = false
    getRegisteredQuery(routeQueryId).then(q => {
      if (!cancelled && q) {
        setActiveQuery(q)
        setView('editor')
      }
    })
    return () => { cancelled = true }
  }, [routeQueryId, registeredQueries]) // eslint-disable-line react-hooks/exhaustive-deps

  // Init active selection after first load. Skipped while a deep link is
  // pending so we don't flash an unrelated draft over the requested query.
  useEffect(() => {
    if (activeQuery) return
    if (routeQueryId) return
    if (localQueries.length > 0) {
      setActiveQuery(localQueries[0])
    }
  }, [localQueries, activeQuery, routeQueryId])

  // ── Actions ────────────────────────────────────────────────────────────

  const handleSelectQuery = useCallback((q) => {
    setActiveQuery(q)
    setView('editor') // picking a query from the sidebar always lands in the editor
    // Keep the URL in step so the selection is shareable and back-navigable.
    // Unsaved drafts have no id — those fall back to the bare /queries URL.
    navigate(q?.id ? `/queries/${encodeURIComponent(q.id)}` : '/queries', { replace: true })
  }, [navigate])

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
  // Label the last breadcrumb segment with the open query's name on /queries/:id.
  usePageCrumb(routeQueryId ? (activeQuery?.name ?? null) : null)

  // ── Topbar cluster (md+) — Editor/Rollups view toggle + RHS panel button.
  //    Rendered inside the QueryWorkspace toolbar, or in the slim page bar when
  //    the workspace isn't mounted (rollups/empty).
  const viewSwitch = (
    <SegmentedControl
      size="sm"
      value={view}
      onChange={setView}
      options={[
        { value: 'editor', label: <span className="inline-flex items-center gap-1.5"><FileCode2 size={13} aria-hidden="true" /> Editor</span> },
        { value: 'preagg', label: <span className="inline-flex items-center gap-1.5" title="Auto rollups mined from the query log"><Boxes size={13} aria-hidden="true" /> Rollups</span> },
      ]}
    />
  )
  const toolbarCluster = (
    <div className="hidden md:flex items-center gap-2 shrink-0">
      {viewSwitch}

      {/* Queries-panel toggle. Chat is opened via the shell's single global
          chat button (far right) — no second chat button here. The two panels
          still share the right edge: opening chat hides the Queries panel,
          and this toggle brings the Queries panel back (closing chat). */}
      <Tooltip content="Queries panel" side="bottom">
        <Button
          variant={queriesPanelVisible ? 'primary' : 'secondary'}
          size="icon-sm"
          data-testid="panel-toggle-queries"
          aria-label="Queries panel"
          aria-pressed={queriesPanelVisible}
          onClick={toggleQueriesPanel}
        >
          <List size={14} aria-hidden="true" />
        </Button>
      </Tooltip>
    </div>
  )

  return (
    <div className="flex h-[calc(100vh-var(--shell-header-h,56px))] overflow-hidden bg-surface">

      {/* ── Main content ───────────────────────────────────────────────── */}
      <div className="flex-1 flex flex-col min-w-0 overflow-hidden">
        <h1 className="sr-only">
          {view === 'preagg' ? 'Rollups' : (activeQuery?.name || 'Queries')}
        </h1>

        {/* Mobile top bar (<md): query dropdown + view toggle. md+ uses the
            toolbar cluster + right sidebar instead. */}
        <div data-testid="queries-mobile-bar" className="md:hidden shrink-0 flex items-center gap-2 px-4 py-2 border-b border-border bg-surface">
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
          {viewSwitch}
        </div>

        {/* Shell-topbar toolbar when the workspace toolbar isn't mounted
            (rollups view / empty state) — portaled into the single top bar.
            When the workspace IS mounted it portals its own toolbar (with
            this cluster appended) instead; the two are mutually exclusive. */}
        {(view === 'preagg' || !activeQuery) && topbarSlot && createPortal(
          <div className="flex items-center gap-2 w-full min-w-0">
            <span className="t-label">{view === 'preagg' ? 'Rollups' : 'Queries'}</span>
            <div className="flex-1" />
            {toolbarCluster}
          </div>,
          topbarSlot
        )}

        {/* Registry error banner (editor view only) */}
        {view === 'editor' && registryError && (
          <div role="alert" className="nubi-notice nubi-notice-error shrink-0 m-3 text-xs">
            <AlertCircle size={14} className="shrink-0" aria-hidden="true" />
            <span className="flex-1">
              Registry unavailable: {registryError}. Ad-hoc queries still work.
            </span>
            <Button size="xs" variant="secondary" onClick={loadRegistry}>Retry</Button>
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
          <div className="flex-1 flex items-center justify-center px-6">
            <EmptyState
              art={<EmptyQueries />}
              title="No query open"
              description="Pick a saved query from the panel (or the menu above on a phone), or start a new one. Queries run in your browser and are cached for every viewer."
              action={canWrite ? (
                <Button onClick={handleNewQuery}>
                  <Plus size={14} aria-hidden="true" />
                  New query
                </Button>
              ) : undefined}
            />
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
            border-l border-frame-line bg-surface flex-col overflow-hidden
            hidden md:flex
            lg:static lg:w-80 lg:shrink-0
            md:fixed md:top-[var(--shell-header-h,56px)] md:bottom-0 md:right-0 md:z-30 md:w-80 ap-pop-md
          `}
        >
          <div className="flex items-center justify-between pl-3 pr-1.5 h-10 border-b border-border shrink-0" style={{ borderTop: '2px solid var(--ink)' }}>
            <span className="t-label">Queries</span>
            <Button variant="ghost" size="icon-sm" onClick={() => setQueriesPanelOpen(false)} title="Collapse panel" aria-label="Collapse side panel">
              <PanelRightClose size={16} aria-hidden="true" />
            </Button>
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
              queryHealth={queryHealth}
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
