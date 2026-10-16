/**
 * DashboardsPage — /dashboards
 *
 * Lists all boards from GET /api/v1/boards.
 * Board shape: { id, name, config: { spec?: { widgets?: [] }, html?: string } }
 *
 * Features:
 *   - PageShell header with the one ink "New dashboard" action → /editor
 *   - Folder chips (Tags), search + sort, Grid ↔ List toggle. The list is
 *     compact rows (36px) and is the default on phones.
 *   - List mode adds multi-select checkboxes with a selection action bar (bulk
 *     delete) and a "Delete all" affordance — both gated by DangerConfirmDialog
 *     (type a random code to confirm).
 *   - Responsive grid: 1 col → sm:2 → lg:3
 *   - Per-card actions: Open, Edit, Delete (confirm dialog), plus versioning
 *     via the overflow menu: Checkpoint / History / Promote
 *   - Layout-matching skeletons, inline error with Retry, empty states from the
 *     illustration set. Light + dark via semantic tokens.
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import {
  BarChart2,
  Check,
  Code2,
  ExternalLink,
  Folder,
  FolderPlus,
  GitCommitHorizontal,
  History,
  MoreVertical,
  Pencil,
  Plus,
  Rocket,
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
import DeleteBoardDialog from '../../components/app/DeleteBoardDialog.jsx'
import { toast } from '../../components/ui/Toast.jsx'
import Button from '../../components/ui/Button.jsx'
import Tag from '../../components/ui/Tag.jsx'
import Modal from '../../components/ui/Modal.jsx'
import Input, { Checkbox } from '../../components/ui/Input.jsx'
import EmptyState from '../../components/ui/EmptyState.jsx'
import Skeleton from '../../components/ui/Skeleton.jsx'
import { EmptyBoards as EmptyBoardsArt, EmptySearch } from '../../components/illustrations'
import PageShell, {
  Toolbar,
  SearchBar,
  SortMenu,
  ViewToggle,
  CardGrid,
  NoticeBanner,
  SelectionBar,
  DropdownMenu,
  DropdownItem,
  DropdownDivider,
} from '../../components/app/PageShell.jsx'
import { useUi } from '../../contexts/UiContext.jsx'
import { useEnv } from '../../contexts/EnvContext.jsx'
import { useProject } from '../../contexts/ProjectContext.jsx'
import { useCanWrite } from '../../contexts/OrgContext.jsx'
import { useAsyncLoad } from '../../hooks/useAsyncLoad.js'
import { LoadError, ListSkeleton } from './appShared.jsx'
import '../../components/app/appPatterns.css'

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function boardMeta(config) {
  if (!config) return 'Dashboard'
  if (config.html) return 'HTML board'
  const count = config.spec?.widgets?.length ?? 0
  return count === 1 ? '1 widget' : `${count} widgets`
}

const SORT_OPTIONS = [
  { value: 'recent', label: 'Recent' },
  { value: 'name',   label: 'Name' },
]

function folderIdOf(config) {
  return config?.folderId || null
}

function formatDay(iso) {
  if (!iso) return null
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return null
  return d.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' })
}

// ---------------------------------------------------------------------------
// Folders (Tags, not pills)
// ---------------------------------------------------------------------------

function FolderChip({ label, count, active, dragOver, onSelect, onDragOver, onDragLeave, onDrop, onDelete = undefined, deleteLabel = undefined }) {
  return (
    <span
      className="inline-flex items-center group/chip"
      onDragOver={onDragOver}
      onDragLeave={onDragLeave}
      onDrop={onDrop}
    >
      <Tag
        as="button"
        type="button"
        size="md"
        caps={false}
        variant={active ? 'ink' : dragOver ? 'accent' : 'neutral'}
        onClick={onSelect}
        aria-pressed={active}
        className="whitespace-nowrap"
      >
        {onDelete && <Folder size={12} aria-hidden="true" />}
        {label}
        <span className="ap-mono opacity-70">{count}</span>
      </Tag>
      {onDelete && (
        <button
          type="button"
          onClick={(e) => { e.preventDefault(); e.stopPropagation(); onDelete() }}
          className="ml-0.5 flex h-6 w-6 items-center justify-center text-subtle opacity-0 hover:text-fg focus-visible:opacity-100 group-hover/chip:opacity-100 group-focus-within/chip:opacity-100 [@media(hover:none)]:opacity-100"
          aria-label={deleteLabel}
        >
          <X size={12} aria-hidden="true" />
        </button>
      )}
    </span>
  )
}

function FolderRail({ folders, boards, activeFolderId, canWrite, onSelect, onNewFolder, onDeleteFolder, onDropBoard }) {
  const [dragOverId, setDragOverId] = useState(null)

  if (folders.length === 0 && !canWrite) return null

  return (
    <div className="flex items-center gap-2 overflow-x-auto pb-1 -mx-1 px-1 mb-3" role="group" aria-label="Folders">
      <FolderChip
        label="All dashboards"
        count={boards.length}
        active={!activeFolderId}
        dragOver={dragOverId === '__all__'}
        onSelect={() => onSelect(null)}
        onDragOver={(e) => { e.preventDefault(); setDragOverId('__all__') }}
        onDragLeave={() => setDragOverId(null)}
        onDrop={(e) => { e.preventDefault(); setDragOverId(null); onDropBoard(e, null) }}
      />

      {folders.map(folder => (
        <FolderChip
          key={folder.id}
          label={folder.name}
          count={boards.filter(b => folderIdOf(b.config) === folder.id).length}
          active={activeFolderId === folder.id}
          dragOver={dragOverId === folder.id}
          onSelect={() => onSelect(folder.id)}
          onDelete={canWrite ? () => onDeleteFolder(folder) : undefined}
          deleteLabel={`Delete folder ${folder.name}`}
          onDragOver={(e) => { e.preventDefault(); setDragOverId(folder.id) }}
          onDragLeave={() => setDragOverId(null)}
          onDrop={(e) => { e.preventDefault(); setDragOverId(null); onDropBoard(e, folder.id) }}
        />
      ))}

      {canWrite && (
        <Button variant="ghost" size="xs" onClick={onNewFolder} className="whitespace-nowrap">
          <FolderPlus size={13} aria-hidden="true" />
          New folder
        </Button>
      )}
    </div>
  )
}

function NewFolderDialog({ onCreate, onCancel, busy }) {
  const [name, setName] = useState('')

  function submit(e) {
    e.preventDefault()
    const trimmed = name.trim()
    if (!trimmed || busy) return
    onCreate(trimmed)
  }

  return (
    <Modal open onClose={busy ? undefined : onCancel} title="New folder" description="Group related dashboards together." size="sm">
      <form onSubmit={submit}>
        <Modal.Body>
          <Input
            label="Folder name"
            autoFocus
            value={name}
            onChange={e => setName(e.target.value)}
            placeholder="Weekly sales"
            maxLength={80}
          />
        </Modal.Body>
        <Modal.Footer>
          <Button type="button" variant="secondary" onClick={onCancel} disabled={busy}>Cancel</Button>
          <Button type="submit" disabled={!name.trim()} loading={busy}>Create folder</Button>
        </Modal.Footer>
      </form>
    </Modal>
  )
}

function DeleteFolderDialog({ folder, count, onConfirm, onCancel, busy }) {
  return (
    <Modal open onClose={busy ? undefined : onCancel} title="Delete this folder?" size="sm">
      <Modal.Body>
        <p className="text-sm text-muted leading-relaxed">
          <b className="font-semibold text-fg">{folder.name}</b> will be deleted.
          {count > 0
            ? ` Its ${count} dashboard${count === 1 ? '' : 's'} will be ungrouped, not deleted.`
            : ' It is empty, so no dashboards are affected.'}
        </p>
      </Modal.Body>
      <Modal.Footer>
        <Button variant="secondary" onClick={onCancel} disabled={busy}>Cancel</Button>
        <Button variant="danger" onClick={onConfirm} loading={busy}>Delete folder</Button>
      </Modal.Footer>
    </Modal>
  )
}

function CheckpointDialog({ boardName, onSubmit, onCancel }) {
  const [message, setMessage] = useState('')
  const [busy, setBusy] = useState(false)
  async function submit(e) {
    e.preventDefault()
    setBusy(true)
    try { await onSubmit(message.trim()) } finally { setBusy(false) }
  }
  return (
    <Modal open onClose={busy ? undefined : onCancel} title="Create a checkpoint" description={`Saves the current state of ${boardName} as a named version you can restore or promote.`} size="sm">
      <form onSubmit={submit}>
        <Modal.Body>
          <Input
            label="Message"
            hint="Optional. A line about what changed."
            autoFocus
            value={message}
            onChange={e => setMessage(e.target.value)}
            placeholder="Q3 targets added"
          />
        </Modal.Body>
        <Modal.Footer>
          <Button type="button" variant="secondary" onClick={onCancel} disabled={busy}>Cancel</Button>
          <Button type="submit" loading={busy}>Create checkpoint</Button>
        </Modal.Footer>
      </form>
    </Modal>
  )
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

/**
 * CardThumbnail — the board, shown whole on a sunk, hairline-framed sheet.
 *
 * The render keeps its own aspect and is never cropped: `object-cover` would
 * quietly amputate a wide board's edge widgets, and a card exists to answer
 * "which dashboard is this?". No gradient wash, no shadow, no lift: the card
 * border turns ink on hover (see .nubi-resource-card).
 *
 * BoardThumbnail is itself two layers: the spec-derived <BoardMiniature> (drawn
 * instantly from the widgets' real grid positions) and, swapped in when it
 * arrives, the board rendered server-side with its real data.
 *
 * Boards with no truthful layout (legacy HTML boards, specs without geometry)
 * show a plain glyph tile instead.
 */
