/**
 * Playwright drag test for Nubi dashboard editor.
 * Run from repo root: node test-drag.mjs
 */
import { chromium } from '@playwright/test';

const BASE = 'http://localhost:5173';

async function run() {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage();
  page.on('console', msg => {
    if (msg.type() === 'error') console.log('[browser error]', msg.text());
  });

  // ── Login ──────────────────────────────────────────────────────────────────
  console.log('1. Navigating to login…');
  await page.goto(`${BASE}/login`, { waitUntil: 'networkidle' });
  await page.waitForSelector('#email', { state: 'visible', timeout: 10000 });
  await page.fill('#email', 'admin@nubi.dev');
  await page.fill('#password', 'nubi-admin-2026');
  await page.click('button[type="submit"]');
  await page.waitForURL(url => !url.toString().includes('/login'), { timeout: 15000 });
  console.log('   Logged in, URL:', page.url());

  // ── Navigate to editor ────────────────────────────────────────────────────
  console.log('2. Navigating to /editor…');
  await page.goto(`${BASE}/editor`, { waitUntil: 'networkidle' });
  await page.waitForTimeout(1000);
  console.log('   Editor URL:', page.url());

  // ── Add 2 widgets ─────────────────────────────────────────────────────────
  console.log('3. Adding KPI widget…');
  const kpiBtn = page.locator('[data-testid="palette-add-kpi"]');
  await kpiBtn.waitFor({ state: 'visible', timeout: 8000 });
  await kpiBtn.click();
  await page.waitForTimeout(500);

  console.log('4. Adding Table widget…');
  const tableBtn = page.locator('[data-testid="palette-add-table"]');
  await tableBtn.click();
  await page.waitForTimeout(500);

  // ── Inspect grid items ────────────────────────────────────────────────────
  console.log('\n[DEBUG] Grid item classes and state:');
  const items = await page.locator('.react-grid-item').all();
  for (const item of items) {
    const cls = await item.evaluate(el => el.className);
    const style = await item.evaluate(el => el.style.cssText);
    console.log('  class:', cls.split(' ').slice(0, 5).join(' '));
    console.log('  style:', style.substring(0, 100));
  }

  // Check draggable class
  const hasDraggableClass = await page.evaluate(() => {
    const items = document.querySelectorAll('.react-grid-item');
    return [...items].map(el => ({
      draggable: el.classList.contains('react-draggable'),
      cssTransforms: el.classList.contains('cssTransforms'),
      transform: el.style.transform
    }));
  });
  console.log('\n[DEBUG] Draggable info:', JSON.stringify(hasDraggableClass, null, 2));

  // ── Get drag handle of first widget ──────────────────────────────────────
  const handles = page.locator('.drag-handle');
  const count = await handles.count();
  console.log(`\n5. Found ${count} drag handles`);
  if (count < 2) {
    console.log('DRAG WORKS: false — not enough widgets on canvas');
    await browser.close();
    process.exit(1);
  }

  const handle = handles.first();
  const handleBox = await handle.boundingBox();
  console.log('   Handle bounding box:', handleBox);

  // Check cursor
  const cursor = await handle.evaluate(el => window.getComputedStyle(el).cursor);
  console.log('   Handle cursor:', cursor);

  // ── Check initial transform of the grid item ──────────────────────────────
  const gridItem = page.locator('.react-grid-item').first();
  const getTransform = async () => {
    return await gridItem.evaluate(el => el.style.transform);
  };

  const transformBefore = await getTransform();
  console.log('6. Transform BEFORE drag:', transformBefore);

  // ── Perform drag ──────────────────────────────────────────────────────────
  console.log('7. Starting drag…');
  const startX = handleBox.x + handleBox.width / 2;
  const startY = handleBox.y + handleBox.height / 2;
  const endX = startX + 300;
  const endY = startY + 250;

  await page.mouse.move(startX, startY);
  await page.waitForTimeout(100);
  await page.mouse.down();
  await page.waitForTimeout(100);

  // Move in increments to give react-draggable time to respond
  for (let step = 1; step <= 15; step++) {
    const px = startX + (endX - startX) * (step / 15);
    const py = startY + (endY - startY) * (step / 15);
    await page.mouse.move(px, py, { steps: 1 });
    await page.waitForTimeout(20);
    if (step === 7) {
      const midT = await getTransform();
      console.log(`   Transform at step ${step}:`, midT);
    }
  }

  // Check transform MID-DRAG (before mouseup)
  const transformMidDrag = await getTransform();
  console.log('8. Transform MID-DRAG:', transformMidDrag);

  // Check if dragging class was added
  const isDragging = await gridItem.evaluate(el => el.classList.contains('react-draggable-dragging'));
  console.log('   Has react-draggable-dragging class:', isDragging);

  await page.mouse.up();
  await page.waitForTimeout(800);

  const transformAfter = await getTransform();
  console.log('9. Transform AFTER drag:', transformAfter);

  // Check positions of all grid items after drag
  const allTransforms = await page.evaluate(() => {
    return [...document.querySelectorAll('.react-grid-item')].map(el => ({
      class: el.className.split(' ').slice(0, 4).join(' '),
      transform: el.style.transform
    }));
  });
  console.log('\n[DEBUG] All items after drag:', JSON.stringify(allTransforms, null, 2));

  // ── Evaluate results ──────────────────────────────────────────────────────
  const midDragChanged = transformMidDrag !== transformBefore;
  const afterChanged = transformAfter !== transformBefore;

  console.log('\n── Results ──────────────────────────────────────────────');
  console.log(`   Transform changed mid-drag: ${midDragChanged}`);
  console.log(`   Transform changed after drop: ${afterChanged}`);

  // ── Resize test ───────────────────────────────────────────────────────────
  console.log('\n10. Testing resize…');
  const resizeHandle = page.locator('.react-resizable-handle').first();
  const resizeCount = await resizeHandle.count();
  console.log(`   Found ${resizeCount} resize handles`);

  let resizeWorks = false;
  if (resizeCount > 0) {
    const resizeBox = await resizeHandle.boundingBox();
    const item = page.locator('.react-grid-item').first();
    const sizeBefore = await item.boundingBox();
    console.log('   Size before resize:', sizeBefore?.width, 'x', sizeBefore?.height);

    await page.mouse.move(resizeBox.x + resizeBox.width / 2, resizeBox.y + resizeBox.height / 2);
    await page.waitForTimeout(50);
    await page.mouse.down();
    await page.waitForTimeout(50);
    await page.mouse.move(
      resizeBox.x + resizeBox.width / 2 + 120,
      resizeBox.y + resizeBox.height / 2 + 120,
      { steps: 8 }
    );
    await page.waitForTimeout(200);
    await page.mouse.up();
    await page.waitForTimeout(500);

    const sizeAfter = await item.boundingBox();
    console.log('   Size after resize:', sizeAfter?.width, 'x', sizeAfter?.height);
    resizeWorks = (sizeAfter?.width > sizeBefore?.width + 5) || (sizeAfter?.height > sizeBefore?.height + 5);
  }

  console.log(`   Resize works: ${resizeWorks}`);

  const dragWorks = midDragChanged && afterChanged;
  console.log(`\nDRAG WORKS: ${dragWorks}`);
  console.log(`RESIZE WORKS: ${resizeWorks}`);

  await browser.close();
  process.exit((dragWorks && resizeWorks) ? 0 : 1);
}

run().catch(err => {
  console.error('Test error:', err);
  process.exit(1);
});
