/**
 * CanvasesPage — /canvases
 *
 * Lists all canvases from GET /api/v1/canvases.
 * Canvas shape: { id, name, config: { doc?: CanvasDoc } }
 *
 * Mirrors DashboardsPage.jsx in structure and behaviour:
 *   - Header with "New canvas" CTA → /canvas (editor)
 *   - Search by name + sort (recent / name)
 *   - Grid ↔ List view toggle
 *   - Per-card actions: Open, Edit, Delete (confirm dialog)
 *   - Loading skeleton, error state, empty state
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import {
  ChevronDown,
  ExternalLink,
  FileCode2,
  LayoutGrid,
  List,
  Loader2,
  MoreVertical,
  Pencil,
  Plus,
  Search,
  Trash2,
  X,
} from 'lucide-react'
import * as api from '../../lib/api.js'
import { useCanWrite } from '../../contexts/OrgContext.jsx'
import { useProject } from '../../contexts/ProjectContext.jsx'

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Derive a human-readable meta label from canvas config. */
function canvasMeta(config) {
  if (!config) return 'Canvas'
  const doc = config.doc
  if (!doc) return 'Empty canvas'
  const bindCount = Object.keys(doc.bindings ?? {}).length
  return bindCount === 0 ? 'No bindings' : `${bindCount} binding${bindCount === 1 ? '' : 's'}`
}

/** Pick a deterministic gradient for a canvas card thumbnail. */
const GRADIENTS = [
  'linear-gradient(135deg, #2456a6 0%, #17b3a3 60%, #1b2363 100%)',
  'linear-gradient(135deg, #17b3a3 0%, #1b2363 50%, #2456a6 100%)',
  'linear-gradient(135deg, #1b2363 0%, #2456a6 40%, #17b3a3 100%)',
  'linear-gradient(135deg, #0f9e90 0%, #1b2363 100%)',
  'linear-gradient(135deg, #2456a6 0%, #17b3a3 100%)',
]

