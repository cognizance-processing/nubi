// Throwaway verification: /docs/connectors renders -dark screenshot variants
// in dark mode and base names in light mode, and all of them actually load.
import { chromium } from '@playwright/test'

const APP = 'http://localhost:5173'
const browser = await chromium.launch()
let failed = false

for (const theme of ['dark', 'light']) {
  const ctx = await browser.newContext({
    viewport: { width: 1440, height: 900 },
    colorScheme: theme,
  })
  await ctx.addInitScript((t) => localStorage.setItem('nubi-theme', t), theme)
  const page = await ctx.newPage()
  await page.goto(`${APP}/docs/connectors`, { waitUntil: 'networkidle', timeout: 45_000 })
  await page.waitForSelector('article img', { timeout: 30_000 })

  const imgs = page.locator('article img')
  const n = await imgs.count()
  console.log(`\n[${theme}] /docs/connectors — ${n} <img> elements`)
  for (let i = 0; i < n; i++) {
    const img = imgs.nth(i)
    await img.scrollIntoViewIfNeeded()
    // lazy-loaded: give the fetch a beat, then poll naturalWidth
    await page.waitForFunction(
      (el) => el.complete && el.naturalWidth > 0,
      await img.elementHandle(),
      { timeout: 15_000 }
    ).catch(() => {})
    const { src, w } = await img.evaluate((el) => ({ src: el.getAttribute('src'), w: el.naturalWidth }))
    const isShot = /^\/docs\/screenshots\//.test(src)
    let ok = w > 0
    if (isShot) {
      const wantDark = theme === 'dark'
      const isDark = /-dark\.png$/.test(src)
      ok = ok && (wantDark ? isDark : !isDark)
    }
    if (!ok) failed = true
    console.log(`  ${ok ? 'OK ' : 'BAD'} src=${src} naturalWidth=${w}`)
  }
  await ctx.close()
}

await browser.close()
if (failed) {
  console.error('\nVERIFICATION FAILED')
  process.exit(1)
}
console.log('\nVERIFICATION PASSED')
