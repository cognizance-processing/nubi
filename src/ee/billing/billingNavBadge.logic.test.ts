/**
 * billingNavBadge.logic.test.mjs — logic tests for
 * src/ee/billing/BillingNavBadge.jsx.
 *
 * Mirrored (not imported) for the same import.meta.env / JSX reason
 * documented at the top of billingPage.logic.test.mjs.
 *
 * BillingNavBadge is the smallest possible degradation surface in this
 * whole suite: it fetches billing status purely to decide whether to show
 * a plan chip, and on ANY failure it must render nothing (return null) —
 * never a broken chip, never a thrown error that could take down the nav
 * sidebar it lives in.
 *
 * Run: npm run test:dash  (node --test 'src/**\/*.test.mjs')
 */

import test, { describe } from 'node:test'
import assert from 'node:assert/strict'

// ---------------------------------------------------------------------------
// Mirror: the badge/label style maps — BillingNavBadge.jsx lines ~16-30
// ---------------------------------------------------------------------------

const BADGE_STYLES = {
  free:       'bg-surface-2 text-muted',
  starter:    'bg-teal-100 text-teal-700 dark:bg-teal-900/40 dark:text-teal-300',
  pro:        'bg-indigo-100 text-indigo-700 dark:bg-indigo-900/40 dark:text-indigo-300',
  business:   'bg-violet-100 text-violet-700 dark:bg-violet-900/40 dark:text-violet-300',
  enterprise: 'bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-300',
}

const TIER_LABELS = {
  free:       'Free',
  starter:    'Starter',
  pro:        'Pro',
  business:   'Business',
  enterprise: 'Enterprise',
}

/** Mirrors the component's render decision, given a resolved `tier`. */
function badgeState(tier) {
  if (!tier) return null // BillingNavBadge.jsx: `if (!tier) return null`
  return {
    style: BADGE_STYLES[tier] ?? BADGE_STYLES.free,
    label: TIER_LABELS[tier] ?? tier,
  }
}

describe('BillingNavBadge render decision', () => {
  test('null tier (not yet loaded, or fetch failed) renders nothing', () => {
    assert.equal(badgeState(null), null)
  })

  test('known tier resolves to its documented style + label', () => {
    assert.deepEqual(badgeState('pro'), {
      style: BADGE_STYLES.pro,
      label: 'Pro',
    })
  })

  test('unknown tier id falls back to the free style but keeps the raw id as its label', () => {
    const state = badgeState('mystery-tier')
    assert.equal(state.style, BADGE_STYLES.free)
    assert.equal(state.label, 'mystery-tier')
  })

  test('every documented tier has both a style and a label entry (no silent gaps)', () => {
    for (const tier of Object.keys(TIER_LABELS)) {
      assert.ok(BADGE_STYLES[tier], `${tier} must have a badge style`)
    }
  })
})

// ---------------------------------------------------------------------------
// Mirror: the useEffect fetch + silent-degrade catch — BillingNavBadge.jsx
// lines ~35-41
// ---------------------------------------------------------------------------

async function loadTier(fetchBillingStatusFn) {
  try {
    const s = await fetchBillingStatusFn()
    return s.tier
  } catch {
    // degrade silently — badge just won't render
    return null
  }
}

describe('BillingNavBadge fetch degradation (OSS deployment / EE endpoint absent)', () => {
  test('fetchBillingStatus succeeds → tier is set, badge renders', async () => {
    const tier = await loadTier(async () => ({ tier: 'enterprise' }))
    assert.equal(tier, 'enterprise')
    assert.notEqual(badgeState(tier), null)
  })

  test('fetchBillingStatus 404s (EE billing routes absent) → tier stays null, no throw', async () => {
    const notFound = new Error('Request failed: 404 Not Found')
    let threw = false
    let tier
    try {
      tier = await loadTier(async () => { throw notFound })
    } catch {
      threw = true
    }
    assert.equal(threw, false, 'the component must never let a fetch failure escape as an uncaught error')
    assert.equal(tier, null)
    assert.equal(badgeState(tier), null, 'and the badge must render nothing, not a broken chip')
  })

  test('any other rejection (network error, auth failure) also degrades to null', async () => {
    const tier = await loadTier(async () => { throw new TypeError('Failed to fetch') })
    assert.equal(tier, null)
  })
})
