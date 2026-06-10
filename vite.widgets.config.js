/**
 * vite.widgets.config.js — Vite lib-mode build for the Nubi widget kit (M8-A).
 *
 * Produces: dist-embed/nubi-widgets.js  (UMD, drop-in <script src>)
 *           dist-embed/nubi-widgets.es.js (ES module)
 *
 * Key decisions
 * -------------
 * - Entry: embed/widgets/index.js  (registers all three custom elements)
 * - apache-arrow + regl ARE bundled in — the whole point is a single-file
 *   drop-in. Hosts need zero extra imports.
 * - UMD global name: NubiWidgets  (window.NubiWidgets.registerNubiWidgets)
 * - The UMD output is named nubi-widgets.js (no format suffix) so the
 *   examples/widgets-demo page can reference it as a plain <script src>.
 * - Output dir: dist-embed/ (same as the nubi-dashboard bundle, different file).
 *   emptyOutDir: false so both builds can coexist.
 * - Target: es2020 — custom elements require at minimum ES2019.
 * - Minify: esbuild for speed. Source maps for the ES module only.
 *
 * Usage
 * -----
 *   npm run build:widgets
 *
 * Output
 * ------
 *   dist-embed/
 *     nubi-widgets.js      ← UMD  (plain <script src="...">)
 *     nubi-widgets.es.js   ← ES module (<script type="module">)
 *     nubi-widgets.es.js.map
 */

import { defineConfig } from 'vite'
import { resolve } from 'path'

export default defineConfig({
  plugins: [],  // vanilla JS; no React, no JSX

  build: {
    outDir: resolve(import.meta.dirname, 'dist-embed'),

    // Do NOT wipe the directory — the nubi-dashboard bundle already lives here.
    emptyOutDir: false,

    lib: {
      entry: resolve(import.meta.dirname, 'embed/widgets/index.js'),

      // UMD global: window.NubiWidgets
      name: 'NubiWidgets',

      formats: ['es', 'umd'],

      fileName: (format) =>
        format === 'umd' ? 'nubi-widgets.js' : `nubi-widgets.${format}.js`,
    },

    rollupOptions: {
      // Bundle everything in — no externals. apache-arrow + regl are included.
      external: [],

      output: {
        exports: 'named',
        globals: {},
        inlineDynamicImports: false,
      },
    },

    target: 'es2020',
    minify: 'esbuild',

    // Source map for the ES module (useful for debugging in host apps).
    sourcemap: true,

    // apache-arrow is large (~500 KB gzip); raise the warning limit.
    chunkSizeWarningLimit: 2000,
  },
})
