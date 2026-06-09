/**
 * docs-screenshots.mjs — regenerate every product screenshot embedded in the docs.
 *
 *   node scripts/docs-screenshots.mjs
 *
 * Requirements:
 *   - Dev stack running: vite on :5173, API on :8000.
 *   - Seeded admin account (admin@nubi.dev) with the Demo project
 *     (10 dashboards, 38 queries, demo connector).
 *
 * Output: public/docs/screenshots/<name>.png — referenced from docs markdown
 * as /docs/screenshots/<name>.png. Viewport 1440x900 @2x, light theme.
 * Re-runnable: resource ids (boards, queries, flows, projects) are discovered
 * by name via the API on every run, so reseeding the DB does not break it.
 */
import { chromium } from '@playwright/test'
import { mkdirSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const APP = process.env.NUBI_APP_URL ?? 'http://localhost:5173'
const API = process.env.NUBI_API_URL ?? 'http://localhost:8000'
const EMAIL = process.env.NUBI_ADMIN_EMAIL ?? 'admin@nubi.dev'
const PASSWORD = process.env.NUBI_ADMIN_PASSWORD ?? 'nubi-admin-2026'

const OUT_DIR = path.join(
  path.dirname(fileURLToPath(import.meta.url)),
  '..', 'public', 'docs', 'screenshots'
)
mkdirSync(OUT_DIR, { recursive: true })

const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

// ── Discover seeded resource ids via the API ─────────────────────────────────
async function api(pathname, token, projectId) {
  const headers = { 'Content-Type': 'application/json' }
  if (token) headers.Authorization = `Bearer ${token}`
  if (projectId) headers['X-Project-Id'] = projectId
  const res = await fetch(`${API}/api/v1${pathname}`, { headers })
  if (!res.ok) throw new Error(`GET ${pathname} → ${res.status}`)
  return res.json()
}

async function discover() {
  const loginRes = await fetch(`${API}/api/v1/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email: EMAIL, password: PASSWORD }),
  })
  if (!loginRes.ok) throw new Error(`API login failed: ${loginRes.status}`)
  const { access_token: token } = await loginRes.json()

  const { orgs } = await api('/orgs', token)
  const org = orgs[0]
  const projects = await api(`/projects?org_id=${org.id}`, token)
  const demo = projects.find((p) => p.slug === 'demo' || p.name === 'Demo') ?? projects[0]
  const dflt = projects.find((p) => p.slug === 'default' || p.name === 'Default') ?? projects[0]

  const boards = await api('/boards', token, demo.id)
  const board =
    boards.find((b) => b.name === 'Retail Sales Overview') ?? boards[0]

  const queries = await api('/queries', token, demo.id)
  const queryList = Array.isArray(queries) ? queries : queries.queries ?? []
  const query =
    queryList.find((q) => /Retail — region × month/.test(q.name ?? '')) ??
    queryList.find((q) => /Retail/.test(q.name ?? '')) ??
    queryList[0]

  const flows = await api('/flows', token, demo.id)
  const flow =
    flows.find((f) => f.name === 'Retail revenue pipeline') ??
    flows.find((f) => (f.spec?.tasks?.length ?? 0) > 1) ??
    flows[0]

  return { org, demo, dflt, board, query, flow }
}

// ── Browser helpers ──────────────────────────────────────────────────────────
async function uiLogin(page) {
  await page.goto(`${APP}/login`, { waitUntil: 'networkidle' })
  await page.fill('input[type="email"]', EMAIL)
  await page.fill('input[type="password"]', PASSWORD)
  await page.click('button[type="submit"]')
  // Post-login redirect may target /home or a `next` deep link.
  await page.waitForURL((u) => !String(u).includes('/login'), { timeout: 30_000 })
}

async function setProject(page, orgId, projectId) {
  await page.evaluate(
    ([k, v]) => localStorage.setItem(k, v),
    [`nubi-active-project-id:${orgId}`, projectId]
  )
}

async function shoot(page, name, { settle = 1200 } = {}) {
  await sleep(settle)
  // Reset both window scroll and any internally scrolled pane (autofocus on
  // forms below the fold scrolls split-screen layouts).
  await page.evaluate(() => {
    window.scrollTo(0, 0)
    for (const el of document.querySelectorAll('*')) {
      if (el.scrollTop > 0) el.scrollTop = 0
    }
  })
  await sleep(150)
  await page.screenshot({ path: path.join(OUT_DIR, `${name}.png`) })
  console.log(`✓ ${name}.png`)
}

// Rapid successive full page loads can race the refresh-token rotation and
// bounce the session to /login. `ensureApp` recovers by re-logging-in and
// restoring the active project, then retrying the navigation.
let desiredProject = null
let orgId = null
async function ensureApp(page, url) {
  await page.goto(`${APP}${url}`, { waitUntil: 'networkidle', timeout: 45_000 })
  await sleep(600)
  if (page.url().includes('/login')) {
    await uiLogin(page)
    if (orgId && desiredProject) {
      await setProject(page, orgId, desiredProject)
    }
    await page.goto(`${APP}${url}`, { waitUntil: 'networkidle', timeout: 45_000 })
    await sleep(600)
  }
}

async function goAndShoot(page, url, name, opts = {}) {
  await ensureApp(page, url)
  await shoot(page, name, opts)
}

// ── Main ─────────────────────────────────────────────────────────────────────
const ids = await discover()
console.log('Discovered:', {
  demoProject: ids.demo.id,
  board: ids.board?.id,
  query: ids.query?.id,
  flow: ids.flow?.id,
})

const browser = await chromium.launch()
const ctx = await browser.newContext({
  viewport: { width: 1440, height: 900 },
  deviceScaleFactor: 2,
  colorScheme: 'light',
})
// Force light theme before any app code runs (consistent docs look).
await ctx.addInitScript(() => {
  localStorage.setItem('nubi-theme', 'light')
})
const page = await ctx.newPage()

// Logged-out shots first. The register form is vertically centered and taller
// than 900px, so it gets a taller viewport of its own.
{
  // dpr 1: the full-bleed gradient artwork makes a 2x capture ~2.7 MB; the
  // docs render this at ~800px wide so 1x stays crisp and 10x smaller.
  const regCtx = await browser.newContext({
    viewport: { width: 1440, height: 1080 },
    deviceScaleFactor: 1,
    colorScheme: 'light',
  })
  await regCtx.addInitScript(() => localStorage.setItem('nubi-theme', 'light'))
  const regPage = await regCtx.newPage()
  await regPage.goto(`${APP}/register`, { waitUntil: 'networkidle', timeout: 45_000 })
  await shoot(regPage, 'register', { settle: 800 })
  await regCtx.close()
}

// Log in, then activate the seeded Demo project.
orgId = ids.org.id
desiredProject = ids.demo.id
await uiLogin(page)
await setProject(page, ids.org.id, ids.demo.id)
await goAndShoot(page, '/home', 'home', { settle: 2000 })

// Onboarding — only renders for accounts that have not finished setup, so it
// is captured with a dedicated throwaway user (idempotent fixed email).
{
  const obEmail = 'docs-screenshots@nubi.dev'
  const obPassword = 'docs-shots-2026!'
  await fetch(`${API}/api/v1/auth/register`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email: obEmail, password: obPassword, name: 'Docs Screenshots' }),
  }).catch(() => {}) // 409 (already exists) is fine
  const obCtx = await browser.newContext({
    viewport: { width: 1440, height: 900 },
    deviceScaleFactor: 2,
    colorScheme: 'light',
  })
  await obCtx.addInitScript(() => localStorage.setItem('nubi-theme', 'light'))
  const obPage = await obCtx.newPage()
  await obPage.goto(`${APP}/login`, { waitUntil: 'networkidle' })
  await obPage.fill('input[type="email"]', obEmail)
  await obPage.fill('input[type="password"]', obPassword)
  await obPage.click('button[type="submit"]')
  await obPage.waitForURL(/\/(home|onboarding)/, { timeout: 30_000 })
  await obPage.goto(`${APP}/onboarding`, { waitUntil: 'networkidle' })
  await shoot(obPage, 'onboarding', { settle: 2000 })
  await obCtx.close()
}

// Connectors — the demo connector lives in the Default project.
desiredProject = ids.dflt.id
await setProject(page, ids.org.id, ids.dflt.id)
await goAndShoot(page, '/connectors', 'connectors', { settle: 1500 })

// Data browser for the demo connector (rich seeded tables).
await goAndShoot(page, '/connectors/__demo__/data', 'data-browser', { settle: 2500 })

// Back to the Demo project for everything else.
desiredProject = ids.demo.id
await setProject(page, ids.org.id, ids.demo.id)

// Queries editor — open a seeded query from the registry list, run it, wait
// for the results grid. (Clicking the list item is more reliable than the
// /queries/:id deep link while the registry list is still loading.)
await ensureApp(page, '/queries')
await sleep(1500)
await page.getByText(ids.query.name, { exact: true }).first().click()
await sleep(1500)
const runBtn = page.locator('button[title^="Run query"]').first()
await runBtn.click()
// Results render as a data table; give the kernel time on first run.
await page
  .waitForSelector('table tbody tr, [class*="dataTable"] tr', { timeout: 60_000 })
  .catch(() => {})
await shoot(page, 'queries-editor', { settle: 2500 })

// Dashboard editor on Retail Sales Overview, then its Preview mode for a
// clean rendered view. (The standalone /d/:id route is captured via Preview
// because it renders chrome-free inside the editor.)
await goAndShoot(page, `/editor/${ids.board.id}`, 'dashboard-editor', { settle: 4500 })
const previewBtn = page.getByRole('button', { name: 'Preview', exact: true }).first()
if (await previewBtn.count()) {
  await previewBtn.click()
  await shoot(page, 'dashboard-view', { settle: 3000 })
}

// Flows — canvas (default view) then notebook view.
await ensureApp(page, `/flows/${ids.flow.id}`)
await shoot(page, 'flows-canvas', { settle: 2500 })
const notebookToggle = page.locator('button[title="Notebook / cell view"]').first()
if (await notebookToggle.count()) {
  await notebookToggle.click()
  await shoot(page, 'flows-notebook', { settle: 2000 })
}

// Automations (schedules).
await goAndShoot(page, '/automations', 'automations')

// Secrets (flow-scoped).
await goAndShoot(page, '/flows/secrets', 'secrets')

// Settings — organization.
await goAndShoot(page, '/settings/organization', 'settings-organization')

await browser.close()
console.log(`\nDone. Screenshots in ${OUT_DIR}`)
