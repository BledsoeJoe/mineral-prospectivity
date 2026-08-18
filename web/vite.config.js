import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// In dev, the FastAPI service runs separately; proxy keeps the frontend
// origin-relative so the same code works behind nginx in the compose stack.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': {
        target: process.env.MPM_API_URL || 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
})
