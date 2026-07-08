/**
 * ExplorePage — first-party dogfood of the embeddable component library.
 *
 * This screen proves "the app is its own SDK" by consuming an embed/ web
 * component directly inside the React SPA:
 *
 *   <nubi-metric-explorer>   — governed metric query builder
 *
 * The component is wired to:
 *  - The SPA's active session token via the embedBridge token callback.
 *  - The SPA's current light/dark theme via the embedBridge theme adapter,
 *    so it looks native inside the app without style duplication.
 *
 * Custom elements are registered once on app bootstrap (src/main.jsx) via
 * registerNubiAuthoringWidgets().  Here we just mount it in JSX using a
 * useRef + useEffect pattern that:
 *  1. Sets --nubi-* tokens on the host element immediately after mount.
 *  2. Re-applies tokens whenever the SPA theme changes (useTheme()).
 *  3. Disconnects cleanly on unmount (the CE's disconnectedCallback handles
 *     its own cleanup; we only need to release the ref).
 */

import { useEffect, useRef, useCallback } from 'react'
import { Compass } from 'lucide-react'
import { useTheme } from '../../contexts/ThemeContext.jsx'
import { getAccessToken } from '../../lib/api.js'
import { registerSpaTokenBridge, applySpaThemeTo } from '../../lib/embedBridge.js'
import { PageRoot, PageHeader } from '../../components/app/PageShell.jsx'

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

  // Ref for the embedded component.
  const explorerRef = useEmbedRef(theme)

  // Ref callback: apply theme immediately when the element is inserted into
  // the DOM (before the first paint).
  const attachExplorer = useCallback((el) => {
    explorerRef.current = el
    if (el) applySpaThemeTo(el)
  }, [explorerRef])

  return (
    <PageRoot>
      <PageHeader
        title="Explore"
        subtitle="Query and analyse metrics using the shared embeddable component library."
      >
        {/* Dogfood badge — visible signal that this surface consumes embed/ */}
        <span
          className="inline-flex items-center gap-1.5 rounded-full border border-border bg-surface px-3 py-1 text-xs font-medium text-muted select-none"
          title="This page consumes the shared embed/ component library"
        >
          <Compass size={12} aria-hidden="true" />
          embed dogfood
        </span>
      </PageHeader>

      {/* ── Metric Explorer ───────────────────────────────────────────────── */}
      <div className="mt-2 min-h-[520px] flex flex-col rounded-xl border border-border overflow-hidden bg-surface shadow-sm">
        <div className="px-4 py-2.5 border-b border-border bg-surface-2 flex items-center gap-2 shrink-0">
          <span className="text-xs font-semibold text-muted uppercase tracking-wide">
            Metric Explorer
          </span>
          <span className="ml-auto text-[10px] text-muted font-mono opacity-60">
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

      {/* Info strip */}
      <p className="mt-4 text-xs text-muted leading-relaxed max-w-2xl">
        The panel above is a standard web component from{' '}
        <code className="font-mono bg-surface-2 px-1 py-0.5 rounded text-[11px]">embed/widgets/</code>.
        It inherits the app&apos;s current light/dark theme via a CSS-variable bridge
        and authenticates using the SPA session token — no separate SDK initialisation required.
      </p>
    </PageRoot>
  )
}
