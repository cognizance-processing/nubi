/**
 * DashboardViewPage.jsx — Route component for /d/:id
 *
 * Loads a board by id via GET /boards/:id, then:
 *   - If board.config.spec is present → renders via <SpecRenderer> (new path).
 *   - Else if board.config.html is present → renders via <DashboardView> (legacy HTML path).
 *   - Else → shows an honest "no content yet" notice.
 *
 * A failed load NEVER renders the sample dashboard: fabricated rows under a real
 * board's name is how a broken board comes to look like a working one. The board
 * fetch is org-scoped, so it also waits for the active workspace to be restored
 * before firing — a cold-pasted deep link used to lose that race and 404.
 *
 * Special cases:
 *   /d/sample  — renders the built-in sample dashboard without a backend request.
 *
 * Variable / URL integration (M14-C)
 * ------------------------------------
 * For spec dashboards, DashboardViewPage manages the variable store seed values:
 *
 *   Precedence (highest → lowest):
 *     1. Embed-token-locked params (wired — see `embedLockedParams` below)
 *     2. URL search params (?varName=value)
 *     3. spec.variables defaults
 *
 * When a filter widget changes a variable, the new value is written back to the
 * URL via setSearchParams (shallow replace, so no extra history entry).
 *
 * Embed-token integration:
 *   `embedLockedParams` reads `_token`/`_embed` off the URL, client-side
 *   base64-decodes the JWT payload via `decodeJwtPayload` (dashboards/embedLock.js),
 *   and extracts `locked_params`.  Those locked values:
 *     a) Override URL params (the token wins — merged last into initialVariables).
 *     b) Are stripped from `knownVarNames`, so filter widgets/URL sync cannot
 *        write to a locked variable name.
 *   Fail-closed: an absent/malformed token yields `embedLockedParams = {}` (no
 *   access widening). The client-side decode is trust-on-read only — the
 *   server independently verifies the token signature and re-enforces the lock.
 */

import { useState, useEffect, useMemo, useCallback, useRef } from 'react'
import { useParams, useLocation, Link, useSearchParams, useNavigate } from 'react-router-dom'
import { ArrowLeft, Pencil, Eye, Rocket, Sun, Moon, MoreVertical, Trash2, History, Undo2 } from 'lucide-react'
import { get, del } from '../lib/api.js'
import DashboardView from '../dashboards/DashboardView.jsx'
import SpecRenderer from '../dashboards/SpecRenderer.jsx'
import { backgroundToCss } from '../dashboards/widgetHtml.js'
import EditorShell from '../editor/EditorShell.jsx'
import { useUi } from '../contexts/UiContext.jsx'
import { useEnv } from '../contexts/EnvContext.jsx'
import { pushToLive, resolveLiveEnvKey, resolveLiveRender, canPushToLive } from '../lib/liveBoard.js'
import { toast } from '../components/ui/Toast.jsx'
import ExportShareMenu from '../components/ExportShareMenu.jsx'
import DeleteBoardDialog from '../components/app/DeleteBoardDialog.jsx'
import { DropdownMenu, DropdownItem } from '../components/app/PageShell.jsx'
import { extractVarsFromURL, applyVarToSearchParams } from '../dashboards/urlSync.js'
import { decodeJwtPayload } from '../dashboards/embedLock.js'
import { useCanWrite, useOrg } from '../contexts/OrgContext.jsx'
import { useProject } from '../contexts/ProjectContext.jsx'
import { useTheme } from '../contexts/ThemeContext.jsx'
import { boardColorMode } from '../lib/boardTheme.js'
import { colorModeClass } from '../dashboards/ColorModeScope.jsx'
import Skeleton from '../components/ui/Skeleton.jsx'
import Tag from '../components/ui/Tag.jsx'
import Button from '../components/ui/Button.jsx'
import Modal from '../components/ui/Modal.jsx'
import EmptyState from '../components/ui/EmptyState.jsx'
import { ErrorFigure, EmptyBoards } from '../components/illustrations/index.js'
import VersionHistoryDialog from '../components/app/VersionHistoryDialog.jsx'
import { BoardHealthProvider, BoardErrorBanner } from '../dashboards/widgets/boardHealth.jsx'
import { restoreVersion } from '../lib/versions.js'
import { summarizeSpecChanges, describeChanges, describeBreakdown, relativeTime } from '../editor/boardMeta.js'
import '../dashboards/widgets/chrome.css'

// ---------------------------------------------------------------------------
// Built-in sample dashboard HTML
// ---------------------------------------------------------------------------

export const SAMPLE_DASHBOARD_HTML = `
<header style="padding:1.5rem 2rem; background:var(--ink); border-radius:2px; margin-bottom:1.5rem; color:var(--ink-fg);">
  <h1 style="margin:0;font-size:1.5rem;font-weight:700;letter-spacing:-0.02em;font-family:'Space Grotesk',sans-serif;">Nubi Sample Dashboard</h1>
  <p style="margin:0.5rem 0 0;opacity:0.85;font-size:0.875rem;">
    Powered by <strong>nubi-kpi</strong>, <strong>nubi-table</strong>, and <strong>nubi-chart</strong> widgets.
    Each widget fetches live Arrow data via the registered query <code style="border:1px solid currentColor;padding:0 0.3em;border-radius:2px;">demo_all</code>.
  </p>
</header>

<section style="display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:1rem;margin-bottom:1.5rem;">
  <nubi-kpi
    query-id="demo_all"
    value-col="n"
    label="Total Records"
    format="integer"
  ></nubi-kpi>

  <nubi-kpi
    query-id="demo_all"
    value-col="x"
    label="Sample X"
    format="number"
  ></nubi-kpi>

  <nubi-kpi
    query-id="demo_all"
    value-col="y"
    label="Sample Y"
    format="number"
  ></nubi-kpi>
</section>

<section style="margin-bottom:1.5rem;">
  <h2 style="font-size:1rem;font-weight:600;color:var(--text);margin:0 0 0.75rem;">Data Table</h2>
  <nubi-table
    query-id="demo_all"
    limit="25"
  ></nubi-table>
</section>

<section>
  <h2 style="font-size:1rem;font-weight:600;color:var(--text);margin:0 0 0.75rem;">Scatter Chart</h2>
  <nubi-chart
    query-id="demo_all"
    type="scatter"
    x="x"
    y="y"
    color="category"
  ></nubi-chart>
</section>
`

