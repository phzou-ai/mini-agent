import {
  isTerminalStatus,
  mergeTaskProjection,
  mergeTaskWithA2ASnapshot,
  taskErrorFromA2AEvent,
  taskStateProjection,
} from "@/lib/agent/task-presentation"
import type {
  AgentA2ATask,
  AgentMessageFailure,
  AgentTask,
  AgentTaskEvent,
} from "@/lib/agent/types"

export type TaskProjectionState = Record<string, AgentTask>

export type TaskProjectionAction =
  | { type: "upsert"; task: AgentTask }
  | { type: "upsert_many"; tasks: AgentTask[] }
  | { type: "replace"; removeTaskId: string; task: AgentTask }
  | { type: "snapshot"; snapshot: AgentA2ATask }
  | { type: "event"; event: AgentTaskEvent; finalAnswer?: string }
  | { type: "stream_error"; taskId: string; error: AgentMessageFailure }
  | { type: "remove"; taskIds: string[] }

function upsertTask(
  state: TaskProjectionState,
  task: AgentTask
): TaskProjectionState {
  const current = state[task.task_id]
  const merged = current ? mergeTaskProjection(current, task) : task
  if (merged === current) return state
  return { ...state, [task.task_id]: merged }
}

function metadataFromEvent(event: AgentTaskEvent) {
  const metadata = event.payload.metadata
  return metadata && typeof metadata === "object" && !Array.isArray(metadata)
    ? (metadata as Record<string, unknown>)
    : {}
}

function applyTaskEvent(
  state: TaskProjectionState,
  event: AgentTaskEvent,
  finalAnswer?: string
): TaskProjectionState {
  const current = state[event.task_id]
  if (!current) return state

  const eventMetadata = metadataFromEvent(event)
  const status = event.status ?? current.status
  const stateProjection = taskStateProjection(
    event.a2a_state,
    eventMetadata,
    event.local_process_status ?? current.local_process_status
  )
  const candidate: AgentTask = {
    ...current,
    lifecycle_revision: event.lifecycle_revision,
    a2a_state: stateProjection.a2a_state,
    local_process_status: stateProjection.local_process_status,
    status,
    final_answer: finalAnswer ?? current.final_answer,
    updated_at: event.created_at,
    error:
      event.status === "failed"
        ? taskErrorFromA2AEvent(event) ?? current.error
        : event.status
          ? null
          : current.error,
    stream_error: isTerminalStatus(status) ? null : current.stream_error,
    metadata: {
      ...(current.metadata ?? {}),
      ...eventMetadata,
    },
  }
  const merged = mergeTaskProjection(current, candidate)
  if (merged === current) return state
  return { ...state, [event.task_id]: merged }
}

export function taskProjectionReducer(
  state: TaskProjectionState,
  action: TaskProjectionAction
): TaskProjectionState {
  switch (action.type) {
    case "upsert":
      return upsertTask(state, action.task)
    case "upsert_many":
      return action.tasks.reduce(upsertTask, state)
    case "replace": {
      const withoutPending = { ...state }
      delete withoutPending[action.removeTaskId]
      return upsertTask(withoutPending, action.task)
    }
    case "snapshot": {
      const current = state[action.snapshot.id]
      if (!current) return state
      const merged = mergeTaskWithA2ASnapshot(current, action.snapshot)
      return merged === current
        ? state
        : { ...state, [action.snapshot.id]: merged }
    }
    case "event":
      return applyTaskEvent(state, action.event, action.finalAnswer)
    case "stream_error": {
      const current = state[action.taskId]
      if (!current) return state
      return {
        ...state,
        [action.taskId]: { ...current, stream_error: action.error },
      }
    }
    case "remove": {
      const next = { ...state }
      for (const taskId of action.taskIds) delete next[taskId]
      return next
    }
  }
}
