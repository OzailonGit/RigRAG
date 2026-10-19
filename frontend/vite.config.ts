import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'

// The FastAPI backend runs on :8000 unless API_URL says otherwise; proxying
// keeps the browser same-origin, so the API needs no CORS configuration in
// development.
export default defineConfig(({ mode }) => {
  const { API_URL } = loadEnv(mode, '.', '')
  return {
    plugins: [react()],
    server: {
      proxy: {
        '/api': API_URL || 'http://localhost:8000',
      },
    },
  }
})
