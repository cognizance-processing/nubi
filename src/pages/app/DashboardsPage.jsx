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
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import {
  BarChart2,
  Check,
  CheckCircle2,
  Code2,
  ExternalLink,
  Folder,
  FolderPlus,
  GitCommitHorizontal,
  History,
  LayoutDashboard,
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
import BoardThumbnail from '../../components/app/BoardThumbnail.jsx'
import { buildMiniature } from '../../dashboards/specMiniature.js'
import DangerConfirmDialog from '../../components/app/DangerConfirmDialog.jsx'
import { toast } from '../../components/ui/Toast.jsx'
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
  NoticeBanner,
  SelectionBar,
  ErrorState,
  CardSkeleton,
  ListRowSkeleton,
  DropdownMenu,
  DropdownItem,
  DropdownDivider,
} from '../../components/app/PageShell.jsx'
import { useUi } from '../../contexts/UiContext.jsx'
import { useEnv } from '../../contexts/EnvContext.jsx'
import { useProject } from '../../contexts/ProjectContext.jsx'
import { useCanWrite } from '../../contexts/OrgContext.jsx'
import { useAsyncLoad } from '../../hooks/useAsyncLoad.js'

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function boardMeta(config) {
  if (!config) return 'Dashboard'
  if (config.html) return 'HTML board'
  const count = config.spec?.widgets?.length ?? 0
  return count === 1 ? '1 widget' : `${count} widgets`
}

