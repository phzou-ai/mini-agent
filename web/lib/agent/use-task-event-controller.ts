import {
  type Dispatch,
  type SetStateAction,
  useCallback,
  useEffect,
  useRef,
} from "react"

import { errorFromA2AStreamEnvelope } from "@/lib/agent/a2a-stream-contract"
import { getAgentA2ATask } from "@/lib/agent/client"
import {
  a2aEnvelopeToTaskEvent,
  eventWithTaskThreadId,
  failureFromRequestError,
  mergeEvents,
  preferredEventId,
  textFromA2AArtifact,
} from "@/lib/agent/conversation-projection"
import { openAgentA2ATaskEventStream } from "@/lib/agent/stream"
import { TaskEventStreamRegistry } from "@/lib/agent/task-event-stream-registry"
import {
  isTerminalStatus,
  taskStatusFromA2A,
} from "@/lib/agent/task-presentation"
import type { TaskProjectionAction } from "@/lib/agent/task-projection-reducer"
import type {
  AgentA2AStreamEnvelope,
  AgentA2ATask,
  AgentMessageFailure,
  AgentTask,
  AgentTaskEvent,
} from "@/lib/agent/types"
import { getRequestErrorMessage } from "@/lib/request"

type TaskEventProjection =
  | {
      ok: true
      event: AgentTaskEvent
      finalAnswer: string
    }
  | {
      ok: false
      failure: AgentMessageFailure
    }

type TaskEventControllerOptions = {
  tasks: Record<string, AgentTask>
  eventsByTask: Record<string, AgentTaskEvent[]>
  dispatchTasks: Dispatch<TaskProjectionAction>
  setEventsByTask: Dispatch<
    SetStateAction<Record<string, AgentTaskEvent[]>>
  >
  setSelectedEventId: Dispatch<SetStateAction<string>>
  setError: Dispatch<SetStateAction<string>>
}

