"use client"

import React, { useCallback, useEffect, useMemo, useRef, useState } from "react"
import {
  Activity,
  AlertCircle,
  Bot,
  Check,
  ChevronDown,
  Clock3,
  Copy,
  Database,
  History,
  Menu,
  MessageSquarePlus,
  Network,
  Pause,
  Pencil,
  Play,
  RefreshCcw,
  Sparkles,
  TerminalSquare,
  Trash2,
  X,
} from "lucide-react"
import ReactMarkdown from "react-markdown"
import remarkGfm from "remark-gfm"

import {
  errorFromA2AStreamEnvelope,
  isA2AFinalMessage,
  isA2APartialMessage,
  textFromA2AParts as textFromParts,
} from "@/lib/agent/a2a-stream-contract"
import {
  cancelAgentA2ATask,
  deleteAgentContext,
  deleteAgentRegisteredAgent,
  getAgentA2AAgentCard,
  getAgentA2ATask,
  getAgentModelConfig,
  listAgentContextDelegations,
  listAgentContextMessages,
  listAgentContextRouteDecisions,
  listAgentContexts,
  listAgentContextTasks,
  listAgentRegisteredAgents,
  refreshAgentRegisteredAgent,
  resumeAgentA2ATask,
  submitAgentA2ATaskInput,
  updateAgentContext,
  upsertAgentRegisteredAgent,
} from "@/lib/agent/client"
import {
  openAgentA2AMessageStream,
  openAgentA2ATaskEventStream,
} from "@/lib/agent/stream"
import type {
  AgentA2AAgentCard,
  AgentA2AExecutionMode,
  AgentA2AStreamEnvelope,
  AgentA2ATask,
  AgentContextRecord,
  AgentContextTaskRecord,
  AgentDelegation,
  AgentMessage,
  AgentModelConfig,
  AgentRegisteredAgent,
  AgentRouteDecision,
  AgentSession,
  AgentStoredMessage,
  AgentTask,
  AgentTaskEvent,
  AgentTaskStatus,
} from "@/lib/agent/types"
import { getRequestErrorMessage } from "@/lib/request"
import { cn } from "@/lib/utils"
import { MainAgentCardPanel } from "@/app/(agent)/agent/_components/agent-card-panel"
import { RouteDiagnosticsPanel } from "@/app/(agent)/agent/_components/route-diagnostics-panel"

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

const AGENT_GRADIENT = "linear-gradient(173.79deg, #AD1A98 0%, #3768C7 100.12%)"
const COMPOSER_IDLE_BORDER = "#C6C3C8"
const COMPOSER_ACTIVE_BORDER = "#8F2BB8"
const COMPOSER_TEXT = "#1F0013"
const COMPOSER_MUTED_TEXT = "#54465C"

type AgentRegistryForm = {
  agentId: string
  name: string
  cardUrl: string
  keywords: string
}

const EVENT_LABELS: Record<
  string,
  { title: string; detail: string; icon: React.ElementType }
> = {
  task_created: {
    title: "Task created",
    detail: "Task record created",
    icon: MessageSquarePlus,
  },
  task_queued: {
    title: "Task queued",
    detail: "Task entered the execution queue",
    icon: Clock3,
  },
  task_started: {
    title: "Task started",
    detail: "Runtime started execution",
    icon: Play,
  },
  task_interrupted: {
    title: "Task interrupted",
    detail: "Waiting for user input or approval",
    icon: Pause,
  },
  task_resumed: {
    title: "Task resumed",
    detail: "Task resumed execution",
    icon: Play,
  },
  task_retry_requested: {
    title: "Retry requested",
    detail: "Retry requested",
    icon: RefreshCcw,
  },
  task_retried: {
    title: "Task retried",
    detail: "Retry task created",
    icon: RefreshCcw,
  },
  task_cancel_requested: {
    title: "Cancel requested",
    detail: "Cancel requested",
    icon: Pause,
  },
  task_cancelled: {
    title: "Task cancelled",
    detail: "Task cancelled",
    icon: Pause,
  },
  task_artifact_created: {
    title: "Artifact created",
    detail: "Artifact created",
    icon: Database,
  },
  task_artifact_updated: {
    title: "Artifact updated",
    detail: "Artifact updated",
    icon: Database,
  },
  task_completed: {
    title: "Task completed",
    detail: "Task completed",
    icon: Check,
  },
  task_stopped: {
    title: "Task stopped",
    detail: "Task reached a stop condition",
    icon: AlertCircle,
  },
  task_failed: {
    title: "Task failed",
    detail: "Task failed",
    icon: AlertCircle,
  },
}

function formatTime(value: string) {
  return new Intl.DateTimeFormat("en-US", {
    hour: "2-digit",
    minute: "2-digit",
    timeZone: "Asia/Shanghai",
  }).format(new Date(value))
}

function formatDateTime(value: string) {
  return new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    timeZone: "Asia/Shanghai",
  }).format(new Date(value))
}

function isTerminalStatus(status?: AgentTaskStatus | null) {
  return Boolean(status && TERMINAL_STATUSES.has(status))
}

function isActiveStatus(status?: AgentTaskStatus | null) {
  return Boolean(status && ACTIVE_STATUSES.has(status))
}

function isApprovalRequiredStatus(status?: AgentTaskStatus | null) {
  return status === "interrupted"
}

type TaskInputRequest = {
  kind: string
  prompt: string
  choices: string[]
}

