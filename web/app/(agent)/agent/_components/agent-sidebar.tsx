"use client"

import {
  Bot,
  Check,
  Database,
  History,
  Menu,
  MessageSquarePlus,
  Pencil,
  Trash2,
  X,
} from "lucide-react"

import { AGENT_GRADIENT } from "@/app/(agent)/agent/_components/agent-console-theme"
import {
  formatDateTime,
  isActiveStatus,
  isCancellationRequestedStatus,
} from "@/lib/agent/task-presentation"
import type {
  AgentModelConfig,
  AgentSession,
  AgentTaskStatus,
} from "@/lib/agent/types"
import { cn } from "@/lib/utils"

type AgentSidebarProps = {
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
}

export function AgentSidebar({
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
}: AgentSidebarProps) {
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

function StatusDot({ status }: { status: AgentTaskStatus }) {
  const tone = isCancellationRequestedStatus(status)
    ? "bg-[#A855F7]"
    : isActiveStatus(status)
      ? "bg-[#3768C7]"
      : status === "completed"
        ? "bg-[#16A34A]"
        : status === "canceled" ||
            status === "cancelled" ||
            status === "stopped"
          ? "bg-[#F97316]"
          : status === "failed"
            ? "bg-[#DC2626]"
            : "bg-[#CBD5E1]"
  return (
    <span className="mt-[5px] flex h-3 w-3 shrink-0 items-center justify-center rounded-full bg-[#EEF2FF]">
      <span className={cn("h-1.5 w-1.5 rounded-full", tone)} />
    </span>
  )
}
