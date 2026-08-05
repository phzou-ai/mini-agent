import { expect, test, type Page } from "@playwright/test"

import type { AgentStoredMessage } from "@/lib/agent/types"

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
  test("does not repeatedly refresh a completed task after its replay stream closes", async ({
    page,
  }) => {
    const now = Date.now()
    const contextId = `ctx-reliability-terminal-replay-${now}`
    const taskId = `task-reliability-terminal-replay-${now}`
    const threadId = `thread-reliability-terminal-replay-${now}`
    const prompt = `completed replay ${now}`
    const answer = `Completed replay answer ${now}`
    const completedAt = new Date(now).toISOString()
    let contextMessageRequests = 0
    let contextTaskRequests = 0
    let diagnosticsRequests = 0
    let taskSnapshotRequests = 0
    let taskEventStreamRequests = 0

    await mockAgentRegressionBootstrap(page, [
      {
        context_id: contextId,
        title: prompt,
        metadata: {},
        created_at: completedAt,
        updated_at: completedAt,
      },
    ])
    await page.route(
      `**/api/bff/agent/contexts/${contextId}/messages`,
      async (route) => {
        contextMessageRequests += 1
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify([
            {
              message_id: "msg-reliability-terminal-replay-user",
              context_id: contextId,
              role: "user",
              parts: [{ kind: "text", text: prompt }],
              task_id: taskId,
              metadata: { executionMode: "task" },
              created_at: completedAt,
            },
            {
              message_id: "msg-reliability-terminal-replay-agent",
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
        contextTaskRequests += 1
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify([
            {
              task_id: taskId,
              context_id: contextId,
              status: "completed",
              input_message_id: "msg-reliability-terminal-replay-user",
              output_message_id: "msg-reliability-terminal-replay-agent",
              runtime_thread_id: threadId,
              attempt: 1,
              created_at: completedAt,
              updated_at: completedAt,
            },
          ]),
        })
      }
    )
    await page.route(
      `**/api/bff/agent/contexts/${contextId}/route-decisions`,
      async (route) => {
        diagnosticsRequests += 1
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
        diagnosticsRequests += 1
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: "[]",
        })
      }
    )
    await page.route(`**/api/bff/agent/a2a/tasks/${taskId}`, async (route) => {
      taskSnapshotRequests += 1
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          kind: "task",
          id: taskId,
          contextId,
          status: { state: "completed", timestamp: completedAt },
          metadata: {
            localStatus: "completed",
            localThreadId: threadId,
            runtimeThreadId: threadId,
          },
        }),
      })
    })
    await page.route(
      `**/api/bff/agent/a2a/tasks/${taskId}/events**`,
      async (route) => {
        taskEventStreamRequests += 1
        await route.fulfill({
          status: 200,
          contentType: "text/event-stream",
          body: "",
        })
      }
    )

    await page.goto("/agent")
    await expect(page.getByTestId("agent-console")).toBeVisible()
    await expect(assistantMessages(page).filter({ hasText: answer })).toBeVisible()

    await page.waitForTimeout(750)
    const settledCounts = {
      contextMessageRequests,
      contextTaskRequests,
      diagnosticsRequests,
      taskSnapshotRequests,
      taskEventStreamRequests,
    }

    await page.waitForTimeout(1_250)
    expect({
      contextMessageRequests,
      contextTaskRequests,
      diagnosticsRequests,
      taskSnapshotRequests,
      taskEventStreamRequests,
    }).toEqual(settledCounts)
  })

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
          `event: artifact-update\ndata: ${JSON.stringify({
            jsonrpc: "2.0",
            id: "reliability-trailing-error-artifact",
            result: {
              kind: "artifact-update",
              taskId,
              contextId,
              artifact: {
                artifactId: "final_answer",
                parts: [{ kind: "text", text: answer }],
              },
              append: false,
              lastChunk: true,
            },
          })}\n\n`,
          `event: status-update\ndata: ${JSON.stringify({
            jsonrpc: "2.0",
            id: "reliability-trailing-error-completed",
            result: {
              kind: "status-update",
              taskId,
              contextId,
              status: { state: "completed", timestamp: completedAt },
              final: true,
              metadata: {
                localStatus: "completed",
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
    await expect(assistantMessages(page)).toHaveCount(1)
    await expect(page.getByText(answer)).toHaveCount(1)
    await expectLatestTaskStatus(page, "completed")
    await expect(page.getByTestId("agent-direct-message-failure")).toHaveCount(0)
  })

  test("renders a failed task instead of leaving its answer loading", async ({
    page,
  }) => {
    const now = Date.now()
    const contextId = `ctx-reliability-failed-task-${now}`
    const taskId = `task-reliability-failed-task-${now}`
    const threadId = `thread-reliability-failed-task-${now}`
    const prompt = `run failing task ${now}`
    const startedAt = new Date(now).toISOString()
    const failedAt = new Date(now + 1000).toISOString()
    const contexts: unknown[] = []
    const failureMetadata = {
      localStatus: "failed",
      localThreadId: threadId,
      runtimeThreadId: threadId,
      localErrorCode: "model_error",
      localErrorMessage: "Model request failed.",
      localErrorRetryable: true,
    }

    await mockAgentRegressionBootstrap(page, contexts)
    await page.route("**/api/bff/agent/a2a/message-stream", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "text/event-stream",
        body: [
          `event: task\ndata: ${JSON.stringify({
            jsonrpc: "2.0",
            id: "reliability-failed-task",
            result: {
              kind: "task",
              id: taskId,
              contextId,
              status: { state: "working", timestamp: startedAt },
              metadata: {
                localStatus: "running",
                localThreadId: threadId,
                runtimeThreadId: threadId,
              },
            },
          })}\n\n`,
          `event: status-update\ndata: ${JSON.stringify({
            jsonrpc: "2.0",
            id: "reliability-failed-task",
            result: {
              kind: "status-update",
              taskId,
              contextId,
              status: { state: "failed", timestamp: failedAt },
              final: true,
              metadata: {
                localEventId: 2,
                localEventType: "task_failed",
                localEventCreatedAt: failedAt,
                ...failureMetadata,
              },
            },
          })}\n\n`,
        ].join(""),
      })
    })
    await page.route(`**/api/bff/agent/a2a/tasks/${taskId}`, async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          kind: "task",
          id: taskId,
          contextId,
          status: { state: "failed", timestamp: failedAt },
          metadata: failureMetadata,
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
              message_id: "msg-reliability-failed-task-user",
              context_id: contextId,
              role: "user",
              parts: [{ kind: "text", text: prompt }],
              task_id: taskId,
              metadata: { executionMode: "task" },
              created_at: startedAt,
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
              status: "failed",
              input_message_id: "msg-reliability-failed-task-user",
              output_message_id: null,
              runtime_thread_id: threadId,
              error_code: "model_error",
              error_message: "Model request failed.",
              attempt: 1,
              created_at: startedAt,
              updated_at: failedAt,
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

    const failure = page.getByTestId("agent-task-failure")
    await expect(failure).toBeVisible()
    await expect(failure).toContainText("Task failed")
    await expect(failure).toContainText("Model request failed.")
    await expect(failure).toContainText("model_error")
    await expect(page.getByText("Waiting for final answer...")).toHaveCount(0)
    await expectLatestTaskStatus(page, "failed")

    contexts.push({
      context_id: contextId,
      title: prompt,
      metadata: {},
      created_at: startedAt,
      updated_at: failedAt,
    })
    await page.reload()

    await expect(page.getByTestId("agent-task-failure")).toBeVisible()
    await expect(page.getByTestId("agent-task-failure")).toContainText(
      "Model request failed."
    )
    await expect(page.getByText("Waiting for final answer...")).toHaveCount(0)
  })

  test("retries a safe failed task as one new task attempt", async ({ page }) => {
    const now = Date.now()
    const contextId = `ctx-reliability-task-retry-${now}`
    const sourceTaskId = `task-reliability-task-retry-source-${now}`
    const retryTaskId = `task-reliability-task-retry-child-${now}`
    const sourceThreadId = `thread-reliability-task-retry-source-${now}`
    const retryThreadId = `thread-reliability-task-retry-child-${now}`
    const prompt = `retry failed task ${now}`
    const retryAnswer = `Recovered task answer ${now}`
    const startedAt = new Date(now).toISOString()
    const failedAt = new Date(now + 1000).toISOString()
    const completedAt = new Date(now + 2000).toISOString()
    let retryRequested = false
    let retryRequestCount = 0

    const sourceTask = {
      task_id: sourceTaskId,
      context_id: contextId,
      status: "failed",
      input_message_id: "msg-reliability-task-retry-source-user",
      output_message_id: null,
      runtime_thread_id: sourceThreadId,
      attempt: 1,
      error_code: "model_error",
      error_message: "Model request failed.",
      error_retryable: true,
      created_at: startedAt,
      updated_at: failedAt,
    }
    const retryTask = {
      task_id: retryTaskId,
      context_id: contextId,
      status: "completed",
      input_message_id: "msg-reliability-task-retry-child-user",
      output_message_id: "msg-reliability-task-retry-child-agent",
      runtime_thread_id: retryThreadId,
      retry_of_task_id: sourceTaskId,
      attempt: 2,
      created_at: completedAt,
      updated_at: completedAt,
    }

    await mockAgentRegressionBootstrap(page, [
      {
        context_id: contextId,
        title: prompt,
        metadata: {},
        created_at: startedAt,
        updated_at: failedAt,
      },
    ])
    await page.route(
      `**/api/bff/agent/contexts/${contextId}/messages`,
      async (route) => {
        const messages: AgentStoredMessage[] = [
          {
            message_id: sourceTask.input_message_id,
            context_id: contextId,
            role: "user",
            parts: [{ kind: "text", text: prompt }],
            task_id: sourceTaskId,
            metadata: { executionMode: "task" },
            created_at: startedAt,
          },
        ]
        if (retryRequested) {
          messages.push(
            {
              message_id: retryTask.input_message_id,
              context_id: contextId,
              role: "user",
              parts: [{ kind: "text", text: prompt }],
              task_id: retryTaskId,
              metadata: { executionMode: "task", retryOfTaskId: sourceTaskId },
              created_at: completedAt,
            },
            {
              message_id: retryTask.output_message_id,
              context_id: contextId,
              role: "agent",
              parts: [{ kind: "text", text: retryAnswer }],
              task_id: retryTaskId,
              metadata: { routeKind: "local_task" },
              created_at: completedAt,
            }
          )
        }
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify(messages),
        })
      }
    )
    await page.route(
      `**/api/bff/agent/contexts/${contextId}/tasks`,
      async (route) => {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify(retryRequested ? [sourceTask, retryTask] : [sourceTask]),
        })
      }
    )
    await page.route(
      `**/api/bff/agent/contexts/${contextId}/route-decisions`,
      async (route) => {
        await route.fulfill({ status: 200, contentType: "application/json", body: "[]" })
      }
    )
    await page.route(
      `**/api/bff/agent/contexts/${contextId}/delegations`,
      async (route) => {
        await route.fulfill({ status: 200, contentType: "application/json", body: "[]" })
      }
    )
    await page.route(
      `**/api/bff/agent/a2a/tasks/${sourceTaskId}`,
      async (route) => {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            kind: "task",
            id: sourceTaskId,
            contextId,
            status: { state: "failed", timestamp: failedAt },
            metadata: {
              localStatus: "failed",
              localThreadId: sourceThreadId,
              runtimeThreadId: sourceThreadId,
              localErrorCode: "model_error",
              localErrorMessage: "Model request failed.",
              localErrorRetryable: true,
            },
          }),
        })
      }
    )
    await page.route(
      `**/api/bff/agent/a2a/tasks/${retryTaskId}`,
      async (route) => {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            kind: "task",
            id: retryTaskId,
            contextId,
            status: { state: "completed", timestamp: completedAt },
            metadata: {
              localStatus: "completed",
              localThreadId: retryThreadId,
              runtimeThreadId: retryThreadId,
            },
          }),
        })
      }
    )
    await page.route(
      `**/api/bff/agent/a2a/tasks/${sourceTaskId}/events**`,
      async (route) => {
        await route.fulfill({ status: 200, contentType: "text/event-stream", body: "" })
      }
    )
    await page.route(
      `**/api/bff/agent/a2a/tasks/${retryTaskId}/events**`,
      async (route) => {
        await route.fulfill({ status: 200, contentType: "text/event-stream", body: "" })
      }
    )
    await page.route(
      `**/api/bff/agent/tasks/${sourceTaskId}/retry`,
      async (route) => {
        retryRequestCount += 1
        retryRequested = true
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify(retryTask),
        })
      }
    )

    await page.goto("/agent")
    await expect(page.getByTestId("agent-console")).toBeVisible()

    const retryButton = page.getByTestId("agent-task-retry")
    await expect(retryButton).toBeVisible()
    await expect(retryButton).toBeEnabled()
    await retryButton.click()

    await expect(page.getByText(retryAnswer)).toHaveCount(1)
    await expect(page.getByText(retryAnswer)).toBeVisible()
    await expectLatestTaskStatus(page, "completed")
    expect(retryRequestCount).toBe(1)
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
