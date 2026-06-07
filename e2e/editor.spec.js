/**
 * e2e/editor.spec.js
 *
 * Dashboard editor flow (route: /editor):
 *   1. Open /editor — loads a blank canvas.
 *   2. Add a KPI widget via the palette.
 *   3. Add a chart widget via the palette.
 *   4. Add a filter widget via the palette.
 *   5. Edit the dashboard title.
 *   6. Save → backend creates the board and the URL becomes /editor/:id.
 *   7. Reload the page → title and widgets persist.
 *
 * Stable selectors:
 *   data-testid="editor-title"        — title input in the top bar
 *   data-testid="editor-save-btn"     — Save / Create button
 *   data-testid="palette-add-kpi"     — palette button to add a KPI widget
 *   data-testid="palette-add-chart"   — palette button to add a chart widget
 *   data-testid="palette-add-filter"  — palette button to add a filter widget
 *   data-testid="editor-canvas"       — the RGL canvas container
 */

import { test, expect } from '@playwright/test'
import { loginAs } from './helpers/auth.js'

// Unique title per test run avoids collisions when the backend persists data
const BOARD_TITLE = `E2E Board ${Date.now()}`

test.describe('Dashboard Editor', () => {
  test.beforeEach(async ({ page }) => {
    await loginAs(page)
  })

  // ---------------------------------------------------------------------------

  test('opens /editor with an empty canvas', async ({ page }) => {
    await page.goto('/editor')

    // Title input exists and has the default value "New Dashboard"
    const titleInput = page.getByTestId('editor-title')
    await expect(titleInput).toBeVisible({ timeout: 20_000 })
    await expect(titleInput).toHaveValue('New Dashboard')

    // Palette KPI button is visible
    await expect(page.getByTestId('palette-add-kpi')).toBeVisible()
  })

  // ---------------------------------------------------------------------------

  test('add KPI + chart + filter, set title, save → URL becomes /editor/:id', async ({ page }) => {
    await page.goto('/editor')

    // Wait for editor to be ready
    const titleInput = page.getByTestId('editor-title')
    await expect(titleInput).toBeVisible({ timeout: 20_000 })

    // ── 1. Add KPI widget ──
    await page.getByTestId('palette-add-kpi').click()
    // Canvas should now have at least one widget
    await expect(page.getByTestId('editor-canvas')).toBeVisible()
    // Widget cards contain their type label
    await expect(page.locator('[data-testid^="widget-kpi_"]').first()).toBeVisible({ timeout: 10_000 })

    // ── 2. Add Chart widget ──
    await page.getByTestId('palette-add-chart').click()
    await expect(page.locator('[data-testid^="widget-chart_"]').first()).toBeVisible({ timeout: 10_000 })

    // ── 3. Add Filter widget ──
    await page.getByTestId('palette-add-filter').click()
    await expect(page.locator('[data-testid^="widget-filter_"]').first()).toBeVisible({ timeout: 10_000 })

    // ── 4. Edit title ──
    await titleInput.triple_click?.() // Playwright uses .fill() which is better
    await titleInput.fill(BOARD_TITLE)
    await expect(titleInput).toHaveValue(BOARD_TITLE)

    // ── 5. Save ──
    const saveBtn = page.getByTestId('editor-save-btn')
    await expect(saveBtn).toBeVisible()
    await saveBtn.click()

    // Button shows "Saving…" briefly then "Save" (id now set)
    // Wait for URL to become /editor/<uuid>
    await page.waitForURL(/\/editor\/.+/, { timeout: 20_000 })
    const boardId = page.url().split('/editor/')[1]
    expect(boardId).toBeTruthy()

    // ── 6. Reload and verify persistence ──
    await page.reload()
    await expect(page.getByTestId('editor-title')).toHaveValue(BOARD_TITLE, { timeout: 20_000 })
    // At least three widget types should still be visible
    await expect(page.locator('[data-testid^="widget-kpi_"]').first()).toBeVisible()
    await expect(page.locator('[data-testid^="widget-chart_"]').first()).toBeVisible()
    await expect(page.locator('[data-testid^="widget-filter_"]').first()).toBeVisible()
  })

  // ---------------------------------------------------------------------------

  test('Preview toggle switches to preview mode and back', async ({ page }) => {
    await page.goto('/editor')
    await expect(page.getByTestId('palette-add-kpi')).toBeVisible({ timeout: 20_000 })

    // Add one widget so preview has something to show
    await page.getByTestId('palette-add-kpi').click()

    // Click Preview
    await page.getByRole('button', { name: 'Preview' }).click()
    // Palette should no longer be visible in preview mode
    await expect(page.getByTestId('palette-add-kpi')).not.toBeVisible()

    // Click Edit to go back
    await page.getByRole('button', { name: 'Edit' }).click()
    await expect(page.getByTestId('palette-add-kpi')).toBeVisible()
  })

  // ---------------------------------------------------------------------------

  test('Ask AI panel opens and accepts a prompt', async ({ page }) => {
    await page.goto('/editor')
    await expect(page.getByTestId('editor-title')).toBeVisible({ timeout: 20_000 })

    // Click the Ask AI button in the header
    await page.getByRole('button', { name: /ask ai/i }).first().click()

    // The AI textarea / panel should appear
    const textarea = page.locator('textarea').filter({ hasText: '' }).first()
    await expect(textarea).toBeVisible({ timeout: 10_000 })

    // Type a prompt
    await textarea.fill('Show me daily active users')
    // The Generate button should become enabled
    await expect(page.getByRole('button', { name: /generate/i })).not.toBeDisabled()
  })
})

// ---------------------------------------------------------------------------
// Helper — triple-click workaround via fill
// ---------------------------------------------------------------------------
// Playwright's .fill() replaces all text; no need for triple-click.
