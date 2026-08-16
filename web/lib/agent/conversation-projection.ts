import { textFromA2AParts as textFromParts } from "@/lib/agent/a2a-stream-contract"
import {
  isActiveStatus,
  isApprovalRequiredStatus,
  isTerminalStatus,
  lifecycleRevisionFromMetadata,
  normalizeStatus,
  taskStateProjection,
  taskStatusFromA2A,
  threadIdFromMetadata,
} from "@/lib/agent/task-presentation"
import type {
  AgentA2ARoute,
  AgentA2AStreamEnvelope,
  AgentA2ATask,
  AgentContextRecord,
  AgentContextTaskRecord,
  AgentMessage,
  AgentMessageFailure,
  AgentMessageRequest,
  AgentSession,
  AgentStoredMessage,
  AgentTask,
  AgentTaskEvent,
} from "@/lib/agent/types"
import { getRequestErrorMessage, RequestError } from "@/lib/request"

export function mergeEvents(
  previous: AgentTaskEvent[],
  incoming: AgentTaskEvent[]
) {
  const byId = new Map(previous.map((event) => [event.event_id, event]))
  for (const event of incoming) {
    byId.set(event.event_id, event)
  }

  return Array.from(byId.values()).sort(
    (left, right) => left.event_id - right.event_id
  )
}

export function preferredEvent(events: AgentTaskEvent[]) {
  const finalArtifact = [...events]
    .reverse()
    .find((event) => event.event_type.includes("artifact"))
  if (finalArtifact) return finalArtifact

  const terminal = [...events]
    .reverse()
    .find((event) => isTerminalStatus(event.status))
  if (terminal) return terminal

  const approvalRequired = [...events]
    .reverse()
    .find((event) => isApprovalRequiredStatus(event.status))
  return approvalRequired ?? events[events.length - 1]
}

export function preferredEventId(events: AgentTaskEvent[]) {
  const event = preferredEvent(events)
  return event ? String(event.event_id) : ""
}

export function mergeConversationMessages(
  previous: AgentMessage[],
  incoming: AgentMessage[]
) {
  const byId = new Map(previous.map((message) => [message.id, message]))
  for (const message of incoming) {
    // Task SSE uses a client-only pending assistant message until the durable
    // final assistant Message is available. Once that Message arrives, it is
    // the canonical conversation record; retaining the pending entry would
    // render the same answer twice.
    if (
      message.role === "assistant" &&
      message.taskId &&
      !message.id.startsWith("pending:")
    ) {
      for (const [messageId, existing] of byId) {
        if (
          messageId.startsWith("pending:") &&
          existing.role === "assistant" &&
          existing.taskId === message.taskId
        ) {
          byId.delete(messageId)
        }
      }
    }
    byId.set(message.id, {
      ...byId.get(message.id),
      ...message,
    })
  }

  return sortConversationMessages(Array.from(byId.values()))
}

function sortConversationMessages(messages: AgentMessage[]) {
  const latestUserTimestampByTask = new Map<string, number>()
  for (const message of messages) {
    if (message.role !== "user" || !message.taskId) continue
    const timestamp = conversationMessageTimestamp(message)
    latestUserTimestampByTask.set(
      message.taskId,
      Math.max(latestUserTimestampByTask.get(message.taskId) ?? 0, timestamp)
    )
  }

  return messages
    .map((message, index) => ({ message, index }))
    .sort((left, right) => {
      const leftTimestamp = conversationSortTimestamp(
        left.message,
        latestUserTimestampByTask
      )
      const rightTimestamp = conversationSortTimestamp(
        right.message,
        latestUserTimestampByTask
      )
      if (leftTimestamp !== rightTimestamp) {
        return leftTimestamp - rightTimestamp
      }

      if (
        left.message.taskId &&
        left.message.taskId === right.message.taskId &&
        left.message.role !== right.message.role
      ) {
        if (left.message.role === "user") return -1
        if (right.message.role === "user") return 1
      }

      return left.index - right.index
    })
    .map(({ message }) => message)
}

