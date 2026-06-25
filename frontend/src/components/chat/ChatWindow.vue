<script setup lang="ts">
import { ref, nextTick, watch, onMounted } from 'vue'
import type { Message } from '@/types'
import { marked } from 'marked'

const props = defineProps<{
  messages: Message[]
  streamingContent: string
  streamingThinking?: string
  isStreaming: boolean
  isLoading?: boolean
}>()

const container = ref<HTMLElement | null>(null)

watch(
  () => [props.messages.length, props.streamingContent],
  () => {
    nextTick(() => {
      if (container.value) {
        container.value.scrollTop = container.value.scrollHeight
      }
    })
  },
)

onMounted(() => {
  nextTick(() => {
    if (container.value) {
      container.value.scrollTop = container.value.scrollHeight
    }
  })
})

function renderMarkdown(text: string): string {
  return marked(text, { breaks: true }) as string
}
</script>

<template>
  <div ref="container" class="flex-1 overflow-y-auto px-4 py-6 space-y-4">
    <!-- Loading -->
    <div v-if="isLoading" class="flex items-center justify-center h-full text-gray-400">
      加载中...
    </div>

    <!-- Empty State -->
    <div v-if="messages.length === 0 && !isStreaming && !isLoading" class="flex flex-col items-center justify-center h-full text-gray-400">
      <div class="text-6xl mb-4">🍳</div>
      <p class="text-lg font-medium text-gray-600 mb-2">欢迎使用 CookAgent</p>
      <p class="text-sm">输入你想做的菜或烹饪问题，我来帮你！</p>
      <div class="mt-6 grid grid-cols-2 gap-2 text-xs">
        <button class="px-3 py-2 bg-gray-100 rounded-lg hover:bg-gray-200 transition-colors text-left">
          🍅 番茄炒蛋怎么做？
        </button>
        <button class="px-3 py-2 bg-gray-100 rounded-lg hover:bg-gray-200 transition-colors text-left">
          🥬 夏天适合吃什么菜？
        </button>
        <button class="px-3 py-2 bg-gray-100 rounded-lg hover:bg-gray-200 transition-colors text-left">
          🥩 牛肉有哪些做法？
        </button>
        <button class="px-3 py-2 bg-gray-100 rounded-lg hover:bg-gray-200 transition-colors text-left">
          🔪 新手必学的简单菜
        </button>
      </div>
    </div>

    <!-- Messages -->
    <div
      v-for="msg in messages"
      :key="msg.id"
      :class="['flex', msg.role === 'user' ? 'justify-end' : 'justify-start']"
    >
      <div
        :class="[
          'max-w-[80%] rounded-2xl px-4 py-3 text-sm leading-relaxed',
          msg.role === 'user'
            ? 'bg-orange-500 text-white'
            : 'bg-gray-100 text-gray-800',
        ]"
      >
        <div v-if="msg.role === 'assistant'" class="markdown-body" v-html="renderMarkdown(msg.content)" />
        <div v-else class="whitespace-pre-wrap">{{ msg.content }}</div>
      </div>
    </div>

    <!-- Streaming Message -->
    <div v-if="isStreaming" class="flex justify-start">
      <div class="max-w-[80%] bg-gray-100 rounded-2xl px-4 py-3">
        <div v-if="streamingThinking" class="text-xs text-gray-400 italic mb-1">
          {{ streamingThinking }}
        </div>
        <div v-if="streamingContent" class="markdown-body text-sm text-gray-800" v-html="renderMarkdown(streamingContent)" />
        <div v-if="!streamingContent" class="flex gap-1">
          <span class="w-2 h-2 bg-gray-400 rounded-full animate-bounce" style="animation-delay: 0ms" />
          <span class="w-2 h-2 bg-gray-400 rounded-full animate-bounce" style="animation-delay: 150ms" />
          <span class="w-2 h-2 bg-gray-400 rounded-full animate-bounce" style="animation-delay: 300ms" />
        </div>
      </div>
    </div>
  </div>
</template>
