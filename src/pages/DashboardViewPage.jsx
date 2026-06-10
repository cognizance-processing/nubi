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
 *     1. Embed-token-locked params (HOOK — not yet wired; see comment below)
 *     2. URL search params (?varName=value)
 *     3. spec.variables defaults
 *
 * When a filter widget changes a variable, the new value is written back to the
 * URL via setSearchParams (shallow replace, so no extra history entry).
 *
 * Embed-token integration hook:
 *   A future embed integration can call SpecRenderer with locked initialVariables
 *   sourced from a verified embed JWT.  Those locked values must:
 *     a) Override URL params (the token wins).
 *     b) Not be writable by filter widgets in the page (the store should be
 *        initialised with those values and the filter widget's setVariable call
 *        should be a no-op for locked names).
 *   Until that integration lands, the hook is represented by the
 *   `embedLockedParams` constant below (always {} for now).  Wire it once the
 *   embed token is verified server-side and passed to this page as a prop.
 */

import { useState, useEffect, useMemo, useCallback } from 'react'
import { useParams, Link, useSearchParams } from 'react-router-dom'
import { get } from '../lib/api.js'
import DashboardView from '../dashboards/DashboardView.jsx'
import SpecRenderer from '../dashboards/SpecRenderer.jsx'
import { useCanWrite } from '../contexts/OrgContext.jsx'

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
// URL ↔ variable store sync helpers
// ---------------------------------------------------------------------------

/**
 * Extract variable values from URLSearchParams.
 * All URL param values are strings; callers should cast if needed.
 *
 * @param {URLSearchParams} searchParams
 * @param {string[]} knownVarNames — variable names declared in spec.variables.
 *   Only these names are extracted to avoid polluting the store with unrelated
 *   query params (e.g. ?utm_source=…).
 * @returns {Record<string, string>}
 */
function extractVarsFromURL(searchParams, knownVarNames) {
  const values = {}
  for (const name of knownVarNames) {
    const val = searchParams.get(name)
    if (val !== null) {
      values[name] = val
    }
  }
  return values
}

// ---------------------------------------------------------------------------
// DashboardViewPage
// ---------------------------------------------------------------------------

export default function DashboardViewPage() {
  const { id } = useParams()
  const [searchParams, setSearchParams] = useSearchParams()

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

  // EMBED-TOKEN HOOK:
  // When an embed token is verified and passed to this page (e.g. via a prop or
  // a future EmbedContext), populate embedLockedParams with the token's locked
  // variable values.  These MUST take precedence over URL params and cannot be
  // written by filter widgets.
  //
  // Implementation plan for embed integration:
  //   1. Verify the embed JWT server-side (M3 flow).
  //   2. Pass locked param names+values to this component (prop / context).
  //   3. Merge them into initialVariables AFTER urlVars (so they win).
  //   4. Strip locked param names from the URL before extracting urlVars so the
  //      URL cannot shadow a locked param.
  //
  // For now (non-embed flow), this is always an empty object.
  const embedLockedParams = {}

  // Names of variables declared in the spec that participate in URL sync.
  // A variable opts in via `url_bind: true`. For backward-compatibility, if NO
  // variable declares url_bind, ALL declared variables sync (prior behaviour).
  const knownVarNames = useMemo(() => {
    if (!spec?.variables) return []
    const named = spec.variables.filter(v => v.name)
    const anyOptIn = named.some(v => v.url_bind)
    const eligible = anyOptIn ? named.filter(v => v.url_bind) : named
    return eligible.map(v => v.name)
  }, [spec])

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
   * Called by filter widgets (via the VariableStore) when a variable changes.
   * Writes the new value back to the URL as a search param (shallow replace).
   *
   * NOTE: VariableProvider handles internal state; this callback propagates
   * changes to the URL so the state survives a page refresh / is shareable.
   * The VariableProvider itself is the source of truth while the page is mounted;
   * the URL is the persistence layer.
   *
   * This callback is not yet plumbed to the VariableProvider directly — that
   * wiring belongs in a future URLSyncVariableProvider wrapper.  For M14-C we
   * seed the store from the URL on mount; full two-way sync (filter → URL) is
   * the next step and is left as a clearly-marked hook here.
   *
   * TODO(M14-C-sync): wrap VariableProvider in a URLSyncProvider that intercepts
   *   setVariable calls and also calls setSearchParams for non-locked names.
   */
  const handleVariableChange = useCallback((name, value) => {
    // Do not write embed-locked params back to the URL
    if (Object.prototype.hasOwnProperty.call(embedLockedParams, name)) return
    // Only sync URL-bound variables (see knownVarNames opt-in logic).
    if (!knownVarNames.includes(name)) return

    setSearchParams(prev => {
      const next = new URLSearchParams(prev)
      if (value === undefined || value === null || value === '') {
        next.delete(name)
      } else {
        next.set(name, String(value))
      }
      return next
    }, { replace: true })
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [setSearchParams, JSON.stringify(embedLockedParams), knownVarNames])

  // ---------------------------------------------------------------------------
  // Render states
  // ---------------------------------------------------------------------------

  if (loading) {
    return (
      <div className="max-w-5xl mx-auto px-4 sm:px-6 lg:px-8 py-12">
        <div className="flex items-center justify-center py-24 text-sm text-muted animate-pulse">
          Loading dashboard…
        </div>
      </div>
    )
  }

  return (
    <div className="max-w-[110rem] mx-auto px-4 sm:px-6 lg:px-8 py-8" data-testid="dashboard-view-page">

      {/* Fallback / error notice */}
      {error && (
        <div className="mb-4 px-4 py-3 rounded-lg text-sm flex items-start gap-2 border"
          data-testid="dashboard-view-error"
          style={{ background: 'color-mix(in srgb, #f59e0b 8%, transparent)', color: '#d97706', borderColor: 'color-mix(in srgb, #f59e0b 25%, transparent)' }}>
          <span className="shrink-0 mt-0.5" aria-hidden="true">&#9888;</span>
          <span>{error}</span>
        </div>
      )}

      {/* Edit link for spec boards */}
      {canWrite && renderMode === 'spec' && boardId && id !== 'sample' && (
        <div className="mb-4 flex justify-end">
          <Link
            to={`/editor/${boardId}`}
            className="text-sm text-primary hover:opacity-80 font-medium transition-opacity focus:outline-none focus:ring-2 focus:ring-ring rounded"
          >
            Edit in editor &rarr;
          </Link>
        </div>
      )}

      {/* Dashboard content */}
      {renderMode === 'spec' && spec && (
        <div data-testid="dashboard-spec-renderer">
          <SpecRenderer
            spec={spec}
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
  )
}
