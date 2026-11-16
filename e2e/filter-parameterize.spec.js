/**
 * e2e/filter-parameterize.spec.js
 *
 * Connecting a filter to a widget whose query was never written to be
 * filtered — without the author touching SQL.
 *
 * Before this, "its query takes no parameters" was where the dashboard editor
 * gave up: the only way forward was to leave the editor, open the query, and
 * hand-write a `{{param}}` placeholder into the right subquery. This covers
 * the replacement path end to end:
 *
 * 1. Create a board via API with a filter and a widget on a query that
 *    declares NO params (so the filter cannot reach it).
 * 2. In the editor, the widget is listed as unconnectable — with an offer to
 *    add the filter to its query.
 * 3. Take that offer, pick a column.
 * 4. Assert the query itself was rewritten and now declares the param, and
 *    that the widget is bound to the variable.
 * 5. Assert the rewrite is SAFE: running the query with the filter unset
 *    returns exactly what it returned before.
 * 6. Assert the dropdown got its CHOICES too — a filter wired to its data but
 *    with an empty option list is not a working filter.
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

/**
 * A query whose dimension is aggregated away.
 *
 * With *params* empty the filter panel classifies a widget on it as
 * `no-param`; with unrelated params declared it classifies as `choose` —
 * "this query has parameters, just not the one you asked for". Both are the
 * same dead end for the author, so both must offer to add the parameter.
 */
async function createUnfilterableQuery(request, token, orgId, { params = [], name = 'E2E unfilterable' } = {}) {
  const res = await request.post(`${BACKEND}/api/v1/queries`, {
    headers: { Authorization: `Bearer ${token}`, ...(orgId ? { 'X-Org-Id': orgId } : {}) },
    data: {
      name,
      config: {
        // `name` exists only inside the subquery — the outer SELECT groups it
        // away, so a predicate appended at the top level could not see it.
        sql: 'SELECT active, SUM(value) AS total FROM (SELECT name, active, value FROM demo) d GROUP BY active',
        datastore_id: null,
        params,
      },
    },
  })
  expect(res.ok(), `query creation failed: ${res.status()} ${await res.text()}`).toBeTruthy()
  return (await res.json()).id
}

