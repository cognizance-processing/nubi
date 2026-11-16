/**
 * e2e/dashboard-variables.spec.js
 *
 * End-to-end test for dashboard parameter routing + variable binding.
 *
 * Flow tested:
 * 1. Create a board via API (logged-in fetch) with:
 *    - spec.variables: [{ name: 'region', type: 'text', default: null }]
 *    - A filter widget (subtype: 'text') bound to target_var: 'region'
 *    - A table widget whose params.region = { ref: 'region' } referencing
 *      the registered query 'demo_by_region'
 * 2. Navigate to /d/<id>?region=alpha
 *    - Assert filter input shows 'alpha'
 *    - Assert a /api/v1/query request was made with named_params.region = 'alpha'
 * 3. Change the filter to 'gamma'
 *    - Assert URL updates to ?region=gamma
 *    - Assert a new /api/v1/query request fires with named_params.region = 'gamma'
 */

import { test, expect } from '@playwright/test'

const BACKEND = 'http://localhost:8000'
const ADMIN_EMAIL = 'admin@nubi.dev'
const ADMIN_PASSWORD = 'nubi-admin-2026'

/** Log in via backend API and return the access token. */
async function apiLogin(request) {
  const res = await request.post(`${BACKEND}/api/v1/auth/login`, {
    data: { email: ADMIN_EMAIL, password: ADMIN_PASSWORD },
  })
  expect(res.ok()).toBeTruthy()
  const body = await res.json()
  return body.access_token
}

/**
 * The workspace the app lands in by default: the first org GET /orgs returns.
 * The account belongs to several, and a board created in a different one
 * renders "Board not found in this workspace" instead of the dashboard.
 */
async function firstOrgId(request, token) {
  const res = await request.get(`${BACKEND}/api/v1/orgs`, {
    headers: { Authorization: `Bearer ${token}` },
  })
  expect(res.ok()).toBeTruthy()
  const body = await res.json()
  const rows = Array.isArray(body) ? body : (body.orgs ?? [])
  return rows[0]?.id ?? null
}

/** Create a test board via the API and return its id. */
async function createVariableBoard(request, token, orgId) {
  const spec = {
    version: 1,
    title: 'E2E Variable Test',
    layout: { cols: 12, row_height: 60 },
    variables: [
      { name: 'region', type: 'text', default: null },
    ],
    widgets: [
      {
        id: 'filter_region',
        type: 'filter',
        subtype: 'text',
        target_var: 'region',
        query_id: '',
        props: { label: 'Region' },
        pos: { x: 1, y: 1, w: 4, h: 2 },
        params: {},
      },
      {
        id: 'table_region',
        type: 'table',
        query_id: 'demo_by_region',
        props: { limit: 50 },
        pos: { x: 1, y: 3, w: 12, h: 5 },
        params: { region: { ref: 'region' } },
        encoding: {},
      },
    ],
  }

  const res = await request.post(`${BACKEND}/api/v1/boards`, {
    headers: {
      Authorization: `Bearer ${token}`,
      ...(orgId ? { 'X-Org-Id': orgId } : {}),
    },
    data: { name: 'E2E Variable Test', config: { spec } },
  })
  expect(res.ok(), `Board creation failed: ${res.status()} ${await res.text()}`).toBeTruthy()
  const board = await res.json()
  return board.id
}

/** Log in through the browser UI so the front-end has a valid session. */
async function browserLogin(page) {
  await page.goto('/login')
  // Target the inputs by type, not label: the password field now sits next to a
  // "Show password" toggle that also carries the Password label.
  await page.locator('input[type="email"]').fill(ADMIN_EMAIL)
  await page.locator('input[type="password"]').fill(ADMIN_PASSWORD)
  await page.locator('button[type="submit"]').click()
  await page.waitForURL(url => !url.pathname.startsWith('/login'), { timeout: 20_000 })
}

test.describe('Dashboard variable routing', () => {
  let token
  let orgId
  let boardId

  test.beforeAll(async ({ request }) => {
    token = await apiLogin(request)
    orgId = await firstOrgId(request, token)
    boardId = await createVariableBoard(request, token, orgId)
  })

  test('URL param seeds filter + re-queries data widget', async ({ page }) => {
    await browserLogin(page)
    // Pin the browser to the workspace the board was created in.
    await page.evaluate(id => { if (id) localStorage.setItem('nubi-active-org-id', id) }, orgId)

    // Collect all /query requests for inspection.
    const queryRequests = []
    page.on('request', req => {
      if (req.url().includes('/api/v1/query') && req.method() === 'POST') {
        queryRequests.push(req)
      }
    })

    // ── Step 1: Navigate with ?region=alpha ─────────────────────────────────
    await page.goto(`/d/${boardId}?region=alpha`)
    await page.waitForSelector('[data-testid="dashboard-spec-renderer"]', { timeout: 15_000 })

    // The filter widget is a text input; it must show 'alpha' (seeded from URL)
    const filterInput = page.locator('input[type="text"]').first()
    await expect(filterInput).toHaveValue('alpha', { timeout: 10_000 })

    // Wait for at least one query request to fire with named_params.region = 'alpha'
    await page.waitForTimeout(2000) // give widgets time to fire queries

    const alphaRequests = queryRequests.filter(r => {
      try {
        const body = JSON.parse(r.postData() ?? '{}')
        return (
          body.query_id === 'demo_by_region' &&
          body.named_params?.region === 'alpha'
        )
      } catch { return false }
    })
    expect(
      alphaRequests.length,
      `Expected at least one /query call with named_params.region='alpha', got: ${queryRequests.map(r => r.postData()).join(' | ')}`
    ).toBeGreaterThan(0)

    // ── Step 2: Change filter to 'gamma' ────────────────────────────────────
    const prevCount = queryRequests.length
    await filterInput.fill('gamma')

    // URL should update to ?region=gamma
    await expect(page).toHaveURL(/[?&]region=gamma/, { timeout: 8_000 })

    // A new query should fire with region=gamma
    await page.waitForTimeout(2000)

    const gammaRequests = queryRequests.filter(r => {
      try {
        const body = JSON.parse(r.postData() ?? '{}')
        return (
          body.query_id === 'demo_by_region' &&
          body.named_params?.region === 'gamma'
        )
      } catch { return false }
    })
    expect(
      gammaRequests.length,
      `Expected at least one /query call with named_params.region='gamma', got: ${queryRequests.slice(prevCount).map(r => r.postData()).join(' | ')}`
    ).toBeGreaterThan(0)
  })
})
