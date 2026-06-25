import { ref } from 'vue'
import type { Message, AgentSession, AgentStep } from '@/types'
import {
  listAgentSessions,
  getAgentSession,
  deleteAgentSession,
  streamAgentChat,
} from '@/api/agent'

// ===== Module-level shared state =====
// 所有调用 useAgent() 的组件共享同一份状态
const sessions = ref<AgentSession[]>([])
const messages = ref<Message[]>([])
const streamingContent = ref('')
const currentSteps = ref<AgentStep[]>([])
const isStreaming = ref(false)
const currentSessionId = ref<string | null>(null)
let abortController: AbortController | null = null

export function useAgent() {

  async function loadSessions() {
    try {
      sessions.value = await listAgentSessions()
    } catch {
      // ignore
    }
  }

  async function loadSession(sessionId: string) {
    currentSessionId.value = sessionId
    try {
      const session = await getAgentSession(sessionId)
      // Convert agent messages to unified Message format
      messages.value = (session as unknown as { messages?: Array<{ id: string; role: string; content: string; created_at: string }> }).messages?.map(m => ({
        ...m,
        role: m.role as Message['role'],
      })) || []
    } catch {
      messages.value = []
    }
  }

  function newSession() {
    currentSessionId.value = null
    messages.value = []
    currentSteps.value = []
  }

  async function sendMessage(content: string) {
    const userMsg: Message = {
      id: `temp-${Date.now()}`,
      role: 'user',
      content,
      created_at: new Date().toISOString(),
    }
    messages.value.push(userMsg)

    isStreaming.value = true
    streamingContent.value = ''
    currentSteps.value = []
    abortController = new AbortController()

    try {
      for await (const event of streamAgentChat(currentSessionId.value, content, abortController.signal)) {
        switch (event.type) {
          case 'session':
            if (event.session_id) {
              currentSessionId.value = event.session_id
            }
            break
          case 'thought':
            currentSteps.value.push({
              type: 'thought',
              content: event.content || '',
              step_number: event.step_number || currentSteps.value.length,
            })
            break
          case 'tool_call':
            currentSteps.value.push({
              type: 'tool_call',
              content: `调用工具: ${event.tool_name}`,
              tool_name: event.tool_name,
              step_number: event.step_number || currentSteps.value.length,
            })
            break
          case 'observation':
            currentSteps.value.push({
              type: 'observation',
              content: event.content || '',
              step_number: event.step_number || currentSteps.value.length,
            })
            break
          case 'token':
            streamingContent.value += event.content || ''
            break
          case 'done':
            if (streamingContent.value) {
              messages.value.push({
                id: event.message_id || `msg-${Date.now()}`,
                role: 'assistant',
                content: streamingContent.value,
                created_at: new Date().toISOString(),
                thoughts: [...currentSteps.value],
              })
            }
            streamingContent.value = ''
            currentSteps.value = []
            break
          case 'error':
            messages.value.push({
              id: `err-${Date.now()}`,
              role: 'assistant',
              content: event.content || '出错了',
              created_at: new Date().toISOString(),
            })
            break
        }
      }
    } catch (e: unknown) {
      if (e instanceof DOMException && e.name === 'AbortError') return
      messages.value.push({
        id: `err-${Date.now()}`,
        role: 'assistant',
        content: e instanceof Error ? e.message : '请求失败',
        created_at: new Date().toISOString(),
      })
    } finally {
      isStreaming.value = false
      streamingContent.value = ''
      currentSteps.value = []
      abortController = null
      await loadSessions()
    }
  }

  function stopGeneration() {
    abortController?.abort()
  }

  async function removeSession(sessionId: string) {
    await deleteAgentSession(sessionId)
    if (currentSessionId.value === sessionId) {
      currentSessionId.value = null
      messages.value = []
    }
    await loadSessions()
  }

  return {
    sessions, messages, streamingContent, currentSteps,
    isStreaming, currentSessionId,
    loadSessions, loadSession, newSession, sendMessage,
    stopGeneration, removeSession,
  }
}
