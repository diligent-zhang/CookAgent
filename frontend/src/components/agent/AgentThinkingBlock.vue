<script setup lang="ts">
import type { AgentStep } from '@/types'
import { Brain, Wrench, Eye } from 'lucide-vue-next'

defineProps<{
  steps: AgentStep[]
}>()

function iconForType(type: string) {
  switch (type) {
    case 'thought': return Brain
    case 'tool_call': return Wrench
    case 'observation': return Eye
    default: return Brain
  }
}
</script>

<template>
  <div class="border border-gray-200 rounded-lg mb-3 overflow-hidden text-xs">
    <div
      v-for="(step, idx) in steps"
      :key="idx"
      class="px-3 py-2 border-b border-gray-100 last:border-b-0 flex items-start gap-2"
    >
      <component
        :is="iconForType(step.type)"
        :size="14"
        :class="[
          'mt-0.5 flex-shrink-0',
          step.type === 'thought' ? 'text-blue-500' :
          step.type === 'tool_call' ? 'text-amber-500' : 'text-green-500',
        ]"
      />
      <div class="text-gray-600">
        <span v-if="step.type === 'tool_call'" class="font-medium text-amber-600">
          {{ step.tool_name }}
        </span>
        <span class="whitespace-pre-wrap">{{ step.content }}</span>
      </div>
    </div>
  </div>
</template>
