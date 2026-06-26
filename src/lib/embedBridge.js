/**
 * embedBridge.js — Theme adapter and token bridge between the SPA and the
 * embeddable web component library (embed/).
 *
 * Two concerns:
 *
 * 1. THEME ADAPTER
 *    The SPA uses --brand-* / --primary / --bg / --surface / etc. CSS custom
 *    properties (src/index.css).  The embed library uses --nubi-* tokens
 *    (embed/theme.js).  `buildNubiTokensFromSpa()` reads the SPA's computed
 *    tokens from :root and maps them to the 25 --nubi-* slots so web
 *    components look native inside the app.
 *
 * 2. TOKEN BRIDGE
 *    Web components resolve their JWT via a `get-token` attribute that names a
 *    window.* callback.  `registerSpaTokenBridge(getTokenFn, name?)` installs
 *    that callback on `window` and returns the function name to pass as the
 *    `get-token` attribute value.
 *
 * Usage (in a React component):
 *
 *   import { registerSpaTokenBridge, buildNubiTokensFromSpa } from '../../lib/embedBridge.js'
 *
 *   const getTokenFnName = registerSpaTokenBridge(() => api.getAccessToken())
 *
 *   // Pass theme tokens to a custom element via a ref:
 *   const tokens = buildNubiTokensFromSpa()
 *   Object.entries(tokens).forEach(([k, v]) => el.style.setProperty(k, v))
 */

// ---------------------------------------------------------------------------
// Token bridge
// ---------------------------------------------------------------------------

const BRIDGE_FN_NAME = '__nubiSpaGetToken'

/**
 * Scopes that the backend grants to every first-party HS256 session token that
 * carries no explicit `scope` claim (see _FIRST_PARTY_SCOPES in
 * backend/app/auth/verify.py).  We mirror them here so that client-side
 * cosmetic gating in the web components sees the same set the server applies.
 *
 * SECURITY NOTE: this never changes what the server will accept — the backend
 * validates scopes independently.  These are purely for client-side UI gating
 * (show/hide controls) inside the shadow DOM.  The server remains the sole
 * authority.
 */
const _SPA_FIRST_PARTY_SCOPES = 'read:* edit:* author:sql author:metric'

/**
 * Inject the standard first-party scope claim into a JWT payload for
 * client-side cosmetic capability gating.
 *
 * The SPA session token is an HS256 first-party token whose payload carries
 * no `scope` claim — the backend applies `_FIRST_PARTY_SCOPES` as a default
 * only at verification time.  The embed web components decode scopes from the
 * JWT payload client-side (without verifying the signature) to decide whether
 * to enable authoring controls.  Without this injection they always see an
 * empty scope list and render READ-ONLY even though the server would accept
 * `author:metric` requests from the same token.
 *
 * This function:
 *  1. Base64url-decodes the JWT payload.
 *  2. Sets `scope` to `_SPA_FIRST_PARTY_SCOPES` if and only if the payload
 *     carries no `scope` / `scopes` / `scp` claim (i.e. this is a plain
 *     first-party token, not an already-scoped embed or agent token).
 *  3. Re-encodes the payload and returns a new `header.payload.signature`
 *     string.  The signature stays the same; `decodeScopes()` never verifies
 *     it, so the injected claim is read correctly.
 *
 * Returns the original token unchanged on any parse error so we never break
 * the auth flow.
 *
 * @param {string | null} token
 * @returns {string | null}
 */
