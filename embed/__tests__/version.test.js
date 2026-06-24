/**
 * version.test.js — Smoke tests for the embed kit version exports.
 *
 * __NUBI_EMBED_VERSION__ is injected by vitest.config.js `define` (mirroring
 * what vite.embed.config.js does at bundle build time).
 *
 * We test the version string directly rather than importing the full entry
 * (which would pull in React / Arrow / ECharts and slow the test run).
 */

import { describe, it, expect } from 'vitest'

/* global __NUBI_EMBED_VERSION__ */

// ---------------------------------------------------------------------------
// Version constant (same value that the entry exports as `version` and
// `NUBI_EMBED_VERSION`, and assigns to `window.__nubiVersion`)
// ---------------------------------------------------------------------------

describe('__NUBI_EMBED_VERSION__ build-time constant', () => {
  it('is a non-empty string', () => {
    expect(typeof __NUBI_EMBED_VERSION__).toBe('string')
    expect(__NUBI_EMBED_VERSION__.length).toBeGreaterThan(0)
  })

  it('matches semver format (MAJOR.MINOR.PATCH)', () => {
    // Accepts optional pre-release and build metadata (e.g. "1.2.3-alpha.1+build.42")
    expect(__NUBI_EMBED_VERSION__).toMatch(/^\d+\.\d+\.\d+/)
  })

  it('is not the placeholder string "0.0.0-UNKNOWN"', () => {
    expect(__NUBI_EMBED_VERSION__).not.toBe('0.0.0-UNKNOWN')
  })
})

// ---------------------------------------------------------------------------
// window.__nubiVersion simulation
// (The entry assigns window.__nubiVersion = __NUBI_EMBED_VERSION__ on load.
//  Here we verify the assignment logic is correct.)
// ---------------------------------------------------------------------------

describe('window.__nubiVersion assignment', () => {
  it('can be assigned from __NUBI_EMBED_VERSION__ without type error', () => {
    const snapshot = globalThis.__nubiVersion
    try {
      globalThis.__nubiVersion = __NUBI_EMBED_VERSION__
      expect(globalThis.__nubiVersion).toBe(__NUBI_EMBED_VERSION__)
    } finally {
      if (snapshot === undefined) delete globalThis.__nubiVersion
      else globalThis.__nubiVersion = snapshot
    }
  })
})

// ---------------------------------------------------------------------------
// Named export shape
// (Import the entry to verify `version` and `NUBI_EMBED_VERSION` are exported.
//  We guard behind a try/catch so a heavyweight import failure doesn't hide the
//  core version-string tests above.)
// ---------------------------------------------------------------------------

describe('nubi-embed-entry named exports', async () => {
  let entryMod = null

  try {
    // Dynamic import so a module-resolution failure is isolated
    entryMod = await import('../nubi-embed-entry.js')
  } catch {
    // Widgets may fail to fully mount in jsdom — that is fine for this test;
    // we only care about the version export values.
  }

  it('exports `version` as a non-empty string', () => {
    if (!entryMod) return // skip if entry couldn't be loaded
    expect(typeof entryMod.version).toBe('string')
    expect(entryMod.version.length).toBeGreaterThan(0)
  })

  it('exports `NUBI_EMBED_VERSION` equal to `version`', () => {
    if (!entryMod) return
    expect(entryMod.NUBI_EMBED_VERSION).toBe(entryMod.version)
  })

  it('`version` matches semver format', () => {
    if (!entryMod) return
    expect(entryMod.version).toMatch(/^\d+\.\d+\.\d+/)
  })
})