const GRADIENTS = [
  'linear-gradient(135deg, #1b2363 0%, #2456a6 60%, #17b3a3 100%)',
  'linear-gradient(135deg, #17b3a3 0%, #2456a6 50%, #1b2363 100%)',
  'linear-gradient(135deg, #2456a6 0%, #1b2363 40%, #17b3a3 100%)',
  'linear-gradient(135deg, #0f9e90 0%, #2456a6 100%)',
  'linear-gradient(135deg, #1b2363 0%, #17b3a3 100%)',
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

function folderIdOf(config) {
  return config?.folderId || null
}

// ---------------------------------------------------------------------------
// Folders
// ---------------------------------------------------------------------------

function FolderPill({ folder, count, active, canWrite, dragOver, onSelect, onDelete, onDragOver, onDragLeave, onDrop }) {
  return (
    <button
      type="button"
      onClick={onSelect}
      onDragOver={onDragOver}
      onDragLeave={onDragLeave}
      onDrop={onDrop}
      className={`group/pill inline-flex items-center gap-1.5 h-8 pl-3 pr-2 rounded-full border text-xs font-medium whitespace-nowrap transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring ${
        active
          ? 'bg-primary text-primary-fg border-primary'
          : dragOver
          ? 'bg-primary/10 border-primary text-primary'
          : 'bg-surface-2 border-border text-fg hover:border-primary/50'
      }`}
    >
      <Folder size={12} aria-hidden="true" />
      {folder.name}
      <span className={`text-[10px] tabular-nums ${active ? 'text-primary-fg/70' : 'text-muted'}`}>{count}</span>
      {canWrite && (
        <span
          role="button"
          tabIndex={-1}
          onClick={(e) => { e.preventDefault(); e.stopPropagation(); onDelete() }}
          className={`ml-0.5 flex items-center justify-center w-4 h-4 rounded-full opacity-0 group-hover/pill:opacity-100 transition-opacity ${active ? 'hover:bg-white/20' : 'hover:bg-surface'}`}
          aria-label={`Delete folder ${folder.name}`}
        >
          <X size={10} />
        </span>
      )}
    </button>
  )
}

function FolderRail({ folders, boards, activeFolderId, canWrite, onSelect, onNewFolder, onDeleteFolder, onDropBoard }) {
  const [dragOverId, setDragOverId] = useState(null)

  if (folders.length === 0 && !canWrite) return null

  return (
    <div className="flex items-center gap-2 overflow-x-auto pb-1 -mx-1 px-1">
      <button
        type="button"
        onClick={() => onSelect(null)}
        onDragOver={(e) => { e.preventDefault(); setDragOverId('__all__') }}
        onDragLeave={() => setDragOverId(null)}
        onDrop={(e) => { e.preventDefault(); setDragOverId(null); onDropBoard(e, null) }}
        className={`inline-flex items-center gap-1.5 h-8 px-3 rounded-full border text-xs font-medium whitespace-nowrap transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring ${
          !activeFolderId
            ? 'bg-primary text-primary-fg border-primary'
            : dragOverId === '__all__'
            ? 'bg-primary/10 border-primary text-primary'
            : 'bg-surface-2 border-border text-fg hover:border-primary/50'
        }`}
      >
        All dashboards
        <span className={`text-[10px] tabular-nums ${!activeFolderId ? 'text-primary-fg/70' : 'text-muted'}`}>{boards.length}</span>
      </button>

      {folders.map(folder => (
        <FolderPill
          key={folder.id}
          folder={folder}
          count={boards.filter(b => folderIdOf(b.config) === folder.id).length}
          active={activeFolderId === folder.id}
          canWrite={canWrite}
          dragOver={dragOverId === folder.id}
          onSelect={() => onSelect(folder.id)}
          onDelete={() => onDeleteFolder(folder)}
          onDragOver={(e) => { e.preventDefault(); setDragOverId(folder.id) }}
          onDragLeave={() => setDragOverId(null)}
          onDrop={(e) => { e.preventDefault(); setDragOverId(null); onDropBoard(e, folder.id) }}
        />
      ))}

      {canWrite && (
        <button
          type="button"
          onClick={onNewFolder}
          className="inline-flex items-center gap-1.5 h-8 px-3 rounded-full border border-dashed border-border text-xs font-medium text-muted hover:text-primary hover:border-primary/50 transition-colors whitespace-nowrap focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          <FolderPlus size={12} aria-hidden="true" />
          New folder
        </button>
      )}
    </div>
  )
}

function NewFolderDialog({ onCreate, onCancel, busy }) {
  const [name, setName] = useState('')
  const inputRef = useRef(null)

  useEffect(() => {
    const t = setTimeout(() => inputRef.current?.focus(), 50)
    return () => clearTimeout(t)
  }, [])

  function submit(e) {
    e.preventDefault()
    const trimmed = name.trim()
    if (!trimmed || busy) return
    onCreate(trimmed)
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4" role="dialog" aria-modal="true" aria-labelledby="new-folder-title">
      <div className="absolute inset-0 bg-black/50 backdrop-blur-sm" onClick={busy ? undefined : onCancel} />
      <form
        onSubmit={submit}
        className="relative z-10 w-full max-w-sm rounded-2xl border border-border bg-surface p-6 shadow-nubi-xl nubi-animate-scale-in"
      >
        <div className="flex items-center justify-center w-11 h-11 rounded-xl bg-primary/10 mb-4">
          <Folder size={20} className="text-primary" />
        </div>
        <h2 id="new-folder-title" className="font-display font-semibold text-lg text-fg mb-1">New folder</h2>
        <p className="text-muted text-sm mb-4 leading-relaxed">Group related dashboards together.</p>
        <input
          ref={inputRef}
          type="text"
          value={name}
          onChange={e => setName(e.target.value)}
          placeholder="e.g. Route Efficiency"
          className="w-full h-10 px-3 rounded-xl border border-border bg-surface-2 text-sm text-fg placeholder:text-muted focus:outline-none focus:ring-2 focus:ring-ring mb-6"
        />
        <div className="flex gap-2.5">
          <button
            type="button"
            onClick={onCancel}
            disabled={busy}
            className="flex-1 h-9 rounded-xl border border-border bg-surface-2 text-sm font-medium text-fg hover:bg-surface-2/80 transition-colors disabled:opacity-50"
          >
            Cancel
          </button>
          <button
            type="submit"
            disabled={busy || !name.trim()}
            className="flex-1 h-9 rounded-xl bg-primary text-primary-fg text-sm font-medium hover:opacity-90 transition-opacity disabled:opacity-50 flex items-center justify-center gap-2"
          >
            {busy && <Loader2 size={13} className="animate-spin" />}
            {busy ? 'Creating…' : 'Create'}
          </button>
        </div>
      </form>
    </div>
  )
}

function DeleteFolderDialog({ folder, count, onConfirm, onCancel, busy }) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4" role="dialog" aria-modal="true" aria-labelledby="delete-folder-title">
      <div className="absolute inset-0 bg-black/50 backdrop-blur-sm" onClick={busy ? undefined : onCancel} />
      <div className="relative z-10 w-full max-w-sm rounded-2xl border border-border bg-surface p-6 shadow-nubi-xl nubi-animate-scale-in">
        <div className="flex items-center justify-center w-11 h-11 rounded-xl bg-danger-bg mb-4">
          <Trash2 size={20} className="text-danger" />
        </div>
        <h2 id="delete-folder-title" className="font-display font-semibold text-lg text-fg mb-1">Delete folder?</h2>
        <p className="text-muted text-sm mb-6 leading-relaxed">
          <span className="font-medium text-fg">&ldquo;{folder.name}&rdquo;</span> will be deleted.
          {count > 0 ? ` Its ${count} dashboard${count === 1 ? '' : 's'} will be ungrouped, not deleted.` : ''}
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
      <rect x="10" y="30" width="12" height="22" rx="2" fill="white" />
      <rect x="27" y="18" width="12" height="34" rx="2" fill="white" />
      <rect x="44" y="24" width="12" height="28" rx="2" fill="white" />
      <rect x="61" y="12" width="12" height="40" rx="2" fill="white" />
      <rect x="78" y="20" width="12" height="32" rx="2" fill="white" />
      <rect x="95" y="36" width="12" height="16" rx="2" fill="white" />
      <polyline
        points="10,52 27,38 44,44 61,28 78,34 95,22 112,30"
        fill="none" stroke="white" strokeWidth="1.5"
        strokeLinecap="round" strokeLinejoin="round"
      />
    </svg>
  )
}

