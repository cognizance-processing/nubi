/**
 * vite.config.js — Vite lib-mode build for @nubi/sdk.
 *
 * Produces: dist/nubi-sdk.js (ES module) + dist/nubi-sdk.umd.cjs (CJS/UMD)
 *
 * Key decisions
 * -------------
 * - Entry:   src/index.js
 * - Formats: ['es', 'umd'] — ES for modern bundlers / <script type="module">;
 *   UMD/CJS for Node require() and legacy hosts.
 * - apache-arrow is BUNDLED (no external) — makes the SDK a zero-dep drop-in.
 *   Hosts don't need to install apache-arrow separately. Trade-off: larger
 *   bundle (~1.5 MB before gzip) vs zero install friction. For M6 drop-in
 *   simplicity is the priority; tree-shaking cuts unused Arrow APIs at build.
 * - Target: es2020 — covers all environments that support custom elements.
 * - Minify: esbuild — fast and tight.
 */

import { defineConfig } from 'vite'
import { resolve } from 'path'

export default defineConfig({
  build: {
    outDir: resolve(import.meta.dirname, 'dist'),
    emptyOutDir: true,

    lib: {
      entry: resolve(import.meta.dirname, 'src/index.js'),
      name: 'NubiSDK',
      formats: ['es', 'umd'],
      fileName: (format) =>
        format === 'umd' ? 'nubi-sdk.umd.cjs' : 'nubi-sdk.js',
    },

    rollupOptions: {
      // No externals — apache-arrow is bundled for drop-in usage.
      external: [],
      output: {
        exports: 'named',
        globals: {},
      },
    },

    target: 'es2020',
    minify: 'esbuild',
    sourcemap: true,
    chunkSizeWarningLimit: 2000,
  },
})
