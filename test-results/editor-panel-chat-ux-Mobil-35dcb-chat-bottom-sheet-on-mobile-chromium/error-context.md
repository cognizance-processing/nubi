# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: editor-panel-chat-ux.spec.js >> Mobile 390px — editor panel icon toggles >> clicking Chat opens chat bottom sheet on mobile
- Location: e2e/editor-panel-chat-ux.spec.js:159:3

# Error details

```
Error: expect(locator).toBeVisible() failed

Locator: getByTestId('editor-title')
Expected: visible
Error: element(s) not found

Call log:
  - Expect "toBeVisible" with timeout 30000ms
  - waiting for getByTestId('editor-title')

```

```yaml
- link "Nubi Nubi":
  - /url: /
  - img "Nubi"
  - text: Nubi
- button "Switch to dark mode"
- heading "Welcome back" [level=1]
- paragraph: Sign in to your Nubi account to continue
- button "Continue with Google"
- text: or sign in with email Email address
- textbox "Email address":
  - /placeholder: you@example.com
- text: Password
- textbox "Password":
  - /placeholder: ••••••••
- button "Sign in"
- text: Don't have an account?
- link "Create one":
  - /url: /register
- paragraph: By continuing, you agree to our Terms and Privacy Policy
```

```
Error: write EPIPE
```

# Test source

