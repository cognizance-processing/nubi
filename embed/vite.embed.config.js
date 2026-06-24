/**
 * vite.embed.config.js — Vite library build for the Nubi embed kit.
 *
 * Produces a SELF-CONTAINED browser ESM bundle at embed/dist/nubi-embed.js.
 * All dependencies — React 19, react-dom, apache-arrow, ECharts — are bundled
 * in.  No bare specifiers remain.  A plain <script type="module"> on any web
 * page is enough to load the full component suite with no import map and no
 * node_modules.
 *
 * Monaco editor is intentionally excluded — it's only used by nubi-query-editor
 * via a dynamic import() and is far too large (~7 MB) to bundle inline.  Hosts
 * that want the query editor must supply Monaco themselves (CDN or their own
 * bundler).  All other demos work without it.
 *
 * Usage:
 *   npm run build:embed
 *   # → embed/dist/nubi-embed.js  (~2–3 MB gzip: ~600 KB)
 */

import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { resolve } from 'path'

const __embedDir = resolve(new URL(import.meta.url).pathname, '..')

export default defineConfig({
  plugins: [
    // @vitejs/plugin-react handles JSX transform (React 19 automatic runtime)
    react(),
  ],

  // Run from the repo root so node_modules resolution works correctly
  root: resolve(__embedDir, '..'),

  // Treat .js files as JSX — nubi-kpi-react.js contains JSX with a .js extension
  esbuild: {
    loader: 'jsx',
    // Only apply JSX loader to embed source files (avoids breaking other .js files)
    include: [/embed\/.*\.js$/, /embed\/.*\.jsx$/],
    exclude: [],
  },

  optimizeDeps: {
    esbuildOptions: {
      loader: {
        '.js': 'jsx',
      },
    },
    include: ['react', 'react-dom', 'apache-arrow', 'echarts'],
  },

  build: {
    // Output into embed/dist (absolute path)
    outDir: resolve(__embedDir, 'dist'),
    emptyOutDir: true,

    lib: {
      entry: resolve(__embedDir, 'nubi-embed-entry.js'),
      name: 'NubiEmbed',
      // Single ES module — browsers don't need UMD/CJS
      formats: ['es'],
      fileName: () => 'nubi-embed.js',
    },

    rollupOptions: {
      // Externalize Monaco — too large to bundle; hosts supply it separately
      external: ['monaco-editor'],

      output: {
        // Inline all dynamic imports into one file so no extra files need hosting
        inlineDynamicImports: true,
      },

      // Silence "use client" directive warnings from React 19
      onwarn(warning, warn) {
        if (warning.code === 'MODULE_LEVEL_DIRECTIVE') return
        if (warning.code === 'SOURCEMAP_ERROR') return
        warn(warning)
      },
    },

    // Minify for smaller payload
    minify: true,

    // Self-contained embeds are expected to be large (React + Arrow + ECharts)
    chunkSizeWarningLimit: 6000,

    // Target modern browsers with native custom-elements support
    target: ['es2020', 'chrome89', 'firefox90', 'safari15'],
  },

  define: {
    // Shim process.env for CJS deps that reference it
    'process.env': JSON.stringify({ NODE_ENV: 'production' }),
    'process.env.NODE_ENV': JSON.stringify('production'),
  },
})
