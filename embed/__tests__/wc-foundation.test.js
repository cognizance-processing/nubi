/**
 * wc-foundation.test.js — Unit tests for the React web-component foundation.
 *
 * Reconciled against the canonical (authoring) theme.js and events.js APIs:
 *   theme.js  → NUBI_THEME_TOKENS, NUBI_THEME_PRESETS, NUBI_THEME_CSS, applyTheme
 *   events.js → NUBI_RUN/SAVE/DIRTY/ERROR/SELECT, emitRun/emitSave/emitDirty/emitError/emitSelect
 *   nubi-context.js → decodeScopes, hasScope
 *
 * Strategy
 * --------
 * - Uses vitest + jsdom environment (configured in embed/vitest.config.js).
 * - theme.js and events.js have no React dependency — imported directly.
 * - defineNubiElement attribute-coercion logic is tested inline.
 * - Cross-filter bus logic is tested inline to avoid heavy context imports.
 */

import { describe, it, expect } from 'vitest'

// ---------------------------------------------------------------------------
// Compatibility shim: map assert.* → expect(...)
// ---------------------------------------------------------------------------

const assert = {
  ok:        (val, msg)  => expect(val, msg).toBeTruthy(),
  equal:     (a, b, msg) => expect(a, msg).toBe(b),
  deepEqual: (a, b, msg) => expect(a, msg).toEqual(b),
}

// ---------------------------------------------------------------------------
// Import modules under test
// (jsdom environment is provided by vitest.config.js + setup.js)
// ---------------------------------------------------------------------------

import {
  NUBI_THEME_TOKENS,
  NUBI_THEME_PRESETS,
  NUBI_THEME_CSS,
  applyTheme,
} from '../theme.js'

import {
  NUBI_RUN,
  NUBI_SAVE,
  NUBI_DIRTY,
  NUBI_ERROR,
  NUBI_SELECT,
  emitRun,
  emitSave,
  emitDirty,
  emitError,
  emitSelect,
} from '../events.js'

import { decodeScopes, hasScope } from '../nubi-context.js'

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/**
 * Make a minimal mock element that tracks dispatched events.
 * Uses the jsdom CustomEvent / EventTarget from the vitest environment.
 */
function makeMockElement() {
  const listeners = {}
  return {
    tagName: 'NUBI-TEST',
    style: { setProperty: () => {} },
    dispatchEvent(event) {
      ;(listeners[event.type] || []).forEach(cb => cb(event))
    },
    addEventListener(type, cb) {
      if (!listeners[type]) listeners[type] = []
      listeners[type].push(cb)
    },
  }
}

// ---------------------------------------------------------------------------
// theme tests (canonical: 25-token design system)
// ---------------------------------------------------------------------------

