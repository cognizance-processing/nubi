/**
 * API client for Nubi backend.
 *
 * Base URL:  import.meta.env.VITE_BACKEND_URL + "/api/v1"
 *
 * Access token is held in memory only (never localStorage/sessionStorage).
 * Refresh token is an HttpOnly cookie managed by the browser automatically.
 *
 * On any 401 the client will attempt one silent refresh via POST /auth/refresh,
 * update the in-memory token, and replay the original request.
 * If the refresh also fails the token is cleared and the error is re-thrown
 * so the caller (AuthContext) can redirect to /login.
 */

const BASE = (import.meta.env.VITE_BACKEND_URL ?? 'http://localhost:8000') + '/api/v1'

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
// JSDoc type stub (no TypeScript; gives IDE hints to C1)
// ---------------------------------------------------------------------------

/**
 * @typedef {{ id: string, email: string, name: string | null, avatar_url: string | null, email_verified: boolean, created_at: string }} User
 */
