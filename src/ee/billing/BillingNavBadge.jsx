/**
 * BillingNavBadge.jsx — small plan-name chip for the sidebar nav (EE only).
 *
 * Slotted into core via 'billing-nav-badge' in registerBilling.js.
 * Renders nothing while billing status is loading so the nav layout stays stable.
 *
 * Usage in core (after EE is loaded):
 *   import { getSlot } from '../../ee/registry.js'
 *   const BillingNavBadge = getSlot('billing-nav-badge')
 *   return BillingNavBadge ? <BillingNavBadge /> : null
 */

import { useEffect, useState } from 'react'
import { fetchBillingStatus } from '../../lib/ee/billing.js'

const BADGE_STYLES = {
  free:       'bg-surface-2 text-muted',
  starter:    'bg-teal-100 text-teal-700 dark:bg-teal-900/40 dark:text-teal-300',
  pro:        'bg-indigo-100 text-indigo-700 dark:bg-indigo-900/40 dark:text-indigo-300',
  business:   'bg-violet-100 text-violet-700 dark:bg-violet-900/40 dark:text-violet-300',
  enterprise: 'bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-300',
}

const TIER_LABELS = {
  free:       'Free',
  starter:    'Starter',
  pro:        'Pro',
  business:   'Business',
  enterprise: 'Enterprise',
}

export default function BillingNavBadge() {
  const [tier, setTier] = useState(null)

  useEffect(() => {
    let cancelled = false
    fetchBillingStatus()
      .then((s) => { if (!cancelled) setTier(s.tier) })
      .catch(() => { /* degrade silently — badge just won't render */ })
    return () => { cancelled = true }
  }, [])

  if (!tier) return null

  return (
    <span
      className={`inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-semibold uppercase tracking-wide ${BADGE_STYLES[tier] ?? BADGE_STYLES.free}`}
      title={`Current plan: ${TIER_LABELS[tier] ?? tier}`}
    >
      {TIER_LABELS[tier] ?? tier}
    </span>
  )
}
