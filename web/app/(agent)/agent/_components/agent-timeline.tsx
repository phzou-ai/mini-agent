import React from "react"
import {
  AlertCircle,
  Check,
  Clock3,
  Database,
  MessageSquarePlus,
  Pause,
  Play,
  RefreshCcw,
  TerminalSquare,
} from "lucide-react"

import { eventKey, formatTime } from "@/lib/agent/task-presentation"
import type { AgentTask, AgentTaskEvent } from "@/lib/agent/types"
import { cn } from "@/lib/utils"

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

export function taskEventTitle(eventType: string) {
  return EVENT_LABELS[eventType]?.title ?? eventType
}

export function AgentTimeline({
  task,
  events,
  selectedEventId,
  onSelectEvent,
}: {
  task?: AgentTask
  events: AgentTaskEvent[]
  selectedEventId: string
  onSelectEvent: (eventId: string) => void
}) {
  return (
    <div className="p-5">
      <div className="mb-4 flex items-center justify-between">
        <h2 className="m-0 text-[14px] font-semibold leading-5">Timeline</h2>
        <span className="rounded-full bg-[#F1F5F9] px-2 py-1 text-[12px] text-[#64748B]">
          {task?.local_process_status ?? task?.status ?? "idle"}
        </span>
      </div>
      <div className="grid gap-3">
        {events.map((event) => {
          const key = eventKey(event)
          return (
            <TimelineEvent
              key={event.event_id}
              event={event}
              selected={key === selectedEventId}
              onSelect={() => onSelectEvent(key)}
            />
          )
        })}
        {!events.length && (
          <div className="rounded-[4px] border border-dashed border-[#CBD5E1] px-4 py-8 text-center text-[13px] leading-5 text-[#64748B]">
            Task events will appear here.
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
  const detail = [
    event.a2a_state ? `A2A: ${event.a2a_state}` : "",
    event.local_process_status
      ? `Process: ${event.local_process_status}`
      : "",
  ]
    .filter(Boolean)
    .join(" · ")

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
              {taskEventTitle(event.event_type)}
            </p>
            <span className="shrink-0 text-[11px] text-[#94A3B8]">
              {formatTime(event.created_at)}
            </span>
          </div>
          <p className="m-0 mt-1 line-clamp-2 text-[12px] leading-5 text-[#64748B]">
            {detail || config?.detail || event.status || "event"}
          </p>
        </div>
      </div>
    </button>
  )
}
