/**
 * DashboardsPage — /dashboards
 *
 * Lists all boards from GET /api/v1/boards.
 * Board shape: { id, name, config: { spec?: { widgets?: [] }, html?: string } }
 *
 * Features:
 *   - Header with "New dashboard" CTA → /editor
 *   - Search by name + sort (recent / name)
 *   - Grid ↔ List view toggle. List mode adds multi-select checkboxes with a
 *     selection action bar (bulk delete) and a "Delete all" affordance — both
 *     gated by DangerConfirmDialog (type a random code to confirm).
 *   - Responsive grid: 1 col → sm:2 → lg:3
 *   - Per-card actions: Open, Edit, Delete (confirm dialog), plus versioning
 *     via the overflow menu: Checkpoint / History / Promote
 *   - Loading skeleton, error state, empty state
 *   - Light + dark via semantic tokens
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import {
  BarChart2,
  CheckCircle2,
  ChevronDown,
  Code2,
  ExternalLink,
  GitCommitHorizontal,
  History,
  LayoutDashboard,
  LayoutGrid,
  List,
  Loader2,
  MoreVertical,
  Pencil,
  Plus,
  Rocket,
  Search,
  Sparkles,
  Trash2,
  X,
} from 'lucide-react'
import * as api from '../../lib/api.js'
import { checkpoint, listEnvironments } from '../../lib/versions.js'
import VersionHistoryDialog from '../../components/app/VersionHistoryDialog.jsx'
import PromoteDialog from '../../components/app/PromoteDialog.jsx'
import DangerConfirmDialog from '../../components/app/DangerConfirmDialog.jsx'
import { useUi } from '../../contexts/UiContext.jsx'
import { useEnv } from '../../contexts/EnvContext.jsx'
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
function CardMenu({ onEdit, onCheckpoint, onHistory, onPromote, onDelete }) {
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
            onClick={(e) => { e.stopPropagation(); setOpen(false); onCheckpoint() }}
            className="flex items-center gap-2.5 w-full px-4 py-2.5 text-sm text-fg hover:bg-surface-2 transition-colors"
          >
            <GitCommitHorizontal size={14} className="text-muted" />
            Checkpoint
          </button>
          <button
            onClick={(e) => { e.stopPropagation(); setOpen(false); onHistory() }}
            className="flex items-center gap-2.5 w-full px-4 py-2.5 text-sm text-fg hover:bg-surface-2 transition-colors"
          >
            <History size={14} className="text-muted" />
            History
          </button>
          <button
            onClick={(e) => { e.stopPropagation(); setOpen(false); onPromote() }}
            className="flex items-center gap-2.5 w-full px-4 py-2.5 text-sm text-fg hover:bg-surface-2 transition-colors"
          >
            <Rocket size={14} className="text-muted" />
            Promote
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
function BoardCard({ board, onDeleted, onRestored, canWrite, environments, strictEnv }) {
  const navigate = useNavigate()
  const [confirmDelete, setConfirmDelete] = useState(false)
  const [deleteBusy, setDeleteBusy] = useState(false)
  const [historyOpen, setHistoryOpen] = useState(false)
  const [promoteOpen, setPromoteOpen] = useState(false)

  // Snapshot the board's saved config as a new version.
  async function handleCheckpoint() {
    const message = window.prompt('Checkpoint message (optional):', '')
    if (message === null) return // cancelled
    try {
      const v = await checkpoint('board', board.id, { message: message.trim() || undefined })
      window.alert(v?.deduped
        ? `No changes since v${v.version} — the existing version was reused.`
        : `Created version v${v?.version}.`)
    } catch (err) {
      window.alert(err?.message || 'Checkpoint failed.')
    }
  }

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
              <p className="text-xs text-muted mt-0.5 flex items-center gap-1.5 flex-wrap">
                {boardMeta(board.config)}
                {/* Strict-env visibility: the active env is protected and this
                    board has no pinned version there (pinned_envs from the
                    list API). */}
                {strictEnv && Array.isArray(board.pinned_envs)
                  && !board.pinned_envs.includes(strictEnv) && (
                  <span
                    title={`No version is pinned to ${strictEnv} — promote one to make it visible there.`}
                    className="inline-flex items-center px-1.5 py-0.5 text-[10px] font-medium rounded bg-rose-500/10 text-rose-600 dark:text-rose-400 border border-rose-500/20"
                  >
                    not in {strictEnv}
                  </span>
                )}
              </p>
            </div>
            {canWrite && (
              <CardMenu
                onEdit={() => navigate(`/editor/${board.id}`)}
                onCheckpoint={handleCheckpoint}
                onHistory={() => setHistoryOpen(true)}
                onPromote={() => setPromoteOpen(true)}
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

      {/* Version history (kind='board') — restore refetches the board list */}
      <VersionHistoryDialog
        kind="board"
        resourceId={board.id}
        resourceName={board.name || 'Untitled dashboard'}
        open={historyOpen}
        onClose={() => setHistoryOpen(false)}
        onRestored={onRestored}
        environments={environments ?? undefined}
      />

      {/* Promote (kind='board') — also moves referenced queries; the dialog
          surfaces the returned `promoted` list before closing */}
      <PromoteDialog
        kind="board"
        resourceId={board.id}
        open={promoteOpen}
        onClose={() => setPromoteOpen(false)}
        environments={environments ?? undefined}
      />
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

/** Grid ↔ List view switcher (flows view-switcher icon pattern). */
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
          data-testid={`boards-view-${v.id}`}
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

/** Compact list-mode row: checkbox, name, badges, updated-at, quick actions. */
function BoardListRow({ board, canWrite, selected, onToggle, strictEnv }) {
  const updated = board.updated_at ?? board.created_at
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
      data-testid="board-list-row"
      className={[
        'flex items-center gap-3 px-3 sm:px-4 py-2.5 transition-colors',
        selected ? 'bg-primary/5' : 'hover:bg-surface-2/60',
      ].join(' ')}
    >
      {canWrite && (
        <input
          type="checkbox"
          checked={selected}
          onChange={onToggle}
          aria-label={`Select ${board.name || 'Untitled dashboard'}`}
          className="w-4 h-4 shrink-0 rounded border-border accent-primary cursor-pointer"
        />
      )}

      {/* Mini thumbnail */}
      <span
        className="hidden sm:flex items-center justify-center w-8 h-8 rounded-lg shrink-0"
        style={{ background: cardGradient(board.id) }}
        aria-hidden="true"
      >
        {board.config?.html
          ? <Code2 size={14} className="text-white" />
          : <BarChart2 size={14} className="text-white" />}
      </span>

      {/* Name + meta */}
      <div className="flex-1 min-w-0">
        <Link
          to={`/d/${board.id}`}
          className="block text-sm font-medium text-fg hover:text-primary transition-colors truncate"
        >
          {board.name || 'Untitled dashboard'}
        </Link>
        <p className="text-xs text-muted truncate flex items-center gap-1.5">
          {boardMeta(board.config)}
          {strictEnv && Array.isArray(board.pinned_envs)
            && !board.pinned_envs.includes(strictEnv) && (
            <span
              title={`No version is pinned to ${strictEnv} — promote one to make it visible there.`}
              className="inline-flex items-center px-1.5 py-0.5 text-[10px] font-medium rounded bg-rose-500/10 text-rose-600 dark:text-rose-400 border border-rose-500/20"
            >
              not in {strictEnv}
            </span>
          )}
        </p>
      </div>

      {/* Updated at */}
      {updatedLabel && (
        <span className="hidden md:block text-xs text-muted shrink-0 tabular-nums">
          {updatedLabel}
        </span>
      )}

      {/* Quick actions */}
      <div className="flex items-center gap-1 shrink-0">
        <Link
          to={`/d/${board.id}`}
          title="Open"
          aria-label={`Open ${board.name || 'Untitled dashboard'}`}
          className="flex items-center justify-center w-8 h-8 rounded-lg text-muted hover:text-fg hover:bg-surface-2 transition-colors"
        >
          <ExternalLink size={14} />
        </Link>
        {canWrite && (
          <Link
            to={`/editor/${board.id}`}
            title="Edit"
            aria-label={`Edit ${board.name || 'Untitled dashboard'}`}
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

export default function DashboardsPage() {
  const [boards, setBoards] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [search, setSearch] = useState('')
  const [sort, setSort] = useState('recent') // 'recent' | 'name'

  // Grid ↔ List view (persisted so the choice survives reloads).
  const [viewMode, setViewMode] = useState(() => {
    try {
      return localStorage.getItem('nubi-dashboards-view') === 'list' ? 'list' : 'grid'
    } catch {
      return 'grid'
    }
  })
  const changeViewMode = useCallback((v) => {
    setViewMode(v)
    try { localStorage.setItem('nubi-dashboards-view', v) } catch { /* private mode */ }
  }, [])

  // List-mode multi-select + bulk delete (gated by DangerConfirmDialog).
  const [selected, setSelected] = useState(() => new Set())
  const [bulkDialog, setBulkDialog] = useState(null) // { ids, names, all } | null
  const [bulkBusy, setBulkBusy] = useState(false)
  const [bulkError, setBulkError] = useState(null)
  const [notice, setNotice] = useState(null)
  const noticeTimer = useRef(null)
  useEffect(() => () => clearTimeout(noticeTimer.current), [])

  // Re-scope the list whenever the active project changes (api.js sends X-Project-Id).
  const { activeProject } = useProject()
  const projectId = activeProject?.id

  // Project environments for the version/promote dialogs (null → dialogs fall
  // back to their built-in dev/prod defaults).
  const [environments, setEnvironments] = useState(null)
  useEffect(() => {
    let cancelled = false
    if (!projectId) { setEnvironments(null); return }
    listEnvironments(projectId).then(envs => { if (!cancelled) setEnvironments(envs) })
    return () => { cancelled = true }
  }, [projectId])

  // Viewers are read-only — hide mutating actions (backend enforces too).
  const canWrite = useCanWrite()

  // Strict-env badges: when the ACTIVE env is protected, cards whose
  // pinned_envs lack it get a 'not in <env>' chip. (NOTE: no 'View' action on
  // boards — rendering an arbitrary version config would need the full board
  // renderer mounted here; the history dialog still offers Restore/Promote.)
  const { environments: envList, activeEnv } = useEnv()
  const strictEnv = (Array.isArray(envList)
    && envList.find(e => e.key === activeEnv)?.protected)
    ? activeEnv
    : null

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
    setSelected(prev => {
      if (!prev.has(id)) return prev
      const next = new Set(prev)
      next.delete(id)
      return next
    })
  }, [])

  // Filter + sort
  const filtered = boards
    .filter(b => b.name?.toLowerCase().includes(search.toLowerCase()))
    .sort((a, b) => {
      if (sort === 'name') return (a.name ?? '').localeCompare(b.name ?? '')
      // 'recent' — keep API order (assume descending created_at)
      return 0
    })

  // ── List-mode selection helpers ─────────────────────────────────────────
  // Only boards that still exist count as selected (stale ids are ignored).
  const selectedBoards = boards.filter(b => selected.has(b.id))
  const allVisibleSelected =
    filtered.length > 0 && filtered.every(b => selected.has(b.id))

  const toggleSelected = useCallback((id) => {
    setSelected(prev => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }, [])

  const toggleSelectAllVisible = useCallback(() => {
    setSelected(prev => {
      const next = new Set(prev)
      const allIn = filtered.length > 0 && filtered.every(b => next.has(b.id))
      if (allIn) filtered.forEach(b => next.delete(b.id))
      else filtered.forEach(b => next.add(b.id))
      return next
    })
  }, [filtered])

  const clearSelection = useCallback(() => setSelected(new Set()), [])

  function showNotice(text) {
    clearTimeout(noticeTimer.current)
    setNotice(text)
    noticeTimer.current = setTimeout(() => setNotice(null), 5000)
  }

  /** Open the confirm dialog for the current selection. */
  function openBulkDeleteSelected() {
    if (selectedBoards.length === 0) return
    setBulkError(null)
    setBulkDialog({
      ids: selectedBoards.map(b => b.id),
      names: selectedBoards.map(b => b.name || 'Untitled dashboard'),
      all: false,
    })
  }

  /** Open the confirm dialog for ALL boards matching the current filter. */
  function openBulkDeleteAll() {
    if (filtered.length === 0) return
    setBulkError(null)
    setBulkDialog({
      ids: filtered.map(b => b.id),
      names: filtered.map(b => b.name || 'Untitled dashboard'),
      all: true,
    })
  }

  /** Run the bulk delete — loops the existing per-board DELETE endpoint. */
  async function handleBulkConfirm() {
    if (!bulkDialog || bulkBusy) return
    setBulkBusy(true)
    setBulkError(null)
    let failed = 0
    for (const id of bulkDialog.ids) {
      try {
        await api.del(`/boards/${id}`)
      } catch (err) {
        console.error(`Delete failed for board ${id}:`, err)
        failed++
      }
    }
    const deleted = bulkDialog.ids.length - failed
    setBulkBusy(false)
    clearSelection()
    await fetchBoards()
    if (failed > 0) {
      setBulkError(
        `Deleted ${deleted} of ${bulkDialog.ids.length} dashboards — ${failed} failed. The list has been refreshed.`
      )
    } else {
      setBulkDialog(null)
      showNotice(`Deleted ${deleted} dashboard${deleted === 1 ? '' : 's'}.`)
    }
  }

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

          {/* Grid ↔ List */}
          <ViewToggle view={viewMode} onChange={changeViewMode} />
        </div>
      )}

      {/* ── Bulk-delete success notice ───────────────────── */}
      {notice && (
        <div
          data-testid="boards-bulk-notice"
          className="flex items-center gap-2 mb-4 px-4 py-2.5 rounded-xl border border-emerald-500/30 bg-emerald-500/10 text-sm text-emerald-700 dark:text-emerald-400"
          role="status"
        >
          <CheckCircle2 size={15} className="shrink-0" />
          <span className="flex-1">{notice}</span>
          <button
            onClick={() => setNotice(null)}
            aria-label="Dismiss"
            className="shrink-0 p-1 rounded-md hover:bg-emerald-500/10 transition-colors"
          >
            <X size={13} />
          </button>
        </div>
      )}

      {/* ── Selection action bar (list mode) ─────────────── */}
      {viewMode === 'list' && canWrite && !loading && !error && selectedBoards.length > 0 && (
        <div
          data-testid="boards-selection-bar"
          className="flex flex-wrap items-center gap-3 mb-4 px-4 py-2.5 rounded-xl border border-primary/30 bg-primary/5"
        >
          <span className="text-sm font-medium text-fg">
            {selectedBoards.length} selected
          </span>
          <span className="text-muted">·</span>
          <button
            onClick={toggleSelectAllVisible}
            className="text-sm font-medium text-primary hover:underline"
          >
            {allVisibleSelected ? 'Deselect all' : `Select all (${filtered.length})`}
          </button>
          <button
            onClick={clearSelection}
            className="text-sm font-medium text-muted hover:text-fg transition-colors"
          >
            Clear
          </button>
          <div className="flex-1" />
          <button
            data-testid="boards-delete-selected"
            onClick={openBulkDeleteSelected}
            className="inline-flex items-center gap-1.5 h-8 px-3 rounded-lg bg-red-600 text-white text-xs font-semibold hover:bg-red-700 transition-colors"
          >
            <Trash2 size={13} />
            Delete {selectedBoards.length}
          </button>
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
      {!loading && !error && filtered.length > 0 && viewMode === 'grid' && (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
          {filtered.map(board => (
            <BoardCard
              key={board.id}
              board={board}
              onDeleted={handleDeleted}
              onRestored={fetchBoards}
              canWrite={canWrite}
              environments={environments}
              strictEnv={strictEnv}
            />
          ))}
        </div>
      )}

      {/* Board list */}
      {!loading && !error && filtered.length > 0 && viewMode === 'list' && (
        <div className="rounded-xl border border-border bg-surface overflow-hidden">
          {/* List header: select-all-visible + delete-all affordance */}
          <div className="flex items-center gap-3 px-3 sm:px-4 py-2 border-b border-border bg-surface-2/40">
            {canWrite && (
              <input
                type="checkbox"
                checked={allVisibleSelected}
                onChange={toggleSelectAllVisible}
                aria-label="Select all visible dashboards"
                data-testid="boards-select-all"
                className="w-4 h-4 shrink-0 rounded border-border accent-primary cursor-pointer"
              />
            )}
            <span className="flex-1 text-xs font-semibold uppercase tracking-wider text-muted">
              {filtered.length} dashboard{filtered.length === 1 ? '' : 's'}
              {search && ' (filtered)'}
            </span>
            {canWrite && (
              <button
                data-testid="boards-delete-all"
                onClick={openBulkDeleteAll}
                className="inline-flex items-center gap-1.5 h-7 px-2.5 rounded-lg border border-red-500/30 text-red-600 dark:text-red-400 text-xs font-medium hover:bg-red-500/10 transition-colors"
              >
                <Trash2 size={12} />
                Delete all{search ? ' matching' : ''}
              </button>
            )}
          </div>

          <ul className="divide-y divide-border">
            {filtered.map(board => (
              <BoardListRow
                key={board.id}
                board={board}
                canWrite={canWrite}
                selected={selected.has(board.id)}
                onToggle={() => toggleSelected(board.id)}
                strictEnv={strictEnv}
              />
            ))}
          </ul>
        </div>
      )}

      {/* Bulk-delete confirmation — random-code gate */}
      {bulkDialog && (
        <DangerConfirmDialog
          title={bulkDialog.all
            ? `Delete ALL ${bulkDialog.ids.length} dashboard${bulkDialog.ids.length === 1 ? '' : 's'}`
            : `Delete ${bulkDialog.ids.length} dashboard${bulkDialog.ids.length === 1 ? '' : 's'}`}
          description={bulkDialog.all
            ? (search
              ? `You are about to wipe every dashboard matching “${search}” in this project. Every widget, layout and share link on these boards will be destroyed.`
              : 'You are about to wipe EVERY dashboard in this project. Every widget, layout and share link on these boards will be destroyed.')
            : 'The selected dashboards — including their widgets, layouts and share links — will be permanently deleted.'}
          items={bulkDialog.names}
          count={bulkDialog.ids.length}
          itemNoun="dashboard"
          confirmLabel={`Delete ${bulkDialog.ids.length} dashboard${bulkDialog.ids.length === 1 ? '' : 's'}`}
          loading={bulkBusy}
          error={bulkError}
          onCancel={() => { if (!bulkBusy) { setBulkDialog(null); setBulkError(null) } }}
          onConfirm={handleBulkConfirm}
        />
      )}
    </div>
  )
}
