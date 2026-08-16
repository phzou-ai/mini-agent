import type {
  AgentA2ATask,
  AgentTask,
  AgentTaskEvent,
  AgentTaskStatus,
} from "@/lib/agent/types"

const TERMINAL_STATUSES = new Set<AgentTaskStatus>([
  "completed",
  "stopped",
  "failed",
  "canceled",
  "cancelled",
])

const ACTIVE_STATUSES = new Set<AgentTaskStatus>([
  "active",
  "created",
  "queued",
  "running",
  "cancel_request",
  "cancel_requested",
])

export type TaskInputRequest = {
  kind: string
  prompt: string
  choices: string[]
}

export function formatTime(value: string) {
  return new Intl.DateTimeFormat("en-US", {
    hour: "2-digit",
    minute: "2-digit",
    timeZone: "Asia/Shanghai",
  }).format(new Date(value))
}

export function formatDateTime(value: string) {
  return new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    timeZone: "Asia/Shanghai",
  }).format(new Date(value))
}

export function isTerminalStatus(status?: AgentTaskStatus | null) {
  return Boolean(status && TERMINAL_STATUSES.has(status))
}

export function isActiveStatus(status?: AgentTaskStatus | null) {
  return Boolean(status && ACTIVE_STATUSES.has(status))
}

export function isCancellationRequestedStatus(status?: AgentTaskStatus | null) {
  return status === "cancel_request" || status === "cancel_requested"
}

export function taskActivityLabel(status: AgentTaskStatus) {
  return isCancellationRequestedStatus(status)
    ? "cancellation requested"
    : status
}

export function isApprovalRequiredStatus(status?: AgentTaskStatus | null) {
  return status === "interrupted"
}

export function taskInputRequest(task?: AgentTask): TaskInputRequest | null {
  const value = task?.metadata?.inputRequest
  if (!value || typeof value !== "object" || Array.isArray(value)) return null
  const request = value as Record<string, unknown>
  const kind = typeof request.kind === "string" ? request.kind : ""
  const promptValue = request.prompt ?? request.message
  const prompt =
    typeof promptValue === "string" && promptValue.trim()
      ? promptValue.trim()
      : "Please provide the information required to continue."
  const directChoices = Array.isArray(request.choices) ? request.choices : []
  const inputSchema =
    request.inputSchema &&
    typeof request.inputSchema === "object" &&
    !Array.isArray(request.inputSchema)
      ? (request.inputSchema as Record<string, unknown>)
      : undefined
  const schemaChoices = Array.isArray(inputSchema?.enum)
    ? inputSchema.enum
    : []
  const choices = (directChoices.length ? directChoices : schemaChoices).filter(
    (choice): choice is string =>
      typeof choice === "string" && Boolean(choice.trim())
  )
  return { kind, prompt, choices }
}

export function isGeneralInputRequiredTask(task?: AgentTask) {
  return (
    isApprovalRequiredStatus(task?.status) &&
    taskInputRequest(task)?.kind === "user_input_required"
  )
}

export function normalizeStatus(status?: string | null): AgentTaskStatus {
  switch (status) {
    case "submitted":
      return "queued"
    case "working":
      return "running"
    case "input-required":
    case "input_required":
      return "interrupted"
    case "auth-required":
    case "auth_required":
      return "interrupted"
    case "completed":
    case "canceled":
    case "cancelled":
    case "failed":
    case "stopped":
    case "active":
    case "created":
    case "queued":
    case "running":
    case "interrupted":
    case "cancel_request":
    case "cancel_requested":
      return status
    default:
      return "unknown"
  }
}

export function getTaskTitle(task: AgentTask) {
  const displayTitle = task.metadata?.displayTitle
  if (typeof displayTitle === "string" && displayTitle.trim()) {
    return displayTitle.trim()
  }
  return task.input.replace(/\s+/g, " ").trim().slice(0, 36) || "Agent task"
}

export function isMessageDisplayTask(task: AgentTask) {
  return task.metadata?.displayKind === "message"
}

export function eventKey(event: AgentTaskEvent) {
  return String(event.event_id)
}

export function metadataString(
  metadata: Record<string, unknown> | undefined,
  key: string
) {
  const value = metadata?.[key]
  return typeof value === "string" ? value : ""
}

