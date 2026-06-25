import { ref } from 'vue'
import type { Message, Conversation, Source } from '@/types'
import {
  listConversations,
  createConversation,
  getConversation,
  deleteConversation,
  streamConversation,
} from '@/api/conversation'

// ===== Module-level shared state =====
// 所有调用 useConversation() 的组件共享同一份状态
const conversations = ref<Conversation[]>([])
const messages = ref<Message[]>([])
const streamingContent = ref('')
const streamingThinking = ref('')
const streamingSources = ref<Source[]>([])
const isStreaming = ref(false)
const currentConvId = ref<string | null>(null)
let abortController: AbortController | null = null

export function useConversation() {

  async function loadConversations() {
    try {
      conversations.value = await listConversations()
    } catch {
      // ignore
    }
  }

  async function loadMessages(convId: string) {
    currentConvId.value = convId
    try {
      const conv = await getConversation(convId)
      messages.value = conv.messages || []
    } catch {
      messages.value = []
    }
  }

  async function sendMessage(content: string) {
    let convId = currentConvId.value
    if (!convId) {
      const conv = await createConversation()
      convId = conv.id
      currentConvId.value = convId
      await loadConversations()
    }

    const userMsg: Message = {
      id: `temp-${Date.now()}`,
      role: 'user',
      content,
      created_at: new Date().toISOString(),
    }
    messages.value.push(userMsg)

    isStreaming.value = true
    streamingContent.value = ''
    streamingThinking.value = ''
    streamingSources.value = []
    abortController = new AbortController()

    try {
      for await (const event of streamConversation(convId, content, abortController.signal)) {
        switch (event.type) {
          case 'thinking':
            streamingThinking.value = event.content || ''
            break
          case 'sources':
            streamingSources.value = event.sources || []
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
                sources: [...streamingSources.value],
              })
            }
            streamingContent.value = ''
            streamingThinking.value = ''
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
      streamingThinking.value = ''
      abortController = null
      await loadConversations()
    }
  }

  function stopGeneration() {
    abortController?.abort()
  }

  function newConversation() {
    currentConvId.value = null
    messages.value = []
    streamingContent.value = ''
    streamingThinking.value = ''
    streamingSources.value = []
  }

  async function removeConversation(convId: string) {
    await deleteConversation(convId)
    if (currentConvId.value === convId) {
      currentConvId.value = null
      messages.value = []
    }
    await loadConversations()
  }

  return {
    conversations, messages, streamingContent, streamingThinking,
    streamingSources, isStreaming, currentConvId,
    loadConversations, loadMessages, newConversation, sendMessage,
    stopGeneration, removeConversation,
  }
}
