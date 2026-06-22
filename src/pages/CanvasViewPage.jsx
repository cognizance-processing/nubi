/**
 * CanvasViewPage.jsx — Route component for /c/:id (public canvas viewer).
 *
 * Mirrors DashboardViewPage.jsx closely:
 *   - GET /canvases/{id} to load the canvas
 *   - URL ↔ variable sync (extractVarsFromURL / applyVarToSearchParams)
 *   - Read-only render via <CanvasRenderer>
 *   - "Edit in editor" link for users with write access (/canvas/:id)
 *   - Loading, error, and fallback states
 *
 * Variable precedence (highest → lowest):
 *   1. URL search params (?varName=value)
 *   2. doc.variables defaults (handled inside CanvasRenderer → VariableProvider)
 */

import { useState, useEffect, useMemo, useCallback } from 'react'
import { useParams, Link, useSearchParams } from 'react-router-dom'
import { get } from '../lib/api.js'
import CanvasRenderer from '../dashboards/CanvasRenderer.jsx'
import { extractVarsFromURL, applyVarToSearchParams } from '../dashboards/urlSync.js'
import { useCanWrite } from '../contexts/OrgContext.jsx'
import Skeleton from '../components/ui/Skeleton.jsx'

// ---------------------------------------------------------------------------
// Sample canvas fallback (shown when no backend / canvas not found)
// ---------------------------------------------------------------------------

const SAMPLE_CANVAS_DOC = {
  version: 1,
  title: 'Nubi Sample Canvas',
  html: `
<header style="padding:1.5rem 2rem; background:linear-gradient(135deg,#1b2363 0%,#2456a6 60%,#17b3a3 100%); border-radius:1rem; margin-bottom:1.5rem; color:#fff;">
  <h1 style="margin:0;font-size:1.5rem;font-weight:700;letter-spacing:-0.02em;font-family:'Space Grotesk',sans-serif;">Nubi Sample Canvas</h1>
  <p style="margin:0.5rem 0 0;opacity:0.85;font-size:0.875rem;">
    A freeform HTML surface — bind <strong>nubi-*</strong> elements to live queries via
    the <code style="background:rgba(255,255,255,0.2);padding:0 0.3em;border-radius:0.25em;">bindings</code> map.
  </p>
</header>

<section style="display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:1rem;margin-bottom:1.5rem;">
  <nubi-kpi
    data-el-id="kpi_1"
    query-id="demo_all"
    value-col="n"
    label="Total Records"
    format="integer"
  ></nubi-kpi>

  <nubi-kpi
    data-el-id="kpi_2"
    query-id="demo_all"
    value-col="x"
    label="Sample X"
    format="number"
  ></nubi-kpi>
</section>

<section style="margin-bottom:1.5rem;">
  <h2 style="font-size:1rem;font-weight:600;color:#374151;margin:0 0 0.75rem;">Data Table</h2>
  <nubi-table
    data-el-id="table_1"
    query-id="demo_all"
    limit="25"
  ></nubi-table>
</section>
`,
  bindings: {},
  variables: [],
}

// ---------------------------------------------------------------------------
// CanvasViewPage
// ---------------------------------------------------------------------------

export default function CanvasViewPage() {
  const { id } = useParams()
  const [searchParams, setSearchParams] = useSearchParams()

  const canWrite = useCanWrite()

  const [doc, setDoc] = useState(null)
  const [canvasId, setCanvasId] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  useEffect(() => {
    // Short-circuit for the built-in sample route.
    if (id === 'sample') {
      setDoc(SAMPLE_CANVAS_DOC)
      setLoading(false)
      return
    }

    let cancelled = false

    async function load() {
      setLoading(true)
      setError(null)
      try {
        const canvas = await get(`/canvases/${id}`)
        if (cancelled) return

        setCanvasId(canvas?.id ?? id)

        const canvasDoc = canvas?.config?.doc ?? canvas?.doc ?? null
        if (canvasDoc) {
          setDoc(canvasDoc)
        } else {
          setError('This canvas has no content yet. Showing the sample canvas.')
          setDoc(SAMPLE_CANVAS_DOC)
        }
      } catch (err) {
        if (cancelled) return
        setError(
          err.status === 404
            ? `Canvas "${id}" not found. Showing the sample canvas.`
            : `Could not load canvas "${id}" (${err.message}). Showing the sample canvas.`
        )
        setDoc(SAMPLE_CANVAS_DOC)
      } finally {
        if (!cancelled) setLoading(false)
      }
    }

    load()
    return () => { cancelled = true }
  }, [id])

  // ---------------------------------------------------------------------------
  // Variable ↔ URL sync (mirrors DashboardViewPage)
  // ---------------------------------------------------------------------------

  const knownVarNames = useMemo(() => {
    if (!doc?.variables) return []
    const named = doc.variables.filter(v => v.name)
    const anyOptIn = named.some(v => v.url_bind)
    const eligible = anyOptIn ? named.filter(v => v.url_bind) : named
    return eligible.map(v => v.name)
  }, [doc])

  const urlVars = useMemo(
    () => extractVarsFromURL(searchParams, knownVarNames),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [searchParams.toString(), knownVarNames],
  )

  const initialVariables = useMemo(
    () => ({ ...urlVars }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [JSON.stringify(urlVars)],
  )

  const handleVariableChange = useCallback((name, value) => {
    setSearchParams(
      prev => applyVarToSearchParams(prev, name, value, {
        knownVarNames,
        lockedParams: {},
      }),
      { replace: true },
    )
  }, [setSearchParams, knownVarNames])

  // ---------------------------------------------------------------------------
  // Render states
  // ---------------------------------------------------------------------------

  if (loading) {
    return (
      <div className="max-w-[110rem] mx-auto px-4 sm:px-6 lg:px-8 py-8" aria-busy="true" aria-label="Loading canvas">
        <Skeleton className="h-8 w-64 rounded-lg mb-6" />
        <div className="space-y-4">
          <Skeleton className="h-32 w-full rounded-xl" />
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <Skeleton className="h-40 rounded-xl" />
            <Skeleton className="h-40 rounded-xl" />
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className="max-w-[110rem] mx-auto px-4 sm:px-6 lg:px-8 py-8" data-testid="canvas-view-page">

      {/* Fallback / error notice */}
      {error && (
        <div
          className="mb-4 px-4 py-3 rounded-xl text-sm flex items-start gap-2 border"
          data-testid="canvas-view-error"
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

      {/* Edit link for users with write access */}
      {canWrite && canvasId && id !== 'sample' && (
        <div className="mb-4 flex justify-end">
          <Link
            to={`/canvas/${canvasId}`}
            className="text-sm text-primary hover:opacity-80 font-medium transition-opacity focus:outline-none focus:ring-2 focus:ring-ring rounded"
          >
            Edit in editor &rarr;
          </Link>
        </div>
      )}

      {/* Canvas title */}
      {doc?.title && (
        <h1 className="text-2xl font-bold font-display text-fg mb-4 leading-tight">{doc.title}</h1>
      )}

      {/* Canvas content */}
      {doc && (
        <div data-testid="canvas-renderer-wrapper">
          <CanvasRenderer
            doc={doc}
            initialVariables={initialVariables}
            onVariableChange={handleVariableChange}
          />
        </div>
      )}
    </div>
  )
}
