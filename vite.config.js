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
    server: {
      port: 5174,
      host,
      hmr: {
        host,
        port: 5174,
      }
    }
  }
})
