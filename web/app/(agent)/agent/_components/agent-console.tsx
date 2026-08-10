"use client"

import React, { useCallback, useEffect, useMemo, useRef, useState } from "react"
import {
  Activity,
  Bot,
  ChevronDown,
  Network,
  RefreshCcw,
  Trash2,
} from "lucide-react"

import {
  errorFromA2AStreamEnvelope,
  isA2AFinalMessage,
  isA2APartialMessage,
  textFromA2AParts as textFromParts,
} from "@/lib/agent/a2a-stream-contract"
import {
  a2aEnvelopeToTaskEvent,
  a2aMessageToDisplayTask,
  approvalTasksToConversation,
  buildAssistantConversationMessage,
  buildDirectMessageFailure,
  buildUserConversationMessage,
  contextToSession,
  eventWithTaskThreadId,
  failedTasksToConversation,
  failureFromRequestError,
  inputMessageIdFromFailureMessage,
  mergeConversationMessages,
  mergeEvents,
  messagesToDisplayTask,
  preferredEventId,
  pruneHydratedTransientMessages,
  storedMessagesToConversation,
  storedTaskToAgentTask,
  textFromA2AArtifact,
} from "@/lib/agent/conversation-projection"
import {
  a2aStateFromLocalProcessStatus,
  eventKey,
  formatDateTime,
  getTaskTitle,
  isActiveStatus,
  isApprovalRequiredStatus,
  isCancellationRequestedStatus,
  isGeneralInputRequiredTask,
  isMessageDisplayTask,
  isTerminalStatus,
  normalizeStatus,
  taskActivityLabel,
  taskErrorFromA2AEvent,
  taskErrorFromA2AMetadata,
  taskFailureForDisplay,
  taskInputRequest,
  taskStateProjection,
  taskStatusFromA2A,
  threadIdFromMetadata,
} from "@/lib/agent/task-presentation"
import {
  cancelAgentA2ATask,
  deleteAgentContext,
  deleteAgentRegisteredAgent,
  getAgentA2AAgentCard,
  getAgentA2ATask,
  getAgentContext,
  getAgentModelConfig,
  getAgentMessageIngress,
  listAgentContextDelegations,
  listAgentContextMessages,
  listAgentContextRouteDecisions,
  listAgentContexts,
  listAgentContextTasks,
  listAgentRegisteredAgents,
  refreshAgentRegisteredAgent,
  retryAgentTask,
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
  AgentA2ATask,
  AgentDelegation,
  AgentMessage,
  AgentMessageFailure,
  AgentMessageRequest,
  AgentModelConfig,
  AgentRegisteredAgent,
  AgentRouteDecision,
  AgentSession,
  AgentTask,
  AgentTaskEvent,
} from "@/lib/agent/types"
import { getRequestErrorMessage } from "@/lib/request"
import { cn } from "@/lib/utils"
import { MainAgentCardPanel } from "@/app/(agent)/agent/_components/agent-card-panel"
import { AgentComposer } from "@/app/(agent)/agent/_components/agent-composer"
import { AgentSidebar } from "@/app/(agent)/agent/_components/agent-sidebar"
import { AgentTranscript } from "@/app/(agent)/agent/_components/agent-transcript"
import {
  AgentTimeline,
  taskEventTitle,
} from "@/app/(agent)/agent/_components/agent-timeline"
import { AgentWelcomePanel } from "@/app/(agent)/agent/_components/agent-welcome-panel"
import { RouteDiagnosticsPanel } from "@/app/(agent)/agent/_components/route-diagnostics-panel"

type AgentRegistryForm = {
  agentId: string
  name: string
  cardUrl: string
  keywords: string
}

