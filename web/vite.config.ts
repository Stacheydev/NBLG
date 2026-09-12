import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  build: {
    // The dashboard is small; a single chunk keeps the waterfall short.
    chunkSizeWarningLimit: 700,
  },
})
