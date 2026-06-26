/**
 * nubi-kpi.test.js — Unit tests for <nubi-kpi> (big-number metric card).
 *
 * Exercises the under-covered rendering paths: sample fallback, inline data
 * injection, no-sample-fallback error state, theme tokens, and widget events.
 *
 * Run with:
 *   npm run test:embed
 *   # or: npx vitest run embed/__tests__/nubi-kpi.test.js
 */

import { describe, test, expect, beforeEach, afterEach } from 'vitest'
import { mount, unmount, nextTick } from './helpers.js'

// Register the widget under test. The widget module only exports the class;
// custom-element registration normally happens in widgets/index.js, so we
// define it here directly (avoids pulling in echarts via the index barrel).
import { NubiKpi } from '../widgets/nubi-kpi.js'
if (!customElements.get('nubi-kpi')) customElements.define('nubi-kpi', NubiKpi)

function makeKpi(attrs = {}) {
  const el = document.createElement('nubi-kpi')
  for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v)
  return el
}

// ---------------------------------------------------------------------------
// Sample fallback (no backend, no data → demo card always renders)
// ---------------------------------------------------------------------------

describe('<nubi-kpi> — sample fallback', () => {
  let el
  beforeEach(() => { el = makeKpi({ theme: 'dark' }); mount(el) })
  afterEach(() => unmount(el))

  test('renders into shadow DOM', async () => {
    await nextTick(5)
    expect(el.shadowRoot).toBeTruthy()
    expect(el.shadowRoot.querySelector('.kpi-wrap')).toBeTruthy()
  })

  test('never stays stuck on the loading state', async () => {
    await nextTick(5)
    const value = el.shadowRoot.querySelector('.kpi-value')
    expect(value.classList.contains('nubi-loading')).toBe(false)
    expect(value.textContent).not.toBe('…')
  })

  test('shows the SAMPLE badge + sample note', async () => {
    await nextTick(5)
    const badge = el.shadowRoot.querySelector('.nubi-badge.sample')
    const note = el.shadowRoot.querySelector('.nubi-sample-note')
    expect(badge.style.display).not.toBe('none')
    expect(note.style.display).not.toBe('none')
  })

  test('renders a non-empty formatted metric value', async () => {
    await nextTick(5)
    const value = el.shadowRoot.querySelector('.kpi-value')
    expect(value.textContent.trim().length).toBeGreaterThan(0)
  })

  test('emits nubi:widget-ready with renderer:kpi', async () => {
    const el2 = makeKpi({ theme: 'dark' })
    const events = []
    el2.addEventListener('nubi:widget-ready', (e) => events.push(e.detail))
    mount(el2)
    await nextTick(5)
    expect(events.length).toBeGreaterThanOrEqual(1)
    expect(events[0].renderer).toBe('kpi')
    expect(events[0].rows).toBeGreaterThanOrEqual(1)
    unmount(el2)
  })
})

// ---------------------------------------------------------------------------
// Inline data injection (bypasses fetch, no SAMPLE badge)
// ---------------------------------------------------------------------------

