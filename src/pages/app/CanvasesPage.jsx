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
import { useAsyncLoad } from '../../hooks/useAsyncLoad.js'
import { Link, useNavigate } from 'react-router-dom'
import {
  ExternalLink,
  FileCode2,
  Loader2,
  MoreVertical,
  Pencil,
  Plus,
  Search,
  Trash2,
  X,
} from 'lucide-react'
import * as api from '../../lib/api.js'
import { toast } from '../../components/ui/Toast.jsx'
import { useCanWrite } from '../../contexts/OrgContext.jsx'
import { useProject } from '../../contexts/ProjectContext.jsx'
import {
  PageRoot,
  PageHeader,
  Toolbar,
  SearchBar,
  SortMenu,
  ViewToggle,
  CardGrid,
  ListWrap,
  ListHeader,
  ListHeaderLabel,
  ListRow,
  ErrorState,
  CardSkeleton,
  DropdownMenu,
  DropdownItem,
  DropdownDivider,
} from '../../components/app/PageShell.jsx'

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function canvasMeta(config) {
  if (!config) return 'Canvas'
  const doc = config.doc
  if (!doc) return 'Empty canvas'
  const bindCount = Object.keys(doc.bindings ?? {}).length
  return bindCount === 0 ? 'No bindings' : `${bindCount} binding${bindCount === 1 ? '' : 's'}`
}

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

const SORT_OPTIONS = [
  { value: 'recent', label: 'Recent' },
  { value: 'name',   label: 'Name' },
]

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function ThumbnailPattern() {
  return (
    <svg
      className="absolute inset-0 w-full h-full opacity-[0.12]"
      viewBox="0 0 120 60"
      preserveAspectRatio="xMidYMid slice"
      aria-hidden="true"
    >
      <rect x="10" y="8"  width="40" height="4"  rx="2" fill="white" />
      <rect x="10" y="16" width="60" height="3"  rx="1.5" fill="white" />
      <rect x="10" y="23" width="50" height="3"  rx="1.5" fill="white" />
      <rect x="10" y="30" width="70" height="3"  rx="1.5" fill="white" />
      <rect x="10" y="37" width="45" height="3"  rx="1.5" fill="white" />
      <rect x="10" y="44" width="55" height="3"  rx="1.5" fill="white" />
      <rect x="75" y="20" width="36" height="28" rx="3" fill="white" opacity="0.4" />
      <rect x="78" y="30" width="8"  height="12" rx="1" fill="white" />
      <rect x="89" y="24" width="8"  height="18" rx="1" fill="white" />
      <rect x="100" y="28" width="8" height="14" rx="1" fill="white" />
    </svg>
  )
}

function CardThumbnail({ canvas }) {
  return (
    <div
      className="relative w-full h-[6.5rem] overflow-hidden flex items-center justify-center"
      style={{ background: cardGradient(canvas.id) }}
    >
      <ThumbnailPattern />
      <div className="relative z-10 flex items-center justify-center w-9 h-9 rounded-xl bg-white/15 backdrop-blur-sm">
        <FileCode2 size={18} className="text-white" aria-hidden="true" />
      </div>
    </div>
  )
}

function CardMenu({ onEdit, onDelete }) {
  const [open, setOpen] = useState(false)
  const ref = useRef(null)

  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        onClick={(e) => { e.preventDefault(); e.stopPropagation(); setOpen(v => !v) }}
        className="
          flex items-center justify-center w-7 h-7 rounded-lg
          text-muted hover:text-fg hover:bg-surface-2
          transition-colors duration-100
          focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring
        "
        aria-label="Canvas options"
        aria-expanded={open}
        aria-haspopup="menu"
      >
        <MoreVertical size={14} aria-hidden="true" />
      </button>

      <DropdownMenu open={open} onClose={() => setOpen(false)}>
        <DropdownItem icon={Pencil} onClick={() => { setOpen(false); onEdit() }}>Edit</DropdownItem>
        <DropdownDivider />
        <DropdownItem icon={Trash2} danger onClick={() => { setOpen(false); onDelete() }}>Delete</DropdownItem>
      </DropdownMenu>
    </div>
  )
}

