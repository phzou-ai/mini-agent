import type {
  AgentA2AAgentCard,
  AgentA2AMessagePayload,
  AgentA2ASendResult,
  AgentA2ATask,
  AgentContextRecord,
  AgentContextTaskRecord,
  AgentDelegation,
  AgentModelConfig,
  AgentRegisteredAgent,
  AgentRegisteredAgentPayload,
  AgentRouteDecision,
  AgentStoredMessage,
} from "@/lib/agent/types"
import { requestDelete, requestGet, requestPatch, requestPost } from "@/lib/request"

export function getAgentA2AAgentCard() {
  return requestGet<AgentA2AAgentCard>("/api/bff/agent/a2a/agent-card")
}

export function getAgentModelConfig() {
  return requestGet<AgentModelConfig>("/api/bff/agent/model-config")
}

export function sendAgentA2AMessage(payload: AgentA2AMessagePayload) {
  return requestPost<AgentA2ASendResult>("/api/bff/agent/a2a/message", payload)
}

export function submitAgentA2ATaskInput(
  taskId: string,
  contextId: string,
  text: string,
  metadata?: Record<string, unknown>
) {
  return sendAgentA2AMessage({ taskId, contextId, text, metadata })
}

export function getAgentA2ATask(taskId: string) {
  return requestGet<AgentA2ATask>(
    `/api/bff/agent/a2a/tasks/${encodeURIComponent(taskId)}`
  )
}

export function cancelAgentA2ATask(taskId: string, reason?: string) {
  return requestPost<AgentA2ATask>(
    `/api/bff/agent/a2a/tasks/${encodeURIComponent(taskId)}/cancel`,
    { reason }
  )
}

export function resumeAgentA2ATask(
  taskId: string,
  approved: boolean,
  reason?: string
) {
  return requestPost<AgentA2ATask>(
    `/api/bff/agent/a2a/tasks/${encodeURIComponent(taskId)}/resume`,
    { approved, reason }
  )
}

export function listAgentContexts() {
  return requestGet<AgentContextRecord[]>("/api/bff/agent/contexts")
}

export function getAgentContext(contextId: string) {
  return requestGet<AgentContextRecord>(
    `/api/bff/agent/contexts/${encodeURIComponent(contextId)}`
  )
}

export function deleteAgentContext(contextId: string, force = false) {
  const params = force ? "?force=true" : ""
  return requestDelete<void>(
    `/api/bff/agent/contexts/${encodeURIComponent(contextId)}${params}`
  )
}

export function updateAgentContext(contextId: string, payload: { title: string }) {
  return requestPatch<AgentContextRecord>(
    `/api/bff/agent/contexts/${encodeURIComponent(contextId)}`,
    payload
  )
}

export function listAgentContextMessages(contextId: string, limit?: number) {
  const params = new URLSearchParams()
  if (limit !== undefined) {
    params.set("limit", String(limit))
  }
  const query = params.toString()
  return requestGet<AgentStoredMessage[]>(
    `/api/bff/agent/contexts/${encodeURIComponent(contextId)}/messages${query ? `?${query}` : ""}`
  )
}

export function listAgentContextTasks(contextId: string) {
  return requestGet<AgentContextTaskRecord[]>(
    `/api/bff/agent/contexts/${encodeURIComponent(contextId)}/tasks`
  )
}

export function listAgentContextRouteDecisions(contextId: string) {
  return requestGet<AgentRouteDecision[]>(
    `/api/bff/agent/contexts/${encodeURIComponent(contextId)}/route-decisions`
  )
}

export function listAgentContextDelegations(contextId: string) {
  return requestGet<AgentDelegation[]>(
    `/api/bff/agent/contexts/${encodeURIComponent(contextId)}/delegations`
  )
}

export function listAgentRegisteredAgents(enabledOnly = false) {
  const params = enabledOnly ? "?enabled_only=true" : ""
  return requestGet<AgentRegisteredAgent[]>(
    `/api/bff/agent/registered-agents${params}`
  )
}

export function upsertAgentRegisteredAgent(
  payload: AgentRegisteredAgentPayload
) {
  return requestPost<AgentRegisteredAgent>(
    "/api/bff/agent/registered-agents",
    payload
  )
}

export function refreshAgentRegisteredAgent(agentId: string) {
  return requestPost<AgentRegisteredAgent>(
    `/api/bff/agent/registered-agents/${encodeURIComponent(agentId)}/refresh-card`,
    {}
  )
}

export function deleteAgentRegisteredAgent(agentId: string) {
  return requestDelete<void>(
    `/api/bff/agent/registered-agents/${encodeURIComponent(agentId)}`
  )
}
