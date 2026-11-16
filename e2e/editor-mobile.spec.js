/**
 * e2e/editor-mobile.spec.js
 *
 * Verifies the dashboard editor is usable on mobile (390px) and tablet (820px).
 *
 * Checks per breakpoint:
 *   - No horizontal overflow (scrollWidth <= innerWidth)
 *   - Palette/config are reachable (open their drawer/sheet)
 *   - A drag still moves a widget (committed grid placement changes)
 *   - Add KPI + chart works
 */

import { test, expect } from '@playwright/test'
import { loginAs } from './helpers/auth.js'

// The editor's mobile toolbar collapses behind a hamburger; "Add widget" lives in that slide-out.
async function openMobileAdd(page) {
  await page.getByTestId('editor-hamburger').click()
  await page.getByTestId('editor-mobile-menu').getByTestId('panel-toggle-add').click()
}

// dnd-kit's pointer sensor starts a drag only after the pointer has travelled a few
// pixels, so drive the drag like a person does: press, move in steps, release.
async function dragBy(page, handle, dx, dy) {
  const box = await handle.boundingBox()
  const x = box.x + box.width / 2, y = box.y + box.height / 2
  await page.mouse.move(x, y)
  await page.mouse.down()
  await page.mouse.move(x + 8, y + 8, { steps: 4 })
  await page.mouse.move(x + dx, y + dy, { steps: 20 })
  await page.mouse.up()
}

// On tablet the panel toggles sit in the toolbar (the slide-out copy is hidden).
const panelToggle = (page, id) => page.getByTestId(`panel-toggle-${id}`).filter({ visible: true }).first()

// ---------------------------------------------------------------------------
// Mobile — 390 × 844 (iPhone 14 viewport)
// ---------------------------------------------------------------------------

test.describe('Mobile (390px)', () => {
  test.use({ viewport: { width: 390, height: 844 } })

  test.beforeEach(async ({ page }) => {
    await loginAs(page)
  })

  test('no horizontal overflow on /editor', async ({ page }) => {
    await page.goto('/editor')
    await expect(page.getByTestId('editor-title')).toBeVisible({ timeout: 20_000 })

    const overflow = await page.evaluate(() =>
      document.documentElement.scrollWidth <= window.innerWidth
    )
    expect(overflow, 'horizontal overflow detected on mobile').toBe(true)
  })

  test('palette sheet opens and adds a KPI widget', async ({ page }) => {
    await page.goto('/editor')
    await expect(page.getByTestId('editor-title')).toBeVisible({ timeout: 20_000 })

    // Open the mobile Add sheet
    await openMobileAdd(page)

    // Sheet should be visible — find the visible KPI button (sheet may coexist
    // with a hidden aside in the DOM; pick the one that's actually visible)
    const kpiBtn = page.getByTestId('palette-add-kpi').filter({ visible: true }).first()
    await expect(kpiBtn).toBeVisible({ timeout: 5_000 })
    await kpiBtn.click()

    // Widget should appear on canvas
    await expect(page.locator('[data-testid^="widget-kpi_"]').first()).toBeVisible({ timeout: 10_000 })
  })

  test('adds KPI + chart, config sheet opens when widget tapped, no overflow', async ({ page }) => {
    await page.goto('/editor')
    await expect(page.getByTestId('editor-title')).toBeVisible({ timeout: 20_000 })

    // Add KPI via mobile Add sheet
    await openMobileAdd(page)
    await expect(page.getByTestId('palette-add-kpi').filter({ visible: true }).first()).toBeVisible({ timeout: 5_000 })
    await page.getByTestId('palette-add-kpi').filter({ visible: true }).first().click()
    await expect(page.locator('[data-testid^="widget-kpi_"]').first()).toBeVisible({ timeout: 10_000 })

    // Add Chart — open Add sheet again
    await openMobileAdd(page)
    await expect(page.getByTestId('palette-add-chart').filter({ visible: true }).first()).toBeVisible({ timeout: 5_000 })
    await page.getByTestId('palette-add-chart').filter({ visible: true }).first().click()
    await expect(page.locator('[data-testid^="widget-chart_"]').first()).toBeVisible({ timeout: 10_000 })

    // Tap KPI widget → config sheet should open
    await page.locator('[data-testid^="widget-kpi_"]').first().click()
    // Config sheet should be visible (it has a close button with aria-label)
    await expect(page.locator('[aria-label="Close sheet"]')).toBeVisible({ timeout: 5_000 })

    // No horizontal overflow after interactions
    const overflow = await page.evaluate(() =>
      document.documentElement.scrollWidth <= window.innerWidth
    )
    expect(overflow, 'horizontal overflow on mobile after adding widgets').toBe(true)
  })

  test('drag moves a widget (committed grid placement changes) on mobile', async ({ page }) => {
    await page.goto('/editor')
    await expect(page.getByTestId('editor-title')).toBeVisible({ timeout: 20_000 })

    // Add a KPI widget
    await openMobileAdd(page)
    await expect(page.getByTestId('palette-add-kpi').filter({ visible: true }).first()).toBeVisible({ timeout: 5_000 })
    await page.getByTestId('palette-add-kpi').filter({ visible: true }).first().click()

    const widget = page.locator('[data-testid^="widget-kpi_"]').first()
    await expect(widget).toBeVisible({ timeout: 10_000 })

    // The new dnd-kit + CSS Grid engine wraps each widget in a `.grid-item`
    // (the element carrying `data-grid-id`) and positions it via inline
    // `gridColumn` / `gridRow` styles. A committed drag re-writes those, so we
    // assert the grid placement changed rather than a raw CSS transform.
    const getPlacement = () => widget.evaluate(el => {
      const cell = el.closest('.grid-item') ?? el.closest('[data-grid-id]')
      if (!cell) return null
      return `${cell.style.gridColumn}|${cell.style.gridRow}`
    })

    const before = await getPlacement()

    // Get the drag handle inside the widget
    const dragHandle = widget.locator('.drag-handle')
    await expect(dragHandle).toBeVisible()

    // Drag the widget by its handle to a different spot on the canvas.
    await dragBy(page, dragHandle, 0, 220)

    // Widget survives the drag and its committed grid placement changed.
    await expect(widget).toBeVisible()
    await expect.poll(getPlacement, {
      message: 'widget grid placement should change after a drag',
      timeout: 5_000,
    }).not.toBe(before)
    console.log('Mobile drag: before placement =', before, '| after =', await getPlacement())
  })
})

