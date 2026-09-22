import { ref } from 'vue'
import type {
  DietPlanWeek,
  DietLog,
  NutritionSummary,
  DeviationAnalysis,
  FoodPreference,
  Dish,
  MealType,
} from '@/types'
import {
  getPlanByWeek, addMeal, updateMeal, deleteMeal, copyMeal, markMealEaten,
  getLogsByDate, createLogFromText, deleteLog,
  getDailySummary, getWeeklySummary, getDeviationAnalysis,
  getPreferences, updatePreferences,
} from '@/api/diet'

// ===== Module-level shared state =====
const weekStart = ref(getMonday(new Date()))
const plan = ref<DietPlanWeek | null>(null)
const logs = ref<DietLog[]>([])
const dailySummary = ref<NutritionSummary | null>(null)
const weeklySummary = ref<NutritionSummary | null>(null)
const deviation = ref<DeviationAnalysis | null>(null)
const preference = ref<FoodPreference | null>(null)
const loading = ref(false)

function getMonday(d: Date): string {
  const date = new Date(d)
  const day = (date.getDay() + 6) % 7
  date.setDate(date.getDate() - day)
  return date.toISOString().slice(0, 10)
}

export function useDiet() {

  async function loadWeek(start?: string) {
    if (start) weekStart.value = start
    loading.value = true
    try {
      plan.value = await getPlanByWeek(weekStart.value)
    } catch {
      plan.value = null
    } finally {
      loading.value = false
    }
  }

  async function saveMeal(payload: { plan_date: string; meal_type: MealType; dishes: Dish[]; notes?: string }) {
    await addMeal(payload)
    await loadWeek()
  }

  async function updateOne(mealId: string, data: { dishes?: Dish[]; notes?: string }) {
    await updateMeal(mealId, data)
    await loadWeek()
  }

  async function removeMeal(mealId: string) {
    await deleteMeal(mealId)
    await loadWeek()
  }

  async function duplicateMeal(mealId: string, targetDate: string) {
    await copyMeal(mealId, targetDate)
    await loadWeek()
  }

  async function markEaten(mealId: string) {
    await markMealEaten(mealId)
    await loadWeek()
    await loadLogs()
  }

  async function loadLogs(date?: string) {
    try {
      const d = date || new Date().toISOString().slice(0, 10)
      const res = await getLogsByDate(d)
      logs.value = res.logs
    } catch {
      logs.value = []
    }
  }

  async function logFromText(text: string) {
    const log = await createLogFromText({ text })
    logs.value.unshift(log)
    return log
  }

  async function removeLog(logId: string) {
    await deleteLog(logId)
    logs.value = logs.value.filter(l => l.log_id !== logId)
  }

  async function loadDaily(date?: string) {
    const d = date || new Date().toISOString().slice(0, 10)
    dailySummary.value = await getDailySummary(d)
  }

  async function loadWeekly() {
    weeklySummary.value = await getWeeklySummary(weekStart.value)
    deviation.value = await getDeviationAnalysis(weekStart.value)
  }

  async function loadPreference() {
    try {
      const res = await getPreferences()
      preference.value = res.preference
    } catch {
      preference.value = null
    }
  }

  async function savePreference(data: Partial<Omit<FoodPreference, 'id' | 'user_id'>>) {
    const res = await updatePreferences(data)
    preference.value = res.preference
  }

  return {
    weekStart, plan, logs, dailySummary, weeklySummary, deviation, preference, loading,
    loadWeek, saveMeal, updateOne, removeMeal, duplicateMeal, markEaten,
    loadLogs, logFromText, removeLog,
    loadDaily, loadWeekly, loadPreference, savePreference,
  }
}