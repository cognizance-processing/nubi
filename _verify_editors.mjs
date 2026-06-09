/**
 * VerifyAgent browser test.
 *
 * Tests:
 *  (a) SQL editor: invalid SQL → error marker/squiggle, SQL keyword highlighting
 *  (b) Python query block: syntax highlighting + scaffold comment
 *  (c) Dashboard code panel: Monaco JSON/YAML highlighting + problems indicator
 *
 * Mocks all /api/v1/** calls to avoid needing a running backend.
 */

import { chromium } from '@playwright/test'
import { mkdirSync } from 'fs'

mkdirSync('/tmp/editors', { recursive: true })

const b = await chromium.launch({ headless: true })
const ctx = await b.newContext({
  viewport: { width: 1440, height: 900 },
  baseURL: 'http://localhost:5173',
})
const page = await ctx.newPage()

// ── Mock /api/v1/** requests ──────────────────────────────────────────────────
await page.route('**/api/v1/**', async (route) => {
  const url = route.request().url()
  const method = route.request().method()

  // POST /auth/refresh → return access token (session restore)
  if (url.includes('/auth/refresh')) {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ access_token: 'mock-token', token_type: 'bearer' }),
    })
    return
  }

  // GET /auth/me → return user profile
  if (url.includes('/auth/me')) {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        user: { id: 'u1', email: 'test@nubi.dev', name: 'Test User', role: 'admin' },
      }),
    })
    return
  }

  // POST /auth/login
  if (url.includes('/auth/login')) {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        access_token: 'mock-token',
        user: { id: 'u1', email: 'test@nubi.dev', name: 'Test User', role: 'admin' },
      }),
    })
    return
  }

  // POST /query/validate → return a parse error so squiggles appear
  if (url.includes('/query/validate') && method === 'POST') {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        ok: false,
        errors: [{ line: 1, col: 1, message: 'Syntax error near "INVALID"', severity: 'error' }],
      }),
    })
    return
  }

  // GET /query/schema → return a small schema
  if (url.includes('/query/schema')) {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ tables: { demo: ['id', 'name', 'value'] } }),
    })
    return
  }

  // GET /query/registry or /query/list → empty list
  if (url.includes('/query/registry') || url.includes('/query/list')) {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify([]),
    })
    return
  }

  // Connectors / datastores
  if (url.includes('/connectors') || url.includes('/datastores')) {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify([]),
    })
    return
  }

  // Dashboards / boards
  if (url.includes('/dashboards') || url.includes('/boards')) {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify([]),
    })
    return
  }

  // Orgs
  if (url.includes('/orgs')) {
    // Single org or list
    if (url.match(/\/orgs\/[^/]+$/)) {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ id: 'org1', name: 'Test Org', plan: 'starter', slug: 'test' }),
      })
    } else {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify([{ id: 'org1', name: 'Test Org', plan: 'starter', slug: 'test' }]),
      })
    }
    return
  }

  // Projects
  if (url.includes('/projects')) {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify([{ id: 'p1', name: 'Default', slug: 'default' }]),
    })
    return
  }

  // Features / flags
  if (url.includes('/features') || url.includes('/flags')) {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({}),
    })
    return
  }

  // Flows
  if (url.includes('/flows')) {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify([]),
    })
    return
  }

  // Default fallback
  await route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify({}),
  })
})

// ── Navigate to /queries ──────────────────────────────────────────────────────
// Auth context will call /auth/refresh then /auth/me on mount.
// With mocked responses, the user should be set and loading=false.
await page.goto('http://localhost:5173/queries', { waitUntil: 'networkidle', timeout: 40000 })

let finalUrl = page.url()
console.log('Final URL after nav to /queries:', finalUrl)

// If still on login page, auth mocking might not have worked via route intercept
// because /auth/refresh is a POST — let's try the login form
if (finalUrl.includes('/login')) {
  console.log('Still on login page — trying login form...')
  try {
    await page.locator('input[type="email"]').fill('admin@nubi.dev')
    await page.locator('input[type="password"]').fill('admin123')
    await page.locator('button[type="submit"]').click()
    await page.waitForTimeout(3000)
    finalUrl = page.url()
    console.log('URL after login attempt:', finalUrl)
  } catch (e) {
    console.log('Login form attempt error:', e.message)
  }
}

// ============================================================================
// TEST (a): SQL Editor — keywords + error squiggles
// ============================================================================
console.log('\n=== TEST (a): SQL Editor ===')

// Ensure we're on the queries page
if (!page.url().includes('/queries')) {
  await page.goto('http://localhost:5173/queries', { waitUntil: 'domcontentloaded', timeout: 20000 })
  await page.waitForTimeout(2000)
}

console.log('Page URL:', page.url())

// Wait for Monaco to load
await page.waitForSelector('.monaco-editor', { timeout: 15000 }).catch(() => {
  console.log('Monaco editor not found within timeout')
})

const monacoCount = await page.locator('.monaco-editor').count()
console.log('Monaco editor count:', monacoCount)

