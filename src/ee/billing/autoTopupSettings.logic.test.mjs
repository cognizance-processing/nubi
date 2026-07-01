/**
 * autoTopupSettings.logic.test.mjs — logic tests for
 * src/ee/billing/AutoTopupSettings.jsx.
 *
 * Mirrored (not imported) for the same reason documented at the top of
 * billingPage.logic.test.mjs: the real component transitively imports
 * src/lib/api.js, whose unguarded `import.meta.env.VITE_BACKEND_URL` throws
 * under plain `node --test`. Each mirror below cites the source line range
 * in AutoTopupSettings.jsx it copies; keep them in sync on changes.
 *
 * Covers: config hydration (cents→USD), the hasCard gate that disables the
 * whole auto-topup toggle until a card is on file, and the handleSave
 * payload-construction / cap-nulling logic (form validation surface).
 *
 * Run: npm run test:dash  (node --test 'src/**\/*.test.mjs')
 */

import test, { describe } from 'node:test'
import assert from 'node:assert/strict'

// ---------------------------------------------------------------------------
// Mirror: centsToUsd / usdToCents (src/lib/ee/wallet.js lines ~122-136)
// ---------------------------------------------------------------------------

function centsToUsd(cents) {
  if (!cents) return 0
  return cents / 100
}

function usdToCents(usd) {
  if (!usd) return 0
  return Math.round(usd * 100)
}

// ---------------------------------------------------------------------------
// Mirror: the useEffect hydration block — AutoTopupSettings.jsx lines ~113-134
// ---------------------------------------------------------------------------

function hydrateFromConfig(config) {
  if (!config) return null // no-op: form keeps its current (default) state

  const state = {
    enabled: config.auto_topup_enabled ?? false,
    threshold: centsToUsd(config.threshold_usd_cents ?? 1000),
    topupAmount: centsToUsd(config.topup_amount_usd_cents ?? 5000),
  }

  if (config.monthly_topup_cap_usd_cents != null) {
    state.hasMonthlyCap = true
    state.monthlyCap = centsToUsd(config.monthly_topup_cap_usd_cents)
  } else {
    state.hasMonthlyCap = false
    state.monthlyCap = ''
  }

  if (config.spend_cap_usd_cents != null) {
    state.hasSpendCap = true
    state.spendCap = centsToUsd(config.spend_cap_usd_cents)
  } else {
    state.hasSpendCap = false
    state.spendCap = ''
  }

  return state
}

describe('config hydration (cents → USD form fields)', () => {
  test('null config (no EE wallet data yet) is a no-op — form keeps its defaults', () => {
    assert.equal(hydrateFromConfig(null), null)
  })

  test('hydrates enabled/threshold/topupAmount from cents', () => {
    const state = hydrateFromConfig({
      auto_topup_enabled: true,
      threshold_usd_cents: 2000,
      topup_amount_usd_cents: 10000,
    })
    assert.equal(state.enabled, true)
    assert.equal(state.threshold, 20)
    assert.equal(state.topupAmount, 100)
  })

  test('missing threshold/topupAmount fall back to the documented defaults ($10 / $50)', () => {
    const state = hydrateFromConfig({ auto_topup_enabled: false })
    assert.equal(state.threshold, 10)
    assert.equal(state.topupAmount, 50)
  })

  test('monthly cap absent (null) → hasMonthlyCap false, field cleared to ""', () => {
    const state = hydrateFromConfig({ monthly_topup_cap_usd_cents: null })
    assert.equal(state.hasMonthlyCap, false)
    assert.equal(state.monthlyCap, '')
  })

  test('monthly cap present → hasMonthlyCap true, field set in USD', () => {
    const state = hydrateFromConfig({ monthly_topup_cap_usd_cents: 30000 })
    assert.equal(state.hasMonthlyCap, true)
    assert.equal(state.monthlyCap, 300)
  })

  test('spend cap present → hasSpendCap true, field set in USD (independent of monthly cap)', () => {
    const state = hydrateFromConfig({ spend_cap_usd_cents: 50000 })
    assert.equal(state.hasSpendCap, true)
    assert.equal(state.spendCap, 500)
  })
})

// ---------------------------------------------------------------------------
// Mirror: hasCard — AutoTopupSettings.jsx line ~136
// ---------------------------------------------------------------------------

function hasCard(config) {
  return !!(config?.paystack_auth_reusable && config?.paystack_card_last4)
}

