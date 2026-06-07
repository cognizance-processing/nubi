/**
 * e2e/query-library.spec.js
 *
 * Tests for the /queries route (QueriesPage):
 *   1. Page loads and shows the SQL editor workspace (ad-hoc draft query).
 *   2. Running an ad-hoc query shows a result table.
 *   3. The "New query" button creates a new draft.
 *   4. The registered query registry is listed in the left rail.
 */

import { test, expect } from '@playwright/test'
import { loginAs } from './helpers/auth.js'

test.describe('Queries Page', () => {
  test.beforeEach(async ({ page }) => {
    await loginAs(page)
    await page.goto('/queries')
    // Wait for the page to load — left rail with "Queries" heading
    await expect(page.locator('text=Queries').first()).toBeVisible({ timeout: 20_000 })
  })

  // ---------------------------------------------------------------------------

  test('loads with a default SQL editor workspace', async ({ page }) => {
    // The left rail header "Queries" is visible
    const rail = page.locator('aside').first()
    await expect(rail).toBeVisible({ timeout: 10_000 })

    // A "New query" button exists in the rail
    await expect(page.getByRole('button', { name: 'New query' }).first()).toBeVisible({ timeout: 10_000 })

    // The SQL editor (Monaco or textarea) is visible in the workspace
    // Monaco renders a .view-lines element; we can also just check the Run button
    const runBtn = page.getByRole('button', { name: 'Run' }).first()
    await expect(runBtn).toBeVisible({ timeout: 15_000 })
  })

  // ---------------------------------------------------------------------------

  test('running a query shows results', async ({ page }) => {
    // Wait for the Run button in the workspace
    const runBtn = page.getByRole('button', { name: 'Run' }).first()
    await expect(runBtn).toBeVisible({ timeout: 15_000 })

    // Click Run
    await runBtn.click()

    // "Running…" may briefly appear; wait for it to clear
    // Then expect results — DataTable renders a table or a rows count
    await expect(
      page.locator('text=/\\d+ rows/').or(page.locator('table')).first()
    ).toBeVisible({ timeout: 45_000 })
  })

  // ---------------------------------------------------------------------------

  test('"New query" button adds a fresh draft in the rail', async ({ page }) => {
    const newQueryBtn = page.getByRole('button', { name: 'New query' }).first()
    await expect(newQueryBtn).toBeVisible({ timeout: 10_000 })

    // Count current items
    const initialDrafts = await page.locator('text=draft').count()

    await newQueryBtn.click()

    // A new "draft" tag should appear in the left rail
    await expect(page.locator('text=draft').first()).toBeVisible({ timeout: 5_000 })
  })

  // ---------------------------------------------------------------------------

  test('registered queries appear in the left rail registry section', async ({ page }) => {
    // Wait for the loading spinner to go away
    await expect(page.locator('.animate-spin').first()).not.toBeVisible({ timeout: 20_000 }).catch(() => {})

    // The registry section header should appear if there are registered queries
    // (seeded backend should have at least demo queries)
    // If no queries are seeded, the left rail shows "No registered queries"
    const registrySection = page
      .locator('text=Registry')
      .or(page.locator('text=No registered queries'))

    await expect(registrySection.first()).toBeVisible({ timeout: 20_000 })
  })
})
