import { type NextRequest } from "next/server"

import { buildA2ARpcTaskResubscribeEnvelope } from "@/lib/agent/a2a"
import { buildAgentRootPath, proxyAgentRootStream } from "@/lib/agent/server"

export async function GET(
  request: NextRequest,
  { params }: { params: Promise<{ taskId: string }> },
) {
  const { taskId } = await params
  const rawAfter = request.nextUrl.searchParams.get("after")
  const afterEventId =
    rawAfter === null || rawAfter.trim() === "" ? 0 : Number(rawAfter)

  return proxyAgentRootStream(buildAgentRootPath("/rpc"), {
    method: "POST",
    body: JSON.stringify(
      buildA2ARpcTaskResubscribeEnvelope(
        taskId,
        Number.isFinite(afterEventId) ? afterEventId : 0,
      ),
    ),
  })
}
