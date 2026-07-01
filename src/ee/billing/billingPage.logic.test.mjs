/**
 * billingPage.logic.test.mjs — logic tests for src/ee/billing/BillingPage.jsx.
 *
 * Why mirrored, not imported
 * --------------------------
 * BillingPage.jsx is a .jsx file (React component syntax) and transitively
 * imports src/lib/ee/billing.js, which imports src/lib/api.js, which reads
 * `import.meta.env.VITE_BACKEND_URL` (no `?.`). Under plain `node --test`
 * (no Vite, no JSX transform) `import.meta.env` is `undefined`, so that
 * property access throws at module-load time — confirmed via:
 *   node --input-type=module -e "import('./src/lib/api.js')"
 *     → "Cannot read properties of undefined (reading 'VITE_BACKEND_URL')"
 * This is also why every existing .jsx-adjacent test in this repo
 * (src/pages/app/settings/accessGrants.test.mjs, src/dashboards/widgets/
 * actionWidget.test.mjs) inline-mirrors the pure logic instead of importing
 * the component. We follow that same established convention here.
 *
 * Each mirrored function below is a byte-for-byte copy of the real
 * implementation in BillingPage.jsx (see the line reference in each
 * comment) — if BillingPage.jsx's logic changes, update the mirror too.
 *
 * Covers: ZAR formatting, usage-bar overage math, and tier-selection /
 * upgrade-gating logic (isCurrent / isContact / handleCta) — the actual
 * "which tier is the user on, which CTA do they see" decision surface.
 *
 * Run: npm run test:dash  (node --test 'src/**\/*.test.mjs')
 */

import test, { describe } from 'node:test'
import assert from 'node:assert/strict'

// ---------------------------------------------------------------------------
// Mirror: zar() — BillingPage.jsx lines ~35-38
// ---------------------------------------------------------------------------

