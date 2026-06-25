<script setup lang="ts">
import { ref } from 'vue'
import { useAuth } from '@/composables/useAuth'

const { register, error, loading } = useAuth()
const username = ref('')
const password = ref('')
const confirmPassword = ref('')
const localError = ref('')

async function handleSubmit() {
  localError.value = ''
  if (!username.value.trim() || !password.value.trim()) return
  if (password.value !== confirmPassword.value) {
    localError.value = '两次密码不一致'
    return
  }
  await register(username.value.trim(), password.value.trim())
}
</script>

<template>
  <div class="min-h-screen flex items-center justify-center bg-gray-50">
    <div class="w-full max-w-sm">
      <h1 class="text-3xl font-bold text-center text-orange-600 mb-2">CookAgent</h1>
      <p class="text-center text-gray-500 mb-8">创建账号</p>

      <form @submit.prevent="handleSubmit" class="bg-white rounded-xl shadow-sm p-6 space-y-4">
        <div>
          <label class="block text-sm font-medium text-gray-700 mb-1">用户名</label>
          <input
            v-model="username"
            type="text"
            class="w-full px-3 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-orange-500 focus:border-transparent"
            placeholder="请输入用户名"
          />
        </div>
        <div>
          <label class="block text-sm font-medium text-gray-700 mb-1">密码</label>
          <input
            v-model="password"
            type="password"
            class="w-full px-3 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-orange-500 focus:border-transparent"
            placeholder="请输入密码"
          />
        </div>
        <div>
          <label class="block text-sm font-medium text-gray-700 mb-1">确认密码</label>
          <input
            v-model="confirmPassword"
            type="password"
            class="w-full px-3 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-orange-500 focus:border-transparent"
            placeholder="请再次输入密码"
          />
        </div>

        <p v-if="localError || error" class="text-red-500 text-sm">{{ localError || error }}</p>

        <button
          type="submit"
          :disabled="loading"
          class="w-full py-2 bg-orange-500 text-white rounded-lg hover:bg-orange-600 disabled:opacity-50 transition-colors"
        >
          {{ loading ? '注册中...' : '注册' }}
        </button>

        <p class="text-center text-sm text-gray-500">
          已有账号？
          <router-link to="/login" class="text-orange-500 hover:underline">去登录</router-link>
        </p>
      </form>
    </div>
  </div>
</template>
