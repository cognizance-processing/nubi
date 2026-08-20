/**
 * shared/QueryBrowser.tsx — searchable list of registered queries, by NAME
 * rather than by opaque id. Backs `QueryPicker`'s popover.
 *
 * Data comes from three cheap, already-existing reads (never a hand-built
 * fetch — everything routes through src/lib/api.js so tenant headers stay
 * attached):
 *   - listRegisteredQueries()  GET /query/registry     → id, name, datastore_id
 *   - listConnectors()         GET /connectors          → datastore_id → name
 *   - get('/queries')          persisted rows            → pinned_envs (the
 *     same join QueriesPage.jsx does for its "not in env" badge)
 *
 * Output columns are NOT in that list response (the registry only exposes
 * SQL/params/datastore there), so they are not fetched for every row — that
 * would mean running every query in the registry just to render a dropdown.
 * Instead a row's columns are introspected lazily, only for the row currently
 * hovered/keyboard-active, via the same client-side query engine the focus
 * mode's data rail already uses (`useColumnIntrospection`).
 */

import { useEffect, useMemo, useRef, useState } from 'react'
import { FileCode2, AlertTriangle, Search, X } from 'lucide-react'
import Badge from '../../components/ui/Badge.jsx'
import EmptyState from '../../components/ui/EmptyState.jsx'
import { ListRowSkeleton } from '../../components/app/PageShell.jsx'
import { listRegisteredQueries, listConnectors, get } from '../../lib/api.js'
import { useEnv } from '../../contexts/EnvContext.jsx'
import { useColumnIntrospection } from './useInspectorData.js'
import { DEMO_QUERY_IDS } from './constants.js'

// ---------------------------------------------------------------------------
// Data: merge the registry + connectors + env-pinning into browsable rows
// ---------------------------------------------------------------------------

/**
 * @typedef {{
 *   id: string,
 *   name: string|null,
 *   datastoreId: string|null,
 *   connectorName: string|null,
 *   builtin: boolean,
 *   known: boolean,
 *   pinnedEnvs: string[]|null,
 *   notInActiveEnv: boolean,
 * }} QueryRow
 */

/**
 * Loads and merges the query registry into rows a person can recognise.
 * `extraIds` (the picker's existing prop — ids the caller already knows about,
 * e.g. `{id, name}` pairs collected elsewhere in the editor) are merged in too
 * so a query visible to the caller but for some reason absent from this read
 * still shows its name instead of silently falling back to a bare id.
 */
// eslint-disable-next-line react-refresh/only-export-components -- hook lives alongside the component it feeds; both are internal to the picker.
export function useQueryRegistry(extraIds = [], currentValue = '') {
  const [registry, setRegistry] = useState([])
  const [connectors, setConnectors] = useState([])
  const [pinnedById, setPinnedById] = useState(() => new Map())
  const [loading, setLoading] = useState(true)
  const { environments, activeEnv } = useEnv()

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    Promise.all([
      listRegisteredQueries(),
      listConnectors(),
      get('/queries').catch(() => null),
    ]).then(([regRows, connRows, persistedRows]) => {
      if (cancelled) return
      setRegistry(Array.isArray(regRows) ? regRows : [])
      setConnectors(Array.isArray(connRows) ? connRows : [])
      const map = new Map()
      for (const r of Array.isArray(persistedRows) ? persistedRows : []) {
        if (r?.id && Array.isArray(r.pinned_envs)) map.set(r.id, r.pinned_envs)
      }
      setPinnedById(map)
    }).finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [])

  // A protected active env is the only one worth flagging — matches
  // QueriesPage's `strictEnv` logic exactly.
  const strictEnv = useMemo(() => (
    Array.isArray(environments) && environments.find(e => e.key === activeEnv)?.protected
      ? activeEnv
      : null
  ), [environments, activeEnv])

  const connectorNameById = useMemo(() => {
    const m = new Map()
    for (const c of connectors) m.set(c.id, c?.name || c.id)
    return m
  }, [connectors])

  const entries = useMemo(() => {
    /** @type {Map<string, QueryRow>} */
    const map = new Map()
    const upsert = (id, patch) => {
      if (!id) return
      const prev = map.get(id) ?? {
        id, name: null, datastoreId: null, connectorName: null,
        builtin: false, known: false, pinnedEnvs: null,
      }
      map.set(id, { ...prev, ...patch })
    }

    // Built-in demo queries — always offered, never gated by org scoping.
    DEMO_QUERY_IDS.forEach(id => upsert(id, { builtin: true, known: true }))

    registry.forEach(q => upsert(q.id, {
      name: q.name ?? null,
      datastoreId: q.datastore_id ?? null,
      connectorName: q.datastore_id
        ? (connectorNameById.get(q.datastore_id) ?? q.datastore_id)
        : null,
      known: true,
      pinnedEnvs: pinnedById.get(q.id) ?? null,
    }))

    extraIds.forEach(entry => {
      const { id, name } = typeof entry === 'string' ? { id: entry, name: null } : (entry ?? {})
      if (!id) return
      const prev = map.get(id)
      upsert(id, { name: prev?.name ?? name ?? null, known: true })
    })

    // The currently-bound id, even if it resolves to nothing above — so a
    // widget's binding is never silently dropped from the list it came from.
    if (currentValue) upsert(currentValue, {})

    return [...map.values()].map(row => ({
      ...row,
      notInActiveEnv: Boolean(
        strictEnv && !row.builtin && Array.isArray(row.pinnedEnvs) && !row.pinnedEnvs.includes(strictEnv)
      ),
    }))
  }, [registry, extraIds, connectorNameById, pinnedById, strictEnv, currentValue])

  return { entries, loading, strictEnv }
}

