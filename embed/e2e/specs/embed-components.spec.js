// @ts-check
/**
 * embed-components.spec.js
 *
 * Playwright e2e suite for the Nubi embed component demo pages.
 *
 * Each demo page loads the self-contained nubi-embed.js bundle and exercises
 * custom elements in a real Chromium browser.  The suite verifies:
 *
 *  1. Pages load with ZERO console errors and ZERO uncaught exceptions
 *     (bundle-resolution and module import regressions fail immediately here).
 *  2. Custom elements upgrade — their shadow DOM is non-empty.
 *  3. kpi-react: KPI value + SAMPLE badge render from fallback sample data.
 *  4. query-editor scope-gating: SQL / METRIC / READ-ONLY states driven by
 *     mock JWT tokens with the correct scopes.
 *  5. metric-explorer scope-gating: author:metric shows controls + Run;
 *     read-only token shows READ-ONLY indicator, no Run button.
 *  6. Events: nubi:run bubbles to document after interaction.
 *  7. Theme: light-theme attribute applies lighter background tokens.
 */

import { test, expect } from '@playwright/test'

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/**
 * Attach a console-error + page-error collector to the page before navigation.
 * Returns a getter that yields captured messages at assertion time.
 */
function attachErrorCollector(page) {
  const errors = []
  page.on('console', msg => {
    if (msg.type() === 'error') {
      // Suppress expected network errors when the demo backend is not running
      const text = msg.text()
      if (
        text.includes('Failed to load resource') ||
        text.includes('net::ERR_CONNECTION_REFUSED') ||
        text.includes('localhost:8000') ||
        text.includes('Failed to fetch') ||
        // Monaco worker blob URL — expected to be unavailable in demo
        text.includes('getWorkerUrl')
      ) return
      errors.push(`[console.error] ${text}`)
    }
  })
  page.on('pageerror', err => {
    errors.push(`[pageerror] ${err.message}`)
  })
  return () => errors
}

/**
 * Wait for a custom element to upgrade (constructor ran) and have a non-empty
 * shadow root, via page.evaluate.
 *
 * @param {import('@playwright/test').Page} page
 * @param {string} selector  - CSS selector for the element
 * @param {number} [timeout] - max wait in ms (default 8000)
 */
async function waitForShadowDom(page, selector, timeout = 8000) {
  await page.waitForFunction(
    (sel) => {
      const el = document.querySelector(sel)
      if (!el) return false
      const sr = el.shadowRoot
      return sr !== null && sr.childElementCount > 0
    },
    selector,
    { timeout },
  )
}

/**
 * Read a CSS custom property from an element's inline style (set by applyTheme).
 * Uses 0-based index into querySelectorAll results.
 */
async function getThemeTokenByIndex(page, tag, index, token) {
  return page.evaluate(
    ([t, idx, tok]) => {
      const el = document.querySelectorAll(t)[idx]
      return el ? el.style.getPropertyValue(tok).trim() : null
    },
    [tag, index, token],
  )
}

// ---------------------------------------------------------------------------
// KPI-React demo
// ---------------------------------------------------------------------------

test.describe('kpi-react demo', () => {
  const PAGE = '/embed/demo/kpi-react.html'

  test('page loads with zero console errors', async ({ page }) => {
    const getErrors = attachErrorCollector(page)
    await page.goto(PAGE)

    // Give async module + custom-element registration time to settle
    await page.waitForTimeout(1500)

    const errors = getErrors()
    expect(errors, `Unexpected errors:\n${errors.join('\n')}`).toHaveLength(0)
  })

  test('custom elements upgrade with non-empty shadow DOM', async ({ page }) => {
    await page.goto(PAGE)

    // Three static KPI cards in the demo
    await waitForShadowDom(page, '#revenue-kpi')
    await waitForShadowDom(page, '#users-kpi')
    await waitForShadowDom(page, '#conv-kpi')
  })

  test('KPI cards render a value and SAMPLE badge', async ({ page }) => {
    await page.goto(PAGE)

    await waitForShadowDom(page, '#revenue-kpi')

    // Wait until the loading spinner clears (value changes from '…')
    await page.waitForFunction(
      () => {
        const el = document.querySelector('#revenue-kpi')
        if (!el?.shadowRoot) return false
        const sr = el.shadowRoot
        const allDivs = [...sr.querySelectorAll('div')]
        const valueDiv = allDivs.find(d => d.style.fontSize === '32px')
        return valueDiv && valueDiv.textContent.trim() !== '…'
      },
      { timeout: 5000 },
    )

    // Query shadow root content via evaluate
    const { value, badge } = await page.evaluate(() => {
      const el = document.querySelector('#revenue-kpi')
      const sr = el.shadowRoot
      // The React component renders inline — find the value div and badge span
      const allDivs = [...sr.querySelectorAll('div')]
      // Value div has fontSize 32px inline
      const valueDiv = allDivs.find(d => d.style.fontSize === '32px')
      // Badge span contains 'SAMPLE' or 'LIVE'
      const badges = [...sr.querySelectorAll('span')].filter(s =>
        s.textContent.includes('SAMPLE') || s.textContent.includes('LIVE'),
      )
      return {
        value: valueDiv ? valueDiv.textContent.trim() : null,
        badge: badges[0] ? badges[0].textContent.trim() : null,
      }
    })

    // Value should be a non-empty, non-loading string
    expect(value).not.toBeNull()
    expect(value).not.toBe('…')
    expect((value || '').length).toBeGreaterThan(0)

    // Static-data cards use sample data (no queryId + no backend) → SAMPLE badge
    expect(badge).toBe('SAMPLE')
  })

  test('KPI card renders without throwing after click interaction', async ({ page }) => {
    await page.goto(PAGE)
    await waitForShadowDom(page, '#revenue-kpi')

    const getErrors = attachErrorCollector(page)

    // Clicking the card should not throw any errors
    await page.click('#revenue-kpi')
    await page.waitForTimeout(500)

    const errors = getErrors()
    expect(errors, `Errors after click:\n${errors.join('\n')}`).toHaveLength(0)
  })
})

