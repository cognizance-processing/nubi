/**
 * billing.js — EE billing API client (src/lib/ee/billing.js)
 *
 * Thin wrappers around the EE billing endpoints at /api/v1/ee/billing/*.
 * This file lives in src/lib/ee/ (EE territory) and is ONLY imported by
 * EE components (PricingPage, BillingPage, UpgradePrompt).  Core must never
 * import it.
 *
 * API surface
 * -----------
 * GET  /api/v1/ee/billing/status
 *   → { tier, seats_used, seats_limit, trial_ends_at, renewal_date, features }
 *
 * GET  /api/v1/ee/billing/tiers
 *   → { tiers: [{ id, name, price_usd, price_zar, price_label, ... }] }
 *
 * GET  /api/v1/ee/billing/fx
 *   → { rate: number, updated_at: string, fallback: boolean }
 *
 * POST /api/v1/ee/billing/checkout
 *   Body: { tier_id: string, success_url: string, cancel_url: string }
 *   → { checkout_url: string }   — Paystack hosted-page URL; caller navigates to it
 *
 * POST /api/v1/ee/billing/portal
 *   → { portal_url: string }     — Paystack customer portal URL
 *
 * All helpers throw on HTTP errors (the api.js wrapper surfaces them as
 * Error objects with .status and .payload).  Callers should handle errors.
 */

import { get, post, getBlob } from '../api.js'

// ---------------------------------------------------------------------------
// FX helpers
// ---------------------------------------------------------------------------

/**
 * ZAR rounding: ceil to nearest R10 (protects margin during ZAR weakness).
 * Matches the backend formula: ceil_to_nearest_10(usd * rate * 1.02)
 *
 * @param {number} usd    USD amount
 * @param {number} rate   USD→ZAR exchange rate
 * @returns {number}      ZAR amount rounded up to nearest 10
 */
export function computeZar(usd, rate) {
  if (!usd || !rate) return 0
  const raw = usd * rate * 1.02
  return Math.ceil(raw / 10) * 10
}

/**
 * Format a ZAR integer as a locale string, e.g. 1310 → "R 1,310"
 *
 * @param {number} zar
 * @returns {string}
 */
export function formatZar(zar) {
  if (!zar) return 'R 0'
  return 'R ' + zar.toLocaleString('en-ZA')
}

// ---------------------------------------------------------------------------
// Tier definitions — static fallback and type docs
// ---------------------------------------------------------------------------

/**
 * @typedef {{
 *   id: string,
 *   name: string,
 *   usd_monthly: number,
 *   price_zar: number,
 *   price_label: string,
 *   annual_usd: number | null,
 *   annual_zar_monthly_equiv: number | null,
 *   seats: number | null,
 *   description: string,
 *   features: string[],
 *   cta_label: string,
 *   highlight?: boolean,
 *   is_enterprise?: boolean,
 * }} TierInfo
 */

/**
 * Static fallback tier list used when the backend /api/v1/ee/billing/tiers is
 * unreachable.  Prices reflect the June 2026 reference amounts at R16.26 USD/ZAR
 * + 2% FX buffer per the approved pricing blueprint.
 *
 * Tier model: Free / Starter ($9) / Team ($49) / Pro ($149) / Enterprise (from $1,000 + SLA).
 * No per-seat pricing — unlimited seats at every tier.
 *
 * Backend response is authoritative; this is a display fallback only.
 *
 * @type {TierInfo[]}
 */
