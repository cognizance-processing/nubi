/**
 * BillingPage.jsx — EE billing page (src/ee/billing/BillingPage.jsx)
 *
 * Mounted by App.jsx at /billing via the 'billing-page' slot when:
 *   1. The EE module has loaded (registerEe() was called), AND
 *   2. useFeature('billing') returns true.
 *
 * Shows the org's current tier + seat usage, then a pricing table of ZAR tiers
 * with an Upgrade button that creates a Paystack checkout session and redirects
 * to the hosted payment page.
 *
 * Styling follows SettingsPage.jsx (max-w-2xl mx-auto, header pattern, card
 * surfaces with rounded-2xl border-border).
 */

import { useEffect, useState, useCallback } from 'react'
import { CreditCard, Users, ArrowUpRight, Loader2, CheckCircle, AlertCircle, ExternalLink, Download, FileText, Gauge } from 'lucide-react'
import {
  fetchBillingStatus,
  fetchBillingTiers,
  createCheckout,
  openBillingPortal,
  fetchInvoices,
  fetchCurrentCycle,
  downloadInvoicePdf,
  FALLBACK_TIERS,
} from '../../lib/ee/billing.js'
import { useOrg } from '../../contexts/OrgContext.jsx'
import { getSlot } from '../registry.js'

// ---------------------------------------------------------------------------
// Formatting helpers
// ---------------------------------------------------------------------------

