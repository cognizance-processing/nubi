/**
 * e2e/admin.spec.js
 *
 * Superadmin portal (/admin):
 *   1. The seeded superuser (admin@nubi.dev, is_superadmin=true) sees the
 *      /admin overview with populated stat cards.
 *   2. A freshly-registered normal user visiting /admin gets the generic
 *      404-style not-found view (the portal's existence is never revealed)
 *      and has NO "Admin" link in the nav.
 *
 * Conventions follow e2e/helpers/auth.js + e2e/onboarding.spec.js.
 */

import { test, expect } from '@playwright/test'
import { loginAs, randomEmail } from './helpers/auth.js'

const PASSWORD = 'NewPassword123!'

// ---------------------------------------------------------------------------
// 1. Superuser sees the admin overview
// ---------------------------------------------------------------------------

test('superuser sees /admin overview with populated stat cards', async ({ page }) => {
  await loginAs(page)

  await page.goto('/admin')

  // The overview container renders only after a successful /admin/overview fetch.
  await expect(page.getByTestId('admin-overview')).toBeVisible({ timeout: 20_000 })

  // All 7 stat cards render with a numeric value.
  for (const key of ['users', 'orgs', 'projects', 'queries', 'boards', 'flows', 'datastores']) {
    const card = page.getByTestId(`admin-stat-${key}`)
    await expect(card).toBeVisible()
    await expect(card).toContainText(/\d/)
  }

  // At least the seeded superuser exists, so the users count is ≥ 1.
  const usersText = await page.getByTestId('admin-stat-users').innerText()
  const usersCount = parseInt(usersText.replace(/\D+/g, ''), 10)
  expect(usersCount).toBeGreaterThanOrEqual(1)

  // Tabs navigate within the portal: Users page renders its table chrome.
  await page.getByRole('link', { name: 'Users', exact: true }).click()
  await expect(page.getByTestId('admin-users')).toBeVisible({ timeout: 20_000 })
})

// ---------------------------------------------------------------------------
// 2. Normal user gets the 404-style view and no admin nav
// ---------------------------------------------------------------------------

test('normal user visiting /admin sees not-found and no Admin nav', async ({ page }) => {
  // Register a fresh (non-superadmin) user through the UI.
  const email = randomEmail()
  await page.goto('/register')
  await page.locator('#name').fill('Admin E2E Normal User')
  await page.locator('#orgName').fill('Admin E2E Org')
  await page.locator('#projectName').fill('Admin E2E Project')
  await page.locator('input[type="email"]').fill(email)
  await page.locator('input[type="password"]').fill(PASSWORD)
  const demo = page.locator('#demoProject')
  if (await demo.isChecked()) await demo.uncheck()
  await page.locator('button[type="submit"]').click()
  await page.waitForURL(url => !url.pathname.startsWith('/register'), { timeout: 30_000 })

  // No "Admin" link anywhere in the navigation for a normal user.
  await page.goto('/home')
  await expect(page.getByRole('heading', { level: 1 })).toBeVisible({ timeout: 20_000 })
  await expect(page.getByRole('link', { name: 'Admin', exact: true })).toHaveCount(0)

  // Visiting /admin directly renders the generic 404-style view — never the
  // portal, never a "forbidden" hint that the portal exists.
  await page.goto('/admin')
  await expect(page.getByText('Page not found')).toBeVisible({ timeout: 20_000 })
  await expect(page.getByText('404')).toBeVisible()
  await expect(page.getByTestId('admin-overview')).toHaveCount(0)
  await expect(page.getByTestId('admin-stat-users')).toHaveCount(0)
})
