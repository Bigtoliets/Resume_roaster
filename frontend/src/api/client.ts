const BASE = '/api/v1'

export class ApiError extends Error {
  status: number
  detail: unknown

  constructor(status: number, detail: unknown) {
    super(typeof detail === 'string' ? detail : JSON.stringify(detail))
    this.status = status
    this.detail = detail
  }
}

async function parse(resp: Response) {
  const text = await resp.text()
  if (!text) return null
  try {
    return JSON.parse(text)
  } catch {
    return text
  }
}

export async function request<T>(
  method: string,
  path: string,
  body?: unknown,
): Promise<T> {
  const resp = await fetch(`${BASE}${path}`, {
    method,
    headers: body instanceof FormData ? undefined : { 'Content-Type': 'application/json' },
    body: body instanceof FormData ? body : body ? JSON.stringify(body) : undefined,
  })
  const data = await parse(resp)
  if (!resp.ok) {
    const detail = (data as { detail?: unknown })?.detail ?? data ?? resp.statusText
    throw new ApiError(resp.status, detail)
  }
  return data as T
}

export const api = {
  get: <T>(path: string) => request<T>('GET', path),
  post: <T>(path: string, body?: unknown) => request<T>('POST', path, body),
  put: <T>(path: string, body?: unknown) => request<T>('PUT', path, body),
  del: <T>(path: string) => request<T>('DELETE', path),
}

export type SseHandler = (event: string, data: Record<string, unknown>) => void

/**
 * 诊断 SSE：用 fetch + ReadableStream 手工解析（EventSource 不支持 POST + 自定义请求体）。
 * 断开后允许"重新诊断"，一期不做断点续传（方案 §8.4）。
 */
export async function streamDiagnosis(
  sessionId: number,
  payload: { target_role: string; attachment_id?: number; resume_text?: string },
  onEvent: SseHandler,
  signal?: AbortSignal,
): Promise<void> {
  const resp = await fetch(`${BASE}/sessions/${sessionId}/diagnosis`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
    signal,
  })
  if (!resp.ok || !resp.body) {
    const data = await parse(resp)
    throw new ApiError(resp.status, (data as { detail?: unknown })?.detail ?? data)
  }

  const reader = resp.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    const frames = buffer.split('\n\n')
    buffer = frames.pop() ?? ''
    for (const frame of frames) {
      if (!frame.trim() || frame.startsWith(':')) continue
      let event = 'message'
      let dataText = ''
      for (const line of frame.split('\n')) {
        if (line.startsWith('event:')) event = line.slice(6).trim()
        else if (line.startsWith('data:')) dataText += line.slice(5).trim()
      }
      if (!dataText) continue
      try {
        onEvent(event, JSON.parse(dataText))
      } catch {
        onEvent(event, { raw: dataText })
      }
    }
  }
}

export interface TurnPayload {
  content: string
  agent_id?: number | null
  attachment_id?: number | null
  resume_text?: string | null
}

/** 一个 ReAct 回合：SSE 推 step / token / assistant / approval_required。 */
export async function streamTurn(
  sessionId: number,
  payload: TurnPayload,
  onEvent: SseHandler,
  signal?: AbortSignal,
): Promise<void> {
  const resp = await fetch(`${BASE}/sessions/${sessionId}/messages`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
    signal,
  })
  if (!resp.ok || !resp.body) {
    const data = await parse(resp)
    throw new ApiError(resp.status, (data as { detail?: unknown })?.detail ?? data)
  }

  const reader = resp.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    const frames = buffer.split('\n\n')
    buffer = frames.pop() ?? ''
    for (const frame of frames) {
      if (!frame.trim() || frame.startsWith(':')) continue
      let event = 'message'
      let dataText = ''
      for (const line of frame.split('\n')) {
        if (line.startsWith('event:')) event = line.slice(6).trim()
        else if (line.startsWith('data:')) dataText += line.slice(5).trim()
      }
      if (!dataText) continue
      try {
        onEvent(event, JSON.parse(dataText))
      } catch {
        onEvent(event, { raw: dataText })
      }
    }
  }
}
