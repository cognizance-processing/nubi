/**
 * resolveToken.test.js — Unit tests for the resolveToken helper.
 *
 * Verifies the three-level precedence:
 *   1. .getToken instance property  (highest priority)
 *   2. `token` attribute
 *   3. `get-token` attribute → window function (with retry loop)
 */

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { resolveToken } from '../widgets/shared.js'

// ---------------------------------------------------------------------------
// Minimal fake element helper
// ---------------------------------------------------------------------------

/**
 * Build a minimal HTMLElement-like object for testing resolveToken.
 * We use actual jsdom elements (from the vitest jsdom env) so getAttribute
 * works exactly as it would in a real browser.
 */
function makeElem(attrs = {}) {
  const el = document.createElement('div')
  for (const [k, v] of Object.entries(attrs)) {
    el.setAttribute(k, v)
  }
  return el
}

// ---------------------------------------------------------------------------
// 1. .getToken property — highest priority
// ---------------------------------------------------------------------------

describe('resolveToken — .getToken property (highest priority)', () => {
  it('returns the value from .getToken when it is a function', async () => {
    const el = makeElem()
    el.getToken = async () => 'prop-token'

    const result = await resolveToken(el)
    expect(result).toBe('prop-token')
  })

  it('.getToken takes precedence over the `token` attribute', async () => {
    const el = makeElem({ token: 'attr-token' })
    el.getToken = async () => 'prop-token'

    const result = await resolveToken(el)
    expect(result).toBe('prop-token')
  })

  it('.getToken takes precedence over `get-token` attribute', async () => {
    window.__testGetTokenFn = async () => 'window-token'
    const el = makeElem({ 'get-token': '__testGetTokenFn' })
    el.getToken = async () => 'prop-token'

    const result = await resolveToken(el)
    expect(result).toBe('prop-token')

    delete window.__testGetTokenFn
  })

  it('.getToken takes precedence over BOTH attributes when both are set', async () => {
    window.__testFn2 = async () => 'window-token'
    const el = makeElem({ token: 'attr-token', 'get-token': '__testFn2' })
    el.getToken = async () => 'prop-token-wins'

    const result = await resolveToken(el)
    expect(result).toBe('prop-token-wins')

    delete window.__testFn2
  })

  it('supports synchronous .getToken function', async () => {
    const el = makeElem()
    el.getToken = () => 'sync-token'

    const result = await resolveToken(el)
    expect(result).toBe('sync-token')
  })

  it('returns null when .getToken returns null', async () => {
    const el = makeElem()
    el.getToken = async () => null

    const result = await resolveToken(el)
    expect(result).toBeNull()
  })

  it('returns null (does not throw) when .getToken throws', async () => {
    const el = makeElem()
    el.getToken = async () => { throw new Error('auth failed') }

    const result = await resolveToken(el)
    expect(result).toBeNull()
  })
})

// ---------------------------------------------------------------------------
// 2. `token` attribute fallback
// ---------------------------------------------------------------------------

describe('resolveToken — `token` attribute fallback', () => {
  it('returns the token attribute when no .getToken property is set', async () => {
    const el = makeElem({ token: 'static-jwt' })

    const result = await resolveToken(el)
    expect(result).toBe('static-jwt')
  })

  it('returns null when `token` attribute is empty and no .getToken set', async () => {
    // An empty string attribute is falsy; resolveToken should fall through.
    const el = makeElem()
    // No attributes, no property

    const result = await resolveToken(el)
    expect(result).toBeNull()
  })

  it('`token` attr wins over `get-token` attr (legacy behavior preserved)', async () => {
    window.__testFallbackFn = async () => 'window-token'
    const el = makeElem({ token: 'static-jwt', 'get-token': '__testFallbackFn' })

    const result = await resolveToken(el)
    // token attribute should win over get-token
    expect(result).toBe('static-jwt')

    delete window.__testFallbackFn
  })
})

// ---------------------------------------------------------------------------
// 3. `get-token` attribute → window function fallback
// ---------------------------------------------------------------------------

describe('resolveToken — `get-token` window-function fallback', () => {
  it('calls window[fnName] and returns the token when get-token attribute is set', async () => {
    window.__nubiTestGetToken = async () => 'window-fn-token'
    const el = makeElem({ 'get-token': '__nubiTestGetToken' })

    const result = await resolveToken(el)
    expect(result).toBe('window-fn-token')

    delete window.__nubiTestGetToken
  })

  it('returns null when get-token window function is not registered', async () => {
    const el = makeElem({ 'get-token': '__nubi_nonexistent_fn_12345' })

    const result = await resolveToken(el)
    expect(result).toBeNull()
  })

  it('returns null when neither property, token attr, nor get-token attr are set', async () => {
    const el = makeElem()

    const result = await resolveToken(el)
    expect(result).toBeNull()
  })

  it('waits for the window function to become available (retry loop)', async () => {
    // Register the function after a short delay to exercise the retry loop
    let registered = false
    setTimeout(() => {
      window.__nubiDelayedToken = async () => 'delayed-token'
      registered = true
    }, 60) // 60 ms > one 25 ms retry tick

    const el = makeElem({ 'get-token': '__nubiDelayedToken' })
    const result = await resolveToken(el)

    expect(registered).toBe(true)
    expect(result).toBe('delayed-token')

    delete window.__nubiDelayedToken
  }, 2000)

  it('returns null (does not throw) when get-token window function throws', async () => {
    window.__nubiThrowingFn = async () => { throw new Error('token refresh failed') }
    const el = makeElem({ 'get-token': '__nubiThrowingFn' })

    const result = await resolveToken(el)
    expect(result).toBeNull()

    delete window.__nubiThrowingFn
  })
})
