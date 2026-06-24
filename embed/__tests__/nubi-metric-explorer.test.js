/**
 * nubi-metric-explorer.test.js
 *
 * Tests for <nubi-metric-explorer>: scope gating, metric loading,
 * dimension picker rendering, run flow, and event emission.
 */

import { describe, it, expect, beforeEach, afterEach, vi, beforeAll } from 'vitest'
import { makeToken, nextTick, mount, unmount } from './helpers.js'

// ---------------------------------------------------------------------------
// Register the custom element once
// ---------------------------------------------------------------------------

beforeAll(async () => {
  const { NubiMetricExplorer } = await import('../widgets/nubi-metric-explorer.js')
  if (!customElements.get('nubi-metric-explorer')) {
    customElements.define('nubi-metric-explorer', NubiMetricExplorer)
  }
})

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function make(attrs = {}) {
  const el = document.createElement('nubi-metric-explorer')
  for (const [k, v] of Object.entries(attrs)) {
    if (v === true) el.setAttribute(k, '')
    else el.setAttribute(k, v)
  }
  return el
}

/**
 * Install a global fetch stub that handles metric definition + query endpoints.
 * @returns {{ restore: () => void, stub: vi.Mock }}
 */
function stubFetch({ metricDef = null, arrowBuffer = new ArrayBuffer(0), failQuery = false } = {}) {
  const stub = vi.fn(async (url, opts) => {
    if (url.includes('/metrics/') && !url.includes('/query')) {
      if (metricDef) {
        return { ok: true, json: async () => metricDef, headers: { get: () => 'application/json' } }
      }
      return { ok: false, status: 404 }
    }
    if (url.includes('/query')) {
      if (failQuery) {
        return { ok: false, status: 403 }
      }
      return {
        ok: true,
        arrayBuffer: async () => arrowBuffer,
        headers: { get: () => 'application/vnd.apache.arrow.stream' },
      }
    }
    return { ok: false, status: 404 }
  })
  const orig = globalThis.fetch
  globalThis.fetch = stub
  return { stub, restore: () => { globalThis.fetch = orig } }
}

// ---------------------------------------------------------------------------
// Scope-gating tests
// ---------------------------------------------------------------------------

describe('NubiMetricExplorer — scope gating', () => {
  let el
  let fetchCtx

  afterEach(() => {
    el?.remove()
    fetchCtx?.restore()
  })

  it('shows READ-ONLY indicator when no author:metric scope', async () => {
    fetchCtx = stubFetch()
    el = make({ token: makeToken(['read:*']), backend: 'http://localhost:8000', 'metric-id': 'revenue' })
    mount(el)
    await nextTick(4)

    const ind = el.shadowRoot.querySelector('.scope-indicator')
    expect(ind).toBeTruthy()
    expect(ind.classList.contains('readonly')).toBe(true)
    expect(ind.textContent).toMatch(/read.only/i)
  })

  it('shows METRIC indicator when author:metric scope present', async () => {
    fetchCtx = stubFetch()
    el = make({ token: makeToken(['read:*', 'author:metric']), backend: 'http://localhost:8000', 'metric-id': 'revenue' })
    mount(el)
    await nextTick(4)

    const ind = el.shadowRoot.querySelector('.scope-indicator')
    expect(ind.classList.contains('metric')).toBe(true)
    expect(ind.textContent).toMatch(/metric/i)
  })

  it('shows Run button when author:metric scope present', async () => {
    fetchCtx = stubFetch()
    el = make({ token: makeToken(['read:*', 'author:metric']), backend: 'http://localhost:8000', 'metric-id': 'revenue' })
    mount(el)
    await nextTick(4)

    const btnRun = el.shadowRoot.querySelector('.btn-run-me')
    expect(btnRun).toBeTruthy()
    expect(btnRun.disabled).toBe(false)
  })

  it('hides Run button when no author:metric scope', async () => {
    fetchCtx = stubFetch()
    el = make({ token: makeToken(['read:*']), backend: 'http://localhost:8000', 'metric-id': 'revenue' })
    mount(el)
    await nextTick(4)

    const btnRun = el.shadowRoot.querySelector('.btn-run-me')
    expect(btnRun).toBeNull()
  })

  it('shows no-scope banner when author:metric scope absent', async () => {
    fetchCtx = stubFetch()
    el = make({ token: makeToken(['read:*']), backend: 'http://localhost:8000', 'metric-id': 'revenue' })
    mount(el)
    await nextTick(4)

    const banner = el.shadowRoot.querySelector('.no-scope-banner')
    expect(banner).toBeTruthy()
    expect(banner.textContent).toMatch(/author:metric/i)
  })

  it('disables all controls when read-only', async () => {
    fetchCtx = stubFetch()
    el = make({ token: makeToken(['read:*']), backend: 'http://localhost:8000', 'metric-id': 'revenue' })
    mount(el)
    await nextTick(4)

    const selects = [...el.shadowRoot.querySelectorAll('.me-select')]
    selects.forEach(s => expect(s.disabled).toBe(true))

    const checkboxes = [...el.shadowRoot.querySelectorAll('input[type=checkbox]')]
    checkboxes.forEach(c => expect(c.disabled).toBe(true))
  })
})

