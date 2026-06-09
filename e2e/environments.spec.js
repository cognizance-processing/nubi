/**
 * e2e/environments.spec.js
 *
 * Project environments + resource versioning (migration 0029):
 *   1. /flows — the run-environment selector lists the project's 'dev' and
 *      'prod' environments, sourced from GET /projects/{id}/environments
 *      (not the legacy localStorage fallback).
 *   2. /flows — with a saved flow open, the toolbar exposes the Checkpoint
 *      and History (version history) controls.
 *   3. /dashboards — a board card's overflow menu exposes History
 *      (switches to the seeded Demo project, which carries boards).
 *
 * Notes:
 *   - The flows builder toolbar (env selector, Validate, Checkpoint, History)
 *     only renders once a flow is open, so tests click a saved flow from the
 *     right rail first (rows show a truncated mono id like "a1b2c3d4…").
 *
 * Conventions follow e2e/helpers/auth.js + e2e/admin.spec.js.
 */

import { test, expect } from '@playwright/test'
import { loginAs } from './helpers/auth.js'

/** Locator for saved-flow rows in the flows right rail. */
function savedFlowItems(page) {
  return page.locator('aside button').filter({ hasText: /[0-9a-f]{8}…/ })
}

/** Open the first saved flow (skips the test when none exist). */
async function openFirstSavedFlow(page) {
  const items = savedFlowItems(page)
  await page.waitForTimeout(500) // let the rail finish its initial load
  const count = await items.count()
  test.skip(count === 0, 'No saved flows in this workspace')
  await items.first().click()
}

// ---------------------------------------------------------------------------
// 1. Flows: env selector is API-backed and lists dev + prod
// ---------------------------------------------------------------------------

test('flows env selector lists dev and prod from the environments API', async ({ page }) => {
  await loginAs(page)

  // The env list must come from the API, not the localStorage fallback —
  // capture the environments fetch triggered by the flows page.
  const envResponsePromise = page.waitForResponse(
    res => res.request().method() === 'GET'
      && /\/projects\/[^/]+\/environments/.test(res.url()),
    { timeout: 20_000 },
  )

  await page.goto('/flows')

  const envResponse = await envResponsePromise
  expect(envResponse.ok()).toBeTruthy()
  const envs = await envResponse.json()
  const keys = (Array.isArray(envs) ? envs : envs?.environments ?? []).map(e => e.key)
  expect(keys).toContain('dev')
  expect(keys).toContain('prod')

  // The builder toolbar (and its env selector) renders once a flow is open.
  await openFirstSavedFlow(page)

  const envButton = page.getByRole('button', { name: 'Run environment' })
  await expect(envButton).toBeVisible({ timeout: 20_000 })
  await envButton.click()
  await expect(page.getByRole('option', { name: 'dev', exact: true })).toBeVisible()
  await expect(page.getByRole('option', { name: 'prod', exact: true })).toBeVisible()
  await page.keyboard.press('Escape')
})

// ---------------------------------------------------------------------------
// 2. Flows: saved flow exposes Checkpoint + History toolbar controls
// ---------------------------------------------------------------------------

test('saved flow exposes Checkpoint and History toolbar controls', async ({ page }) => {
  await loginAs(page)
  await page.goto('/flows')

  await openFirstSavedFlow(page)

  // Toolbar is present once a flow is open.
  await expect(page.getByTitle('Validate flow')).toBeVisible({ timeout: 20_000 })

  // Checkpoint + History appear for saved flows (canRun).
  // (title attributes are the stable hooks; the label spans hide below lg.)
  await expect(
    page.getByTitle('Checkpoint — snapshot the current draft as a new version'),
  ).toBeVisible({ timeout: 20_000 })
  await expect(page.getByTitle('Version history')).toBeVisible()
})

// ---------------------------------------------------------------------------
// 3. Dashboards: board card overflow menu exposes History
// ---------------------------------------------------------------------------

test('board card overflow menu exposes History', async ({ page }) => {
  await loginAs(page)
  await page.goto('/dashboards')
  await expect(page.getByRole('heading', { name: 'Dashboards' })).toBeVisible({ timeout: 20_000 })

  // Boards are seeded in the Demo project — switch to it when the active
  // project has no boards.
  let menuButtons = page.getByRole('button', { name: 'Board options' })
  if (await menuButtons.count() === 0) {
    const switcher = page.getByRole('button', { name: 'Switch project' })
    await switcher.click()
    const demo = page.getByRole('button', { name: /Demo/ }).first()
    test.skip(await demo.count() === 0, 'No Demo project to source boards from')
    await demo.click()
    menuButtons = page.getByRole('button', { name: 'Board options' })
  }

  const visible = await menuButtons.first()
    .waitFor({ state: 'visible', timeout: 20_000 })
    .then(() => true, () => false)
  test.skip(!visible, 'No boards in any reachable project')

  await menuButtons.first().click()
  await expect(page.getByRole('button', { name: 'History', exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Checkpoint', exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Promote', exact: true })).toBeVisible()
})
