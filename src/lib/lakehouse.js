/**
 * lakehouse.js — API client for the built-in MANAGED LAKEHOUSE surface.
 *
 * Nubi can store a project's data in a Nubi-managed lakehouse: an isolated,
 * secure object-storage prefix that the user never has to provision or manage.
 * Storage is billed by usage. The alternative is "bring your own bucket" — the
 * existing object-storage connector flow on the Connectors page.
 *
 * Backend contract (paths under /api/v1; still being built in parallel):
 *   GET    /lakehouse              → status (see shape below)
 *   POST   /lakehouse/provision    (optional ?seed_demo=true) → provisions, returns status
 *   POST   /lakehouse/demo         → seeds demo data, returns status
 *   DELETE /lakehouse              → deprovisions (destructive)
 *
 * Status shape:
 *   {
 *     configured:   bool,    // central storage is set up (false ⇒ local/OSS dev)
 *     provisioned:  bool,    // a managed lake exists for this project
 *     datastore_id?: string, // the managed datastore/connector id (link target)
 *     prefix?:      string,  // the isolated storage prefix
 *     demo_seeded?: bool,    // demo data has been seeded
 *     usage_bytes?: number,  // bytes currently stored
 *   }
 *
 * GRACEFUL DEGRADATION: the backend may not be deployed yet. A 404 (or any
 * transport error) on the status read is treated as ``configured: false`` so
 * the page shows the explanatory "needs central storage" state rather than a
 * scary error. The mutating helpers surface real errors to the caller so the
 * page can show them inline.
 */

import { get, post, del } from './api.js'

// Re-export the shared byte formatter so callers can import everything
// lakehouse-related from one module without reaching into usage.js.
export { formatBytes } from './usage.js'

/** Safe default status used when the lakehouse surface is unavailable. */
const UNCONFIGURED = Object.freeze({
  configured: false,
  provisioned: false,
  datastore_id: null,
  prefix: null,
  demo_seeded: false,
  usage_bytes: 0,
})

/** Normalize an arbitrary payload into the documented status shape. */
function normStatus(data) {
  if (!data || typeof data !== 'object') return { ...UNCONFIGURED }
  return {
    configured: data.configured === true,
    provisioned: data.provisioned === true,
    datastore_id: data.datastore_id ?? null,
    prefix: data.prefix ?? null,
    demo_seeded: data.demo_seeded === true,
    usage_bytes: Number.isFinite(Number(data.usage_bytes)) ? Number(data.usage_bytes) : 0,
  }
}

/**
 * Fetch the managed-lakehouse status for the active project.
 *
 * Degrades gracefully: on a 404 (endpoint not deployed) or any transport error
 * it returns ``{ configured: false, ... }`` so the page renders the
 * "needs central storage / BYO available" explanatory state, never an error.
 *
 * @returns {Promise<{configured:boolean, provisioned:boolean, datastore_id:string|null, prefix:string|null, demo_seeded:boolean, usage_bytes:number}>}
 */
export async function lakehouseStatus() {
  try {
    return normStatus(await get('/lakehouse'))
  } catch (cause) {
    // 404 ⇒ surface not deployed ⇒ treat as unconfigured (not an error).
    if (cause?.status && cause.status !== 404) {
      console.warn('[lakehouse] status failed; treating as unconfigured:', cause.message)
    }
    return { ...UNCONFIGURED }
  }
}

/**
 * Provision the managed lakehouse for the active project.
 *
 * @param {{ seedDemo?: boolean }} [opts]
 * @returns {Promise<object>} the new status (normalized)
 *   Throws on failure so the page can show the error inline.
 */
export async function provisionLakehouse({ seedDemo = false } = {}) {
  const qs = seedDemo ? '?seed_demo=true' : ''
  return normStatus(await post(`/lakehouse/provision${qs}`))
}

/**
 * Seed demo data into the (already provisioned) managed lakehouse.
 *
 * @returns {Promise<object>} the new status (normalized)
 *   Throws on failure so the page can show the error inline.
 */
export async function seedDemoData() {
  return normStatus(await post('/lakehouse/demo'))
}

/**
 * Deprovision (delete) the managed lakehouse. Destructive — the caller must
 * confirm first. Returns null on a 204.
 *
 * @returns {Promise<null>} Throws on failure so the page can show the error.
 */
export async function deprovisionLakehouse() {
  return del('/lakehouse')
}
