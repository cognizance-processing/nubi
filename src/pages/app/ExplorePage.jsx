/**
 * ExplorePage — first-party dogfood of the embeddable component library.
 *
 * This screen proves "the app is its own SDK" by consuming two of the embed/
 * web components directly inside the React SPA:
 *
 *   <nubi-metric-explorer>   — governed metric query builder
 *   <nubi-explain>           — root-cause contribution analysis
 *
 * Both components are wired to:
 *  - The SPA's active session token via the embedBridge token callback.
 *  - The SPA's current light/dark theme via the embedBridge theme adapter,
 *    so they look native inside the app without style duplication.
 *
 * Custom elements are registered once on app bootstrap (src/main.jsx) via
 * registerNubiAuthoringWidgets().  Here we just mount them in JSX using a
 * useRef + useEffect pattern that:
 *  1. Sets --nubi-* tokens on the host element immediately after mount.
 *  2. Re-applies tokens whenever the SPA theme changes (useTheme()).
 *  3. Disconnects cleanly on unmount (the CE's disconnectedCallback handles
 *     its own cleanup; we only need to release the ref).
 *
 * Layout: two-column on desktop (explorer left, explain right), stacked on mobile.
 */

import { useEffect, useRef, useCallback } from 'react'
import { Compass } from 'lucide-react'
import { useTheme } from '../../contexts/ThemeContext.jsx'
import { getAccessToken } from '../../lib/api.js'
import { registerSpaTokenBridge, applySpaThemeTo } from '../../lib/embedBridge.js'
import { PageRoot, PageHeader } from '../../components/app/PageShell.jsx'
import { _makeSampleModelAttribution } from '../../../embed/widgets/nubi-explain.js'

// ---------------------------------------------------------------------------
// Host-supplied model-attribution payload (the SECOND, distinct payload).
//
// Nubi does NOT compute this — it answers "why did the model predict X" rather
// than "why did the number move". A real deployment would build this from its
// own model's per-prediction SHAP/feature attributions and pass it in via the
// `.modelAttribution` property. Here we use the widget's sample so the dogfood
// shows the two payloads rendered side-by-side, visually namespaced.
// ---------------------------------------------------------------------------
const SAMPLE_MODEL_ATTRIBUTION = _makeSampleModelAttribution()

// ---------------------------------------------------------------------------
// Token bridge — install the window callback once per module load.
// The function name is stable; re-registration on re-render is harmless.
// ---------------------------------------------------------------------------
const GET_TOKEN_FN = registerSpaTokenBridge(
  () => getAccessToken() ?? null,
  '__nubiSpaGetToken',
)

// ---------------------------------------------------------------------------
// Backend URL helper (mirrors src/lib/api.js BASE logic)
// ---------------------------------------------------------------------------
const _backendUrl = import.meta.env.VITE_BACKEND_URL ?? ''
const BACKEND_BASE =
  import.meta.env.DEV || !_backendUrl
    ? `${window.location.protocol}//${window.location.host}`
    : _backendUrl

// ---------------------------------------------------------------------------
// useEmbedRef — attach a web-component ref and keep its theme in sync.
// ---------------------------------------------------------------------------

/**
 * Returns a ref callback for a custom element host.
 * - On attach: applies SPA theme tokens.
 * - On theme change: re-applies tokens.
 * The CE's own disconnectedCallback handles teardown; we don't need to do
 * anything extra on the React side.
 *
 * @param {string} _theme — current SPA theme string ('light'|'dark');
 *   included as a dep so the effect re-runs on theme toggle.
 * @returns {React.RefObject}
 */
function useEmbedRef(_theme) {
  const ref = useRef(null)

  useEffect(() => {
    const el = ref.current
    if (!el) return
    applySpaThemeTo(el)
  }, [_theme])

  return ref
}

// ---------------------------------------------------------------------------
// ExplorePage
// ---------------------------------------------------------------------------