describe('theme', () => {
  it('NUBI_THEME_TOKENS contains the required background tokens', () => {
    const required = ['--nubi-bg', '--nubi-bg-2', '--nubi-bg-3']
    for (const tok of required) {
      assert.ok(tok in NUBI_THEME_TOKENS, `missing token: ${tok}`)
    }
  })

  it('NUBI_THEME_TOKENS contains foreground tokens', () => {
    assert.ok('--nubi-fg' in NUBI_THEME_TOKENS)
    assert.ok('--nubi-fg-muted' in NUBI_THEME_TOKENS)
  })

  it('NUBI_THEME_TOKENS contains semantic color tokens', () => {
    const required = ['--nubi-primary', '--nubi-success', '--nubi-warning', '--nubi-error']
    for (const tok of required) {
      assert.ok(tok in NUBI_THEME_TOKENS, `missing token: ${tok}`)
    }
  })

  it('NUBI_THEME_TOKENS contains typography tokens', () => {
    assert.ok('--nubi-font-sans'       in NUBI_THEME_TOKENS)
    assert.ok('--nubi-font-mono'       in NUBI_THEME_TOKENS)
    assert.ok('--nubi-font-size-base'  in NUBI_THEME_TOKENS)
  })

  it('NUBI_THEME_TOKENS contains shape tokens', () => {
    assert.ok('--nubi-radius'    in NUBI_THEME_TOKENS)
    assert.ok('--nubi-radius-sm' in NUBI_THEME_TOKENS)
  })

  it('NUBI_THEME_TOKENS contains z-layer tokens', () => {
    assert.ok('--nubi-z-editor'  in NUBI_THEME_TOKENS)
    assert.ok('--nubi-z-overlay' in NUBI_THEME_TOKENS)
    assert.ok('--nubi-z-popover' in NUBI_THEME_TOKENS)
  })

  it('NUBI_THEME_TOKENS has exactly 25 tokens', () => {
    assert.equal(Object.keys(NUBI_THEME_TOKENS).length, 25)
  })

  it('NUBI_THEME_PRESETS has dark and light presets', () => {
    assert.ok('dark'  in NUBI_THEME_PRESETS)
    assert.ok('light' in NUBI_THEME_PRESETS)
  })

  it('dark preset is NUBI_THEME_TOKENS', () => {
    assert.equal(NUBI_THEME_PRESETS.dark, NUBI_THEME_TOKENS)
  })

  it('light preset has same token keys as dark preset', () => {
    const darkKeys  = Object.keys(NUBI_THEME_PRESETS.dark).sort()
    const lightKeys = Object.keys(NUBI_THEME_PRESETS.light).sort()
    assert.deepEqual(darkKeys, lightKeys)
  })

  it('light preset --nubi-bg is white (#ffffff)', () => {
    assert.equal(NUBI_THEME_PRESETS.light['--nubi-bg'], '#ffffff')
  })

  it('NUBI_THEME_CSS is a non-empty string containing :host', () => {
    assert.ok(typeof NUBI_THEME_CSS === 'string' && NUBI_THEME_CSS.length > 0)
    assert.ok(NUBI_THEME_CSS.includes(':host {'))
    assert.ok(NUBI_THEME_CSS.includes('--nubi-bg'))
  })

  it('applyTheme sets CSS custom properties on an element', () => {
    const el = document.createElement('div')
    applyTheme(el, 'dark')
    // The inline style should be set (jsdom supports setProperty)
    const bg = el.style.getPropertyValue('--nubi-bg')
    assert.ok(bg && bg.length > 0, `expected --nubi-bg to be set, got: "${bg}"`)
  })

  it('applyTheme respects overrides', () => {
    const el = document.createElement('div')
    applyTheme(el, 'dark', { '--nubi-bg': '#abcdef' })
    const bg = el.style.getPropertyValue('--nubi-bg')
    assert.equal(bg, '#abcdef')
  })

  it('applyTheme falls back to dark preset for unknown preset name', () => {
    const el = document.createElement('div')
    applyTheme(el, 'ocean')  // unknown preset
    const bg = el.style.getPropertyValue('--nubi-bg')
    // Should fall back to dark preset value
    assert.equal(bg, NUBI_THEME_TOKENS['--nubi-bg'])
  })
})

// ---------------------------------------------------------------------------
// events tests (canonical event name constants + emit helpers)
// ---------------------------------------------------------------------------