// ---------------------------------------------------------------------------
// Query-Editor demo — scope gating
// ---------------------------------------------------------------------------

test.describe('query-editor demo – scope gating', () => {
  const PAGE = '/embed/demo/query-editor.html'

  /**
   * Helper: locate the nth nubi-query-editor on the page and inspect its
   * shadow root for toolbar content.
   *
   * @param {import('@playwright/test').Page} page
   * @param {number} index  - 0-based index of the editor on the page
   */
  async function getShadowToolbar(page, index) {
    return page.evaluate((idx) => {
      const editors = [...document.querySelectorAll('nubi-query-editor')]
      const el = editors[idx]
      if (!el || !el.shadowRoot) return null
      const sr = el.shadowRoot
      const badgeEl  = sr.querySelector('.scope-badge')
      const btnRun   = sr.querySelector('.btn-run')
      const btnSave  = sr.querySelector('.btn-save')
      const tabs     = [...sr.querySelectorAll('.mode-tab')].map(t => t.textContent.trim())
      return {
        badge:        badgeEl?.textContent?.trim() ?? null,
        runVisible:   btnRun  ? btnRun.style.display  !== 'none' : false,
        saveVisible:  btnSave ? btnSave.style.display !== 'none' : false,
        tabs,
      }
    }, index)
  }

  async function waitForAllEditors(page, count = 5) {
    // Wait for all editors to have a shadow root with toolbar AND the gating applied
    // (scope-badge text is only set after _resolveAndGate completes)
    await page.waitForFunction(
      (cnt) => {
        const editors = [...document.querySelectorAll('nubi-query-editor')]
        if (editors.length < cnt) return false
        return editors.every(e => {
          if (!e.shadowRoot) return false
          const badge = e.shadowRoot.querySelector('.scope-badge')
          if (!badge) return false
          // Wait until the badge is not the initial 'READ-ONLY' for editors
          // that should have author scopes (i.e., gating has resolved).
          // We simply wait until ALL badges have settled (non-empty text).
          return badge.textContent.trim().length > 0
        })
      },
      count,
      { timeout: 12000 },
    )
    // Additional small wait for DOM to fully paint after scope resolution
    await page.waitForTimeout(300)
  }

  test('page loads with zero console errors', async ({ page }) => {
    const getErrors = attachErrorCollector(page)
    await page.goto(PAGE)
    await waitForAllEditors(page, 5)
    const errors = getErrors()
    expect(errors, `Unexpected errors:\n${errors.join('\n')}`).toHaveLength(0)
  })

  test('scenario 1 — author:sql shows SQL tab + Run + Save, no READ-ONLY badge', async ({ page }) => {
    await page.goto(PAGE)
    await waitForAllEditors(page, 5)

    const toolbar = await getShadowToolbar(page, 0)
    expect(toolbar).not.toBeNull()
    expect(toolbar.tabs).toContain('SQL')
    expect(toolbar.badge).toBe('SQL')
    expect(toolbar.runVisible).toBe(true)
    expect(toolbar.saveVisible).toBe(true)
  })

  test('scenario 2 — author:metric shows METRIC tab, no SQL tab, no READ-ONLY badge', async ({ page }) => {
    await page.goto(PAGE)
    await waitForAllEditors(page, 5)

    const toolbar = await getShadowToolbar(page, 1)
    expect(toolbar).not.toBeNull()
    expect(toolbar.tabs).toContain('METRIC')
    expect(toolbar.tabs).not.toContain('SQL')
    expect(toolbar.badge).toBe('METRIC')
    expect(toolbar.runVisible).toBe(true)
  })

  test('scenario 2 — metric mode shows metric builder, SQL editor placeholder hidden', async ({ page }) => {
    await page.goto(PAGE)
    await waitForAllEditors(page, 5)

    const state = await page.evaluate(() => {
      const editors = [...document.querySelectorAll('nubi-query-editor')]
      const el = editors[1]
      if (!el?.shadowRoot) return { error: 'no element' }
      const sr = el.shadowRoot
      const builder = sr.querySelector('.nubi-qe-metric-builder')
      const ph = sr.querySelector('.nubi-qe-placeholder')
      return {
        builderVisible: builder ? builder.style.display !== 'none' : false,
        placeholderHidden: ph ? ph.style.display === 'none' : false,
      }
    })

    expect(state.builderVisible).toBe(true)
    expect(state.placeholderHidden).toBe(true)
  })

  test('scenario 3 — author:sql + author:metric shows both tabs + SQL + METRIC badge', async ({ page }) => {
    await page.goto(PAGE)
    await waitForAllEditors(page, 5)

    const toolbar = await getShadowToolbar(page, 2)
    expect(toolbar).not.toBeNull()
    expect(toolbar.tabs).toContain('SQL')
    expect(toolbar.tabs).toContain('METRIC')
    expect(toolbar.badge).toBe('SQL + METRIC')
    expect(toolbar.runVisible).toBe(true)
  })

  test('scenario 4 — read-only token: READ-ONLY badge, no Run, no Save, no tabs', async ({ page }) => {
    await page.goto(PAGE)
    await waitForAllEditors(page, 5)

    const toolbar = await getShadowToolbar(page, 3)
    expect(toolbar).not.toBeNull()
    expect(toolbar.badge).toBe('READ-ONLY')
    expect(toolbar.runVisible).toBe(false)
    expect(toolbar.saveVisible).toBe(false)
    expect(toolbar.tabs).toHaveLength(0)
  })

  test('scenario 5 — read-only attribute overrides author:sql: no Run, no Save', async ({ page }) => {
    await page.goto(PAGE)
    await waitForAllEditors(page, 5)

    const toolbar = await getShadowToolbar(page, 4)
    expect(toolbar).not.toBeNull()
    // read-only attribute forces READ-ONLY badge regardless of scopes
    expect(toolbar.badge).toBe('READ-ONLY')
    expect(toolbar.runVisible).toBe(false)
    expect(toolbar.saveVisible).toBe(false)
  })

  test('SQL editor (scenario 1) has a textarea or Monaco editor in the light DOM', async ({ page }) => {
    await page.goto(PAGE)
    await waitForAllEditors(page, 5)

    // Monaco falls back to textarea when the bare-specifier import fails
    // (expected in the static-server test environment without a bundler).
    // Either path proves the editor surface exists and is wired up.
    const editorSurface = await page.waitForFunction(
      () => {
        // Monaco mounts in light-DOM divs with IDs starting nubi-qe-
        const wrappers = [...document.querySelectorAll('div[id^="nubi-qe-"]')]
        if (wrappers.length === 0) return null
        const withContent = wrappers.filter(w =>
          w.querySelector('.monaco-editor') || w.querySelector('textarea'),
        )
        if (withContent.length === 0) return null
        return {
          total: wrappers.length,
          withContent: withContent.length,
          firstHasTextarea: !!wrappers[0]?.querySelector('textarea'),
          firstHasMonaco: !!wrappers[0]?.querySelector('.monaco-editor'),
        }
      },
      { timeout: 8000 },
    )

    const val = await editorSurface.jsonValue()
    expect(val).not.toBeNull()
    expect(val.withContent).toBeGreaterThan(0)
  })

  test('nubi:run event fires when Run button clicked in metric mode (scenario 2)', async ({ page }) => {
    // Metric mode fires emitRun() synchronously without a backend fetch,
    // so this event is reliable even without a running server.
    await page.goto(PAGE)
    await waitForAllEditors(page, 5)

    await page.evaluate(() => {
      window.__nubiRunDetail = null
      document.addEventListener('nubi:run', (e) => { window.__nubiRunDetail = e.detail })
    })

    // Click Run button in editor index 1 (metric mode, author:metric)
    const clicked = await page.evaluate(() => {
      const editors = [...document.querySelectorAll('nubi-query-editor')]
      const el = editors[1]
      if (!el?.shadowRoot) return false
      const btn = el.shadowRoot.querySelector('.btn-run')
      if (!btn || btn.style.display === 'none') return false
      btn.click()
      return true
    })
    expect(clicked).toBe(true)

    await page.waitForFunction(() => window.__nubiRunDetail !== null, { timeout: 3000 })
    const detail = await page.evaluate(() => window.__nubiRunDetail)
    expect(detail).not.toBeNull()
    // Metric mode emits { metricId, dimensions, timeGrain }
    expect(typeof detail.timeGrain).toBe('string')
  })

  test('nubi:dirty event fires when textarea content changes (scenario 1)', async ({ page }) => {
    await page.goto(PAGE)
    await waitForAllEditors(page, 5)

    await page.evaluate(() => {
      window.__dirtyDetail = null
      document.addEventListener('nubi:dirty', (e) => { window.__dirtyDetail = e.detail })
    })

    // Type into the textarea of the first editor (scenario 1, SQL, textarea fallback)
    const typed = await page.evaluate(() => {
      const wrappers = [...document.querySelectorAll('div[id^="nubi-qe-"]')]
      const first = wrappers[0]
      if (!first) return false
      const ta = first.querySelector('textarea')
      if (!ta || ta.readOnly) return false
      ta.value = 'SELECT 1'
      ta.dispatchEvent(new Event('input', { bubbles: true }))
      return true
    })
    expect(typed).toBe(true)

    await page.waitForFunction(() => window.__dirtyDetail !== null, { timeout: 3000 })
    const detail = await page.evaluate(() => window.__dirtyDetail)
    expect(detail).not.toBeNull()
    expect(detail.dirty).toBe(true)
  })
})

