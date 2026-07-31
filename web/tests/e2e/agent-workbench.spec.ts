import { expect, test, type Page } from "@playwright/test"

import {
  a2aFinalMessageMetadata,
  a2aPartialMessageMetadata,
} from "../../lib/agent/a2a-stream-contract"

function userMessages(page: Page) {
  return page.locator(
    '[data-testid="agent-message-item"][data-agent-role="user"]'
  )
}

function assistantMessages(page: Page) {
  return page.locator(
    '[data-testid="agent-message-item"][data-agent-role="assistant"]'
  )
}

function taskStatusMessages(page: Page, status: string) {
  return assistantMessages(page).filter({ hasText: `task · ${status}` })
}

async function expectLatestTaskStatus(page: Page, status: string) {
  await expect(taskStatusMessages(page, status).last()).toBeVisible({
    timeout: 30000,
  })
}

async function expandChildAgentsPanel(page: Page) {
  const expandButton = page.getByRole("button", {
    name: "Expand child agents panel",
  })
  if ((await expandButton.count()) > 0) {
    await expandButton.click()
  }
}

async function mockAgentBootstrap(page: Page, contexts: unknown[] = []) {
  await page.route("**/api/bff/agent/contexts", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(contexts),
    })
  })
  await page.route("**/api/bff/agent/registered-agents**", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify([]),
    })
  })
  await page.route("**/api/bff/agent/a2a/agent-card", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        name: "Vermay Agent",
        description: "A2A-first main agent",
        url: "http://127.0.0.1:8000",
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
  })
  await page.route("**/api/bff/agent/model-config", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        primary_model: {
          name: "local_ollama",
          provider: "ollama",
          model: "qwen",
          base_url: "http://127.0.0.1:11434",
          timeout_seconds: 120,
        },
        router_model: {
          name: "local_ollama",
          provider: "ollama",
          model: "qwen",
          base_url: "http://127.0.0.1:11434",
          timeout_seconds: 120,
        },
        router_model_overridden: false,
        config_path: "/tmp/models.json",
      }),
    })
  })
}

