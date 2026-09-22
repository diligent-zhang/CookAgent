<script setup lang="ts">
import { ref, nextTick, watch, onMounted } from 'vue'
import type { AgentStep, Message } from '@/types'
import AgentThinkingBlock from './AgentThinkingBlock.vue'
import { marked } from 'marked'

const props = defineProps<{
  messages: Message[]
  streamingContent: string
  isStreaming: boolean
  isLoading?: boolean
  currentSteps?: AgentStep[]
}>()

const emit = defineEmits<{
  send: [content: string]
}>()

const suggestions = [
  '🍅 番茄炒蛋怎么做？再帮我估算一下热量',
  '🥬 我对虾过敏，推荐几道简单的家常菜',
  '📅 帮我制定一周减脂食谱，每天约1500千卡，不吃辣',
  '🗓️ 把饮食计划整理成能导入手机日历的文件',
]

function handleSuggest(text: string) {
  if (props.isStreaming) return
  emit('send', text)
}

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
  let html = marked(text, { breaks: true }) as string
  // 饮食计划下载链接 → 加 download 属性 + 醒目样式（不离开页面直接下载）
  html = html.replace(
    /<a href="(\/api\/v1\/agent\/meal-plan\/download\/[^"]+)">/g,
    '<a href="$1" download target="_blank" rel="noopener" class="plan-download">📥 ',
  )
  // 其他外链 → 新窗口打开
  html = html.replace(
    /<a href="(https?:\/\/[^"]+)">/g,
    '<a href="$1" target="_blank" rel="noopener">',
  )
  return html
}
</script>

<template>
  <div ref="container" class="flex-1 overflow-y-auto px-4 py-6 space-y-6">
    <!-- Loading -->
    <div v-if="isLoading" class="flex items-center justify-center h-full text-gray-400">
      加载中...
    </div>

    <!-- Empty State -->
    <div v-if="messages.length === 0 && !isStreaming && !isLoading" class="flex flex-col items-center justify-center h-full text-gray-400">
      <div class="text-6xl mb-4">🤖</div>
      <p class="text-lg font-medium text-gray-600 mb-2">Agent 智能烹饪助手</p>
      <p class="text-sm">我会自动搜索菜谱、估算营养、制定饮食计划，一步步帮你解决烹饪问题！</p>
      <div class="mt-6 grid grid-cols-2 gap-2 text-xs max-w-xl w-full px-4">
        <button
          v-for="s in suggestions"
          :key="s"
          @click="handleSuggest(s)"
          class="px-3 py-2 bg-gray-100 rounded-lg hover:bg-orange-50 hover:text-orange-600 transition-colors text-left"
        >
          {{ s }}
        </button>
      </div>
    </div>

    <!-- Messages -->
    <div v-for="msg in messages" :key="msg.id">
      <div
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
          <!-- Agent thinking steps -->
          <AgentThinkingBlock
            v-if="msg.role === 'assistant' && msg.thoughts"
            :steps="msg.thoughts"
          />

          <div v-if="msg.role === 'assistant'" class="markdown-body" v-html="renderMarkdown(msg.content)" />
          <div v-else class="whitespace-pre-wrap">{{ msg.content }}</div>
        </div>
      </div>
    </div>

    <!-- Streaming Agent Response -->
    <div v-if="isStreaming" class="flex justify-start">
      <div class="max-w-[80%] bg-gray-100 rounded-2xl px-4 py-3 min-w-[200px]">
        <!-- 实时思考步骤 -->
        <AgentThinkingBlock
          v-if="currentSteps && currentSteps.length > 0"
          :steps="currentSteps"
        />
        <div v-if="streamingContent" class="markdown-body text-sm text-gray-800" v-html="renderMarkdown(streamingContent)" />
        <div v-else-if="!currentSteps || currentSteps.length === 0" class="flex gap-1">
          <span class="w-2 h-2 bg-gray-400 rounded-full animate-bounce" style="animation-delay: 0ms" />
          <span class="w-2 h-2 bg-gray-400 rounded-full animate-bounce" style="animation-delay: 150ms" />
          <span class="w-2 h-2 bg-gray-400 rounded-full animate-bounce" style="animation-delay: 300ms" />
        </div>
      </div>
    </div>
  </div>
</template>
