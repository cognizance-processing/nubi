/**
 * nubi-health.test.js — unit tests for <nubi-health>.
 *
 * Tests use jsdom (via vitest.config.js environment: 'jsdom').
 *
 * Run with:
 *   npm run test:embed
 */

import { describe, test, expect, beforeEach, afterEach, vi } from 'vitest'
import { mount, unmount, nextTick } from './helpers.js'

// Register the widget under test  (path relative to embed/__tests__/)
import '../widgets/nubi-health.js'

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function makeHealth(attrs = {}) {
  const el = document.createElement('nubi-health')
  for (const [k, v] of Object.entries(attrs)) {
    el.setAttribute(k, v)
  }
  return el
}

// ---------------------------------------------------------------------------
// Sample fallback
// ---------------------------------------------------------------------------

describe('<nubi-health> — sample fallback', () => {
  let el

  beforeEach(() => {
    el = makeHealth({ theme: 'dark' })   // no backend → sample fallback
    mount(el)
  })

  afterEach(() => {
    unmount(el)
  })

  test('renders into shadow DOM', async () => {
    await nextTick(5)
    expect(el.shadowRoot).toBeTruthy()
  })

  test('sample fallback shows SAMPLE badge', async () => {
    await nextTick(5)
    const badge = el.shadowRoot.querySelector('.nubi-badge.sample')
    expect(badge).toBeTruthy()
    expect(badge.style.display).not.toBe('none')
  })

  test('sample fallback shows numeric health score', async () => {
    await nextTick(5)
    const scoreEl = el.shadowRoot.querySelector('.hl-score-number')
    expect(scoreEl).toBeTruthy()
    const score = parseInt(scoreEl.textContent, 10)
    expect(score).toBeGreaterThan(0)
    expect(score).toBeLessThanOrEqual(100)
  })

  test('sample fallback shows a grade letter', async () => {
    await nextTick(5)
    const gradeEl = el.shadowRoot.querySelector('.hl-gauge-grade')
    expect(gradeEl).toBeTruthy()
    expect(['A', 'B', 'C', 'D', 'F']).toContain(gradeEl.textContent.trim())
  })

  test('sample fallback renders freshness table', async () => {
    await nextTick(5)
    const table = el.shadowRoot.querySelector('.hl-fresh-table')
    expect(table).toBeTruthy()
    const rows = table.querySelectorAll('tbody tr')
    expect(rows.length).toBeGreaterThanOrEqual(2)
  })

  test('freshness rows include RAG dots', async () => {
    await nextTick(5)
    const dots = el.shadowRoot.querySelectorAll('.hl-rag')
    expect(dots.length).toBeGreaterThan(0)
  })

  test('freshness table has green/amber/red dots', async () => {
    await nextTick(5)
    const dots = [...el.shadowRoot.querySelectorAll('.hl-rag')]
    const ragClasses = dots.map(d => {
      if (d.classList.contains('green')) return 'green'
      if (d.classList.contains('amber')) return 'amber'
      if (d.classList.contains('red'))   return 'red'
      return 'unknown'
    })
    // Sample data includes at least one green and one red
    expect(ragClasses).toContain('green')
    expect(ragClasses).toContain('red')
  })

  test('emits nubi:widget-ready with renderer=health', async () => {
    const el2 = makeHealth({ theme: 'dark' })
    const events = []
    el2.addEventListener('nubi:widget-ready', (e) => events.push(e.detail))
    mount(el2)
    await nextTick(10)
    expect(events.length).toBeGreaterThan(0)
    expect(events[0].renderer).toBe('health')
    expect(typeof events[0].score).toBe('number')
    expect(typeof events[0].grade).toBe('string')
    unmount(el2)
  })
})

// ---------------------------------------------------------------------------
// Theme attribute
// ---------------------------------------------------------------------------