function DeleteDialog({ canvas, onConfirm, onCancel, busy }) {
  const dialogRef = useRef(null)

  // Escape to close (ignored while a delete is in flight)
  useEffect(() => {
    const handler = (e) => {
      if (e.key === 'Escape' && !busy) onCancel()
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [busy, onCancel])

  // Focus the dialog panel on open
  useEffect(() => {
    const t = setTimeout(() => dialogRef.current?.focus(), 50)
    return () => clearTimeout(t)
  }, [])

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4"
      role="dialog"
      aria-modal="true"
      aria-labelledby="canvas-delete-dlg-title"
    >
      <div className="absolute inset-0 bg-black/50 backdrop-blur-sm" onClick={busy ? undefined : onCancel} />
      <div
        ref={dialogRef}
        tabIndex={-1}
        className="relative z-10 w-full max-w-sm rounded-2xl border border-border bg-surface p-6 shadow-nubi-xl nubi-animate-scale-in outline-none"
      >
        <button
          onClick={onCancel}
          disabled={busy}
          className="absolute top-4 right-4 text-muted hover:text-fg transition-colors p-1 rounded-lg hover:bg-surface-2 disabled:opacity-50"
          aria-label="Cancel"
        >
          <X size={16} />
        </button>
        <div className="flex items-center justify-center w-11 h-11 rounded-xl bg-danger-bg mb-4">
          <Trash2 size={20} className="text-danger" />
        </div>
        <h2 id="canvas-delete-dlg-title" className="font-display font-semibold text-lg text-fg mb-1">
          Delete canvas?
        </h2>
        <p className="text-muted text-sm mb-6 leading-relaxed">
          <span className="font-medium text-fg">&ldquo;{canvas.name}&rdquo;</span> will be
          permanently deleted. This cannot be undone.
        </p>
        <div className="flex gap-2.5">
          <button
            onClick={onCancel}
            disabled={busy}
            className="flex-1 h-9 rounded-xl border border-border bg-surface-2 text-sm font-medium text-fg hover:bg-surface-2/80 transition-colors disabled:opacity-50"
          >
            Cancel
          </button>
          <button
            onClick={onConfirm}
            disabled={busy}
            className="flex-1 h-9 rounded-xl bg-danger text-white text-sm font-medium hover:opacity-90 transition-opacity disabled:opacity-50 flex items-center justify-center gap-2"
          >
            {busy && <Loader2 size={13} className="animate-spin" />}
            {busy ? 'Deleting…' : 'Delete'}
          </button>
        </div>
      </div>
    </div>
  )
}

function CanvasCard({ canvas, onDeleted, canWrite }) {
  const navigate = useNavigate()
  const [confirmDelete, setConfirmDelete] = useState(false)
  const [deleteBusy, setDeleteBusy] = useState(false)

  async function handleDelete() {
    setDeleteBusy(true)
    try {
      await api.del(`/canvases/${canvas.id}`)
      onDeleted(canvas.id)
      setConfirmDelete(false)
    } catch (err) {
      console.error('Delete failed:', err)
      toast.error(err?.message || 'Failed to delete canvas. Please try again.')
    } finally {
      setDeleteBusy(false)
    }
  }

  return (
    <>
      <article className="nubi-resource-card group">
        <Link to={`/c/${canvas.id}`} className="block" tabIndex={-1} aria-hidden="true">
          <CardThumbnail canvas={canvas} />
        </Link>

        <div className="nubi-resource-card-body">
          <div className="flex items-start justify-between gap-2 min-w-0">
            <div className="min-w-0 flex-1">
              <Link
                to={`/c/${canvas.id}`}
                className="block font-display font-semibold text-sm text-fg hover:text-primary transition-colors truncate leading-snug"
              >
                {canvas.name || 'Untitled canvas'}
              </Link>
              <p className="text-xs text-muted mt-0.5 leading-tight">{canvasMeta(canvas.config)}</p>
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
              className="flex items-center gap-1.5 flex-1 justify-center h-8 rounded-xl bg-primary text-primary-fg text-xs font-medium hover:opacity-90 transition-opacity focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              <ExternalLink size={12} aria-hidden="true" />
              Open
            </Link>
            {canWrite && (
              <Link
                to={`/canvas/${canvas.id}`}
                className="flex items-center gap-1.5 flex-1 justify-center h-8 rounded-xl border border-border bg-surface-2 text-fg text-xs font-medium hover:bg-surface-2/60 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                <Pencil size={12} aria-hidden="true" />
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
    <ListRow data-testid="canvas-list-row">
      <span
        className="hidden sm:flex items-center justify-center w-8 h-8 rounded-lg shrink-0"
        style={{ background: cardGradient(canvas.id) }}
        aria-hidden="true"
      >
        <FileCode2 size={13} className="text-white" />
      </span>

      <div className="flex-1 min-w-0">
        <Link
          to={`/c/${canvas.id}`}
          className="block text-sm font-medium text-fg hover:text-primary transition-colors truncate"
        >
          {canvas.name || 'Untitled canvas'}
        </Link>
        <p className="text-xs text-muted truncate mt-0.5">{canvasMeta(canvas.config)}</p>
      </div>

      {updatedLabel && (
        <span className="hidden md:block text-xs text-muted shrink-0 tabular-nums">
          {updatedLabel}
        </span>
      )}

      <div className="flex items-center gap-0.5 shrink-0">
        <Link
          to={`/c/${canvas.id}`}
          title="Open"
          aria-label={`Open ${canvas.name || 'Untitled canvas'}`}
          className="flex items-center justify-center w-7 h-7 rounded-lg text-muted hover:text-fg hover:bg-surface-2 transition-colors"
        >
          <ExternalLink size={13} />
        </Link>
        {canWrite && (
          <Link
            to={`/canvas/${canvas.id}`}
            title="Edit"
            aria-label={`Edit ${canvas.name || 'Untitled canvas'}`}
            className="flex items-center justify-center w-7 h-7 rounded-lg text-muted hover:text-fg hover:bg-surface-2 transition-colors"
          >
            <Pencil size={13} />
          </Link>
        )}
      </div>
    </ListRow>
  )
}

// ---------------------------------------------------------------------------
// Empty state
// ---------------------------------------------------------------------------

function EmptyCanvases({ hasFilter, onClearFilter, canWrite }) {
  if (hasFilter) {
    return (
      <div className="flex flex-col items-center justify-center py-20 px-6 text-center">
        <div className="flex items-center justify-center w-12 h-12 rounded-2xl bg-surface-2 mb-4">
          <Search size={20} className="text-muted" />
        </div>
        <p className="font-display font-semibold text-base text-fg mb-1.5">No results</p>
        <p className="text-muted text-sm max-w-xs leading-relaxed mb-5">
          No canvases match your search. Try a different term.
        </p>
        <button
          onClick={onClearFilter}
          className="h-9 px-4 rounded-xl border border-border bg-surface-2 text-sm text-fg font-medium hover:bg-surface-2/60 transition-colors"
        >
          Clear search
        </button>
      </div>
    )
  }

  return (
    <div className="flex flex-col items-center justify-center py-20 px-6 text-center">
      <div className="relative mb-5">
        <div
          className="flex items-center justify-center w-16 h-16 rounded-2xl"
          style={{ background: 'linear-gradient(135deg, #2456a6, #17b3a3, #1b2363)' }}
        >
          <FileCode2 size={28} className="text-white" />
        </div>
        {canWrite && (
          <div className="absolute -top-1 -right-1 flex items-center justify-center w-6 h-6 rounded-full bg-accent text-white shadow-nubi-md">
            <Plus size={12} />
          </div>
        )}
      </div>

      <h2 className="font-display font-semibold text-xl text-fg mb-1.5">
        {canWrite ? 'Create your first canvas' : 'No canvases yet'}
      </h2>
      <p className="text-muted text-sm max-w-sm leading-relaxed mb-6">
        {canWrite
          ? 'Canvases are freeform HTML documents with live data bindings. Write HTML directly and bind elements to queries, metrics, or API connectors.'
          : 'There are no canvases to view yet. You have read-only access in this organisation.'}
      </p>

      {canWrite && (
        <Link
          to="/canvas"
          className="inline-flex items-center justify-center gap-2 h-10 px-5 rounded-xl bg-primary text-primary-fg text-sm font-semibold hover:opacity-90 transition-opacity focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          <Plus size={15} />
          New canvas
        </Link>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------

export default function CanvasesPage() {
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

  const { data: canvasesData, loading, error, reload: reloadCanvases } = useAsyncLoad(async () => {
    const data = await api.get('/canvases')
    return Array.isArray(data) ? data : Array.isArray(data?.canvases) ? data.canvases : []
  }, [projectId])
  const canvases = canvasesData ?? []

  const handleDeleted = useCallback((_id) => {
    reloadCanvases()
  }, [reloadCanvases])

  const filtered = canvases
    .filter(c => c.name?.toLowerCase().includes(search.toLowerCase()))
    .sort((a, b) => {
      if (sort === 'name') return (a.name ?? '').localeCompare(b.name ?? '')
      return 0
    })

  const subtitle = loading
    ? undefined
    : error
    ? undefined
    : canvases.length === 0
    ? 'No canvases yet'
    : `${canvases.length} canvas${canvases.length === 1 ? '' : 'es'}`

  return (
    <PageRoot>
      {/* Page header */}
      <PageHeader title="Canvases" subtitle={subtitle}>
        {canWrite ? (
          <Link
            to="/canvas"
            className="inline-flex items-center justify-center gap-1.5 h-9 px-4 rounded-xl bg-primary text-primary-fg text-sm font-semibold hover:opacity-90 transition-opacity focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            <Plus size={15} aria-hidden="true" />
            New canvas
          </Link>
        ) : (
          <span className="inline-flex items-center h-9 px-3 rounded-xl text-xs font-medium text-muted bg-surface-2 border border-border">
            Read-only
          </span>
        )}
      </PageHeader>

      {/* Search + sort bar */}
      {!loading && !error && canvases.length > 0 && (
        <Toolbar>
          <SearchBar
            value={search}
            onChange={setSearch}
            placeholder="Search canvases…"
          />
          <SortMenu value={sort} onChange={setSort} options={SORT_OPTIONS} />
          <ViewToggle value={viewMode} onChange={changeViewMode} />
        </Toolbar>
      )}

      {/* Loading skeletons */}
      {loading && (
        <CardGrid>
          {Array.from({ length: 6 }).map((_, i) => (
            <CardSkeleton key={i} />
          ))}
        </CardGrid>
      )}

      {/* Error */}
      {!loading && error && (
        <ErrorState
          icon={<FileCode2 size={22} />}
          message={error?.message ?? String(error)}
          onRetry={reloadCanvases}
        />
      )}

      {/* Empty state */}
      {!loading && !error && filtered.length === 0 && (
        <EmptyCanvases
          hasFilter={search.length > 0}
          onClearFilter={() => setSearch('')}
          canWrite={canWrite}
        />
      )}

      {/* Canvas grid */}
      {!loading && !error && filtered.length > 0 && viewMode === 'grid' && (
        <CardGrid>
          {filtered.map(canvas => (
            <CanvasCard
              key={canvas.id}
              canvas={canvas}
              onDeleted={handleDeleted}
              canWrite={canWrite}
            />
          ))}
        </CardGrid>
      )}

      {/* Canvas list */}
      {!loading && !error && filtered.length > 0 && viewMode === 'list' && (
        <ListWrap>
          <ListHeader>
            <ListHeaderLabel>
              {filtered.length} canvas{filtered.length === 1 ? '' : 'es'}
              {search && ' (filtered)'}
            </ListHeaderLabel>
          </ListHeader>

          {filtered.map(canvas => (
            <CanvasListRow
              key={canvas.id}
              canvas={canvas}
              canWrite={canWrite}
            />
          ))}
        </ListWrap>
      )}
    </PageRoot>
  )
}
