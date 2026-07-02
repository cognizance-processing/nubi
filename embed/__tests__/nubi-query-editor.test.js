/**
 * nubi-query-editor.test.js
 *
 * Tests for <nubi-query-editor> scope gating, DOM lifecycle, and event emission.
 *
 * Monaco is mocked (dynamic import) so tests run in jsdom without a bundler.
 */

import { describe, it, expect, afterEach, vi, beforeAll } from 'vitest'
import { makeToken, nextTick, mount } from './helpers.js'

// ---------------------------------------------------------------------------
// Mock Monaco (the dynamic import in nubi-query-editor.js)
// ---------------------------------------------------------------------------

// Track editor instances for assertion
const _mockEditorInstances = []

vi.mock('monaco-editor', () => {
  const create = vi.fn((_container, _options) => {
    const handlers = { change: null }
    const instance = {
      getValue:    vi.fn(() => ''),
      setValue:    vi.fn(),
      dispose:     vi.fn(),
      layout:      vi.fn(),
      updateOptions: vi.fn(),
      addCommand:  vi.fn(),
      onDidChangeModelContent: vi.fn(cb => {
        handlers.change = cb
        return { dispose: vi.fn() }
      }),
      _triggerChange: () => handlers.change?.(),
    }
    _mockEditorInstances.push(instance)
    return instance
  })
  return {
    editor: {
      create,
      defineTheme:    vi.fn(),
      setModelMarkers: vi.fn(),
    },
    KeyMod:  { CtrlCmd: 2048 },
    KeyCode: { Enter: 3 },
  }
})

// ---------------------------------------------------------------------------
// Register the custom element once
// ---------------------------------------------------------------------------

beforeAll(async () => {
  // Import after mock is set up so the dynamic import is intercepted
  const { NubiQueryEditor } = await import('../widgets/nubi-query-editor.js')
  if (!customElements.get('nubi-query-editor')) {
    customElements.define('nubi-query-editor', NubiQueryEditor)
  }
})

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function make(attrs = {}) {
  const el = document.createElement('nubi-query-editor')
  for (const [k, v] of Object.entries(attrs)) {
    if (v === true) el.setAttribute(k, '')
    else el.setAttribute(k, v)
  }
  return el
}

// ---------------------------------------------------------------------------
// Scope-gating tests
// ---------------------------------------------------------------------------