/**
 * CardThumbnail — the board, shown as a page.
 *
 * The dashboard render is presented like a sheet of paper floating on a tinted
 * backdrop: the render keeps its own aspect and is shown WHOLE (never cropped),
 * the board's identity gradient survives only as a soft wash behind it, and a
 * shadow lifts the page off that wash.
 *
 * Why whole and not filled-to-frame: `object-cover` would crop a wide board's
 * left/right edges — quietly amputating real widgets (the Total Sales KPI, say).
 * A card exists to answer "which dashboard is this?", and a cropped board is a
 * worse answer than a slightly letterboxed one. The server caps the render's
 * aspect (_THUMB_MAX_HEIGHT_PX) so the letterbox stays small.
 *
 * The earlier treatment put a blurred glass pane over a full-bleed gradient.
 * That made sense when the content was a monochrome wireframe; once the content
 * is a real, white, densely-coloured dashboard it was just noise competing with
 * the thing you're meant to be looking at — thirteen loud gradients per screen.
 *
 * BoardThumbnail is itself two layers: the spec-derived <BoardMiniature> (drawn
 * instantly from the widgets' real grid positions) and, swapped in when it
 * arrives, the board rendered server-side with its real data. See
 * BoardThumbnail.jsx for why the real render can't be the thing a card waits on.
 *
 * Falls back to the icon treatment when there's no truthful layout to show:
 * legacy HTML boards (no spec) and specs whose widgets carry no geometry.
 */
function CardThumbnail({ board }) {
  const isHtml = Boolean(board.config?.html)
  // Ask the pure model, not the component — calling BoardMiniature() directly
  // would run its hooks inside this component. `buildMiniature` returns null for
  // exactly the boards with no truthful layout (no spec / no geometry), which is
  // the same rule the component itself uses to bow out.
  const hasLayout = !isHtml && buildMiniature(board.config?.spec) !== null

  if (!hasLayout) {
    return (
      <div
        className="relative w-full h-[6.5rem] overflow-hidden flex items-center justify-center"
        style={{ background: cardGradient(board.id) }}
      >
        <ThumbnailPattern />
        <div className="relative z-10 flex items-center justify-center w-9 h-9 rounded-xl bg-white/15 backdrop-blur-sm">
          {isHtml
            ? <Code2 size={18} className="text-white" aria-hidden="true" />
            : <BarChart2 size={18} className="text-white" aria-hidden="true" />}
        </div>
      </div>
    )
  }

  return (
    // 16:9 rather than something squarer: a real board's render lands between
    // ~1.4:1 (tall, height-capped by the server) and ~2.2:1 (a short board), and
    // 16:9 sits close enough to both that neither extreme carries much dead
    // letterbox once contained.
    <div className="relative w-full aspect-video overflow-hidden bg-surface-2">
      {/* Identity wash — the per-board gradient, dialled from a full-bleed
          background down to a soft corner glow. Still enough to tell two cards
          apart at a glance; no longer shouting over the dashboard itself. */}
      <div
        className="absolute inset-0 opacity-[0.22] dark:opacity-[0.3]"
        style={{ background: cardGradient(board.id) }}
        aria-hidden="true"
      />
      {/* Fade the wash out toward the middle so the page reads against a calm
          backdrop rather than a saturated one. */}
      <div
        className="absolute inset-0"
        style={{ background: 'radial-gradient(120% 100% at 50% 0%, transparent 30%, var(--surface-2) 100%)' }}
        aria-hidden="true"
      />

      {/* The page. Insets leave the wash visible as a frame; the shadow lifts it. */}
      <div className="absolute inset-x-3 top-3 bottom-0">
        <div
          className="w-full h-full rounded-t-md overflow-hidden transition-transform duration-300 ease-out group-hover:-translate-y-0.5"
          style={{
            border: '1px solid var(--border)',
            borderBottom: 'none',
            boxShadow: '0 6px 20px rgba(0,0,0,0.18)',
            background: 'var(--surface)',
          }}
        >
          <BoardThumbnail boardId={board.id} spec={board.config?.spec} />
        </div>
      </div>
    </div>
  )
}

