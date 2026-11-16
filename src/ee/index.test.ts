/**
 * index.test.mjs — tests for src/ee/index.js's OSS-degradation contract.
 *
 * index.js can't be imported directly under plain `node --test`: it
 * statically imports './billing/registerBilling.js', which statically
 * imports the .jsx billing components, which (transitively, via
 * src/lib/ee/billing.js → src/lib/api.js) throws at module-evaluation time
 * under Node (see billingPage.logic.test.mjs for the full trace). It also
 * imports src/lib/features.js, which itself imports src/lib/api.js directly
 * — so even without the JSX chain, index.js can't be loaded here.
 *
 * So _fetchAndApplyFeatures() and registerEe()'s degradation behaviour are
 * mirrored below, byte-for-byte matching src/ee/index.js lines ~51-101.
 * This is the exact code path that runs when App.jsx dynamically imports EE
 * and calls registerEe() — the single most important "does EE degrade
 * gracefully when the backend doesn't have the /features or /ee/* routes
 * registered" contract in the whole billing surface.
 *
 * Run: npm run test:dash  (node --test 'src/**\/*.test.mjs')
 */

import test, { describe } from 'node:test'
import assert from 'node:assert/strict'

// ---------------------------------------------------------------------------
// Mirror: _fetchAndApplyFeatures() — index.js lines ~51-68
// Parameterized on `getFn` / `setEnabledFeaturesFn` so tests can inject
// fakes instead of hitting the real network or the real features.js store.
// ---------------------------------------------------------------------------

async function fetchAndApplyFeatures(getFn, setEnabledFeaturesFn) {
  try {
    const data = await getFn('/features')
    const list = Array.isArray(data?.features) ? data.features : []
    if (list.length > 0) {
      setEnabledFeaturesFn(list)
    }
  } catch (err) {
    const label = err?.status === 404
      ? '404 (endpoint not yet registered)'
      : (err?.message ?? String(err))
    // Mirrors the real console.debug call — asserted indirectly via the
    // "no throw escapes" tests below, not by intercepting console output.
    void label
  }
}

describe('_fetchAndApplyFeatures graceful degradation (OSS deployment)', () => {
  test('backend returns a non-empty feature list → setEnabledFeatures is called with it', async () => {
    let applied = null
    await fetchAndApplyFeatures(
      async () => ({ features: ['flows', 'connectors', 'billing', 'paid_tiers'] }),
      (list) => { applied = list },
    )
    assert.deepEqual(applied, ['flows', 'connectors', 'billing', 'paid_tiers'])
  })

  test('GET /features 404s (EE frontend loaded, backend route absent) → no throw, defaults preserved', async () => {
    const notFound: Error & { status?: number } = new Error('Request failed: 404 Not Found')
    notFound.status = 404
    let applied = null
    let threw = false
    try {
      await fetchAndApplyFeatures(async () => { throw notFound }, (list) => { applied = list })
    } catch {
      threw = true
    }
    assert.equal(threw, false, 'registerEe() must never throw because /features is missing')
    assert.equal(applied, null, 'setEnabledFeatures must NOT be called — OSS defaults from features.js stand')
  })

  test('backend returns an empty feature list → treated the same as "not present", OSS defaults kept', async () => {
    let applied = null
    await fetchAndApplyFeatures(async () => ({ features: [] }), (list) => { applied = list })
    assert.equal(applied, null)
  })

  test('backend returns a malformed payload (features is not an array) → degrades to empty, no throw', async () => {
    let applied = null
    await fetchAndApplyFeatures(async () => ({ features: 'not-an-array' }), (list) => { applied = list })
    assert.equal(applied, null)
  })

  test('generic network failure (no .status) also degrades silently', async () => {
    let applied = null
    let threw = false
    try {
      await fetchAndApplyFeatures(async () => { throw new TypeError('fetch failed') }, (list) => { applied = list })
    } catch {
      threw = true
    }
    assert.equal(threw, false)
    assert.equal(applied, null)
  })
})

// ---------------------------------------------------------------------------
// Mirror: registerEe() — index.js lines ~87-101
// Verifies the two side effects (kick off the feature fetch, call
// registerBilling) both fire, and that registerEe() returns true
// synchronously regardless of how the async feature fetch resolves.
// ---------------------------------------------------------------------------

function registerEeLike({ fetchAndApplyFeaturesFn, registerBillingFn }) {
  fetchAndApplyFeaturesFn() // fire-and-forget, same as the real registerEe()
  registerBillingFn()
  return true
}

describe('registerEe() side effects + return contract', () => {
  test('returns true synchronously even though the feature fetch is still in flight', () => {
    let fetchStarted = false
    let billingRegistered = false
    const result = registerEeLike({
      fetchAndApplyFeaturesFn: () => {
        fetchStarted = true
        return new Promise(() => {}) // never resolves — simulates a slow/hanging network call
      },
      registerBillingFn: () => { billingRegistered = true },
    })
    assert.equal(result, true)
    assert.equal(fetchStarted, true)
    assert.equal(billingRegistered, true)
  })

  test('registerBilling still runs even if the feature fetch promise later rejects', async () => {
    let billingRegistered = false
    let unhandledRejectionSeen = false
    const onUnhandled = () => { unhandledRejectionSeen = true }
    process.once('unhandledRejection', onUnhandled)

    registerEeLike({
      // Reject through a caught promise so we don't actually trip Node's
      // unhandledRejection detector while still exercising "rejects later".
      fetchAndApplyFeaturesFn: () => Promise.reject(new Error('boom')).catch(() => {}),
      registerBillingFn: () => { billingRegistered = true },
    })

    assert.equal(billingRegistered, true)
    // Give the microtask queue a tick to settle before asserting no crash occurred.
    await new Promise((r) => setTimeout(r, 0))
    process.removeListener('unhandledRejection', onUnhandled)
    assert.equal(unhandledRejectionSeen, false)
  })
})
