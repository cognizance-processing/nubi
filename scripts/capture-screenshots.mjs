/**
 * capture-screenshots.mjs — capture docs screenshots for Nubi.
 *
 * Prioritises public (no-auth) pages, then attempts authenticated pages if
 * the backend is reachable and credentials work.
 *
 * Usage:
 *   node scripts/capture-screenshots.mjs
 *   # or with overrides:
 *   NUBI_APP_URL=http://localhost:5173 node scripts/capture-screenshots.mjs
 *
 * Saves PNGs to docs/screenshots/ (1440×900 @2x, light theme unless noted).
 */

import { chromium } from '@playwright/test'
import { mkdirSync, writeFileSync, statSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const APP = process.env.NUBI_APP_URL ?? 'http://localhost:5280'
const API = process.env.NUBI_API_URL ?? 'http://localhost:8000'
const EMAIL = process.env.NUBI_ADMIN_EMAIL ?? 'admin@nubi.dev'
const PASSWORD = process.env.NUBI_ADMIN_PASSWORD ?? process.env.SUPERUSER_PASSWORD ?? 'nubi-admin-2026'

const ROOT = path.join(path.dirname(fileURLToPath(import.meta.url)), '..')
const OUT_DIR = path.join(ROOT, 'docs', 'screenshots')
mkdirSync(OUT_DIR, { recursive: true })

const captured = []
const failed = []
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

async function shoot(page, name, opts = {}) {
  const { settle = 1500, viewport } = opts
  if (viewport) await page.setViewportSize(viewport)
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

// ── Public pages (no auth needed) ────────────────────────────────────────────
async function capturePublicPages(browser) {
  const ctx = await browser.newContext({
    viewport: { width: 1440, height: 900 },
    deviceScaleFactor: 2,
    colorScheme: 'light',
  })
  await ctx.addInitScript(() => localStorage.setItem('nubi-theme', 'light'))
  const page = await ctx.newPage()

  // Landing — desktop
  console.log('\n── Public pages ──')
  try {
    await page.goto(`${APP}/`, { waitUntil: 'networkidle', timeout: 30_000 })
    await shoot(page, 'landing', { settle: 2000 })
  } catch (e) {
    console.error(`✗ landing: ${e.message}`)
    failed.push({ name: 'landing', reason: e.message })
  }

  // Landing — mobile (375px wide)
  try {
    const mobileCtx = await browser.newContext({
      viewport: { width: 375, height: 812 },
      deviceScaleFactor: 2,
      colorScheme: 'light',
      isMobile: true,
    })
    await mobileCtx.addInitScript(() => localStorage.setItem('nubi-theme', 'light'))
    const mobilePage = await mobileCtx.newPage()
    await mobilePage.goto(`${APP}/`, { waitUntil: 'networkidle', timeout: 30_000 })
    await shoot(mobilePage, 'landing-mobile', { settle: 2000 })
    await mobileCtx.close()
  } catch (e) {
    console.error(`✗ landing-mobile: ${e.message}`)
    failed.push({ name: 'landing-mobile', reason: e.message })
  }

  // Pricing
  try {
    await page.goto(`${APP}/pricing`, { waitUntil: 'networkidle', timeout: 30_000 })
    await shoot(page, 'pricing', { settle: 2000 })
  } catch (e) {
    console.error(`✗ pricing: ${e.message}`)
    failed.push({ name: 'pricing', reason: e.message })
  }

  // Compare
  try {
    await page.goto(`${APP}/compare`, { waitUntil: 'networkidle', timeout: 30_000 })
    await shoot(page, 'compare', { settle: 2000 })
  } catch (e) {
    console.error(`✗ compare: ${e.message}`)
    failed.push({ name: 'compare', reason: e.message })
  }

  await ctx.close()
}

// ── Authenticated pages ───────────────────────────────────────────────────────
async function tryLogin(page) {
  // Log in via the API through the app origin (Vite proxies /api), so the
  // HttpOnly refresh cookie lands in the browser's cookie jar.
  for (let attempt = 1; attempt <= 3; attempt++) {
    try {
      await page.context().clearCookies()
      const res = await page.context().request.post(`${APP}/api/v1/auth/login`, {
        data: { email: EMAIL, password: PASSWORD },
        timeout: 15_000,
      })
      if (res.ok()) {
        await page.goto(`${APP}/home`, { waitUntil: 'networkidle', timeout: 30_000 })
        await sleep(1000)
        if (!page.url().includes('/login')) return true
        console.log(`  attempt ${attempt}: redirected to /login after successful API login`)
      } else {
        console.log(`  attempt ${attempt}: API login returned ${res.status()}`)
      }
    } catch (e) {
      console.log(`  attempt ${attempt}: ${e.message}`)
    }
    await sleep(1500)
  }
  return false
}

async function discoverIds(token) {
  const headers = { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' }
  const base = `${API}/api/v1`

  const orgsRes = await fetch(`${base}/orgs`, { headers })
  if (!orgsRes.ok) return null
  const { orgs } = await orgsRes.json()
  const org = orgs?.[0]
  if (!org) return null

  const projectsRes = await fetch(`${base}/projects?org_id=${org.id}`, { headers })
  if (!projectsRes.ok) return null
  const projects = await projectsRes.json()
  const demo =
    projects.find((p) => p.slug === 'demo' || p.name?.toLowerCase() === 'demo') ?? projects[0]
  if (!demo) return null

  const boardsRes = await fetch(`${base}/boards`, {
    headers: { ...headers, 'X-Project-Id': demo.id },
  })
  const boards = boardsRes.ok ? await boardsRes.json() : []
  const board =
    boards.find((b) => /retail/i.test(b.name)) ??
    boards.find((b) => /overview/i.test(b.name)) ??
    boards[0]

  const flowsRes = await fetch(`${base}/flows`, {
    headers: { ...headers, 'X-Project-Id': demo.id },
  })
  const flows = flowsRes.ok ? await flowsRes.json() : []
  const flow = flows.find((f) => f.spec?.tasks?.length > 1) ?? flows[0]

  return { org, demo, board, flow }
}

async function captureAuthPages(browser) {
  const ctx = await browser.newContext({
    viewport: { width: 1440, height: 900 },
    deviceScaleFactor: 2,
    colorScheme: 'light',
  })
  await ctx.addInitScript(() => localStorage.setItem('nubi-theme', 'light'))
  const page = await ctx.newPage()

  console.log('\n── Auth pages ──')
  const loggedIn = await tryLogin(page)
  if (!loggedIn) {
    console.log('  Skipping all authenticated shots.')
    await ctx.close()
    return
  }

  // Try to get a token for API discovery
  let ids = null
  try {
    const loginRes = await fetch(`${API}/api/v1/auth/login`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email: EMAIL, password: PASSWORD }),
    })
    if (loginRes.ok) {
      const { access_token } = await loginRes.json()
      ids = await discoverIds(access_token)
    }
  } catch (e) {
    console.log(`  API discovery failed: ${e.message}`)
  }

  // Set demo project in localStorage if discovered
  if (ids) {
    await page.evaluate(
      ([k, v]) => localStorage.setItem(k, v),
      [`nubi-active-project-id:${ids.org.id}`, ids.demo.id]
    )
    await page.goto(`${APP}/home`, { waitUntil: 'networkidle', timeout: 30_000 })
    await sleep(1000)
  }

  // Dashboard list
  try {
    await page.goto(`${APP}/dashboards`, { waitUntil: 'networkidle', timeout: 30_000 })
    await sleep(2000)
    await shoot(page, 'dashboard', { settle: 1000 })
  } catch (e) {
    console.error(`✗ dashboard: ${e.message}`)
    failed.push({ name: 'dashboard', reason: e.message })
  }

  // Dashboard editor (if we have a board id)
  if (ids?.board) {
    try {
      await page.goto(`${APP}/editor/${ids.board.id}`, {
        waitUntil: 'networkidle',
        timeout: 45_000,
      })
      await sleep(4000)
      await shoot(page, 'editor', { settle: 1000 })
    } catch (e) {
      console.error(`✗ editor: ${e.message}`)
      failed.push({ name: 'editor', reason: e.message })
    }
  } else {
    failed.push({ name: 'editor', reason: 'no board id discovered' })
  }

  // Flows
  try {
    await page.goto(`${APP}/flows`, { waitUntil: 'networkidle', timeout: 30_000 })
    await sleep(2000)
    await shoot(page, 'flows', { settle: 1000 })
  } catch (e) {
    console.error(`✗ flows: ${e.message}`)
    failed.push({ name: 'flows', reason: e.message })
  }

  // Canvas — try /canvas list first, then a specific canvas if we have an id
  try {
    const canvasUrl = ids?.flow ? `${APP}/canvas` : `${APP}/canvas`
    await page.goto(canvasUrl, { waitUntil: 'networkidle', timeout: 30_000 })
    await sleep(2000)
    await shoot(page, 'canvas', { settle: 1000 })
  } catch (e) {
    console.error(`✗ canvas: ${e.message}`)
    failed.push({ name: 'canvas', reason: e.message })
  }

  await ctx.close()
}

// ── Main ──────────────────────────────────────────────────────────────────────
console.log(`\nCapturing screenshots from ${APP}`)
console.log(`Output → ${OUT_DIR}\n`)

const browser = await chromium.launch({ headless: true })

try {
  await capturePublicPages(browser)
  await captureAuthPages(browser)
} finally {
  await browser.close()
}

// Summary
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

// Write a manifest
writeFileSync(
  path.join(OUT_DIR, 'capture-manifest.json'),
  JSON.stringify(
    {
      generatedAt: new Date().toISOString(),
      app: APP,
      captured: captured.map((c) => c.name + '.png'),
      failed: failed.map((f) => ({ name: f.name + '.png', reason: f.reason })),
    },
    null,
    2
  ) + '\n'
)
console.log(`\nManifest written to ${OUT_DIR}/capture-manifest.json`)