// ---------------------------------------------------------------------------
// ViewToolbar — the only navigation chrome on this full-viewport route.
//
// /d/:id deliberately renders outside AppShell (no sidebar/navbar) so a
// filter-locked, embed-token'd view (?_token=/&_embed=) stays clean for an
// external viewer — see the `isEmbedView` gate at the call site. For every
// other visit (a teammate opening a dashboard from inside the app) this bar
// is the only way back in, so it always renders in that case.
//
// Anatomy (live):  ← Dashboards / Workspace / Title   LIVE v3   updated 2h ago   …   theme  share  [Live|Edit]  ⋮
// Anatomy (edit):  ← Dashboards  [Live|Edit]  <editor toolbar portal>  ⋮
// The draft bar (below) is a separate row, so the header never has to carry the
// push / discard actions next to the editor's own toolbar.
// ---------------------------------------------------------------------------

/**
 * ModeSwitch — the Live ↔ Edit control.
 *
 * This is the spine of the merged surface: one board, one URL, two modes. It
 * mirrors the control every mature BI tool converged on (Power BI's Reading /
 * Editing views, Looker Studio's View / Edit) rather than sending an author to a
 * different page to change the thing they're looking at. The active segment is
 * ink: this is a mutually exclusive choice, and blue is reserved for selection
 * inside the board itself.
 */
function ModeSwitch({ mode, onChange }) {
  const items = [
    { id: 'live', label: 'Live', Icon: Eye, title: 'Live view: what viewers see' },
    { id: 'edit', label: 'Edit', Icon: Pencil, title: 'Edit this dashboard' },
  ]
  return (
    <div className="nbc-seg" role="group" aria-label="Dashboard mode" data-testid="dashboard-mode-switch">
      {items.map(it => (
        <button
          key={it.id}
          type="button"
          onClick={() => onChange(it.id)}
          aria-pressed={mode === it.id}
          title={it.title}
          data-testid={`dashboard-mode-${it.id}`}
        >
          <it.Icon size={14} strokeWidth={1.5} aria-hidden="true" />
          <span className="hidden sm:inline">{it.label}</span>
        </button>
      ))}
    </div>
  )
}

/**
 * BoardOptionsMenu — board-level actions that aren't worth a toolbar slot.
 *
 * Delete lives here rather than as a bare toolbar button for the reason every
 * BI tool puts it behind an overflow menu: a one-click destructive control
 * sitting next to Save and Push to live is a mis-click waiting to happen. It
 * renders in BOTH modes, because "I opened the board and it's wrong" is just as
 * often a live-mode realisation as an editing one.
 *
 * On a phone the header has no room for the theme switch, so it moves in here.
 */
function BoardOptionsMenu({ onDelete, onHistory = undefined, onToggleTheme = undefined, isDark = false }) {
  const [open, setOpen] = useState(false)

  return (
    <div className="relative shrink-0">
      <button
        type="button"
        onClick={() => setOpen(v => !v)}
        aria-label="Board options"
        aria-expanded={open}
        aria-haspopup="menu"
        data-testid="dashboard-options-menu"
        className="nbc-tool nbc-tool-bordered !px-0"
      >
        <MoreVertical size={16} strokeWidth={1.5} aria-hidden="true" />
      </button>

      <DropdownMenu open={open} onClose={() => setOpen(false)}>
        {onHistory && (
          <DropdownItem
            icon={History}
            className="whitespace-nowrap"
            data-testid="dashboard-history"
            onClick={() => { setOpen(false); onHistory() }}
          >
            Version history
          </DropdownItem>
        )}
        {onToggleTheme && (
          <DropdownItem
            icon={isDark ? Sun : Moon}
            className="whitespace-nowrap sm:hidden"
            onClick={() => { setOpen(false); onToggleTheme() }}
          >
            {isDark ? 'Switch to light' : 'Switch to dark'}
          </DropdownItem>
        )}
        <DropdownItem
          icon={Trash2}
          danger
          // The menu's containing block is the 32px trigger wrapper, so
          // shrink-to-fit stops at .nubi-dropdown's 10rem min-width and the
          // label wraps. nowrap lets it size to its text instead.
          className="whitespace-nowrap"
          data-testid="dashboard-delete"
          onClick={() => { setOpen(false); onDelete() }}
        >
          Delete dashboard
        </DropdownItem>
      </DropdownMenu>
    </div>
  )
}

/**
 * ViewToolbar — the single header for BOTH modes.
 *
 * In edit mode the embedded DashboardEditor portals its own toolbar into
 * `editorSlotRef` (via UiContext's topbarSlot), so the board keeps ONE header
 * instead of the old stack of two. The view-mode actions (theme, share)
 * collapse away in edit mode because the editor's toolbar already carries its
 * own.
 */
