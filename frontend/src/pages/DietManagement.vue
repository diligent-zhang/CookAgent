<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { useDiet } from '@/composables/useDiet'
import type { MealType, DietPlanMeal } from '@/types'

const {
  weekStart, plan, logs, dailySummary, weeklySummary, deviation, preference, loading,
  loadWeek, saveMeal, removeMeal, duplicateMeal, markEaten,
  loadLogs, logFromText, removeLog, loadDaily, loadWeekly, loadPreference, savePreference,
} = useDiet()

// ===== 日期工具 =====
const MEAL_LABELS: Record<MealType, string> = {
  breakfast: '早餐', lunch: '午餐', dinner: '晚餐', snack: '加餐',
}
const MEAL_ORDER: MealType[] = ['breakfast', 'lunch', 'dinner', 'snack']

function addDays(dateStr: string, days: number): string {
  const d = new Date(dateStr + 'T00:00:00')
  d.setDate(d.getDate() + days)
  return d.toISOString().slice(0, 10)
}

function formatDateCn(dateStr: string): string {
  const d = new Date(dateStr + 'T00:00:00')
  const weekdays = ['日', '一', '二', '三', '四', '五', '六']
  return `${d.getMonth() + 1}月${d.getDate()}日 周${weekdays[d.getDay()]}`
}

const daysOfWeek = computed(() => Array.from({ length: 7 }, (_, i) => addDays(weekStart.value, i)))

function mealsOfDay(dateStr: string): DietPlanMeal[] {
  return plan.value?.meals.filter(m => m.plan_date === dateStr) || []
}

// ===== Tab 切换 =====
type Tab = 'plan' | 'log' | 'analysis' | 'preference'
const activeTab = ref<Tab>('plan')

// ===== 添加餐次弹窗 =====
const showAddMeal = ref(false)
const addForm = reactive({
  plan_date: '',
  meal_type: 'breakfast' as MealType,
  dish_text: '',
  notes: '',
})

function openAddMeal(dateStr?: string) {
  addForm.plan_date = dateStr || addDays(new Date().toISOString().slice(0, 10), 0)
  addForm.meal_type = 'breakfast'
  addForm.dish_text = ''
  addForm.notes = ''
  showAddMeal.value = true
}

function parseDishes(text: string): Array<{ name: string; calories?: number }> {
  return text.split(/[，,;；]/).map(s => s.trim()).filter(Boolean).map(s => {
    const match = s.match(/^(.+?)\s*[（(]?\s*(\d+)\s*千?卡?\)?\s*$/)
    if (match) return { name: match[1], calories: Number(match[2]) }
    return { name: s }
  })
}

async function confirmAddMeal() {
  const dishes = parseDishes(addForm.dish_text)
  if (dishes.length === 0) return
  await saveMeal({
    plan_date: addForm.plan_date,
    meal_type: addForm.meal_type,
    dishes,
    notes: addForm.notes || undefined,
  })
  showAddMeal.value = false
}

// ===== 饮食记录 =====
const logDate = ref(new Date().toISOString().slice(0, 10))
const logText = ref('')
const logging = ref(false)

async function submitLog() {
  if (!logText.value.trim()) return
  logging.value = true
  try {
    await logFromText(logText.value)
    logText.value = ''
    await loadLogs(logDate.value)
  } finally {
    logging.value = false
  }
}

// ===== 偏好 =====
const prefForm = reactive<{ restrictions: string; allergies: string; cuisines: string; avoided: string; calorie_goal: string }>({
  restrictions: '', allergies: '', cuisines: '', avoided: '', calorie_goal: '',
})

function splitList(s: string): string[] {
  return s.split(/[，,、;；]/).map(x => x.trim()).filter(Boolean)
}

function initPrefForm() {
  if (!preference.value) return
  prefForm.restrictions = (preference.value.dietary_restrictions || []).join('，')
  prefForm.allergies = (preference.value.allergies || []).join('，')
  prefForm.cuisines = (preference.value.favorite_cuisines || []).join('，')
  prefForm.avoided = (preference.value.avoided_foods || []).join('，')
  prefForm.calorie_goal = preference.value.calorie_goal ? String(preference.value.calorie_goal) : ''
}

