import { chromium } from '@playwright/test'

const browser = await chromium.launch()
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } })
await page.goto('http://localhost:5173/', { waitUntil: 'networkidle' })
await page.waitForTimeout(1200)

const report = await page.evaluate(() => {
  const doc = document.documentElement
  const out = {
    viewport: { w: window.innerWidth, h: window.innerHeight },
    docScroll: { w: doc.scrollWidth, h: doc.scrollHeight, clientW: doc.clientWidth },
    bodyScroll: { w: document.body.scrollWidth },
    horizontalOverflow: doc.scrollWidth > doc.clientWidth,
    wideElements: [],
    verticalScrollContainers: [],
  }
  const all = document.querySelectorAll('*')
  for (const el of all) {
    const r = el.getBoundingClientRect()
    // elements extending past viewport right edge or left of 0
    if (r.right > window.innerWidth + 1 || r.left < -1) {
      const cs = getComputedStyle(el)
      if (cs.position === 'fixed') continue
      out.wideElements.push({
        tag: el.tagName.toLowerCase(),
        cls: (el.className?.baseVal ?? el.className ?? '').toString().slice(0, 120),
        left: Math.round(r.left), right: Math.round(r.right), width: Math.round(r.width),
        pos: cs.position,
      })
    }
    // nested vertical scrollbars
    const cs2 = getComputedStyle(el)
    if ((cs2.overflowY === 'auto' || cs2.overflowY === 'scroll') && el.scrollHeight > el.clientHeight + 1) {
      out.verticalScrollContainers.push({
        tag: el.tagName.toLowerCase(),
        cls: (el.className?.baseVal ?? el.className ?? '').toString().slice(0, 120),
        clientH: el.clientHeight, scrollH: el.scrollHeight,
        overflowY: cs2.overflowY, overflowX: cs2.overflowX,
      })
    }
  }
  out.wideElements = out.wideElements.slice(0, 40)
  return out
})
console.log(JSON.stringify(report, null, 2))
await page.screenshot({ path: '/tmp/landing-top.png' })
await browser.close()
