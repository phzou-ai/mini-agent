import type {
  AgentA2AAgentCard,
  AgentA2AMessagePayload,
  AgentA2ASendResult,
  AgentA2ATask,
  AgentContextRecord,
  AgentContextTaskRecord,
  AgentDelegation,
  AgentModelConfig,
  AgentMessageIngress,
  AgentRegisteredAgent,
  AgentRegisteredAgentPayload,
  AgentRouteDecision,
  AgentStoredMessage,
} from "@/lib/agent/types"
import { requestDelete, requestGet, requestPatch, requestPost } from "@/lib/request"

export type AgentContextReadPage = {
  limit?: number
  offset?: number
}

function contextReadQuery(page?: AgentContextReadPage) {
  const params = new URLSearchParams()
  if (page?.limit !== undefined) params.set("limit", String(page.limit))
  if (page?.offset !== undefined) params.set("offset", String(page.offset))
  const query = params.toString()
  return query ? `?${query}` : ""
}

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

export function listAgentContextMessages(
  contextId: string,
  page?: AgentContextReadPage
) {
  return requestGet<AgentStoredMessage[]>(
    `/api/bff/agent/contexts/${encodeURIComponent(contextId)}/messages${contextReadQuery(page)}`
  )
}

export function getAgentMessageIngress(messageId: string) {
  return requestGet<AgentMessageIngress>(
    `/api/bff/agent/message-ingress/${encodeURIComponent(messageId)}`
  )
}

export function listAgentContextTasks(
  contextId: string,
  page?: AgentContextReadPage
) {
  return requestGet<AgentContextTaskRecord[]>(
    `/api/bff/agent/contexts/${encodeURIComponent(contextId)}/tasks${contextReadQuery(page)}`
  )
}

export function retryAgentTask(taskId: string) {
  return requestPost<AgentContextTaskRecord>(
    `/api/bff/agent/tasks/${encodeURIComponent(taskId)}/retry`,
    {}
  )
}

export function listAgentContextRouteDecisions(
  contextId: string,
  page?: AgentContextReadPage
) {
  return requestGet<AgentRouteDecision[]>(
    `/api/bff/agent/contexts/${encodeURIComponent(contextId)}/route-decisions${contextReadQuery(page)}`
  )
}

export function listAgentContextDelegations(
  contextId: string,
  page?: AgentContextReadPage
) {
  return requestGet<AgentDelegation[]>(
    `/api/bff/agent/contexts/${encodeURIComponent(contextId)}/delegations${contextReadQuery(page)}`
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
