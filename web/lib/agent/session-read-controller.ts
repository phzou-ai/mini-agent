import {
  listAgentContextDelegations,
  listAgentContextMessages,
  listAgentContextRouteDecisions,
  listAgentContextTasks,
} from "@/lib/agent/client"
import type {
  AgentContextTaskRecord,
  AgentDelegation,
  AgentRouteDecision,
  AgentStoredMessage,
} from "@/lib/agent/types"

export const AGENT_SESSION_READ_LIMIT = 200

const firstPage = {
  limit: AGENT_SESSION_READ_LIMIT,
  offset: 0,
} as const

export type AgentSessionDiagnostics = {
  routeDecisions: AgentRouteDecision[]
  delegations: AgentDelegation[]
}

export type AgentSessionReadModel = AgentSessionDiagnostics & {
  messages: AgentStoredMessage[]
  tasks: AgentContextTaskRecord[]
}

export async function loadAgentSessionDiagnostics(
  contextId: string
): Promise<AgentSessionDiagnostics> {
  const [routeDecisions, delegations] = await Promise.all([
    listAgentContextRouteDecisions(contextId, firstPage),
    listAgentContextDelegations(contextId, firstPage),
  ])
  return { routeDecisions, delegations }
}

export async function loadAgentSessionReadModel(
  contextId: string
): Promise<AgentSessionReadModel> {
  const [messages, tasks, diagnostics] = await Promise.all([
    listAgentContextMessages(contextId, firstPage),
    listAgentContextTasks(contextId, firstPage),
    loadAgentSessionDiagnostics(contextId),
  ])
  return { messages, tasks, ...diagnostics }
}