export function lifecycleRevisionFromMetadata(
  metadata?: Record<string, unknown> | null
) {
  const value = metadata?.lifecycleRevision
  return typeof value === "number" && Number.isInteger(value) && value >= 1
    ? value
    : null
}

export function taskLifecycleRevision(task?: AgentTask | null) {
  const value = task?.lifecycle_revision
  if (typeof value === "number" && Number.isInteger(value) && value >= 1) {
    return value
  }
  return lifecycleRevisionFromMetadata(task?.metadata)
}

export function a2aStateFromLocalProcessStatus(status?: string | null) {
  switch (status) {
    case "created":
    case "queued":
      return "submitted"
    case "running":
    case "cancel_request":
    case "cancel_requested":
      return "working"
    case "input_required":
      return "input-required"
    case "auth_required":
      return "auth-required"
    case "completed":
    case "canceled":
    case "failed":
      return status
    default:
      return ""
  }
}

export function localProcessStatusFromA2AState(status?: string | null) {
  switch (status) {
    case "submitted":
      return "queued"
    case "working":
      return "running"
    case "input-required":
      return "input_required"
    case "auth-required":
      return "auth_required"
    case "completed":
    case "canceled":
    case "failed":
      return status
    default:
      return ""
  }
}

export function taskStateProjection(
  a2aState?: string | null,
  metadata?: Record<string, unknown> | null,
  fallbackLocalProcessStatus?: string | null
) {
  const localProcessStatus =
    metadataString(metadata ?? undefined, "localStatus") ||
    fallbackLocalProcessStatus ||
    localProcessStatusFromA2AState(a2aState)
  const projectedA2AState =
    a2aState || a2aStateFromLocalProcessStatus(localProcessStatus)

  return {
    a2a_state: projectedA2AState || null,
    local_process_status: localProcessStatus || null,
  }
}

export function taskStatusFromA2A(
  status?: string | null,
  metadata?: Record<string, unknown> | null
): AgentTaskStatus {
  return normalizeStatus(
    metadataString(metadata ?? undefined, "localStatus") || status
  )
}

export function taskErrorFromA2AMetadata(
  metadata?: Record<string, unknown> | null
): NonNullable<AgentTask["error"]> | null {
  const code = metadataString(metadata ?? undefined, "localErrorCode")
  const message = metadataString(metadata ?? undefined, "localErrorMessage")
  if (!code && !message) return null

  return {
    code: code || "task_failed",
    message: message || "Agent execution failed.",
    retryable: metadata?.localErrorRetryable === true,
  }
}

export function taskErrorFromA2AEvent(event: AgentTaskEvent) {
  const metadata = event.payload.metadata
  if (!metadata || typeof metadata !== "object" || Array.isArray(metadata)) {
    return null
  }
  return taskErrorFromA2AMetadata(metadata as Record<string, unknown>)
}

export function taskFailureForDisplay(
  task?: AgentTask
): NonNullable<AgentTask["error"]> | null {
  if (!task || isMessageDisplayTask(task)) {
    return null
  }

  if (task.stream_error) return task.stream_error
  if (task.status !== "failed") return null

  return (
    task.error ?? {
      code: "task_failed",
      message: "The task failed before it produced a final answer.",
      retryable: false,
    }
  )
}

export function threadIdFromMetadata(
  metadata?: Record<string, unknown> | null
) {
  return (
    metadataString(metadata ?? undefined, "runtimeThreadId") ||
    metadataString(metadata ?? undefined, "localThreadId") ||
    metadataString(metadata ?? undefined, "threadId")
  )
}

function timestampMillis(value?: string | null) {
  if (!value) return null
  const parsed = Date.parse(value)
  return Number.isFinite(parsed) ? parsed : null
}

function mergeAcceptedTaskProjection(
  current: AgentTask,
  incoming: AgentTask
): AgentTask {
  const metadata = {
    ...(current.metadata ?? {}),
    ...(incoming.metadata ?? {}),
  }
  if (incoming.status !== "interrupted") {
    delete metadata.inputRequest
  }

  return {
    ...current,
    ...incoming,
    thread_id: incoming.thread_id || current.thread_id,
    final_answer: incoming.final_answer ?? current.final_answer,
    error: incoming.error === undefined ? current.error : incoming.error,
    stream_error:
      incoming.stream_error === undefined
        ? isTerminalStatus(incoming.status)
          ? null
          : current.stream_error
        : incoming.stream_error,
    metadata,
  }
}

