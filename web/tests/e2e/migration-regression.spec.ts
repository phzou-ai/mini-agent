import { expect, test } from "@playwright/test"

import { mockAgentRegressionBootstrap } from "./agent-regression-fixtures"

test.describe("Migrated frontend regression baseline", () => {
  test.beforeEach(async ({ page }) => {
    await mockAgentRegressionBootstrap(page)
  })

  test("loads the repository-owned agent workspace without authentication", async ({
    page,
  }) => {
    await page.goto("/agent")

    await expect(page.getByTestId("agent-console")).toBeVisible()
    await expect(page.getByTestId("agent-sidebar")).toBeVisible()
    await expect(page.getByTestId("agent-composer-input")).toBeVisible()
    await expect(page.getByRole("link", { name: "Login" })).toHaveCount(0)
    await expect(page.getByText("primary-test-model")).toBeVisible()
    await expect(page.getByText("router-test-model")).toBeVisible()
  })

  test("renders the public SSE error and hides provider diagnostics", async ({
    page,
  }) => {
    const internalMessage =
      "Ollama request failed: <urlopen error [Errno 61] Connection refused>"
    await page.route("**/api/bff/agent/a2a/message-stream", (route) =>
      route.fulfill({
        status: 200,
        contentType: "text/event-stream",
        body: `event: error\ndata: ${JSON.stringify({
          jsonrpc: "2.0",
          id: "regression-model-error",
          error: {
            code: -32000,
            message: "Model request failed.",
            data: { localCode: "model_error", retryable: true },
          },
        })}\n\n`,
      })
    )

    await page.goto("/agent")
    await page.getByTestId("agent-composer-input").fill("hello")
    await page.getByTestId("agent-composer-send").click()

    await expect(page.getByTestId("agent-error-banner")).toHaveText(
      "Model request failed."
    )
    await expect(page.getByTestId("agent-direct-message-failure")).toContainText(
      "Model request failed."
    )
    await expect(page.getByText("model_error")).toBeVisible()
    await expect(page.getByText(internalMessage)).toHaveCount(0)
    await expect(page.getByText("Connection refused")).toHaveCount(0)
  })
})
