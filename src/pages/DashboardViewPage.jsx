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
 * The sample dashboard demonstrates all three Nubi widget types in a responsive
 * CSS grid layout, each pointing at the built-in "demo_all" registered query.
 */

import { useState, useEffect } from 'react'
import { useParams, Link } from 'react-router-dom'
import { get } from '../lib/api.js'
import DashboardView from '../dashboards/DashboardView.jsx'
import SpecRenderer from '../dashboards/SpecRenderer.jsx'

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
// DashboardViewPage
// ---------------------------------------------------------------------------

export default function DashboardViewPage() {
  const { id } = useParams()

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
    <div className="max-w-6xl mx-auto px-4 sm:px-6 lg:px-8 py-8">

      {/* Fallback / error notice */}
      {error && (
        <div className="mb-4 px-4 py-3 rounded-lg text-sm flex items-start gap-2 border"
          style={{ background: 'color-mix(in srgb, #f59e0b 8%, transparent)', color: '#d97706', borderColor: 'color-mix(in srgb, #f59e0b 25%, transparent)' }}>
          <span className="shrink-0 mt-0.5" aria-hidden="true">&#9888;</span>
          <span>{error}</span>
        </div>
      )}

      {/* Edit link for spec boards */}
      {renderMode === 'spec' && boardId && id !== 'sample' && (
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
        <SpecRenderer spec={spec} />
      )}

      {renderMode === 'html' && html != null && (
        <DashboardView html={html} />
      )}
    </div>
  )
}
