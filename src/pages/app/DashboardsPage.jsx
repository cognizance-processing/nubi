/**
 * DashboardsPage — /dashboards
 *
 * Lists all boards from GET /api/v1/boards.
 * Board shape: { id, name, config: { spec?: { widgets?: [] }, html?: string } }
 *
 * Features:
 *   - Header with "New dashboard" CTA → /editor
 *   - Search by name + sort (recent / name)
 *   - Responsive grid: 1 col → sm:2 → lg:3
 *   - Per-card actions: Open, Edit, Delete (confirm dialog)
 *   - Loading skeleton, error state, empty state
 *   - Light + dark via semantic tokens
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import {
  BarChart2,
  ChevronDown,
  Code2,
  ExternalLink,
  LayoutDashboard,
  Loader2,
  MoreVertical,
  Pencil,
  Plus,
  Search,
  Sparkles,
  Trash2,
  X,
} from 'lucide-react'
import * as api from '../../lib/api.js'
import { useUi } from '../../contexts/UiContext.jsx'
import { useProject } from '../../contexts/ProjectContext.jsx'
import { useCanWrite } from '../../contexts/OrgContext.jsx'

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Derive a human-readable meta label from board config. */
function boardMeta(config) {
  if (!config) return 'Dashboard'
  if (config.html) return 'HTML board'
  const count = config.spec?.widgets?.length ?? 0
  return count === 1 ? '1 widget' : `${count} widgets`
}

/** Pick a deterministic gradient for a board card thumbnail. */
const GRADIENTS = [
  'linear-gradient(135deg, #1b2363 0%, #2456a6 60%, #17b3a3 100%)',
  'linear-gradient(135deg, #17b3a3 0%, #2456a6 50%, #1b2363 100%)',
  'linear-gradient(135deg, #2456a6 0%, #1b2363 40%, #17b3a3 100%)',
  'linear-gradient(135deg, #0f9e90 0%, #2456a6 100%)',
  'linear-gradient(135deg, #1b2363 0%, #17b3a3 100%)',
]

