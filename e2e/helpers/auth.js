/**
 * e2e/helpers/auth.js
 *
 * Shared login helper for Playwright specs.
 * Logs in with the seeded admin credentials and waits until the authed
 * route is confirmed (redirected away from /login).
 */

export const DEMO_EMAIL = 'admin@nubi.dev'
export const DEMO_PASSWORD = 'nubi-admin-2026'

/**
 * Log in using the seeded admin account.
 * After this call the page is on /home (or the post-login redirect).
 *
 * Uses `button[type="submit"]` to avoid matching the "Sign in with Google"
 * button which also contains "Sign in" in its label.
 *
 * @param {import('@playwright/test').Page} page
 * @param {{ email?: string, password?: string }} [opts]
 */
export async function loginAs(page, { email = DEMO_EMAIL, password = DEMO_PASSWORD } = {}) {
  await page.goto('/login')
  await page.locator('input[type="email"]').fill(email)
  await page.locator('input[type="password"]').fill(password)
  await page.locator('button[type="submit"]').click()
  // Wait until we have left the login page
  await page.waitForURL(url => !url.pathname.startsWith('/login'), { timeout: 20_000 })
}

/**
 * Generate a unique random email for registration tests.
 * @returns {string}
 */
export function randomEmail() {
  return `e2e_${Date.now()}_${Math.random().toString(36).slice(2, 8)}@test.nubi.dev`
}
