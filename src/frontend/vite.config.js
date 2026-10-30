import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    // FastAPI runs on :8000 (see CLAUDE.md); the browser only ever talks to /api.
    proxy: { '/api': 'http://127.0.0.1:8000' },
  },
})
