"use client"

import { Sparkles } from "lucide-react"

import type {
  AgentA2AExecutionMode,
  AgentRegisteredAgent,
  AgentSession,
} from "@/lib/agent/types"

import { AgentComposer } from "@/app/(agent)/agent/_components/agent-composer"
import { AGENT_GRADIENT } from "@/app/(agent)/agent/_components/agent-console-theme"

const SUGGESTIONS = [
  "Inspect why the latest tool call failed",
  "Compare memory retrieval across two tasks",
  "Design a SQLite task event schema",
]

export function AgentWelcomePanel({
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
            Vermay
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
          {SUGGESTIONS.map((suggestion) => (
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
        <AgentComposer
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