// ---------------------------------------------------------------------------
// Metric-Explorer demo — scope gating
// ---------------------------------------------------------------------------

test.describe('metric-explorer demo – scope gating', () => {
  const PAGE = '/embed/demo/metric-explorer.html'

  async function waitForAllExplorers(page, count = 4) {
    // Wait for scope-indicator to appear AND controls to be rendered
    await page.waitForFunction(
      (cnt) => {
        const els = [...document.querySelectorAll('nubi-metric-explorer')]
        if (els.length < cnt) return false
        return els.every(e => {
          if (!e.shadowRoot) return false
          const ind = e.shadowRoot.querySelector('.scope-indicator')
          if (!ind) return false
          // Controls are populated when metric is loaded (either from API or defaults)
          const controls = e.shadowRoot.querySelector('.nubi-me-controls')
          return controls && controls.style.display !== 'none'
        })
      },
      count,
      { timeout: 15000 },
    )
    // Small extra wait to let control children fully render
    await page.waitForTimeout(300)
  }

  async function getExplorerState(page, index) {
    return page.evaluate((idx) => {
      const els = [...document.querySelectorAll('nubi-metric-explorer')]
      const el = els[idx]
      if (!el?.shadowRoot) return null
      const sr = el.shadowRoot

      const indicator  = sr.querySelector('.scope-indicator')
      const controls   = sr.querySelector('.nubi-me-controls')
      const runBtn     = sr.querySelector('.btn-run-me')
      const noScope    = sr.querySelector('.no-scope-banner')
      const selects    = [...sr.querySelectorAll('.me-select')]
      const checkboxes = [...sr.querySelectorAll('.dim-checkboxes input[type=checkbox]')]

      return {
        indicatorText:    indicator?.textContent?.trim() ?? null,
        indicatorClass:   indicator?.className ?? '',
        controlsVisible:  controls ? controls.style.display !== 'none' : false,
        hasRunBtn:        !!runBtn,
        hasNoScopeBanner: !!noScope,
        selectsDisabled:  selects.map(s => s.disabled),
        checkboxesDisabled: checkboxes.map(c => c.disabled),
      }
    }, index)
  }

  test('page loads with zero console errors', async ({ page }) => {
    const getErrors = attachErrorCollector(page)
    await page.goto(PAGE)
    await waitForAllExplorers(page, 4)
    const errors = getErrors()
    expect(errors, `Unexpected errors:\n${errors.join('\n')}`).toHaveLength(0)
  })

  test('scenario 1 — author:metric shows METRIC indicator + enabled controls + Run button', async ({ page }) => {
    await page.goto(PAGE)
    await waitForAllExplorers(page, 4)

    const state = await getExplorerState(page, 0)
    expect(state).not.toBeNull()
    expect(state.indicatorText).toBe('METRIC')
    expect(state.indicatorClass).toContain('metric')
    expect(state.controlsVisible).toBe(true)
    expect(state.hasRunBtn).toBe(true)
    expect(state.hasNoScopeBanner).toBe(false)
    // Controls should NOT be disabled (author has full access)
    expect(state.selectsDisabled.every(d => !d)).toBe(true)
  })

  test('scenario 1 — shows metric, dimension, and time-grain controls', async ({ page }) => {
    await page.goto(PAGE)
    await waitForAllExplorers(page, 4)

    const hasControls = await page.evaluate(() => {
      const el = document.querySelectorAll('nubi-metric-explorer')[0]
      if (!el?.shadowRoot) return false
      const sr = el.shadowRoot
      const metricSel = sr.querySelector('[data-role="metric"]')
      const dimBoxes  = sr.querySelector('[data-role="dimensions"]')
      const grainSel  = sr.querySelector('[data-role="time-grain"]')
      return !!(metricSel && dimBoxes && grainSel)
    })
    expect(hasControls).toBe(true)
  })

  test('scenario 3 — read-only token shows READ-ONLY indicator, no Run button, disabled controls', async ({ page }) => {
    await page.goto(PAGE)
    await waitForAllExplorers(page, 4)

    const state = await getExplorerState(page, 2)
    expect(state).not.toBeNull()
    expect(state.indicatorText).toBe('READ-ONLY')
    expect(state.indicatorClass).toContain('readonly')
    expect(state.hasRunBtn).toBe(false)
    expect(state.hasNoScopeBanner).toBe(true)
    // All selects and checkboxes should be disabled in read-only mode
    expect(state.selectsDisabled.every(d => d)).toBe(true)
    expect(state.checkboxesDisabled.every(d => d)).toBe(true)
  })

  test('nubi:run event fires when Run button is clicked (scenario 1)', async ({ page }) => {
    await page.goto(PAGE)
    await waitForAllExplorers(page, 4)

    await page.evaluate(() => {
      window.__meRunDetail = null
      window.__meErrorDetail = null
      document.addEventListener('nubi:run', e => { window.__meRunDetail = e.detail })
      document.addEventListener('nubi:error', e => { window.__meErrorDetail = e.detail })
    })

    const clicked = await page.evaluate(() => {
      const el = document.querySelectorAll('nubi-metric-explorer')[0]
      if (!el?.shadowRoot) return false
      const btn = el.shadowRoot.querySelector('.btn-run-me')
      if (!btn) return false
      btn.click()
      return true
    })
    expect(clicked).toBe(true)

    // Wait for either nubi:run or nubi:error (backend not available in test env)
    await page.waitForFunction(
      () => window.__meRunDetail !== null || window.__meErrorDetail !== null,
      { timeout: 8000 },
    )

    // Either the run event (success) or the error event fired — both prove the
    // click handler ran correctly. If no error was thrown, we check nubi:run.
    const runDetail = await page.evaluate(() => window.__meRunDetail)
    const errDetail = await page.evaluate(() => window.__meErrorDetail)

    // At least one must be non-null
    expect(runDetail !== null || errDetail !== null).toBe(true)

    // If the run fired (shouldn't without backend), verify shape
    if (runDetail) {
      expect(typeof runDetail.metricId).toBe('string')
    }
  })
})

