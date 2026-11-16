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
 * Swiss: a hairline panel under a 2px ink rule, a mono tabular balance, square
 * status dots, an ink primary button. Semantic colour is a 6px square dot plus text.
 */

import { useEffect, useState, useCallback, useRef, useId } from 'react'
import { Plus, RefreshCcw } from 'lucide-react'
import Figure from '../../components/marketing/Figure.jsx'
import {
  getWallet,
  manualTopup,
  formatUsd,
  formatZarCents,
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
    <div className="flex flex-col gap-1">
      <span className="t-label">Balance</span>
      <span className="font-mono text-4xl font-medium tabular-nums text-fg tracking-tight">{zarLabel}</span>
      <span className="font-mono text-sm text-muted tabular-nums">{usdLabel}</span>
      {zero && (
        <span className="mt-1.5 inline-flex items-center gap-2 text-xs text-fg">
          <i aria-hidden="true" className="inline-block h-1.5 w-1.5 shrink-0" style={{ background: 'var(--danger)' }} />
          Balance depleted. Usage beyond your included quota is paused.
        </span>
      )}
      {!zero && low && (
        <span className="mt-1.5 inline-flex items-center gap-2 text-xs text-fg">
          <i aria-hidden="true" className="inline-block h-1.5 w-1.5 shrink-0" style={{ background: 'var(--warning)' }} />
          Low balance. Consider topping up.
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
    <div className="space-y-2">
      <div className="flex items-baseline justify-between gap-3 text-sm">
        <span className="t-label">Spend this month</span>
        <span className="font-mono tabular-nums text-fg">
          {formatUsd(spent)}
          {spendCapCents ? ` / ${formatUsd(spendCapCents)} cap` : ''}
        </span>
      </div>
      {spendCapCents ? (
        <div
          className="h-1.5 bg-surface-2"
          role="progressbar"
          aria-label="Spend against monthly cap"
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={pct}
        >
          <div className="h-full" style={{ width: `${pct}%`, background: near ? 'var(--warning)' : 'var(--ink)' }} />
        </div>
      ) : (
        <p className="text-xs text-muted">No monthly spend cap configured.</p>
      )}
      {near && spendCapCents && (
        <p className="inline-flex items-center gap-2 text-xs text-fg">
          <i aria-hidden="true" className="inline-block h-1.5 w-1.5 shrink-0" style={{ background: 'var(--warning)' }} />
          Approaching the monthly spend cap ({pct}% used).
        </p>
      )}
    </div>
  )
}

/**
 * Usage breakdown by category.
 */
const USAGE_CATEGORIES = [
  { key: 'usage_llm_usd_cents',     label: 'AI / LLM calls',    color: 'var(--viz-1)' },
  { key: 'usage_storage_usd_cents', label: 'Storage',           color: 'var(--viz-2)' },
  { key: 'usage_compute_usd_cents', label: 'Compute',           color: 'var(--viz-3)' },
  { key: 'usage_embed_usd_cents',   label: 'Embedded sessions', color: 'var(--viz-6)' },
  { key: 'usage_overage_usd_cents', label: 'Overage',           color: 'var(--viz-5)' },
]

function UsageBreakdown({ monthSpend }) {
  if (!monthSpend) return null

  const total = USAGE_CATEGORIES.reduce((s, c) => s + (monthSpend[c.key] ?? 0), 0)

  if (total === 0) {
    return (
      <p className="text-sm text-muted">No usage charged this month.</p>
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
            <div className="h-2 w-2 shrink-0" style={{ background: color }} aria-hidden="true" />
            <span className="text-sm text-fg flex-1">{label}</span>
            <span className="font-mono text-sm tabular-nums text-fg">
              {formatUsd(cents)}
            </span>
            <span className="font-mono text-xs text-muted w-10 text-right tabular-nums">
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
    <div className="flex items-start gap-3 py-2.5 border-b border-border last:border-0">
      <i
        aria-hidden="true"
        className="mt-[7px] inline-block h-1.5 w-1.5 shrink-0"
        style={{ background: isCredit ? 'var(--success)' : isDebit ? 'var(--ink)' : 'var(--border-[var(--border-control,var(--text-muted))], var(--text-muted))' }}
      />

      <div className="flex-1 min-w-0">
        <p className="text-sm font-medium text-fg truncate">{meta.label}</p>
        {entry.description && (
          <p className="text-xs text-muted truncate">{entry.description}</p>
        )}
        <p className="font-mono text-[11px] text-muted mt-0.5">{fmtDateTime(entry.created_at)}</p>
      </div>

      <div className="text-right shrink-0">
        <p className="font-mono text-sm font-medium tabular-nums text-fg">
          {isCredit ? '+' : isDebit ? '−' : ''}
          {formatUsd(Math.abs(entry.amount_usd_cents))}
        </p>
        <p className="font-mono text-[11px] text-muted tabular-nums">
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
  const [amountUsd, setAmountUsd] = useState<string | number>(50)
  const [custom, setCustom] = useState(false)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)

  // Accessible-dialog wiring: a11y APG "non-modal dialog" pattern — the form
  // isn't a full-screen overlay (no redesign), but it IS a self-contained
  // task ("add credit") that appears/disappears, so it gets role="dialog",
  // a labelled heading, focus-on-open, and Escape-to-close like a real dialog.
  const headingId = `topup-heading-${useId()}`
  const customInputId = `topup-custom-amount-${useId()}`
  const headingRef = useRef(null)

  useEffect(() => {
    // Move focus into the dialog when it mounts (i.e. when the user opens it)
    // so keyboard/screen-reader users land on the new content immediately.
    headingRef.current?.focus()
  }, [])

  function handleKeyDown(e) {
    if (e.key === 'Escape' && !loading) {
      e.stopPropagation()
      onCancel?.()
    }
  }

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
      onKeyDown={handleKeyDown}
      role="dialog"
      aria-labelledby={headingId}
      className="border border-border bg-surface-2 p-5 space-y-4"
    >
      <h3
        id={headingId}
        ref={headingRef}
        tabIndex={-1}
        className="text-sm font-semibold text-fg focus:outline-none"
      >
        Add credit to wallet
      </h3>

      {/* Preset amounts */}
      <div className="flex flex-wrap gap-2" role="group" aria-label="Top-up amount presets">
        {TOPUP_PRESETS_USD.map((v) => (
          <button
            key={v}
            type="button"
            aria-pressed={!custom && amountUsd === v}
            onClick={() => { setAmountUsd(v); setCustom(false) }}
            className={`px-3 py-1.5 rounded-sm font-mono text-sm tabular-nums border transition-colors ${
              !custom && amountUsd === v
                ? 'bg-[var(--ink)] text-[var(--ink-fg)] border-[var(--ink)]'
                : 'bg-surface border-[var(--frame-line)] text-fg hover:border-[var(--ink)]'
            }`}
          >
            ${v}
          </button>
        ))}
        <button
          type="button"
          aria-pressed={custom}
          onClick={() => setCustom(true)}
          className={`px-3 py-1.5 rounded-sm text-sm font-medium border transition-colors ${
            custom
              ? 'bg-[var(--ink)] text-[var(--ink-fg)] border-[var(--ink)]'
              : 'bg-surface border-[var(--frame-line)] text-fg hover:border-[var(--ink)]'
          }`}
        >
          Custom
        </button>
      </div>

      {/* Custom amount input */}
      {custom && (
        <div className="flex items-center gap-2">
          <label htmlFor={customInputId} className="text-sm font-medium text-fg" aria-hidden="true">$</label>
          <input
            id={customInputId}
            type="number"
            min="1"
            max="10000"
            step="1"
            value={amountUsd}
            onChange={(e) => setAmountUsd(e.target.value)}
            aria-label="Custom top-up amount in US dollars"
            className="w-32 px-3 py-1.5 rounded-sm border border-[var(--border-control,var(--text-muted))] bg-surface font-mono text-sm tabular-nums text-fg focus:border-ring"
            autoFocus
          />
          <span className="text-xs text-muted">USD</span>
        </div>
      )}

      <p className="text-xs text-muted">
        You will be redirected to Paystack to complete the payment.
        The ZAR equivalent is charged using the current exchange rate.
      </p>

      {error && (
        <p role="alert" className="text-xs text-fg flex items-center gap-2">
          <i aria-hidden="true" className="inline-block h-1.5 w-1.5 shrink-0" style={{ background: 'var(--danger)' }} />
          {error}
        </p>
      )}

      <div className="flex items-center gap-3">
        <button
          type="submit"
          disabled={loading}
          aria-busy={loading}
          className="nubi-btn nubi-btn-primary nubi-btn-md"
        >
          {loading ? <span className="nubi-spinner nubi-spinner-sm" aria-hidden="true" /> : <Plus size={14} strokeWidth={1.75} aria-hidden="true" />}
          {loading ? 'Redirecting…' : `Add $${amountUsd}`}
        </button>
        <button
          type="button"
          onClick={onCancel}
          disabled={loading}
          className="nubi-btn nubi-btn-ghost nubi-btn-md"
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
      <section aria-busy="true" className={`border border-border border-t-2 border-t-[var(--ink)] bg-surface p-6 ${className ?? ''}`}>
        <div className="flex items-center gap-3 text-muted text-sm" aria-live="polite">
          <span className="nubi-spinner nubi-spinner-sm" aria-hidden="true" />
          Loading wallet…
        </div>
      </section>
    )
  }

  if (error) {
    return (
      <section className={`border border-border border-t-2 border-t-[var(--ink)] bg-surface p-6 ${className ?? ''}`}>
        <div role="alert" className="flex items-center gap-3 text-sm text-fg">
          <i aria-hidden="true" className="inline-block h-1.5 w-1.5 shrink-0" style={{ background: 'var(--danger)' }} />
          <span>{error}</span>
          <button
            onClick={load}
            className="nubi-btn nubi-btn-secondary nubi-btn-sm ml-auto"
          >
            <RefreshCcw size={12} strokeWidth={1.75} aria-hidden="true" />
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
    <section className={`border border-border border-t-2 border-t-[var(--ink)] bg-surface p-6 space-y-6 ${className ?? ''}`}>
      {/* Header */}
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0">
          <h2 className="font-display font-semibold text-base text-fg">Usage wallet</h2>
          <p className="text-xs text-muted mt-0.5">Prepaid credit, drawn down by metered usage.</p>
        </div>
        <div className="flex items-center gap-3 shrink-0">
          <div className="hidden w-36 sm:block" aria-hidden="true">
            <Figure name="MeteringWallet" compact className="w-full h-auto" />
          </div>
          <button
            onClick={load}
            className="nubi-btn nubi-btn-ghost nubi-btn-icon-sm"
            aria-label="Refresh wallet"
            title="Refresh"
          >
            <RefreshCcw size={14} strokeWidth={1.75} aria-hidden="true" />
          </button>
        </div>
      </div>

      {/* Paystack return banners */}
      {walletParam === 'funded' && (
        <div role="status" className="nubi-notice nubi-notice-success">
          Payment successful. Your wallet has been topped up.
        </div>
      )}
      {walletParam === 'cancelled' && (
        <div role="status" className="nubi-notice nubi-notice-warning">
          Top-up cancelled. No charge was made.
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
            aria-expanded={showTopup}
            aria-controls="topup-form-region"
            className="nubi-btn nubi-btn-primary nubi-btn-md shrink-0"
          >
            <Plus size={14} strokeWidth={1.75} aria-hidden="true" />
            Add credit
          </button>
        )}
      </div>

      {/* Manual top-up form */}
      {showTopup && (
        <div id="topup-form-region">
          <TopupForm
            onCancel={() => setShowTopup(false)}
            onSuccess={() => setShowTopup(false)}
          />
        </div>
      )}

      {/* Spend meter */}
      <SpendMeter
        monthSpend={month}
        spendCapCents={config.spend_cap_usd_cents ?? null}
      />

      {/* Usage breakdown */}
      <div className="space-y-2">
        <h3 className="t-label">
          Usage this month
        </h3>
        <UsageBreakdown monthSpend={month} />
      </div>

      {/* FX note */}
      {balance.last_fx_rate && (
        <p className="text-xs text-muted">
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

      {/* Recent ledger — explicit empty state so the panel never just "goes quiet" */}
      <div className="space-y-2">
        <h3 className="t-label">
          Recent transactions
        </h3>
        {ledger.length > 0 ? (
          <div>
            {ledger.slice(0, 10).map((entry) => (
              <LedgerRow key={entry.id} entry={entry} />
            ))}
          </div>
        ) : (
          <p className="text-sm text-muted">No transactions yet.</p>
        )}
      </div>
    </section>
  )
}