describe('hasCard gate (auto-topup toggle is disabled without a saved card)', () => {
  test('no config at all → false', () => {
    assert.equal(hasCard(null), false)
  })

  test('reusable auth but no last4 on file → false (defensive AND)', () => {
    assert.equal(hasCard({ paystack_auth_reusable: true, paystack_card_last4: null }), false)
  })

  test('last4 present but not marked reusable → false', () => {
    assert.equal(hasCard({ paystack_auth_reusable: false, paystack_card_last4: '4242' }), false)
  })

  test('both present → true, toggle becomes usable', () => {
    assert.equal(hasCard({ paystack_auth_reusable: true, paystack_card_last4: '4242' }), true)
  })
})

// ---------------------------------------------------------------------------
// Mirror: handleSave's payload construction — AutoTopupSettings.jsx
// lines ~144-155
// ---------------------------------------------------------------------------

function buildSavePayload({ enabled, threshold, topupAmount, hasMonthlyCap, monthlyCap, hasSpendCap, spendCap }) {
  return {
    auto_topup_enabled: enabled,
    threshold_usd_cents: usdToCents(Number(threshold)),
    topup_amount_usd_cents: usdToCents(Number(topupAmount)),
    monthly_topup_cap_usd_cents: hasMonthlyCap && monthlyCap
      ? usdToCents(Number(monthlyCap))
      : null,
    spend_cap_usd_cents: hasSpendCap && spendCap
      ? usdToCents(Number(spendCap))
      : null,
  }
}

describe('handleSave payload construction (form validation surface)', () => {
  test('caps unchecked → sent as null (clears any previously-set cap on the backend)', () => {
    const payload = buildSavePayload({
      enabled: true, threshold: 10, topupAmount: 50,
      hasMonthlyCap: false, monthlyCap: '', hasSpendCap: false, spendCap: '',
    })
    assert.equal(payload.monthly_topup_cap_usd_cents, null)
    assert.equal(payload.spend_cap_usd_cents, null)
  })

  test('caps checked with a value → converted to cents', () => {
    const payload = buildSavePayload({
      enabled: true, threshold: 10, topupAmount: 50,
      hasMonthlyCap: true, monthlyCap: 300, hasSpendCap: true, spendCap: 500,
    })
    assert.equal(payload.monthly_topup_cap_usd_cents, 30000)
    assert.equal(payload.spend_cap_usd_cents, 50000)
  })

  test('cap checked but field left blank ("") still nulls out (guards against sending 0)', () => {
    const payload = buildSavePayload({
      enabled: true, threshold: 10, topupAmount: 50,
      hasMonthlyCap: true, monthlyCap: '', hasSpendCap: false, spendCap: '',
    })
    assert.equal(payload.monthly_topup_cap_usd_cents, null)
  })

  test('threshold/topupAmount are always sent as cents regardless of cap state', () => {
    const payload = buildSavePayload({
      enabled: false, threshold: 25, topupAmount: 75,
      hasMonthlyCap: false, monthlyCap: '', hasSpendCap: false, spendCap: '',
    })
    assert.equal(payload.threshold_usd_cents, 2500)
    assert.equal(payload.topup_amount_usd_cents, 7500)
    assert.equal(payload.auto_topup_enabled, false)
  })
})

// ---------------------------------------------------------------------------
// Graceful degradation: setAutoTopup() fails (EE wallet endpoint 404s / any
// network error) → save error surfaced, not a crash. Mirrors the
// try/catch/finally in handleSave — lines ~144-165.
// ---------------------------------------------------------------------------

describe('handleSave graceful-degradation path', () => {
  async function runSave(setAutoTopupFn) {
    let saving = true
    let saveError = null
    let savedOk = false
    try {
      await setAutoTopupFn()
      savedOk = true
    } catch (err) {
      saveError = err?.message ?? 'Failed to save settings. Please try again.'
    } finally {
      saving = false
    }
    return { saving, saveError, savedOk }
  }

  test('OSS mode: wallet/autotopup endpoint 404s → saveError set, no throw escapes', async () => {
    const notFound = new Error('Request failed: 404 Not Found')
    let threw = false
    let result
    try {
      result = await runSave(async () => { throw notFound })
    } catch {
      threw = true
    }
    assert.equal(threw, false)
    assert.equal(result.savedOk, false)
    assert.equal(result.saveError, 'Request failed: 404 Not Found')
    assert.equal(result.saving, false)
  })

  test('success path clears any prior error and flips savedOk', async () => {
    const result = await runSave(async () => ({ auto_topup_enabled: true }))
    assert.equal(result.savedOk, true)
    assert.equal(result.saveError, null)
  })
})
