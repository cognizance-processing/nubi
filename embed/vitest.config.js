import { defineConfig } from 'vitest/config'

export default defineConfig({
  test: {
    // jsdom provides CustomElementRegistry, shadowRoot, CustomEvent, etc.
    environment: 'jsdom',
    include: ['embed/__tests__/**/*.test.js'],
    // Polyfills for APIs jsdom lacks (ResizeObserver, getBoundingClientRect, etc.)
    setupFiles: ['embed/__tests__/setup.js'],
    // Pre-transform apache-arrow ESM so jsdom can consume it
    server: {
      deps: {
        inline: ['apache-arrow'],
      },
    },
    // Silence noisy console output in CI
    silent: false,
  },
})
