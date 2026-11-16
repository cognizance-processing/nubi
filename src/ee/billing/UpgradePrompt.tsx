/**
 * UpgradePrompt.jsx — EE inline upgrade CTA (src/ee/billing/UpgradePrompt.jsx)
 *
 * A compact gated-feature block rendered when a paid feature is locked.
 * Slotted into core via 'upgrade-prompt' in registerBilling.js.
 *
 * Usage in EE components (after registerEe() has filled the slot):
 *
 *   import { getSlot } from '../../ee/registry.js'
 *   const UpgradePrompt = getSlot('upgrade-prompt')
 *   if (!UpgradePrompt) return null   // OSS — no EE
 *   return <UpgradePrompt feature="SSO" />
 *
 * Props
 * -----
 * feature    string   Human-readable feature name shown in the prompt.
 * tier       string   Minimum tier required, e.g. 'Pro'.  Defaults to 'Pro'.
 * compact    boolean  When true, renders a single-line inline badge instead
 *                     of the full card block.  Defaults to false.
 * className  string   Extra class names applied to the root element.
 */

import { useState } from 'react'
import { ArrowUpRight, Lock } from 'lucide-react'
import { useNavigate } from 'react-router-dom'
import { createCheckout } from '../../lib/ee/billing.js'
import EmptyState from '../../components/ui/EmptyState.jsx'
import Button from '../../components/ui/Button.jsx'

// ---------------------------------------------------------------------------
// Compact (inline) variant
// ---------------------------------------------------------------------------

function CompactPrompt({ feature, tier, className }) {
  const navigate = useNavigate()

  return (
    <span
      className={`inline-flex items-center gap-1.5 text-xs font-medium text-accent cursor-pointer underline-offset-2 hover:underline ${className ?? ''}`}
      onClick={() => navigate('/billing')}
      role="button"
      tabIndex={0}
      onKeyDown={(e) => e.key === 'Enter' && navigate('/billing')}
      aria-label={`Upgrade to ${tier} to unlock ${feature}`}
    >
      <Lock size={11} />
      {feature} requires {tier}
      <ArrowUpRight size={11} />
    </span>
  )
}

// ---------------------------------------------------------------------------
// Full card variant
// ---------------------------------------------------------------------------

function CardPrompt({ feature, tier, className }) {
  const navigate = useNavigate()
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)

  async function handleUpgrade() {
    setLoading(true)
    setError(null)
    try {
      const { checkout_url } = await createCheckout(tier.toLowerCase())
      window.location.href = checkout_url
    } catch {
      // If checkout API fails (e.g. no EE backend), fall back to billing page.
      setLoading(false)
      navigate('/billing')
    }
  }

  return (
    <EmptyState
      kind="gated"
      bordered
      className={className}
      title={`${feature} is on the ${tier} plan`}
      description={error ?? 'Upgrade to unlock it. Viewers stay free on every plan.'}
      action={
        <Button onClick={handleUpgrade} loading={loading} size="md">
          {!loading && <ArrowUpRight size={14} strokeWidth={1.75} aria-hidden="true" />}
          Upgrade to {tier}
        </Button>
      }
      secondaryAction={<Button variant="ghost" size="md" onClick={() => navigate('/billing')}>View plans</Button>}
    />
  )
}

// ---------------------------------------------------------------------------
// UpgradePrompt — public export
// ---------------------------------------------------------------------------

/**
 * @param {{
 *   feature?: string,
 *   tier?: string,
 *   compact?: boolean,
 *   className?: string,
 * }} props
 */
export default function UpgradePrompt({ feature = 'This feature', tier = 'Pro', compact = false, className }) {
  if (compact) {
    return <CompactPrompt feature={feature} tier={tier} className={className} />
  }
  return <CardPrompt feature={feature} tier={tier} className={className} />
}
