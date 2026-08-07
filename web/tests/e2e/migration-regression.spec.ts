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

  test("renders structured Markdown without overflowing the transcript", async ({
    page,
  }) => {
    const now = Date.now()
    const contextId = `ctx-markdown-${now}`
    const prompt = `render markdown ${now}`
    const answer = [
      "# Learning plan",
      "",
      "A concise introduction with **important context** and inline code that preserves literal math delimiters: `\\(alpha\\)`. This deliberately long paragraph verifies that intrinsic content width cannot expand a message beyond the center workspace when both the session sidebar and inspector are visible.",
      "",
      "## Foundations",
      "",
      "- Mathematics",
      "- Programming",
      "",
      String.raw`Inline derivative: \(\frac{dy}{dx}\).`,
      "",
      String.raw`\[\frac{dy}{dx} = \frac{dy}{dg} \cdot \frac{dg}{dx}\]`,
      "",
      "$$",
      String.raw`L = f(\text{output})`,
      "$$",
      "",
      "> Build one concept at a time.",
      "",
      "| Area | Key topics | Suggested duration |",
      "| --- | --- | --- |",
      "| Mathematics | Probability, linear algebra, optimization | 4 weeks |",
      "| Programming | Python, NumPy, PyTorch | 3 weeks |",
      "",
      "```python",
      'print("hello")',
      "```",
    ].join("\n")
    const assistantMessageId = `agent-markdown-${now}`
    let inputMessageId = ""

    await page.route("**/api/bff/agent/a2a/message-stream", async (route) => {
      const payload = JSON.parse(route.request().postData() || "{}")
      inputMessageId = payload.messageId
      await route.fulfill({
        status: 200,
        contentType: "text/event-stream",
        body: `event: message\ndata: ${JSON.stringify({
          jsonrpc: "2.0",
          id: "markdown-final",
          result: {
            kind: "message",
            role: "agent",
            messageId: assistantMessageId,
            contextId,
            parts: [{ kind: "text", text: answer }],
            metadata: { partial: false, append: false, final: true },
          },
        })}\n\n`,
      })
    })
    await page.route(
      `**/api/bff/agent/contexts/${contextId}/messages`,
      async (route) => {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify([
            {
              message_id: inputMessageId,
              context_id: contextId,
              role: "user",
              parts: [{ kind: "text", text: prompt }],
              metadata: { executionMode: "message" },
              created_at: new Date(now).toISOString(),
            },
            {
              message_id: assistantMessageId,
              context_id: contextId,
              role: "agent",
              parts: [{ kind: "text", text: answer }],
              metadata: {},
              created_at: new Date(now + 1).toISOString(),
            },
          ]),
        })
      }
    )
    for (const suffix of ["tasks", "route-decisions", "delegations"]) {
      await page.route(
        `**/api/bff/agent/contexts/${contextId}/${suffix}`,
        async (route) => {
          await route.fulfill({
            status: 200,
            contentType: "application/json",
            body: "[]",
          })
        }
      )
    }

    await page.goto("/agent")
    await page.getByTestId("agent-mode-message").click()
    await page.getByTestId("agent-composer-input").fill(prompt)
    await page.getByTestId("agent-composer-send").click()

    const assistantMessage = page.locator(
      '[data-testid="agent-message-item"][data-agent-role="assistant"]'
    )
    const markdown = assistantMessage.locator(".agent-markdown")
    await expect(markdown.getByRole("heading", { level: 1 })).toHaveText(
      "Learning plan"
    )
    await expect(markdown.getByRole("heading", { level: 2 })).toHaveText(
      "Foundations"
    )
    await expect(markdown.locator("blockquote")).toContainText(
      "Build one concept at a time."
    )
    await expect(markdown.locator("pre code")).toContainText('print("hello")')
    await expect(markdown.locator("code").first()).toHaveText(
      String.raw`\(alpha\)`
    )
    await expect(markdown.locator("table")).toBeVisible()
    await expect(markdown.locator(".katex")).toHaveCount(3)
    await expect(markdown.locator(".katex-display")).toHaveCount(2)

    await page.setViewportSize({ width: 1920, height: 900 })
    const [transcriptBox, composerBox] = await Promise.all([
      page.getByTestId("agent-transcript-content").boundingBox(),
      page.getByTestId("agent-composer-content").boundingBox(),
    ])
    expect(transcriptBox).not.toBeNull()
    expect(composerBox).not.toBeNull()
    expect(Math.abs(transcriptBox!.x - composerBox!.x)).toBeLessThanOrEqual(1)
    expect(
      Math.abs(transcriptBox!.width - composerBox!.width)
    ).toBeLessThanOrEqual(1)

    const messageBubble = assistantMessage.getByTestId("agent-message-bubble")
    const mainPanel = page.getByTestId("agent-main")
    const [bubbleBox, mainBox] = await Promise.all([
      messageBubble.boundingBox(),
      mainPanel.boundingBox(),
    ])
    expect(bubbleBox).not.toBeNull()
    expect(mainBox).not.toBeNull()
    expect(bubbleBox!.x + bubbleBox!.width).toBeLessThanOrEqual(
      mainBox!.x + mainBox!.width
    )

    await page.setViewportSize({ width: 640, height: 900 })
    const tableRegion = markdown.getByRole("region", {
      name: "Scrollable table",
    })
    await expect(tableRegion).toBeVisible()
    expect(
      await tableRegion.evaluate(
        (element) => element.scrollWidth <= element.clientWidth + 1
      )
    ).toBe(true)
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= window.innerWidth
      )
    ).toBe(true)
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
    await page.route("**/api/bff/agent/message-ingress/**", (route) =>
      route.fulfill({
        status: 404,
        contentType: "application/json",
        body: JSON.stringify({
          code: "message_ingress_not_found",
          message: "Message ingress was not persisted.",
          retryable: false,
        }),
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

  test("reconciles a streamed direct answer with its persisted message", async ({
    page,
  }) => {
    const now = Date.now()
    const contextId = `ctx-direct-message-${now}`
    const prompt = `dedupe direct message ${now}`
    const answer = `One durable answer ${now}`
    const assistantMessageId = `agent-direct-message-${now}`
    let inputMessageId = ""

    await page.route("**/api/bff/agent/a2a/message-stream", async (route) => {
      const payload = JSON.parse(route.request().postData() || "{}")
      inputMessageId = payload.messageId
      await route.fulfill({
        status: 200,
        contentType: "text/event-stream",
        body: `event: message\ndata: ${JSON.stringify({
          jsonrpc: "2.0",
          id: "direct-message-final",
          result: {
            kind: "message",
            role: "agent",
            messageId: assistantMessageId,
            contextId,
            parts: [{ kind: "text", text: answer }],
            metadata: { partial: false, append: false, final: true },
          },
        })}\n\n`,
      })
    })
    await page.route(
      `**/api/bff/agent/contexts/${contextId}/messages`,
      async (route) => {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify([
            {
              message_id: inputMessageId,
              context_id: contextId,
              role: "user",
              parts: [{ kind: "text", text: prompt }],
              metadata: { executionMode: "message" },
              created_at: new Date(now).toISOString(),
            },
            {
              message_id: assistantMessageId,
              context_id: contextId,
              role: "agent",
              parts: [{ kind: "text", text: answer }],
              metadata: {},
              created_at: new Date(now + 1).toISOString(),
            },
          ]),
        })
      }
    )
    for (const suffix of [
      "tasks",
      "route-decisions",
      "delegations",
    ]) {
      await page.route(
        `**/api/bff/agent/contexts/${contextId}/${suffix}`,
        async (route) => {
          await route.fulfill({
            status: 200,
            contentType: "application/json",
            body: "[]",
          })
        }
      )
    }

    await page.goto("/agent")
    await page.getByTestId("agent-mode-message").click()
    await page.getByTestId("agent-composer-input").fill(prompt)
    await page.getByTestId("agent-composer-send").click()

    const assistantMessages = page.locator(
      '[data-testid="agent-message-item"][data-agent-role="assistant"]'
    )
    await expect(page.getByText(answer)).toBeVisible()
    await expect(assistantMessages).toHaveCount(1)

    await page.getByTestId("agent-session-select").click()

    await expect(assistantMessages).toHaveCount(1)
    await expect(page.getByText(answer)).toHaveCount(1)
  })

  test("retries a retryable direct-message failure as a new ingress", async ({
    page,
  }) => {
    const now = Date.now()
    const contextId = `ctx-retryable-message-${now}`
    const prompt = `retry request ${now}`
    const retryAnswer = `Recovered response ${now}`
    const draft = "Keep this composer draft"
    let requestCount = 0
    let firstMessageId = ""

    await page.route("**/api/bff/agent/a2a/message-stream", async (route) => {
      const payload = JSON.parse(route.request().postData() || "{}")
      requestCount += 1

      if (requestCount === 1) {
        firstMessageId = payload.messageId
        expect(payload.text).toBe(prompt)
        expect(payload.executionMode).toBe("message")
        await route.fulfill({
          status: 200,
          contentType: "text/event-stream",
          body: `event: error\ndata: ${JSON.stringify({
            jsonrpc: "2.0",
            id: "retryable-message-error",
            error: {
              code: -32000,
              message: "Model request failed.",
              data: { localCode: "model_error", retryable: true },
            },
          })}\n\n`,
        })
        return
      }

      expect(payload.text).toBe(prompt)
      expect(payload.contextId).toBe(contextId)
      expect(payload.executionMode).toBe("message")
      expect(payload.messageId).not.toBe(firstMessageId)
      await route.fulfill({
        status: 200,
        contentType: "text/event-stream",
        body: `event: message\ndata: ${JSON.stringify({
          jsonrpc: "2.0",
          id: "retryable-message-success",
          result: {
            kind: "message",
            role: "agent",
            messageId: `agent-retry-${now}`,
            contextId,
            parts: [{ kind: "text", text: retryAnswer }],
          },
        })}\n\n`,
      })
    })
    await page.route("**/api/bff/agent/message-ingress/**", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          message_id: firstMessageId,
          context_id: contextId,
          state: "failed",
          failure: {
            code: "model_error",
            message: "Model request failed.",
            retryable: true,
          },
          created_at: new Date(now).toISOString(),
          updated_at: new Date(now).toISOString(),
        }),
      })
    })
    await page.route(
      `**/api/bff/agent/contexts/${contextId}/messages`,
      async (route) => {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: "[]",
        })
      }
    )
    await page.route(
      `**/api/bff/agent/contexts/${contextId}/tasks`,
      async (route) => {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: "[]",
        })
      }
    )
    await page.route(
      `**/api/bff/agent/contexts/${contextId}/route-decisions`,
      async (route) => {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: "[]",
        })
      }
    )
    await page.route(
      `**/api/bff/agent/contexts/${contextId}/delegations`,
      async (route) => {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: "[]",
        })
      }
    )

    await page.goto("/agent")
    await page.getByTestId("agent-mode-message").click()
    await page.getByTestId("agent-composer-input").fill(prompt)
    await page.getByTestId("agent-composer-send").click()

    const retryButton = page.getByTestId("agent-direct-message-retry")
    await expect(retryButton).toBeVisible()
    await expect(retryButton).toBeEnabled()
    await page.getByTestId("agent-composer-input").fill(draft)
    await retryButton.click()

    await expect(page.getByText(retryAnswer)).toBeVisible()
    await expect(page.getByTestId("agent-composer-input")).toHaveValue(draft)
    expect(requestCount).toBe(2)
  })
})
