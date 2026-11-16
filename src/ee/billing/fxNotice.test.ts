/**
 * fxNotice.test.mjs — tests for src/ee/billing/FxNotice.jsx.
 *
 * FxNotice.jsx has no pure exported helpers to unit-test in isolation — its
 * entire job is rendering the customer-facing ZAR/USD disclosure copy, and
 * it can't be imported directly under plain `node --test` (JSX syntax, no
 * transform loader registered here — see billingPage.logic.test.mjs for the
 * full explanation of why .jsx files in src/ee/billing/ are mirrored/scanned
 * rather than imported).
 *
 * So instead of mirroring (which would silently drift from the real copy if
 * someone edited the component and forgot the test), this file reads the
 * ACTUAL shipped source text via fs and asserts on the literal disclosure
 * string. That gives real, non-mirrored coverage of the exact wording
 * customers see, and fails loudly if the disclosure is edited without
 * updating this guard.
 *
 * We also mirror the tiny pure fmtDate() helper (trivial, no imports) to
 * cover its edge cases directly.
 *
 * Run: npm run test:dash  (node --test 'src/**\/*.test.mjs')
 */

import test, { describe } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'

const __dirname = dirname(fileURLToPath(import.meta.url))
const SOURCE = readFileSync(join(__dirname, 'FxNotice.tsx'), 'utf8')

// ---------------------------------------------------------------------------
// Copy content — the honest ZAR-charged / USD-anchored disclosure
// ---------------------------------------------------------------------------

describe('FxNotice disclosure copy (real shipped source text)', () => {
  test('states prices are set in USD and converted to ZAR', () => {
    assert.match(SOURCE, /prices are set in US dollars \(USD\)/)
    assert.match(SOURCE, /converted to South African rand \(ZAR\)/)
  })

  test('explicitly says the ZAR amount may vary slightly cycle to cycle', () => {
    assert.match(SOURCE, /vary slightly from cycle to cycle/)
  })

  test('explicitly says the USD price stays fixed for the plan duration', () => {
    assert.match(SOURCE, /USD price remains fixed for the/)
    assert.match(SOURCE, /duration of your plan/)
  })

  test('discloses the FX source cadence (daily refresh, tier-1 provider)', () => {
    assert.match(SOURCE, /tier-1 FX provider/)
    assert.match(SOURCE, /refreshed daily/)
  })

  test('discloses that non-ZAR cards may incur a foreign-transaction fee', () => {
    assert.match(SOURCE, /foreign-transaction fee/)
  })

  test('provides a contact address for billing questions', () => {
    assert.match(SOURCE, /billing@nubi\.io/)
  })

  test('the full (non-compact) variant renders as a labelled disclosure region', () => {
    assert.match(SOURCE, /aria-label="ZAR pricing disclosure"/)
  })
})

// ---------------------------------------------------------------------------
// Prop-contract sanity — confirms the documented default props still exist
// (guards against a signature change silently breaking every caller)
// ---------------------------------------------------------------------------

describe('FxNotice prop contract (source-level guard)', () => {
  test('rate/updatedAt/isFallback/compact all have documented safe defaults', () => {
    assert.match(SOURCE, /rate\s*=\s*null/)
    assert.match(SOURCE, /updatedAt\s*=\s*null/)
    assert.match(SOURCE, /isFallback\s*=\s*false/)
    assert.match(SOURCE, /compact\s*=\s*false/)
  })

  test('a fallback (reference-estimate) rate is called out distinctly from a live rate', () => {
    assert.match(SOURCE, /reference estimate/)
  })
})

// ---------------------------------------------------------------------------
// Mirror: fmtDate() — FxNotice.jsx lines ~30-45 (trivial, no imports; the
// mirror is a same-length copy so it doubles as a regression pin for the
// exact formatting options used, e.g. day/month/year order). Includes the
// Number.isNaN(d.getTime()) guard added alongside these tests: Node/ICU's
// toLocaleDateString does NOT throw on an invalid Date — it silently returns
// the literal string "Invalid Date" — so the guard (and this test) protect
// against that string ever leaking into the "About ZAR pricing" disclosure.
// ---------------------------------------------------------------------------

function fmtDate(iso) {
  if (!iso) return ''
  try {
    const d = new Date(iso)
    if (Number.isNaN(d.getTime())) return ''
    return d.toLocaleDateString('en-ZA', {
      day: 'numeric',
      month: 'short',
      year: 'numeric',
    })
  } catch {
    return ''
  }
}

describe('fmtDate() helper', () => {
  test('formats a valid ISO date (en-ZA locale zero-pads the day even with day:"numeric")', () => {
    assert.equal(fmtDate('2026-06-08T00:00:00Z'), '08 Jun 2026')
  })

  test('returns "" for null/undefined (omits the "updated" line)', () => {
    assert.equal(fmtDate(null), '')
    assert.equal(fmtDate(undefined), '')
  })

  test('returns "" — never the literal string "Invalid Date" — for a garbage date string', () => {
    const result = fmtDate('not-a-date')
    assert.equal(result, '')
    assert.notEqual(result, 'Invalid Date')
  })
})
