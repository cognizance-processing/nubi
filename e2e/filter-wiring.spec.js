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

/**
 * A board with a filter ALREADY declared (variable + widget) but its data
 * widget has NO query bound yet — the mirror image of createUnwiredBoard,
 * exercising the OTHER auto-bind entry point: picking a query for a widget
 * that already reaches a matching board variable (DashboardEditor's
 * `setQueryId` → `autoBindParams`), rather than connecting a pre-existing
 * widget from the filter's own panel.
 */
async function createBoardWithFilterOnly(request, token, orgId) {
  const spec = {
    version: 1,
    title: 'E2E Auto-bind-on-pick',
    layout: { cols: 12, row_height: 60 },
    variables: [{ name: 'region', type: 'select', default: null }],
    widgets: [
      {
        id: 'w_filter', type: 'filter', target_var: 'region',
        props: { label: 'Region', subtype: 'select' },
        pos: { x: 1, y: 1, w: 4, h: 2 },
      },
      {
        id: 'w_table', type: 'table', query_id: '', params: {},
        config: { title: 'Demo rows' },
        pos: { x: 1, y: 3, w: 8, h: 6 },
      },
    ],
  }
  const res = await request.post(`${BACKEND}/api/v1/boards`, {
    headers: {
      Authorization: `Bearer ${token}`,
      ...(orgId ? { 'X-Org-Id': orgId } : {}),
    },
    data: { name: 'E2E Auto-bind-on-pick', config: { spec } },
  })
  expect(res.ok(), `Board creation failed: ${res.status()} ${await res.text()}`).toBeTruthy()
  return (await res.json()).id
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

  test('picking a query auto-binds an existing filter, and the wired variable actually changes rendered data', async ({ page, request }) => {
    test.setTimeout(120_000)
    await browserLogin(page)
    const orgId = await firstOrgId(request, token)
    await page.evaluate(id => { if (id) localStorage.setItem('nubi-active-org-id', id) }, orgId)
    const boardId = await createBoardWithFilterOnly(request, token, orgId)
    await page.goto(`/d/${boardId}/edit`)
    await expect(page.getByTestId('editor-shell')).toBeVisible({ timeout: 30_000 })
    await page.waitForTimeout(3000)

    // ── Select the table widget and pick its query ──────────────────────────
    // GridCanvas keeps an off-breakpoint layout in the DOM (hidden via CSS),
    // so more than one node can carry the same data-grid-id — the draggable
    // wrapper (role="button") is the one actually visible/interactive.
    await page.locator('[role="button"][data-grid-id="w_table"]:visible').first().click()
    await page.waitForTimeout(500)
    // ConfigPanel opens on the "Widget" tab by default — the query picker
    // lives under "Data".
    await page.getByRole('button', { name: 'Data', exact: true }).click()
    await page.waitForTimeout(500)

    const queryTrigger = page.locator('button[aria-label="Query"]:visible').first()
    await expect(queryTrigger).toBeVisible({ timeout: 10_000 })
    await queryTrigger.click()
    await page.locator('#query-picker-list-opt-demo_by_region').click()
    await page.waitForTimeout(1000)

    // ── The pick auto-bound the widget to the pre-existing "region" filter,
    //    and said so — the widget must not change in silence. ──────────────
    await expect(page.getByTestId('param-autobind-note')).toContainText('region')

    // ── Save and check what actually persisted ───────────────────────────────
    await page.locator('[data-testid="editor-save-btn"]:visible').first().click()
    await page.waitForTimeout(4000)

    const res = await request.get(`${BACKEND}/api/v1/boards/${boardId}`, {
      headers: { Authorization: `Bearer ${token}`, ...(orgId ? { 'X-Org-Id': orgId } : {}) },
    })
    expect(res.ok()).toBeTruthy()
    const spec = (await res.json()).config.spec
    const table = spec.widgets.find(w => w.id === 'w_table')
    expect(table.query_id).toBe('demo_by_region')
    expect(table.params).toEqual({ region: { ref: 'region' } })

    // ── The wiring is REAL, not just a config-panel artifact: drive the
    //    variable via the URL (the same mechanism a filter click uses under
    //    the hood — VariableProvider syncs both ways) and confirm the live
    //    board's table actually renders different rows. ─────────────────────
    // Scoped to the table itself — with the filter set, the filter's OWN
    // trigger also displays "alpha" (the current selection), so an
    // unscoped page-wide text search is ambiguous.
    const tableRows = page.getByRole('table')
    await page.goto(`/d/${boardId}`)
    await expect(tableRows.getByText('alpha', { exact: true })).toBeVisible({ timeout: 15_000 })
    await expect(tableRows.getByText('beta', { exact: true })).toBeVisible()

    await page.goto(`/d/${boardId}?region=alpha`)
    await expect(tableRows.getByText('alpha', { exact: true })).toBeVisible({ timeout: 15_000 })
    await expect(tableRows.getByText('beta', { exact: true })).not.toBeVisible()

    // ── The filter's rendered trigger is clickable anywhere on its body, not
    //    just the caret glyph — click near the LEFT edge (not the caret,
    //    which sits at the far right) and confirm the popover still opens. ──
    const trigger = page.locator('[data-grid-id="w_filter"] button[aria-haspopup]').first()
    await expect(trigger).toBeVisible({ timeout: 10_000 })
    const box = await trigger.boundingBox()
    await page.mouse.click(box.x + box.width * 0.15, box.y + box.height / 2)
    await expect(page.locator('[role="listbox"]')).toBeVisible({ timeout: 5000 })
  })
})
