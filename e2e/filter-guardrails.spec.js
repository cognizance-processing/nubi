/**
 * e2e/filter-guardrails.spec.js
 *
 * The ways a filter used to fail silently, and what now stops each one:
 *
 * 1. A descriptive label ("The region filter") minted `the_region_filter`,
 *    which no query takes. It now adopts the board's existing `region`.
 * 2. A filter that reaches no widget looked fine and did nothing. The canvas
 *    tile and the filter panel now say "Not connected", and the panel offers
 *    the names this board's queries take — one click switches and connects.
 * 3. Renaming a filter's variable orphaned every widget bound to the old name.
 *    The rename now carries those connections (and the declaration) along.
 *
 * Assertions read the PERSISTED spec, not just the panel.
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

async function browserLogin(page) {
  await page.goto('/login')
  await page.locator('input[type="email"]').fill(ADMIN_EMAIL)
  await page.locator('input[type="password"]').fill(ADMIN_PASSWORD)
  await page.locator('button[type="submit"]').click()
  await page.waitForURL(url => !url.pathname.startsWith('/login'), { timeout: 20_000 })
}

/** Two widgets whose query takes `region`, one that takes nothing; no filter. */
async function createBoard(request, token, orgId) {
  const spec = {
    version: 1,
    title: 'E2E Filter Guardrails',
    layout: { cols: 12, row_height: 60 },
    variables: [],
    widgets: [
      { id: 'w_a', type: 'table', query_id: 'demo_by_region', config: { title: 'Regional A' },
        pos: { x: 1, y: 3, w: 4, h: 5 }, params: {} },
      { id: 'w_b', type: 'table', query_id: 'demo_by_region', config: { title: 'Regional B' },
        pos: { x: 5, y: 3, w: 4, h: 5 }, params: {} },
      { id: 'w_all', type: 'table', query_id: 'demo_all', config: { title: 'Everything' },
        pos: { x: 9, y: 3, w: 4, h: 5 }, params: {} },
    ],
  }
  const res = await request.post(`${BACKEND}/api/v1/boards`, {
    headers: { Authorization: `Bearer ${token}`, ...(orgId ? { 'X-Org-Id': orgId } : {}) },
    data: { name: 'E2E Filter Guardrails', config: { spec } },
  })
  expect(res.ok(), `Board creation failed: ${res.status()} ${await res.text()}`).toBeTruthy()
  return (await res.json()).id
}

async function savedSpec(request, token, orgId, boardId) {
  const res = await request.get(`${BACKEND}/api/v1/boards/${boardId}`, {
    headers: { Authorization: `Bearer ${token}`, ...(orgId ? { 'X-Org-Id': orgId } : {}) },
  })
  expect(res.ok()).toBeTruthy()
  return (await res.json()).config.spec
}

async function openNewFilter(page) {
  await page.locator('[data-testid="palette-add-filter"]:visible').first().click()
  await page.waitForTimeout(1200)
  const label = page.locator('[data-testid="filter-label"]:visible').first()
  const cfgBtn = page.locator('[aria-label="Configure panel"]:visible').first()
  for (let i = 0; i < 3 && !(await label.count()); i++) {
    await cfgBtn.click()
    await page.waitForTimeout(900)
  }
  await expect(label).toBeVisible({ timeout: 10_000 })
  return label
}

test.describe('Filter guardrails', () => {
  let token
  let orgId
  let boardId

  test.beforeEach(async ({ page, request }) => {
    token = await apiLogin(request)
    await browserLogin(page)
    orgId = await firstOrgId(request, token)
    await page.evaluate(id => { if (id) localStorage.setItem('nubi-active-org-id', id) }, orgId)
    boardId = await createBoard(request, token, orgId)
    await page.goto(`/d/${boardId}/edit`)
    await expect(page.getByTestId('editor-shell')).toBeVisible({ timeout: 30_000 })
    await page.waitForTimeout(3000)   // params index loads
  })

  test('a descriptive label adopts the name the board’s queries take', async ({ page }) => {
    test.setTimeout(120_000)
    const label = await openNewFilter(page)
    await label.fill('The region filter')
    await label.blur()
    await page.waitForTimeout(800)
    await expect(page.locator('[data-testid="filter-target-var"]').first()).toHaveValue('region')
    await expect(page.getByTestId('filter-connect-all')).toContainText('Connect all 2')
  })

  test('an unconnected filter is flagged, and one click switches + connects it; a rename carries the connections', async ({ page, request }) => {
    test.setTimeout(150_000)
    const label = await openNewFilter(page)
    await label.fill('Zone')
    await label.blur()
    await page.waitForTimeout(800)

    // Flagged in the panel and on the canvas tile.
    const warn = page.getByTestId('filter-not-connected')
    await expect(warn).toBeVisible()
    await expect(page.locator('[data-testid^="filter-unconnected-badge-"]:visible').first()).toBeVisible()

    // The suggestion switches the variable and connects both regional widgets.
    await page.getByTestId('filter-use-name-region').click()
    await page.waitForTimeout(800)
    await expect(page.locator('[data-testid="filter-target-var"]').first()).toHaveValue('region')
    await expect(page.getByTestId('filter-connections')).toContainText('2 of 3 connected')
    await expect(warn).toHaveCount(0)
    await expect(page.locator('[data-testid^="filter-unconnected-badge-"]:visible')).toHaveCount(0)

    // Rename the variable: connections follow, and the panel says so.
    const varInput = page.locator('[data-testid="filter-target-var"]').first()
    if (!(await varInput.isVisible())) {
      await page.locator('summary', { hasText: 'Variable name' }).first().click()
    }
    await varInput.click()
    await varInput.fill('area')
    await varInput.blur()
    await page.waitForTimeout(800)
    await expect(page.getByTestId('filter-rename-note')).toContainText('Moved 2 connections')
    await expect(page.getByTestId('filter-connections')).toContainText('2 of 3 connected')

    // Relabelling a filter that is in use changes only its display text.
    await label.fill('Zone (sales)')
    await label.blur()
    await page.waitForTimeout(600)
    await expect(varInput).toHaveValue('area')

    await page.locator('[data-testid="editor-save-btn"]:visible').first().click()
    await page.waitForTimeout(4000)

    const spec = await savedSpec(request, token, orgId, boardId)
    const byId = Object.fromEntries(spec.widgets.map(w => [w.id, w]))
    expect(byId.w_a.params).toEqual({ region: { ref: 'area' } })
    expect(byId.w_b.params).toEqual({ region: { ref: 'area' } })
    expect(byId.w_all.params ?? {}).toEqual({})
    expect(spec.widgets.find(w => w.type === 'filter').target_var).toBe('area')
    const names = (spec.variables ?? []).map(v => v.name)
    expect(names).toContain('area')
    expect(names).not.toContain('region')
  })
})
