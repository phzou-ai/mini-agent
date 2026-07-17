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
  { after = 0, onEvent, onError, onDone }: AgentA2AStreamHandlers
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
    onError?.(error)
    source.close()
    onDone?.()
  }

  const forwardEvent = (message: MessageEvent<string>) => {
    try {
      onEvent(JSON.parse(message.data) as AgentA2AStreamEnvelope)
    } catch {
      // Ignore malformed events from an interrupted A2A stream.
    }
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

  try {
    onEvent(JSON.parse(data) as AgentA2AStreamEnvelope)
  } catch {
    // Ignore malformed events from an interrupted A2A stream.
  }
}
