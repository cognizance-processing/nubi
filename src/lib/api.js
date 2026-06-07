/**
 * API client for Nubi backend.
 *
 * Base URL:
 *   - In dev the Vite proxy rewrites /api/* to the backend so we use a
 *     relative base URL.  This keeps auth cookies same-origin so SameSite=Lax
 *     cookies are sent on every request (including POST /auth/refresh).
 *   - In production (where the frontend is served from the same origin as the
 *     backend, or VITE_BACKEND_URL is set to a different origin) we use the
 *     full absolute URL.
 *
 * Active org:
 *   OrgContext calls ``setActiveOrgId(id)`` (exported below) each time the
 *   active org changes.  The api client reads this value and attaches it as an
 *   ``X-Org-Id`` header on every authenticated request.  The backend validates
 *   membership before honouring the header.  This avoids any circular-import
 *   dependency between api.js and OrgContext.
 *
 * Access token is held in memory only (never localStorage/sessionStorage).
 * Refresh token is an HttpOnly cookie managed by the browser automatically.
 *
 * On any 401 the client will attempt one silent refresh via POST /auth/refresh,
 * update the in-memory token, and replay the original request.
 * If the refresh also fails the token is cleared and the error is re-thrown
 * so the caller (AuthContext) can redirect to /login.
 */

// In development Vite proxies /api/* to the backend via vite.config.js, so
// we use a relative base URL.  This ensures auth cookies are same-origin
// (localhost:5173) and SameSite=Lax cookies are sent on every fetch call.
//
// In production builds we use the absolute VITE_BACKEND_URL when it is set
// to a *different* origin (e.g. api.example.com), otherwise assume the
// frontend is served from the same origin as the backend.
const _backendUrl = import.meta.env.VITE_BACKEND_URL ?? ''
const BASE = (import.meta.env.DEV || !_backendUrl) ? '/api/v1' : _backendUrl + '/api/v1'

// ---------------------------------------------------------------------------
// In-memory token store
// ---------------------------------------------------------------------------

/** @type {string | null} */
let _accessToken = null

/** @returns {string | null} */
export function getAccessToken() {
  return _accessToken
}

/** @param {string | null} token */
export function setAccessToken(token) {
  _accessToken = token
}

// ---------------------------------------------------------------------------
// Active org store (registered by OrgContext; read by the fetch wrapper)
// ---------------------------------------------------------------------------

/**
 * The active org id to include as X-Org-Id on authenticated requests.
 * Set to null (or the sentinel 'personal') to omit the header.
 * @type {string | null}
 */
let _activeOrgId = null

/**
 * Called by OrgContext whenever the active org changes.
 * @param {string | null} orgId
 */
export function setActiveOrgId(orgId) {
  _activeOrgId = orgId && orgId !== 'personal' ? orgId : null
}

// ---------------------------------------------------------------------------
// Active project store (registered by ProjectContext; read by the fetch wrapper)
// ---------------------------------------------------------------------------

/**
 * The active project id to include as X-Project-Id on authenticated requests.
 * Set to null to omit the header (the backend then defaults to the org's
 * default project).
 * @type {string | null}
 */
let _activeProjectId = null

/**
 * Called by ProjectContext whenever the active project changes.
 * @param {string | null} projectId
 */
export function setActiveProjectId(projectId) {
  _activeProjectId = projectId || null
}

// ---------------------------------------------------------------------------
// Core fetch wrapper
// ---------------------------------------------------------------------------

/**
 * Internal fetch helper.
 * @param {string} path   — path relative to BASE, must start with "/"
 * @param {RequestInit} options
 * @param {boolean} [_isRetry] — true when replaying after a token refresh
 * @returns {Promise<any>}
 */
