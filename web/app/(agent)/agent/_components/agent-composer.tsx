"use client"

import { useState } from "react"

import type {
  AgentA2AExecutionMode,
  AgentRegisteredAgent,
} from "@/lib/agent/types"
import { cn } from "@/lib/utils"

import {
  AGENT_GRADIENT,
  COMPOSER_ACTIVE_BORDER,
  COMPOSER_IDLE_BORDER,
  COMPOSER_MUTED_TEXT,
  COMPOSER_TEXT,
} from "@/app/(agent)/agent/_components/agent-console-theme"

export function AgentComposer({
  input,
  isGenerating,
  isCancelling = false,
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
  isCancelling?: boolean
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
  const isButtonActive = !isCancelling && (isGenerating || hasInput)
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
              "min-h-[106px] w-full resize-none bg-transparent pr-20 text-[14px] leading-5 outline-none disabled:cursor-not-allowed disabled:opacity-70",
              hasInput
                ? "text-[#1F0013] placeholder:text-[#1F0013]"
                : "text-[#54465C] placeholder:text-[#54465C]"
            )}
            style={{
              color: hasInput ? COMPOSER_TEXT : COMPOSER_MUTED_TEXT,
            }}
            data-testid="agent-composer-input"
            value={input}
            disabled={isCancelling}
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
                if (isGenerating || isCancelling) return
                onSend()
              }
            }}
          />
          {isCancelling && (
            <p className="m-0 mt-2 text-[12px] leading-5 text-[#7C3AED]">
              Cancellation requested. Waiting for the current operation to reach a safe boundary.
            </p>
          )}
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
            disabled={isCancelling || (!isGenerating && !hasInput)}
            aria-label={
              isCancelling
                ? "Cancellation requested"
                : isGenerating
                  ? "Stop generating"
                  : "Send"
            }
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
