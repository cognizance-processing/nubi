/**
 * Wave-2 visual verification: screenshot the surface switch and each surface canvas.
 */
import { test } from '@playwright/test'

const BASE = process.env.E2E_BASE_URL ?? 'http://localhost:5173'

test('wave2 surface screenshots', async ({ page }) => {
  page.on('pageerror', () => {})

  await page.goto(BASE, { waitUntil: 'networkidle', timeout: 30_000 })
  await page.screenshot({ path: '/tmp/wave2-home.png' })

  let switchExists = await page.locator('[data-testid="surface-switch"]').count()

  if (!switchExists) {
    const links = await page.locator('a').all()
    let editorUrl = null
    for (const link of links) {
      const href = await link.getAttribute('href')
      if (href && (href.includes('/dashboard') || href.includes('/editor') || href.includes('/board'))) {
        editorUrl = href
        break
      }
    }
    if (editorUrl) {
      const full = editorUrl.startsWith('http') ? editorUrl : BASE + editorUrl
      await page.goto(full, { waitUntil: 'networkidle', timeout: 30_000 })
      switchExists = await page.locator('[data-testid="surface-switch"]').count()
    }
  }

  if (await page.locator('[data-testid="surface-switch"]').count() > 0) {
    await page.screenshot({ path: '/tmp/wave2-switch.png' })
    const reportTab = page.locator('[data-testid="surface-tab-report"]')
    if (await reportTab.count() > 0) {
      await reportTab.click()
      await page.waitForTimeout(800)
      await page.screenshot({ path: '/tmp/doccanvas.png' })
    }
    const slideTab = page.locator('[data-testid="surface-tab-presentation"]')
    if (await slideTab.count() > 0) {
      await slideTab.click()
      await page.waitForTimeout(800)
      await page.screenshot({ path: '/tmp/slidecanvas.png' })
    }
    const dashTab = page.locator('[data-testid="surface-tab-dashboard"]')
    if (await dashTab.count() > 0) {
      await dashTab.click()
      await page.waitForTimeout(800)
      await page.screenshot({ path: '/tmp/wave2-dashboard.png' })
    }
  } else {
    const title = await page.title()
    console.log('surface-switch not found; page title:', title)
    await page.screenshot({ path: '/tmp/wave2-nosurface.png' })
  }
})