// ---------------------------------------------------------------------------
// Lazy per-row column preview
// ---------------------------------------------------------------------------

function ColumnsPreview({ queryId }) {
  const { columns, introspecting } = useColumnIntrospection(queryId)
  if (introspecting) {
    return <p className="text-[10px] text-muted/60 mt-1.5 pl-[19px]">Checking columns…</p>
  }
  if (columns.length === 0) return null
  return (
    <div className="flex flex-wrap gap-1 mt-1.5 pl-[19px]">
      {columns.slice(0, 8).map(c => (
        <span key={c} className="px-1.5 py-0.5 rounded bg-surface-2 text-[9px] font-mono text-muted/80 truncate max-w-[110px]">
          {c}
        </span>
      ))}
      {columns.length > 8 && (
        <span className="text-[9px] text-muted/60 self-center">+{columns.length - 8} more</span>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// QueryBrowser — search + list. Presentational: all data comes in as props.
// ---------------------------------------------------------------------------

/**
 * Props:
 *   entries   QueryRow[]
 *   loading   boolean
 *   value     string        — the currently-bound query_id, for the checkmark
 *   strictEnv string|null   — active protected env key, for the badge title
 *   onSelect  (id)=>void
 *   listId    string        — id prefix for aria-activedescendant wiring
 */
export function QueryBrowser({ entries, loading, value, strictEnv, onSelect, listId = 'query-browser' }) {
  const [search, setSearch] = useState('')
  const [activeIdx, setActiveIdx] = useState(0)
  const inputRef = useRef(null)
  const listRef = useRef(null)

  useEffect(() => { inputRef.current?.focus() }, [])

  const filtered = useMemo(() => {
    const sorted = [...entries].sort((a, b) => (a.name || a.id).localeCompare(b.name || b.id))
    const q = search.trim().toLowerCase()
    if (!q) return sorted
    return sorted.filter(e =>
      (e.name ?? '').toLowerCase().includes(q) ||
      e.id.toLowerCase().includes(q) ||
      (e.connectorName ?? '').toLowerCase().includes(q)
    )
  }, [entries, search])

  // Keep the active index in range whenever the filtered list changes.
  useEffect(() => { setActiveIdx(i => Math.min(i, Math.max(filtered.length - 1, 0))) }, [filtered.length])

  useEffect(() => {
    const el = listRef.current?.querySelector(`[data-idx="${activeIdx}"]`)
    el?.scrollIntoView({ block: 'nearest' })
  }, [activeIdx])

  const handleKeyDown = (e) => {
    if (filtered.length === 0) return
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      setActiveIdx(i => (i + 1) % filtered.length)
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      setActiveIdx(i => (i - 1 + filtered.length) % filtered.length)
    } else if (e.key === 'Enter') {
      e.preventDefault()
      const row = filtered[activeIdx]
      if (row) onSelect(row.id)
    }
    // Escape is intentionally left to bubble — the popover closes it.
  }

  return (
    <div className="flex flex-col min-h-0">
      <div className="p-2 border-b border-border shrink-0">
        <div className="relative">
          <Search size={13} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-muted pointer-events-none" aria-hidden="true" />
          <input
            ref={inputRef}
            type="text"
            role="combobox"
            aria-expanded="true"
            aria-controls={listId}
            aria-activedescendant={filtered[activeIdx] ? `${listId}-opt-${filtered[activeIdx].id}` : undefined}
            aria-label="Search queries by name or slug"
            placeholder="Search queries by name or slug…"
            value={search}
            onChange={e => setSearch(e.target.value)}
            onKeyDown={handleKeyDown}
            className="w-full h-8 pl-8 pr-7 text-xs border border-border rounded-lg bg-surface text-fg placeholder:text-muted/60 focus:outline-none focus:ring-2 focus:ring-ring/60 focus:border-ring/40"
          />
          {search && (
            <button
              type="button"
              onClick={() => setSearch('')}
              aria-label="Clear search"
              className="absolute right-1.5 top-1/2 -translate-y-1/2 w-5 h-5 flex items-center justify-center rounded text-muted hover:text-fg hover:bg-surface-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              <X size={11} />
            </button>
          )}
        </div>
      </div>

      <div
        id={listId}
        ref={listRef}
        role="listbox"
        aria-label="Queries"
        className="overflow-y-auto max-h-[280px] p-1.5 space-y-0.5"
      >
        {loading && entries.length === 0 && (
          <div className="space-y-1 py-1" aria-hidden="true">
            <ListRowSkeleton />
            <ListRowSkeleton />
          </div>
        )}

        {!loading && filtered.length === 0 && (
          <EmptyState
            compact
            icon={<FileCode2 size={18} />}
            title={search ? 'No matching queries' : 'No registered queries yet'}
            description={search
              ? `Nothing matches "${search}".`
              : 'Register a query in the query editor, then bind it here.'}
          />
        )}

        {filtered.map((row, idx) => {
          const isActive = idx === activeIdx
          const isSelected = row.id === value
          return (
            <button
              key={row.id}
              type="button"
              id={`${listId}-opt-${row.id}`}
              data-idx={idx}
              role="option"
              aria-selected={isSelected}
              onMouseEnter={() => setActiveIdx(idx)}
              onClick={() => onSelect(row.id)}
              className={[
                'w-full text-left px-2 py-1.5 rounded-lg transition-colors duration-100',
                'focus-visible:outline-none',
                isActive ? 'bg-surface-2' : 'hover:bg-surface-2/70',
                isSelected ? 'ring-1 ring-inset ring-primary/40' : '',
              ].join(' ')}
            >
              <div className="flex items-start gap-1.5 min-w-0">
                <FileCode2 size={12} className={`shrink-0 mt-0.5 ${isSelected ? 'text-primary' : 'text-muted'}`} />
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-1">
                    <span className="text-xs font-medium text-fg truncate max-w-full">
                      {row.name || row.id}
                    </span>
                    {row.builtin && <Badge size="sm" variant="info">Built-in</Badge>}
                    {!row.known && (
                      <Badge size="sm" variant="default" title="Not found in the current registry — the binding is kept as-is.">
                        Custom ID
                      </Badge>
                    )}
                    {row.notInActiveEnv && (
                      <Badge
                        size="sm"
                        variant="warning"
                        title={`No version of this query is pinned to ${strictEnv} — promote one to make it visible there.`}
                      >
                        <AlertTriangle size={8} />
                        not in {strictEnv}
                      </Badge>
                    )}
                  </div>
                  <div className="flex items-center gap-1.5 mt-0.5">
                    <span className="text-[10px] font-mono text-muted/80 truncate">{row.id}</span>
                    {row.connectorName && (
                      <span className="text-[10px] text-muted/60 truncate shrink-0">· {row.connectorName}</span>
                    )}
                  </div>
                </div>
              </div>
              {isActive && <ColumnsPreview queryId={row.id} />}
            </button>
          )
        })}
      </div>
    </div>
  )
}
