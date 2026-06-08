/**
 * PricingPage.jsx — customer-facing pricing / upgrade page (src/ee/billing/PricingPage.jsx)
 *
 * Slotted into core via 'billing-page' in registerBilling.js.  Mounted by
 * App.jsx at /billing when EE is loaded and useFeature('billing') is true.
 *
 * Layout
 * ------
 * 1. Page header (CreditCard icon, title, subtitle) — matches SettingsPage.jsx.
 * 2. Current plan summary strip (tier badge + seat usage).
 * 3. Monthly/Annual billing toggle.
 * 4. Tier grid — Free · Starter · Pro · Business · Enterprise — via TierCard.
 * 5. Overage rate schedule (collapsible).
 * 6. FxNotice (disclosure + current rate + last-updated timestamp).
 *
 * Data flow
 * ---------
 *  fetchBillingStatus() → current tier, seat usage, renewal date
 *  fetchFxRate()        → live USD→ZAR rate, updated_at, fallback flag
 *  fetchBillingTiers()  → tier list (falls back to FALLBACK_TIERS)
 *
 * All three fetches run in parallel on mount.  If any fail the page degrades:
 *  - Status error → hides current-plan strip, still shows pricing table.
 *  - FX error     → shows reference prices from FALLBACK_TIERS; FxNotice flags fallback.
 *  - Tiers error  → FALLBACK_TIERS used automatically by fetchBillingTiers().
 *
 * Styling follows SettingsPage.jsx pattern (max-w-5xl for the wider pricing
 * grid, standard header block with gradient icon).
 *
 * OSS degradation: registerBilling.js only calls registerSlot() when this
 * module is loaded, which only happens via the EE dynamic import in ee/index.js.
 * In an OSS build this file is never reached.
 */

import { useEffect, useState, useCallback } from 'react'
import {
  CreditCard,
  Users,
  ChevronDown,
  ChevronUp,
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
  formatZar,
} from '../../lib/ee/billing.js'
import TierCard from './TierCard.jsx'
import FxNotice from './FxNotice.jsx'
import PricingCalculator from './PricingCalculator.jsx'

// ---------------------------------------------------------------------------
// Overage rate schedule
// ---------------------------------------------------------------------------

const OVERAGES = [
  { metric: 'Storage',            rate: 'R 1.50 / GB-month',       margin: '~84%',  note: 'Available on all paid tiers' },
  { metric: 'Compute',            rate: 'R 100 / 1,000 CU',        margin: '~77%',  note: 'Starter+' },
  { metric: 'AI calls',           rate: 'R 5 / call',              margin: '~99%',  note: 'Haiku grounding or Sonnet chat' },
  { metric: 'Embedded sessions',  rate: 'R 50 / 10,000 sessions',  margin: '~99%',  note: 'Free on Enterprise; near-zero egress cost on R2' },
  { metric: 'Agent / kernel run', rate: 'R 2 / run',               margin: '~99%',  note: 'Pro+ remote kernel (E2B)' },
]

