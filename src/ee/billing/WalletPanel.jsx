/**
 * WalletPanel.jsx — prepaid usage-credit wallet panel (src/ee/billing/WalletPanel.jsx)
 *
 * Shows:
 *   - Current balance in ZAR (primary) + USD (secondary)
 *   - Spend-this-month vs monthly spend cap progress bar
 *   - Usage breakdown by category (AI, storage, compute, embedded sessions, overage)
 *   - Recent ledger entries (last 10)
 *   - "Add credit" button → manual top-up flow via Paystack
 *
 * Slotted into BillingPage via registerBilling.js (wallet-panel slot).
 * Degrades gracefully on API failure (skeleton → error state).
 *
 * Styling matches BillingPage.jsx: rounded-2xl border border-border bg-surface,
 * font-display headings, text-muted secondaries, accent CTA button.
 */

import { useEffect, useState, useCallback } from 'react'
import {
  Wallet,
  TrendingDown,
  Plus,
  Loader2,
  AlertCircle,
  RefreshCcw,
  ArrowUpRight,
  ArrowDownLeft,
  Info,
} from 'lucide-react'
import {
  getWallet,
  manualTopup,
  formatUsd,
  formatZarCents,
  centsToUsd,
  usdToCents,
  ENTRY_META,
} from '../../lib/ee/wallet.js'

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/**
 * Format an ISO timestamp as a short locale date+time string.
 * @param {string} iso
 * @returns {string}
 */
function fmtDateTime(iso) {
  if (!iso) return ''
  try {
    return new Date(iso).toLocaleString('en-ZA', {
      day: 'numeric', month: 'short', year: 'numeric',
      hour: '2-digit', minute: '2-digit',
    })
  } catch {
    return iso
  }
}

/**
 * Compute a progress fraction (0–1) for the spend meter.
 * @param {number} spentCents
 * @param {number | null} capCents
 * @returns {number}
 */
