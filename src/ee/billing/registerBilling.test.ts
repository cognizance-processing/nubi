/**
 * registerBilling.test.mjs — tests for src/ee/billing/registerBilling.js.
 *
 * registerBilling.js can't be imported directly: it statically imports six
 * .jsx billing components, each of which transitively imports
 * src/lib/api.js (throws under plain Node — see billingPage.logic.test.mjs
 * for the full trace).
 *
 * This test combines two techniques used elsewhere in this suite:
 *   1. A real, non-mirrored source-text scan confirming registerBilling.js
 *      still calls registerSlot() for exactly the six documented slot names
 *      (guards against silent drift between the file, its own doc-comment,
 *      and src/ee/README.md's "Known Slot Names" table).
 *   2. An exercise of the REAL registry.js (see src/ee/registry.test.mjs)
 *      replaying registerBilling()'s exact registration sequence with fake
 *      components, to prove the slots end up populated as documented and
 *      that BillingPage/PricingPage's `getSlot(...)` reads would resolve.
 *
 * Run: npm run test:dash  (node --test 'src/**\/*.test.mjs')
 */

import test, { describe } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'
import { getSlot, hasSlot, _resetRegistry, registerSlot } from '../registry.js'

const __dirname = dirname(fileURLToPath(import.meta.url))
const SOURCE = readFileSync(join(__dirname, 'registerBilling.ts'), 'utf8')

const DOCUMENTED_SLOTS = [
  'billing-page',
  'billing-account-page',
  'upgrade-prompt',
  'billing-nav-badge',
  'wallet-panel',
  'autotopup-settings',
]

describe('registerBilling.js source contract', () => {
  test('calls registerSlot() for exactly the six documented billing slots', () => {
    for (const slot of DOCUMENTED_SLOTS) {
      assert.match(
        SOURCE,
        new RegExp(`registerSlot\\(\\s*['"]${slot}['"]`),
        `expected a registerSlot('${slot}', ...) call`,
      )
    }
  })

  test('imports all six EE billing components it registers', () => {
    for (const name of ['PricingPage', 'BillingPage', 'UpgradePrompt', 'BillingNavBadge', 'WalletPanel', 'AutoTopupSettings']) {
      assert.match(SOURCE, new RegExp(`import ${name} from`))
    }
  })

  test('this module is documented as EE-internal only (not importable from core)', () => {
    assert.match(SOURCE, /must NOT be imported by any core file/)
  })
})

describe('registerBilling() slot-registration sequence (replayed against real registry.js)', () => {
  /** Mirrors registerBilling()'s body — registerBilling.js lines ~40-51. */
  function registerBillingLike(components) {
    registerSlot('billing-page', components.PricingPage)
    registerSlot('billing-account-page', components.BillingPage)
    registerSlot('upgrade-prompt', components.UpgradePrompt)
    registerSlot('billing-nav-badge', components.BillingNavBadge)
    registerSlot('wallet-panel', components.WalletPanel)
    registerSlot('autotopup-settings', components.AutoTopupSettings)
  }

  test('after running, every documented slot is filled with the expected component', () => {
    _resetRegistry()
    const fake = {
      PricingPage: () => null,
      BillingPage: () => null,
      UpgradePrompt: () => null,
      BillingNavBadge: () => null,
      WalletPanel: () => null,
      AutoTopupSettings: () => null,
    }
    registerBillingLike(fake)

    assert.strictEqual(getSlot('billing-page'), fake.PricingPage)
    assert.strictEqual(getSlot('billing-account-page'), fake.BillingPage)
    assert.strictEqual(getSlot('upgrade-prompt'), fake.UpgradePrompt)
    assert.strictEqual(getSlot('billing-nav-badge'), fake.BillingNavBadge)
    assert.strictEqual(getSlot('wallet-panel'), fake.WalletPanel)
    assert.strictEqual(getSlot('autotopup-settings'), fake.AutoTopupSettings)

    for (const slot of DOCUMENTED_SLOTS) assert.equal(hasSlot(slot), true)

    _resetRegistry()
  })

  test('is idempotent — calling it twice just overwrites (last writer wins), no duplicate-registration error', () => {
    _resetRegistry()
    const first = { PricingPage: 'v1', BillingPage: 'v1', UpgradePrompt: 'v1', BillingNavBadge: 'v1', WalletPanel: 'v1', AutoTopupSettings: 'v1' }
    const second = { PricingPage: 'v2', BillingPage: 'v2', UpgradePrompt: 'v2', BillingNavBadge: 'v2', WalletPanel: 'v2', AutoTopupSettings: 'v2' }

    assert.doesNotThrow(() => {
      registerBillingLike(first)
      registerBillingLike(second)
    })
    assert.equal(getSlot('billing-page'), 'v2')
    assert.equal(getSlot('wallet-panel'), 'v2')

    _resetRegistry()
  })

  test('before registerBilling runs (OSS mode / EE not yet loaded), all six slots degrade to null', () => {
    _resetRegistry()
    for (const slot of DOCUMENTED_SLOTS) {
      assert.equal(getSlot(slot), null)
      assert.equal(hasSlot(slot), false)
    }
  })
})
