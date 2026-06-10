/**
 * PricingPage.jsx — EE billing page (src/ee/billing/PricingPage.jsx)
 *
 * Slotted into core via 'billing-page' in registerBilling.js.  Mounted by
 * App.jsx at /billing when EE is loaded and useFeature('billing') is true.
 *
 * Architecture
 * ------------
 * All PRESENTATION is delegated to core components in src/components/pricing/:
 *   - TierCards          → tier grid (OSS-safe; no checkout logic)
 *   - PricingCalculator  → usage estimator + two-tab competitor comparison
 *   - BillingToggle      → monthly/annual switch
 *   - OverageTable       → collapsible overage rate schedule
 *   - FxDisclosure       → ZAR/USD disclosure block
 *
 * EE-SPECIFIC behaviour in this file:
 *   - fetchBillingStatus() → current tier, seat usage, renewal date
 *   - fetchBillingTiers()  → live tier list (falls back to FALLBACK_TIERS)
 *   - fetchFxRate()        → live USD→ZAR rate
 *   - createCheckout()     → Paystack checkout session
 *   - openBillingPortal()  → Paystack customer portal
 *   - CurrentPlanStrip     → shows current plan badge + "Manage billing" link
 *   - Checkout result banners (success / cancelled URL params)
 *
 * Data flow
 * ---------
 *  fetchBillingStatus() → current tier, seat usage, renewal date
 *  fetchFxRate()        → live USD→ZAR rate, updated_at, fallback flag
 *  fetchBillingTiers()  → tier list (falls back to FALLBACK_TIERS)
 *
 * All three fetches run in parallel on mount.  If any fail the page degrades:
 *  - Status error → hides current-plan strip, still shows pricing table.
 *  - FX error     → shows reference prices from FALLBACK_TIERS; FxDisclosure flags fallback.
 *  - Tiers error  → FALLBACK_TIERS used automatically by fetchBillingTiers().
 *
 * OSS degradation: registerBilling.js only calls registerSlot() when this
 * module is loaded, which only happens via the EE dynamic import in ee/index.js.
 * In an OSS build this file is never reached.
 */

import { useEffect, useState, useCallback } from 'react'
import {
  CreditCard,
  Users,
  Loader2,
  CheckCircle,
  AlertCircle,
  ExternalLink,
} from 'lucide-react'
import {
  fetchBillingStatus,
  fetchBillingTiers,
  fetchFxRate,
  createCheckout,
  openBillingPortal,
  FALLBACK_TIERS,
} from '../../lib/ee/billing.js'

// Core pricing components — OSS-safe, no EE imports inside them
import TierCards from '../../components/pricing/TierCards.jsx'
import PricingCalculator from '../../components/pricing/PricingCalculator.jsx'
import { BillingToggle, OverageTable, FxDisclosure, WalletBillingExplainer } from '../../components/pricing/PricingTables.jsx'

// ---------------------------------------------------------------------------
// Tier badge styles (EE-only; the badge is shown in the plan strip)
// ---------------------------------------------------------------------------

const TIER_LABELS = {
  free: 'Free', starter: 'Starter', team: 'Team', pro: 'Pro', enterprise: 'Enterprise',
  // Legacy tier IDs kept for backward compat during migration
  launch: 'Starter', growth: 'Pro', scale: 'Enterprise',
}

const TIER_BADGE_STYLES = {
  free:       'bg-surface-2 text-muted',
  starter:    'bg-teal-100 text-teal-700 dark:bg-teal-900/40 dark:text-teal-300',
  team:       'bg-violet-100 text-violet-700 dark:bg-violet-900/40 dark:text-violet-300',
  pro:        'bg-indigo-100 text-indigo-700 dark:bg-indigo-900/40 dark:text-indigo-300',
  enterprise: 'bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-300',
  // Legacy
  launch: 'bg-teal-100 text-teal-700 dark:bg-teal-900/40 dark:text-teal-300',
  growth: 'bg-indigo-100 text-indigo-700 dark:bg-indigo-900/40 dark:text-indigo-300',
  scale:  'bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-300',
}

// ---------------------------------------------------------------------------
// EE-only: Current plan strip
// ---------------------------------------------------------------------------

