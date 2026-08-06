export type AgentErrorContract = {
  code: string
  message: string
  retryable: boolean
}

type ErrorFallback = {
  code?: string
  message?: string
  retryable?: boolean
}

export function normalizeAgentError(
  status: number,
  payload: unknown,
  fallback: ErrorFallback = {}
): AgentErrorContract {
  const direct = asRecord(payload)
  const detail = asRecord(direct?.detail)
  const rpcError = asRecord(direct?.error)
  const rpcData = asRecord(rpcError?.data)
  const rpcErrorInfo = asRecord(rpcData?.errorInfo)
  const rpcMetadata = asRecord(rpcErrorInfo?.metadata)

  const message = firstString(
    direct?.message,
    detail?.message,
    rpcError?.message,
    typeof direct?.detail === "string" ? direct.detail : undefined,
    fallback.message
  )
  const code = firstString(
    direct?.code,
    detail?.code,
    rpcData?.localCode,
    rpcErrorInfo?.reason,
    rpcMetadata?.localCode,
    fallback.code
  )
  const retryable = firstBoolean(
    direct?.retryable,
    detail?.retryable,
    rpcData?.retryable,
    rpcMetadata?.retryable,
    fallback.retryable
  )

  return {
    code: code || defaultCode(status),
    message: message || defaultMessage(status),
    retryable: retryable ?? status >= 500,
  }
}

export function agentApiUnavailableError(): AgentErrorContract {
  return {
    code: "agent_api_unavailable",
    message: "Vermay API is unavailable.",
    retryable: true,
  }
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object"
    ? (value as Record<string, unknown>)
    : null
}

function firstString(...values: unknown[]) {
  for (const value of values) {
    if (typeof value === "string" && value.trim()) return value.trim()
  }
  return ""
}

function firstBoolean(...values: unknown[]) {
  for (const value of values) {
    if (typeof value === "boolean") return value
  }
  return undefined
}

function defaultCode(status: number) {
  if (status === 400 || status === 422) return "invalid_request"
  if (status === 403) return "permission_error"
  if (status === 404) return "not_found"
  if (status === 409) return "invalid_state"
  if (status >= 500) return "upstream_error"
  return "request_error"
}

function defaultMessage(status: number) {
  if (status === 400 || status === 422) return "The request is invalid."
  if (status === 403) return "The operation is not permitted."
  if (status === 404) return "The requested resource was not found."
  if (status === 409) return "The operation conflicts with the current state."
  if (status >= 500) return "Vermay could not complete the request."
  return `Request failed (${status}).`
}
