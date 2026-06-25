<script setup lang="ts">
import { onMounted } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { useAuth } from '@/composables/useAuth'
import { useConversation } from '@/composables/useConversation'
import { useAgent } from '@/composables/useAgent'
import { Plus, MessageCircle, Bot, LogOut, ChefHat } from 'lucide-vue-next'

const router = useRouter()
const route = useRoute()
const { user, logout } = useAuth()
const { conversations, loadConversations, newConversation } = useConversation()
const { sessions, loadSessions, removeSession, newSession } = useAgent()

const isAgentMode = () => route.path.startsWith('/agent')

onMounted(() => {
  loadConversations()
  loadSessions()
})

function goChat(convId: string) {
  router.push(`/chat/${convId}`)
}

function goAgent(sessionId: string) {
  router.push(`/agent/${sessionId}`)
}

function startNewChat() {
  if (isAgentMode()) {
    newSession()
    router.push('/agent')
  } else {
    newConversation()
    router.push('/chat')
  }
}

function handleLogout() {
  logout()
}
</script>

<template>
  <aside class="w-64 h-screen bg-gray-50 border-r border-gray-200 flex flex-col flex-shrink-0">
    <!-- Header -->
    <div class="p-4 border-b border-gray-200">
      <div class="flex items-center gap-2 text-orange-600 font-bold text-lg cursor-pointer" @click="startNewChat">
        <ChefHat :size="24" />
        <span>CookAgent</span>
      </div>
    </div>

    <!-- New Chat Button -->
    <div class="p-3">
      <button
        @click="startNewChat"
        class="w-full flex items-center justify-center gap-2 py-2 px-4 bg-orange-500 text-white rounded-lg hover:bg-orange-600 transition-colors text-sm"
      >
        <Plus :size="16" />
        新对话
      </button>
    </div>

    <!-- Mode Tabs -->
    <div class="px-3 pb-2 flex gap-1">
      <button
        @click="router.push('/agent')"
        :class="[
          'flex-1 py-1.5 text-xs font-medium rounded-lg transition-colors',
          isAgentMode() ? 'bg-white text-orange-600 shadow-sm' : 'text-gray-500 hover:text-gray-700',
        ]"
      >
        <Bot :size="14" class="inline mr-1" />
        Agent
      </button>
      <button
        @click="router.push('/chat')"
        :class="[
          'flex-1 py-1.5 text-xs font-medium rounded-lg transition-colors',
          !isAgentMode() ? 'bg-white text-orange-600 shadow-sm' : 'text-gray-500 hover:text-gray-700',
        ]"
      >
        <MessageCircle :size="14" class="inline mr-1" />
        对话
      </button>
    </div>

    <!-- Agent Sessions -->
    <div v-if="isAgentMode()" class="flex-1 overflow-y-auto px-3 space-y-1">
      <p class="text-xs text-gray-400 px-2 py-1">Agent 会话</p>
      <div
        v-for="s in sessions"
        :key="s.id"
        @click="goAgent(s.id)"
        :class="[
          'group flex items-center justify-between px-3 py-2 rounded-lg cursor-pointer text-sm transition-colors',
          route.params.id === s.id ? 'bg-white shadow-sm text-orange-600' : 'text-gray-600 hover:bg-gray-100',
        ]"
      >
        <span class="truncate">{{ s.title || '新对话' }}</span>
        <button
          @click.stop="removeSession(s.id)"
          class="opacity-0 group-hover:opacity-100 text-gray-400 hover:text-red-500 transition-all"
          title="删除"
        >
          &times;
        </button>
      </div>
      <p v-if="sessions.length === 0" class="text-gray-400 text-xs text-center py-4">
        暂无会话
      </p>
    </div>

    <!-- RAG Conversations -->
    <div v-else class="flex-1 overflow-y-auto px-3 space-y-1">
      <p class="text-xs text-gray-400 px-2 py-1">RAG 对话</p>
      <div
        v-for="c in conversations"
        :key="c.id"
        @click="goChat(c.id)"
        :class="[
          'group flex items-center justify-between px-3 py-2 rounded-lg cursor-pointer text-sm transition-colors',
          route.params.id === c.id ? 'bg-white shadow-sm text-orange-600' : 'text-gray-600 hover:bg-gray-100',
        ]"
      >
        <span class="truncate">{{ c.title || '新对话' }}</span>
        <span class="text-xs text-gray-400 ml-1">{{ c.updated_at?.slice(0, 10) }}</span>
      </div>
      <p v-if="conversations.length === 0" class="text-gray-400 text-xs text-center py-4">
        暂无对话
      </p>
    </div>

    <!-- User Footer -->
    <div class="p-3 border-t border-gray-200">
      <div class="flex items-center justify-between">
        <span class="text-sm text-gray-600 truncate">{{ user?.username || '用户' }}</span>
        <button
          @click="handleLogout"
          class="text-gray-400 hover:text-red-500 transition-colors"
          title="退出登录"
        >
          <LogOut :size="16" />
        </button>
      </div>
    </div>
  </aside>
</template>