describe('events', () => {
  it('event name constants are correct', () => {
    assert.equal(NUBI_RUN,    'nubi:run')
    assert.equal(NUBI_SAVE,   'nubi:save')
    assert.equal(NUBI_DIRTY,  'nubi:dirty')
    assert.equal(NUBI_ERROR,  'nubi:error')
    assert.equal(NUBI_SELECT, 'nubi:select')
  })

  it('emitRun fires nubi:run with bubbles and composed true', () => {
    const el = makeMockElement()
    let received = null
    el.addEventListener('nubi:run', (e) => { received = e })

    emitRun(el, { sql: 'SELECT 1' })

    assert.ok(received, 'event should have been received')
    assert.equal(received.detail.sql, 'SELECT 1')
    assert.equal(received.bubbles,  true)
    assert.equal(received.composed, true)
  })

  it('emitSave fires nubi:save with queryId and name', () => {
    const el = makeMockElement()
    let detail = null
    el.addEventListener('nubi:save', (e) => { detail = e.detail })

    emitSave(el, { queryId: 'q-1', name: 'My query' })

    assert.ok(detail)
    assert.equal(detail.queryId, 'q-1')
    assert.equal(detail.name, 'My query')
  })

  it('emitDirty fires nubi:dirty with dirty flag', () => {
    const el = makeMockElement()
    let detail = null
    el.addEventListener('nubi:dirty', (e) => { detail = e.detail })

    emitDirty(el, { dirty: true })

    assert.ok(detail)
    assert.equal(detail.dirty, true)
  })

  it('emitError fires nubi:error with message', () => {
    const el = makeMockElement()
    let detail = null
    el.addEventListener('nubi:error', (e) => { detail = e.detail })

    emitError(el, { message: 'Network timeout', code: 'ERR_TIMEOUT' })

    assert.ok(detail)
    assert.equal(detail.message, 'Network timeout')
    assert.equal(detail.code, 'ERR_TIMEOUT')
  })

  it('emitError fires without code field', () => {
    const el = makeMockElement()
    let detail = null
    el.addEventListener('nubi:error', (e) => { detail = e.detail })

    emitError(el, { message: 'oops' })

    assert.ok(detail)
    assert.equal(detail.message, 'oops')
    assert.equal(detail.code, undefined)
  })

  it('emitSelect fires nubi:select with column, value, row', () => {
    const el = makeMockElement()
    let detail = null
    el.addEventListener('nubi:select', (e) => { detail = e.detail })

    emitSelect(el, { column: 'region', value: 'North', row: { region: 'North', revenue: 42 } })

    assert.ok(detail)
    assert.equal(detail.column, 'region')
    assert.equal(detail.value, 'North')
    assert.deepEqual(detail.row, { region: 'North', revenue: 42 })
  })

  it('emitSelect event has composed:true (escapes shadow DOM)', () => {
    const el = makeMockElement()
    let evt = null
    el.addEventListener('nubi:select', (e) => { evt = e })

    emitSelect(el, { column: 'x', value: 1 })

    assert.ok(evt)
    assert.equal(evt.composed, true)
    assert.equal(evt.bubbles,  true)
  })
})

// ---------------------------------------------------------------------------
// Attribute coercion (defineNubiElement internals — tested inline)
// Mirrors the coerce() function in react-wc.js exactly.
// ---------------------------------------------------------------------------

describe('defineNubiElement attribute coercion', () => {
  function coerceAttr(value, type) {
    if (value === null) {
      if (type === 'boolean') return false
      return null
    }
    switch (type) {
      case 'boolean': return value !== 'false' && value !== '0'
      case 'number':  return parseFloat(value)
      case 'json':    try { return JSON.parse(value) } catch { return null }
      default:        return value
    }
  }

  it('maps string attribute to string prop', () => {
    assert.equal(coerceAttr('hello', 'string'), 'hello')
  })

  it('maps number attribute to number prop', () => {
    assert.equal(coerceAttr('42', 'number'), 42)
    assert.equal(coerceAttr('-5.5', 'number'), -5.5)
  })

  it('maps boolean attribute — true cases', () => {
    assert.equal(coerceAttr('true', 'boolean'),  true)
    assert.equal(coerceAttr('', 'boolean'),       true)
    assert.equal(coerceAttr('1', 'boolean'),      true)
  })

  it('maps boolean attribute — false cases', () => {
    assert.equal(coerceAttr('false', 'boolean'), false)
    assert.equal(coerceAttr('0', 'boolean'),     false)
  })

  it('maps absent boolean attribute (null) to false', () => {
    assert.equal(coerceAttr(null, 'boolean'), false)
  })

  it('maps json attribute to parsed object', () => {
    assert.deepEqual(coerceAttr('{"a":1}', 'json'), { a: 1 })
    assert.deepEqual(coerceAttr('[1,2,3]', 'json'), [1, 2, 3])
  })

  it('maps invalid json to null', () => {
    assert.equal(coerceAttr('{bad json}', 'json'), null)
  })

  it('maps absent attribute (null) to null for string/number/json', () => {
    assert.equal(coerceAttr(null, 'string'), null)
    assert.equal(coerceAttr(null, 'number'), null)
    assert.equal(coerceAttr(null, 'json'),   null)
  })
})

