import { defineConfig } from 'vitest/config'

export default defineConfig({
  test: {
    // The analyzer's logic lives in web/api/. Tests live outside it, because
    // Vercel turns every non-underscore path under api/ into a route.
    include: ['tests/**/*.test.ts'],
    environment: 'node',
  },
})
