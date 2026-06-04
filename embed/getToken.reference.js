/**
 * getToken.reference.js — Reference implementation of the Nubi `getToken()` contract.
 *
 * PURPOSE
 * -------
 * The <nubi-dashboard> web component accepts a `get-token` attribute whose value
 * is the name of a function on `window`. That function must conform to the
 * `getToken` contract:
 *
 *   getToken: () => Promise<string>
 *
 * It is called every time the component needs to make a query. The function must
 * return a short-lived JWT (≤ 15 minutes recommended) signed by the host's private
 * key. The component itself NEVER stores the token beyond a single render cycle.
 *
 * TOKEN SHAPE (required claims)
 * -----------------------------
 * {
 *   iss:          string,           // Issuer URI registered with the Nubi backend
 *   sub:          string,           // End-user or service-account identifier
 *   aud:          string,           // Nubi project / audience ("nubi:<project-id>")
 *   org:          string,           // Nubi organisation slug
 *   project:      string,           // Nubi project slug
 *   roles:        string[],         // e.g. ["viewer"]
 *   scope:        string[],         // Must include "read:*" or "read:dashboard:<id>"
 *   policies:     Record<string, string>, // RLS column-value pairs: {"tenant_id": "acme"}
 *   embed_origin: string,           // The exact Origin the embed is served from
 *   exp:          number,           // Unix timestamp — MUST be ≤ now + 900 (15 min)
 *   iat:          number,           // Issued-at
 * }
 *
 * TOKEN VERIFICATION (backend)
 * ----------------------------
 * The Nubi backend verifies:
 *   - Signature via JWKS endpoint registered for `iss`
 *   - Algorithm pinned to RS256 or ES256 (never `none`)
 *   - `exp` not exceeded
 *   - `aud` matches the registered project
 *   - `iss` is in the issuer registry
 *   - `embed_origin` matches the request's Origin header
 *   - `scope` contains the required `read:*` or narrower scope
 *   - `policies` are injected as server-side RLS predicates (not trusted from body)
 *
 * HOW TO USE THIS FILE
 * --------------------
 * 1. Copy this file into your host application.
 * 2. Replace `mintUrl` with the URL of YOUR backend endpoint that mints and signs
 *    the short-lived embed JWT (using your private key).
 * 3. Assign the result to `window.getToken` (or any name you pass to `get-token`).
 * 4. Add `<nubi-dashboard get-token="getToken" ...>` to your page.
 *
 * EXAMPLE (host page)
 * -------------------
 *   import { createGetToken } from './getToken.reference.js'
 *
 *   window.getToken = createGetToken({
 *     mintUrl: 'https://your-app.example.com/api/embed-token',
 *   })
 *
 * SECURITY NOTES
 * --------------
 * - Your `mintUrl` endpoint must be authenticated — only serve tokens to users
 *   who have proven access to the dashboard being embedded.
 * - The mint endpoint MUST set `embed_origin` to the exact origin that will
 *   host the embed. The Nubi backend enforces this.
 * - Keep `exp` short (≤ 15 min). This implementation refreshes ~60 s before
 *   expiry to avoid serving a stale token on the leading edge of a re-render.
 * - Tokens are cached in memory only (closure variable) — they are never written
 *   to localStorage or sessionStorage.
 */

/**
 * Create a `getToken()` function that:
 *  - Calls `mintUrl` (POST) the first time a token is needed.
 *  - Caches the returned JWT in memory.
 *  - Silently re-fetches ~60 seconds before the token expires.
 *  - Works without any third-party libraries (vanilla JS, browser-native fetch).
 *
 * @param {object} options
 * @param {string} options.mintUrl
 *   The absolute URL of your backend's token-mint endpoint.
 *   It should return `{ token: "<jwt>" }` (or just `"<jwt>"` as plain text).
 * @param {number} [options.refreshLeadSeconds=60]
 *   How many seconds before `exp` to treat the cached token as stale
 *   and proactively fetch a fresh one. Default: 60.
 * @param {RequestInit} [options.fetchOptions={}]
 *   Extra options forwarded to every `fetch()` call (e.g. headers for your
 *   own auth cookie, `credentials: 'include'`, etc.).
 * @returns {() => Promise<string>}
 *   An async function that resolves to a valid JWT string. Pass its NAME on
 *   `window` to the `get-token` attribute of `<nubi-dashboard>`.
 *
 * @example
 *   window.getToken = createGetToken({
 *     mintUrl: '/api/embed-token',
 *     fetchOptions: { credentials: 'include' },   // send your session cookie
 *   })
 */