export function useTaskEventController({
  tasks,
  eventsByTask,
  dispatchTasks,
  setEventsByTask,
  setSelectedEventId,
  setError,
}: TaskEventControllerOptions) {
  const streamsRef = useRef(new TaskEventStreamRegistry())
  const hydratingRef = useRef(new Set<string>())
  const completedHydrationsRef = useRef(new Set<string>())
  const recoveryAttemptsRef = useRef(new Set<string>())
  const tasksRef = useRef(tasks)
  const eventsRef = useRef(eventsByTask)
  const refreshContextRef = useRef<(contextId: string) => void>(() => {})

  useEffect(() => {
    tasksRef.current = tasks
  }, [tasks])

  useEffect(() => {
    eventsRef.current = eventsByTask
  }, [eventsByTask])

  useEffect(() => {
    const streams = streamsRef.current
    const hydrating = hydratingRef.current
    const completed = completedHydrationsRef.current
    const recoveryAttempts = recoveryAttemptsRef.current
    return () => {
      streams.closeAll()
      hydrating.clear()
      completed.clear()
      recoveryAttempts.clear()
    }
  }, [])

  const setRefreshContextHandler = useCallback(
    (handler: (contextId: string) => void) => {
      refreshContextRef.current = handler
    },
    []
  )

  const applyA2ATaskSnapshot = useCallback(
    (snapshot: AgentA2ATask) => {
      dispatchTasks({ type: "snapshot", snapshot })
    },
    [dispatchTasks]
  )

  const reconcileA2ATaskSnapshot = useCallback(
    async (taskId: string) => {
      let snapshot: AgentA2ATask
      try {
        snapshot = await getAgentA2ATask(taskId)
      } catch (taskError) {
        setError(
          getRequestErrorMessage(taskError, "Failed to refresh task snapshot")
        )
        return null
      }

      applyA2ATaskSnapshot(snapshot)
      return snapshot
    },
    [applyA2ATaskSnapshot, setError]
  )

  const recordTaskEnvelope = useCallback(
    (envelope: AgentA2AStreamEnvelope): TaskEventProjection => {
      if (envelope.error) {
        return {
          ok: false,
          failure: errorFromA2AStreamEnvelope(envelope),
        }
      }

      const projectedEvent = a2aEnvelopeToTaskEvent(envelope)
      if (!projectedEvent) {
        return {
          ok: false,
          failure: {
            code: "invalid_a2a_stream",
            message: "The Task stream returned an invalid A2A event.",
            retryable: false,
          },
        }
      }

      const event = eventWithTaskThreadId(
        projectedEvent,
        tasksRef.current[projectedEvent.task_id]
      )
      const merged = mergeEvents(eventsRef.current[event.task_id] ?? [], [event])
      eventsRef.current = {
        ...eventsRef.current,
        [event.task_id]: merged,
      }
      setEventsByTask((previous) => ({
        ...previous,
        [event.task_id]: merged,
      }))
      setSelectedEventId(preferredEventId(merged))

      const finalAnswer = textFromA2AArtifact(envelope)
      dispatchTasks({
        type: "event",
        event,
        finalAnswer: finalAnswer || undefined,
      })
      return { ok: true, event, finalAnswer }
    },
    [dispatchTasks, setEventsByTask, setSelectedEventId]
  )

  const resetTaskHydration = useCallback((taskId: string) => {
    streamsRef.current.close(taskId)
    hydratingRef.current.delete(taskId)
    completedHydrationsRef.current.delete(taskId)
    recoveryAttemptsRef.current.delete(taskId)
  }, [])

  const hydrateTaskEvents = useCallback(
    (taskId: string, knownTerminal = false) => {
      if (
        !taskId ||
        hydratingRef.current.has(taskId) ||
        completedHydrationsRef.current.has(taskId)
      ) {
        return
      }

      hydratingRef.current.add(taskId)
      const afterEventId = Math.max(
        0,
        ...(eventsRef.current[taskId] ?? []).map((event) => event.event_id)
      )
      let finished = false
      let source: EventSource | null = null

      const taskIsTerminal = () =>
        knownTerminal || isTerminalStatus(tasksRef.current[taskId]?.status)

      const finish = (terminal = false) => {
        if (finished) return
        finished = true
        if (source) streamsRef.current.close(taskId, source)
        hydratingRef.current.delete(taskId)
        if (terminal) {
          completedHydrationsRef.current.add(taskId)
          recoveryAttemptsRef.current.delete(taskId)
        }
      }

      const refreshTaskContext = (fallbackContextId?: string) => {
        void reconcileA2ATaskSnapshot(taskId).finally(() => {
          const contextId =
            tasksRef.current[taskId]?.session_id || fallbackContextId
          if (contextId) refreshContextRef.current(contextId)
        })
      }

      const recoverStreamFailure = (streamFailure: AgentMessageFailure) => {
        if (finished) return
        finish()
        void reconcileA2ATaskSnapshot(taskId).then((snapshot) => {
          const snapshotStatus = snapshot
            ? taskStatusFromA2A(snapshot.status.state, snapshot.metadata)
            : null
          const contextId =
            snapshot?.contextId || tasksRef.current[taskId]?.session_id

          if (snapshotStatus && isTerminalStatus(snapshotStatus)) {
            completedHydrationsRef.current.add(taskId)
            recoveryAttemptsRef.current.delete(taskId)
            if (contextId) refreshContextRef.current(contextId)
          }

          dispatchTasks({
            type: "stream_error",
            taskId,
            error: streamFailure,
          })
          setError(streamFailure.message)
        })
      }

      source = openAgentA2ATaskEventStream(taskId, {
        after: afterEventId,
        onEvent: (envelope) => {
          const projection = recordTaskEnvelope(envelope)
          if (!projection.ok) {
            recoverStreamFailure(projection.failure)
            return
          }

          const { event } = projection
          if (event.status === "interrupted") {
            finish(false)
            refreshTaskContext(event.session_id)
            return
          }

          if (isTerminalStatus(event.status)) {
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

          const alreadyRecovered = recoveryAttemptsRef.current.has(taskId)
          finish()
          if (alreadyRecovered) return

          recoveryAttemptsRef.current.add(taskId)
          refreshTaskContext(tasksRef.current[taskId]?.session_id)
        },
        onProtocolError: (protocolError) => {
          recoverStreamFailure(
            failureFromRequestError(protocolError, protocolError.message)
          )
        },
        onDone: () => {
          finish(taskIsTerminal())
        },
      })
      streamsRef.current.replace(taskId, source)
    },
    [dispatchTasks, reconcileA2ATaskSnapshot, recordTaskEnvelope, setError]
  )

  return {
    applyA2ATaskSnapshot,
    hydrateTaskEvents,
    reconcileA2ATaskSnapshot,
    recordTaskEnvelope,
    resetTaskHydration,
    setRefreshContextHandler,
  }
}
