/**
 * nubi-context.js — Shared context factory for multi-widget pages.
 *
 * createNubiContext({ token?, getTokenFn?, backend }) → NubiContext
 *
 * NubiContext provides:
 *  - resolveToken()        → Promise<string|null>  — resolves the JWT
 *  - fetch(queryId, sig?)  → Promise<Table>         — Arrow IPC fetch
 *  - bus: EventTarget       — in-page cross-filter event bus
 *  - emitFilter(col, val)   — broadcast a filter event on the bus
 *  - onFilter(handler)      → () => void             — subscribe; returns unsub fn
 *  - backend: string
 *
 * decodeScopes(token) — standalone export; base64-decodes the JWT payload
 *   and returns the scope array.  Never verifies the signature (UI gating only;
 *   the server is the real gate).
 */

import { resolveToken as resolveTokenAttr, fetchArrow } from './widgets/shared.js'

// ---------------------------------------------------------------------------
// Scope decoder (UI capability gating — cosmetic, server enforces real gate)
// ---------------------------------------------------------------------------

/**
 * Decode the `scope`, `scopes`, or `scp` claim from a JWT payload.
 * Handles both space-delimited strings (RFC 6749) and arrays.
 * Returns [] on any parse failure.
 *
 * Mirrors the Python `parse_scopes` in backend/app/auth/scopes.py:
 *   - accepts `scope` (string or list) or `scopes` fallback
 *   - also tolerates the `scp` claim (used by some OIDC providers)
 *
 * @param {string|null|undefined} token — raw JWT string
 * @returns {string[]}
 */
export function decodeScopes(token) {
  if (!token) return []
  try {
    const parts = token.split('.')
    if (parts.length < 2) return []
    // Base64url → base64 → JSON
    const b64 = parts[1].replace(/-/g, '+').replace(/_/g, '/')
    const padded = b64 + '='.repeat((4 - (b64.length % 4)) % 4)
    const json = atob(padded)
    const payload = JSON.parse(json)
    // Precedence: scope → scopes → scp (mirrors backend parse_scopes)
    const raw = payload.scope ?? payload.scopes ?? payload.scp ?? ''
    if (Array.isArray(raw)) return raw.map(String).filter(Boolean)
    // space-delimited string (RFC 6749)
    return String(raw).split(' ').filter(Boolean)
  } catch {
    return []
  }
}

/**
 * Return true if `scopes` contains the given required scope,
 * respecting wildcard patterns:
 *   '*'               → matches everything (super-admin sentinel)
 *   'read:*'          → matches any 'read:…' scope (including 'read:a:b')
 *   'read:dashboard:*'→ matches 'read:dashboard:abc' but NOT 'read:other:abc'
 *
 * Exactly mirrors the Python `has_scope` in backend/app/auth/scopes.py:
 *   prefix = granted[:-1]  (strip trailing '*')
 *   required.startswith(prefix)
 *
 * @param {string[]} scopes
 * @param {string} required
 * @returns {boolean}
 */
export function hasScope(scopes, required) {
  for (const granted of scopes) {
    if (granted === required) return true
    if (granted === '*') return true
    if (granted.endsWith(':*')) {
      // e.g. 'read:*' → prefix 'read:'  — matches any scope beginning with 'read:'
      const prefix = granted.slice(0, -1)  // everything before the trailing '*'
      if (required.startsWith(prefix)) return true
    }
  }
  return false
}

// ---------------------------------------------------------------------------
// Cross-filter bus
// ---------------------------------------------------------------------------

const FILTER_EVENT = 'nubi:filter'

// ---------------------------------------------------------------------------
// Context factory
// ---------------------------------------------------------------------------

/**
 * @typedef {Object} NubiContext
 * @property {() => Promise<string|null>} resolveToken
 * @property {(queryId: string, signal?: AbortSignal) => Promise<import('apache-arrow').Table>} fetch
 * @property {EventTarget} bus
 * @property {(column: string, value: unknown) => void} emitFilter
 * @property {(handler: (e: CustomEvent) => void) => () => void} onFilter
 * @property {string} backend
 */

/**
 * Create a shared NubiContext for coordinating multiple widgets on one page.
 *
 * @param {{ token?: string, getTokenFn?: () => Promise<string>, backend?: string }} opts
 * @returns {NubiContext}
 */
export function createNubiContext({ token, getTokenFn, backend = 'http://localhost:8000' } = {}) {
  const bus = new EventTarget()

  /** Resolve a JWT; static string > provided function > null */
  async function resolveToken() {
    if (token) return token
    if (typeof getTokenFn === 'function') {
      try { return await getTokenFn() ?? null } catch { return null }
    }
    return null
  }

  /**
   * Fetch an Arrow table for the given registered query ID.
   * @param {string} queryId
   * @param {AbortSignal} [signal]
   */
  async function fetch(queryId, signal) {
    const tok = await resolveToken()
    return fetchArrow(backend, queryId, tok, signal)
  }

  /** Broadcast a cross-filter selection on the bus. */
  function emitFilter(column, value) {
    bus.dispatchEvent(new CustomEvent(FILTER_EVENT, {
      detail: { column, value },
    }))
  }

  /**
   * Subscribe to cross-filter events.
   * @param {(e: CustomEvent) => void} handler
   * @returns {() => void} unsubscribe
   */
  function onFilter(handler) {
    bus.addEventListener(FILTER_EVENT, handler)
    return () => bus.removeEventListener(FILTER_EVENT, handler)
  }

  return { resolveToken, fetch, bus, emitFilter, onFilter, backend }
}
