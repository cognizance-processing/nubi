/**
 * wc-foundation.test.js — Unit tests for the React web-component foundation.
 *
 * Run with:
 *   node --test embed/__tests__/wc-foundation.test.js
 *
 * Strategy
 * --------
 * - Uses jsdom (already a devDependency) to provide DOM APIs.
 * - theme.js and events.js have no React dependency — imported directly.
 * - All assertions use node:assert/strict.
 * - defineNubiElement attribute-coercion logic is tested inline (factory
 *   brings in React; we keep those tests hermetic by inlining the coercion).
 * - Cross-filter bus logic is tested inline to avoid React context imports.
 */

import { describe, it } from 'node:test'
import assert from 'node:assert/strict'
import { JSDOM } from 'jsdom'

// ---------------------------------------------------------------------------
// Bootstrap a jsdom window so CustomEvent and document.createElement work
// ---------------------------------------------------------------------------

const { window: jsWindow } = new JSDOM('<!DOCTYPE html><html><body></body></html>', {
  url: 'http://localhost',
})
globalThis.window    = jsWindow
globalThis.document  = jsWindow.document
globalThis.CustomEvent = jsWindow.CustomEvent
globalThis.HTMLElement = jsWindow.HTMLElement

// ---------------------------------------------------------------------------
// Import modules under test
// ---------------------------------------------------------------------------

import {
  NUBI_TOKENS,
  NUBI_TOKEN_DEFAULTS,
  BRANDING_OFF_TOKENS,
  injectTheme,
} from '../theme.js'

import {
  emitNubiEvent,
  emitSave,
  emitDirty,
  emitRun,
  emitSelect,
  emitCrossFilter,
  emitNavigate,
  emitError,
} from '../events.js'

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/**
 * Make a minimal mock element that tracks dispatched events.
 */
function makeMockElement() {
  const listeners = {}
  return {
    tagName: 'NUBI-TEST',
    dispatchEvent(event) {
      ;(listeners[event.type] || []).forEach(cb => cb(event))
    },
    addEventListener(type, cb) {
      if (!listeners[type]) listeners[type] = []
      listeners[type].push(cb)
    },
  }
}

/**
 * Make a minimal mock shadow root backed by document.createElement.
 * querySelector only handles 'style[data-nubi-theme]'.
 */
function makeMockShadowRoot() {
  const styleEl = document.createElement('style')
  styleEl.setAttribute('data-nubi-theme', '1')
  // Pre-populate so querySelector finds it on repeated calls
  let injected = false

  return {
    _styleEl: styleEl,
    querySelector(sel) {
      if (sel === 'style[data-nubi-theme]') return injected ? styleEl : null
      return null
    },
    insertBefore(node) {
      injected = true
    },
    appendChild(node) {
      injected = true
    },
  }
}

// ---------------------------------------------------------------------------
// theme tests
// ---------------------------------------------------------------------------

