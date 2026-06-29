/**
 * CanvasRenderer.jsx — Runtime renderer for a CanvasDoc.
 *
 * Pipeline:
 *   1. sanitizeDashboardHtml(doc.html)  — NEVER bypass (done inside renderDashboardDoc)
 *   2. set innerHTML on a container div
 *   3. upgrade <nubi-*> custom elements (renderDashboardDoc.js)
 *   4. For each element with a data-el-id that has a binding:
 *      - kind "query"  → runArrowQueryById + widget render
 *      - kind "metric" → metric binding via element attributes
 *      - kind "api"    → HTTP fetch via connector path
 *   5. Interpolate {{token}} text/attr bindings via renderWidgetHtml() with
 *      resolved, HTML-escaped values.
 *
 * Security:
 *   - All HTML passes through sanitizeDashboardHtml (via renderDashboardDoc).
 *   - Data values are HTML-escaped before injection (via renderWidgetHtml).
 *   - RLS honored: data flows through runArrowQueryById → collect.py / run_query_rows.
 *
 * Context providers:
 *   Wrapped in VariableProvider + CrossFilterProvider + RefreshContext, exactly
 *   as SpecRenderer does, so variables, cross-filter clicks, and auto-refresh work.
 */

import { useEffect, useRef, useMemo, useState } from 'react'
import { renderDashboardDoc } from './renderDashboardDoc.js'
import { renderWidgetHtml } from './widgetHtml.js'
import { VariableProvider, useSetVariable } from './VariableStore.jsx'
import { CrossFilterProvider } from './CrossFilterContext.jsx'
import { RefreshContext } from './RefreshContext.jsx'
import { useAutoRefresh } from './useAutoRefresh.js'
import { runArrowQueryById } from '../lib/wasmRuntime.js'
import { get } from '../lib/api.js'

// ---------------------------------------------------------------------------
// Build initial variable defaults from doc.variables
// ---------------------------------------------------------------------------

function buildVariableDefaults(docVariables) {
  if (!Array.isArray(docVariables)) return {}
  const defaults = {}
  for (const v of docVariables) {
    if (v?.name) {
      defaults[v.name] = v.default ?? undefined
    }
  }
  return defaults
}

// ---------------------------------------------------------------------------
// CrossFilterProviderWrapper — reads setVariable from VariableStore context
// ---------------------------------------------------------------------------

function CrossFilterProviderWrapper({ onCrossFilter, children }) {
  const setVariable = useSetVariable()
  return (
    <CrossFilterProvider setVariable={setVariable} onCrossFilter={onCrossFilter}>
      {children}
    </CrossFilterProvider>
  )
}

// ---------------------------------------------------------------------------
// CanvasRendererInner — the actual renderer once providers are mounted
// ---------------------------------------------------------------------------

/**
 * @param {{
 *   doc: object,              — CanvasDoc { version, html, bindings, variables, ... }
 *   className?: string,
 * }} props
 */
