/**
 * registry.test.mjs — tests for the EE extension-point slot registry
 * (src/ee/registry.js).
 *
 * This is the open-core seam: core reads slots via getSlot(); EE fills them
 * via registerSlot(). registry.js has zero imports of its own (no JSX, no
 * import.meta.env), so — unlike the rest of src/ee/billing/*.jsx — it can be
 * imported directly here and exercised for real (no logic-mirroring needed).
 *
 * These tests exist to guard the exact contract the rest of the app depends
 * on for graceful OSS degradation: an unfilled slot must return null (never
 * throw, never undefined-that-crashes-on-.method-access), so that core code
 * written as `const X = getSlot('billing-page'); if (!X) return null` never
 * breaks when EE isn't loaded.
 *
 * Run: npm run test:dash  (node --test 'src/**\/*.test.mjs')
 */

import test, { beforeEach } from 'node:test'
import assert from 'node:assert/strict'
import { registerSlot, getSlot, hasSlot, onSlotRegistered, _resetRegistry } from './registry.js'

// The registry is a module-level singleton — reset it before every test so
// tests don't leak state into each other (mirrors _resetRegistry's stated
// "for testing" purpose in the source doc-comment).
beforeEach(() => {
  _resetRegistry()
})

// ---------------------------------------------------------------------------
// Core round-trip
// ---------------------------------------------------------------------------

test('registerSlot + getSlot round-trips any value (component, object, function)', () => {
  const FakeComponent = () => null
  registerSlot('billing-page', FakeComponent)
  assert.strictEqual(getSlot('billing-page'), FakeComponent)

  const configObj = { threshold: 10 }
  registerSlot('some-config', configObj)
  assert.strictEqual(getSlot('some-config'), configObj)
})

// ---------------------------------------------------------------------------
// Graceful-degradation contract: unfilled slot → null, never throws
// ---------------------------------------------------------------------------

test('getSlot returns null (not undefined) for a slot that was never registered', () => {
  const value = getSlot('wallet-panel')
  assert.strictEqual(value, null)
  // Explicitly guard against the common OSS-degradation bug: `undefined`
  // still passes a loose `!x` check, but callers may also do `x == null` or
  // render `{x}` directly — null is the documented, safe contract.
  assert.notStrictEqual(value, undefined)
})

test('hasSlot distinguishes "never registered" from "registered with a falsy value"', () => {
  assert.strictEqual(hasSlot('autotopup-settings'), false)
  registerSlot('autotopup-settings', null)
  assert.strictEqual(hasSlot('autotopup-settings'), true)
  // getSlot uses ?? so an explicitly-registered null still normalizes to null
  // (not accidentally becoming some other default) — behaviour stays graceful.
  assert.strictEqual(getSlot('autotopup-settings'), null)
})

test('OSS mode: with zero registerSlot calls, every known billing slot degrades to null', () => {
  const KNOWN_SLOTS = [
    'billing-page',
    'billing-account-page',
    'billing-nav-badge',
    'upgrade-prompt',
    'wallet-panel',
    'autotopup-settings',
  ]
  for (const name of KNOWN_SLOTS) {
    assert.strictEqual(getSlot(name), null, `${name} must degrade to null in OSS mode`)
    assert.strictEqual(hasSlot(name), false)
  }
})

// ---------------------------------------------------------------------------
// Last-writer-wins overwrite semantics
// ---------------------------------------------------------------------------

test('registerSlot overwrites a previous registration (last writer wins)', () => {
  registerSlot('upgrade-prompt', 'first')
  registerSlot('upgrade-prompt', 'second')
  assert.strictEqual(getSlot('upgrade-prompt'), 'second')
})

// ---------------------------------------------------------------------------
// Listener notifications
// ---------------------------------------------------------------------------

test('onSlotRegistered notifies listeners with (name, value) on every registerSlot call', () => {
  const calls = []
  const unsubscribe = onSlotRegistered((name, value) => calls.push([name, value]))

  registerSlot('billing-nav-badge', 'chip-v1')
  registerSlot('billing-nav-badge', 'chip-v2')

  assert.deepStrictEqual(calls, [
    ['billing-nav-badge', 'chip-v1'],
    ['billing-nav-badge', 'chip-v2'],
  ])

  unsubscribe()
})

test('unsubscribe stops further notifications to that listener', () => {
  const calls = []
  const unsubscribe = onSlotRegistered((name, value) => calls.push([name, value]))

  registerSlot('a', 1)
  unsubscribe()
  registerSlot('a', 2)

  assert.deepStrictEqual(calls, [['a', 1]])
})

test('a listener throwing does not stop other listeners or the registration', () => {
  const calls = []
  const unsubBad = onSlotRegistered(() => { throw new Error('boom') })
  const unsubGood = onSlotRegistered((name, value) => calls.push([name, value]))

  assert.doesNotThrow(() => registerSlot('billing-page', 'X'))
  assert.deepStrictEqual(calls, [['billing-page', 'X']])
  assert.strictEqual(getSlot('billing-page'), 'X')

  unsubBad()
  unsubGood()
})

test('multiple independent listeners can subscribe and unsubscribe without interfering', () => {
  const a = []
  const b = []
  const unsubA = onSlotRegistered((n, v) => a.push([n, v]))
  const unsubB = onSlotRegistered((n, v) => b.push([n, v]))

  registerSlot('x', 1)
  unsubA()
  registerSlot('x', 2)
  unsubB()
  registerSlot('x', 3) // no listeners left — must not throw

  assert.deepStrictEqual(a, [['x', 1]])
  assert.deepStrictEqual(b, [['x', 1], ['x', 2]])
})

// ---------------------------------------------------------------------------
// _resetRegistry
// ---------------------------------------------------------------------------

test('_resetRegistry clears all slots but leaves listeners registered', () => {
  const calls = []
  const unsubscribe = onSlotRegistered((n, v) => calls.push([n, v]))

  registerSlot('billing-page', 'X')
  assert.strictEqual(hasSlot('billing-page'), true)

  _resetRegistry()
  assert.strictEqual(hasSlot('billing-page'), false)
  assert.strictEqual(getSlot('billing-page'), null)

  // Listener survives the reset — re-registering still notifies it.
  registerSlot('billing-page', 'Y')
  assert.deepStrictEqual(calls, [['billing-page', 'X'], ['billing-page', 'Y']])

  unsubscribe()
})
