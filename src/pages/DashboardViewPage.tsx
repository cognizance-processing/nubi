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
import { ArrowLeft, Pencil, Eye, Rocket, Sun, Moon, ChevronRight, LayoutDashboard } from 'lucide-react'
import { get } from '../lib/api.js'
import DashboardView from '../dashboards/DashboardView.jsx'
import SpecRenderer from '../dashboards/SpecRenderer.jsx'
import EditorShell from '../editor/EditorShell.jsx'
import { useUi } from '../contexts/UiContext.jsx'
import { useEnv } from '../contexts/EnvContext.jsx'
import { pushToLive, resolveLiveEnvKey, resolveLiveRender, canPushToLive } from '../lib/liveBoard.js'
import { toast } from '../components/ui/Toast.jsx'
import ExportShareMenu from '../components/ExportShareMenu.jsx'
import { extractVarsFromURL, applyVarToSearchParams } from '../dashboards/urlSync.js'
import { decodeJwtPayload } from '../dashboards/embedLock.js'
import { useCanWrite, useOrg } from '../contexts/OrgContext.jsx'
import { useProject } from '../contexts/ProjectContext.jsx'
import { useTheme } from '../contexts/ThemeContext.jsx'
import Skeleton from '../components/ui/Skeleton.jsx'

// ---------------------------------------------------------------------------
// Built-in sample dashboard HTML
// ---------------------------------------------------------------------------