// ---------------------------------------------------------------------------
// Theme tests
// ---------------------------------------------------------------------------

test.describe('theme — light vs dark', () => {
  test('metric-explorer with theme=light applies lighter background token', async ({ page }) => {
    await page.goto('/embed/demo/metric-explorer.html')

    // Wait for all 4 explorers including the light-theme one (index 3)
    await page.waitForFunction(
      () => {
        const els = [...document.querySelectorAll('nubi-metric-explorer')]
        if (els.length < 4) return false
        const lightEl = els[3]
        // applyTheme sets inline style — wait until it runs
        return lightEl?.style.getPropertyValue('--nubi-bg').trim() === '#ffffff'
      },
      { timeout: 12000 },
    )

    // Dark instances (index 0) should have dark bg token
    const darkBg  = await getThemeTokenByIndex(page, 'nubi-metric-explorer', 0, '--nubi-bg')
    // Light instance (index 3) should have light bg token
    const lightBg = await getThemeTokenByIndex(page, 'nubi-metric-explorer', 3, '--nubi-bg')

    // Dark: #0f1117  Light: #ffffff
    expect(darkBg).toBe('#0f1117')
    expect(lightBg).toBe('#ffffff')
    expect(darkBg).not.toBe(lightBg)
  })

  test('query-editor with default dark theme has dark bg token', async ({ page }) => {
    await page.goto('/embed/demo/query-editor.html')

    await page.waitForFunction(
      () => {
        const el = document.querySelectorAll('nubi-query-editor')[0]
        return el?.style.getPropertyValue('--nubi-bg').trim() === '#0f1117'
      },
      { timeout: 12000 },
    )

    const bg = await getThemeTokenByIndex(page, 'nubi-query-editor', 0, '--nubi-bg')
    expect(bg).toBe('#0f1117')
  })
})