function cardGradient(id) {
  let h = 0
  for (let i = 0; i < (id?.length ?? 0); i++) h = (h * 31 + id.charCodeAt(i)) >>> 0
  return GRADIENTS[h % GRADIENTS.length]
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

/** Mini SVG pattern for canvas card thumbnail. */
function ThumbnailPattern() {
  return (
    <svg
      className="absolute inset-0 w-full h-full opacity-10"
      viewBox="0 0 120 60"
      preserveAspectRatio="xMidYMid slice"
      aria-hidden="true"
    >
      {/* HTML document lines */}
      <rect x="10" y="8" width="40" height="4" rx="2" fill="white" />
      <rect x="10" y="16" width="60" height="3" rx="1.5" fill="white" />
      <rect x="10" y="23" width="50" height="3" rx="1.5" fill="white" />
      <rect x="10" y="30" width="70" height="3" rx="1.5" fill="white" />
      <rect x="10" y="37" width="45" height="3" rx="1.5" fill="white" />
      <rect x="10" y="44" width="55" height="3" rx="1.5" fill="white" />
      {/* Widget placeholder */}
      <rect x="75" y="20" width="36" height="28" rx="3" fill="white" opacity="0.4" />
      <rect x="78" y="30" width="8" height="12" rx="1" fill="white" />
      <rect x="89" y="24" width="8" height="18" rx="1" fill="white" />
      <rect x="100" y="28" width="8" height="14" rx="1" fill="white" />
    </svg>
  )
}

/** Card thumbnail area. */
function CardThumbnail({ canvas }) {
  return (
    <div
      className="relative w-full h-28 rounded-t-xl overflow-hidden flex items-center justify-center"
      style={{ background: cardGradient(canvas.id) }}
    >
      <ThumbnailPattern />
      <div className="relative z-10 flex items-center justify-center w-10 h-10 rounded-xl bg-white/15 backdrop-blur-sm">
        <FileCode2 size={20} className="text-white" />
      </div>
    </div>
  )
}

/** Three-dot dropdown menu on a card. */
function CardMenu({ onEdit, onDelete }) {
  const [open, setOpen] = useState(false)
  const ref = useRef(null)

  useEffect(() => {
    if (!open) return
    function handle(e) {
      if (ref.current && !ref.current.contains(e.target)) setOpen(false)
    }
    document.addEventListener('mousedown', handle)
    return () => document.removeEventListener('mousedown', handle)
  }, [open])

  return (
    <div ref={ref} className="relative">
      <button
        onClick={(e) => { e.preventDefault(); e.stopPropagation(); setOpen(v => !v) }}
        className="flex items-center justify-center w-8 h-8 rounded-lg text-muted hover:text-fg hover:bg-surface-2 transition-colors"
        aria-label="Canvas options"
      >
        <MoreVertical size={16} />
      </button>

      {open && (
        <div className="absolute right-0 top-10 z-20 w-44 rounded-xl border border-border bg-surface shadow-lg py-1">
          <button
            onClick={(e) => { e.stopPropagation(); setOpen(false); onEdit() }}
            className="flex items-center gap-2.5 w-full px-4 py-2.5 text-sm text-fg hover:bg-surface-2 transition-colors"
          >
            <Pencil size={14} className="text-muted" />
            Edit
          </button>
          <button
            onClick={(e) => { e.stopPropagation(); setOpen(false); onDelete() }}
            className="flex items-center gap-2.5 w-full px-4 py-2.5 text-sm text-red-500 hover:bg-surface-2 transition-colors"
          >
            <Trash2 size={14} />
            Delete
          </button>
        </div>
      )}
    </div>
  )
}

/** Confirm delete dialog. */
function DeleteDialog({ canvas, onConfirm, onCancel, busy }) {
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4"
      role="dialog"
      aria-modal="true"
      aria-labelledby="canvas-delete-dlg-title"
    >
      <div className="absolute inset-0 bg-black/50 backdrop-blur-sm" onClick={onCancel} />
      <div className="relative z-10 w-full max-w-sm rounded-2xl border border-border bg-surface p-6 shadow-2xl">
        <button
          onClick={onCancel}
          className="absolute top-4 right-4 text-muted hover:text-fg transition-colors"
          aria-label="Cancel"
        >
          <X size={18} />
        </button>
        <div className="flex items-center justify-center w-12 h-12 rounded-xl bg-red-500/10 mb-4">
          <Trash2 size={22} className="text-red-500" />
        </div>
        <h2 id="canvas-delete-dlg-title" className="font-display font-semibold text-lg text-fg mb-1">
          Delete canvas?
        </h2>
        <p className="text-muted text-sm mb-6 leading-relaxed">
          <span className="font-medium text-fg">&ldquo;{canvas.name}&rdquo;</span> will be
          permanently deleted. This cannot be undone.
        </p>
        <div className="flex gap-3">
          <button
            onClick={onCancel}
            disabled={busy}
            className="flex-1 h-10 rounded-lg border border-border bg-surface-2 text-sm font-medium text-fg hover:bg-surface-2/80 transition-colors disabled:opacity-50"
          >
            Cancel
          </button>
          <button
            onClick={onConfirm}
            disabled={busy}
            className="flex-1 h-10 rounded-lg bg-red-500 text-white text-sm font-medium hover:bg-red-600 transition-colors disabled:opacity-50 flex items-center justify-center gap-2"
          >
            {busy && <Loader2 size={14} className="animate-spin" />}
            Delete
          </button>
        </div>
      </div>
    </div>
  )
}