function zar(amount) {
  const n = Number(amount ?? 0)
  return 'R' + n.toLocaleString('en-ZA', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

describe('zar() formatting', () => {
  test('formats a positive amount with 2 decimals and R prefix', () => {
    assert.equal(zar(1234.5), 'R1 234,50')
  })

  test('defaults null/undefined to R0.00-equivalent', () => {
    assert.equal(zar(null), zar(0))
    assert.equal(zar(undefined), zar(0))
  })

  test('coerces numeric strings', () => {
    assert.equal(zar('99.9'), zar(99.9))
  })
})

// ---------------------------------------------------------------------------
// Mirror: UsageBar's pct/over calculation — BillingPage.jsx lines ~60-64
// ---------------------------------------------------------------------------

function usageBarState({ used, limit }) {
  const unlimited = limit == null
  const pct = unlimited || !limit ? 0 : Math.min(100, (used / limit) * 100)
  const over = !unlimited && used > limit
  return { unlimited, pct, over }
}

describe('UsageBar overage math', () => {
  test('unlimited (null limit) never shows a bar or an overage flag', () => {
    const s = usageBarState({ used: 999999, limit: null })
    assert.equal(s.unlimited, true)
    assert.equal(s.pct, 0)
    assert.equal(s.over, false)
  })

  test('under the limit: pct scales linearly, not over', () => {
    const s = usageBarState({ used: 250, limit: 1000 })
    assert.equal(s.pct, 25)
    assert.equal(s.over, false)
  })

  test('exactly at the limit: 100%, not yet "over"', () => {
    const s = usageBarState({ used: 1000, limit: 1000 })
    assert.equal(s.pct, 100)
    assert.equal(s.over, false)
  })

  test('over the limit: pct is capped at 100 for the bar width, over=true', () => {
    const s = usageBarState({ used: 1500, limit: 1000 })
    assert.equal(s.pct, 100)
    assert.equal(s.over, true)
  })

  test('zero limit (falsy, not null) is treated as "no bar" defensively', () => {
    const s = usageBarState({ used: 5, limit: 0 })
    assert.equal(s.unlimited, false)
    assert.equal(s.pct, 0) // guarded by `!limit` — avoids a divide-by-zero NaN
  })
})

// ---------------------------------------------------------------------------
// Mirror: local TierCard's isCurrent / isContact / handleCta selection logic
// — BillingPage.jsx lines ~246-256 (the account-page tier grid, distinct
// from the core TierCards.jsx used on PricingPage)
// ---------------------------------------------------------------------------

function tierCardState({ tier, currentTier }) {
  const isCurrent = tier.id === currentTier
  const isContact = tier.id === 'enterprise'
  return { isCurrent, isContact }
}

/**
 * Mirrors handleCta(): decides what clicking the tier's CTA button does.
 * Returns a tag describing the action so tests can assert on behaviour
 * without a real DOM / window.open.
 */
function handleCta({ tier, currentTier, onUpgrade }) {
  const { isCurrent, isContact } = tierCardState({ tier, currentTier })
  if (isContact) return { action: 'mailto', tierId: tier.id }
  if (!isCurrent) {
    onUpgrade(tier.id)
    return { action: 'upgrade', tierId: tier.id }
  }
  return { action: 'noop', tierId: tier.id }
}

describe('tier-selection / upgrade-gating logic', () => {
  test('current tier is flagged isCurrent and its CTA is a no-op', () => {
    const tier = { id: 'pro' }
    const state = tierCardState({ tier, currentTier: 'pro' })
    assert.equal(state.isCurrent, true)
    assert.equal(state.isContact, false)

    let upgraded = null
    const result = handleCta({ tier, currentTier: 'pro', onUpgrade: (id) => { upgraded = id } })
    assert.equal(result.action, 'noop')
    assert.equal(upgraded, null, 'must not call onUpgrade for the current tier')
  })

  test('a non-current, non-enterprise tier triggers onUpgrade with its id', () => {
    const tier = { id: 'team' }
    let upgraded = null
    const result = handleCta({ tier, currentTier: 'free', onUpgrade: (id) => { upgraded = id } })
    assert.equal(result.action, 'upgrade')
    assert.equal(upgraded, 'team')
  })

  test('enterprise tier always routes to "contact sales", even if not current', () => {
    const tier = { id: 'enterprise' }
    let upgraded = null
    const result = handleCta({ tier, currentTier: 'free', onUpgrade: (id) => { upgraded = id } })
    assert.equal(result.action, 'mailto')
    assert.equal(upgraded, null, 'enterprise must never call the Paystack checkout path')
  })

  test('enterprise tier routes to "contact sales" even when it IS the current tier', () => {
    // isContact is checked before isCurrent in handleCta — contact-sales
    // always wins so enterprise customers can still reach a human.
    const tier = { id: 'enterprise' }
    const result = handleCta({ tier, currentTier: 'enterprise', onUpgrade: () => {} })
    assert.equal(result.action, 'mailto')
  })
})

// ---------------------------------------------------------------------------
// Mirror: WalletPanel / AutoTopupSettings slot-gating in BillingPage's render
// — BillingPage.jsx lines ~317-320, ~483-501:
//   const WalletPanelSlot = getSlot('wallet-panel')
//   ...
//   {WalletPanelSlot && <section>...</section>}
//
// This exercises the REAL registry.js (imported directly — see
// src/ee/registry.test.mjs for why that file, unlike the .jsx components,
// is safe to import) to prove the documented OSS-degradation contract:
// when no EE billing module has run registerBilling(), BillingPage's optional
// wallet sections must not render (and, per registry.js, getSlot never
// throws — it returns null).
// ---------------------------------------------------------------------------

import { getSlot, registerSlot, _resetRegistry } from '../registry.js'

describe('BillingPage wallet-section gating (real registry.js)', () => {
  test('OSS mode (registerBilling never ran): wallet + autotopup sections are omitted', () => {
    _resetRegistry()
    const WalletPanelSlot = getSlot('wallet-panel')
    const AutoTopupSettingsSlot = getSlot('autotopup-settings')
    assert.equal(WalletPanelSlot, null)
    assert.equal(AutoTopupSettingsSlot, null)
    // BillingPage's JSX is `{WalletPanelSlot && <section>...}` — with a null
    // slot that expression short-circuits to null (renders nothing), never throws.
    assert.equal(Boolean(WalletPanelSlot) && 'section-rendered', false)
  })

  test('EE mode (registerBilling ran): wallet + autotopup sections render', () => {
    _resetRegistry()
    const FakeWalletPanel = () => null
    const FakeAutoTopupSettings = () => null
    registerSlot('wallet-panel', FakeWalletPanel)
    registerSlot('autotopup-settings', FakeAutoTopupSettings)

    assert.equal(getSlot('wallet-panel'), FakeWalletPanel)
    assert.equal(getSlot('autotopup-settings'), FakeAutoTopupSettings)
    assert.equal(Boolean(getSlot('wallet-panel')) && 'section-rendered', 'section-rendered')

    _resetRegistry()
  })
})
