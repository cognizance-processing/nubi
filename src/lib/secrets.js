/**
 * secrets.js — API client for the Nubi secrets store.
 *
 * The backend /secrets endpoints are name-only: values are write-only and
 * are NEVER returned after creation. The UI stores only secret names in task
 * config (the 'secret' field), which the runtime resolves server-side at
 * execution time.
 *
 * Endpoints (backend/app/routes/secrets.py):
 *   GET    /secrets                  listSecrets  → [{ name, created_at }]
 *   POST   /secrets                  createSecret { name, value }
 *   DELETE /secrets/{name}           deleteSecret
 *
 * All functions degrade gracefully — they catch transport errors and return
 * safe empty values so callers can render without crashing.
 */

import { get, post, del } from './api.js'

const BASE = '/secrets'

// ---------------------------------------------------------------------------
// List secrets (names only — values are never returned)
// ---------------------------------------------------------------------------

/**
 * List secrets for the active org.
 *
 * @returns {Promise<Array<{ name: string, created_at: string }>>}
 */
export async function listSecrets() {
  try {
    const data = await get(BASE)
    if (Array.isArray(data)) return data
    if (Array.isArray(data?.secrets)) return data.secrets
    if (Array.isArray(data?.items)) return data.items
    return []
  } catch (err) {
    console.warn('[secrets] listSecrets failed:', err.message)
    return []
  }
}

// ---------------------------------------------------------------------------
// Create / upsert a secret
// ---------------------------------------------------------------------------

/**
 * Create (or overwrite) a secret.
 *
 * The value is write-only — after this call it cannot be retrieved via the API.
 *
 * @param {string} name   — unique name within the org (alphanumeric + _ + -)
 * @param {string} value  — plaintext secret value; encrypted at rest
 * @returns {Promise<{ name: string, created_at: string } | null>}
 */
export async function createSecret(name, value) {
  try {
    return await post(BASE, { name, value })
  } catch (err) {
    console.warn('[secrets] createSecret failed:', err.message)
    throw err
  }
}

// ---------------------------------------------------------------------------
// Delete a secret
// ---------------------------------------------------------------------------

/**
 * Delete a secret by name.
 *
 * @param {string} name
 * @returns {Promise<boolean>}
 */
export async function deleteSecret(name) {
  try {
    await del(`${BASE}/${encodeURIComponent(name)}`)
    return true
  } catch (err) {
    console.warn('[secrets] deleteSecret failed:', err.message)
    throw err
  }
}
