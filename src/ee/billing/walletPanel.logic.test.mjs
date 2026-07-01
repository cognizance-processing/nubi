/**
 * walletPanel.logic.test.mjs — logic tests for src/ee/billing/WalletPanel.jsx.
 *
 * WalletPanel.jsx is a .jsx component that imports src/lib/ee/wallet.js,
 * which imports src/lib/api.js (throws at import time under plain Node —
 * see the header comment in billingPage.logic.test.mjs for the full
 * explanation). So, following this repo's established convention for
 * .jsx-adjacent logic tests, the pure helpers below are mirrored from the
 * real file rather than imported. Each mirror cites its source line range;
 * keep them in sync if WalletPanel.jsx's logic changes.
 *
 * Covers: wallet balance rendering thresholds, ledger/transaction sign
 * rendering, spend-meter math, and manual top-up form validation — plus the
 * "wallet endpoint 404s" graceful-degradation path (error state, not a crash).
 *
 * Run: npm run test:dash  (node --test 'src/**\/*.test.mjs')
 */

import test, { describe } from 'node:test'
import assert from 'node:assert/strict'

// ---------------------------------------------------------------------------
// Mirror: formatUsd / formatZarCents / centsToUsd / usdToCents
// (src/lib/ee/wallet.js lines ~100-136 — plain formatting helpers, safe to
// mirror since they have no external imports themselves)
// ---------------------------------------------------------------------------

