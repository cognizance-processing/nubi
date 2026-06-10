/**
 * security.js — API helpers for the Security settings page.
 *
 * Manages JWT issuer / JWKS configurations used to verify host-signed embed
 * JWTs (RS256 / ES256).  These issuers are stored in the backend and consulted
 * by backend/app/routes/embed.py when validating signed dashboard-embed tokens.
 *
 * Endpoints (JwksIssuersBackendAgent):
 *   GET    /api/v1/security/jwt-issuers
 *   POST   /api/v1/security/jwt-issuers
 *   PATCH  /api/v1/security/jwt-issuers/{id}
 *   DELETE /api/v1/security/jwt-issuers/{id}
 */

import { get, getAccessToken } from './api.js' // noqa: PLC0415

// ---------------------------------------------------------------------------
// Base URL (mirrors api.js / settings.js)
// ---------------------------------------------------------------------------

const _backendUrl = import.meta.env.VITE_BACKEND_URL ?? ''
const BASE = (import.meta.env.DEV || !_backendUrl) ? '/api/v1' : _backendUrl + '/api/v1'

// ---------------------------------------------------------------------------
// Shared request helpers
// ---------------------------------------------------------------------------

/**
 * Build the common auth + org headers.
 * @returns {Headers}
 */
function _headers() {
  const token = getAccessToken()
  const h = new Headers({ 'Content-Type': 'application/json' })
  if (token) h.set('Authorization', `Bearer ${token}`)
  try {
    const orgId = localStorage.getItem('nubi-active-org-id')
    if (orgId && orgId !== 'personal') h.set('X-Org-Id', orgId)
  } catch { /* ignore (private mode) */ }
  return h
}

/**
 * Generic PATCH helper with JSON body.
 * @param {string} path
 * @param {object} body
 * @returns {Promise<any>}
 */
async function _patch(path, body) {
  const response = await fetch(`${BASE}${path}`, {
    method: 'PATCH',
    headers: _headers(),
    body: JSON.stringify(body),
    credentials: 'include',
  })
  if (response.status === 204) return null
  if (!response.ok) {
    let payload = null
    try { payload = await response.json() } catch { /* empty */ }
    const message =
      payload?.error?.message ??
      payload?.detail ??
      `Request failed: ${response.status} ${response.statusText}`
    const err = new Error(message)
    err.status = response.status
    err.payload = payload
    throw err
  }
  return response.json()
}

/**
 * Generic POST helper with JSON body.
 * @param {string} path
 * @param {object} body
 * @returns {Promise<any>}
 */
async function _post(path, body) {
  const response = await fetch(`${BASE}${path}`, {
    method: 'POST',
    headers: _headers(),
    body: JSON.stringify(body),
    credentials: 'include',
  })
  if (response.status === 204) return null
  if (!response.ok) {
    let payload = null
    try { payload = await response.json() } catch { /* empty */ }
    const message =
      payload?.error?.message ??
      payload?.detail ??
      `Request failed: ${response.status} ${response.statusText}`
    const err = new Error(message)
    err.status = response.status
    err.payload = payload
    throw err
  }
  return response.json()
}

/**
 * DELETE by path.
 * @param {string} path
 * @returns {Promise<null>}
 */
async function _del(path) {
  const response = await fetch(`${BASE}${path}`, {
    method: 'DELETE',
    headers: _headers(),
    credentials: 'include',
  })
  if (response.status === 204) return null
  if (!response.ok) {
    let payload = null
    try { payload = await response.json() } catch { /* empty */ }
    const message =
      payload?.error?.message ??
      payload?.detail ??
      `Request failed: ${response.status} ${response.statusText}`
    const err = new Error(message)
    err.status = response.status
    err.payload = payload
    throw err
  }
  return null
}

// ---------------------------------------------------------------------------
// JWT Issuers
// ---------------------------------------------------------------------------

/**
 * @typedef {{
 *   id: string,
 *   name: string,
 *   issuer: string,
 *   jwks_url: string | null,
 *   jwk_pem: string | null,
 *   algorithms: string[],
 *   audience: string | null,
 *   enabled: boolean,
 *   created_at: string
 * }} JwtIssuer
 */

/**
 * List all JWT issuers configured for the active org.
 *
 * GET /security/jwt-issuers
 *
 * Returns [] on any failure so the page can degrade gracefully.
 *
 * @returns {Promise<JwtIssuer[]>}
 */
export async function listJwtIssuers() {
  try {
    const data = await get('/security/jwt-issuers')
    if (Array.isArray(data)) return data
    if (Array.isArray(data?.issuers)) return data.issuers
    if (Array.isArray(data?.items)) return data.items
    return []
  } catch (cause) {
    console.warn('[security] listJwtIssuers failed; returning []:', cause.message)
    return []
  }
}

/**
 * Create a new JWT issuer.
 *
 * POST /security/jwt-issuers
 *
 * @param {{
 *   name: string,
 *   issuer: string,
 *   jwks_url?: string,
 *   jwk_pem?: string,
 *   algorithms?: string[],
 *   audience?: string,
 *   enabled?: boolean
 * }} body
 * @returns {Promise<JwtIssuer>}
 */
export function createJwtIssuer(body) {
  return _post('/security/jwt-issuers', body)
}

/**
 * Update an existing JWT issuer.
 *
 * PATCH /security/jwt-issuers/{id}
 *
 * @param {string} id
 * @param {Partial<{
 *   name: string,
 *   issuer: string,
 *   jwks_url: string,
 *   jwk_pem: string,
 *   algorithms: string[],
 *   audience: string,
 *   enabled: boolean
 * }>} body
 * @returns {Promise<JwtIssuer>}
 */
export function updateJwtIssuer(id, body) {
  return _patch(`/security/jwt-issuers/${id}`, body)
}

/**
 * Delete a JWT issuer.
 *
 * DELETE /security/jwt-issuers/{id}
 *
 * @param {string} id
 * @returns {Promise<null>}
 */
export function deleteJwtIssuer(id) {
  return _del(`/security/jwt-issuers/${id}`)
}
