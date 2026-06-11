/**
 * integrationsApi.js — thin transport layer for per-org notify integrations.
 *
 * One *integration* is a connected channel (Slack / WhatsApp / Google Chat /
 * Teams / Email) that powers BOTH inbound chat and outbound alerts. The UI
 * lives in ``src/pages/app/settings/IntegrationsSettings.jsx`` and the watches
 * channel picker (``src/pages/app/WatchesPage.jsx``).
 *
 * Contract (backend/app/routes/integrations.py — paths under /api/v1):
 *   GET    /integrations              listIntegrations
 *   POST   /integrations              createIntegration
 *   GET    /integrations/{id}         getIntegration
 *   PUT    /integrations/{id}         updateIntegration
 *   DELETE /integrations/{id}         deleteIntegration
 *   POST   /integrations/{id}/test    testIntegration
 *
 * Secret handling: list/get responses scrub secrets — they return only the
 * non-secret ``config`` plus ``configured: bool`` (whether a secret is stored).
 * Secret fields are WRITE-ONLY: send them on create/update, never read them
 * back. Omitting a secret on update leaves the stored secret untouched.
 *
 * Read helpers degrade gracefully (return a safe empty value on transport/auth
 * errors) so the UI can still render; write/test helpers re-throw so forms can
 * surface the error.
 *
 * Integration shape (scrubbed):
 *   {
 *     id, kind, name, enabled, configured,
 *     config: { ...non-secret fields per kind },
 *     created_at, updated_at,
 *   }
 */

import { get, post, put, del } from './api.js'

const BASE = '/integrations'

/** The kinds the UI knows how to connect. */
export const INTEGRATION_KINDS = ['slack', 'whatsapp', 'google_chat', 'teams', 'email']

/**
 * List the active org's connected integrations.
 * @returns {Promise<Array<object>>}  [] on any failure.
 */
export async function listIntegrations() {
  try {
    const data = await get(BASE)
    if (Array.isArray(data)) return data
    if (Array.isArray(data?.integrations)) return data.integrations
    if (Array.isArray(data?.items)) return data.items
    return []
  } catch (err) {
    console.warn('[integrations] listIntegrations failed; returning []:', err.message)
    return []
  }
}

/**
 * Fetch a single integration (scrubbed) by id.
 * @param {string} id
 * @returns {Promise<object|null>}  null on any failure.
 */
export async function getIntegration(id) {
  try {
    return await get(`${BASE}/${encodeURIComponent(id)}`)
  } catch (err) {
    console.warn('[integrations] getIntegration failed:', err.message)
    return null
  }
}

/**
 * Create an integration. Re-throws on failure so the form can show the error.
 * @param {{ kind: string, name: string, config?: object, enabled?: boolean }} body
 *   ``config`` carries BOTH non-secret and secret fields on write; the backend
 *   splits them and stores secrets encrypted.
 * @returns {Promise<object>}  the created (scrubbed) integration.
 */
export function createIntegration(body) {
  return post(BASE, body)
}

/**
 * Update an integration. Re-throws on failure.
 * Omit secret fields to leave the stored secret untouched.
 * @param {string} id
 * @param {{ name?: string, config?: object, enabled?: boolean }} body
 * @returns {Promise<object>}  the updated (scrubbed) integration.
 */
export function updateIntegration(id, body) {
  return put(`${BASE}/${encodeURIComponent(id)}`, body)
}

/**
 * Delete an integration.
 * @param {string} id
 * @returns {Promise<boolean>}  true on success, false on failure.
 */
export async function deleteIntegration(id) {
  try {
    await del(`${BASE}/${encodeURIComponent(id)}`)
    return true
  } catch (err) {
    console.warn('[integrations] deleteIntegration failed:', err.message)
    return false
  }
}

/**
 * Send a test message through an integration. Re-throws on failure so the
 * caller can surface the result.
 * @param {string} id
 * @returns {Promise<{ ok: boolean, detail?: string, error?: string }>}
 */
export function testIntegration(id) {
  return post(`${BASE}/${encodeURIComponent(id)}/test`, {})
}
