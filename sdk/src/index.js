/**
 * @nubi/sdk — index.js
 *
 * createNubiClient({ baseUrl, getToken }) → NubiClient
 *
 * The SDK is framework-agnostic ESM. It has no React dependency and no
 * reliance on the main Nubi app's src/ tree. The only runtime dependency is
 * apache-arrow (bundled into the dist by vite lib mode).
 *
 * API surface
 * -----------
 * client.auth.me()                            → Promise<{ user }>
 * client.query(sqlOrId, { params })           → Promise<arrow.Table>
 * client.resources.datastores.list()          → Promise<Resource[]>
 * client.resources.datastores.get(id)         → Promise<Resource>
 * client.resources.datastores.create({name, config}) → Promise<Resource>
 * client.resources.datastores.update(id, fields)     → Promise<Resource>
 * client.resources.datastores.remove(id)             → Promise<null>
 * (same for .boards / .widgets / .queries)
 * client.embed.mount(el, { query, token, backend }) → { unmount() }
 *
 * Token resolution
 * ----------------
 * getToken can be:
 *   - an async () => string function  (called before every authenticated request)
 *   - a static string                 (wrapped internally into a function)
 *
 * Error shape
 * -----------
 * Backend errors arrive as { error: { code, message } }.
 * The fetch wrapper throws an Error whose .code property is the error.code
 * string and whose .message is error.message. On non-JSON error responses,
 * .code is set to "http_error".
 */

import { tableFromIPC } from 'apache-arrow'

// ---------------------------------------------------------------------------
// createNubiClient
// ---------------------------------------------------------------------------

/**
 * Create a Nubi API client.
 *
 * @param {object} options
 * @param {string} options.baseUrl   — Base URL of the Nubi backend, e.g.
 *                                    "https://api.example.com". Should NOT
 *                                    include a trailing slash or "/api/v1".
 * @param {string | (() => Promise<string> | string)} options.getToken
 *                                  — Either a static JWT string or an async
 *                                    function that returns a JWT. Called
 *                                    before each authenticated request.
 * @returns {NubiClient}
 */
