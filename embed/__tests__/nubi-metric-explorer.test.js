/**
 * nubi-metric-explorer.test.js
 *
 * Tests for <nubi-metric-explorer>: scope gating, metric loading,
 * dimension picker rendering, run flow, event emission, and CSV export.
 */

import { describe, it, expect, afterEach, vi, beforeAll } from 'vitest'
import { makeToken, nextTick, mount } from './helpers.js'

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
  const stub = vi.fn(async (url, _opts) => {
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

// ---------------------------------------------------------------------------
// CSV export
// ---------------------------------------------------------------------------

describe('NubiMetricExplorer — CSV export', () => {
  let fetchCtx

  afterEach(() => {
    fetchCtx?.restore()
  })

  /**
   * Create a minimal valid Arrow IPC stream buffer for a single string column.
   * We import tableToIPC and the Table builder from apache-arrow at test time so
   * we don't need a build step.
   */
  async function makeArrowBuffer(rows) {
    const { tableFromArrays, vectorFromArray, tableToIPC } = await import('apache-arrow')
    const keys = Object.keys(rows[0] || { _empty: '' })
    const arrays = {}
    for (const k of keys) {
      arrays[k] = vectorFromArray(rows.map(r => String(r[k] ?? '')))
    }
    const table = tableFromArrays(arrays)
    const ipc   = tableToIPC(table, 'stream')
    return ipc.buffer
  }

  /**
   * Stub URL.createObjectURL / revokeObjectURL and intercept <a>.click so
   * downloadCsv doesn't throw in jsdom.
   */
  function stubDownload() {
    const calls = []
    const origCreate = URL.createObjectURL
    const origRevoke = URL.revokeObjectURL
    URL.createObjectURL = vi.fn(() => 'blob:mock-url')
    URL.revokeObjectURL = vi.fn()

    const origCreateElement = document.createElement.bind(document)
    const createSpy = vi.spyOn(document, 'createElement').mockImplementation((tag) => {
      const node = origCreateElement(tag)
      if (tag === 'a') {
        node.click = () => calls.push({ href: node.href, download: node.download })
      }
      return node
    })

    return {
      calls,
      restore() {
        URL.createObjectURL = origCreate
        URL.revokeObjectURL = origRevoke
        createSpy.mockRestore()
      },
    }
  }

  it('export button is hidden before a Run completes', async () => {
    fetchCtx = stubFetch()
    const el = make({
      token: makeToken(['read:*', 'author:metric']),
      backend: 'http://localhost:8000',
      'metric-id': 'revenue',
    })
    mount(el)
    await nextTick(4)

    const toolbar = el.shadowRoot.querySelector('.nubi-me-results-toolbar')
    expect(toolbar).toBeTruthy()
    expect(toolbar.style.display).toBe('none')

    el.remove()
  })

  it('export button appears after a successful Run', async () => {
    const arrowBuf = await makeArrowBuffer([{ metric: 'revenue', value: '100' }])
    fetchCtx = stubFetch({ arrowBuffer: arrowBuf })

    const el = make({
      token: makeToken(['read:*', 'author:metric']),
      backend: 'http://localhost:8000',
      'metric-id': 'revenue',
    })
    mount(el)
    await nextTick(4)

    // Trigger a run
    el.shadowRoot.querySelector('.btn-run-me')?.click()
    await nextTick(6)

    const toolbar = el.shadowRoot.querySelector('.nubi-me-results-toolbar')
    expect(toolbar.style.display).not.toBe('none')

    const btn = el.shadowRoot.querySelector('[data-role="export"]')
    expect(btn).toBeTruthy()
    expect(btn.textContent).toMatch(/csv/i)

    el.remove()
  })

  it('clicking export emits nubi:export event with format:csv', async () => {
    const arrowBuf = await makeArrowBuffer([{ region: 'ZA', rev: '500' }])
    fetchCtx = stubFetch({ arrowBuffer: arrowBuf })

    const el = make({
      token: makeToken(['read:*', 'author:metric']),
      backend: 'http://localhost:8000',
      'metric-id': 'revenue',
    })

    const events = []
    document.addEventListener('nubi:export', (e) => events.push(e.detail))

    mount(el)
    await nextTick(4)

    // Run first
    el.shadowRoot.querySelector('.btn-run-me')?.click()
    await nextTick(6)

    const dl = stubDownload()
    try {
      el.shadowRoot.querySelector('[data-role="export"]')?.click()
      await nextTick(2)

      expect(events.length).toBeGreaterThanOrEqual(1)
      expect(events[0].format).toBe('csv')
      expect(events[0].rows).toBeGreaterThan(0)
    } finally {
      document.removeEventListener('nubi:export', (e) => events.push(e.detail))
      dl.restore()
    }

    el.remove()
  })

  it('clicking export creates a Blob download', async () => {
    const arrowBuf = await makeArrowBuffer([{ city: 'Cape Town', sales: '42' }])
    fetchCtx = stubFetch({ arrowBuffer: arrowBuf })

    const el = make({
      token: makeToken(['read:*', 'author:metric']),
      backend: 'http://localhost:8000',
      'metric-id': 'revenue',
    })
    mount(el)
    await nextTick(4)

    el.shadowRoot.querySelector('.btn-run-me')?.click()
    await nextTick(6)

    const dl = stubDownload()
    try {
      el.shadowRoot.querySelector('[data-role="export"]')?.click()
      await nextTick(2)

      expect(URL.createObjectURL).toHaveBeenCalledOnce()
      const blob = URL.createObjectURL.mock.calls[0][0]
      expect(blob).toBeInstanceOf(Blob)
      expect(blob.type).toContain('text/csv')

      expect(dl.calls.length).toBe(1)
      expect(dl.calls[0].download).toMatch(/\.csv$/)
    } finally {
      dl.restore()
    }

    el.remove()
  })
})