describe('<nubi-kpi> — inline data', () => {
  let el
  afterEach(() => el && unmount(el))

  test('renders the injected value without a SAMPLE badge', async () => {
    el = makeKpi({
      'value-col': 'revenue',
      label: 'Revenue',
      data: JSON.stringify([{ revenue: 50000 }]),
    })
    mount(el)
    await nextTick(5)
    const badge = el.shadowRoot.querySelector('.nubi-badge.sample')
    expect(badge.style.display).toBe('none')
    const value = el.shadowRoot.querySelector('.kpi-value')
    // Auto-compact: 50000 → "50.0K"
    expect(value.textContent).toContain('K')
    expect(el.shadowRoot.querySelector('.kpi-label').textContent).toBe('Revenue')
  })

  test('renders target row (goal bar + pct + RAG chip) when target columns present', async () => {
    el = makeKpi({
      'value-col': 'revenue',
      'target-col': 'revenue_target',
      data: JSON.stringify([{
        revenue: 80000,
        revenue_target: 100000,
        revenue_pct_to_goal: 0.8,
        revenue_rag: 'amber',
      }]),
    })
    mount(el)
    await nextTick(5)
    const row = el.shadowRoot.querySelector('.kpi-target-row')
    expect(row.style.display).toBe('flex')
    const chip = el.shadowRoot.querySelector('.kpi-rag-chip')
    expect(chip.classList.contains('amber')).toBe(true)
    expect(chip.textContent).toBe('AMBER')
    const pctLabel = el.shadowRoot.querySelector('.kpi-pct-label')
    expect(pctLabel.textContent).toBe('80%')
    const bar = el.shadowRoot.querySelector('.kpi-goal-bar')
    // (jsdom's CSSOM may normalise "80.0%" → "80%")
    expect(parseFloat(bar.style.width)).toBeCloseTo(80)
  })

  test('goal bar width is clamped to 100% when over goal', async () => {
    el = makeKpi({
      'value-col': 'revenue',
      'target-col': 'revenue_target',
      data: JSON.stringify([{
        revenue: 200000,
        revenue_pct_to_goal: 2.5,
        revenue_rag: 'green',
      }]),
    })
    mount(el)
    await nextTick(5)
    const bar = el.shadowRoot.querySelector('.kpi-goal-bar')
    expect(parseFloat(bar.style.width)).toBeCloseTo(100)
  })

  test('target row hidden when target-col set but columns absent from data', async () => {
    el = makeKpi({
      'value-col': 'revenue',
      'target-col': 'revenue_target',
      data: JSON.stringify([{ revenue: 1234 }]),
    })
    mount(el)
    await nextTick(5)
    expect(el.shadowRoot.querySelector('.kpi-target-row').style.display).toBe('none')
  })

  test('invalid JSON in data attribute does not throw and leaves loading scaffold', async () => {
    el = makeKpi({ 'value-col': 'revenue', data: '{not valid json' })
    expect(() => mount(el)).not.toThrow()
    await nextTick(5)
    expect(el.shadowRoot.querySelector('.kpi-wrap')).toBeTruthy()
  })
})

// ---------------------------------------------------------------------------
// no-sample-fallback → error state instead of sample card
// ---------------------------------------------------------------------------

describe('<nubi-kpi> — error state', () => {
  let el
  afterEach(() => el && unmount(el))

  test('no-sample-fallback with no backend renders friendly error, not a giant number', async () => {
    el = makeKpi({ 'value-col': 'revenue', 'no-sample-fallback': '' })
    mount(el)
    await nextTick(5)
    const errState = el.shadowRoot.querySelector('.kpi-error-state')
    expect(errState).toBeTruthy()
    const value = el.shadowRoot.querySelector('.kpi-value')
    expect(value.textContent).toBe('')
    // No SAMPLE badge in error state
    expect(el.shadowRoot.querySelector('.nubi-badge.sample').style.display).toBe('none')
  })
})

// ---------------------------------------------------------------------------
// Theme tokens
// ---------------------------------------------------------------------------

describe('<nubi-kpi> — theme tokens', () => {
  let el
  afterEach(() => el && unmount(el))

  test('applies theme CSS custom properties to the host', async () => {
    el = makeKpi({ theme: 'light', 'value-col': 'x', data: JSON.stringify([{ x: 1 }]) })
    mount(el)
    await nextTick(5)
    // applyTheme sets at least one --nubi-* custom property inline
    const bg = el.style.getPropertyValue('--nubi-bg')
    expect(typeof bg).toBe('string')
    expect(bg.length).toBeGreaterThan(0)
  })

  test('observedAttributes includes theme, target columns and data', () => {
    const attrs = customElements.get('nubi-kpi').observedAttributes
    for (const a of ['theme', 'target-col', 'rag-col', 'pct-col', 'data', 'no-sample-fallback']) {
      expect(attrs).toContain(a)
    }
  })
})