function formatUsd(cents) {
  if (cents == null) return '$0.00'
  return '$' + (cents / 100).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

function usdToCents(usd) {
  if (!usd) return 0
  return Math.round(usd * 100)
}

function centsToUsd(cents) {
  if (!cents) return 0
  return cents / 100
}

// ---------------------------------------------------------------------------
// Mirror: spendFraction() — WalletPanel.jsx lines ~67-70
// ---------------------------------------------------------------------------

function spendFraction(spentCents, capCents) {
  if (!capCents || capCents <= 0) return 0
  return Math.min(spentCents / capCents, 1)
}

describe('spendFraction (spend meter math)', () => {
  test('no cap configured → 0 (renders "no cap" copy, not a bar)', () => {
    assert.equal(spendFraction(5000, null), 0)
    assert.equal(spendFraction(5000, 0), 0)
  })

  test('under the cap → linear fraction', () => {
    assert.equal(spendFraction(2500, 10000), 0.25)
  })

  test('over the cap → clamped to 1 (never renders a >100% bar)', () => {
    assert.equal(spendFraction(15000, 10000), 1)
  })
})

// ---------------------------------------------------------------------------
// Mirror: BalanceDisplay's low/zero thresholds — WalletPanel.jsx lines ~80-81
// ---------------------------------------------------------------------------

function balanceState(balanceUsdCents) {
  const low = balanceUsdCents != null && balanceUsdCents < 500 // < $5
  const zero = balanceUsdCents != null && balanceUsdCents <= 0
  return { low, zero }
}

describe('BalanceDisplay low/zero balance thresholds', () => {
  test('null balance (not yet loaded) is neither low nor zero', () => {
    assert.deepEqual(balanceState(null), { low: false, zero: false })
  })

  test('balance of exactly $5.00 (500 cents) is NOT low (boundary is exclusive)', () => {
    assert.deepEqual(balanceState(500), { low: false, zero: false })
  })

  test('balance below $5.00 is low but not zero', () => {
    assert.deepEqual(balanceState(499), { low: true, zero: false })
  })

  test('zero balance is both zero and low (depleted takes priority in the UI)', () => {
    assert.deepEqual(balanceState(0), { low: true, zero: true })
  })

  test('negative balance (should never happen, but defensively) is zero', () => {
    assert.deepEqual(balanceState(-100), { low: true, zero: true })
  })
})

// ---------------------------------------------------------------------------
// Mirror: LedgerRow's entry_type → sign resolution — WalletPanel.jsx
// lines ~206-208, cross-referenced against wallet.js's ENTRY_META
// (src/lib/ee/wallet.js lines ~147-160)
// ---------------------------------------------------------------------------

const ENTRY_META = {
  TOPUP_MANUAL:      { label: 'Manual top-up',     sign: 'credit'  },
  TOPUP_AUTO:        { label: 'Auto top-up',        sign: 'credit'  },
  TOPUP_PROMO:       { label: 'Promo credit',       sign: 'credit'  },
  TOPUP_FAILED:      { label: 'Top-up failed',      sign: 'neutral' },
  USAGE_LLM:         { label: 'AI / LLM usage',     sign: 'debit'   },
  USAGE_STORAGE:     { label: 'Storage',            sign: 'debit'   },
  USAGE_COMPUTE:     { label: 'Compute',            sign: 'debit'   },
  USAGE_EMBED:       { label: 'Embedded sessions',  sign: 'debit'   },
  USAGE_OVERAGE:     { label: 'Overage',            sign: 'debit'   },
  ADJUSTMENT_CREDIT: { label: 'Credit adjustment',  sign: 'credit'  },
  ADJUSTMENT_DEBIT:  { label: 'Debit adjustment',   sign: 'debit'   },
  EXPIRY:            { label: 'Credit expiry',      sign: 'debit'   },
}

function ledgerRowState(entry) {
  const meta = ENTRY_META[entry.entry_type] ?? { label: entry.entry_type, sign: 'neutral' }
  const isCredit = meta.sign === 'credit'
  const isDebit = meta.sign === 'debit'
  const signPrefix = isCredit ? '+' : isDebit ? '−' : ''
  return { label: meta.label, isCredit, isDebit, signPrefix }
}

describe('LedgerRow sign / label resolution', () => {
  test('a manual top-up renders as a credit with a "+" prefix', () => {
    const s = ledgerRowState({ entry_type: 'TOPUP_MANUAL', amount_usd_cents: 5000 })
    assert.equal(s.label, 'Manual top-up')
    assert.equal(s.isCredit, true)
    assert.equal(s.signPrefix, '+')
  })

  test('LLM usage renders as a debit with a minus prefix', () => {
    const s = ledgerRowState({ entry_type: 'USAGE_LLM', amount_usd_cents: -120 })
    assert.equal(s.isDebit, true)
    assert.equal(s.signPrefix, '−')
  })

  test('an unknown entry_type falls back to neutral with the raw type as its label', () => {
    const s = ledgerRowState({ entry_type: 'SOMETHING_NEW' })
    assert.equal(s.label, 'SOMETHING_NEW')
    assert.equal(s.isCredit, false)
    assert.equal(s.isDebit, false)
    assert.equal(s.signPrefix, '')
  })

  test('every ENTRY_META entry resolves to a real, non-empty label', () => {
    for (const [type, meta] of Object.entries(ENTRY_META)) {
      assert.ok(meta.label.length > 0, `${type} must have a label`)
      assert.ok(['credit', 'debit', 'neutral'].includes(meta.sign), `${type} has a valid sign`)
    }
  })
})

// ---------------------------------------------------------------------------
// Mirror: TopupForm's handleSubmit validation — WalletPanel.jsx lines ~261-278
// ---------------------------------------------------------------------------

/**
 * Mirrors the validation branch of handleSubmit: returns either
 * { ok: true, cents } or { ok: false, error }.
 */
function validateTopupAmount(amountUsd) {
  const cents = usdToCents(Number(amountUsd))
  if (!cents || cents < 100) {
    return { ok: false, error: 'Minimum top-up is $1.00.' }
  }
  return { ok: true, cents }
}

describe('TopupForm amount validation', () => {
  test('accepts a preset amount ($50)', () => {
    const result = validateTopupAmount(50)
    assert.equal(result.ok, true)
    assert.equal(result.cents, 5000)
  })

  test('rejects $0', () => {
    const result = validateTopupAmount(0)
    assert.equal(result.ok, false)
    assert.match(result.error, /Minimum top-up is \$1\.00/)
  })

  test('rejects a sub-$1 custom amount ($0.50)', () => {
    const result = validateTopupAmount(0.5)
    assert.equal(result.ok, false)
  })

  test('accepts exactly $1.00 (the documented minimum)', () => {
    const result = validateTopupAmount(1)
    assert.equal(result.ok, true)
    assert.equal(result.cents, 100)
  })

  test('rejects non-numeric input (e.g. an empty custom-amount field)', () => {
    const result = validateTopupAmount('')
    assert.equal(result.ok, false)
  })

  test('rejects negative amounts', () => {
    const result = validateTopupAmount(-10)
    assert.equal(result.ok, false)
  })
})

// ---------------------------------------------------------------------------
// Graceful degradation: wallet endpoint 404s / network failure → error state,
// never a crash. Mirrors WalletPanel's load() — lines ~387-398.
// ---------------------------------------------------------------------------

describe('WalletPanel load() graceful-degradation path', () => {
  /** Mirrors the try/catch/finally shape of load(). */
  async function runLoad(getWalletFn) {
    let state = null
    let loading = true
    let error = null
    loading = true
    error = null
    try {
      state = await getWalletFn()
    } catch (err) {
      error = err?.message ?? 'Failed to load wallet.'
    } finally {
      loading = false
    }
    return { state, loading, error }
  }

  test('EE backend present: resolves with wallet state, no error', async () => {
    const fakeWallet = { balance: { balance_usd_cents: 4200 }, ledger: [] }
    const { state, loading, error } = await runLoad(async () => fakeWallet)
    assert.deepEqual(state, fakeWallet)
    assert.equal(loading, false)
    assert.equal(error, null)
  })

  test('OSS mode: EE endpoint 404s → error state set, no throw escapes', async () => {
    const notFound = new Error('Request failed: 404 Not Found')
    notFound.status = 404
    let threw = false
    let result
    try {
      result = await runLoad(async () => { throw notFound })
    } catch {
      threw = true
    }
    assert.equal(threw, false, 'load() must catch the 404 itself, never let it propagate')
    assert.equal(result.state, null)
    assert.equal(result.error, 'Request failed: 404 Not Found')
    assert.equal(result.loading, false)
  })

  test('error without a .message still yields a usable fallback string', async () => {
    const { error } = await runLoad(async () => { throw {} })
    assert.equal(error, 'Failed to load wallet.')
  })
})

// ---------------------------------------------------------------------------
// Balance / USD formatting sanity (used throughout WalletPanel's render)
// ---------------------------------------------------------------------------

describe('formatUsd / centsToUsd / usdToCents round-trips', () => {
  test('formatUsd renders cents as a 2-decimal dollar string', () => {
    assert.equal(formatUsd(5000), '$50.00')
    assert.equal(formatUsd(1), '$0.01')
    assert.equal(formatUsd(null), '$0.00')
  })

  test('centsToUsd / usdToCents are inverse for whole-dollar amounts', () => {
    for (const usd of [1, 5, 10, 50, 100, 250]) {
      assert.equal(centsToUsd(usdToCents(usd)), usd)
    }
  })
})
