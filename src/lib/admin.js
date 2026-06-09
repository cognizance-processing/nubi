/**
 * lib/admin.js — thin API client for the superadmin portal (/admin).
 *
 * Wraps the 5 admin endpoints (backend: app/routes/admin.py). All endpoints
 * require is_superadmin and return 403 otherwise.
 *
 * Every helper is graceful on error: it logs a warning and returns null so
 * the admin pages can render an explicit error state instead of crashing.
 */

import { get } from './api.js'

/** Build a ?search=&limit=&offset= query string. */
function listQs({ search = '', limit = 50, offset = 0 } = {}) {
  const qs = new URLSearchParams()
  if (search) qs.set('search', search)
  qs.set('limit', String(limit))
  qs.set('offset', String(offset))
  return `?${qs.toString()}`
}

/**
 * GET /admin/overview
 * @returns {Promise<{
 *   counts: { users: number, orgs: number, projects: number, boards: number, queries: number, flows: number, datastores: number },
 *   signups_by_day: Array<{ day: string, count: number }>,
 *   logins_by_day: Array<{ day: string, count: number }>
 * } | null>}
 */
export async function getAdminOverview() {
  try {
    return await get('/admin/overview')
  } catch (cause) {
    console.warn('[admin] getAdminOverview failed:', cause.message)
    return null
  }
}

/**
 * GET /admin/users?search=&limit=&offset=
 * @param {{ search?: string, limit?: number, offset?: number }} [opts]
 * @returns {Promise<{
 *   users: Array<{ id: string, email: string, name: string|null, created_at: string,
 *     is_superadmin: boolean, last_login_at: string|null, last_ip: string|null,
 *     last_location: string|null, orgs: Array<{ id: string, name: string, role: string }> }>,
 *   total: number
 * } | null>}
 */
export async function getAdminUsers(opts) {
  try {
    return await get(`/admin/users${listQs(opts)}`)
  } catch (cause) {
    console.warn('[admin] getAdminUsers failed:', cause.message)
    return null
  }
}

/**
 * GET /admin/orgs?search=&limit=&offset=
 * @param {{ search?: string, limit?: number, offset?: number }} [opts]
 * @returns {Promise<{
 *   orgs: Array<{ id: string, name: string, slug: string|null, created_at: string,
 *     member_count: number, project_count: number }>,
 *   total: number
 * } | null>}
 */
export async function getAdminOrgs(opts) {
  try {
    return await get(`/admin/orgs${listQs(opts)}`)
  } catch (cause) {
    console.warn('[admin] getAdminOrgs failed:', cause.message)
    return null
  }
}

/**
 * GET /admin/orgs/{id}
 * @param {string} id
 * @returns {Promise<{
 *   org: { id: string, name: string, slug: string|null, created_at: string },
 *   members: Array<{ user_id: string, email: string, name: string|null, role: string }>,
 *   projects: Array<{ id: string, name: string, slug: string|null, created_at: string }>
 * } | null>}
 */
export async function getAdminOrg(id) {
  try {
    return await get(`/admin/orgs/${id}`)
  } catch (cause) {
    console.warn('[admin] getAdminOrg failed:', cause.message)
    return null
  }
}

/**
 * GET /admin/geo/summary
 * @returns {Promise<{
 *   countries: Array<{ country: string, count: number }>,
 *   total_located: number,
 *   total_events: number
 * } | null>}
 */
export async function getAdminGeoSummary() {
  try {
    return await get('/admin/geo/summary')
  } catch (cause) {
    console.warn('[admin] getAdminGeoSummary failed:', cause.message)
    return null
  }
}
