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
 * The live feature set reaches the core feature store (src/lib/features.js)
 * through the shell's GET /bootstrap, or the store's own GET /features once a
 * session exists. registerEe() no longer fetches it at load time: that ran
 * before the session was restored and always cost a 401 plus a retry.
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

import { registerBilling } from './billing/registerBilling.js'

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
  // The live feature set arrives with the shell's GET /bootstrap (or the
  // feature store's own GET /features once signed in); components using
  // useFeature() re-render when it lands. Fetching here, at module load,
  // ran before the session was restored: a guaranteed 401 plus a retry.

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