type SendMessageOptions = {
  prompt?: string
  contextId?: string
  request?: AgentMessageRequest
  preserveComposerInput?: boolean
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
  const [retryingTaskId, setRetryingTaskId] = useState("")
  const [error, setError] = useState("")
  const messageStreamAbortRef = useRef<AbortController | null>(null)
  const hydratingTaskEventsRef = useRef(new Set<string>())
  const completedTaskEventHydrationsRef = useRef(new Set<string>())
  const taskEventRecoveryAttemptsRef = useRef(new Set<string>())
  const currentTaskIdRef = useRef("")
  const eventsByTaskRef = useRef<Record<string, AgentTaskEvent[]>>({})
  const tasksRef = useRef<Record<string, AgentTask>>({})
  const refreshContextMessagesRef = useRef<(contextId: string) => void>(() => {})
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
  const isCurrentSessionCancellationPending = taskList.some(
    (task) =>
      task.session_id === currentSessionId &&
      isCancellationRequestedStatus(task.status)
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
    const status = taskStatusFromA2A(snapshot.status.state, snapshot.metadata)
    const updatedAt = snapshot.status.timestamp || new Date().toISOString()
    const runtimeThreadId = threadIdFromMetadata(snapshot.metadata)

    setTasks((previous) => {
      const task = previous[snapshot.id]
      if (!task) return previous
      const stateProjection = taskStateProjection(
        snapshot.status.state,
        snapshot.metadata,
        task.local_process_status
      )
      const taskError = taskErrorFromA2AMetadata(snapshot.metadata)
      return {
        ...previous,
        [snapshot.id]: {
          ...task,
          thread_id: runtimeThreadId || task.thread_id,
          a2a_state: stateProjection.a2a_state,
          local_process_status: stateProjection.local_process_status,
          status,
          updated_at: updatedAt,
          error: status === "failed" ? taskError ?? task.error : task.error,
          metadata: {
            ...(task.metadata ?? {}),
            ...(snapshot.metadata ?? {}),
          },
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

  const hydrateTaskEvents = useCallback((taskId: string, knownTerminal = false) => {
    if (
      !taskId ||
      hydratingTaskEventsRef.current.has(taskId) ||
      completedTaskEventHydrationsRef.current.has(taskId)
    ) {
      return
    }

    hydratingTaskEventsRef.current.add(taskId)
    const afterEventId = Math.max(
      0,
      ...(eventsByTaskRef.current[taskId] ?? []).map(
        (event) => event.event_id
      )
    )
    let finished = false
    let source: EventSource | null = null

    // EventSource reports a normal EOF from a finite terminal Task stream as an
    // error. Remember terminal replays so that EOF cannot restart hydration.
    const taskIsTerminal = () =>
      knownTerminal || isTerminalStatus(tasksRef.current[taskId]?.status)

    const finish = (terminal = false) => {
      if (finished) return
      finished = true
      source?.close()
      hydratingTaskEventsRef.current.delete(taskId)
      if (terminal) {
        completedTaskEventHydrationsRef.current.add(taskId)
        taskEventRecoveryAttemptsRef.current.delete(taskId)
      }
    }

    const refreshTaskContext = (fallbackContextId?: string) => {
      void reconcileA2ATaskSnapshot(taskId).finally(() => {
        const contextId = tasksRef.current[taskId]?.session_id || fallbackContextId
        if (contextId) refreshContextMessagesRef.current(contextId)
      })
    }

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
            const stateProjection = taskStateProjection(
              event.a2a_state,
              undefined,
              event.local_process_status ?? task.local_process_status
            )
            const taskError = taskErrorFromA2AEvent(event)
            return {
              ...previous,
              [event.task_id]: {
                ...task,
                a2a_state: stateProjection.a2a_state,
                local_process_status: stateProjection.local_process_status,
                status,
                updated_at: event.created_at,
                error: status === "failed" ? taskError ?? task.error : task.error,
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
          finish(true)
          refreshTaskContext(event.session_id)
        }
      },
      onError: () => {
        if (finished) return
        if (taskIsTerminal()) {
          finish(true)
          return
        }

        const alreadyRecovered = taskEventRecoveryAttemptsRef.current.has(taskId)
        finish()
        if (alreadyRecovered) return

        taskEventRecoveryAttemptsRef.current.add(taskId)
        refreshTaskContext(tasksRef.current[taskId]?.session_id)
      },
      onDone: () => {
        finish(taskIsTerminal())
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
            ...failedTasksToConversation(storedTasks, storedMessages),
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
        hydrateTaskEvents(
          latestTask.task_id,
          isTerminalStatus(latestTask.status)
        )
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

  useEffect(() => {
    refreshContextMessagesRef.current = (contextId) => {
      void loadContextMessages(contextId)
    }
  }, [loadContextMessages])

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
              "Cannot connect to the Vermay API. Confirm that vermay serve is running."
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

  async function sendMessage(options?: SendMessageOptions) {
    const prompt = (options?.prompt ?? input).trim()
    if (!prompt) return
    if (busy) {
      setError("The previous request is still finishing. Try again in a moment.")
      return
    }
    if (isCurrentSessionTaskActive) {
      setError(
        "This session has an active task. Wait for it to finish or cancel it before sending another request."
      )
      return
    }

    const messageRequest: AgentMessageRequest = options?.request ?? {
      executionMode,
      ...(selectedRemoteAgentId
        ? {
            route: "remote_agent",
            targetAgentId: selectedRemoteAgentId,
          }
        : {}),
    }
    const requestContextId = options?.contextId ?? currentSessionId
    const requestRoute =
      messageRequest.route ??
      (messageRequest.targetAgentId ? "remote_agent" : undefined)

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
    const displayContextId = requestContextId || draftContextId
    const pendingActivityId = `pending:${outgoingMessageId}`
    const pendingAssistantMessageId = `${pendingActivityId}:assistant`

    closeStream()
    messageStreamAbortRef.current = abortController
    setBusy(true)
    setError("")
    if (!options?.preserveComposerInput) {
      setInput("")
    }
    setSelectedEventId("")
    const pendingTask: AgentTask = {
      task_id: pendingActivityId,
      session_id: displayContextId,
      thread_id: "",
      a2a_state: messageRequest.executionMode === "message" ? null : "working",
      local_process_status:
        messageRequest.executionMode === "message" ? null : "running",
      status: "running",
      input: prompt,
      attempt: 1,
      final_answer: null,
      metadata: {
        displayKind:
          messageRequest.executionMode === "message" ? "message" : "task",
        displayTitle:
          messageRequest.executionMode === "message" ? "Direct message" : prompt,
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
        pendingActivityId,
        messageRequest
      ),
      buildAssistantConversationMessage(
        pendingAssistantMessageId,
        "",
        outgoingCreatedAt,
        true,
        pendingActivityId
      ),
    ])

    const promoteDraftContext = (contextId: string, omitMessageId?: string) => {
      setMessagesByContext((previous) => {
        const next = { ...previous }
        const draftMessages = (next[displayContextId] ?? []).filter(
          (message) => message.id !== omitMessageId
        )
        if (contextId === displayContextId) {
          next[contextId] = draftMessages
          return next
        }
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

    const removePendingActivity = () => {
      setTasks((previous) => {
        const next = { ...previous }
        delete next[pendingActivityId]
        return next
      })
    }

    const recoverMessageStreamFailure = async (
      fallbackFailure: AgentMessageFailure
    ) => {
      let contextId = displayContextId
      let failure = fallbackFailure
      let createdAt = new Date().toISOString()

      try {
        const ingress = await getAgentMessageIngress(outgoingMessageId)
        contextId = ingress.context_id
        createdAt = ingress.updated_at

        // A stream can fail after the server has accepted the message or even
        // completed its task. Ingress is the durable outcome owner, so reload
        // that context instead of projecting a false message failure locally.
        if (ingress.state === "resolved") {
          promoteDraftContext(contextId, pendingAssistantMessageId)
          removePendingActivity()
          setCurrentSessionId(contextId)
          setCurrentTaskId("")

          try {
            upsertResolvedSession(contextToSession(await getAgentContext(contextId)))
          } catch {
            upsertResolvedSession({
              session_id: contextId,
              context_id: contextId,
              title: prompt,
              status: "active",
              metadata: {},
              created_at: outgoingCreatedAt,
              updated_at: createdAt,
            })
          }

          try {
            await loadContextMessages(contextId)
          } catch (loadError) {
            setError(
              getRequestErrorMessage(
                loadError,
                "Message completed, but the conversation could not be refreshed"
              )
            )
          }
          return
        }

        if (ingress.state === "failed" && ingress.failure) {
          failure = ingress.failure
        }
      } catch {
        // The server may have failed before reserving ingress. Keep a local
        // error activity so the pending spinner is never mistaken for output.
      }

      setError(failure.message)
      promoteDraftContext(contextId, pendingAssistantMessageId)
      appendConversationMessages(contextId, [
        buildDirectMessageFailure(outgoingMessageId, failure, createdAt),
      ])
      removePendingActivity()
      setCurrentSessionId(contextId)
      setCurrentTaskId("")
      setSessions((previous) => {
        const existing = previous.find(
          (session) => session.session_id === contextId
        )
        const failureSession: AgentSession = {
          session_id: contextId,
          context_id: contextId,
          title: existing?.title || prompt,
          status: "failed",
          metadata: existing?.metadata ?? {},
          created_at: existing?.created_at || outgoingCreatedAt,
          updated_at: createdAt,
        }
        const withoutDraft = previous.filter(
          (session) => session.session_id !== displayContextId
        )
        const withoutCurrent = withoutDraft.filter(
          (session) => session.session_id !== contextId
        )
        return [failureSession, ...withoutCurrent]
      })

      if (contextId !== displayContextId) {
        void loadContextMessages(contextId).catch((loadError) => {
          setError(
            getRequestErrorMessage(
              loadError,
              "Failed to load the failed message"
            )
          )
        })
      }
    }

    try {
      await openAgentA2AMessageStream(
        {
          contextId: requestContextId || undefined,
          messageId: outgoingMessageId,
          text: prompt,
          executionMode: messageRequest.executionMode,
          ...(requestRoute
            ? {
                route: requestRoute,
                ...(messageRequest.targetAgentId
                  ? { targetAgentId: messageRequest.targetAgentId }
                  : {}),
              }
            : {}),
        },
        {
          signal: abortController.signal,
          onEvent: (envelope) => {
            receivedStreamEvent = true
            if (envelope.error) {
              const failure = errorFromA2AStreamEnvelope(envelope)
              // A JSON-RPC error is terminal. Abort so the sender's finally
              // block releases the composer even if a proxy keeps SSE open.
              abortController.abort()
              void recoverMessageStreamFailure(failure)
              return
            }

            const result = envelope.result
            if (!result || typeof result !== "object") return

            if (result.kind === "message") {
              const metadata = result.metadata ?? {}
              const messageText = textFromParts(result.parts)
              promoteDraftContext(result.contextId, pendingAssistantMessageId)
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
                    displayTask.task_id,
                    messageRequest
                  ),
                  buildAssistantConversationMessage(
                    result.messageId,
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
                  displayTask.task_id,
                  messageRequest
                ),
                buildAssistantConversationMessage(
                  result.messageId,
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
              const stateProjection = taskStateProjection(
                result.status.state,
                result.metadata
              )
              promoteDraftContext(result.contextId)
              const task: AgentTask = {
                task_id: result.id,
                session_id: result.contextId,
                thread_id: runtimeThreadId,
                a2a_state: stateProjection.a2a_state,
                local_process_status: stateProjection.local_process_status,
                status: taskStatusFromA2A(result.status.state, result.metadata),
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
                  task.task_id,
                  messageRequest
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
                const stateProjection = taskStateProjection(
                  event.a2a_state,
                  undefined,
                  event.local_process_status ?? task.local_process_status
                )
                const taskError = taskErrorFromA2AEvent(event)
                const inputRequest = result.metadata?.inputRequest
                const metadata = {
                  ...(task.metadata ?? {}),
                  ...(result.metadata ?? {}),
                }
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
                    a2a_state: stateProjection.a2a_state,
                    local_process_status: stateProjection.local_process_status,
                    status: event.status ?? task.status,
                    updated_at: event.created_at,
                    error:
                      event.status === "failed"
                        ? taskError ?? task.error
                        : task.error,
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
              // The artifact may have updated a client-only pending assistant
              // message. Reconcile the durable Context after the Task reaches
              // a terminal A2A state so that record replaces the pending item.
              void reconcileA2ATaskSnapshot(event.task_id).finally(() => {
                refreshContextMessagesRef.current(event.session_id)
              })
            }
          },
          onError: (streamError) => {
            void recoverMessageStreamFailure(
              failureFromRequestError(
                streamError,
                "Failed to stream message"
              )
            )
          },
        }
      )
    } catch (sendError) {
      if (!receivedStreamEvent && !options?.preserveComposerInput) {
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

  function retryDirectMessage(failureMessage: AgentMessage) {
    if (!failureMessage.failure?.retryable) return
    if (busy) {
      setError("The previous request is still finishing. Try again in a moment.")
      return
    }

    const inputMessageId = inputMessageIdFromFailureMessage(failureMessage)
    const originalMessage = conversationMessages.find(
      (message) => message.id === inputMessageId && message.role === "user"
    )
    if (!currentSessionId || !originalMessage?.content.trim()) {
      setError("The original request is no longer available to retry.")
      return
    }

    void sendMessage({
      prompt: originalMessage.content,
      contextId: currentSessionId,
      request: originalMessage.request ?? { executionMode: "auto" },
      preserveComposerInput: true,
    })
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
      const nextStatus = taskStatusFromA2A(
        canceledTask.status.state,
        canceledTask.metadata
      )
      const stateProjection = taskStateProjection(
        canceledTask.status.state,
        canceledTask.metadata,
        currentTask.local_process_status
      )
      setTasks((previous) => ({
        ...previous,
        [currentTask.task_id]: {
          ...currentTask,
          a2a_state: stateProjection.a2a_state,
          local_process_status: stateProjection.local_process_status,
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
      const nextStatus = taskStatusFromA2A(
        resumedTask.status.state,
        resumedTask.metadata
      )
      const updatedAt = resumedTask.status.timestamp || new Date().toISOString()
      const contextId = resumedTask.contextId || task.session_id
      const runtimeThreadId = threadIdFromMetadata(resumedTask.metadata)
      const stateProjection = taskStateProjection(
        resumedTask.status.state,
        resumedTask.metadata,
        task.local_process_status
      )

      setTasks((previous) => ({
        ...previous,
        [taskId]: {
          ...task,
          thread_id: runtimeThreadId || task.thread_id,
          a2a_state: stateProjection.a2a_state,
          local_process_status: stateProjection.local_process_status,
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

  async function retryFailedTask(taskId: string) {
    const task = tasks[taskId]
    if (
      !task ||
      task.status !== "failed" ||
      !task.error?.retryable ||
      retryingTaskId
    )
      return

    setError("")
    setRetryingTaskId(taskId)
    try {
      const retriedTask = await retryAgentTask(taskId)
      setCurrentSessionId(retriedTask.context_id)
      setCurrentTaskId(retriedTask.task_id)
      setSelectedMessageId(retriedTask.input_message_id)
      setSelectedEventId("")
      await loadContextMessages(retriedTask.context_id)
    } catch (retryError) {
      setError(getRequestErrorMessage(retryError, "Failed to retry task"))
    } finally {
      setRetryingTaskId("")
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
      const nextStatus = taskStatusFromA2A(
        snapshot.status.state,
        snapshot.metadata
      )
      const updatedAt = snapshot.status.timestamp || new Date().toISOString()
      const runtimeThreadId = threadIdFromMetadata(snapshot.metadata)
      const stateProjection = taskStateProjection(
        snapshot.status.state,
        snapshot.metadata,
        task.local_process_status
      )
      setTasks((previous) => ({
        ...previous,
        [taskId]: {
          ...task,
          thread_id: runtimeThreadId || task.thread_id,
          a2a_state: stateProjection.a2a_state,
          local_process_status: stateProjection.local_process_status,
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
      hydrateTaskEvents(message.taskId, isTerminalStatus(task?.status))
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
          <AgentTranscript
                messages={conversationMessages}
                tasks={tasks}
                selectedMessageId={selectedMessageId}
                copiedMessageId={copiedMessageId}
                onCopyMessage={copyMessage}
                onSelectMessage={selectMessage}
                onRetryMessage={retryDirectMessage}
                onRetryTask={retryFailedTask}
                onResumeTask={resumeTask}
                onSubmitTaskInput={submitTaskInput}
                resumingTaskId={resumingTaskId}
                submittingTaskInputId={submittingTaskInputId}
                retryingTaskId={retryingTaskId}
                busy={busy}
              />
            ) : (
              <AgentWelcomePanel
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
              <AgentComposer
                input={input}
                isGenerating={Boolean(isCurrentSessionTaskActive || busy)}
                isCancelling={isCurrentSessionCancellationPending}
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
            Route, task state, and events
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

        <TaskStateSummary task={task} />

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

        <AgentTimeline
          task={task}
          events={events}
          selectedEventId={selectedEventId}
          onSelectEvent={onSelectEvent}
        />

        <SelectedEventSummary event={selectedEvent} task={task} />

        <div className="border-t border-[#E7E5E8] p-5">
          <details
            className="overflow-hidden rounded-[6px] border border-[#E7E5E8] bg-[#F8FAFC]"
            data-testid="agent-raw-event-record"
          >
            <summary className="cursor-pointer px-3 py-2.5 text-[13px] font-semibold leading-5 text-[#1F0013] marker:text-[#64748B]">
              Raw event record
            </summary>
            <div className="border-t border-[#E7E5E8] p-3">
              <pre
                className="max-h-[360px] overflow-y-auto overflow-x-hidden whitespace-pre-wrap break-words rounded-[4px] bg-[#0F172A] p-3 text-[12px] leading-5 text-[#E2E8F0]"
                data-testid="agent-event-payload"
              >
                {JSON.stringify(selectedEvent ?? { state: "empty" }, null, 2)}
              </pre>
            </div>
          </details>
        </div>
      </div>
    </aside>
  )
}

function InspectorStateCell({
  label,
  value,
  className,
}: {
  label: string
  value?: string | null
  className?: string
}) {
  const displayValue = value || "not reported"
  return (
    <div
      className={cn(
        "min-w-0 rounded-[4px] border border-[#E7E5E8] bg-[#F8FAFC] px-3 py-2",
        className
      )}
    >
      <p className="m-0 text-[10px] font-medium uppercase leading-4 text-[#64748B]">
        {label}
      </p>
      <p
        className="m-0 mt-1 truncate font-mono text-[12px] font-medium leading-5 text-[#1F0013]"
        title={displayValue}
      >
        {displayValue}
      </p>
    </div>
  )
}

function TaskStateSummary({ task }: { task?: AgentTask }) {
  if (!task || isMessageDisplayTask(task)) return null

  const a2aState =
    task.a2a_state ||
    a2aStateFromLocalProcessStatus(task.local_process_status) ||
    null

  return (
    <div
      className="border-b border-[#E7E5E8] px-5 py-4"
      data-testid="agent-task-state-summary"
    >
      <h2 className="m-0 text-[14px] font-semibold leading-5">Task state</h2>
      <div className="mt-3 grid min-w-0 grid-cols-2 gap-2">
        <InspectorStateCell label="A2A Task" value={a2aState} />
        <InspectorStateCell
          label="Local process"
          value={task.local_process_status}
        />
        <InspectorStateCell
          className="col-span-2"
          label="LangGraph thread"
          value={task.thread_id}
        />
      </div>
    </div>
  )
}

function SelectedEventSummary({
  event,
  task,
}: {
  event?: AgentTaskEvent
  task?: AgentTask
}) {
  if (!event) return null

  const changesTaskState = Boolean(
    event.a2a_state || event.local_process_status
  )

  return (
    <div
      className="border-t border-[#E7E5E8] px-5 py-4"
      data-testid="agent-selected-event-summary"
    >
      <div className="flex min-w-0 items-center justify-between gap-3">
        <h2 className="m-0 text-[14px] font-semibold leading-5">
          Selected event
        </h2>
        <span className="truncate text-[11px] text-[#64748B]" title={event.event_type}>
          {taskEventTitle(event.event_type)}
        </span>
      </div>
      {changesTaskState ? (
        <div className="mt-3 grid min-w-0 grid-cols-2 gap-2">
          <InspectorStateCell label="A2A Task" value={event.a2a_state} />
          <InspectorStateCell
            label="Local process"
            value={event.local_process_status}
          />
          <InspectorStateCell
            className="col-span-2"
            label="LangGraph thread"
            value={event.thread_id || task?.thread_id}
          />
        </div>
      ) : (
        <p className="m-0 mt-2 text-[12px] leading-5 text-[#64748B]">
          This event records output or metadata; it does not change Task state.
        </p>
      )}
    </div>
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
