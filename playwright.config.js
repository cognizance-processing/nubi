// @ts-check
import { defineConfig, devices } from '@playwright/test'

/**
 * Playwright configuration for Nubi end-to-end tests.
 *
 * The stack must be running before tests execute (see scripts/e2e.sh).
 * Override baseURL with the E2E_BASE_URL environment variable if the
 * frontend is not at the default localhost:5173 (e.g. in CI or Docker).
 *
 * @see https://playwright.dev/docs/test-configuration
 */
export default defineConfig({
  testDir: './e2e',
  testMatch: '**/*.spec.js',

  /* Maximum time (ms) per test before it is considered failed. */
  timeout: 45_000,

  /* Each test file gets its own browser context, so they are independent. */
  fullyParallel: false,

  /* Retry once on CI / live stack (network jitter, cold-boot delays). */
  retries: 1,

  /* Use a single worker in the default run so tests don't race on shared
     backend state (the seeded user / boards). Parallelism can be enabled
     per-file with test.describe.configure({ mode: 'parallel' }) once the
     seed data is truly read-only for that file. */
  workers: 1,

  /* Reporter: rich HTML report + concise terminal output. */
  reporter: [
    ['html', { outputFolder: 'playwright-report', open: 'never' }],
    ['list'],
  ],

  use: {
    /* Base URL — override with E2E_BASE_URL env var, e.g.:
       E2E_BASE_URL=http://localhost:8080 npx playwright test */
    baseURL: process.env.E2E_BASE_URL ?? 'http://localhost:5173',

    /* Keep cookies/localStorage across navigations within a test. */
    storageState: undefined,

    /* Capture a screenshot + trace on first retry to aid debugging. */
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
    trace: 'on-first-retry',

    /* Standard viewport */
    viewport: { width: 1280, height: 800 },

    /* Allow self-signed certs in dev */
    ignoreHTTPSErrors: true,

    /* Action timeout (click, fill, …) */
    actionTimeout: 15_000,

    /* Navigation timeout */
    navigationTimeout: 30_000,
  },

  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],

  /* Output folder for test artefacts (screenshots, traces, videos). */
  outputDir: 'test-results/',
})