export function createGetToken({ mintUrl, refreshLeadSeconds = 60, fetchOptions = {} }) {
  if (!mintUrl) throw new Error('[getToken] mintUrl is required')

  /** @type {string | null} Cached JWT string */
  let _cachedToken = null

  /** @type {number | null} Cached expiry in Unix seconds (from JWT payload) */
  let _cachedExp = null

  /** @type {Promise<string> | null} In-flight mint request (deduplicates parallel calls) */
  let _inflight = null

  /**
   * Decode the `exp` claim from a JWT payload without verifying the signature.
   * Used solely to decide when to refresh — the backend always re-verifies.
   *
   * @param {string} token
   * @returns {number | null} Unix timestamp, or null if unparseable.
   */
  function decodeExp(token) {
    try {
      const parts = token.split('.')
      if (parts.length !== 3) return null

      // Base64url decode
      const b64 = parts[1].replace(/-/g, '+').replace(/_/g, '/')
      const payload = JSON.parse(atob(b64))

      return typeof payload.exp === 'number' ? payload.exp : null
    } catch {
      return null
    }
  }

  /**
   * Return true if the cached token is still valid (will not expire within
   * `refreshLeadSeconds`).
   *
   * @returns {boolean}
   */
  function isCachedTokenFresh() {
    if (!_cachedToken || _cachedExp === null) return false
    const nowSeconds = Math.floor(Date.now() / 1000)
    return _cachedExp - nowSeconds > refreshLeadSeconds
  }

  /**
   * Fetch a fresh token from `mintUrl`.
   * Accepts both `{ token: "<jwt>" }` (JSON) and a plain JWT string response.
   *
   * @returns {Promise<string>}
   */
  async function mintFresh() {
    const response = await fetch(mintUrl, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Accept': 'application/json, text/plain',
        ...(fetchOptions.headers || {}),
      },
      ...fetchOptions,
    })

    if (!response.ok) {
      throw new Error(`[getToken] Mint endpoint returned HTTP ${response.status}`)
    }

    const contentType = response.headers.get('content-type') || ''

    let token
    if (contentType.includes('application/json')) {
      const body = await response.json()
      // Accept { token: "..." } or { access_token: "..." } or bare string
      if (typeof body === 'string') {
        token = body
      } else if (typeof body?.token === 'string') {
        token = body.token
      } else if (typeof body?.access_token === 'string') {
        token = body.access_token
      } else {
        throw new Error('[getToken] Mint response did not contain a token string')
      }
    } else {
      // Plain text JWT
      token = (await response.text()).trim()
    }

    if (!token || token.split('.').length !== 3) {
      throw new Error('[getToken] Mint endpoint returned a malformed JWT')
    }

    // Cache the token and decode its expiry
    _cachedToken = token
    _cachedExp = decodeExp(token)

    return token
  }

  /**
   * The public getToken function.
   *
   * - Returns the cached token immediately if it is still fresh.
   * - Deduplicates concurrent calls: if a mint is already in flight, all callers
   *   wait for the same promise.
   * - On error, throws — the <nubi-dashboard> component will catch and fall back
   *   to its sample table.
   *
   * @returns {Promise<string>}
   */
  async function getToken() {
    // Fast path: serve from cache
    if (isCachedTokenFresh()) {
      return _cachedToken
    }

    // Deduplicate parallel calls during an in-flight mint
    if (_inflight) {
      return _inflight
    }

    // Mint a fresh token
    _inflight = mintFresh().finally(() => {
      _inflight = null
    })

    return _inflight
  }

  return getToken
}

// ---------------------------------------------------------------------------
// USAGE SUMMARY (printed as a comment for host developers)
// ---------------------------------------------------------------------------
//
//  Step 1 — Your backend mint endpoint (/api/embed-token or similar):
//    • Authenticate the calling user (session / OAuth / your auth system).
//    • Build the JWT payload with the claims listed in TOKEN SHAPE above.
//    • Sign with your RS256 or ES256 private key.
//    • Return { "token": "<signed-jwt>" }.
//
//  Step 2 — Register your JWKS with Nubi:
//    • Provide your JWKS endpoint URL (e.g. https://your-app.com/.well-known/jwks.json).
//    • Nubi will fetch and cache it to verify your embed tokens.
//
//  Step 3 — Host page setup:
//    <script type="module">
//      import { createGetToken } from './getToken.reference.js'
//      window.getEmbedToken = createGetToken({
//        mintUrl: '/api/embed-token',
//        fetchOptions: { credentials: 'include' },
//      })
//    </script>
//    <script src="https://cdn.nubi.dev/embed/nubi-dashboard.js"></script>
//    <nubi-dashboard
//      query="SELECT * FROM sales WHERE region = 'EMEA'"
//      get-token="getEmbedToken"
//      backend="https://api.nubi.dev"
//    ></nubi-dashboard>
