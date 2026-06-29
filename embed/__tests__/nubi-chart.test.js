/**
 * nubi-chart.test.js — Component tests for <nubi-chart>.
 *
 * Covers sample fallback, inline data injection, theme application, config
 * parsing, the nubi:select / nubi:widget-ready events, and the no-sample
 * error state. ECharts is mocked so these run cleanly in jsdom (which has no
 * real canvas 2D context); the pure option-building logic is verified
 * separately in chart-options.test.js.
 *
 * Run with:  npm run test:embed
 */

import { describe, test, expect, beforeEach, afterEach, vi } from 'vitest'
import { mount, unmount, nextTick } from './helpers.js'

// --- Mock ECharts so init()/setOption() don't need a real canvas. ---------
const chartInstances = []
vi.mock('echarts', () => {
  function init() {
    const handlers = {}
    const inst = {
      _option: null,
      setOption(o) { this._option = o },
      getOption() { return this._option },
      on(ev, cb) { handlers[ev] = cb },
      _fire(ev, payload) { if (handlers[ev]) handlers[ev](payload) },
      resize() {},
      isDisposed() { return this._disposed === true },
      dispose() { this._disposed = true },
    }
    chartInstances.push(inst)
    return inst
  }
  return { init, __esModule: true }
})

// glScatter is only used for the >20k WebGL path; mock it to avoid WebGL.
vi.mock('../widgets/glScatter.js', () => ({
  createGlScatter: () => ({ draw() {}, destroy() {} }),
}))

const { NubiChart } = await import('../widgets/nubi-chart.js')
if (!customElements.get('nubi-chart')) customElements.define('nubi-chart', NubiChart)

function makeChart(attrs = {}) {
  const el = document.createElement('nubi-chart')
  for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v)
  return el
}

const INLINE = JSON.stringify([
  { month: 'Jan', sales: 120, region: 'North' },
  { month: 'Feb', sales: 200, region: 'South' },
  { month: 'Mar', sales: 150, region: 'North' },
])

beforeEach(() => { chartInstances.length = 0 })

// ---------------------------------------------------------------------------
// Sample fallback
// ---------------------------------------------------------------------------

describe('<nubi-chart> — sample fallback', () => {
  let el
  afterEach(() => el && unmount(el))

  test('renders sample data and shows the SAMPLE badge', async () => {
    el = makeChart({ 'query-id': 'demo', backend: 'http://localhost:9' })
    // No token / unreachable backend → fetch fails → sample fallback.
    mount(el)
    await nextTick(30)
    const badge = el.shadowRoot.querySelector('.nubi-badge')
    expect(badge.className).toContain('sample')
    const note = el.shadowRoot.querySelector('.nubi-sample-note')
    expect(note.style.display).toBe('block')
  })

  test('no-sample-fallback shows an error state instead', async () => {
    el = makeChart({ 'query-id': 'demo', backend: 'http://localhost:9', 'no-sample-fallback': '' })
    mount(el)
    await nextTick(30)
    expect(el.shadowRoot.querySelector('.nubi-error-state')).toBeTruthy()
  })
})

// ---------------------------------------------------------------------------
// Inline data + events
// ---------------------------------------------------------------------------

describe('<nubi-chart> — inline data & events', () => {
  let el
  afterEach(() => el && unmount(el))

  test('renders injected rows via echarts (badge live, note hidden)', async () => {
    el = makeChart({ type: 'bar', x: 'month', y: 'sales', data: INLINE })
    mount(el)
    await nextTick(5)
    expect(chartInstances.length).toBe(1)
    expect(el.shadowRoot.querySelector('.nubi-sample-note').style.display).toBe('none')
    const opt = chartInstances[0].getOption()
    expect(opt.series[0].type).toBe('bar')
  })

  test('emits nubi:widget-ready with type + renderer', async () => {
    el = makeChart({ type: 'line', x: 'month', y: 'sales', data: INLINE })
    const events = []
    el.addEventListener('nubi:widget-ready', (e) => events.push(e.detail))
    mount(el)
    await nextTick(5)
    expect(events.length).toBeGreaterThanOrEqual(1)
    expect(events[0].type).toBe('line')
    expect(events[0].renderer).toBe('echarts')
    expect(events[0].rows).toBe(3)
  })

  test('clicking a point emits nubi:select', async () => {
    el = makeChart({ type: 'bar', x: 'month', y: 'sales', data: INLINE })
    const selected = []
    el.addEventListener('nubi:select', (e) => selected.push(e.detail))
    mount(el)
    await nextTick(5)
    // Simulate an echarts click on the mocked instance.
    chartInstances[0]._fire('click', { name: 'Feb', value: 200, dataIndex: 1, seriesName: 'sales', seriesIndex: 0 })
    expect(selected).toHaveLength(1)
    expect(selected[0].name).toBe('Feb')
    expect(selected[0].value).toBe(200)
  })

  test('invalid data attribute → error state, no throw', async () => {
    el = makeChart({ data: '{not json' })
    mount(el)
    await nextTick(5)
    expect(el.shadowRoot.querySelector('.nubi-error-state')).toBeTruthy()
  })
})

// ---------------------------------------------------------------------------
// Config + attribute customization
// ---------------------------------------------------------------------------

describe('<nubi-chart> — customization', () => {
  let el
  afterEach(() => el && unmount(el))

  test('title attribute flows into the built option', async () => {
    el = makeChart({ type: 'bar', x: 'month', y: 'sales', title: 'Monthly Sales', data: INLINE })
    mount(el)
    await nextTick(5)
    expect(chartInstances[0].getOption().title.text).toBe('Monthly Sales')
  })

  test('config JSON deep-merges and wins over attributes', async () => {
    el = makeChart({
      type: 'bar', x: 'month', y: 'sales', title: 'Attr Title', data: INLINE,
      config: JSON.stringify({ title: 'Config Title', animation: false }),
    })
    mount(el)
    await nextTick(5)
    const opt = chartInstances[0].getOption()
    expect(opt.title.text).toBe('Config Title')
    expect(opt.animation).toBe(false)
  })

  test('reference line attribute via config renders a markLine', async () => {
    el = makeChart({
      type: 'bar', x: 'month', y: 'sales', data: INLINE,
      config: JSON.stringify({ referenceLines: [{ value: 175, label: 'Target' }] }),
    })
    mount(el)
    await nextTick(5)
    expect(chartInstances[0].getOption().series[0].markLine).toBeTruthy()
  })

  test('theme tokens applied to host as CSS custom properties', async () => {
    el = makeChart({ type: 'bar', x: 'month', y: 'sales', data: INLINE, theme: 'light' })
    mount(el)
    await nextTick(5)
    expect(el.style.getPropertyValue('--nubi-bg').trim()).toBe('#ffffff')
  })

  test('dual-axis via y2 attribute builds two y-axes', async () => {
    const rows = JSON.stringify([{ m: 'Jan', rev: 100, mar: 0.2 }, { m: 'Feb', rev: 140, mar: 0.3 }])
    el = makeChart({ type: 'line', x: 'm', y: 'rev', y2: 'mar', data: rows })
    mount(el)
    await nextTick(5)
    const opt = chartInstances[0].getOption()
    expect(Array.isArray(opt.yAxis)).toBe(true)
    expect(opt.yAxis).toHaveLength(2)
  })
})
