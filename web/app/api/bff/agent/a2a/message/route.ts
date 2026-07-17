import { NextResponse, type NextRequest } from "next/server"

import { buildA2ARpcMessageSendEnvelope } from "@/lib/agent/a2a"
import { jsonRpcResultResponse, readJsonRpcResponse } from "@/lib/agent/bff-rpc"
import { buildAgentRootPath, proxyAgentRootJson } from "@/lib/agent/server"
import type { AgentA2AMessagePayload } from "@/lib/agent/types"

export async function POST(request: NextRequest) {
  const payload = (await request.json()) as AgentA2AMessagePayload
  const upstreamPayload = buildA2ARpcMessageSendEnvelope(payload)

  const upstream = await proxyAgentRootJson(buildAgentRootPath("/rpc"), {
    method: "POST",
    body: JSON.stringify(upstreamPayload),
  })

  if (!upstream.ok) {
    return upstream
  }

  const body = await readJsonRpcResponse(upstream)
  return jsonRpcResultResponse(body, {
    invalidMessage: "Invalid A2A response",
    mapResult: (result) => {
      if (result.kind === "message") {
        return {
          kind: "message",
          contextId: result.contextId,
          message: result,
          raw: body,
        }
      }
      if (result.kind === "task") {
        return {
          kind: "task",
          contextId: result.contextId,
          task: result,
          raw: body,
        }
      }
      return NextResponse.json(
        {
          code: "unsupported_a2a_result",
          message: "Unsupported A2A result kind.",
          retryable: false,
        },
        { status: 502 }
      )
    },
  })
}
