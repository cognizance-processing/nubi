/**
 * e2e/filter-toggle.spec.js
 *
 * Getting filters out of the way, without losing track of them.
 *
 * A board can place a filter three ways — in the grid, in the bar above it, or
 * in a slide-over drawer. Only the drawer had a toggle; the bar was permanent
 * and on a board with several filters it eats the top of the first screen.
 *
 * Collapsing filters creates a specific hazard: a board that is filtered but
 * looks unfiltered. So the rule under test is not just "it collapses" — it is
 * that a collapsed bar still reports how many filters are applied, and that
 * the count reflects what is NARROWING the data rather than how many controls
 * happen to exist.
 */

import { test, expect } from '@playwright/test'

const BACKEND = 'http://localhost:8000'
const ADMIN_EMAIL = 'admin@nubi.dev'
const ADMIN_PASSWORD = 'nubi-admin-2026'

async function apiLogin(request) {
  const res = await request.post(`${BACKEND}/api/v1/auth/login`, {
    data: { email: ADMIN_EMAIL, password: ADMIN_PASSWORD },
  })
  expect(res.ok()).toBeTruthy()
  return (await res.json()).access_token
}

async function firstOrgId(request, token) {
  const res = await request.get(`${BACKEND}/api/v1/orgs`, {
    headers: { Authorization: `Bearer ${token}` },
  })
  expect(res.ok()).toBeTruthy()
  const body = await res.json()
  const rows = Array.isArray(body) ? body : (body.orgs ?? [])
  return rows[0]?.id ?? null
}

/** An options query so the first filter's dropdown has real choices. */
async function createOptionsQuery(request, token, orgId) {
  const res = await request.post(`${BACKEND}/api/v1/queries`, {
    headers: { Authorization: `Bearer ${token}`, ...(orgId ? { 'X-Org-Id': orgId } : {}) },
    data: {
      name: 'E2E toggle — name options',
      config: {
        sql: 'SELECT DISTINCT name AS value, name AS label FROM demo ORDER BY name',
        datastore_id: null,
        params: [],
      },
    },
  })
  expect(res.ok(), `options query failed: ${res.status()} ${await res.text()}`).toBeTruthy()
  return (await res.json()).id
}

/** A board with two header-bar filters over one demo-backed table. */
async function createBarFilterBoard(request, token, orgId, optionsQueryId) {
  const spec = {
    version: 1,
    title: 'E2E Filter Toggle',
    layout: { cols: 12, row_height: 60 },
    variables: [
      { name: 'name', type: 'multiselect', default: [] },
      { name: 'active', type: 'multiselect', default: [] },
    ],
    widgets: [
      {
        id: 'w_f1', type: 'filter', target_var: 'name', placement: 'header', order: 1,
        options_query_id: optionsQueryId,
        props: { label: 'Name', subtype: 'multiselect' },
        pos: { x: 1, y: 1, w: 3, h: 2 },
      },
      {
        id: 'w_f2', type: 'filter', target_var: 'active', placement: 'header', order: 2,
        props: { label: 'Active', subtype: 'multiselect' },
        pos: { x: 4, y: 1, w: 3, h: 2 },
      },
      {
        id: 'w_table', type: 'table', query_id: 'demo_all', params: {},
        config: { title: 'Rows' },
        pos: { x: 1, y: 3, w: 8, h: 5 },
      },
    ],
  }
  const res = await request.post(`${BACKEND}/api/v1/boards`, {
    headers: { Authorization: `Bearer ${token}`, ...(orgId ? { 'X-Org-Id': orgId } : {}) },
    data: { name: 'E2E Filter Toggle', config: { spec } },
  })
  expect(res.ok(), `board creation failed: ${res.status()} ${await res.text()}`).toBeTruthy()
  return (await res.json()).id
}

async function browserLogin(page) {
  await page.goto('/login')
  await page.locator('input[type="email"]').fill(ADMIN_EMAIL)
  await page.locator('input[type="password"]').fill(ADMIN_PASSWORD)
  await page.locator('button[type="submit"]').click()
  await page.waitForURL(url => !url.pathname.startsWith('/login'), { timeout: 20_000 })
}

test.describe('Header filter bar toggle', () => {
  let token

  test.beforeAll(async ({ request }) => {
    token = await apiLogin(request)
  })

  test('the bar collapses, and reports applied filters while hidden', async ({ page, request }) => {
    test.setTimeout(120_000)
    await browserLogin(page)
    const orgId = await firstOrgId(request, token)
    await page.evaluate(id => { if (id) localStorage.setItem('nubi-active-org-id', id) }, orgId)
    const optionsQueryId = await createOptionsQuery(request, token, orgId)
    const boardId = await createBarFilterBoard(request, token, orgId, optionsQueryId)

    await page.goto(`/d/${boardId}`)
    const bar = page.locator('.nubi-filter-bar').first()
    await expect(bar).toBeVisible({ timeout: 30_000 })

    const toggle = bar.getByRole('button', { name: /Filters/ })
    await expect(toggle).toBeVisible()
    await expect(toggle).toHaveAttribute('aria-expanded', 'true')

    // Open by default: both filter controls are reachable.
    const controls = bar.locator('button[aria-haspopup]')
    await expect(controls).toHaveCount(2)

    // Nothing selected yet, so no badge — the count is state, not decoration.
    // (The label also carries a caret glyph, so assert on the absence of a
    // number rather than an exact string.)
    await expect(toggle).not.toHaveText(/\d/)

    // ── Collapse ───────────────────────────────────────────────────────────
    await toggle.click()
    await expect(toggle).toHaveAttribute('aria-expanded', 'false')
    await expect(controls).toHaveCount(0)

    // ── Re-open, apply a filter, collapse again ────────────────────────────
    await toggle.click()
    await expect(controls).toHaveCount(2)
    await controls.first().click()
    // Target the option by NAME, not .first(): the table widget's own column
    // and grouping menus contain hidden `role="option"` nodes that come
    // earlier in DOM order, so a positional match lands on one of those.
    await page.getByRole('option', { name: 'alpha' }).click()
    await page.keyboard.press('Escape')

    // Applied count shows even while the bar is open…
    await expect(toggle).toContainText('1', { timeout: 10_000 })

    // …and crucially survives collapsing: a filtered board must never look
    // unfiltered just because the controls are hidden.
    await toggle.click()
    await expect(controls).toHaveCount(0)
    await expect(toggle).toContainText('1')
  })
})
