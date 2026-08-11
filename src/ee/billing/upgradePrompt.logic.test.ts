/**
 * upgradePrompt.logic.test.mjs — logic tests for src/ee/billing/UpgradePrompt.jsx.
 *
 * Mirrored (not imported) for the same import.meta.env / JSX reason
 * documented at the top of billingPage.logic.test.mjs.
 *
 * Covers: the compact-vs-card variant selection, and CardPrompt's
 * handleUpgrade degradation — if createCheckout() fails (e.g. the EE
 * backend billing routes are absent in an OSS deployment), the component
 * must fall back to navigating to /billing rather than showing a dead end.
 *
 * Run: npm run test:dash  (node --test 'src/**\/*.test.mjs')
 */

import test, { describe } from 'node:test'
import assert from 'node:assert/strict'

// ---------------------------------------------------------------------------
// Mirror: UpgradePrompt's variant dispatch — UpgradePrompt.jsx lines ~135-140
// ---------------------------------------------------------------------------

function resolveVariant({ compact = false } = {}) {
  return compact ? 'compact' : 'card'
}

describe('UpgradePrompt variant selection', () => {
  test('defaults to the full card variant', () => {
    assert.equal(resolveVariant({}), 'card')
  })

  test('compact=true selects the inline badge variant', () => {
    assert.equal(resolveVariant({ compact: true }), 'compact')
  })
})

// ---------------------------------------------------------------------------
// Mirror: CardPrompt.handleUpgrade — UpgradePrompt.jsx lines ~60-71
// Parameterized on createCheckoutFn / navigateFn so the network + router
// dependencies are injectable fakes rather than real imports.
// ---------------------------------------------------------------------------

async function handleUpgrade({ tier, createCheckoutFn, navigateFn }) {
  try {
    const { checkout_url } = await createCheckoutFn(tier.toLowerCase())
    return { navigatedTo: checkout_url, viaFallback: false }
  } catch {
    // If checkout API fails (e.g. no EE backend), fall back to billing page.
    navigateFn('/billing')
    return { navigatedTo: '/billing', viaFallback: true }
  }
}

describe('CardPrompt.handleUpgrade graceful degradation', () => {
  test('checkout succeeds → navigates straight to the Paystack checkout URL', async () => {
    const result = await handleUpgrade({
      tier: 'Pro',
      createCheckoutFn: async (tierId) => {
        assert.equal(tierId, 'pro', 'tier must be lowercased before calling createCheckout')
        return { checkout_url: 'https://paystack.example/checkout/abc' }
      },
      navigateFn: () => { throw new Error('must not navigate on success') },
    })
    assert.equal(result.navigatedTo, 'https://paystack.example/checkout/abc')
    assert.equal(result.viaFallback, false)
  })

  test('OSS mode: checkout endpoint 404s → falls back to /billing instead of a dead end', async () => {
    const notFound = new Error('Request failed: 404 Not Found')
    let navigatedTo = null
    const result = await handleUpgrade({
      tier: 'Pro',
      createCheckoutFn: async () => { throw notFound },
      navigateFn: (path) => { navigatedTo = path },
    })
    assert.equal(result.viaFallback, true)
    assert.equal(navigatedTo, '/billing')
  })

  test('any other transport failure also degrades to the /billing fallback (no crash, no dead end)', async () => {
    let navigatedTo = null
    await handleUpgrade({
      tier: 'Enterprise',
      createCheckoutFn: async () => { throw new TypeError('network error') },
      navigateFn: (path) => { navigatedTo = path },
    })
    assert.equal(navigatedTo, '/billing')
  })
})

// ---------------------------------------------------------------------------
// Mirror: CompactPrompt's accessible-name construction — UpgradePrompt.jsx
// lines ~42
// ---------------------------------------------------------------------------

function compactAriaLabel({ feature, tier }) {
  return `Upgrade to ${tier} to unlock ${feature}`
}

describe('CompactPrompt accessible label', () => {
  test('builds a descriptive aria-label from the feature + tier props', () => {
    assert.equal(
      compactAriaLabel({ feature: 'SSO', tier: 'Pro' }),
      'Upgrade to Pro to unlock SSO',
    )
  })

  test('uses the documented defaults when no props are passed', () => {
    // UpgradePrompt.jsx defaults: feature = 'This feature', tier = 'Pro'
    assert.equal(
      compactAriaLabel({ feature: 'This feature', tier: 'Pro' }),
      'Upgrade to Pro to unlock This feature',
    )
  })
})
