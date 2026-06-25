import { apiGet, apiPost, apiDelete, API_BASE, getToken } from './client'
import type { Conversation, Message, SSEEvent } from '@/types'

// ===== CRUD =====

export async function listConversations(): Promise<Conversation[]> {
  return apiGet<Conversation[]>('/conversations')
}

export async function createConversation(title?: string): Promise<Conversation> {
  return apiPost<Conversation>('/conversations', { title })
}

export async function getConversation(id: string): Promise<Conversation & { messages: Message[] }> {
  return apiGet<Conversation & { messages: Message[] }>(`/conversations/${id}`)
}

export async function deleteConversation(id: string): Promise<void> {
  return apiDelete<void>(`/conversations/${id}`)
}

// ===== SSE Streaming =====

export async function* streamConversation(
  convId: string,
  message: string,
  signal?: AbortSignal,
): AsyncGenerator<SSEEvent> {
  const token = getToken()
  const res = await fetch(`${API_BASE}/conversations/${convId}/chat`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: JSON.stringify({ content: message }),
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