function taskInputRequest(task?: AgentTask): TaskInputRequest | null {
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

function isGeneralInputRequiredTask(task?: AgentTask) {
  return (
    isApprovalRequiredStatus(task?.status) &&
    taskInputRequest(task)?.kind === "user_input_required"
  )
}

function normalizeStatus(status?: string | null): AgentTaskStatus {
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

function getTaskTitle(task: AgentTask) {
  const displayTitle = task.metadata?.displayTitle
  if (typeof displayTitle === "string" && displayTitle.trim()) {
    return displayTitle.trim()
  }
  return task.input.replace(/\s+/g, " ").trim().slice(0, 36) || "Agent task"
}

function isMessageDisplayTask(task: AgentTask) {
  return task.metadata?.displayKind === "message"
}

function eventKey(event: AgentTaskEvent) {
  return String(event.event_id)
}

function parseKeywords(value: string) {
  const seen = new Set<string>()
  return value
    .split(",")
    .map((item) => item.trim())
    .filter((item) => {
      if (!item) return false
      const key = item.toLowerCase()
      if (seen.has(key)) return false
      seen.add(key)
      return true
    })
}

function agentKeywords(agent: AgentRegisteredAgent) {
  const value = agent.metadata.keywords
  if (!Array.isArray(value)) return []
  return value.filter(
    (item): item is string => typeof item === "string" && Boolean(item.trim())
  )
}

function agentCardSkillTags(agent: AgentRegisteredAgent) {
  const skills = agent.card_json.skills
  if (!Array.isArray(skills)) return []
  const tags = new Set<string>()
  for (const skill of skills) {
    if (!skill || typeof skill !== "object" || Array.isArray(skill)) continue
    const rawTags = (skill as Record<string, unknown>).tags
    if (!Array.isArray(rawTags)) continue
    for (const tag of rawTags) {
      if (typeof tag === "string" && tag.trim()) {
        tags.add(tag.trim())
      }
    }
  }
  return Array.from(tags)
}

function agentCardSkillCount(agent: AgentRegisteredAgent) {
  const skills = agent.card_json.skills
  return Array.isArray(skills) ? skills.length : 0
}

function metadataString(
  metadata: Record<string, unknown> | undefined,
  key: string
) {
  const value = metadata?.[key]
  return typeof value === "string" ? value : ""
}

function threadIdFromMetadata(metadata?: Record<string, unknown> | null) {
  return (
    metadataString(metadata ?? undefined, "runtimeThreadId") ||
    metadataString(metadata ?? undefined, "localThreadId") ||
    metadataString(metadata ?? undefined, "threadId")
  )
}

function mergeEvents(previous: AgentTaskEvent[], incoming: AgentTaskEvent[]) {
  const byId = new Map(previous.map((event) => [event.event_id, event]))
  for (const event of incoming) {
    byId.set(event.event_id, event)
  }

  return Array.from(byId.values()).sort(
    (left, right) => left.event_id - right.event_id
  )
}

function preferredEvent(events: AgentTaskEvent[]) {
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

function preferredEventId(events: AgentTaskEvent[]) {
  const event = preferredEvent(events)
  return event ? eventKey(event) : ""
}

function mergeConversationMessages(
  previous: AgentMessage[],
  incoming: AgentMessage[]
) {
  const byId = new Map(previous.map((message) => [message.id, message]))
  for (const message of incoming) {
    byId.set(message.id, {
      ...byId.get(message.id),
      ...message,
    })
  }

  return Array.from(byId.values()).sort(
    (left, right) =>
      new Date(left.createdAt).getTime() - new Date(right.createdAt).getTime()
  )
}

function contextToSession(context: AgentContextRecord): AgentSession {
  return {
    session_id: context.context_id,
    context_id: context.context_id,
    title: context.title || "Vermay Agent",
    status: "active",
    metadata: context.metadata,
    created_at: context.created_at,
    updated_at: context.updated_at,
  }
}

function storedMessagesToConversation(
  messages: AgentStoredMessage[]
): AgentMessage[] {
  return messages
    .slice()
    .sort((left, right) => left.created_at.localeCompare(right.created_at))
    .map((message) => ({
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
    }))
}

function approvalTasksToConversation(
  tasks: AgentContextTaskRecord[],
  messages: AgentStoredMessage[]
): AgentMessage[] {
  const messageIds = new Set(messages.map((message) => message.message_id))
  return tasks
    .filter(
      (task) =>
        isApprovalRequiredStatus(normalizeStatus(task.status)) &&
        !task.output_message_id &&
        messageIds.has(task.input_message_id)
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

function pruneHydratedTransientMessages(
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
    )
      return false
    return true
  })
}

function messagesToDisplayTask(
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

function storedTaskToAgentTask(
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
    ? normalizeStatus(snapshot.status.state)
    : normalizeStatus(task.status)
  const updatedAt = snapshot?.status.timestamp || task.updated_at

  return {
    task_id: task.task_id,
    session_id: task.context_id,
    thread_id: task.runtime_thread_id,
    retry_of_task_id: task.retry_of_task_id,
    status,
    input: inputMessage ? textFromParts(inputMessage.parts) : "Agent task",
    attempt: task.attempt,
    final_answer: outputMessage ? textFromParts(outputMessage.parts) : null,
    error: task.error_code
      ? {
          code: task.error_code,
          message: task.error_message || task.error_code,
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

function a2aMessageToDisplayTask(
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

function buildUserConversationMessage(
  messageId: string,
  prompt: string,
  createdAt: string,
  taskId?: string | null
): AgentMessage {
  return {
    id: messageId,
    role: "user",
    content: prompt,
    createdAt,
    taskId,
  }
}

function buildAssistantConversationMessage(
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

function a2aEnvelopeToTaskEvent(
  envelope: AgentA2AStreamEnvelope
): AgentTaskEvent | null {
  const result = envelope.result
  if (!result || typeof result !== "object") return null
  if (result.kind !== "status-update" && result.kind !== "artifact-update")
    return null

  const metadata = result.metadata ?? {}
  const localEventId = metadata.localEventId
  if (typeof localEventId !== "number") return null
  const localEventCreatedAt = metadata.localEventCreatedAt
  const runtimeThreadId = threadIdFromMetadata(metadata)

  return {
    event_id: localEventId,
    task_id: result.taskId,
    session_id: result.contextId,
    context_id: result.contextId,
    thread_id: runtimeThreadId,
    event_type:
      typeof metadata.localEventType === "string"
        ? metadata.localEventType
        : result.kind,
    status:
      result.kind === "status-update"
        ? normalizeStatus(result.status.state)
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

function eventWithTaskThreadId(
  event: AgentTaskEvent,
  task?: AgentTask
): AgentTaskEvent {
  if (event.thread_id || !task?.thread_id) return event
  return {
    ...event,
    thread_id: task.thread_id,
  }
}

function textFromA2AArtifact(envelope: AgentA2AStreamEnvelope) {
  const result = envelope.result
  if (
    !result ||
    typeof result !== "object" ||
    result.kind !== "artifact-update"
  )
    return ""
  return textFromParts(result.artifact.parts)
}

export function AgentConsole() {
  const [sidebarExpanded, setSidebarExpanded] = useState(true)
  const [sessions, setSessions] = useState<AgentSession[]>([])
  const [registeredAgents, setRegisteredAgents] = useState<
    AgentRegisteredAgent[]
  >([])
  const [mainAgentCard, setMainAgentCard] = useState<AgentA2AAgentCard | null>(
    null
  )
  const [modelConfig, setModelConfig] = useState<AgentModelConfig | null>(null)
  const [routeDecisionsByContext, setRouteDecisionsByContext] = useState<
    Record<string, AgentRouteDecision[]>
  >({})
  const [delegationsByContext, setDelegationsByContext] = useState<
    Record<string, AgentDelegation[]>
  >({})
  const [messagesByContext, setMessagesByContext] = useState<
    Record<string, AgentMessage[]>
  >({})
  const [tasks, setTasks] = useState<Record<string, AgentTask>>({})
  const [eventsByTask, setEventsByTask] = useState<
    Record<string, AgentTaskEvent[]>
  >({})
  const [currentSessionId, setCurrentSessionId] = useState("")
  const [currentTaskId, setCurrentTaskId] = useState("")
  const [selectedMessageId, setSelectedMessageId] = useState("")
  const [selectedEventId, setSelectedEventId] = useState("")
  const [selectedRemoteAgentId, setSelectedRemoteAgentId] = useState("")
  const [input, setInput] = useState("")
  const [agentRegistryForm, setAgentRegistryForm] = useState<AgentRegistryForm>(
    {
      agentId: "",
      name: "",
      cardUrl: "",
      keywords: "",
    }
  )
  const [executionMode, setExecutionMode] =
    useState<AgentA2AExecutionMode>("auto")
  const [copiedMessageId, setCopiedMessageId] = useState("")
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [registryBusy, setRegistryBusy] = useState(false)
  const [refreshingAgentId, setRefreshingAgentId] = useState("")
  const [deletingSessionId, setDeletingSessionId] = useState("")
  const [editingSessionId, setEditingSessionId] = useState("")
  const [editingSessionTitle, setEditingSessionTitle] = useState("")
  const [updatingSessionId, setUpdatingSessionId] = useState("")
  const [resumingTaskId, setResumingTaskId] = useState("")
  const [submittingTaskInputId, setSubmittingTaskInputId] = useState("")
  const [error, setError] = useState("")
  const messageStreamAbortRef = useRef<AbortController | null>(null)
  const hydratingTaskEventsRef = useRef(new Set<string>())
  const currentTaskIdRef = useRef("")
  const eventsByTaskRef = useRef<Record<string, AgentTaskEvent[]>>({})
  const tasksRef = useRef<Record<string, AgentTask>>({})
  const userSessionSelectionRef = useRef(false)

  const taskList = useMemo(
    () =>
      Object.values(tasks).sort(
        (left, right) =>
          new Date(right.updated_at).getTime() -
          new Date(left.updated_at).getTime()
      ),
    [tasks]
  )
  const currentSession = sessions.find(
    (session) => session.session_id === currentSessionId
  )
  const currentTask = currentTaskId ? tasks[currentTaskId] : undefined
  const currentEvents = currentTaskId ? (eventsByTask[currentTaskId] ?? []) : []
  const currentRouteDecisions = currentSessionId
    ? (routeDecisionsByContext[currentSessionId] ?? [])
    : []
  const currentDelegations = currentSessionId
    ? (delegationsByContext[currentSessionId] ?? [])
    : []
  const enabledRegisteredAgents = useMemo(
    () => registeredAgents.filter((agent) => agent.enabled),
    [registeredAgents]
  )
  const selectedEvent =
    currentEvents.find((event) => eventKey(event) === selectedEventId) ??
    currentEvents[0]
  const isTaskActive = isActiveStatus(currentTask?.status)
  const isCurrentSessionTaskActive = taskList.some(
    (task) =>
      task.session_id === currentSessionId && isActiveStatus(task.status)
  )
  const conversationMessages = currentSessionId
    ? (messagesByContext[currentSessionId] ?? [])
    : []

  const appendConversationMessages = useCallback(
    (contextId: string, incoming: AgentMessage[]) => {
      setMessagesByContext((previous) => ({
        ...previous,
        [contextId]: mergeConversationMessages(
          previous[contextId] ?? [],
          incoming
        ),
      }))
    },
    []
  )

  const closeStream = useCallback(() => {
    messageStreamAbortRef.current?.abort()
    messageStreamAbortRef.current = null
  }, [])

  useEffect(() => {
    currentTaskIdRef.current = currentTaskId
  }, [currentTaskId])

  useEffect(() => {
    eventsByTaskRef.current = eventsByTask
  }, [eventsByTask])

  useEffect(() => {
    tasksRef.current = tasks
  }, [tasks])

  const applyA2ATaskSnapshot = useCallback((snapshot: AgentA2ATask) => {
    const status = normalizeStatus(snapshot.status.state)
    const updatedAt = snapshot.status.timestamp || new Date().toISOString()
    const runtimeThreadId = threadIdFromMetadata(snapshot.metadata)

    setTasks((previous) => {
      const task = previous[snapshot.id]
      if (!task) return previous
      return {
        ...previous,
        [snapshot.id]: {
          ...task,
          thread_id: runtimeThreadId || task.thread_id,
          status,
          updated_at: updatedAt,
          metadata: snapshot.metadata ?? task.metadata,
        },
      }
    })
    setSessions((previous) =>
      previous.map((session) =>
        session.session_id === snapshot.contextId
          ? {
              ...session,
              status,
              metadata: snapshot.metadata ?? session.metadata,
              updated_at: updatedAt,
            }
          : session
      )
    )
  }, [])

  const reconcileA2ATaskSnapshot = useCallback(
    async (taskId: string) => {
      let snapshot: AgentA2ATask
      try {
        snapshot = await getAgentA2ATask(taskId)
      } catch (taskError) {
        setError(
          getRequestErrorMessage(taskError, "Failed to refresh task snapshot")
        )
        return
      }

      applyA2ATaskSnapshot(snapshot)
    },
    [applyA2ATaskSnapshot]
  )

  const hydrateTaskEvents = useCallback((taskId: string) => {
    if (!taskId || hydratingTaskEventsRef.current.has(taskId)) return

    hydratingTaskEventsRef.current.add(taskId)
    const afterEventId = Math.max(
      0,
      ...(eventsByTaskRef.current[taskId] ?? []).map(
        (event) => event.event_id
      )
    )
    let finished = false
    let source: EventSource | null = null

    const finish = () => {
      if (finished) return
      finished = true
      source?.close()
      hydratingTaskEventsRef.current.delete(taskId)
    }

    const timeout = window.setTimeout(finish, 1800)

    source = openAgentA2ATaskEventStream(taskId, {
      after: afterEventId,
      onEvent: (envelope) => {
        const projectedEvent = a2aEnvelopeToTaskEvent(envelope)
        if (!projectedEvent) return
        const event = eventWithTaskThreadId(
          projectedEvent,
          tasksRef.current[projectedEvent.task_id]
        )

        const merged = mergeEvents(
          eventsByTaskRef.current[event.task_id] ?? [],
          [event]
        )
        eventsByTaskRef.current = {
          ...eventsByTaskRef.current,
          [event.task_id]: merged,
        }
        setEventsByTask((previous) => {
          return { ...previous, [event.task_id]: merged }
        })
        setSelectedEventId(preferredEventId(merged))

        if (event.status) {
          const status = event.status
          setTasks((previous) => {
            const task = previous[event.task_id]
            if (!task) return previous
            return {
              ...previous,
              [event.task_id]: {
                ...task,
                status,
                updated_at: event.created_at,
              },
            }
          })
          setSessions((previous) =>
            previous.map((session) =>
              session.session_id === event.session_id
                ? {
                    ...session,
                    status,
                    updated_at: event.created_at,
                  }
                : session
            )
          )
        }

        if (event.status === "interrupted" || isTerminalStatus(event.status)) {
          void reconcileA2ATaskSnapshot(event.task_id)
          window.clearTimeout(timeout)
          finish()
        }
      },
      onDone: () => {
        window.clearTimeout(timeout)
        finish()
      },
    })
  }, [reconcileA2ATaskSnapshot])

  const loadContextDiagnostics = useCallback(async (contextId: string) => {
    const [routeDecisions, delegations] = await Promise.all([
      listAgentContextRouteDecisions(contextId),
      listAgentContextDelegations(contextId),
    ])
    setRouteDecisionsByContext((previous) => ({
      ...previous,
      [contextId]: routeDecisions,
    }))
    setDelegationsByContext((previous) => ({
      ...previous,
      [contextId]: delegations,
    }))
  }, [])

  const loadContextMessages = useCallback(
    async (contextId: string) => {
      const [storedMessages, storedTasks] = await Promise.all([
        listAgentContextMessages(contextId),
        listAgentContextTasks(contextId),
        loadContextDiagnostics(contextId),
      ])

      setMessagesByContext((previous) => ({
        ...previous,
        [contextId]: mergeConversationMessages(
          pruneHydratedTransientMessages(
            previous[contextId] ?? [],
            storedTasks
          ),
          [
            ...storedMessagesToConversation(storedMessages),
            ...approvalTasksToConversation(storedTasks, storedMessages),
          ]
        ),
      }))

      const sortedStoredTasks = [...storedTasks].sort((left, right) =>
        right.updated_at.localeCompare(left.updated_at)
      )
      const latestStoredTask = sortedStoredTasks[0]
      if (latestStoredTask) {
        let snapshot: AgentA2ATask | null = null
        try {
          snapshot = await getAgentA2ATask(latestStoredTask.task_id)
        } catch (taskError) {
          setError(
            getRequestErrorMessage(taskError, "Failed to refresh task snapshot")
          )
        }
        const latestTask = storedTaskToAgentTask(
          latestStoredTask,
          storedMessages,
          snapshot
        )
        const loadedTasks = sortedStoredTasks.map((task) =>
          task.task_id === latestStoredTask.task_id
            ? latestTask
            : storedTaskToAgentTask(task, storedMessages)
        )
        setTasks((previous) => {
          const next = { ...previous }
          for (const task of loadedTasks) {
            next[task.task_id] = task
          }
          return next
        })
        setCurrentTaskId(latestTask.task_id)
        hydrateTaskEvents(latestTask.task_id)
        setSessions((previous) =>
          previous.map((session) =>
            session.session_id === contextId
              ? {
                  ...session,
                  status: latestTask.status,
                  updated_at: latestTask.updated_at,
                }
              : session
          )
        )
        return
      }

      const displayTask = messagesToDisplayTask(contextId, storedMessages)
      if (!displayTask) {
        setCurrentTaskId("")
        return
      }
      setTasks((previous) => ({
        ...previous,
        [displayTask.task_id]: displayTask,
      }))
      setCurrentTaskId(displayTask.task_id)
    },
    [hydrateTaskEvents, loadContextDiagnostics]
  )

  const reloadRegisteredAgents = useCallback(async () => {
    const agents = await listAgentRegisteredAgents()
    setRegisteredAgents(agents)
    setSelectedRemoteAgentId((current) =>
      current &&
      agents.some((agent) => agent.agent_id === current && agent.enabled)
        ? current
        : ""
    )
  }, [])

  useEffect(() => {
    let cancelled = false

    async function loadInitialData() {
      setLoading(true)
      setError("")

      try {
        const [contexts, agents, agentCard, loadedModelConfig] =
          await Promise.all([
            listAgentContexts(),
            listAgentRegisteredAgents(),
            getAgentA2AAgentCard(),
            getAgentModelConfig().catch(() => null),
          ])
        const loadedSessions = contexts.map(contextToSession)

        if (cancelled) return

        setSessions(loadedSessions)
        setRegisteredAgents(agents)
        setMainAgentCard(agentCard)
        setModelConfig(loadedModelConfig)
        if (userSessionSelectionRef.current) return
        const firstSessionId = loadedSessions[0]?.session_id ?? ""
        setCurrentSessionId(firstSessionId)
        if (firstSessionId) {
          await loadContextMessages(firstSessionId)
        }
      } catch (loadError) {
        if (!cancelled) {
          setError(
            getRequestErrorMessage(
              loadError,
              "Cannot connect to the Vermay Agent API. Confirm that vermay-agent serve is running."
            )
          )
        }
      } finally {
        if (!cancelled) {
          setLoading(false)
        }
      }
    }

    void loadInitialData()

    return () => {
      cancelled = true
      closeStream()
    }
  }, [closeStream, loadContextMessages])

  async function sendMessage() {
    const prompt = input.trim()
    if (!prompt || busy || isCurrentSessionTaskActive) return

    if (currentTask && isApprovalRequiredStatus(currentTask.status)) {
      setInput("")
      if (isGeneralInputRequiredTask(currentTask)) {
        await submitTaskInput(currentTask.task_id, prompt)
        return
      }

      const approval = prompt.toLowerCase()
      if (approval === "yes" || approval === "no") {
        await resumeTask(currentTask.task_id, approval === "yes")
        return
      }

      setError(
        "This task is waiting for approval. Use the approval controls or type yes or no."
      )
      return
    }

    const abortController = new AbortController()
    let streamedTaskId = ""
    let receivedStreamEvent = false
    let streamedMessageText = ""
    let diagnosticsRequestedForContext = ""
    const outgoingMessageId = `msg-${crypto.randomUUID()}`
    const outgoingCreatedAt = new Date().toISOString()
    const draftContextId = `draft:${crypto.randomUUID()}`
    const displayContextId = currentSessionId || draftContextId
    const pendingActivityId = `pending:${outgoingMessageId}`
    const pendingAssistantMessageId = `${pendingActivityId}:assistant`

    closeStream()
    messageStreamAbortRef.current = abortController
    setBusy(true)
    setError("")
    setInput("")
    setSelectedEventId("")
    const pendingTask: AgentTask = {
      task_id: pendingActivityId,
      session_id: displayContextId,
      thread_id: "",
      status: "running",
      input: prompt,
      attempt: 1,
      final_answer: null,
      metadata: {
        displayKind: executionMode === "message" ? "message" : "task",
        displayTitle: executionMode === "message" ? "Direct message" : prompt,
      },
      created_at: outgoingCreatedAt,
      updated_at: outgoingCreatedAt,
    }
    const pendingSession: AgentSession = {
      session_id: displayContextId,
      context_id: displayContextId,
      title: prompt,
      status: "running",
      metadata: {},
      created_at: outgoingCreatedAt,
      updated_at: outgoingCreatedAt,
    }
    setSessions((previous) => {
      const exists = previous.some(
        (session) => session.session_id === displayContextId
      )
      return exists
        ? previous.map((session) =>
            session.session_id === displayContextId
              ? { ...session, status: "running", updated_at: outgoingCreatedAt }
              : session
          )
        : [pendingSession, ...previous]
    })
    setTasks((previous) => ({ ...previous, [pendingActivityId]: pendingTask }))
    setCurrentSessionId(displayContextId)
    setCurrentTaskId(pendingActivityId)
    appendConversationMessages(displayContextId, [
      buildUserConversationMessage(
        outgoingMessageId,
        prompt,
        outgoingCreatedAt,
        pendingActivityId
      ),
      buildAssistantConversationMessage(
        pendingAssistantMessageId,
        "",
        outgoingCreatedAt,
        true,
        pendingActivityId
      ),
    ])

    const promoteDraftContext = (contextId: string) => {
      if (contextId === displayContextId) return
      setMessagesByContext((previous) => {
        const next = { ...previous }
        const draftMessages = next[displayContextId] ?? []
        next[contextId] = mergeConversationMessages(
          next[contextId] ?? [],
          draftMessages
        )
        delete next[displayContextId]
        return next
      })
    }

    const upsertResolvedSession = (session: AgentSession) => {
      setSessions((previous) => {
        const withoutDraft = previous.filter(
          (item) => item.session_id !== displayContextId
        )
        const exists = withoutDraft.some(
          (item) => item.session_id === session.session_id
        )
        return exists
          ? withoutDraft.map((item) =>
              item.session_id === session.session_id ? session : item
            )
          : [session, ...withoutDraft]
      })
    }

    try {
      await openAgentA2AMessageStream(
        {
          contextId: currentSessionId || undefined,
          messageId: outgoingMessageId,
          text: prompt,
          executionMode,
          ...(selectedRemoteAgentId
            ? { route: "remote_agent", targetAgentId: selectedRemoteAgentId }
            : {}),
        },
        {
          signal: abortController.signal,
          onEvent: (envelope) => {
            receivedStreamEvent = true
            if (envelope.error) {
              setError(errorFromA2AStreamEnvelope(envelope).message)
              return
            }

            const result = envelope.result
            if (!result || typeof result !== "object") return

            if (result.kind === "message") {
              const metadata = result.metadata ?? {}
              const messageText = textFromParts(result.parts)
              promoteDraftContext(result.contextId)
              if (
                result.contextId &&
                diagnosticsRequestedForContext !== result.contextId
              ) {
                diagnosticsRequestedForContext = result.contextId
                void loadContextDiagnostics(result.contextId).catch(
                  (loadError) => {
                    setError(
                      getRequestErrorMessage(
                        loadError,
                        "Failed to load route diagnostics"
                      )
                    )
                  }
                )
              }

              if (isA2APartialMessage(result)) {
                streamedMessageText += messageText
                const displayTask = {
                  ...a2aMessageToDisplayTask(
                    result.contextId,
                    prompt,
                    result.messageId,
                    [{ text: streamedMessageText }]
                  ),
                  status: "running" as const,
                  final_answer: streamedMessageText,
                }
                const session: AgentSession = {
                  session_id: result.contextId,
                  context_id: result.contextId,
                  title: prompt,
                  status: "running",
                  metadata,
                  created_at: displayTask.created_at,
                  updated_at: displayTask.updated_at,
                }
                upsertResolvedSession(session)
                setTasks((previous) => {
                  const next = { ...previous }
                  delete next[pendingActivityId]
                  next[displayTask.task_id] = displayTask
                  return next
                })
                appendConversationMessages(result.contextId, [
                  buildUserConversationMessage(
                    outgoingMessageId,
                    prompt,
                    outgoingCreatedAt,
                    displayTask.task_id
                  ),
                  buildAssistantConversationMessage(
                    pendingAssistantMessageId,
                    streamedMessageText,
                    displayTask.updated_at,
                    true,
                    displayTask.task_id
                  ),
                ])
                setCurrentSessionId(result.contextId)
                setCurrentTaskId(displayTask.task_id)
                return
              }

              if (!isA2AFinalMessage(result)) return

              const displayTask = a2aMessageToDisplayTask(
                result.contextId,
                prompt,
                result.messageId,
                result.parts
              )
              const session: AgentSession = {
                session_id: result.contextId,
                context_id: result.contextId,
                title: prompt,
                status: "completed",
                metadata,
                created_at: displayTask.created_at,
                updated_at: displayTask.updated_at,
              }
              upsertResolvedSession(session)
              setTasks((previous) => {
                const next = { ...previous }
                delete next[pendingActivityId]
                next[displayTask.task_id] = displayTask
                return next
              })
              appendConversationMessages(result.contextId, [
                buildUserConversationMessage(
                  outgoingMessageId,
                  prompt,
                  outgoingCreatedAt,
                  displayTask.task_id
                ),
                buildAssistantConversationMessage(
                  pendingAssistantMessageId,
                  textFromParts(result.parts),
                  displayTask.updated_at,
                  false,
                  displayTask.task_id
                ),
              ])
              setCurrentSessionId(result.contextId)
              setCurrentTaskId(displayTask.task_id)
              setBusy(false)
              return
            }

            if (result.kind === "task") {
              streamedTaskId = result.id
              const runtimeThreadId = threadIdFromMetadata(result.metadata)
              promoteDraftContext(result.contextId)
              const task: AgentTask = {
                task_id: result.id,
                session_id: result.contextId,
                thread_id: runtimeThreadId,
                status: normalizeStatus(result.status.state),
                input: prompt,
                attempt: 1,
                final_answer: null,
                metadata: result.metadata ?? {},
                created_at: result.status.timestamp || new Date().toISOString(),
                updated_at: result.status.timestamp || new Date().toISOString(),
              }
              appendConversationMessages(result.contextId, [
                buildUserConversationMessage(
                  outgoingMessageId,
                  prompt,
                  outgoingCreatedAt,
                  task.task_id
                ),
                buildAssistantConversationMessage(
                  pendingAssistantMessageId,
                  "",
                  task.updated_at,
                  isActiveStatus(task.status),
                  task.task_id
                ),
              ])
              const session: AgentSession = {
                session_id: result.contextId,
                context_id: result.contextId,
                title: prompt,
                status: task.status,
                metadata: result.metadata ?? {},
                created_at: task.created_at,
                updated_at: task.updated_at,
              }
              upsertResolvedSession(session)
              setTasks((previous) => {
                const next = { ...previous }
                delete next[pendingActivityId]
                next[task.task_id] = task
                return next
              })
              setCurrentSessionId(result.contextId)
              setCurrentTaskId(task.task_id)
              void loadContextDiagnostics(result.contextId).catch(
                (loadError) => {
                  setError(
                    getRequestErrorMessage(
                      loadError,
                      "Failed to load route diagnostics"
                    )
                  )
                }
              )
              return
            }

            const projectedEvent = a2aEnvelopeToTaskEvent(envelope)
            if (!projectedEvent) return
            const event = eventWithTaskThreadId(
              projectedEvent,
              tasksRef.current[projectedEvent.task_id]
            )
            streamedTaskId = event.task_id

            setEventsByTask((previous) => {
              const merged = mergeEvents(previous[event.task_id] ?? [], [event])
              eventsByTaskRef.current = {
                ...eventsByTaskRef.current,
                [event.task_id]: merged,
              }
              setSelectedEventId(preferredEventId(merged))
              return { ...previous, [event.task_id]: merged }
            })

            if (event.status) {
              setTasks((previous) => {
                const task = previous[event.task_id]
                if (!task) return previous
                const inputRequest = result.metadata?.inputRequest
                const metadata = { ...(task.metadata ?? {}) }
                if (
                  event.status === "interrupted" &&
                  inputRequest &&
                  typeof inputRequest === "object"
                ) {
                  metadata.inputRequest = inputRequest
                } else if (event.status !== "interrupted") {
                  delete metadata.inputRequest
                }
                return {
                  ...previous,
                  [event.task_id]: {
                    ...task,
                    status: event.status ?? task.status,
                    updated_at: event.created_at,
                    metadata,
                  },
                }
              })
              setSessions((previous) =>
                previous.map((session) =>
                  session.session_id === event.session_id
                    ? {
                        ...session,
                        status: event.status ?? session.status,
                        updated_at: event.created_at,
                      }
                    : session
                )
              )
            }

            const finalAnswer = textFromA2AArtifact(envelope)
            if (finalAnswer) {
              setTasks((previous) => {
                const task = previous[event.task_id]
                if (!task) return previous
                return {
                  ...previous,
                  [event.task_id]: {
                    ...task,
                    final_answer: finalAnswer,
                    updated_at: event.created_at,
                  },
                }
              })
              appendConversationMessages(event.session_id, [
                buildAssistantConversationMessage(
                  pendingAssistantMessageId,
                  finalAnswer,
                  event.created_at,
                  false,
                  event.task_id
                ),
              ])
            }

            if (result.kind === "status-update" && result.final) {
              void reconcileA2ATaskSnapshot(event.task_id)
            }
          },
          onError: (streamError) => {
            setError(
              getRequestErrorMessage(streamError, "Failed to stream message")
            )
            if (streamedTaskId) {
              void reconcileA2ATaskSnapshot(streamedTaskId)
            }
          },
        }
      )
    } catch (sendError) {
      if (!receivedStreamEvent) {
        setInput(prompt)
      }
      setError(getRequestErrorMessage(sendError, "Failed to send message"))
    } finally {
      if (messageStreamAbortRef.current === abortController) {
        messageStreamAbortRef.current = null
      }
      if (streamedTaskId) {
        void reconcileA2ATaskSnapshot(streamedTaskId)
      }
      setBusy(false)
    }
  }

  async function newSession() {
    userSessionSelectionRef.current = true
    setError("")
    closeStream()
    setBusy(false)
    setCurrentSessionId("")
    setCurrentTaskId("")
    setSelectedMessageId("")
    setSelectedEventId("")
    setInput("")
    setExecutionMode("auto")
  }

  async function deleteSession(sessionId: string) {
    const session = sessions.find((item) => item.session_id === sessionId)
    const confirmed = window.confirm(
      `Delete ${session?.title || "this session"} and all related messages, tasks, events, and artifacts?`
    )
    if (!confirmed) return

    const sessionTasks = taskList.filter(
      (task) => task.session_id === sessionId
    )
    setDeletingSessionId(sessionId)
    setError("")
    if (sessionId === currentSessionId) {
      closeStream()
    }

    try {
      await deleteAgentContext(sessionId, true)
      const deletedTaskIds = new Set(sessionTasks.map((task) => task.task_id))
      const nextSessions = sessions.filter(
        (item) => item.session_id !== sessionId
      )
      const nextSessionId =
        sessionId === currentSessionId
          ? (nextSessions[0]?.session_id ?? "")
          : currentSessionId
      const nextTask =
        nextSessionId && sessionId === currentSessionId
          ? taskList.find((task) => task.session_id === nextSessionId)
          : undefined
      const nextSelectedEventId = nextTask
        ? preferredEventId(eventsByTask[nextTask.task_id] ?? [])
        : ""

      setSessions(nextSessions)
      setRouteDecisionsByContext((previous) => {
        const next = { ...previous }
        delete next[sessionId]
        return next
      })
      setDelegationsByContext((previous) => {
        const next = { ...previous }
        delete next[sessionId]
        return next
      })
      setTasks((previous) => {
        const next = { ...previous }
        for (const taskId of deletedTaskIds) {
          delete next[taskId]
        }
        return next
      })
      setEventsByTask((previous) => {
        const next = { ...previous }
        for (const taskId of deletedTaskIds) {
          delete next[taskId]
        }
        return next
      })
      setMessagesByContext((previous) => {
        const next = { ...previous }
        delete next[sessionId]
        return next
      })

      if (sessionId === currentSessionId) {
        setCurrentSessionId(nextSessionId)
        setCurrentTaskId(nextTask?.task_id ?? "")
        setSelectedMessageId("")
        setSelectedEventId(nextSelectedEventId)
      } else if (deletedTaskIds.has(currentTaskId)) {
        setCurrentTaskId("")
        setSelectedMessageId("")
        setSelectedEventId("")
      }
    } catch (deleteError) {
      setError(getRequestErrorMessage(deleteError, "Failed to delete session"))
    } finally {
      setDeletingSessionId("")
    }
  }

  function startEditingSession(session: AgentSession) {
    setError("")
    setEditingSessionId(session.session_id)
    setEditingSessionTitle(session.title || session.session_id)
  }

  function cancelEditingSession() {
    setEditingSessionId("")
    setEditingSessionTitle("")
  }

  async function saveSessionTitle(sessionId: string) {
    const title = editingSessionTitle.trim()
    if (!title || updatingSessionId) return

    setUpdatingSessionId(sessionId)
    setError("")
    try {
      const updatedContext = await updateAgentContext(sessionId, { title })
      const updatedSession = contextToSession(updatedContext)
      setSessions((previous) =>
        previous.map((session) =>
          session.session_id === sessionId
            ? {
                ...session,
                ...updatedSession,
              }
            : session
        )
      )
      cancelEditingSession()
    } catch (updateError) {
      setError(getRequestErrorMessage(updateError, "Failed to update session"))
    } finally {
      setUpdatingSessionId("")
    }
  }

  async function cancelCurrentTask() {
    if (!currentTask || !isActiveStatus(currentTask.status)) return

    setError("")
    try {
      const canceledTask = await cancelAgentA2ATask(
        currentTask.task_id,
        "operator requested"
      )
      const nextStatus = normalizeStatus(canceledTask.status.state)
      setTasks((previous) => ({
        ...previous,
        [currentTask.task_id]: {
          ...currentTask,
          status: nextStatus,
          updated_at: canceledTask.status.timestamp || new Date().toISOString(),
        },
      }))
      setSessions((previous) =>
        previous.map((session) =>
          session.session_id === currentTask.session_id
            ? {
                ...session,
                status: nextStatus,
                updated_at: canceledTask.status.timestamp || session.updated_at,
              }
            : session
        )
      )
    } catch (cancelError) {
      setError(getRequestErrorMessage(cancelError, "Failed to cancel task"))
    }
  }

  async function resumeTask(taskId: string, approved: boolean) {
    const task = tasks[taskId]
    if (!task || !isApprovalRequiredStatus(task.status) || resumingTaskId)
      return

    const reason = approved
      ? "operator approved in web UI"
      : "operator rejected in web UI"
    setError("")
    setResumingTaskId(taskId)
    try {
      const resumedTask = await resumeAgentA2ATask(taskId, approved, reason)
      const nextStatus = normalizeStatus(resumedTask.status.state)
      const updatedAt = resumedTask.status.timestamp || new Date().toISOString()
      const contextId = resumedTask.contextId || task.session_id
      const runtimeThreadId = threadIdFromMetadata(resumedTask.metadata)

      setTasks((previous) => ({
        ...previous,
        [taskId]: {
          ...task,
          thread_id: runtimeThreadId || task.thread_id,
          status: nextStatus,
          updated_at: updatedAt,
          metadata: resumedTask.metadata ?? task.metadata,
        },
      }))
      setSessions((previous) =>
        previous.map((session) =>
          session.session_id === contextId
            ? {
                ...session,
                status: nextStatus,
                metadata: resumedTask.metadata ?? session.metadata,
                updated_at: updatedAt,
              }
            : session
        )
      )
      setCurrentTaskId(taskId)
      hydrateTaskEvents(taskId)
      if (contextId) {
        await loadContextMessages(contextId)
      }
    } catch (resumeError) {
      setError(getRequestErrorMessage(resumeError, "Failed to resume task"))
    } finally {
      setResumingTaskId("")
    }
  }

  async function submitTaskInput(taskId: string, value: string) {
    const task = tasks[taskId]
    const text = value.trim()
    if (
      !task ||
      !text ||
      !isGeneralInputRequiredTask(task) ||
      submittingTaskInputId
    )
      return

    setError("")
    setSubmittingTaskInputId(taskId)
    try {
      const result = await submitAgentA2ATaskInput(
        taskId,
        task.session_id,
        text,
        { source: "web-ui" }
      )
      if (result.kind !== "task") {
        throw new Error("Task input continuation returned a message result.")
      }

      const snapshot = result.task
      const nextStatus = normalizeStatus(snapshot.status.state)
      const updatedAt = snapshot.status.timestamp || new Date().toISOString()
      const runtimeThreadId = threadIdFromMetadata(snapshot.metadata)
      setTasks((previous) => ({
        ...previous,
        [taskId]: {
          ...task,
          thread_id: runtimeThreadId || task.thread_id,
          status: nextStatus,
          updated_at: updatedAt,
          metadata: snapshot.metadata ?? {},
        },
      }))
      setSessions((previous) =>
        previous.map((session) =>
          session.session_id === snapshot.contextId
            ? {
                ...session,
                status: nextStatus,
                updated_at: updatedAt,
              }
            : session
        )
      )
      setCurrentTaskId(taskId)
      hydrateTaskEvents(taskId)
      await loadContextMessages(snapshot.contextId)
    } catch (submitError) {
      setError(
        getRequestErrorMessage(submitError, "Failed to submit task input")
      )
    } finally {
      setSubmittingTaskInputId("")
    }
  }

  async function saveRegisteredAgent() {
    const agentId = agentRegistryForm.agentId.trim()
    const name = agentRegistryForm.name.trim()
    const cardUrl = agentRegistryForm.cardUrl.trim()
    const keywords = parseKeywords(agentRegistryForm.keywords)
    if (!agentId || !name || !cardUrl || registryBusy) return

    setRegistryBusy(true)
    setError("")
    try {
      await upsertAgentRegisteredAgent({
        agent_id: agentId,
        name,
        card_url: cardUrl,
        enabled: true,
        metadata: { keywords },
      })
      setAgentRegistryForm({ agentId: "", name: "", cardUrl: "", keywords: "" })
      await reloadRegisteredAgents()
    } catch (saveError) {
      setError(
        getRequestErrorMessage(saveError, "Failed to save registered agent")
      )
    } finally {
      setRegistryBusy(false)
    }
  }

  async function deleteRegisteredAgent(agentId: string) {
    const agent = registeredAgents.find((item) => item.agent_id === agentId)
    const confirmed = window.confirm(
      `Delete registered agent ${agent?.name || agentId}?`
    )
    if (!confirmed) return

    setRegistryBusy(true)
    setError("")
    try {
      await deleteAgentRegisteredAgent(agentId)
      if (selectedRemoteAgentId === agentId) {
        setSelectedRemoteAgentId("")
      }
      await reloadRegisteredAgents()
    } catch (deleteError) {
      setError(
        getRequestErrorMessage(deleteError, "Failed to delete registered agent")
      )
    } finally {
      setRegistryBusy(false)
    }
  }

  async function refreshRegisteredAgent(agentId: string) {
    setRefreshingAgentId(agentId)
    setError("")
    try {
      const refreshed = await refreshAgentRegisteredAgent(agentId)
      setRegisteredAgents((previous) =>
        previous.map((agent) =>
          agent.agent_id === agentId ? refreshed : agent
        )
      )
    } catch (refreshError) {
      setError(
        getRequestErrorMessage(refreshError, "Failed to refresh agent card")
      )
    } finally {
      setRefreshingAgentId("")
    }
  }

  function editRegisteredAgent(agent: AgentRegisteredAgent) {
    setAgentRegistryForm({
      agentId: agent.agent_id,
      name: agent.name,
      cardUrl: agent.card_url,
      keywords: agentKeywords(agent).join(", "),
    })
  }

  async function copyMessage(message: AgentMessage) {
    await navigator.clipboard.writeText(message.content)
    setCopiedMessageId(message.id)
    window.setTimeout(() => setCopiedMessageId(""), 1200)
  }

  function selectSession(sessionId: string) {
    userSessionSelectionRef.current = true
    closeStream()
    setCurrentSessionId(sessionId)
    const latestTask = taskList.find((task) => task.session_id === sessionId)
    setCurrentTaskId(latestTask?.task_id ?? "")
    setSelectedMessageId("")
    setSelectedEventId("")
    void loadContextMessages(sessionId).catch((loadError) => {
      setError(
        getRequestErrorMessage(loadError, "Failed to load session messages")
      )
    })
  }

  function selectMessage(message: AgentMessage) {
    setSelectedMessageId(message.id)
    if (!message.taskId) {
      setCurrentTaskId("")
      setSelectedEventId("")
      return
    }
    const task = tasks[message.taskId]
    setCurrentTaskId(message.taskId)
    setCurrentSessionId(task?.session_id ?? currentSessionId)
    if (!eventsByTask[message.taskId]?.length) {
      hydrateTaskEvents(message.taskId)
    }
    setSelectedEventId(preferredEventId(eventsByTask[message.taskId] ?? []))
  }

  return (
    <main
      className="agent-view flex h-dvh overflow-hidden bg-[#F8FAFC] text-[#1F0013]"
      data-testid="agent-console"
    >
      <AgentSidebar
        expanded={sidebarExpanded}
        sessions={sessions}
        currentSessionId={currentSessionId}
        loading={loading}
        modelConfig={modelConfig}
        deletingSessionId={deletingSessionId}
        editingSessionId={editingSessionId}
        editingSessionTitle={editingSessionTitle}
        updatingSessionId={updatingSessionId}
        onToggle={() => setSidebarExpanded((value) => !value)}
        onNewSession={newSession}
        onSelectSession={selectSession}
        onDeleteSession={deleteSession}
        onStartEditSession={startEditingSession}
        onEditSessionTitleChange={setEditingSessionTitle}
        onCancelEditSession={cancelEditingSession}
        onSaveSessionTitle={saveSessionTitle}
      />

      <section className="flex min-w-0 flex-1 flex-col overflow-hidden">
        {error && (
          <div
            role="alert"
            data-testid="agent-error-banner"
            className="border-b border-[#FECACA] bg-[#FEF2F2] px-4 py-2 text-[13px] leading-5 text-[#991B1B] md:px-8"
          >
            {error}
          </div>
        )}

        <div className="flex min-h-0 flex-1 overflow-hidden">
          <section
            className="flex min-w-0 flex-1 flex-col overflow-hidden"
            data-testid="agent-main"
          >
            {currentSessionId ? (
              <MessageList
                messages={conversationMessages}
                tasks={tasks}
                selectedMessageId={selectedMessageId}
                copiedMessageId={copiedMessageId}
                onCopyMessage={copyMessage}
                onSelectMessage={selectMessage}
                onResumeTask={resumeTask}
                onSubmitTaskInput={submitTaskInput}
                resumingTaskId={resumingTaskId}
                submittingTaskInputId={submittingTaskInputId}
                busy={busy}
              />
            ) : (
              <WelcomePanel
                input={input}
                isGenerating={busy}
                session={currentSession}
                executionMode={executionMode}
                registeredAgents={enabledRegisteredAgents}
                selectedRemoteAgentId={selectedRemoteAgentId}
                onInputChange={setInput}
                onModeChange={setExecutionMode}
                onRemoteAgentChange={setSelectedRemoteAgentId}
                onSend={sendMessage}
              />
            )}
            {currentSessionId && (
              <Composer
                input={input}
                isGenerating={Boolean(isCurrentSessionTaskActive || busy)}
                onInputChange={setInput}
                executionMode={executionMode}
                registeredAgents={enabledRegisteredAgents}
                selectedRemoteAgentId={selectedRemoteAgentId}
                onModeChange={setExecutionMode}
                onRemoteAgentChange={setSelectedRemoteAgentId}
                onSend={sendMessage}
                onStop={cancelCurrentTask}
              />
            )}
          </section>

          <Inspector
            mainAgentCard={mainAgentCard}
            task={currentTask}
            events={currentEvents}
            selectedEvent={selectedEvent}
            selectedEventId={selectedEventId}
            registeredAgents={registeredAgents}
            routeDecisions={currentRouteDecisions}
            delegations={currentDelegations}
            selectedRemoteAgentId={selectedRemoteAgentId}
            registryForm={agentRegistryForm}
            registryBusy={registryBusy}
            refreshingAgentId={refreshingAgentId}
            onSelectEvent={setSelectedEventId}
            onRemoteAgentChange={setSelectedRemoteAgentId}
            onRegistryFormChange={setAgentRegistryForm}
            onSaveRegisteredAgent={saveRegisteredAgent}
            onRefreshRegisteredAgent={refreshRegisteredAgent}
            onEditRegisteredAgent={editRegisteredAgent}
            onDeleteRegisteredAgent={deleteRegisteredAgent}
          />
        </div>
      </section>
    </main>
  )
}

function AgentSidebar({
  expanded,
  sessions,
  currentSessionId,
  loading,
  modelConfig,
  deletingSessionId,
  editingSessionId,
  editingSessionTitle,
  updatingSessionId,
  onToggle,
  onNewSession,
  onSelectSession,
  onDeleteSession,
  onStartEditSession,
  onEditSessionTitleChange,
  onCancelEditSession,
  onSaveSessionTitle,
}: {
  expanded: boolean
  sessions: AgentSession[]
  currentSessionId: string
  loading: boolean
  modelConfig: AgentModelConfig | null
  deletingSessionId: string
  editingSessionId: string
  editingSessionTitle: string
  updatingSessionId: string
  onToggle: () => void
  onNewSession: () => void
  onSelectSession: (sessionId: string) => void
  onDeleteSession: (sessionId: string) => void
  onStartEditSession: (session: AgentSession) => void
  onEditSessionTitleChange: (title: string) => void
  onCancelEditSession: () => void
  onSaveSessionTitle: (sessionId: string) => void
}) {
  return (
    <aside
      className={cn(
        "hidden h-full shrink-0 overflow-hidden border-r border-[#CBD5E1] bg-white transition-[width] duration-200 ease-out md:block",
        expanded ? "w-[324px]" : "w-20"
      )}
      data-expanded={expanded ? "true" : "false"}
      data-testid="agent-sidebar"
    >
      {expanded ? (
        <div className="flex h-full flex-col">
          <div className="flex h-[68px] items-center gap-3 border-b border-[#E7E5E8] px-5">
            <div
              className="flex h-9 w-9 items-center justify-center rounded-full text-white"
              style={{ background: AGENT_GRADIENT }}
            >
              <Bot className="h-[18px] w-[18px]" />
            </div>
            <div className="min-w-0 flex-1">
              <p className="m-0 truncate text-[15px] font-semibold leading-5">
                Agent Console
              </p>
              <p className="m-0 mt-0.5 truncate text-[11px] leading-4 text-[#64748B]">
                Sessions and workspace
              </p>
            </div>
            <button
              className="flex h-7 w-7 shrink-0 items-center justify-center rounded-[5px] text-[#1F0013] transition hover:bg-[#F1F5F9]"
              type="button"
              aria-label="Collapse sidebar"
              title="Collapse sidebar"
              onClick={onToggle}
            >
              <Menu className="h-[18px] w-[18px]" />
            </button>
          </div>

          <div className="px-5 py-3.5">
            <button
              className="flex h-9 w-full items-center justify-center gap-2 rounded-full bg-[#1E3A8A] text-[13px] font-medium text-white transition hover:brightness-105"
              type="button"
              onClick={onNewSession}
            >
              <MessageSquarePlus className="h-4 w-4" />
              New session
            </button>
          </div>

          <div className="flex items-center justify-between px-5 pb-2">
            <div className="flex items-center gap-2 text-[13px] font-semibold text-[#54465C]">
              <History className="h-4 w-4" />
              Sessions
            </div>
            <span className="rounded-full bg-[#F8FAFC] px-2 py-0.5 text-[11px] font-medium leading-4 text-[#64748B]">
              {sessions.length}
            </span>
          </div>

          <div className="min-h-0 flex-1 overflow-y-auto overflow-x-hidden px-4 pb-5">
            <div className="grid gap-2">
              {sessions.map((session) => {
                const selected = session.session_id === currentSessionId
                const deleting = deletingSessionId === session.session_id
                const editing = editingSessionId === session.session_id
                const updating = updatingSessionId === session.session_id
                const sessionTitle = session.title || session.session_id

                return (
                  <div
                    key={session.session_id}
                    className={cn(
                      "group relative w-full overflow-hidden rounded-[8px] transition-[background,box-shadow] duration-200",
                      selected
                        ? "bg-[#EEF4FF] text-[#1F0013] shadow-[inset_0_0_0_1px_rgba(183,205,255,0.65)]"
                        : "bg-[#F7F7F8] text-[#54465C] hover:bg-[#F0F4FB]"
                    )}
                    data-selected={selected ? "true" : "false"}
                    data-session-id={session.session_id}
                    data-testid="agent-session-item"
                  >
                    {selected && (
                      <span className="absolute left-0 top-2.5 bottom-2.5 w-[2px] rounded-r-full bg-[#3768C7]" />
                    )}
                    {editing ? (
                      <div className="flex min-w-0 items-start gap-2 px-2.5 py-2.5 pl-4 pr-[68px]">
                        <StatusDot status={session.status} />
                        <div className="min-w-0 flex-1">
                          <input
                            autoFocus
                            className="block h-[22px] w-full min-w-0 rounded-[4px] border border-[#CBD5E1] bg-white px-1.5 text-[13px] font-semibold leading-[18px] text-[#1F0013] outline-none transition focus:border-[#3768C7] focus:shadow-[0_0_0_2px_rgba(55,104,199,0.14)]"
                            value={editingSessionTitle}
                            aria-label="Session title"
                            data-testid="agent-session-title-input"
                            disabled={updating}
                            onChange={(event) =>
                              onEditSessionTitleChange(event.target.value)
                            }
                            onKeyDown={(event) => {
                              if (event.key === "Enter") {
                                event.preventDefault()
                                onSaveSessionTitle(session.session_id)
                              }
                              if (event.key === "Escape") {
                                event.preventDefault()
                                onCancelEditSession()
                              }
                            }}
                          />
                          <p className="m-0 mt-0.5 truncate text-[11px] leading-4 text-[#64748B]">
                            {formatDateTime(session.updated_at)}
                          </p>
                        </div>
                      </div>
                    ) : (
                      <button
                        className="block min-w-0 w-full rounded-[8px] px-2.5 py-2.5 pl-4 pr-[68px] text-left outline-none transition-shadow focus-visible:shadow-[inset_0_0_0_2px_rgba(55,104,199,0.35)]"
                        type="button"
                        data-testid="agent-session-select"
                        onClick={() => onSelectSession(session.session_id)}
                      >
                        <div className="flex min-w-0 items-start gap-2">
                          <StatusDot status={session.status} />
                          <div className="min-w-0 flex-1 overflow-hidden">
                            <p
                              className={cn(
                                "m-0 max-w-full truncate text-[13px] leading-[18px]",
                                selected
                                  ? "font-semibold text-[#1F0013]"
                                  : "font-medium text-[#62576A]"
                              )}
                              title={sessionTitle}
                            >
                              {sessionTitle}
                            </p>
                            <p
                              className="m-0 mt-0.5 truncate text-[11px] leading-4 text-[#64748B]"
                              title={formatDateTime(session.updated_at)}
                            >
                              {formatDateTime(session.updated_at)}
                            </p>
                          </div>
                        </div>
                      </button>
                    )}
                    <div
                      className={cn(
                        "absolute right-2 top-2 flex h-6 w-[52px] items-center justify-end gap-1 transition-opacity duration-200",
                        selected || editing
                          ? "opacity-55 hover:opacity-100 focus-visible:opacity-100"
                          : "opacity-100 md:opacity-0 md:group-hover:opacity-100 md:focus-visible:opacity-100"
                      )}
                    >
                      {editing ? (
                        <>
                          <button
                            className="flex h-6 w-6 items-center justify-center rounded-[5px] bg-transparent text-[#3768C7] transition-[background,color] duration-200 hover:bg-[#EAF1FF] focus-visible:bg-[#EAF1FF] focus-visible:outline-none disabled:cursor-not-allowed disabled:opacity-50"
                            type="button"
                            aria-label="Save session title"
                            data-testid="agent-session-title-save"
                            title="Save title"
                            disabled={updating || !editingSessionTitle.trim()}
                            onClick={() =>
                              onSaveSessionTitle(session.session_id)
                            }
                          >
                            <Check className="h-3.5 w-3.5" />
                          </button>
                          <button
                            className="flex h-6 w-6 items-center justify-center rounded-[5px] bg-transparent text-[#94A3B8] transition-[background,color] duration-200 hover:bg-[#F1F5F9] hover:text-[#54465C] focus-visible:bg-[#F1F5F9] focus-visible:outline-none disabled:cursor-not-allowed disabled:opacity-50"
                            type="button"
                            aria-label="Cancel title edit"
                            data-testid="agent-session-title-cancel"
                            title="Cancel"
                            disabled={updating}
                            onClick={onCancelEditSession}
                          >
                            <X className="h-3.5 w-3.5" />
                          </button>
                        </>
                      ) : (
                        <>
                          <button
                            className="flex h-6 w-6 items-center justify-center rounded-[5px] bg-transparent text-[#94A3B8] transition-[background,color] duration-200 hover:bg-[#EAF1FF] hover:text-[#1E3A8A] focus-visible:bg-[#EAF1FF] focus-visible:text-[#1E3A8A] focus-visible:outline-none"
                            type="button"
                            aria-label="Edit session title"
                            data-testid="agent-session-edit"
                            title="Edit title"
                            onClick={() => onStartEditSession(session)}
                          >
                            <Pencil className="h-3.5 w-3.5" />
                          </button>
                          <button
                            className="flex h-6 w-6 items-center justify-center rounded-[5px] bg-transparent text-[#94A3B8] transition-[background,color] duration-200 hover:bg-[#FEF2F2] hover:text-[#B91C1C] focus-visible:bg-[#FEF2F2] focus-visible:text-[#B91C1C] focus-visible:outline-none disabled:cursor-not-allowed disabled:opacity-50"
                            type="button"
                            aria-label="Delete session"
                            data-testid="agent-session-delete"
                            title="Delete session"
                            disabled={deleting}
                            onClick={() => onDeleteSession(session.session_id)}
                          >
                            <Trash2 className="h-3.5 w-3.5" />
                          </button>
                        </>
                      )}
                    </div>
                  </div>
                )
              })}
              {loading && (
                <div className="px-3 py-4 text-[13px] text-[#64748B]">
                  Loading sessions...
                </div>
              )}
            </div>
          </div>

          <SidebarBottomSummary modelConfig={modelConfig} loading={loading} />
        </div>
      ) : (
        <div className="flex h-full flex-col items-center py-6">
          <button
            className="flex h-10 w-10 items-center justify-center rounded-[4px] text-[#1F0013] transition hover:bg-[#F1F5F9]"
            type="button"
            aria-label="Expand sidebar"
            title="Expand sidebar"
            onClick={onToggle}
          >
            <Menu className="h-6 w-6" />
          </button>
          <button
            className="mt-6 flex h-9 w-9 items-center justify-center rounded-full bg-[#1E3A8A] text-white transition hover:brightness-105"
            type="button"
            aria-label="New session"
            onClick={onNewSession}
          >
            <MessageSquarePlus className="h-4 w-4" />
          </button>
          <div className="mt-6 flex h-12 w-12 items-center justify-center rounded-[4px] text-[#1F0013]">
            <History className="h-6 w-6" />
          </div>
          <div className="mt-auto flex h-9 w-9 items-center justify-center rounded-[4px] text-[#64748B]">
            <span
              className={cn(
                "h-2.5 w-2.5 rounded-full",
                loading ? "bg-[#F59E0B]" : "bg-[#10B981]"
              )}
            />
          </div>
        </div>
      )}
    </aside>
  )
}

function SidebarBottomSummary({
  modelConfig,
  loading,
}: {
  modelConfig: AgentModelConfig | null
  loading: boolean
}) {
  const primaryModel = modelConfig?.primary_model
  const routerModel = modelConfig?.router_model

  return (
    <div className="shrink-0 border-t border-[#F1F5F9] px-4 py-3">
      <div className="rounded-[8px] border border-[#E7E5E8] bg-[#F8FAFC] p-3">
        <div className="mb-2 flex items-center justify-between gap-2">
          <div className="flex items-center gap-2 text-[12px] font-semibold leading-4 text-[#54465C]">
            <Database className="h-3.5 w-3.5" />
            Models
          </div>
          <span className="rounded-full bg-white px-2 py-0.5 text-[10px] font-medium leading-4 text-[#64748B]">
            read-only
          </span>
        </div>
        <div className="grid gap-2">
          <SidebarModelRow
            label="Main"
            loading={loading && !primaryModel}
            name={primaryModel?.name}
            provider={primaryModel?.provider}
            model={primaryModel?.model}
          />
          <SidebarModelRow
            label="Router"
            loading={loading && !routerModel}
            name={routerModel?.name}
            provider={routerModel?.provider}
            model={routerModel?.model}
            badge={
              modelConfig?.router_model_overridden ? "override" : undefined
            }
          />
        </div>
      </div>
    </div>
  )
}

function SidebarModelRow({
  label,
  loading,
  name,
  provider,
  model,
  badge,
}: {
  label: string
  loading: boolean
  name?: string | null
  provider?: string | null
  model?: string | null
  badge?: string
}) {
  const title = loading ? "Loading..." : name || "Not configured"
  const detail = loading
    ? "Reading model config"
    : [provider, model].filter(Boolean).join(" · ") || "No model details"

  return (
    <div className="rounded-[6px] border border-[#E7E5E8] bg-white px-2.5 py-2">
      <div className="mb-1 flex items-center justify-between gap-2">
        <span className="text-[10px] font-semibold uppercase leading-4 tracking-[0.04em] text-[#94A3B8]">
          {label}
        </span>
        {badge && (
          <span className="rounded-full bg-[#EEF4FF] px-1.5 py-0.5 text-[10px] font-medium leading-3 text-[#1E3A8A]">
            {badge}
          </span>
        )}
      </div>
      <p className="m-0 truncate text-[12px] font-semibold leading-4 text-[#1F0013]">
        {title}
      </p>
      <p className="m-0 mt-0.5 truncate text-[11px] leading-4 text-[#64748B]">
        {detail}
      </p>
    </div>
  )
}

function WelcomePanel({
  input,
  isGenerating,
  session,
  executionMode,
  registeredAgents,
  selectedRemoteAgentId,
  onInputChange,
  onModeChange,
  onRemoteAgentChange,
  onSend,
}: {
  input: string
  isGenerating: boolean
  session?: AgentSession
  executionMode: AgentA2AExecutionMode
  registeredAgents: AgentRegisteredAgent[]
  selectedRemoteAgentId: string
  onInputChange: (value: string) => void
  onModeChange: (value: AgentA2AExecutionMode) => void
  onRemoteAgentChange: (agentId: string) => void
  onSend: () => void
}) {
  const suggestions = [
    "Inspect why the latest tool call failed",
    "Compare memory retrieval across two tasks",
    "Design a SQLite task event schema",
  ]

  return (
    <div className="flex min-h-0 flex-1 flex-col overflow-y-auto px-4 py-8 md:px-8">
      <div className="mx-auto flex w-full max-w-[1080px] flex-1 flex-col justify-center">
        <div className="mb-8 flex w-full flex-col items-center text-center">
          <div
            className="mb-5 flex h-14 w-14 items-center justify-center rounded-full text-white"
            style={{ background: AGENT_GRADIENT }}
          >
            <Sparkles className="h-7 w-7" />
          </div>
          <h1 className="m-0 text-[30px] font-semibold leading-10 text-[#1F0013]">
            Vermay Agent
          </h1>
          <p className="m-0 mx-auto mt-3 max-w-[680px] text-[15px] leading-7 text-[#54465C]">
            Start tasks from chat, then inspect task events, status, and final
            answers.
          </p>
          {session && (
            <p className="m-0 mt-2 max-w-[680px] truncate text-[12px] leading-5 text-[#64748B]">
              Current session: {session.session_id}
            </p>
          )}
        </div>
        <div className="mb-5 grid w-full gap-3 sm:grid-cols-3">
          {suggestions.map((suggestion) => (
            <button
              key={suggestion}
              className="min-h-[74px] rounded-[4px] border border-[#E7E5E8] bg-white px-4 py-3 text-left text-[13px] leading-5 text-[#1F0013] transition hover:border-[#3768C7] hover:bg-[#F8FAFC]"
              type="button"
              onClick={() => onInputChange(suggestion)}
            >
              {suggestion}
            </button>
          ))}
        </div>
        <Composer
          input={input}
          isGenerating={isGenerating}
          executionMode={executionMode}
          registeredAgents={registeredAgents}
          selectedRemoteAgentId={selectedRemoteAgentId}
          onInputChange={onInputChange}
          onModeChange={onModeChange}
          onRemoteAgentChange={onRemoteAgentChange}
          onSend={onSend}
          onStop={() => undefined}
          embedded
        />
      </div>
    </div>
  )
}

function MessageList({
  messages,
  tasks,
  selectedMessageId,
  copiedMessageId,
  busy,
  onCopyMessage,
  onSelectMessage,
  onResumeTask,
  onSubmitTaskInput,
  resumingTaskId,
  submittingTaskInputId,
}: {
  messages: AgentMessage[]
  tasks: Record<string, AgentTask>
  selectedMessageId: string
  copiedMessageId: string
  busy: boolean
  onCopyMessage: (message: AgentMessage) => void
  onSelectMessage: (message: AgentMessage) => void
  onResumeTask: (taskId: string, approved: boolean) => void
  onSubmitTaskInput: (taskId: string, value: string) => void
  resumingTaskId: string
  submittingTaskInputId: string
}) {
  const listRef = useRef<HTMLDivElement>(null)
  const latestMessage = messages.at(-1)

  useEffect(() => {
    const list = listRef.current
    if (!list) return

    const frame = window.requestAnimationFrame(() => {
      list.scrollTo({
        top: list.scrollHeight,
        behavior: latestMessage?.loading ? "auto" : "smooth",
      })
    })

    return () => window.cancelAnimationFrame(frame)
  }, [
    latestMessage?.id,
    latestMessage?.content,
    latestMessage?.loading,
    latestMessage?.taskId
      ? tasks[latestMessage.taskId]?.status
      : undefined,
    messages.length,
  ])

  return (
    <div
      ref={listRef}
      className="min-h-0 flex-1 overflow-x-hidden overflow-y-auto px-4 py-6 md:px-8"
      data-testid="agent-message-list"
    >
      <div className="mx-auto grid w-full max-w-[980px] gap-5">
        {messages.length ? (
          messages.map((message) => {
            const task = message.taskId ? tasks[message.taskId] : undefined
            const selected = message.id === selectedMessageId

            return (
              <MessageItem
                key={message.id}
                message={message}
                task={task}
                selected={selected}
                copied={copiedMessageId === message.id}
                busy={busy}
                resuming={message.taskId === resumingTaskId}
                submittingInput={message.taskId === submittingTaskInputId}
                onSelect={() => onSelectMessage(message)}
                onCopy={() => onCopyMessage(message)}
                onResume={(approved) => {
                  if (message.taskId) onResumeTask(message.taskId, approved)
                }}
                onSubmitInput={(value) => {
                  if (message.taskId) onSubmitTaskInput(message.taskId, value)
                }}
              />
            )
          })
        ) : (
          <div className="rounded-[4px] border border-dashed border-[#CBD5E1] px-4 py-10 text-center text-[13px] leading-5 text-[#64748B]">
            No messages in this session yet.
          </div>
        )}
      </div>
    </div>
  )
}

function MessageItem({
  message,
  task,
  selected,
  copied,
  busy,
  resuming,
  submittingInput,
  onSelect,
  onCopy,
  onResume,
  onSubmitInput,
}: {
  message: AgentMessage
  task?: AgentTask
  selected: boolean
  copied: boolean
  busy: boolean
  resuming: boolean
  submittingInput: boolean
  onSelect: () => void
  onCopy: () => void
  onResume: (approved: boolean) => void
  onSubmitInput: (value: string) => void
}) {
  const isUser = message.role === "user"
  const isInputPending = Boolean(
    !isUser && task && isApprovalRequiredStatus(task.status) && !message.content
  )
  const isLoadingOnly =
    message.loading && !message.content && !isInputPending
  const hasTaskEvents = Boolean(task && !isMessageDisplayTask(task))

  return (
    <div
      className={cn("flex w-full", isUser ? "justify-end" : "justify-start")}
      data-agent-role={message.role}
      data-testid="agent-message-item"
    >
      <div
        className={cn(
          "flex w-full max-w-[min(100%,980px)] items-start gap-3",
          isUser ? "flex-row-reverse" : ""
        )}
      >
        {!isUser && (
          <div className="mt-1 flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-[#1E3A8A] text-white">
            <Bot className="h-5 w-5" />
          </div>
        )}
        <div
          className={cn(
            "flex min-w-0 flex-col",
            !isUser && isInputPending ? "w-full" : "",
            isUser ? "items-end" : "items-start"
          )}
        >
          <div
            role="button"
            tabIndex={0}
            className={cn(
              "cursor-pointer text-[14px] leading-6 shadow-sm outline-none transition-[border-color,box-shadow]",
              isLoadingOnly ? "px-3 py-2.5" : "px-4 py-4",
              isUser
                ? "max-w-[520px] rounded-[4px_0_4px_4px] border border-[#E2E8F0] bg-[#EFF6FF] text-[#0F172A]"
                : cn(
                    "max-w-[844px] rounded-[0_4px_4px_4px] border border-[#E7E5E8] bg-white text-[#1F0013]",
                    isInputPending ? "w-full" : "w-fit"
                  ),
              selected &&
                "border-[#8F2BB8] shadow-[0_0_0_2px_rgba(143,43,184,0.14)]"
            )}
            data-selected={selected ? "true" : "false"}
            data-task-id={message.taskId ?? ""}
            data-testid="agent-message-bubble"
            onClick={onSelect}
            onKeyDown={(event) => {
              if (event.key === "Enter" || event.key === " ") {
                event.preventDefault()
                onSelect()
              }
            }}
          >
            {isInputPending && task ? (
              isGeneralInputRequiredTask(task) ? (
                <TaskInputRequiredCard
                  task={task}
                  submitting={submittingInput}
                  onSubmit={onSubmitInput}
                />
              ) : (
                <ApprovalRequiredCard
                  task={task}
                  resuming={resuming}
                  onResume={onResume}
                />
              )
            ) : isLoadingOnly ? (
              <TypingIndicator />
            ) : message.content ? (
              <MarkdownText content={message.content} />
            ) : (
              <p className="m-0 text-[#64748B]">Waiting for final answer...</p>
            )}
          </div>
          <div className="mt-2 flex flex-wrap items-center gap-2">
            {task && (
              <button
                className={cn(
                  "rounded-full px-2 py-0.5 text-[11px] font-medium leading-4 transition",
                  selected
                    ? "bg-[#F3E8FF] text-[#6B21A8]"
                    : "bg-[#F1F5F9] text-[#64748B] hover:bg-[#EEF4FF] hover:text-[#1E3A8A]"
                )}
                type="button"
                onClick={onSelect}
              >
                {hasTaskEvents ? `task · ${task.status}` : "message"}
              </button>
            )}
            {busy && message.loading && !isInputPending && (
              <span className="rounded-full bg-[#F1F5F9] px-2 py-0.5 text-[11px] font-medium leading-4 text-[#64748B]">
                Updating
              </span>
            )}
            {message.content && (
              <button
                className={cn(
                  "flex h-6 w-8 items-center justify-center rounded-[4px] border text-[#0F172A] transition hover:border-[#4C1C6A]",
                  copied
                    ? "border-[#4C1C6A] bg-[#E7E5E8] text-[#4C1C6A]"
                    : "border-[#E7E5E8] bg-white"
                )}
                type="button"
                aria-label="Copy message"
                onClick={(event) => {
                  event.stopPropagation()
                  onCopy()
                }}
              >
                {copied ? (
                  <Check className="h-4 w-4" />
                ) : (
                  <Copy className="h-4 w-4" />
                )}
              </button>
            )}
            <span className="text-[12px] leading-4 text-[#94A3B8]">
              {formatTime(message.createdAt)}
            </span>
          </div>
        </div>
      </div>
    </div>
  )
}

function ApprovalRequiredCard({
  task,
  resuming,
  onResume,
}: {
  task: AgentTask
  resuming: boolean
  onResume: (approved: boolean) => void
}) {
  const message =
    task.interrupt_message ||
    task.error?.message ||
    "This task is waiting for operator approval before it can continue."

  return (
    <div className="w-full max-w-[640px] min-w-0 overflow-hidden">
      <div className="mb-3 flex items-start justify-between gap-3">
        <div>
          <p className="m-0 text-[13px] font-semibold leading-5 text-[#1F0013]">
            Approval required
          </p>
          <p className="m-0 mt-1 text-[12px] leading-5 text-[#64748B]">
            Review the requested action, then approve or reject it.
          </p>
        </div>
        <span className="rounded-full bg-[#F3E8FF] px-2 py-0.5 text-[11px] font-semibold leading-4 text-[#6B21A8]">
          input required
        </span>
      </div>
      <div className="mb-4 break-words rounded-[4px] border border-[#E7E5E8] bg-[#F8FAFC] px-3 py-2 text-[12px] leading-5 text-[#54465C] [overflow-wrap:anywhere]">
        {message}
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <button
          className="inline-flex h-8 items-center gap-1.5 rounded-full bg-[#1E3A8A] px-3 text-[12px] font-semibold text-white transition hover:bg-[#264AA6] disabled:cursor-not-allowed disabled:opacity-60"
          type="button"
          disabled={resuming}
          data-testid="agent-approval-approve"
          onClick={(event) => {
            event.stopPropagation()
            onResume(true)
          }}
        >
          <Check className="h-3.5 w-3.5" />
          Approve
        </button>
        <button
          className="inline-flex h-8 items-center gap-1.5 rounded-full border border-[#E7E5E8] bg-white px-3 text-[12px] font-semibold text-[#54465C] transition hover:border-[#CBD5E1] hover:bg-[#F8FAFC] disabled:cursor-not-allowed disabled:opacity-60"
          type="button"
          disabled={resuming}
          data-testid="agent-approval-reject"
          onClick={(event) => {
            event.stopPropagation()
            onResume(false)
          }}
        >
          <X className="h-3.5 w-3.5" />
          Reject
        </button>
        {resuming && (
          <span className="text-[12px] leading-4 text-[#64748B]">
            Resuming...
          </span>
        )}
      </div>
    </div>
  )
}

function TaskInputRequiredCard({
  task,
  submitting,
  onSubmit,
}: {
  task: AgentTask
  submitting: boolean
  onSubmit: (value: string) => void
}) {
  const request = taskInputRequest(task)
  const [value, setValue] = useState("")

  useEffect(() => {
    setValue("")
  }, [request?.prompt])

  if (!request) return null

  return (
    <form
      className="w-full max-w-[640px] min-w-0 overflow-hidden"
      data-testid="agent-task-input-card"
      onClick={(event) => event.stopPropagation()}
      onSubmit={(event) => {
        event.preventDefault()
        if (!submitting && value.trim()) onSubmit(value)
      }}
    >
      <div className="mb-3 flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="m-0 text-[13px] font-semibold leading-5 text-[#1F0013]">
            Input required
          </p>
          <p className="m-0 mt-1 text-[12px] leading-5 text-[#64748B]">
            The task is paused until you provide this information.
          </p>
        </div>
        <span className="shrink-0 rounded-full bg-[#F3E8FF] px-2 py-0.5 text-[11px] font-semibold leading-4 text-[#6B21A8]">
          waiting
        </span>
      </div>
      <div className="mb-3 break-words rounded-[4px] border border-[#E7E5E8] bg-[#F8FAFC] px-3 py-2 text-[12px] leading-5 text-[#54465C] [overflow-wrap:anywhere]">
        {request.prompt}
      </div>
      {request.choices.length > 0 && (
        <div className="mb-3 flex flex-wrap gap-2" data-testid="agent-task-input-choices">
          {request.choices.map((choice) => (
            <button
              key={choice}
              className="min-h-8 max-w-full break-words whitespace-normal rounded-full border border-[#CBD5E1] bg-white px-3 text-[12px] font-medium text-[#334155] transition hover:border-[#3768C7] hover:bg-[#EFF6FF] hover:text-[#1E3A8A] disabled:cursor-not-allowed disabled:opacity-60 [overflow-wrap:anywhere]"
              type="button"
              disabled={submitting}
              onClick={() => onSubmit(choice)}
            >
              {choice}
            </button>
          ))}
        </div>
      )}
      <div className="flex min-w-0 items-center gap-2">
        <input
          className="h-9 min-w-0 flex-1 rounded-[4px] border border-[#CBD5E1] bg-white px-3 text-[12px] text-[#1F0013] outline-none transition focus:border-[#8F2BB8] focus:shadow-[0_0_0_2px_rgba(143,43,184,0.12)]"
          type="text"
          value={value}
          disabled={submitting}
          placeholder="Type your answer"
          aria-label="Task input"
          data-testid="agent-task-input-field"
          onChange={(event) => setValue(event.target.value)}
        />
        <button
          className="h-9 shrink-0 rounded-[4px] bg-[#1E3A8A] px-3 text-[12px] font-semibold text-white transition hover:bg-[#264AA6] disabled:cursor-not-allowed disabled:opacity-50"
          type="submit"
          disabled={submitting || !value.trim()}
          data-testid="agent-task-input-submit"
        >
          {submitting ? "Submitting..." : "Continue"}
        </button>
      </div>
    </form>
  )
}

function MarkdownText({ content }: { content: string }) {
  return (
    <ReactMarkdown
      remarkPlugins={[remarkGfm]}
      components={{
        p: ({ children }) => <p className="m-0 [&+p]:mt-2">{children}</p>,
        ul: ({ children }) => (
          <ul className="m-0 list-disc space-y-1 pl-5">{children}</ul>
        ),
        ol: ({ children }) => (
          <ol className="m-0 list-decimal space-y-1 pl-5">{children}</ol>
        ),
        code: ({ children }) => (
          <code className="rounded-[4px] bg-slate-900/10 px-1.5 py-0.5 font-mono text-[0.92em]">
            {children}
          </code>
        ),
      }}
    >
      {content}
    </ReactMarkdown>
  )
}

function Composer({
  input,
  isGenerating,
  executionMode,
  registeredAgents,
  selectedRemoteAgentId,
  onInputChange,
  onModeChange,
  onRemoteAgentChange,
  onSend,
  onStop,
  embedded,
}: {
  input: string
  isGenerating: boolean
  executionMode: AgentA2AExecutionMode
  registeredAgents: AgentRegisteredAgent[]
  selectedRemoteAgentId: string
  onInputChange: (value: string) => void
  onModeChange: (value: AgentA2AExecutionMode) => void
  onRemoteAgentChange: (agentId: string) => void
  onSend: () => void
  onStop: () => void
  embedded?: boolean
}) {
  const hasInput = Boolean(input.trim())
  const [isFocused, setIsFocused] = useState(false)
  const showHighlight = isFocused || hasInput
  const isButtonActive = isGenerating || hasInput
  const composerBorder = showHighlight
    ? COMPOSER_ACTIVE_BORDER
    : COMPOSER_IDLE_BORDER
  const composerShadow = showHighlight
    ? "0 0 0 3px rgba(143, 43, 184, 0.08), 0 14px 28px -28px rgba(55, 104, 199, 0.55)"
    : "0 0 0 0 rgba(55, 104, 199, 0)"

  return (
    <div className={embedded ? "pb-3 pt-4" : "px-4 pb-3 pt-4 md:px-6 lg:px-8"}>
      <div
        className={cn(
          "mx-auto w-full",
          embedded ? "max-w-none" : "max-w-[1120px]"
        )}
      >
        <div
          className="relative rounded-[12px] bg-white px-5 pb-5 pt-5 transition-[border-color,box-shadow] duration-300 ease-out"
          data-active={showHighlight}
          data-composer-active={showHighlight ? "true" : "false"}
          onFocusCapture={() => setIsFocused(true)}
          onBlurCapture={(event) => {
            if (
              !event.currentTarget.contains(event.relatedTarget as Node | null)
            ) {
              setIsFocused(false)
            }
          }}
          style={{
            border: `1px solid ${composerBorder}`,
            boxShadow: composerShadow,
          }}
        >
          <div className="mb-3 grid min-w-0 grid-cols-[minmax(0,auto)_minmax(0,1fr)] items-center gap-3">
            <div className="flex min-w-0 flex-nowrap items-center gap-2">
              <div className="inline-flex rounded-full bg-[#F1F5F9] p-1">
                {(["auto", "message", "task"] as const).map((mode) => {
                  const selected = executionMode === mode
                  return (
                    <button
                      key={mode}
                      className={cn(
                        "h-7 rounded-full px-3 text-[12px] font-medium capitalize transition-[background,color,box-shadow] duration-200",
                        selected
                          ? "bg-white text-[#1F0013] shadow-sm"
                          : "text-[#64748B] hover:text-[#1F0013]"
                      )}
                      type="button"
                      data-testid={`agent-mode-${mode}`}
                      onClick={() => onModeChange(mode)}
                    >
                      {mode}
                    </button>
                  )
                })}
              </div>
              <select
                className="h-8 w-[220px] max-w-[42vw] shrink-0 rounded-full border border-[#E7E5E8] bg-white px-3 text-[12px] font-medium text-[#54465C] outline-none transition focus:border-[#8F2BB8]"
                value={selectedRemoteAgentId}
                aria-label="Route target"
                data-testid="agent-route-target"
                onChange={(event) => onRemoteAgentChange(event.target.value)}
              >
                <option value="">Main agent</option>
                {registeredAgents.map((agent) => (
                  <option key={agent.agent_id} value={agent.agent_id}>
                    {agent.name}
                  </option>
                ))}
              </select>
            </div>
            <span className="hidden min-w-0 truncate whitespace-nowrap text-right text-[12px] leading-5 text-[#64748B] md:block">
              {selectedRemoteAgentId
                ? "Delegated to a registered child agent"
                : executionMode === "auto"
                  ? "Auto routes to answer, task, or child agent"
                  : executionMode === "message"
                    ? "Fast answer, no task events"
                    : "Run as task with events and artifacts"}
            </span>
          </div>
          <textarea
            className={cn(
              "min-h-[106px] w-full resize-none bg-transparent pr-20 text-[14px] leading-5 outline-none",
              hasInput
                ? "text-[#1F0013] placeholder:text-[#1F0013]"
                : "text-[#54465C] placeholder:text-[#54465C]"
            )}
            style={{
              color: hasInput ? COMPOSER_TEXT : COMPOSER_MUTED_TEXT,
            }}
            data-testid="agent-composer-input"
            value={input}
            placeholder={
              executionMode === "task"
                ? "Enter an agent task. Enter to run, Shift + Enter for a new line."
                : executionMode === "message"
                  ? "Ask the agent. Enter to send, Shift + Enter for a new line."
                  : "Ask the agent. Auto routes when tools or delegation are needed."
            }
            onChange={(event) => onInputChange(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && !event.shiftKey) {
                event.preventDefault()
                if (isGenerating) return
                onSend()
              }
            }}
          />
          <button
            className={cn(
              "absolute bottom-5 right-5 flex h-10 w-10 items-center justify-center rounded-full text-white transition-[background,box-shadow,filter] duration-300 ease-out disabled:cursor-not-allowed"
            )}
            data-send-active={isButtonActive ? "true" : "false"}
            data-testid="agent-composer-send"
            style={{
              background: isButtonActive
                ? AGENT_GRADIENT
                : COMPOSER_IDLE_BORDER,
              boxShadow: isButtonActive
                ? "0 12px 22px -16px rgba(55, 104, 199, 0.8)"
                : "none",
            }}
            type="button"
            disabled={!isGenerating && !hasInput}
            aria-label={isGenerating ? "Stop generating" : "Send"}
            onClick={isGenerating ? onStop : onSend}
          >
            {isGenerating ? <StopIcon /> : <SendIcon />}
          </button>
        </div>
        {!embedded && (
          <p className="m-0 mt-2 text-center text-[12px] leading-5 text-[#54465C]">
            Backed by Vermay Agent BFF SSE. Start Vermay Agent API before sending
            tasks.
          </p>
        )}
      </div>
    </div>
  )
}

function SendIcon() {
  return (
    <svg
      viewBox="0 0 18 18"
      className="h-[18px] w-[18px]"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      aria-hidden="true"
    >
      <path
        d="M2.26562 14.625L15.75 9L2.26562 3.375L2.25 7.75L11.25 9L2.25 10.25L2.26562 14.625Z"
        fill="white"
      />
    </svg>
  )
}

function StopIcon() {
  return (
    <svg
      width="40"
      height="40"
      viewBox="0 0 40 40"
      className="h-10 w-10"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      aria-hidden="true"
    >
      <rect
        width="40"
        height="40"
        rx="20"
        fill="url(#agent-composer-stop-gradient)"
      />
      <path d="M14 14H26V26H14V14Z" fill="white" />
      <defs>
        <linearGradient
          id="agent-composer-stop-gradient"
          x1="0"
          y1="0"
          x2="4.77684"
          y2="43.8871"
          gradientUnits="userSpaceOnUse"
        >
          <stop stopColor="#AD1A98" />
          <stop offset="1" stopColor="#3768C7" />
        </linearGradient>
      </defs>
    </svg>
  )
}

function Inspector({
  mainAgentCard,
  task,
  events,
  selectedEvent,
  selectedEventId,
  registeredAgents,
  routeDecisions,
  delegations,
  selectedRemoteAgentId,
  registryForm,
  registryBusy,
  refreshingAgentId,
  onSelectEvent,
  onRemoteAgentChange,
  onRegistryFormChange,
  onSaveRegisteredAgent,
  onRefreshRegisteredAgent,
  onEditRegisteredAgent,
  onDeleteRegisteredAgent,
}: {
  mainAgentCard: AgentA2AAgentCard | null
  task?: AgentTask
  events: AgentTaskEvent[]
  selectedEvent?: AgentTaskEvent
  selectedEventId: string
  registeredAgents: AgentRegisteredAgent[]
  routeDecisions: AgentRouteDecision[]
  delegations: AgentDelegation[]
  selectedRemoteAgentId: string
  registryForm: AgentRegistryForm
  registryBusy: boolean
  refreshingAgentId: string
  onSelectEvent: (eventId: string) => void
  onRemoteAgentChange: (agentId: string) => void
  onRegistryFormChange: (value: AgentRegistryForm) => void
  onSaveRegisteredAgent: () => void
  onRefreshRegisteredAgent: (agentId: string) => void
  onEditRegisteredAgent: (agent: AgentRegisteredAgent) => void
  onDeleteRegisteredAgent: (agentId: string) => void
}) {
  return (
    <aside className="hidden min-h-0 w-[390px] shrink-0 overflow-x-hidden border-l border-[#CBD5E1] bg-white xl:flex xl:flex-col">
      <div className="flex h-[76px] items-center gap-3 border-b border-[#E7E5E8] px-5">
        <div className="flex h-10 w-10 items-center justify-center rounded-full bg-[#F1F5F9] text-[#1E3A8A]">
          <Activity className="h-5 w-5" />
        </div>
        <div className="min-w-0">
          <p className="m-0 truncate text-[16px] font-semibold leading-6">
            Inspector
          </p>
          <p className="m-0 text-[12px] leading-4 text-[#64748B]">
            Route, activity, and payload
          </p>
        </div>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto overflow-x-hidden">
        <div className="border-b border-[#E7E5E8] p-5">
          <div className="grid min-w-0 grid-cols-[repeat(3,minmax(0,1fr))] gap-2">
            <Metric label="Events" value={String(events.length)} />
            <Metric
              label="Artifacts"
              value={String(
                events.filter((event) => event.event_type.includes("artifact"))
                  .length
              )}
            />
            <Metric label="Attempt" value={String(task?.attempt ?? 0)} />
          </div>
        </div>

        <MainAgentCardPanel card={mainAgentCard} />

        <AgentRegistryPanel
          agents={registeredAgents}
          selectedRemoteAgentId={selectedRemoteAgentId}
          form={registryForm}
          busy={registryBusy}
          refreshingAgentId={refreshingAgentId}
          onRemoteAgentChange={onRemoteAgentChange}
          onFormChange={onRegistryFormChange}
          onSave={onSaveRegisteredAgent}
          onRefresh={onRefreshRegisteredAgent}
          onEdit={onEditRegisteredAgent}
          onDelete={onDeleteRegisteredAgent}
        />

        <RouteDiagnosticsPanel
          routeDecisions={routeDecisions}
          delegations={delegations}
        />

        <div className="p-5">
          <div className="mb-4 flex items-center justify-between">
            <h2 className="m-0 text-[14px] font-semibold leading-5">
              Timeline
            </h2>
            <span className="rounded-full bg-[#F1F5F9] px-2 py-1 text-[12px] text-[#64748B]">
              {task?.status ?? "idle"}
            </span>
          </div>
          <div className="grid gap-3">
            {events.map((event) => (
              <TimelineEvent
                key={event.event_id}
                event={event}
                selected={eventKey(event) === selectedEventId}
                onSelect={() => onSelectEvent(eventKey(event))}
              />
            ))}
            {!events.length && (
              <div className="rounded-[4px] border border-dashed border-[#CBD5E1] px-4 py-8 text-center text-[13px] leading-5 text-[#64748B]">
                Task events will appear here.
              </div>
            )}
          </div>
        </div>

        <div className="border-t border-[#E7E5E8] p-5">
          <h2 className="m-0 mb-3 text-[14px] font-semibold leading-5">
            Payload
          </h2>
          <pre
            className="max-h-[360px] overflow-y-auto overflow-x-hidden whitespace-pre-wrap break-words rounded-[6px] bg-[#0F172A] p-4 text-[12px] leading-5 text-[#E2E8F0]"
            data-testid="agent-event-payload"
          >
            {JSON.stringify(selectedEvent ?? { state: "empty" }, null, 2)}
          </pre>
        </div>
      </div>
    </aside>
  )
}

function AgentRegistryPanel({
  agents,
  selectedRemoteAgentId,
  form,
  busy,
  refreshingAgentId,
  onRemoteAgentChange,
  onFormChange,
  onSave,
  onRefresh,
  onEdit,
  onDelete,
}: {
  agents: AgentRegisteredAgent[]
  selectedRemoteAgentId: string
  form: AgentRegistryForm
  busy: boolean
  refreshingAgentId: string
  onRemoteAgentChange: (agentId: string) => void
  onFormChange: (value: AgentRegistryForm) => void
  onSave: () => void
  onRefresh: (agentId: string) => void
  onEdit: (agent: AgentRegisteredAgent) => void
  onDelete: (agentId: string) => void
}) {
  const [expanded, setExpanded] = useState(false)
  const canSave = Boolean(
    form.agentId.trim() && form.name.trim() && form.cardUrl.trim()
  )
  const enabledCount = agents.filter((agent) => agent.enabled).length

  return (
    <div className="min-w-0 border-b border-[#E7E5E8] p-4">
      <div className="min-w-0 overflow-hidden rounded-[7px] border border-[#E7E5E8] bg-[#F8FAFC]">
        <div className="flex min-w-0 items-start justify-between gap-3 px-3 py-3">
          <div className="flex min-w-0 items-start gap-2">
            <Network className="mt-0.5 h-4 w-4 shrink-0 text-[#54465C]" />
            <div className="min-w-0">
              <h2 className="m-0 truncate text-[13px] font-semibold leading-5">
                Child agents
              </h2>
              <p className="m-0 truncate text-[11px] leading-4 text-[#64748B]">
                Registered delegation targets
              </p>
            </div>
          </div>
          <div className="flex shrink-0 items-center gap-1.5">
            <span className="rounded-full bg-white px-2 py-1 text-[11px] font-medium leading-4 text-[#64748B]">
              {enabledCount} enabled
            </span>
            <span className="rounded-full bg-white px-2 py-1 text-[11px] font-medium leading-4 text-[#64748B]">
              {agents.length} total
            </span>
            <button
              className="flex h-7 w-7 items-center justify-center rounded-full bg-white text-[#64748B] transition hover:bg-[#EEF4FF] hover:text-[#1E3A8A] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#D6C2EA]"
              type="button"
              aria-expanded={expanded}
              aria-label={
                expanded
                  ? "Collapse child agents panel"
                  : "Expand child agents panel"
              }
              onClick={() => setExpanded((value) => !value)}
            >
              <ChevronDown
                className={cn(
                  "h-4 w-4 transition-transform duration-200",
                  expanded ? "rotate-180" : "rotate-0"
                )}
              />
            </button>
          </div>
        </div>

        {expanded && (
          <div className="border-t border-[#E7E5E8] bg-white px-3 py-3">
            <div className="grid gap-2">
              <input
                className="h-9 rounded-[5px] border border-[#E7E5E8] bg-white px-3 text-[12px] text-[#1F0013] outline-none transition focus:border-[#8F2BB8]"
                value={form.agentId}
                placeholder="agent-id"
                data-testid="agent-registry-id"
                onChange={(event) =>
                  onFormChange({ ...form, agentId: event.target.value })
                }
              />
              <input
                className="h-9 rounded-[5px] border border-[#E7E5E8] bg-white px-3 text-[12px] text-[#1F0013] outline-none transition focus:border-[#8F2BB8]"
                value={form.name}
                placeholder="Display name"
                data-testid="agent-registry-name"
                onChange={(event) =>
                  onFormChange({ ...form, name: event.target.value })
                }
              />
              <input
                className="h-9 rounded-[5px] border border-[#E7E5E8] bg-white px-3 text-[12px] text-[#1F0013] outline-none transition focus:border-[#8F2BB8]"
                value={form.cardUrl}
                placeholder="http://127.0.0.1:9001/.well-known/agent-card.json"
                data-testid="agent-registry-card-url"
                onChange={(event) =>
                  onFormChange({ ...form, cardUrl: event.target.value })
                }
              />
              <input
                className="h-9 rounded-[5px] border border-[#E7E5E8] bg-white px-3 text-[12px] text-[#1F0013] outline-none transition focus:border-[#8F2BB8]"
                value={form.keywords}
                placeholder="Keywords: sqlite, kubernetes, memory"
                data-testid="agent-registry-keywords"
                onChange={(event) =>
                  onFormChange({ ...form, keywords: event.target.value })
                }
              />
              <button
                className="h-9 rounded-full bg-[#1E3A8A] text-[12px] font-semibold text-white transition hover:brightness-105 disabled:cursor-not-allowed disabled:bg-[#CBD5E1]"
                type="button"
                data-testid="agent-registry-save"
                disabled={!canSave || busy}
                onClick={onSave}
              >
                {busy ? "Saving..." : "Save child agent"}
              </button>
            </div>

            <div className="mt-4 grid gap-2">
              {agents.map((agent) => {
                const selected = agent.agent_id === selectedRemoteAgentId
                const keywords = agentKeywords(agent)
                const skillTags = agentCardSkillTags(agent)
                const skillCount = agentCardSkillCount(agent)
                const refreshing = refreshingAgentId === agent.agent_id
                return (
                  <div
                    key={agent.agent_id}
                    className={cn(
                      "min-w-0 rounded-[6px] border px-3 py-2 transition",
                      selected
                        ? "border-[#B7CDFF] bg-[#EEF4FF]"
                        : "border-[#E7E5E8] bg-white hover:border-[#CBD5E1]"
                    )}
                    data-agent-id={agent.agent_id}
                    data-selected={selected ? "true" : "false"}
                    data-testid="agent-registry-item"
                  >
                    <div className="flex items-start justify-between gap-2">
                      <button
                        className="min-w-0 flex-1 text-left"
                        type="button"
                        data-testid="agent-registry-select"
                        disabled={!agent.enabled}
                        onClick={() =>
                          onRemoteAgentChange(selected ? "" : agent.agent_id)
                        }
                      >
                        <p className="m-0 truncate text-[13px] font-semibold leading-5 text-[#1F0013]">
                          {agent.name}
                        </p>
                        <p className="m-0 mt-0.5 truncate text-[11px] leading-4 text-[#64748B]">
                          {agent.agent_id}
                        </p>
                        {skillCount > 0 && (
                          <p className="m-0 mt-0.5 truncate text-[10px] font-medium leading-4 text-[#3768C7]">
                            {skillCount} card skill
                            {skillCount === 1 ? "" : "s"}
                          </p>
                        )}
                      </button>
                      <div className="flex shrink-0 items-center gap-1">
                        <button
                          className="flex h-7 w-7 items-center justify-center rounded-[5px] text-[#64748B] transition hover:bg-[#EAF1FF] hover:text-[#1E3A8A] disabled:cursor-wait disabled:opacity-50"
                          type="button"
                          aria-label={`Refresh ${agent.name} card`}
                          title={`Refresh ${agent.name} card`}
                          disabled={refreshing}
                          onClick={() => onRefresh(agent.agent_id)}
                        >
                          <RefreshCcw
                            className={cn(
                              "h-3.5 w-3.5",
                              refreshing && "animate-spin"
                            )}
                          />
                        </button>
                        <button
                          className="h-7 rounded-[5px] px-2 text-[11px] font-medium text-[#1E3A8A] transition hover:bg-[#EAF1FF]"
                          type="button"
                          onClick={() => onEdit(agent)}
                        >
                          Edit
                        </button>
                        <button
                          className="flex h-7 w-7 items-center justify-center rounded-[5px] text-[#94A3B8] transition hover:bg-[#FEF2F2] hover:text-[#B91C1C]"
                          type="button"
                          aria-label={`Delete ${agent.name}`}
                          data-testid="agent-registry-delete"
                          title={`Delete ${agent.name}`}
                          onClick={() => onDelete(agent.agent_id)}
                        >
                          <Trash2 className="h-3.5 w-3.5" />
                        </button>
                      </div>
                    </div>
                    <p className="m-0 mt-2 min-w-0 truncate text-[11px] leading-4 text-[#64748B]">
                      {agent.card_url}
                    </p>
                    {keywords.length > 0 && (
                      <div className="mt-2 flex min-w-0 flex-wrap gap-1">
                        {keywords.map((keyword) => (
                          <span
                            key={keyword}
                            className="max-w-full break-all rounded-full bg-[#F1F5F9] px-2 py-0.5 text-[10px] font-medium leading-4 text-[#64748B]"
                          >
                            {keyword}
                          </span>
                        ))}
                      </div>
                    )}
                    {skillTags.length > 0 && (
                      <div className="mt-2 flex min-w-0 flex-wrap gap-1">
                        {skillTags.map((tag) => (
                          <span
                            key={tag}
                            className="max-w-full break-all rounded-full bg-[#EAF1FF] px-2 py-0.5 text-[10px] font-medium leading-4 text-[#1E3A8A]"
                          >
                            {tag}
                          </span>
                        ))}
                      </div>
                    )}
                  </div>
                )
              })}
              {!agents.length && (
                <div className="rounded-[4px] border border-dashed border-[#CBD5E1] px-3 py-5 text-center text-[12px] leading-5 text-[#64748B]">
                  Register a child A2A agent to enable remote delegation.
                </div>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
function TimelineEvent({
  event,
  selected,
  onSelect,
}: {
  event: AgentTaskEvent
  selected: boolean
  onSelect: () => void
}) {
  const config = EVENT_LABELS[event.event_type]
  const Icon = config?.icon ?? TerminalSquare

  return (
    <button
      className={cn(
        "w-full rounded-[4px] border px-3 py-3 text-left transition",
        selected
          ? "border-[#3768C7] bg-[#F8FAFC]"
          : "border-[#E7E5E8] bg-white hover:border-[#CBD5E1] hover:bg-[#F8FAFC]"
      )}
      type="button"
      data-event-type={event.event_type}
      data-selected={selected ? "true" : "false"}
      data-testid="agent-timeline-event"
      onClick={onSelect}
    >
      <div className="flex items-start gap-3">
        <div className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-[#EAF1FF] text-[#1E3A8A]">
          <Icon className="h-4 w-4" />
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex items-center justify-between gap-2">
            <p className="m-0 truncate text-[13px] font-semibold leading-5 text-[#1F0013]">
              {config?.title ?? event.event_type}
            </p>
            <span className="shrink-0 text-[11px] text-[#94A3B8]">
              {formatTime(event.created_at)}
            </span>
          </div>
          <p className="m-0 mt-1 line-clamp-2 text-[12px] leading-5 text-[#64748B]">
            {event.status || config?.detail || "event"}
          </p>
        </div>
      </div>
    </button>
  )
}

function StatusDot({ status }: { status: AgentTaskStatus }) {
  return (
    <span className="mt-[5px] flex h-3 w-3 shrink-0 items-center justify-center rounded-full bg-[#EEF2FF]">
      <span
        className={cn(
          "h-1.5 w-1.5 rounded-full",
          isActiveStatus(status) && "bg-[#3768C7]",
          status === "completed" && "bg-[#16A34A]",
          (status === "canceled" ||
            status === "cancelled" ||
            status === "stopped") &&
            "bg-[#F97316]",
          status === "failed" && "bg-[#DC2626]",
          status === "unknown" && "bg-[#CBD5E1]"
        )}
      />
    </span>
  )
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-[4px] border border-[#E7E5E8] bg-[#F8FAFC] px-3 py-2">
      <p className="m-0 text-[18px] font-semibold leading-6 text-[#1F0013]">
        {value}
      </p>
      <p className="m-0 mt-1 text-[11px] leading-4 text-[#64748B]">{label}</p>
    </div>
  )
}

function TypingIndicator() {
  return (
    <span className="inline-flex h-3 items-center gap-1">
      <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-[#94A3B8]" />
      <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-[#94A3B8] [animation-delay:140ms]" />
      <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-[#94A3B8] [animation-delay:280ms]" />
    </span>
  )
}
