import { NextResponse } from "next/server"

import { normalizeAgentError } from "@/lib/agent/error-contract"

export type A2AJsonRpcResponse = {
  jsonrpc?: string
  id?: unknown
  result?: unknown
  status?: unknown
  message?: unknown
  code?: unknown
  error?: {
    code?: unknown
    message?: unknown
    data?: unknown
  }
}

export async function readJsonRpcResponse(
  response: Response
): Promise<A2AJsonRpcResponse> {
  return (await response.json().catch(() => ({}))) as A2AJsonRpcResponse
}

export function isJsonRpcMethodNotFound(body: A2AJsonRpcResponse) {
  return (
    body.code === "method_not_found" ||
    body.message === "JSON-RPC method not found." ||
    body.error?.code === -32601 ||
    body.error?.message === "JSON-RPC method not found."
  )
}

export function staleResumeRouteResponse() {
  return NextResponse.json(
    {
      code: "a2a_resume_method_not_found",
      message:
        "The running Vermay Agent API does not support task resume yet. Restart `vermay-agent serve` so the A2A ResumeTask route is loaded.",
      retryable: false,
    },
    { status: 502 }
  )
}

export function jsonRpcResultResponse(
  body: A2AJsonRpcResponse,
  {
    invalidMessage,
    mapResult,
  }: {
    invalidMessage: string
    mapResult?: (
      result: Record<string, unknown>,
      body: A2AJsonRpcResponse
    ) => Response | unknown
  }
) {
  if (body.error) {
    return NextResponse.json(normalizeAgentError(502, body), { status: 502 })
  }

  if (body.result && typeof body.result === "object") {
    const result = body.result as Record<string, unknown>
    const mapped = mapResult ? mapResult(result, body) : result
    if (mapped instanceof Response) {
      return mapped
    }
    return NextResponse.json(mapped)
  }

  return NextResponse.json(
    {
      code: "invalid_a2a_response",
      message: invalidMessage,
      retryable: false,
    },
    { status: 502 }
  )
}
