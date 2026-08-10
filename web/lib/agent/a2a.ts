import type { AgentA2AMessagePayload } from "@/lib/agent/types"

function buildA2AMessageEnvelope(
  payload: AgentA2AMessagePayload,
  method: "SendMessage" | "SendStreamingMessage"
) {
  const requestId = `req-${crypto.randomUUID()}`
  const messageId = payload.messageId || `msg-${crypto.randomUUID()}`

  return {
    jsonrpc: "2.0",
    id: requestId,
    method,
    params: {
      message: {
        kind: "message",
        role: "user",
        messageId,
        ...(payload.contextId ? { contextId: payload.contextId } : {}),
        ...(payload.taskId ? { taskId: payload.taskId } : {}),
        parts: [{ kind: "text", text: payload.text }],
        ...(payload.metadata ? { metadata: payload.metadata } : {}),
      },
      metadata: {
        executionMode: payload.executionMode || "auto",
        ...(payload.route ? { route: payload.route } : {}),
        ...(payload.targetAgentId
          ? { targetAgentId: payload.targetAgentId }
          : {}),
      },
    },
  }
}

export function buildA2ARpcMessageSendEnvelope(
  payload: AgentA2AMessagePayload
) {
  return buildA2AMessageEnvelope(payload, "SendMessage")
}

export function buildA2ARpcMessageStreamEnvelope(
  payload: AgentA2AMessagePayload
) {
  return buildA2AMessageEnvelope(payload, "SendStreamingMessage")
}

export function buildA2ARpcTaskGetEnvelope(taskId: string) {
  return {
    jsonrpc: "2.0",
    id: `get-task-${crypto.randomUUID()}`,
    method: "GetTask",
    params: {
      id: taskId,
    },
  }
}

export function buildA2ARpcTaskCancelEnvelope(taskId: string, reason?: string) {
  return {
    jsonrpc: "2.0",
    id: `cancel-task-${crypto.randomUUID()}`,
    method: "CancelTask",
    params: {
      id: taskId,
      ...(reason ? { reason } : {}),
    },
  }
}

export function buildA2ARpcTaskResumeEnvelope(
  taskId: string,
  approved: boolean,
  reason?: string
) {
  return {
    jsonrpc: "2.0",
    id: `resume-task-${crypto.randomUUID()}`,
    method: "tasks/resume",
    params: {
      id: taskId,
      approved,
      ...(reason ? { reason } : {}),
    },
  }
}

export function buildA2ARpcTaskSubscribeEnvelope(
  taskId: string,
  afterEventId = 0
) {
  return {
    jsonrpc: "2.0",
    id: `subscribe-task-${crypto.randomUUID()}`,
    method: "SubscribeToTask",
    params: {
      id: taskId,
      afterEventId,
    },
  }
}
