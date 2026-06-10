/**
 * e2e/auth.spec.js
 *
 * Auth flows:
 *   1. Login with seeded admin credentials → lands on /home.
 *   2. Register a fresh random user → lands on /home (via /dashboard redirect).
 *   3. Invalid login shows an error message.
 *   4. Login page has a "Create one" link that goes to /register.
 */

import { test, expect } from '@playwright/test'
import { loginAs, randomEmail, DEMO_EMAIL, DEMO_PASSWORD } from './helpers/auth.js'

// ---------------------------------------------------------------------------
// Login with seeded credentials
// ---------------------------------------------------------------------------

test('login with seeded admin user → lands on /home', async ({ page }) => {
  await loginAs(page)

  // /dashboard redirects to /home
  await expect(page).toHaveURL(/\/(home|dashboard)/)

  // The page should show authenticated content — sidebar nav link to Home
  await expect(page.getByRole('link', { name: 'Home' }).first()).toBeVisible({ timeout: 15_000 })
})

// ---------------------------------------------------------------------------
// Login failure shows an error
// ---------------------------------------------------------------------------

test('login with wrong password → shows error alert', async ({ page }) => {
  await page.goto('/login')
  await page.locator('input[type="email"]').fill(DEMO_EMAIL)
  await page.locator('input[type="password"]').fill('definitely-wrong-password')
  await page.locator('button[type="submit"]').click()

  // Error banner has role="alert"
  const alert = page.getByRole('alert')
  await expect(alert).toBeVisible({ timeout: 10_000 })
  // Stay on /login
  await expect(page).toHaveURL(/\/login/)
})

// ---------------------------------------------------------------------------
// Register a fresh random user
// ---------------------------------------------------------------------------

test('register a fresh random user → lands on an authed route', async ({ page }) => {
  const email = randomEmail()
  const password = 'NewPassword123!'

  await page.goto('/register')

  await page.locator('#name').fill('E2E Tester')
  await page.locator('#orgName').fill('E2E Org')
  await page.locator('#projectName').fill('E2E Project')
  await page.locator('input[type="email"]').fill(email)
  await page.locator('input[type="password"]').fill(password)
  await page.locator('button[type="submit"]').click()

  // After successful registration the app navigates away from /register
  // (register.jsx navigates to /dashboard which redirects to /home)
  await page.waitForURL(url => !url.pathname.startsWith('/register'), { timeout: 20_000 })
  // Must be on an authed route — not back on /login
  await expect(page).not.toHaveURL(/\/login/)
})

// ---------------------------------------------------------------------------
// Register page is reachable from the login page
// ---------------------------------------------------------------------------

test('login page has a "Create one" link that goes to /register', async ({ page }) => {
  await page.goto('/login')
  await page.getByRole('link', { name: /create one/i }).click()
  await expect(page).toHaveURL(/\/register/)
})
