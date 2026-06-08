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

import { get, post } from '../api.js'

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
      '2 GB storage',
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
  },
  {
    id: 'starter',
    name: 'Starter',
    usd_monthly: 79,
    // Correct ceil10 value: ceil10($79 × 16.26 × 1.02) = ceil10(R1310.23) = R1320
    price_zar: 1320,
    price_label: 'R 1,320 / month',
    annual_usd: 790,
    // Annual monthly equivalent: ceil10($79 × 10/12 × 16.26 × 1.02) ≈ R1100
    annual_zar_monthly_equiv: 1100,
    seats: null,
    description: 'For small teams and single-product SaaS that need cloud hosting.',
    features: [
      'Unlimited editors & viewers — no per-seat charge',
      '10 GB storage',
      '5,000 compute units / month',
      '5,000 embedded sessions / month',
      '10 connectors (incl. 2 cloud)',
      '25 dashboards · 5 scheduled flows',
      '10 AI calls / month',
      'Basic row-level security',
      'Google OAuth SSO · Nubi badge removable',
      '30-day audit log',
      '99.5% uptime SLA',
    ],
    cta_label: 'Upgrade to Starter',
    highlight: false,
    is_enterprise: false,
  },
  {
    id: 'pro',
    name: 'Pro',
    usd_monthly: 199,
    // Correct ceil10 value: ceil10($199 × 16.26 × 1.02) = ceil10(R3302.15) = R3310
    price_zar: 3310,
    price_label: 'R 3,310 / month',
    annual_usd: 1990,
    // Annual monthly equivalent ≈ ceil10($199 × 10/12 × 16.26 × 1.02) ≈ R2760
    annual_zar_monthly_equiv: 2760,
    seats: null,
    description: 'For growing teams and embedded analytics ISVs.',
    features: [
      'Unlimited editors & viewers — no per-seat charge',
      '50 GB storage',
      '10,000 compute units / month',
      '25,000 embedded sessions / month',
      '50 agent / kernel runs · 50 AI calls / month',
      'All connectors',
      '100 dashboards · 20 scheduled flows',
      'Full RLS with JWT claims',
      'Google OAuth + SAML (1 IdP)',
      'Full white-label (custom domain)',
      '90-day audit log',
      '99.5% uptime SLA',
    ],
    cta_label: 'Upgrade to Pro',
    highlight: true,
    is_enterprise: false,
  },
  {
    id: 'business',
    name: 'Business',
    usd_monthly: 499,
    price_zar: 8280,
    price_label: 'R 8,280 / month',
    annual_usd: 4990,
    annual_zar_monthly_equiv: 6900,
    seats: null,
    description: 'For mid-market SaaS and multi-product analytics platforms.',
    features: [
      'Unlimited editors & viewers — no per-seat charge',
      '200 GB storage',
      '40,000 compute units / month',
      '100,000 embedded sessions / month',
      '200 agent / kernel runs · 200 AI calls / month',
      'Unlimited dashboards & flows',
      'Full RLS + host-signed JWT pass-through',
      'SAML (unlimited IdPs) + SCIM',
      'Full white-label + multi-tenant workspaces',
      '1-year audit log + export',
      'Email & Slack priority support',
      '99.9% uptime SLA',
    ],
    cta_label: 'Upgrade to Business',
    highlight: false,
    is_enterprise: false,
  },
  {
    id: 'enterprise',
    name: 'Enterprise',
    usd_monthly: 1799,
    price_zar: 29840,
    price_label: 'From R 29,840 / month',
    annual_usd: 17990,
    annual_zar_monthly_equiv: 24870,
    seats: null,
    description: 'Unlimited scale, dedicated infrastructure, BYOC, and white-glove support.',
    features: [
      'Unlimited editors & viewers — no per-seat charge',
      '500 GB+ storage (hosted) or unlimited (BYOC)',
      '200,000+ compute units / month',
      'Unlimited embedded sessions',
      '1,000 agent / kernel runs · 500 AI calls / month',
      'Custom connector SDK',
      'Full RLS + HIPAA-ready controls',
      'SAML/SCIM, multi-IdP, custom domains',
      'Full white-label + custom JS SDK',
      'Unlimited audit log + SIEM export',
      'Dedicated CSM · 99.99% uptime SLA',
      'BYOC / air-gap / on-prem deployment',
      'BAA / HIPAA on request',
    ],
    cta_label: 'Contact sales',
    highlight: false,
    is_enterprise: true,
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
