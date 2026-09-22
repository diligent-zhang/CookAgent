// ===== SSE Events =====
export interface SSEEvent {
  type: string
  content?: string
  sources?: Source[]
  message_id?: string
  session_id?: string
  tool_name?: string
  arguments?: Record<string, unknown>
  result?: string
  step_number?: number
}

// ===== Chat =====
export interface Source {
  dish_name: string
  category: string
  source: string
  relevance_score: number
}

export interface Message {
  id: string
  role: 'user' | 'assistant'
  content: string
  created_at: string
  sources?: Source[]
  thoughts?: AgentStep[]
}

export interface Conversation {
  id: string
  title: string
  created_at: string
  updated_at: string
}

// ===== Agent =====
export interface AgentStep {
  type: 'thought' | 'tool_call' | 'observation'
  content: string
  tool_name?: string
  step_number: number
}

export interface AgentSession {
  id: string
  title: string
  status: 'active' | 'completed' | 'error'
  created_at: string
  updated_at: string
}

// ===== Auth =====
export interface User {
  id: string
  username: string
  nickname: string
}

export interface AuthResponse {
  access_token: string
  token_type: string
  user: User
}

// ===== Diet =====
export type MealType = 'breakfast' | 'lunch' | 'dinner' | 'snack'

export interface Dish {
  name: string
  weight_g?: number
  unit?: string
  calories?: number
  protein?: number
  fat?: number
  carbs?: number
}

export interface DietPlanMeal {
  id: string
  plan_date: string
  meal_type: MealType
  dishes: Dish[]
  total_calories?: number
  total_protein?: number
  total_fat?: number
  total_carbs?: number
  notes?: string
}

export interface DietPlanWeek {
  week_start: string
  meals: DietPlanMeal[]
}

export interface DietLogFood {
  id?: string
  food_name: string
  weight_g?: number
  unit?: string
  calories?: number
  protein?: number
  fat?: number
  carbs?: number
  source?: string
}

export interface DietLog {
  log_id: string
  log_date: string
  meal_type: MealType
  items: DietLogFood[]
  total_calories?: number
  total_protein?: number
  total_fat?: number
  total_carbs?: number
  notes?: string
}

export interface NutritionSummary {
  start_date: string
  end_date: string
  totals: {
    calories: number
    protein: number
    fat: number
    carbs: number
  }
  meal_breakdown: Record<string, unknown>
  log_count: number
  daily?: Array<{
    date: string
    calories: number
    protein: number
    fat: number
    carbs: number
  }>
}

export interface DeviationAnalysis {
  week_start: string
  plan_total_calories: number
  actual_total_calories: number
  total_deviation_pct?: number
  execution_rate?: number
  per_meal: Array<{
    date: string
    meal_type: string
    plan_calories: number
    actual_calories: number
    deviation_pct?: number
  }>
}

export interface FoodPreference {
  id: string
  user_id: string
  dietary_restrictions: string[]
  allergies: string[]
  favorite_cuisines: string[]
  avoided_foods: string[]
  calorie_goal?: number
  protein_goal?: number
  fat_goal?: number
  carbs_goal?: number
  avg_daily_calories?: number
  common_foods: string[]
}

// ===== LLM Usage =====

export interface LlmUsageLogEntry {
  id: string
  module_name: string
  model_name?: string
  tool_name?: string
  input_tokens?: number
  output_tokens?: number
  total_tokens?: number
  duration_ms?: number
  created_at: string
}

export interface LlmUsageSummary {
  total_calls: number
  total_input_tokens: number
  total_output_tokens: number
  total_tokens: number
  avg_duration_ms?: number
  by_model: Array<{
    model_name: string
    calls: number
    tokens: number
    avg_duration_ms?: number
  }>
}