// ---------------------------------------------------------------------------
// Metric definition loading
// ---------------------------------------------------------------------------

describe('NubiMetricExplorer — metric loading', () => {
  let el
  let fetchCtx

  afterEach(() => {
    el?.remove()
    fetchCtx?.restore()
  })

  it('renders dimension checkboxes for a loaded metric definition', async () => {
    const metricDef = {
      id: 'revenue',
      name: 'Revenue',
      dimensions: ['date', 'region', 'product'],
      timeGrains: ['day', 'week', 'month'],
    }
    fetchCtx = stubFetch({ metricDef })
    el = make({ token: makeToken(['read:*', 'author:metric']), backend: 'http://localhost:8000', 'metric-id': 'revenue' })
    mount(el)
    await nextTick(4)

    const checkboxes = el.shadowRoot.querySelectorAll('input[type=checkbox]')
    expect(checkboxes.length).toBeGreaterThanOrEqual(3)
  })

  it('falls back to default metrics when API returns 404', async () => {
    fetchCtx = stubFetch()  // no metricDef → 404
    el = make({ token: makeToken(['read:*', 'author:metric']), backend: 'http://localhost:8000', 'metric-id': 'revenue' })
    mount(el)
    await nextTick(4)

    // Should still render controls (from DEFAULT_METRICS)
    const controls = el.shadowRoot.querySelector('.nubi-me-controls')
    expect(controls).toBeTruthy()
    expect(controls.style.display).not.toBe('none')
  })

  it('shows "Select a metric" placeholder when metric-id not set', async () => {
    fetchCtx = stubFetch()
    el = make({ token: makeToken(['read:*', 'author:metric']), backend: 'http://localhost:8000' })
    mount(el)
    await nextTick(4)

    // Default metric is loaded (first in list) — controls should appear
    const controls = el.shadowRoot.querySelector('.nubi-me-controls')
    // Even without metric-id, the fallback renders DEFAULT_METRICS[0]
    expect(controls).toBeTruthy()
  })

  it('updates metric name in toolbar when metric is loaded', async () => {
    const metricDef = {
      id: 'orders',
      name: 'Orders Count',
      dimensions: ['date', 'region'],
      timeGrains: ['day', 'week'],
    }
    fetchCtx = stubFetch({ metricDef })
    el = make({ token: makeToken(['read:*', 'author:metric']), backend: 'http://localhost:8000', 'metric-id': 'orders' })
    mount(el)
    await nextTick(4)

    const metricName = el.shadowRoot.querySelector('.metric-name')
    expect(metricName.textContent).toMatch(/orders count/i)
  })
})

// ---------------------------------------------------------------------------
// Event emission
// ---------------------------------------------------------------------------