test.describe("Agent Workbench", () => {
  test("does not require frontend auth when visiting the agent workspace", async ({
    page,
  }) => {
    let authSessionRequested = false
    await mockAgentBootstrap(page)
    await page.route("**/api/bff/auth/session", async (route) => {
      authSessionRequested = true
      await route.fulfill({
        status: 401,
        contentType: "application/json",
        body: JSON.stringify({ authenticated: false, username: null }),
      })
    })

    await page.goto("/agent")

    await expect(page.getByTestId("agent-console")).toBeVisible()
    await expect(page.getByRole("link", { name: "Login" })).toHaveCount(0)
    expect(authSessionRequested).toBe(false)
  })

  test("submits a local message and renders the model-backed answer", async ({
    page,
  }) => {
    const prompt = `hello e2e message ${Date.now()}`

    await page.goto("/agent")
    await expect(page.getByTestId("agent-console")).toBeVisible()
    await page.getByRole("button", { name: "New session" }).click()
    const assistantCount = await assistantMessages(page).count()

    await page.getByTestId("agent-mode-message").click()
    await page.getByTestId("agent-composer-input").fill(prompt)
    await page.getByTestId("agent-composer-send").click()

    await expect(userMessages(page).filter({ hasText: prompt })).toBeVisible()
    await expect(assistantMessages(page)).toHaveCount(assistantCount + 1)
    await expect(assistantMessages(page).nth(assistantCount)).not.toBeEmpty()
    await expect(
      assistantMessages(page)
        .nth(assistantCount)
        .getByRole("button", { name: "message" })
    ).toBeVisible()
    await expect(assistantMessages(page).nth(assistantCount)).not.toContainText(
      /message:/
    )
  })

  test("keeps multiple local messages visible in the same session", async ({
    page,
  }) => {
    const now = Date.now()
    const contextId = `ctx-e2e-transcript-${now}`
    const firstPrompt = `first transcript e2e message ${now}`
    const secondPrompt = `second transcript e2e message ${now}`

    await page.route("**/api/bff/agent/a2a/message-stream**", async (route) => {
      const body = JSON.parse(route.request().postData() || "{}")
      const text = String(body.text || "")
      const messageId = String(body.messageId || `msg-e2e-${Date.now()}`)
      await route.fulfill({
        status: 200,
        contentType: "text/event-stream",
        body: `event: message\ndata: ${JSON.stringify({
          jsonrpc: "2.0",
          id: "e2e-transcript-message",
          result: {
            kind: "message",
            role: "agent",
            messageId: `agent-${messageId}`,
            contextId: body.contextId || contextId,
            parts: [{ kind: "text", text: `Transcript answer: ${text}` }],
            metadata: { routeKind: "local_message" },
          },
        })}\n\n`,
      })
    })
    await page.route(
      `**/api/bff/agent/contexts/${contextId}/route-decisions`,
      async (route) => {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify([]),
        })
      }
    )
    await page.route(
      `**/api/bff/agent/contexts/${contextId}/delegations`,
      async (route) => {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify([]),
        })
      }
    )

    await page.goto("/agent")
    await expect(page.getByTestId("agent-console")).toBeVisible()

    await page.getByRole("button", { name: "New session" }).click()
    await page.getByTestId("agent-mode-message").click()
    await expect(page.getByTestId("agent-composer-send")).toHaveAttribute(
      "aria-label",
      "Send"
    )
    const assistantCount = await assistantMessages(page).count()
    await page.getByTestId("agent-composer-input").fill(firstPrompt)
    await page.getByTestId("agent-composer-send").click()

    await expect(
      userMessages(page).filter({ hasText: firstPrompt })
    ).toBeVisible()
    await expect(assistantMessages(page)).toHaveCount(assistantCount + 1)
    await expect(assistantMessages(page).nth(assistantCount)).toContainText(
      `Transcript answer: ${firstPrompt}`
    )
    await expect(page.getByTestId("agent-composer-send")).toHaveAttribute(
      "aria-label",
      "Send"
    )

    await page.getByTestId("agent-composer-input").fill(secondPrompt)
    await page.getByTestId("agent-composer-send").click()

    await expect(
      userMessages(page).filter({ hasText: firstPrompt })
    ).toBeVisible()
    await expect(
      userMessages(page).filter({ hasText: secondPrompt })
    ).toBeVisible()
    await expect(assistantMessages(page)).toHaveCount(assistantCount + 2)
    await expect(assistantMessages(page).nth(assistantCount)).toContainText(
      `Transcript answer: ${firstPrompt}`
    )
    await expect(assistantMessages(page).nth(assistantCount + 1)).toContainText(
      `Transcript answer: ${secondPrompt}`
    )
  })

  test("appends partial local-message stream chunks into one assistant bubble", async ({
    page,
  }) => {
    const now = Date.now()
    const contextId = `ctx-e2e-partial-stream-${now}`
    const prompt = `partial stream e2e message ${now}`
    const firstChunk = "streamed "
    const secondChunk = "answer"
    const finalAnswer = `${firstChunk}${secondChunk}`
    const firstPartialMetadata = {
      routeKind: "local_message",
      ...a2aPartialMessageMetadata(1),
    }
    const secondPartialMetadata = {
      routeKind: "local_message",
      ...a2aPartialMessageMetadata(2),
    }
    const finalMetadata = {
      routeKind: "local_message",
      ...a2aFinalMessageMetadata(),
    }

    await mockAgentBootstrap(page)
    await page.route(
      `**/api/bff/agent/contexts/${contextId}/route-decisions`,
      async (route) => {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify([]),
        })
      }
    )
    await page.route(
      `**/api/bff/agent/contexts/${contextId}/delegations`,
      async (route) => {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify([]),
        })
      }
    )
    await page.addInitScript(
      ({
        contextId,
        firstChunk,
        secondChunk,
        finalAnswer,
        firstPartialMetadata,
        secondPartialMetadata,
        finalMetadata,
      }) => {
        const originalFetch = window.fetch.bind(window)
        const encoder = new TextEncoder()
        const streamWindow = window as Window & {
          __releaseAgentStream?: () => void
        }

        function messageEvent(payload: unknown) {
          return encoder.encode(
            `event: message\ndata: ${JSON.stringify(payload)}\n\n`
          )
        }

        window.fetch = (input, init) => {
          const url =
            typeof input === "string"
              ? input
              : input instanceof Request
                ? input.url
                : String(input)
          if (!url.includes("/api/bff/agent/a2a/message-stream")) {
            return originalFetch(input, init)
          }

          const body = JSON.parse(String(init?.body || "{}"))
          const messageId = String(body.messageId || `msg-e2e-${Date.now()}`)
          const stream = new ReadableStream({
            start(controller) {
              controller.enqueue(
                messageEvent({
                  jsonrpc: "2.0",
                  id: "e2e-partial-message",
                  result: {
                    kind: "message",
                    role: "agent",
                    messageId: `agent-${messageId}`,
                    contextId,
                    parts: [{ kind: "text", text: firstChunk }],
                    metadata: firstPartialMetadata,
                  },
                })
              )

              streamWindow.__releaseAgentStream = () => {
                controller.enqueue(
                  messageEvent({
                    jsonrpc: "2.0",
                    id: "e2e-partial-message",
                    result: {
                      kind: "message",
                      role: "agent",
                      messageId: `agent-${messageId}`,
                      contextId,
                      parts: [{ kind: "text", text: secondChunk }],
                      metadata: secondPartialMetadata,
                    },
                  })
                )
                controller.enqueue(
                  messageEvent({
                    jsonrpc: "2.0",
                    id: "e2e-partial-message",
                    result: {
                      kind: "message",
                      role: "agent",
                      messageId: `agent-${messageId}`,
                      contextId,
                      parts: [{ kind: "text", text: finalAnswer }],
                      metadata: finalMetadata,
                    },
                  })
                )
                controller.close()
              }
            },
          })

          return Promise.resolve(
            new Response(stream, {
              status: 200,
              headers: { "Content-Type": "text/event-stream" },
            })
          )
        }
      },
      {
        contextId,
        firstChunk,
        secondChunk,
        finalAnswer,
        firstPartialMetadata,
        secondPartialMetadata,
        finalMetadata,
      }
    )

    await page.goto("/agent")
    await expect(page.getByTestId("agent-console")).toBeVisible()

    await page.getByRole("button", { name: "New session" }).click()
    await page.getByTestId("agent-mode-message").click()
    const assistantCount = await assistantMessages(page).count()
    await page.getByTestId("agent-composer-input").fill(prompt)
    await page.getByTestId("agent-composer-send").click()

    const streamedAssistant = assistantMessages(page).nth(assistantCount)
    await expect(streamedAssistant).toContainText(firstChunk)
    await expect(streamedAssistant).not.toContainText(finalAnswer)
    await expect(assistantMessages(page)).toHaveCount(assistantCount + 1)

    await page.evaluate(() => {
      const streamWindow = window as Window & {
        __releaseAgentStream?: () => void
      }
      streamWindow.__releaseAgentStream?.()
    })

    await expect(streamedAssistant).toContainText(finalAnswer)
    await expect(assistantMessages(page)).toHaveCount(assistantCount + 1)
    await expect(page.getByTestId("agent-composer-send")).toHaveAttribute(
      "aria-label",
      "Send"
    )
  })

  test("submits a task and renders the streamed final answer", async ({
    page,
  }) => {
    const prompt = `run e2e task ${Date.now()}`

    await page.goto("/agent")
    await expect(page.getByTestId("agent-console")).toBeVisible()

    await page.getByRole("button", { name: "New session" }).click()
    await page.getByTestId("agent-mode-task").click()
    await page.getByTestId("agent-composer-input").fill(prompt)
    await page.getByTestId("agent-composer-send").click()

    await expectLatestTaskStatus(page, "completed")
    await expect(userMessages(page).filter({ hasText: prompt })).toBeVisible()
    await expect(assistantMessages(page).last()).not.toBeEmpty()
  })

  test("keeps a completed task answer visible after page reload", async ({
    page,
  }) => {
    const prompt = `persist e2e task ${Date.now()}`

    await page.goto("/agent")
    await expect(page.getByTestId("agent-console")).toBeVisible()

    await page.getByRole("button", { name: "New session" }).click()
    await page.getByTestId("agent-mode-task").click()
    await page.getByTestId("agent-composer-input").fill(prompt)
    await page.getByTestId("agent-composer-send").click()

    await expectLatestTaskStatus(page, "completed")
    await expect(userMessages(page).filter({ hasText: prompt })).toBeVisible()
    await expect(assistantMessages(page).last()).not.toBeEmpty()

    await page.reload()
    await expect(page.getByTestId("agent-console")).toBeVisible()
    await expectLatestTaskStatus(page, "completed")
    await expect(userMessages(page).filter({ hasText: prompt })).toBeVisible()
    await expect(assistantMessages(page).last()).not.toBeEmpty()
  })

  test("updates the inspector event payload from the task timeline", async ({
    page,
  }) => {
    const prompt = `inspect e2e task ${Date.now()}`

    await page.goto("/agent")
    await expect(page.getByTestId("agent-console")).toBeVisible()

    await page.getByRole("button", { name: "New session" }).click()
    await page.getByTestId("agent-mode-task").click()
    await page.getByTestId("agent-composer-input").fill(prompt)
    await page.getByTestId("agent-composer-send").click()
    await expectLatestTaskStatus(page, "completed")
    await expect(assistantMessages(page).last()).not.toBeEmpty()

    await page
      .locator(
        '[data-testid="agent-timeline-event"][data-event-type="task_completed"]'
      )
      .click()

    await expect(page.getByTestId("agent-event-payload")).toContainText(
      '"event_type": "task_completed"'
    )
  })

  test("cancels an active task from the composer", async ({ page }) => {
    const now = Date.now()
    const prompt = `cancel e2e task ${now}`
    const contextId = `ctx-e2e-cancel-${now}`
    const taskId = `task-e2e-cancel-${now}`
    let canceled = false

    await page.route("**/api/bff/agent/a2a/message-stream", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "text/event-stream",
        body: `event: task\ndata: ${JSON.stringify({
          jsonrpc: "2.0",
          id: "e2e-cancel-task",
          result: {
            kind: "task",
            id: taskId,
            contextId,
            status: {
              state: "working",
              timestamp: new Date(now).toISOString(),
            },
            metadata: {},
          },
        })}\n\n`,
      })
    })

    await page.route("**/api/bff/agent/a2a/tasks/*/cancel", async (route) => {
      canceled = true
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          kind: "task",
          id: taskId,
          contextId,
          status: {
            state: "canceled",
            timestamp: new Date(now + 1).toISOString(),
          },
          metadata: {},
        }),
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
          status: {
            state: canceled ? "canceled" : "working",
            timestamp: new Date(now + 1).toISOString(),
          },
          metadata: {},
        }),
      })
    })

    await page.goto("/agent")
    await expect(page.getByTestId("agent-console")).toBeVisible()

    await page.getByRole("button", { name: "New session" }).click()
    await page.getByTestId("agent-mode-task").click()
    await page.getByTestId("agent-composer-input").fill(prompt)
    await page.getByTestId("agent-composer-send").click()

    await expectLatestTaskStatus(page, "running")
    await expect(page.getByTestId("agent-composer-send")).toHaveAttribute(
      "aria-label",
      "Stop generating"
    )

    await page.getByTestId("agent-composer-send").click()

    await expectLatestTaskStatus(page, "canceled")
  })

  test("shows a cancel error when the task cancel request is rejected", async ({
    page,
  }) => {
    const prompt = `cancel error e2e task ${Date.now()}`
    const errorMessage = "task is terminal and cannot be canceled: e2e"

    await page.goto("/agent")
    await expect(page.getByTestId("agent-console")).toBeVisible()

    await page.getByTestId("agent-mode-task").click()
    await page.getByTestId("agent-composer-input").fill(prompt)
    await page.getByTestId("agent-composer-send").click()

    await expectLatestTaskStatus(page, "running")

    await page.route("**/api/bff/agent/a2a/tasks/*/cancel", async (route) => {
      await route.fulfill({
        status: 409,
        contentType: "application/json",
        body: JSON.stringify({
          code: "invalid_session_state",
          message: errorMessage,
        }),
      })
    })

    await page.getByTestId("agent-composer-send").click()
    await expect(page.getByText(errorMessage)).toBeVisible()

    const selectedSession = page.locator(
      '[data-testid="agent-session-item"][data-selected="true"]'
    )
    await expect(selectedSession).toBeVisible()
    const sessionId = await selectedSession.getAttribute("data-session-id")
    expect(sessionId).toBeTruthy()

    page.once("dialog", (dialog) => dialog.accept())
    await selectedSession.getByTestId("agent-session-delete").click()
    await expect(
      page.locator(
        `[data-testid="agent-session-item"][data-session-id="${sessionId}"]`
      )
    ).toHaveCount(0)
  })

  test("resumes an approval-required task from the transcript", async ({
    page,
  }) => {
    const now = Date.now()
    const contextId = `ctx-e2e-approval-${now}`
    const taskId = `task-e2e-approval-${now}`
    const prompt = `approval e2e task ${now}`
    const answer = `Approved answer ${now}`
    const startedAt = new Date(now).toISOString()
    const completedAt = new Date(now + 1000).toISOString()
    let approvedPayload: { approved?: boolean; reason?: string } | null = null

    await mockAgentBootstrap(page)
    await page.route("**/api/bff/agent/a2a/message-stream", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "text/event-stream",
        body: `event: task\ndata: ${JSON.stringify({
          jsonrpc: "2.0",
          id: "e2e-approval-task",
          result: {
            kind: "task",
            id: taskId,
            contextId,
            status: {
              state: "input-required",
              timestamp: startedAt,
            },
            metadata: {
              localThreadId: `thread-${now}`,
              runtimeThreadId: `thread-${now}`,
            },
          },
        })}\n\n`,
      })
    })
    await page.route(
      `**/api/bff/agent/contexts/${contextId}/route-decisions`,
      async (route) => {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify([]),
        })
      }
    )
    await page.route(
      `**/api/bff/agent/contexts/${contextId}/delegations`,
      async (route) => {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify([]),
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
          status: {
            state: approvedPayload ? "completed" : "input-required",
            timestamp: approvedPayload ? completedAt : startedAt,
          },
          metadata: {
            localThreadId: `thread-${now}`,
            runtimeThreadId: `thread-${now}`,
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
    await page.route(
      `**/api/bff/agent/a2a/tasks/${taskId}/resume`,
      async (route) => {
        approvedPayload = JSON.parse(route.request().postData() || "{}")
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            kind: "task",
            id: taskId,
            contextId,
            status: {
              state: "completed",
              timestamp: completedAt,
            },
            metadata: {
              localThreadId: `thread-${now}`,
              runtimeThreadId: `thread-${now}`,
            },
          }),
        })
      }
    )
    await page.route(
      `**/api/bff/agent/contexts/${contextId}/messages`,
      async (route) => {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify([
            {
              message_id: `msg-user-${now}`,
              context_id: contextId,
              role: "user",
              parts: [{ kind: "text", text: prompt }],
              task_id: taskId,
              metadata: {},
              created_at: startedAt,
            },
            {
              message_id: `msg-agent-${now}`,
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
              status: approvedPayload ? "completed" : "interrupted",
              input_message_id: `msg-user-${now}`,
              output_message_id: approvedPayload ? `msg-agent-${now}` : null,
              runtime_thread_id: `thread-${now}`,
              attempt: 1,
              error_code: approvedPayload ? null : "input_required",
              error_message: approvedPayload ? null : "approval required",
              created_at: startedAt,
              updated_at: approvedPayload ? completedAt : startedAt,
            },
          ]),
        })
      }
    )

    await page.goto("/agent")
    await expect(page.getByTestId("agent-console")).toBeVisible()
    await page.getByTestId("agent-mode-task").click()
    await page.getByTestId("agent-composer-input").fill(prompt)
    await page.getByTestId("agent-composer-send").click()

    await expect(page.getByTestId("agent-approval-approve")).toBeVisible()
    await page.getByTestId("agent-composer-input").fill("yes")
    await page.getByTestId("agent-composer-send").click()

    await expect(
      assistantMessages(page).filter({ hasText: answer })
    ).toBeVisible()
    expect(approvedPayload).toMatchObject({ approved: true })
  })

  test("reconciles a repeated approval interrupt after task resume", async ({
    page,
  }) => {
    const now = Date.now()
    const contextId = `ctx-e2e-repeat-approval-${now}`
    const taskId = `task-e2e-repeat-approval-${now}`
    const threadId = `thread-e2e-repeat-approval-${now}`
    const prompt = `repeat approval e2e task ${now}`
    const startedAt = new Date(now).toISOString()
    const resumedAt = new Date(now + 1000).toISOString()
    let resumed = false
    const resumeAfterEventIds: string[] = []

    const statusEvent = (
      eventId: number,
      eventType: string,
      state: "working" | "input-required",
      timestamp: string
    ) => ({
      jsonrpc: "2.0",
      id: `e2e-repeat-approval-event-${eventId}`,
      result: {
        kind: "status-update",
        taskId,
        contextId,
        status: { state, timestamp },
        metadata: {
          localEventId: eventId,
          localEventType: eventType,
          localEventCreatedAt: timestamp,
          localThreadId: threadId,
          runtimeThreadId: threadId,
        },
      },
    })

    await mockAgentBootstrap(page)
    await page.route("**/api/bff/agent/a2a/message-stream", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "text/event-stream",
        body: [
          `event: task\ndata: ${JSON.stringify({
            jsonrpc: "2.0",
            id: "e2e-repeat-approval-task",
            result: {
              kind: "task",
              id: taskId,
              contextId,
              status: { state: "input-required", timestamp: startedAt },
              metadata: {
                localThreadId: threadId,
                runtimeThreadId: threadId,
              },
            },
          })}\n\n`,
          `event: status-update\ndata: ${JSON.stringify(
            statusEvent(10, "task_interrupted", "input-required", startedAt)
          )}\n\n`,
        ].join(""),
      })
    })
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
          status: {
            state: "input-required",
            timestamp: resumed ? resumedAt : startedAt,
          },
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
        const url = new URL(route.request().url())
        if (!resumed) {
          await route.fulfill({
            status: 200,
            contentType: "text/event-stream",
            body: "",
          })
          return
        }

        resumeAfterEventIds.push(url.searchParams.get("after") ?? "")
        await route.fulfill({
          status: 200,
          contentType: "text/event-stream",
          body: [
            `event: status-update\ndata: ${JSON.stringify(
              statusEvent(12, "task_started", "working", resumedAt)
            )}\n\n`,
            `event: status-update\ndata: ${JSON.stringify(
              statusEvent(
                13,
                "task_interrupted",
                "input-required",
                resumedAt
              )
            )}\n\n`,
          ].join(""),
        })
      }
    )
    await page.route(
      `**/api/bff/agent/a2a/tasks/${taskId}/resume`,
      async (route) => {
        resumed = true
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            kind: "task",
            id: taskId,
            contextId,
            status: { state: "submitted", timestamp: resumedAt },
            metadata: {
              localThreadId: threadId,
              runtimeThreadId: threadId,
            },
          }),
        })
      }
    )
    await page.route(
      `**/api/bff/agent/contexts/${contextId}/messages`,
      async (route) => {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify([
            {
              message_id: `msg-user-${now}`,
              context_id: contextId,
              role: "user",
              parts: [{ kind: "text", text: prompt }],
              task_id: taskId,
              metadata: {},
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
              status: "interrupted",
              input_message_id: `msg-user-${now}`,
              output_message_id: null,
              runtime_thread_id: threadId,
              attempt: 1,
              error_code: "input_required",
              error_message: "approval required",
              created_at: startedAt,
              updated_at: resumed ? resumedAt : startedAt,
            },
          ]),
        })
      }
    )

    await page.goto("/agent")
    await page.getByTestId("agent-mode-task").click()
    await page.getByTestId("agent-composer-input").fill(prompt)
    await page.getByTestId("agent-composer-send").click()

    await expect(page.getByTestId("agent-approval-approve")).toBeVisible()
    await page.getByTestId("agent-approval-approve").click()

    await expect(page.getByTestId("agent-approval-approve")).toBeVisible()
    await expectLatestTaskStatus(page, "interrupted")
    expect(resumeAfterEventIds).toContain("10")
    expect(resumeAfterEventIds).not.toContain("")
    expect(resumeAfterEventIds).not.toContain("0")
  })

  test("submits model-requested task input and continues the same task", async ({
    page,
  }) => {
    const now = Date.now()
    const contextId = `ctx-e2e-input-${now}`
    const taskId = `task-e2e-input-${now}`
    const prompt = `check deployment ${now}`
    const answer = `Staging is healthy ${now}`
    const startedAt = new Date(now).toISOString()
    const completedAt = new Date(now + 1000).toISOString()
    const inputRequest = {
      kind: "user_input_required",
      prompt: "Which environment should I inspect?",
      choices: ["staging", "production"],
      inputSchema: { type: "string", enum: ["staging", "production"] },
    }
    let submittedPayload: Record<string, unknown> | null = null

    await mockAgentBootstrap(page)
    await page.route("**/api/bff/agent/a2a/message-stream", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "text/event-stream",
        body: `event: task\ndata: ${JSON.stringify({
          jsonrpc: "2.0",
          id: "e2e-input-task",
          result: {
            kind: "task",
            id: taskId,
            contextId,
            status: { state: "input-required", timestamp: startedAt },
            metadata: {
              localThreadId: `thread-${now}`,
              runtimeThreadId: `thread-${now}`,
              inputRequest,
            },
          },
        })}\n\n`,
      })
    })
    await page.route("**/api/bff/agent/a2a/message", async (route) => {
      submittedPayload = JSON.parse(route.request().postData() || "{}")
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          kind: "task",
          contextId,
          task: {
            kind: "task",
            id: taskId,
            contextId,
            status: { state: "completed", timestamp: completedAt },
            metadata: {
              localThreadId: `thread-${now}`,
              runtimeThreadId: `thread-${now}`,
            },
          },
          raw: {},
        }),
      })
    })
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
    await page.route(`**/api/bff/agent/a2a/tasks/${taskId}`, async (route) => {
      const completed = submittedPayload !== null
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          kind: "task",
          id: taskId,
          contextId,
          status: {
            state: completed ? "completed" : "input-required",
            timestamp: completed ? completedAt : startedAt,
          },
          metadata: {
            localThreadId: `thread-${now}`,
            runtimeThreadId: `thread-${now}`,
            ...(completed ? {} : { inputRequest }),
          },
        }),
      })
    })
    await page.route(
      `**/api/bff/agent/a2a/tasks/${taskId}/events**`,
      async (route) => {
        await route.fulfill({ status: 200, contentType: "text/event-stream", body: "" })
      }
    )
    await page.route(
      `**/api/bff/agent/contexts/${contextId}/messages`,
      async (route) => {
        const completed = submittedPayload !== null
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify([
            {
              message_id: `msg-user-${now}`,
              context_id: contextId,
              role: "user",
              parts: [{ kind: "text", text: prompt }],
              task_id: taskId,
              metadata: {},
              created_at: startedAt,
            },
            ...(completed
              ? [
                  {
                    message_id: `msg-input-${now}`,
                    context_id: contextId,
                    role: "user",
                    parts: [{ kind: "text", text: "staging" }],
                    task_id: taskId,
                    metadata: {},
                    created_at: completedAt,
                  },
                  {
                    message_id: `msg-agent-${now}`,
                    context_id: contextId,
                    role: "agent",
                    parts: [{ kind: "text", text: answer }],
                    task_id: taskId,
                    metadata: {},
                    created_at: completedAt,
                  },
                ]
              : []),
          ]),
        })
      }
    )
    await page.route(
      `**/api/bff/agent/contexts/${contextId}/tasks`,
      async (route) => {
        const completed = submittedPayload !== null
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify([
            {
              task_id: taskId,
              context_id: contextId,
              status: completed ? "completed" : "interrupted",
              input_message_id: `msg-user-${now}`,
              output_message_id: completed ? `msg-agent-${now}` : null,
              runtime_thread_id: `thread-${now}`,
              attempt: 1,
              created_at: startedAt,
              updated_at: completed ? completedAt : startedAt,
            },
          ]),
        })
      }
    )

    await page.goto("/agent")
    await page.getByTestId("agent-mode-task").click()
    await page.getByTestId("agent-composer-input").fill(prompt)
    await page.getByTestId("agent-composer-send").click()

    await expect(page.getByTestId("agent-task-input-card")).toBeVisible()
    await expect(page.getByText("Which environment should I inspect?")).toBeVisible()
    const messageList = page.getByTestId("agent-message-list")
    const inputCard = page.getByTestId("agent-task-input-card")
    const [messageListWidth, messageListScrollWidth, inputCardBox] =
      await Promise.all([
        messageList.evaluate((element) => element.clientWidth),
        messageList.evaluate((element) => element.scrollWidth),
        inputCard.boundingBox(),
      ])
    expect(messageListScrollWidth).toBeLessThanOrEqual(messageListWidth + 1)
    expect(inputCardBox?.width).toBeLessThanOrEqual(messageListWidth)
    await page.getByTestId("agent-composer-input").fill("staging")
    await page.getByTestId("agent-composer-send").click()

    await expect(assistantMessages(page).filter({ hasText: answer })).toBeVisible()
    await expect
      .poll(() =>
        messageList.evaluate(
          (element) =>
            element.scrollHeight - element.scrollTop - element.clientHeight
        )
      )
      .toBeLessThanOrEqual(2)
    expect(submittedPayload).toMatchObject({
      taskId,
      contextId,
      text: "staging",
    })
  })

  test("deletes the selected session from the sidebar", async ({ page }) => {
    const prompt = `delete e2e session ${Date.now()}`

    await page.goto("/agent")
    await expect(page.getByTestId("agent-console")).toBeVisible()

    await page.getByTestId("agent-mode-message").click()
    await page.getByTestId("agent-composer-input").fill(prompt)
    await page.getByTestId("agent-composer-send").click()
    await expect(userMessages(page).filter({ hasText: prompt })).toBeVisible()
    await expect(assistantMessages(page).last()).not.toBeEmpty()

    const selectedSession = page.locator(
      '[data-testid="agent-session-item"][data-selected="true"]'
    )
    await expect(selectedSession).toBeVisible()
    const sessionId = await selectedSession.getAttribute("data-session-id")
    expect(sessionId).toBeTruthy()

    page.on("dialog", (dialog) => dialog.accept())
    await selectedSession.getByTestId("agent-session-delete").click()

    await expect(
      page.locator(
        `[data-testid="agent-session-item"][data-session-id="${sessionId}"]`
      )
    ).toHaveCount(0)
  })

  test("collapses and expands the desktop sidebar", async ({ page }) => {
    await page.goto("/agent")
    await expect(page.getByTestId("agent-console")).toBeVisible()

    await expect(page.getByTestId("agent-sidebar")).toHaveAttribute(
      "data-expanded",
      "true"
    )

    await page.getByRole("button", { name: "Collapse sidebar" }).click()
    await expect(page.getByTestId("agent-sidebar")).toHaveAttribute(
      "data-expanded",
      "false"
    )

    await page.getByRole("button", { name: "Expand sidebar" }).click()
    await expect(page.getByTestId("agent-sidebar")).toHaveAttribute(
      "data-expanded",
      "true"
    )
  })

  test("keeps the composer usable on a mobile viewport", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 })
    await page.goto("/agent")

    await expect(page.getByTestId("agent-console")).toBeVisible()
    await expect(page.getByTestId("agent-sidebar")).toBeHidden()
    await expect(page.getByTestId("agent-composer-input")).toBeVisible()
    await expect(page.getByTestId("agent-composer-send")).toBeVisible()
  })

  test("registers and selects a child agent route target", async ({ page }) => {
    const agentId = `e2e-child-${Date.now()}`
    const agentName = `E2E Child ${Date.now()}`

    await page.goto("/agent")
    await expect(page.getByTestId("agent-console")).toBeVisible()

    await expandChildAgentsPanel(page)
    await page.getByTestId("agent-registry-id").fill(agentId)
    await page.getByTestId("agent-registry-name").fill(agentName)
    await page
      .getByTestId("agent-registry-card-url")
      .fill("http://127.0.0.1:8000/.well-known/agent-card.json")
    await page.getByTestId("agent-registry-keywords").fill("e2e, route")
    await page.getByTestId("agent-registry-save").click()

    const registeredAgent = page.locator(
      `[data-testid="agent-registry-item"][data-agent-id="${agentId}"]`
    )
    await expect(registeredAgent).toBeVisible()

    await registeredAgent.getByTestId("agent-registry-select").click()
    await expect(page.getByTestId("agent-route-target")).toHaveValue(agentId)

    page.once("dialog", (dialog) => dialog.accept())
    await registeredAgent.getByTestId("agent-registry-delete").click()
    await expect(registeredAgent).toHaveCount(0)
  })

  test("renders a delegated child-agent message with route diagnostics", async ({
    page,
  }) => {
    const now = Date.now()
    const agentId = `e2e-delegate-child-${now}`
    const agentName = `E2E Delegate Child ${now}`
    const contextId = `ctx-e2e-delegate-${now}`
    const prompt = `delegate e2e message ${now}`
    const answer = `Remote child answer ${now}`
    const agentRecord = {
      agent_id: agentId,
      name: agentName,
      card_url: "http://127.0.0.1:8001/.well-known/agent-card.json",
      card_json: {},
      enabled: true,
      metadata: { keywords: ["delegation", "e2e"] },
      created_at: new Date(now).toISOString(),
      updated_at: new Date(now).toISOString(),
    }
    let registeredAgents: (typeof agentRecord)[] = []

    await page.route("**/api/bff/agent/contexts", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify([]),
      })
    })

    await page.route("**/api/bff/agent/registered-agents**", async (route) => {
      const method = route.request().method()
      if (method === "GET") {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify(registeredAgents),
        })
        return
      }
      if (method === "POST") {
        registeredAgents = [agentRecord]
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify(agentRecord),
        })
        return
      }
      if (method === "DELETE") {
        registeredAgents = []
        await route.fulfill({ status: 204 })
        return
      }
      await route.continue()
    })

    await page.route("**/api/bff/agent/a2a/message-stream", async (route) => {
      const body = JSON.parse(route.request().postData() || "{}")
      expect(body.route).toBe("remote_agent")
      expect(body.targetAgentId).toBe(agentId)

      await route.fulfill({
        status: 200,
        contentType: "text/event-stream",
        body: `event: message\ndata: ${JSON.stringify({
          jsonrpc: "2.0",
          id: "e2e-remote-message",
          result: {
            kind: "message",
            role: "agent",
            messageId: "remote-msg-e2e",
            contextId,
            parts: [{ kind: "text", text: answer }],
            metadata: {
              routeKind: "remote_agent",
              remoteAgentId: agentId,
              delegationId: "delegate-e2e",
            },
          },
        })}\n\n`,
      })
    })

    await page.route(
      `**/api/bff/agent/contexts/${contextId}/route-decisions`,
      async (route) => {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify([
            {
              decision_id: "route-e2e",
              context_id: contextId,
              message_id: "msg-e2e",
              kind: "remote_agent",
              reason: "metadata requested remote agent",
              confidence: null,
              target_agent_id: agentId,
              metadata: { source: "metadata" },
              created_at: new Date(now).toISOString(),
            },
          ]),
        })
      }
    )

    await page.route(
      `**/api/bff/agent/contexts/${contextId}/delegations`,
      async (route) => {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify([
            {
              delegation_id: "delegate-e2e",
              context_id: contextId,
              input_message_id: "msg-e2e",
              route_decision_id: "route-e2e",
              remote_agent_id: agentId,
              local_task_id: null,
              remote_task_id: null,
              remote_context_id: "remote-ctx-e2e",
              remote_message_id: "remote-msg-e2e",
              result_kind: "message",
              status: "completed",
              metadata: {},
              created_at: new Date(now).toISOString(),
              updated_at: new Date(now).toISOString(),
            },
          ]),
        })
      }
    )

    await page.goto("/agent")
    await expect(page.getByTestId("agent-console")).toBeVisible()

    await expandChildAgentsPanel(page)
    await page.getByTestId("agent-registry-id").fill(agentId)
    await page.getByTestId("agent-registry-name").fill(agentName)
    await page
      .getByTestId("agent-registry-card-url")
      .fill("http://127.0.0.1:8001/.well-known/agent-card.json")
    await page.getByTestId("agent-registry-keywords").fill("delegation, e2e")
    await page.getByTestId("agent-registry-save").click()

    const registeredAgent = page.locator(
      `[data-testid="agent-registry-item"][data-agent-id="${agentId}"]`
    )
    await expect(registeredAgent).toBeVisible()
    await registeredAgent.getByTestId("agent-registry-select").click()

    await page.getByTestId("agent-mode-message").click()
    await page.getByTestId("agent-composer-input").fill(prompt)
    await page.getByTestId("agent-composer-send").click()

    await expect(
      page
        .locator('[data-testid="agent-message-item"][data-agent-role="user"]')
        .filter({ hasText: prompt })
    ).toBeVisible()
    await expect(
      page
        .locator(
          '[data-testid="agent-message-item"][data-agent-role="assistant"]'
        )
        .filter({ hasText: answer })
    ).toBeVisible()
    await expect(page.getByText("Child agent", { exact: true })).toBeVisible()
    await expect(page.getByText(`target: ${agentId}`)).toBeVisible()
    await expect(page.getByText("remote task: remote-msg-e2e")).toBeVisible()

    page.once("dialog", (dialog) => dialog.accept())
    await registeredAgent.getByTestId("agent-registry-delete").click()
    await expect(registeredAgent).toHaveCount(0)
  })

  test("uses the first user input as session title and supports editing", async ({
    page,
  }) => {
    const now = Date.now()
    const contextId = `ctx-e2e-title-${now}`
    const longPrompt = `Show me a very long Kubernetes diagnosis title for truncation coverage ${now}`
    const renamedTitle = `Renamed title ${now}`

    await mockAgentBootstrap(page)
    await page.route("**/api/bff/agent/a2a/message-stream", async (route) => {
      const body = JSON.parse(route.request().postData() || "{}")
      await route.fulfill({
        status: 200,
        contentType: "text/event-stream",
        body: `event: message\ndata: ${JSON.stringify({
          jsonrpc: "2.0",
          id: "e2e-title-message",
          result: {
            kind: "message",
            role: "agent",
            messageId: `msg-agent-${now}`,
            contextId,
            parts: [{ kind: "text", text: `Answer: ${body.text}` }],
            metadata: { routeKind: "local_message" },
          },
        })}\n\n`,
      })
    })
    await page.route(
      `**/api/bff/agent/contexts/${contextId}/route-decisions`,
      async (route) => {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify([]),
        })
      }
    )
    await page.route(
      `**/api/bff/agent/contexts/${contextId}/delegations`,
      async (route) => {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify([]),
        })
      }
    )
    await page.route(
      `**/api/bff/agent/contexts/${contextId}`,
      async (route) => {
        if (route.request().method() !== "PATCH") return route.continue()
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            context_id: contextId,
            title: renamedTitle,
            metadata: {},
            created_at: new Date(now).toISOString(),
            updated_at: new Date(now).toISOString(),
          }),
        })
      }
    )

    await page.goto("/agent")
    await expect(page.getByTestId("agent-console")).toBeVisible()
    await page.getByTestId("agent-mode-message").click()
    await page.getByTestId("agent-composer-input").fill(longPrompt)
    await page.getByTestId("agent-composer-send").click()

    const selectedSession = page.locator(
      '[data-testid="agent-session-item"][data-selected="true"]'
    )
    await expect(selectedSession.getByTitle(longPrompt)).toBeVisible()
    const sidebarWidth = await page
      .getByTestId("agent-sidebar")
      .evaluate((element) => element.clientWidth)
    const sidebarScrollWidth = await page
      .getByTestId("agent-sidebar")
      .evaluate((element) => element.scrollWidth)
    expect(sidebarScrollWidth).toBeLessThanOrEqual(sidebarWidth + 1)

    await selectedSession.getByTestId("agent-session-edit").click()
    await page.getByTestId("agent-session-title-input").fill(renamedTitle)
    await page.getByTestId("agent-session-title-save").click()

    await expect(selectedSession.getByTitle(renamedTitle)).toBeVisible()
  })

  test("keeps composer layout stable across execution modes", async ({
    page,
  }) => {
    await mockAgentBootstrap(page)
    await page.goto("/agent")
    await expect(page.getByTestId("agent-console")).toBeVisible()

    async function composerHeight() {
      return page.getByTestId("agent-composer-input").evaluate((element) => {
        const root = element.closest("[data-composer-active]")
        return root instanceof HTMLElement ? root.offsetHeight : 0
      })
    }

    const autoHeight = await composerHeight()
    await page.getByTestId("agent-mode-message").click()
    const messageHeight = await composerHeight()
    await page.getByTestId("agent-mode-task").click()
    const taskHeight = await composerHeight()
    await page.getByTestId("agent-mode-auto").click()
    const autoHeightAgain = await composerHeight()

    expect(messageHeight).toBe(autoHeight)
    expect(taskHeight).toBe(autoHeight)
    expect(autoHeightAgain).toBe(autoHeight)
  })
})