function conversationSortTimestamp(
  message: AgentMessage,
  latestUserTimestampByTask: Map<string, number>
) {
  const timestamp = conversationMessageTimestamp(message)
  if (!isTransientTaskAssistantMessage(message) || !message.taskId) {
    return timestamp
  }
  return Math.max(
    timestamp,
    latestUserTimestampByTask.get(message.taskId) ?? timestamp
  )
}

function conversationMessageTimestamp(message: AgentMessage) {
  const timestamp = Date.parse(message.createdAt)
  return Number.isFinite(timestamp) ? timestamp : 0
}

function isTransientTaskAssistantMessage(message: AgentMessage) {
  return (
    message.role === "assistant" &&
    Boolean(message.taskId) &&
    (message.loading === true || !message.content)
  )
}

export function contextToSession(context: AgentContextRecord): AgentSession {
  return {
    session_id: context.context_id,
    context_id: context.context_id,
    title: context.title || "Vermay",
    status: "active",
    metadata: context.metadata,
    created_at: context.created_at,
    updated_at: context.updated_at,
  }
}

export function storedMessagesToConversation(
  messages: AgentStoredMessage[]
): AgentMessage[] {
  return messages
    .slice()
    .sort((left, right) => left.created_at.localeCompare(right.created_at))
    .flatMap((message) => {
      const conversationMessage: AgentMessage = {
        id: message.message_id,
        role:
          message.role === "agent"
            ? "assistant"
            : message.role === "system"
              ? "system"
              : "user",
        content: textFromParts(message.parts),
        createdAt: message.created_at,
        taskId: message.task_id ?? null,
        messageKind:
          message.metadata.messageKind === "task_input_request"
            ? "task_input_request"
            : undefined,
        inputRequestRevision: inputRequestRevisionFromMetadata(message.metadata),
        request:
          message.role === "user"
            ? messageRequestFromMetadata(message.metadata)
            : undefined,
      }
      return message.failure
        ? [
            conversationMessage,
            buildDirectMessageFailure(
              message.message_id,
              message.failure,
              message.created_at
            ),
          ]
        : [conversationMessage]
    })
}

export function approvalTasksToConversation(
  tasks: AgentContextTaskRecord[],
  messages: AgentStoredMessage[]
): AgentMessage[] {
  const messageIds = new Set(messages.map((message) => message.message_id))
  return tasks
    .filter(
      (task) =>
        isApprovalRequiredStatus(normalizeStatus(task.status)) &&
        !task.output_message_id &&
        messageIds.has(task.input_message_id) &&
        !hasDurableInputRequestMessage(task, messages)
    )
    .map((task) =>
      buildAssistantConversationMessage(
        `approval:${task.task_id}`,
        "",
        task.updated_at,
        true,
        task.task_id
      )
    )
}

function hasDurableInputRequestMessage(
  task: AgentContextTaskRecord,
  messages: AgentStoredMessage[]
) {
  const taskRevision = task.lifecycle_revision
  return messages.some((message) => {
    if (
      message.role !== "agent" ||
      message.task_id !== task.task_id ||
      message.metadata.messageKind !== "task_input_request"
    ) {
      return false
    }

    const messageRevision = inputRequestRevisionFromMetadata(message.metadata)
    return (
      taskRevision == null ||
      messageRevision == null ||
      messageRevision === taskRevision
    )
  })
}

function inputRequestRevisionFromMetadata(
  metadata: Record<string, unknown>
) {
  const value = metadata.inputRequestRevision
  return typeof value === "number" && Number.isInteger(value) && value >= 1
    ? value
    : null
}

export function failedTasksToConversation(
  tasks: AgentContextTaskRecord[],
  messages: AgentStoredMessage[]
): AgentMessage[] {
  const messageIds = new Set(messages.map((message) => message.message_id))
  return tasks
    .filter(
      (task) =>
        normalizeStatus(task.status) === "failed" &&
        !task.output_message_id &&
        messageIds.has(task.input_message_id)
    )
    .map((task) =>
      buildAssistantConversationMessage(
        `task-failure:${task.task_id}`,
        "",
        task.updated_at,
        false,
        task.task_id
      )
    )
}

