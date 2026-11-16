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
  starter:    'border border-[var(--frame-line)] text-fg',
  pro:        'bg-[var(--ink)] text-[var(--ink-fg)] border border-[var(--ink)]',
  business:   'border border-[var(--frame-line)] text-fg',
  enterprise: 'border border-[var(--frame-line)] text-fg',
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
      className={`inline-flex items-center px-2 py-0.5 rounded-sm text-[10px] font-semibold uppercase tracking-wide ${BADGE_STYLES[tier] ?? BADGE_STYLES.free}`}
      title={`Current plan: ${TIER_LABELS[tier] ?? tier}`}
    >
      {TIER_LABELS[tier] ?? tier}
    </span>
  )
}