async function request(path, options = {}, _isRetry = false) {
  const headers = new Headers(options.headers ?? {})

  if (!headers.has('Content-Type') && options.body !== undefined) {
    headers.set('Content-Type', 'application/json')
  }

  if (_accessToken) {
    headers.set('Authorization', `Bearer ${_accessToken}`)
  }

  // Attach the active org so the backend scopes resources to that org.
  // The backend validates membership before honouring the header.
  if (_activeOrgId) {
    headers.set('X-Org-Id', _activeOrgId)
  }

  // Attach the active project so the backend scopes resources to that project.
  // When absent the backend defaults to the org's default project.
  if (_activeProjectId) {
    headers.set('X-Project-Id', _activeProjectId)
  }

  const response = await fetch(`${BASE}${path}`, {
    ...options,
    headers,
    credentials: 'include', // send & receive the HttpOnly refresh cookie
  })

  // -- Silent refresh on 401 -----------------------------------------------
  if (response.status === 401 && !_isRetry) {
    let refreshed = false
    try {
      const data = await _doRefresh()
      setAccessToken(data.access_token)
      refreshed = true
    } catch {
      setAccessToken(null)
      // surface a consistent error
      const err = new Error('Session expired. Please log in again.')
      err.status = 401
      throw err
    }

    if (refreshed) {
      // replay the original request with the new token
      return request(path, options, true)
    }
  }

  // -- Parse and surface errors --------------------------------------------
  if (!response.ok) {
    let errPayload
    try {
      errPayload = await response.json()
    } catch {
      errPayload = null
    }
    const message =
      errPayload?.error?.message ??
      errPayload?.detail ??
      `Request failed: ${response.status} ${response.statusText}`
    const err = new Error(message)
    err.status = response.status
    err.payload = errPayload
    throw err
  }

  // 204 No Content
  if (response.status === 204) return null

  return response.json()
}

// ---------------------------------------------------------------------------
// HTTP verb helpers
// ---------------------------------------------------------------------------

/** GET /path */
export function get(path) {
  return request(path, { method: 'GET' })
}

/** POST /path with JSON body */
export function post(path, body) {
  return request(path, { method: 'POST', body: body !== undefined ? JSON.stringify(body) : undefined })
}

/** PUT /path with JSON body */
export function put(path, body) {
  return request(path, { method: 'PUT', body: body !== undefined ? JSON.stringify(body) : undefined })
}

/** DELETE /path (named 'del' to avoid reserved-word clash) */
export function del(path) {
  return request(path, { method: 'DELETE' })
}

/**
 * POST a JSON body and consume a Server-Sent Events (text/event-stream)
 * response, invoking `onEvent` with each parsed JSON event as it arrives.
 *
 * Reuses the same Bearer token + X-Org-Id headers and the silent 401-refresh
 * retry as `request()`. Resolves when the stream closes; rejects on transport
 * or HTTP errors. Pass an AbortSignal to cancel.
 *
 * @param {string} path
 * @param {any} body
 * @param {{ onEvent?: (ev: any) => void, signal?: AbortSignal }} [opts]
 * @param {boolean} [_isRetry]
 * @returns {Promise<void>}
 */
export async function postStream(path, body, { onEvent, signal } = {}, _isRetry = false) {
  const headers = new Headers({
    'Content-Type': 'application/json',
    Accept: 'text/event-stream',
  })
  if (_accessToken) headers.set('Authorization', `Bearer ${_accessToken}`)
  if (_activeOrgId) headers.set('X-Org-Id', _activeOrgId)
  if (_activeProjectId) headers.set('X-Project-Id', _activeProjectId)

  const response = await fetch(`${BASE}${path}`, {
    method: 'POST',
    headers,
    body: body !== undefined ? JSON.stringify(body) : undefined,
    credentials: 'include',
    signal,
  })

  // Silent refresh on 401, then replay once.
  if (response.status === 401 && !_isRetry) {
    try {
      const data = await _doRefresh()
      setAccessToken(data.access_token)
    } catch {
      setAccessToken(null)
      const err = new Error('Session expired. Please log in again.')
      err.status = 401
      throw err
    }
    return postStream(path, body, { onEvent, signal }, true)
  }

  if (!response.ok || !response.body) {
    let payload
    try { payload = await response.json() } catch { payload = null }
    const err = new Error(
      payload?.error?.message ?? payload?.detail ??
      `Request failed: ${response.status} ${response.statusText}`,
    )
    err.status = response.status
    throw err
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  // eslint-disable-next-line no-constant-condition
  while (true) {
    const { value, done } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })

    // SSE events are separated by a blank line.
    let sep
    while ((sep = buffer.indexOf('\n\n')) !== -1) {
      const rawEvent = buffer.slice(0, sep)
      buffer = buffer.slice(sep + 2)
      // Concatenate all `data:` lines within this event.
      const dataLines = rawEvent
        .split('\n')
        .filter(l => l.startsWith('data:'))
        .map(l => l.slice(5).trimStart())
      if (dataLines.length === 0) continue
      const json = dataLines.join('\n')
      if (!json) continue
      try { onEvent?.(JSON.parse(json)) } catch { /* ignore malformed event */ }
    }
  }
}

