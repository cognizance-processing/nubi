/**
 * capture-light-dark.mjs — capture user-facing Nubi screenshots in BOTH themes.
 *
 * Saves docs/screenshots/<name>-light.png and <name>-dark.png (and copies to
 * public/docs/screenshots/). 1440×900 @2x. Requires the dev stack running:
 *   API  on http://localhost:8000  (CORS_ORIGINS includes the SPA origin)
 *   SPA  on http://localhost:5173
 *   demo workspace seeded (admin@nubi.dev / nubi-admin-2026)
 *
 * Usage: node scripts/capture-light-dark.mjs
 */
import { chromium } from 'playwright'
import { mkdirSync, copyFileSync, statSync } from 'node:fs'
import path from 'node:path'

const APP = process.env.NUBI_APP_URL ?? 'http://localhost:5173'
const API = process.env.NUBI_API_URL ?? 'http://localhost:8000'
const EMAIL = process.env.NUBI_ADMIN_EMAIL ?? 'admin@nubi.dev'
const PASSWORD = process.env.NUBI_ADMIN_PASSWORD ?? 'nubi-admin-2026'
const ROOT = path.resolve(import.meta.dirname, '..')
const OUT = path.join(ROOT, 'docs', 'screenshots')
const PUB = path.join(ROOT, 'public', 'docs', 'screenshots')
mkdirSync(OUT, { recursive: true }); mkdirSync(PUB, { recursive: true })
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
const captured = [], failed = []

async function discover() {
  try {
    const r = await fetch(`${API}/api/v1/auth/login`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email: EMAIL, password: PASSWORD }),
    })
    if (!r.ok) return null
    const { access_token } = await r.json()
    const h = { Authorization: `Bearer ${access_token}` }
    const orgs = await (await fetch(`${API}/api/v1/orgs`, { headers: h })).json().catch(() => [])
    const org = Array.isArray(orgs) ? orgs[0] : (orgs.orgs ?? [])[0]
    const projs = await (await fetch(`${API}/api/v1/projects`, { headers: h })).json().catch(() => [])
    const plist = Array.isArray(projs) ? projs : (projs.projects ?? [])
    const demo = plist.find((p) => /default|demo/i.test(p.name)) ?? plist[0]
    const ph = { ...h, 'X-Project-Id': demo?.id }
    const boards = await (await fetch(`${API}/api/v1/boards`, { headers: ph })).json().catch(() => [])
    const blist = Array.isArray(boards) ? boards : (boards.boards ?? [])
    const board = blist.find((b) => /retail|overview|sales/i.test(b.name)) ?? blist[0]
    return { org, demo, board }
  } catch { return null }
}

async function capture(theme, ids) {
  const ctx = await chromium.launchPersistentContext === undefined ? null : null
  const browser = await chromium.launch()
  const context = await browser.newContext({
    viewport: { width: 1440, height: 900 }, deviceScaleFactor: 2, colorScheme: theme,
  })
  await context.addInitScript((t) => localStorage.setItem('nubi-theme', t), theme)
  if (ids?.org && ids?.demo) {
    await context.addInitScript(
      ([k, v]) => localStorage.setItem(k, v),
      [`nubi-active-project-id:${ids.org.id}`, ids.demo.id]
    )
  }
  const page = await context.newPage()

  const shot = async (name, route, { settle = 2200, auth = true } = {}) => {
    try {
      await page.goto(`${APP}${route}`, { waitUntil: 'domcontentloaded', timeout: 30_000 })
      await sleep(settle)
      await page.evaluate(() => window.scrollTo(0, 0))
      await sleep(150)
      const file = `${name}-${theme}.png`
      const out = path.join(OUT, file)
      await page.screenshot({ path: out, fullPage: false })
      copyFileSync(out, path.join(PUB, file))
      captured.push(file)
      console.log(`  ✓ ${file} (${(statSync(out).size / 1024).toFixed(0)} KB)`)
    } catch (e) { failed.push(`${name}-${theme}: ${e.message}`); console.log(`  ✗ ${name}-${theme}: ${e.message}`) }
  }

  // Public (no auth)
  await shot('landing', '/', { auth: false })
  await shot('pricing', '/pricing', { auth: false })

  // Login via form
  await page.goto(`${APP}/login`, { waitUntil: 'domcontentloaded' })
  await sleep(1500)
  try {
    await page.locator('input[type="email"],input[type="text"]').first().fill(EMAIL)
    await page.locator('input[type="password"]').first().fill(PASSWORD)
    await page.locator('button:has-text("Sign in"),button[type="submit"]').first().click().catch(() => {})
    await sleep(4000)
  } catch (e) { console.log(`  login: ${e.message}`) }

  // Authed surfaces
  await shot('home', '/home')
  await shot('overview', '/overview')
  await shot('workqueue', '/workqueue')
  await shot('dashboards', '/dashboards')
  if (ids?.board?.id) await shot('dashboard-view', `/d/${ids.board.id}`, { settle: 4000 })
  if (ids?.board?.id) await shot('editor', `/editor/${ids.board.id}`, { settle: 4500 })
  await shot('queries', '/queries')
  await shot('explore', '/explore', { settle: 3500 })
  await shot('flows', '/flows')
  await shot('connectors', '/connectors')
  await shot('data', '/data')
  await shot('settings', '/settings')

  await context.close(); await browser.close()
}

const ids = await discover()
console.log(`discovery: ${ids ? `org+project+board (${ids.board?.name ?? 'no board'})` : 'FAILED (login/API)'}`)
for (const theme of ['light', 'dark']) {
  console.log(`\n── ${theme} ──`)
  await capture(theme, ids)
}
console.log(`\ncaptured ${captured.length} | failed ${failed.length}`)
if (failed.length) console.log(failed.join('\n'))
