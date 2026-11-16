/**
 * e2e/editor-toolbar-tooltips.spec.js
 *
 * The dashboard editor toolbar is a horizontally scrolling row
 * (`overflow-x-auto`) portaled into the app top bar. Anything positioned
 * `absolute` inside it is clipped, so tooltips and the zoom / export menus are
 * portaled to <body> with position: fixed. These specs guard that:
 *
 *   - every toolbar tooltip is on screen AND painted — `elementFromPoint` at the
 *     bubble's centre must land inside it (catches clipped-but-in-DOM bubbles);
 *   - Esc dismisses, keyboard focus opens instantly, a neighbour opens warm;
 *   - a focus-opened bubble follows its trigger while the toolbar scrolls;
 *   - Tooltip-wrapped segments keep the ink "pressed" fill and dividers;
 *   - zoom and export/share menus open fully painted after the toolbar has
 *     scrolled, with no tooltip left over them, and survive clicks inside.
 *
 * Read-only: opens /editor and never saves.
 *
 * Widths: at 640px (<md) the device switcher, zoom and panel toggles live
 * behind the hamburger, so those are covered at 1024px.
 */

import { test, expect } from '@playwright/test'
import { loginAs } from './helpers/auth.js'

async function openEditor(page, width) {
  await page.setViewportSize({ width, height: 800 })
  await loginAs(page)
  await page.goto('/editor')
  await expect(page.getByTestId('editor-title')).toBeVisible({ timeout: 20_000 })
}

// The scrolling toolbar row (parent of the title input)
const toolbar = (page) => page.getByTestId('editor-title').locator('xpath=..')

/** On screen and painted: the topmost element at its centre is inside it. */
async function paintCheck(locator) {
  return locator.evaluate((el) => {
    const r = el.getBoundingClientRect()
    const at = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2)
    return {
      inViewport: r.width > 0 && r.left >= 0 && r.top >= 0 && r.right <= innerWidth && r.bottom <= innerHeight,
      painted: !!at && el.contains(at),
    }
  })
}

async function expectPaintedTooltip(page, text) {
  const tip = page.getByRole('tooltip')
  await expect(tip).toHaveCount(1)
  await expect(tip).toHaveText(new RegExp(`^${text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}`))
  expect(await paintCheck(tip), `tooltip "${text}"`).toEqual({ inViewport: true, painted: true })
}

async function moveAway(page) {
  const vp = page.viewportSize()
  await page.mouse.move(vp.width / 2, vp.height - 10)
  await expect(page.getByRole('tooltip')).toHaveCount(0)
}

async function hoverExpect(page, trigger, text) {
  await trigger.hover()
  await expectPaintedTooltip(page, text)
  await moveAway(page)
}

const tipLeft = (page) => page.getByRole('tooltip').evaluate((el) => el.getBoundingClientRect().left)