export function createNubiClient({ baseUrl, getToken }) {
  if (!baseUrl) throw new Error('[NubiSDK] baseUrl is required')
  if (!getToken) throw new Error('[NubiSDK] getToken is required')

  // Normalise getToken to always be a function
  const resolveToken =
    typeof getToken === 'function'
      ? getToken
      : () => Promise.resolve(getToken)

  // Strip trailing slash + ensure /api/v1 prefix is NOT baked in (callers
  // may pass "https://api.example.com" or "https://api.example.com/api/v1").
  const origin = baseUrl.replace(/\/+$/, '')
  const apiBase = origin.endsWith('/api/v1')
    ? origin
    : `${origin}/api/v1`

  // -------------------------------------------------------------------------
  // Internal fetch wrapper
  // -------------------------------------------------------------------------

  /**
   * Perform an authenticated fetch against the Nubi REST API.
   *
   * - Resolves the token via getToken().
   * - Sets Authorization: Bearer <token>.
   * - Sets Content-Type: application/json when a body is present.
   * - On a successful response returns the parsed JSON (or null for 204).
   * - On an error response, parses { error: { code, message } } and throws
   *   an Error with .code and .message set; falls back to HTTP status text.
   *
   * @param {string} path      — path relative to apiBase, e.g. "/auth/me"
   * @param {RequestInit} [init]
   * @returns {Promise<any>}
   */
  async function apiFetch(path, init = {}) {
    const token = await resolveToken()

    const headers = new Headers(init.headers ?? {})
    headers.set('Authorization', `Bearer ${token}`)

    if (init.body !== undefined && !headers.has('Content-Type')) {
      headers.set('Content-Type', 'application/json')
    }

    const response = await globalThis.fetch(`${apiBase}${path}`, {
      ...init,
      headers,
    })

    // 204 No Content
    if (response.status === 204) return null

    if (!response.ok) {
      let errPayload = null
      try {
        errPayload = await response.json()
      } catch {
        /* ignore parse failure */
      }

      const code = errPayload?.error?.code ?? 'http_error'
      const message =
        errPayload?.error?.message ??
        errPayload?.detail ??
        `Request failed: ${response.status} ${response.statusText}`

      const err = new Error(message)
      err.code = code
      err.status = response.status
      throw err
    }

    return response.json()
  }

  /**
   * Perform an authenticated fetch that returns raw binary (Arrow IPC).
   *
   * Same auth logic as apiFetch but returns an ArrayBuffer instead of JSON.
   *
   * @param {string} path
   * @param {RequestInit} [init]
   * @returns {Promise<ArrayBuffer>}
   */
  async function apiFetchBinary(path, init = {}) {
    const token = await resolveToken()

    const headers = new Headers(init.headers ?? {})
    headers.set('Authorization', `Bearer ${token}`)

    if (init.body !== undefined && !headers.has('Content-Type')) {
      headers.set('Content-Type', 'application/json')
    }

    headers.set('Accept', 'application/vnd.apache.arrow.stream')

    const response = await globalThis.fetch(`${apiBase}${path}`, {
      ...init,
      headers,
    })

    if (!response.ok) {
      let errPayload = null
      try {
        errPayload = await response.json()
      } catch {
        /* ignore */
      }

      const code = errPayload?.error?.code ?? 'http_error'
      const message =
        errPayload?.error?.message ??
        `Request failed: ${response.status} ${response.statusText}`

      const err = new Error(message)
      err.code = code
      err.status = response.status
      throw err
    }

    return response.arrayBuffer()
  }

  // -------------------------------------------------------------------------
  // auth
  // -------------------------------------------------------------------------

  const auth = {
    /**
     * Fetch the currently authenticated user.
     * @returns {Promise<{ user: object }>}
     */
    me() {
      return apiFetch('/auth/me', { method: 'GET' })
    },
  }

  // -------------------------------------------------------------------------
  // query
  // -------------------------------------------------------------------------

  /**
   * A simple heuristic: if the argument contains no whitespace and does not
   * look like a SQL keyword-led statement, treat it as a registered query_id
   * rather than an inline SQL string.
   *
   * Concretely: if the arg is a single "word" (no spaces, no SELECT/WITH/
   * INSERT at the start) we send { query_id }; otherwise we send { sql }.
   *
   * @param {string} arg
   * @returns {boolean}
   */
  function looksLikeQueryId(arg) {
    const trimmed = arg.trim()
    // Has whitespace → definitely SQL
    if (/\s/.test(trimmed)) return false
    // Starts with a SQL keyword → SQL
    if (/^(select|with|insert|update|delete|create|drop|alter|explain)/i.test(trimmed)) return false
    // Everything else (uuid, slug, "my_query_name") → treat as query_id
    return true
  }

  /**
   * Run a query and return an Apache Arrow Table.
   *
   * @param {string} sqlOrId     — An inline SQL string OR a registered query id.
   * @param {object} [options]
   * @param {object} [options.params]   — Optional query parameters.
   * @returns {Promise<import('apache-arrow').Table>}
   */
  async function query(sqlOrId, { params } = {}) {
    const body = looksLikeQueryId(sqlOrId)
      ? { query_id: sqlOrId, ...(params ? { params } : {}) }
      : { sql: sqlOrId, ...(params ? { params } : {}) }

    const buffer = await apiFetchBinary('/query', {
      method: 'POST',
      body: JSON.stringify(body),
    })

    return tableFromIPC(new Uint8Array(buffer))
  }

  // -------------------------------------------------------------------------
  // resources — generic CRUD factory
  // -------------------------------------------------------------------------

  /**
   * Build a resource client for one of the four domain resources.
   *
   * @param {string} resourceName  — e.g. "datastores", "boards", "widgets", "queries"
   * @returns {{ list, get, create, update, remove }}
   */
  function makeResourceClient(resourceName) {
    const base = `/${resourceName}`

    return {
      /**
       * List all resources for the authenticated user's org.
       * @returns {Promise<object[]>}
       */
      list() {
        return apiFetch(base, { method: 'GET' })
      },

      /**
       * Get a single resource by id.
       * @param {string} id
       * @returns {Promise<object>}
       */
      get(id) {
        return apiFetch(`${base}/${id}`, { method: 'GET' })
      },

      /**
       * Create a new resource.
       * @param {{ name: string, config?: object }} fields
       * @returns {Promise<object>}
       */
      create(fields) {
        return apiFetch(base, {
          method: 'POST',
          body: JSON.stringify(fields),
        })
      },

      /**
       * Update an existing resource.
       * @param {string} id
       * @param {{ name?: string, config?: object }} fields
       * @returns {Promise<object>}
       */
      update(id, fields) {
        return apiFetch(`${base}/${id}`, {
          method: 'PUT',
          body: JSON.stringify(fields),
        })
      },

      /**
       * Delete a resource.
       * @param {string} id
       * @returns {Promise<null>}
       */
      remove(id) {
        return apiFetch(`${base}/${id}`, { method: 'DELETE' })
      },
    }
  }

  const resources = {
    datastores: makeResourceClient('datastores'),
    boards:     makeResourceClient('boards'),
    widgets:    makeResourceClient('widgets'),
    queries:    makeResourceClient('queries'),
  }

  // -------------------------------------------------------------------------
  // embed
  // -------------------------------------------------------------------------

  const embed = {
    /**
     * Mount a <nubi-dashboard> custom element inside `el`.
     *
     * The host page must have loaded the nubi-dashboard bundle so that the
     * `<nubi-dashboard>` custom element is registered. This method constructs
     * the element by tag name, sets its attributes, and appends it to el.
     *
     * @param {HTMLElement} el          — Container element to append into.
     * @param {object} options
     * @param {string} options.query    — SQL string or registered query id.
     * @param {string} [options.token] — Static JWT. If omitted the nubi-dashboard
     *                                   element must have a get-token attribute
     *                                   set separately, or the SDK's getToken is
     *                                   used via a window bridge (see below).
     * @param {string} [options.backend] — Override the backend URL. Defaults to
     *                                     the baseUrl used to create this client.
     * @returns {{ unmount: () => void }}
     */
    mount(el, { query: queryArg, token, backend } = {}) {
      const dashboard = document.createElement('nubi-dashboard')

      if (queryArg !== undefined) {
        dashboard.setAttribute('query', queryArg)
      }

      // Token resolution: prefer the explicitly passed token; fall back to a
      // bridge function on window so the web component can call getToken().
      if (token) {
        dashboard.setAttribute('token', token)
      } else {
        // Register a window-level bridge so <nubi-dashboard get-token="...">
        // can call the client's getToken without the host having to wire it up.
        const bridgeName = `__nubiGetToken_${Math.random().toString(36).slice(2)}`
        window[bridgeName] = resolveToken
        dashboard.setAttribute('get-token', bridgeName)

        // Store bridgeName on the element for cleanup in unmount()
        dashboard._nubiGetTokenBridge = bridgeName
      }

      dashboard.setAttribute(
        'backend',
        (backend ?? origin).replace(/\/+$/, ''),
      )

      el.appendChild(dashboard)

      return {
        unmount() {
          // Clean up the window bridge if we created one
          if (dashboard._nubiGetTokenBridge) {
            delete window[dashboard._nubiGetTokenBridge]
          }
          if (dashboard.parentNode) {
            dashboard.parentNode.removeChild(dashboard)
          }
        },
      }
    },
  }

  // -------------------------------------------------------------------------
  // Assemble and return the client
  // -------------------------------------------------------------------------

  return {
    auth,
    query,
    resources,
    embed,
  }
}

// ---------------------------------------------------------------------------
// Re-export apache-arrow for callers who want to work with Table objects
// without adding apache-arrow as a separate dep.
// ---------------------------------------------------------------------------
export { tableFromIPC } from 'apache-arrow'
