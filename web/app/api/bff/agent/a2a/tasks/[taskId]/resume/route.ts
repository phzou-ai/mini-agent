import { NextResponse, type NextRequest } from "next/server"

import { buildA2ARpcTaskResumeEnvelope } from "@/lib/agent/a2a"
import {
  isJsonRpcMethodNotFound,
  jsonRpcResultResponse,
  readJsonRpcResponse,
  staleResumeRouteResponse,
} from "@/lib/agent/bff-rpc"
import { buildAgentRootPath, proxyAgentRootJson } from "@/lib/agent/server"

export async function POST(
  request: NextRequest,
  { params }: { params: Promise<{ taskId: string }> }
) {
  const { taskId } = await params
  const payload = await request.json().catch(() => ({}))
  const approved =
    payload && typeof payload === "object" && "approved" in payload
      ? payload.approved
      : undefined
  const reason =
    payload && typeof payload === "object" && "reason" in payload
      ? payload.reason
      : undefined

  if (typeof approved !== "boolean") {
    return NextResponse.json(
      {
        code: "invalid_request",
        message: "approved must be a boolean",
        retryable: false,
      },
      { status: 400 }
    )
  }

  const reasonText = typeof reason === "string" ? reason : undefined
  const upstream = await proxyAgentRootJson(buildAgentRootPath("/rpc"), {
    method: "POST",
    body: JSON.stringify(
      buildA2ARpcTaskResumeEnvelope(taskId, approved, reasonText)
    ),
  })

  const body = await readJsonRpcResponse(upstream)
  if (!upstream.ok && !isJsonRpcMethodNotFound(body)) {
    return NextResponse.json(body, { status: upstream.status })
  }

  if (isJsonRpcMethodNotFound(body)) {
    const slashMethodUpstream = await proxyAgentRootJson(
      buildAgentRootPath("/rpc"),
      {
        method: "POST",
        body: JSON.stringify(
          buildA2ARpcTaskResumeEnvelope(
            taskId,
            approved,
            reasonText,
            "tasks/resume"
          )
        ),
      }
    )
    const slashMethodBody = await readJsonRpcResponse(slashMethodUpstream)
    if (!slashMethodUpstream.ok && !isJsonRpcMethodNotFound(slashMethodBody)) {
      return NextResponse.json(slashMethodBody, {
        status: slashMethodUpstream.status,
      })
    }
    if (isJsonRpcMethodNotFound(slashMethodBody)) {
      return staleResumeRouteResponse()
    }
    return jsonRpcResultResponse(slashMethodBody, {
      invalidMessage: "Invalid A2A resume response",
    })
  }

  return jsonRpcResultResponse(body, {
    invalidMessage: "Invalid A2A resume response",
  })
}
