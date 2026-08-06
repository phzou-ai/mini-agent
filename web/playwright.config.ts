import { defineConfig, devices } from "@playwright/test"

const frontendPort = Number(process.env.PLAYWRIGHT_FRONTEND_PORT ?? 3000)
const backendPort = Number(process.env.PLAYWRIGHT_BACKEND_PORT ?? 8000)

const frontendBaseUrl =
  process.env.PLAYWRIGHT_BASE_URL ?? `http://localhost:${frontendPort}`
const backendBaseUrl =
  process.env.VERMAY_API_BASE ?? `http://127.0.0.1:${backendPort}`

const backendCommand =
  process.env.PLAYWRIGHT_BACKEND_COMMAND ??
  [
    "cd .. &&",
    ".venv/bin/python -m vermay.main serve",
    "--host 127.0.0.1",
    `--port ${backendPort}`,
  ].join(" ")

const frontendCommand =
  process.env.PLAYWRIGHT_FRONTEND_COMMAND ??
  `VERMAY_API_BASE=${backendBaseUrl} pnpm exec next dev --hostname 127.0.0.1 --port ${frontendPort}`

export default defineConfig({
  testDir: "./tests/e2e",
  timeout: 30_000,
  expect: {
    timeout: 10_000,
  },
  use: {
    baseURL: frontendBaseUrl,
    trace: "retain-on-failure",
  },
  webServer: [
    {
      command: backendCommand,
      reuseExistingServer: !process.env.CI,
      timeout: 30_000,
      url: `${backendBaseUrl}/health`,
    },
    {
      command: frontendCommand,
      reuseExistingServer: !process.env.CI,
      timeout: 60_000,
      url: frontendBaseUrl,
    },
  ],
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
})
