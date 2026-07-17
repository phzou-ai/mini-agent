export type AgentTaskStatus =
  | "active"
  | "created"
  | "queued"
  | "running"
  | "interrupted"
  | "cancel_request"
  | "cancel_requested"
  | "canceled"
  | "cancelled"
  | "completed"
  | "stopped"
  | "failed"
  | "unknown"

export type AgentSession = {
  session_id: string
  context_id?: string | null
  title?: string | null
  status: AgentTaskStatus
  metadata: Record<string, unknown>
  created_at: string
  updated_at: string
}

export type AgentModelSelection = {
  name: string
  provider: string
  model?: string | null
  base_url?: string | null
  timeout_seconds?: number | string | null
}

export type AgentModelConfig = {
  primary_model: AgentModelSelection
  router_model: AgentModelSelection
  router_model_overridden: boolean
  config_path: string
}

export type AgentTaskError = {
  code: string
  message: string
}

export type AgentTask = {
  task_id: string
  session_id: string
  thread_id: string
  root_task_id?: string | null
  retry_of_task_id?: string | null
  status: AgentTaskStatus
  input: string
  attempt: number
  final_answer?: string | null
  interrupt?: unknown
  interrupt_message?: string | null
  stop_message?: string | null
  error?: AgentTaskError | null
  model?: Record<string, unknown> | null
  max_loops?: number | null
  mcp?: Record<string, unknown> | null
  metadata?: Record<string, unknown> | null
  created_at: string
  updated_at: string
}

export type AgentTaskEvent = {
  event_id: number
  task_id: string
  session_id: string
  context_id?: string | null
  thread_id?: string | null
  event_type: string
  status?: AgentTaskStatus | null
  payload: Record<string, unknown>
  created_at: string
}

export type AgentMessageRole = "user" | "assistant" | "system"

export type AgentMessage = {
  id: string
  role: AgentMessageRole
  content: string
  createdAt: string
  taskId?: string | null
  loading?: boolean
}

export type AgentA2AExecutionMode = "message" | "task" | "auto"
export type AgentA2ARoute = "local_message" | "local_task" | "remote_agent"

export type AgentA2AAgentSkill = {
  id: string
  name: string
  description: string
  tags?: string[]
  examples?: string[]
}

export type AgentA2AAgentCard = {
  name: string
  description: string
  url: string
  version: string
  protocolVersions?: string[]
  capabilities?: {
    streaming?: boolean
    pushNotifications?: boolean
    extendedAgentCard?: boolean
    [key: string]: unknown
  }
  defaultInputModes?: string[]
  defaultOutputModes?: string[]
  skills: AgentA2AAgentSkill[]
  securitySchemes?: Record<string, unknown>
  security?: Array<Record<string, unknown>>
  metadata?: Record<string, unknown>
}

export type AgentA2AMessagePayload = {
  contextId?: string
  messageId?: string
  text: string
  executionMode?: AgentA2AExecutionMode
  route?: AgentA2ARoute
  targetAgentId?: string
}

export type AgentA2APart = {
  kind?: string
  text?: string
  [key: string]: unknown
}

export type AgentA2AMessage = {
  kind: "message"
  role: "user" | "agent" | "system"
  messageId: string
  contextId: string
  parts: AgentA2APart[]
  metadata?: Record<string, unknown>
}

export type AgentA2ATask = {
  kind: "task"
  id: string
  contextId: string
  status: {
    state: string
    timestamp?: string
  }
  metadata?: Record<string, unknown>
}

export type AgentA2ASendResult =
  | {
      kind: "message"
      contextId: string
      message: AgentA2AMessage
      raw: unknown
    }
  | {
      kind: "task"
      contextId: string
      task: AgentA2ATask
      raw: unknown
    }

export type AgentA2AStatusUpdateEvent = {
  kind: "status-update"
  taskId: string
  contextId: string
  status: {
    state: string
    timestamp?: string
  }
  final?: boolean
  metadata?: Record<string, unknown>
}

export type AgentA2AArtifactUpdateEvent = {
  kind: "artifact-update"
  taskId: string
  contextId: string
  artifact: {
    artifactId: string
    name?: string
    parts: AgentA2APart[]
    metadata?: Record<string, unknown>
  }
  append?: boolean
  lastChunk?: boolean
  metadata?: Record<string, unknown>
}

export type AgentA2AStreamEnvelope = {
  jsonrpc: "2.0"
  id?: unknown
  result?:
    | AgentA2AStatusUpdateEvent
    | AgentA2AArtifactUpdateEvent
    | AgentA2AMessage
    | AgentA2ATask
  error?: {
    code?: unknown
    message?: unknown
    data?: unknown
  }
}

export type AgentContextRecord = {
  context_id: string
  title?: string | null
  metadata: Record<string, unknown>
  created_at: string
  updated_at: string
}

export type AgentStoredMessage = {
  message_id: string
  context_id: string
  role: "user" | "agent" | "system"
  parts: AgentA2APart[]
  task_id?: string | null
  metadata: Record<string, unknown>
  created_at: string
}

export type AgentContextTaskRecord = {
  task_id: string
  context_id: string
  status: AgentTaskStatus
  input_message_id: string
  output_message_id?: string | null
  runtime_thread_id: string
  assigned_agent_id?: string | null
  retry_of_task_id?: string | null
  attempt: number
  model?: Record<string, unknown> | null
  max_loops?: number | null
  mcp?: Record<string, unknown> | null
  error_code?: string | null
  error_message?: string | null
  created_at: string
  updated_at: string
}

export type AgentRouteDecision = {
  decision_id: string
  context_id: string
  message_id: string
  kind: "local_message" | "local_task" | "remote_agent"
  reason: string
  confidence?: number | null
  target_agent_id?: string | null
  metadata: Record<string, unknown>
  created_at: string
}

export type AgentDelegation = {
  delegation_id: string
  context_id: string
  input_message_id: string
  route_decision_id: string
  remote_agent_id: string
  local_task_id?: string | null
  remote_task_id?: string | null
  remote_context_id?: string | null
  remote_message_id?: string | null
  result_kind: string
  status: string
  metadata: Record<string, unknown>
  created_at: string
  updated_at: string
}

export type AgentRegisteredAgent = {
  agent_id: string
  name: string
  card_url: string
  card_json: Record<string, unknown>
  enabled: boolean
  metadata: Record<string, unknown>
  created_at: string
  updated_at: string
}

export type AgentRegisteredAgentPayload = {
  agent_id: string
  name: string
  card_url: string
  card_json?: Record<string, unknown>
  enabled?: boolean
  metadata?: Record<string, unknown>
}