function ViewToolbar({
  backTo, title, boardId, spec, canEdit, mode, onModeChange = undefined, editorSlotRef = undefined,
  isLive = false, liveVersion = null, updatedAt = null,
  canDelete = false, onDelete = undefined, onHistory = undefined,
}) {
  const { activeOrg } = useOrg()
  const { activeProject } = useProject()
  const { theme, toggleTheme } = useTheme()
  const isDark = theme === 'dark'
  const isEdit = mode === 'edit'

  const orgProject = [activeOrg?.name, activeProject?.name].filter(Boolean).join(' / ')
  const updated = relativeTime(updatedAt)

  return (
    <header
      className="nbc-strip sticky top-0 z-40 min-h-14 shrink-0 flex-wrap gap-x-2 gap-y-0 py-1.5 sm:flex-nowrap sm:py-0"
      data-testid="dashboard-header"
    >
      {/* Back */}
      <Link
        to={backTo}
        aria-label="Back to dashboards"
        className="nbc-tool !px-0"
      >
        <ArrowLeft size={16} strokeWidth={1.5} aria-hidden="true" />
      </Link>

      {/* Title block. On a phone the title takes the full line and the status
          and time sit under it; from sm up it is one row. In edit mode the
          editor's own toolbar owns the (editable) title, so we drop ours rather
          than show it twice. */}
      <div className="flex min-w-0 flex-1 basis-0 flex-col justify-center sm:flex-initial sm:basis-auto sm:flex-row sm:items-center sm:gap-3">
        <nav aria-label="Breadcrumb" className="flex min-w-0 items-center gap-2 text-[13px]">
          <Link
            to={backTo}
            className="hidden sm:inline shrink-0 text-muted hover:text-fg rounded-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            Dashboards
          </Link>
          {orgProject && !isEdit && (
            <>
              <span aria-hidden="true" className="hidden md:inline text-subtle">/</span>
              <span className="hidden md:inline truncate max-w-[16rem] shrink text-muted" title={orgProject}>
                {orgProject}
              </span>
            </>
          )}
          {title && !isEdit && (
            <>
              <span aria-hidden="true" className="hidden sm:inline text-subtle">/</span>
              <h1 className="m-0 min-w-0 truncate text-sm font-semibold text-fg" title={title}>
                {title}
              </h1>
            </>
          )}
        </nav>

        {/* Live provenance: which version viewers are actually on. A board that
            has never been pushed says so rather than implying it's published. */}
        {!isEdit && (canEdit || updated) && (
          <div className="flex min-w-0 items-center gap-2">
            {canEdit && (
              isLive ? (
                <Tag
                  variant="success"
                  dot
                  title="Viewers are seeing this published version"
                  data-testid="dashboard-live-version"
                  className="shrink-0"
                >
                  Live{liveVersion ? ` · v${liveVersion}` : ''}
                </Tag>
              ) : (
                <Tag
                  variant="warning"
                  dot
                  title="This board has never been pushed live. Viewers see the draft."
                  data-testid="dashboard-not-pushed"
                  className="shrink-0"
                >
                  Draft · not pushed
                </Tag>
              )
            )}
            {updated && (
              <span className="nbc-meta truncate" title={updatedAt ? new Date(updatedAt).toLocaleString() : undefined}>
                Updated {updated}
              </span>
            )}
          </div>
        )}
      </div>

      {/* The editor's toolbar lands here in edit mode (createPortal via
          UiContext.topbarSlot). Always mounted so the ref is attached before
          the editor first renders and looks the slot up. On a phone it wraps
          to its own full-width line under the mode switch. */}
      <div
        ref={editorSlotRef}
        className={isEdit ? 'flex min-w-0 flex-1 items-center max-sm:order-last max-sm:basis-full max-sm:pt-1' : 'hidden'}
      />

      {!isEdit && <span className="hidden flex-1 sm:block" />}

      {/* View-mode actions. Edit mode's equivalents live in the editor toolbar. */}
      {!isEdit && (
        <div className="flex shrink-0 items-center gap-1.5">
          <button
            type="button"
            onClick={toggleTheme}
            aria-pressed={isDark}
            aria-label={isDark ? 'Switch to light mode' : 'Switch to dark mode'}
            title={isDark ? 'Switch to light mode' : 'Switch to dark mode'}
            className="nbc-tool nbc-tool-bordered !px-0 !hidden sm:!inline-flex"
          >
            {isDark ? <Sun size={16} strokeWidth={1.5} /> : <Moon size={16} strokeWidth={1.5} />}
          </button>

          {boardId && <ExportShareMenu board={boardId} spec={spec} />}
        </div>
      )}

      {canEdit && (
        <div className="shrink-0">
          <ModeSwitch mode={mode} onChange={onModeChange} />
        </div>
      )}

      {/* Board-level overflow — the same menu in both modes, so an author
          never has to leave the board to get rid of it. Gated on canDelete,
          NOT canEdit: a legacy HTML board (or one saved with no content yet)
          can't be opened in the spec editor, and those are precisely the
          boards someone wants to get rid of. */}
      {canDelete && boardId && onDelete && (
        <BoardOptionsMenu
          onDelete={onDelete}
          onHistory={canEdit ? onHistory : undefined}
          onToggleTheme={isEdit ? undefined : toggleTheme}
          isDark={isDark}
        />
      )}
    </header>
  )
}

/**
 * DraftBar — the persistent answer to "what will viewers see, and what have I
 * changed since?".
 *
 *   Edit:  ■ DRAFT   3 unpushed changes   2 edited · 1 moved        Discard  [Push to live]
 *   Live:  ■ DRAFT   The draft has 3 unpushed changes.                       Open draft
 *
 * "Unpushed" is measured against the live version's spec, so it counts real
 * differences (a widget added, edited, moved or removed, or a board setting
 * changed), not saves. Discard resets the draft to the live version.
 */
