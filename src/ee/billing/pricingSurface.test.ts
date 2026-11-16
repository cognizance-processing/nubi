/**
 * pricingSurface.test.mjs — tests for the EE pricing-calculator / tier-card
 * surface: src/ee/billing/PricingCalculator.jsx and src/ee/billing/TierCard.jsx.
 *
 * Both files are thin EE re-exports of core components:
 *   PricingCalculator.jsx → export { default } from '../../components/pricing/PricingCalculator.jsx'
 *   TierCard.jsx          → export { TierCard as default } from '../../components/pricing/TierCards.jsx'
 * The actual calculator math and tier-comparison logic live in core
 * (src/components/pricing/*, src/lib/pricing.js) and are already covered by
 * the existing core suite (src/lib/pricing.competitors.test.mjs —
 * recommendNubi, computeZar, FALLBACK_TIERS, competitor models). We don't
 * duplicate that here; instead we (a) guard that the EE wrapper files still
 * point at the right core export (a real, non-mirrored source-text check —
 * this is exactly the kind of silent-drift bug a thin re-export is prone
 * to), and (b) cover the EE-SPECIFIC pricing math that lives in
 * src/lib/ee/billing.js (computeZar/formatZar/FALLBACK_TIERS for the
 * account-billing surface, which uses different tier IDs/prices than the
 * public marketing pricing.js).
 *
 * src/lib/ee/billing.js can't be imported directly (it imports
 * src/lib/api.js, which throws under plain Node — see billingPage.logic.test.mjs
 * for the full explanation), so its pure math is mirrored below.
 *
 * Run: npm run test:dash  (node --test 'src/**\/*.test.mjs')
 */

import test, { describe } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'

const __dirname = dirname(fileURLToPath(import.meta.url))

// ---------------------------------------------------------------------------
// Re-export wiring — real source-text checks (no mirroring; this is
// literally the file's entire content, so scanning it IS testing it)
// ---------------------------------------------------------------------------

