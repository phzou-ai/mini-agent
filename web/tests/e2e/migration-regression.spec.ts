import { expect, test, type Page } from "@playwright/test"

async function mockAgentBootstrap(page: Page) {
  await page.route("**/api/bff/agent/contexts", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: "[]" })
  )
  await page.route("**/api/bff/agent/registered-agents**", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: "[]" })
  )
  await page.route("**/api/bff/agent/a2a/agent-card", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        name: "Vermay Agent",
        description: "A2A-first main agent",
        url: "http://127.0.0.1:8000/rpc",
        version: "0.1.0",
        capabilities: { streaming: true },
        defaultInputModes: ["text/plain"],
        defaultOutputModes: ["text/plain"],
        skills: [],
        metadata: {
          routeKinds: ["local_message", "local_task", "remote_agent"],
          executionModes: ["message", "task", "auto"],
        },
      }),
    })
  )
  await page.route("**/api/bff/agent/model-config", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        primary_model: {
          name: "primary-test-model",
          provider: "ollama",
          model: "test-primary",
          base_url: "http://127.0.0.1:11434",
        },
        router_model: {
          name: "router-test-model",
          provider: "ollama",
          model: "test-router",
          base_url: "http://127.0.0.1:11434",
        },
        router_model_overridden: false,
        config_path: "config/models.json",
      }),
    })
  )
}

test.describe("Migrated frontend regression baseline", () => {
  test.beforeEach(async ({ page }) => {
    await mockAgentBootstrap(page)
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
    await expect(page.getByText(internalMessage)).toHaveCount(0)
    await expect(page.getByText("Connection refused")).toHaveCount(0)
  })
})