// Actions are individually optional: the menu now sits on every card, including
// ones a read-only viewer sees, where Open is the only thing they may do.
function CardMenu({ onOpen, onEdit, onCheckpoint, onHistory, onPromote, onDelete, folders, currentFolderId, onMoveToFolder, onNewFolderFromCard }) {
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
        aria-label="Board options"
        aria-expanded={open}
        aria-haspopup="menu"
      >
        <MoreVertical size={14} aria-hidden="true" />
      </button>

      <DropdownMenu open={open} onClose={() => setOpen(false)}>
        {onOpen && <DropdownItem icon={ExternalLink} onClick={() => { setOpen(false); onOpen() }}>Open</DropdownItem>}
        {onEdit && <DropdownItem icon={Pencil} onClick={() => { setOpen(false); onEdit() }}>Edit</DropdownItem>}
        {(onOpen || onEdit) && (onCheckpoint || onHistory || onPromote) && <DropdownDivider />}
        {onCheckpoint && <DropdownItem icon={GitCommitHorizontal} onClick={() => { setOpen(false); onCheckpoint() }}>Checkpoint</DropdownItem>}
        {onHistory && <DropdownItem icon={History} onClick={() => { setOpen(false); onHistory() }}>History</DropdownItem>}
        {onPromote && <DropdownItem icon={Rocket} onClick={() => { setOpen(false); onPromote() }}>Promote</DropdownItem>}
        {onMoveToFolder && (
          <>
            <DropdownDivider />
            <div className="px-3 pt-1.5 pb-1 text-[10px] font-semibold text-muted uppercase tracking-wider">
              Move to folder
            </div>
            {folders.map(folder => (
              <DropdownItem
                key={folder.id}
                icon={Folder}
                onClick={() => { setOpen(false); onMoveToFolder(folder.id === currentFolderId ? null : folder.id) }}
              >
                <span className="flex-1 text-left truncate">{folder.name}</span>
                {folder.id === currentFolderId && <Check size={13} className="text-primary shrink-0" aria-hidden="true" />}
              </DropdownItem>
            ))}
            <DropdownItem icon={FolderPlus} onClick={() => { setOpen(false); onNewFolderFromCard() }}>
              New folder…
            </DropdownItem>
          </>
        )}
        <DropdownDivider />
        <DropdownItem icon={Trash2} danger onClick={() => { setOpen(false); onDelete() }}>Delete</DropdownItem>
      </DropdownMenu>
    </div>
  )
}