export default function ExplorePage() {
  const { theme } = useTheme()

  // One ref per embedded component.
  const explorerRef = useEmbedRef(theme)
  const explainRef  = useEmbedRef(theme)

  // Date range for the explain panel. The demo dataset is historical, so the
  // dogfood compares H2 vs H1 of the demo year (this is where retail_nsv has
  // data) — current = Jul–Dec, comparison = Jan–Jun. A real deployment would
  // pass live, today-relative windows here.
  const currentStart    = '2024-07-01'
  const currentEnd      = '2024-12-31'
  const comparisonStart = '2024-01-01'
  const comparisonEnd   = '2024-06-30'

  // Ref callback: apply theme immediately when the element is inserted into
  // the DOM (before the first paint).
  const attachExplorer = useCallback((el) => {
    explorerRef.current = el
    if (el) applySpaThemeTo(el)
  }, [explorerRef])

  const attachExplain = useCallback((el) => {
    explainRef.current = el
    if (el) {
      applySpaThemeTo(el)
      // Explicit prop pathway: hand the host's model-attribution payload to the
      // widget. The widget renders it as a separate, namespaced section and
      // never calls the backend for it.
      el.modelAttribution = SAMPLE_MODEL_ATTRIBUTION
    }
  }, [explainRef])

  return (
    <PageRoot>
      <PageHeader
        title="Explore"
        subtitle="Query and analyse metrics using the shared embeddable component library."
      >
        {/* Dogfood badge — visible signal that this surface consumes embed/ */}
        <span
          className="inline-flex items-center gap-1.5 rounded-full border border-border bg-surface px-3 py-1 text-xs font-medium text-text-muted select-none"
          title="This page consumes the shared embed/ component library"
        >
          <Compass size={12} aria-hidden="true" />
          embed dogfood
        </span>
      </PageHeader>

      {/*
        Two-column layout:
          left (flex-1):  nubi-metric-explorer — governed metric query builder
          right (w-96):   nubi-explain         — contribution analysis
        On mobile they stack vertically.
      */}
      <div className="flex flex-col lg:flex-row gap-4 mt-2">

        {/* ── Metric Explorer ─────────────────────────────────────────────── */}
        <div className="flex-1 min-h-[520px] flex flex-col rounded-xl border border-border overflow-hidden bg-surface shadow-sm">
          <div className="px-4 py-2.5 border-b border-border bg-surface-2 flex items-center gap-2 shrink-0">
            <span className="text-xs font-semibold text-text-muted uppercase tracking-wide">
              Metric Explorer
            </span>
            <span className="ml-auto text-[10px] text-text-muted font-mono opacity-60">
              &lt;nubi-metric-explorer&gt;
            </span>
          </div>

          {/*
            The custom element is mounted as a plain HTML element.
            React doesn't know about its shadow DOM internals.
            Attributes:
              get-token  — name of the window.* bridge function
              backend    — base URL of the Nubi API
              theme      — 'light'|'dark' propagated from ThemeContext
            The ref callback applies --nubi-* tokens synchronously on mount.
          */}
          <nubi-metric-explorer
            ref={attachExplorer}
            get-token={GET_TOKEN_FN}
            backend={BACKEND_BASE}
            theme={theme}
            metric-id="demo_revenue"
            style={{ flex: 1, display: 'flex', flexDirection: 'column' }}
          />
        </div>

        {/* ── Explain (contribution analysis) ─────────────────────────────── */}
        <div
          className="w-full lg:w-96 flex flex-col rounded-xl border border-border overflow-hidden bg-surface shadow-sm"
          style={{ minHeight: '360px' }}
        >
          <div className="px-4 py-2.5 border-b border-border bg-surface-2 flex items-center gap-2 shrink-0">
            <span className="text-xs font-semibold text-text-muted uppercase tracking-wide">
              Contribution Analysis
            </span>
            <span className="ml-auto text-[10px] text-text-muted font-mono opacity-60">
              &lt;nubi-explain&gt;
            </span>
          </div>

          {/*
            nubi-explain needs metric-id + date range attributes.
            The sample fallback in the CE renders demo data if the API call
            fails (e.g. no metric configured yet), so this always shows
            something useful.
          */}
          <nubi-explain
            ref={attachExplain}
            get-token={GET_TOKEN_FN}
            backend={BACKEND_BASE}
            theme={theme}
            metric-id="retail_nsv"
            current-start={currentStart}
            current-end={currentEnd}
            comparison-start={comparisonStart}
            comparison-end={comparisonEnd}
            include-summary="false"
            style={{ flex: 1, display: 'block' }}
          />
        </div>

      </div>

      {/* Info strip */}
      <p className="mt-4 text-xs text-text-muted leading-relaxed max-w-2xl">
        Both panels above are standard web components from{' '}
        <code className="font-mono bg-surface-2 px-1 py-0.5 rounded text-[11px]">embed/widgets/</code>.
        They inherit the app&apos;s current light/dark theme via a CSS-variable bridge
        and authenticate using the SPA session token — no separate SDK initialisation required.
      </p>
    </PageRoot>
  )
}