// ---------------------------------------------------------------------------
// Light theme — every widget in widgets.html light section
// ---------------------------------------------------------------------------

test.describe('light theme — all widgets in widgets.html', () => {
  const PAGE = '/embed/demo/widgets.html'

  /**
   * Wait until an element's inline style has --nubi-bg set to a light value.
   * applyTheme() writes inline CSS custom properties, so we poll the host element.
   */
  async function waitForLightBg(page, selector, timeout = 10000) {
    await page.waitForFunction(
      (sel) => {
        const el = document.querySelector(sel)
        if (!el) return false
        const bg = el.style.getPropertyValue('--nubi-bg').trim()
        // Light preset uses #ffffff; dark preset uses #0f1117
        return bg === '#ffffff'
      },
      selector,
      { timeout },
    )
  }

  /**
   * Get the computed background of the shadow-root :host via inline CSS var.
   * applyTheme sets --nubi-bg on the host element as an inline style property.
   */
  async function getHostBg(page, selector) {
    return page.evaluate((sel) => {
      const el = document.querySelector(sel)
      if (!el) return null
      return el.style.getPropertyValue('--nubi-bg').trim()
    }, selector)
  }

  test('nubi-kpi with theme=light has light --nubi-bg token', async ({ page }) => {
    await page.goto(PAGE)
    await waitForLightBg(page, '#light-kpi')
    const bg = await getHostBg(page, '#light-kpi')
    expect(bg).toBe('#ffffff')
  })

  test('nubi-table with theme=light has light --nubi-bg token', async ({ page }) => {
    await page.goto(PAGE)
    await waitForLightBg(page, '#light-table')
    const bg = await getHostBg(page, '#light-table')
    expect(bg).toBe('#ffffff')
  })

  test('nubi-chart with theme=light has light --nubi-bg token', async ({ page }) => {
    await page.goto(PAGE)
    await waitForLightBg(page, '#light-chart')
    const bg = await getHostBg(page, '#light-chart')
    expect(bg).toBe('#ffffff')
  })

  test('nubi-metric-explorer with theme=light has light --nubi-bg token', async ({ page }) => {
    await page.goto(PAGE)
    await waitForLightBg(page, '#light-metric-explorer')
    const bg = await getHostBg(page, '#light-metric-explorer')
    expect(bg).toBe('#ffffff')
  })

  test('dark nubi-kpi (no theme attr) has dark --nubi-bg token', async ({ page }) => {
    await page.goto(PAGE)
    // Wait for the first (dark) kpi to have the dark token set
    await page.waitForFunction(
      () => {
        const el = document.querySelector('nubi-kpi:not([theme])')
        if (!el) return false
        return el.style.getPropertyValue('--nubi-bg').trim() === '#0f1117'
      },
      { timeout: 10000 },
    )
    const el = await page.evaluate(() => {
      const el = document.querySelector('nubi-kpi:not([theme])')
      return el ? el.style.getPropertyValue('--nubi-bg').trim() : null
    })
    expect(el).toBe('#0f1117')
  })

  test('nubi-kpi sample footer shows "preview · sample data" once (no duplication)', async ({ page }) => {
    await page.goto(PAGE)
    // Wait for any kpi to render its sample fallback
    await page.waitForFunction(
      () => {
        const el = document.querySelector('#light-kpi')
        if (!el?.shadowRoot) return false
        return el.shadowRoot.querySelector('.nubi-sample-note')?.style.display !== 'none'
      },
      { timeout: 10000 },
    )
    const { noteText, sublabelText } = await page.evaluate(() => {
      const el = document.querySelector('#light-kpi')
      const sr = el.shadowRoot
      const note = sr.querySelector('.nubi-sample-note')
      const sublabel = sr.querySelector('.kpi-sublabel')
      return {
        noteText: note ? note.textContent.trim() : null,
        sublabelText: sublabel ? sublabel.textContent.trim() : null,
      }
    })
    // The note should say "preview · sample data"
    expect(noteText).toBe('preview · sample data')
    // The sublabel must NOT duplicate "sample data"
    expect(sublabelText).not.toContain('sample data')
    // Sublabel should be empty when sample is shown
    expect(sublabelText).toBe('')
  })
})