```ts
  1   | /**
  2   |  * e2e/editor-panel-chat-ux.spec.js
  3   |  *
  4   |  * Verifies the refactored editor panel + chat UX:
  5   |  *
  6   |  * (a) Desktop /editor — topbar shows Add/Configure/Layout/Chat ICON buttons;
  7   |  *     clicking Chat opens chat in the RHS sidebar; global MessageSquare chat
  8   |  *     button is NOT present; only ONE right sidebar visible.
  9   |  *
  10  |  * (b) Non-editor page (/home) — global chat MessageSquare button IS present
  11  |  *     and opens the global chat panel.
  12  |  *
  13  |  * (c) Mobile 390px and tablet 820px — four icon toggles are reachable in the
  14  |  *     topbar and open their panel/sheet; no horizontal overflow.
  15  |  */
  16  | 
  17  | import { test, expect } from '@playwright/test'
  18  | import { loginAs } from './helpers/auth.js'
  19  | 
  20  | // Shared login helper that navigates straight to the editor
  21  | async function openEditor(page) {
  22  |   await loginAs(page)
  23  |   await page.goto('/editor')
> 24  |   await expect(page.getByTestId('editor-title')).toBeVisible({ timeout: 30_000 })
      |   ^ Error: write EPIPE
  25  | }
  26  | 
  27  | // ────────────────────────────────────────────────────────────────────────────
  28  | // (a) Desktop: editor panel icons + chat in RHS sidebar
  29  | // ────────────────────────────────────────────────────────────────────────────
  30  | 
  31  | test.describe('Desktop /editor — panel icon toggles + single chat', () => {
  32  |   test.use({ viewport: { width: 1280, height: 800 } })
  33  | 
  34  |   test('shows Add/Configure/Layout/Chat icon buttons in the topbar', async ({ page }) => {
  35  |     await openEditor(page)
  36  | 
  37  |     const toggles = page.getByTestId('editor-panel-toggles')
  38  |     await expect(toggles).toBeVisible()
  39  | 
  40  |     // All four panel toggle buttons present
  41  |     await expect(page.getByTestId('panel-toggle-add')).toBeVisible()
  42  |     await expect(page.getByTestId('panel-toggle-config')).toBeVisible()
  43  |     await expect(page.getByTestId('panel-toggle-board')).toBeVisible()
  44  |     await expect(page.getByTestId('panel-toggle-chat')).toBeVisible()
  45  |   })
  46  | 
  47  |   test('global chat button is NOT present in the editor', async ({ page }) => {
  48  |     await openEditor(page)
  49  |     await expect(page.getByTestId('global-chat-btn')).not.toBeVisible()
  50  |   })
  51  | 
  52  |   test('clicking Chat opens chat in the RHS sidebar (no global chat panel)', async ({ page }) => {
  53  |     await openEditor(page)
  54  |     await page.getByTestId('panel-toggle-chat').click()
  55  | 
  56  |     // The global AI chat panel aside should NOT be visible (page owns chat)
  57  |     const globalChatAside = page.locator('aside[aria-label="AI chat panel"]')
  58  |     await expect(globalChatAside).not.toBeVisible()
  59  | 
  60  |     // The editor's own right sidebar should be open
  61  |     // Verify by checking the "Chat" panel title text is visible in the sidebar header
  62  |     await expect(page.getByText('Chat', { exact: true }).first()).toBeVisible({ timeout: 5_000 })
  63  |   })
  64  | 
  65  |   test('editor panel icons are icon-only (no visible text labels)', async ({ page }) => {
  66  |     await openEditor(page)
  67  |     // The toggle buttons should not have visible text — only icons (SVG)
  68  |     const addBtn = page.getByTestId('panel-toggle-add')
  69  |     await expect(addBtn).toBeVisible()
  70  |     const btnText = await addBtn.textContent()
  71  |     expect((btnText ?? '').trim()).toBe('')
  72  |   })
  73  | 
  74  |   test('clicking active panel toggle collapses the sidebar', async ({ page }) => {
  75  |     await openEditor(page)
  76  | 
  77  |     // The Add panel is open by default; RHS aside visible
  78  |     const aside = page.locator('aside').last()
  79  |     await expect(aside).toBeVisible()
  80  | 
  81  |     // Click Add toggle (active) → collapses
  82  |     await page.getByTestId('panel-toggle-add').click()
  83  |     await expect(aside).not.toBeVisible({ timeout: 3_000 })
  84  |   })
  85  | 
  86  |   test('clicking Add toggle re-opens the sidebar after collapsing', async ({ page }) => {
  87  |     await openEditor(page)
  88  | 
  89  |     // Collapse
  90  |     await page.getByTestId('panel-toggle-add').click()
  91  |     await page.waitForTimeout(200)
  92  | 
  93  |     // Re-open Add
  94  |     await page.getByTestId('panel-toggle-add').click()
  95  |     await expect(page.locator('aside').last()).toBeVisible({ timeout: 5_000 })
  96  |   })
  97  | 
  98  |   test('clicking Layout panel shows Dashboard settings', async ({ page }) => {
  99  |     await openEditor(page)
  100 |     await page.getByTestId('panel-toggle-board').click()
  101 |     await expect(page.getByText('Dashboard').first()).toBeVisible({ timeout: 5_000 })
  102 |   })
  103 | })
  104 | 
  105 | // ────────────────────────────────────────────────────────────────────────────
  106 | // (b) Non-editor page (/home) — global chat button present and works
  107 | // ────────────────────────────────────────────────────────────────────────────
  108 | 
  109 | test.describe('Non-editor page (/home) — global chat is available', () => {
  110 |   test.use({ viewport: { width: 1280, height: 800 } })
  111 | 
  112 |   test('global chat MessageSquare button IS present on /home', async ({ page }) => {
  113 |     await loginAs(page)
  114 |     await page.goto('/home')
  115 |     await page.waitForLoadState('networkidle')
  116 |     await expect(page.getByTestId('global-chat-btn')).toBeVisible({ timeout: 10_000 })
  117 |   })
  118 | 
  119 |   test('global chat button opens the global chat panel', async ({ page }) => {
  120 |     await loginAs(page)
  121 |     await page.goto('/home')
  122 |     await page.waitForLoadState('networkidle')
  123 |     await page.getByTestId('global-chat-btn').click()
  124 |     const globalChatAside = page.locator('aside[aria-label="AI chat panel"]')
```