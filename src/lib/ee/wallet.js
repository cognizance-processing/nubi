/**
 * wallet.js — EE wallet API client (src/lib/ee/wallet.js)
 *
 * Thin wrappers around the EE billing wallet endpoints at /api/v1/ee/billing/wallet/*.
 * This file lives in src/lib/ee/ (EE territory) and is ONLY imported by EE
 * components (WalletPanel, AutoTopupSettings).  Core must never import it.
 *
 * API surface
 * -----------
 * GET  /api/v1/ee/billing/wallet
 *   → WalletState (balance, config, spend summary, recent ledger entries)
 *
 * POST /api/v1/ee/billing/wallet/topup
 *   Body: { amount_usd_cents: number }
 *   → { checkout_url: string }  — redirect to Paystack to complete manual top-up
 *
 * PUT  /api/v1/ee/billing/wallet/autotopup
 *   Body: AutoTopupConfig
 *   → AutoTopupConfig (echo of saved settings)
 *
 * All helpers throw on HTTP errors (the api.js wrapper surfaces them as Error
 * objects with .status and .payload).  Callers should handle errors.
 */

import { get, post, put } from '../api.js'

// ---------------------------------------------------------------------------
// Typedefs
// ---------------------------------------------------------------------------

/**
 * @typedef {{
 *   balance_usd_cents: number,
 *   balance_zar_cents: number,
 *   last_fx_rate: number | null,
 *   last_fx_at: string | null,
 * }} WalletBalance
 */

/**
 * @typedef {{
 *   auto_topup_enabled: boolean,
 *   threshold_usd_cents: number,
 *   topup_amount_usd_cents: number,
 *   monthly_topup_cap_usd_cents: number | null,
 *   spend_cap_usd_cents: number | null,
 *   paystack_card_last4: string | null,
 *   paystack_card_brand: string | null,
 *   paystack_card_exp_month: string | null,
 *   paystack_card_exp_year: string | null,
 *   paystack_auth_reusable: boolean,
 * }} AutoTopupConfig
 */

/**
 * @typedef {{
 *   id: string,
 *   entry_type: string,
 *   amount_usd_cents: number,
 *   balance_after_usd_cents: number,
 *   description: string | null,
 *   ref_id: string | null,
 *   metadata: Record<string, any> | null,
 *   created_at: string,
 * }} LedgerEntry
 */

/**
 * @typedef {{
 *   total_spend_usd_cents: number,
 *   auto_topup_total_usd_cents: number,
 *   manual_topup_total_usd_cents: number,
 *   usage_llm_usd_cents: number,
 *   usage_storage_usd_cents: number,
 *   usage_compute_usd_cents: number,
 *   usage_embed_usd_cents: number,
 *   usage_overage_usd_cents: number,
 * }} MonthSpend
 */

/**
 * @typedef {{
 *   balance: WalletBalance,
 *   config: AutoTopupConfig,
 *   month_spend: MonthSpend,
 *   ledger: LedgerEntry[],
 * }} WalletState
 */

// ---------------------------------------------------------------------------
// Formatting helpers (exported for use in UI components)
// ---------------------------------------------------------------------------

/**
 * Format USD cents as a display string, e.g. 5000 → "$50.00"
 *
 * @param {number} cents
 * @returns {string}
 */
export function formatUsd(cents) {
  if (cents == null) return '$0.00'
  return '$' + (cents / 100).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

/**
 * Format ZAR cents as a display string, e.g. 50000 → "R 500.00"
 *
 * @param {number} cents
 * @returns {string}
 */
export function formatZarCents(cents) {
  if (cents == null) return 'R 0.00'
  return 'R ' + (cents / 100).toLocaleString('en-ZA', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

/**
 * Convert USD cents to whole USD dollars as a number (for input fields).
 *
 * @param {number} cents
 * @returns {number}
 */
export function centsToUsd(cents) {
  if (!cents) return 0
  return cents / 100
}

/**
 * Convert whole USD dollars to cents (for API payloads).
 *
 * @param {number} usd
 * @returns {number}
 */
export function usdToCents(usd) {
  if (!usd) return 0
  return Math.round(usd * 100)
}

// ---------------------------------------------------------------------------
// Entry type metadata — for ledger display
// ---------------------------------------------------------------------------

/**
 * Map entry_type to a human label and colour category.
 *
 * @type {Record<string, { label: string, sign: 'credit' | 'debit' | 'neutral' }>}
 */
export const ENTRY_META = {
  TOPUP_MANUAL:      { label: 'Manual top-up',     sign: 'credit'  },
  TOPUP_AUTO:        { label: 'Auto top-up',        sign: 'credit'  },
  TOPUP_PROMO:       { label: 'Promo credit',       sign: 'credit'  },
  TOPUP_FAILED:      { label: 'Top-up failed',      sign: 'neutral' },
  USAGE_LLM:         { label: 'AI / LLM usage',     sign: 'debit'   },
  USAGE_STORAGE:     { label: 'Storage',            sign: 'debit'   },
  USAGE_COMPUTE:     { label: 'Compute',            sign: 'debit'   },
  USAGE_EMBED:       { label: 'Embedded sessions',  sign: 'debit'   },
  USAGE_OVERAGE:     { label: 'Overage',            sign: 'debit'   },
  ADJUSTMENT_CREDIT: { label: 'Credit adjustment',  sign: 'credit'  },
  ADJUSTMENT_DEBIT:  { label: 'Debit adjustment',   sign: 'debit'   },
  EXPIRY:            { label: 'Credit expiry',      sign: 'debit'   },
}

// ---------------------------------------------------------------------------
// API helpers
// ---------------------------------------------------------------------------

/**
 * Fetch the full wallet state: balance, auto-topup config, month spend summary,
 * and recent ledger entries.
 *
 * GET /api/v1/ee/billing/wallet
 *
 * @returns {Promise<WalletState>}
 */
export function getWallet() {
  return get('/ee/billing/wallet')
}

/**
 * Initiate a manual top-up by creating a Paystack checkout session.
 * The caller should navigate to the returned checkout_url.
 *
 * POST /api/v1/ee/billing/wallet/topup
 *
 * @param {number} amountUsdCents   — e.g. 5000 for $50.00
 * @param {{ successUrl?: string, cancelUrl?: string }} [opts]
 * @returns {Promise<{ checkout_url: string }>}
 */
export function manualTopup(amountUsdCents, { successUrl, cancelUrl } = {}) {
  return post('/ee/billing/wallet/topup', {
    amount_usd_cents: amountUsdCents,
    success_url: successUrl ?? window.location.origin + '/billing?wallet=funded',
    cancel_url:  cancelUrl  ?? window.location.origin + '/billing?wallet=cancelled',
  })
}

/**
 * Update the org's auto-topup settings.
 *
 * PUT /api/v1/ee/billing/wallet/autotopup
 *
 * @param {{
 *   auto_topup_enabled?: boolean,
 *   threshold_usd_cents?: number,
 *   topup_amount_usd_cents?: number,
 *   monthly_topup_cap_usd_cents?: number | null,
 *   spend_cap_usd_cents?: number | null,
 * }} config
 * @returns {Promise<AutoTopupConfig>}
 */
export function setAutoTopup(config) {
  return put('/ee/billing/wallet/autotopup', config)
}