function mergeEqualTaskProjection(
  current: AgentTask,
  incoming: AgentTask
): AgentTask {
  const incomingMetadata = { ...(incoming.metadata ?? {}) }
  const sameLifecycle =
    incoming.status === current.status &&
    (!incoming.a2a_state || incoming.a2a_state === current.a2a_state) &&
    (!incoming.local_process_status ||
      incoming.local_process_status === current.local_process_status)

  if (!sameLifecycle) {
    for (const key of [
      "localStatus",
      "inputRequest",
      "inputRequestKind",
      "outputMessageId",
      "localErrorCode",
      "localErrorMessage",
      "localErrorRetryable",
      "remoteStatus",
    ]) {
      delete incomingMetadata[key]
    }
  }

  const metadata = {
    ...incomingMetadata,
    ...(current.metadata ?? {}),
  }
  if (current.status !== "interrupted") {
    delete metadata.inputRequest
    delete metadata.inputRequestKind
  }
  if (current.status !== "failed") {
    delete metadata.localErrorCode
    delete metadata.localErrorMessage
    delete metadata.localErrorRetryable
  }

  return {
    ...current,
    thread_id: current.thread_id || incoming.thread_id,
    final_answer: current.final_answer ?? incoming.final_answer,
    error: sameLifecycle ? current.error ?? incoming.error : current.error,
    stream_error: sameLifecycle
      ? current.stream_error ?? incoming.stream_error
      : current.stream_error,
    metadata,
  }
}

/** Merge one public Task projection without allowing lifecycle regression.
 *
 * Revision is authoritative when both sources provide it. Timestamp ordering
 * remains only as a bounded compatibility path for transient or older data.
 * Equal revisions preserve lifecycle fields while accepting additive evidence
 * such as an artifact-derived final answer or metadata.
 */
export function mergeTaskProjection(
  current: AgentTask,
  incoming: AgentTask
): AgentTask {
  const currentRevision = taskLifecycleRevision(current)
  const incomingRevision = taskLifecycleRevision(incoming)

  if (currentRevision !== null && incomingRevision !== null) {
    if (incomingRevision < currentRevision) return current
    if (incomingRevision === currentRevision) {
      return mergeEqualTaskProjection(current, incoming)
    }
    return mergeAcceptedTaskProjection(current, incoming)
  }

  const currentTimestamp = timestampMillis(current.updated_at)
  const incomingTimestamp = timestampMillis(incoming.updated_at)
  if (
    currentTimestamp !== null &&
    incomingTimestamp !== null &&
    incomingTimestamp < currentTimestamp
  ) {
    return current
  }
  return mergeAcceptedTaskProjection(current, incoming)
}

export function mergeTaskWithA2ASnapshot(
  current: AgentTask,
  snapshot: AgentA2ATask
): AgentTask {
  const snapshotUpdatedAt = snapshot.status.timestamp
  const status = taskStatusFromA2A(snapshot.status.state, snapshot.metadata)
  const stateProjection = taskStateProjection(
    snapshot.status.state,
    snapshot.metadata,
    current.local_process_status
  )
  const taskError = taskErrorFromA2AMetadata(snapshot.metadata)
  const runtimeThreadId = threadIdFromMetadata(snapshot.metadata)
  const candidate: AgentTask = {
    ...current,
    lifecycle_revision: lifecycleRevisionFromMetadata(snapshot.metadata),
    thread_id: runtimeThreadId || current.thread_id,
    a2a_state: stateProjection.a2a_state,
    local_process_status: stateProjection.local_process_status,
    status,
    updated_at: snapshotUpdatedAt || current.updated_at,
    error: status === "failed" ? taskError ?? current.error : null,
    stream_error: isTerminalStatus(status) ? null : current.stream_error,
    metadata: snapshot.metadata ?? {},
  }
  const merged = mergeTaskProjection(current, candidate)
  return merged
}
