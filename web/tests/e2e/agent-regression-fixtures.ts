import type { Page } from "@playwright/test"

export async function mockAgentRegressionBootstrap(
  page: Page,
  contexts: unknown[] = []
) {
  await page.route("**/api/bff/agent/contexts", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(contexts),
    })
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
