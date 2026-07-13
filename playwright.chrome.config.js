// TEMP scratch config — Ubuntu 26.04 has no prebuilt Playwright browser bundle,
// so drive the system-installed Google Chrome instead (channel: 'chrome').
import { defineConfig, devices } from '@playwright/test'

export default defineConfig({
  testDir: './e2e',
  testMatch: '**/*.spec.js',
  timeout: 45_000,
  fullyParallel: false,
  retries: 0,
  workers: 1,
  reporter: [['list'], ['json', { outputFile: '/tmp/claude-1000/-home-exo-Documents-nubi/4764acb7-5cb0-4df1-a077-20f3cb547bb6/scratchpad/results.json' }]],
  use: {
    baseURL: process.env.E2E_BASE_URL || 'http://localhost:5173',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  projects: [{ name: 'chrome', use: { ...devices['Desktop Chrome'], channel: 'chrome' } }],
})
