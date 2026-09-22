import { apiGet } from './client'
import type { LlmUsageSummary, LlmUsageLogEntry } from '@/types'

export function getUsageSummary(): Promise<LlmUsageSummary> {
  return apiGet<LlmUsageSummary>('/llm/usage/summary')
}

export function getUsageLogs(limit = 50, offset = 0): Promise<{ logs: LlmUsageLogEntry[]; count: number }> {
  return apiGet<{ logs: LlmUsageLogEntry[]; count: number }>(`/llm/usage/logs?limit=${limit}&offset=${offset}`)
}