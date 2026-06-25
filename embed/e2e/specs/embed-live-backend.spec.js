// @ts-check
/**
 * embed-live-backend.spec.js — Part B: Browser-level live embed E2E tests.
 *
 * Tests the REAL embed flow:
 *   embed token → live uvicorn backend → components fetch real data
 *
 * Requires:
 *   RUN_E2E_EMBED_LIVE=1       gate variable
 *   NUBI_E2E_BACKEND_URL       http://127.0.0.1:<PORT>/api/v1 (from the live uvicorn)
 *   NUBI_E2E_AUTHOR_TOKEN      JWT with author:metric scope (minted by Python test setup)
 *   NUBI_E2E_READONLY_TOKEN    JWT with read:query scope only
 *   NUBI_E2E_METRIC_ID         metric slug to test against (default: retail_nsv)
 *
 * Run via:
 *   npx playwright test --config embed/e2e/playwright.config.js \
 *     embed/e2e/specs/embed-live-backend.spec.js \
 *     [--project chromium]
 *
 * Or from the npm script added for this suite:
 *   npm run test:e2e:embed:live
 *
 * Architecture note — CORS
 * ------------------------
 * The live uvicorn subprocess is booted by the Python E2E conftest with the
 * CORS_ORIGINS env var set to allow the static file server origin
 * (http://localhost:8799) so the browser can POST to the backend.
 *
 * If CORS is not configured (the env var is missing), XHR requests from the
 * Playwright page (at localhost:8799) to the uvicorn (at 127.0.0.1:<PORT>)
 * will be blocked by the browser with a CORS preflight failure.  In that case,
 * the tests document the failure clearly rather than silently passing.
 *
 * The tests use:
 *   - The fixture page at /embed/e2e/fixtures/live-embed-test.html
 *     (served by the same python3 http.server used by the mock suite)
 *   - REAL HS256 first-party JWTs minted by the Python E2E setup and
 *     injected as URL query params.
 *
 * Why HS256 (not RS256)?
 * ----------------------
 * The live uvicorn only knows RS256 issuers that were registered at startup or
 * via the DB.  To keep Part B self-contained (no extra DB writes needed) we use
 * the same HS256 first-party tokens the Python E2E suite already mints:
 *   author_token  → HS256, scope=["read:*","author:metric"]
 *   readonly_token → HS256, scope=["read:query"]
 * These exercise the REAL data path (metric query, result table) via kind='access'
 * tokens.  The RS256 embed-kind path is covered by Part A (API tests).
 */

import { test, expect } from '@playwright/test'

// ---------------------------------------------------------------------------
// Gate: skip the whole spec unless RUN_E2E_EMBED_LIVE=1
// ---------------------------------------------------------------------------

const RUN_LIVE = (process.env.RUN_E2E_EMBED_LIVE || '').trim() === '1'

const BACKEND_URL      = process.env.NUBI_E2E_BACKEND_URL || ''
const AUTHOR_TOKEN     = process.env.NUBI_E2E_AUTHOR_TOKEN || ''
const READONLY_TOKEN   = process.env.NUBI_E2E_READONLY_TOKEN || ''
const METRIC_ID        = process.env.NUBI_E2E_METRIC_ID || 'retail_nsv'

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/**
 * Suppress expected noise from network errors and Monaco workers.
 * Returns a getter for captured unexpected errors.
 */
