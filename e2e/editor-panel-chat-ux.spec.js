/**
 * e2e/editor-panel-chat-ux.spec.js
 *
 * Verifies the refactored editor panel + chat UX:
 *
 * (a) Desktop /editor — topbar shows Add/Configure/Dashboard/Chat ICON buttons;
 *     clicking Chat opens chat in the RHS sidebar; global MessageSquare chat
 *     button is NOT present; only ONE right sidebar visible.
 *
 * (b) Non-editor page (/home) — global chat MessageSquare button IS present
 *     and opens the global chat panel.
 *
 * (c) Mobile 390px and tablet 820px — four icon toggles are reachable in the
 *     topbar and open their panel/sheet; no horizontal overflow.
 */

import { test, expect } from '@playwright/test'
import { loginAs } from './helpers/auth.js'

// Shared login helper that navigates straight to the editor
async function openEditor(page) {
  await loginAs(page)
  await page.goto('/editor')
  await expect(page.getByTestId('editor-title')).toBeVisible({ timeout: 30_000 })
}

// ────────────────────────────────────────────────────────────────────────────
// (a) Desktop: editor panel icons + chat in RHS sidebar
// ────────────────────────────────────────────────────────────────────────────

test.describe('Desktop /editor — panel icon toggles + single chat', () => {
  test.use({ viewport: { width: 1280, height: 800 } })

  test('shows Add/Configure/Dashboard/Chat icon buttons in the topbar', async ({ page }) => {
    await openEditor(page)

    const toggles = page.getByTestId('editor-panel-toggles')
    await expect(toggles).toBeVisible()

    // Configure / Dashboard / Chat toggles present. At lg+ "Add" is hidden on purpose:
    // the permanent widget-library rail on the left covers it.
    await expect(page.getByTestId('panel-toggle-add')).not.toBeVisible()
    await expect(page.getByTestId('panel-toggle-config')).toBeVisible()
    await expect(page.getByTestId('panel-toggle-board')).toBeVisible()
    await expect(page.getByTestId('panel-toggle-chat')).toBeVisible()
  })

  test('global chat button is NOT present in the editor', async ({ page }) => {
    await openEditor(page)
    await expect(page.getByTestId('rail-toggle-chat')).not.toBeVisible()
  })

  test('clicking Chat opens chat in the RHS sidebar (no global chat panel)', async ({ page }) => {
    await openEditor(page)
    await page.getByTestId('panel-toggle-chat').click()

    // The global AI chat panel aside should NOT be visible (page owns chat)
    const globalChatAside = page.locator('aside[aria-label="AI chat panel"]')
    await expect(globalChatAside).not.toBeVisible()

    // The editor's own right sidebar should be open
    // Verify by checking the "Chat" panel title text is visible in the sidebar header
    await expect(page.getByText('Chat', { exact: true }).first()).toBeVisible({ timeout: 5_000 })
  })

  test('editor panel icons are icon-only (no visible text labels)', async ({ page }) => {
    await openEditor(page)
    // The toggle buttons should not have visible text — only icons (SVG)
    const configBtn = page.getByTestId('panel-toggle-config')
    await expect(configBtn).toBeVisible()
    const btnText = await configBtn.textContent()
    expect((btnText ?? '').trim()).toBe('')
  })

  test('clicking active panel toggle collapses the sidebar', async ({ page }) => {
    await openEditor(page)

    // The Configure panel is open by default; the editor's right panel is visible
    const aside = page.getByTestId('editor-right-panel')
    await expect(aside).toBeVisible()

    // Click Configure toggle (active) → collapses
    await page.getByTestId('panel-toggle-config').click()
    await expect(aside).not.toBeVisible({ timeout: 3_000 })
  })

  test('clicking Configure toggle re-opens the sidebar after collapsing', async ({ page }) => {
    await openEditor(page)

    // Collapse
    await page.getByTestId('panel-toggle-config').click()
    await page.waitForTimeout(200)

    // Re-open Configure
    await page.getByTestId('panel-toggle-config').click()
    await expect(page.getByTestId('editor-right-panel')).toBeVisible({ timeout: 5_000 })
  })

  test('clicking Dashboard panel toggle shows Dashboard settings', async ({ page }) => {
    await openEditor(page)
    await page.getByTestId('panel-toggle-board').click()
    await expect(page.getByText('Dashboard').first()).toBeVisible({ timeout: 5_000 })
  })
})

// ────────────────────────────────────────────────────────────────────────────
// (b) Non-editor page (/home) — global chat button present and works
// ────────────────────────────────────────────────────────────────────────────

test.describe('Non-editor page (/home) — global chat is available', () => {
  test.use({ viewport: { width: 1280, height: 800 } })

  test('global chat MessageSquare button IS present on /home', async ({ page }) => {
    await loginAs(page)
    await page.goto('/home')
    await page.waitForLoadState('networkidle')
    await expect(page.getByTestId('rail-toggle-chat')).toBeVisible({ timeout: 10_000 })
  })

  test('global chat button opens the global chat panel', async ({ page }) => {
    await loginAs(page)
    await page.goto('/home')
    await page.waitForLoadState('networkidle')
    await page.getByTestId('rail-toggle-chat').click()
    const globalChatAside = page.locator('aside[aria-label="AI chat panel"]')
    await expect(globalChatAside).toBeVisible({ timeout: 5_000 })
  })
})