// ---------------------------------------------------------------------------
// Internal refresh (called only by the 401 interceptor — not exported)
// ---------------------------------------------------------------------------

/**
 * POST /auth/refresh — uses the HttpOnly cookie; returns { access_token }.
 * Throws on failure. Called internally; consumers use the auth helpers below.
 */
function _doRefresh() {
  // bypass the interceptor loop: pass _isRetry = true
  return request('/auth/refresh', { method: 'POST' }, true)
}

// ---------------------------------------------------------------------------
// Auth API helpers (consumed by C1 — AuthContext)
// ---------------------------------------------------------------------------

/**
 * Register a new account.
 * @param {{ email: string, password: string, name: string }} body
 * @returns {Promise<{ user: User, access_token: string }>}
 *   Side-effect: backend sets the HttpOnly refresh cookie.
 */
export function register(body) {
  return post('/auth/register', body)
}

/**
 * Log in with email + password.
 * @param {{ email: string, password: string }} body
 * @returns {Promise<{ user: User, access_token: string }>}
 *   Side-effect: backend sets the HttpOnly refresh cookie.
 */
export function login(body) {
  return post('/auth/login', body)
}

/**
 * Silently exchange the HttpOnly refresh cookie for a new access token.
 * Also rotates the refresh cookie.
 * @returns {Promise<{ access_token: string }>}
 */
export function refresh() {
  return post('/auth/refresh')
}

/**
 * Log out — revokes the full session family and clears the refresh cookie.
 * @returns {Promise<null>}
 */
export function logout() {
  return post('/auth/logout')
}

/**
 * Fetch the currently authenticated user.
 * Requires a valid in-memory access token (attaches Authorization header).
 * @returns {Promise<{ user: User }>}
 */
export function me() {
  return get('/auth/me')
}

/**
 * Build the URL to start Google OAuth (redirect-based, PKCE on the backend).
 * Navigate to this URL; no fetch needed.
 * @returns {string}
 */
export function googleStartUrl() {
  return `${BASE}/auth/google/start`
}

// ---------------------------------------------------------------------------
// Query registry (M13-B)
// ---------------------------------------------------------------------------

/**
 * List all registered queries and their declared params.
 *
 * GET /api/v1/query/registry
 *
 * Returns an array of RegisteredQuery objects:
 *   { id, name, sql, params: [{ name, type, default?, required?, options_query_id? }] }
 *
 * Returns [] on any failure (backend unavailable, unauthenticated, etc.)
 * so callers can degrade gracefully.
 *
 * @returns {Promise<Array<{
 *   id: string,
 *   name: string,
 *   sql: string,
 *   params: Array<{ name: string, type: string, default?: any, required?: boolean, options_query_id?: string }>
 * }>>}
 */
export async function listRegisteredQueries() {
  try {
    const data = await get('/query/registry')
    if (Array.isArray(data)) return data
    if (Array.isArray(data?.queries)) return data.queries
    return []
  } catch (cause) {
    console.warn('[api] listRegisteredQueries failed; returning []:', cause.message)
    return []
  }
}

// ---------------------------------------------------------------------------
// Query registration
// ---------------------------------------------------------------------------

/**
 * Register (or update) a query in the runtime QueryRegistry.
 *
 * POST /api/v1/query/registry
 *
 * @param {{ name: string, sql: string, params?: Array, id?: string }} body
 * @returns {Promise<{ id: string, name: string, sql: string, params: Array }>}
 */
export function registerQuery(body) {
  return post('/query/registry', body)
}

// ---------------------------------------------------------------------------
// Datastores (connectors) — used by the Queries connector picker
// ---------------------------------------------------------------------------

/**
 * List the org's datastores (connectors).
 *
 * GET /api/v1/datastores
 *
 * Returns an array of datastore rows: { id, name, config, ... } with no secret
 * material.  Returns [] on any failure (backend unavailable, unauthenticated,
 * etc.) so the connector picker degrades gracefully.
 *
 * @returns {Promise<Array<{ id: string, name: string, config?: object }>>}
 */