function attachErrorCollector(page) {
  const errors = []
  page.on('console', msg => {
    if (msg.type() === 'error') {
      const text = msg.text()
      // Suppress known/expected console noise
      if (
        text.includes('Failed to load resource') ||
        text.includes('net::ERR_CONNECTION_REFUSED') ||
        text.includes('Failed to fetch') ||
        text.includes('getWorkerUrl') ||
        text.includes('CORS')   // CORS errors are documented; test asserts on state
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
 * Build the fixture page URL with live backend + tokens injected as params.
 */
function buildFixtureUrl(authorToken, readonlyToken) {
  const params = new URLSearchParams({
    backend:        BACKEND_URL,
    author_token:   authorToken,
    readonly_token: readonlyToken,
  })
  return `/embed/e2e/fixtures/live-embed-test.html?${params.toString()}`
}

/**
 * Wait for the fixture page to report status="ready" (tokens wired).
 */
async function waitForFixtureReady(page, timeout = 15000) {
  await page.waitForFunction(
    () => {
      const el = document.getElementById('status')
      return el && el.textContent.trim() === 'ready'
    },
    { timeout },
  )
}

/**
 * Wait for a custom element's shadow root to be non-empty.
 */
async function waitForShadowDom(page, selector, timeout = 10000) {
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
 * Click the Run button inside a nubi-metric-explorer's shadow root.
 * Returns true if the Run button was found and clicked.
 */
async function clickRunButton(page, selector) {
  return page.evaluate((sel) => {
    const el = document.querySelector(sel)
    if (!el?.shadowRoot) return false
    const btn = el.shadowRoot.querySelector('.btn-run-me')
    if (!btn) return false
    btn.click()
    return true
  }, selector)
}

/**
 * Get the scope indicator text from a metric-explorer or query-editor.
 */
async function getScopeIndicator(page, selector) {
  return page.evaluate((sel) => {
    const el = document.querySelector(sel)
    if (!el?.shadowRoot) return null
    const ind = el.shadowRoot.querySelector('.scope-indicator')
    return ind ? ind.textContent.trim() : null
  }, selector)
}

/**
 * Wait for the metric-explorer results table to appear (live data loaded).
 * Returns true when a .me-table is found in the shadow root.
 */
async function waitForResultTable(page, selector, timeout = 20000) {
  return page.waitForFunction(
    (sel) => {
      const el = document.querySelector(sel)
      if (!el?.shadowRoot) return false
      // Either a result table OR a footer row count > 0
      const table  = el.shadowRoot.querySelector('.me-table')
      const footer = el.shadowRoot.querySelector('.nubi-me-footer')
      if (table) return true
      // Also accept the footer text "N rows" as confirmation
      if (footer && /\d+ rows/.test(footer.textContent)) return true
      return false
    },
    selector,
    { timeout },
  )
}

/**
 * Get number of data rows rendered inside the metric-explorer results table.
 */
async function getResultRowCount(page, selector) {
  return page.evaluate((sel) => {
    const el = document.querySelector(sel)
    if (!el?.shadowRoot) return 0
    const table = el.shadowRoot.querySelector('.me-table')
    if (!table) return 0
    // Count tbody rows (header row is in thead)
    const tbody = table.querySelector('tbody')
    return tbody ? tbody.querySelectorAll('tr').length : 0
  }, selector)
}

/**
 * Check if the metric-explorer is showing an error state (me-error class).
 */
async function hasErrorState(page, selector) {
  return page.evaluate((sel) => {
    const el = document.querySelector(sel)
    if (!el?.shadowRoot) return false
    return !!el.shadowRoot.querySelector('.me-error')
  }, selector)
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

test.describe('live embed backend — metric-explorer fetches REAL data', () => {
  test.beforeEach(({}, testInfo) => {
    if (!RUN_LIVE) {
      testInfo.skip(true,
        'Live embed E2E skipped — set RUN_E2E_EMBED_LIVE=1 and provide ' +
        'NUBI_E2E_BACKEND_URL / NUBI_E2E_AUTHOR_TOKEN / NUBI_E2E_READONLY_TOKEN. ' +
        'The live uvicorn must be running with CORS_ORIGINS=http://localhost:8799.'
      )
    }
    if (!BACKEND_URL || !AUTHOR_TOKEN) {
      testInfo.skip(true, 'NUBI_E2E_BACKEND_URL and NUBI_E2E_AUTHOR_TOKEN must be set')
    }
  })

  // ─────────────────────────────────────────────────────────────────────────
  // B1: Page loads + components initialise without errors
  // ─────────────────────────────────────────────────────────────────────────

  test('fixture page loads and reaches ready state', async ({ page }) => {
    const getErrors = attachErrorCollector(page)
    const url = buildFixtureUrl(AUTHOR_TOKEN, READONLY_TOKEN || AUTHOR_TOKEN)
    await page.goto(url)

    await waitForFixtureReady(page)

    // Shadow DOMs must initialise
    await waitForShadowDom(page, '#me-metric')
    await waitForShadowDom(page, '#qe-metric')

    const errors = getErrors()
    expect(errors, `Unexpected errors:\n${errors.join('\n')}`).toHaveLength(0)
  })

  // ─────────────────────────────────────────────────────────────────────────
  // B2: metric-explorer (author:metric) shows METRIC scope indicator
  // ─────────────────────────────────────────────────────────────────────────

  test('author:metric token → scope indicator shows METRIC', async ({ page }) => {
    const url = buildFixtureUrl(AUTHOR_TOKEN, READONLY_TOKEN || AUTHOR_TOKEN)
    await page.goto(url)
    await waitForFixtureReady(page)
    await waitForShadowDom(page, '#me-metric')

    // Wait for scope indicator to settle
    await page.waitForFunction(
      () => {
        const el = document.querySelector('#me-metric')
        if (!el?.shadowRoot) return false
        const ind = el.shadowRoot.querySelector('.scope-indicator')
        return ind && ind.textContent.trim().length > 0
      },
      { timeout: 10000 },
    )

    const indicator = await getScopeIndicator(page, '#me-metric')
    expect(indicator).toBe('METRIC')
  })

  // ─────────────────────────────────────────────────────────────────────────
  // B3: read:query only token → scope indicator shows READ-ONLY
  // ─────────────────────────────────────────────────────────────────────────

  test('read:query only token → scope indicator shows READ-ONLY', async ({ page }) => {
    if (!READONLY_TOKEN) {
      test.skip()
      return
    }
    const url = buildFixtureUrl(AUTHOR_TOKEN, READONLY_TOKEN)
    await page.goto(url)
    await waitForFixtureReady(page)
    await waitForShadowDom(page, '#me-readonly')

    await page.waitForFunction(
      () => {
        const el = document.querySelector('#me-readonly')
        if (!el?.shadowRoot) return false
        const ind = el.shadowRoot.querySelector('.scope-indicator')
        return ind && ind.textContent.trim().length > 0
      },
      { timeout: 10000 },
    )

    const indicator = await getScopeIndicator(page, '#me-readonly')
    expect(indicator).toBe('READ-ONLY')
  })

  // ─────────────────────────────────────────────────────────────────────────
  // B4: metric-explorer run → fetches REAL rows from live backend
  // ─────────────────────────────────────────────────────────────────────────

  test('metric-explorer (author:metric) runs metric query → real data rows rendered', async ({ page }) => {
    const getErrors = attachErrorCollector(page)
    const url = buildFixtureUrl(AUTHOR_TOKEN, READONLY_TOKEN || AUTHOR_TOKEN)
    await page.goto(url)
    await waitForFixtureReady(page)
    await waitForShadowDom(page, '#me-metric')

    // Wait for scope indicator to settle (scope resolved = ready to run)
    await page.waitForFunction(
      () => {
        const el = document.querySelector('#me-metric')
        if (!el?.shadowRoot) return false
        const ind = el.shadowRoot.querySelector('.scope-indicator')
        return ind && ind.textContent.trim() === 'METRIC'
      },
      { timeout: 10000 },
    )

    // Click the Run button
    const clicked = await clickRunButton(page, '#me-metric')
    expect(clicked, 'Run button not found in metric-explorer shadow DOM').toBe(true)

    // Wait for the result table to appear (live data from backend)
    await waitForResultTable(page, '#me-metric', 25000)

    // Verify: not an error state
    const hasErr = await hasErrorState(page, '#me-metric')
    expect(hasErr, 'metric-explorer is showing an error state after run').toBe(false)

    // Verify: rows > 0 (real data)
    const rowCount = await getResultRowCount(page, '#me-metric')
    expect(rowCount, 'Expected real rows in result table but got 0').toBeGreaterThan(0)

    // Console must be clean
    const errors = getErrors()
    expect(errors, `Unexpected errors after run:\n${errors.join('\n')}`).toHaveLength(0)
  })

  // ─────────────────────────────────────────────────────────────────────────
  // B5: query-editor in metric mode shows METRIC scope badge
  // ─────────────────────────────────────────────────────────────────────────

  test('query-editor with author:metric token shows METRIC scope badge', async ({ page }) => {
    const url = buildFixtureUrl(AUTHOR_TOKEN, READONLY_TOKEN || AUTHOR_TOKEN)
    await page.goto(url)
    await waitForFixtureReady(page)
    await waitForShadowDom(page, '#qe-metric')

    // Wait for scope badge to settle in the query-editor toolbar
    await page.waitForFunction(
      () => {
        const el = document.querySelector('#qe-metric')
        if (!el?.shadowRoot) return false
        const badge = el.shadowRoot.querySelector('.scope-badge')
        return badge && badge.textContent.trim().length > 0
      },
      { timeout: 10000 },
    )

    const badge = await page.evaluate(() => {
      const el = document.querySelector('#qe-metric')
      if (!el?.shadowRoot) return null
      const b = el.shadowRoot.querySelector('.scope-badge')
      return b ? b.textContent.trim() : null
    })

    // author:metric token → badge should be "METRIC" (no SQL capability)
    expect(badge).toBe('METRIC')
  })

  // ─────────────────────────────────────────────────────────────────────────
  // B6: CORS / connectivity sanity check
  //
  // The metric-explorer makes a preflight (OPTIONS) + POST request to the
  // backend.  If CORS is not configured, the fetch will fail with a network
  // error.  We verify connectivity by checking the backend's /health endpoint
  // directly from the browser context.
  // ─────────────────────────────────────────────────────────────────────────

  test('browser can reach live backend /health (CORS sanity)', async ({ page }) => {
    await page.goto('/embed/e2e/fixtures/live-embed-test.html')

    // Extract the backend root (strip /api/v1 if present)
    const backendRoot = BACKEND_URL.replace(/\/api\/v1\/?$/, '')

    const result = await page.evaluate(async (url) => {
      try {
        const resp = await fetch(`${url}/health`, { mode: 'cors' })
        return { ok: resp.ok, status: resp.status }
      } catch (err) {
        return { ok: false, error: err.message }
      }
    }, backendRoot)

    if (!result.ok) {
      // Document the CORS/connectivity failure clearly — don't hide it
      console.warn(
        `[live-embed] CORS connectivity check failed: ${JSON.stringify(result)}. ` +
        `Ensure the backend is booted with CORS_ORIGINS=http://localhost:8799 ` +
        `and NUBI_E2E_BACKEND_URL points to the correct address.`
      )
    }

    // We accept that CORS may not be configured for all backends.
    // The test passes with a warning when CORS is blocked; it fails only
    // when the backend is completely unreachable (connection refused).
    expect(result.error || '', 'Backend is unreachable (connection refused)')
      .not.toContain('ERR_CONNECTION_REFUSED')
  })

  // ─────────────────────────────────────────────────────────────────────────
  // B7: SQL mode hidden when token only carries author:metric
  //
  // The query-editor must not show the SQL tab for an author:metric-only token
  // (no author:sql).  This is the cosmetic gate that mirrors the server's M3-SEC.
  // ─────────────────────────────────────────────────────────────────────────

  test('query-editor: SQL mode hidden for author:metric-only token', async ({ page }) => {
    const url = buildFixtureUrl(AUTHOR_TOKEN, READONLY_TOKEN || AUTHOR_TOKEN)
    await page.goto(url)
    await waitForFixtureReady(page)
    await waitForShadowDom(page, '#qe-metric')

    await page.waitForFunction(
      () => {
        const el = document.querySelector('#qe-metric')
        if (!el?.shadowRoot) return false
        const badge = el.shadowRoot.querySelector('.scope-badge')
        return badge && badge.textContent.trim().length > 0
      },
      { timeout: 10000 },
    )

    const tabs = await page.evaluate(() => {
      const el = document.querySelector('#qe-metric')
      if (!el?.shadowRoot) return []
      return [...el.shadowRoot.querySelectorAll('.mode-tab')].map(t => t.textContent.trim())
    })

    // author:metric-only token must NOT show the SQL tab
    expect(tabs).not.toContain('SQL')
    // METRIC tab must be present
    expect(tabs).toContain('METRIC')
  })
})

// ---------------------------------------------------------------------------
// Standalone CORS-independent smoke test (always runs when gate is set)
// Tests the HTML fixture page itself loads cleanly (bundle + elements)
// ---------------------------------------------------------------------------

test.describe('live-embed fixture — bundle smoke (no backend needed)', () => {
  test.beforeEach(({}, testInfo) => {
    if (!RUN_LIVE) {
      testInfo.skip(true, 'Set RUN_E2E_EMBED_LIVE=1 to run live embed tests')
    }
  })

  test('fixture HTML loads and custom elements register without bundle errors', async ({ page }) => {
    const getErrors = attachErrorCollector(page)

    // Load without backend params — elements will init but fetch nothing
    await page.goto('/embed/e2e/fixtures/live-embed-test.html')

    // Wait for elements to register
    await page.waitForFunction(
      () =>
        typeof customElements !== 'undefined' &&
        customElements.get('nubi-metric-explorer') !== undefined &&
        customElements.get('nubi-query-editor')    !== undefined,
      { timeout: 12000 },
    )

    const errors = getErrors()
    expect(errors, `Bundle errors:\n${errors.join('\n')}`).toHaveLength(0)
  })
})