function DraftBar({
  isEdit, isLive, liveVersion, summary, dirty, pushing, pushable, discarding,
  onPush, onDiscard, onOpenDraft,
}) {
  const total = summary?.total ?? 0
  const breakdown = summary ? describeBreakdown(summary) : ''
  const canDiscard = isEdit && isLive && total > 0

  return (
    <div className="nbc-strip nbc-draftbar" data-testid="dashboard-draft-bar" role="region" aria-label="Draft status">
      <Tag variant="warning" dot className="shrink-0">Draft</Tag>

      <div className="flex min-w-0 flex-1 flex-wrap items-center gap-x-3 gap-y-0.5">
        {!isLive ? (
          <span className="font-medium text-fg">Not live yet</span>
        ) : (
          <span className="font-medium text-fg" data-testid="dashboard-unpushed-count">
            <span className="sm:hidden">{total} unpushed</span>
            <span className="hidden sm:inline">
              {isEdit ? describeChanges(total) : `The draft has ${describeChanges(total)}`}
            </span>
          </span>
        )}
        {isLive && breakdown && <span className="nbc-meta hidden md:inline">{breakdown}</span>}
        {isLive && total === 0 && (
          <span className="nbc-meta hidden sm:inline">Matches live{liveVersion ? ` v${liveVersion}` : ''}</span>
        )}
        {!isLive && <span className="hidden text-muted sm:inline">Viewers see this draft until you push it.</span>}
        {dirty && (
          <Tag size="sm" className="shrink-0" data-testid="dashboard-draft-pending" title="Edits not saved to the draft yet">
            Unsaved
          </Tag>
        )}
      </div>

      {isEdit ? (
        <div className="flex shrink-0 items-center gap-1.5">
          {isLive && (
            <Button
              size="xs"
              variant="ghost"
              disabled={!canDiscard || discarding || pushing}
              onClick={onDiscard}
              data-testid="dashboard-discard"
              title={canDiscard ? `Reset the draft to live${liveVersion ? ` v${liveVersion}` : ''}` : 'Nothing to discard'}
            >
              <Undo2 size={14} strokeWidth={1.5} aria-hidden="true" />
              <span className="max-sm:sr-only">Discard</span>
            </Button>
          )}
          <Button
            size="xs"
            onClick={onPush}
            disabled={pushing || !pushable}
            loading={pushing}
            data-testid="dashboard-push-live"
            title={
              pushable
                ? 'Publish the current draft so viewers see it'
                : `Live is already up to date${liveVersion ? ` (v${liveVersion})` : ''}`
            }
          >
            {!pushing && <Rocket size={14} strokeWidth={1.5} aria-hidden="true" />}
            {pushing ? 'Pushing…' : 'Push to live'}
          </Button>
        </div>
      ) : (
        <Button size="xs" variant="secondary" onClick={onOpenDraft} className="shrink-0">
          <Pencil size={14} strokeWidth={1.5} aria-hidden="true" />
          Open draft
        </Button>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// URL ↔ variable store sync helpers live in the pure, unit-tested
// ./../dashboards/urlSync.js module (extractVarsFromURL / applyVarToSearchParams),
// imported above.
// ---------------------------------------------------------------------------
// DashboardViewPage
// ---------------------------------------------------------------------------

export default function DashboardViewPage({ edit = false }) {
  const { id } = useParams()
  const location = useLocation()
  const navigate = useNavigate()
  const [searchParams, setSearchParams] = useSearchParams()
  const { setTopbarSlot } = useUi()

  // Full navigation chrome is suppressed only for a genuine filter-locked
  // embed view (a share link opened with ?_token=/&_embed=) — see the
  // ExportShareMenu "Share" tab, which mints exactly this kind of URL for
  // external viewers. Every other visit (a teammate browsing from /dashboards,
  // a bookmarked link, a fresh tab) gets the toolbar; it's the only way back
  // into the app since this route renders outside AppShell.
  const isEmbedView = Boolean(searchParams.get('_token') || searchParams.get('_embed'))
  const backTo = location.state?.folder ? `/dashboards?folder=${location.state.folder}` : '/dashboards'

  // Viewers (read-only) cannot edit — hide the mode switch entirely.
  const canWrite = useCanWrite()

  // Live-mode custom background (spec.background) is propagated up to this
  // page's own chrome (not just SpecRenderer's inner box) so a board with a
  // custom background paints edge-to-edge instead of leaving a seam of the
  // default theme background around a padded, rounded inner card — see
  // SpecRenderer's `flushBackground` prop, which this page sets in tandem so
  // SpecRenderer stops drawing its own (now redundant) padded/rounded box.
  const { theme: pageTheme } = useTheme()

  // The board fetch is org-scoped (api.js sends X-Org-Id from the active
  // workspace), so the load effect below must wait for the workspace to be
  // restored — see the comment there.
  const { activeOrg: viewOrg, loading: orgLoading } = useOrg()

  // ── Live ↔ Edit mode ──────────────────────────────────────────────────────
  // Mode is the route (/d/:id vs /d/:id/edit), so browser back leaves edit mode
  // and a refresh reopens it. The sample board and legacy HTML boards have no
  // spec for the editor to edit, so they are live-only.
  const mode = edit ? 'edit' : 'live'

  const handleModeChange = useCallback((next) => {
    // Preserve the query string across a mode flip: variables/_tab are the
    // author's current view of the board and shouldn't reset when they hit Edit.
    const qs = searchParams.toString()
    const suffix = qs ? `?${qs}` : ''
    navigate(next === 'edit' ? `/d/${id}/edit${suffix}` : `/d/${id}${suffix}`, {
      state: location.state,
    })
  }, [navigate, id, searchParams, location.state])

  // The editor owns the draft; we only observe its dirty flag, to drive the
  // header's "unsaved edits" pill when the author is looking at Live.
  const [draftDirty, setDraftDirty] = useState(false)

  // Once entered, the editor stays MOUNTED (hidden in live mode) so undo history
  // and unsaved work survive a mode flip — flipping to Live to check something
  // must not silently throw away edits.
  const [editorMounted, setEditorMounted] = useState(edit)
  useEffect(() => { if (edit) setEditorMounted(true) }, [edit])

  // What to render — 'spec' | 'html' | null (loading / fallback)
  const [renderMode, setRenderMode] = useState(null)
  const [spec, setSpec]     = useState(null)
  const [html, setHtml]     = useState(null)
  const [boardId, setBoardId] = useState(null)
  // The board ROW's name — what the dashboards list shows on the card, which
  // an author may have set independently of spec.title. Kept so the delete
  // confirm names the board the same way the list does.
  const [boardName, setBoardName] = useState(null)
  const [updatedAt, setUpdatedAt] = useState(null)

  const [loading, setLoading] = useState(true)
  const [error, setError]     = useState(null)
  // 'empty' = the board exists but has no content yet (not a failure);
  // 'load' = the board could not be fetched.
  const [errorKind, setErrorKind] = useState('load')

  // Same computation SpecRenderer uses internally for its own root box —
  // mirrored here so the page chrome around it (outer page root + content
  // wrapper) can paint the identical background, closing the seam. Only
  // meaningful for renderMode === 'spec' (legacy `html` boards have no
  // `spec.background` concept).
  // The board's own mode wins over the app theme (lib/boardTheme.ts), so the
  // page around it must paint in that mode too or the seam shows.
  const boardMode = renderMode === 'spec' ? boardColorMode(spec) : null
  const themeAdaptOn = spec?.themeAdapt !== 'off'
  const bgTheme = boardMode ?? pageTheme
  const pageBgStyle = useMemo(
    () => (renderMode === 'spec' ? backgroundToCss(spec?.background, themeAdaptOn ? { theme: bgTheme } : undefined) : undefined),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [renderMode, JSON.stringify(spec?.background), themeAdaptOn, bgTheme],
  )

  // ── Draft vs live ─────────────────────────────────────────────────────────
  // Live mode serves the version pinned to the project's default environment.
  // A board nobody has pushed yet has no pinned version, so `?env=` hands back
  // the draft and `isLive` is false — which is why every pre-existing board
  // keeps behaving exactly as it did before this feature.
  const { environments } = useEnv()
  const liveEnvKey = useMemo(() => resolveLiveEnvKey(environments), [environments])
  const [isLive, setIsLive] = useState(false)
  const [liveVersion, setLiveVersion] = useState(null)
  const [pushing, setPushing] = useState(false)

  // The author's working draft, as far as this page can see it. The editor
  // reports it live while it is mounted (onSpecChange); before that, a draft
  // fetch fills it in. The draft bar diffs it against the live spec.
  const [draftSpec, setDraftSpec] = useState(null)
  // Bumped to remount the editor after a discard, so it reloads the reset draft
  // from the server and its in-memory edits and undo history are dropped.
  const [editorKey, setEditorKey] = useState(0)
  const [discardOpen, setDiscardOpen] = useState(false)
  const [discarding, setDiscarding] = useState(false)
  const [historyOpen, setHistoryOpen] = useState(false)

  // A save is a DRAFT write. It only changes what Live shows for a board that
  // has never been pushed (where live === draft); once a board has a pinned
  // version, Live must keep showing that version until the author pushes again.
  // Getting this backwards would make "Push to live" decorative.
  const handleEditorSaved = useCallback((board) => {
    if (!isLive && board?.config?.spec) setSpec(board.config.spec)
    if (board?.name) setBoardName(board.name)
    if (board?.updated_at) setUpdatedAt(board.updated_at)
    if (board?.config?.spec) setDraftSpec(board.config.spec)
  }, [isLive])

  // The editor hands us its save action (see DashboardEditor's onSetSave seam)
  // so Push can flush the draft first. Held in a ref, not state, because it's an
  // imperative handle rather than something we render from.
  const editorSaveRef = useRef(null)
  const registerEditorSave = useCallback((fn) => { editorSaveRef.current = fn }, [])

  /**
   * Push to live: flush the draft, then checkpoint + promote it.
   *
   * Saving first is not optional — checkpoint snapshots the SERVER's draft row,
   * so publishing without flushing would quietly ship the previously-saved spec
   * while the author watches their newest edits not appear.
   */
  const handlePushToLive = useCallback(async () => {
    if (!boardId || pushing) return
    setPushing(true)
    try {
      if (draftDirty && editorSaveRef.current) {
        // Throws on failure → we abort rather than publish stale work.
        await editorSaveRef.current()
      }
      const result = await pushToLive(boardId, { environments })
      setIsLive(true)
      setLiveVersion(result.version)

      // Adopt the just-published spec as what Live renders. The draft and the
      // live version are identical at this instant, so this is accurate — and it
      // saves a refetch.
      const board = await get(`/boards/${id}?env=${encodeURIComponent(liveEnvKey)}`)
      const live = resolveLiveRender(board)
      if (live.spec) { setSpec(live.spec); setRenderMode('spec') }
      setIsLive(live.isLive)
      setLiveVersion(live.version)

      toast.success(
        result.deduped
          ? `Already live — nothing changed since v${result.version}.`
          : `Pushed live${result.version ? ` as v${result.version}` : ''}.`
      )
    } catch (err) {
      toast.error(err?.message || 'Push to live failed.')
    } finally {
      setPushing(false)
    }
  }, [boardId, id, pushing, draftDirty, environments, liveEnvKey])

  /**
   * Discard: put the draft back to the live version. Restores the pinned
   * version's config into the draft row server-side, then remounts the editor so
   * it reloads that row. Unsaved in-memory edits go with it, which is exactly
   * what the confirm says.
   */
  const handleDiscard = useCallback(async () => {
    if (!boardId || !liveVersion || discarding) return
    setDiscarding(true)
    try {
      await restoreVersion('board', boardId, liveVersion)
      setDraftSpec(spec)
      setDraftDirty(false)
      setEditorKey(k => k + 1)
      setDiscardOpen(false)
      toast.success(`Draft reset to live v${liveVersion}.`)
    } catch (err) {
      toast.error(err?.message || 'Could not discard the draft.')
    } finally {
      setDiscarding(false)
    }
  }, [boardId, liveVersion, discarding, spec])

  // ── Delete ─────────────────────────────────────────────────────────────────
  // Reachable from the header's overflow menu in both modes. There is no undo
  // and no trash, so DeleteBoardDialog's confirm is the whole gate — the same
  // dialog (and wording) the dashboards list uses.
  const [confirmDelete, setConfirmDelete] = useState(false)
  const [deleting, setDeleting] = useState(false)

  const handleDelete = useCallback(async () => {
    if (!boardId || deleting) return
    setDeleting(true)
    try {
      await del(`/boards/${boardId}`)
      // Leave before the dialog closes: this route's whole subject is now gone,
      // and staying would leave the editor holding a draft of a board that no
      // longer exists (its beforeunload guard included).
      toast.success('Dashboard deleted.')
      navigate('/dashboards', { replace: true })
    } catch (err) {
      toast.error(err?.message || 'Failed to delete dashboard. Please try again.')
      setDeleting(false)
    }
  }, [boardId, deleting, navigate])

  useEffect(() => {
    // Short-circuit for the built-in sample route
    if (id === 'sample') {
      setHtml(SAMPLE_DASHBOARD_HTML)
      setRenderMode('html')
      setLoading(false)
      return
    }

    // Deep links (/d/:id pasted cold) mount BEFORE OrgContext has restored the
    // active workspace, so api.js would send no X-Org-Id and the server would
    // resolve the user's DEFAULT org — a 404 for any board in another
    // workspace. That isn't a slow load, it's a lost race the effect never
    // re-ran to recover from. Wait for the workspace, and refetch if it changes.
    if (orgLoading) return

    let cancelled = false

    async function load() {
      setLoading(true)
      setError(null)
      try {
        // `?env=` asks the server for the version pinned to the live env, and
        // transparently falls back to the draft (resolved_version: null) when
        // nothing is pinned — see resources.py _apply_env_resolution.
        const board = await get(`/boards/${id}?env=${encodeURIComponent(liveEnvKey)}`)
        if (cancelled) return

        setBoardId(board?.id ?? id)
        setBoardName(board?.name ?? null)
        setUpdatedAt(board?.updated_at ?? null)

        const live = resolveLiveRender(board)
        setIsLive(live.isLive)
        setLiveVersion(live.version)

        if (live.spec) {
          // New spec path
          setSpec(live.spec)
          setRenderMode('spec')
        } else if (live.html) {
          // Legacy HTML path
          setHtml(live.html)
          setRenderMode('html')
        } else {
          // Board exists but has no content — say so rather than dressing an
          // empty board in the sample's fabricated rows.
          setError('This board has no content yet. Open the editor to add widgets.')
          setErrorKind('empty')
          setRenderMode(null)
        }
      } catch (err) {
        if (cancelled) return
        // Never substitute the sample dashboard for a board that failed to
        // load: fabricated alpha/beta/gamma rows under a real board's name is
        // how a broken board comes to look like a working one. Say what went
        // wrong — and for a 404, name the workspace, because "not found" here
        // usually means "not in THIS workspace".
        setErrorKind('load')
        setError(
          err.status === 404
            ? `Board not found in ${viewOrg?.name ? `the "${viewOrg.name}" workspace` : 'this workspace'}. `
              + 'It may belong to a different workspace — switch workspace and try again.'
            : `Could not load this board: ${err.message}`
        )
        setRenderMode(null)
      } finally {
        if (!cancelled) setLoading(false)
      }
    }

    load()
    return () => { cancelled = true }
  }, [id, liveEnvKey, orgLoading, viewOrg?.id, viewOrg?.name])

  // ---------------------------------------------------------------------------
  // Variable ↔ URL sync
  // ---------------------------------------------------------------------------

  // EMBED-TOKEN HOOK (wired):
  // Read `_token` (or `_embed`) from the URL, base64-decode the JWT payload
  // (client-side only — the server verifies the signature), and extract
  // `locked_params` from the payload.  These values take precedence over URL
  // params and cannot be overridden by filter widgets.
  //
  // Fail-closed: if the token is absent or malformed, embedLockedParams = {}
  // (no restriction widening — the server will still enforce its own check).
  const embedLockedParams = useMemo(() => {
    const raw = searchParams.get('_token') ?? searchParams.get('_embed')
    if (!raw) return {}
    const payload = decodeJwtPayload(raw)
    if (!payload || typeof payload.locked_params !== 'object' || !payload.locked_params) return {}
    return payload.locked_params
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchParams.get('_token'), searchParams.get('_embed')])

  // Names of variables declared in the spec that participate in URL sync.
  // A variable opts in via `url_bind: true`. For backward-compatibility, if NO
  // variable declares url_bind, ALL declared variables sync (prior behaviour).
  // Locked param names are stripped so the URL cannot shadow embed-token values.
  const knownVarNames = useMemo(() => {
    if (!spec?.variables) return []
    const named = spec.variables.filter(v => v.name)
    const anyOptIn = named.some(v => v.url_bind)
    const eligible = anyOptIn ? named.filter(v => v.url_bind) : named
    const lockedNames = new Set(Object.keys(embedLockedParams))
    return eligible.map(v => v.name).filter(n => !lockedNames.has(n))
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [spec, JSON.stringify(embedLockedParams)])

  // Extract variable values from the URL, restricted to declared variable names.
  const urlVars = useMemo(
    () => extractVarsFromURL(searchParams, knownVarNames),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [searchParams.toString(), knownVarNames],
  )

  // Compose the initialVariables prop for SpecRenderer:
  //   spec defaults (inside SpecRenderer)  ←  lowest precedence
  //   URL params                           ←  middle
  //   embed-token locked params            ←  highest (overrides URL)
  //
  // SpecRenderer merges these over spec.variable defaults internally.
  const initialVariables = useMemo(
    () => ({ ...urlVars, ...embedLockedParams }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [JSON.stringify(urlVars), JSON.stringify(embedLockedParams)],
  )

  // ---------------------------------------------------------------------------
  // Tab ↔ URL sync (_tab param)
  // ---------------------------------------------------------------------------

  // Read the _tab URL param (underscore-prefixed to avoid collisions with user
  // variable names).  Falls back to the first tab when absent or unrecognised.
  const activeTabId = useMemo(() => {
    const tabParam = searchParams.get('_tab')
    const firstTabId = spec?.tabs?.[0]?.id ?? null
    if (!tabParam) return firstTabId
    // Validate: only accept the param if it matches a declared tab id
    const knownTabIds = (spec?.tabs ?? []).map(t => t.id)
    return knownTabIds.includes(tabParam) ? tabParam : firstTabId
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchParams.get('_tab'), spec?.tabs])

  const handleTabChange = useCallback((id) => {
    setSearchParams(prev => {
      const next = new URLSearchParams(prev)
      next.set('_tab', id)
      return next
    }, { replace: true })
  }, [setSearchParams])

  /**
   * Two-way URL sync (M14-C, WIRED): this callback is passed as
   * `onVariableChange` → SpecRenderer → VariableProvider, which fires it on every
   * `setVariable`. So a filter change propagates to the URL (shallow replace) and
   * the state is shareable / survives a refresh. The read direction is seeded from
   * the URL on mount via `extractVarsFromURL` (initialVariables, above). The
   * VariableProvider is the source of truth while mounted; the URL is the
   * persistence layer. Embed-locked params are never written back (the token wins),
   * and only declared variable names sync — both enforced in applyVarToSearchParams.
   */
  // One widget interaction can set SEVERAL variables (a table row click with
  // `onClick.setVars`, legacy's multipleOutputs). Each arrives as its own
  // callback, and react-router resolves every functional `setSearchParams`
  // updater against the CURRENT location — batched calls in one tick all see
  // the same `prev`, so the last write wins and the earlier variables never
  // reach the URL. The board itself was correct (VariableStore uses a real
  // functional updater); only the shareable link lost them. So coalesce: merge
  // pending writes into a ref and flush them in a single update.
  const pendingVarWrites = useRef(null)
  const handleVariableChange = useCallback((name, value) => {
    if (pendingVarWrites.current) {
      // A flush is already scheduled — just join this write to it.
      pendingVarWrites.current.push([name, value])
      return
    }
    pendingVarWrites.current = [[name, value]]
    // setTimeout (not queueMicrotask): setVariable defers each callback with
    // setTimeout(…, 0), so sibling writes land in later macrotasks. A
    // microtask would flush before they arrive and re-split the batch.
    setTimeout(() => {
      const writes = pendingVarWrites.current ?? []
      pendingVarWrites.current = null
      if (writes.length === 0) return
      setSearchParams(
        prev => writes.reduce(
          (params, [n, v]) => applyVarToSearchParams(params, n, v, {
            knownVarNames,
            lockedParams: embedLockedParams,
          }),
          prev,
        ),
        { replace: true },
      )
    }, 0)
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [setSearchParams, JSON.stringify(embedLockedParams), knownVarNames])

  // ---------------------------------------------------------------------------
  // Render states
  // ---------------------------------------------------------------------------

  // The editor edits a spec — a legacy HTML board or the built-in sample has
  // nothing for it to open, so those stay live-only.
  const canEdit = canWrite && renderMode === 'spec' && id !== 'sample'

  // Deleting is not editing: an HTML-only board and a board that failed to
  // load content are both undeletable-from-here if this rides on canEdit,
  // which is backwards — they're the ones most likely to want deleting.
  const canDelete = canWrite && id !== 'sample'

  // Before the editor has been opened nothing is reporting the draft, so fetch it
  // once to be able to say "the draft has N unpushed changes" on the live view.
  useEffect(() => {
    if (!canEdit || !boardId || !isLive || editorMounted) return
    let cancelled = false
    get(`/boards/${boardId}`)
      .then(b => { if (!cancelled) setDraftSpec(b?.config?.spec ?? null) })
      .catch(() => { /* the bar just stays quiet about the count */ })
    return () => { cancelled = true }
  }, [canEdit, boardId, isLive, editorMounted, liveVersion])

  // Draft vs live, counted by what actually differs. Only meaningful once the
  // board has a live version; before that live IS the draft.
  const changeSummary = useMemo(
    () => (isLive && spec && draftSpec ? summarizeSpecChanges(spec, draftSpec) : null),
    [isLive, spec, draftSpec],
  )

  // A viewer (or anyone) landing on /d/:id/edit for an uneditable board is sent
  // back to live rather than shown a broken half-editor. Waits for `loading` so
  // we don't bounce before renderMode is known. Backend enforces the same rule
  // on save (app/auth/roles.py) — this is UX, not the security boundary.
  useEffect(() => {
    if (edit && !loading && !canEdit) {
      navigate(`/d/${id}`, { replace: true, state: location.state })
    }
  }, [edit, loading, canEdit, navigate, id, location.state])

  if (loading) {
    return (
      <div data-testid="dashboard-view-page">
        {!isEmbedView && <ViewToolbar backTo={backTo} title="" boardId={null} spec={null} canEdit={false} mode="live" />}
        <div className="max-w-[110rem] mx-auto px-4 sm:px-6 lg:px-8 py-6" aria-busy="true" aria-label="Loading dashboard">
          {/* Title + tab row skeleton — mirrors the real board anatomy so the
              loaded page doesn't jump. */}
          <div className="mb-6 space-y-4">
            <Skeleton className="h-7 w-64" />
            <div className="flex items-center gap-2">
              {[72, 60, 60, 88].map((w, i) => (
                <Skeleton key={i} className="h-8" style={{ width: w }} />
              ))}
            </div>
          </div>
          {/* KPI row: hairline tiles holding the same bar a loaded KPI has. */}
          <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-3 mb-3">
            {[1, 2, 3, 4, 5].map(i => (
              <div key={i} className="rounded-xl border border-border bg-surface p-5" style={{ height: 112 }}>
                <Skeleton.Kpi />
              </div>
            ))}
          </div>
          {/* Chart tiles: axes first, bars pulse. */}
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
            {[1, 2].map(i => (
              <div key={i} className="rounded-xl border border-border bg-surface p-4">
                <Skeleton className="h-3 w-40 mb-4" />
                <Skeleton.Chart height={220} />
              </div>
            ))}
          </div>
        </div>
      </div>
    )
  }

  const isEdit = mode === 'edit' && canEdit
  const pushable = canPushToLive({ isLive, dirty: draftDirty }) || (changeSummary?.total ?? 0) > 0
  // The draft bar is for authors only, and only while there is something to say:
  // always in edit mode; on the live view only when the draft has moved on.
  const showDraftBar = !isEmbedView && canEdit && Boolean(boardId) && (
    isEdit || (isLive && (changeSummary?.total ?? 0) > 0)
  )

  return (
    <div
      // Live mode is a normal page — min-h-screen just keeps a short board
      // from looking cramped, and the page scrolls with its content. Edit
      // mode wants the opposite: a hard-capped viewport (h-screen, clipped)
      // so DashboardEditor's own "fixed shell, only the canvas/side panels
      // scroll" layout has a real bounded ancestor — without it, its
      // `flex-1 min-h-0` classes are inert (nothing capping this root's
      // height), the editor renders at its full natural content height, and
      // the WHOLE PAGE scrolls as one block instead of the editor's internal
      // regions doing so independently.
      className={`flex flex-col ${isEdit ? 'h-screen overflow-hidden' : 'min-h-screen'}`}
      data-testid="dashboard-view-page"
      data-mode={isEdit ? 'edit' : 'live'}
    >
      {!isEmbedView && (
        <ViewToolbar
          backTo={backTo}
          title={spec?.title || boardName || 'Dashboard'}
          boardId={id !== 'sample' ? boardId : null}
          spec={spec}
          canEdit={canEdit}
          mode={isEdit ? 'edit' : 'live'}
          onModeChange={handleModeChange}
          editorSlotRef={setTopbarSlot}
          isLive={isLive}
          liveVersion={liveVersion}
          updatedAt={updatedAt}
          canDelete={canDelete}
          onDelete={() => setConfirmDelete(true)}
          onHistory={() => setHistoryOpen(true)}
        />
      )}

      {showDraftBar && (
        <DraftBar
          isEdit={isEdit}
          isLive={isLive}
          liveVersion={liveVersion}
          summary={changeSummary}
          dirty={draftDirty}
          pushing={pushing}
          pushable={pushable}
          discarding={discarding}
          onPush={handlePushToLive}
          onDiscard={() => setDiscardOpen(true)}
          onOpenDraft={() => handleModeChange('edit')}
        />
      )}

      {confirmDelete && (
        <DeleteBoardDialog
          board={{ name: boardName || spec?.title || 'Dashboard' }}
          busy={deleting}
          onConfirm={handleDelete}
          onCancel={() => setConfirmDelete(false)}
        />
      )}

      {/* Discard the draft: reset it to the live version. */}
      <Modal
        open={discardOpen}
        onClose={() => { if (!discarding) setDiscardOpen(false) }}
        title="Discard draft changes?"
        description={`The draft goes back to live${liveVersion ? ` v${liveVersion}` : ''}. This cannot be undone.`}
        size="sm"
        persistent={discarding}
      >
        <Modal.Body data-testid="discard-draft-dialog">
          {changeSummary && changeSummary.total > 0 ? (
            <>
              <p className="m-0 text-[13px] text-muted">
                {describeChanges(changeSummary.total, 'unpushed')} will be lost:
              </p>
              <ul className="mt-3 m-0 list-none border-t border-border p-0">
                {changeSummary.changes.slice(0, 6).map(c => (
                  <li key={`${c.kind}:${c.id}`} className="flex items-center gap-3 border-b border-border py-2 text-[13px]">
                    <span className="nbc-label w-14 shrink-0">{c.kind === 'board' ? 'board' : c.kind}</span>
                    <span className="min-w-0 flex-1 truncate text-fg">{c.label}</span>
                  </li>
                ))}
              </ul>
              {changeSummary.total > 6 && (
                <p className="mt-2 m-0 nbc-meta">and {changeSummary.total - 6} more</p>
              )}
            </>
          ) : (
            <p className="m-0 text-[13px] text-muted">Unsaved edits in the editor will be lost.</p>
          )}
        </Modal.Body>
        <Modal.Footer>
          <Button variant="ghost" size="sm" onClick={() => setDiscardOpen(false)} disabled={discarding}>
            Keep editing
          </Button>
          <Button variant="danger" size="sm" onClick={handleDiscard} loading={discarding} data-testid="discard-draft-confirm">
            Discard changes
          </Button>
        </Modal.Footer>
      </Modal>

      <VersionHistoryDialog
        kind="board"
        resourceId={boardId}
        resourceName={boardName || spec?.title || 'Dashboard'}
        open={historyOpen && Boolean(boardId)}
        onClose={() => setHistoryOpen(false)}
        onRestored={() => { setEditorKey(k => k + 1); setDraftDirty(false) }}
        environments={environments}
      />

      {/* ── Edit mode ──────────────────────────────────────────────────────
          The editor owns the whole viewport below the header and portals its
          toolbar up into it. Kept mounted but hidden when the author flips to
          Live, so undo history and unsaved edits survive the round trip. */}
      {editorMounted && (
        <div
          className={isEdit ? 'flex flex-col flex-1 min-h-0' : 'hidden'}
          data-testid="dashboard-edit-surface"
          aria-hidden={!isEdit}
        >
          <EditorShell
            key={editorKey}
            boardId={boardId ?? id}
            onDirtyChange={setDraftDirty}
            onSaved={handleEditorSaved}
            onSpecChange={setDraftSpec}
            onSetSave={registerEditorSave}
          />
        </div>
      )}

      {/* ── Live mode ──────────────────────────────────────────────────────
          Renders the SAVED spec, never the editor's in-memory draft — that's
          what makes "Live" an honest answer to "what do my viewers see?". */}
      <div
        className={isEdit ? 'hidden' : ['flex-1 max-w-[110rem] w-full mx-auto px-4 sm:px-6 lg:px-8 py-6', pageBgStyle && colorModeClass(boardMode)].filter(Boolean).join(' ')}
        style={!isEdit ? pageBgStyle : undefined}
      >

      {/* Could not open the board, or it has nothing in it yet. */}
      {error && (
        <div data-testid="dashboard-view-error" role="status" className="mb-4">
          {errorKind === 'empty' ? (
            <EmptyState
              bordered
              art={<EmptyBoards />}
              title="This board is empty"
              description="It has no widgets yet. Add a chart, a number or a table to start."
              action={canWrite ? (
                <Button size="sm" onClick={() => navigate(`/d/${id}/edit`)}>Edit board</Button>
              ) : undefined}
            />
          ) : (
            <EmptyState
              bordered
              kind="error"
              art={<ErrorFigure />}
              title="This board couldn't be opened"
              description={error}
              action={<Button size="sm" variant="secondary" onClick={() => navigate(backTo)}>Back to dashboards</Button>}
            />
          )}
        </div>
      )}

      {/* Dashboard content */}
      {renderMode === 'spec' && spec && (
        <BoardHealthProvider>
          {/* One banner when several tiles share a cause, instead of a wall of
              identical error tiles. */}
          <BoardErrorBanner className="mb-4" />
          <div data-testid="dashboard-spec-renderer">
            <SpecRenderer
              spec={spec}
              boardId={boardId}
              initialVariables={initialVariables}
              onVariableChange={handleVariableChange}
              activeTabId={activeTabId}
              onTabChange={handleTabChange}
              flushBackground={Boolean(pageBgStyle)}
            />
          </div>
        </BoardHealthProvider>
      )}

      {renderMode === 'html' && html != null && (
        <div data-testid="dashboard-html-renderer">
          <DashboardView html={html} />
        </div>
      )}
      </div>
    </div>
  )
}
