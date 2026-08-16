import { expect, test, type Page } from "@playwright/test"

import { mergeTaskWithA2ASnapshot } from "@/lib/agent/task-presentation"
import { taskProjectionReducer } from "@/lib/agent/task-projection-reducer"
import type {
  AgentStoredMessage,
  AgentTask,
  AgentTaskEvent,
} from "@/lib/agent/types"
import { TaskEventStreamRegistry } from "@/lib/agent/task-event-stream-registry"

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

async function mockRunningTaskStream(
  page: Page,
  prefix: string,
  streamBody: (fixture: {
    contextId: string
    taskId: string
    threadId: string
    startedAt: string
  }) => string
) {
  const now = Date.now()
  const contextId = `ctx-${prefix}-${now}`
  const taskId = `task-${prefix}-${now}`
  const threadId = `thread-${prefix}-${now}`
  const prompt = `${prefix} ${now}`
  const startedAt = new Date(now).toISOString()
  const requests = {
    taskSnapshots: 0,
    taskEventStreams: 0,
  }

  await mockAgentRegressionBootstrap(page, [
    {
      context_id: contextId,
      title: prompt,
      metadata: {},
      created_at: startedAt,
      updated_at: startedAt,
    },
  ])
  await page.route(
    `**/api/bff/agent/contexts/${contextId}/messages**`,
    async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify([
          {
            message_id: `msg-${prefix}-user-${now}`,
            context_id: contextId,
            role: "user",
            parts: [{ kind: "text", text: prompt }],
            task_id: taskId,
            metadata: { executionMode: "task" },
            created_at: startedAt,
          },
          {
            message_id: `msg-${prefix}-pending-${now}`,
            context_id: contextId,
            role: "agent",
            parts: [],
            task_id: taskId,
            metadata: {},
            created_at: startedAt,
          },
        ]),
      })
    }
  )
  await page.route(
    `**/api/bff/agent/contexts/${contextId}/tasks**`,
    async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify([
          {
            task_id: taskId,
            context_id: contextId,
            status: "running",
            input_message_id: `msg-${prefix}-user-${now}`,
            output_message_id: null,
            runtime_thread_id: threadId,
            attempt: 1,
            created_at: startedAt,
            updated_at: startedAt,
          },
        ]),
      })
    }
  )
  for (const resource of ["route-decisions", "delegations"]) {
    await page.route(
      `**/api/bff/agent/contexts/${contextId}/${resource}**`,
      async (route) => {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: "[]",
        })
      }
    )
  }
  await page.route(`**/api/bff/agent/a2a/tasks/${taskId}`, async (route) => {
    requests.taskSnapshots += 1
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        kind: "task",
        id: taskId,
        contextId,
        status: { state: "working", timestamp: startedAt },
        metadata: {
          localStatus: "running",
          localThreadId: threadId,
          runtimeThreadId: threadId,
        },
      }),
    })
  })
  await page.route(
    `**/api/bff/agent/a2a/tasks/${taskId}/events**`,
    async (route) => {
      requests.taskEventStreams += 1
      await route.fulfill({
        status: 200,
        contentType: "text/event-stream",
        body: streamBody({ contextId, taskId, threadId, startedAt }),
      })
    }
  )

  return { contextId, taskId, threadId, startedAt, requests }
}