function injectSpaScopes(token) {
  if (!token) return token
  try {
    const parts = token.split('.')
    if (parts.length !== 3) return token

    // Decode payload (no signature verification needed here).
    const b64 = parts[1].replace(/-/g, '+').replace(/_/g, '/')
    const padded = b64 + '='.repeat((4 - (b64.length % 4)) % 4)
    const payload = JSON.parse(atob(padded))

    // Only inject when the token carries no scope claim (plain first-party
    // session token).  Restricted tokens (agent sandbox, embed) already
    // carry explicit scope lists — don't overwrite them.
    if (payload.scope || payload.scopes || payload.scp) return token

    // Inject the first-party default scope set.
    payload.scope = _SPA_FIRST_PARTY_SCOPES

    // Re-encode payload (base64url, no padding).
    const newPayload = btoa(JSON.stringify(payload))
      .replace(/\+/g, '-')
      .replace(/\//g, '_')
      .replace(/=+$/, '')

    return `${parts[0]}.${newPayload}.${parts[2]}`
  } catch {
    // Never break the auth flow on parse errors.
    return token
  }
}

/**
 * Install a `window[name]` callback that the embed web components can call to
 * obtain the SPA's current session JWT.  Safe to call multiple times —
 * subsequent calls with the same name simply overwrite the previous callback.
 *
 * The returned token has the first-party scope set injected into its payload
 * (see `injectSpaScopes`) so that client-side cosmetic gating inside the web
 * components (e.g. the `author:metric` check in `<nubi-metric-explorer>`)
 * correctly enables authoring controls when the SPA session has full access.
 * The server still verifies all requests independently.
 *
 * @param {() => string | null | Promise<string | null>} getTokenFn
 *   Synchronous or async function returning the current access token.
 *   Typically wraps `import { getAccessToken } from './api.js'`.
 * @param {string} [name]   window property name (default '__nubiSpaGetToken')
 * @returns {string}        the window property name to pass as `get-token`
 */
export function registerSpaTokenBridge(getTokenFn, name = BRIDGE_FN_NAME) {
  if (typeof window !== 'undefined') {
    window[name] = async () => {
      const token = await getTokenFn()
      return injectSpaScopes(token)
    }
  }
  return name
}

// ---------------------------------------------------------------------------
// Theme adapter
// ---------------------------------------------------------------------------

/**
 * Read the SPA's computed CSS custom properties from the document root and
 * return a map of --nubi-* token overrides that mirror the current SPA theme.
 *
 * This is called once per component mount (or on theme change) so the embed
 * components pick up light ↔ dark switches automatically.
 *
 * The mapping intentionally stays close to the 25-token spec in embed/theme.js
 * so every visual surface in the shadow DOM is theme-consistent.
 *
 * @returns {Record<string, string>}
 */
export function buildNubiTokensFromSpa() {
  if (typeof window === 'undefined' || typeof getComputedStyle === 'undefined') {
    return {}
  }

  const cs = getComputedStyle(document.documentElement)

  /**
   * Read a CSS variable from :root, falling back to `fallback` if empty.
   * @param {string} varName
   * @param {string} fallback
   */
  function v(varName, fallback = '') {
    return cs.getPropertyValue(varName).trim() || fallback
  }

  return {
    // Backgrounds — map SPA surface tokens to nubi bg layers
    '--nubi-bg':     v('--bg',        '#f6f8fb'),
    '--nubi-bg-2':   v('--surface',   '#ffffff'),
    '--nubi-bg-3':   v('--surface-2', '#eef2f7'),

    // Foreground
    '--nubi-fg':       v('--text',       '#0e1729'),
    '--nubi-fg-muted': v('--text-muted', '#566377'),

    // Surface / accent
    '--nubi-accent':   v('--surface-2',  '#eef2f7'),
    '--nubi-border':   v('--border',     '#e2e8f0'),

    // Palette — map SPA primary/accent to nubi primary
    '--nubi-primary':    v('--primary',    '#2456a6'),
    '--nubi-primary-fg': v('--primary-fg', '#ffffff'),

    // Semantic
    '--nubi-success': v('--success', '#059669'),
    '--nubi-warning': v('--warning', '#d97706'),
    '--nubi-error':   v('--danger',  '#dc2626'),

    // Shape — use the SPA's radius scale
    '--nubi-radius':    v('--radius-md', '0.625rem'),
    '--nubi-radius-sm': v('--radius-sm', '0.375rem'),

    // Typography — reuse SPA font stack (Inter)
    '--nubi-font-sans': "'Inter', system-ui, -apple-system, 'Segoe UI', sans-serif",
    '--nubi-font-mono': "'Fira Code', 'Cascadia Code', Consolas, monospace",
    '--nubi-font-size-base': '13px',
    '--nubi-font-size-sm':   '11px',
    '--nubi-font-size-xs':   '10px',
    '--nubi-line-height':    '1.5',

    // Layout / z-index / motion — same as embed defaults
    '--nubi-toolbar-h':   '36px',
    '--nubi-z-editor':    v('--z-dropdown', '100'),
    '--nubi-z-overlay':   v('--z-overlay',  '300'),
    '--nubi-z-popover':   v('--z-popover',  '500'),
    '--nubi-transition':  '0.15s ease',
  }
}

/**
 * Apply SPA-derived theme tokens directly on a DOM element (usually a custom
 * element host).  Call this after mount and whenever the SPA theme changes.
 *
 * @param {HTMLElement} el
 */
export function applySpaThemeTo(el) {
  if (!el) return
  const tokens = buildNubiTokensFromSpa()
  for (const [k, val] of Object.entries(tokens)) {
    el.style.setProperty(k, val)
  }
}