/** Single canvas card. */
function CanvasCard({ canvas, onDeleted, canWrite }) {
  const navigate = useNavigate()
  const [confirmDelete, setConfirmDelete] = useState(false)
  const [deleteBusy, setDeleteBusy] = useState(false)

  async function handleDelete() {
    setDeleteBusy(true)
    try {
      await api.del(`/canvases/${canvas.id}`)
      onDeleted(canvas.id)
    } catch (err) {
      console.error('Delete failed:', err)
    } finally {
      setDeleteBusy(false)
      setConfirmDelete(false)
    }
  }

  return (
    <>
      <article className="group relative flex flex-col rounded-xl border border-border bg-surface hover:border-primary/40 hover:shadow-md transition-all duration-200 overflow-hidden">
        <Link to={`/c/${canvas.id}`} className="block" tabIndex={-1} aria-hidden="true">
          <CardThumbnail canvas={canvas} />
        </Link>

        <div className="flex flex-col flex-1 px-4 pt-3 pb-4 gap-3">
          <div className="flex items-start justify-between gap-2 min-w-0">
            <div className="min-w-0">
              <Link
                to={`/c/${canvas.id}`}
                className="block font-display font-semibold text-base text-fg hover:text-primary transition-colors truncate leading-snug"
              >
                {canvas.name || 'Untitled canvas'}
              </Link>
              <p className="text-xs text-muted mt-0.5">{canvasMeta(canvas.config)}</p>
            </div>
            {canWrite && (
              <CardMenu
                onEdit={() => navigate(`/canvas/${canvas.id}`)}
                onDelete={() => setConfirmDelete(true)}
              />
            )}
          </div>

          <div className="flex gap-2 mt-auto">
            <Link
              to={`/c/${canvas.id}`}
              className="flex items-center gap-1.5 flex-1 justify-center h-9 rounded-lg bg-primary text-primary-fg text-xs font-medium hover:opacity-90 transition-opacity"
            >
              <ExternalLink size={13} />
              Open
            </Link>
            {canWrite && (
              <Link
                to={`/canvas/${canvas.id}`}
                className="flex items-center gap-1.5 flex-1 justify-center h-9 rounded-lg border border-border bg-surface-2 text-fg text-xs font-medium hover:bg-surface-2/60 transition-colors"
              >
                <Pencil size={13} />
                Edit
              </Link>
            )}
          </div>
        </div>
      </article>

      {confirmDelete && (
        <DeleteDialog
          canvas={canvas}
          onConfirm={handleDelete}
          onCancel={() => setConfirmDelete(false)}
          busy={deleteBusy}
        />
      )}
    </>
  )
}

/** Loading skeleton. */
function SkeletonCard() {
  return (
    <div className="flex flex-col rounded-xl border border-border bg-surface overflow-hidden animate-pulse">
      <div className="h-28 bg-surface-2" />
      <div className="px-4 pt-3 pb-4 flex flex-col gap-3">
        <div className="flex items-start justify-between gap-2">
          <div className="flex-1 space-y-1.5">
            <div className="h-4 w-3/4 rounded bg-surface-2" />
            <div className="h-3 w-1/3 rounded bg-surface-2" />
          </div>
          <div className="h-8 w-8 rounded-lg bg-surface-2" />
        </div>
        <div className="flex gap-2 mt-auto">
          <div className="h-9 flex-1 rounded-lg bg-surface-2" />
          <div className="h-9 flex-1 rounded-lg bg-surface-2" />
        </div>
      </div>
    </div>
  )
}

