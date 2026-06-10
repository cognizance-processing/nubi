/**
 * vite.embed.config.js — Vite lib-mode build for the <nubi-dashboard> embed bundle.
 *
 * Produces: dist-embed/nubi-dashboard.js (ES module + UMD via separate format pass)
 *
 * Key decisions
 * -------------
 * - `build.lib` entry: embed/nubi-dashboard.js  (the custom element source)
 * - `formats: ['es', 'umd']` — ES for `<script type="module">` hosts; UMD for
 *   `<script>` (no type) hosts (legacy CDN / CMS drop-in).
 * - `rollupOptions.external: []` — apache-arrow IS bundled in. The whole point of
 *   the embed is drop-in: one script tag, no extra imports required by the host.
 * - `minify: 'esbuild'` — esbuild is fast; treeshake removes unused arrow APIs.
 * - The output filename is fixed to `nubi-dashboard.[format].js`; UMD also gets
 *   the global name `NubiDashboard` (window.NubiDashboard for UMD script-tag usage).
 * - No React plugin — the embed is vanilla JS. No HMR, no JSX transform needed.
 * - Target: 'es2020' — broad browser support; custom elements are natively
 *   available in all modern browsers.
 *
 * Usage
 * -----
 *   npm run build:embed
 *
 * Output
 * ------
 *   dist-embed/
 *     nubi-dashboard.es.js   ← ES module (use with <script type="module">)
 *     nubi-dashboard.umd.js  ← UMD (use with <script src="...">)
 *
 * The example pages (examples/embed-demo/*) reference
 * ../../dist-embed/nubi-dashboard.js which resolves to the UMD build (the
 * filename alias is set via fileNames below).
 */

import { defineConfig } from 'vite'
import { resolve } from 'path'

export default defineConfig({
  // No plugins: no React, no Tailwind — purely vanilla JS.
  plugins: [],

  build: {
    // Output directory: separate from the main React app's dist/
    outDir: resolve(import.meta.dirname, 'dist-embed'),

    // Keep both embed bundles (nubi-dashboard + nubi-widgets) in the same dir.
    // Each build writes its own named files so they coexist without conflict.
    // Run `rm -rf dist-embed` manually to do a full clean.
    emptyOutDir: false,

    // Lib mode: produces a reusable bundle rather than an HTML app.
    lib: {
      entry: resolve(import.meta.dirname, 'embed/nubi-dashboard.js'),

      // The `name` field is used as the UMD global name:
      //   window.NubiDashboard will hold the module exports in UMD mode.
      name: 'NubiDashboard',

      // Emit both ES module and UMD.
      // ES module → consumed by modern bundlers and <script type="module">.
      // UMD       → consumed by plain <script src="..."> (legacy hosts / CMSes).
      formats: ['es', 'umd'],

      // File names for each format.
      // The UMD build will be named nubi-dashboard.js (no format suffix) so that
      // the example pages can reference dist-embed/nubi-dashboard.js as a plain script.
      fileName: (format) =>
        format === 'umd' ? 'nubi-dashboard.js' : `nubi-dashboard.${format}.js`,
    },

    rollupOptions: {
      // No externals — apache-arrow is intentionally bundled so the script is
      // self-contained. Hosts do NOT need to load apache-arrow separately.
      external: [],

      output: {
        // Ensure no `exports: 'named'` warning for the UMD build (we export a class).
        exports: 'named',

        // Globals are irrelevant since we have no externals, but required by rollup
        // when `external` is defined.
        globals: {},

        // Minimal polyfill for interop helpers in UMD builds.
        inlineDynamicImports: false,
      },
    },

    // Target modern JS — custom elements require at minimum ES2019.
    // es2020 gives us: nullish coalescing, optional chaining, BigInt, etc.
    target: 'es2020',

    // Use esbuild for fast, tight minification.
    minify: 'esbuild',

    // Source maps for the es format only (useful for debugging in host apps).
    sourcemap: true,

    // Raise the chunk-size warning threshold slightly because apache-arrow
    // is large but is intentionally bundled. We still want to KNOW the size.
    chunkSizeWarningLimit: 1500,
  },
})
