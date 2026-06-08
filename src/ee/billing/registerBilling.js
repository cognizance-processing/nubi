/**
 * registerBilling.js — wire EE billing components into the slot registry.
 *
 * Called by src/ee/index.js inside registerEe() after setEnabledFeatures()
 * has run.  Fills the three billing slots so App.jsx and other EE consumers
 * can read them via getSlot() without ever statically importing this module.
 *
 * Slots filled
 * ------------
 *   'billing-page'         → PricingPage  (full /billing route — customer-facing pricing table
 *                                          with FX-computed ZAR prices, tier grid, FxNotice,
 *                                          current-plan strip, and upgrade CTAs via Paystack)
 *   'billing-account-page' → BillingPage  (account management — current plan card, manage portal)
 *   'upgrade-prompt'       → UpgradePrompt (inline gated-feature CTA)
 *   'billing-nav-badge'    → BillingNavBadge (small plan chip in sidebar nav)
 *
 * PricingPage is the primary 'billing-page' slot.  It incorporates the current
 * plan strip inline so customers can see their plan while browsing upgrade options.
 * BillingNavBadge and UpgradePrompt are auxiliary slots used by other EE pages.
 *
 * This module is ONLY imported by src/ee/index.js.
 * It must NOT be imported by any core file (src/ outside src/ee/).
 */

import { registerSlot } from '../registry.js'
import PricingPage from './PricingPage.jsx'
import BillingPage from './BillingPage.jsx'
import UpgradePrompt from './UpgradePrompt.jsx'
import BillingNavBadge from './BillingNavBadge.jsx'

/**
 * Register all billing EE slots.
 * Idempotent — safe to call more than once (last writer wins in registry.js).
 */
export function registerBilling() {
  // Primary billing route — customer-facing pricing/upgrade page
  registerSlot('billing-page', PricingPage)
  // Secondary — account management (manage portal, invoices) for direct nav
  registerSlot('billing-account-page', BillingPage)
  // Auxiliary slots
  registerSlot('upgrade-prompt', UpgradePrompt)
  registerSlot('billing-nav-badge', BillingNavBadge)
}
