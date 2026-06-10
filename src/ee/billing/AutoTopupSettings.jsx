/**
 * AutoTopupSettings.jsx — auto-topup configuration panel (src/ee/billing/AutoTopupSettings.jsx)
 *
 * Lets org admins configure the wallet's auto-reload behaviour:
 *   - Toggle auto-topup on / off
 *   - Set threshold (balance floor that triggers a charge)
 *   - Set top-up amount (credits purchased per trigger)
 *   - Set max monthly auto-topups (soft cap on automatic recharges)
 *   - Set monthly spend cap (hard stop on all metered usage)
 *
 * Shows saved card info (last4, brand, expiry) when a reusable authorization
 * is already on file.  Displays a CTA to add a card via a manual top-up if
 * none is saved.
 *
 * Slotted into BillingPage via registerBilling.js alongside WalletPanel.
 *
 * Props
 * -----
 * config      AutoTopupConfig    Current settings loaded by BillingPage / WalletPanel.
 *                                Passed in so we don't double-fetch.
 * onSaved     (config) => void   Called with the updated config after a successful PUT.
 * className   string             Extra class names applied to the root element.
 */

import { useState, useEffect } from 'react'
import {
  Settings2,
  CreditCard,
  AlertCircle,
  CheckCircle,
  Loader2,
  Info,
} from 'lucide-react'
import { setAutoTopup, formatUsd, centsToUsd, usdToCents } from '../../lib/ee/wallet.js'

// ---------------------------------------------------------------------------
// Small helpers
// ---------------------------------------------------------------------------

/**
 * A labelled number input that works in USD.
 */
function UsdInput({ label, hint, value, onChange, min = 1, max = 100000, disabled }) {
  return (
    <div className="space-y-1.5">
      <label className="block text-sm font-medium text-fg">
        {label}
        {hint && <span className="ml-1.5 text-xs font-normal text-muted">({hint})</span>}
      </label>
      <div className="flex items-center gap-2">
        <span className="text-sm font-medium text-fg">$</span>
        <input
          type="number"
          min={min}
          max={max}
          step="1"
          value={value}
          onChange={(e) => onChange(e.target.value)}
          disabled={disabled}
          className="w-32 px-3 py-1.5 rounded-lg border border-border bg-surface text-sm text-fg focus:outline-none focus:ring-2 focus:ring-accent/40 disabled:opacity-50 disabled:cursor-not-allowed"
        />
        <span className="text-xs text-muted">USD</span>
      </div>
    </div>
  )
}

/**
 * Saved-card chip.
 */
function CardChip({ brand, last4, expMonth, expYear }) {
  if (!last4) return null
  const brandLabel = brand ? brand.charAt(0).toUpperCase() + brand.slice(1) : 'Card'
  return (
    <div className="inline-flex items-center gap-2 px-3 py-1.5 rounded-xl bg-surface-2 border border-border text-sm text-fg">
      <CreditCard size={14} className="text-muted" />
      <span>
        {brandLabel} ending in <span className="font-mono font-semibold">{last4}</span>
        {expMonth && expYear && (
          <span className="text-muted ml-1.5 text-xs">exp {expMonth}/{expYear}</span>
        )}
      </span>
    </div>
  )
}

// ---------------------------------------------------------------------------
// AutoTopupSettings
// ---------------------------------------------------------------------------

/**
 * @param {{
 *   config: import('../../lib/ee/wallet.js').AutoTopupConfig | null,
 *   onSaved?: (config: import('../../lib/ee/wallet.js').AutoTopupConfig) => void,
 *   className?: string,
 * }} props
 */
