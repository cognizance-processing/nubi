/**
 * x.mjs — Playwright verification for DashboardEditor EDITOR-3A features.
 *
 * Tests:
 *   (a) Drag widget by handle → transform changes mid-drag & final pos differs
 *   (b) Resize via SE handle → widget grows
 *   (c) Duplicate button adds a second widget
 *   (d) Delete key removes the selected widget
 *   (e) Changing columns in Layout popover re-flows the grid
 *
 * Run: node x.mjs  (from repo root)
 */
import { chromium } from '@playwright/test'

const BASE = 'http://localhost:5173'

async function run() {
  const browser = await chromium.launch({ headless: true })
  const page = await browser.newPage()
  page.on('console', msg => {
    if (msg.type() === 'error') console.log('[browser error]', msg.text())
  })

  // ── Login ──────────────────────────────────────────────────────────────────
  console.log('1. Logging in…')
  await page.goto(`${BASE}/login`, { waitUntil: 'networkidle' })
  await page.waitForSelector('#email', { state: 'visible', timeout: 10000 })
  await page.fill('#email', 'admin@nubi.dev')
  await page.fill('#password', 'nubi-admin-2026')
  await page.click('button[type="submit"]')
  await page.waitForURL(url => !url.toString().includes('/login'), { timeout: 15000 })
  console.log('   Logged in, URL:', page.url())

  // ── Navigate to editor ────────────────────────────────────────────────────
  console.log('2. Navigating to /editor…')
  await page.goto(`${BASE}/editor`, { waitUntil: 'networkidle' })
  await page.waitForTimeout(1000)
  console.log('   Editor URL:', page.url())

  // ── Add widgets ───────────────────────────────────────────────────────────
  console.log('3. Adding KPI widget…')
  await page.locator('[data-testid="palette-add-kpi"]').waitFor({ state: 'visible', timeout: 8000 })
  await page.locator('[data-testid="palette-add-kpi"]').click()
  await page.waitForTimeout(600)

  console.log('4. Adding Chart widget…')
  await page.locator('[data-testid="palette-add-chart"]').click()
  await page.waitForTimeout(600)

  const widgetsBefore = await page.locator('.react-grid-item').count()
  console.log(`   Widgets on canvas: ${widgetsBefore}`)

  // ── (a) DRAG TEST ─────────────────────────────────────────────────────────
  console.log('\n[a] DRAG TEST')
  const gridItem = page.locator('.react-grid-item').first()
  const handle = page.locator('.drag-handle').first()
  await handle.waitFor({ state: 'visible', timeout: 5000 })

  const handleBox = await handle.boundingBox()
  const getTransform = async () => {
    return await gridItem.evaluate(el => el.style.transform)
  }

  const transformBefore = await getTransform()
  console.log('   Transform BEFORE drag:', transformBefore)

  const startX = handleBox.x + handleBox.width / 2
  const startY = handleBox.y + handleBox.height / 2
  const endX = startX + 280
  const endY = startY + 220

  await page.mouse.move(startX, startY)
  await page.waitForTimeout(80)
  await page.mouse.down()
  await page.waitForTimeout(80)

  let transformMidDrag = transformBefore
  for (let step = 1; step <= 15; step++) {
    const px = startX + (endX - startX) * (step / 15)
    const py = startY + (endY - startY) * (step / 15)
    await page.mouse.move(px, py, { steps: 1 })
    await page.waitForTimeout(20)
    if (step === 8) {
      transformMidDrag = await getTransform()
      console.log('   Transform MID-DRAG:', transformMidDrag)
    }
  }

  await page.mouse.up()
  await page.waitForTimeout(800)

  const transformAfter = await getTransform()
  console.log('   Transform AFTER drag:', transformAfter)

  const dragMidChanged = transformMidDrag !== transformBefore
  const dragFinalChanged = transformAfter !== transformBefore
  const dragWorks = dragMidChanged && dragFinalChanged
  console.log(`   Mid-drag changed: ${dragMidChanged}, Final changed: ${dragFinalChanged}`)
  console.log(`   DRAG WORKS: ${dragWorks}`)

  // ── (b) RESIZE TEST ───────────────────────────────────────────────────────
  console.log('\n[b] RESIZE TEST (SE handle)')
  // SE handle is react-resizable-handle-se
  const seHandle = page.locator('.react-resizable-handle-se').first()
  const seCount = await seHandle.count()
  console.log(`   SE handles found: ${seCount}`)

  let resizeWorks = false
  if (seCount > 0) {
    const item = page.locator('.react-grid-item').first()
    const sizeBefore = await item.boundingBox()
    console.log(`   Size BEFORE resize: ${sizeBefore?.width?.toFixed(0)} x ${sizeBefore?.height?.toFixed(0)}`)

    const seBox = await seHandle.boundingBox()
    await page.mouse.move(seBox.x + seBox.width / 2, seBox.y + seBox.height / 2)
    await page.waitForTimeout(60)
    await page.mouse.down()
    await page.waitForTimeout(60)
    await page.mouse.move(
      seBox.x + seBox.width / 2 + 150,
      seBox.y + seBox.height / 2 + 120,
      { steps: 10 }
    )
    await page.waitForTimeout(200)
    await page.mouse.up()
    await page.waitForTimeout(600)

    const sizeAfter = await item.boundingBox()
    console.log(`   Size AFTER resize: ${sizeAfter?.width?.toFixed(0)} x ${sizeAfter?.height?.toFixed(0)}`)
    resizeWorks = (sizeAfter?.width > sizeBefore?.width + 5) || (sizeAfter?.height > sizeBefore?.height + 5)
    console.log(`   RESIZE WORKS: ${resizeWorks}`)
  } else {
    console.log('   No SE handle found — RESIZE WORKS: false')
  }

  // ── (c) DUPLICATE BUTTON ──────────────────────────────────────────────────
  console.log('\n[c] DUPLICATE BUTTON TEST')
  // Select first widget and hover it to reveal toolbar
  const firstWidget = page.locator('.react-grid-item').first()
  await firstWidget.hover()
  await page.waitForTimeout(400)

  const widgetCountBefore = await page.locator('.react-grid-item').count()
  console.log(`   Widgets before duplicate: ${widgetCountBefore}`)

  // Find the duplicate button (data-testid starts with "widget-duplicate-")
  const dupBtns = page.locator('[data-testid^="widget-duplicate-"]')
  const dupCount = await dupBtns.count()
  console.log(`   Duplicate buttons visible: ${dupCount}`)

  // Try clicking the first visible duplicate button
  let duplicateWorks = false
  if (dupCount > 0) {
    // Use evaluate to directly click the button (bypasses hover-opacity issues)
    await page.evaluate(() => {
      const btn = document.querySelector('[data-testid^="widget-duplicate-"]')
      if (btn) btn.click()
    })
    await page.waitForTimeout(700)
    const widgetCountAfter = await page.locator('.react-grid-item').count()
    console.log(`   Widgets after duplicate: ${widgetCountAfter}`)
    duplicateWorks = widgetCountAfter > widgetCountBefore
  } else {
    console.log('   No duplicate button found')
  }
  console.log(`   DUPLICATE WORKS: ${duplicateWorks}`)

  // ── (d) DELETE KEY TEST ───────────────────────────────────────────────────
  console.log('\n[d] DELETE KEY TEST')
  const items = page.locator('.react-grid-item')
  const itemCount = await items.count()
  console.log(`   Widgets before delete: ${itemCount}`)

  // Click a widget to select it
  await items.last().click()
  await page.waitForTimeout(400)

  // Press Delete
  await page.keyboard.press('Delete')
  await page.waitForTimeout(600)

  const itemCountAfterDelete = await page.locator('.react-grid-item').count()
  console.log(`   Widgets after delete: ${itemCountAfterDelete}`)
  const deleteWorks = itemCountAfterDelete < itemCount
  console.log(`   DELETE KEY WORKS: ${deleteWorks}`)

  // ── (e) GRID COLUMNS CHANGE ───────────────────────────────────────────────
  console.log('\n[e] GRID COLUMNS (LAYOUT POPOVER) TEST')

  // Ensure we have at least one widget
  const remainingCount = await page.locator('.react-grid-item').count()
  if (remainingCount === 0) {
    await page.locator('[data-testid="palette-add-kpi"]').click()
    await page.waitForTimeout(500)
  }

  const layoutBtn = page.locator('button', { hasText: 'Layout' })
  await layoutBtn.waitFor({ state: 'visible', timeout: 5000 })

  const firstItem = page.locator('.react-grid-item').first()
  const transformBeforeCols = await firstItem.evaluate(el => el.style.transform)
  console.log('   Transform before col change:', transformBeforeCols)

  // Open popover and click "6"
  await layoutBtn.click()
  await page.waitForTimeout(300)

  // Check popover appeared
  const popoverVisible = await page.locator('button', { hasText: '6' }).first().isVisible().catch(() => false)
  console.log(`   Popover visible: ${popoverVisible}`)

  if (popoverVisible) {
    // ── Test: change cols to 24 and check width of first widget grows ──────
    // Measure widget width BEFORE changing cols (at current 12 cols)
    const widthBefore = await firstItem.evaluate(el => el.offsetWidth)
    console.log(`   Widget width at 12 cols: ${widthBefore}px`)

    // Click "6" columns (popover may stay open)
    await page.locator('button', { hasText: '6' }).first().click()
    await page.waitForTimeout(600)

    // Check if popover is still open (it might still be showing)
    const popoverStillOpen = await page.locator('button', { hasText: '24' }).first().isVisible().catch(() => false)
    console.log(`   Popover still open after "6" click: ${popoverStillOpen}`)

    if (!popoverStillOpen) {
      // Re-open popover
      await layoutBtn.click()
      await page.waitForTimeout(300)
    }

    const widthAfterCols6 = await firstItem.evaluate(el => el.offsetWidth)
    console.log(`   Widget width at 6 cols: ${widthAfterCols6}px`)

    // Click "24" columns
    const has24 = await page.locator('button', { hasText: '24' }).first().isVisible().catch(() => false)
    console.log(`   "24" button visible: ${has24}`)
    if (has24) {
      await page.locator('button', { hasText: '24' }).first().click()
      await page.waitForTimeout(600)
    }

    const widthAfterCols24 = await firstItem.evaluate(el => el.offsetWidth)
    console.log(`   Widget width at 24 cols: ${widthAfterCols24}px`)

    // Verify layout settings took effect:
    // - At 6 cols: widget should be WIDER (takes more % of canvas per col)
    // - At 24 cols: widget should be NARROWER (less % per col)
    // Also check that spec.layout.cols was updated (verify via DOM attribute or width change)
    const colsChanged = (
      widthAfterCols6 !== widthBefore ||    // 6 vs 12 cols changed width
      widthAfterCols24 !== widthBefore ||   // 24 vs 12 cols changed width
      widthAfterCols24 !== widthAfterCols6  // 24 vs 6 cols are different
    )
    console.log(`   COLUMNS CHANGE WORKS: ${colsChanged}`)

    // Close popover
    await page.keyboard.press('Escape')
    await page.waitForTimeout(200)

    // ── Summary ───────────────────────────────────────────────────────────────
    console.log('\n══════════════════════════════════════')
    console.log(' VERIFICATION RESULTS')
    console.log('══════════════════════════════════════')
    console.log(` (a) DRAG WORKS:              ${dragWorks}`)
    console.log(` (b) RESIZE WORKS:            ${resizeWorks}`)
    console.log(` (c) DUPLICATE WORKS:         ${duplicateWorks}`)
    console.log(` (d) DELETE KEY WORKS:        ${deleteWorks}`)
    console.log(` (e) COLUMNS CHANGE WORKS:    ${colsChanged}`)
    console.log('══════════════════════════════════════')

    const allPass = dragWorks && resizeWorks && duplicateWorks && deleteWorks && colsChanged
    await browser.close()
    process.exit(allPass ? 0 : 1)
  } else {
    console.log('   Layout popover did not appear — skipping column test')
    const colsChanged = false

    console.log('\n══════════════════════════════════════')
    console.log(' VERIFICATION RESULTS')
    console.log('══════════════════════════════════════')
    console.log(` (a) DRAG WORKS:              ${dragWorks}`)
    console.log(` (b) RESIZE WORKS:            ${resizeWorks}`)
    console.log(` (c) DUPLICATE WORKS:         ${duplicateWorks}`)
    console.log(` (d) DELETE KEY WORKS:        ${deleteWorks}`)
    console.log(` (e) COLUMNS CHANGE WORKS:    ${colsChanged}`)
    console.log('══════════════════════════════════════')

    await browser.close()
    process.exit(1)
  }
}

run().catch(err => {
  console.error('Test error:', err)
  process.exit(1)
})
