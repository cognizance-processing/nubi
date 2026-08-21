/**
 * e2e/filter-wiring.spec.js
 *
 * Connecting a filter to widgets, without typing a single name.
 *
 * The editor knows which params every registered query declares and which
 * variables the board has, so naming a filter should be enough to wire it.
 * This covers that whole path end to end:
 *
 * 1. Create a board via API with two data widgets and NO filter, NO variables:
 *      - one on `demo_by_region`, which declares a `region` param
 *      - one on `demo_all`, which declares none
 * 2. In the editor, add a filter and label it "Region".
 *      - its variable is derived from the label (`region`)
 *      - "Controls these widgets" lists both widgets: one connectable, one
 *        explained as having no parameters
 * 3. Connect the connectable one from the filter's own panel.
 * 4. Save, then assert the PERSISTED spec has the variable declared and the
 *    param bound — the wiring is real, not just a rendering.
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

/**
 * A board with data widgets but nothing wired — the state before this feature.
 * Created into the workspace the BROWSER is actually in: the active org is
 * remembered in localStorage, so a board made in the account's default org can
 * be invisible to the session driving the editor.
 */
async function createUnwiredBoard(request, token, orgId) {
  const spec = {
    version: 1,
    title: 'E2E Filter Wiring',
    layout: { cols: 12, row_height: 60 },
    variables: [],
    widgets: [
      {
        id: 'w_regional', type: 'table', query_id: 'demo_by_region',
        config: { title: 'Rows by region' },
        pos: { x: 1, y: 1, w: 6, h: 5 }, params: {},
      },
      {
        id: 'w_everything', type: 'table', query_id: 'demo_all',
        config: { title: 'Everything' },
        pos: { x: 7, y: 1, w: 6, h: 5 }, params: {},
      },
    ],
  }
  const res = await request.post(`${BACKEND}/api/v1/boards`, {
    headers: {
      Authorization: `Bearer ${token}`,
      ...(orgId ? { 'X-Org-Id': orgId } : {}),
    },
    data: { name: 'E2E Filter Wiring', config: { spec } },
  })
  expect(res.ok(), `Board creation failed: ${res.status()} ${await res.text()}`).toBeTruthy()
  return (await res.json()).id
}

/** The workspace the app lands in by default: the first org GET /orgs returns. */
async function firstOrgId(request, token) {
  const res = await request.get(`${BACKEND}/api/v1/orgs`, {
    headers: { Authorization: `Bearer ${token}` },
  })
  expect(res.ok()).toBeTruthy()
  const body = await res.json()
  const rows = Array.isArray(body) ? body : (body.orgs ?? [])
  return rows[0]?.id ?? null
}

async function browserLogin(page) {
  await page.goto('/login')
  await page.locator('input[type="email"]').fill(ADMIN_EMAIL)
  await page.locator('input[type="password"]').fill(ADMIN_PASSWORD)
  await page.locator('button[type="submit"]').click()
  await page.waitForURL(url => !url.pathname.startsWith('/login'), { timeout: 20_000 })
}

test.describe('Filter wiring', () => {
  let token

  test.beforeAll(async ({ request }) => {
    token = await apiLogin(request)
  })

  test('naming a filter wires it to the widgets that can take it', async ({ page, request }) => {
    test.setTimeout(120_000)
    await browserLogin(page)
    // Pin the browser to the same workspace the board is created in — the
    // account has several, and a board in the wrong one renders "not found".
    const orgId = await firstOrgId(request, token)
    await page.evaluate(id => { if (id) localStorage.setItem('nubi-active-org-id', id) }, orgId)
    const boardId = await createUnwiredBoard(request, token, orgId)
    await page.goto(`/d/${boardId}/edit`)
    await expect(page.getByTestId('editor-shell')).toBeVisible({ timeout: 30_000 })
    // The registry read that backs param matching happens on mount.
    await page.waitForTimeout(3000)

    // ── Add a filter and label it ──────────────────────────────────────────
    await page.locator('[data-testid="palette-add-filter"]:visible').first().click()
    await page.waitForTimeout(1200)

    // Show the Configure panel. The segment toggles, and adding a widget may
    // already have switched to it, so click only until the panel is showing.
    const label = page.locator('[data-testid="filter-label"]:visible').first()
    const cfgBtn = page.locator('[aria-label="Configure panel"]:visible').first()
    for (let i = 0; i < 3 && !(await label.count()); i++) {
      await cfgBtn.click()
      await page.waitForTimeout(900)
    }
    await expect(label).toBeVisible({ timeout: 10_000 })
    await label.fill('Region')
    await label.blur()
    await page.waitForTimeout(1000)

    // The variable follows the label — nobody typed "region".
    await expect(page.locator('[data-testid="filter-target-var"]').first()).toHaveValue('region')

    // ── The connections list explains every widget ─────────────────────────
    const conns = page.locator('[data-testid="filter-connections"]').first()
    await expect(conns).toBeVisible({ timeout: 10_000 })
    await expect(conns).toContainText('0 of 2 connected')
    await expect(conns).toContainText('Rows by region')
    // The query with no params says so rather than offering a dead checkbox.
    await expect(conns).toContainText('its query takes no parameters')

    // ── Connect from the filter's own panel ────────────────────────────────
    await page.locator('[data-testid="filter-connect-all"]').first().click()
    await page.waitForTimeout(800)
    await expect(conns).toContainText('1 of 2 connected')

    // ── Save and check what actually persisted ─────────────────────────────
    await page.locator('[data-testid="editor-save-btn"]:visible').first().click()
    await page.waitForTimeout(4000)

    const res = await request.get(`${BACKEND}/api/v1/boards/${boardId}`, {
      headers: { Authorization: `Bearer ${token}`, ...(orgId ? { 'X-Org-Id': orgId } : {}) },
    })
    expect(res.ok()).toBeTruthy()
    const spec = (await res.json()).config.spec

    // The board variable was declared on the filter's behalf, typed from its subtype.
    expect(spec.variables.map(v => v.name)).toContain('region')

    // The matching widget is bound; the one with no params is untouched.
    const regional = spec.widgets.find(w => w.id === 'w_regional')
    const everything = spec.widgets.find(w => w.id === 'w_everything')
    expect(regional.params).toEqual({ region: { ref: 'region' } })
    expect(everything.params ?? {}).toEqual({})

    // And the filter itself points at that variable.
    const filter = spec.widgets.find(w => w.type === 'filter')
    expect(filter.target_var).toBe('region')
  })
})
