"use client"

import { useState, type ElementType } from "react"
import {
  Activity,
  Bot,
  ChevronDown,
  Network,
  TerminalSquare,
  Wrench,
} from "lucide-react"

import type { AgentA2AAgentCard } from "@/lib/agent/types"
import { cn } from "@/lib/utils"

export function MainAgentCardPanel({
  card,
}: {
  card: AgentA2AAgentCard | null
}) {
  const [expanded, setExpanded] = useState(false)
  const skills = Array.isArray(card?.skills) ? card.skills : []
  const routeKinds = [
    ...stringArrayFromMetadata(card?.metadata, "routeKinds"),
    ...stringArrayFromMetadata(card?.metadata, "route_kinds"),
  ]
  const executionModes = [
    ...stringArrayFromMetadata(card?.metadata, "executionModes"),
    ...stringArrayFromMetadata(card?.metadata, "execution_modes"),
  ]
  const inputModes = card?.defaultInputModes ?? []
  const outputModes = card?.defaultOutputModes ?? []
  const shownSkills = skills.slice(0, 3)
  const displayedRouteKinds = routeKinds.length
    ? routeKinds
    : ["local_message", "local_task", "remote_agent"]
  const displayedExecutionModes = executionModes.length
    ? executionModes
    : ["message", "task", "auto"]

  return (
    <div className="min-w-0 border-b border-[#E7E5E8] p-4">
      <div className="min-w-0 overflow-hidden rounded-[7px] border border-[#E7E5E8] bg-[#F8FAFC]">
        <div className="flex min-w-0 items-start justify-between gap-3 px-3 py-3">
          <div className="flex min-w-0 items-start gap-2">
            <Bot className="mt-0.5 h-4 w-4 shrink-0 text-[#54465C]" />
            <div className="min-w-0">
              <h2 className="m-0 truncate text-[13px] font-semibold leading-5">
                Main agent
              </h2>
              <p className="m-0 truncate text-[11px] leading-4 text-[#64748B]">
                Agent card summary
              </p>
            </div>
          </div>
          <div className="flex shrink-0 items-center gap-1.5">
            <span className="rounded-full bg-white px-2 py-1 text-[11px] font-medium leading-4 text-[#64748B]">
              v{card?.version ?? "-"}
            </span>
            <span className="rounded-full bg-white px-2 py-1 text-[11px] font-medium leading-4 text-[#64748B]">
              {skills.length} skills
            </span>
            <button
              className="flex h-7 w-7 items-center justify-center rounded-full bg-white text-[#64748B] transition hover:bg-[#EEF4FF] hover:text-[#1E3A8A] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#D6C2EA]"
              type="button"
              aria-expanded={expanded}
              aria-label={
                expanded ? "Collapse agent card" : "Expand agent card"
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
            <div className="mb-3 min-w-0">
              <p className="m-0 truncate text-[12px] font-semibold leading-5 text-[#1F0013]">
                {card?.name ?? "Vermay"}
              </p>
              <p className="m-0 mt-0.5 line-clamp-2 text-[11px] leading-4 text-[#64748B]">
                {card?.description ?? "Agent Card is not loaded yet."}
              </p>
            </div>

            <TokenRow
              icon={Network}
              label="Route modes"
              values={displayedRouteKinds}
            />
            <TokenRow
              icon={Activity}
              label="Execution"
              values={displayedExecutionModes}
            />
            <TokenRow
              icon={TerminalSquare}
              label="IO"
              values={
                [...inputModes, ...outputModes].length
                  ? [...inputModes, ...outputModes]
                  : ["text/plain"]
              }
            />

            <div className="mt-3 grid gap-2">
              {shownSkills.map((skill) => (
                <div
                  key={skill.id}
                  className="rounded-[5px] bg-[#F8FAFC] px-3 py-2"
                >
                  <div className="flex items-start gap-2">
                    <Wrench className="mt-0.5 h-3.5 w-3.5 shrink-0 text-[#1E3A8A]" />
                    <div className="min-w-0 flex-1">
                      <p className="m-0 truncate text-[12px] font-semibold leading-4 text-[#1F0013]">
                        {skill.name}
                      </p>
                      <p className="m-0 mt-0.5 line-clamp-2 text-[11px] leading-4 text-[#64748B]">
                        {skill.description}
                      </p>
                    </div>
                  </div>
                  {skill.tags && skill.tags.length > 0 && (
                    <div className="mt-2 flex min-w-0 flex-wrap gap-1">
                      {skill.tags.slice(0, 4).map((tag) => (
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
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

function TokenRow({
  icon: Icon,
  label,
  values,
}: {
  icon: ElementType
  label: string
  values: string[]
}) {
  const uniqueValues = Array.from(new Set(values.filter(Boolean)))

  return (
    <div className="mt-3">
      <div className="mb-1.5 flex items-center gap-1.5 text-[11px] font-semibold leading-4 text-[#54465C]">
        <Icon className="h-3.5 w-3.5" />
        {label}
      </div>
      <div className="flex min-w-0 flex-wrap gap-1">
        {uniqueValues.map((value) => (
          <span
            key={value}
            className="max-w-full break-all rounded-full bg-[#F1F5F9] px-2 py-0.5 text-[10px] font-medium leading-4 text-[#64748B]"
          >
            {value}
          </span>
        ))}
      </div>
    </div>
  )
}

function stringArrayFromMetadata(
  metadata: Record<string, unknown> | undefined,
  key: string
) {
  const value = metadata?.[key]
  return Array.isArray(value)
    ? value.filter((item): item is string => typeof item === "string")
    : []
}
