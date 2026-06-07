/**
 * e2e/active_org.spec.js
 *
 * Verifies two behaviours introduced by the multi-tenant org switching + auth
 * session fixes:
 *
 * 1. Auth session survives past the page load:
 *    - Log in as the admin user.
 *    - After login, navigate to the home page which triggers AuthContext's
 *      restoreSession() useEffect — this calls POST /auth/refresh.
 *    - Confirm that at least one refresh call returns 200 (session is restored).
 *      Note: multiple concurrent renderings may fire parallel refreshes; the
 *      second one will 401 because the first already rotated the token — this
 *      is expected behaviour and not a regression.
 *
 * 2. Resource fetch succeeds after session restore:
 *    - /orgs returns 200, confirming the access token is usable.
 *
 * 3. No 403s on resource fetches — org context is valid after login.
 *
 * These tests run against the live stack (localhost:5173 + localhost:8000).
 */

import { test, expect } from '@playwright/test'

const ADMIN_EMAIL = 'admin@nubi.dev'
const ADMIN_PASSWORD = 'nubi-admin-2026'

// ---------------------------------------------------------------------------
// Helper: login and wait for dashboard
// ---------------------------------------------------------------------------
async function loginAsAdmin(page) {
  await page.goto('/login')
  await page.locator('input[type="email"]').fill(ADMIN_EMAIL)
  await page.locator('input[type="password"]').fill(ADMIN_PASSWORD)
  await page.locator('button[type="submit"]').first().click()
  await page.waitForURL(url => !url.pathname.startsWith('/login'), {
    timeout: 20_000,
  })
}

// ---------------------------------------------------------------------------
// Test 1: /auth/refresh returns 200 at least once after login
//
// Before the fix: ALL refreshes returned 401 because the browser would not
// send SameSite=Lax cookies on cross-origin fetch() calls (5173→8000).
//
// After the fix: the Vite proxy makes the cookie same-origin so at least one
// refresh succeeds, restoring the session.
// ---------------------------------------------------------------------------

test('POST /auth/refresh returns 200 at least once after login (session restored)', async ({
  page,
}) => {
  // Login first — sets the HttpOnly cookie via the Vite proxy (same-origin).
  await loginAsAdmin(page)

  // Capture refresh responses that happen AFTER the login redirect.
  const refreshStatuses = []
  page.on('response', res => {
    if (res.url().includes('/auth/refresh')) {
      refreshStatuses.push(res.status())
    }
  })

  // Navigate to the root — this triggers AuthContext.restoreSession() which
  // calls POST /auth/refresh to silently re-issue the access token.
  await page.goto('/')
  await page.waitForTimeout(4000) // let the async restore settle

  // At least one refresh call must have been observed.
  expect(refreshStatuses.length, 'No /auth/refresh calls observed after navigation').toBeGreaterThan(0)

  // AT LEAST ONE refresh must succeed (200).  There may be concurrent requests
  // that 401 because the first one already rotated the token — that is expected.
  const succeeded = refreshStatuses.filter(s => s === 200)
  expect(
    succeeded.length,
    `Expected at least one 200 from /auth/refresh, all returned: ${refreshStatuses.join(', ')}`,
  ).toBeGreaterThan(0)
})

// ---------------------------------------------------------------------------
// Test 2: /auth/me returns 200 after session restore
// ---------------------------------------------------------------------------

test('GET /auth/me returns 200 after session restore', async ({ page }) => {
  await loginAsAdmin(page)

  const meStatuses = []
  page.on('response', res => {
    if (res.url().includes('/auth/me')) {
      meStatuses.push(res.status())
    }
  })

  await page.goto('/')
  await page.waitForTimeout(4000)

  const succeeded = meStatuses.filter(s => s === 200)
  expect(
    succeeded.length,
    `Expected at least one 200 from /auth/me, got: ${meStatuses}`,
  ).toBeGreaterThan(0)
})

// ---------------------------------------------------------------------------
// Test 3: /orgs returns 200 after session restore (org context loads)
// ---------------------------------------------------------------------------

test('GET /orgs returns 200 after session restore', async ({ page }) => {
  await loginAsAdmin(page)

  const orgsStatuses = []
  page.on('response', res => {
    if (res.url().includes('/api/v1/orgs') && !res.url().includes('/orgs/')) {
      orgsStatuses.push(res.status())
    }
  })

  // Navigate to an authenticated page — OrgProvider only mounts on auth routes.
  await page.goto('/home')
  await page.waitForTimeout(4000)

  const succeeded = orgsStatuses.filter(s => s === 200)
  expect(
    succeeded.length,
    `Expected at least one 200 from /orgs, got: ${orgsStatuses}`,
  ).toBeGreaterThan(0)
})

// ---------------------------------------------------------------------------
// Test 4: No 403s on resource fetches — org context is valid after login
// ---------------------------------------------------------------------------

test('no 403 errors on resource fetches after login', async ({ page }) => {
  await loginAsAdmin(page)

  const forbidden = []
  page.on('response', res => {
    const url = res.url()
    if (url.includes('/api/v1/') && !url.includes('/auth/') && res.status() === 403) {
      forbidden.push(url)
    }
  })

  await page.goto('/dashboard')
  await page.waitForTimeout(3000)

  expect(
    forbidden,
    `Unexpected 403s on resource fetches: ${forbidden.join(', ')}`,
  ).toHaveLength(0)
})
