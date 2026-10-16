/**
 * TierCards.jsx — core (OSS-safe) tier card grid (src/components/pricing/TierCards.jsx)
 *
 * Renders a responsive grid of pricing tier cards from a list of tier objects.
 * This is CORE — it has no EE imports and no checkout logic.  The EE billing
 * page re-uses this component and passes an `onUpgrade` callback to wire in
 * checkout CTAs.  On an OSS landing page `onUpgrade` can be omitted or used
 * for a "get started" CTA.
 *
 * Props
 * -----
 * tiers        TierInfo[]       Array of tier objects (from pricing.js FALLBACK_TIERS or API)
 * fxRate       number|null      Live USD→ZAR rate; when null the tier.price_zar is used as-is
 * currentTier  string           ID of the org's current tier ('free', 'starter', …)
 * billing      'monthly'|'annual'
 * onUpgrade    (tierId) => void  Optional — called when a non-current, non-enterprise CTA is clicked
 * loading      string|null      Tier id currently showing a spinner (upgrade in flight)
 *
 * Tier model (5 tiers): Free / Starter ($9) / Team ($49) / Pro ($149) / Enterprise (from $1,000 + SLA)
 * No per-seat pricing — unlimited seats at every tier.
 */

import { ArrowUpRight, CheckCircle, Loader2, Users, ShieldCheck, Clock, Construction } from 'lucide-react'
import { computeZar, formatZar } from '../../lib/pricing.js'

// ---------------------------------------------------------------------------
// Price display helper
// ---------------------------------------------------------------------------

