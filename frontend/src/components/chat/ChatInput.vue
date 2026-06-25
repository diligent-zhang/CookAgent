<script setup lang="ts">
import { ref } from 'vue'
import { Send, Square } from 'lucide-vue-next'

const props = defineProps<{
  isStreaming: boolean
}>()

const emit = defineEmits<{
  send: [content: string]
  stop: []
}>()

const input = ref('')

function handleSend() {
  const content = input.value.trim()
  if (!content || props.isStreaming) return
  emit('send', content)
  input.value = ''
}
</script>

<template>
  <div class="border-t border-gray-200 p-4 bg-white">
    <div class="flex items-end gap-3 max-w-3xl mx-auto">
      <textarea
        v-model="input"
        @keydown.enter.exact.prevent="handleSend"
        placeholder="输入你的烹饪问题，按 Enter 发送..."
        class="flex-1 resize-none rounded-xl border border-gray-300 px-4 py-3 text-sm focus:outline-none focus:ring-2 focus:ring-orange-500 focus:border-transparent max-h-32"
        rows="1"
        :disabled="isStreaming"
      />
      <button
        v-if="!isStreaming"
        @click="handleSend"
        :disabled="!input.trim()"
        class="p-3 bg-orange-500 text-white rounded-xl hover:bg-orange-600 disabled:opacity-40 transition-colors"
      >
        <Send :size="18" />
      </button>
      <button
        v-else
        @click="emit('stop')"
        class="p-3 bg-red-500 text-white rounded-xl hover:bg-red-600 transition-colors"
      >
        <Square :size="18" />
      </button>
    </div>
  </div>
</template>