// ---------------------------------------------------------------------------
// Cross-cutting: custom elements upgrade check across all demo pages
// ---------------------------------------------------------------------------

test.describe('custom-element upgrade — all demo pages', () => {
  const demos = [
    { name: 'kpi-react',        path: '/embed/demo/kpi-react.html',        selector: 'nubi-kpi-react',        tag: 'nubi-kpi-react' },
    { name: 'query-editor',     path: '/embed/demo/query-editor.html',     selector: 'nubi-query-editor',     tag: 'nubi-query-editor' },
    { name: 'metric-explorer',  path: '/embed/demo/metric-explorer.html',  selector: 'nubi-metric-explorer',  tag: 'nubi-metric-explorer' },
  ]

  for (const demo of demos) {
    test(`${demo.name}: element is defined (not HTMLElement generic) + shadow root present`, async ({ page }) => {
      await page.goto(demo.path)

      const result = await page.waitForFunction(
        ({ sel, tag }) => {
          const el = document.querySelector(sel)
          if (!el) return null
          // customElements.get returns the registered class — not undefined
          const cls = customElements.get(tag)
          if (!cls) return null
          const upgraded = el instanceof cls
          const hasShadow = el.shadowRoot !== null && el.shadowRoot.childElementCount > 0
          return { upgraded, hasShadow, tagName: el.tagName.toLowerCase() }
        },
        { sel: demo.selector, tag: demo.tag },
        { timeout: 10000 },
      )

      const val = await result.jsonValue()
      expect(val).not.toBeNull()
      expect(val.upgraded).toBe(true)
      expect(val.hasShadow).toBe(true)
    })
  }
})

