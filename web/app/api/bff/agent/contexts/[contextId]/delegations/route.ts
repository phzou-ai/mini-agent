import { type NextRequest } from "next/server"

import { buildAgentPath, proxyAgentJson } from "@/lib/agent/server"

export async function GET(
  request: NextRequest,
  { params }: { params: Promise<{ contextId: string }> },
) {
  const { contextId } = await params
  return proxyAgentJson(
    buildAgentPath(
      `/contexts/${encodeURIComponent(contextId)}/delegations`,
      request.nextUrl.searchParams,
    ),
  )
}
