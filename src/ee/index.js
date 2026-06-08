/**
 * EE Module Entry Point (src/ee/index.js)
 *
 * This module is loaded DYNAMICALLY by App.jsx (via dynamic import) — never
 * statically imported by core.  It registers EE components into the slot
 * registry (src/ee/registry.js) and updates the feature-flag set by fetching
 * the live enabled list from GET /api/v1/features on the backend.
 *
 * Feature-flag integration
 * ------------------------
 * registerEe() kicks off a background fetch of GET /api/v1/features and calls
 * setEnabledFeatures() with whatever the backend returns.  The core features.js
 * module has already queued its own fetch (deduplicated), but this explicit call
 * ensures the EE loader's boot sequence is clearly visible and can be extended
 * with EE-specific endpoints (e.g. /api/v1/ee/features) in future phases.
 *
 * On 404/network error the EE loader degrades silently — features.js already
 * applies OSS defaults (commercial features = false) so no explicit fallback is
 * needed here.
 *
 * Adding an EE component (for BillingFrontendAgent)
 * -------------------------------------------------
 *   1. Create the component in src/ee/, e.g. src/ee/BillingPage.jsx.
 *   2. Import it here (static import is fine — this whole file is lazy from core).
 *   3. Call registerSlot('billing-page', BillingPage) inside registerEe().
 *
 * This file MUST NOT import anything from outside src/ee/ except:
 *   - src/ee/registry.js  (the seam — safe boundary)
 *   - src/lib/features.js (the feature-flag store — safe boundary)
 */

import { setEnabledFeatures } from '../lib/features.js'
import { registerBilling } from './billing/registerBilling.js'

// ---------------------------------------------------------------------------
// _fetchAndApplyFeatures — fetch backend feature set and update the store
// ---------------------------------------------------------------------------

/**
 * Fetch GET /api/v1/features from the backend and update the shared feature
 * store via setEnabledFeatures().
 *
 * Contract: { features: string[] }
 *   e.g. { features: ["flows", "connectors", "billing", "paid_tiers"] }
 *
 * On 404, 401, or any network error the call degrades silently and leaves the
 * feature store in its current state (OSS defaults from features.js).
 *
 * @returns {Promise<void>}
 */
async function _fetchAndApplyFeatures() {
  try {
    // Lazy-import api.js to avoid a static dependency on the core module from
    // within the EE loader (dynamic import keeps the boundary clear).
    // noqa: PLC0415
    const { get } = await import('../lib/api.js') // noqa: PLC0415
    const data = await get('/features')
    const list = Array.isArray(data?.features) ? data.features : []
    if (list.length > 0) {
      setEnabledFeatures(list)
    }
  } catch (err) {
    const label = err?.status === 404
      ? '404 (endpoint not yet registered)'
      : (err?.message ?? String(err))
    console.debug('[ee] GET /features failed (' + label + '); EE defaults preserved')
  }
}

// ---------------------------------------------------------------------------
// registerEe — called by App.jsx after dynamic import succeeds
// ---------------------------------------------------------------------------

/**
 * Wire EE components and features into the core extension points.
 *
 * Called once at app startup, after the EE module is dynamically imported.
 * Kicks off a background fetch of the backend feature set so that commercial
 * features are enabled only when the backend explicitly returns them (rather
 * than from a hardcoded static list).
 *
 * Returns true synchronously so App.jsx can log/track the EE mount; the
 * feature-flag update arrives asynchronously once the backend responds.
 *
 * @returns {boolean}
 */
export function registerEe() {
  // ── Feature flags ──────────────────────────────────────────────────────────
  // Fetch the live feature set from the backend.  The update is async; any
  // component that calls useFeature() will re-render once the fetch resolves
  // because features.js notifies all active listeners on setEnabledFeatures().
  _fetchAndApplyFeatures()

  // ── Slot registrations ─────────────────────────────────────────────────────
  // Billing slots: filled by BillingFrontendAgent (Phase 2).
  // registerBilling() calls registerSlot() for 'billing-page', 'billing-nav-badge',
  // and 'upgrade-prompt'.  In an OSS build (ee/ absent) this line is never reached.
  registerBilling()

  return true
}

// ---------------------------------------------------------------------------
// useEeFeature (convenience re-export for EE-internal components)
// ---------------------------------------------------------------------------
// EE components should import useFeature from src/lib/features.js directly.
// This re-export exists purely for convenience inside src/ee/*.
export { useFeature } from '../lib/features.js'
