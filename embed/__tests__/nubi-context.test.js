/**
 * nubi-context.test.js — Unit tests for the shared NubiContext factory.
 *
 * Focuses on the bits NOT already covered by wc-foundation.test.js
 * (which exercises decodeScopes / hasScope): the cross-filter event bus,
 * token-resolution precedence, and getTokenFn error tolerance.
 *
 * Run with:
 *   npm run test:embed
 */

import { describe, test, expect, vi } from 'vitest'
import { createNubiContext, decodeScopes, hasScope } from '../nubi-context.js'

// ---------------------------------------------------------------------------
// Factory shape + defaults
// ---------------------------------------------------------------------------

describe('createNubiContext — shape', () => {
  test('returns the documented API surface', () => {
    const ctx = createNubiContext({ backend: 'http://x' })
    expect(typeof ctx.resolveToken).toBe('function')
    expect(typeof ctx.fetch).toBe('function')
    expect(typeof ctx.emitFilter).toBe('function')
    expect(typeof ctx.onFilter).toBe('function')
    expect(ctx.bus).toBeInstanceOf(EventTarget)
    expect(ctx.backend).toBe('http://x')
  })

  test('defaults backend to localhost:8000 when not given', () => {
    const ctx = createNubiContext()
    expect(ctx.backend).toBe('http://localhost:8000')
  })
})

// ---------------------------------------------------------------------------
// Token resolution precedence: static token > getTokenFn > null
// ---------------------------------------------------------------------------

describe('createNubiContext — resolveToken precedence', () => {
  test('static token wins over getTokenFn', async () => {
    const getTokenFn = vi.fn(async () => 'from-fn')
    const ctx = createNubiContext({ token: 'static-token', getTokenFn })
    expect(await ctx.resolveToken()).toBe('static-token')
    expect(getTokenFn).not.toHaveBeenCalled()
  })

  test('falls back to getTokenFn when no static token', async () => {
    const ctx = createNubiContext({ getTokenFn: async () => 'fn-token' })
    expect(await ctx.resolveToken()).toBe('fn-token')
  })

  test('returns null when getTokenFn resolves to undefined', async () => {
    const ctx = createNubiContext({ getTokenFn: async () => undefined })
    expect(await ctx.resolveToken()).toBeNull()
  })

  test('returns null (not throws) when getTokenFn rejects', async () => {
    const ctx = createNubiContext({ getTokenFn: async () => { throw new Error('boom') } })
    await expect(ctx.resolveToken()).resolves.toBeNull()
  })

  test('returns null when neither token nor getTokenFn provided', async () => {
    const ctx = createNubiContext({ backend: 'http://x' })
    expect(await ctx.resolveToken()).toBeNull()
  })
})

// ---------------------------------------------------------------------------
// Cross-filter bus
// ---------------------------------------------------------------------------

describe('createNubiContext — cross-filter bus', () => {
  test('onFilter receives emitted column/value events', () => {
    const ctx = createNubiContext()
    const seen = []
    ctx.onFilter((e) => seen.push(e.detail))
    ctx.emitFilter('country', 'ZA')
    expect(seen).toEqual([{ column: 'country', value: 'ZA' }])
  })

  test('multiple subscribers all receive the event', () => {
    const ctx = createNubiContext()
    const a = []
    const b = []
    ctx.onFilter((e) => a.push(e.detail.value))
    ctx.onFilter((e) => b.push(e.detail.value))
    ctx.emitFilter('x', 1)
    expect(a).toEqual([1])
    expect(b).toEqual([1])
  })

  test('unsubscribe stops further delivery', () => {
    const ctx = createNubiContext()
    const seen = []
    const off = ctx.onFilter((e) => seen.push(e.detail.value))
    ctx.emitFilter('x', 1)
    off()
    ctx.emitFilter('x', 2)
    expect(seen).toEqual([1])
  })

  test('emitFilter carries complex values verbatim', () => {
    const ctx = createNubiContext()
    let detail
    ctx.onFilter((e) => { detail = e.detail })
    ctx.emitFilter('range', { from: '2024-01-01', to: '2024-02-01' })
    expect(detail.column).toBe('range')
    expect(detail.value).toEqual({ from: '2024-01-01', to: '2024-02-01' })
  })

  test('two contexts have independent buses', () => {
    const a = createNubiContext()
    const b = createNubiContext()
    const seenA = []
    a.onFilter((e) => seenA.push(e.detail))
    b.emitFilter('x', 99) // emitted on b's bus only
    expect(seenA).toEqual([])
  })
})

// ---------------------------------------------------------------------------
// Re-exported scope helpers (smoke — full matrix lives in wc-foundation)
// ---------------------------------------------------------------------------

describe('nubi-context re-exports decodeScopes / hasScope', () => {
  test('decodeScopes returns [] for falsy / malformed tokens', () => {
    expect(decodeScopes(null)).toEqual([])
    expect(decodeScopes('not-a-jwt')).toEqual([])
  })

  test('hasScope honours wildcard precedence', () => {
    expect(hasScope(['*'], 'anything')).toBe(true)
    expect(hasScope(['read:*'], 'read:dashboard')).toBe(true)
    expect(hasScope(['read:*'], 'write:dashboard')).toBe(false)
  })
})