// ---------------------------------------------------------------------------
// decodeScopes + hasScope (nubi-context.js)
// ---------------------------------------------------------------------------

describe('decodeScopes', () => {
  function makeToken(scopes = []) {
    const header  = btoa(JSON.stringify({ alg: 'none', typ: 'JWT' }))
      .replace(/\+/g, '-').replace(/\//g, '_').replace(/=/g, '')
    const payload = btoa(JSON.stringify({
      sub: 'test', iat: 1000, exp: 9999, scope: scopes.join(' '),
    })).replace(/\+/g, '-').replace(/\//g, '_').replace(/=/g, '')
    return `${header}.${payload}.fakesig`
  }

  function makeTokenRaw(claims) {
    const header  = btoa(JSON.stringify({ alg: 'none', typ: 'JWT' }))
      .replace(/\+/g, '-').replace(/\//g, '_').replace(/=/g, '')
    const payload = btoa(JSON.stringify({ sub: 'test', iat: 1000, exp: 9999, ...claims }))
      .replace(/\+/g, '-').replace(/\//g, '_').replace(/=/g, '')
    return `${header}.${payload}.fakesig`
  }

  it('returns [] for null/undefined token', () => {
    assert.deepEqual(decodeScopes(null),      [])
    assert.deepEqual(decodeScopes(undefined), [])
    assert.deepEqual(decodeScopes(''),        [])
  })

  it('returns [] for malformed token', () => {
    assert.deepEqual(decodeScopes('not.a.valid.jwt'), [])
  })

  it('decodes space-delimited scope string (RFC 6749 format)', () => {
    const tok = makeToken(['read:*', 'author:sql', 'author:metric'])
    const scopes = decodeScopes(tok)
    assert.deepEqual(scopes, ['read:*', 'author:sql', 'author:metric'])
  })

  it('decodes space-delimited scopes with multiple tokens', () => {
    const tok = makeToken(['read', 'write', 'author:sql'])
    assert.deepEqual(decodeScopes(tok), ['read', 'write', 'author:sql'])
  })

  it('decodes single scope', () => {
    const tok = makeToken(['author:metric'])
    assert.deepEqual(decodeScopes(tok), ['author:metric'])
  })

  it('returns [] when scope claim is empty string', () => {
    const tok = makeToken([])
    assert.deepEqual(decodeScopes(tok), [])
  })

  it('decodes scope claim that is an array (not a string)', () => {
    const tok = makeTokenRaw({ scope: ['read:*', 'author:sql'] })
    const scopes = decodeScopes(tok)
    assert.deepEqual(scopes, ['read:*', 'author:sql'])
  })

  it('decodes scopes claim (plural) as fallback when scope absent', () => {
    const tok = makeTokenRaw({ scopes: ['read:*', 'author:metric'] })
    const scopes = decodeScopes(tok)
    assert.deepEqual(scopes, ['read:*', 'author:metric'])
  })

  it('decodes scp claim as final fallback', () => {
    const tok = makeTokenRaw({ scp: 'read:* author:sql' })
    const scopes = decodeScopes(tok)
    assert.ok(scopes.includes('read:*'),    'should include read:*')
    assert.ok(scopes.includes('author:sql'), 'should include author:sql')
  })

  it('decodes scp claim as array', () => {
    const tok = makeTokenRaw({ scp: ['author:metric', 'read:*'] })
    const scopes = decodeScopes(tok)
    assert.ok(scopes.includes('author:metric'), 'should include author:metric')
    assert.ok(scopes.includes('read:*'),        'should include read:*')
  })

  it('scope takes precedence over scopes and scp', () => {
    const tok = makeTokenRaw({ scope: 'author:sql', scopes: ['author:metric'], scp: 'read:*' })
    const scopes = decodeScopes(tok)
    assert.deepEqual(scopes, ['author:sql'])
  })

  it('author:metric space-delimited token is not read-only (hasScope check)', () => {
    const tok = makeTokenRaw({ scope: 'read:* author:metric' })
    const scopes = decodeScopes(tok)
    assert.ok(scopes.includes('author:metric'), 'author:metric must be in parsed scopes')
    // Simulate the read-only check: forceRO || (!hasSql && !hasMetric)
    const hasSql    = hasScope(scopes, 'author:sql')
    const hasMetric = hasScope(scopes, 'author:metric')
    const readOnly  = !hasSql && !hasMetric
    assert.equal(readOnly, false, 'author:metric token must NOT be read-only')
  })
})

describe('hasScope', () => {
  it('returns true when scope is present', () => {
    assert.equal(hasScope(['read', 'author:sql'], 'author:sql'), true)
  })

  it('returns false when scope is absent', () => {
    assert.equal(hasScope(['read'], 'author:sql'), false)
  })

  it('wildcard * matches everything (super-admin sentinel)', () => {
    assert.equal(hasScope(['*'], 'author:sql'),        true)
    assert.equal(hasScope(['*'], 'author:metric'),     true)
    assert.equal(hasScope(['*'], 'read'),              true)
    assert.equal(hasScope(['*'], 'read:dashboard:x'),  true)
  })

  it('namespace wildcard author:* matches author:sql and author:metric', () => {
    assert.equal(hasScope(['author:*'], 'author:sql'),    true)
    assert.equal(hasScope(['author:*'], 'author:metric'), true)
  })

  it('namespace wildcard does NOT match cross-namespace', () => {
    assert.equal(hasScope(['author:*'], 'read'), false)
  })

  it('read:* matches read:anything and read:a:b (multi-segment)', () => {
    // Matches backend: prefix = 'read:' → required.startsWith('read:')
    assert.equal(hasScope(['read:*'], 'read:dashboard'),     true)
    assert.equal(hasScope(['read:*'], 'read:dashboard:abc'), true)
    assert.equal(hasScope(['read:*'], 'read:'),              true)
  })

  it('read:dashboard:* matches read:dashboard:abc but NOT read:other:abc', () => {
    assert.equal(hasScope(['read:dashboard:*'], 'read:dashboard:abc'),  true)
    assert.equal(hasScope(['read:dashboard:*'], 'read:other:abc'),      false)
    assert.equal(hasScope(['read:dashboard:*'], 'read:dashboard'),      false)
  })

  it('empty scopes array returns false', () => {
    assert.equal(hasScope([], 'author:sql'), false)
  })

  it('author:metric token resolves as NOT read-only', () => {
    const scopes = ['read:*', 'author:metric']
    const hasSql    = hasScope(scopes, 'author:sql')
    const hasMetric = hasScope(scopes, 'author:metric')
    assert.equal(hasMetric, true,  'author:metric must be present')
    assert.equal(!hasSql && !hasMetric, false, 'must not be read-only')
  })
})

// ---------------------------------------------------------------------------
// Cross-filter bus (inline implementation matching nubi-context.js semantics)
// ---------------------------------------------------------------------------

describe('crossFilterBus', () => {
  function createCrossFilterBus() {
    const _filters   = new Map()
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
    bus.publish({ filterId: 'year',  values: [2024] })
    bus.publish({ filterId: 'month', values: [6, 7] })

    const f = bus.getFilters()
    assert.deepEqual(f.get('year'),  [2024])
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
