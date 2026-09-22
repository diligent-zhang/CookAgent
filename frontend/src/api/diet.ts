import { apiGet, apiPost, apiPatch, apiDelete, apiPut } from './client'
import type {
  DietPlanWeek,
  DietPlanMeal,
  DietLog,
  NutritionSummary,
  DeviationAnalysis,
  FoodPreference,
  Dish,
  MealType,
} from '@/types'

// ===== 计划管理 =====

export function getPlanByWeek(weekStart: string): Promise<DietPlanWeek> {
  return apiGet<DietPlanWeek>(`/diet/plans?week_start=${weekStart}`)
}

export function addMeal(data: {
  plan_date: string
  meal_type: MealType
  dishes: Dish[]
  notes?: string
}): Promise<DietPlanMeal> {
  return apiPost<DietPlanMeal>('/diet/plans', data)
}

export function updateMeal(mealId: string, data: { dishes?: Dish[]; notes?: string }): Promise<DietPlanMeal> {
  return apiPatch<DietPlanMeal>(`/diet/plans/${mealId}`, data)
}

export function deleteMeal(mealId: string): Promise<{ message: string }> {
  return apiDelete<{ message: string }>(`/diet/plans/${mealId}`)
}

export function copyMeal(mealId: string, targetDate: string, targetMealType?: string): Promise<DietPlanMeal> {
  return apiPost<DietPlanMeal>(`/diet/plans/${mealId}/copy`, { target_date: targetDate, target_meal_type: targetMealType })
}

export function markMealEaten(mealId: string, logDate?: string): Promise<DietLog> {
  return apiPost<DietLog>(`/diet/plans/${mealId}/mark-eaten`, { log_date: logDate })
}

// ===== 记录管理 =====

export function getLogsByDate(logDate: string): Promise<{ logs: DietLog[]; date: string }> {
  return apiGet<{ logs: DietLog[]; date: string }>(`/diet/logs?log_date=${logDate}`)
}

export function createLog(data: {
  log_date: string
  meal_type: MealType
  items: Array<{ food_name: string; calories?: number }>
  notes?: string
}): Promise<DietLog> {
  return apiPost<DietLog>('/diet/logs', data)
}

export function createLogFromText(data: {
  text: string
  log_date?: string
  meal_type?: MealType
}): Promise<DietLog> {
  return apiPost<DietLog>('/diet/logs/from-text', data)
}

export function deleteLog(logId: string): Promise<{ message: string }> {
  return apiDelete<{ message: string }>(`/diet/logs/${logId}`)
}

// ===== 营养分析 =====

export function getDailySummary(targetDate: string): Promise<NutritionSummary> {
  return apiGet<NutritionSummary>(`/diet/analysis/daily?target_date=${targetDate}`)
}

export function getWeeklySummary(weekStart?: string): Promise<NutritionSummary> {
  return apiGet<NutritionSummary>(`/diet/analysis/weekly${weekStart ? `?week_start=${weekStart}` : ''}`)
}

export function getDeviationAnalysis(weekStart?: string): Promise<DeviationAnalysis> {
  return apiGet<DeviationAnalysis>(`/diet/analysis/deviation${weekStart ? `?week_start=${weekStart}` : ''}`)
}

// ===== 偏好 =====

export function getPreferences(): Promise<{ preference: FoodPreference | null }> {
  return apiGet<{ preference: FoodPreference | null }>('/diet/preferences')
}

export function updatePreferences(data: Partial<Omit<FoodPreference, 'id' | 'user_id' | 'created_at' | 'updated_at'>>): Promise<{ preference: FoodPreference }> {
  return apiPut<{ preference: FoodPreference }>('/diet/preferences', data)
}