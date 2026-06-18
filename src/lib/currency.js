/**
 * currency.js — display-currency catalog, locale detection, and formatting.
 *
 * Nubi anchors prices in USD and BILLS in ZAR (via Paystack). Every other
 * currency here is a DISPLAY convenience only — an indicative conversion shown
 * to help a visitor gauge the price in money they recognise. The real charge is
 * always the ZAR amount (see the "billed in ZAR" note next to every price).
 *
 * Rates are USD→X. USD is exactly 1. ZAR is authoritative (matches the billing
 * FX); the rest are refreshed live from a keyless FX API at runtime and fall
 * back to the indicative table below when the network is unavailable.
 */

// ---------------------------------------------------------------------------
// Catalog — curated set of display currencies (flag, symbol, name)
// ---------------------------------------------------------------------------

/** @typedef {{ code: string, symbol: string, flag: string, name: string }} Currency */

/** @type {Record<string, Currency>} */
export const CURRENCIES = {
  USD: { code: 'USD', symbol: '$',   flag: '🇺🇸', name: 'US Dollar' },
  ZAR: { code: 'ZAR', symbol: 'R',   flag: '🇿🇦', name: 'South African Rand' },
  EUR: { code: 'EUR', symbol: '€',   flag: '🇪🇺', name: 'Euro' },
  GBP: { code: 'GBP', symbol: '£',   flag: '🇬🇧', name: 'British Pound' },
  AUD: { code: 'AUD', symbol: 'A$',  flag: '🇦🇺', name: 'Australian Dollar' },
  CAD: { code: 'CAD', symbol: 'C$',  flag: '🇨🇦', name: 'Canadian Dollar' },
  INR: { code: 'INR', symbol: '₹',   flag: '🇮🇳', name: 'Indian Rupee' },
  NGN: { code: 'NGN', symbol: '₦',   flag: '🇳🇬', name: 'Nigerian Naira' },
  KES: { code: 'KES', symbol: 'KSh', flag: '🇰🇪', name: 'Kenyan Shilling' },
  AED: { code: 'AED', symbol: 'د.إ', flag: '🇦🇪', name: 'UAE Dirham' },
}

/** Stable display order for the selector. */
export const CURRENCY_ORDER = ['USD', 'ZAR', 'EUR', 'GBP', 'AUD', 'CAD', 'INR', 'NGN', 'KES', 'AED']

/** USD is the anchor and the default display currency. */
export const DEFAULT_CURRENCY = 'USD'

/** ZAR is what we actually bill in (Paystack). */
export const BILLING_CURRENCY = 'ZAR'

export const CURRENCY_STORAGE_KEY = 'nubi-currency'

/**
 * Indicative fallback USD→X rates (used until the live fetch resolves, or if it
 * fails). ZAR matches the repo's June 2026 reference; others are approximate and
 * get overwritten by the live fetch.
 */
export const FALLBACK_RATES = {
  USD: 1,
  ZAR: 16.26,
  EUR: 0.92,
  GBP: 0.78,
  AUD: 1.52,
  CAD: 1.37,
  INR: 83.2,
  NGN: 1550,
  KES: 129,
  AED: 3.67,
}

// ---------------------------------------------------------------------------
// Region → currency (for locale detection)
// ---------------------------------------------------------------------------

const EUROZONE = ['DE', 'FR', 'ES', 'IT', 'NL', 'IE', 'PT', 'AT', 'BE', 'FI', 'GR', 'LU', 'SK', 'SI', 'EE', 'LV', 'LT', 'CY', 'MT', 'HR']

const REGION_CURRENCY = {
  US: 'USD', ZA: 'ZAR', GB: 'GBP', AU: 'AUD', CA: 'CAD',
  IN: 'INR', NG: 'NGN', KE: 'KES', AE: 'AED',
  ...Object.fromEntries(EUROZONE.map((r) => [r, 'EUR'])),
}

/**
 * Detect the visitor's preferred display currency from the browser/OS locale.
 *
 * Browsers do not expose the physical keyboard layout in a way that maps to a
 * currency, so we use the closest reliable signal — `navigator.languages`
 * (e.g. "en-GB" → GB → GBP). Returns a currency code present in {@link CURRENCIES},
 * or {@link DEFAULT_CURRENCY} (USD) when the region is unknown/unsupported.
 *
 * @returns {string} a supported currency code
 */
export function detectCurrency() {
  try {
    const langs = (typeof navigator !== 'undefined' && (navigator.languages || [navigator.language])) || []
    for (const tag of langs) {
      if (!tag) continue
      // Region is the last hyphen-separated subtag, e.g. "en-GB" → "GB", "zh-Hant-HK" → "HK".
      const parts = String(tag).split('-')
      const region = parts[parts.length - 1].toUpperCase()
      const cur = REGION_CURRENCY[region]
      if (cur && CURRENCIES[cur]) return cur
    }
  } catch {
    // navigator unavailable — fall through to the default
  }
  return DEFAULT_CURRENCY
}

// ---------------------------------------------------------------------------
// Formatting
// ---------------------------------------------------------------------------

/**
 * Convert a USD amount to `code` using `rates` (USD→X). Falls back to the
 * indicative table when a rate is missing.
 *
 * @param {number} usd
 * @param {string} code
 * @param {Record<string, number>} [rates]
 * @returns {number}
 */
export function convertFromUsd(usd, code, rates = FALLBACK_RATES) {
  const rate = rates?.[code] ?? FALLBACK_RATES[code] ?? 1
  return usd * rate
}

/**
 * Format a USD amount in the given display currency as a whole-number money
 * string (prices are whole-ish; we never show cents on plan prices).
 *
 * @param {number} usd
 * @param {string} code
 * @param {Record<string, number>} [rates]
 * @returns {string} e.g. "$9", "R 150", "€8", "₹749"
 */
export function formatMoney(usd, code, rates = FALLBACK_RATES) {
  const amount = convertFromUsd(usd, code, rates)
  try {
    return new Intl.NumberFormat(undefined, {
      style: 'currency',
      currency: code,
      maximumFractionDigits: 0,
      currencyDisplay: 'narrowSymbol',
    }).format(amount)
  } catch {
    const sym = CURRENCIES[code]?.symbol ?? ''
    return `${sym}${Math.round(amount).toLocaleString('en-US')}`
  }
}
