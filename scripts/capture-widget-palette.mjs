/**
 * Recapture NEW-widget-palette.webp (light + dark) from the editor widget library.
 * Requires dev stack on :5173 / :8000 and a seeded admin account.
 *
 *   node scripts/capture-widget-palette.mjs
 */
import { chromium } from '@playwright/test'
import { copyFileSync, mkdirSync, unlinkSync, writeFileSync } from 'node:fs'
import { execSync } from 'node:child_process'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const APP = process.env.NUBI_APP_URL ?? 'http://localhost:5173'
const API = process.env.NUBI_API_URL ?? 'http://localhost:8000'
const EMAIL = process.env.NUBI_ADMIN_EMAIL ?? 'admin@nubi.dev'
const PASSWORD = process.env.NUBI_ADMIN_PASSWORD ?? 'nubi-admin-2026'

const ROOT = path.join(path.dirname(fileURLToPath(import.meta.url)), '..')
const OUT_DIR = path.join(ROOT, 'public', 'docs', 'screenshots')
const DOCS_MIRROR_DIR = path.join(ROOT, 'docs', 'screenshots')
mkdirSync(OUT_DIR, { recursive: true })
mkdirSync(DOCS_MIRROR_DIR, { recursive: true })

const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

function encodePngToWebp(tmpPng, webp) {
  try {
    execSync(
      `cwebp -quiet -q 86 -sharp_yuv -m 6 ${JSON.stringify(tmpPng)} -o ${JSON.stringify(webp)}`,
    )
  } catch {
    const py = path.join(OUT_DIR, '_encode_webp.py')
    writeFileSync(
      py,
      `from PIL import Image\nImage.open(${JSON.stringify(tmpPng)}).save(${JSON.stringify(webp)}, 'WEBP', quality=86)\n`,
    )
    try {
      execSync(`python ${JSON.stringify(py)}`)
    } finally {
      unlinkSync(py)
    }
  }
  unlinkSync(tmpPng)
}

async function uiLogin(page) {
  await page.context().clearCookies()
  const res = await page.context().request.post(`${APP}/api/v1/auth/login`, {
    data: { email: EMAIL, password: PASSWORD },
  })
  if (!res.ok()) throw new Error(`login ${res.status()}`)
  await page.goto(`${APP}/home`, { waitUntil: 'networkidle', timeout: 45_000 })
  await sleep(800)
  if (page.url().includes('/login')) throw new Error('bounced to /login')
}

async function discoverOrgProject(token) {
  const headers = { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' }
  const orgsRes = await fetch(`${API}/api/v1/orgs`, { headers })
  const { orgs } = await orgsRes.json()
  const org = orgs[0]
  const projectsRes = await fetch(`${API}/api/v1/projects?org_id=${org.id}`, { headers })
  const projects = await projectsRes.json()
  const demo = projects.find((p) => p.slug === 'demo' || p.name === 'Demo') ?? projects[0]
  return { org, demo }
}

async function shootLocator(page, locator, name) {
  await sleep(400)
  const tmpPng = path.join(OUT_DIR, `${name}.png`)
  const webp = path.join(OUT_DIR, `${name}.webp`)
  await locator.screenshot({ path: tmpPng })
  encodePngToWebp(tmpPng, webp)
  copyFileSync(webp, path.join(DOCS_MIRROR_DIR, `${name}.webp`))
  console.log(`✓ ${name}.webp`)
}

async function capturePalette(browser, theme) {
  const sfx = theme === 'dark' ? '-dark' : ''
  const ctx = await browser.newContext({
    viewport: { width: 1440, height: 900 },
    deviceScaleFactor: 2,
    colorScheme: theme,
  })
  await ctx.addInitScript((t) => localStorage.setItem('nubi-theme', t), theme)
  const page = await ctx.newPage()
  await uiLogin(page)

  const loginRes = await fetch(`${API}/api/v1/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email: EMAIL, password: PASSWORD }),
  })
  const { access_token: token } = await loginRes.json()
  const { org, demo } = await discoverOrgProject(token)
  await page.evaluate(
    ([k, v]) => localStorage.setItem(k, v),
    [`nubi-active-project-id:${org.id}`, demo.id],
  )
  await page.goto(`${APP}/home`, { waitUntil: 'networkidle', timeout: 45_000 })
  await sleep(600)
  await page.goto(`${APP}/editor`, { waitUntil: 'networkidle', timeout: 45_000 })
  await sleep(2000)
  await page.waitForSelector('[data-testid="palette-add-breadcrumb"]', { timeout: 30_000 })
  await page.waitForSelector('[data-testid="palette-add-datedisplay"]', { timeout: 10_000 })
  const library = page.locator('aside[aria-label="Widget library"]')
  await library.waitFor({ state: 'visible', timeout: 15_000 })
  await page.evaluate(() => {
    const scroller = document.querySelector('[data-testid="add-panel-search"]')?.closest('.overflow-y-auto')
    if (scroller) scroller.scrollTop = 0
  })
  await shootLocator(page, library, `NEW-widget-palette${sfx}`)
  await ctx.close()
}

console.log(`Capturing widget palette from ${APP}\n`)
const browser = await chromium.launch()
try {
  for (const theme of ['light', 'dark']) {
    console.log(`── ${theme} ──`)
    await capturePalette(browser, theme)
  }
} finally {
  await browser.close()
}
console.log('\nDone.')
