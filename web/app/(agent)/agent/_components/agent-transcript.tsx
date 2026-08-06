"use client"

import { useEffect, useRef, useState } from "react"
import {
  AlertCircle,
  Bot,
  Check,
  Copy,
  RefreshCcw,
  X,
} from "lucide-react"
import ReactMarkdown from "react-markdown"
import remarkGfm from "remark-gfm"

import {
  formatTime,
  isApprovalRequiredStatus,
  isGeneralInputRequiredTask,
  isMessageDisplayTask,
  taskActivityLabel,
  taskFailureForDisplay,
  taskInputRequest,
} from "@/lib/agent/task-presentation"
import type {
  AgentMessage,
  AgentMessageFailure,
  AgentTask,
} from "@/lib/agent/types"
import { cn } from "@/lib/utils"

export function AgentTranscript({
  messages,
  tasks,
  selectedMessageId,
  copiedMessageId,
  busy,
  onCopyMessage,
  onSelectMessage,
  onRetryMessage,
  onRetryTask,
  onResumeTask,
  onSubmitTaskInput,
  resumingTaskId,
  submittingTaskInputId,
  retryingTaskId,
}: {
  messages: AgentMessage[]
  tasks: Record<string, AgentTask>
  selectedMessageId: string
  copiedMessageId: string
  busy: boolean
  onCopyMessage: (message: AgentMessage) => void
  onSelectMessage: (message: AgentMessage) => void
  onRetryMessage: (message: AgentMessage) => void
  onRetryTask: (taskId: string) => void
  onResumeTask: (taskId: string, approved: boolean) => void
  onSubmitTaskInput: (taskId: string, value: string) => void
  resumingTaskId: string
  submittingTaskInputId: string
  retryingTaskId: string
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
                retryingTask={message.taskId === retryingTaskId}
                onSelect={() => onSelectMessage(message)}
                onCopy={() => onCopyMessage(message)}
                onRetry={() => onRetryMessage(message)}
                onRetryTask={() => {
                  if (message.taskId) onRetryTask(message.taskId)
                }}
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
  retryingTask,
  onSelect,
  onCopy,
  onRetry,
  onRetryTask,
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
  retryingTask: boolean
  onSelect: () => void
  onCopy: () => void
  onRetry: () => void
  onRetryTask: () => void
  onResume: (approved: boolean) => void
  onSubmitInput: (value: string) => void
}) {
  const isUser = message.role === "user"
  const isDirectMessageFailure = Boolean(message.failure)
  const taskFailure = taskFailureForDisplay(task)
  const isTaskFailure = Boolean(
    !isUser && !isDirectMessageFailure && taskFailure && !message.content
  )
  const isInputPending = Boolean(
    !isUser &&
      !isDirectMessageFailure &&
      task &&
      isApprovalRequiredStatus(task.status) &&
      !message.content
  )
  const isLoadingOnly =
    message.loading &&
    !message.content &&
    !isInputPending &&
    !isDirectMessageFailure &&
    !isTaskFailure
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
                    "max-w-[844px] rounded-[0_4px_4px_4px] border bg-white text-[#1F0013]",
                    isDirectMessageFailure || isTaskFailure
                      ? "border-[#FCA5A5] bg-[#FFF7F7]"
                      : "border-[#E7E5E8]",
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
            {isDirectMessageFailure && message.failure ? (
              <DirectMessageFailureCard
                failure={message.failure}
                retrying={busy}
                onRetry={onRetry}
              />
            ) : isTaskFailure && taskFailure ? (
              <TaskFailureCard
                failure={taskFailure}
                retrying={retryingTask}
                onRetry={onRetryTask}
              />
            ) : isInputPending && task ? (
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
            {isDirectMessageFailure && message.failure && (
              <span className="rounded-full bg-[#FEE2E2] px-2 py-0.5 text-[11px] font-medium leading-4 text-[#B91C1C]">
                {message.failure.retryable
                  ? "message failed · retryable"
                  : "message failed"}
              </span>
            )}
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
                {hasTaskEvents
                  ? `task · ${taskActivityLabel(task.status)}`
                  : "message"}
              </button>
            )}
            {busy && message.loading && !isInputPending && (
              <span className="rounded-full bg-[#F1F5F9] px-2 py-0.5 text-[11px] font-medium leading-4 text-[#64748B]">
                Updating
              </span>
            )}
            {message.content && !isDirectMessageFailure && (
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

function TaskFailureCard({
  failure,
  retrying,
  onRetry,
}: {
  failure: NonNullable<AgentTask["error"]>
  retrying: boolean
  onRetry: () => void
}) {
  return (
    <div
      className="flex min-w-0 items-start gap-2.5"
      data-testid="agent-task-failure"
    >
      <AlertCircle className="mt-0.5 h-4 w-4 shrink-0 text-[#B91C1C]" />
      <div className="min-w-0">
        <p className="m-0 text-[13px] font-semibold leading-5 text-[#7F1D1D]">
          Task failed
        </p>
        <p className="m-0 mt-1 break-words text-[13px] leading-5 text-[#7F1D1D] [overflow-wrap:anywhere]">
          {failure.message}
        </p>
        <div className="mt-2 flex flex-wrap items-center gap-2">
          <span className="inline-flex rounded-full bg-[#FEE2E2] px-2 py-0.5 font-mono text-[10px] leading-4 text-[#B91C1C]">
            {failure.code}
          </span>
          {failure.retryable && (
            <button
              className="inline-flex h-7 items-center gap-1.5 rounded-[4px] border border-[#FCA5A5] bg-white px-2.5 text-[11px] font-semibold text-[#B91C1C] transition hover:border-[#DC2626] hover:bg-[#FEF2F2] disabled:cursor-not-allowed disabled:opacity-60"
              type="button"
              disabled={retrying}
              data-testid="agent-task-retry"
              onClick={(event) => {
                event.stopPropagation()
                onRetry()
              }}
            >
              <RefreshCcw className="h-3.5 w-3.5" />
              {retrying ? "Retrying..." : "Retry"}
            </button>
          )}
        </div>
      </div>
    </div>
  )
}

function DirectMessageFailureCard({
  failure,
  retrying,
  onRetry,
}: {
  failure: AgentMessageFailure
  retrying: boolean
  onRetry: () => void
}) {
  return (
    <div
      className="flex min-w-0 items-start gap-2.5"
      data-testid="agent-direct-message-failure"
    >
      <AlertCircle className="mt-0.5 h-4 w-4 shrink-0 text-[#B91C1C]" />
      <div className="min-w-0">
        <p className="m-0 text-[13px] font-semibold leading-5 text-[#7F1D1D]">
          Request failed
        </p>
        <p className="m-0 mt-1 break-words text-[13px] leading-5 text-[#7F1D1D] [overflow-wrap:anywhere]">
          {failure.message}
        </p>
        <div className="mt-2 flex flex-wrap items-center gap-2">
          <span className="inline-flex rounded-full bg-[#FEE2E2] px-2 py-0.5 font-mono text-[10px] leading-4 text-[#B91C1C]">
            {failure.code}
          </span>
          {failure.retryable && (
            <button
              className="inline-flex h-7 items-center gap-1.5 rounded-[4px] border border-[#FCA5A5] bg-white px-2.5 text-[11px] font-semibold text-[#B91C1C] transition hover:border-[#DC2626] hover:bg-[#FEF2F2] disabled:cursor-not-allowed disabled:opacity-60"
              type="button"
              disabled={retrying}
              data-testid="agent-direct-message-retry"
              onClick={(event) => {
                event.stopPropagation()
                onRetry()
              }}
            >
              <RefreshCcw className="h-3.5 w-3.5" />
              {retrying ? "Retrying..." : "Retry"}
            </button>
          )}
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
        <div
          className="mb-3 flex flex-wrap gap-2"
          data-testid="agent-task-input-choices"
        >
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

function TypingIndicator() {
  return (
    <span className="inline-flex h-3 items-center gap-1">
      <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-[#94A3B8]" />
      <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-[#94A3B8] [animation-delay:140ms]" />
      <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-[#94A3B8] [animation-delay:280ms]" />
    </span>
  )
}