describe('NubiQueryEditor — scope gating', () => {
  let el

  afterEach(() => {
    el?.remove()
    _mockEditorInstances.length = 0
  })

  it('shows READ-ONLY badge when token has no authoring scopes', async () => {
    el = make({ token: makeToken(['read:*']) })
    mount(el)
    await nextTick(2)

    const badge = el.shadowRoot.querySelector('.scope-badge')
    expect(badge).toBeTruthy()
    expect(badge.textContent).toMatch(/read.only/i)
    expect(badge.classList.contains('readonly')).toBe(true)
  })

  it('shows SQL badge when token has author:sql scope', async () => {
    el = make({ token: makeToken(['read:*', 'author:sql']) })
    mount(el)
    await nextTick(2)

    const badge = el.shadowRoot.querySelector('.scope-badge')
    expect(badge.textContent).toMatch(/sql/i)
    expect(badge.classList.contains('sql')).toBe(true)
  })

  it('shows METRIC badge when token has only author:metric scope', async () => {
    el = make({ token: makeToken(['read:*', 'author:metric']) })
    mount(el)
    await nextTick(2)

    const badge = el.shadowRoot.querySelector('.scope-badge')
    expect(badge.textContent).toMatch(/metric/i)
    expect(badge.classList.contains('metric')).toBe(true)
  })

  it('shows SQL + METRIC badge when token has both scopes', async () => {
    el = make({ token: makeToken(['read:*', 'author:sql', 'author:metric']) })
    mount(el)
    await nextTick(2)

    const badge = el.shadowRoot.querySelector('.scope-badge')
    expect(badge.textContent).toMatch(/sql/i)
    expect(badge.textContent).toMatch(/metric/i)
    expect(badge.classList.contains('both')).toBe(true)
  })

  it('hides Run and Save buttons when read-only (no authoring scopes)', async () => {
    el = make({ token: makeToken(['read:*']) })
    mount(el)
    await nextTick(2)

    const btnRun  = el.shadowRoot.querySelector('.btn-run')
    const btnSave = el.shadowRoot.querySelector('.btn-save')
    // Either hidden (display:none) or disabled
    const runHidden  = !btnRun  || btnRun.style.display  === 'none' || btnRun.disabled
    const saveHidden = !btnSave || btnSave.style.display === 'none' || btnSave.disabled
    expect(runHidden).toBe(true)
    expect(saveHidden).toBe(true)
  })

  it('shows Run and Save buttons when author:sql scope present', async () => {
    el = make({ token: makeToken(['read:*', 'author:sql']) })
    mount(el)
    await nextTick(2)

    const btnRun  = el.shadowRoot.querySelector('.btn-run')
    const btnSave = el.shadowRoot.querySelector('.btn-save')
    expect(btnRun?.style.display).not.toBe('none')
    expect(btnSave?.style.display).not.toBe('none')
    expect(btnRun?.disabled).toBe(false)
    expect(btnSave?.disabled).toBe(false)
  })

  it('forces read-only when read-only attribute is set regardless of scope', async () => {
    el = make({ token: makeToken(['read:*', 'author:sql']), 'read-only': true })
    mount(el)
    await nextTick(2)

    const badge = el.shadowRoot.querySelector('.scope-badge')
    expect(badge.classList.contains('readonly')).toBe(true)
    const btnRun = el.shadowRoot.querySelector('.btn-run')
    const isHiddenOrDisabled = !btnRun || btnRun.style.display === 'none' || btnRun.disabled
    expect(isHiddenOrDisabled).toBe(true)
  })

  it('treats wildcard scope (*) as super-admin with all capabilities', async () => {
    el = make({ token: makeToken(['*']) })
    mount(el)
    await nextTick(2)

    const badge = el.shadowRoot.querySelector('.scope-badge')
    // Should have SQL at minimum (wildcard covers all)
    expect(badge.classList.contains('readonly')).toBe(false)
    const btnRun = el.shadowRoot.querySelector('.btn-run')
    expect(btnRun?.disabled).toBe(false)
  })

  it('shows SQL mode tab when author:sql present', async () => {
    el = make({ token: makeToken(['read:*', 'author:sql']) })
    mount(el)
    await nextTick(2)

    const tabs = el.shadowRoot.querySelectorAll('.mode-tab')
    const tabTexts = [...tabs].map(t => t.textContent.trim().toLowerCase())
    expect(tabTexts).toContain('sql')
  })

  it('SQL mode tab is absent when only author:metric present', async () => {
    el = make({ token: makeToken(['read:*', 'author:metric']) })
    mount(el)
    await nextTick(2)

    const tabs = el.shadowRoot.querySelectorAll('.mode-tab')
    const tabTexts = [...tabs].map(t => t.textContent.trim().toLowerCase())
    expect(tabTexts).not.toContain('sql')
    expect(tabTexts).toContain('metric')
  })
})

// ---------------------------------------------------------------------------
// Light-DOM wrapper lifecycle
// ---------------------------------------------------------------------------

describe('NubiQueryEditor — light-DOM wrapper lifecycle', () => {
  it('creates a light-DOM wrapper div in document.body on connect', async () => {
    const countBefore = document.body.querySelectorAll('[id^="nubi-qe-"]').length
    const el = make({ token: makeToken([]) })
    mount(el)
    await nextTick(1)

    const countAfter = document.body.querySelectorAll('[id^="nubi-qe-"]').length
    expect(countAfter).toBe(countBefore + 1)
    el.remove()
  })

  it('removes the light-DOM wrapper on disconnect', async () => {
    const el = make({ token: makeToken([]) })
    mount(el)
    await nextTick(1)

    // Capture the wrapper ID before removal
    const wrappers = document.body.querySelectorAll('[id^="nubi-qe-"]')
    expect(wrappers.length).toBeGreaterThan(0)
    const wrapperId = wrappers[wrappers.length - 1].id

    el.remove()
    await nextTick(1)

    expect(document.getElementById(wrapperId)).toBeNull()
  })

  it('two elements create two separate light-DOM wrappers', async () => {
    const el1 = make({ token: makeToken([]) })
    const el2 = make({ token: makeToken([]) })
    mount(el1)
    mount(el2)
    await nextTick(1)

    const wrappers = document.body.querySelectorAll('[id^="nubi-qe-"]')
    expect(wrappers.length).toBeGreaterThanOrEqual(2)

    el1.remove()
    el2.remove()
  })
})