export default function AutoTopupSettings({ config, onSaved, className }) {
  // Form state — all stored in USD (not cents) for the input fields
  const [enabled, setEnabled]             = useState(false)
  const [threshold, setThreshold]         = useState(10)        // $10
  const [topupAmount, setTopupAmount]     = useState(50)        // $50
  const [monthlyCap, setMonthlyCap]       = useState('')        // '' = unlimited
  const [spendCap, setSpendCap]           = useState('')        // '' = unlimited
  const [hasMonthlyCap, setHasMonthlyCap] = useState(false)
  const [hasSpendCap, setHasSpendCap]     = useState(false)

  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState(null)
  const [savedOk, setSavedOk] = useState(false)

  // Hydrate form from incoming config
  useEffect(() => {
    if (!config) return
    setEnabled(config.auto_topup_enabled ?? false)
    setThreshold(centsToUsd(config.threshold_usd_cents ?? 1000))
    setTopupAmount(centsToUsd(config.topup_amount_usd_cents ?? 5000))

    if (config.monthly_topup_cap_usd_cents != null) {
      setHasMonthlyCap(true)
      setMonthlyCap(centsToUsd(config.monthly_topup_cap_usd_cents))
    } else {
      setHasMonthlyCap(false)
      setMonthlyCap('')
    }

    if (config.spend_cap_usd_cents != null) {
      setHasSpendCap(true)
      setSpendCap(centsToUsd(config.spend_cap_usd_cents))
    } else {
      setHasSpendCap(false)
      setSpendCap('')
    }
  }, [config])

  const hasCard = !!(config?.paystack_auth_reusable && config?.paystack_card_last4)

  async function handleSave(e) {
    e.preventDefault()
    setSaving(true)
    setSaveError(null)
    setSavedOk(false)

    try {
      const payload = {
        auto_topup_enabled:        enabled,
        threshold_usd_cents:       usdToCents(Number(threshold)),
        topup_amount_usd_cents:    usdToCents(Number(topupAmount)),
        monthly_topup_cap_usd_cents: hasMonthlyCap && monthlyCap
          ? usdToCents(Number(monthlyCap))
          : null,
        spend_cap_usd_cents: hasSpendCap && spendCap
          ? usdToCents(Number(spendCap))
          : null,
      }
      const updated = await setAutoTopup(payload)
      setSavedOk(true)
      onSaved?.(updated)
      setTimeout(() => setSavedOk(false), 3000)
    } catch (err) {
      setSaveError(err?.message ?? 'Failed to save settings. Please try again.')
    } finally {
      setSaving(false)
    }
  }

  return (
    <section className={`rounded-2xl border border-border bg-surface p-6 space-y-6 ${className ?? ''}`}>
      {/* Header */}
      <div className="flex items-center gap-3">
        <div
          className="flex items-center justify-center w-10 h-10 rounded-xl shrink-0"
          style={{ background: 'linear-gradient(135deg, #1b2363, #2456a6)' }}
        >
          <Settings2 size={18} className="text-white" />
        </div>
        <div>
          <h2 className="font-display font-semibold text-base text-fg">Auto-topup</h2>
          <p className="text-xs text-muted">
            Automatically charge your saved card when your balance runs low.
          </p>
        </div>
      </div>

      {/* Saved card */}
      {hasCard ? (
        <div className="space-y-1">
          <p className="text-xs text-muted font-medium">Saved payment method</p>
          <CardChip
            brand={config.paystack_card_brand}
            last4={config.paystack_card_last4}
            expMonth={config.paystack_card_exp_month}
            expYear={config.paystack_card_exp_year}
          />
        </div>
      ) : (
        <div className="flex items-start gap-2 rounded-xl bg-amber-50 dark:bg-amber-900/20 border border-amber-200 dark:border-amber-800 px-4 py-3 text-sm text-amber-800 dark:text-amber-200">
          <AlertCircle size={15} className="shrink-0 mt-0.5" />
          <span>
            No saved payment method. Make a{' '}
            <strong>manual top-up</strong> first — your card will be saved
            automatically for future auto-topups.
          </span>
        </div>
      )}

      <form onSubmit={handleSave} className="space-y-5">
        {/* Enable toggle */}
        <div className="flex items-center justify-between">
          <div className="space-y-0.5">
            <p className="text-sm font-medium text-fg">Enable auto-topup</p>
            <p className="text-xs text-muted">
              When enabled, your card is charged automatically when the balance
              falls below the threshold you set.
            </p>
          </div>
          <button
            type="button"
            onClick={() => setEnabled((v) => !v)}
            disabled={!hasCard}
            title={!hasCard ? 'Add a payment method first' : undefined}
            className={`relative inline-flex h-6 w-11 flex-shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors duration-200 focus:outline-none focus:ring-2 focus:ring-accent/40 ${
              enabled && hasCard ? 'bg-accent' : 'bg-surface-3'
            } ${!hasCard ? 'opacity-40 cursor-not-allowed' : ''}`}
            role="switch"
            aria-checked={enabled}
          >
            <span
              className={`pointer-events-none inline-block h-5 w-5 transform rounded-full bg-white shadow ring-0 transition duration-200 ${
                enabled && hasCard ? 'translate-x-5' : 'translate-x-0'
              }`}
            />
          </button>
        </div>

        {/* Threshold + Top-up amount (only meaningful when enabled) */}
        <div className={`space-y-4 transition-opacity ${enabled && hasCard ? '' : 'opacity-40 pointer-events-none'}`}>
          <div className="grid gap-4 sm:grid-cols-2">
            <UsdInput
              label="Balance threshold"
              hint="trigger when below this"
              value={threshold}
              onChange={setThreshold}
              min={1}
              max={10000}
              disabled={!enabled || !hasCard}
            />
            <UsdInput
              label="Top-up amount"
              hint="credits added per trigger"
              value={topupAmount}
              onChange={setTopupAmount}
              min={5}
              max={100000}
              disabled={!enabled || !hasCard}
            />
          </div>

          {/* Monthly auto-topup cap */}
          <div className="space-y-2">
            <div className="flex items-center gap-2">
              <input
                type="checkbox"
                id="enable-monthly-cap"
                checked={hasMonthlyCap}
                onChange={(e) => setHasMonthlyCap(e.target.checked)}
                disabled={!enabled || !hasCard}
                className="rounded border-border accent-accent"
              />
              <label
                htmlFor="enable-monthly-cap"
                className="text-sm font-medium text-fg cursor-pointer"
              >
                Monthly auto-topup cap
              </label>
              <span className="inline-flex items-center" title="Soft cap — limits how much is auto-recharged per calendar month. Manual top-ups are still allowed.">
                <Info size={12} className="text-muted cursor-help" />
              </span>
            </div>
            {hasMonthlyCap && (
              <UsdInput
                label=""
                hint="max auto-topups per month"
                value={monthlyCap}
                onChange={setMonthlyCap}
                min={5}
                max={100000}
                disabled={!enabled || !hasCard}
              />
            )}
            {hasMonthlyCap && (
              <p className="text-xs text-muted/70">
                Auto-topup stops for the rest of the month once this amount has been
                auto-recharged. Manual top-ups are still permitted.
              </p>
            )}
          </div>
        </div>

        {/* Spend cap — independent of auto-topup */}
        <div className="border-t border-border/60 pt-5 space-y-2">
          <div className="flex items-center gap-2">
            <input
              type="checkbox"
              id="enable-spend-cap"
              checked={hasSpendCap}
              onChange={(e) => setHasSpendCap(e.target.checked)}
              className="rounded border-border accent-accent"
            />
            <label htmlFor="enable-spend-cap" className="text-sm font-medium text-fg cursor-pointer">
              Monthly spend cap
            </label>
            <span
              className="inline-flex items-center"
              title="Hard stop — once your cumulative wallet credits consumed this month exceed this value, metered API calls beyond your tier's included quota are refused with an error. Auto-topups also halt."
            >
              <Info size={12} className="text-muted cursor-help" />
            </span>
          </div>
          {hasSpendCap && (
            <UsdInput
              label=""
              hint="hard monthly ceiling"
              value={spendCap}
              onChange={setSpendCap}
              min={1}
              max={100000}
            />
          )}
          {hasSpendCap && (
            <p className="text-xs text-muted/70">
              Once this limit is hit, usage beyond your included tier quota returns an
              error until the start of the next month. Manual top-ups are still allowed.
            </p>
          )}
        </div>

        {/* Feedback */}
        {saveError && (
          <p className="text-xs text-red-600 dark:text-red-400 flex items-center gap-1.5">
            <AlertCircle size={12} />
            {saveError}
          </p>
        )}
        {savedOk && (
          <p className="text-xs text-teal-600 dark:text-teal-400 flex items-center gap-1.5">
            <CheckCircle size={12} />
            Settings saved.
          </p>
        )}

        {/* Save button */}
        <div className="flex items-center gap-3 pt-1">
          <button
            type="submit"
            disabled={saving}
            className="inline-flex items-center gap-2 px-4 py-2 rounded-xl text-sm font-semibold bg-accent text-white hover:bg-accent/90 disabled:opacity-60 transition-colors"
          >
            {saving ? <Loader2 size={14} className="animate-spin" /> : null}
            {saving ? 'Saving…' : 'Save settings'}
          </button>
        </div>
      </form>
    </section>
  )
}