async function createBoard(request, token, orgId, queryId) {
  const spec = {
    version: 1,
    title: 'E2E Parameterize',
    layout: { cols: 12, row_height: 60 },
    variables: [{ name: 'name', type: 'multiselect', default: [] }],
    widgets: [
      {
        id: 'w_filter', type: 'filter', target_var: 'name',
        props: { label: 'Name', subtype: 'multiselect' },
        pos: { x: 1, y: 1, w: 4, h: 2 },
      },
      {
        id: 'w_table', type: 'table', query_id: queryId, params: {},
        config: { title: 'Totals' },
        pos: { x: 1, y: 3, w: 8, h: 5 },
      },
    ],
  }
  const res = await request.post(`${BACKEND}/api/v1/boards`, {
    headers: { Authorization: `Bearer ${token}`, ...(orgId ? { 'X-Org-Id': orgId } : {}) },
    data: { name: 'E2E Parameterize', config: { spec } },
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

test.describe('Parameterize a query from the filter panel', () => {
  let token

  test.beforeAll(async ({ request }) => {
    token = await apiLogin(request)
  })

  test('a filter can be added to a query that declares no params, without writing SQL', async ({ page, request }) => {
    test.setTimeout(150_000)
    await browserLogin(page)
    const orgId = await firstOrgId(request, token)
    await page.evaluate(id => { if (id) localStorage.setItem('nubi-active-org-id', id) }, orgId)

    const queryId = await createUnfilterableQuery(request, token, orgId)
    const boardId = await createBoard(request, token, orgId, queryId)

    const hdrs = { Authorization: `Bearer ${token}`, ...(orgId ? { 'X-Org-Id': orgId } : {}) }

    // Baseline: what the query returns before anything is changed.
    const beforeRes = await request.post(`${BACKEND}/api/v1/query`, {
      headers: hdrs, data: { query_id: queryId },
    })
    expect(beforeRes.ok()).toBeTruthy()
    const beforeBody = await beforeRes.body()

    await page.goto(`/d/${boardId}/edit`)
    await expect(page.getByTestId('editor-shell')).toBeVisible({ timeout: 30_000 })
    await page.waitForTimeout(3000)

    // Select the filter widget so its config panel (and wiring list) shows.
    await page.locator('[role="button"][data-grid-id="w_filter"]:visible').first().click()
    await page.waitForTimeout(1000)

    const conns = page.locator('[data-testid="filter-connections"]').first()
    await expect(conns).toBeVisible({ timeout: 15_000 })
    // The widget is present but unreachable — that is the state being fixed.
    await expect(conns).toContainText('its query takes no parameters')
    await expect(conns).toContainText('0 of 1 connected')

    // ── Take the offer instead of going off to edit SQL ────────────────────
    // The editor renders its config panel twice (desktop rail + mobile
    // sheet), so every control matches more than once — scope to the visible
    // one rather than asserting a single match.
    await page.locator(`[data-testid="make-filterable-${queryId}"]:visible`).first().click()
    const colSelect = page.locator('select[aria-label="Column to filter on"]:visible').first()
    await expect(colSelect).toBeVisible({ timeout: 15_000 })
    await colSelect.selectOption('name')
    await page.getByRole('button', { name: 'Add & connect' }).first().click()

    // The widget becomes connected once the query declares the param.
    await expect(conns).toContainText('1 of 1 connected', { timeout: 30_000 })

    // ── The query itself really was rewritten ──────────────────────────────
    const qRes = await request.get(`${BACKEND}/api/v1/queries/${queryId}`, { headers: hdrs })
    expect(qRes.ok()).toBeTruthy()
    const cfg = (await qRes.json()).config
    expect(cfg.params.map(p => p.name)).toContain('name')
    expect(cfg.sql).toContain('{% if name %}')
    // Injected INSIDE the subquery, where `name` actually exists — not
    // appended to the outer SELECT that grouped it away.
    expect(cfg.sql.indexOf('{% if name %}')).toBeLessThan(cfg.sql.indexOf(') d GROUP BY'))

    // ── And the rewrite is inert until the filter is used ──────────────────
    const afterRes = await request.post(`${BACKEND}/api/v1/query`, {
      headers: hdrs, data: { query_id: queryId },
    })
    expect(afterRes.ok()).toBeTruthy()
    expect(Buffer.compare(await afterRes.body(), beforeBody)).toBe(0)

    // ── While actually filtering when it is ────────────────────────────────
    const filtered = await request.post(`${BACKEND}/api/v1/query`, {
      headers: hdrs, data: { query_id: queryId, named_params: { name: ['alpha'] } },
    })
    expect(filtered.ok()).toBeTruthy()
    expect(Buffer.compare(await filtered.body(), beforeBody)).not.toBe(0)

    // Save so the widget binding persists too.
    await page.locator('[data-testid="editor-save-btn"]:visible').first().click()
    await page.waitForTimeout(3000)
    const bRes = await request.get(`${BACKEND}/api/v1/boards/${boardId}`, { headers: hdrs })
    const spec = (await bRes.json()).config.spec
    expect(spec.widgets.find(w => w.id === 'w_table').params).toEqual({ name: { ref: 'name' } })
  })

  test('a query with the WRONG parameters also offers to add the right one', async ({ page, request }) => {
    // The real-world shape this covers: a converted board whose queries all
    // declare Period1/Period2/country_description. Adding a Region filter
    // classifies every widget as `choose`, and the old UI's only offer was a
    // dropdown of those unrelated params — binding a Region filter to a date
    // param type-checks and is nonsense. Not having the RIGHT parameter is
    // the same dead end as having none.
    test.setTimeout(150_000)
    await browserLogin(page)
    const orgId = await firstOrgId(request, token)
    await page.evaluate(id => { if (id) localStorage.setItem('nubi-active-org-id', id) }, orgId)

    const queryId = await createUnfilterableQuery(request, token, orgId, {
      name: 'E2E wrong-params',
      params: [{ name: 'Period1', type: 'text', default: null, required: false }],
    })
    const boardId = await createBoard(request, token, orgId, queryId)
    const hdrs = { Authorization: `Bearer ${token}`, ...(orgId ? { 'X-Org-Id': orgId } : {}) }

    await page.goto(`/d/${boardId}/edit`)
    await expect(page.getByTestId('editor-shell')).toBeVisible({ timeout: 30_000 })
    await page.waitForTimeout(3000)
    await page.locator('[role="button"][data-grid-id="w_filter"]:visible').first().click()
    await page.waitForTimeout(1000)

    const conns = page.locator('[data-testid="filter-connections"]').first()
    await expect(conns).toBeVisible({ timeout: 15_000 })
    // It says plainly that the param it wants is missing, rather than just
    // presenting the unrelated ones as if one of them would do.
    await expect(conns).toContainText('None of its parameters is called')

    await page.locator(`[data-testid="make-filterable-${queryId}"]:visible`).first().click()
    const colSelect = page.locator('select[aria-label="Column to filter on"]:visible').first()
    await expect(colSelect).toBeVisible({ timeout: 15_000 })
    await colSelect.selectOption('name')
    await page.getByRole('button', { name: 'Add & connect' }).first().click()
    await expect(conns).toContainText('1 of 1 connected', { timeout: 30_000 })

    // The new param is added ALONGSIDE the existing one, not replacing it.
    const qRes = await request.get(`${BACKEND}/api/v1/queries/${queryId}`, { headers: hdrs })
    const cfg = (await qRes.json()).config
    expect(cfg.params.map(p => p.name).sort()).toEqual(['Period1', 'name'])
  })

  test('a dropdown filter also gets its list of choices, without writing that query either', async ({ page, request }) => {
    // Parameterizing the data query is only half a filter. The options query —
    // the one supplying what appears IN the dropdown — used to be entirely the
    // author's problem: the picker could only choose an existing query, so a
    // column nobody had built a lookup for left a correctly-wired filter with
    // an empty menu. The server now derives it from the same column.
    test.setTimeout(150_000)
    await browserLogin(page)
    const orgId = await firstOrgId(request, token)
    await page.evaluate(id => { if (id) localStorage.setItem('nubi-active-org-id', id) }, orgId)

    const queryId = await createUnfilterableQuery(request, token, orgId, { name: 'E2E options' })
    const boardId = await createBoard(request, token, orgId, queryId)
    const hdrs = { Authorization: `Bearer ${token}`, ...(orgId ? { 'X-Org-Id': orgId } : {}) }

    await page.goto(`/d/${boardId}/edit`)
    await expect(page.getByTestId('editor-shell')).toBeVisible({ timeout: 30_000 })
    await page.waitForTimeout(3000)
    await page.locator('[role="button"][data-grid-id="w_filter"]:visible').first().click()
    await page.waitForTimeout(1000)

    // Before: the dropdown is explicitly called out as having no choices.
    await expect(page.locator('[data-testid="filter-options-missing"]:visible').first())
      .toBeVisible({ timeout: 15_000 })

    await page.locator(`[data-testid="make-filterable-${queryId}"]:visible`).first().click()
    const colSelect = page.locator('select[aria-label="Column to filter on"]:visible').first()
    await expect(colSelect).toBeVisible({ timeout: 15_000 })
    await colSelect.selectOption('name')
    await page.getByRole('button', { name: 'Add & connect' }).first().click()

    const conns = page.locator('[data-testid="filter-connections"]').first()
    await expect(conns).toContainText('1 of 1 connected', { timeout: 30_000 })

    // After: the panel says where the choices came from, and the warning is gone.
    const derived = page.locator('[data-testid="filter-options-derived"]:visible').first()
    await expect(derived).toBeVisible({ timeout: 15_000 })
    await expect(derived).toContainText('distinct values')
    await expect(page.locator('[data-testid="filter-options-missing"]:visible')).toHaveCount(0)

    // The widget is bound to a REAL query that returns choices.
    await page.locator('[data-testid="editor-save-btn"]:visible').first().click()
    await page.waitForTimeout(3000)
    const bRes = await request.get(`${BACKEND}/api/v1/boards/${boardId}`, { headers: hdrs })
    const spec = (await bRes.json()).config.spec
    const optionsQueryId = spec.widgets.find(w => w.id === 'w_filter').options_query_id
    expect(optionsQueryId, 'filter widget should have an options query').toBeTruthy()

    const optRes = await request.post(`${BACKEND}/api/v1/query`, {
      headers: hdrs, data: { query_id: optionsQueryId },
    })
    expect(optRes.ok(), `options query failed: ${optRes.status()}`).toBeTruthy()
    expect((await optRes.body()).length).toBeGreaterThan(0)
  })
})

test.describe('Build an options query from the picker', () => {
  let token

  test.beforeAll(async ({ request }) => {
    token = await apiLogin(request)
  })

  test('a dropdown with no choices can have its options query built from a column', async ({ page, request }) => {
    // The path for filters the derivation cannot serve: one not yet connected
    // to any widget, or a column that is computed everywhere it appears. The
    // picker could only PICK, so this was a dead end inside the editor.
    test.setTimeout(150_000)
    await browserLogin(page)
    const orgId = await firstOrgId(request, token)
    await page.evaluate(id => { if (id) localStorage.setItem('nubi-active-org-id', id) }, orgId)

    const queryId = await createUnfilterableQuery(request, token, orgId, { name: 'E2E picker-create' })
    const boardId = await createBoard(request, token, orgId, queryId)
    const hdrs = { Authorization: `Bearer ${token}`, ...(orgId ? { 'X-Org-Id': orgId } : {}) }

    await page.goto(`/d/${boardId}/edit`)
    await expect(page.getByTestId('editor-shell')).toBeVisible({ timeout: 30_000 })
    await page.waitForTimeout(3000)
    await page.locator('[role="button"][data-grid-id="w_filter"]:visible').first().click()
    await page.waitForTimeout(1000)

    // The filter has no options query, and says so.
    await expect(page.locator('[data-testid="filter-options-missing"]:visible').first())
      .toBeVisible({ timeout: 15_000 })

    await page.locator('[data-testid="query-picker-create"]:visible').first().click()
    const builder = page.locator('[data-testid="options-query-builder"]:visible').first()
    await expect(builder).toBeVisible({ timeout: 15_000 })

    await builder.locator('select[aria-label="Table"]').selectOption('demo')
    await expect(builder.locator('select[aria-label="Column"]')).toBeVisible({ timeout: 15_000 })
    await builder.locator('select[aria-label="Column"]').selectOption('name')

    // It RUNS before it saves: the real choices are on screen first.
    const preview = page.locator('[data-testid="options-preview"]:visible').first()
    await expect(preview).toBeVisible({ timeout: 30_000 })
    await expect(preview).toContainText(/choice/)

    await page.locator('[data-testid="options-create-btn"]:visible').first().click()

    // Bound, named (not a bare UUID), and the "no choices" warning is gone.
    const derived = page.locator('[data-testid="filter-options-derived"]:visible').first()
    await expect(derived).toBeVisible({ timeout: 30_000 })
    await expect(derived).toContainText('name options')
    await expect(page.locator('[data-testid="filter-options-missing"]:visible')).toHaveCount(0)

    await page.locator('[data-testid="editor-save-btn"]:visible').first().click()
    await page.waitForTimeout(3000)
    const bRes = await request.get(`${BACKEND}/api/v1/boards/${boardId}`, { headers: hdrs })
    const spec = (await bRes.json()).config.spec
    const optionsQueryId = spec.widgets.find(w => w.id === 'w_filter').options_query_id
    expect(optionsQueryId, 'filter widget should be bound to the built query').toBeTruthy()

    // And it is a real, runnable query.
    const optRes = await request.post(`${BACKEND}/api/v1/query`, {
      headers: hdrs, data: { query_id: optionsQueryId },
    })
    expect(optRes.ok(), `options query failed: ${optRes.status()}`).toBeTruthy()
  })
})
