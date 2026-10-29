import { defineConfig } from '@playwright/test'

const PORT = Number(process.env.FIXTURE_PORT || 4317)

export default defineConfig({
  testDir: './tests',
  testMatch: '**/*.spec.js',
  timeout: 30_000,
  use: {
    baseURL: `http://127.0.0.1:${PORT}`,
  },
  webServer: {
    command: 'node tests/fixture-server.mjs',
    url: `http://127.0.0.1:${PORT}/call/test`,
    reuseExistingServer: true,
  },
})
