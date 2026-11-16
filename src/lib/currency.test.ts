/**
 * currency.test.mjs — display-currency catalog, conversion, and formatting.
 * Run: npm run test:dash  (node --test 'src/**\/*.test.mjs')
 */

import test from 'node:test'
import assert from 'node:assert/strict'
import {
  CURRENCIES, CURRENCY_ORDER, DEFAULT_CURRENCY, BILLING_CURRENCY,
  FALLBACK_RATES, convertFromUsd, formatMoney,
} from './currency.js'

test('USD is the default display currency; ZAR is the billing currency', () => {
  assert.equal(DEFAULT_CURRENCY, 'USD')
  assert.equal(BILLING_CURRENCY, 'ZAR')
  assert.equal(FALLBACK_RATES.USD, 1)
})

test('every ordered currency exists in the catalog with flag + symbol', () => {
  for (const code of CURRENCY_ORDER) {
    const c = CURRENCIES[code]
    assert.ok(c, `missing catalog entry: ${code}`)
    assert.equal(c.code, code)
    assert.ok(c.flag && c.symbol && c.name)
    assert.ok(typeof FALLBACK_RATES[code] === 'number' && FALLBACK_RATES[code] > 0, `no rate for ${code}`)
  }
})

test('convertFromUsd multiplies by the USD→X rate (USD is identity)', () => {
  assert.equal(convertFromUsd(9, 'USD', FALLBACK_RATES), 9)
  assert.ok(Math.abs(convertFromUsd(10, 'ZAR', { ZAR: 16.26 }) - 162.6) < 1e-9)
})

test('convertFromUsd falls back to the indicative table for a missing rate', () => {
  assert.equal(convertFromUsd(1, 'EUR', {}), FALLBACK_RATES.EUR)
})

test('formatMoney renders a whole-number money string in the target currency', () => {
  assert.equal(formatMoney(9, 'USD', FALLBACK_RATES), '$9')
  const eur = formatMoney(9, 'EUR', { EUR: 0.9 })
  assert.match(eur, /8/) // 9 × 0.9 = 8.1 → rounded, no cents
  assert.doesNotMatch(eur, /\./) // no fractional digits on plan prices
})