describe('theme', () => {
  it('NUBI_TOKEN_DEFAULTS contains all required color tokens', () => {
    const required = [
      '--nubi-bg', '--nubi-fg', '--nubi-accent', '--nubi-border',
      '--nubi-surface', '--nubi-muted', '--nubi-error',
      '--nubi-success', '--nubi-warning',
    ]
    for (const tok of required) {
      assert.ok(tok in NUBI_TOKEN_DEFAULTS, `missing token: ${tok}`)
    }
  })

  it('NUBI_TOKEN_DEFAULTS contains typography tokens', () => {
    assert.ok('--nubi-font-family'   in NUBI_TOKEN_DEFAULTS)
    assert.ok('--nubi-font-size-base' in NUBI_TOKEN_DEFAULTS)
    assert.ok('--nubi-font-weight-bold' in NUBI_TOKEN_DEFAULTS)
  })

  it('NUBI_TOKEN_DEFAULTS contains spacing tokens', () => {
    const spacers = [
      '--nubi-space-xs', '--nubi-space-sm', '--nubi-space-md',
      '--nubi-space-lg', '--nubi-space-xl',
    ]
    for (const tok of spacers) {
      assert.ok(tok in NUBI_TOKEN_DEFAULTS, `missing spacing token: ${tok}`)
    }
  })

  it('NUBI_TOKEN_DEFAULTS contains radius tokens', () => {
    assert.ok('--nubi-radius-sm' in NUBI_TOKEN_DEFAULTS)
    assert.ok('--nubi-radius-md' in NUBI_TOKEN_DEFAULTS)
    assert.ok('--nubi-radius-lg' in NUBI_TOKEN_DEFAULTS)
  })

  it('BRANDING_OFF_TOKENS sets --nubi-brand-show to 0', () => {
    assert.equal(BRANDING_OFF_TOKENS['--nubi-brand-show'], '0')
  })

  it('NUBI_TOKEN_DEFAULTS --nubi-brand-show defaults to 1', () => {
    assert.equal(NUBI_TOKEN_DEFAULTS['--nubi-brand-show'], '1')
  })

  it('NUBI_TOKENS list covers all token names', () => {
    // Every key in NUBI_TOKEN_DEFAULTS should appear in NUBI_TOKENS
    for (const k of Object.keys(NUBI_TOKEN_DEFAULTS)) {
      assert.ok(NUBI_TOKENS.includes(k), `${k} missing from NUBI_TOKENS`)
    }
  })

  it('injectTheme inserts :host block with --nubi-bg', () => {
    const styleEl = document.createElement('style')
    styleEl.setAttribute('data-nubi-theme', '1')
    const sr = {
      _styleEl: styleEl,
      querySelector(sel) { return sel === 'style[data-nubi-theme]' ? styleEl : null },
      insertBefore() {},
      appendChild() {},
    }
    injectTheme(sr, {})
    assert.ok(styleEl.textContent.includes(':host {'), 'should contain :host block')
    assert.ok(styleEl.textContent.includes('--nubi-bg'), 'should contain --nubi-bg')
  })

  it('injectTheme sets custom token overrides in shadow root', () => {
    const styleEl = document.createElement('style')
    styleEl.setAttribute('data-nubi-theme', '1')
    const sr = {
      querySelector() { return styleEl },
      insertBefore() {},
      appendChild() {},
    }
    injectTheme(sr, { '--nubi-bg': '#ff0000', '--nubi-brand-show': '0' })
    assert.ok(styleEl.textContent.includes('--nubi-bg: #ff0000'), 'override should be set')
    assert.ok(styleEl.textContent.includes('--nubi-brand-show: 0'), 'brand-show override should be set')
  })

  it('injectTheme merges overrides with defaults', () => {
    const styleEl = document.createElement('style')
    styleEl.setAttribute('data-nubi-theme', '1')
    const sr = {
      querySelector() { return styleEl },
      insertBefore() {},
      appendChild() {},
    }
    injectTheme(sr, { '--nubi-accent': '#custom-color' })
    assert.ok(styleEl.textContent.includes('--nubi-accent: #custom-color'))
    assert.ok(styleEl.textContent.includes('--nubi-fg:'))  // non-overridden default present
  })

  it('injectTheme creates new style element when none exists', () => {
    let inserted = null
    const sr = {
      querySelector() { return null },  // no existing element
      insertBefore(node) { inserted = node },
      appendChild() {},
    }
    injectTheme(sr, {})
    assert.ok(inserted, 'should have inserted a new style element')
    assert.ok(inserted.textContent.includes(':host {'))
  })
})

// ---------------------------------------------------------------------------
// events tests
// ---------------------------------------------------------------------------

