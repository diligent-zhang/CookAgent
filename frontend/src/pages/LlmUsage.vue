<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { getUsageSummary, getUsageLogs } from '@/api/llm'
import type { LlmUsageSummary, LlmUsageLogEntry } from '@/types'

const summary = ref<LlmUsageSummary | null>(null)
const logs = ref<LlmUsageLogEntry[]>([])
const loading = ref(true)

const totalCalls = computed(() => summary.value?.total_calls ?? 0)
const totalTokens = computed(() => summary.value?.total_tokens ?? 0)
const maxModelTokens = computed(() => Math.max(1, ...(summary.value?.by_model.map(m => m.tokens) ?? [1])))

function duration(v?: number): string {
  if (v == null) return '—'
  if (v < 1000) return `${Math.round(v)}ms`
  return `${(v / 1000).toFixed(1)}s`
}

function tokens(v?: number): string {
  return (v ?? 0).toLocaleString()
}

onMounted(async () => {
  try {
    summary.value = await getUsageSummary()
    const res = await getUsageLogs(50)
    logs.value = res.logs
  } finally {
    loading.value = false
  }
})
</script>

<template>
  <div class="h-screen bg-white p-6 overflow-y-auto">
    <h2 class="text-xl font-bold mb-6">LLM 用量统计</h2>

    <div v-if="loading" class="text-gray-400">加载中...</div>

    <template v-else>
      <!-- 汇总卡片 -->
      <div class="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
        <div class="border border-gray-200 rounded-xl p-4 text-center">
          <div class="text-2xl font-bold text-orange-600">{{ totalCalls }}</div>
          <div class="text-xs text-gray-500 mt-1">总调用次数</div>
        </div>
        <div class="border border-gray-200 rounded-xl p-4 text-center">
          <div class="text-2xl font-bold text-orange-600">{{ tokens(totalTokens) }}</div>
          <div class="text-xs text-gray-500 mt-1">总 Token</div>
        </div>
        <div class="border border-gray-200 rounded-xl p-4 text-center">
          <div class="text-2xl font-bold text-orange-600">{{ tokens(summary?.total_input_tokens) }}</div>
          <div class="text-xs text-gray-500 mt-1">输入 Token</div>
        </div>
        <div class="border border-gray-200 rounded-xl p-4 text-center">
          <div class="text-2xl font-bold text-orange-600">{{ tokens(summary?.total_output_tokens) }}</div>
          <div class="text-xs text-gray-500 mt-1">输出 Token</div>
        </div>
      </div>

      <!-- 按模型 -->
      <div class="border border-gray-200 rounded-xl p-4 mb-6">
        <h3 class="font-bold mb-3">按模型统计</h3>
        <div v-if="summary?.by_model.length" class="space-y-3">
          <div v-for="m in summary.by_model" :key="m.model_name" class="flex items-center gap-2">
            <span class="w-32 text-sm text-gray-600 truncate">{{ m.model_name }}</span>
            <div class="flex-1 bg-gray-100 rounded h-4 overflow-hidden">
              <div class="h-full bg-orange-400" :style="{ width: `${(m.tokens / maxModelTokens) * 100}%` }"></div>
            </div>
            <span class="text-xs text-gray-500">{{ m.calls }} 次 · {{ tokens(m.tokens) }} tok · {{ duration(m.avg_duration_ms) }}</span>
          </div>
        </div>
        <p v-else class="text-gray-400 text-sm">暂无模型调用记录</p>
      </div>

      <!-- 最近调用日志 -->
      <div class="border border-gray-200 rounded-xl p-4">
        <h3 class="font-bold mb-3">最近调用日志</h3>
        <table class="w-full text-sm">
          <thead>
            <tr class="text-left text-gray-400 border-b border-gray-100">
              <th class="py-2">时间</th>
              <th>模块</th>
              <th>模型</th>
              <th>工具</th>
              <th class="text-right">Token</th>
              <th class="text-right">耗时</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="log in logs" :key="log.id" class="border-b border-gray-50">
              <td class="py-2 text-gray-500">{{ new Date(log.created_at).toLocaleString() }}</td>
              <td>{{ log.module_name }}</td>
              <td class="text-gray-600">{{ log.model_name || '—' }}</td>
              <td class="text-gray-600">{{ log.tool_name || '—' }}</td>
              <td class="text-right">{{ tokens(log.total_tokens) }}</td>
              <td class="text-right text-gray-500">{{ duration(log.duration_ms) }}</td>
            </tr>
          </tbody>
        </table>
        <p v-if="logs.length === 0" class="text-gray-400 text-sm py-4 text-center">暂无调用日志</p>
      </div>
    </template>
  </div>
</template>