// ---------------------------------------------------------------------------
// Event emission
// ---------------------------------------------------------------------------

describe('NubiQueryEditor — event emission', () => {
  afterEach(() => {
    _mockEditorInstances.length = 0
  })

  it('emits nubi:run on Run button click', async () => {
    const el = make({ token: makeToken(['read:*', 'author:sql']), backend: 'http://localhost:8000' })

    // Stub fetch to avoid real network calls
    const fetchStub = vi.fn().mockResolvedValue({
      ok: true,
      arrayBuffer: async () => new ArrayBuffer(0),
      headers: { get: () => null },
    })
    const origFetch = globalThis.fetch
    globalThis.fetch = fetchStub

    const events = []
    document.addEventListener('nubi:run', e => events.push(e))

    mount(el)
    await nextTick(3)

    const btnRun = el.shadowRoot.querySelector('.btn-run')
    btnRun?.click()
    await nextTick(3)

    expect(events.length).toBeGreaterThan(0)

    document.removeEventListener('nubi:run', e => events.push(e))
    el.remove()
    globalThis.fetch = origFetch
  })

  it('emits nubi:error when fetch fails', async () => {
    const el = make({ token: makeToken(['read:*', 'author:sql']), backend: 'http://localhost:8000' })

    const fetchStub = vi.fn().mockResolvedValue({ ok: false, status: 403 })
    const origFetch = globalThis.fetch
    globalThis.fetch = fetchStub

    const errors = []
    document.addEventListener('nubi:error', e => errors.push(e))

    mount(el)
    await nextTick(3)

    const btnRun = el.shadowRoot.querySelector('.btn-run')
    btnRun?.click()
    await nextTick(3)

    expect(errors.length).toBeGreaterThan(0)

    document.removeEventListener('nubi:error', e => errors.push(e))
    el.remove()
    globalThis.fetch = origFetch
  })

  it('emits nubi:save on Save button click', async () => {
    const el = make({ token: makeToken(['read:*', 'author:sql']) })
    const saves = []
    document.addEventListener('nubi:save', e => saves.push(e))

    mount(el)
    await nextTick(3)

    const btnSave = el.shadowRoot.querySelector('.btn-save')
    btnSave?.click()
    await nextTick(1)

    expect(saves.length).toBeGreaterThan(0)

    document.removeEventListener('nubi:save', e => saves.push(e))
    el.remove()
  })

  it('emits nubi:dirty when Save clears dirty flag', async () => {
    const el = make({ token: makeToken(['read:*', 'author:sql']) })
    const dirtyEvents = []
    document.addEventListener('nubi:dirty', e => dirtyEvents.push(e))

    mount(el)
    await nextTick(3)

    const btnSave = el.shadowRoot.querySelector('.btn-save')
    btnSave?.click()
    await nextTick(1)

    // After save, dirty should be false
    const lastDirty = dirtyEvents[dirtyEvents.length - 1]
    expect(lastDirty?.detail?.dirty).toBe(false)

    document.removeEventListener('nubi:dirty', e => dirtyEvents.push(e))
    el.remove()
  })
})

// ---------------------------------------------------------------------------
// Mounting / rendering basics
// ---------------------------------------------------------------------------

describe('NubiQueryEditor — rendering', () => {
  it('mounts in a bare page and renders shadow root', () => {
    const el = make({})
    mount(el)
    expect(el.shadowRoot).toBeTruthy()
    expect(el.shadowRoot.querySelector('.nubi-qe-wrap')).toBeTruthy()
    el.remove()
  })

  it('renders toolbar with expected elements', async () => {
    const el = make({ token: makeToken(['read:*', 'author:sql']) })
    mount(el)
    await nextTick(2)

    const toolbar = el.shadowRoot.querySelector('.nubi-qe-toolbar')
    expect(toolbar).toBeTruthy()
    expect(el.shadowRoot.querySelector('.scope-badge')).toBeTruthy()
    el.remove()
  })

  it('renders status bar', () => {
    const el = make({})
    mount(el)
    expect(el.shadowRoot.querySelector('.nubi-qe-status')).toBeTruthy()
    el.remove()
  })
})