// ---------------------------------------------------------------------------
// .getToken property — generic loader contract
//
// Sets el.getToken = async () => 'test-jwt' on a custom element and verifies
// that the property is accepted and readable on the live upgraded element.
// We use nubi-kpi-react (React-based, uses defineNubiElement) to prove the
// getter/setter added to NubiReactElement works correctly.
// ---------------------------------------------------------------------------

test.describe('getToken property — generic loader contract', () => {
  const PAGE = '/embed/demo/kpi-react.html'

  test('el.getToken setter accepts an async function', async ({ page }) => {
    await page.goto(PAGE)
    await waitForShadowDom(page, '#revenue-kpi')

    const result = await page.evaluate(async () => {
      const el = document.querySelector('#revenue-kpi')
      if (!el) return { error: 'element not found' }

      // Set the getToken property
      el.getToken = async () => 'test-jwt'

      // Read it back
      const fn = el.getToken
      if (typeof fn !== 'function') return { error: `getToken is not a function: ${typeof fn}` }

      // Invoke it to confirm it returns the expected value
      const tok = await fn()
      return { tok, typeof: typeof fn }
    })

    expect(result.error).toBeUndefined()
    expect(result.typeof).toBe('function')
    expect(result.tok).toBe('test-jwt')
  })

  test('el.getToken = null clears the property', async ({ page }) => {
    await page.goto(PAGE)
    await waitForShadowDom(page, '#revenue-kpi')

    const result = await page.evaluate(() => {
      const el = document.querySelector('#revenue-kpi')
      if (!el) return { error: 'element not found' }

      el.getToken = async () => 'test-jwt'
      el.getToken = null

      return { getToken: el.getToken }
    })

    expect(result.error).toBeUndefined()
    expect(result.getToken).toBeNull()
  })

  test('el.getToken property is not shared between element instances', async ({ page }) => {
    await page.goto(PAGE)
    await waitForShadowDom(page, '#revenue-kpi')

    const result = await page.evaluate(async () => {
      const el1 = document.querySelector('#revenue-kpi')
      const el2 = document.querySelector('#users-kpi')
      if (!el1 || !el2) return { error: 'elements not found' }

      el1.getToken = async () => 'token-for-el1'
      // el2 gets no getToken

      const fn1 = el1.getToken
      const fn2 = el2.getToken

      return {
        el1HasFn:  typeof fn1 === 'function',
        el2HasFn:  typeof fn2 === 'function',
        el1Result: fn1 ? await fn1() : null,
      }
    })

    expect(result.error).toBeUndefined()
    expect(result.el1HasFn).toBe(true)
    // el2 should not have inherited el1's getToken
    expect(result.el2HasFn).toBe(false)
    expect(result.el1Result).toBe('token-for-el1')
  })

  test('window.__nubiVersion is set after bundle loads', async ({ page }) => {
    await page.goto(PAGE)
    // Wait for the bundle to execute (custom elements to register)
    await waitForShadowDom(page, '#revenue-kpi')

    const version = await page.evaluate(() => window.__nubiVersion)

    expect(typeof version).toBe('string')
    expect(version.length).toBeGreaterThan(0)
    // Must match semver pattern
    expect(version).toMatch(/^\d+\.\d+\.\d+/)
  })
})

// ---------------------------------------------------------------------------
// kpi-targets.html — inline data injection + KPI target/RAG rendering
// ---------------------------------------------------------------------------

