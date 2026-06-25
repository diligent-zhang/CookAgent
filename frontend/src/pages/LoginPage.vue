<script setup lang="ts">
import { ref } from 'vue'
import { useAuth } from '@/composables/useAuth'

const { login, error, loading } = useAuth()
const username = ref('')
const password = ref('')

async function handleSubmit() {
  if (!username.value.trim() || !password.value.trim()) return
  await login(username.value.trim(), password.value.trim())
}
</script>

<template>
  <div class="min-h-screen flex items-center justify-center bg-gray-50">
    <div class="w-full max-w-sm">
      <h1 class="text-3xl font-bold text-center text-orange-600 mb-2">CookAgent</h1>
      <p class="text-center text-gray-500 mb-8">智能烹饪助手</p>

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

        <p v-if="error" class="text-red-500 text-sm">{{ error }}</p>

        <button
          type="submit"
          :disabled="loading"
          class="w-full py-2 bg-orange-500 text-white rounded-lg hover:bg-orange-600 disabled:opacity-50 transition-colors"
        >
          {{ loading ? '登录中...' : '登录' }}
        </button>

        <p class="text-center text-sm text-gray-500">
          还没有账号？
          <router-link to="/register" class="text-orange-500 hover:underline">去注册</router-link>
        </p>
      </form>
    </div>
  </div>
</template>