function CardThumbnail({ board }) {
  const isHtml = Boolean(board.config?.html)
  // Ask the pure model, not the component — calling BoardMiniature() directly
  // would run its hooks inside this component.
  const hasLayout = !isHtml && buildMiniature(board.config?.spec) !== null

  if (!hasLayout) {
    return (
      <div className="relative flex aspect-video w-full items-center justify-center overflow-hidden border-b border-border bg-surface-2">
        <span className="flex h-10 w-10 items-center justify-center border border-frame-line bg-surface text-muted" style={{ borderRadius: 2 }}>
          {isHtml
            ? <Code2 size={18} strokeWidth={1.5} aria-hidden="true" />
            : <BarChart2 size={18} strokeWidth={1.5} aria-hidden="true" />}
        </span>
        <span className="absolute bottom-2 left-3 ap-count">{isHtml ? 'HTML board' : 'No layout yet'}</span>
      </div>
    )
  }

  return (
    <div className="relative aspect-video w-full overflow-hidden border-b border-border bg-surface-2">
      <div className="absolute inset-x-3 top-3 bottom-0">
        <div
          className="h-full w-full overflow-hidden bg-surface"
          style={{ border: '1px solid var(--frame-line)', borderBottom: 'none', borderRadius: '2px 2px 0 0' }}
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
        className="flex h-8 w-8 items-center justify-center border border-frame-line bg-surface text-muted transition-colors hover:border-ink hover:text-fg"
        style={{ borderRadius: 2 }}
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
            <div className="px-3 pt-1.5 pb-1 ap-count">Move to folder</div>
            {folders.map(folder => (
              <DropdownItem
                key={folder.id}
                icon={Folder}
                onClick={() => { setOpen(false); onMoveToFolder(folder.id === currentFolderId ? null : folder.id) }}
              >
                <span className="flex-1 text-left truncate">{folder.name}</span>
                {folder.id === currentFolderId && <Check size={13} className="text-accent shrink-0" aria-hidden="true" />}
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

function StrictEnvTag({ pinned_envs, strictEnv }) {
  if (!strictEnv || !Array.isArray(pinned_envs)) return null
  if (pinned_envs.includes(strictEnv)) return null
  return (
    <Tag
      variant="warning"
      dot
      caps={false}
      title={`No version is pinned to ${strictEnv}. Promote one to make it visible there.`}
    >
      not in {strictEnv}
    </Tag>
  )
}

function BoardCard({ board, onDeleted, onRestored, canWrite, environments, strictEnv, folders, onMoveToFolder, onNewFolderFromCard, linkState }) {
  const navigate = useNavigate()
  const [confirmDelete, setConfirmDelete] = useState(false)
  const [deleteBusy, setDeleteBusy] = useState(false)
  const [historyOpen, setHistoryOpen] = useState(false)
  const [promoteOpen, setPromoteOpen] = useState(false)
  const [checkpointOpen, setCheckpointOpen] = useState(false)

  async function handleCheckpoint(message) {
    try {
      const v = await checkpoint('board', board.id, { message: message || undefined })
      toast.success(v?.deduped
        ? `No changes since v${v.version}. The existing version was reused.`
        : `Created version v${v?.version}.`)
      setCheckpointOpen(false)
    } catch (err) {
      toast.error(err?.message || 'Checkpoint failed.')
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
  const name = board.name || 'Untitled dashboard'

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
            not a child — a link inside a link is invalid HTML. */}
        <div className="relative">
          <Link to={`/d/${board.id}`} state={linkState} className="block" tabIndex={-1} aria-hidden="true">
            <CardThumbnail board={board} />
          </Link>

          {/* One affordance in the corner. Kept mounted and revealed with
              opacity (never display:none) so it stays tabbable; focus-within
              covers keyboards and hover:none devices show it always. */}
          <div className="absolute top-2 right-2 opacity-0 transition-opacity duration-100 group-hover:opacity-100 group-focus-within:opacity-100 [@media(hover:none)]:opacity-100">
            <CardMenu
              onOpen={() => navigate(`/d/${board.id}`, { state: linkState })}
              onEdit={canWrite ? () => navigate(`/d/${board.id}/edit`) : undefined}
              onCheckpoint={canWrite ? () => setCheckpointOpen(true) : undefined}
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

        <div className="nubi-resource-card-body" style={{ gap: 4, paddingBlock: 12 }}>
          <Link
            to={`/d/${board.id}`}
            state={linkState}
            className="block truncate text-sm font-semibold leading-snug text-fg hover:underline underline-offset-4"
          >
            {name}
          </Link>
          <p className="m-0 flex flex-wrap items-center gap-2 ap-meta">
            {boardMeta(board.config)}
            <StrictEnvTag pinned_envs={board.pinned_envs} strictEnv={strictEnv} />
          </p>
        </div>
      </article>

      {confirmDelete && (
        <DeleteBoardDialog
          board={board}
          onConfirm={handleDelete}
          onCancel={() => setConfirmDelete(false)}
          busy={deleteBusy}
        />
      )}

      {checkpointOpen && (
        <CheckpointDialog boardName={name} onSubmit={handleCheckpoint} onCancel={() => setCheckpointOpen(false)} />
      )}

      <VersionHistoryDialog
        kind="board"
        resourceId={board.id}
        resourceName={name}
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
  const updatedLabel = formatDay(board.updated_at ?? board.created_at)
  const name = board.name || 'Untitled dashboard'

  return (
    <div
      className={`ap-row ap-row-compact ${selected ? 'is-selected' : ''}`}
      data-testid="board-list-row"
      style={{ flexWrap: 'nowrap', alignItems: 'center' }}
    >
      {canWrite && (
        <Checkbox
          checked={selected}
          onChange={onToggle}
          aria-label={`Select ${name}`}
        />
      )}

      <span
        className="hidden sm:flex h-6 w-6 shrink-0 items-center justify-center border border-frame-line text-muted"
        style={{ borderRadius: 2 }}
        aria-hidden="true"
      >
        {board.config?.html
          ? <Code2 size={13} strokeWidth={1.5} />
          : <BarChart2 size={13} strokeWidth={1.5} />}
      </span>

      <div className="ap-row-main" style={{ flexBasis: 0 }}>
        <div className="ap-row-title" style={{ fontSize: 14 }}>
          <Link to={`/d/${board.id}`} state={linkState} className="ap-trunc">{name}</Link>
          <StrictEnvTag pinned_envs={board.pinned_envs} strictEnv={strictEnv} />
        </div>
      </div>

      <span className="ap-meta shrink-0 hidden sm:block">{boardMeta(board.config)}</span>
      {updatedLabel && <span className="ap-meta shrink-0 hidden md:block w-28 text-right">{updatedLabel}</span>}

      <div className="flex shrink-0 items-center gap-0.5" style={{ width: 'auto' }}>
        <Button asChild variant="ghost" size="icon-xs">
          <Link to={`/d/${board.id}`} state={linkState} title="Open" aria-label={`Open ${name}`}>
            <ExternalLink size={14} aria-hidden="true" />
          </Link>
        </Button>
        {canWrite && (
          <Button asChild variant="ghost" size="icon-xs">
            <Link to={`/d/${board.id}/edit`} title="Edit" aria-label={`Edit ${name}`}>
              <Pencil size={14} aria-hidden="true" />
            </Link>
          </Button>
        )}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Empty state
// ---------------------------------------------------------------------------

function BoardsEmpty({ hasFilter, onClearFilter, onAskAI, canWrite }) {
  if (hasFilter) {
    return (
      <EmptyState
        kind="none-match"
        art={<EmptySearch />}
        title="No dashboards match"
        description="Nothing in this search or folder. Clear the filters to see every dashboard in the project."
        action={<Button variant="secondary" onClick={onClearFilter}>Clear filters</Button>}
      />
    )
  }

  return (
    <EmptyState
      art={<EmptyBoardsArt />}
      title={canWrite ? 'No dashboards yet' : 'Nothing to view yet'}
      description={canWrite
        ? 'A dashboard is a set of charts that share filters, built from your queries. Start blank, or describe one to the assistant.'
        : 'No dashboards have been published to this workspace. You have read-only access, so ask an editor to add one.'}
      action={canWrite ? (
        <Button asChild>
          <Link to="/editor"><Plus size={14} aria-hidden="true" /> New dashboard</Link>
        </Button>
      ) : undefined}
      secondaryAction={canWrite ? (
        <Button variant="ghost" onClick={onAskAI}><Sparkles size={14} aria-hidden="true" /> Ask the assistant</Button>
      ) : undefined}
    />
  )
}

/** Card-shaped skeleton: 16:9 picture, title, meta (same box as .nubi-resource-card). */
function BoardCardSkeleton() {
  return (
    <div className="nubi-resource-card" aria-hidden="true">
      <Skeleton className="aspect-video w-full" />
      <div className="flex flex-col gap-2 p-4">
        <Skeleton className="h-3.5 w-2/3" />
        <Skeleton className="h-3 w-1/4" />
      </div>
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

  // Phones default to the compact list (one board per 36px row); a stored
  // choice always wins.
  const [viewMode, setViewMode] = useState(() => {
    try {
      const stored = localStorage.getItem('nubi-dashboards-view')
      if (stored === 'list' || stored === 'grid') return stored
    } catch { /* private mode */ }
    try {
      return window.matchMedia('(max-width: 639px)').matches ? 'list' : 'grid'
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

  const countLabel = `${boards.length} dashboard${boards.length === 1 ? '' : 's'}`
  const eyebrow = loading || error ? 'Build' : `Build · ${countLabel}`
  const hasFilter = search.length > 0 || Boolean(activeFolderId)
  const linkState = activeFolderId ? { folder: activeFolderId } : undefined

  return (
    <PageShell
      eyebrow={eyebrow}
      title="Dashboards"
      description="Boards your team builds from queries and shares with viewers."
      actions={canWrite ? (
        <Button asChild>
          <Link to="/editor"><Plus size={14} aria-hidden="true" /> New dashboard</Link>
        </Button>
      ) : (
        <Tag title="You can view dashboards but not change them">Read-only</Tag>
      )}
      toolbar={!loading && !error && boards.length > 0 ? (
        <Toolbar>
          <SearchBar value={search} onChange={setSearch} placeholder="Search dashboards" />
          <SortMenu value={sort} onChange={setSort} options={SORT_OPTIONS} />
          <ViewToggle value={viewMode} onChange={changeViewMode} />
        </Toolbar>
      ) : undefined}
    >
      {/* ── Folder chips ── */}
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

      {/* ── Bulk-delete success notice ── */}
      {notice && (
        <NoticeBanner
          variant="success"
          onDismiss={() => setNotice(null)}
          data-testid="boards-bulk-notice"
        >
          {notice}
        </NoticeBanner>
      )}

      {/* ── Selection action bar (list mode) ── */}
      {viewMode === 'list' && canWrite && !loading && !error && selectedBoards.length > 0 && (
        <SelectionBar data-testid="boards-selection-bar">
          <span className="text-sm font-semibold text-fg ap-mono">
            {selectedBoards.length} selected
          </span>
          <button type="button" onClick={toggleSelectAllVisible} className="ap-link-btn" style={{ color: 'var(--accent)' }}>
            {allVisibleSelected ? 'Deselect all' : `Select all ${filtered.length}`}
          </button>
          <button type="button" onClick={clearSelection} className="ap-link-btn">Clear</button>
          <div className="flex-1" />
          <Button
            variant="danger"
            size="xs"
            data-testid="boards-delete-selected"
            onClick={openBulkDeleteSelected}
          >
            <Trash2 size={13} aria-hidden="true" />
            Delete {selectedBoards.length}
          </Button>
        </SelectionBar>
      )}

      {/* ── Loading skeletons (layout-matching; hidden for the first 300ms) ── */}
      {loading && (viewMode === 'grid' ? (
        <CardGrid>
          {Array.from({ length: 6 }).map((_, i) => <BoardCardSkeleton key={i} />)}
        </CardGrid>
      ) : (
        <ListSkeleton rows={6} tile={false} />
      ))}

      {/* ── Error ── */}
      {!loading && error && (
        <LoadError title="Dashboards didn’t load" error={error} onRetry={reloadBoards} />
      )}

      {/* ── Empty state ── */}
      {!loading && !error && filtered.length === 0 && (
        <BoardsEmpty
          hasFilter={hasFilter}
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
              linkState={linkState}
            />
          ))}
        </CardGrid>
      )}

      {/* ── Board list: compact 36px rows ── */}
      {!loading && !error && filtered.length > 0 && viewMode === 'list' && (
        <div className="ap-list">
          <div className="ap-list-head">
            {canWrite && (
              <Checkbox
                checked={allVisibleSelected}
                indeterminate={!allVisibleSelected && selectedBoards.length > 0}
                onChange={toggleSelectAllVisible}
                aria-label="Select all visible dashboards"
                data-testid="boards-select-all"
              />
            )}
            <span className="flex-1" aria-live="polite">
              {filtered.length} dashboard{filtered.length === 1 ? '' : 's'}
              {hasFilter && ' (filtered)'}
            </span>
            <span className="hidden sm:block">Contents</span>
            <span className="hidden md:block w-28 text-right">Updated</span>
            {canWrite ? (
              <button
                type="button"
                data-testid="boards-delete-all"
                onClick={openBulkDeleteAll}
                className="ap-clear"
                style={{ color: 'var(--danger-fg)' }}
              >
                Delete all{hasFilter ? ' matching' : ''}
              </button>
            ) : <span className="w-14" />}
          </div>

          {filtered.map(board => (
            <BoardListRow
              key={board.id}
              board={board}
              canWrite={canWrite}
              selected={selected.has(board.id)}
              onToggle={() => toggleSelected(board.id)}
              strictEnv={strictEnv}
              linkState={linkState}
            />
          ))}
        </div>
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
    </PageShell>
  )
}
