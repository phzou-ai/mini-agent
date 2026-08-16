import { A2A_STREAM_EVENT_NAMES } from "@/lib/agent/a2a-stream-contract"
import type {
  AgentA2AMessagePayload,
  AgentA2AStreamEnvelope,
} from "@/lib/agent/types"
import { RequestError, requestErrorFromResponse } from "@/lib/request"

type AgentA2AStreamHandlers = {
  after?: number
  onEvent: (event: AgentA2AStreamEnvelope) => void
  onError?: (error: Event) => void
  onProtocolError?: (error: RequestError) => void
  onDone?: () => void
}

type AgentA2AMessageStreamHandlers = {
  signal?: AbortSignal
  onEvent: (event: AgentA2AStreamEnvelope) => void
  onError?: (error: Error) => void
  onDone?: () => void
}

export function openAgentA2ATaskEventStream(
  taskId: string,
  {
    after = 0,
    onEvent,
    onError,
    onProtocolError,
    onDone,
  }: AgentA2AStreamHandlers
) {
  const params = new URLSearchParams()
  if (after > 0) {
    params.set("after", String(after))
  }

  const query = params.toString()
  const source = new EventSource(
    `/api/bff/agent/a2a/tasks/${encodeURIComponent(taskId)}/events${query ? `?${query}` : ""}`
  )

  source.onerror = (error) => {
    // A server-sent `event: error` is a MessageEvent and is forwarded through
    // the JSON-RPC error contract below. Only transport failures reach here.
    if (error instanceof MessageEvent) return
    onError?.(error)
    source.close()
    onDone?.()
  }

  const forwardEvent = (message: MessageEvent<string>) => {
    let envelope: AgentA2AStreamEnvelope
    try {
      envelope = parseA2AStreamEnvelope(message.data)
    } catch (error) {
      onProtocolError?.(asInvalidA2AStreamError(error))
      return
    }
    onEvent(envelope)
  }

  for (const eventName of A2A_STREAM_EVENT_NAMES) {
    source.addEventListener(eventName, forwardEvent as EventListener)
  }

  source.onmessage = forwardEvent

  return source
}

export async function openAgentA2AMessageStream(
  payload: AgentA2AMessagePayload,
  { signal, onEvent, onError, onDone }: AgentA2AMessageStreamHandlers
) {
  try {
    const response = await fetch("/api/bff/agent/a2a/message-stream", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
      signal,
    })
    if (!response.ok) {
      throw await requestErrorFromResponse(
        response,
        `A2A message stream failed (${response.status})`
      )
    }
    if (!response.body) {
      throw new RequestError(
        "A2A message stream returned no response body.",
        502,
        undefined,
        "invalid_a2a_stream",
        false
      )
    }

    await readSseStream(response.body, onEvent, signal)
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") {
      return
    }
    onError?.(
      error instanceof Error ? error : new Error("A2A message stream failed")
    )
  } finally {
    onDone?.()
  }
}

async function readSseStream(
  body: ReadableStream<Uint8Array>,
  onEvent: (event: AgentA2AStreamEnvelope) => void,
  signal?: AbortSignal
) {
  const reader = body.getReader()
  const decoder = new TextDecoder()
  let buffer = ""

  while (true) {
    if (signal?.aborted) {
      await reader.cancel()
      return
    }

    const { done, value } = await reader.read()
    if (done) break

    buffer += decoder.decode(value, { stream: true })
    const chunks = buffer.split("\n\n")
    buffer = chunks.pop() || ""
    for (const chunk of chunks) {
      emitSseChunk(chunk, onEvent)
    }
  }

  buffer += decoder.decode()
  if (buffer.trim()) {
    emitSseChunk(buffer, onEvent)
  }
}

function emitSseChunk(
  chunk: string,
  onEvent: (event: AgentA2AStreamEnvelope) => void
) {
  const data = chunk
    .split("\n")
    .filter((line) => line.startsWith("data:"))
    .map((line) => line.slice("data:".length).trimStart())
    .join("\n")

  if (!data) return

  onEvent(parseA2AStreamEnvelope(data))
}

function parseA2AStreamEnvelope(data: string): AgentA2AStreamEnvelope {
  let value: unknown
  try {
    value = JSON.parse(data)
  } catch (error) {
    throw invalidA2AStreamError("The A2A stream returned malformed JSON.", error)
  }

  if (!isRecord(value) || value.jsonrpc !== "2.0") {
    throw invalidA2AStreamError(
      "The A2A stream returned an invalid JSON-RPC envelope."
    )
  }

  if (isRecord(value.error)) {
    return value as AgentA2AStreamEnvelope
  }

  if (!isRecord(value.result) || !isValidA2AStreamResult(value.result)) {
    throw invalidA2AStreamError(
      "The A2A stream returned an invalid result payload."
    )
  }

  return value as AgentA2AStreamEnvelope
}

function isValidA2AStreamResult(result: Record<string, unknown>) {
  switch (result.kind) {
    case "message":
      return (
        typeof result.messageId === "string" &&
        typeof result.contextId === "string" &&
        Array.isArray(result.parts)
      )
    case "task":
      return (
        typeof result.id === "string" &&
        typeof result.contextId === "string" &&
        hasState(result.status)
      )
    case "status-update":
      return (
        typeof result.taskId === "string" &&
        typeof result.contextId === "string" &&
        hasState(result.status)
      )
    case "artifact-update":
      return (
        typeof result.taskId === "string" &&
        typeof result.contextId === "string" &&
        isRecord(result.artifact) &&
        Array.isArray(result.artifact.parts)
      )
    default:
      return false
  }
}

function hasState(value: unknown) {
  return isRecord(value) && typeof value.state === "string"
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value)
}

function invalidA2AStreamError(message: string, details?: unknown) {
  return new RequestError(message, 502, details, "invalid_a2a_stream", false)
}

function asInvalidA2AStreamError(error: unknown) {
  return error instanceof RequestError
    ? error
    : invalidA2AStreamError("The A2A stream returned invalid data.", error)
}
