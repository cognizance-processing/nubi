import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd())
  const backendUrl = new URL(env.VITE_BACKEND_URL || 'http://localhost:8000')
  const host = backendUrl.hostname === 'localhost' || backendUrl.hostname === '127.0.0.1'
    ? '0.0.0.0'
    : '127.0.0.1'

  return {
    plugins: [react()],
    // Define process.env so that CJS deps like react-draggable that reference
    // process.env.DRAGGABLE_DEBUG don't throw ReferenceError in the browser.
    define: {
      'process.env': {},
    },
    // Also inject into esbuild dep pre-bundling (Vite's define doesn't cover that path).
    optimizeDeps: {
      esbuildOptions: {
        define: {
          'process.env.DRAGGABLE_DEBUG': 'false',
          'process.env.NODE_ENV': JSON.stringify(mode),
        },
      },
    },
    server: {
      port: 5173,
      strictPort: true,
      host,
      hmr: {
        host,
        port: 5173,
      },
      // Proxy /api/* to the backend so all auth cookies are same-origin in dev.
      // Without this, the browser treats localhost:5173 → localhost:8000 as
      // cross-origin and refuses to send SameSite=Lax cookies on fetch() POSTs
      // (e.g. /auth/refresh), causing a 401 on every page load.
      proxy: {
        '/api': {
          target: env.VITE_BACKEND_URL || 'http://localhost:8000',
          changeOrigin: true,
          secure: false,
        },
      },
    }
  }
})
