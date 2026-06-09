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
  await page.locator('#name').fill('Onboarding Tester')
  await page.locator('#orgName').fill(orgName)
  await page.locator('#projectName').fill(projectName)
  await page.locator('input[type="email"]').fill(email)
  await page.locator('input[type="password"]').fill(PASSWORD)

  const checkbox = page.locator('#demoProject')
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

// ---------------------------------------------------------------------------
// 1. Register with demo checkbox ON → named project + Demo project
// ---------------------------------------------------------------------------

test('register with demo project ON → named project and Demo both exist', async ({ page }) => {
  const email = randomEmail()
  await registerViaUi(page, {
    email,
    orgName: 'Onboard Org A',
    projectName: 'Main Project',
    demo: true,
  })

  // Lands in the app (authed route, not /login)
  await expect(page).not.toHaveURL(/\/login/)

  const names = await fetchProjectNames(page, email)
  expect(names).toContain('Main Project')
  expect(names).toContain('Demo')
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
  await expect(page.getByRole('heading', { name: /create a new organization/i })).toBeVisible()

  // Create org + project from the onboarding page (skip the demo seed).
  await page.locator('#ob-org-name').fill('Guarded Org')
  await page.locator('#ob-project-name').fill('Guarded Project')
  await page.locator('#ob-demo-project').uncheck()
  await page.locator('button[type="submit"]').click()

  // Hard redirect to /home; the shell renders with the new org.
  await page.waitForURL(/\/home/, { timeout: 30_000 })
  await expect(page.getByRole('link', { name: 'Home' }).first()).toBeVisible({ timeout: 15_000 })
})
