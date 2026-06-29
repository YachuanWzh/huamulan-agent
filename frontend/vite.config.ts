/// <reference types="vitest/config" />
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

const API_TARGET = process.env.VITE_API_TARGET || 'http://localhost:8000'

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': API_TARGET,
    },
  },
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
    globals: true,
  },
})
