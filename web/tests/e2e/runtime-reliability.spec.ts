import { expect, test, type Page } from "@playwright/test"

import { mockAgentRegressionBootstrap } from "./agent-regression-fixtures"

function assistantMessages(page: Page) {
  return page.locator(
    '[data-testid="agent-message-item"][data-agent-role="assistant"]'
  )
}

async function expectLatestTaskStatus(page: Page, status: string) {
  await expect(
    assistantMessages(page)
      .filter({ hasText: `task · ${status}` })
      .last()
  ).toBeVisible({ timeout: 30_000 })
}

test.describe("Single-host runtime reliability", () => {
  test("recovers a completed task after a late stream error", async ({ page }) => {
    const now = Date.now()
    const contextId = `ctx-reliability-trailing-error-${now}`
    const taskId = `task-reliability-trailing-error-${now}`
    const threadId = `thread-reliability-trailing-error-${now}`
    const prompt = `recover completed task ${now}`
    const answer = `Recovered completed answer ${now}`
    const startedAt = new Date(now).toISOString()
    const completedAt = new Date(now + 1000).toISOString()

    await mockAgentRegressionBootstrap(page)
    await page.route("**/api/bff/agent/a2a/message-stream", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "text/event-stream",
        body: [
          `event: task\ndata: ${JSON.stringify({
            jsonrpc: "2.0",
            id: "reliability-trailing-error-task",
            result: {
              kind: "task",
              id: taskId,
              contextId,
              status: { state: "working", timestamp: startedAt },
              metadata: {
                localThreadId: threadId,
                runtimeThreadId: threadId,
              },
            },
          })}\n\n`,
          `event: error\ndata: ${JSON.stringify({
            jsonrpc: "2.0",
            id: "reliability-trailing-error",
            error: {
              code: -32000,
              message: "Agent execution failed.",
              data: {
                localCode: "runtime_error",
                retryable: false,
              },
            },
          })}\n\n`,
        ].join(""),
      })
    })
    await page.route("**/api/bff/agent/message-ingress/**", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          message_id: "msg-reliability-trailing-error",
          context_id: contextId,
          state: "resolved",
          created_at: startedAt,
          updated_at: completedAt,
        }),
      })
    })
    await page.route(`**/api/bff/agent/contexts/${contextId}`, async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          context_id: contextId,
          title: prompt,
          metadata: {},
          created_at: startedAt,
          updated_at: completedAt,
        }),
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
              message_id: "msg-reliability-trailing-error-user",
              context_id: contextId,
              role: "user",
              parts: [{ kind: "text", text: prompt }],
              task_id: taskId,
              metadata: {},
              created_at: startedAt,
            },
            {
              message_id: "msg-reliability-trailing-error-agent",
              context_id: contextId,
              role: "agent",
              parts: [{ kind: "text", text: answer }],
              task_id: taskId,
              metadata: {},
              created_at: completedAt,
            },
          ]),
        })
      }
    )
    await page.route(
      `**/api/bff/agent/contexts/${contextId}/tasks`,
      async (route) => {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify([
            {
              task_id: taskId,
              context_id: contextId,
              status: "completed",
              input_message_id: "msg-reliability-trailing-error-user",
              output_message_id: "msg-reliability-trailing-error-agent",
              runtime_thread_id: threadId,
              attempt: 1,
              created_at: startedAt,
              updated_at: completedAt,
            },
          ]),
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
    await page.route(`**/api/bff/agent/a2a/tasks/${taskId}`, async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          kind: "task",
          id: taskId,
          contextId,
          status: { state: "completed", timestamp: completedAt },
          metadata: {
            localThreadId: threadId,
            runtimeThreadId: threadId,
          },
        }),
      })
    })
    await page.route(
      `**/api/bff/agent/a2a/tasks/${taskId}/events**`,
      async (route) => {
        await route.fulfill({
          status: 200,
          contentType: "text/event-stream",
          body: "",
        })
      }
    )

    await page.goto("/agent")
    await expect(page.getByTestId("agent-console")).toBeVisible()
    await page.getByRole("button", { name: "New session" }).click()
    await page.getByTestId("agent-mode-task").click()
    await page.getByTestId("agent-composer-input").fill(prompt)
    await page.getByTestId("agent-composer-send").click()

    await expect(assistantMessages(page).filter({ hasText: answer })).toBeVisible()
    await expectLatestTaskStatus(page, "completed")
    await expect(page.getByTestId("agent-direct-message-failure")).toHaveCount(0)
  })

  test("separates A2A state from local process state in the Inspector", async ({
    page,
  }) => {
    const now = Date.now()
    const contextId = `ctx-reliability-inspector-${now}`
    const taskId = `task-reliability-inspector-${now}`
    const threadId = `thread-reliability-inspector-${now}`
    const createdAt = new Date(now).toISOString()

    await mockAgentRegressionBootstrap(page, [
      {
        context_id: contextId,
        title: "Inspect task state",
        metadata: {},
        created_at: createdAt,
        updated_at: createdAt,
      },
    ])
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
          body: JSON.stringify([
            {
              task_id: taskId,
              context_id: contextId,
              status: "cancel_requested",
              input_message_id: "msg-inspector-input",
              runtime_thread_id: threadId,
              attempt: 1,
              created_at: createdAt,
              updated_at: createdAt,
            },
          ]),
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
    await page.route(`**/api/bff/agent/a2a/tasks/${taskId}`, async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          kind: "task",
          id: taskId,
          contextId,
          status: { state: "working", timestamp: createdAt },
          metadata: {
            localStatus: "cancel_requested",
            localThreadId: threadId,
            runtimeThreadId: threadId,
          },
        }),
      })
    })
    await page.route(
      `**/api/bff/agent/a2a/tasks/${taskId}/events**`,
      async (route) => {
        await route.fulfill({
          status: 200,
          contentType: "text/event-stream",
          body: `event: status-update\ndata: ${JSON.stringify({
            jsonrpc: "2.0",
            id: "reliability-inspector-event",
            result: {
              kind: "status-update",
              taskId,
              contextId,
              status: { state: "working", timestamp: createdAt },
              metadata: {
                localEventId: 1,
                localEventType: "task_cancel_requested",
                localEventCreatedAt: createdAt,
                localStatus: "cancel_requested",
                localThreadId: threadId,
                runtimeThreadId: threadId,
              },
            },
          })}\n\n`,
        })
      }
    )

    await page.goto("/agent")
    await expect(page.getByTestId("agent-console")).toBeVisible()

    const taskSummary = page.getByTestId("agent-task-state-summary")
    await expect(taskSummary).toBeVisible()
    await expect(taskSummary).toContainText("A2A Task")
    await expect(taskSummary).toContainText("working")
    await expect(taskSummary).toContainText("Local process")
    await expect(taskSummary).toContainText("cancel_requested")
    await expect(taskSummary).toContainText(threadId)

    const eventSummary = page.getByTestId("agent-selected-event-summary")
    await expect(eventSummary).toBeVisible()
    await expect(eventSummary).toContainText("working")
    await expect(eventSummary).toContainText("cancel_requested")

    const rawRecord = page.getByTestId("agent-raw-event-record")
    await expect(rawRecord).not.toHaveAttribute("open", "")
    await rawRecord.locator("summary").click()
    await expect(page.getByTestId("agent-event-payload")).toContainText(
      '"localStatus": "cancel_requested"'
    )
  })
})