function DeleteDialog({ board, onConfirm, onCancel, busy }) {
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
      aria-labelledby="delete-dlg-title"
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
        <h2 id="delete-dlg-title" className="font-display font-semibold text-lg text-fg mb-1">
          Delete dashboard?
        </h2>
        <p className="text-muted text-sm mb-6 leading-relaxed">
          <span className="font-medium text-fg">&ldquo;{board.name}&rdquo;</span> will be
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

function StrictEnvBadge({ pinned_envs, strictEnv }) {
  if (!strictEnv || !Array.isArray(pinned_envs)) return null
  if (pinned_envs.includes(strictEnv)) return null
  return (
    <span
      title={`No version is pinned to ${strictEnv} — promote one to make it visible there.`}
      className="inline-flex items-center px-1.5 py-0.5 text-[10px] font-medium rounded-md bg-red-500/10 text-red-600 dark:text-red-400 border border-red-500/20 leading-none"
    >
      not in {strictEnv}
    </span>
  )
}

function BoardCard({ board, onDeleted, onRestored, canWrite, environments, strictEnv, folders, onMoveToFolder, onNewFolderFromCard, linkState }) {
  const navigate = useNavigate()
  const [confirmDelete, setConfirmDelete] = useState(false)
  const [deleteBusy, setDeleteBusy] = useState(false)
  const [historyOpen, setHistoryOpen] = useState(false)
  const [promoteOpen, setPromoteOpen] = useState(false)

  async function handleCheckpoint() {
    const message = window.prompt('Checkpoint message (optional):', '')
    if (message === null) return
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
      setConfirmDelete(false)
    } catch (err) {
      console.error('Delete failed:', err)
      toast.error(err?.message || 'Failed to delete dashboard. Please try again.')
    } finally {
      setDeleteBusy(false)
    }
  }

  const currentFolderId = folderIdOf(board.config)

  return (
    <>
      <article
        className="nubi-resource-card group"
        draggable={canWrite && Boolean(onMoveToFolder)}
        onDragStart={(e) => {
          if (!canWrite || !onMoveToFolder) return
          e.dataTransfer.setData('text/nubi-board-id', board.id)
          e.dataTransfer.effectAllowed = 'move'
        }}
      >
        {/* Picture + its hover actions. The actions are a SIBLING of the link,
            not a child — a link inside a link is invalid HTML and browsers
            resolve it unpredictably. */}
        <div className="relative">
          <Link to={`/d/${board.id}`} state={linkState} className="block" tabIndex={-1} aria-hidden="true">
            <CardThumbnail board={board} />
          </Link>

          {/* One affordance in the corner instead of a row of buttons across
              the picture. The board render is the point of the card, and a
              full-width action bar (plus its scrim) covered the bottom third of
              it on hover — exactly where a board's first chart sits. Everything
              those buttons did now lives in the menu.

              A SIBLING of the link, never a child: a link inside a link is
              invalid HTML and browsers resolve it unpredictably. Kept mounted
              and revealed with opacity (never display:none) so it stays
              tabbable; focus-within covers keyboards and hover:none devices
              show it always. */}
          <div className="absolute top-2 right-2 opacity-0 group-hover:opacity-100 group-focus-within:opacity-100 [@media(hover:none)]:opacity-100 transition-opacity duration-200">
            <CardMenu
              onOpen={() => navigate(`/d/${board.id}`, { state: linkState })}
              onEdit={canWrite ? () => navigate(`/d/${board.id}/edit`) : undefined}
              onCheckpoint={canWrite ? handleCheckpoint : undefined}
              onHistory={canWrite ? () => setHistoryOpen(true) : undefined}
              onPromote={canWrite ? () => setPromoteOpen(true) : undefined}
              onDelete={canWrite ? () => setConfirmDelete(true) : undefined}
              folders={folders}
              currentFolderId={currentFolderId}
              onMoveToFolder={canWrite && onMoveToFolder ? (folderId) => onMoveToFolder(board, folderId) : undefined}
              onNewFolderFromCard={canWrite && onNewFolderFromCard ? () => onNewFolderFromCard(board) : undefined}
            />
          </div>
        </div>

        <div className="nubi-resource-card-body">
          <div className="flex items-start justify-between gap-2 min-w-0">
            <div className="min-w-0 flex-1">
              <Link
                to={`/d/${board.id}`}
                state={linkState}
                className="block font-display font-semibold text-sm text-fg hover:text-primary transition-colors truncate leading-snug"
              >
                {board.name || 'Untitled dashboard'}
              </Link>
              <p className="text-xs text-muted mt-0.5 flex items-center gap-1.5 flex-wrap leading-tight">
                {boardMeta(board.config)}
                <StrictEnvBadge pinned_envs={board.pinned_envs} strictEnv={strictEnv} />
              </p>
            </div>
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

      <VersionHistoryDialog
        kind="board"
        resourceId={board.id}
        resourceName={board.name || 'Untitled dashboard'}
        open={historyOpen}
        onClose={() => setHistoryOpen(false)}
        onRestored={onRestored}
        environments={environments ?? undefined}
      />

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

function BoardListRow({ board, canWrite, selected, onToggle, strictEnv, linkState }) {
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
    <ListRow
      data-testid="board-list-row"
      selected={selected}
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

      <span
        className="hidden sm:flex items-center justify-center w-8 h-8 rounded-lg shrink-0"
        style={{ background: cardGradient(board.id) }}
        aria-hidden="true"
      >
        {board.config?.html
          ? <Code2 size={13} className="text-white" />
          : <BarChart2 size={13} className="text-white" />}
      </span>

      <div className="flex-1 min-w-0">
        <Link
          to={`/d/${board.id}`}
          state={linkState}
          className="block text-sm font-medium text-fg hover:text-primary transition-colors truncate"
        >
          {board.name || 'Untitled dashboard'}
        </Link>
        <p className="text-xs text-muted truncate flex items-center gap-1.5 mt-0.5">
          {boardMeta(board.config)}
          <StrictEnvBadge pinned_envs={board.pinned_envs} strictEnv={strictEnv} />
        </p>
      </div>

      {updatedLabel && (
        <span className="hidden md:block text-xs text-muted shrink-0 tabular-nums">
          {updatedLabel}
        </span>
      )}

      <div className="flex items-center gap-0.5 shrink-0">
        <Link
          to={`/d/${board.id}`}
          state={linkState}
          title="Open"
          aria-label={`Open ${board.name || 'Untitled dashboard'}`}
          className="flex items-center justify-center w-7 h-7 rounded-lg text-muted hover:text-fg hover:bg-surface-2 transition-colors"
        >
          <ExternalLink size={13} />
        </Link>
        {canWrite && (
          <Link
            to={`/d/${board.id}/edit`}
            title="Edit"
            aria-label={`Edit ${board.name || 'Untitled dashboard'}`}
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
// Empty / error state
// ---------------------------------------------------------------------------

function EmptyBoards({ hasFilter, onClearFilter, onAskAI, canWrite }) {
  if (hasFilter) {
    return (
      <div className="flex flex-col items-center justify-center py-20 px-6 text-center">
        <div className="flex items-center justify-center w-12 h-12 rounded-2xl bg-surface-2 mb-4">
          <Search size={20} className="text-muted" />
        </div>
        <p className="font-display font-semibold text-base text-fg mb-1.5">No results</p>
        <p className="text-muted text-sm max-w-xs leading-relaxed mb-5">
          No dashboards match your search. Try a different term.
        </p>
        <button
          onClick={onClearFilter}
          className="h-9 px-4 rounded-xl border border-border bg-surface-2 text-sm text-fg font-medium hover:bg-surface-2/60 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
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
          style={{ background: 'linear-gradient(135deg, #1b2363, #2456a6, #17b3a3)' }}
        >
          <LayoutDashboard size={28} className="text-white" />
        </div>
        <div className="absolute -top-1 -right-1 flex items-center justify-center w-6 h-6 rounded-full bg-accent text-white shadow-nubi-md">
          <Plus size={12} />
        </div>
      </div>

      <h2 className="font-display font-semibold text-xl text-fg mb-1.5">
        {canWrite ? 'Create your first dashboard' : 'No dashboards yet'}
      </h2>
      <p className="text-muted text-sm max-w-sm leading-relaxed mb-6">
        {canWrite
          ? 'Bring your data to life with charts, tables, and widgets. Build one manually or let AI do the heavy lifting.'
          : 'There are no dashboards to view yet. You have read-only access in this organisation.'}
      </p>

      {canWrite && (
        <div className="flex flex-col sm:flex-row gap-3">
          <Link
            to="/editor"
            className="inline-flex items-center justify-center gap-2 h-10 px-5 rounded-xl bg-primary text-primary-fg text-sm font-semibold hover:opacity-90 transition-opacity focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            <Plus size={15} />
            New dashboard
          </Link>
          <button
            onClick={onAskAI}
            className="inline-flex items-center justify-center gap-2 h-10 px-5 rounded-xl border border-border bg-surface-2 text-fg text-sm font-semibold hover:bg-surface-2/60 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            <Sparkles size={15} className="text-accent" />
            Ask AI to build one
          </button>
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------

export default function DashboardsPage() {
  const [search, setSearch] = useState('')
  const [sort, setSort] = useState('recent')

  const [searchParams, setSearchParams] = useSearchParams()
  const activeFolderId = searchParams.get('folder') || null
  const setActiveFolderId = useCallback((folderId) => {
    setSearchParams(prev => {
      const next = new URLSearchParams(prev)
      if (folderId) next.set('folder', folderId)
      else next.delete('folder')
      return next
    }, { replace: true })
  }, [setSearchParams])

  const [newFolderState, setNewFolderState] = useState(null) // null | { busy, forBoard? }
  const [deleteFolderDialog, setDeleteFolderDialog] = useState(null)
  const [deleteFolderBusy, setDeleteFolderBusy] = useState(false)

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

  const [selected, setSelected] = useState(() => new Set())
  const [bulkDialog, setBulkDialog] = useState(null)
  const [bulkBusy, setBulkBusy] = useState(false)
  const [bulkError, setBulkError] = useState(null)
  const [notice, setNotice] = useState(null)
  const noticeTimer = useRef(null)
  useEffect(() => () => clearTimeout(noticeTimer.current), [])

  const { activeProject } = useProject()
  const projectId = activeProject?.id

  const [environments, setEnvironments] = useState(null)
  useEffect(() => {
    let cancelled = false
    if (!projectId) { setEnvironments(null); return }
    listEnvironments(projectId).then(envs => { if (!cancelled) setEnvironments(envs) })
    return () => { cancelled = true }
  }, [projectId])

  const canWrite = useCanWrite()

  const { environments: envList, activeEnv } = useEnv()
  const strictEnv = (Array.isArray(envList)
    && envList.find(e => e.key === activeEnv)?.protected)
    ? activeEnv
    : null

  let openChat = null
  try {
    const ui = useUi()
    openChat = ui?.openChat ?? null
  } catch {
    // Not inside UiProvider — ignore
  }

  const { data: boardsData, loading, error, reload: reloadBoards } = useAsyncLoad(async () => {
    const data = await api.get('/boards')
    return Array.isArray(data) ? data : Array.isArray(data?.boards) ? data.boards : []
  }, [projectId])
  const boards = boardsData ?? []

  const { data: foldersData, reload: reloadFolders } = useAsyncLoad(async () => {
    const data = await api.get('/dashboard_folders')
    return Array.isArray(data) ? data : Array.isArray(data?.dashboard_folders) ? data.dashboard_folders : []
  }, [projectId])
  const folders = foldersData ?? []

  // Clear a stale/deleted folder out of the URL so the grid doesn't silently
  // stay empty pointing at a folder that no longer exists.
  useEffect(() => {
    if (activeFolderId && folders.length > 0 && !folders.some(f => f.id === activeFolderId)) {
      setActiveFolderId(null)
    }
  }, [activeFolderId, folders, setActiveFolderId])

  const moveBoardToFolder = useCallback(async (board, folderId) => {
    try {
      await api.put(`/boards/${board.id}`, { config: { ...board.config, folderId: folderId || undefined } })
      reloadBoards()
    } catch (err) {
      console.error('Move to folder failed:', err)
      toast.error(err?.message || 'Failed to move dashboard.')
    }
  }, [reloadBoards])

  const createFolder = useCallback(async (name, forBoard) => {
    setNewFolderState(prev => ({ ...(prev ?? {}), busy: true }))
    try {
      const folder = await api.post('/dashboard_folders', { name, config: {} })
      reloadFolders()
      if (forBoard) await moveBoardToFolder(forBoard, folder.id)
      else setActiveFolderId(folder.id)
      setNewFolderState(null)
    } catch (err) {
      console.error('Create folder failed:', err)
      toast.error(err?.message || 'Failed to create folder.')
      setNewFolderState(prev => (prev ? { ...prev, busy: false } : null))
    }
  }, [reloadFolders, moveBoardToFolder, setActiveFolderId])

  const confirmDeleteFolder = useCallback(async () => {
    if (!deleteFolderDialog || deleteFolderBusy) return
    setDeleteFolderBusy(true)
    try {
      const affected = boards.filter(b => folderIdOf(b.config) === deleteFolderDialog.id)
      for (const board of affected) {
        try {
          await api.put(`/boards/${board.id}`, { config: { ...board.config, folderId: undefined } })
        } catch (err) {
          console.error(`Failed to ungroup board ${board.id}:`, err)
        }
      }
      await api.del(`/dashboard_folders/${deleteFolderDialog.id}`)
      if (activeFolderId === deleteFolderDialog.id) setActiveFolderId(null)
      reloadFolders()
      reloadBoards()
      setDeleteFolderDialog(null)
    } catch (err) {
      console.error('Delete folder failed:', err)
      toast.error(err?.message || 'Failed to delete folder.')
    } finally {
      setDeleteFolderBusy(false)
    }
  }, [deleteFolderDialog, deleteFolderBusy, boards, activeFolderId, setActiveFolderId, reloadFolders, reloadBoards])

  const handleDeleted = useCallback((id) => {
    setSelected(prev => {
      if (!prev.has(id)) return prev
      const next = new Set(prev)
      next.delete(id)
      return next
    })
    reloadBoards()
  }, [reloadBoards])

  const filtered = boards
    .filter(b => b.name?.toLowerCase().includes(search.toLowerCase()))
    .filter(b => !activeFolderId || folderIdOf(b.config) === activeFolderId)
    .sort((a, b) => {
      if (sort === 'name') return (a.name ?? '').localeCompare(b.name ?? '')
      return 0
    })

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

  function openBulkDeleteSelected() {
    if (selectedBoards.length === 0) return
    setBulkError(null)
    setBulkDialog({
      ids: selectedBoards.map(b => b.id),
      names: selectedBoards.map(b => b.name || 'Untitled dashboard'),
      all: false,
    })
  }

  function openBulkDeleteAll() {
    if (filtered.length === 0) return
    setBulkError(null)
    setBulkDialog({
      ids: filtered.map(b => b.id),
      names: filtered.map(b => b.name || 'Untitled dashboard'),
      all: true,
    })
  }

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
    reloadBoards()
    if (failed > 0) {
      setBulkError(
        `Deleted ${deleted} of ${bulkDialog.ids.length} dashboards — ${failed} failed. The list has been refreshed.`
      )
    } else {
      setBulkDialog(null)
      showNotice(`Deleted ${deleted} dashboard${deleted === 1 ? '' : 's'}.`)
    }
  }

  const subtitle = loading
    ? undefined
    : error
    ? undefined
    : boards.length === 0
    ? 'No dashboards yet'
    : `${boards.length} dashboard${boards.length === 1 ? '' : 's'}`

  return (
    <PageRoot>
      {/* ── Page header ── */}
      <PageHeader title="Dashboards" subtitle={subtitle}>
        {canWrite ? (
          <Link
            to="/editor"
            className="inline-flex items-center justify-center gap-1.5 h-9 px-4 rounded-xl bg-primary text-primary-fg text-sm font-semibold hover:opacity-90 transition-opacity focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            <Plus size={15} aria-hidden="true" />
            New dashboard
          </Link>
        ) : (
          <span className="inline-flex items-center h-9 px-3 rounded-xl text-xs font-medium text-muted bg-surface-2 border border-border">
            Read-only
          </span>
        )}
      </PageHeader>

      {/* ── Folder rail ── */}
      {!loading && !error && (boards.length > 0 || folders.length > 0) && (
        <FolderRail
          folders={folders}
          boards={boards}
          activeFolderId={activeFolderId}
          canWrite={canWrite}
          onSelect={setActiveFolderId}
          onNewFolder={() => setNewFolderState({ busy: false })}
          onDeleteFolder={(folder) => setDeleteFolderDialog(folder)}
          onDropBoard={(e, folderId) => {
            const boardId = e.dataTransfer.getData('text/nubi-board-id')
            const board = boards.find(b => b.id === boardId)
            if (board) moveBoardToFolder(board, folderId)
          }}
        />
      )}

      {/* ── Search + sort bar ── */}
      {!loading && !error && boards.length > 0 && (
        <Toolbar>
          <SearchBar
            value={search}
            onChange={setSearch}
            placeholder="Search dashboards…"
          />
          <SortMenu value={sort} onChange={setSort} options={SORT_OPTIONS} />
          <ViewToggle value={viewMode} onChange={changeViewMode} />
        </Toolbar>
      )}

      {/* ── Bulk-delete success notice ── */}
      {notice && (
        <NoticeBanner
          variant="success"
          icon={<CheckCircle2 size={14} />}
          onDismiss={() => setNotice(null)}
          data-testid="boards-bulk-notice"
        >
          {notice}
        </NoticeBanner>
      )}

      {/* ── Selection action bar (list mode) ── */}
      {viewMode === 'list' && canWrite && !loading && !error && selectedBoards.length > 0 && (
        <SelectionBar data-testid="boards-selection-bar">
          <span className="text-sm font-medium text-fg">
            {selectedBoards.length} selected
          </span>
          <span className="text-muted" aria-hidden="true">·</span>
          <button
            type="button"
            onClick={toggleSelectAllVisible}
            className="text-sm font-medium text-primary hover:underline"
          >
            {allVisibleSelected ? 'Deselect all' : `Select all (${filtered.length})`}
          </button>
          <button
            type="button"
            onClick={clearSelection}
            className="text-sm font-medium text-muted hover:text-fg transition-colors"
          >
            Clear
          </button>
          <div className="flex-1" />
          <button
            type="button"
            data-testid="boards-delete-selected"
            onClick={openBulkDeleteSelected}
            className="inline-flex items-center gap-1.5 h-7 px-3 rounded-lg bg-danger text-white text-xs font-semibold hover:opacity-90 transition-opacity focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            <Trash2 size={12} />
            Delete {selectedBoards.length}
          </button>
        </SelectionBar>
      )}

      {/* ── Loading skeletons ── */}
      {loading && (
        <CardGrid>
          {Array.from({ length: 6 }).map((_, i) => (
            <CardSkeleton key={i} />
          ))}
        </CardGrid>
      )}

      {/* ── Error ── */}
      {!loading && error && (
        <ErrorState
          icon={<LayoutDashboard size={22} />}
          message={error?.message ?? String(error)}
          onRetry={reloadBoards}
        />
      )}

      {/* ── Empty state ── */}
      {!loading && !error && filtered.length === 0 && (
        <EmptyBoards
          hasFilter={search.length > 0 || Boolean(activeFolderId)}
          onClearFilter={() => { setSearch(''); setActiveFolderId(null) }}
          onAskAI={() => openChat?.()}
          canWrite={canWrite}
        />
      )}

      {/* ── Board grid ── */}
      {!loading && !error && filtered.length > 0 && viewMode === 'grid' && (
        <CardGrid>
          {filtered.map(board => (
            <BoardCard
              key={board.id}
              board={board}
              onDeleted={handleDeleted}
              onRestored={reloadBoards}
              canWrite={canWrite}
              environments={environments}
              strictEnv={strictEnv}
              folders={folders}
              onMoveToFolder={moveBoardToFolder}
              onNewFolderFromCard={(board) => setNewFolderState({ busy: false, forBoard: board })}
              linkState={activeFolderId ? { folder: activeFolderId } : undefined}
            />
          ))}
        </CardGrid>
      )}

      {/* ── Board list ── */}
      {!loading && !error && filtered.length > 0 && viewMode === 'list' && (
        <ListWrap>
          <ListHeader>
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
            <ListHeaderLabel>
              {filtered.length} dashboard{filtered.length === 1 ? '' : 's'}
              {search && ' (filtered)'}
            </ListHeaderLabel>
            {canWrite && (
              <button
                type="button"
                data-testid="boards-delete-all"
                onClick={openBulkDeleteAll}
                className="inline-flex items-center gap-1.5 h-6 px-2.5 rounded-lg border border-danger/30 text-danger text-xs font-medium hover:bg-danger-bg transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                <Trash2 size={11} />
                Delete all{search ? ' matching' : ''}
              </button>
            )}
          </ListHeader>

          {filtered.map(board => (
            <BoardListRow
              key={board.id}
              board={board}
              canWrite={canWrite}
              selected={selected.has(board.id)}
              onToggle={() => toggleSelected(board.id)}
              strictEnv={strictEnv}
              linkState={activeFolderId ? { folder: activeFolderId } : undefined}
            />
          ))}

          {/* Skeleton rows while loading next page (future pagination) */}
        </ListWrap>
      )}

      {/* ── Bulk-delete confirmation ── */}
      {bulkDialog && (
        <DangerConfirmDialog
          title={bulkDialog.all
            ? `Delete ALL ${bulkDialog.ids.length} dashboard${bulkDialog.ids.length === 1 ? '' : 's'}`
            : `Delete ${bulkDialog.ids.length} dashboard${bulkDialog.ids.length === 1 ? '' : 's'}`}
          description={bulkDialog.all
            ? (search
              ? `You are about to wipe every dashboard matching "${search}" in this project. Every widget, layout and share link on these boards will be destroyed.`
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

      {/* ── New folder ── */}
      {newFolderState && (
        <NewFolderDialog
          busy={newFolderState.busy}
          onCancel={() => setNewFolderState(null)}
          onCreate={(name) => createFolder(name, newFolderState.forBoard)}
        />
      )}

      {/* ── Delete folder ── */}
      {deleteFolderDialog && (
        <DeleteFolderDialog
          folder={deleteFolderDialog}
          count={boards.filter(b => folderIdOf(b.config) === deleteFolderDialog.id).length}
          busy={deleteFolderBusy}
          onCancel={() => (!deleteFolderBusy && setDeleteFolderDialog(null))}
          onConfirm={confirmDeleteFolder}
        />
      )}
    </PageRoot>
  )
}
