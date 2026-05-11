import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 3000,
    // WHY: proxy /api/* to the backend in local dev so the browser never
    // hits a CORS preflight — both frontend and "backend" appear same-origin
    // from the browser's perspective. In production, VITE_API_BASE_URL is used.
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ''),
      },
    },
  },
})