if (monacoCount > 0) {
  // Get the Monaco editor textarea and type invalid SQL
  // Monaco uses a textarea with class "ime-text-area" for keyboard input
  const monacoArea = page.locator('.monaco-editor textarea.ime-text-area, .monaco-editor textarea').first()
  // Click on the actual editor view first to focus it
  await page.locator('.monaco-editor .view-lines').first().click({ force: true })
  // Select all and replace with invalid SQL
  await page.keyboard.press('ControlOrMeta+a')
  await page.keyboard.type('INVALID SQL GIBBERISH @@#@!', { delay: 20 })
  // Wait for debounced validation (500ms + 200ms buffer)
  await page.waitForTimeout(1200)

  // Check for Monaco error/warning markers
  const squigglyErrors = await page.locator('.monaco-editor .squiggly-error').count()
  const squigglyWarnings = await page.locator('.monaco-editor .squiggly-warning').count()
  const overviewRuler = await page.locator('.monaco-editor .decorationsOverviewRuler').count()
  const errorGlyph = await page.locator('.monaco-editor .codicon-error, .monaco-editor .error-decoration').count()
  console.log('Squiggly error decorations:', squigglyErrors)
  console.log('Squiggly warning decorations:', squigglyWarnings)
  console.log('Overview ruler:', overviewRuler)
  console.log('Error glyph:', errorGlyph)

  // Check Monaco model markers via JS
  const markers = await page.evaluate(() => {
    try {
      if (!window.monaco) return null
      const models = window.monaco.editor.getModels()
      const allMarkers = []
      for (const m of models) {
        const ms = window.monaco.editor.getModelMarkers({ resource: m.uri })
        if (ms.length) allMarkers.push(...ms.map(e => ({ message: e.message, severity: e.severity, line: e.startLineNumber, col: e.startColumn })))
      }
      return allMarkers
    } catch (e) { return null }
  })
  console.log('Monaco markers from JS:', JSON.stringify(markers))

  // Check for token classes (syntax highlighting)
  const tokenInfo = await page.evaluate(() => {
    const spans = document.querySelectorAll('.monaco-editor .view-line span[class*="mtk"]')
    const classes = {}
    spans.forEach(s => {
      const cls = s.className.split(' ').find(c => c.startsWith('mtk'))
      if (cls) classes[cls] = (classes[cls] || 0) + 1
    })
    return classes
  })
  console.log('Token classes (SQL highlighting):', JSON.stringify(tokenInfo))

  await page.screenshot({ path: '/tmp/editors/sql_editor_with_error.png' })
  console.log('Screenshot: /tmp/editors/sql_editor_with_error.png')
} else {
  // Page didn't load properly — take a full-page screenshot for debugging
  await page.screenshot({ path: '/tmp/editors/sql_editor_with_error.png', fullPage: true })
  console.log('No Monaco found — debug screenshot: /tmp/editors/sql_editor_with_error.png')
}

// ============================================================================
// TEST (b): Python Cell
// ============================================================================
console.log('\n=== TEST (b): Python Cell ===')

// Scroll down or look for "Add Python cell" / "Add cell" button
await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight))
await page.waitForTimeout(500)

// Look for Python cell add button at the bottom of QueryWorkspace
const pythonBtnSelectors = [
  'button:has-text("Python")',
  'button[title*="Python"]',
  'button:has-text("Python cell")',
  '[data-testid="add-python-cell"]',
  'button:has-text("+ Python")',
  'button:has-text("Add Python")',
]

let pythonFound = false
for (const sel of pythonBtnSelectors) {
  const cnt = await page.locator(sel).count()
  if (cnt > 0) {
    console.log(`Found Python button: "${sel}" (${cnt})`)
    try {
      await page.locator(sel).first().scrollIntoViewIfNeeded()
      await page.locator(sel).first().click()
      await page.waitForTimeout(1500)
      pythonFound = true
      break
    } catch (e) {
      console.log('Click error:', e.message)
    }
  }
}

if (!pythonFound) {
  // Check all buttons on the page to find the add-cell buttons
  const btnTexts = await page.evaluate(() => {
    const btns = document.querySelectorAll('button')
    return [...btns].map(b => b.textContent?.trim()).filter(t => t && t.length < 50)
  })
  console.log('All buttons on page:', btnTexts.slice(0, 30))

  // Try to find "Python" among them
  const pythonBtn = await page.locator('button').filter({ hasText: /python/i }).first()
  const pythonBtnCount = await pythonBtn.count()
  console.log('Python button (case insensitive):', pythonBtnCount)
  if (pythonBtnCount > 0) {
    await pythonBtn.click()
    await page.waitForTimeout(1500)
    pythonFound = true
  }
}

// Wait for Monaco editor to appear in Python context
await page.waitForTimeout(1000)

const allMonacoAfterPython = await page.locator('.monaco-editor').count()
console.log('Monaco editor count after Python cell:', allMonacoAfterPython)

