import { apiGet, apiDelete, API_BASE, getToken } from './client'
import type { AgentSession, SSEEvent } from '@/types'

// ===== CRUD =====

export async function listAgentSessions(): Promise<AgentSession[]> {
  return apiGet<AgentSession[]>('/agent/sessions')
}

export async function getAgentSession(id: string): Promise<AgentSession> {
  return apiGet<AgentSession>(`/agent/sessions/${id}`)
}

export async function deleteAgentSession(id: string): Promise<void> {
  return apiDelete<void>(`/agent/sessions/${id}`)
}

// ===== SSE Streaming =====

export async function* streamAgentChat(
  sessionId: string | null,
  message: string,
  signal?: AbortSignal,
): AsyncGenerator<SSEEvent> {
  const token = getToken()
  const res = await fetch(`${API_BASE}/agent/chat`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: JSON.stringify({
      content: message,
      ...(sessionId ? { session_id: sessionId } : {}),
    }),
    signal,
  })

  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }))
    throw new Error(err.detail || `HTTP ${res.status}`)
  }

  const reader = res.body?.getReader()
  if (!reader) throw new Error('No response body')

  const decoder = new TextDecoder()
  let buffer = ''

  try {
    while (true) {
      const { done, value } = await reader.read()
      if (done) break

      buffer += decoder.decode(value, { stream: true })
      const lines = buffer.split('\n')
      buffer = lines.pop() || ''

      for (const line of lines) {
        if (line.startsWith('data: ')) {
          const data = line.slice(6)
          if (data.trim()) {
            try {
              yield JSON.parse(data) as SSEEvent
            } catch {
              // skip malformed JSON
            }
          }
        }
      }
    }
  } finally {
    reader.releaseLock()
  }
}
