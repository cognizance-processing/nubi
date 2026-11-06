/**
 * e2e/editor-toolbar-tooltips.spec.js
 *
 * The editor toolbar scrolls horizontally on narrow screens, and its segmented
 * groups hide overflow, so anything positioned inside them can be clipped while
 * still being in the DOM. toBeVisible() can't see that, so these tests hit-test
 * the painted point instead.
 */

import { test, expect } from '@playwright/test'
import { loginAs } from './helpers/auth.js'

/** True when the element is what the browser actually paints at its own centre. */
function isPainted(locator) {
  return locator.evaluate((el) => {
    const r = el.getBoundingClientRect()
    const hit = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2)
    return !!hit && el.contains(hit)
  })
}

const CONTROLS = [
  ['button[aria-label="Undo"]', 'Undo'],
  ['button[aria-label="Redo"]', 'Redo'],
  ['button[aria-label="Tablet"]', 'Tablet'],
  ['[data-testid="editor-zoom-menu"]', 'Canvas zoom'],
  ['[data-testid="panel-toggle-config"]', 'Configure widget'],
  ['[data-testid="panel-toggle-board"]', 'Dashboard settings'],
  ['[data-testid="panel-toggle-filters"]', 'Filters'],
  ['button[aria-label="Code view (dashboard.json)"]', 'Code'],
  ['button[aria-label="Preview dashboard"]', 'Preview'],
  ['button[aria-label="Export & share dashboard"]', 'Export & share'],
  ['[data-testid="editor-save-btn"]', 'Create dashboard'],
]

test.describe('editor toolbar tooltips', () => {
  test.beforeEach(async ({ page }) => {
    await loginAs(page)
    await page.goto('/editor')
    await expect(page.getByTestId('editor-title')).toBeVisible({ timeout: 20_000 })
  })

  for (const [selector, text] of CONTROLS) {
    test(`hovering shows "${text}", unclipped`, async ({ page }) => {
      await page.locator(selector).hover({ force: true })
      const tip = page.getByRole('tooltip')
      await expect(tip).toContainText(text)
      expect(await isPainted(tip)).toBe(true)
    })
  }

  test('keyboard focus opens the tooltip and Escape dismisses it', async ({ page }) => {
    const tablet = page.locator('button[aria-label="Tablet"]')
    await tablet.focus()
    await expect(page.getByRole('tooltip')).toContainText('Tablet')
    await page.keyboard.press('Escape')
    await expect(page.getByRole('tooltip')).toHaveCount(0)
    await expect(tablet).toBeFocused()
  })

  test('segmented buttons keep their pressed styling inside a tooltip wrapper', async ({ page }) => {
    const tablet = page.locator('button[aria-label="Tablet"]')
    await tablet.click()
    await expect(tablet).toHaveAttribute('aria-pressed', 'true')
    const ink = await page.evaluate(() => {
      const probe = document.createElement('div')
      probe.style.background = 'var(--ink)'
      document.body.appendChild(probe)
      const value = getComputedStyle(probe).backgroundColor
      probe.remove()
      return value
    })
    await expect(tablet).toHaveCSS('background-color', ink)
  })

  test('zoom popover opens on click, painted, with no tooltip over it', async ({ page }) => {
    const trigger = page.getByTestId('editor-zoom-menu')
    await trigger.hover()
    await expect(page.getByRole('tooltip')).toBeVisible()
    await trigger.click()
    await expect(trigger).toHaveAttribute('aria-expanded', 'true')
    await expect(trigger).toBeFocused()
    await expect(page.getByRole('tooltip')).toHaveCount(0)
    expect(await isPainted(page.locator('.nubi-dropdown'))).toBe(true)
  })

  test('share popover opens on click, painted, on a toolbar narrow enough to scroll', async ({ page }) => {
    await page.setViewportSize({ width: 1024, height: 800 })
    const trigger = page.locator('button[aria-label="Export & share dashboard"]')
    await trigger.scrollIntoViewIfNeeded()
    await trigger.click()
    await expect(trigger).toHaveAttribute('aria-expanded', 'true')
    const panel = page.getByRole('button', { name: 'Export as PNG' })
    expect(await isPainted(panel)).toBe(true)
  })
})