// Check for the Python scaffold comment in Monaco
const scaffoldCheck = await page.evaluate(() => {
  if (!window.monaco) return { found: false, reason: 'no window.monaco' }
  try {
    const models = window.monaco.editor.getModels()
    for (const m of models) {
      const val = m.getValue()
      if (val.includes('inputs') && (val.includes('result') || val.includes('# Python'))) {
        return { found: true, snippet: val.slice(0, 300), languageId: m.getLanguageId() }
      }
    }
    return {
      found: false,
      reason: 'no model with scaffold',
      modelCount: models.length,
      models: models.map(m => ({ lang: m.getLanguageId(), snippet: m.getValue().slice(0, 80) }))
    }
  } catch (e) {
    return { found: false, reason: e.message }
  }
})
console.log('Python scaffold check:', JSON.stringify(scaffoldCheck, null, 2))

// Check Python token classes
const pythonTokenInfo = await page.evaluate(() => {
  const editors = document.querySelectorAll('.monaco-editor')
  // Find the last editor (most likely the Python one)
  const lastEditor = editors[editors.length - 1]
  if (!lastEditor) return {}
  const spans = lastEditor.querySelectorAll('.view-line span[class*="mtk"]')
  const classes = {}
  spans.forEach(s => {
    const cls = s.className.split(' ').find(c => c.startsWith('mtk'))
    if (cls) classes[cls] = (classes[cls] || 0) + 1
  })
  return classes
})
console.log('Python token classes:', JSON.stringify(pythonTokenInfo))

// Check for the visible comment text
const lineTexts = await page.evaluate(() => {
  const editors = document.querySelectorAll('.monaco-editor')
  const lastEditor = editors[editors.length - 1]
  if (!lastEditor) return []
  const lines = lastEditor.querySelectorAll('.view-line')
  return [...lines].map(l => l.textContent).filter(t => t && t.trim()).slice(0, 10)
})
console.log('Visible Python editor lines:', lineTexts)

await page.screenshot({ path: '/tmp/editors/python_cell.png' })
console.log('Screenshot: /tmp/editors/python_cell.png')

// ============================================================================
// TEST (c): Dashboard Code Panel
// ============================================================================
console.log('\n=== TEST (c): Dashboard Code Panel ===')
await page.goto('http://localhost:5173/editor', { waitUntil: 'domcontentloaded', timeout: 30000 })
await page.waitForTimeout(3000)

console.log('Dashboard editor URL:', page.url())

// Look for Code button
const codeBtn = page.locator('button:has-text("Code"), [title*="code"], [title*="Code"]').first()
const codeBtnCount = await codeBtn.count()
console.log('Code button count:', codeBtnCount)

if (codeBtnCount > 0) {
  await codeBtn.scrollIntoViewIfNeeded()
  await codeBtn.click()
  await page.waitForTimeout(2000)
  console.log('Code panel opened')
} else {
  // Debug: list visible buttons
  const btns = await page.evaluate(() => {
    return [...document.querySelectorAll('button')].map(b => ({
      text: b.textContent?.trim(),
      title: b.title,
      dataTestId: b.dataset.testid,
    })).filter(b => b.text || b.title).slice(0, 30)
  })
  console.log('Visible buttons on editor page:', JSON.stringify(btns.slice(0, 20)))
}

// Check Monaco on dashboard page
const dashMonaco = await page.locator('.monaco-editor').count()
console.log('Monaco editor count on dashboard page:', dashMonaco)

if (dashMonaco > 0) {
  // Check for YAML/JSON token classes
  const dashTokenInfo = await page.evaluate(() => {
    const spans = document.querySelectorAll('.monaco-editor .view-line span[class*="mtk"]')
    const classes = {}
    spans.forEach(s => {
      const cls = s.className.split(' ').find(c => c.startsWith('mtk'))
      if (cls) classes[cls] = (classes[cls] || 0) + 1
    })
    return classes
  })
  console.log('Dashboard code token classes:', JSON.stringify(dashTokenInfo))

  // Check for problems indicator
  const problemsText = await page.evaluate(() => {
    const allText = [...document.querySelectorAll('*')].map(el => el.textContent?.trim()).filter(t => t && t.includes('problem'))
    return allText.slice(0, 5)
  })
  console.log('Problems indicator text:', problemsText)

  // Check Monaco model language
  const modelInfo = await page.evaluate(() => {
    if (!window.monaco) return null
    try {
      const models = window.monaco.editor.getModels()
      return models.map(m => ({ lang: m.getLanguageId(), snippet: m.getValue().slice(0, 60) }))
    } catch (e) { return null }
  })
  console.log('Monaco models (dashboard):', JSON.stringify(modelInfo))
}

await page.screenshot({ path: '/tmp/editors/dashboard_code_panel.png' })
console.log('Screenshot: /tmp/editors/dashboard_code_panel.png')

// ── SUMMARY ──────────────────────────────────────────────────────────────────
console.log('\n=== DONE — screenshots in /tmp/editors/ ===')

await b.close()