describe('events', () => {
  it('emitNubiEvent fires with bubbles and composed true', () => {
    const el = makeMockElement()
    let received = null
    el.addEventListener('nubi:test', (e) => { received = e })

    emitNubiEvent(el, 'nubi:test', { foo: 'bar' })

    assert.ok(received, 'event should have been received')
    assert.equal(received.detail.foo, 'bar')
    assert.equal(received.bubbles, true)
    assert.equal(received.composed, true)
  })

  it('emitSave fires nubi:save with id and spec', () => {
    const el = makeMockElement()
    let detail = null
    el.addEventListener('nubi:save', (e) => { detail = e.detail })

    emitSave(el, { id: 'widget-1', spec: { type: 'kpi' } })

    assert.ok(detail, 'should have received detail')
    assert.equal(detail.id, 'widget-1')
    assert.deepEqual(detail.spec, { type: 'kpi' })
  })

  it('emitDirty fires nubi:dirty with isDirty flag', () => {
    const el = makeMockElement()
    let detail = null
    el.addEventListener('nubi:dirty', (e) => { detail = e.detail })

    emitDirty(el, { id: 'w', isDirty: true })

    assert.ok(detail)
    assert.equal(detail.isDirty, true)
  })

  it('emitRun fires nubi:run with query', () => {
    const el = makeMockElement()
    let detail = null
    el.addEventListener('nubi:run', (e) => { detail = e.detail })

    emitRun(el, { id: 'w', query: 'SELECT 1' })

    assert.ok(detail)
    assert.equal(detail.query, 'SELECT 1')
  })

  it('emitSelect fires nubi:select with rowIndex and row', () => {
    const el = makeMockElement()
    let detail = null
    el.addEventListener('nubi:select', (e) => { detail = e.detail })

    emitSelect(el, { id: 'kpi', rowIndex: 3, row: { revenue: 42 } })

    assert.ok(detail)
    assert.equal(detail.rowIndex, 3)
    assert.deepEqual(detail.row, { revenue: 42 })
  })

  it('emitCrossFilter fires nubi:cross-filter with correct detail', () => {
    const el = makeMockElement()
    let detail = null
    el.addEventListener('nubi:cross-filter', (e) => { detail = e.detail })

    emitCrossFilter(el, { filterId: 'country', values: ['US', 'ZA'] })

    assert.ok(detail)
    assert.equal(detail.filterId, 'country')
    assert.deepEqual(detail.values, ['US', 'ZA'])
  })

  it('emitNavigate fires nubi:navigate with href and target', () => {
    const el = makeMockElement()
    let detail = null
    el.addEventListener('nubi:navigate', (e) => { detail = e.detail })

    emitNavigate(el, { href: '/dashboard/123', target: '_blank' })

    assert.ok(detail)
    assert.equal(detail.href, '/dashboard/123')
    assert.equal(detail.target, '_blank')
  })

  it('emitError fires nubi:error with id, error, and code', () => {
    const el = makeMockElement()
    let detail = null
    el.addEventListener('nubi:error', (e) => { detail = e.detail })

    emitError(el, { id: 'kpi-1', error: 'Network timeout', code: 'ERR_TIMEOUT' })

    assert.ok(detail)
    assert.equal(detail.id, 'kpi-1')
    assert.equal(detail.error, 'Network timeout')
    assert.equal(detail.code, 'ERR_TIMEOUT')
  })

  it('emitError fires nubi:error without code', () => {
    const el = makeMockElement()
    let detail = null
    el.addEventListener('nubi:error', (e) => { detail = e.detail })

    emitError(el, { id: 'k', error: 'oops' })

    assert.ok(detail)
    assert.equal(detail.error, 'oops')
    assert.equal(detail.code, undefined)
  })

  it('nubi:cross-filter event has composed:true', () => {
    const el = makeMockElement()
    let evt = null
    el.addEventListener('nubi:cross-filter', (e) => { evt = e })

    emitCrossFilter(el, { filterId: 'year', values: [2024] })

    assert.ok(evt)
    assert.equal(evt.composed, true, 'cross-filter must escape shadow DOM')
    assert.equal(evt.bubbles, true)
  })
})

// ---------------------------------------------------------------------------
// Attribute coercion (defineNubiElement internals — tested inline)
// ---------------------------------------------------------------------------

describe('defineNubiElement attribute coercion', () => {
  // Inline coercion function mirrors react-wc.js exactly
  function coerceAttr(value, type, attrName) {
    if (value === null) {
      if (type === 'boolean') return false
      return null
    }
    switch (type) {
      case 'number': {
        const n = Number(value)
        return Number.isNaN(n) ? null : n
      }
      case 'boolean':
        return value !== 'false' && value !== '0'
      case 'json':
        try { return JSON.parse(value) }
        catch { return null }
      case 'string':
      default:
        return value
    }
  }

  it('maps string attribute to string prop', () => {
    assert.equal(coerceAttr('hello', 'string', 'x'), 'hello')
  })

  it('maps number attribute to number prop', () => {
    assert.equal(coerceAttr('42', 'number', 'x'), 42)
    assert.equal(coerceAttr('-5.5', 'number', 'x'), -5.5)
  })

  it('maps non-numeric string to null for number type', () => {
    assert.equal(coerceAttr('not-a-number', 'number', 'x'), null)
  })

  it('maps boolean attribute — true cases', () => {
    assert.equal(coerceAttr('true', 'boolean', 'x'), true)
    assert.equal(coerceAttr('', 'boolean', 'x'), true)
    assert.equal(coerceAttr('1', 'boolean', 'x'), true)
  })

  it('maps boolean attribute — false cases', () => {
    assert.equal(coerceAttr('false', 'boolean', 'x'), false)
    assert.equal(coerceAttr('0', 'boolean', 'x'), false)
  })

  it('maps absent boolean attribute (null) to false', () => {
    assert.equal(coerceAttr(null, 'boolean', 'x'), false)
  })

  it('maps json attribute to parsed object', () => {
    assert.deepEqual(coerceAttr('{"a":1}', 'json', 'x'), { a: 1 })
    assert.deepEqual(coerceAttr('[1,2,3]', 'json', 'x'), [1, 2, 3])
  })

  it('maps invalid json to null', () => {
    assert.equal(coerceAttr('{bad json}', 'json', 'x'), null)
  })

  it('maps absent attribute (null) to null for string/number/json', () => {
    assert.equal(coerceAttr(null, 'string', 'x'), null)
    assert.equal(coerceAttr(null, 'number', 'x'), null)
    assert.equal(coerceAttr(null, 'json', 'x'), null)
  })
})

