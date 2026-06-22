/**
 * capture-auth-shots.mjs — retry authenticated + specific-resource shots.
 * Run after capture-screenshots.mjs has confirmed auth works.
 *
 * Usage:
 *   node scripts/capture-auth-shots.mjs
 */

import { chromium } from '@playwright/test'
import { mkdirSync, statSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const APP = process.env.NUBI_APP_URL ?? 'http://localhost:5280'
const API = process.env.NUBI_API_URL ?? 'http://localhost:8000'
const EMAIL = process.env.NUBI_ADMIN_EMAIL ?? 'admin@nubi.dev'
const PASSWORD = process.env.NUBI_ADMIN_PASSWORD ?? 'nubi-admin-2026'

const ROOT = path.join(path.dirname(fileURLToPath(import.meta.url)), '..')
const OUT_DIR = path.join(ROOT, 'docs', 'screenshots')
mkdirSync(OUT_DIR, { recursive: true })

const captured = []
const failed = []
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

async function shoot(page, name, opts = {}) {
  const { settle = 1500 } = opts
  await sleep(settle)
  await page.evaluate(() => {
    window.scrollTo(0, 0)
    for (const el of document.querySelectorAll('*')) {
      if (el.scrollTop > 0) el.scrollTop = 0
    }
  })
  await sleep(200)
  const outPath = path.join(OUT_DIR, `${name}.png`)
  await page.screenshot({ path: outPath, fullPage: false })
  const size = statSync(outPath).size
  captured.push({ name, size })
  console.log(`✓ ${name}.png  (${(size / 1024).toFixed(1)} KB)`)
}

const browser = await chromium.launch({ headless: true })
const ctx = await browser.newContext({
  viewport: { width: 1440, height: 900 },
  deviceScaleFactor: 2,
  colorScheme: 'light',
})
await ctx.addInitScript(() => localStorage.setItem('nubi-theme', 'light'))
const page = await ctx.newPage()

// Log in
await page.context().clearCookies()
const loginRes = await page.context().request.post(`${APP}/api/v1/auth/login`, {
  data: { email: EMAIL, password: PASSWORD },
})
if (!loginRes.ok()) throw new Error(`Login failed: ${loginRes.status()}`)
console.log('Logged in via proxy')

// Discover IDs
const tokenData = await loginRes.json()
// We need to do API calls to find IDs
const apiHeaders = {
  Authorization: `Bearer ${tokenData.access_token}`,
  'Content-Type': 'application/json',
}

const orgsRes = await fetch(`${API}/api/v1/orgs`, { headers: apiHeaders })
const { orgs } = await orgsRes.json()
const orgId = orgs[0].id

const projectsRes = await fetch(`${API}/api/v1/projects?org_id=${orgId}`, { headers: apiHeaders })
const projects = await projectsRes.json()
const defaultProject = projects.find((p) => p.slug === 'default' || p.name === 'Default') ?? projects[0]
console.log('Using project:', defaultProject.name, defaultProject.id)

// Navigate to home first (which loads the app), then set the project
await page.goto(`${APP}/home`, { waitUntil: 'networkidle', timeout: 30_000 })
await sleep(1000)

// Set the project in localStorage (page is now on the app origin)
await page.evaluate(
  ([k, v]) => localStorage.setItem(k, v),
  [`nubi-active-project-id:${orgId}`, defaultProject.id]
)
// Reload so the app reads the new project from localStorage
await page.goto(`${APP}/home`, { waitUntil: 'networkidle', timeout: 30_000 })
await sleep(1200)
if (page.url().includes('/login')) {
  throw new Error('Got bounced to /login after setting project — auth issue')
}
console.log('Home loaded:', page.url())

// ── Flows — navigate to a specific flow ──────────────────────────────────────
const flowsRes = await fetch(`${API}/api/v1/flows`, {
  headers: { ...apiHeaders, 'X-Project-Id': defaultProject.id },
})
const flows = await flowsRes.json()
console.log('Flows found:', flows.length)

if (flows.length > 0) {
  // Pick the flow with the most tasks for a richer screenshot
  const flow = flows.sort((a, b) => (b.spec?.tasks?.length ?? 0) - (a.spec?.tasks?.length ?? 0))[0]
  console.log('Opening flow:', flow.name, flow.id)

  // Navigate client-side to avoid token rotation
  await page.evaluate((u) => {
    window.history.pushState({}, '', u)
    window.dispatchEvent(new PopStateEvent('popstate'))
  }, `/flows/${flow.id}`)
  await page.waitForLoadState('networkidle', { timeout: 30_000 }).catch(() => {})
  await sleep(3000)

  try {
    await shoot(page, 'flows', { settle: 1500 })
  } catch (e) {
    failed.push({ name: 'flows', reason: e.message })
    console.error(`✗ flows: ${e.message}`)
  }
} else {
  failed.push({ name: 'flows', reason: 'no flows found in project' })
}

// ── Canvas — navigate to /canvases list ──────────────────────────────────────
await page.evaluate((u) => {
  window.history.pushState({}, '', u)
  window.dispatchEvent(new PopStateEvent('popstate'))
}, '/canvases')
await page.waitForLoadState('networkidle', { timeout: 30_000 }).catch(() => {})
await sleep(3000)

try {
  await shoot(page, 'canvas', { settle: 1500 })
} catch (e) {
  failed.push({ name: 'canvas', reason: e.message })
  console.error(`✗ canvas: ${e.message}`)
}

// ── Dashboard list (re-capture with demo boards visible) ─────────────────────
await page.evaluate((u) => {
  window.history.pushState({}, '', u)
  window.dispatchEvent(new PopStateEvent('popstate'))
}, '/dashboards')
await page.waitForLoadState('networkidle', { timeout: 30_000 }).catch(() => {})
await sleep(2500)

try {
  await shoot(page, 'dashboard', { settle: 1000 })
} catch (e) {
  failed.push({ name: 'dashboard', reason: e.message })
  console.error(`✗ dashboard: ${e.message}`)
}

// ── Dashboard editor ──────────────────────────────────────────────────────────
const boardsRes = await fetch(`${API}/api/v1/boards`, {
  headers: { ...apiHeaders, 'X-Project-Id': defaultProject.id },
})
const boards = await boardsRes.json()
console.log('Boards found:', boards.length)

if (boards.length > 0) {
  const board = boards.find((b) => /Retail/i.test(b.name)) ?? boards[0]
  console.log('Opening board editor:', board.name, board.id)

  await page.evaluate((u) => {
    window.history.pushState({}, '', u)
    window.dispatchEvent(new PopStateEvent('popstate'))
  }, `/editor/${board.id}`)
  await page.waitForLoadState('networkidle', { timeout: 45_000 }).catch(() => {})
  await sleep(5000)

  try {
    await shoot(page, 'editor', { settle: 1500 })
  } catch (e) {
    failed.push({ name: 'editor', reason: e.message })
    console.error(`✗ editor: ${e.message}`)
  }
} else {
  failed.push({ name: 'editor', reason: 'no boards found in project' })
}

await ctx.close()
await browser.close()

console.log('\n══ Summary ══')
if (captured.length) {
  console.log(`\nCaptured (${captured.length}):`)
  for (const { name, size } of captured) {
    console.log(`  ${name}.png  — ${(size / 1024).toFixed(1)} KB`)
  }
}
if (failed.length) {
  console.log(`\nFailed (${failed.length}):`)
  for (const { name, reason } of failed) {
    console.log(`  ${name}: ${reason}`)
  }
}
