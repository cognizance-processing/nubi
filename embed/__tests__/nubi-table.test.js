/**
 * nubi-table.test.js — Unit tests for <nubi-table> (HTML table widget).
 *
 * Covers sample fallback, inline data injection (columns/limit), error state,
 * theme tokens, and widget events.
 *
 * Run with:
 *   npm run test:embed
 */

import { describe, test, expect, beforeEach, afterEach } from 'vitest'
import { mount, unmount, nextTick } from './helpers.js'

// The widget module only exports the class; registration normally happens in
// widgets/index.js. Define it directly here (avoids the echarts barrel import).
import { NubiTable } from '../widgets/nubi-table.js'
if (!customElements.get('nubi-table')) customElements.define('nubi-table', NubiTable)

function makeTable(attrs = {}) {
  const el = document.createElement('nubi-table')
  for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v)
  return el
}

// ---------------------------------------------------------------------------
// Sample fallback
// ---------------------------------------------------------------------------

describe('<nubi-table> — sample fallback', () => {
  let el
  beforeEach(() => { el = makeTable({ theme: 'dark', 'query-id': 'demo' }); mount(el) })
  afterEach(() => unmount(el))

  test('renders a table with rows into shadow DOM', async () => {
    await nextTick(5)
    const table = el.shadowRoot.querySelector('table')
    expect(table).toBeTruthy()
    const rows = table.querySelectorAll('tbody tr')
    expect(rows.length).toBeGreaterThanOrEqual(2)
  })

  test('shows the SAMPLE badge', async () => {
    await nextTick(5)
    const badge = el.shadowRoot.querySelector('.nubi-badge')
    expect(badge.style.display).not.toBe('none')
    expect(badge.textContent).toBe('SAMPLE')
  })

  test('footer reports the displayed / total row count', async () => {
    await nextTick(5)
    const footer = el.shadowRoot.querySelector('.nubi-footer')
    expect(footer.textContent).toMatch(/\d+ \/ \d+ rows/)
  })

  test('emits nubi:widget-ready with renderer:table', async () => {
    const el2 = makeTable({ 'query-id': 'demo' })
    const events = []
    el2.addEventListener('nubi:widget-ready', (e) => events.push(e.detail))
    mount(el2)
    await nextTick(5)
    expect(events.length).toBeGreaterThanOrEqual(1)
    expect(events[0].renderer).toBe('table')
    unmount(el2)
  })
})

// ---------------------------------------------------------------------------
// Inline data injection
// ---------------------------------------------------------------------------

describe('<nubi-table> — inline data', () => {
  let el
  afterEach(() => el && unmount(el))

  test('renders injected rows and hides SAMPLE badge', async () => {
    el = makeTable({ data: JSON.stringify([{ a: 1, b: 'x' }, { a: 2, b: 'y' }]) })
    mount(el)
    await nextTick(5)
    expect(el.shadowRoot.querySelector('.nubi-badge').style.display).toBe('none')
    const rows = el.shadowRoot.querySelectorAll('tbody tr')
    expect(rows.length).toBe(2)
  })

  test('limit attribute caps displayed rows but total reflects full count', async () => {
    const data = Array.from({ length: 5 }, (_, i) => ({ id: i }))
    el = makeTable({ limit: '2', data: JSON.stringify(data) })
    mount(el)
    await nextTick(5)
    const rows = el.shadowRoot.querySelectorAll('tbody tr')
    expect(rows.length).toBe(2)
    expect(el.shadowRoot.querySelector('.nubi-footer').textContent).toContain('2 / 5')
  })

  test('columns attribute restricts and orders displayed columns', async () => {
    el = makeTable({
      columns: 'b,a',
      data: JSON.stringify([{ a: 1, b: 'x', c: 'hidden' }]),
    })
    mount(el)
    await nextTick(5)
    const headers = [...el.shadowRoot.querySelectorAll('thead th')].map((th) => th.textContent)
    expect(headers).toEqual(['b', 'a'])
    expect(headers).not.toContain('c')
  })

  test('escapes HTML in cell values (XSS safety)', async () => {
    el = makeTable({ data: JSON.stringify([{ name: '<img src=x onerror=alert(1)>' }]) })
    mount(el)
    await nextTick(5)
    const td = el.shadowRoot.querySelector('tbody td')
    // The raw markup must be escaped — no real <img> element injected.
    expect(td.querySelector('img')).toBeNull()
    expect(td.textContent).toContain('<img')
  })

  test('invalid JSON does not throw', async () => {
    el = makeTable({ data: 'nonsense{' })
    expect(() => mount(el)).not.toThrow()
    await nextTick(5)
    expect(el.shadowRoot.querySelector('.nubi-wrap')).toBeTruthy()
  })
})

// ---------------------------------------------------------------------------
// Error state
// ---------------------------------------------------------------------------

describe('<nubi-table> — error state', () => {
  let el
  afterEach(() => el && unmount(el))

  test('no-sample-fallback with no backend renders error state, no SAMPLE badge', async () => {
    el = makeTable({ 'query-id': 'demo', 'no-sample-fallback': '' })
    mount(el)
    await nextTick(5)
    expect(el.shadowRoot.querySelector('.nubi-error-state')).toBeTruthy()
    expect(el.shadowRoot.querySelector('.nubi-badge').style.display).toBe('none')
    expect(el.shadowRoot.querySelector('.nubi-footer').textContent).toBe('')
  })
})

// ---------------------------------------------------------------------------
// Theme + attributes
// ---------------------------------------------------------------------------

describe('<nubi-table> — theme + attributes', () => {
  let el
  afterEach(() => el && unmount(el))

  test('applies theme CSS custom properties to the host', async () => {
    el = makeTable({ theme: 'light', data: JSON.stringify([{ a: 1 }]) })
    mount(el)
    await nextTick(5)
    expect(el.style.getPropertyValue('--nubi-bg').length).toBeGreaterThan(0)
  })

  test('observedAttributes includes data, columns, limit and theme', () => {
    const attrs = customElements.get('nubi-table').observedAttributes
    for (const a of ['data', 'columns', 'limit', 'theme', 'no-sample-fallback']) {
      expect(attrs).toContain(a)
    }
  })
})
