/**
 * e2e/ai-chat.spec.js
 *
 * Tests for the AI Chat Panel (ChatPanel.jsx) accessed via the topbar:
 *
 *   1. Chat icon in the topbar opens the chat panel.
 *   2. Chat panel has a textarea and send button.
 *   3. Typing a message and sending shows the user message in the chat log.
 *   4. Suggestion chips are displayed in the empty state.
 *   5. Chat panel can be closed.
 *
 * Also covers the editor's "Ask AI" panel:
 *   6. The Ask AI button in the editor opens the AI panel with a textarea.
 *   7. Typing a prompt enables the Generate button.
 *
 * NOTE: The AppShell renders TWO ChatPanel instances (mobile overlay + desktop
 * slide-in). On a 1280×800 viewport only the desktop panel is visible.
 * We scope to `aside[aria-label="AI chat panel"]` to target the desktop panel.
 */

import { test, expect } from '@playwright/test'
import { loginAs } from './helpers/auth.js'

// Helper: get the desktop chat panel aside element
const desktopPanel = (page) => page.locator('aside[aria-label="AI chat panel"]')

// ---------------------------------------------------------------------------
// Chat Panel tests (topbar chat toggle)
// ---------------------------------------------------------------------------

test.describe('Chat Panel (topbar)', () => {
  test.beforeEach(async ({ page }) => {
    await loginAs(page)
    await page.goto('/home')
    // Wait for home page to load
    await expect(page.getByRole('heading', { level: 1 }).first()).toBeVisible({ timeout: 20_000 })
  })

  // -------------------------------------------------------------------------

  test('chat icon in topbar opens the chat panel', async ({ page }) => {
    // The chat toggle button in the topbar (aria-label: "Open AI chat")
    const chatToggle = page.getByRole('button', { name: /open ai chat/i })
    await expect(chatToggle).toBeVisible({ timeout: 10_000 })

    await chatToggle.click()

    // The desktop slide-in panel should become visible (non-zero width, md:flex)
    const panel = desktopPanel(page)
    await expect(panel).toBeVisible({ timeout: 10_000 })

    // The chat textarea should be visible inside the desktop panel
    const chatInput = panel.locator('textarea[aria-label="Chat input"]')
    await expect(chatInput).toBeVisible({ timeout: 10_000 })
  })

  // -------------------------------------------------------------------------

  test('chat panel shows suggestion chips in empty state', async ({ page }) => {
    const chatToggle = page.getByRole('button', { name: /open ai chat/i })
    await chatToggle.click()

    const panel = desktopPanel(page)
    await expect(panel).toBeVisible({ timeout: 10_000 })

    // "Ask Nubi anything" heading in the empty state
    const emptyState = panel.getByText(/ask nubi anything/i)
      .or(panel.getByText(/build a sales dashboard/i))
      .or(panel.getByText(/show revenue by region/i))

    await expect(emptyState.first()).toBeVisible({ timeout: 10_000 })
  })

  // -------------------------------------------------------------------------

  test('typing and sending a message shows it in the chat log', async ({ page }) => {
    const chatToggle = page.getByRole('button', { name: /open ai chat/i })
    await chatToggle.click()

    const panel = desktopPanel(page)
    const chatInput = panel.locator('textarea[aria-label="Chat input"]')
    await expect(chatInput).toBeVisible({ timeout: 10_000 })

    // Type a message
    await chatInput.fill('Hello Nubi!')

    // Send via the send button inside the panel
    const sendBtn = panel.getByRole('button', { name: /send message/i })
    await expect(sendBtn).toBeVisible()
    await sendBtn.click()

    // The user message should appear in the chat log (inside the panel)
    await expect(panel.getByText('Hello Nubi!')).toBeVisible({ timeout: 10_000 })
  })

  // -------------------------------------------------------------------------

  test('chat panel can be closed via the close button', async ({ page }) => {
    const chatToggle = page.getByRole('button', { name: /open ai chat/i })
    await chatToggle.click()

    const panel = desktopPanel(page)
    await expect(panel).toBeVisible({ timeout: 10_000 })

    // Close the panel via the close button inside
    const closeBtn = panel.getByRole('button', { name: /close chat panel/i })
    await closeBtn.click()

    // Panel should collapse (aria-hidden becomes true / width→0)
    // The topbar button should now read "Open AI chat" again (aria-pressed=false)
    await expect(chatToggle).toHaveAttribute('aria-pressed', 'false', { timeout: 5_000 })
  })

  // -------------------------------------------------------------------------

  test('Enter key in chat input sends the message', async ({ page }) => {
    const chatToggle = page.getByRole('button', { name: /open ai chat/i })
    await chatToggle.click()

    const panel = desktopPanel(page)
    const chatInput = panel.locator('textarea[aria-label="Chat input"]')
    await expect(chatInput).toBeVisible({ timeout: 10_000 })

    await chatInput.fill('Quick enter test')
    await chatInput.press('Enter')

    // Message appears in the log inside the panel
    await expect(panel.getByText('Quick enter test')).toBeVisible({ timeout: 10_000 })
  })
})

// ---------------------------------------------------------------------------
// Editor Ask AI panel (separate describe block)
// ---------------------------------------------------------------------------

test.describe('Editor Ask AI panel', () => {
  test.beforeEach(async ({ page }) => {
    await loginAs(page)
    await page.goto('/editor')
    // Wait for the editor to be ready
    await expect(page.getByTestId('editor-title')).toBeVisible({ timeout: 20_000 })
  })

  // -------------------------------------------------------------------------

  test('clicking Ask AI opens a textarea in the right panel', async ({ page }) => {
    // The Ask AI toggle button in the editor header — use ✨ Ask AI text
    const askAiBtn = page.getByRole('button', { name: /ask ai/i }).first()
    await expect(askAiBtn).toBeVisible({ timeout: 10_000 })
    await askAiBtn.click()

    // The AskAI textarea should appear
    const textarea = page.locator('textarea[placeholder*="Describe"]').first()
    await expect(textarea).toBeVisible({ timeout: 10_000 })
  })

  // -------------------------------------------------------------------------

  test('typing a prompt in the Ask AI panel enables the Generate button', async ({ page }) => {
    const askAiBtn = page.getByRole('button', { name: /ask ai/i }).first()
    await askAiBtn.click()

    const textarea = page.locator('textarea[placeholder*="Describe"]').first()
    await expect(textarea).toBeVisible({ timeout: 10_000 })

    await textarea.fill('Show daily active users with a line chart')

    // Generate button should become enabled (button text is "✨ Generate" with emoji)
    const generateBtn = page.getByRole('button', { name: /generate/i }).first()
    await expect(generateBtn).not.toBeDisabled({ timeout: 5_000 })
  })
})
