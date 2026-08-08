import { defineConfig, devices } from "@playwright/test"

const frontendPort = Number(process.env.PLAYWRIGHT_FRONTEND_PORT ?? 3101)
const frontendBaseUrl =
  process.env.PLAYWRIGHT_BASE_URL ?? `http://127.0.0.1:${frontendPort}`
const frontendDistDir =
  process.env.PLAYWRIGHT_NEXT_DIST_DIR ?? ".next-playwright-regression"

export default defineConfig({
  testDir: "./tests/e2e",
  testMatch: /(?:frontend-regression|runtime-reliability)\.spec\.ts/,
  timeout: 30_000,
  expect: { timeout: 10_000 },
  use: {
    baseURL: frontendBaseUrl,
    trace: "retain-on-failure",
  },
  webServer: {
    command: `VERMAY_NEXT_DIST_DIR=${frontendDistDir} pnpm exec next dev --webpack --hostname 127.0.0.1 --port ${frontendPort}`,
    reuseExistingServer: false,
    timeout: 60_000,
    url: frontendBaseUrl,
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
})
