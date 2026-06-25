<script setup lang="ts">
import { onMounted, watch } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { useAuth } from '@/composables/useAuth'

const router = useRouter()
const route = useRoute()
const { isAuthenticated } = useAuth()

// Redirect to login if not authenticated
onMounted(() => {
  window.addEventListener('auth-unauthorized', () => {
    router.push('/login')
  })
})

watch(isAuthenticated, (val) => {
  if (!val && route.path !== '/login' && route.path !== '/register') {
    router.push('/login')
  }
}, { immediate: true })
</script>

<template>
  <router-view />
</template>
