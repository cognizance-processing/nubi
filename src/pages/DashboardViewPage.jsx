/**
 * DashboardViewPage.jsx — Route component for /d/:id
 *
 * Loads a board by id via GET /boards/:id, then:
 *   - If board.config.spec is present → renders via <SpecRenderer> (new path).
 *   - Else if board.config.html is present → renders via <DashboardView> (legacy HTML path).
 *   - Else → falls back to the built-in sample dashboard.
 *
 * Special cases:
 *   /d/sample  — renders the built-in sample dashboard without a backend request.
 *   Any fetch failure (no backend, 404, etc.) — falls back to the same sample.
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

import { useState, useEffect, useMemo, useCallback } from 'react'
import { useParams, useLocation, Link, useSearchParams } from 'react-router-dom'
import { ArrowLeft, Pencil, Sun, Moon, ChevronRight, LayoutDashboard } from 'lucide-react'
import { get } from '../lib/api.js'
import DashboardView from '../dashboards/DashboardView.jsx'
import SpecRenderer from '../dashboards/SpecRenderer.jsx'
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

function ViewToolbar({ backTo, title, boardId, spec, canEdit }) {
  const { activeOrg } = useOrg()
  const { activeProject } = useProject()
  const { theme, toggleTheme } = useTheme()
  const isDark = theme === 'dark'

  const orgProject = [activeOrg?.name, activeProject?.name].filter(Boolean).join(' / ')

  return (
    <header
      className="sticky top-0 z-40 border-b border-border"
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

        {/* Breadcrumb → board title */}
        <nav aria-label="Breadcrumb" className="flex items-center gap-1.5 min-w-0">
          <Link
            to={backTo}
            className="hidden sm:inline-flex items-center gap-1.5 text-[13px] text-muted hover:text-fg transition-colors shrink-0 focus:outline-none focus-visible:ring-2 focus-visible:ring-ring rounded px-1 py-0.5"
          >
            <LayoutDashboard size={14} aria-hidden="true" className="opacity-70" />
            Dashboards
          </Link>
          {orgProject && (
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
          {title && (
            <>
              <ChevronRight size={13} aria-hidden="true" className="hidden sm:block shrink-0" style={{ color: 'var(--text-subtle)' }} />
              <span className="text-sm font-semibold text-fg truncate min-w-0" title={title}>
                {title}
              </span>
            </>
          )}
        </nav>

        <span className="flex-1" />

        {/* Actions */}
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

          {canEdit && boardId && (
            <>
              <span className="w-px h-5 mx-1 bg-border hidden sm:block" aria-hidden="true" />
              <Link
                to={`/editor/${boardId}`}
                className="inline-flex items-center gap-1.5 h-8 px-3.5 rounded-lg text-[13px] font-medium transition-all shadow-sm hover:opacity-90 active:scale-[0.98] focus:outline-none focus:ring-2 focus:ring-ring/60"
                style={{ background: 'var(--primary)', color: 'var(--primary-fg)' }}
              >
                <Pencil size={13} aria-hidden="true" />
                <span className="hidden sm:inline">Edit</span>
              </Link>
            </>
          )}
        </div>
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

export default function DashboardViewPage() {
  const { id } = useParams()
  const location = useLocation()
  const [searchParams, setSearchParams] = useSearchParams()

  // Full navigation chrome is suppressed only for a genuine filter-locked
  // embed view (a share link opened with ?_token=/&_embed=) — see the
  // ExportShareMenu "Share" tab, which mints exactly this kind of URL for
  // external viewers. Every other visit (a teammate browsing from /dashboards,
  // a bookmarked link, a fresh tab) gets the toolbar; it's the only way back
  // into the app since this route renders outside AppShell.
  const isEmbedView = Boolean(searchParams.get('_token') || searchParams.get('_embed'))
  const backTo = location.state?.folder ? `/dashboards?folder=${location.state.folder}` : '/dashboards'

  // Viewers (read-only) cannot edit — hide the "Edit in editor" link.
  const canWrite = useCanWrite()

  // What to render — 'spec' | 'html' | null (loading / fallback)
  const [renderMode, setRenderMode] = useState(null)
  const [spec, setSpec]     = useState(null)
  const [html, setHtml]     = useState(null)
  const [boardId, setBoardId] = useState(null)

  const [loading, setLoading] = useState(true)
  const [error, setError]     = useState(null)

  useEffect(() => {
    // Short-circuit for the built-in sample route
    if (id === 'sample') {
      setHtml(SAMPLE_DASHBOARD_HTML)
      setRenderMode('html')
      setLoading(false)
      return
    }

    let cancelled = false

    async function load() {
      setLoading(true)
      setError(null)
      try {
        const board = await get(`/boards/${id}`)
        if (cancelled) return

        setBoardId(board?.id ?? id)

        if (board?.config?.spec) {
          // New spec path
          setSpec(board.config.spec)
          setRenderMode('spec')
        } else if (board?.config?.html) {
          // Legacy HTML path
          setHtml(board.config.html)
          setRenderMode('html')
        } else {
          // Board exists but has no content
          setError('This board has no content yet. Showing the sample dashboard.')
          setHtml(SAMPLE_DASHBOARD_HTML)
          setRenderMode('html')
        }
      } catch (err) {
        if (cancelled) return
        setError(
          err.status === 404
            ? `Board "${id}" not found. Showing the sample dashboard.`
            : `Could not load board "${id}" (${err.message}). Showing the sample dashboard.`
        )
        setHtml(SAMPLE_DASHBOARD_HTML)
        setRenderMode('html')
      } finally {
        if (!cancelled) setLoading(false)
      }
    }

    load()
    return () => { cancelled = true }
  }, [id])

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
  const handleVariableChange = useCallback((name, value) => {
    setSearchParams(
      prev => applyVarToSearchParams(prev, name, value, {
        knownVarNames,
        lockedParams: embedLockedParams,
      }),
      { replace: true },
    )
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [setSearchParams, JSON.stringify(embedLockedParams), knownVarNames])

  // ---------------------------------------------------------------------------
  // Render states
  // ---------------------------------------------------------------------------

  const canEdit = canWrite && renderMode === 'spec' && id !== 'sample'

  if (loading) {
    return (
      <div data-testid="dashboard-view-page">
        {!isEmbedView && <ViewToolbar backTo={backTo} title="" boardId={null} spec={null} canEdit={false} />}
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

  return (
    <div data-testid="dashboard-view-page">
      {!isEmbedView && (
        <ViewToolbar
          backTo={backTo}
          title={spec?.title || 'Dashboard'}
          boardId={id !== 'sample' ? boardId : null}
          spec={spec}
          canEdit={canEdit}
        />
      )}

      <div className="max-w-[110rem] mx-auto px-4 sm:px-6 lg:px-8 py-6">

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