export function pruneHydratedTransientMessages(
  previous: AgentMessage[],
  tasks: AgentContextTaskRecord[]
) {
  const nonActiveTaskIds = new Set(
    tasks
      .filter((task) => !isActiveStatus(normalizeStatus(task.status)))
      .map((task) => task.task_id)
  )
  return previous.filter((message) => {
    if (message.id.startsWith("approval:")) return false
    if (
      message.taskId &&
      nonActiveTaskIds.has(message.taskId) &&
      (message.loading || (message.role === "assistant" && !message.content))
    ) {
      return false
    }
    return true
  })
}

export function messagesToDisplayTask(
  contextId: string,
  messages: AgentStoredMessage[]
): AgentTask | null {
  if (!messages.length) return null
  const latestUser = [...messages]
    .reverse()
    .find((message) => message.role === "user")
  const latestAgent = [...messages]
    .reverse()
    .find((message) => message.role === "agent")
  if (!latestUser) return null

  return {
    task_id: `context:${contextId}:messages`,
    session_id: contextId,
    thread_id: "",
    status: latestAgent ? "completed" : "running",
    input: textFromParts(latestUser.parts),
    attempt: 1,
    final_answer: latestAgent ? textFromParts(latestAgent.parts) : null,
    metadata: {
      displayKind: "message",
      displayTitle: "Direct message",
    },
    created_at: latestUser.created_at,
    updated_at: latestAgent?.created_at || latestUser.created_at,
  }
}

export function storedTaskToAgentTask(
  task: AgentContextTaskRecord,
  messages: AgentStoredMessage[],
  snapshot?: AgentA2ATask | null
): AgentTask {
  const inputMessage = messages.find(
    (message) => message.message_id === task.input_message_id
  )
  const outputMessage = task.output_message_id
    ? messages.find((message) => message.message_id === task.output_message_id)
    : undefined
  const status = snapshot
    ? taskStatusFromA2A(snapshot.status.state, snapshot.metadata)
    : normalizeStatus(task.status)
  const stateProjection = taskStateProjection(
    snapshot?.status.state,
    snapshot?.metadata,
    task.status
  )
  const updatedAt = snapshot?.status.timestamp || task.updated_at

  return {
    task_id: task.task_id,
    session_id: task.context_id,
    thread_id: task.runtime_thread_id,
    lifecycle_revision:
      lifecycleRevisionFromMetadata(snapshot?.metadata) ??
      task.lifecycle_revision ??
      null,
    a2a_state: stateProjection.a2a_state,
    local_process_status: stateProjection.local_process_status,
    retry_of_task_id: task.retry_of_task_id,
    status,
    input: inputMessage ? textFromParts(inputMessage.parts) : "Agent task",
    attempt: task.attempt,
    final_answer: outputMessage ? textFromParts(outputMessage.parts) : null,
    error: task.error_code
      ? {
          code: task.error_code,
          message: task.error_message || task.error_code,
          retryable: task.error_retryable === true,
        }
      : null,
    model: task.model,
    max_loops: task.max_loops,
    mcp: task.mcp,
    metadata: snapshot?.metadata ?? {},
    created_at: task.created_at,
    updated_at: updatedAt,
  }
}

export function a2aMessageToDisplayTask(
  contextId: string,
  input: string,
  messageId: string,
  parts: Array<{ text?: string }>
): AgentTask {
  const now = new Date().toISOString()
  return {
    task_id: `message:${messageId}`,
    session_id: contextId,
    thread_id: "",
    status: "completed",
    input,
    attempt: 1,
    final_answer: textFromParts(parts),
    metadata: {
      displayKind: "message",
      displayTitle: "Direct message",
    },
    created_at: now,
    updated_at: now,
  }
}

export function buildUserConversationMessage(
  messageId: string,
  prompt: string,
  createdAt: string,
  taskId?: string | null,
  request?: AgentMessageRequest
): AgentMessage {
  return {
    id: messageId,
    role: "user",
    content: prompt,
    createdAt,
    taskId,
    request,
  }
}

