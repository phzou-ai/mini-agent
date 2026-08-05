import { buildAgentPath, proxyAgentJson } from "@/lib/agent/server"

export async function POST(
  _request: Request,
  { params }: { params: Promise<{ taskId: string }> }
) {
  const { taskId } = await params
  return proxyAgentJson(
    buildAgentPath(`/management/tasks/${encodeURIComponent(taskId)}/retry`),
    { method: "POST" }
  )
}
