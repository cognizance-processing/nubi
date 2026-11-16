/**
 * e2e/dashboard-delete.spec.js
 *
 * Deleting a board from the board surface itself (/d/:id and /d/:id/edit),
 * rather than having to navigate back to the dashboards list to find the card
 * for the board you are already looking at.
 *
 * Flow:
 *   1. Create a throwaway board via /editor → save → /d/:id/edit.
 *   2. Assert the overflow menu offers Delete in EDIT mode.
 *   3. Flip to Live and delete from there → lands back on /dashboards.
 *   4. Assert the board is really gone (not in the list, and /d/:id 404s).
 *
 * Also covers the dashboards LIST's own per-card delete, because both entry
 * points now share one DeleteBoardDialog — a regression in the shared dialog
 * would otherwise only surface on whichever page happened to be tested.
 *
 * Test ids exercised:
 *   data-testid="dashboard-options-menu"  — the header's overflow trigger
 *   data-testid="dashboard-delete"        — the Delete item inside it
 *   data-testid="delete-board-dialog"     — the shared confirm dialog
 *   data-testid="delete-board-confirm"    — its red confirm button
 */

import { test, expect } from '@playwright/test'
import { loginAs } from './helpers/auth.js'

const BOARD_TITLE = `E2E delete me ${Date.now()}`

test.describe('Delete a dashboard from the board surface', () => {
  test.beforeEach(async ({ page }) => {
    await loginAs(page)
  })

  test('create → delete from the board header → gone', async ({ page }) => {
    // ── 1. Create a throwaway board ──
    await page.goto('/editor')
    const titleInput = page.getByTestId('editor-title')
    await expect(titleInput).toBeVisible({ timeout: 20_000 })
    await titleInput.fill(BOARD_TITLE)

    await page.getByTestId('editor-save-btn').click()
    await page.waitForURL(/\/d\/[^/]+\/edit/, { timeout: 20_000 })

    const boardId = page.url().match(/\/d\/([^/]+)\/edit/)[1]
    expect(boardId).toBeTruthy()

    // ── 2. Edit mode offers Delete ──
    // The menu is in the shared header, so it must survive a mode flip — this
    // is the half that used to have no delete affordance at all.
    await page.getByTestId('dashboard-options-menu').click()
    await expect(page.getByTestId('dashboard-delete')).toBeVisible()
    await page.keyboard.press('Escape')
    await page.locator('body').click({ position: { x: 5, y: 300 } })

    // ── 3. Delete from Live mode ──
    await page.getByTestId('dashboard-mode-live').click()
    await expect(page).toHaveURL(new RegExp(`/d/${boardId}$`))

    await page.getByTestId('dashboard-options-menu').click()
    await page.getByTestId('dashboard-delete').click()

    // The confirm names the board — it is the only gate in front of the DELETE.
    const dialog = page.getByTestId('delete-board-dialog')
    await expect(dialog).toBeVisible()
    await expect(dialog).toContainText(BOARD_TITLE)

    await page.getByTestId('delete-board-confirm').click()

    // ── 4. Lands back on the list, and the board is really gone ──
    await page.waitForURL(/\/dashboards$/, { timeout: 20_000 })
    await expect(page.getByText(BOARD_TITLE, { exact: false })).toHaveCount(0)

    // A deep link to the deleted board must not resurrect it.
    await page.goto(`/d/${boardId}`)
    await expect(page.getByText(/not found/i).first()).toBeVisible({ timeout: 20_000 })
  })

  test('cancelling the confirm leaves the board alone', async ({ page }) => {
    await page.goto('/editor')
    const titleInput = page.getByTestId('editor-title')
    await expect(titleInput).toBeVisible({ timeout: 20_000 })
    const keepTitle = `E2E keep me ${Date.now()}`
    await titleInput.fill(keepTitle)

    await page.getByTestId('editor-save-btn').click()
    await page.waitForURL(/\/d\/[^/]+\/edit/, { timeout: 20_000 })
    const boardId = page.url().match(/\/d\/([^/]+)\/edit/)[1]

    await page.goto(`/d/${boardId}`)
    await page.getByTestId('dashboard-options-menu').click()
    await page.getByTestId('dashboard-delete').click()
    await expect(page.getByTestId('delete-board-dialog')).toBeVisible()

    // Escape is the cheap way out and must not be a silent confirm.
    await page.keyboard.press('Escape')
    await expect(page.getByTestId('delete-board-dialog')).toHaveCount(0)
    await expect(page).toHaveURL(new RegExp(`/d/${boardId}$`))

    // Clean up so repeated local runs don't accumulate boards.
    await page.getByTestId('dashboard-options-menu').click()
    await page.getByTestId('dashboard-delete').click()
    await page.getByTestId('delete-board-confirm').click()
    await page.waitForURL(/\/dashboards$/, { timeout: 20_000 })
  })

  test('the dashboards list card still deletes via the shared dialog', async ({ page }) => {
    await page.goto('/editor')
    const titleInput = page.getByTestId('editor-title')
    await expect(titleInput).toBeVisible({ timeout: 20_000 })
    const listTitle = `E2E list delete ${Date.now()}`
    await titleInput.fill(listTitle)

    await page.getByTestId('editor-save-btn').click()
    await page.waitForURL(/\/d\/[^/]+\/edit/, { timeout: 20_000 })

    await page.goto('/dashboards')
    const card = page.locator('article').filter({ hasText: listTitle }).first()
    await expect(card).toBeVisible({ timeout: 20_000 })

    await card.getByRole('button', { name: /board options/i }).click()
    await page.getByRole('menuitem', { name: /^Delete$/ }).click()

    await expect(page.getByTestId('delete-board-dialog')).toContainText(listTitle)
    await page.getByTestId('delete-board-confirm').click()

    await expect(page.getByText(listTitle, { exact: false })).toHaveCount(0, { timeout: 20_000 })
  })
})