function priceDisplay(tier, fxRate, billing) {
  if (tier.id === 'free') return { label: 'Free forever', sub: null }
  if (tier.is_enterprise) return { label: 'Custom pricing', sub: 'Annual billing required' }

  const isAnnual = billing === 'annual'
  const zarMonthly = fxRate ? computeZar(tier.usd_monthly, fxRate) : tier.price_zar

  if (isAnnual && tier.annual_usd) {
    const zarAnnualMonthly = fxRate
      ? computeZar(tier.usd_monthly * (10 / 12), fxRate)
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
// SLA badge — shown on Enterprise tier
// ---------------------------------------------------------------------------

function SlaBadge({ sla }) {
  return (
    <div className="border border-[var(--frame-line)] border-l-2 border-l-[var(--ink)] bg-surface-2 px-3 py-2 space-y-1.5">
      <div className="flex items-center gap-1.5">
        <ShieldCheck size={12} className="text-[color:var(--warning-fg)] shrink-0" />
        <span className="text-xs font-bold text-[color:var(--warning-fg)] uppercase tracking-wide">
          SLA included
        </span>
      </div>
      <div className="space-y-0.5">
        <p className="text-xs text-amber-800 dark:text-amber-200 font-semibold">{sla.uptime} uptime guarantee</p>
        <div className="flex items-start gap-1">
          <Clock size={10} className="text-[color:var(--warning-fg)] mt-0.5 shrink-0" />
          <p className="text-[11px] text-[color:var(--warning-fg)] leading-tight">{sla.response_time}</p>
        </div>
        <p className="text-[11px] text-[color:var(--warning-fg)] leading-tight">{sla.support}</p>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Single TierCard (exported so EE can use it independently)
// ---------------------------------------------------------------------------

/**
 * @param {{
 *   tier: object,
 *   fxRate?: number | null,
 *   currentTier?: string,
 *   onUpgrade?: (id: string) => void,
 *   loading?: string | null,
 *   billing?: 'monthly' | 'annual',
 * }} props
 */
export function TierCard({
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
      window.open('mailto:hello@nubi.dev?subject=Enterprise%20plan%20enquiry', '_blank', 'noopener,noreferrer')
      return
    }
    if (!isCurrent) onUpgrade?.(tier.id)
  }

  const ctaLabel = isCurrent
    ? 'Current plan'
    : tier.is_enterprise
      ? 'Contact sales'
      : tier.cta_label

  return (
    <div
      className={[
        'relative flex flex-col rounded-sm border p-6 gap-5',
        tier.highlight
          ? 'border-[var(--ink)] bg-surface shadow-[inset_0_3px_0_var(--accent)]'
          : tier.has_sla
            ? 'border-[var(--ink)] bg-surface'
            : 'border-border bg-surface',
      ].join(' ')}
    >
      {tier.highlight && (
        <span className="absolute -top-3 left-1/2 -translate-x-1/2 bg-[var(--ink)] text-[var(--ink-fg)] font-mono text-[10px] font-medium uppercase tracking-[0.08em] px-2.5 py-1 rounded-sm">
          Most popular
        </span>
      )}
      {tier.has_sla && !tier.highlight && (
        <span className="absolute -top-3 left-1/2 -translate-x-1/2 bg-[var(--ink)] text-[var(--ink-fg)] font-mono text-[10px] font-medium uppercase tracking-[0.08em] px-2.5 py-1 rounded-sm">
          Enterprise SLA
        </span>
      )}

      <div>
        <p className="font-display font-semibold text-lg text-fg">{tier.name}</p>
        <p className="text-xs text-muted mt-1 leading-snug">{tier.description}</p>
      </div>

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

      {/* Unlimited seats — headline differentiator */}
      <div className="flex items-center gap-1.5 rounded-sm bg-surface-2 border border-border px-3 py-1.5">
        <Users size={12} className="text-[color:var(--success-fg)] shrink-0" />
        <span className="text-xs font-semibold text-teal-700 dark:text-teal-300">
          Unlimited users — no per-seat charges
        </span>
      </div>

      {/* SLA badge — Enterprise tier only */}
      {tier.has_sla && tier.sla && (
        <SlaBadge sla={tier.sla} />
      )}

      <ul className="space-y-2 flex-1">
        {tier.features.map((f) => {
          const isComingSoon = /coming soon/i.test(f)
          // Strip the inline "(coming soon)" annotations so we can render a dedicated badge
          const cleanText = f.replace(/\s*[(·]?\s*coming soon[)\s]*/gi, '').replace(/\s*·\s*$/, '').trim()
          return (
            <li key={f} className={`flex items-start gap-2 text-sm ${isComingSoon ? 'opacity-70' : 'text-fg'}`}>
              {isComingSoon ? (
                <Construction size={13} className="mt-0.5 shrink-0 text-muted" aria-hidden="true" />
              ) : (
                <CheckCircle size={13} className="mt-0.5 shrink-0 text-fg" aria-hidden="true" />
              )}
              <span className={isComingSoon ? 'text-muted' : ''}>
                {cleanText}
                {isComingSoon && (
                  <span className="ml-1.5 inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded-sm border border-[var(--frame-line)] text-fg font-mono text-[10px] font-medium uppercase tracking-[0.06em] whitespace-nowrap">
                    Coming soon
                  </span>
                )}
              </span>
            </li>
          )
        })}
      </ul>

      {/* CTA — rendered only when onUpgrade is provided or enterprise */}
      {(onUpgrade || tier.is_enterprise || isCurrent) && (
        <button
          onClick={handleCta}
          disabled={isCurrent || isLoading}
          aria-label={ctaLabel}
          className={[
            'w-full flex items-center justify-center gap-2 px-4 py-2.5 rounded-sm text-sm font-semibold transition-colors',
            isCurrent
              ? 'bg-surface-2 text-muted cursor-default'
              : tier.highlight
                ? 'bg-[var(--ink)] text-[var(--ink-fg)] hover:bg-[var(--ink-hover)]'
                : tier.has_sla
                  ? 'bg-[var(--ink)] text-[var(--ink-fg)] hover:bg-[var(--ink-hover)]'
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
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// TierCards — the grid wrapper (adapts to 4- or 5-tier layout)
// ---------------------------------------------------------------------------

/**
 * @param {{
 *   tiers: object[],
 *   fxRate?: number | null,
 *   currentTier?: string,
 *   billing?: 'monthly' | 'annual',
 *   onUpgrade?: (id: string) => void,
 *   loading?: string | null,
 * }} props
 */
export default function TierCards({
  tiers,
  fxRate = null,
  currentTier = 'free',
  billing = 'monthly',
  onUpgrade,
  loading = null,
}) {
  // Grid adapts to tier count: 4-tier model → 4-up; 5-tier model → 5-up.
  const xlCols = tiers.length >= 5 ? 'xl:grid-cols-5' : 'xl:grid-cols-4'
  return (
    <section aria-label="Pricing tiers">
      <div className={`grid gap-4 sm:grid-cols-2 lg:grid-cols-3 ${xlCols}`}>
        {tiers.map((tier) => (
          <TierCard
            key={tier.id}
            tier={tier}
            fxRate={fxRate}
            currentTier={currentTier}
            billing={billing}
            onUpgrade={onUpgrade}
            loading={loading}
          />
        ))}
      </div>
    </section>
  )
}