export const FALLBACK_TIERS = [
  {
    id: 'free',
    name: 'Free',
    usd_monthly: 0,
    price_zar: 0,
    price_label: 'Free forever',
    annual_usd: null,
    annual_zar_monthly_equiv: null,
    // seats: null — unlimited at every tier per the pricing blueprint
    seats: null,
    description: 'For indie devs, OSS evaluators, and small experiments.',
    features: [
      'Unlimited editors & viewers',
      '1 GB storage',
      '500 compute units / month',
      'Up to 5 dashboards',
      '2 scheduled flows',
      '3 built-in connectors (CSV, DuckDB, Postgres)',
      '10,000 row query cap per execution',
      'Nubi branding on all embeds',
      'Community support',
    ],
    cta_label: 'Get started free',
    highlight: false,
    is_enterprise: false,
    has_sla: false,
  },
  {
    id: 'starter',
    name: 'Starter',
    usd_monthly: 9,
    // ceil10($9 × 16.26 × 1.02) = ceil10(R149.35) = R150
    price_zar: 150,
    price_label: 'R 150 / month',
    annual_usd: 90,
    // ceil10($9 × 10/12 × 16.26 × 1.02) = ceil10(R124.46) = R130
    annual_zar_monthly_equiv: 130,
    seats: null,
    description: 'For hobbyists and side-projects that need more headroom.',
    features: [
      'Unlimited editors & viewers — no per-seat charge',
      '5 GB storage',
      '2,000 compute units / month',
      '1,000 embedded sessions / month',
      '5 connectors',
      '10 dashboards · 3 scheduled flows',
      '5 AI calls / month',
      'Basic row-level security',
      'Nubi badge removable',
      'Usage wallet — pay-as-you-go overages',
      'Email support',
    ],
    cta_label: 'Upgrade to Starter',
    highlight: false,
    is_enterprise: false,
    has_sla: false,
  },
  {
    id: 'team',
    name: 'Team',
    usd_monthly: 49,
    // ceil10($49 × 16.26 × 1.02) = ceil10(R812.77) = R820
    price_zar: 820,
    price_label: 'R 820 / month',
    annual_usd: 490,
    // ceil10($49 × 10/12 × 16.26 × 1.02) = ceil10(R677.31) = R680
    annual_zar_monthly_equiv: 680,
    seats: null,
    description: 'For small teams collaborating on production analytics.',
    features: [
      'Unlimited editors & viewers — no per-seat charge',
      '15 GB storage',
      '6,000 compute units / month',
      '5,000 embedded sessions / month',
      '15 connectors (incl. cloud)',
      '30 dashboards · 8 scheduled flows',
      '15 AI calls / month · 10 agent / kernel runs',
      'Basic row-level security',
      'Nubi badge removable',
      'Usage wallet — pay-as-you-go overages',
      'Email support',
    ],
    cta_label: 'Upgrade to Team',
    highlight: false,
    is_enterprise: false,
    has_sla: false,
  },
  {
    id: 'pro',
    name: 'Pro',
    usd_monthly: 149,
    // ceil10($149 × 16.26 × 1.02) = ceil10(R2471.86) = R2480
    price_zar: 2480,
    price_label: 'R 2,480 / month',
    annual_usd: 1490,
    // ceil10($149 × 10/12 × 16.26 × 1.02) = ceil10(R2059.88) = R2060
    annual_zar_monthly_equiv: 2060,
    seats: null,
    description: 'For growing teams shipping production embedded analytics.',
    features: [
      'Unlimited editors & viewers — no per-seat charge',
      '50 GB storage',
      '15,000 compute units / month',
      '25,000 embedded sessions / month',
      '50 AI calls / month · 50 agent / kernel runs',
      'All connectors',
      '100 dashboards · 20 scheduled flows',
      'Full RLS with JWT claims',
      'Google OAuth + SAML (1 IdP)',
      'Full white-label (custom domain)',
      '90-day audit log',
      'Usage wallet — prepaid credits, auto-topup',
      '99.5% uptime SLA',
    ],
    cta_label: 'Upgrade to Pro',
    highlight: true,
    is_enterprise: false,
    has_sla: false,
  },
  {
    id: 'enterprise',
    name: 'Enterprise',
    usd_monthly: 1000,
    // ceil10($1000 × 16.26 × 1.02) = ceil10(R16585.20) = R16590
    price_zar: 16590,
    price_label: 'From R 16,590 / month',
    annual_usd: 10000,
    // ceil10($1000 × 10/12 × 16.26 × 1.02) = ceil10(R13821) = R13830
    annual_zar_monthly_equiv: 13830,
    seats: null,
    description: 'For enterprise teams that need SLA guarantees and dedicated support.',
    features: [
      'Unlimited editors & viewers — no per-seat charge',
      '500 GB+ storage (hosted) or unlimited (BYOC)',
      '200,000 compute units / month',
      'Unlimited embedded sessions',
      '500 AI calls / month · 1,000 agent / kernel runs',
      'All connectors + custom connector SDK',
      'Unlimited dashboards & scheduled flows',
      'Full RLS + host-signed JWT pass-through',
      'SAML (unlimited IdPs) + SCIM',
      'Full white-label + custom JS SDK',
      'Unlimited audit log + SIEM export',
      'Usage wallet — prepaid credits, auto-topup, spend cap',
      'BYOC / air-gap / on-prem deployment',
      'BAA / HIPAA on request',
    ],
    cta_label: 'Contact sales',
    highlight: false,
    is_enterprise: true,
    has_sla: true,
    sla: {
      uptime: '99.99%',
      response_time: '4-hour critical / 8-hour standard',
      support: 'Named dedicated support engineer',
    },
  },
]