// ---------------------------------------------------------------------------
// Cross-filter bus (from nubi-context.js — logic tested inline)
// ---------------------------------------------------------------------------

describe('crossFilterBus', () => {
  function createCrossFilterBus() {
    const _filters = new Map()
    const _listeners = new Set()
    return {
      subscribe(cb) {
        _listeners.add(cb)
        return () => _listeners.delete(cb)
      },
      publish({ filterId, values }) {
        if (values.length === 0) { _filters.delete(filterId) }
        else { _filters.set(filterId, values) }
        const snapshot = new Map(_filters)
        _listeners.forEach(cb => cb(snapshot))
      },
      getFilters() { return new Map(_filters) },
    }
  }

  it('subscribe receives publish events', () => {
    const bus = createCrossFilterBus()
    let received = null
    bus.subscribe((f) => { received = f })

    bus.publish({ filterId: 'country', values: ['ZA', 'US'] })

    assert.ok(received)
    assert.deepEqual(received.get('country'), ['ZA', 'US'])
  })

  it('publish with empty values clears the filter', () => {
    const bus = createCrossFilterBus()
    bus.publish({ filterId: 'region', values: ['West'] })
    bus.publish({ filterId: 'region', values: [] })

    assert.equal(bus.getFilters().has('region'), false)
  })

  it('getFilters returns current state', () => {
    const bus = createCrossFilterBus()
    bus.publish({ filterId: 'year', values: [2024] })
    bus.publish({ filterId: 'month', values: [6, 7] })

    const f = bus.getFilters()
    assert.deepEqual(f.get('year'), [2024])
    assert.deepEqual(f.get('month'), [6, 7])
  })

  it('unsubscribe stops receiving events', () => {
    const bus = createCrossFilterBus()
    let callCount = 0
    const unsub = bus.subscribe(() => callCount++)
    bus.publish({ filterId: 'x', values: [1] })
    unsub()
    bus.publish({ filterId: 'x', values: [2] })

    assert.equal(callCount, 1)
  })

  it('multiple subscribers each receive publish', () => {
    const bus = createCrossFilterBus()
    let a = 0, b = 0
    bus.subscribe(() => a++)
    bus.subscribe(() => b++)
    bus.publish({ filterId: 'f', values: ['v'] })

    assert.equal(a, 1)
    assert.equal(b, 1)
  })
})

// ---------------------------------------------------------------------------
// nubi-kpi-react — lightweight proof tests (no React render needed)
// ---------------------------------------------------------------------------

describe('nubi-kpi-react contract', () => {
  it('emitSelect fires nubi:select with composed:true', () => {
    const el = makeMockElement()
    let evt = null
    el.addEventListener('nubi:select', (e) => { evt = e })

    emitSelect(el, { id: 'kpi-react', rowIndex: 0, row: { value: 100 } })

    assert.ok(evt, 'nubi:select must fire')
    assert.equal(evt.composed, true, 'event must escape shadow DOM')
    assert.equal(evt.bubbles, true, 'event must bubble')
    assert.equal(evt.detail.id, 'kpi-react')
    assert.equal(evt.detail.rowIndex, 0)
    assert.deepEqual(evt.detail.row, { value: 100 })
  })

  it('BRANDING_OFF_TOKENS disables branding (contract verification)', () => {
    assert.equal(BRANDING_OFF_TOKENS['--nubi-brand-show'], '0')
    assert.equal(NUBI_TOKEN_DEFAULTS['--nubi-brand-show'], '1')
  })

  it('injectTheme with BRANDING_OFF_TOKENS sets brand-show to 0', () => {
    const styleEl = document.createElement('style')
    styleEl.setAttribute('data-nubi-theme', '1')
    const sr = {
      querySelector() { return styleEl },
      insertBefore() {},
      appendChild() {},
    }
    injectTheme(sr, BRANDING_OFF_TOKENS)
    assert.ok(styleEl.textContent.includes('--nubi-brand-show: 0'))
  })
})