function spendFraction(spentCents, capCents) {
  if (!capCents || capCents <= 0) return 0
  return Math.min(spentCents / capCents, 1)
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

/**
 * Colour-coded balance amount display.
 */
function BalanceDisplay({ balanceUsdCents, balanceZarCents }) {
  const low = balanceUsdCents != null && balanceUsdCents < 500  // < $5
  const zero = balanceUsdCents != null && balanceUsdCents <= 0

  const zarLabel = balanceZarCents != null
    ? formatZarCents(balanceZarCents)
    : '—'
  const usdLabel = balanceUsdCents != null
    ? formatUsd(balanceUsdCents)
    : '—'

  return (
    <div className="flex flex-col gap-0.5">
      <span
        className={`text-4xl font-bold tabular-nums ${
          zero ? 'text-red-600 dark:text-red-400'
               : low  ? 'text-amber-600 dark:text-amber-400'
                      : 'text-fg'
        }`}
      >
        {zarLabel}
      </span>
      <span className="text-sm text-muted">{usdLabel}</span>
      {zero && (
        <span className="inline-flex items-center gap-1.5 text-xs font-medium text-red-600 dark:text-red-400 mt-1">
          <AlertCircle size={12} />
          Balance depleted — usage beyond your included quota is paused.
        </span>
      )}
      {!zero && low && (
        <span className="inline-flex items-center gap-1.5 text-xs font-medium text-amber-600 dark:text-amber-400 mt-1">
          <AlertCircle size={12} />
          Low balance — consider topping up.
        </span>
      )}
    </div>
  )
}

/**
 * Spend-this-month vs cap progress bar.
 */
function SpendMeter({ monthSpend, spendCapCents }) {
  const spent = monthSpend?.total_spend_usd_cents ?? 0
  const fraction = spendFraction(spent, spendCapCents)
  const pct = Math.round(fraction * 100)
  const near = fraction >= 0.9

  return (
    <div className="space-y-1.5">
      <div className="flex items-center justify-between text-sm">
        <span className="text-muted">Spend this month</span>
        <span className={`font-medium tabular-nums ${near ? 'text-amber-600 dark:text-amber-400' : 'text-fg'}`}>
          {formatUsd(spent)}
          {spendCapCents ? ` / ${formatUsd(spendCapCents)} cap` : ''}
        </span>
      </div>
      {spendCapCents ? (
        <div className="h-2 rounded-full bg-surface-3 overflow-hidden">
          <div
            className={`h-full rounded-full transition-all duration-500 ${
              near ? 'bg-amber-500' : 'bg-accent'
            }`}
            style={{ width: `${pct}%` }}
          />
        </div>
      ) : (
        <p className="text-xs text-muted/70">No monthly spend cap configured.</p>
      )}
      {near && spendCapCents && (
        <p className="text-xs text-amber-600 dark:text-amber-400">
          Approaching monthly spend cap ({pct}% used).
        </p>
      )}
    </div>
  )
}

/**
 * Usage breakdown by category.
 */
const USAGE_CATEGORIES = [
  { key: 'usage_llm_usd_cents',     label: 'AI / LLM calls',       color: 'bg-violet-400' },
  { key: 'usage_storage_usd_cents', label: 'Storage',              color: 'bg-teal-400'   },
  { key: 'usage_compute_usd_cents', label: 'Compute',              color: 'bg-indigo-400' },
  { key: 'usage_embed_usd_cents',   label: 'Embedded sessions',    color: 'bg-sky-400'    },
  { key: 'usage_overage_usd_cents', label: 'Overage',              color: 'bg-orange-400' },
]

function UsageBreakdown({ monthSpend }) {
  if (!monthSpend) return null

  const total = USAGE_CATEGORIES.reduce((s, c) => s + (monthSpend[c.key] ?? 0), 0)

  if (total === 0) {
    return (
      <p className="text-sm text-muted/70 italic">No usage charged this month.</p>
    )
  }

  return (
    <div className="space-y-2">
      {USAGE_CATEGORIES.map(({ key, label, color }) => {
        const cents = monthSpend[key] ?? 0
        if (!cents) return null
        const frac = total > 0 ? cents / total : 0
        return (
          <div key={key} className="flex items-center gap-2">
            <div className={`w-2.5 h-2.5 rounded-full shrink-0 ${color}`} />
            <span className="text-sm text-fg flex-1">{label}</span>
            <span className="text-sm font-medium tabular-nums text-fg/80">
              {formatUsd(cents)}
            </span>
            <span className="text-xs text-muted/60 w-10 text-right tabular-nums">
              {Math.round(frac * 100)}%
            </span>
          </div>
        )
      })}
    </div>
  )
}

/**
 * Single ledger entry row.
 */
function LedgerRow({ entry }) {
  const meta = ENTRY_META[entry.entry_type] ?? { label: entry.entry_type, sign: 'neutral' }
  const isCredit = meta.sign === 'credit'
  const isDebit  = meta.sign === 'debit'

  return (
    <div className="flex items-start gap-3 py-2.5 border-b border-border/50 last:border-0">
      <div
        className={`flex items-center justify-center w-7 h-7 rounded-full shrink-0 mt-0.5 ${
          isCredit ? 'bg-teal-100 dark:bg-teal-900/40 text-teal-600 dark:text-teal-300'
                   : isDebit ? 'bg-red-100 dark:bg-red-900/30 text-red-500 dark:text-red-400'
                              : 'bg-surface-2 text-muted'
        }`}
      >
        {isCredit ? <ArrowDownLeft size={12} /> : isDebit ? <ArrowUpRight size={12} /> : <Info size={12} />}
      </div>

      <div className="flex-1 min-w-0">
        <p className="text-sm font-medium text-fg truncate">{meta.label}</p>
        {entry.description && (
          <p className="text-xs text-muted truncate">{entry.description}</p>
        )}
        <p className="text-xs text-muted/60 mt-0.5">{fmtDateTime(entry.created_at)}</p>
      </div>

      <div className="text-right shrink-0">
        <p
          className={`text-sm font-semibold tabular-nums ${
            isCredit ? 'text-teal-600 dark:text-teal-400'
                     : isDebit ? 'text-red-600 dark:text-red-400'
                                : 'text-muted'
          }`}
        >
          {isCredit ? '+' : isDebit ? '−' : ''}
          {formatUsd(Math.abs(entry.amount_usd_cents))}
        </p>
        <p className="text-xs text-muted/60 tabular-nums">
          bal {formatUsd(entry.balance_after_usd_cents)}
        </p>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Manual top-up modal / inline form
// ---------------------------------------------------------------------------

const TOPUP_PRESETS_USD = [10, 25, 50, 100, 250]

function TopupForm({ onCancel, onSuccess }) {
  const [amountUsd, setAmountUsd] = useState(50)
  const [custom, setCustom] = useState(false)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)

  async function handleSubmit(e) {
    e.preventDefault()
    const cents = usdToCents(Number(amountUsd))
    if (!cents || cents < 100) {
      setError('Minimum top-up is $1.00.')
      return
    }
    setLoading(true)
    setError(null)
    try {
      const { checkout_url } = await manualTopup(cents)
      window.location.href = checkout_url
      onSuccess?.()
    } catch (err) {
      setError(err?.message ?? 'Failed to start checkout. Please try again.')
      setLoading(false)
    }
  }

  return (
    <form
      onSubmit={handleSubmit}
      className="rounded-2xl border border-border bg-surface-2 p-5 space-y-4"
    >
      <p className="text-sm font-semibold text-fg">Add credit to wallet</p>

      {/* Preset amounts */}
      <div className="flex flex-wrap gap-2">
        {TOPUP_PRESETS_USD.map((v) => (
          <button
            key={v}
            type="button"
            onClick={() => { setAmountUsd(v); setCustom(false) }}
            className={`px-3 py-1.5 rounded-lg text-sm font-medium border transition-colors ${
              !custom && amountUsd === v
                ? 'bg-accent text-white border-accent'
                : 'bg-surface border-border text-fg hover:bg-surface-3'
            }`}
          >
            ${v}
          </button>
        ))}
        <button
          type="button"
          onClick={() => setCustom(true)}
          className={`px-3 py-1.5 rounded-lg text-sm font-medium border transition-colors ${
            custom
              ? 'bg-accent text-white border-accent'
              : 'bg-surface border-border text-fg hover:bg-surface-3'
          }`}
        >
          Custom
        </button>
      </div>

      {/* Custom amount input */}
      {custom && (
        <div className="flex items-center gap-2">
          <span className="text-sm font-medium text-fg">$</span>
          <input
            type="number"
            min="1"
            max="10000"
            step="1"
            value={amountUsd}
            onChange={(e) => setAmountUsd(e.target.value)}
            className="w-32 px-3 py-1.5 rounded-lg border border-border bg-surface text-sm text-fg focus:outline-none focus:ring-2 focus:ring-accent/40"
            autoFocus
          />
          <span className="text-xs text-muted">USD</span>
        </div>
      )}

      <p className="text-xs text-muted/70">
        You will be redirected to Paystack to complete the payment.
        The ZAR equivalent is charged using the current exchange rate.
      </p>

      {error && (
        <p className="text-xs text-red-600 dark:text-red-400 flex items-center gap-1.5">
          <AlertCircle size={12} />
          {error}
        </p>
      )}

      <div className="flex items-center gap-3">
        <button
          type="submit"
          disabled={loading}
          className="inline-flex items-center gap-2 px-4 py-2 rounded-xl text-sm font-semibold bg-accent text-white hover:bg-accent/90 disabled:opacity-60 transition-colors"
        >
          {loading ? <Loader2 size={14} className="animate-spin" /> : <Plus size={14} />}
          {loading ? 'Redirecting…' : `Add $${amountUsd}`}
        </button>
        <button
          type="button"
          onClick={onCancel}
          disabled={loading}
          className="text-sm text-muted hover:text-fg transition-colors disabled:opacity-50"
        >
          Cancel
        </button>
      </div>
    </form>
  )
}

// ---------------------------------------------------------------------------
// WalletPanel — main export
// ---------------------------------------------------------------------------

/**
 * @param {{ className?: string }} props
 */
export default function WalletPanel({ className }) {
  const [state, setState] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [showTopup, setShowTopup] = useState(false)

  // URL param feedback (returned from Paystack)
  const params = typeof window !== 'undefined'
    ? new URLSearchParams(window.location.search)
    : new URLSearchParams()
  const walletParam = params.get('wallet')  // 'funded' | 'cancelled'

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const data = await getWallet()
      setState(data)
    } catch (err) {
      setError(err?.message ?? 'Failed to load wallet.')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load() }, [load])

  // ---------------------------------------------------------------------------
  // Render helpers
  // ---------------------------------------------------------------------------

  if (loading) {
    return (
      <section className={`rounded-2xl border border-border bg-surface p-6 ${className ?? ''}`}>
        <div className="flex items-center gap-3 text-muted text-sm">
          <Loader2 size={16} className="animate-spin" />
          Loading wallet…
        </div>
      </section>
    )
  }

  if (error) {
    return (
      <section className={`rounded-2xl border border-red-200 dark:border-red-800 bg-red-50 dark:bg-red-900/10 p-6 ${className ?? ''}`}>
        <div className="flex items-center gap-3 text-sm text-red-700 dark:text-red-300">
          <AlertCircle size={16} className="shrink-0" />
          <span>{error}</span>
          <button
            onClick={load}
            className="ml-auto flex items-center gap-1.5 text-xs font-medium hover:underline"
          >
            <RefreshCcw size={12} />
            Retry
          </button>
        </div>
      </section>
    )
  }

  const balance   = state?.balance   ?? {}
  const config    = state?.config    ?? {}
  const month     = state?.month_spend ?? {}
  const ledger    = state?.ledger    ?? []

  return (
    <section className={`rounded-2xl border border-border bg-surface p-6 space-y-6 ${className ?? ''}`}>
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div
            className="flex items-center justify-center w-10 h-10 rounded-xl shrink-0"
            style={{ background: 'linear-gradient(135deg, #17b3a3, #2456a6)' }}
          >
            <Wallet size={18} className="text-white" />
          </div>
          <div>
            <h2 className="font-display font-semibold text-base text-fg">Usage Wallet</h2>
            <p className="text-xs text-muted">Prepaid credits drawn down by metered usage</p>
          </div>
        </div>
        <button
          onClick={load}
          className="p-1.5 rounded-lg text-muted hover:text-fg hover:bg-surface-2 transition-colors"
          title="Refresh"
        >
          <RefreshCcw size={14} />
        </button>
      </div>

      {/* Paystack return banners */}
      {walletParam === 'funded' && (
        <div className="flex items-center gap-3 rounded-xl bg-teal-50 dark:bg-teal-900/20 border border-teal-200 dark:border-teal-800 px-4 py-3 text-sm text-teal-800 dark:text-teal-200">
          <TrendingDown size={15} className="shrink-0 rotate-180" />
          Payment successful — your wallet has been topped up.
        </div>
      )}
      {walletParam === 'cancelled' && (
        <div className="flex items-center gap-3 rounded-xl bg-amber-50 dark:bg-amber-900/20 border border-amber-200 dark:border-amber-800 px-4 py-3 text-sm text-amber-800 dark:text-amber-200">
          <AlertCircle size={15} className="shrink-0" />
          Top-up cancelled — no charge was made.
        </div>
      )}

      {/* Balance + Add credit */}
      <div className="flex items-end justify-between gap-4 flex-wrap">
        <BalanceDisplay
          balanceUsdCents={balance.balance_usd_cents ?? null}
          balanceZarCents={balance.balance_zar_cents ?? null}
        />
        {!showTopup && (
          <button
            onClick={() => setShowTopup(true)}
            className="inline-flex items-center gap-2 px-4 py-2 rounded-xl text-sm font-semibold bg-accent text-white hover:bg-accent/90 transition-colors shrink-0"
          >
            <Plus size={14} />
            Add credit
          </button>
        )}
      </div>

      {/* Manual top-up form */}
      {showTopup && (
        <TopupForm
          onCancel={() => setShowTopup(false)}
          onSuccess={() => setShowTopup(false)}
        />
      )}

      {/* Spend meter */}
      <SpendMeter
        monthSpend={month}
        spendCapCents={config.spend_cap_usd_cents ?? null}
      />

      {/* Usage breakdown */}
      <div className="space-y-2">
        <h3 className="text-xs font-semibold uppercase tracking-wider text-muted">
          Usage this month
        </h3>
        <UsageBreakdown monthSpend={month} />
      </div>

      {/* FX note */}
      {balance.last_fx_rate && (
        <p className="text-xs text-muted/70">
          ZAR balance calculated at{' '}
          <span className="font-mono font-medium">
            1 USD = R {Number(balance.last_fx_rate).toFixed(2)}
          </span>
          {balance.last_fx_at && (
            <> (updated {new Date(balance.last_fx_at).toLocaleDateString('en-ZA')})</>
          )}
          .
        </p>
      )}

      {/* Recent ledger */}
      {ledger.length > 0 && (
        <div className="space-y-2">
          <h3 className="text-xs font-semibold uppercase tracking-wider text-muted">
            Recent transactions
          </h3>
          <div>
            {ledger.slice(0, 10).map((entry) => (
              <LedgerRow key={entry.id} entry={entry} />
            ))}
          </div>
        </div>
      )}
    </section>
  )
}
