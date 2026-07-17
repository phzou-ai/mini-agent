import { normalizeAgentError } from "@/lib/agent/error-contract"

type RequestMethod = "GET" | "POST" | "PUT" | "PATCH" | "DELETE"

type RequestOptions = {
  body?: unknown
  headers?: HeadersInit
  signal?: AbortSignal
}

export class RequestError extends Error {
  status: number
  code: string
  retryable: boolean
  details?: unknown

  constructor(
    message: string,
    status: number,
    details?: unknown,
    code = "request_error",
    retryable = false
  ) {
    super(message)
    this.name = "RequestError"
    this.status = status
    this.code = code
    this.retryable = retryable
    this.details = details
  }
}

export function getRequestErrorMessage(error: unknown, fallback = "Request failed") {
  if (error instanceof RequestError) {
    return error.message || fallback
  }

  if (error instanceof Error && error.message) {
    return error.message
  }

  return fallback
}

async function requestJson<T>(
  method: RequestMethod,
  url: string,
  { body, headers, signal }: RequestOptions = {}
): Promise<T> {
  const response = await fetch(url, {
    method,
    headers: {
      ...(body !== undefined ? { "Content-Type": "application/json" } : {}),
      ...headers
    },
    body: body === undefined ? undefined : JSON.stringify(body),
    signal
  })

  if (!response.ok) {
    const fallbackMessage = `Request failed (${response.status})`
    let details: unknown

    try {
      details = await response.clone().json()
    } catch {
      try {
        const text = await response.text()
        details = text ? { message: text } : undefined
      } catch {
        details = undefined
      }
    }

    throw requestErrorFromDetails(response.status, details, fallbackMessage)
  }

  if (response.status === 204) {
    return undefined as T
  }

  return response.json() as Promise<T>
}

export async function requestErrorFromResponse(
  response: Response,
  fallbackMessage = `Request failed (${response.status})`
) {
  let details: unknown
  try {
    details = await response.clone().json()
  } catch {
    details = undefined
  }
  return requestErrorFromDetails(response.status, details, fallbackMessage)
}

function requestErrorFromDetails(
  status: number,
  details: unknown,
  fallbackMessage: string
) {
  const error = normalizeAgentError(status, details, {
    message: fallbackMessage,
  })
  return new RequestError(
    error.message,
    status,
    details,
    error.code,
    error.retryable
  )
}

const requestGet = async <T = any>(
  url: string,
  options?: Omit<RequestOptions, "body">
) => requestJson<T>("GET", url, options)

const requestDelete = async <T = any>(
  url: string,
  options?: Omit<RequestOptions, "body">
) => requestJson<T>("DELETE", url, options)

const requestPost = async <T = any>(
  url: string,
  params: unknown,
  options?: Omit<RequestOptions, "body">
) => requestJson<T>("POST", url, { ...options, body: params })

const requestPut = async <T = any>(
  url: string,
  params: unknown,
  options?: Omit<RequestOptions, "body">
) => requestJson<T>("PUT", url, { ...options, body: params })

const requestPatch = async <T = any>(
  url: string,
  params: unknown,
  options?: Omit<RequestOptions, "body">
) => requestJson<T>("PATCH", url, { ...options, body: params })

export { requestDelete, requestGet, requestPatch, requestPost, requestPut }
