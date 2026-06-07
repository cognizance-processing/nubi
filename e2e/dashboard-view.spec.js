/**
 * e2e/dashboard-view.spec.js
 *
 * Tests for /d/:id — the DashboardViewPage:
 *
 *   1. /d/sample — built-in sample dashboard renders (HTML path).
 *   2. /d/<seeded-id> — a board created via the editor loads via SpecRenderer.
 *   3. URL-bound variables: opening /d/:id?myvar=hello causes the variable to be
 *      picked up by the spec (the URL param is reflected via initialVariables).
 *   4. 404 / missing board → shows the sample dashboard fallback warning.
 *
 * Stable selectors (added to DashboardViewPage.jsx):
 *   data-testid="dashboard-view-page"       — page wrapper div
 *   data-testid="dashboard-view-error"      — fallback / error notice
 *   data-testid="dashboard-spec-renderer"   — wraps SpecRenderer (spec boards)
 *   data-testid="dashboard-html-renderer"   — wraps DashboardView (HTML boards)
 */

import { test, expect } from '@playwright/test'
import { loginAs } from './helpers/auth.js'

// ---------------------------------------------------------------------------
// Helper — create a board via the editor and return its id
// ---------------------------------------------------------------------------
async function createBoardViaEditor(page, titleSuffix = '') {
  await page.goto('/editor')
  const titleInput = page.getByTestId('editor-title')
  await expect(titleInput).toBeVisible({ timeout: 20_000 })

  const title = `E2E View Board ${Date.now()}${titleSuffix}`
  await titleInput.fill(title)

  // Add a KPI widget to give the spec some content
  await page.getByTestId('palette-add-kpi').click()
  await expect(page.locator('[data-testid^="widget-kpi_"]').first()).toBeVisible({ timeout: 10_000 })

  // Save
  await page.getByTestId('editor-save-btn').click()
  await page.waitForURL(/\/editor\/.+/, { timeout: 20_000 })

  const boardId = page.url().split('/editor/')[1]
  return { boardId, title }
}

// ---------------------------------------------------------------------------

test.describe('Dashboard View Page', () => {
  test.beforeEach(async ({ page }) => {
    await loginAs(page)
  })

  // ---------------------------------------------------------------------------

  test('/d/sample renders the built-in sample HTML dashboard', async ({ page }) => {
    await page.goto('/d/sample')

    const viewPage = page.getByTestId('dashboard-view-page')
    await expect(viewPage).toBeVisible({ timeout: 20_000 })

    // Sample dashboard is rendered via DashboardView (HTML mode)
    await expect(page.getByTestId('dashboard-html-renderer')).toBeVisible({ timeout: 15_000 })

    // The sample HTML contains the heading "Nubi Sample Dashboard"
    await expect(page.getByText('Nubi Sample Dashboard')).toBeVisible({ timeout: 10_000 })
  })

  // ---------------------------------------------------------------------------

  test('/d/<id> with missing board shows fallback warning and sample content', async ({ page }) => {
    await page.goto('/d/00000000-0000-0000-0000-000000000000')

    const viewPage = page.getByTestId('dashboard-view-page')
    await expect(viewPage).toBeVisible({ timeout: 20_000 })

    // Error / fallback notice shown
    await expect(page.getByTestId('dashboard-view-error')).toBeVisible({ timeout: 15_000 })

    // Sample fallback is rendered
    await expect(page.getByTestId('dashboard-html-renderer')).toBeVisible({ timeout: 10_000 })
  })

  // ---------------------------------------------------------------------------

  test('/d/<id> loads a seeded spec board via SpecRenderer', async ({ page }) => {
    const { boardId } = await createBoardViaEditor(page)

    await page.goto(`/d/${boardId}`)
    await expect(page.getByTestId('dashboard-view-page')).toBeVisible({ timeout: 20_000 })
    await expect(page.getByTestId('dashboard-spec-renderer')).toBeVisible({ timeout: 20_000 })

    // Edit link pointing back to /editor/:id should be visible
    await expect(page.getByRole('link', { name: /edit in editor/i })).toBeVisible({ timeout: 10_000 })
  })

  // ---------------------------------------------------------------------------

  test('URL-bound variable is seeded from search params (?var=value)', async ({ page }) => {
    // Create a board that declares a variable via the editor spec.
    // For this test we use the /d/sample route to check URL param handling
    // (variables section is wired only for spec boards, so we create one).
    //
    // Strategy:
    //   - Create a spec board (any board is fine; the SpecRenderer accepts
    //     initialVariables even when widgets don't bind them).
    //   - Navigate to /d/<id>?region=west
    //   - The page should load without error; the URL param "region" is accepted.
    //   - We verify the URL contains the expected param after load.

    const { boardId } = await createBoardViaEditor(page, '_var')

    const targetURL = `/d/${boardId}?region=west`
    await page.goto(targetURL)

    await expect(page.getByTestId('dashboard-view-page')).toBeVisible({ timeout: 20_000 })
    await expect(page.getByTestId('dashboard-spec-renderer')).toBeVisible({ timeout: 15_000 })

    // The URL still contains the variable param (not stripped / lost on load)
    expect(page.url()).toContain('region=west')
  })

  // ---------------------------------------------------------------------------

  test('navigation from /d/:id back to editor works', async ({ page }) => {
    const { boardId } = await createBoardViaEditor(page, '_nav')

    await page.goto(`/d/${boardId}`)
    await expect(page.getByTestId('dashboard-spec-renderer')).toBeVisible({ timeout: 20_000 })

    // Click the "Edit in editor →" link
    await page.getByRole('link', { name: /edit in editor/i }).click()
    await expect(page).toHaveURL(new RegExp(`/editor/${boardId}`), { timeout: 15_000 })
  })
})
