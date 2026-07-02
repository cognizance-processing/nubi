import { defineConfig } from 'vitest/config'
import { resolveVersion } from '../scripts/resolveVersion.mjs'

export default defineConfig({
  // Inject the same build-time global that vite.embed.config.js provides so
  // tests can import nubi-embed-entry.js without a separate build step. Uses the
  // same resolver so the version format matches real builds.
  define: {
    '__NUBI_EMBED_VERSION__': JSON.stringify(resolveVersion()),
  },
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