function OverageTable() {
  return (
    <div className="overflow-x-auto rounded-xl border border-border">
      <table className="min-w-full text-sm">
        <thead>
          <tr className="border-b border-border bg-surface-2">
            <th className="text-left px-4 py-2.5 font-semibold text-muted text-xs uppercase tracking-wide">Metric</th>
            <th className="text-left px-4 py-2.5 font-semibold text-muted text-xs uppercase tracking-wide">Rate (ZAR)</th>
            <th className="text-left px-4 py-2.5 font-semibold text-muted text-xs uppercase tracking-wide hidden sm:table-cell">Notes</th>
          </tr>
        </thead>
        <tbody>
          {OVERAGES.map((row, i) => (
            <tr key={row.metric} className={i % 2 === 0 ? 'bg-surface' : 'bg-surface-2'}>
              <td className="px-4 py-2.5 font-medium text-fg">{row.metric}</td>
              <td className="px-4 py-2.5 text-fg font-mono">{row.rate}</td>
              <td className="px-4 py-2.5 text-muted hidden sm:table-cell">{row.notes ?? row.note}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Current plan strip
// ---------------------------------------------------------------------------

const TIER_LABELS = {
  free: 'Free',
  starter: 'Starter',
  pro: 'Pro',
  business: 'Business',
  enterprise: 'Enterprise',
}

const TIER_BADGE_STYLES = {
  free:       'bg-surface-2 text-muted',
  starter:    'bg-teal-100 text-teal-700 dark:bg-teal-900/40 dark:text-teal-300',
  pro:        'bg-indigo-100 text-indigo-700 dark:bg-indigo-900/40 dark:text-indigo-300',
  business:   'bg-violet-100 text-violet-700 dark:bg-violet-900/40 dark:text-violet-300',
  enterprise: 'bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-300',
}

function CurrentPlanStrip({ status, onManage, managing }) {
  const tierLabel = TIER_LABELS[status.tier] ?? status.tier
  const badgeStyle = TIER_BADGE_STYLES[status.tier] ?? TIER_BADGE_STYLES.free
  // Seats are unlimited on every tier — Nubi has no per-seat pricing.
  const seatText = 'Unlimited seats'

  return (
    <div className="flex flex-wrap items-center gap-3 rounded-xl border border-border bg-surface-2 px-5 py-3">
      <span className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-semibold uppercase tracking-wide ${badgeStyle}`}>
        {tierLabel}
      </span>
      <span className="flex items-center gap-1.5 text-sm text-muted">
        <Users size={13} />
        {seatText}
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
// Billing toggle (monthly / annual)
// ---------------------------------------------------------------------------

function BillingToggle({ value, onChange }) {
  return (
    <div className="flex items-center gap-3 justify-center">
      <span className={`text-sm font-medium ${value === 'monthly' ? 'text-fg' : 'text-muted'}`}>
        Monthly
      </span>
      <button
        role="switch"
        aria-checked={value === 'annual'}
        onClick={() => onChange(value === 'monthly' ? 'annual' : 'monthly')}
        className={[
          'relative inline-flex h-6 w-11 shrink-0 rounded-full border-2 border-transparent transition-colors duration-200',
          'focus:outline-none focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-offset-2',
          value === 'annual' ? 'bg-accent' : 'bg-border',
        ].join(' ')}
      >
        <span
          className={[
            'pointer-events-none inline-block h-5 w-5 rounded-full bg-white shadow ring-0 transition-transform duration-200',
            value === 'annual' ? 'translate-x-5' : 'translate-x-0',
          ].join(' ')}
        />
      </button>
      <span className={`text-sm font-medium ${value === 'annual' ? 'text-fg' : 'text-muted'}`}>
        Annual
        <span className="ml-1.5 inline-flex items-center px-1.5 py-0.5 rounded-full bg-teal-100 text-teal-700 dark:bg-teal-900/30 dark:text-teal-300 text-[10px] font-bold uppercase tracking-wide">
          2 months free
        </span>
      </span>
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
  const [showOverages, setShowOverages]       = useState(false)

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
      if (s === null) {
        setStatusError('Could not load billing status.')
      }
      setTiers(t)
      setFxRate(fx.rate)
      setFxUpdatedAt(fx.updated_at)
      setFxFallback(fx.fallback ?? false)
      setLoadingStatus(false)
    })

    return () => { cancelled = true }
  }, [])

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
            Choose the plan that fits your team. All prices in ZAR, converted from USD daily.
          </p>
        </div>
      </header>

      {/* Checkout result banners */}
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

      {/* Current plan strip */}
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
        <FxNotice
          rate={fxRate}
          updatedAt={fxUpdatedAt}
          isFallback={fxFallback}
          compact
        />
      )}

      {/* Billing period toggle */}
      <BillingToggle value={billing} onChange={setBilling} />

      {/* Tier grid */}
      <section aria-label="Pricing tiers">
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5">
          {tiers.map((tier) => (
            <TierCard
              key={tier.id}
              tier={tier}
              fxRate={fxRate}
              currentTier={currentTier}
              onUpgrade={handleUpgrade}
              loading={upgradeLoading}
              billing={billing}
            />
          ))}
        </div>
      </section>

      {/* Overage schedule (collapsible) */}
      <section>
        <button
          onClick={() => setShowOverages((v) => !v)}
          className="flex items-center gap-2 text-sm font-medium text-muted hover:text-fg transition-colors"
          aria-expanded={showOverages}
        >
          {showOverages ? <ChevronUp size={15} /> : <ChevronDown size={15} />}
          Overage rate schedule
        </button>
        {showOverages && (
          <div className="mt-3">
            <OverageTable />
            <p className="text-xs text-muted mt-2">
              Overages are billed monthly in arrears. Seats are unlimited at every tier — no per-seat charges.
            </p>
          </div>
        )}
      </section>

      {/* Pricing calculator — interactive usage estimator + competitor comparison */}
      <PricingCalculator fxRate={fxRate} />

      {/* FX disclosure notice — full-width, below calculator */}
      <FxNotice
        rate={fxRate}
        updatedAt={fxUpdatedAt}
        isFallback={fxFallback}
      />

      {/* Footer links */}
      <p className="text-xs text-muted border-t border-border pt-4">
        All paid plans billed monthly (or annually — 2 months free) in ZAR via Paystack. VAT may apply.
        Enterprise pricing is custom-quoted — contact{' '}
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
