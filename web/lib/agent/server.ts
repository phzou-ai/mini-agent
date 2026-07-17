import { NextResponse } from "next/server"

import {
  agentApiUnavailableError,
  normalizeAgentError,
} from "@/lib/agent/error-contract"

const DEFAULT_VERMAY_AGENT_API_BASE = "http://127.0.0.1:8000"

function vermayAgentBaseUrl() {
  return (
    process.env.VERMAY_AGENT_API_BASE?.replace(/\/$/, "") ||
    process.env.MINI_AGENT_API_BASE?.replace(/\/$/, "") ||
    DEFAULT_VERMAY_AGENT_API_BASE
  )
}

function buildVermayAgentUrl(path: string) {
  return `${vermayAgentBaseUrl()}${path}`
}

export function buildAgentPath(path: string, searchParams?: URLSearchParams) {
  const query = searchParams?.toString()
  return `/api${path}${query ? `?${query}` : ""}`
}

export function buildAgentRootPath(path: string, searchParams?: URLSearchParams) {
  const query = searchParams?.toString()
  return `${path}${query ? `?${query}` : ""}`
}

export async function proxyAgentJson(path: string, init?: RequestInit) {
  try {
    const response = await fetch(buildVermayAgentUrl(path), {
      ...init,
      cache: "no-store",
      headers: {
        ...(init?.body ? { "Content-Type": "application/json" } : {}),
        ...(init?.headers ?? {}),
      },
    })
    const text = await response.text()
    let payload: unknown = null

    if (text) {
      try {
        payload = JSON.parse(text)
      } catch {
        payload = null
      }
    }

    if (!response.ok) {
      return NextResponse.json(normalizeAgentError(response.status, payload), {
        status: response.status,
      })
    }

    if (response.status === 204) {
      return new Response(null, { status: 204 })
    }

    return NextResponse.json(payload)
  } catch {
    return NextResponse.json(agentApiUnavailableError(), { status: 502 })
  }
}

export function proxyAgentRootJson(path: string, init?: RequestInit) {
  return proxyAgentJson(path, init)
}

export async function proxyAgentStream(path: string, init?: RequestInit) {
  try {
    const response = await fetch(buildVermayAgentUrl(path), {
      ...init,
      cache: "no-store",
      headers: {
        Accept: "text/event-stream",
        ...(init?.body ? { "Content-Type": "application/json" } : {}),
        ...(init?.headers ?? {}),
      },
    })

    if (!response.ok) {
      let payload: unknown = null
      try {
        payload = await response.json()
      } catch {
        payload = null
      }

      return NextResponse.json(normalizeAgentError(response.status, payload), {
        status: response.status,
      })
    }
    if (!response.body) {
      return NextResponse.json(
        {
          code: "invalid_a2a_stream",
          message: "Vermay Agent returned an empty event stream.",
          retryable: false,
        },
        { status: 502 }
      )
    }

    return new Response(response.body, {
      status: response.status,
      headers: {
        "Cache-Control": "no-cache",
        Connection: "keep-alive",
        "Content-Type": "text/event-stream",
        "X-Accel-Buffering": "no",
      },
    })
  } catch {
    return NextResponse.json(agentApiUnavailableError(), { status: 502 })
  }
}

export function proxyAgentRootStream(path: string, init?: RequestInit) {
  return proxyAgentStream(path, init)
}
