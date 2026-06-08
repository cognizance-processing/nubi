/**
 * TierCard.jsx — single tier column card for the pricing table (src/ee/billing/TierCard.jsx)
 *
 * Used by PricingPage.jsx.  Displays tier name, ZAR price (computed from the
 * live USD anchor × current FX rate), included quotas, feature list, and a CTA.
 *
 * Props
 * -----
 * tier         TierInfo     Tier definition object from billing.js.
 * fxRate       number|null  Current USD→ZAR rate; when null the reference price
 *                           from tier.price_zar is shown unchanged.
 * currentTier  string       The org's current billing tier id (e.g. 'free').
 * onUpgrade    Function     Callback invoked with tier.id when the CTA is clicked
 *                           (enterprise uses mail-to; free redirects to register).
 * loading      string|null  The tier id currently being upgraded (shows spinner).
 * billing      string       'monthly' | 'annual' — controls which price is shown.
 */

import { ArrowUpRight, CheckCircle, Loader2, Users } from 'lucide-react'
import { computeZar, formatZar } from '../../lib/ee/billing.js'

// ---------------------------------------------------------------------------
// Price display
// ---------------------------------------------------------------------------

/**
 * Compute the displayed ZAR price label for a tier given the current FX rate
 * and billing period.
 *
 * @param {import('../../lib/ee/billing.js').TierInfo} tier
 * @param {number|null} fxRate
 * @param {'monthly'|'annual'} billing
 * @returns {{ label: string, sub: string | null }}
 */
function priceDisplay(tier, fxRate, billing) {
  if (tier.id === 'free') {
    return { label: 'Free forever', sub: null }
  }
  if (tier.is_enterprise) {
    return { label: 'Custom pricing', sub: 'Annual billing required' }
  }

  const isAnnual = billing === 'annual'

  // Use live-computed ZAR when we have a rate; else fall back to reference
  const zarMonthly = fxRate
    ? computeZar(tier.usd_monthly, fxRate)
    : tier.price_zar

  if (isAnnual && tier.annual_usd) {
    const zarAnnualMonthly = fxRate
      ? computeZar(tier.usd_monthly * (10 / 12), fxRate)  // 10 months of 12
      : tier.annual_zar_monthly_equiv
    return {
      label: `${formatZar(zarAnnualMonthly)} / mo`,
      sub: `billed annually — 2 months free (${formatZar(zarMonthly)}/mo monthly)`,
    }
  }

  return {
    label: `${formatZar(zarMonthly)} / mo`,
    sub: tier.annual_usd
      ? `or ${formatZar(fxRate ? computeZar(tier.usd_monthly * (10 / 12), fxRate) : tier.annual_zar_monthly_equiv)}/mo billed annually`
      : null,
  }
}

// ---------------------------------------------------------------------------
// TierCard
// ---------------------------------------------------------------------------

/**
 * @param {{
 *   tier: import('../../lib/ee/billing.js').TierInfo,
 *   fxRate?: number | null,
 *   currentTier?: string,
 *   onUpgrade?: (id: string) => void,
 *   loading?: string | null,
 *   billing?: 'monthly' | 'annual',
 * }} props
 */
export default function TierCard({
  tier,
  fxRate = null,
  currentTier = 'free',
  onUpgrade,
  loading = null,
  billing = 'monthly',
}) {
  const isCurrent = tier.id === currentTier
  const isLoading = loading === tier.id
  const { label: priceLabel, sub: priceSub } = priceDisplay(tier, fxRate, billing)

  function handleCta() {
    if (tier.is_enterprise) {
      window.open('mailto:hello@nubi.dev?subject=Enterprise%20enquiry', '_blank', 'noopener,noreferrer')
      return
    }
    if (!isCurrent) {
      onUpgrade?.(tier.id)
    }
  }

  // CTA label
  const ctaLabel = isCurrent
    ? 'Current plan'
    : tier.is_enterprise
      ? 'Contact sales'
      : tier.cta_label

  return (
    <div
      className={[
        'relative flex flex-col rounded-2xl border p-6 gap-5 transition-shadow',
        tier.highlight
          ? 'border-accent bg-surface shadow-lg shadow-accent/10'
          : 'border-border bg-surface',
      ].join(' ')}
    >
      {/* Most popular badge */}
      {tier.highlight && (
        <span className="absolute -top-3 left-1/2 -translate-x-1/2 bg-accent text-white text-[10px] font-bold uppercase tracking-widest px-3 py-1 rounded-full shadow">
          Most popular
        </span>
      )}

      {/* Tier name + description */}
      <div>
        <p className="font-display font-semibold text-lg text-fg">{tier.name}</p>
        <p className="text-xs text-muted mt-1 leading-snug">{tier.description}</p>
      </div>

      {/* Price block */}
      <div>
        <p className="text-2xl font-bold text-fg leading-none">{priceLabel}</p>
        {priceSub && (
          <p className="text-xs text-muted mt-1.5 leading-snug">{priceSub}</p>
        )}
        {tier.usd_monthly > 0 && !tier.is_enterprise && (
          <p className="text-xs text-muted/60 mt-1">
            ${tier.usd_monthly} USD / month
          </p>
        )}
      </div>

      {/* Unlimited seats badge — headline differentiator */}
      <div className="flex items-center gap-1.5 rounded-lg bg-teal-50 dark:bg-teal-900/20 border border-teal-200 dark:border-teal-800 px-3 py-1.5">
        <Users size={12} className="text-teal-600 dark:text-teal-400 shrink-0" />
        <span className="text-xs font-semibold text-teal-700 dark:text-teal-300">
          Unlimited users — no per-seat charges
        </span>
      </div>

      {/* Feature list */}
      <ul className="space-y-2 flex-1">
        {tier.features.map((f) => (
          <li key={f} className="flex items-start gap-2 text-sm text-fg">
            <CheckCircle
              size={13}
              className="mt-0.5 shrink-0 text-teal-500"
              aria-hidden="true"
            />
            <span>{f}</span>
          </li>
        ))}
      </ul>

      {/* CTA button */}
      <button
        onClick={handleCta}
        disabled={isCurrent || isLoading}
        aria-label={ctaLabel}
        className={[
          'w-full flex items-center justify-center gap-2 px-4 py-2.5 rounded-xl text-sm font-semibold transition-colors',
          isCurrent
            ? 'bg-surface-2 text-muted cursor-default'
            : tier.highlight
              ? 'bg-accent text-white hover:bg-accent/90'
              : 'bg-surface-2 text-fg border border-border hover:bg-surface-3',
          'disabled:opacity-60',
        ].join(' ')}
      >
        {isLoading ? (
          <Loader2 size={15} className="animate-spin" aria-label="Loading" />
        ) : (
          <>
            {ctaLabel}
            {!isCurrent && !tier.is_enterprise && <ArrowUpRight size={14} />}
          </>
        )}
      </button>
    </div>
  )
}