function messageRequestFromMetadata(
  metadata: Record<string, unknown>
): AgentMessageRequest {
  const executionMode = metadata.executionMode
  const route = metadata.route
  const targetAgentId = metadata.targetAgentId

  return {
    executionMode:
      executionMode === "message" ||
      executionMode === "task" ||
      executionMode === "auto"
        ? executionMode
        : "auto",
    ...(route === "local_message" ||
    route === "local_task" ||
    route === "remote_agent"
      ? { route: route as AgentA2ARoute }
      : {}),
    ...(typeof targetAgentId === "string" && targetAgentId
      ? { targetAgentId }
      : {}),
  }
}

export function buildAssistantConversationMessage(
  messageId: string,
  content: string,
  createdAt: string,
  loading = false,
  taskId?: string | null
): AgentMessage {
  return {
    id: messageId,
    role: "assistant",
    content,
    createdAt,
    loading,
    taskId,
  }
}

export function inputMessageIdFromFailureMessage(message: AgentMessage) {
  return message.id.startsWith("failure:")
    ? message.id.slice("failure:".length)
    : ""
}

export function buildDirectMessageFailure(
  inputMessageId: string,
  failure: AgentMessageFailure,
  createdAt: string
): AgentMessage {
  return {
    id: `failure:${inputMessageId}`,
    role: "assistant",
    content: "",
    createdAt,
    failure,
  }
}

export function failureFromRequestError(
  error: unknown,
  fallbackMessage: string
): AgentMessageFailure {
  if (error instanceof RequestError) {
    return {
      code: error.code,
      message: error.message || fallbackMessage,
      retryable: error.retryable,
    }
  }
  return {
    code: "a2a_stream_error",
    message: getRequestErrorMessage(error, fallbackMessage),
    retryable: true,
  }
}

export function a2aEnvelopeToTaskEvent(
  envelope: AgentA2AStreamEnvelope
): AgentTaskEvent | null {
  const result = envelope.result
  if (!result || typeof result !== "object") return null
  if (result.kind !== "status-update" && result.kind !== "artifact-update") {
    return null
  }

  const metadata = result.metadata ?? {}
  const localEventId = metadata.localEventId
  if (typeof localEventId !== "number") return null
  const localEventCreatedAt = metadata.localEventCreatedAt
  const runtimeThreadId = threadIdFromMetadata(metadata)
  const a2aState =
    result.kind === "status-update" ? result.status.state : undefined
  const stateProjection = taskStateProjection(a2aState, metadata)

  return {
    event_id: localEventId,
    task_id: result.taskId,
    session_id: result.contextId,
    lifecycle_revision: lifecycleRevisionFromMetadata(metadata),
    context_id: result.contextId,
    thread_id: runtimeThreadId,
    a2a_state: stateProjection.a2a_state,
    local_process_status: stateProjection.local_process_status,
    event_type:
      typeof metadata.localEventType === "string"
        ? metadata.localEventType
        : result.kind,
    status:
      result.kind === "status-update"
        ? taskStatusFromA2A(result.status.state, metadata)
        : null,
    payload: result as unknown as Record<string, unknown>,
    created_at:
      result.kind === "status-update" && result.status.timestamp
        ? result.status.timestamp
        : typeof localEventCreatedAt === "string"
          ? localEventCreatedAt
          : new Date().toISOString(),
  }
}

export function eventWithTaskThreadId(
  event: AgentTaskEvent,
  task?: AgentTask
): AgentTaskEvent {
  if (event.thread_id || !task?.thread_id) return event
  return {
    ...event,
    thread_id: task.thread_id,
  }
}

export function textFromA2AArtifact(envelope: AgentA2AStreamEnvelope) {
  const result = envelope.result
  if (
    !result ||
    typeof result !== "object" ||
    result.kind !== "artifact-update"
  ) {
    return ""
  }
  return textFromParts(result.artifact.parts)
}