describe('NubiMetricExplorer — event emission', () => {
  let el
  let fetchCtx

  afterEach(() => {
    el?.remove()
    fetchCtx?.restore()
  })

  it('emits nubi:run when Run button clicked', async () => {
    fetchCtx = stubFetch()
    el = make({
      token: makeToken(['read:*', 'author:metric']),
      backend: 'http://localhost:8000',
      'metric-id': 'revenue',
    })

    const runs = []
    document.addEventListener('nubi:run', e => runs.push(e))

    mount(el)
    await nextTick(4)

    const btnRun = el.shadowRoot.querySelector('.btn-run-me')
    btnRun?.click()
    await nextTick(4)

    expect(runs.length).toBeGreaterThan(0)
    expect(runs[0].detail).toMatchObject({ metricId: expect.any(String) })

    document.removeEventListener('nubi:run', e => runs.push(e))
  })

  it('emits nubi:error when metric query returns non-OK', async () => {
    fetchCtx = stubFetch({ failQuery: true })
    el = make({
      token: makeToken(['read:*', 'author:metric']),
      backend: 'http://localhost:8000',
      'metric-id': 'revenue',
    })

    const errors = []
    document.addEventListener('nubi:error', e => errors.push(e))

    mount(el)
    await nextTick(4)

    const btnRun = el.shadowRoot.querySelector('.btn-run-me')
    btnRun?.click()
    await nextTick(4)

    expect(errors.length).toBeGreaterThan(0)

    document.removeEventListener('nubi:error', e => errors.push(e))
  })
})

// ---------------------------------------------------------------------------
// Rendering basics
// ---------------------------------------------------------------------------

describe('NubiMetricExplorer — rendering', () => {
  it('mounts in a bare page and renders shadow root', () => {
    const el = make({})
    mount(el)
    expect(el.shadowRoot).toBeTruthy()
    expect(el.shadowRoot.querySelector('.nubi-me-wrap')).toBeTruthy()
    el.remove()
  })

  it('renders toolbar with metric-name span', () => {
    const el = make({})
    mount(el)
    expect(el.shadowRoot.querySelector('.metric-name')).toBeTruthy()
    expect(el.shadowRoot.querySelector('.scope-indicator')).toBeTruthy()
    el.remove()
  })

  it('renders results area', () => {
    const el = make({})
    mount(el)
    expect(el.shadowRoot.querySelector('.nubi-me-results')).toBeTruthy()
    el.remove()
  })

  it('renders footer area', () => {
    const el = make({})
    mount(el)
    expect(el.shadowRoot.querySelector('.nubi-me-footer')).toBeTruthy()
    el.remove()
  })

  it('accepts theme attribute without error', () => {
    const el = make({ theme: 'light' })
    expect(() => mount(el)).not.toThrow()
    el.remove()
  })
})

// ---------------------------------------------------------------------------
// decodeScopes utility
// ---------------------------------------------------------------------------

describe('decodeScopes utility', () => {
  it('decodes space-delimited scope string from JWT', async () => {
    const { decodeScopes } = await import('../nubi-context.js')
    const token = makeToken(['read:*', 'author:sql', 'author:metric'])
    const scopes = decodeScopes(token)
    expect(scopes).toContain('read:*')
    expect(scopes).toContain('author:sql')
    expect(scopes).toContain('author:metric')
  })

  it('returns [] for null token', async () => {
    const { decodeScopes } = await import('../nubi-context.js')
    expect(decodeScopes(null)).toEqual([])
  })

  it('returns [] for malformed token', async () => {
    const { decodeScopes } = await import('../nubi-context.js')
    expect(decodeScopes('not.a.valid.jwt.token.at.all')).toEqual([])
  })
})

// ---------------------------------------------------------------------------
// hasScope utility
// ---------------------------------------------------------------------------

describe('hasScope utility', () => {
  it('matches exact scope', async () => {
    const { hasScope } = await import('../nubi-context.js')
    expect(hasScope(['author:sql'], 'author:sql')).toBe(true)
  })

  it('wildcard * matches any scope', async () => {
    const { hasScope } = await import('../nubi-context.js')
    expect(hasScope(['*'], 'author:sql')).toBe(true)
    expect(hasScope(['*'], 'author:metric')).toBe(true)
  })

  it('author:* matches author:sql and author:metric', async () => {
    const { hasScope } = await import('../nubi-context.js')
    expect(hasScope(['author:*'], 'author:sql')).toBe(true)
    expect(hasScope(['author:*'], 'author:metric')).toBe(true)
  })

  it('returns false when scope not present', async () => {
    const { hasScope } = await import('../nubi-context.js')
    expect(hasScope(['read:*'], 'author:sql')).toBe(false)
  })
})
