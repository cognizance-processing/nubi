// @ts-check
import { defineConfig, devices } from '@playwright/test'

/**
 * Playwright configuration for the Nubi embed-component e2e suite.
 *
 * A lightweight static HTTP server (python3 -m http.server) serves the entire
 * repo root so that embed/demo/*.html pages can load ../dist/nubi-embed.js as
 * a module script.  File:// is not used because ES-module cross-origin
 * restrictions would block the bundle.
 *
 * Pre-requisite: run `npm run build:embed` before this suite (the bundle must
 * exist at embed/dist/nubi-embed.js).  The static server command is defined in
 * the `webServer` block below so Playwright starts and stops it automatically.
 *
 * Run:
 *   npm run test:e2e:embed
 *
 * Or directly:
 *   npx playwright test --config embed/e2e/playwright.config.js
 */

const PORT = 8799

export default defineConfig({
  testDir: './specs',
  testMatch: '**/*.spec.ts',

  timeout: 30_000,
  fullyParallel: false,
  retries: 0,
  workers: 1,

  reporter: [
    ['html', { outputFolder: '../../playwright-report-embed', open: 'never' }],
    ['list'],
  ],

  use: {
    baseURL: `http://localhost:${PORT}`,

    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
    trace: 'on-first-retry',

    viewport: { width: 1280, height: 900 },
    actionTimeout: 10_000,
    navigationTimeout: 20_000,
  },

  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],

  outputDir: '../../test-results-embed/',

  /**
   * Spin up a static HTTP server at the repo root.
   * The demo pages reference ../dist/nubi-embed.js which resolves correctly
   * when the server root is the repo root.
   */
  webServer: {
    // Serve from repo root (two levels up from embed/e2e)
    command: `python3 -m http.server ${PORT} --directory ../../`,
    port: PORT,
    reuseExistingServer: !process.env.CI,
    timeout: 15_000,
  },
})