// ---------------------------------------------------------------------------
// Tablet — 820 × 1180
// ---------------------------------------------------------------------------

test.describe('Tablet (820px)', () => {
  test.use({ viewport: { width: 820, height: 1180 } })

  test.beforeEach(async ({ page }) => {
    await loginAs(page)
  })

  test('no horizontal overflow on /editor', async ({ page }) => {
    await page.goto('/editor')
    await expect(page.getByTestId('editor-title')).toBeVisible({ timeout: 20_000 })

    const overflow = await page.evaluate(() =>
      document.documentElement.scrollWidth <= window.innerWidth
    )
    expect(overflow, 'horizontal overflow detected on tablet').toBe(true)
  })

  test('adds KPI + chart, panel toggle works, no overflow', async ({ page }) => {
    await page.goto('/editor')
    await expect(page.getByTestId('editor-title')).toBeVisible({ timeout: 20_000 })

    // On tablet, the desktop panel segmented control is visible (hidden md:flex)
    // The panel should be open (not collapsed) — palette add button should be visible
    // after clicking 'Add' in the toolbar
    const addSegBtn = panelToggle(page, 'add')
    await expect(addSegBtn).toBeVisible({ timeout: 5_000 })
    await addSegBtn.click()

    // Now palette-add-kpi should be visible in the slide-over sidebar
    const kpiBtn = page.getByTestId('palette-add-kpi').filter({ visible: true }).first()
    await expect(kpiBtn).toBeVisible({ timeout: 8_000 })
    await kpiBtn.click()
    await expect(page.locator('[data-testid^="widget-kpi_"]').first()).toBeVisible({ timeout: 10_000 })

    // Click Configure to switch panel
    const configBtn = panelToggle(page, 'config')
    await expect(configBtn).toBeVisible()
    await configBtn.click()

    // Add chart via Add panel
    await addSegBtn.click()
    await expect(page.getByTestId('palette-add-chart').filter({ visible: true }).first()).toBeVisible({ timeout: 5_000 })
    await page.getByTestId('palette-add-chart').filter({ visible: true }).first().click()
    await expect(page.locator('[data-testid^="widget-chart_"]').first()).toBeVisible({ timeout: 10_000 })

    // No horizontal overflow
    const overflow = await page.evaluate(() =>
      document.documentElement.scrollWidth <= window.innerWidth
    )
    expect(overflow, 'horizontal overflow on tablet after interactions').toBe(true)
  })

  test('drag moves a widget on tablet', async ({ page }) => {
    await page.goto('/editor')
    await expect(page.getByTestId('editor-title')).toBeVisible({ timeout: 20_000 })

    // Add a KPI widget via toolbar panel
    const addSegBtn = panelToggle(page, 'add')
    await addSegBtn.click()
    const kpiBtn = page.getByTestId('palette-add-kpi').filter({ visible: true }).first()
    await expect(kpiBtn).toBeVisible({ timeout: 8_000 })
    await kpiBtn.click()

    const widget = page.locator('[data-testid^="widget-kpi_"]').first()
    await expect(widget).toBeVisible({ timeout: 10_000 })

    // On tablet the Add panel is a slide-over with a backdrop over the canvas;
    // close it before working on the canvas, as a person would.
    await addSegBtn.click()
    await expect(page.getByTestId('editor-right-panel')).not.toBeVisible({ timeout: 5_000 })

    const dragHandle = widget.locator('.drag-handle')
    await expect(dragHandle).toBeVisible()

    // Read the committed grid placement from the engine's `.grid-item` cell
    // (inline gridColumn/gridRow), set by GridCanvas after each commit.
    const getPlacement = () => widget.evaluate(el => {
      const cell = el.closest('.grid-item') ?? el.closest('[data-grid-id]')
      if (!cell) return null
      return `${cell.style.gridColumn}|${cell.style.gridRow}`
    })

    const before = await getPlacement()

    // Drag the widget by its handle
    await dragBy(page, dragHandle, 0, 260)

    // Widget should still be visible and its grid placement should have changed.
    await expect(widget).toBeVisible()
    await expect.poll(getPlacement, {
      message: 'widget grid placement should change after a drag',
      timeout: 5_000,
    }).not.toBe(before)
    console.log('Tablet drag: before placement =', before, '| after =', await getPlacement())
  })
})
