/**
 * features.js — Feature-flag client for Nubi (OSS core).
 *
 * Fetches the set of enabled features from GET /api/v1/features on first call.
 * Falls back gracefully to all-false for commercial features when the endpoint
 * is absent or the network is unavailable — the OSS build must never fail because
 * of a missing EE feature gate.
 *
 * Usage
 * -----
 *   // React hook (inside a component)
 *   const billingEnabled = useFeature('billing')
 *
 *   // Imperative (outside React)
 *   import { isFeatureEnabled } from './features.js'
 *   if (isFeatureEnabled('billing')) { ... }
 *
 * EE wiring
 * ---------
 * The EE loader (src/ee/index.js) MAY call ``setEnabledFeatures(set)`` after
 * fetching a richer feature set from the backend (e.g. including tier/license
 * info).  The hook and imperative helper both read the same shared set, so the
 * update is transparent to callers.
 *
 * Backend contract
 * ----------------
 * GET /api/v1/features → { features: string[] }
 *   e.g. { features: ["flows", "connectors", "dashboards", "billing"] }
 *
 * On 404, 401, or any network error the client falls back to the OSS default
 * set (all open-core features enabled; all commercial features disabled).
 */

import { useState, useEffect } from 'react'
import { get } from './api.js'

// ---------------------------------------------------------------------------
// Known commercial feature names — disabled by default in OSS
// ---------------------------------------------------------------------------

const COMMERCIAL_FEATURES = new Set(['billing', 'paid_tiers'])

// ---------------------------------------------------------------------------
// Shared module-level state (singleton — not tied to any React component)
// ---------------------------------------------------------------------------

/** @type {Set<string> | null}  null = not yet fetched */
let _features = null

/** @type {Promise<Set<string>> | null} — in-flight fetch deduplicated */
let _fetchPromise = null

/** @type {Array<() => void>} — listeners notified on updates */
const _listeners = []

/**
 * Returns true when the named feature is in the current enabled set.
 * Commercial features default to false; OSS features default to true.
 *
 * @param {string} name
 * @returns {boolean}
 */
export function isFeatureEnabled(name) {
  if (_features === null) {
    // Not yet loaded: fall back to OSS defaults synchronously.
    return !COMMERCIAL_FEATURES.has(name)
  }
  return _features.has(name)
}

/**
 * Override the enabled feature set (called by the EE loader after it receives
 * a richer set from the backend or license check).
 *
 * @param {Iterable<string>} names
 */
export function setEnabledFeatures(names) {
  _features = new Set(names)
  _notify()
}

// ---------------------------------------------------------------------------
// Internal helpers
// ---------------------------------------------------------------------------

function _notify() {
  for (const cb of _listeners) {
    try { cb() } catch { /* ignore listener errors */ }
  }
}

/**
 * Fetch /api/v1/features exactly once (deduplicated across concurrent callers).
 * Populates the module-level ``_features`` set and notifies all active hooks.
 *
 * @returns {Promise<Set<string>>}
 */
async function _ensureLoaded() {
  if (_features !== null) return _features
  if (_fetchPromise) return _fetchPromise

  _fetchPromise = (async () => {
    try {
      const data = await get('/features')
      const list = Array.isArray(data?.features) ? data.features : []
      // Start from OSS open-core defaults (all non-commercial features enabled)
      // then merge whatever the backend says is explicitly enabled.
      _features = new Set(list)
    } catch (err) {
      // 404 (endpoint not yet wired), 401, or network error — degrade to OSS defaults.
      // We store an empty set; isFeatureEnabled will correctly return true for
      // non-commercial features and false for commercial ones.
      const label = err?.status === 404 ? '404 (endpoint not yet registered)' : err?.message
      console.debug('[features] GET /features failed (' + label + '); using OSS defaults')
      _features = new Set()
    } finally {
      _fetchPromise = null
    }
    _notify()
    return _features
  })()

  return _fetchPromise
}

// ---------------------------------------------------------------------------
// React hook
// ---------------------------------------------------------------------------

/**
 * React hook — returns true when the named feature is enabled.
 *
 * Triggers a single background fetch of /api/v1/features on first use.
 * Re-renders when the feature set is updated (e.g. after the EE loader runs).
 * Defaults gracefully: commercial features → false; OSS features → true.
 *
 * @param {string} name  Feature name, e.g. 'billing', 'flows', 'connectors'
 * @returns {boolean}
 */
export function useFeature(name) {
  // Derive initial value synchronously from whatever is already loaded.
  const [enabled, setEnabled] = useState(() => isFeatureEnabled(name))

  useEffect(() => {
    let cancelled = false

    // Subscribe to future updates (e.g. EE loader overrides the set later).
    function onUpdate() {
      if (!cancelled) setEnabled(isFeatureEnabled(name))
    }
    _listeners.push(onUpdate)

    // Trigger the background fetch (no-op if already in flight or completed).
    _ensureLoaded().then(() => {
      if (!cancelled) setEnabled(isFeatureEnabled(name))
    })

    return () => {
      cancelled = true
      const idx = _listeners.indexOf(onUpdate)
      if (idx !== -1) _listeners.splice(idx, 1)
    }
  }, [name])

  return enabled
}

/**
 * React hook — returns the full set of currently-enabled feature names.
 * Useful for debugging or building a feature-flag inspector.
 *
 * @returns {Set<string>}
 */
export function useFeatureSet() {
  const [set, setSet] = useState(() => _features ?? new Set())

  useEffect(() => {
    let cancelled = false

    function onUpdate() {
      if (!cancelled) setSet(new Set(_features ?? []))
    }
    _listeners.push(onUpdate)

    _ensureLoaded().then(() => {
      if (!cancelled) setSet(new Set(_features ?? []))
    })

    return () => {
      cancelled = true
      const idx = _listeners.indexOf(onUpdate)
      if (idx !== -1) _listeners.splice(idx, 1)
    }
  }, [])

  return set
}