function CanvasRendererInner({ doc, className = '' }) {
  const containerRef = useRef(null)

  // Auto-refresh support (mirrors SpecRenderer pattern).
  const refreshIntervalMs = doc?.refresh?.interval_ms ?? null
  const [refreshEpoch, setRefreshEpoch] = useState(0)
  useAutoRefresh({
    intervalMs: refreshIntervalMs,
    onRefresh: () => setRefreshEpoch(e => e + 1),
    enabled: true,
  })

  const docHtml = doc?.html ?? ''
  const docBindings = doc?.bindings ?? {}

  useEffect(() => {
    const container = containerRef.current
    if (!container) return

    let cancelled = false
    let domCleanup = () => {}

    async function run() {
      // Step 1 + 2 + 3: sanitize → innerHTML → upgrade custom elements.
      domCleanup = renderDashboardDoc(container, docHtml, {
        backend: '',
        getToken: () => null,
      })

      if (cancelled) return

      // Step 4: bind data to elements by data-el-id.
      const bindingEntries = Object.entries(docBindings)
      if (bindingEntries.length === 0) return

      await Promise.allSettled(
        bindingEntries.map(async ([elId, binding]) => {
          if (cancelled) return
          const el = container.querySelector(`[data-el-id="${CSS.escape(elId)}"]`)
          if (!el) return

          try {
            if (binding.kind === 'query') {
              const queryId = binding.query_id
              if (!queryId) return
              const { table } = await runArrowQueryById(queryId, binding.params ?? {})
              if (cancelled) return

              if (el.tagName.toLowerCase().startsWith('nubi-')) {
                // Custom elements handle their own rendering; ensure query-id is set.
                if (!el.hasAttribute('query-id')) {
                  el.setAttribute('query-id', queryId)
                }
              } else {
                // Plain element: resolve {{token}} template.
                const template = el.innerHTML
                if (template && template.includes('{{')) {
                  const field = binding.field
                  const extra = {}
                  if (field && table) {
                    const col = table.getChild(field)
                    if (col && table.numRows > 0) {
                      extra.value = col.get(0)
                    }
                  }
                  el.innerHTML = renderWidgetHtml(template, { table, extra })
                }
              }
            } else if (binding.kind === 'metric') {
              // Metric: set the metric-id attribute for the custom element to pick up.
              if (binding.metric?.metric_id && !el.hasAttribute('metric-id')) {
                el.setAttribute('metric-id', binding.metric.metric_id)
              }
            } else if (binding.kind === 'api') {
              // API connector binding.
              const { connector_id, path: apiPath, select } = binding
              if (!connector_id) return
              const data = await get(
                `/connectors/${connector_id}/fetch?path=${encodeURIComponent(apiPath ?? '')}`
              )
              if (cancelled) return
              // Resolve a basic JSONPath subset.
              let value = data
              if (select && typeof select === 'string') {
                const parts = select.replace(/^\$\.?/, '').split('.')
                for (const part of parts) {
                  if (value == null) break
                  const arrMatch = /^(.+)\[(\d+)\]$/.exec(part)
                  if (arrMatch) {
                    value = value[arrMatch[1]]?.[Number(arrMatch[2])]
                  } else {
                    value = value[part]
                  }
                }
              }
              if (value !== undefined && value !== null) {
                const template = el.innerHTML
                if (template && template.includes('{{')) {
                  el.innerHTML = renderWidgetHtml(template, { extra: { value } })
                } else {
                  el.innerHTML = renderWidgetHtml('{{value}}', { extra: { value } })
                }
              }
            }
          } catch (err) {
            console.warn(`[CanvasRenderer] Binding ${elId} failed:`, err.message)
          }
        })
      )
    }

    run()

    return () => {
      cancelled = true
      domCleanup()
    }
    // refreshEpoch is intentionally included so the canvas re-renders on auto-refresh.
    // JSON.stringify(docBindings) gives a stable primitive dep for the object.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [docHtml, JSON.stringify(docBindings), refreshEpoch])

  return (
    <div
      ref={containerRef}
      className={`nubi-canvas-renderer w-full ${className}`}
      data-testid="canvas-renderer"
    />
  )
}

// ---------------------------------------------------------------------------
// CanvasRenderer — public export with provider wrappers
// ---------------------------------------------------------------------------

/**
 * Render a CanvasDoc in read-only mode.
 *
 * Props:
 *   doc               {object}   — CanvasDoc (version, html, bindings, variables, ...)
 *   initialVariables  {object}   — externally supplied variable values (e.g. from URL)
 *   onVariableChange  {function} — fired when a filter widget changes a variable
 *   onCrossFilter     {function} — fired on cross-filter / navigate events
 *   className         {string}   — additional CSS classes for the root element
 */
export default function CanvasRenderer(props) {
  const { doc, initialVariables = {}, onVariableChange, onCrossFilter, className = '' } = props

  if (!doc) {
    return (
      <div className="flex items-center justify-center py-16 text-sm text-muted">
        No canvas document provided.
      </div>
    )
  }

  // This component follows the same null-guard pattern as SpecRenderer: the early
  // return above means hooks below only run when doc is non-null. React mounts a
  // fresh inner component each time doc transitions null → non-null, which is safe.
  return <CanvasRendererWithProviders doc={doc} initialVariables={initialVariables} onVariableChange={onVariableChange} onCrossFilter={onCrossFilter} className={className} />
}

function CanvasRendererWithProviders({ doc, initialVariables, onVariableChange, onCrossFilter, className }) {
  const variableDefaults = useMemo(
    () => ({
      ...buildVariableDefaults(doc.variables),
      ...initialVariables,
    }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [JSON.stringify(doc.variables), JSON.stringify(initialVariables)],
  )

  return (
    <VariableProvider initialValues={variableDefaults} onVariableChange={onVariableChange}>
      <RefreshContext.Provider value={0}>
        <CrossFilterProviderWrapper onCrossFilter={onCrossFilter}>
          <CanvasRendererInner
            doc={doc}
            className={className}
          />
        </CrossFilterProviderWrapper>
      </RefreshContext.Provider>
    </VariableProvider>
  )
}