test.describe('Editor toolbar tooltips & menus', () => {
  test('640px: toolbar tooltips are painted, Esc / focus / warm-open work', async ({ page }) => {
    await openEditor(page, 640)

    const controls = [
      [page.getByRole('button', { name: 'Undo', exact: true }), 'Undo'],
      [page.getByRole('button', { name: 'Redo', exact: true }), 'Redo'],
      [page.getByTestId('editor-hamburger'), 'Editor menu'],
      [page.getByRole('button', { name: 'Canvas / grid view' }), 'Canvas'],
      [page.getByRole('button', { name: 'Code view (dashboard.json)' }), 'Code'],
      [page.getByRole('button', { name: 'Preview dashboard' }), 'Preview dashboard'],
      [page.getByTestId('export-share-btn'), 'Export & share'],
      [page.getByTestId('editor-save-btn'), 'Create dashboard'],
    ]
    for (const [trigger, text] of controls) await hoverExpect(page, trigger, text)

    // Esc dismisses
    const preview = page.getByRole('button', { name: 'Preview dashboard' })
    await preview.hover()
    await expectPaintedTooltip(page, 'Preview dashboard')
    await page.keyboard.press('Escape')
    await expect(page.getByRole('tooltip')).toHaveCount(0)
    await moveAway(page)

    // Keyboard focus opens instantly (hover waits 300ms)
    await preview.focus()
    await expect(page.getByRole('tooltip')).toHaveText('Preview dashboard', { timeout: 150 })
    await page.getByTestId('editor-title').focus()
    await expect(page.getByRole('tooltip')).toHaveCount(0)

    // Undo -> Redo: the neighbour opens warm, and only one bubble is shown
    await page.getByRole('button', { name: 'Undo', exact: true }).hover()
    await expectPaintedTooltip(page, 'Undo')
    await page.getByRole('button', { name: 'Redo', exact: true }).hover()
    await expect(page.getByRole('tooltip')).toHaveText(/^Redo/, { timeout: 200 })
    await expect(page.getByRole('tooltip')).toHaveCount(1)
    await moveAway(page)
  })

  test('640px: a focus-opened tooltip tracks its trigger while the toolbar scrolls', async ({ page }) => {
    await openEditor(page, 640)
    const bar = toolbar(page)
    const { sw, cw } = await bar.evaluate((el) => ({ sw: el.scrollWidth, cw: el.clientWidth }))
    test.skip(sw <= cw, `toolbar does not overflow at 640px (${sw} <= ${cw})`)

    await bar.evaluate((el) => { el.scrollLeft = 0 })
    const preview = page.getByRole('button', { name: 'Preview dashboard' })
    await preview.focus()
    await expectPaintedTooltip(page, 'Preview dashboard')
    // Focusing may itself have scrolled the row; measure from here
    const startLeft = await tipLeft(page)
    const scrolled = await bar.evaluate((el) => {
      const before = el.scrollLeft
      el.scrollLeft = before === 0 ? 30 : 0
      return el.scrollLeft - before
    })
    expect(Math.abs(scrolled), 'toolbar scrolled').toBeGreaterThan(0)
    await expect.poll(() => tipLeft(page)).toBeCloseTo(startLeft - scrolled, 0)
    expect(await paintCheck(page.getByRole('tooltip'))).toEqual({ inViewport: true, painted: true })
  })

  test('1024px: device, zoom and panel-toggle tooltips are painted; segments keep ink', async ({ page }) => {
    await openEditor(page, 1024)

    const devices = page.getByTestId('device-switcher')
    for (const name of ['Desktop', 'Tablet', 'Mobile']) {
      await hoverExpect(page, devices.getByRole('button', { name, exact: true }), name)
    }
    await hoverExpect(page, page.getByTestId('editor-zoom-menu'), 'Canvas zoom')
    for (const [id, text] of [
      ['config', 'Configure widget'],
      ['board', 'Dashboard settings'],
      ['tabs', 'Tab settings'],
      ['chat', 'AI chat'],
      ['filters', 'Manage filters'],
    ]) {
      await hoverExpect(page, page.getByTestId(`panel-toggle-${id}`), text)
    }

    // Tooltip-wrapped segment buttons: pressed = ink fill, dividers between segments
    const seg = await devices.evaluate((group) => {
      const probe = document.createElement('div')
      probe.style.background = 'var(--ink)'
      group.appendChild(probe)
      const ink = getComputedStyle(probe).backgroundColor
      probe.remove()
      const buttons = [...group.querySelectorAll('button')]
      const pressed = buttons.find((b) => b.getAttribute('aria-pressed') === 'true')
      const unpressed = buttons.find((b) => b.getAttribute('aria-pressed') !== 'true')
      return {
        pressedIsInk: getComputedStyle(pressed).backgroundColor === ink,
        unpressedIsInk: getComputedStyle(unpressed).backgroundColor === ink,
        dividers: [...group.children].slice(1).map((c) => getComputedStyle(c).borderLeftWidth),
      }
    })
    expect(seg.pressedIsInk).toBe(true)
    expect(seg.unpressedIsInk).toBe(false)
    expect(seg.dividers).toEqual(['1px', '1px'])
  })

  for (const menu of [
    { name: 'zoom', trigger: 'editor-zoom-menu', panel: 'editor-zoom-menu-panel', inside: (p) => p.getByRole('button', { name: 'Zoom in' }) },
    { name: 'export/share', trigger: 'export-share-btn', panel: 'export-share-panel', inside: (p) => p.locator('button[aria-pressed]').last() /* "share" tab */ },
  ]) {
    test(`1024px: ${menu.name} menu opens painted after the toolbar scrolls`, async ({ page }) => {
      await openEditor(page, 1024)
      const bar = toolbar(page)
      const { sw, cw } = await bar.evaluate((el) => ({ sw: el.scrollWidth, cw: el.clientWidth }))
      // Scroll the row to its end first (no-op if it doesn't overflow at this width)
      await bar.evaluate((el) => { el.scrollLeft = el.scrollWidth })
      if (sw <= cw) test.info().annotations.push({ type: 'note', description: `toolbar did not overflow (${sw} <= ${cw})` })

      const trigger = page.getByTestId(menu.trigger)
      await trigger.hover()
      await expectPaintedTooltip(page, menu.name === 'zoom' ? 'Canvas zoom' : 'Export & share')
      await trigger.click()

      // Clicking the trigger dismisses its tooltip; the panel is painted on screen
      await expect(page.getByRole('tooltip')).toHaveCount(0)
      const panel = page.getByTestId(menu.panel)
      await expect(panel).toBeVisible()
      expect(await paintCheck(panel), `${menu.name} panel`).toEqual({ inViewport: true, painted: true })

      // A click inside the portaled panel must not count as an outside click
      await menu.inside(panel).click()
      await expect(panel).toBeVisible()

      await page.keyboard.press('Escape')
      await expect(panel).toHaveCount(0)
    })
  }

  test('1280px: side="left" Collapse panel tooltip is painted', async ({ page }) => {
    await openEditor(page, 1280)
    const collapse = page.getByRole('button', { name: 'Collapse side panel' })
    // Open the right panel only if it isn't already (the toggle would close it)
    if (!(await collapse.isVisible())) await page.getByTestId('panel-toggle-config').click()
    await expect(collapse).toBeVisible()
    await collapse.hover()
    await expectPaintedTooltip(page, 'Collapse panel')
    await expect(page.getByRole('tooltip')).toHaveAttribute('data-side', 'left')
  })
})