export async function listDatastores() {
  try {
    const data = await get('/datastores')
    if (Array.isArray(data)) return data
    if (Array.isArray(data?.datastores)) return data.datastores
    if (Array.isArray(data?.items)) return data.items
    return []
  } catch (cause) {
    console.warn('[api] listDatastores failed; returning []:', cause.message)
    return []
  }
}

// ---------------------------------------------------------------------------
// Projects (scoped within the active org via X-Org-Id)
// ---------------------------------------------------------------------------

/**
 * List the active org's projects.
 *
 * GET /api/v1/projects
 *
 * Returns an array of project rows: { id, name, slug, org_id, ... }.
 * Returns [] on any failure (backend unavailable, unauthenticated, etc.) so
 * ProjectContext can degrade gracefully.
 *
 * @returns {Promise<Array<{ id: string, name: string, slug?: string, org_id?: string }>>}
 */
export async function listProjects() {
  try {
    const data = await get('/projects')
    if (Array.isArray(data)) return data
    if (Array.isArray(data?.projects)) return data.projects
    if (Array.isArray(data?.items)) return data.items
    return []
  } catch (cause) {
    console.warn('[api] listProjects failed; returning []:', cause.message)
    return []
  }
}

/**
 * Create a project in the active org.
 *
 * POST /api/v1/projects { name }
 *
 * @param {string} name
 * @returns {Promise<{ id: string, name: string, slug?: string, org_id?: string }>}
 */
export function createProject(name) {
  return post('/projects', { name })
}

/**
 * Update a project.
 *
 * PUT /api/v1/projects/{id}
 *
 * @param {string} id
 * @param {{ name?: string }} body
 * @returns {Promise<{ id: string, name: string, slug?: string, org_id?: string }>}
 */
export function updateProject(id, body) {
  return put(`/projects/${id}`, body)
}

/**
 * Delete a project.
 *
 * DELETE /api/v1/projects/{id}
 *
 * @param {string} id
 * @returns {Promise<null>}
 */
export function deleteProject(id) {
  return del(`/projects/${id}`)
}

// ---------------------------------------------------------------------------
// Query editor tooling (backend: app/routes/query_tools.py)
// ---------------------------------------------------------------------------

/**
 * Validate SQL with sqlglot in a given dialect.
 *
 * POST /api/v1/query/validate
 *
 * @param {string} sql
 * @param {string} [dialect] one of bigquery|duckdb|postgres|mysql
 * @returns {Promise<{ ok: boolean, errors: Array<{ message: string, line: number, col: number }> }>}
 *   Returns ``{ ok: true, errors: [] }`` on any transport failure so the editor
 *   degrades gracefully (no spurious squiggles when the backend is down).
 */
export async function validateSql(sql, dialect) {
  try {
    return await post('/query/validate', { sql, dialect })
  } catch (cause) {
    console.warn('[api] validateSql failed; treating as ok:', cause.message)
    return { ok: true, errors: [] }
  }
}

/**
 * Best-effort LLM autocomplete for the text before the cursor.
 *
 * POST /api/v1/query/complete
 *
 * @param {{ sql: string, cursor?: number, schema?: string }} body
 * @returns {Promise<{ suggestion: string }>}
 *   Returns ``{ suggestion: '' }`` on any failure so the caller falls back to
 *   local completion.
 */
export async function completeSql(body) {
  try {
    const data = await post('/query/complete', body)
    return { suggestion: data?.suggestion ?? '' }
  } catch (cause) {
    console.warn('[api] completeSql failed; no suggestion:', cause.message)
    return { suggestion: '' }
  }
}

/**
 * Fetch the schema catalog used to seed schema-aware autocomplete.
 *
 * GET /api/v1/query/schema
 *
 * @returns {Promise<{ tables: Record<string, string[]> }>}
 *   Returns ``{ tables: {} }`` on any failure.
 */
export async function fetchSchema() {
  try {
    const data = await get('/query/schema')
    return { tables: data?.tables ?? {} }
  } catch (cause) {
    console.warn('[api] fetchSchema failed; returning empty schema:', cause.message)
    return { tables: {} }
  }
}

// ---------------------------------------------------------------------------
// JSDoc type stub (no TypeScript; gives IDE hints to C1)
// ---------------------------------------------------------------------------

/**
 * @typedef {{ id: string, email: string, name: string | null, avatar_url: string | null, email_verified: boolean, created_at: string }} User
 */
