import { svelte } from '@sveltejs/vite-plugin-svelte'
import { svelteTesting } from '@testing-library/svelte/vite'
import { loadEnv } from 'vite'
import { defineConfig } from 'vitest/config'

export default defineConfig(({ mode }) => {
  // The API's port differs per machine (8000 is the Architecture's default, but can be taken locally), so the
  // proxy target comes from API_PORT in the environment or web/.env.local instead of being baked in.
  const env = loadEnv(mode, '.', ['API_'])
  const apiPort = env.API_PORT || '8000'
  return {
    plugins: [svelte(), svelteTesting()],
    server: {
      proxy: {
        // The API's routes already start with /api, so no rewrite; same origin keeps the API free of CORS.
        '/api': { target: `http://localhost:${apiPort}`, changeOrigin: true },
      },
      // errors.test.ts reads the API's code table and the Architecture; the dev server never serves them.
      fs: mode === 'test' ? { allow: ['.', '../src/pdf_splitter', '../docs'] } : undefined,
    },
    test: {
      environment: 'jsdom',
      include: ['src/**/*.test.ts'],
    },
  }
})
