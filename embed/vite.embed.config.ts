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
import { existsSync, copyFileSync } from 'fs'
import { fileURLToPath } from 'url'
import { resolveVersion } from '../scripts/resolveVersion.mjs'

// __filename / __dirname equivalents for ESM configs
// We derive from the config's own path so this works even after Vite bundles
// the config into a temp file (import.meta.url points to the temp location in
// that case, so we resolve relative to the known repo layout instead).
const __configFile = fileURLToPath(import.meta.url)
// vite.embed.config.js lives at <repo>/embed/vite.embed.config.js
// After Vite temp-bundles the config the filename changes, so anchor off __dirname
// using process.cwd() (which is always the repo root when npm scripts run).
const __repoRoot = resolve(process.cwd())
const __embedDir = resolve(__repoRoot, 'embed')

// Real build version: "X.Y.Z" on a release tag, else "X.Y.Z-dev.<sha>" (or a
// NUBI_VERSION stamp from release tooling). Used for both the injected embed
// version global and the content-addressable bundle filename.
const NUBI_VERSION = resolveVersion(__repoRoot)

export default defineConfig({
  plugins: [
    // @vitejs/plugin-react handles JSX transform (React 19 automatic runtime)
    react(),

    // Copy nubi-embed.js → nubi-embed-<version>.js after bundle is written.
    // Keeps embed/dist/nubi-embed.js as the stable "latest" alias while also
    // providing a content-addressable versioned path for pinned hosts.
    {
      name: 'nubi-version-stamp',
      closeBundle() {
        const src = resolve(__embedDir, 'dist/nubi-embed.js')
        const dst = resolve(__embedDir, `dist/nubi-embed-${NUBI_VERSION}.js`)
        if (existsSync(src)) {
          copyFileSync(src, dst)
          console.log(`[nubi-version-stamp] copied → dist/nubi-embed-${NUBI_VERSION}.js`)
        }
      },
    },
  ],

  // Run from the repo root so node_modules resolution works correctly
  root: resolve(__embedDir, '..'),

  optimizeDeps: {
    include: ['react', 'react-dom', 'apache-arrow', 'echarts'],
  },

  build: {
    // Output into embed/dist (absolute path)
    outDir: resolve(__embedDir, 'dist'),
    emptyOutDir: true,

    lib: {
      entry: resolve(__embedDir, 'nubi-embed-entry.ts'),
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
    // Embed kit version — real stamp ("X.Y.Z" on a tag, else "X.Y.Z-dev.<sha>")
    '__NUBI_EMBED_VERSION__': JSON.stringify(NUBI_VERSION),
  },
})
