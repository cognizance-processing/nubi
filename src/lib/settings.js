/**
 * settings.js — API helpers for the Settings page.
 *
 * Wraps the underlying api.js request helpers with named, typed functions
 * for every settings-related endpoint:
 *   - Profile (PATCH /auth/me)
 *   - Org     (PATCH /orgs/{id}, DELETE /orgs/{id}, GET /orgs/{id}/deletion-impact)
 *   - Project (PATCH /projects/{id}, DELETE /projects/{id}, GET /projects/{id}/deletion-impact)
 *
 * PATCH and DELETE-with-body are not in the generic api.js helpers, so we
 * implement them directly here using fetch + the same auth conventions.
 */

import { get, getAccessToken } from './api.js' // noqa: PLC0415

// ---------------------------------------------------------------------------
// Base URL (mirrors api.js)
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
  // Include the active org id so the backend can scope the request.
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
 * DELETE with a JSON body (for confirm_name payloads).
 * @param {string} path
 * @param {object} body
 * @returns {Promise<null>}
 */
async function _delWithBody(path, body) {
  const response = await fetch(`${BASE}${path}`, {
    method: 'DELETE',
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
  return null
}

// ---------------------------------------------------------------------------
// Profile
// ---------------------------------------------------------------------------

/**
 * Update the current user's profile.
 *
 * PATCH /auth/me
 *
 * @param {{ name?: string, avatar_url?: string }} body
 * @returns {Promise<{ user: import('./api.js').User }>}
 */
export function updateMe(body) {
  return _patch('/auth/me', body)
}

// ---------------------------------------------------------------------------
// Org
// ---------------------------------------------------------------------------

/**
 * Rename or update an org's avatar.
 *
 * PATCH /orgs/{id}
 *
 * @param {string} id
 * @param {{ name?: string, avatar_url?: string }} body
 * @returns {Promise<object>}
 */
export function updateOrg(id, body) {
  return _patch(`/orgs/${id}`, body)
}

/**
 * Fetch the deletion impact for an org.
 * When the org has projects, can_delete is false and blockers contains a
 * 'projects' entry with a reason message.
 *
 * GET /orgs/{id}/deletion-impact
 *
 * @param {string} id
 * @returns {Promise<{
 *   can_delete: boolean,
 *   blockers: Array<{ type: string, count: number, reason: string }>,
 *   deletes: Array<{ type: string, count: number }>,
 *   name: string
 * }>}
 */
export function getOrgDeletionImpact(id) {
  return get(`/orgs/${id}/deletion-impact`)
}

/**
 * Delete an org.
 * The server returns 409 when projects exist.
 * Requires { confirm_name } matching the org's current name.
 *
 * DELETE /orgs/{id}
 *
 * @param {string} id
 * @param {string} confirmName — must match the org name exactly
 * @returns {Promise<null>}
 */
export function deleteOrg(id, confirmName) {
  return _delWithBody(`/orgs/${id}`, { confirm_name: confirmName })
}

// ---------------------------------------------------------------------------
// Project
// ---------------------------------------------------------------------------

/**
 * Rename a project.
 *
 * PATCH /projects/{id}
 *
 * @param {string} id
 * @param {{ name?: string }} body
 * @returns {Promise<object>}
 */
export function updateProjectSettings(id, body) {
  return _patch(`/projects/${id}`, body)
}

/**
 * Fetch the deletion impact for a project.
 *
 * GET /projects/{id}/deletion-impact
 *
 * @param {string} id
 * @returns {Promise<{
 *   can_delete: boolean,
 *   blockers: Array<{ type: string, count: number, reason: string }>,
 *   deletes: Array<{ type: string, count: number }>,
 *   name: string
 * }>}
 */
export function getProjectDeletionImpact(id) {
  return get(`/projects/${id}/deletion-impact`)
}

/**
 * Delete a project.
 * Requires { confirm_name } matching the project's current name.
 *
 * DELETE /projects/{id}
 *
 * @param {string} id
 * @param {string} confirmName — must match the project name exactly
 * @returns {Promise<null>}
 */
export function deleteProjectSettings(id, confirmName) {
  return _delWithBody(`/projects/${id}`, { confirm_name: confirmName })
}