/** Empty state. */
function EmptyState({ hasFilter, onClearFilter, canWrite }) {
  if (hasFilter) {
    return (
      <div className="flex flex-col items-center justify-center py-24 px-6 text-center">
        <div className="flex items-center justify-center w-14 h-14 rounded-2xl bg-surface-2 mb-4">
          <Search size={24} className="text-muted" />
        </div>
        <h2 className="font-display font-semibold text-xl text-fg mb-2">No results found</h2>
        <p className="text-muted text-sm max-w-xs leading-relaxed mb-6">
          No canvases match your search. Try a different term.
        </p>
        <button
          onClick={onClearFilter}
          className="h-9 px-5 rounded-lg border border-border bg-surface-2 text-sm text-fg font-medium hover:bg-surface-2/60 transition-colors"
        >
          Clear search
        </button>
      </div>
    )
  }

  return (
    <div className="flex flex-col items-center justify-center py-24 px-6 text-center">
      <div className="relative mb-6">
        <div
          className="flex items-center justify-center w-20 h-20 rounded-2xl"
          style={{ background: 'linear-gradient(135deg, #2456a6, #17b3a3, #1b2363)' }}
        >
          <FileCode2 size={36} className="text-white" />
        </div>
        {canWrite && (
          <div className="absolute -top-1 -right-1 flex items-center justify-center w-7 h-7 rounded-full bg-accent text-white shadow-md">
            <Plus size={14} />
          </div>
        )}
      </div>

      <h2 className="font-display font-semibold text-2xl text-fg mb-2">
        {canWrite ? 'Create your first canvas' : 'No canvases yet'}
      </h2>
      <p className="text-muted text-sm max-w-sm leading-relaxed mb-8">
        {canWrite
          ? 'Canvases are freeform HTML documents with live data bindings. Write HTML directly and bind elements to queries, metrics, or API connectors.'
          : 'There are no canvases to view yet. You have read-only access in this organisation.'}
      </p>

      {canWrite && (
        <Link
          to="/canvas"
          className="inline-flex items-center justify-center gap-2 h-11 px-6 rounded-xl bg-primary text-primary-fg text-sm font-semibold hover:opacity-90 transition-opacity"
        >
          <Plus size={16} />
          New canvas
        </Link>
      )}
    </div>
  )
}

