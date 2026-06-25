<script setup lang="ts">
import { computed, onMounted, watch } from 'vue'
import { useRoute } from 'vue-router'
import { useConversation } from '@/composables/useConversation'
import { useAgent } from '@/composables/useAgent'
import Sidebar from '@/components/layout/Sidebar.vue'
import ChatWindow from '@/components/chat/ChatWindow.vue'
import ChatInput from '@/components/chat/ChatInput.vue'
import AgentChatWindow from '@/components/agent/AgentChatWindow.vue'

const route = useRoute()
const isAgentMode = computed(() => {
  return (route.meta.mode as string) === 'agent'
})

const {
  messages: chatMessages, streamingContent: chatStreaming,
  isStreaming: chatStreaming_,
  loadMessages: loadChatMessages,
  sendMessage: sendChatMessage, stopGeneration: stopChat,
} = useConversation()

const {
  messages: agentMessages, streamingContent: agentStreaming,
  isStreaming: agentStreaming_, currentSteps: agentSteps,
  loadSession: loadAgentSession,
  sendMessage: sendAgentMessage, stopGeneration: stopAgent,
} = useAgent()

// Mode-specific bindings
const messages = computed(() => isAgentMode.value ? agentMessages.value : chatMessages.value)
const streamingContent = computed(() => isAgentMode.value ? agentStreaming.value : chatStreaming.value)
const isStreaming = computed(() => isAgentMode.value ? agentStreaming_.value : chatStreaming_.value)
const isLoading = computed(() => false)
const currentSteps = computed(() => isAgentMode.value ? agentSteps.value : [])

onMounted(async () => {
  const id = route.params.id as string | undefined
  if (id && isAgentMode.value) {
    await loadAgentSession(id)
  } else if (id && !isAgentMode.value) {
    await loadChatMessages(id)
  }
})

// Reload when route id changes
watch(
  () => route.params.id,
  async (id) => {
    if (id && isAgentMode.value) {
      await loadAgentSession(id as string)
    } else if (id && !isAgentMode.value) {
      await loadChatMessages(id as string)
    }
  },
)

function handleSend(content: string) {
  if (isAgentMode.value) {
    sendAgentMessage(content)
  } else {
    sendChatMessage(content)
  }
}

function handleStop() {
  if (isAgentMode.value) {
    stopAgent()
  } else {
    stopChat()
  }
}
</script>

<template>
  <div class="flex h-screen bg-white">
    <Sidebar />

    <main class="flex-1 flex flex-col min-w-0">
      <!-- Agent Chat -->
      <AgentChatWindow
        v-if="isAgentMode"
        :messages="messages"
        :streaming-content="streamingContent"
        :is-streaming="isStreaming"
        :is-loading="isLoading"
        :current-steps="currentSteps"
      />

      <!-- Standard Chat -->
      <ChatWindow
        v-else
        :messages="messages"
        :streaming-content="streamingContent"
        :is-streaming="isStreaming"
        :is-loading="isLoading"
      />

      <ChatInput
        :is-streaming="isStreaming"
        @send="handleSend"
        @stop="handleStop"
      />
    </main>
  </div>
</template>