async function submitPreference() {
  await savePreference({
    dietary_restrictions: splitList(prefForm.restrictions),
    allergies: splitList(prefForm.allergies),
    favorite_cuisines: splitList(prefForm.cuisines),
    avoided_foods: splitList(prefForm.avoided),
    calorie_goal: prefForm.calorie_goal ? Number(prefForm.calorie_goal) : undefined,
  })
}

// ===== 格式化 =====
function fmt(v?: number, suffix = ''): string {
  if (v == null) return '—'
  return `${Math.round(v)}${suffix}`
}

const MEAL_COLORS: Record<MealType, string> = {
  breakfast: 'border-orange-400', lunch: 'border-green-400', dinner: 'border-purple-400', snack: 'border-yellow-400',
}

onMounted(async () => {
  await loadWeek()
  await loadLogs()
  await loadDaily()
  await loadWeekly()
  await loadPreference()
  initPrefForm()
})
</script>

<template>
  <div class="h-screen bg-white flex flex-col">
    <!-- 头部 Tab -->
    <div class="flex border-b border-gray-200 px-6 gap-1">
      <button
        v-for="tab in (['plan', 'log', 'analysis', 'preference'] as Tab[])"
        :key="tab"
        @click="activeTab = tab"
        :class="[
          'px-4 py-3 text-sm font-medium border-b-2 transition-colors',
          activeTab === tab ? 'border-orange-500 text-orange-600' : 'border-transparent text-gray-500 hover:text-gray-700',
        ]"
      >
        {{
          ({ plan: '饮食计划', log: '饮食记录', analysis: '营养分析', preference: '个人偏好' } as Record<Tab, string>)[tab]
        }}
      </button>
    </div>

    <div class="flex-1 overflow-y-auto p-6">
      <!-- ════════ 饮食计划 ════════ -->
      <div v-if="activeTab === 'plan'">
        <div class="flex items-center justify-between mb-4">
          <div class="flex items-center gap-2">
            <button class="px-3 py-1 rounded-lg bg-gray-100 hover:bg-gray-200 text-sm" @click="loadWeek(addDays(weekStart, -7))">‹ 上周</button>
            <span class="font-bold">{{ weekStart }} ~ {{ addDays(weekStart, 6) }}</span>
            <button class="px-3 py-1 rounded-lg bg-gray-100 hover:bg-gray-200 text-sm" @click="loadWeek(addDays(weekStart, 7))">下周 ›</button>
          </div>
          <button class="px-4 py-2 bg-orange-500 text-white rounded-lg hover:bg-orange-600 text-sm" @click="openAddMeal()">
            + 添加餐次
          </button>
        </div>

        <div class="grid grid-cols-7 gap-2">
          <div v-for="day in daysOfWeek" :key="day" class="border border-gray-200 rounded-xl p-2 min-h-[240px]">
            <div class="text-sm font-bold text-gray-700 text-center mb-2">{{ formatDateCn(day) }}</div>
            <div v-for="mt in MEAL_ORDER" :key="mt" class="mb-2">
              <div class="text-xs text-gray-400 mb-1">{{ MEAL_LABELS[mt] }}</div>
              <div
                v-for="meal in mealsOfDay(day).filter(m => m.meal_type === mt)"
                :key="meal.id"
                class="rounded-lg border-l-4 bg-gray-50 px-2 py-1 mb-1 group relative"
                :class="MEAL_COLORS[mt]"
              >
                <div class="text-sm">{{ meal.dishes.map(d => d.name).join('、') || '未命名' }}</div>
                <div class="text-xs text-gray-500">{{ meal.total_calories ? `${Math.round(meal.total_calories)} 千卡` : '' }}</div>
                <div class="absolute right-1 top-1 hidden group-hover:flex gap-1 text-[10px]">
                  <button class="text-gray-400 hover:text-green-500" title="标记已吃" @click="markEaten(meal.id)">吃✓</button>
                  <button class="text-gray-400 hover:text-blue-500" title="复制到下周一" @click="duplicateMeal(meal.id, addDays(day, 7))">复制</button>
                  <button class="text-gray-400 hover:text-red-500" title="删除" @click="removeMeal(meal.id)">×</button>
                </div>
              </div>
              <button
                v-if="!mealsOfDay(day).some(m => m.meal_type === mt)"
                class="w-full text-xs text-gray-300 hover:text-orange-400 py-0.5 text-center"
                @click="openAddMeal(day)"
              >+ 添加</button>
            </div>
          </div>
        </div>
        <p v-if="loading" class="text-gray-400 text-sm mt-2">加载中...</p>
      </div>

      <!-- ════════ 饮食记录 ════════ -->
      <div v-if="activeTab === 'log'">
        <div class="flex items-center gap-2 mb-4">
          <input type="date" v-model="logDate" class="border border-gray-300 rounded-lg px-3 py-2 text-sm" @change="loadLogs(logDate)" />
          <div class="flex-1 flex gap-2">
            <input
              v-model="logText"
              placeholder="用一句话记录：如“中午吃了半碗米饭配红烧肉，还有一杯奶茶”"
              class="flex-1 border border-gray-300 rounded-lg px-3 py-2 text-sm"
              @keyup.enter="submitLog"
            />
            <button class="px-4 py-2 bg-orange-500 text-white rounded-lg hover:bg-orange-600 text-sm" :disabled="logging" @click="submitLog">
              {{ logging ? '解析中...' : 'AI 记录' }}
            </button>
          </div>
        </div>

        <div class="space-y-3">
          <div v-for="log in logs" :key="log.log_id" class="border border-gray-200 rounded-xl p-4 flex justify-between">
            <div>
              <div class="flex items-center gap-2 mb-1">
                <span class="text-sm font-bold text-gray-700">{{ MEAL_LABELS[log.meal_type] || log.meal_type }}</span>
                <span class="text-xs text-gray-400">{{ log.total_calories ? `${Math.round(log.total_calories)} 千卡` : '' }}</span>
              </div>
              <div class="text-sm text-gray-600">{{ log.items.map(i => i.food_name).join('、') }}</div>
            </div>
            <button class="text-gray-400 hover:text-red-500 text-sm" @click="removeLog(log.log_id)">删除</button>
          </div>
          <p v-if="logs.length === 0" class="text-gray-400 text-sm text-center py-8">当天没有记录，用上面的输入框试试 AI 记录吧</p>
        </div>
      </div>

      <!-- ════════ 营养分析 ════════ -->
      <div v-if="activeTab === 'analysis'">
        <div class="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
          <div v-for="(label, key) in { calories: '热量(千卡)', protein: '蛋白质(g)', fat: '脂肪(g)', carbs: '碳水(g)' }" :key="key" class="border border-gray-200 rounded-xl p-4 text-center">
            <div class="text-2xl font-bold text-orange-600">
              {{ key === 'calories' ? fmt(dailySummary?.totals.calories) : fmt(dailySummary?.totals[key as 'protein']) }}
            </div>
            <div class="text-xs text-gray-500 mt-1">今日{{ label }}</div>
          </div>
        </div>

        <div class="grid grid-cols-1 lg:grid-cols-2 gap-4">
          <div class="border border-gray-200 rounded-xl p-4">
            <h3 class="font-bold mb-3">本周每日热量趋势</h3>
            <div v-if="weeklySummary?.daily" class="space-y-2">
              <div v-for="d in weeklySummary.daily" :key="d.date" class="flex items-center gap-2">
                <span class="w-20 text-xs text-gray-500">{{ d.date.slice(5) }}</span>
                <div class="flex-1 bg-gray-100 rounded h-4 overflow-hidden">
                  <div class="h-full bg-orange-400" :style="{ width: `${Math.min(100, (d.calories / 2500) * 100)}%` }"></div>
                </div>
                <span class="text-xs text-gray-600 w-16 text-right">{{ Math.round(d.calories) }}</span>
              </div>
            </div>
            <p v-else class="text-gray-400 text-sm">暂无本周数据</p>
          </div>

          <div class="border border-gray-200 rounded-xl p-4">
            <h3 class="font-bold mb-3">计划 vs 实际（本周）</h3>
            <div v-if="deviation" class="space-y-2">
              <div class="flex justify-between text-sm">
                <span class="text-gray-500">计划热量</span>
                <span class="font-medium">{{ fmt(deviation.plan_total_calories) }} 千卡</span>
              </div>
              <div class="flex justify-between text-sm">
                <span class="text-gray-500">实际摄入</span>
                <span class="font-medium">{{ fmt(deviation.actual_total_calories) }} 千卡</span>
              </div>
              <div class="flex justify-between text-sm">
                <span class="text-gray-500">执行率</span>
                <span class="font-medium" :class="(deviation.execution_rate ?? 0) > 1 ? 'text-green-600' : 'text-orange-600'">
                  {{ deviation.execution_rate ? `${(deviation.execution_rate * 100).toFixed(0)}%` : '—' }}
                </span>
              </div>
              <div class="flex justify-between text-sm">
                <span class="text-gray-500">整体偏差</span>
                <span class="font-medium">{{ deviation.total_deviation_pct != null ? `${deviation.total_deviation_pct > 0 ? '+' : ''}${(deviation.total_deviation_pct * 100).toFixed(0)}%` : '—' }}</span>
              </div>
            </div>
            <p v-else class="text-gray-400 text-sm">暂无偏差数据</p>
          </div>
        </div>
      </div>

      <!-- ════════ 个人偏好 ════════ -->
      <div v-if="activeTab === 'preference'" class="max-w-2xl">
        <div class="border border-gray-200 rounded-xl p-6 space-y-4">
          <h3 class="font-bold">饮食偏好设置</h3>
          <div>
            <label class="text-sm text-gray-600 block mb-1">饮食限制（如：乳糖不耐、蛋奶素）</label>
            <input v-model="prefForm.restrictions" class="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm" placeholder="用逗号分隔" />
          </div>
          <div>
            <label class="text-sm text-gray-600 block mb-1">过敏原（如：花生、虾）</label>
            <input v-model="prefForm.allergies" class="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm" placeholder="用逗号分隔" />
          </div>
          <div>
            <label class="text-sm text-gray-600 block mb-1">喜爱的菜系（如：川菜、日料）</label>
            <input v-model="prefForm.cuisines" class="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm" placeholder="用逗号分隔" />
          </div>
          <div>
            <label class="text-sm text-gray-600 block mb-1">不喜欢的食物</label>
            <input v-model="prefForm.avoided" class="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm" placeholder="用逗号分隔" />
          </div>
          <div>
            <label class="text-sm text-gray-600 block mb-1">每日热量目标（千卡）</label>
            <input v-model.number="prefForm.calorie_goal" type="number" class="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm" placeholder="如 1800" />
          </div>
          <button class="px-4 py-2 bg-orange-500 text-white rounded-lg hover:bg-orange-600 text-sm" @click="submitPreference">保存偏好</button>
        </div>
      </div>
    </div>

    <!-- 添加餐次弹窗 -->
    <div v-if="showAddMeal" class="fixed inset-0 bg-black/30 flex items-center justify-center z-50">
      <div class="bg-white rounded-2xl p-6 w-[480px]">
        <h3 class="font-bold mb-4">添加餐次</h3>
        <div class="space-y-3">
          <div class="flex gap-2">
            <input type="date" v-model="addForm.plan_date" class="flex-1 border border-gray-300 rounded-lg px-3 py-2 text-sm" />
            <select v-model="addForm.meal_type" class="border border-gray-300 rounded-lg px-3 py-2 text-sm">
              <option v-for="mt in MEAL_ORDER" :key="mt" :value="mt">{{ MEAL_LABELS[mt] }}</option>
            </select>
          </div>
          <div>
            <label class="text-sm text-gray-600 block mb-1">菜品（逗号分隔，可带热量：如“清蒸鲈鱼(280千卡), 蒜蓉西兰花(80千卡)”）</label>
            <textarea v-model="addForm.dish_text" rows="3" class="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm"></textarea>
          </div>
          <div>
            <label class="text-sm text-gray-600 block mb-1">备注</label>
            <input v-model="addForm.notes" class="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm" />
          </div>
        </div>
        <div class="flex justify-end gap-2 mt-4">
          <button class="px-4 py-2 text-gray-500 hover:text-gray-700 text-sm" @click="showAddMeal = false">取消</button>
          <button class="px-4 py-2 bg-orange-500 text-white rounded-lg hover:bg-orange-600 text-sm" @click="confirmAddMeal">保存</button>
        </div>
      </div>
    </div>
  </div>
</template>