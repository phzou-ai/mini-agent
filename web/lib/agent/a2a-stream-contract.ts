import type { AgentA2AStreamEnvelope } from "@/lib/agent/types"
import { normalizeAgentError } from "@/lib/agent/error-contract"

export const A2A_STREAM_EVENT_NAMES = [
  "status-update",
  "artifact-update",
  "message",
  "task",
  "error",
] as const

export function isA2APartialMessage(
  result: AgentA2AStreamEnvelope["result"] | null | undefined
): boolean {
  if (!result || result.kind !== "message") return false
  const metadata = result.metadata ?? {}
  return metadata.partial === true && metadata.append === true
}

export function isA2AFinalMessage(
  result: AgentA2AStreamEnvelope["result"] | null | undefined
): boolean {
  if (!result || result.kind !== "message") return false
  return !isA2APartialMessage(result)
}

export function textFromA2AParts(parts: Array<{ text?: string }>) {
  return parts
    .map((part) => (typeof part.text === "string" ? part.text : ""))
    .filter(Boolean)
    .join("\n")
}

export function a2aPartialMessageMetadata(sequence: number) {
  return {
    partial: true,
    append: true,
    sequence,
  }
}

export function a2aFinalMessageMetadata() {
  return {
    partial: false,
    append: false,
    final: true,
  }
}

export function errorFromA2AStreamEnvelope(
  envelope: AgentA2AStreamEnvelope
) {
  return normalizeAgentError(502, envelope, {
    code: "a2a_stream_error",
    message: "A2A message stream returned an error.",
  })
}
