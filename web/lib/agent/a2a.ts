import type { AgentA2AMessagePayload } from "@/lib/agent/types"

const A2A_METHOD = {
  messageSend: "message/send",
  messageStream: "message/stream",
  taskGet: "tasks/get",
  taskCancel: "tasks/cancel",
  taskResubscribe: "tasks/resubscribe",
  taskResume: "tasks/resume",
} as const

function buildA2AMessageEnvelope(
  payload: AgentA2AMessagePayload,
  method: typeof A2A_METHOD.messageSend | typeof A2A_METHOD.messageStream
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
  return buildA2AMessageEnvelope(payload, A2A_METHOD.messageSend)
}

export function buildA2ARpcMessageStreamEnvelope(
  payload: AgentA2AMessagePayload
) {
  return buildA2AMessageEnvelope(payload, A2A_METHOD.messageStream)
}

export function buildA2ARpcTaskGetEnvelope(taskId: string) {
  return {
    jsonrpc: "2.0",
    id: `get-task-${crypto.randomUUID()}`,
    method: A2A_METHOD.taskGet,
    params: {
      id: taskId,
    },
  }
}

export function buildA2ARpcTaskCancelEnvelope(taskId: string, reason?: string) {
  return {
    jsonrpc: "2.0",
    id: `cancel-task-${crypto.randomUUID()}`,
    method: A2A_METHOD.taskCancel,
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
    method: A2A_METHOD.taskResume,
    params: {
      id: taskId,
      approved,
      ...(reason ? { reason } : {}),
    },
  }
}

export function buildA2ARpcTaskResubscribeEnvelope(
  taskId: string,
  afterEventId = 0
) {
  return {
    jsonrpc: "2.0",
    id: `subscribe-task-${crypto.randomUUID()}`,
    method: A2A_METHOD.taskResubscribe,
    params: {
      id: taskId,
      afterEventId,
    },
  }
}