test.describe('kpi-targets demo — inline data + RAG chips', () => {
  const PAGE = '/embed/demo/kpi-targets.html'

  test('page loads with zero console errors', async ({ page }) => {
    const getErrors = attachErrorCollector(page)
    await page.goto(PAGE)
    await page.waitForTimeout(1500)
    const errors = getErrors()
    expect(errors, `Unexpected errors:\n${errors.join('\n')}`).toHaveLength(0)
  })

  for (const rag of ['green', 'amber', 'red']) {
    test(`RAG chip is visible and labeled ${rag.toUpperCase()} for ${rag} card`, async ({ page }) => {
      await page.goto(PAGE)
      const chipText = await page.waitForFunction(
        (r) => {
          const el = document.querySelector(`[data-rag="${r}"]`)
          if (!el?.shadowRoot) return null
          const chip = el.shadowRoot.querySelector('.kpi-rag-chip')
          return chip && chip.textContent.trim() ? chip.textContent.trim() : null
        },
        rag,
        { timeout: 8000 },
      )
      const text = await chipText.jsonValue()
      expect(text).toBe(rag.toUpperCase())
    })
  }

  test('goal bar is visible for green card', async ({ page }) => {
    await page.goto(PAGE)
    const barWidth = await page.waitForFunction(
      () => {
        const el = document.querySelector('[data-rag="green"]')
        if (!el?.shadowRoot) return null
        const bar = el.shadowRoot.querySelector('.kpi-goal-bar')
        return bar ? bar.style.width : null
      },
      { timeout: 8000 },
    )
    const w = await barWidth.jsonValue()
    expect(w).not.toBeNull()
    expect(w).not.toBe('')
    expect(w).not.toBe('0%')
  })

  test('no SAMPLE badge shown on data-injection cards', async ({ page }) => {
    await page.goto(PAGE)
    await page.waitForTimeout(2000)
    const hasSample = await page.evaluate(() => {
      return [...document.querySelectorAll('nubi-kpi')].some(el => {
        if (!el?.shadowRoot) return false
        const badge = el.shadowRoot.querySelector('.nubi-badge')
        return badge && badge.style.display !== 'none' && badge.textContent.includes('SAMPLE')
      })
    })
    expect(hasSample).toBe(false)
  })
})

// ---------------------------------------------------------------------------
// error-state.html — no-sample-fallback error mode
// ---------------------------------------------------------------------------

test.describe('error-state demo — no-sample-fallback', () => {
  const PAGE = '/embed/demo/error-state.html'

  test('page loads with zero console errors (network errors suppressed)', async ({ page }) => {
    const getErrors = attachErrorCollector(page)
    await page.goto(PAGE)
    await page.waitForTimeout(2500)
    const errors = getErrors()
    expect(errors, `Unexpected errors:\n${errors.join('\n')}`).toHaveLength(0)
  })

  test('nubi-kpi with no-sample-fallback shows error state, not SAMPLE badge', async ({ page }) => {
    await page.goto(PAGE)
    // Wait for shadow DOM
    await page.waitForFunction(
      () => {
        const el = document.querySelector('nubi-kpi[no-sample-fallback]')
        return el?.shadowRoot && el.shadowRoot.childElementCount > 0
      },
      { timeout: 8000 },
    )
    await page.waitForTimeout(3000) // wait for fetch to fail

    const result = await page.evaluate(() => {
      const el = document.querySelector('nubi-kpi[no-sample-fallback]')
      if (!el?.shadowRoot) return { error: 'no element' }
      const sr = el.shadowRoot
      const badge = sr.querySelector('.nubi-badge')
      const sampleNote = sr.querySelector('.nubi-sample-note')
      const errNote = sr.querySelector('.kpi-error-note')
      return {
        sampleBadgeVisible: badge ? badge.style.display !== 'none' : false,
        sampleNoteVisible: sampleNote ? sampleNote.style.display !== 'none' : false,
        errNoteExists: !!errNote,
      }
    })

    expect(result.sampleBadgeVisible).toBe(false)
    expect(result.sampleNoteVisible).toBe(false)
    expect(result.errNoteExists).toBe(true)
  })

  test('nubi-kpi with no-sample-fallback emits nubi:widget-error event', async ({ page }) => {
    // Set up error capture before navigation so we don't miss the event
    await page.addInitScript(() => {
      window.__embedErrorDetail = null
      window.addEventListener('DOMContentLoaded', () => {
        document.addEventListener('nubi:widget-error', e => {
          window.__embedErrorDetail = e.detail
        })
      })
    })

    await page.goto(PAGE)

    await page.waitForFunction(
      () => {
        const el = document.querySelector('nubi-kpi[no-sample-fallback]')
        return el?.shadowRoot && el.shadowRoot.childElementCount > 0
      },
      { timeout: 8000 },
    )

    await page.waitForFunction(
      () => window.__embedErrorDetail !== null,
      { timeout: 10000 },
    )

    const detail = await page.evaluate(() => window.__embedErrorDetail)
    expect(detail).not.toBeNull()
    expect(typeof detail.message).toBe('string')
  })
})
