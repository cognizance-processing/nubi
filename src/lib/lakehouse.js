/**
 * lakehouse.js — API client for the built-in MANAGED LAKEHOUSE.
 *
 * The managed lakehouse is just a NORMAL connector: an isolated, secure
 * object-storage prefix that Nubi provisions and manages for the user. It is
 * returned in the normal `GET /connectors` list (each managed row carries
 * `config.managed_lake === true` plus `usage_bytes` / `usage_gb`), rendered as
 * an ordinary — but visually distinct — connector card, and deleted through the
 * normal `DELETE /connectors/{id}` flow (which deprovisions the storage).
 *
 * The only bespoke endpoint is provisioning:
 *
 *   POST /lakehouse/provision   (optional { name }) → the NEW managed connector
 *
 * Multiple managed lakehouses are allowed. The returned connector is shaped like
 * any other connector row, so it can be dropped straight into the list.
 */

import { post } from './api.js'

// Re-export the shared byte formatter so callers can import everything
// lakehouse-related from one module without reaching into usage.js.
export { formatBytes } from './usage.js'

/**
 * Provision a new managed lakehouse for the active project.
 *
 * @param {{ name?: string }} [opts]
 * @returns {Promise<object>} the newly created managed connector row.
 *   Throws on failure so the caller can surface the error inline.
 */
export async function provisionLakehouse({ name } = {}) {
  const body = name ? { name } : {}
  return post('/lakehouse/provision', body)
}