// ---------------------------------------------------------------------------
// API helpers
// ---------------------------------------------------------------------------

/**
 * @typedef {{
 *   tier: string,
 *   seats_used: number,
 *   seats_limit: number | null,
 *   trial_ends_at: string | null,
 *   renewal_date: string | null,
 *   features: string[],
 * }} BillingStatus
 */

/**
 * Fetch the current org's billing status.
 *
 * @returns {Promise<BillingStatus>}
 */
export function fetchBillingStatus() {
  return get('/ee/billing/status')
}

/**
 * Fetch the available upgrade tiers from the backend.
 * Falls back to FALLBACK_TIERS on any error so the billing page always renders.
 *
 * @returns {Promise<TierInfo[]>}
 */
export async function fetchBillingTiers() {
  try {
    const data = await get('/ee/billing/tiers')
    if (Array.isArray(data?.tiers) && data.tiers.length > 0) return data.tiers
    return FALLBACK_TIERS
  } catch {
    return FALLBACK_TIERS
  }
}

/**
 * @typedef {{
 *   rate: number,
 *   updated_at: string,
 *   fallback: boolean,
 * }} FxRate
 */

/**
 * Fetch the current USD→ZAR exchange rate from the backend.
 * Falls back to a hardcoded reference rate (R16.26 per USD, June 2026) on error.
 *
 * The backend refreshes this daily at 07:00 SAST via a scheduled Flow.
 * If the rate has not been refreshed in 72 hours, the backend itself uses a
 * hardcoded emergency fallback and marks { fallback: true } in the response.
 *
 * @returns {Promise<FxRate>}
 */
export async function fetchFxRate() {
  try {
    const data = await get('/ee/billing/fx')
    if (data?.rate && typeof data.rate === 'number') return data
    // Backend returned something unexpected — use reference rate
    return { rate: 16.26, updated_at: null, fallback: true }
  } catch {
    return { rate: 16.26, updated_at: null, fallback: true }
  }
}

/**
 * Create a Paystack checkout session for the given tier.
 * The caller should navigate to the returned URL.
 *
 * @param {string} tierId
 * @param {{ successUrl?: string, cancelUrl?: string }} [opts]
 * @returns {Promise<{ checkout_url: string }>}
 */
export function createCheckout(tierId, { successUrl, cancelUrl } = {}) {
  return post('/ee/billing/checkout', {
    tier_id: tierId,
    success_url: successUrl ?? window.location.origin + '/billing?status=success',
    cancel_url: cancelUrl ?? window.location.origin + '/billing?status=cancelled',
  })
}

/**
 * Open the Paystack customer billing portal (manage subscription, invoices, etc.)
 * The caller should navigate to the returned URL.
 *
 * @returns {Promise<{ portal_url: string }>}
 */
export function openBillingPortal() {
  return post('/ee/billing/portal', {})
}

// ---------------------------------------------------------------------------
// Invoices + current-cycle projection (org_id-scoped, matches routes.py)
// ---------------------------------------------------------------------------

/**
 * List invoices for an org (newest first).
 *
 * @param {string} orgId
 * @returns {Promise<{ org_id: string, invoices: object[], count: number }>}
 */
export async function fetchInvoices(orgId) {
  if (!orgId) return { org_id: null, invoices: [], count: 0 }
  return get(`/ee/billing/invoices?org_id=${encodeURIComponent(orgId)}&limit=50`)
}

/**
 * Project the current billing cycle (usage vs quota + gross overage). Dry-run —
 * never collects money. Powers the "this cycle" panel in billing settings.
 *
 * @param {string} orgId
 * @returns {Promise<object>}
 */
export async function fetchCurrentCycle(orgId) {
  if (!orgId) return null
  return get(`/ee/billing/invoices/current-cycle?org_id=${encodeURIComponent(orgId)}`)
}

/**
 * Download an invoice PDF and trigger a browser save. Fetches as an authed Blob
 * (the access token lives in memory, so a plain link won't authenticate).
 *
 * @param {string} orgId
 * @param {string} invoiceId
 * @param {string} [filename]
 */
export async function downloadInvoicePdf(orgId, invoiceId, filename) {
  const blob = await getBlob(
    `/ee/billing/invoices/${encodeURIComponent(invoiceId)}/pdf?org_id=${encodeURIComponent(orgId)}`,
  )
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename || `${invoiceId}.pdf`
  document.body.appendChild(a)
  a.click()
  a.remove()
  URL.revokeObjectURL(url)
}