function cardGradient(id) {
  // stable hash from id
  let h = 0
  for (let i = 0; i < (id?.length ?? 0); i++) h = (h * 31 + id.charCodeAt(i)) >>> 0
  return GRADIENTS[h % GRADIENTS.length]
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

/** Mini SVG pattern overlay for the card thumbnail. */
function ThumbnailPattern() {
  return (
    <svg
      className="absolute inset-0 w-full h-full opacity-10"
      viewBox="0 0 120 60"
      preserveAspectRatio="xMidYMid slice"
      aria-hidden="true"
    >
      {/* bar chart silhouette */}
      <rect x="10" y="30" width="12" height="22" rx="2" fill="white" />
      <rect x="27" y="18" width="12" height="34" rx="2" fill="white" />
      <rect x="44" y="24" width="12" height="28" rx="2" fill="white" />
      <rect x="61" y="12" width="12" height="40" rx="2" fill="white" />
      <rect x="78" y="20" width="12" height="32" rx="2" fill="white" />
      <rect x="95" y="36" width="12" height="16" rx="2" fill="white" />
      {/* sparkline */}
      <polyline
        points="10,52 27,38 44,44 61,28 78,34 95,22 112,30"
        fill="none"
        stroke="white"
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  )
}

/** Card thumbnail area. */
function CardThumbnail({ board }) {
  const isHtml = Boolean(board.config?.html)
  return (
    <div
      className="relative w-full h-28 rounded-t-xl overflow-hidden flex items-center justify-center"
      style={{ background: cardGradient(board.id) }}
    >
      <ThumbnailPattern />
      <div className="relative z-10 flex items-center justify-center w-10 h-10 rounded-xl bg-white/15 backdrop-blur-sm">
        {isHtml
          ? <Code2 size={20} className="text-white" />
          : <BarChart2 size={20} className="text-white" />}
      </div>
    </div>
  )
}

/** Three-dot dropdown menu on a card. */
function CardMenu({ board, onEdit, onDelete }) {
  const [open, setOpen] = useState(false)
  const ref = useRef(null)

  // close on outside click
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
        aria-label="Board options"
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
function DeleteDialog({ board, onConfirm, onCancel, busy }) {
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4"
      role="dialog"
      aria-modal="true"
      aria-labelledby="delete-dlg-title"
    >
      {/* backdrop */}
      <div
        className="absolute inset-0 bg-black/50 backdrop-blur-sm"
        onClick={onCancel}
      />

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

        <h2 id="delete-dlg-title" className="font-display font-semibold text-lg text-fg mb-1">
          Delete dashboard?
        </h2>
        <p className="text-muted text-sm mb-6 leading-relaxed">
          <span className="font-medium text-fg">&ldquo;{board.name}&rdquo;</span> will be
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

/** Single board card. */
function BoardCard({ board, onDeleted, canWrite }) {
  const navigate = useNavigate()
  const [confirmDelete, setConfirmDelete] = useState(false)
  const [deleteBusy, setDeleteBusy] = useState(false)

  async function handleDelete() {
    setDeleteBusy(true)
    try {
      await api.del(`/boards/${board.id}`)
      onDeleted(board.id)
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
        {/* Thumbnail */}
        <Link to={`/d/${board.id}`} className="block" tabIndex={-1} aria-hidden="true">
          <CardThumbnail board={board} />
        </Link>

        {/* Body */}
        <div className="flex flex-col flex-1 px-4 pt-3 pb-4 gap-3">
          <div className="flex items-start justify-between gap-2 min-w-0">
            <div className="min-w-0">
              <Link
                to={`/d/${board.id}`}
                className="block font-display font-semibold text-base text-fg hover:text-primary transition-colors truncate leading-snug"
              >
                {board.name || 'Untitled dashboard'}
              </Link>
              <p className="text-xs text-muted mt-0.5">{boardMeta(board.config)}</p>
            </div>
            {canWrite && (
              <CardMenu
                board={board}
                onEdit={() => navigate(`/editor/${board.id}`)}
                onDelete={() => setConfirmDelete(true)}
              />
            )}
          </div>

          {/* Actions */}
          <div className="flex gap-2 mt-auto">
            <Link
              to={`/d/${board.id}`}
              className="flex items-center gap-1.5 flex-1 justify-center h-9 rounded-lg bg-primary text-primary-fg text-xs font-medium hover:opacity-90 transition-opacity"
            >
              <ExternalLink size={13} />
              Open
            </Link>
            {canWrite && (
              <Link
                to={`/editor/${board.id}`}
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
          board={board}
          onConfirm={handleDelete}
          onCancel={() => setConfirmDelete(false)}
          busy={deleteBusy}
        />
      )}
    </>
  )
}

/** Loading skeleton — matches the card layout. */
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

/** Empty state when no boards exist. */
function EmptyState({ hasFilter, onClearFilter, onAskAI, canWrite }) {
  if (hasFilter) {
    return (
      <div className="flex flex-col items-center justify-center py-24 px-6 text-center">
        <div className="flex items-center justify-center w-14 h-14 rounded-2xl bg-surface-2 mb-4">
          <Search size={24} className="text-muted" />
        </div>
        <h2 className="font-display font-semibold text-xl text-fg mb-2">No results found</h2>
        <p className="text-muted text-sm max-w-xs leading-relaxed mb-6">
          No dashboards match your search. Try a different term.
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
      {/* Illustration */}
      <div className="relative mb-6">
        <div
          className="flex items-center justify-center w-20 h-20 rounded-2xl"
          style={{ background: 'linear-gradient(135deg, #1b2363, #2456a6, #17b3a3)' }}
        >
          <LayoutDashboard size={36} className="text-white" />
        </div>
        <div className="absolute -top-1 -right-1 flex items-center justify-center w-7 h-7 rounded-full bg-accent text-white shadow-md">
          <Plus size={14} />
        </div>
      </div>

      <h2 className="font-display font-semibold text-2xl text-fg mb-2">
        {canWrite ? 'Create your first dashboard' : 'No dashboards yet'}
      </h2>
      <p className="text-muted text-sm max-w-sm leading-relaxed mb-8">
        {canWrite
          ? 'Dashboards bring your data to life with charts, tables, and widgets. Build one manually or let AI do the heavy lifting.'
          : 'There are no dashboards to view yet. You have read-only access in this organisation.'}
      </p>

      {canWrite && (
        <div className="flex flex-col sm:flex-row gap-3">
          <Link
            to="/editor"
            className="inline-flex items-center justify-center gap-2 h-11 px-6 rounded-xl bg-primary text-primary-fg text-sm font-semibold hover:opacity-90 transition-opacity"
          >
            <Plus size={16} />
            New dashboard
          </Link>
          <button
            onClick={onAskAI}
            className="inline-flex items-center justify-center gap-2 h-11 px-6 rounded-xl border border-border bg-surface-2 text-fg text-sm font-semibold hover:bg-surface-2/60 transition-colors"
          >
            <Sparkles size={16} className="text-accent" />
            Ask AI to build one
          </button>
        </div>
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

// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------

export default function DashboardsPage() {
  const [boards, setBoards] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [search, setSearch] = useState('')
  const [sort, setSort] = useState('recent') // 'recent' | 'name'

  // Re-scope the list whenever the active project changes (api.js sends X-Project-Id).
  const { activeProject } = useProject()
  const projectId = activeProject?.id

  // Viewers are read-only — hide mutating actions (backend enforces too).
  const canWrite = useCanWrite()

  // Access the chat panel opener if UiContext is available
  let openChat = null
  try {
    // eslint-disable-next-line react-hooks/rules-of-hooks
    const ui = useUi()
    openChat = ui?.openChat ?? null
  } catch {
    // Not inside UiProvider — ignore
  }

  // Fetch boards
  const fetchBoards = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const data = await api.get('/boards')
      const list = Array.isArray(data)
        ? data
        : Array.isArray(data?.boards)
        ? data.boards
        : []
      setBoards(list)
    } catch (err) {
      setError(err.message ?? 'Failed to load dashboards')
    } finally {
      setLoading(false)
    }
  }, [projectId])

  useEffect(() => { fetchBoards() }, [fetchBoards])

  // Delete handler — remove from local list immediately
  const handleDeleted = useCallback((id) => {
    setBoards(prev => prev.filter(b => b.id !== id))
  }, [])

  // Filter + sort
  const filtered = boards
    .filter(b => b.name?.toLowerCase().includes(search.toLowerCase()))
    .sort((a, b) => {
      if (sort === 'name') return (a.name ?? '').localeCompare(b.name ?? '')
      // 'recent' — keep API order (assume descending created_at)
      return 0
    })

  // Ask AI handler
  function handleAskAI() {
    if (openChat) {
      openChat()
    }
  }

  return (
    <div className="min-h-full px-4 sm:px-6 lg:px-8 py-6">
      {/* ── Page header ──────────────────────────────────── */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4 mb-6">
        <div>
          <h1 className="font-display font-semibold text-2xl text-fg leading-tight">
            Dashboards
          </h1>
          {!loading && !error && (
            <p className="text-muted text-sm mt-0.5">
              {boards.length === 0
                ? 'No dashboards yet'
                : `${boards.length} dashboard${boards.length === 1 ? '' : 's'}`}
            </p>
          )}
        </div>

        {canWrite ? (
          <Link
            to="/editor"
            className="inline-flex items-center justify-center gap-2 h-11 px-5 rounded-xl bg-primary text-primary-fg text-sm font-semibold hover:opacity-90 transition-opacity shrink-0 self-start sm:self-auto"
          >
            <Plus size={16} />
            New dashboard
          </Link>
        ) : (
          <span className="inline-flex items-center h-11 px-3 rounded-xl text-xs font-medium text-muted self-start sm:self-auto">
            Read-only
          </span>
        )}
      </div>

      {/* ── Search + sort bar ────────────────────────────── */}
      {!loading && !error && boards.length > 0 && (
        <div className="flex flex-col sm:flex-row gap-3 mb-6">
          {/* Search */}
          <div className="relative flex-1">
            <Search
              size={15}
              className="absolute left-3 top-1/2 -translate-y-1/2 text-muted pointer-events-none"
            />
            <input
              type="search"
              value={search}
              onChange={e => setSearch(e.target.value)}
              placeholder="Search dashboards…"
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

          {/* Sort */}
          <SortMenu sort={sort} onChange={setSort} />
        </div>
      )}

      {/* ── Content ──────────────────────────────────────── */}

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
            <LayoutDashboard size={24} className="text-red-500" />
          </div>
          <h2 className="font-display font-semibold text-xl text-fg mb-2">
            Something went wrong
          </h2>
          <p className="text-muted text-sm max-w-xs leading-relaxed mb-6">{error}</p>
          <button
            onClick={fetchBoards}
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
          onAskAI={handleAskAI}
          canWrite={canWrite}
        />
      )}

      {/* Board grid */}
      {!loading && !error && filtered.length > 0 && (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
          {filtered.map(board => (
            <BoardCard
              key={board.id}
              board={board}
              onDeleted={handleDeleted}
              canWrite={canWrite}
            />
          ))}
        </div>
      )}
    </div>
  )
}