function CurrentPlanStrip({ status, onManage, managing }) {
  const tierLabel = TIER_LABELS[status.tier] ?? status.tier
  const badgeStyle = TIER_BADGE_STYLES[status.tier] ?? TIER_BADGE_STYLES.free

  return (
    <div className="flex flex-wrap items-center gap-3 rounded-xl border border-border bg-surface-2 px-5 py-3">
      <span className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-semibold uppercase tracking-wide ${badgeStyle}`}>
        {tierLabel}
      </span>
      <span className="flex items-center gap-1.5 text-sm text-muted">
        <Users size={13} />
        Unlimited seats
      </span>
      {status.renewal_date && (
        <span className="text-sm text-muted">
          · Renews {new Date(status.renewal_date).toLocaleDateString('en-ZA')}
        </span>
      )}
      {status.trial_ends_at && (
        <span className="text-sm text-amber-600 dark:text-amber-400">
          · Trial ends {new Date(status.trial_ends_at).toLocaleDateString('en-ZA')}
        </span>
      )}
      {status.tier !== 'free' && (
        <button
          onClick={onManage}
          disabled={managing}
          className="ml-auto inline-flex items-center gap-1.5 text-sm font-medium text-accent hover:underline disabled:opacity-50"
        >
          {managing
            ? <Loader2 size={13} className="animate-spin" />
            : <ExternalLink size={13} />}
          Manage billing
        </button>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// PricingPage
// ---------------------------------------------------------------------------

export default function PricingPage() {
  const [status, setStatus]                   = useState(null)
  const [tiers, setTiers]                     = useState(FALLBACK_TIERS)
  const [fxRate, setFxRate]                   = useState(null)
  const [fxUpdatedAt, setFxUpdatedAt]         = useState(null)
  const [fxFallback, setFxFallback]           = useState(false)
  const [loadingStatus, setLoadingStatus]     = useState(true)
  const [statusError, setStatusError]         = useState(null)
  const [billing, setBilling]                 = useState('monthly')
  const [upgradeLoading, setUpgradeLoading]   = useState(null)
  const [upgradeError, setUpgradeError]       = useState(null)
  const [managing, setManaging]               = useState(false)

  // URL param feedback (returned from Paystack redirect)
  const params = typeof window !== 'undefined'
    ? new URLSearchParams(window.location.search)
    : new URLSearchParams()
  const checkoutStatus = params.get('status') // 'success' | 'cancelled'

  // Load status, tiers, and FX rate in parallel on mount
  useEffect(() => {
    let cancelled = false

    Promise.all([
      fetchBillingStatus().catch(() => null),
      fetchBillingTiers(),
      fetchFxRate(),
    ]).then(([s, t, fx]) => {
      if (cancelled) return
      setStatus(s)
      if (s === null) setStatusError('Could not load billing status.')
      setTiers(t)
      setFxRate(fx.rate)
      setFxUpdatedAt(fx.updated_at)
      setFxFallback(fx.fallback ?? false)
      setLoadingStatus(false)
    })

    return () => { cancelled = true }
  }, [])

  // EE-specific: Paystack checkout
  const handleUpgrade = useCallback(async (tierId) => {
    setUpgradeLoading(tierId)
    setUpgradeError(null)
    try {
      const { checkout_url } = await createCheckout(tierId)
      window.location.href = checkout_url
    } catch (err) {
      setUpgradeError(err?.message ?? 'Failed to start checkout. Please try again.')
      setUpgradeLoading(null)
    }
  }, [])

  // EE-specific: Paystack customer portal
  const handleManage = useCallback(async () => {
    setManaging(true)
    try {
      const { portal_url } = await openBillingPortal()
      window.open(portal_url, '_blank', 'noopener,noreferrer')
    } catch (err) {
      setUpgradeError(err?.message ?? 'Failed to open billing portal.')
    } finally {
      setManaging(false)
    }
  }, [])

  const currentTier = status?.tier ?? 'free'

  return (
    <div className="max-w-5xl mx-auto px-6 py-8 space-y-8">

      {/* Page header */}
      <header className="flex items-center gap-3">
        <div
          className="flex items-center justify-center w-11 h-11 rounded-2xl shrink-0"
          style={{ background: 'linear-gradient(135deg, #1b2363, #2456a6, #17b3a3)' }}
        >
          <CreditCard size={22} className="text-white" />
        </div>
        <div>
          <h1 className="font-display font-semibold text-2xl text-fg">Plans &amp; Billing</h1>
          <p className="text-muted text-sm">
            Flat plan + prepaid usage wallet. Unlimited seats. All prices in ZAR, converted from USD daily.
          </p>
        </div>
      </header>

      {/* Checkout result banners (EE-specific) */}
      {checkoutStatus === 'success' && (
        <div className="flex items-center gap-3 rounded-xl bg-teal-50 dark:bg-teal-900/20 border border-teal-200 dark:border-teal-800 px-4 py-3 text-sm text-teal-800 dark:text-teal-200">
          <CheckCircle size={16} className="shrink-0" />
          Payment successful — your plan has been updated.
        </div>
      )}
      {checkoutStatus === 'cancelled' && (
        <div className="flex items-center gap-3 rounded-xl bg-amber-50 dark:bg-amber-900/20 border border-amber-200 dark:border-amber-800 px-4 py-3 text-sm text-amber-800 dark:text-amber-200">
          <AlertCircle size={16} className="shrink-0" />
          Checkout was cancelled — no charge was made.
        </div>
      )}

      {/* Upgrade error */}
      {upgradeError && (
        <div className="flex items-center gap-3 rounded-xl bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 px-4 py-3 text-sm text-red-800 dark:text-red-200">
          <AlertCircle size={16} className="shrink-0" />
          {upgradeError}
        </div>
      )}

      {/* Current plan strip (EE-specific) */}
      {loadingStatus ? (
        <div className="flex items-center gap-2 text-muted text-sm">
          <Loader2 size={15} className="animate-spin" />
          Loading plan status…
        </div>
      ) : status ? (
        <CurrentPlanStrip status={status} onManage={handleManage} managing={managing} />
      ) : statusError ? (
        <div className="flex items-center gap-3 rounded-xl bg-surface-2 border border-border px-4 py-3 text-sm text-muted">
          <AlertCircle size={15} className="shrink-0 text-amber-500" />
          {statusError}
        </div>
      ) : null}

      {/* FX rate notice — compact strip above tiers */}
      {fxRate && (
        <FxDisclosure
          rate={fxRate}
          updatedAt={fxUpdatedAt}
          isFallback={fxFallback}
          compact
        />
      )}

      {/* Billing period toggle — core component */}
      <BillingToggle value={billing} onChange={setBilling} />

      {/* Tier grid — core component; onUpgrade wires in EE checkout */}
      <TierCards
        tiers={tiers}
        fxRate={fxRate}
        currentTier={currentTier}
        billing={billing}
        onUpgrade={handleUpgrade}
        loading={upgradeLoading}
      />

      {/* How billing works: flat plan + wallet — core component */}
      <WalletBillingExplainer />

      {/* Wallet overage schedule — core component */}
      <OverageTable />

      {/* Pricing calculator — core component with two-tab comparison */}
      <PricingCalculator fxRate={fxRate} />

      {/* FX disclosure — full-width, below calculator */}
      <FxDisclosure
        rate={fxRate}
        updatedAt={fxUpdatedAt}
        isFallback={fxFallback}
      />

      {/* Footer links */}
      <p className="text-xs text-muted border-t border-border pt-4">
        All paid plans (Starter, Team, Pro) billed monthly or annually — 2 months free on annual.
        Enterprise pricing is custom-quoted and includes a dedicated SLA + named support engineer.
        Usage overages are debited from your prepaid wallet balance in real-time.
        All amounts in ZAR, charged via Paystack. VAT may apply.{' '}
        Contact{' '}
        <a href="mailto:hello@nubi.dev" className="underline hover:text-fg transition-colors">
          hello@nubi.dev
        </a>{' '}
        or{' '}
        <a href="mailto:billing@nubi.io" className="underline hover:text-fg transition-colors">
          billing@nubi.io
        </a>
        .
      </p>
    </div>
  )
}