test.describe("Single-host runtime reliability", () => {
  test("does not let an older continuation snapshot regress Task state", () => {
    const current: AgentTask = {
      task_id: "task-1",
      session_id: "ctx-1",
      thread_id: "thread-1",
      a2a_state: "completed",
      local_process_status: "completed",
      lifecycle_revision: 5,
      status: "completed",
      input: "run diagnostics",
      attempt: 1,
      metadata: { currentEvidence: true },
      created_at: "2026-08-16T10:00:00.000Z",
      updated_at: "2026-08-16T10:00:10.000Z",
    }

    const merged = mergeTaskWithA2ASnapshot(current, {
      kind: "task",
      id: "task-1",
      contextId: "ctx-1",
      status: {
        state: "submitted",
        timestamp: "2026-08-16T10:00:15.000Z",
      },
      metadata: { localStatus: "queued", lifecycleRevision: 4 },
    })

    expect(merged).toBe(current)
    expect(merged.status).toBe("completed")
    expect(merged.metadata).toEqual({ currentEvidence: true })
  })

  test("applies a newer continuation snapshot without dropping metadata", () => {
    const current: AgentTask = {
      task_id: "task-1",
      session_id: "ctx-1",
      thread_id: "thread-1",
      status: "interrupted",
      lifecycle_revision: 4,
      input: "run diagnostics",
      attempt: 1,
      metadata: { inputRequest: { kind: "approval_required" } },
      created_at: "2026-08-16T10:00:00.000Z",
      updated_at: "2026-08-16T10:00:05.000Z",
    }

    const merged = mergeTaskWithA2ASnapshot(current, {
      kind: "task",
      id: "task-1",
      contextId: "ctx-1",
      status: {
        state: "working",
        timestamp: "2026-08-16T10:00:01.000Z",
      },
      metadata: {
        localStatus: "running",
        runtimeThreadId: "thread-1",
        lifecycleRevision: 5,
      },
    })

    expect(merged.status).toBe("running")
    expect(merged.lifecycle_revision).toBe(5)
    expect(merged.updated_at).toBe("2026-08-16T10:00:01.000Z")
    expect(merged.metadata).toEqual({
      localStatus: "running",
      runtimeThreadId: "thread-1",
      lifecycleRevision: 5,
    })
  })

  test("falls back to timestamps when lifecycle revision is unavailable", () => {
    const current: AgentTask = {
      task_id: "task-legacy",
      session_id: "ctx-1",
      thread_id: "thread-1",
      status: "completed",
      input: "legacy task",
      attempt: 1,
      created_at: "2026-08-16T10:00:00.000Z",
      updated_at: "2026-08-16T10:00:10.000Z",
    }

    const merged = mergeTaskWithA2ASnapshot(current, {
      kind: "task",
      id: "task-legacy",
      contextId: "ctx-1",
      status: {
        state: "submitted",
        timestamp: "2026-08-16T10:00:05.000Z",
      },
      metadata: { localStatus: "queued" },
    })

    expect(merged).toBe(current)
  })

  test("reduces out-of-order and equal-revision Task events deterministically", () => {
    const task: AgentTask = {
      task_id: "task-1",
      session_id: "ctx-1",
      thread_id: "thread-1",
      lifecycle_revision: 3,
      a2a_state: "working",
      local_process_status: "running",
      status: "running",
      input: "run diagnostics",
      attempt: 1,
      created_at: "2026-08-16T10:00:00.000Z",
      updated_at: "2026-08-16T10:00:03.000Z",
    }
    const completed: AgentTaskEvent = {
      event_id: 9,
      task_id: task.task_id,
      session_id: task.session_id,
      lifecycle_revision: 5,
      a2a_state: "completed",
      local_process_status: "completed",
      event_type: "task_completed",
      status: "completed",
      payload: {
        metadata: {
          lifecycleRevision: 5,
          localStatus: "completed",
        },
      },
      created_at: "2026-08-16T10:00:05.000Z",
    }
    const staleQueued: AgentTaskEvent = {
      event_id: 10,
      task_id: task.task_id,
      session_id: task.session_id,
      lifecycle_revision: 4,
      a2a_state: "submitted",
      local_process_status: "queued",
      event_type: "task_queued",
      status: "queued",
      payload: {
        metadata: { lifecycleRevision: 4, localStatus: "queued" },
      },
      created_at: "2026-08-16T10:00:10.000Z",
    }
    const equalRevisionArtifact: AgentTaskEvent = {
      event_id: 11,
      task_id: task.task_id,
      session_id: task.session_id,
      lifecycle_revision: 5,
      event_type: "task_artifact_created",
      status: null,
      payload: { metadata: { lifecycleRevision: 5 } },
      created_at: "2026-08-16T10:00:06.000Z",
    }
    const equalRevisionStaleStatus: AgentTaskEvent = {
      event_id: 12,
      task_id: task.task_id,
      session_id: task.session_id,
      lifecycle_revision: 5,
      a2a_state: "failed",
      local_process_status: "failed",
      event_type: "task_failed",
      status: "failed",
      payload: {
        metadata: {
          lifecycleRevision: 5,
          localStatus: "failed",
          inputRequest: { kind: "approval_required" },
          localErrorCode: "stale_failure",
          localErrorMessage: "stale failure",
          localErrorRetryable: true,
        },
      },
      created_at: "2026-08-16T10:00:07.000Z",
    }

    const completedState = taskProjectionReducer(
      { [task.task_id]: task },
      { type: "event", event: completed }
    )
    const staleState = taskProjectionReducer(completedState, {
      type: "event",
      event: staleQueued,
    })
    const withArtifact = taskProjectionReducer(staleState, {
      type: "event",
      event: equalRevisionArtifact,
      finalAnswer: "diagnostics complete",
    })
    const duplicate = taskProjectionReducer(withArtifact, {
      type: "event",
      event: equalRevisionArtifact,
      finalAnswer: "diagnostics complete",
    })
    const equalRevisionState = taskProjectionReducer(duplicate, {
      type: "event",
      event: equalRevisionStaleStatus,
    })

    expect(staleState[task.task_id].status).toBe("completed")
    expect(staleState[task.task_id].lifecycle_revision).toBe(5)
    expect(withArtifact[task.task_id].status).toBe("completed")
    expect(withArtifact[task.task_id].final_answer).toBe(
      "diagnostics complete"
    )
    expect(duplicate[task.task_id]).toEqual(withArtifact[task.task_id])
    expect(equalRevisionState[task.task_id].status).toBe("completed")
    expect(equalRevisionState[task.task_id].metadata?.localStatus).toBe(
      "completed"
    )
    expect(equalRevisionState[task.task_id].metadata?.inputRequest).toBeUndefined()
    expect(equalRevisionState[task.task_id].metadata?.localErrorCode).toBeUndefined()
    expect(equalRevisionState[task.task_id].error).toBeFalsy()
  })

  test("owns and closes one physical event stream per task", () => {
    const registry = new TaskEventStreamRegistry()
    let firstCloseCount = 0
    let replacementCloseCount = 0
    let otherCloseCount = 0
    const first = { close: () => (firstCloseCount += 1) }
    const replacement = { close: () => (replacementCloseCount += 1) }
    const other = { close: () => (otherCloseCount += 1) }

    registry.replace("task-1", first)
    registry.replace("task-1", replacement)
    registry.replace("task-2", other)

    expect(firstCloseCount).toBe(1)
    expect(registry.size).toBe(2)
    expect(registry.has("task-1")).toBe(true)

    expect(registry.close("task-1", first)).toBe(false)
    expect(replacementCloseCount).toBe(0)
    expect(registry.close("task-1", replacement)).toBe(true)
    expect(replacementCloseCount).toBe(1)

    registry.closeAll()
    expect(otherCloseCount).toBe(1)
    expect(registry.size).toBe(0)
  })

  test("loads a selected Session through bounded read endpoints", async ({
    page,
  }) => {
    const now = Date.now()
    const firstContextId = `ctx-bounded-first-${now}`
    const secondContextId = `ctx-bounded-second-${now}`
    const createdAt = new Date(now).toISOString()
    const requestedUrls: string[] = []
    const contexts = [
      {
        context_id: firstContextId,
        title: "First bounded session",
        metadata: {},
        created_at: createdAt,
        updated_at: createdAt,
      },
      {
        context_id: secondContextId,
        title: "Second bounded session",
        metadata: {},
        created_at: createdAt,
        updated_at: createdAt,
      },
    ]

    await mockAgentRegressionBootstrap(page, contexts)
    for (const [contextId, label] of [
      [firstContextId, "first"],
      [secondContextId, "second"],
    ] as const) {
      for (const resource of [
        "messages",
        "tasks",
        "route-decisions",
        "delegations",
      ] as const) {
        await page.route(
          `**/api/bff/agent/contexts/${contextId}/${resource}**`,
          async (route) => {
            requestedUrls.push(route.request().url())
            const body =
              resource === "messages"
                ? [
                    {
                      message_id: `msg-${label}-answer`,
                      context_id: contextId,
                      role: "agent",
                      parts: [
                        {
                          kind: "text",
                          text: `${label} bounded answer`,
                        },
                      ],
                      task_id: null,
                      metadata: {},
                      created_at: createdAt,
                    },
                  ]
                : []
            await route.fulfill({
              status: 200,
              contentType: "application/json",
              body: JSON.stringify(body),
            })
          }
        )
      }
    }

    await page.goto("/agent")
    await expect(page.getByText("first bounded answer")).toBeVisible()
    await page
      .getByRole("button", { name: /Second bounded session/ })
      .click()
    await expect(page.getByText("second bounded answer")).toBeVisible()
    await expect(page.getByText("first bounded answer")).toHaveCount(0)

    const secondContextReads = requestedUrls.filter((value) =>
      value.includes(`/contexts/${secondContextId}/`)
    )
    expect(secondContextReads).toHaveLength(4)
    for (const value of secondContextReads) {
      const url = new URL(value)
      expect(url.searchParams.get("limit")).toBe("200")
      expect(url.searchParams.get("offset")).toBe("0")
    }
  })

  test("renders an invalid Task event stream instead of loading forever", async ({
    page,
  }) => {
    const fixture = await mockRunningTaskStream(
      page,
      "reliability-invalid-task-stream",
      ({ contextId, taskId, threadId, startedAt }) =>
        `event: status-update\ndata: ${JSON.stringify({
            jsonrpc: "2.0",
            id: "invalid-task-event",
            result: {
              kind: "status-update",
              taskId,
              contextId,
              status: { state: "working", timestamp: startedAt },
              metadata: {
                localStatus: "running",
                runtimeThreadId: threadId,
              },
            },
          })}\n\n`
    )

    await page.goto("/agent")
    await expect(page.getByTestId("agent-console")).toBeVisible()
    const failure = page.getByTestId("agent-task-failure")
    await expect(failure).toBeVisible()
    await expect(failure).toContainText(
      "The Task stream returned an invalid A2A event."
    )
    await expect(failure).toContainText("invalid_a2a_stream")
    await expect(page.getByText("Waiting for final answer...")).toHaveCount(0)
    await expectLatestTaskStatus(page, "running")
    expect(fixture.requests.taskSnapshots).toBeGreaterThanOrEqual(2)

    await page.waitForTimeout(750)
    expect(fixture.requests.taskEventStreams).toBe(1)
  })

  test("renders a server Task projection error and closes its subscription", async ({
    page,
  }) => {
    const fixture = await mockRunningTaskStream(
      page,
      "reliability-task-projection-error",
      () =>
        `event: error\ndata: ${JSON.stringify({
          jsonrpc: "2.0",
          id: "task-projection-error",
          error: {
            code: -32603,
            message: "A persisted Task event could not be projected to A2A.",
            data: {
              localCode: "task_event_projection_error",
              retryable: false,
            },
          },
        })}\n\n`
    )

    await page.goto("/agent")
    await expect(page.getByTestId("agent-console")).toBeVisible()
    const failure = page.getByTestId("agent-task-failure")
    await expect(failure).toBeVisible()
    await expect(failure).toContainText(
      "A persisted Task event could not be projected to A2A."
    )
    await expect(failure).toContainText("task_event_projection_error")
    await expect(page.getByText("Waiting for final answer...")).toHaveCount(0)

    await page.waitForTimeout(750)
    expect(fixture.requests.taskEventStreams).toBe(1)
  })

  test("renders malformed Message SSE data as a protocol failure", async ({
    page,
  }) => {
    await mockAgentRegressionBootstrap(page)
    await page.route("**/api/bff/agent/a2a/message-stream", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "text/event-stream",
        body: "event: message\ndata: {malformed-json}\n\n",
      })
    })
    await page.route("**/api/bff/agent/message-ingress/**", async (route) => {
      await route.fulfill({
        status: 404,
        contentType: "application/json",
        body: JSON.stringify({
          code: "message_ingress_not_found",
          message: "Message ingress was not persisted.",
          retryable: false,
        }),
      })
    })

    await page.goto("/agent")
    await page.getByTestId("agent-composer-input").fill("malformed stream")
    await page.getByTestId("agent-composer-send").click()

    const failure = page.getByTestId("agent-direct-message-failure")
    await expect(failure).toBeVisible()
    await expect(failure).toContainText(
      "The A2A stream returned malformed JSON."
    )
    await expect(failure).toContainText("invalid_a2a_stream")
    await expect(page.getByText("Waiting for final answer...")).toHaveCount(0)
  })

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
      `**/api/bff/agent/contexts/${contextId}/messages**`,
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
      `**/api/bff/agent/contexts/${contextId}/tasks**`,
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
      `**/api/bff/agent/contexts/${contextId}/route-decisions**`,
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
      `**/api/bff/agent/contexts/${contextId}/delegations**`,
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
      `**/api/bff/agent/contexts/${contextId}/messages**`,
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
      `**/api/bff/agent/contexts/${contextId}/tasks**`,
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
      `**/api/bff/agent/contexts/${contextId}/route-decisions**`,
      async (route) => {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: "[]",
        })
      }
    )
    await page.route(
      `**/api/bff/agent/contexts/${contextId}/delegations**`,
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

  test("shows an approved task result without requiring a page refresh", async ({
    page,
  }) => {
    const now = Date.now()
    const contextId = `ctx-reliability-approval-resume-${now}`
    const taskId = `task-reliability-approval-resume-${now}`
    const threadId = `thread-reliability-approval-resume-${now}`
    const prompt = `delete approved resource ${now}`
    const answer = `Approved task completed ${now}`
    const startedAt = new Date(now).toISOString()
    const resumedAt = new Date(now + 1000).toISOString()
    const completedAt = new Date(now + 2000).toISOString()
    let resumed = false
    let continuationCompleted = false
    let resumeRequestCount = 0
    let taskEventStreamRequests = 0
    const continuationAfterEventIds: string[] = []

    const statusUpdate = (
      eventId: number,
      eventType: string,
      state: "working" | "input-required" | "completed",
      timestamp: string,
      final = false
    ) => ({
      jsonrpc: "2.0",
      id: `reliability-approval-event-${eventId}`,
      result: {
        kind: "status-update",
        taskId,
        contextId,
        status: { state, timestamp },
        final,
        metadata: {
          localEventId: eventId,
          localEventType: eventType,
          localEventCreatedAt: timestamp,
          localStatus:
            state === "input-required"
              ? "input_required"
              : state === "working"
                ? "running"
                : "completed",
          localThreadId: threadId,
          runtimeThreadId: threadId,
        },
      },
    })

    await mockAgentRegressionBootstrap(page)
    await page.route("**/api/bff/agent/a2a/message-stream", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "text/event-stream",
        body: [
          `event: task\ndata: ${JSON.stringify({
            jsonrpc: "2.0",
            id: "reliability-approval-task",
            result: {
              kind: "task",
              id: taskId,
              contextId,
              status: { state: "input-required", timestamp: startedAt },
              metadata: {
                localStatus: "input_required",
                localThreadId: threadId,
                runtimeThreadId: threadId,
              },
            },
          })}\n\n`,
          `event: status-update\ndata: ${JSON.stringify(
            statusUpdate(
              10,
              "task_interrupted",
              "input-required",
              startedAt
            )
          )}\n\n`,
        ].join(""),
      })
    })
    await page.route(
      `**/api/bff/agent/a2a/tasks/${taskId}/events**`,
      async (route) => {
        taskEventStreamRequests += 1
        const url = new URL(route.request().url())
        if (!resumed) {
          await route.fulfill({
            status: 200,
            contentType: "text/event-stream",
            body: "",
          })
          return
        }

        continuationAfterEventIds.push(url.searchParams.get("after") ?? "")
        continuationCompleted = true
        await route.fulfill({
          status: 200,
          contentType: "text/event-stream",
          body: [
            `event: status-update\ndata: ${JSON.stringify(
              statusUpdate(11, "task_started", "working", resumedAt)
            )}\n\n`,
            `event: artifact-update\ndata: ${JSON.stringify({
              jsonrpc: "2.0",
              id: "reliability-approval-artifact-12",
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
                metadata: {
                  localEventId: 12,
                  localEventType: "task_artifact_created",
                  localEventCreatedAt: completedAt,
                  localThreadId: threadId,
                  runtimeThreadId: threadId,
                },
              },
            })}\n\n`,
            `event: status-update\ndata: ${JSON.stringify(
              statusUpdate(
                13,
                "task_completed",
                "completed",
                completedAt,
                true
              )
            )}\n\n`,
          ].join(""),
        })
      }
    )
    await page.route(
      `**/api/bff/agent/a2a/tasks/${taskId}/resume`,
      async (route) => {
        resumeRequestCount += 1
        resumed = true
        expect(JSON.parse(route.request().postData() || "{}")).toMatchObject({
          approved: true,
        })
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            kind: "task",
            id: taskId,
            contextId,
            status: { state: "submitted", timestamp: resumedAt },
            metadata: {
              localStatus: "queued",
              localThreadId: threadId,
              runtimeThreadId: threadId,
            },
          }),
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
            state: resumed ? "completed" : "input-required",
            timestamp: resumed ? completedAt : startedAt,
          },
          metadata: {
            localStatus: resumed ? "completed" : "input_required",
            localThreadId: threadId,
            runtimeThreadId: threadId,
          },
        }),
      })
    })
    await page.route(
      `**/api/bff/agent/contexts/${contextId}/messages**`,
      async (route) => {
        const messages: AgentStoredMessage[] = [
          {
            message_id: `msg-reliability-approval-user-${now}`,
            context_id: contextId,
            role: "user",
            parts: [{ kind: "text", text: prompt }],
            task_id: taskId,
            metadata: { executionMode: "task" },
            created_at: startedAt,
          },
        ]
        if (continuationCompleted) {
          messages.push({
            message_id: `msg-reliability-approval-agent-${now}`,
            context_id: contextId,
            role: "agent",
            parts: [{ kind: "text", text: answer }],
            task_id: taskId,
            metadata: { routeKind: "local_task" },
            created_at: completedAt,
          })
        }
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify(messages),
        })
      }
    )
    await page.route(
      `**/api/bff/agent/contexts/${contextId}/tasks**`,
      async (route) => {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify([
            {
              task_id: taskId,
              context_id: contextId,
              status: resumed ? "completed" : "interrupted",
              input_message_id: `msg-reliability-approval-user-${now}`,
              output_message_id: null,
              runtime_thread_id: threadId,
              attempt: 1,
              error_code: resumed ? null : "input_required",
              error_message: resumed ? null : "approval required",
              created_at: startedAt,
              updated_at: resumed ? completedAt : startedAt,
            },
          ]),
        })
      }
    )
    await page.route(
      `**/api/bff/agent/contexts/${contextId}/route-decisions**`,
      async (route) => {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: "[]",
        })
      }
    )
    await page.route(
      `**/api/bff/agent/contexts/${contextId}/delegations**`,
      async (route) => {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: "[]",
        })
      }
    )

    await page.goto("/agent")
    await expect(page.getByTestId("agent-console")).toBeVisible()
    await page.getByRole("button", { name: "New session" }).click()
    await page.getByTestId("agent-mode-task").click()
    await page.getByTestId("agent-composer-input").fill(prompt)
    await page.getByTestId("agent-composer-send").click()

    await expect(page.getByTestId("agent-approval-approve")).toBeVisible()
    await page.getByTestId("agent-approval-approve").click()

    await expect(assistantMessages(page).filter({ hasText: answer })).toHaveCount(1)
    await expect(assistantMessages(page).filter({ hasText: answer })).toBeVisible()
    await expectLatestTaskStatus(page, "completed")
    await expect(page.getByText("Waiting for final answer...")).toHaveCount(0)
    await expect(page.getByTestId("agent-approval-approve")).toHaveCount(0)
    expect(resumeRequestCount).toBe(1)
    expect(continuationAfterEventIds).toContain("10")

    await page.waitForTimeout(500)
    const settledEventStreamRequests = taskEventStreamRequests
    await page.waitForTimeout(1_000)
    expect(taskEventStreamRequests).toBe(settledEventStreamRequests)
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
      `**/api/bff/agent/contexts/${contextId}/messages**`,
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
      `**/api/bff/agent/contexts/${contextId}/tasks**`,
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
      `**/api/bff/agent/contexts/${contextId}/route-decisions**`,
      async (route) => {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: "[]",
        })
      }
    )
    await page.route(
      `**/api/bff/agent/contexts/${contextId}/delegations**`,
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
      `**/api/bff/agent/contexts/${contextId}/messages**`,
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
      `**/api/bff/agent/contexts/${contextId}/tasks**`,
      async (route) => {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify(retryRequested ? [sourceTask, retryTask] : [sourceTask]),
        })
      }
    )
    await page.route(
      `**/api/bff/agent/contexts/${contextId}/route-decisions**`,
      async (route) => {
        await route.fulfill({ status: 200, contentType: "application/json", body: "[]" })
      }
    )
    await page.route(
      `**/api/bff/agent/contexts/${contextId}/delegations**`,
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
      `**/api/bff/agent/contexts/${contextId}/messages**`,
      async (route) => {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: "[]",
        })
      }
    )
    await page.route(
      `**/api/bff/agent/contexts/${contextId}/tasks**`,
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
      `**/api/bff/agent/contexts/${contextId}/route-decisions**`,
      async (route) => {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: "[]",
        })
      }
    )
    await page.route(
      `**/api/bff/agent/contexts/${contextId}/delegations**`,
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
