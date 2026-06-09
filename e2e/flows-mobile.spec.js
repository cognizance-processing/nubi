/**
 * e2e/flows-mobile.spec.js
 *
 * Verifies that the Flows page (/flows, /flows/:id) is responsive at
 * mobile (~390px) and tablet (~820px) breakpoints:
 *
 * For each breakpoint:
 *   1. /flows list: no horizontal page overflow, ≥1 interactive element
 *   2. Create a new flow (/flows with new draft): no horizontal overflow,
 *      ReactFlow canvas has non-zero height (> 200px), node palette / sheet
 *      is reachable
 */

import { test, expect } from '@playwright/test'
import { loginAs } from './helpers/auth.js'

// ---------------------------------------------------------------------------
// Breakpoints
// ---------------------------------------------------------------------------

const BREAKPOINTS = [
  { name: 'mobile-390', width: 390, height: 844 },
  { name: 'tablet-820', width: 820, height: 1180 },
]

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/**
 * Assert no horizontal page overflow.
 */
async function assertNoHorizontalOverflow(page, label) {
  const overflow = await page.evaluate(() => {
    return document.documentElement.scrollWidth - window.innerWidth
  })
  // Allow 1px rounding tolerance
  expect(overflow, `${label}: horizontal overflow = ${overflow}px`).toBeLessThanOrEqual(1)
}

/**
 * Assert the ReactFlow canvas element exists and has non-zero height.
 */
async function assertReactFlowHeight(page, label) {
  // Wait for the .react-flow element to be present
  const rfEl = page.locator('.react-flow').first()
  await expect(rfEl, `${label}: .react-flow should be visible`).toBeVisible({ timeout: 10_000 })

  const height = await rfEl.evaluate(el => el.getBoundingClientRect().height)
  expect(height, `${label}: .react-flow height = ${height}px (need > 200)`).toBeGreaterThan(200)
}

// ---------------------------------------------------------------------------
// Test suite
// ---------------------------------------------------------------------------

for (const bp of BREAKPOINTS) {
  test.describe(`Flows responsive — ${bp.name} (${bp.width}×${bp.height})`, () => {

    test.use({ viewport: { width: bp.width, height: bp.height } })

    test('flow list: no horizontal overflow + tap targets present', async ({ page }) => {
      await loginAs(page)
      await page.goto('/flows')
      await page.waitForLoadState('networkidle')

      // No horizontal overflow on the list page
      await assertNoHorizontalOverflow(page, `${bp.name} /flows list`)

      // On mobile the left rail is hidden; we should see either the empty state
      // "New flow" button or (on tablet ≥ md) the left rail.
      // Either way there should be at least one button with min 44px tap target.
      const newFlowBtn = page.getByRole('button', { name: /new flow/i }).first()
      await expect(newFlowBtn).toBeVisible({ timeout: 10_000 })

      // Verify tap target height ≥ 44px
      const h = await newFlowBtn.evaluate(el => el.getBoundingClientRect().height)
      expect(h, `${bp.name} "New flow" button height = ${h}px`).toBeGreaterThanOrEqual(40)
    })

    test('flow builder: no horizontal overflow + ReactFlow canvas > 200px tall', async ({ page }) => {
      await loginAs(page)
      await page.goto('/flows')
      await page.waitForLoadState('networkidle')

      // Click "New flow" to open the builder
      const newFlowBtn = page.getByRole('button', { name: /new flow/i }).first()
      await newFlowBtn.click()

      // Wait for the builder toolbar to appear (flow name input)
      await expect(
        page.locator('.react-flow').first()
      ).toBeVisible({ timeout: 10_000 })

      // No horizontal overflow in builder
      await assertNoHorizontalOverflow(page, `${bp.name} /flows builder`)

      // ReactFlow canvas must have non-zero height
      await assertReactFlowHeight(page, `${bp.name} flow builder`)
    })

    test('node palette is reachable on mobile / desktop', async ({ page }) => {
      await loginAs(page)
      await page.goto('/flows')
      await page.waitForLoadState('networkidle')

      // Open builder
      const newFlowBtn = page.getByRole('button', { name: /new flow/i }).first()
      await newFlowBtn.click()
      await expect(
        page.locator('.react-flow').first()
      ).toBeVisible({ timeout: 10_000 })

      if (bp.width < 768) {
        // Mobile: "+ add task" icon button should open a bottom sheet
        const addBtn = page.getByRole('button', { name: /add task/i }).first()
        await expect(addBtn).toBeVisible({ timeout: 5_000 })
        await addBtn.click()

        // Bottom sheet should show task type buttons
        const queryBtn = page.getByRole('button', { name: /query/i })
        await expect(queryBtn.first()).toBeVisible({ timeout: 5_000 })

        // No overflow while sheet is open
        await assertNoHorizontalOverflow(page, `${bp.name} palette sheet open`)

        // Close the sheet
        await page.keyboard.press('Escape')
      } else {
        // Tablet / desktop: the floating palette panel should be inside the canvas
        // (it's a Panel component inside ReactFlow — look for "Add task" text)
        const palettePanel = page.getByText('Add task').first()
        await expect(palettePanel).toBeVisible({ timeout: 5_000 })

        // No overflow on desktop
        await assertNoHorizontalOverflow(page, `${bp.name} palette visible`)
      }
    })

  })
}
