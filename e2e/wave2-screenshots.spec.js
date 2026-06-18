/**
 * Wave-2 visual verification: screenshot the surface switch and each surface canvas.
 */
import { test, expect } from '@playwright/test'
import { loginAs } from './helpers/auth.js'

test('wave2 surface screenshots', async ({ page }) => {
  page.on('pageerror', () => {})
  await loginAs(page)

  // Navigate to a new/blank editor
  await page.goto('/editor', { waitUntil: 'networkidle', timeout: 30_000 })

  // Wait for the editor shell to appear
  const shell = page.locator('[data-testid="editor-shell"]')
  await expect(shell).toBeVisible({ timeout: 20_000 })
  await page.screenshot({ path: '/tmp/wave2-dashboard.png' })
  console.log('Dashboard surface screenshot: /tmp/wave2-dashboard.png')

  // Verify surface-switch is present
  const sw = page.locator('[data-testid="surface-switch"]')
  await expect(sw).toBeVisible()
  await page.screenshot({ path: '/tmp/wave2-switch.png' })
  console.log('Surface switch visible: /tmp/wave2-switch.png')

  // Click Report surface
  const reportTab = page.locator('[data-testid="surface-tab-report"]')
  await expect(reportTab).toBeVisible()
  await reportTab.click()
  await page.waitForTimeout(600)
  await page.screenshot({ path: '/tmp/doccanvas.png' })
  console.log('Report/DocCanvas screenshot: /tmp/doccanvas.png')

  // Verify report canvas is present
  const reportCanvas = page.locator('[data-testid="report-canvas"]')
  await expect(reportCanvas).toBeVisible()

  // Click Presentation surface
  const slideTab = page.locator('[data-testid="surface-tab-presentation"]')
  await expect(slideTab).toBeVisible()
  await slideTab.click()
  await page.waitForTimeout(600)
  await page.screenshot({ path: '/tmp/slidecanvas.png' })
  console.log('Presentation/SlideCanvas screenshot: /tmp/slidecanvas.png')

  // Verify slide canvas is present
  const slideCanvas = page.locator('[data-testid="presentation-canvas"]')
  await expect(slideCanvas).toBeVisible()

  // Back to dashboard — verify grid editor still works
  const dashTab = page.locator('[data-testid="surface-tab-dashboard"]')
  await dashTab.click()
  await page.waitForTimeout(600)
  const editorCanvas = page.locator('[data-testid="editor-canvas"]')
  await expect(editorCanvas).toBeVisible()
  await page.screenshot({ path: '/tmp/wave2-dashboard-after.png' })
  console.log('Dashboard surface (after round-trip) screenshot: /tmp/wave2-dashboard-after.png')
})