describe('<nubi-health> — theme', () => {
  test('theme=light applies light CSS vars', async () => {
    const el = makeHealth({ theme: 'light' })
    mount(el)
    await nextTick(5)
    const bg = el.style.getPropertyValue('--nubi-bg')
    expect(bg.trim()).toBe('#ffffff')
    unmount(el)
  })

  test('theme=dark applies dark CSS vars', async () => {
    const el = makeHealth({ theme: 'dark' })
    mount(el)
    await nextTick(5)
    const bg = el.style.getPropertyValue('--nubi-bg')
    expect(bg.trim()).toBe('#0f1117')
    unmount(el)
  })
})

// ---------------------------------------------------------------------------
// no-sample-fallback
// ---------------------------------------------------------------------------

describe('<nubi-health> — no-sample-fallback', () => {
  test('shows error state (not sample) when no-sample-fallback is set', async () => {
    const el = makeHealth({ 'no-sample-fallback': '' })
    mount(el)
    await nextTick(10)

    const badge = el.shadowRoot.querySelector('.nubi-badge.sample')
    expect(badge.style.display).toBe('none')

    const errorEl = el.shadowRoot.querySelector('.hl-error')
    expect(errorEl).toBeTruthy()
    unmount(el)
  })
})

// ---------------------------------------------------------------------------
// Backend fetch (mocked)
// ---------------------------------------------------------------------------

const MOCK_SCORE = {
  datasets: [
    {
      dataset_key: 'raw/orders',
      score: 94,
      grade: 'A',
      reasons: ['fresh data', 'high completeness'],
      dimensions: [],
    },
    {
      dataset_key: 'raw/sessions',
      score: 52,
      grade: 'F',
      reasons: ['stale — 28 h ago'],
      dimensions: [],
    },
  ],
  default_weights: { freshness: 0.5, completeness: 0.3, availability: 0.2 },
}

const MOCK_FRESHNESS = {
  datasets: [
    { dataset_key: 'raw/orders',   fresh: true,  last_updated: new Date(Date.now() - 2 * 3600_000).toISOString(),  status: 'fresh' },
    { dataset_key: 'raw/sessions', fresh: false, last_updated: new Date(Date.now() - 28 * 3600_000).toISOString(), status: 'stale' },
  ],
}

describe('<nubi-health> — live backend fetch', () => {
  beforeEach(() => {
    globalThis.fetch = vi.fn((url) => {
      if (url.includes('/health/freshness')) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve(MOCK_FRESHNESS) })
      }
      return Promise.resolve({ ok: true, json: () => Promise.resolve(MOCK_SCORE) })
    })
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  test('renders score from backend data', async () => {
    const el = makeHealth({ backend: 'http://test-api', token: 'tok' })
    mount(el)
    await nextTick(20)

    const scoreEl = el.shadowRoot.querySelector('.hl-score-number')
    expect(scoreEl).toBeTruthy()
    // Average of 94 + 52 = 73
    expect(parseInt(scoreEl.textContent, 10)).toBeCloseTo(73, -1)

    const badge = el.shadowRoot.querySelector('.nubi-badge.live')
    expect(badge.style.display).not.toBe('none')

    unmount(el)
  })

  test('shows freshness rows', async () => {
    const el = makeHealth({ backend: 'http://test-api', token: 'tok' })
    mount(el)
    await nextTick(20)

    const rows = el.shadowRoot.querySelectorAll('.hl-fresh-table tbody tr')
    expect(rows.length).toBeGreaterThanOrEqual(2)
    unmount(el)
  })

  test('emits nubi:widget-ready with live data', async () => {
    const el = makeHealth({ backend: 'http://test-api', token: 'tok' })
    const events = []
    el.addEventListener('nubi:widget-ready', (e) => events.push(e.detail))
    mount(el)
    await nextTick(20)

    expect(events[0].renderer).toBe('health')
    expect(typeof events[0].score).toBe('number')
    unmount(el)
  })

  test('falls back to sample on fetch error', async () => {
    globalThis.fetch = vi.fn().mockRejectedValue(new Error('Network error'))
    const el = makeHealth({ backend: 'http://fail-api', token: 'tok' })
    mount(el)
    await nextTick(20)

    const badge = el.shadowRoot.querySelector('.nubi-badge.sample')
    expect(badge.style.display).not.toBe('none')
    unmount(el)
  })
})
