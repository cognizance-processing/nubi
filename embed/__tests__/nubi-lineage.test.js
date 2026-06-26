/**
 * nubi-lineage.test.js — unit tests for <nubi-lineage>.
 *
 * Tests use jsdom (via vitest.config.js environment: 'jsdom') so custom
 * elements, shadow DOM, and CustomEvent are all available.
 *
 * Run with:
 *   npm run test:embed
 */

import { describe, test, expect, beforeEach, afterEach, vi } from 'vitest'
import { mount, unmount, nextTick } from './helpers.js'

// Register the widget under test
import '../widgets/nubi-lineage.js'

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function makeLineage(attrs = {}) {
  const el = document.createElement('nubi-lineage')
  for (const [k, v] of Object.entries(attrs)) {
    el.setAttribute(k, v)
  }
  return el
}

// ---------------------------------------------------------------------------
// Sample fallback
// ---------------------------------------------------------------------------

describe('<nubi-lineage> — sample fallback', () => {
  let el

  beforeEach(() => {
    el = makeLineage({ theme: 'dark' })   // no backend → sample fallback
    mount(el)
  })

  afterEach(() => {
    unmount(el)
  })

  test('renders into shadow DOM', async () => {
    await nextTick(5)
    expect(el.shadowRoot).toBeTruthy()
  })

  test('no-backend path renders sample immediately — never stuck on Loading', async () => {
    // Widget has no backend attr; must never stay on Loading spinner
    await nextTick(5)
    const loading = el.shadowRoot.querySelector('.nubi-loading')
    expect(loading).toBeNull()
    // Must have DAG nodes rendered instead
    const nodes = el.shadowRoot.querySelectorAll('.ln-node')
    expect(nodes.length).toBeGreaterThan(0)
  })

  test('sample fallback shows SAMPLE badge', async () => {
    await nextTick(5)
    const badge = el.shadowRoot.querySelector('.nubi-badge.sample')
    expect(badge).toBeTruthy()
    expect(badge.style.display).not.toBe('none')
  })

  test('sample fallback renders SVG with nodes', async () => {
    await nextTick(5)
    const svg = el.shadowRoot.querySelector('svg')
    expect(svg).toBeTruthy()
    const nodes = el.shadowRoot.querySelectorAll('.ln-node')
    expect(nodes.length).toBeGreaterThanOrEqual(3)
  })

  test('sample fallback renders edges (paths)', async () => {
    await nextTick(5)
    const edges = el.shadowRoot.querySelectorAll('.ln-edge')
    expect(edges.length).toBeGreaterThanOrEqual(2)
  })

  test('emits nubi:widget-ready with renderer=lineage', async () => {
    const el2 = makeLineage({ theme: 'dark' })
    const events = []
    el2.addEventListener('nubi:widget-ready', (e) => events.push(e.detail))
    mount(el2)
    await nextTick(10)
    expect(events.length).toBeGreaterThan(0)
    expect(events[0].renderer).toBe('lineage')
    expect(typeof events[0].nodes).toBe('number')
    expect(typeof events[0].edges).toBe('number')
    unmount(el2)
  })
})

// ---------------------------------------------------------------------------
// nubi:select event on node click
// ---------------------------------------------------------------------------

describe('<nubi-lineage> — node click fires nubi:select', () => {
  test('clicking a node emits nubi:select with node detail', async () => {
    const el = makeLineage({ theme: 'dark' })
    const selected = []
    el.addEventListener('nubi:select', (e) => selected.push(e.detail))
    mount(el)
    await nextTick(10)

    const firstNode = el.shadowRoot.querySelector('.ln-node')
    expect(firstNode).toBeTruthy()
    firstNode.dispatchEvent(new MouseEvent('click', { bubbles: true, composed: true }))

    expect(selected.length).toBe(1)
    expect(selected[0].node).toBeTruthy()
    expect(selected[0].node.id).toBeTruthy()
    unmount(el)
  })

  test('clicked node gets selected class', async () => {
    const el = makeLineage({ theme: 'dark' })
    mount(el)
    await nextTick(10)

    const firstNode = el.shadowRoot.querySelector('.ln-node')
    firstNode.dispatchEvent(new MouseEvent('click', { bubbles: true }))

    expect(firstNode.classList.contains('selected')).toBe(true)
    unmount(el)
  })
})

// ---------------------------------------------------------------------------
// Theme attribute
// ---------------------------------------------------------------------------

describe('<nubi-lineage> — theme', () => {
  test('theme=light applies light CSS vars', async () => {
    const el = makeLineage({ theme: 'light' })
    mount(el)
    await nextTick(5)
    const bg = el.style.getPropertyValue('--nubi-bg')
    expect(bg).toBeTruthy()
    // Light theme bg should be white-ish
    expect(bg.trim()).toBe('#ffffff')
    unmount(el)
  })

  test('theme=dark applies dark CSS vars', async () => {
    const el = makeLineage({ theme: 'dark' })
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

describe('<nubi-lineage> — no-sample-fallback', () => {
  test('shows error state (not sample) when no-sample-fallback is set', async () => {
    const el = makeLineage({ 'no-sample-fallback': '' })
    mount(el)
    await nextTick(10)

    const badge = el.shadowRoot.querySelector('.nubi-badge.sample')
    expect(badge.style.display).toBe('none')

    const errorEl = el.shadowRoot.querySelector('.ln-error')
    expect(errorEl).toBeTruthy()
    unmount(el)
  })
})

// ---------------------------------------------------------------------------
// Backend fetch (mocked)
// ---------------------------------------------------------------------------

describe('<nubi-lineage> — live backend fetch', () => {
  beforeEach(() => {
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({
        nodes: [
          { id: 'table:t1', type: 'table',  name: 'orders' },
          { id: 'query:q1', type: 'query',  name: 'Revenue Q' },
          { id: 'metric:m1', type: 'metric', name: 'Revenue' },
        ],
        edges: [
          { from: 'table:t1', to: 'query:q1', via: 'orders' },
          { from: 'query:q1', to: 'metric:m1', via: 'revenue' },
        ],
      }),
    })
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  test('renders nodes from backend when fetch succeeds', async () => {
    const el = makeLineage({ backend: 'http://test-api', token: 'tok' })
    mount(el)
    await nextTick(15)

    const nodes = el.shadowRoot.querySelectorAll('.ln-node')
    expect(nodes.length).toBe(3)

    const badge = el.shadowRoot.querySelector('.nubi-badge.live')
    expect(badge.style.display).not.toBe('none')

    unmount(el)
  })

  test('emits nubi:widget-ready with live data', async () => {
    const el = makeLineage({ backend: 'http://test-api', token: 'tok' })
    const events = []
    el.addEventListener('nubi:widget-ready', (e) => events.push(e.detail))
    mount(el)
    await nextTick(15)

    expect(events[0].nodes).toBe(3)
    expect(events[0].edges).toBe(2)
    expect(events[0].renderer).toBe('lineage')
    unmount(el)
  })
})