export const SAMPLE_DASHBOARD_HTML = `
<header style="padding:1.5rem 2rem; background:linear-gradient(135deg,#1b2363 0%,#2456a6 60%,#17b3a3 100%); border-radius:1rem; margin-bottom:1.5rem; color:#fff;">
  <h1 style="margin:0;font-size:1.5rem;font-weight:700;letter-spacing:-0.02em;font-family:'Space Grotesk',sans-serif;">Nubi Sample Dashboard</h1>
  <p style="margin:0.5rem 0 0;opacity:0.85;font-size:0.875rem;">
    Powered by <strong>nubi-kpi</strong>, <strong>nubi-table</strong>, and <strong>nubi-chart</strong> widgets.
    Each widget fetches live Arrow data via the registered query <code style="background:rgba(255,255,255,0.2);padding:0 0.3em;border-radius:0.25em;">demo_all</code>.
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
  <h2 style="font-size:1rem;font-weight:600;color:#374151;margin:0 0 0.75rem;">Data Table</h2>
  <nubi-table
    query-id="demo_all"
    limit="25"
  ></nubi-table>
</section>

<section>
  <h2 style="font-size:1rem;font-weight:600;color:#374151;margin:0 0 0.75rem;">Scatter Chart</h2>
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
// ---------------------------------------------------------------------------

/**
 * ModeSwitch — the Live ↔ Edit control.
 *
 * This is the spine of the merged surface: one board, one URL, two modes. It
 * mirrors the control every mature BI tool converged on (Power BI's Reading /
 * Editing views, Looker Studio's View / Edit) rather than sending an author to a
 * different page to change the thing they're looking at.
 */
function ModeSwitch({ mode, onChange }) {
  const items = [
    { id: 'live', label: 'Live', Icon: Eye, title: 'Live view — what viewers see' },
    { id: 'edit', label: 'Edit', Icon: Pencil, title: 'Edit this dashboard' },
  ]
  return (
    <div
      className="flex h-8 rounded-lg border border-border bg-surface overflow-hidden shrink-0"
      role="group"
      aria-label="Dashboard mode"
      data-testid="dashboard-mode-switch"
    >
      {items.map((it, i) => {
        const active = mode === it.id
        return (
          <button
            key={it.id}
            type="button"
            onClick={() => onChange(it.id)}
            aria-pressed={active}
            title={it.title}
            data-testid={`dashboard-mode-${it.id}`}
            className={[
              'inline-flex items-center gap-1.5 px-2.5 sm:px-3 text-[13px] font-medium transition-all duration-150',
              'focus:outline-none focus-visible:ring-inset focus-visible:ring-2 focus-visible:ring-ring/60',
              i > 0 ? 'border-l border-border' : '',
              active
                ? 'bg-primary text-primary-fg'
                : 'text-muted hover:text-fg hover:bg-surface-2',
            ].join(' ')}
          >
            <it.Icon size={13} aria-hidden="true" />
            <span className="hidden sm:inline">{it.label}</span>
          </button>
        )
      })}
    </div>
  )
}

/**
 * ViewToolbar — the single header for BOTH modes.
 *
 * In edit mode the embedded DashboardEditor portals its own toolbar into
 * `editorSlotRef` (via UiContext's topbarSlot), so the board keeps ONE header
 * instead of the old stack of two (EditorPage's route-level toolbar + the
 * editor's portaled one). The view-mode actions (theme, share) collapse away in
 * edit mode because the editor's toolbar already carries its own.
 */
function ViewToolbar({
  backTo, title, boardId, spec, canEdit, mode, onModeChange = undefined, editorSlotRef = undefined, dirty = false,
  isLive = false, liveVersion = null, pushing = false, onPushToLive = undefined,
}) {
  const { activeOrg } = useOrg()
  const { activeProject } = useProject()
  const { theme, toggleTheme } = useTheme()
  const isDark = theme === 'dark'
  const isEdit = mode === 'edit'

  // Nothing to publish when the board is already live and the draft is clean.
  // (A redundant push is harmless — checkpoint dedupes — but offering it invites
  // the author to wonder whether they forgot something.)
  const pushable = canPushToLive({ isLive, dirty })

  const orgProject = [activeOrg?.name, activeProject?.name].filter(Boolean).join(' / ')

  return (
    <header
      className="sticky top-0 z-40 border-b border-border shrink-0"
      style={{
        background: 'color-mix(in srgb, var(--surface) 82%, transparent)',
        backdropFilter: 'blur(14px) saturate(180%)',
        WebkitBackdropFilter: 'blur(14px) saturate(180%)',
      }}
    >
      <div className="flex items-center gap-2 h-14 px-3 sm:px-5 lg:px-7">
        {/* Back */}
        <Link
          to={backTo}
          aria-label="Back to dashboards"
          className="inline-flex items-center justify-center h-8 w-8 rounded-lg text-muted hover:text-fg hover:bg-surface-2 transition-colors shrink-0 focus:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          <ArrowLeft size={16} aria-hidden="true" />
        </Link>

        {/* Breadcrumb → board title. In edit mode the editor's own toolbar owns
            the (editable) title, so we drop ours rather than show it twice. */}
        <nav aria-label="Breadcrumb" className="flex items-center gap-1.5 min-w-0">
          <Link
            to={backTo}
            className="hidden sm:inline-flex items-center gap-1.5 text-[13px] text-muted hover:text-fg transition-colors shrink-0 focus:outline-none focus-visible:ring-2 focus-visible:ring-ring rounded px-1 py-0.5"
          >
            <LayoutDashboard size={14} aria-hidden="true" className="opacity-70" />
            Dashboards
          </Link>
          {orgProject && !isEdit && (
            <>
              <ChevronRight size={13} aria-hidden="true" className="hidden sm:block shrink-0" style={{ color: 'var(--text-subtle)' }} />
              <span
                className="hidden md:inline text-[13px] text-muted truncate max-w-[18rem] shrink"
                title={orgProject}
              >
                {orgProject}
              </span>
            </>
          )}
          {title && !isEdit && (
            <>
              <ChevronRight size={13} aria-hidden="true" className="hidden sm:block shrink-0" style={{ color: 'var(--text-subtle)' }} />
              <span className="text-sm font-semibold text-fg truncate min-w-0" title={title}>
                {title}
              </span>
            </>
          )}
        </nav>

        {canEdit && (
          <div className="shrink-0 ml-1">
            <ModeSwitch mode={mode} onChange={onModeChange} />
          </div>
        )}

        {/* Viewing live while holding unsaved edits is a real state (the editor
            stays mounted across a mode flip so history survives) — say so rather
            than let the author wonder why live doesn't show their change. */}
        {!isEdit && dirty && (
          <span
            className="hidden md:inline-flex items-center gap-1 text-[11px] px-2 h-6 rounded-md border whitespace-nowrap shrink-0"
            style={{ background: 'color-mix(in srgb, #f59e0b 8%, transparent)', color: '#b45309', borderColor: 'color-mix(in srgb, #f59e0b 25%, transparent)' }}
            data-testid="dashboard-draft-pending"
          >
            <span className="w-1.5 h-1.5 rounded-full bg-amber-400" />
            Unsaved edits
          </span>
        )}

        {/* Live provenance: which version viewers are actually on. A board that
            has never been pushed says so rather than implying it's published. */}
        {!isEdit && canEdit && (
          isLive
            ? (
              <span
                className="hidden lg:inline-flex items-center gap-1.5 text-[11px] px-2 h-6 rounded-md border border-border text-muted whitespace-nowrap shrink-0"
                title="Viewers are seeing this published version"
                data-testid="dashboard-live-version"
              >
                <span className="w-1.5 h-1.5 rounded-full" style={{ background: 'var(--success, #22c55e)' }} />
                Live{liveVersion ? ` · v${liveVersion}` : ''}
              </span>
            )
            : (
              <span
                className="hidden lg:inline-flex items-center gap-1.5 text-[11px] px-2 h-6 rounded-md border border-border text-muted whitespace-nowrap shrink-0"
                title="This board has never been pushed live — viewers see the draft"
                data-testid="dashboard-not-pushed"
              >
                Draft · not pushed
              </span>
            )
        )}

        {/* The editor's toolbar lands here in edit mode (createPortal via
            UiContext.topbarSlot). Always mounted so the ref is attached before
            the editor first renders and looks the slot up. */}
        <div ref={editorSlotRef} className={isEdit ? 'flex-1 min-w-0 flex items-center' : 'hidden'} />

        {!isEdit && <span className="flex-1" />}

        {/* Push to live — edit mode's terminal action. Deliberately separate
            from Save (which is a draft write) so publishing is always explicit,
            the way Hex's Publish works. */}
        {isEdit && canEdit && boardId && (
          <button
            type="button"
            onClick={onPushToLive}
            disabled={pushing || !pushable}
            data-testid="dashboard-push-live"
            title={
              pushable
                ? 'Publish the current draft so viewers see it'
                : `Live is already up to date${liveVersion ? ` (v${liveVersion})` : ''}`
            }
            className={[
              'ml-1 shrink-0 inline-flex items-center gap-1.5 h-8 px-3 rounded-lg text-[13px] font-medium',
              'transition-all shadow-sm focus:outline-none focus:ring-2 focus:ring-ring/60',
              pushing || !pushable
                ? 'opacity-50 cursor-not-allowed'
                : 'hover:opacity-90 active:scale-[0.98]',
            ].join(' ')}
            style={{ background: 'var(--primary)', color: 'var(--primary-fg)' }}
          >
            <Rocket size={13} aria-hidden="true" />
            <span className="hidden md:inline">{pushing ? 'Pushing…' : 'Push to live'}</span>
          </button>
        )}

        {/* View-mode actions. Edit mode's equivalents live in the editor toolbar. */}
        {!isEdit && (
          <div className="flex items-center gap-1.5 shrink-0">
            <button
              type="button"
              onClick={toggleTheme}
              aria-pressed={isDark}
              title={isDark ? 'Switch to light mode' : 'Switch to dark mode'}
              className="inline-flex items-center justify-center h-8 w-8 rounded-lg border border-border bg-surface text-muted hover:text-fg hover:bg-surface-2 transition-colors focus:outline-none focus:ring-2 focus:ring-ring/60"
            >
              {isDark ? <Sun size={15} /> : <Moon size={15} />}
            </button>

            {boardId && <ExportShareMenu board={boardId} spec={spec} />}
          </div>
        )}
      </div>
    </header>
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

  const [loading, setLoading] = useState(true)
  const [error, setError]     = useState(null)

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

  // A save is a DRAFT write. It only changes what Live shows for a board that
  // has never been pushed (where live === draft); once a board has a pinned
  // version, Live must keep showing that version until the author pushes again.
  // Getting this backwards would make "Push to live" decorative.
  const handleEditorSaved = useCallback((board) => {
    if (!isLive && board?.config?.spec) setSpec(board.config.spec)
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
          setRenderMode(null)
        }
      } catch (err) {
        if (cancelled) return
        // Never substitute the sample dashboard for a board that failed to
        // load: fabricated alpha/beta/gamma rows under a real board's name is
        // how a broken board comes to look like a working one. Say what went
        // wrong — and for a 404, name the workspace, because "not found" here
        // usually means "not in THIS workspace".
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
            <Skeleton className="h-7 w-64 rounded-lg" />
            <div className="flex items-center gap-2">
              {[72, 60, 60, 88].map((w, i) => (
                <Skeleton key={i} className="h-8 rounded-lg" style={{ width: w }} />
              ))}
            </div>
          </div>
          {/* KPI row */}
          <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-4 mb-4">
            {[1, 2, 3, 4, 5].map(i => (
              <div key={i} className="rounded-xl border border-border bg-surface p-5 space-y-3" style={{ height: 120 }}>
                <Skeleton className="h-3 w-20 rounded" />
                <Skeleton className="h-8 w-24 rounded-lg" />
              </div>
            ))}
          </div>
          {/* Chart cards */}
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            {[1, 2].map(i => (
              <div key={i} className="rounded-xl border border-border bg-surface p-5" style={{ height: 280 }}>
                <Skeleton className="h-4 w-44 rounded mb-5" />
                <div className="flex items-end gap-2 h-[70%] px-2">
                  {[55, 80, 40, 90, 65, 75, 50, 85, 60, 72].map((h, j) => (
                    <Skeleton key={j} className="flex-1 rounded-sm" style={{ height: `${h}%` }} />
                  ))}
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>
    )
  }

  const isEdit = mode === 'edit' && canEdit

  return (
    <div className="flex flex-col min-h-screen" data-testid="dashboard-view-page" data-mode={isEdit ? 'edit' : 'live'}>
      {!isEmbedView && (
        <ViewToolbar
          backTo={backTo}
          title={spec?.title || 'Dashboard'}
          boardId={id !== 'sample' ? boardId : null}
          spec={spec}
          canEdit={canEdit}
          mode={isEdit ? 'edit' : 'live'}
          onModeChange={handleModeChange}
          editorSlotRef={setTopbarSlot}
          dirty={draftDirty}
          isLive={isLive}
          liveVersion={liveVersion}
          pushing={pushing}
          onPushToLive={handlePushToLive}
        />
      )}

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
            boardId={boardId ?? id}
            onDirtyChange={setDraftDirty}
            onSaved={handleEditorSaved}
            onSetSave={registerEditorSave}
          />
        </div>
      )}

      {/* ── Live mode ──────────────────────────────────────────────────────
          Renders the SAVED spec, never the editor's in-memory draft — that's
          what makes "Live" an honest answer to "what do my viewers see?". */}
      <div className={isEdit ? 'hidden' : 'max-w-[110rem] w-full mx-auto px-4 sm:px-6 lg:px-8 py-6'}>

      {/* Fallback / error notice */}
      {error && (
        <div
          className="mb-4 px-4 py-3 rounded-xl text-sm flex items-start gap-2 border"
          data-testid="dashboard-view-error"
          style={{
            background: 'var(--warning-bg)',
            color: 'var(--warning)',
            borderColor: 'color-mix(in srgb, var(--warning) 25%, transparent)',
          }}
          role="status"
        >
          <span className="shrink-0 mt-0.5 text-base leading-none" aria-hidden="true">&#9888;</span>
          <span>{error}</span>
        </div>
      )}

      {/* Dashboard content */}
      {renderMode === 'spec' && spec && (
        <div data-testid="dashboard-spec-renderer">
          <SpecRenderer
            spec={spec}
            boardId={boardId}
            initialVariables={initialVariables}
            onVariableChange={handleVariableChange}
            activeTabId={activeTabId}
            onTabChange={handleTabChange}
          />
        </div>
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
