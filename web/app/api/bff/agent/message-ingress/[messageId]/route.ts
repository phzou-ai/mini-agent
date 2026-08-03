import { buildAgentPath, proxyAgentJson } from "@/lib/agent/server"

export async function GET(
  _: Request,
  { params }: { params: Promise<{ messageId: string }> },
) {
  const { messageId } = await params
  return proxyAgentJson(
    buildAgentPath(`/message-ingress/${encodeURIComponent(messageId)}`),
  )
}
