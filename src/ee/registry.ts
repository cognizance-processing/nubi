/**
 * EE Extension-Point Registry (src/ee/registry.js)
 *
 * Allows EE components and pages to slot into named core mount points without
 * core ever importing from src/ee.  Core queries the registry; EE populates it.
 *
 * Design
 * ------
 * - Core defines "slots" (string names) and reads them at render time via
 *   ``getSlot(name)``.  If the slot is empty (EE not loaded) the call returns
 *   null and core falls back gracefully.
 * - EE's ``registerEe()`` function calls ``registerSlot(name, component)`` to
 *   fill slots.  This happens lazily after the EE module dynamic-imports.
 * - Slots can hold React components, config objects, or any value.
 *
 * Known slot names (by convention — not validated here):
 *   'billing-page'       — Full-page billing UI component (BillingFrontendAgent)
 *   'billing-nav-badge'  — Small nav badge showing plan name / usage
 *   'upgrade-prompt'     — Inline upgrade CTA block
 *
 * Usage in core
 * -------------
 *   import { getSlot } from '../ee/registry.js'   // <-- only import allowed from core to ee
 *
 *   // WRONG — core must never statically import src/ee directly:
 *   // import { BillingPage } from '../ee/BillingPage.jsx'
 *
 *   // RIGHT — read via registry at render time:
 *   const BillingPage = getSlot('billing-page')
 *   if (!BillingPage) return null   // graceful degradation
 *
 * Usage in EE
 * -----------
 *   import { registerSlot } from './registry.js'
 *   registerSlot('billing-page', BillingPage)
 *
 * Note: this file lives inside src/ee/ but it IS imported by core (App.jsx
 * imports it for the dynamic EE mount).  It is intentionally a thin, side-effect-
 * free registry with no EE business logic so the import boundary is safe.
 */

// ---------------------------------------------------------------------------
// Registry store
// ---------------------------------------------------------------------------

/** @type {Map<string, any>} */
const _registry = new Map()

/** @type {Array<(name: string, value: any) => void>} */
const _slotListeners = []

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

/**
 * Register a value (component, config, function) under a named slot.
 * Overwrites any previous registration (last writer wins).
 * Notifies active slot listeners so dynamic consumers re-render.
 *
 * @param {string} name   Slot name, e.g. 'billing-page'
 * @param {any}    value  React component or any serialisable value
 */
export function registerSlot(name, value) {
  _registry.set(name, value)
  for (const cb of _slotListeners) {
    try { cb(name, value) } catch { /* ignore */ }
  }
}

/**
 * Read the current value of a slot.
 * Returns null if the slot has not been filled (EE not loaded or feature disabled).
 *
 * @template T
 * @param {string} name
 * @returns {T | null}
 */
export function getSlot(name) {
  return _registry.get(name) ?? null
}

/**
 * Returns true if the named slot has been filled by EE.
 *
 * @param {string} name
 * @returns {boolean}
 */
export function hasSlot(name) {
  return _registry.has(name)
}

/**
 * Subscribe to slot registrations.  Callback fires whenever any slot is set.
 * Returns an unsubscribe function.
 *
 * @param {(name: string, value: any) => void} callback
 * @returns {() => void}
 */
export function onSlotRegistered(callback) {
  _slotListeners.push(callback)
  return () => {
    const idx = _slotListeners.indexOf(callback)
    if (idx !== -1) _slotListeners.splice(idx, 1)
  }
}

/**
 * Clear all registered slots (for testing).
 * NOT intended for production use.
 */
export function _resetRegistry() {
  _registry.clear()
}