/** Sort options dropdown. */
function SortMenu({ sort, onChange }) {
  const [open, setOpen] = useState(false)
  const ref = useRef(null)

  useEffect(() => {
    if (!open) return
    function handle(e) {
      if (ref.current && !ref.current.contains(e.target)) setOpen(false)
    }
    document.addEventListener('mousedown', handle)
    return () => document.removeEventListener('mousedown', handle)
  }, [open])

  const label = sort === 'name' ? 'Name' : 'Recent'

  return (
    <div ref={ref} className="relative">
      <button
        onClick={() => setOpen(v => !v)}
        className="inline-flex items-center gap-2 h-10 px-4 rounded-lg border border-border bg-surface-2 text-sm text-fg font-medium hover:bg-surface-2/60 transition-colors"
      >
        {label}
        <ChevronDown size={14} className={`text-muted transition-transform ${open ? 'rotate-180' : ''}`} />
      </button>

      {open && (
        <div className="absolute right-0 top-12 z-20 w-36 rounded-xl border border-border bg-surface shadow-lg py-1">
          {[
            { value: 'recent', label: 'Recent' },
            { value: 'name', label: 'Name' },
          ].map(opt => (
            <button
              key={opt.value}
              onClick={() => { onChange(opt.value); setOpen(false) }}
              className={`flex items-center gap-2 w-full px-4 py-2.5 text-sm transition-colors hover:bg-surface-2 ${
                sort === opt.value ? 'text-primary font-medium' : 'text-fg'
              }`}
            >
              {opt.label}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

/** Grid ↔ List view switcher. */
function ViewToggle({ view, onChange }) {
  return (
    <div className="flex h-10 rounded-lg border border-border overflow-hidden shrink-0">
      {[
        { id: 'grid', Icon: LayoutGrid, title: 'Grid view' },
        { id: 'list', Icon: List, title: 'List view' },
      ].map((v, i) => (
        <button
          key={v.id}
          onClick={() => onChange(v.id)}
          title={v.title}
          aria-label={v.title}
          aria-pressed={view === v.id}
          className={[
            'flex items-center justify-center w-10 transition-colors',
            i > 0 ? 'border-l border-border' : '',
            view === v.id
              ? 'bg-primary text-primary-fg'
              : 'bg-surface text-muted hover:text-fg hover:bg-surface-2',
          ].join(' ')}
        >
          <v.Icon size={15} />
        </button>
      ))}
    </div>
  )
}

/** Compact list-mode row. */
function CanvasListRow({ canvas, canWrite }) {
  const updated = canvas.updated_at ?? canvas.created_at
  let updatedLabel = null
  if (updated) {
    const d = new Date(updated)
    if (!Number.isNaN(d.getTime())) {
      updatedLabel = d.toLocaleDateString(undefined, {
        year: 'numeric', month: 'short', day: 'numeric',
      })
    }
  }

  return (
    <li
      data-testid="canvas-list-row"
      className="flex items-center gap-3 px-3 sm:px-4 py-2.5 transition-colors hover:bg-surface-2/60"
    >
      <span
        className="hidden sm:flex items-center justify-center w-8 h-8 rounded-lg shrink-0"
        style={{ background: cardGradient(canvas.id) }}
        aria-hidden="true"
      >
        <FileCode2 size={14} className="text-white" />
      </span>

      <div className="flex-1 min-w-0">
        <Link
          to={`/c/${canvas.id}`}
          className="block text-sm font-medium text-fg hover:text-primary transition-colors truncate"
        >
          {canvas.name || 'Untitled canvas'}
        </Link>
        <p className="text-xs text-muted truncate">{canvasMeta(canvas.config)}</p>
      </div>

      {updatedLabel && (
        <span className="hidden md:block text-xs text-muted shrink-0 tabular-nums">
          {updatedLabel}
        </span>
      )}

      <div className="flex items-center gap-1 shrink-0">
        <Link
          to={`/c/${canvas.id}`}
          title="Open"
          aria-label={`Open ${canvas.name || 'Untitled canvas'}`}
          className="flex items-center justify-center w-8 h-8 rounded-lg text-muted hover:text-fg hover:bg-surface-2 transition-colors"
        >
          <ExternalLink size={14} />
        </Link>
        {canWrite && (
          <Link
            to={`/canvas/${canvas.id}`}
            title="Edit"
            aria-label={`Edit ${canvas.name || 'Untitled canvas'}`}
            className="flex items-center justify-center w-8 h-8 rounded-lg text-muted hover:text-fg hover:bg-surface-2 transition-colors"
          >
            <Pencil size={14} />
          </Link>
        )}
      </div>
    </li>
  )
}

// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------

export default function CanvasesPage() {
  const [canvases, setCanvases] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [search, setSearch] = useState('')
  const [sort, setSort] = useState('recent')

  const [viewMode, setViewMode] = useState(() => {
    try {
      return localStorage.getItem('nubi-canvases-view') === 'list' ? 'list' : 'grid'
    } catch {
      return 'grid'
    }
  })
  const changeViewMode = useCallback((v) => {
    setViewMode(v)
    try { localStorage.setItem('nubi-canvases-view', v) } catch { /* private mode */ }
  }, [])

  const { activeProject } = useProject()
  const projectId = activeProject?.id
  const canWrite = useCanWrite()

  const fetchCanvases = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const data = await api.get('/canvases')
      const list = Array.isArray(data)
        ? data
        : Array.isArray(data?.canvases)
        ? data.canvases
        : []
      setCanvases(list)
    } catch (err) {
      setError(err.message ?? 'Failed to load canvases')
    } finally {
      setLoading(false)
    }
  // projectId change triggers a refetch — the api.js client sends X-Project-Id
  // automatically so the dep is intentional even though it's not in the body.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId])

  useEffect(() => { fetchCanvases() }, [fetchCanvases])

  const handleDeleted = useCallback((id) => {
    setCanvases(prev => prev.filter(c => c.id !== id))
  }, [])

  const filtered = canvases
    .filter(c => c.name?.toLowerCase().includes(search.toLowerCase()))
    .sort((a, b) => {
      if (sort === 'name') return (a.name ?? '').localeCompare(b.name ?? '')
      return 0
    })

  return (
    <div className="min-h-full px-4 sm:px-6 lg:px-8 py-6">
      {/* Page header */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4 mb-6">
        <div>
          <h1 className="font-display font-semibold text-2xl text-fg leading-tight">
            Canvases
          </h1>
          {!loading && !error && (
            <p className="text-muted text-sm mt-0.5">
              {canvases.length === 0
                ? 'No canvases yet'
                : `${canvases.length} canvas${canvases.length === 1 ? '' : 'es'}`}
            </p>
          )}
        </div>

        {canWrite ? (
          <Link
            to="/canvas"
            className="inline-flex items-center justify-center gap-2 h-11 px-5 rounded-xl bg-primary text-primary-fg text-sm font-semibold hover:opacity-90 transition-opacity shrink-0 self-start sm:self-auto"
          >
            <Plus size={16} />
            New canvas
          </Link>
        ) : (
          <span className="inline-flex items-center h-11 px-3 rounded-xl text-xs font-medium text-muted self-start sm:self-auto">
            Read-only
          </span>
        )}
      </div>

      {/* Search + sort bar */}
      {!loading && !error && canvases.length > 0 && (
        <div className="flex flex-col sm:flex-row gap-3 mb-6">
          <div className="relative flex-1">
            <Search
              size={15}
              className="absolute left-3 top-1/2 -translate-y-1/2 text-muted pointer-events-none"
            />
            <input
              type="search"
              value={search}
              onChange={e => setSearch(e.target.value)}
              placeholder="Search canvases…"
              className="w-full h-10 pl-9 pr-4 rounded-lg border border-border bg-surface text-sm text-fg placeholder:text-muted focus:outline-none focus:ring-2 focus:ring-ring transition-shadow"
            />
            {search && (
              <button
                onClick={() => setSearch('')}
                className="absolute right-3 top-1/2 -translate-y-1/2 text-muted hover:text-fg transition-colors"
                aria-label="Clear search"
              >
                <X size={14} />
              </button>
            )}
          </div>
          <SortMenu sort={sort} onChange={setSort} />
          <ViewToggle view={viewMode} onChange={changeViewMode} />
        </div>
      )}

      {/* Loading skeletons */}
      {loading && (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
          {Array.from({ length: 6 }).map((_, i) => (
            <SkeletonCard key={i} />
          ))}
        </div>
      )}

      {/* Error */}
      {!loading && error && (
        <div className="flex flex-col items-center justify-center py-24 px-6 text-center">
          <div className="flex items-center justify-center w-14 h-14 rounded-2xl bg-red-500/10 mb-4">
            <FileCode2 size={24} className="text-red-500" />
          </div>
          <h2 className="font-display font-semibold text-xl text-fg mb-2">
            Something went wrong
          </h2>
          <p className="text-muted text-sm max-w-xs leading-relaxed mb-6">{error}</p>
          <button
            onClick={fetchCanvases}
            className="inline-flex items-center gap-2 h-10 px-5 rounded-lg bg-primary text-primary-fg text-sm font-medium hover:opacity-90 transition-opacity"
          >
            Try again
          </button>
        </div>
      )}

      {/* Empty state */}
      {!loading && !error && filtered.length === 0 && (
        <EmptyState
          hasFilter={search.length > 0}
          onClearFilter={() => setSearch('')}
          canWrite={canWrite}
        />
      )}

      {/* Canvas grid */}
      {!loading && !error && filtered.length > 0 && viewMode === 'grid' && (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
          {filtered.map(canvas => (
            <CanvasCard
              key={canvas.id}
              canvas={canvas}
              onDeleted={handleDeleted}
              canWrite={canWrite}
            />
          ))}
        </div>
      )}

      {/* Canvas list */}
      {!loading && !error && filtered.length > 0 && viewMode === 'list' && (
        <div className="rounded-xl border border-border bg-surface overflow-hidden">
          <div className="flex items-center gap-3 px-3 sm:px-4 py-2 border-b border-border bg-surface-2/40">
            <span className="flex-1 text-xs font-semibold uppercase tracking-wider text-muted">
              {filtered.length} canvas{filtered.length === 1 ? '' : 'es'}
              {search && ' (filtered)'}
            </span>
          </div>
          <ul className="divide-y divide-border">
            {filtered.map(canvas => (
              <CanvasListRow
                key={canvas.id}
                canvas={canvas}
                canWrite={canWrite}
              />
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}
