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
import { ArrowUpRight, Lock, Loader2 } from 'lucide-react'
import { useNavigate } from 'react-router-dom'
import { createCheckout } from '../../lib/ee/billing.js'

// ---------------------------------------------------------------------------
// Compact (inline) variant
// ---------------------------------------------------------------------------

function CompactPrompt({ feature, tier, className }) {
  const navigate = useNavigate()

  return (
    <span
      className={`inline-flex items-center gap-1.5 text-xs font-medium text-accent cursor-pointer hover:underline ${className ?? ''}`}
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
    <div
      className={`rounded-2xl border border-border bg-surface p-6 flex flex-col items-start gap-4 ${className ?? ''}`}
    >
      <div className="flex items-center gap-3">
        <div
          className="flex items-center justify-center w-10 h-10 rounded-xl shrink-0"
          style={{ background: 'linear-gradient(135deg, #1b2363, #2456a6, #17b3a3)' }}
        >
          <Lock size={18} className="text-white" />
        </div>
        <div>
          <p className="font-display font-semibold text-base text-fg">
            {feature} is a {tier} feature
          </p>
          <p className="text-sm text-muted mt-0.5">
            Upgrade your plan to unlock this capability.
          </p>
        </div>
      </div>

      {error && (
        <p className="text-xs text-red-600 dark:text-red-400">{error}</p>
      )}

      <div className="flex items-center gap-3">
        <button
          onClick={handleUpgrade}
          disabled={loading}
          className="inline-flex items-center gap-2 px-4 py-2 rounded-xl text-sm font-semibold bg-accent text-white hover:bg-accent/90 disabled:opacity-60 transition-colors"
        >
          {loading ? (
            <Loader2 size={14} className="animate-spin" />
          ) : (
            <ArrowUpRight size={14} />
          )}
          Upgrade to {tier}
        </button>

        <button
          onClick={() => navigate('/billing')}
          className="text-sm text-muted hover:text-fg transition-colors"
        >
          View plans
        </button>
      </div>
    </div>
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
