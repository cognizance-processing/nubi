/**
 * e2e/onboarding.spec.js
 *
 * Supabase-style forced onboarding:
 *   1. Register with org/project names + demo checkbox ON → the named project
 *      AND the seeded "Demo" project both exist (asserted via the API).
 *   2. Register with the demo checkbox OFF → only the named project exists.
 *   3. Onboarding guard: an authed user whose GET /orgs returns [] (stubbed —
 *      email registration always auto-creates an org server-side) is forced
 *      to /onboarding from any app route, and creating an org + project there
 *      lands them in /home.
 *
 * Conventions follow e2e/auth.spec.js + e2e/helpers/auth.js.
 */

import { test, expect } from '@playwright/test'
import { randomEmail } from './helpers/auth.js'

const PASSWORD = 'NewPassword123!'

/**
 * Register a fresh user through the /register UI.
 * @param {import('@playwright/test').Page} page
 * @param {{ email: string, orgName: string, projectName: string, demo: boolean }} opts
 */
async function registerViaUi(page, { email, orgName, projectName, demo }) {
  await page.goto('/register')
  await page.getByLabel('Full name', { exact: true }).fill('Onboarding Tester')
  await page.getByLabel('Organization', { exact: true }).fill(orgName)
  await page.getByLabel('First project', { exact: true }).fill(projectName)
  await page.locator('input[type="email"]').fill(email)
  await page.locator('input[type="password"]').fill(PASSWORD)

  const checkbox = page.locator('#reg-demo')
  await expect(checkbox).toBeChecked() // default ON
  if (!demo) await checkbox.uncheck()

  await page.locator('button[type="submit"]').click()
  await page.waitForURL(url => !url.pathname.startsWith('/register'), { timeout: 30_000 })
}

/**
 * Fetch the user's project names via the API (login → orgs → projects).
 * @param {import('@playwright/test').Page} page
 * @param {string} email
 * @returns {Promise<string[]>}
 */
async function fetchProjectNames(page, email) {
  const loginRes = await page.request.post('/api/v1/auth/login', {
    data: { email, password: PASSWORD },
  })
  expect(loginRes.ok()).toBeTruthy()
  const { access_token: token } = await loginRes.json()

  const orgsRes = await page.request.get('/api/v1/orgs', {
    headers: { Authorization: `Bearer ${token}` },
  })
  expect(orgsRes.ok()).toBeTruthy()
  const { orgs } = await orgsRes.json()
  expect(orgs.length).toBeGreaterThan(0)

  const projectsRes = await page.request.get('/api/v1/projects', {
    headers: { Authorization: `Bearer ${token}`, 'X-Org-Id': orgs[0].id },
  })
  expect(projectsRes.ok()).toBeTruthy()
  const projects = await projectsRes.json()
  return (Array.isArray(projects) ? projects : projects.projects ?? []).map(p => p.name)
}

/**
 * Count the boards in the user's first org + project (login → orgs → projects → boards).
 * @param {import('@playwright/test').Page} page
 * @param {string} email
 * @returns {Promise<number>}
 */
async function fetchBoardCount(page, email) {
  const loginRes = await page.request.post('/api/v1/auth/login', { data: { email, password: PASSWORD } })
  const { access_token: token } = await loginRes.json()
  const auth = { Authorization: `Bearer ${token}` }
  const { orgs } = await (await page.request.get('/api/v1/orgs', { headers: auth })).json()
  const projects = await (await page.request.get('/api/v1/projects', { headers: { ...auth, 'X-Org-Id': orgs[0].id } })).json()
  const project = (Array.isArray(projects) ? projects : projects.projects ?? [])[0]
  const boardsRes = await page.request.get('/api/v1/boards', { headers: { ...auth, 'X-Org-Id': orgs[0].id, 'X-Project-Id': project.id } })
  expect(boardsRes.ok()).toBeTruthy()
  const boards = await boardsRes.json()
  return (Array.isArray(boards) ? boards : boards.boards ?? []).length
}

// ---------------------------------------------------------------------------
// 1. Register with demo checkbox ON → the named project, seeded with the sample bundle
// ---------------------------------------------------------------------------

test('register with demo project ON → the named project holds the sample boards', async ({ page }) => {
  const email = randomEmail()
  await registerViaUi(page, {
    email,
    orgName: 'Onboard Org A',
    projectName: 'Main Project',
    demo: true,
  })

  // Lands in the app (authed route, not /login)
  await expect(page).not.toHaveURL(/\/login/)

  // The demo bundle is seeded into the org's own project; there is no separate "Demo" project.
  const names = await fetchProjectNames(page, email)
  expect(names).toEqual(['Main Project'])
  expect(await fetchBoardCount(page, email)).toBeGreaterThan(0)
})

// ---------------------------------------------------------------------------
// 2. Register with demo checkbox OFF → only the named project
// ---------------------------------------------------------------------------

test('register with demo project OFF → only the named project exists', async ({ page }) => {
  const email = randomEmail()
  await registerViaUi(page, {
    email,
    orgName: 'Onboard Org B',
    projectName: 'Solo Project',
    demo: false,
  })

  await expect(page).not.toHaveURL(/\/login/)

  const names = await fetchProjectNames(page, email)
  expect(names).toEqual(['Solo Project'])
  expect(await fetchBoardCount(page, email)).toBe(0)
})

// ---------------------------------------------------------------------------
// 3. Onboarding guard — org-less authed user is forced to /onboarding
// ---------------------------------------------------------------------------

test('org-less user is redirected to /onboarding and can create a workspace', async ({ page }) => {
  const email = randomEmail()

  // Create the user via the API. The refresh cookie lands in the browser
  // context's cookie jar, so the app silently restores the session.
  // NOTE: email registration ALWAYS auto-creates an org server-side, so we
  // simulate an org-less (OAuth-style) user by stubbing GET /orgs to []
  // until the user creates an org through the onboarding page.
  const regRes = await page.request.post('/api/v1/auth/register', {
    data: { name: 'Orgless User', email, password: PASSWORD },
  })
  expect(regRes.ok()).toBeTruthy()

  let orgCreated = false
  await page.route(
    url => url.pathname === '/api/v1/orgs',
    async (route) => {
      const method = route.request().method()
      if (method === 'POST') {
        orgCreated = true
        return route.continue()
      }
      if (method === 'GET' && !orgCreated) {
        return route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({ orgs: [] }),
        })
      }
      return route.continue()
    },
  )

  // Any app-shell route must bounce to /onboarding.
  await page.goto('/home')
  await page.waitForURL(/\/onboarding/, { timeout: 20_000 })
  await expect(page.getByRole('heading', { name: /create (a new organization|your workspace)/i })).toBeVisible()

  // Create org + project from the onboarding page (skip the demo seed).
  await page.locator('#ob-org-name').fill('Guarded Org')
  await page.locator('#ob-project-name').fill('Guarded Project')
  await page.locator('#ob-demo-data').uncheck()
  await page.locator('button[type="submit"]').click()

  // Hard redirect to /home; the shell renders with the new org.
  await page.waitForURL(/\/home/, { timeout: 30_000 })
  await expect(page.getByRole('link', { name: 'Home' }).first()).toBeVisible({ timeout: 15_000 })
})