function zar(amount) {
  const n = Number(amount ?? 0)
  return 'R' + n.toLocaleString('en-ZA', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

const INVOICE_STATUS_STYLES = {
  paid: 'bg-teal-100 text-teal-700 dark:bg-teal-900/40 dark:text-teal-300',
  pending: 'bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-300',
  past_due: 'bg-red-100 text-red-700 dark:bg-red-900/40 dark:text-red-300',
  draft: 'bg-surface-2 text-muted',
  void: 'bg-surface-2 text-muted line-through',
}

// ---------------------------------------------------------------------------
// Current-cycle usage card — usage vs quota + projected overage (dry-run)
// ---------------------------------------------------------------------------

const CYCLE_DIMS = [
  { key: 'compute_units', limit: 'max_compute_units_per_month', label: 'Compute units', unit: 'CU' },
  { key: 'ai_calls', limit: 'max_ai_calls_per_month', label: 'AI calls', unit: '' },
  { key: 'embedded_sessions', limit: 'max_embedded_sessions_per_month', label: 'Embedded sessions', unit: '' },
  { key: 'storage_gb', limit: 'max_storage_gb', label: 'Storage', unit: 'GB' },
  { key: 'agent_runs', limit: 'max_agent_runs_per_month', label: 'Agent runs', unit: '' },
]

function UsageBar({ label, used, limit, unit }) {
  const unlimited = limit == null
  const pct = unlimited || !limit ? 0 : Math.min(100, (used / limit) * 100)
  const over = !unlimited && used > limit
  return (
    <div className="space-y-1">
      <div className="flex items-center justify-between text-xs">
        <span className="text-fg font-medium">{label}</span>
        <span className={over ? 'text-amber-600 dark:text-amber-400 font-semibold' : 'text-muted'}>
          {used.toLocaleString('en-ZA')}{unit ? ' ' + unit : ''}
          {unlimited ? ' / ∞' : ` / ${limit.toLocaleString('en-ZA')}`}
        </span>
      </div>
      {!unlimited && (
        <div className="h-1.5 rounded-full bg-surface-2 overflow-hidden">
          <div
            className={`h-full rounded-full ${over ? 'bg-amber-500' : 'bg-accent'}`}
            style={{ width: `${over ? 100 : pct}%` }}
          />
        </div>
      )}
    </div>
  )
}

function CurrentCycleCard({ cycle }) {
  if (!cycle) return null
  const overage = Number(cycle.overage_total_zar ?? 0)
  return (
    <section className="rounded-2xl border border-border bg-surface p-6 space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="font-display font-semibold text-lg text-fg flex items-center gap-2">
          <Gauge size={17} className="text-accent" /> This billing cycle
        </h2>
        <span className="text-xs text-muted capitalize">{cycle.tier} plan</span>
      </div>
      <div className="grid gap-3">
        {CYCLE_DIMS.map((d) => (
          <UsageBar
            key={d.key}
            label={d.label}
            used={Number(cycle.usage?.[d.key] ?? 0)}
            limit={cycle.limits?.[d.limit]}
            unit={d.unit}
          />
        ))}
        {Number(cycle.usage?.warehouse_cu ?? 0) > 0 && (
          <p className="text-[11px] text-muted -mt-1.5 pl-0.5">
            of which warehouse queries (billed at 4×):{' '}
            {Number(cycle.usage.warehouse_cu).toLocaleString('en-ZA')} CU
          </p>
        )}
      </div>
      <div className="flex items-center justify-between border-t border-border pt-3 text-sm">
        <span className="text-muted">Projected overage this cycle</span>
        <span className={overage > 0 ? 'font-semibold text-fg' : 'text-muted'}>{zar(overage)}</span>
      </div>
      <p className="text-[11px] text-muted">
        Overages draw from your usage wallet first; anything beyond that is added to your
        next monthly invoice. Subscription base: {zar(cycle.monthly_price_zar)} / mo.
      </p>
    </section>
  )
}

// ---------------------------------------------------------------------------
// Invoices card — history with PDF download
// ---------------------------------------------------------------------------

function InvoicesCard({ invoices, downloading, onDownload }) {
  return (
    <section className="rounded-2xl border border-border bg-surface p-6 space-y-4">
      <h2 className="font-display font-semibold text-lg text-fg flex items-center gap-2">
        <FileText size={17} className="text-accent" /> Invoices
      </h2>
      {invoices.length === 0 ? (
        <p className="text-sm text-muted">No invoices yet — your first invoice appears after your first billing cycle.</p>
      ) : (
        <ul className="divide-y divide-border -my-1">
          {invoices.map((inv) => (
            <li key={inv.id} className="flex items-center gap-3 py-2.5">
              <div className="flex-1 min-w-0">
                <p className="text-sm font-medium text-fg font-mono truncate">{inv.invoice_number}</p>
                <p className="text-xs text-muted">
                  {new Date(inv.issued_at).toLocaleDateString('en-ZA')} · {zar(inv.total_zar)}
                  {Number(inv.vat_amount_zar) > 0 && <span className="text-muted/70"> (incl. VAT)</span>}
                </p>
              </div>
              <span className={`shrink-0 px-2 py-0.5 rounded-full text-[10px] font-semibold uppercase tracking-wide ${INVOICE_STATUS_STYLES[inv.status] ?? INVOICE_STATUS_STYLES.draft}`}>
                {inv.status?.replace('_', ' ')}
              </span>
              <button
                onClick={() => onDownload(inv)}
                disabled={downloading === inv.id}
                title="Download PDF"
                className="shrink-0 inline-flex items-center justify-center w-8 h-8 rounded-lg text-muted hover:text-fg hover:bg-surface-2 transition-colors disabled:opacity-50"
              >
                {downloading === inv.id ? <Loader2 size={15} className="animate-spin" /> : <Download size={15} />}
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}

// ---------------------------------------------------------------------------
// Status badge
// ---------------------------------------------------------------------------

const TIER_LABELS = {
  free: 'Community',
  pro: 'Pro',
  enterprise: 'Enterprise',
}

function TierBadge({ tier }) {
  const styles = {
    free: 'bg-surface-2 text-muted',
    pro: 'bg-indigo-100 text-indigo-700 dark:bg-indigo-900/40 dark:text-indigo-300',
    enterprise: 'bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-300',
  }
  return (
    <span
      className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-semibold uppercase tracking-wide ${styles[tier] ?? styles.free}`}
    >
      {TIER_LABELS[tier] ?? tier}
    </span>
  )
}

// ---------------------------------------------------------------------------
// Current plan card
// ---------------------------------------------------------------------------

function CurrentPlanCard({ status, onManage, managing }) {
  const seatText =
    status.seats_limit != null
      ? `${status.seats_used} / ${status.seats_limit} seats`
      : `${status.seats_used} seats`

  return (
    <section className="rounded-2xl border border-border bg-surface p-6 space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="font-display font-semibold text-lg text-fg">Current plan</h2>
        <TierBadge tier={status.tier} />
      </div>

      <div className="flex items-center gap-3 text-sm text-muted">
        <Users size={15} />
        <span>{seatText}</span>
        {status.renewal_date && (
          <>
            <span className="text-border">·</span>
            <span>Renews {new Date(status.renewal_date).toLocaleDateString('en-ZA')}</span>
          </>
        )}
        {status.trial_ends_at && (
          <>
            <span className="text-border">·</span>
            <span className="text-amber-600 dark:text-amber-400">
              Trial ends {new Date(status.trial_ends_at).toLocaleDateString('en-ZA')}
            </span>
          </>
        )}
      </div>

      {status.tier !== 'free' && (
        <button
          onClick={onManage}
          disabled={managing}
          className="inline-flex items-center gap-2 text-sm font-medium text-accent hover:underline disabled:opacity-50"
        >
          {managing ? <Loader2 size={14} className="animate-spin" /> : <ExternalLink size={14} />}
          Manage billing
        </button>
      )}
    </section>
  )
}

// ---------------------------------------------------------------------------
// Pricing tier card
// ---------------------------------------------------------------------------

function TierCard({ tier, currentTier, onUpgrade, loading }) {
  const isCurrent = tier.id === currentTier
  const isContact = tier.id === 'enterprise'

  function handleCta() {
    if (isContact) {
      window.open('mailto:hello@nubi.dev?subject=Enterprise%20enquiry', '_blank')
      return
    }
    if (!isCurrent) onUpgrade(tier.id)
  }

  return (
    <div
      className={`relative flex flex-col rounded-2xl border p-6 gap-4 transition-shadow ${
        tier.highlight
          ? 'border-accent bg-surface shadow-lg shadow-accent/10'
          : 'border-border bg-surface'
      }`}
    >
      {tier.highlight && (
        <span className="absolute -top-3 left-1/2 -translate-x-1/2 bg-accent text-white text-[10px] font-bold uppercase tracking-widest px-3 py-1 rounded-full">
          Most popular
        </span>
      )}

      <div>
        <p className="font-display font-semibold text-lg text-fg">{tier.name}</p>
        <p className="text-2xl font-bold text-fg mt-1">{tier.price_label}</p>
        <p className="text-sm text-muted mt-1">{tier.description}</p>
      </div>

      <ul className="space-y-2 flex-1">
        {tier.features.map((f) => (
          <li key={f} className="flex items-start gap-2 text-sm text-fg">
            <CheckCircle size={14} className="mt-0.5 shrink-0 text-teal-500" />
            {f}
          </li>
        ))}
      </ul>

      <button
        onClick={handleCta}
        disabled={isCurrent || (loading === tier.id)}
        className={`w-full flex items-center justify-center gap-2 px-4 py-2.5 rounded-xl text-sm font-semibold transition-colors ${
          isCurrent
            ? 'bg-surface-2 text-muted cursor-default'
            : tier.highlight
              ? 'bg-accent text-white hover:bg-accent/90'
              : 'bg-surface-2 text-fg border border-border hover:bg-surface-3'
        } disabled:opacity-60`}
      >
        {loading === tier.id ? (
          <Loader2 size={15} className="animate-spin" />
        ) : isCurrent ? (
          'Current plan'
        ) : (
          <>
            {tier.cta_label}
            {!isContact && <ArrowUpRight size={15} />}
          </>
        )}
      </button>
    </div>
  )
}

// ---------------------------------------------------------------------------
// BillingPage
// ---------------------------------------------------------------------------

export default function BillingPage() {
  // Resolve wallet slots at render time (only populated when EE is loaded)
  const WalletPanelSlot = getSlot('wallet-panel')
  const AutoTopupSettingsSlot = getSlot('autotopup-settings')

  const [status, setStatus] = useState(null)
  const [tiers, setTiers] = useState(FALLBACK_TIERS)
  const [loadingStatus, setLoadingStatus] = useState(true)
  const [statusError, setStatusError] = useState(null)
  const [upgradeLoading, setUpgradeLoading] = useState(null)
  const [upgradeError, setUpgradeError] = useState(null)
  const [managing, setManaging] = useState(false)
  // Auto-topup config is loaded by WalletPanel; we thread it to AutoTopupSettings
  const [walletConfig, setWalletConfig] = useState(null)

  // Invoices + current-cycle projection (org_id-scoped).
  const { activeOrg } = useOrg()
  const orgId = activeOrg?.id ?? null
  const [invoices, setInvoices] = useState([])
  const [cycle, setCycle] = useState(null)
  const [downloading, setDownloading] = useState(null)

  // URL param feedback (returned from Paystack)
  const params = new URLSearchParams(typeof window !== 'undefined' ? window.location.search : '')
  const checkoutStatus = params.get('status') // 'success' | 'cancelled'

  // Load status + tiers in parallel
  useEffect(() => {
    let cancelled = false

    Promise.all([fetchBillingStatus(), fetchBillingTiers()])
      .then(([s, t]) => {
        if (cancelled) return
        setStatus(s)
        setTiers(t)
        setLoadingStatus(false)
      })
      .catch((err) => {
        if (cancelled) return
        setStatusError(err?.message ?? 'Failed to load billing information.')
        setLoadingStatus(false)
        // Still show tiers even if status load fails
        fetchBillingTiers().then((t) => { if (!cancelled) setTiers(t) }).catch(() => {})
      })

    return () => { cancelled = true }
  }, [])

  // Load invoices + current-cycle projection once we know the active org.
  const loadInvoices = useCallback(() => {
    if (!orgId) return
    fetchInvoices(orgId).then((r) => setInvoices(r?.invoices ?? [])).catch(() => {})
    fetchCurrentCycle(orgId).then(setCycle).catch(() => {})
  }, [orgId])

  useEffect(() => { loadInvoices() }, [loadInvoices])

  const handleDownloadInvoice = useCallback(async (inv) => {
    if (!orgId) return
    setDownloading(inv.id)
    try {
      await downloadInvoicePdf(orgId, inv.id, inv.pdf_filename || `${inv.invoice_number}.pdf`)
    } catch (err) {
      setUpgradeError(err?.message ?? 'Failed to download invoice.')
    } finally {
      setDownloading(null)
    }
  }, [orgId])

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

  return (
    <div className="max-w-2xl mx-auto px-6 py-8 space-y-8">
      {/* Page header */}
      <header className="flex items-center gap-3">
        <div
          className="flex items-center justify-center w-11 h-11 rounded-2xl shrink-0"
          style={{ background: 'linear-gradient(135deg, #1b2363, #2456a6, #17b3a3)' }}
        >
          <CreditCard size={22} className="text-white" />
        </div>
        <div>
          <h1 className="font-display font-semibold text-2xl text-fg">Billing</h1>
          <p className="text-muted text-sm">Manage your plan, seats, and invoices.</p>
        </div>
      </header>

      {/* Checkout feedback banner */}
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

      {/* Current plan */}
      {loadingStatus ? (
        <div className="flex items-center gap-3 text-muted text-sm py-4">
          <Loader2 size={16} className="animate-spin" />
          Loading billing status…
        </div>
      ) : statusError ? (
        <div className="flex items-center gap-3 rounded-xl bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 px-4 py-3 text-sm text-red-800 dark:text-red-200">
          <AlertCircle size={16} className="shrink-0" />
          {statusError}
        </div>
      ) : status ? (
        <CurrentPlanCard status={status} onManage={handleManage} managing={managing} />
      ) : null}

      {/* Current cycle usage vs quota + projected overage */}
      {cycle && <CurrentCycleCard cycle={cycle} />}

      {/* Pricing tiers */}
      <section className="space-y-4">
        <h2 className="font-display font-semibold text-lg text-fg">Plans</h2>
        <div className="grid gap-4 sm:grid-cols-1 md:grid-cols-3">
          {tiers.map((tier) => (
            <TierCard
              key={tier.id}
              tier={tier}
              currentTier={status?.tier ?? 'free'}
              onUpgrade={handleUpgrade}
              loading={upgradeLoading}
            />
          ))}
        </div>
      </section>

      {/* Wallet panel — prepaid balance, ledger, manual topup (EE only) */}
      {WalletPanelSlot && (
        <section className="space-y-4">
          <h2 className="font-display font-semibold text-lg text-fg">Usage Wallet</h2>
          <WalletPanelSlot
            onConfigLoaded={(cfg) => setWalletConfig(cfg)}
          />
        </section>
      )}

      {/* Auto-topup settings — threshold, amount, caps (EE only) */}
      {AutoTopupSettingsSlot && (
        <section className="space-y-4">
          <h2 className="font-display font-semibold text-lg text-fg">Auto Top-up Settings</h2>
          <AutoTopupSettingsSlot
            config={walletConfig}
            onSaved={(cfg) => setWalletConfig(cfg)}
          />
        </section>
      )}

      {/* Invoices — history with PDF download */}
      <InvoicesCard
        invoices={invoices}
        downloading={downloading}
        onDownload={handleDownloadInvoice}
      />

      {/* Footer note */}
      <p className="text-xs text-muted border-t border-border pt-4">
        Pricing in South African Rand (ZAR). Billed monthly via Paystack. VAT may apply.
        Enterprise pricing on request —{' '}
        <a
          href="mailto:hello@nubi.dev"
          className="underline hover:text-fg transition-colors"
        >
          contact sales
        </a>
        .
      </p>
    </div>
  )
}