describe('EE pricing-surface re-exports point at the right core components', () => {
  test('PricingCalculator.jsx re-exports the core calculator as its default', () => {
    const src = readFileSync(join(__dirname, 'PricingCalculator.tsx'), 'utf8')
    assert.match(src, /export \{ default \} from ['"]\.\.\/\.\.\/components\/pricing\/PricingCalculator\.jsx['"]/)
  })

  test('TierCard.jsx re-exports the core TierCard (not the whole TierCards grid) as its default', () => {
    const src = readFileSync(join(__dirname, 'TierCard.tsx'), 'utf8')
    assert.match(src, /export \{ TierCard as default \} from ['"]\.\.\/\.\.\/components\/pricing\/TierCards\.jsx['"]/)
  })
})

// ---------------------------------------------------------------------------
// Mirror: computeZar() / formatZar() — src/lib/ee/billing.js lines ~45-60
// (the account-billing ZAR rounding formula: ceil to nearest R10 with a 2%
// FX buffer — distinct from, but numerically identical to, core pricing.js's
// version; both must match the backend's tiers.py formula)
// ---------------------------------------------------------------------------

function computeZar(usd, rate) {
  if (!usd || !rate) return 0
  const raw = usd * rate * 1.02
  return Math.ceil(raw / 10) * 10
}

function formatZar(zarAmount) {
  if (!zarAmount) return 'R 0'
  return 'R ' + zarAmount.toLocaleString('en-ZA')
}

describe('src/lib/ee/billing.js computeZar (ceil-to-R10, +2% FX buffer)', () => {
  test('zero usd or zero/missing rate short-circuits to 0', () => {
    assert.equal(computeZar(0, 16.26), 0)
    assert.equal(computeZar(9, 0), 0)
    assert.equal(computeZar(9, null), 0)
  })

  test('Starter ($9) at R16.26/USD rounds up to R150 (matches FALLBACK_TIERS)', () => {
    assert.equal(computeZar(9, 16.26), 150)
  })

  test('Team ($49) at R16.26/USD rounds up to R820', () => {
    assert.equal(computeZar(49, 16.26), 820)
  })

  test('Pro ($149) at R16.26/USD rounds up to R2,480', () => {
    assert.equal(computeZar(149, 16.26), 2480)
  })

  test('Enterprise ($1000) at R16.26/USD rounds up to R16,590', () => {
    assert.equal(computeZar(1000, 16.26), 16590)
  })

  test('always rounds UP to the next R10 (never down — protects margin)', () => {
    // $1 at a R1/USD rate → raw R1.02 → must round UP to R10, not down to R0.
    assert.equal(computeZar(1, 1), 10)
    // Every result must always be an exact multiple of 10.
    for (const [usd, rate] of [[9, 16.26], [49, 16.26], [149, 16.26], [1000, 16.26], [23, 12.5]]) {
      assert.equal(computeZar(usd, rate) % 10, 0)
    }
  })
})

describe('src/lib/ee/billing.js formatZar', () => {
  test('formats a positive ZAR integer with locale grouping', () => {
    assert.equal(formatZar(2480), 'R ' + (2480).toLocaleString('en-ZA'))
  })

  test('zero/falsy renders "R 0" rather than "R " + empty', () => {
    assert.equal(formatZar(0), 'R 0')
    assert.equal(formatZar(null), 'R 0')
  })
})

// ---------------------------------------------------------------------------
// Mirror: fetchBillingTiers / fetchFxRate degradation fallbacks —
// src/lib/ee/billing.js lines ~285-322. These wrap their network calls in
// their OWN try/catch (unlike fetchBillingStatus), so this IS the
// EE-endpoints-absent (OSS deployment) graceful-degradation contract for
// the pricing/tier data specifically.
// ---------------------------------------------------------------------------

const FALLBACK_TIERS_STUB = [{ id: 'free' }, { id: 'starter' }, { id: 'pro' }]

async function fetchBillingTiersLike(getFn) {
  try {
    const data = await getFn()
    if (Array.isArray(data?.tiers) && data.tiers.length > 0) return data.tiers
    return FALLBACK_TIERS_STUB
  } catch {
    return FALLBACK_TIERS_STUB
  }
}

async function fetchFxRateLike(getFn) {
  try {
    const data = await getFn()
    if (data?.rate && typeof data.rate === 'number') return data
    return { rate: 16.26, updated_at: null, fallback: true }
  } catch {
    return { rate: 16.26, updated_at: null, fallback: true }
  }
}

describe('graceful degradation: EE billing/tiers + billing/fx absent (OSS deployment)', () => {
  test('tiers endpoint 404s → FALLBACK_TIERS returned, no throw', async () => {
    const tiers = await fetchBillingTiersLike(async () => { throw new Error('404') })
    assert.deepEqual(tiers, FALLBACK_TIERS_STUB)
  })

  test('tiers endpoint returns an empty array → still falls back (never renders a blank pricing page)', async () => {
    const tiers = await fetchBillingTiersLike(async () => ({ tiers: [] }))
    assert.deepEqual(tiers, FALLBACK_TIERS_STUB)
  })

  test('tiers endpoint returns real data → used verbatim (backend is authoritative)', async () => {
    const real = [{ id: 'custom-tier' }]
    const tiers = await fetchBillingTiersLike(async () => ({ tiers: real }))
    assert.deepEqual(tiers, real)
  })

  test('fx endpoint 404s → reference rate R16.26 with fallback=true, no throw', async () => {
    const fx = await fetchFxRateLike(async () => { throw new Error('404') })
    assert.deepEqual(fx, { rate: 16.26, updated_at: null, fallback: true })
  })

  test('fx endpoint returns a malformed payload (no numeric rate) → falls back too', async () => {
    const fx = await fetchFxRateLike(async () => ({ rate: 'not-a-number' }))
    assert.equal(fx.fallback, true)
    assert.equal(fx.rate, 16.26)
  })

  test('fx endpoint returns a valid live rate → used as-is, fallback=false implied by the response', async () => {
    const live = { rate: 17.1, updated_at: '2026-07-01', fallback: false }
    const fx = await fetchFxRateLike(async () => live)
    assert.deepEqual(fx, live)
  })
})