// ────────────────────────────────────────────────────────────────────────────
// (c) Mobile 390px — topbar panel icons accessible, no overflow
// ────────────────────────────────────────────────────────────────────────────

test.describe('Mobile 390px — editor panel icon toggles', () => {
  test.use({ viewport: { width: 390, height: 844 } })

  // Below md the toolbar cluster (device switcher, zoom, panel toggles) collapses
  // behind a hamburger that opens a slide-out menu. The four panel toggles live
  // INSIDE that slide-out on mobile.
  test('panel toggles are reachable via the hamburger slide-out', async ({ page }) => {
    await openEditor(page)
    // Toggles are NOT in the topbar directly on mobile…
    await expect(page.getByTestId('editor-hamburger')).toBeVisible()
    // …open the slide-out menu, then they're all visible.
    await page.getByTestId('editor-hamburger').click()
    await expect(page.getByTestId('editor-mobile-menu')).toBeVisible()
    await expect(page.getByTestId('editor-mobile-menu').getByTestId('panel-toggle-add')).toBeVisible()
    await expect(page.getByTestId('editor-mobile-menu').getByTestId('panel-toggle-config')).toBeVisible()
    await expect(page.getByTestId('editor-mobile-menu').getByTestId('panel-toggle-board')).toBeVisible()
    await expect(page.getByTestId('editor-mobile-menu').getByTestId('panel-toggle-chat')).toBeVisible()
  })

  test('no horizontal overflow on the page', async ({ page }) => {
    await openEditor(page)
    const bodyScrollWidth = await page.evaluate(() => document.body.scrollWidth)
    const innerWidth = await page.evaluate(() => window.innerWidth)
    expect(bodyScrollWidth).toBeLessThanOrEqual(innerWidth + 5) // 5px tolerance
  })

  test('clicking Add (via hamburger) opens bottom sheet on mobile', async ({ page }) => {
    await openEditor(page)
    await page.getByTestId('editor-hamburger').click()
    await page.getByTestId('editor-mobile-menu').getByTestId('panel-toggle-add').click()
    // Mobile bottom sheet (flex variant) becomes visible
    const sheet = page.locator('.md\\:hidden.fixed.inset-0.z-40.flex')
    await expect(sheet).toBeVisible({ timeout: 5_000 })
  })

  test('clicking Chat (via hamburger) opens chat bottom sheet on mobile', async ({ page }) => {
    await openEditor(page)
    await page.getByTestId('editor-hamburger').click()
    await page.getByTestId('editor-mobile-menu').getByTestId('panel-toggle-chat').click()
    const sheet = page.locator('.md\\:hidden.fixed.inset-0.z-40.flex')
    await expect(sheet).toBeVisible({ timeout: 5_000 })
  })

  test('global chat button NOT present in editor on mobile', async ({ page }) => {
    await openEditor(page)
    await expect(page.getByTestId('rail-toggle-chat')).not.toBeVisible()
  })
})

// ────────────────────────────────────────────────────────────────────────────
// (c) Tablet 820px — topbar panel icons accessible, no overflow
// ────────────────────────────────────────────────────────────────────────────

test.describe('Tablet 820px — editor panel icon toggles', () => {
  test.use({ viewport: { width: 820, height: 1180 } })

  test('all four panel toggles are visible in the topbar', async ({ page }) => {
    await openEditor(page)
    await expect(page.getByTestId('panel-toggle-add')).toBeVisible()
    await expect(page.getByTestId('panel-toggle-config')).toBeVisible()
    await expect(page.getByTestId('panel-toggle-board')).toBeVisible()
    await expect(page.getByTestId('panel-toggle-chat')).toBeVisible()
  })

  test('no horizontal overflow on the page', async ({ page }) => {
    await openEditor(page)
    const bodyScrollWidth = await page.evaluate(() => document.body.scrollWidth)
    const innerWidth = await page.evaluate(() => window.innerWidth)
    expect(bodyScrollWidth).toBeLessThanOrEqual(innerWidth + 5)
  })

  test('clicking Add opens slide-over RHS sidebar on tablet', async ({ page }) => {
    await openEditor(page)
    // Below lg the right panel starts collapsed: Add opens it as a slide-over, Add again closes it
    await page.getByTestId('panel-toggle-add').click()
    await expect(page.getByTestId('editor-right-panel')).toBeVisible({ timeout: 5_000 })
    await page.getByTestId('panel-toggle-add').click()
    await expect(page.getByTestId('editor-right-panel')).not.toBeVisible({ timeout: 5_000 })
  })

  test('global chat button NOT present in editor on tablet', async ({ page }) => {
    await openEditor(page)
    await expect(page.getByTestId('rail-toggle-chat')).not.toBeVisible()
  })
})
